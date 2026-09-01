import base64
import io
import logging
import math
import os
import re
import time
import unicodedata
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.request import Request, urlopen
from uuid import uuid4

from fastapi import HTTPException

from .alignment_service import AlignmentService
from .db import connect as connect_db
from .image_normalization import ImageNormalizationService
from .image_verification_category_service import (
    ImageVerificationCategoryService,
    categories_to_runtime_payload,
    ensure_image_verification_categories_table,
    get_image_verification_category,
    list_image_verification_categories,
)
from .layout_analysis_service import (
    AUTO_ROI_EXPAND_BOTTOM_PX,
    AUTO_ROI_EXPAND_LEFT_PX,
    AUTO_ROI_EXPAND_RIGHT_PX,
    AUTO_ROI_EXPAND_TOP_PX,
    analyze_layout,
    detect_text_boxes,
)
from .layout_signature_service import build_layout_signature, compare_layout_signatures, signature_from_json, signature_to_json
from .layout_template_matcher import search_layout_candidates
from .ocr_adapter import OcrUnavailableError, ocr_roi, ocr_rois, recognize_text_roi
from .ocr_postprocess import normalize_ocr_text
from .siglip_image_verification_adapter import (
    verify_image_category,
)
from .table_recognition_v2_adapter import TableRecognitionV2UnavailableError, recognize_table_v2
from .schemas import (
    CustomOcrRequest,
    DocumentUploadRequest,
    ExtractionRequest,
    IgnoreRegionCreate,
    IgnoreRegionUpdate,
    RequestedFieldCreate,
    RequestedFieldUpdate,
    TemplateCreate,
    TemplateFieldCreate,
    TemplateFieldUpdate,
    TemplatePageCreate,
    TemplatePageUpdate,
    TemplateRequestCreate,
    TemplateRequestImageCreate,
    TemplateRequestImageUpdate,
    TemplateRequestUpdate,
    TemplateRequestConvert,
    TemplateTestRequest,
    TemplateUpdate,
    TemplateVersionCreate,
    TemplateVersionFromRequestCreate,
)
from .json_utils import jsonb_dump, jsonb_load


logger = logging.getLogger(__name__)


class EmbeddingContextError(Exception):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stub_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


def _template_group_code() -> str:
    return f"tgrp_{uuid4().hex[:12]}"


def _normalize_extraction_method(value: Optional[str]) -> str:
    if value == "typhoon_ocr":
        return "paddle_thai_ocr"
    if value in {"ocr_text", "ocr_table", "paddle_thai_ocr", "table_recognition_v2", "extract_image"}:
        return value
    return "ocr_text"


def _normalize_data_type(value: Optional[str]) -> str:
    if value in {"text", "number", "date", "table", "image", "string", "address", "currency"}:
        return "text" if value == "string" else value
    return "text"


def _normalize_roi_mode(value: Optional[str]) -> str:
    return "flexible" if value == "flexible" else "fix"


def _normalize_expected_content(value: Optional[str]) -> Optional[str]:
    return "text" if value == "text" else None


def _expand_roi_ratio_by_auto_padding(roi: Dict[str, float], image_width: int, image_height: int) -> Dict[str, float]:
    pad_left = AUTO_ROI_EXPAND_LEFT_PX / max(float(image_width), 1.0)
    pad_right = AUTO_ROI_EXPAND_RIGHT_PX / max(float(image_width), 1.0)
    pad_top = AUTO_ROI_EXPAND_TOP_PX / max(float(image_height), 1.0)
    pad_bottom = AUTO_ROI_EXPAND_BOTTOM_PX / max(float(image_height), 1.0)
    left = max(0.0, float(roi.get("x_ratio") or 0.0) - pad_left)
    top = max(0.0, float(roi.get("y_ratio") or 0.0) - pad_top)
    right = min(1.0, float(roi.get("x_ratio") or 0.0) + float(roi.get("width_ratio") or 0.0) + pad_right)
    bottom = min(1.0, float(roi.get("y_ratio") or 0.0) + float(roi.get("height_ratio") or 0.0) + pad_bottom)
    return {
        "x_ratio": left,
        "y_ratio": top,
        "width_ratio": max(0.0, right - left),
        "height_ratio": max(0.0, bottom - top),
    }


def _normalize_detection_mode(value: Optional[str]) -> str:
    return "main_page" if value == "main_page" else "all_pages"


def _normalize_main_page_number(value: Any) -> int:
    try:
        page_number = int(value or 1)
    except (TypeError, ValueError):
        return 1
    return max(1, page_number)


def _connect() -> Any:
    conn = connect_db()
    conn.execute("PRAGMA foreign_keys = ON")
    ensure_image_verification_categories_table(conn)
    return conn


def _row_to_dict(row: Any) -> Dict[str, Any]:
    return dict(row)


def _request_row_to_api(row: Any) -> Dict[str, Any]:
    item = _row_to_dict(row)
    return {
        "id": item["id"],
        "requested_by": item["requested_by"],
        "request_title": item["request_title"],
        "document_type": item["document_type"],
        "sample_file_url": item.get("sample_file_url"),
        "request_mode": item["request_mode"],
        "status": item["status"],
        "user_note": item["user_note"],
        "admin_note": item["admin_note"],
        "converted_template_id": item.get("converted_template_version_id") or item.get("converted_template_id"),
        "converted_template_group_id": item.get("converted_template_group_id"),
        "converted_template_version_id": item.get("converted_template_version_id"),
        "page_count": item.get("page_count", 1),
        "created_at": item["created_at"],
        "updated_at": item.get("updated_at") or item.get("reviewed_at") or item["created_at"],
    }


def _page_row_to_api(row: Any) -> Dict[str, Any]:
    item = _row_to_dict(row)
    return {
        "id": item["id"],
        "template_request_id": item["template_request_id"],
        "page_number": item["page_number"],
        "page_name": item.get("page_name"),
        "sample_image_url": item["sample_image_url"],
        "source_file_id": item.get("source_file_id") or item.get("source_file_name") or item["id"],
        "source_file_name": item.get("source_file_name"),
        "image_source": item.get("image_source", "user_request"),
        "review_status": item.get("review_status", "pending"),
        "is_canonical": bool(item.get("is_canonical", 0)),
        "layout_signature_json": item.get("layout_signature_json"),
        "created_at": item["created_at"],
        "updated_at": item.get("updated_at") or item["created_at"],
    }


def _field_row_to_api(row: Any) -> Dict[str, Any]:
    item = _row_to_dict(row)
    return {
        "id": item["id"],
        "template_request_id": item.get("template_request_id"),
        "template_request_page_id": item["template_request_page_id"],
        "page_number": item.get("page_number", 1),
        "field_name": item["field_name"],
        "display_label": item["display_label"],
        "roi": {
            "page_number": item["page_number"],
            "x_ratio": item["roi_x_ratio"],
            "y_ratio": item["roi_y_ratio"],
            "width_ratio": item["roi_width_ratio"],
            "height_ratio": item["roi_height_ratio"],
        },
        "data_type": _normalize_data_type(item.get("data_type")),
        "extraction_method": _normalize_extraction_method(item.get("extraction_method")),
        "user_note": item.get("user_note"),
        "created_at": item["created_at"],
        "updated_at": item.get("updated_at") or item["created_at"],
    }


def _template_row_to_api(row: Any) -> Dict[str, Any]:
    item = _row_to_dict(row)
    shared_fields = jsonb_load(item.get("shared_fields_json"), [])
    return {
        "id": item["id"],
        "name": item.get("name") or item.get("template_name") or item.get("version_name") or item["id"],
        "document_type": item["document_type"],
        "category": item["category"],
        "status": item["status"],
        "version": item.get("version") or item.get("version_number") or 1,
        "template_group_id": item.get("template_group_id") or item["id"],
        "version_number": item.get("version_number") or item.get("version") or 1,
        "base_template_id": item.get("base_template_id") or item.get("created_from_version_id"),
        "description": item.get("description"),
        "shared_fields": shared_fields if isinstance(shared_fields, list) else [],
        "creation_type": item.get("creation_type") or "new_template",
        "detection_mode": _normalize_detection_mode(item.get("detection_mode")),
        "main_page_number": _normalize_main_page_number(item.get("main_page_number")),
        "page_count": item.get("page_count", 1),
        "similarity_threshold": item["similarity_threshold"],
        "final_confidence_threshold": item["final_confidence_threshold"],
        "layout_weight": item.get("layout_weight", 0.50),
        "text_anchor_weight": item.get("text_anchor_weight", 0.35),
        "image_anchor_weight": item.get("image_anchor_weight", 0.15),
        "rejection_reason": item.get("rejection_reason"),
        "created_at": item["created_at"],
        "updated_at": item["updated_at"],
    }


def _template_page_row_to_api(row: Any) -> Dict[str, Any]:
    item = _row_to_dict(row)
    layout_signature_json = item.get("layout_signature_json") if "layout_signature_json" in item else None
    return {
        "id": item["id"],
        "template_id": item.get("template_version_id") or item.get("template_id"),
        "page_number": item["page_number"],
        "page_name": item["page_name"],
        "sample_image_url": item["sample_image_url"],
        "normalized_image_url": item["normalized_image_url"],
        "layout_signature_json": layout_signature_json,
        "similarity_threshold": item.get("similarity_threshold"),
        "final_confidence_threshold": item.get("final_confidence_threshold"),
        "created_at": item["created_at"],
        "updated_at": item["updated_at"],
    }


def _template_field_row_to_api(row: Any) -> Dict[str, Any]:
    item = _row_to_dict(row)
    return {
        "id": item["id"],
        "template_id": item.get("template_id"),
        "template_page_id": item["template_page_id"],
        "page_number": item.get("page_number", 1),
        "field_name": item.get("field_name") or item.get("anchor_name"),
        "display_label": item.get("display_label") or item.get("anchor_name") or item.get("field_name"),
        "roi": {
            "page_number": item["page_number"],
            "x_ratio": item["roi_x_ratio"],
            "y_ratio": item["roi_y_ratio"],
            "width_ratio": item["roi_width_ratio"],
            "height_ratio": item["roi_height_ratio"],
        },
        "data_type": item["data_type"],
        "user_selectable": bool(item.get("user_selectable", not item.get("use_for_verification", False))),
        "default_selected": bool(item.get("default_selected", False)),
        "use_for_verification": bool(item.get("use_for_verification", False)),
        "expected_text": item.get("expected_text"),
        "match_type": item.get("match_type"),
        "required_for_verification": bool(item.get("required_for_verification", item.get("required", False))),
        "extraction_method": _normalize_extraction_method(item["extraction_method"]),
        "roi_mode": item.get("roi_mode") or "fix",
        "expected_content": item.get("expected_content"),
        "roi_padding": item.get("roi_padding"),
        "verification_weight": item.get("verification_weight", item.get("weight", 1.0)),
        "image_category": item.get("image_category") or item.get("image_category_id"),
        "sort_order": item["sort_order"],
        "created_at": item["created_at"],
        "updated_at": item["updated_at"],
    }


def _ignore_region_row_to_api(row: Any) -> Dict[str, Any]:
    item = _row_to_dict(row)
    return {
        "id": item["id"],
        "template_id": item.get("template_id"),
        "template_page_id": item["template_page_id"],
        "page_number": item.get("page_number", 1),
        "field_name": item.get("field_name") or item.get("region_name"),
        "roi": {
            "page_number": item["page_number"],
            "x_ratio": item["roi_x_ratio"],
            "y_ratio": item["roi_y_ratio"],
            "width_ratio": item["roi_width_ratio"],
            "height_ratio": item["roi_height_ratio"],
        },
        "created_at": item["created_at"],
        "updated_at": item.get("updated_at") or item["created_at"],
    }


def _embedding_job_row_to_api(row: Optional[Any]) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    item = _row_to_dict(row)
    return {
        "id": item["id"],
        "template_id": item.get("template_version_id") or item.get("template_id"),
        "template_version_id": item.get("template_version_id") or item.get("template_id"),
        "status": item["status"],
        "requested_at": item["requested_at"],
        "started_at": item["started_at"],
        "completed_at": item["completed_at"],
        "error_message": item["error_message"],
        "vector_id": item.get("vector_id"),
        "metadata_json": item["metadata_json"],
        "step": item.get("step"),
    }


def _cosine_similarity(left: List[float], right: List[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


def _storage_root() -> Path:
    return Path(__file__).resolve().parents[1] / "storage"


def _load_image_source(source: Optional[str]):
    if not source:
        return None
    try:
        from PIL import Image
    except ImportError:
        return None

    if source.startswith("data:image"):
        try:
            encoded = source.split(",", 1)[1]
            return Image.open(io.BytesIO(base64.b64decode(encoded))).convert("RGB")
        except Exception:
            return None

    if source.startswith("http://") or source.startswith("https://"):
        try:
            request = Request(source, headers={"User-Agent": "OCR-Studio/1.0"})
            with urlopen(request, timeout=10) as response:
                return Image.open(io.BytesIO(response.read())).convert("RGB")
        except Exception:
            return None

    path = Path(source)
    if not path.exists():
        return None
    try:
        return Image.open(path).convert("RGB")
    except Exception:
        return None


def _image_to_bgr_array(image: Any):
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None
    try:
        rgb = np.array(image.convert("RGB"))
        return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    except Exception:
        return None


def _field_roi_to_layout_region(field: Dict[str, Any], *, source: str) -> Optional[Dict[str, Any]]:
    try:
        roi = {
            "x_ratio": float(field.get("roi_x_ratio") or 0.0),
            "y_ratio": float(field.get("roi_y_ratio") or 0.0),
            "width_ratio": float(field.get("roi_width_ratio") or 0.0),
            "height_ratio": float(field.get("roi_height_ratio") or 0.0),
        }
    except (TypeError, ValueError):
        return None
    if roi["width_ratio"] <= 0 or roi["height_ratio"] <= 0:
        return None
    return {
        "type": _normalize_data_type(field.get("data_type")),
        "roi": roi,
        "confidence": 1.0,
        "source": source,
    }


def _signature_layout_overrides(fields: Optional[List[Dict[str, Any]]]) -> Dict[str, List[Dict[str, Any]]]:
    ignored_regions: List[Dict[str, Any]] = []
    stable_regions: List[Dict[str, Any]] = []
    for field in fields or []:
        region = _field_roi_to_layout_region(field, source="template_roi")
        if not region:
            continue
        if _normalize_roi_mode(field.get("roi_mode")) == "flexible":
            ignored_regions.append(region["roi"])
            stable_regions.append({**region, "source": "flexible_search_boundary"})
        else:
            stable_regions.append({**region, "source": "fix_roi"})
    return {"ignored_regions": ignored_regions, "stable_regions": stable_regions}


def _template_fields_for_page(conn: Any, template_id: str, page_number: int) -> List[Dict[str, Any]]:
    return [
        _row_to_dict(row)
        for row in conn.execute(
            """
            SELECT
                ef.*,
                tp.template_version_id AS template_id,
                tp.page_number
            FROM extraction_fields ef
            JOIN template_pages tp ON tp.id = ef.template_page_id
            WHERE tp.template_version_id = ? AND tp.page_number = ?
            ORDER BY sort_order ASC, created_at ASC
            """,
            (template_id, page_number),
        ).fetchall()
    ]


def _generate_layout_signature_for_source(source: Optional[str], fields: Optional[List[Dict[str, Any]]] = None) -> Optional[Dict[str, Any]]:
    image = _load_image_source(source)
    if image is None:
        return None
    opencv_img = _image_to_bgr_array(image)
    if opencv_img is None:
        return None
    analysis = analyze_layout(opencv_img)
    overrides = _signature_layout_overrides(fields)
    if overrides["ignored_regions"]:
        analysis["ignored_regions"] = overrides["ignored_regions"]
    if overrides["stable_regions"]:
        analysis["stable_regions"] = overrides["stable_regions"]
    return build_layout_signature(analysis)


def _ensure_template_pages_have_layout_signatures(conn: Any, template_id: str) -> None:
    return None


def _refresh_template_layout_signatures(conn: Any, template_id: str) -> List[Dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, page_number, normalized_image_url, sample_image_url
        FROM template_pages
        WHERE template_version_id = ?
        ORDER BY page_number ASC
        """,
        (template_id,),
    ).fetchall()
    refreshed: List[Dict[str, Any]] = []
    for row in rows:
        source = row["normalized_image_url"] or row["sample_image_url"]
        signature = _generate_layout_signature_for_source(source, _template_fields_for_page(conn, template_id, int(row["page_number"])))
        if signature is None:
            refreshed.append(
                {
                    "template_page_id": row["id"],
                    "page_number": row["page_number"],
                    "status": "failed",
                    "reason": "page_image_unavailable_or_invalid",
                }
            )
            continue
        signature_json = signature_to_json(signature)
        conn.execute(
            """
            UPDATE template_pages
            SET layout_signature_json = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (signature_json, row["id"]),
        )
        refreshed.append(
            {
                "template_page_id": row["id"],
                "page_number": row["page_number"],
                "status": "generated",
                "region_count": signature.get("region_count", 0),
                "model": signature.get("model"),
            }
        )
    _ensure_template_pages_have_layout_signatures(conn, template_id)
    return refreshed


def _refresh_template_page_signatures(conn: Any, template_id: str) -> List[Dict[str, Any]]:
    return _refresh_template_layout_signatures(conn, template_id)


def _refresh_template_layout_reference_signatures(conn: Any, template_id: str) -> List[Dict[str, Any]]:
    # Schema V2 uses template_pages as the canonical layout references.
    return _refresh_template_layout_signatures(conn, template_id)


def _crop_anchor_roi(image_path_or_source: str, roi: Dict[str, Any], output_path: Path, padding: float = 0) -> Optional[str]:
    image = _load_image_source(image_path_or_source)
    if image is None:
        return None
    width, height = image.size
    x = float(roi["x_ratio"]) * width
    y = float(roi["y_ratio"]) * height
    w = float(roi["width_ratio"]) * width
    h = float(roi["height_ratio"]) * height
    pad = max(0.0, float(padding or 0))
    left = max(0, int(round(x - pad)))
    top = max(0, int(round(y - pad)))
    right = min(width, int(round(x + w + pad)))
    bottom = min(height, int(round(y + h + pad)))
    if right <= left or bottom <= top:
        return None
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.crop((left, top, right, bottom)).save(output_path, format="PNG")
    return str(output_path)


def _image_path_to_data_url(path_value: Optional[str]) -> Optional[str]:
    if not path_value:
        return None
    path = Path(path_value)
    if not path.exists() or not path.is_file():
        return None
    try:
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    except OSError:
        return None
    return f"data:image/png;base64,{encoded}"


def _pil_image_to_bgr_array(image: Any):
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None
    try:
        rgb = np.array(image.convert("RGB"))
        return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    except Exception:
        return None


def _crop_template_field_image(image_source: Optional[str], roi: Dict[str, Any]) -> Optional[Any]:
    image = _load_image_source(image_source)
    if image is None:
        return None
    width, height = image.size
    try:
        x = float(roi.get("x_ratio") or 0.0) * width
        y = float(roi.get("y_ratio") or 0.0) * height
        w = float(roi.get("width_ratio") or 0.0) * width
        h = float(roi.get("height_ratio") or 0.0) * height
    except (TypeError, ValueError):
        return None
    left = max(0, int(round(x)))
    top = max(0, int(round(y)))
    right = min(width, int(round(x + w)))
    bottom = min(height, int(round(y + h)))
    if right <= left or bottom <= top:
        return None
    return image.crop((left, top, right, bottom)).convert("RGB")


def _pil_image_to_data_url(image: Any) -> Optional[str]:
    if image is None:
        return None
    output = io.BytesIO()
    try:
        image.save(output, format="PNG")
    except Exception:
        return None
    encoded = base64.b64encode(output.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _layout_region_type(region: Dict[str, Any]) -> str:
    return str(region.get("type") or region.get("data_type") or "").lower()


def _layout_regions_from_analysis(analysis: Dict[str, Any]) -> List[Dict[str, Any]]:
    if isinstance(analysis.get("regions"), list):
        return [region for region in analysis["regions"] if isinstance(region, dict)]
    data = analysis.get("data")
    if isinstance(data, dict) and isinstance(data.get("regions"), list):
        return [region for region in data["regions"] if isinstance(region, dict)]
    pages = analysis.get("pages")
    if isinstance(pages, list):
        regions: List[Dict[str, Any]] = []
        for page in pages:
            if isinstance(page, dict) and isinstance(page.get("regions"), list):
                regions.extend(region for region in page["regions"] if isinstance(region, dict))
        return regions
    return []


def _is_supported_layout_region(region: Dict[str, Any]) -> bool:
    region_type = _layout_region_type(region).replace("_", " ").replace("-", " ")
    if any(token in region_type for token in ("header", "footer", "page number")):
        return False
    return bool(region.get("roi")) or bool(region.get("bbox"))


def _resolved_layout_region_type(region: Dict[str, Any]) -> str:
    region_type = _layout_region_type(region).replace("_", " ").replace("-", " ")
    if "table" in region_type and "title" not in region_type and "caption" not in region_type:
        return "table"
    if any(token in region_type for token in ("image", "figure", "pic", "seal", "logo", "chart")):
        return "image"
    return "text"


def _extraction_method_for_resolved_type(data_type: str) -> str:
    if data_type == "table":
        return "table_recognition_v2"
    if data_type == "image":
        return "extract_image"
    return "paddle_thai_ocr"


def _expand_table_roi(roi: Dict[str, float], image_width: int, image_height: int) -> Dict[str, float]:
    pad_x = 10.0 / max(float(image_width), 1.0)
    pad_y = 10.0 / max(float(image_height), 1.0)
    x = max(0.0, float(roi.get("x_ratio") or 0.0) - pad_x)
    y = max(0.0, float(roi.get("y_ratio") or 0.0) - pad_y)
    right = min(1.0, float(roi.get("x_ratio") or 0.0) + float(roi.get("width_ratio") or 0.0) + pad_x)
    bottom = min(1.0, float(roi.get("y_ratio") or 0.0) + float(roi.get("height_ratio") or 0.0) + pad_y)
    return {
        "x_ratio": x,
        "y_ratio": y,
        "width_ratio": max(0.0, right - x),
        "height_ratio": max(0.0, bottom - y),
    }


def _region_roi_from_boundary(region: Dict[str, Any], image_width: int, image_height: int) -> Optional[Dict[str, float]]:
    roi = region.get("roi") if isinstance(region.get("roi"), dict) else None
    if roi:
        return {
            "x_ratio": float(roi.get("x_ratio") or 0.0),
            "y_ratio": float(roi.get("y_ratio") or 0.0),
            "width_ratio": float(roi.get("width_ratio") or 0.0),
            "height_ratio": float(roi.get("height_ratio") or 0.0),
        }
    bbox = region.get("bbox") if isinstance(region.get("bbox"), dict) else None
    if not bbox:
        return None
    try:
        x = max(0.0, float(bbox.get("x") or 0.0))
        y = max(0.0, float(bbox.get("y") or 0.0))
        width = max(0.0, float(bbox.get("width") or 0.0))
        height = max(0.0, float(bbox.get("height") or 0.0))
    except (TypeError, ValueError):
        return None
    right = min(float(image_width), x + width)
    bottom = min(float(image_height), y + height)
    return {
        "x_ratio": x / max(float(image_width), 1.0),
        "y_ratio": y / max(float(image_height), 1.0),
        "width_ratio": max(0.0, right - x) / max(float(image_width), 1.0),
        "height_ratio": max(0.0, bottom - y) / max(float(image_height), 1.0),
    }


def _median_float(values: List[float], fallback: float = 0.0) -> float:
    prepared = sorted(float(value) for value in values if math.isfinite(float(value)))
    if not prepared:
        return fallback
    middle = len(prepared) // 2
    if len(prepared) % 2:
        return prepared[middle]
    return (prepared[middle - 1] + prepared[middle]) / 2.0


def _horizontal_overlap_ratio(left: Dict[str, float], right: Dict[str, float]) -> float:
    left_x = float(left.get("x_ratio") or 0.0)
    left_right = left_x + float(left.get("width_ratio") or 0.0)
    right_x = float(right.get("x_ratio") or 0.0)
    right_right = right_x + float(right.get("width_ratio") or 0.0)
    overlap = max(0.0, min(left_right, right_right) - max(left_x, right_x))
    denominator = max(1e-9, min(float(left.get("width_ratio") or 0.0), float(right.get("width_ratio") or 0.0)))
    return overlap / denominator


def _text_line_regions_from_detection(boundary_image_path: str, image_width: int, image_height: int) -> List[Dict[str, Any]]:
    try:
        detection = detect_text_boxes(boundary_image_path)
    except Exception:
        return []
    regions: List[Dict[str, Any]] = []
    for index, region in enumerate(_layout_regions_from_analysis(detection), start=1):
        roi = _region_roi_from_boundary(region, image_width, image_height)
        if not roi or _roi_area(roi) <= 0:
            continue
        regions.append(
            {
                "type": "text",
                "data_type": "text",
                "layout_type": "text_line",
                "source": "paddle_text_detection_line",
                "confidence": region.get("confidence", 0.0),
                "roi": roi,
                "image_width": image_width,
                "image_height": image_height,
                "_line_index": index,
            }
        )
    return regions


def _paragraph_regions_from_text_lines(lines: List[Dict[str, Any]], debug_scope: str = "flexible") -> List[Dict[str, Any]]:
    prepared = [
        line
        for line in lines
        if isinstance(line.get("roi"), dict) and _roi_area(line["roi"]) > 0
    ]
    if not prepared:
        return []
    prepared.sort(
        key=lambda line: (
            float(line["roi"].get("y_ratio") or 0.0) + float(line["roi"].get("height_ratio") or 0.0) / 2.0,
            float(line["roi"].get("x_ratio") or 0.0),
        )
    )
    heights = [float(line["roi"].get("height_ratio") or 0.0) for line in prepared]
    widths = [float(line["roi"].get("width_ratio") or 0.0) for line in prepared]
    gaps = [
        max(
            0.0,
            float(prepared[index]["roi"].get("y_ratio") or 0.0)
            - (
                float(prepared[index - 1]["roi"].get("y_ratio") or 0.0)
                + float(prepared[index - 1]["roi"].get("height_ratio") or 0.0)
            ),
        )
        for index in range(1, len(prepared))
    ]
    median_height = _median_float(heights, 1.0)
    median_width = _median_float(widths, 1.0)
    median_gap = _median_float(gaps, median_height * 0.35)
    normal_left_edge = _median_float([float(line["roi"].get("x_ratio") or 0.0) for line in prepared], 0.0)

    groups: List[List[Dict[str, Any]]] = []
    current: List[Dict[str, Any]] = []
    for line_index, line in enumerate(prepared):
        if not current:
            current = [line]
            continue
        prev = current[-1]
        prev_roi = prev["roi"]
        roi = line["roi"]
        gap = float(roi.get("y_ratio") or 0.0) - (
            float(prev_roi.get("y_ratio") or 0.0) + float(prev_roi.get("height_ratio") or 0.0)
        )
        current_x = float(roi.get("x_ratio") or 0.0)
        prev_x = float(prev_roi.get("x_ratio") or 0.0)
        indent_delta = abs(current_x - prev_x)
        first_line_indent = current_x - normal_left_edge
        prev_width = float(prev_roi.get("width_ratio") or 0.0)
        previous_width_ratio = prev_width / max(median_width, 1e-9)
        overlap = _horizontal_overlap_ratio(prev_roi, roi)
        gap_ratio = gap / max(median_gap, median_height * 0.25, 1e-9)
        indent_ratio = indent_delta / max(median_width, median_height, 1e-9)
        first_line_indent_ratio = first_line_indent / max(median_width, median_height, 1e-9)
        gap_evidence = gap_ratio >= 1.85 and gap >= median_height * 0.85
        indent_evidence = indent_ratio >= 0.12
        first_line_evidence = first_line_indent_ratio >= 0.16 and current_x > prev_x
        short_previous_evidence = previous_width_ratio <= 0.62
        alignment_break_evidence = overlap <= 0.28
        alignment_merge_evidence = overlap >= 0.68 and indent_ratio < 0.12
        primary_signal_count = sum(1 for value in (gap_evidence, indent_evidence, first_line_evidence) if value)
        supporting_signal_count = primary_signal_count + (1 if short_previous_evidence and gap_ratio >= 1.25 else 0) + (1 if alignment_break_evidence and gap_ratio >= 1.35 else 0)
        break_score = 0.0
        if gap_evidence:
            break_score += 0.45
        if indent_evidence:
            break_score += 0.35
        if first_line_evidence:
            break_score += 0.45
        if short_previous_evidence and gap_ratio >= 1.25:
            break_score += 0.2
        if alignment_break_evidence and gap_ratio >= 1.35:
            break_score += 0.1
        should_break = (
            supporting_signal_count >= 2
            and primary_signal_count >= 1
            and break_score >= 0.85
            and (gap_evidence or (first_line_evidence and gap_ratio >= 1.35))
            and not (alignment_merge_evidence and gap_ratio < 1.85)
        )
        logger.debug(
            "Flexible paragraph pair scope=%s pair=%s gap=%.5f gap_ratio=%.3f indent=%.5f "
            "indent_ratio=%.3f first_line_indent=%.5f width_ratio=%.3f overlap=%.3f "
            "signals=%s break_score=%.3f break=%s",
            debug_scope,
            line_index,
            gap,
            gap_ratio,
            indent_delta,
            indent_ratio,
            first_line_indent,
            previous_width_ratio,
            overlap,
            supporting_signal_count,
            break_score,
            should_break,
        )
        if should_break:
            groups.append(current)
            current = [line]
        else:
            current.append(line)
    if current:
        groups.append(current)

    paragraph_regions: List[Dict[str, Any]] = []
    image_width = int(lines[0].get("image_width") or 1) if lines else 1
    image_height = int(lines[0].get("image_height") or 1) if lines else 1
    for index, group in enumerate(groups, start=1):
        left = min(float(line["roi"].get("x_ratio") or 0.0) for line in group)
        top = min(float(line["roi"].get("y_ratio") or 0.0) for line in group)
        right = max(float(line["roi"].get("x_ratio") or 0.0) + float(line["roi"].get("width_ratio") or 0.0) for line in group)
        bottom = max(float(line["roi"].get("y_ratio") or 0.0) + float(line["roi"].get("height_ratio") or 0.0) for line in group)
        original_roi = {
            "x_ratio": max(0.0, left),
            "y_ratio": max(0.0, top),
            "width_ratio": max(0.0, min(1.0, right) - max(0.0, left)),
            "height_ratio": max(0.0, min(1.0, bottom) - max(0.0, top)),
        }
        expanded_roi = _expand_roi_ratio_by_auto_padding(original_roi, image_width, image_height)
        paragraph_regions.append(
            {
                "type": "text",
                "data_type": "text",
                "layout_type": "paragraph",
                "source": "flexible_paragraph_geometry",
                "confidence": min(1.0, sum(float(line.get("confidence") or 0.0) for line in group) / max(len(group), 1)),
                "roi": expanded_roi,
                "roi_expansion": {
                    "enabled": True,
                    "reason": "flexible_paragraph_auto_roi_padding",
                    "original_roi": original_roi,
                    "expanded_roi": expanded_roi,
                    "padding": {
                        "unit": "px",
                        "top": AUTO_ROI_EXPAND_TOP_PX,
                        "bottom": AUTO_ROI_EXPAND_BOTTOM_PX,
                        "left": AUTO_ROI_EXPAND_LEFT_PX,
                        "right": AUTO_ROI_EXPAND_RIGHT_PX,
                    },
                },
                "line_count": len(group),
                "paragraph_index": index,
            }
        )
    return paragraph_regions


def _roi_overlap_ratio(target: Dict[str, float], container: Dict[str, float]) -> float:
    return _roi_intersection_area(target, container) / max(_roi_area(target), 1e-9)


def _roi_area(roi: Dict[str, float]) -> float:
    return max(0.0, float(roi.get("width_ratio") or 0.0)) * max(0.0, float(roi.get("height_ratio") or 0.0))


def _roi_intersection_area(left: Dict[str, float], right: Dict[str, float]) -> float:
    left_x = float(left.get("x_ratio") or 0.0)
    left_y = float(left.get("y_ratio") or 0.0)
    left_right = left_x + float(left.get("width_ratio") or 0.0)
    left_bottom = left_y + float(left.get("height_ratio") or 0.0)
    right_x = float(right.get("x_ratio") or 0.0)
    right_y = float(right.get("y_ratio") or 0.0)
    right_right = right_x + float(right.get("width_ratio") or 0.0)
    right_bottom = right_y + float(right.get("height_ratio") or 0.0)
    width = max(0.0, min(left_right, right_right) - max(left_x, right_x))
    height = max(0.0, min(left_bottom, right_bottom) - max(left_y, right_y))
    return width * height


def _filter_nested_flexible_regions(regions: List[Dict[str, Any]], image_width: int, image_height: int) -> List[Dict[str, Any]]:
    prepared: List[Dict[str, Any]] = []
    for region in regions:
        roi = _region_roi_from_boundary(region, image_width, image_height)
        if not roi:
            continue
        data_type = _resolved_layout_region_type(region)
        prepared.append({"region": region, "roi": roi, "data_type": data_type, "area": _roi_area(roi)})

    kept: List[Dict[str, Any]] = []
    for item in sorted(prepared, key=lambda value: value["area"], reverse=True):
        if item["area"] <= 0:
            continue
        nested_in_existing = False
        for existing in kept:
            if existing["data_type"] != item["data_type"]:
                continue
            overlap = _roi_intersection_area(item["roi"], existing["roi"])
            item_overlap = overlap / max(item["area"], 1e-9)
            existing_overlap = overlap / max(existing["area"], 1e-9)
            if item["data_type"] in {"table", "image"}:
                if item_overlap >= 0.72 or existing_overlap >= 0.72:
                    nested_in_existing = True
                    break
                continue
            if item_overlap >= 0.88:
                nested_in_existing = True
                break
        if not nested_in_existing:
            kept.append(item)

    kept_regions = [item["region"] for item in kept]
    kept_regions.sort(key=lambda region: (
        float((_region_roi_from_boundary(region, image_width, image_height) or {}).get("y_ratio") or 0.0),
        float((_region_roi_from_boundary(region, image_width, image_height) or {}).get("x_ratio") or 0.0),
    ))
    return kept_regions


def _build_flexible_paragraph_regions(boundary_image_path: str, opencv_img: Any, analysis: Dict[str, Any]) -> List[Dict[str, Any]]:
    image_height, image_width = opencv_img.shape[:2]
    layout_regions = [
        region
        for region in _layout_regions_from_analysis(analysis)
        if _is_supported_layout_region(region)
    ]
    non_text_regions = [region for region in layout_regions if _resolved_layout_region_type(region) != "text"]
    text_regions = [region for region in layout_regions if _resolved_layout_region_type(region) == "text"]
    blockers = [
        _region_roi_from_boundary(region, image_width, image_height)
        for region in non_text_regions
        if _resolved_layout_region_type(region) in {"table", "image"}
    ]
    blockers = [roi for roi in blockers if roi]
    line_regions = []
    for line in _text_line_regions_from_detection(boundary_image_path, image_width, image_height):
        line_roi = _region_roi_from_boundary(line, image_width, image_height)
        if not line_roi:
            continue
        line_area = _roi_area(line_roi)
        if any(_roi_intersection_area(line_roi, blocker) / max(line_area, 1e-9) >= 0.55 for blocker in blockers):
            continue
        line_regions.append(line)
    paragraph_regions: List[Dict[str, Any]] = []
    used_line_ids: set[int] = set()
    for text_region_index, text_region in enumerate(text_regions, start=1):
        text_roi = _region_roi_from_boundary(text_region, image_width, image_height)
        if not text_roi:
            continue
        region_lines = []
        for line in line_regions:
            line_roi = _region_roi_from_boundary(line, image_width, image_height)
            if not line_roi:
                continue
            if _roi_overlap_ratio(line_roi, text_roi) >= 0.55:
                region_lines.append(line)
                used_line_ids.add(id(line))
        paragraph_regions.extend(
            _paragraph_regions_from_text_lines(region_lines, debug_scope=f"text_region_{text_region_index}")
        )
    remaining_lines = [line for line in line_regions if id(line) not in used_line_ids]
    paragraph_regions.extend(_paragraph_regions_from_text_lines(remaining_lines, debug_scope="unscoped_text_region"))
    regions = paragraph_regions + non_text_regions if paragraph_regions else layout_regions
    if not regions:
        regions = [
            {
                "type": "text",
                "roi": {"x_ratio": 0.0, "y_ratio": 0.0, "width_ratio": 1.0, "height_ratio": 1.0},
                "source": "pp_doclayout_v3_search_boundary",
                "data_type": "text",
                "extraction_method": "paddle_thai_ocr",
            }
        ]
    return _filter_nested_flexible_regions(regions, image_width, image_height)


def _ocr_flexible_regions(boundary_image_path: str, regions: List[Dict[str, Any]], source: str) -> Dict[str, Any]:
    image = _load_image_source(boundary_image_path)
    image_width, image_height = image.size if image is not None else (1, 1)
    overlay_preview_data_url = None
    if image is not None:
        overlay = image.copy()
        try:
            from PIL import ImageDraw, ImageFont

            draw = ImageDraw.Draw(overlay)
            font = ImageFont.load_default()
            for index, region in enumerate(regions, start=1):
                roi = _region_roi_from_boundary(region, image_width, image_height)
                if not roi:
                    continue
                left = max(0, int(round(float(roi.get("x_ratio") or 0.0) * image_width)))
                top = max(0, int(round(float(roi.get("y_ratio") or 0.0) * image_height)))
                right = min(image_width, int(round((float(roi.get("x_ratio") or 0.0) + float(roi.get("width_ratio") or 0.0)) * image_width)))
                bottom = min(image_height, int(round((float(roi.get("y_ratio") or 0.0) + float(roi.get("height_ratio") or 0.0)) * image_height)))
                if right <= left or bottom <= top:
                    continue
                draw.rectangle((left, top, right, bottom), outline=(2, 132, 199), width=3)
                label = f"ROI {index}"
                label_bbox = draw.textbbox((left, top), label, font=font)
                label_width = label_bbox[2] - label_bbox[0] + 8
                label_height = label_bbox[3] - label_bbox[1] + 6
                draw.rectangle((left, max(0, top - label_height), left + label_width, top), fill=(2, 132, 199))
                draw.text((left + 4, max(0, top - label_height + 3)), label, fill=(255, 255, 255), font=font)
            buffer = io.BytesIO()
            overlay.save(buffer, format="PNG")
            overlay_preview_data_url = f"data:image/png;base64,{base64.b64encode(buffer.getvalue()).decode('ascii')}"
        except Exception:
            overlay_preview_data_url = None
    texts: List[str] = []
    confidences: List[float] = []
    segments: List[Dict[str, Any]] = []
    for index, region in enumerate(regions):
        roi = _region_roi_from_boundary(region, image_width, image_height)
        if not roi:
            continue
        data_type = _resolved_layout_region_type(region)
        if data_type == "table":
            roi = _expand_table_roi(roi, image_width, image_height)
        extraction_method = _extraction_method_for_resolved_type(data_type)
        block_roi = {
            "page_number": 1,
            "x_ratio": float(roi.get("x_ratio") or 0.0),
            "y_ratio": float(roi.get("y_ratio") or 0.0),
            "width_ratio": float(roi.get("width_ratio") or 0.0),
            "height_ratio": float(roi.get("height_ratio") or 0.0),
        }
        if block_roi["width_ratio"] <= 0 or block_roi["height_ratio"] <= 0:
            continue
        crop_preview_data_url = None
        if image is not None:
            left = max(0, int(round(block_roi["x_ratio"] * image_width)))
            top = max(0, int(round(block_roi["y_ratio"] * image_height)))
            right = min(image_width, int(round((block_roi["x_ratio"] + block_roi["width_ratio"]) * image_width)))
            bottom = min(image_height, int(round((block_roi["y_ratio"] + block_roi["height_ratio"]) * image_height)))
            if right > left and bottom > top:
                buffer = io.BytesIO()
                image.crop((left, top, right, bottom)).save(buffer, format="PNG")
                crop_preview_data_url = f"data:image/png;base64,{base64.b64encode(buffer.getvalue()).decode('ascii')}"
        table_rows = None
        table_structured = None
        table_html = None
        raw_segments = []
        try:
            if data_type == "image":
                text = "(image crop)"
                confidence = 1.0
            elif data_type == "table":
                ocr_result = ocr_rois(
                    boundary_image_path,
                    [
                        {
                            "id": f"flexible_block_{index}",
                            "roi": block_roi,
                            "data_type": "table",
                            "extraction_method": "table_recognition_v2",
                        }
                    ],
                ).get(f"flexible_block_{index}", {})
                text = normalize_ocr_text(ocr_result.get("text"))
                confidence = float(ocr_result.get("confidence") or 0.0)
                table_rows = ocr_result.get("table_rows")
                table_structured = ocr_result.get("table_structured")
                table_html = ocr_result.get("table_html")
                raw_segments = ocr_result.get("raw_segments") or ocr_result.get("segments") or []
            else:
                if image is None:
                    raise ValueError("boundary_image_unreadable")
                left = max(0, int(round(block_roi["x_ratio"] * image_width)))
                top = max(0, int(round(block_roi["y_ratio"] * image_height)))
                right = min(image_width, int(round((block_roi["x_ratio"] + block_roi["width_ratio"]) * image_width)))
                bottom = min(image_height, int(round((block_roi["y_ratio"] + block_roi["height_ratio"]) * image_height)))
                if right <= left or bottom <= top:
                    raise ValueError("resolved_text_roi_outside_boundary")
                try:
                    import numpy as np
                    import cv2
                except ImportError as error:
                    raise OcrUnavailableError("Text OCR requires numpy and OpenCV.") from error
                block_img = cv2.cvtColor(np.array(image.crop((left, top, right, bottom)).convert("RGB")), cv2.COLOR_RGB2BGR)
                ocr_result = recognize_text_roi(block_img)
                text = str(ocr_result.get("text") or "")
                confidence = float(ocr_result.get("confidence") or 0.0)
                raw_segments = ocr_result.get("raw_segments") or ocr_result.get("segments") or []
            error_message = None
        except Exception as error:
            text = ""
            confidence = 0.0
            error_message = str(error)
        if text:
            texts.append(text)
            confidences.append(confidence)
        segments.append(
            {
                "index": index,
                "text": text,
                "confidence": confidence,
                "roi": block_roi,
                "type": data_type,
                "data_type": data_type,
                "extraction_method": extraction_method,
                "layout_type": _layout_region_type(region) or data_type,
                "source": source,
                "crop_preview_data_url": crop_preview_data_url,
                "raw_segments": raw_segments,
                "table_rows": table_rows,
                "table_structured": table_structured,
                "table_html": table_html,
                "ocr_error": error_message,
            }
        )
    return {
        "text": "\n".join(texts),
        "confidence": sum(confidences) / len(confidences) if confidences else 0.0,
        "segments": segments,
        "raw_segments": segments,
        "overlay_preview_data_url": overlay_preview_data_url,
    }


def _flexible_text_ocr_from_boundary(boundary_image_path: Optional[str]) -> Dict[str, Any]:
    if not boundary_image_path:
        return {"text": "", "confidence": 0.0, "segments": [], "failure_reason": "boundary_crop_failed"}
    image = _load_image_source(boundary_image_path)
    opencv_img = _image_to_bgr_array(image) if image is not None else None
    if opencv_img is None:
        return {"text": "", "confidence": 0.0, "segments": [], "failure_reason": "boundary_image_unreadable"}

    analysis = analyze_layout(opencv_img, expand_text_rois=True, auto_roi_mode="text_line")
    regions = _build_flexible_paragraph_regions(boundary_image_path, opencv_img, analysis)

    result = _ocr_flexible_regions(boundary_image_path, regions, "flexible_paragraph_layout_blocks")
    attempts = [{"step": "flexible_roi_paragraph_blocks", "block_count": len(regions), "recognized_count": len(result["segments"])}]

    return {
        "text": result.get("text") or "",
        "confidence": float(result.get("confidence") or 0.0),
        "segments": result.get("segments") or [],
        "raw_segments": result.get("raw_segments") or result.get("segments") or [],
        "resolved_blocks": result.get("segments") or [],
        "flexible_overlay_preview_data_url": result.get("overlay_preview_data_url"),
        "attempts": attempts,
        "engine": "flexible_roi_text",
        "preprocessing": "flexible_roi_search_boundary_paragraph_blocks",
    }


def _save_prepublish_test_image(test_id: str, file_bytes: bytes, page_index: int = 1) -> Path:
    try:
        from PIL import Image
    except ImportError as error:
        raise HTTPException(status_code=400, detail="Image validation requires Pillow") from error
    if not file_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    try:
        image = Image.open(io.BytesIO(file_bytes))
        if image.mode != "RGB":
            image = image.convert("RGB")
    except Exception as error:
        raise HTTPException(status_code=400, detail="Uploaded file is not a valid image or PDF") from error

    output_dir = _storage_root() / "prepublish_detection_tests" / test_id
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"page_{page_index}.png"
    image.save(output_path, format="PNG")
    return output_path


def _convert_prepublish_test_pdf(test_id: str, pdf_bytes: bytes) -> List[Path]:
    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="Uploaded PDF is empty")
    try:
        import fitz
    except ImportError as error:
        raise HTTPException(status_code=501, detail="PDF testing requires PyMuPDF") from error

    output_dir = _storage_root() / "prepublish_detection_tests" / test_id
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        document = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as error:
        raise HTTPException(status_code=400, detail="Uploaded file is not a valid PDF") from error
    if document.page_count == 0:
        document.close()
        raise HTTPException(status_code=400, detail="Uploaded PDF has no pages")

    paths: List[Path] = []
    try:
        for index in range(document.page_count):
            page = document.load_page(index)
            pixmap = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0), alpha=False)
            output_path = output_dir / f"page_{index + 1}.png"
            pixmap.save(str(output_path))
            paths.append(output_path)
    finally:
        document.close()
    return paths


def _prepare_prepublish_test_pages(test_id: str, file_bytes: bytes) -> List[Path]:
    if file_bytes.lstrip().startswith(b"%PDF"):
        return _convert_prepublish_test_pdf(test_id, file_bytes)
    return [_save_prepublish_test_image(test_id, file_bytes, 1)]


def _normalize_prepublish_test_pages(test_id: str, page_paths: List[Path]) -> Dict[int, str]:
    output_dir = _storage_root() / "prepublish_detection_tests" / test_id / "normalized"
    output_dir.mkdir(parents=True, exist_ok=True)
    normalizer = ImageNormalizationService()
    normalized: Dict[int, str] = {}
    for index, page_path in enumerate(page_paths, start=1):
        output_path = output_dir / f"page_{index}_normalized.png"
        info = normalizer.normalize_document(str(page_path), str(output_path))
        normalized[index] = str(info.get("normalized_image_path") or output_path)
    return normalized


def _template_page_image_source(conn: Any, template_page_id: str) -> Optional[str]:
    row = conn.execute(
        "SELECT normalized_image_url, sample_image_url FROM template_pages WHERE id = ?",
        (template_page_id,),
    ).fetchone()
    if row is None:
        return None
    return row["normalized_image_url"] or row["sample_image_url"]


class PageSplitService:
    def create_document_pages(self, document_id: str, payload: DocumentUploadRequest) -> List[Dict[str, Any]]:
        source_pages = payload.pages or [
            {
                "page_number": 1,
                "original_image_url": payload.original_file_url,
                "normalized_image_url": None,
            }
        ]
        return [
            {
                "id": _stub_id("doc_page"),
                "document_id": document_id,
                "page_number": page.page_number if hasattr(page, "page_number") else page["page_number"],
                "original_image_url": page.original_image_url if hasattr(page, "original_image_url") else page["original_image_url"],
                "normalized_image_url": page.normalized_image_url if hasattr(page, "normalized_image_url") else page["normalized_image_url"],
                "status": "uploaded",
            }
            for page in source_pages
        ]


class ImageProcessingService:
    def normalize_pages(self, pages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [{**page, "status": "preprocessing_pending"} for page in pages]


class EmbeddingService:
    def _fetch_template_or_404(self, conn: Any, template_id: str) -> Any:
        template_row = conn.execute(
            """
            SELECT
                tv.id,
                tg.name,
                tg.document_type,
                tg.category,
                tv.status,
                tv.version_number AS version,
                tv.template_group_id,
                tv.version_number,
                tv.created_from_version_id AS base_template_id,
                tg.description,
                tv.detection_mode,
                tv.main_page_number,
                (
                    SELECT COUNT(*)
                    FROM template_pages tp
                    WHERE tp.template_version_id = tv.id
                ) AS page_count,
                tv.similarity_threshold,
                tv.final_confidence_threshold,
                tv.layout_weight,
                tv.text_anchor_weight,
                tv.image_anchor_weight,
                tv.created_at,
                tv.updated_at
            FROM template_versions tv
            JOIN template_groups tg ON tg.id = tv.template_group_id
            WHERE tv.id = ?
            """,
            (template_id,),
        ).fetchone()
        if template_row is None:
            raise HTTPException(status_code=404, detail="Template not found")
        return template_row

    def _job_with_template(self, conn: Any, job_id: str) -> Dict[str, Any]:
        job_row = conn.execute("SELECT * FROM publish_jobs WHERE id = ?", (job_id,)).fetchone()
        if job_row is None:
            raise HTTPException(status_code=404, detail="Publish job not found")

        template_row = self._fetch_template_or_404(conn, job_row["template_version_id"])
        return {
            "job": _embedding_job_row_to_api(job_row),
            "template": _template_row_to_api(template_row),
        }

    def create_embedding_job(self, template_id: str) -> Dict[str, Any]:
        job_id = _stub_id("emb_job")
        with _connect() as conn:
            template_row = self._fetch_template_or_404(conn, template_id)
            if template_row["status"] != "validated":
                raise HTTPException(
                    status_code=409,
                    detail="Template must be validated before creating a publish job",
                )

            conn.execute(
                """
                INSERT INTO publish_jobs (
                    id, template_version_id, status, step, requested_at, metadata_json
                )
                VALUES (?, ?, 'queued', 'layout_signature', CURRENT_TIMESTAMP, ?)
                """,
                (job_id, template_id, '{"source":"admin_template_test","mode":"layout_signature"}'),
            )
            conn.execute(
                """
                UPDATE template_versions
                SET status = 'embedding_pending', updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (template_id,),
            )
            conn.commit()
            return self._job_with_template(conn, job_id)

    def latest_embedding_job(self, template_id: str) -> Dict[str, Any]:
        with _connect() as conn:
            self._fetch_template_or_404(conn, template_id)
            job_row = conn.execute(
                """
                SELECT * FROM publish_jobs
                WHERE template_version_id = ?
                ORDER BY requested_at DESC, id DESC
                LIMIT 1
                """,
                (template_id,),
            ).fetchone()
        return {"template_id": template_id, "job": _embedding_job_row_to_api(job_row)}

    def complete_job_dev(self, job_id: str) -> Dict[str, Any]:
        with _connect() as conn:
            job_row = conn.execute("SELECT * FROM publish_jobs WHERE id = ?", (job_id,)).fetchone()
            if job_row is None:
                raise HTTPException(status_code=404, detail="Publish job not found")

            template_id = job_row["template_version_id"]
            self._fetch_template_or_404(conn, template_id)
            _refresh_template_layout_signatures(conn, template_id)
            generated_references = _refresh_template_layout_reference_signatures(conn, template_id)
            if not generated_references or any(item.get("status") != "generated" for item in generated_references):
                failed_pages = [item for item in generated_references if item.get("status") != "generated"]
                raise HTTPException(
                    status_code=409,
                    detail=f"Layout reference signature generation failed: {failed_pages or 'no layout references'}",
                )
            metadata = {
                "engine": "layout_signature",
                "version": "layout-signature-v1",
                "template_id": template_id,
                "page_count": len(generated_references),
                "layout_signature_pages": generated_references,
                "global_vector_store": "disabled",
                "image_anchor_verification": "siglip_image_category",
                "completed_by": "complete-dev",
            }
            conn.execute(
                """
                UPDATE publish_jobs
                SET status = 'completed',
                    completed_at = CURRENT_TIMESTAMP,
                    error_message = NULL,
                    metadata_json = ?
                WHERE id = ?
                """,
                (jsonb_dump({**metadata, "vector_id": f"layout_{template_id}"}), job_id),
            )
            conn.execute(
                """
                UPDATE template_versions
                SET status = 'active', published_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (template_id,),
            )
            conn.commit()
            return self._job_with_template(conn, job_id)

    def run_job_dev(self, job_id: str) -> Dict[str, Any]:
        with _connect() as conn:
            job_row = conn.execute("SELECT * FROM publish_jobs WHERE id = ?", (job_id,)).fetchone()
            if job_row is None:
                raise HTTPException(status_code=404, detail="Publish job not found")
            if job_row["status"] != "queued":
                raise HTTPException(status_code=409, detail="Publish job must be queued before it can run")

            template_id = job_row["template_version_id"]
            template_row = self._fetch_template_or_404(conn, template_id)
            conn.execute(
                """
                UPDATE publish_jobs
                SET status = 'running',
                    started_at = CURRENT_TIMESTAMP,
                    error_message = NULL
                WHERE id = ?
                """,
                (job_id,),
            )
            conn.commit()

        time.sleep(1)

        try:
            with _connect() as conn:
                _refresh_template_layout_signatures(conn, template_id)
                generated_references = _refresh_template_layout_reference_signatures(conn, template_id)
                if not generated_references or any(item.get("status") != "generated" for item in generated_references):
                    failed_pages = [item for item in generated_references if item.get("status") != "generated"]
                    raise RuntimeError(f"Layout reference signature generation failed: {failed_pages or 'no layout references'}")
                conn.commit()
            metadata = {
                "engine": "layout_signature",
                "version": "layout-signature-v1",
                "template_id": template_id,
                "page_count": len(generated_references),
                "layout_signature_pages": generated_references,
                "global_vector_store": "disabled",
                "image_anchor_verification": "siglip_image_category",
            }
            vector_id = f"layout_{template_id}"
        except (EmbeddingContextError, ValueError, RuntimeError) as error:
            error_message = str(error)
            with _connect() as conn:
                conn.execute(
                    """
                    UPDATE publish_jobs
                    SET status = 'failed',
                        completed_at = CURRENT_TIMESTAMP,
                        error_message = ?
                    WHERE id = ?
                    """,
                    (error_message, job_id),
                )
                conn.execute(
                    """
                    UPDATE template_versions
                    SET status = 'validated', updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (template_id,),
                )
                conn.commit()
                return self._job_with_template(conn, job_id)

        with _connect() as conn:
            conn.execute(
                """
                UPDATE publish_jobs
                SET status = 'completed',
                    completed_at = CURRENT_TIMESTAMP,
                    error_message = NULL,
                    metadata_json = ?
                WHERE id = ?
                """,
                (jsonb_dump({**metadata, "vector_id": vector_id}), job_id),
            )
            conn.execute(
                """
                UPDATE template_versions
                SET status = 'active', published_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (template_id,),
            )
            conn.commit()
            return self._job_with_template(conn, job_id)

    def fail_job_dev(self, job_id: str) -> Dict[str, Any]:
        with _connect() as conn:
            job_row = conn.execute("SELECT * FROM publish_jobs WHERE id = ?", (job_id,)).fetchone()
            if job_row is None:
                raise HTTPException(status_code=404, detail="Publish job not found")
            if job_row["status"] not in {"queued", "running"}:
                raise HTTPException(status_code=409, detail="Only queued or running publish jobs can fail in dev mode")

            template_id = job_row["template_version_id"]
            self._fetch_template_or_404(conn, template_id)
            conn.execute(
                """
                UPDATE publish_jobs
                SET status = 'failed',
                    completed_at = CURRENT_TIMESTAMP,
                    error_message = ?
                WHERE id = ?
                """,
                ("Embedding job failed in dev mode.", job_id),
            )
            conn.execute(
                """
                UPDATE template_versions
                SET status = 'validated', updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (template_id,),
            )
            conn.commit()
            return self._job_with_template(conn, job_id)

    def generate_for_template(self, template_id: str) -> Dict[str, Any]:
        with _connect() as conn:
            self._fetch_template_or_404(conn, template_id)
            pages = _refresh_template_layout_signatures(conn, template_id)
            conn.commit()
        return {
            "template_id": template_id,
            "status": "layout_signature_generated",
            "scope": "template",
            "pages": pages,
        }

    def generate_for_template_page(self, template_id: str, page_id: str) -> Dict[str, Any]:
        with _connect() as conn:
            self._fetch_template_or_404(conn, template_id)
            page_row = conn.execute(
                "SELECT * FROM template_pages WHERE id = ? AND template_version_id = ?",
                (page_id, template_id),
            ).fetchone()
            if page_row is None:
                raise HTTPException(status_code=404, detail="Template page not found")
            image_url = page_row["normalized_image_url"] or page_row["sample_image_url"]
            if not image_url:
                raise HTTPException(status_code=409, detail="Template page image is unavailable")
            signature = build_layout_signature(image_url)
            conn.execute(
                """
                UPDATE template_pages
                SET layout_signature_json = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND template_version_id = ?
                """,
                (signature_to_json(signature), page_id, template_id),
            )
            conn.commit()
        return {
            "template_id": template_id,
            "template_page_id": page_id,
            "status": "layout_signature_generated",
            "scope": "template_page",
            "layout_signature": signature,
        }


class OCRService:
    def ocr_custom_fields(self, document_id: str, payload: CustomOcrRequest) -> Dict[str, Any]:
        return {
            "document_id": document_id,
            "document_page_id": payload.document_page_id,
            "status": "custom_ocr_stubbed",
            "results": [
                {
                    "page_number": field.roi.page_number,
                    "field_name": field.field_name,
                    "display_label": field.display_label,
                    "ocr_text": None,
                    "ocr_confidence": None,
                    "roi": field.roi.model_dump(),
                }
                for field in payload.fields
            ],
        }


def _siglip_image_threshold(category: Optional[str] = None, default: float = 0.70) -> float:
    config = get_image_verification_category(category)
    return round(float(config.match_threshold), 4) if config else default


def _active_image_category_payloads() -> List[Dict[str, Any]]:
    return categories_to_runtime_payload(list_image_verification_categories(enabled_only=True))


def _image_category_api(value: Optional[str]) -> Dict[str, Any]:
    raw_value = str(value or "").strip()
    category = get_image_verification_category(raw_value)
    if category is None:
        return {
            "value": raw_value,
            "label": raw_value,
            "prompt": "",
            "match_threshold": 0.0,
            "margin_threshold": 0.0,
            "enabled": False,
            "error": "category_not_found" if raw_value else "category_missing",
        }
    item = category.to_api()
    if not category.enabled:
        item["error"] = "category_disabled"
    return item


def _image_category_values(value: Optional[str]) -> List[str]:
    raw = str(value or "").strip()
    if not raw:
        return []
    if raw.startswith("["):
        parsed = jsonb_load(raw)
        if isinstance(parsed, list):
            return [str(item or "").strip() for item in parsed if str(item or "").strip()]
        else:
            return [raw]
    return [raw]


def _image_category_display(values: List[str]) -> str:
    labels = []
    for value in values:
        info = _image_category_api(value)
        labels.append(str(info.get("label") or value))
    return ", ".join(labels)


def _resolve_image_category_id(conn: Any, value: Optional[str]) -> Optional[str]:
    values = _image_category_values(value)
    if not values:
        return None
    selected_value = values[0]
    ensure_image_verification_categories_table(conn)
    row = conn.execute(
        """
        SELECT id
        FROM image_verification_categories
        WHERE id = ? OR value = ?
        LIMIT 1
        """,
        (selected_value, selected_value),
    ).fetchone()
    if row is None:
        raise HTTPException(
            status_code=422,
            detail=f"Image verification category not found: {selected_value}",
        )
    return str(row["id"])


class VerificationService:
    FUZZY_THRESHOLD = 0.85
    DEFAULT_VERIFICATION_THRESHOLD = 0.70
    LOW_TEXT_SIMILARITY_GUARD = 0.25
    ZERO_WIDTH_CHARS = {
        "\u200b",
        "\u200c",
        "\u200d",
        "\ufeff",
    }

    def load_verification_fields(self, template_id: str) -> List[Dict[str, Any]]:
        with _connect() as conn:
            template_row = conn.execute("SELECT id FROM template_versions WHERE id = ?", (template_id,)).fetchone()
            if template_row is None:
                raise HTTPException(status_code=404, detail="Template not found")

            rows = conn.execute(
                """
                SELECT
                    va.id,
                    tp.template_version_id AS template_id,
                    va.template_page_id,
                    tp.page_number,
                    va.anchor_name AS field_name,
                    va.anchor_name AS display_label,
                    va.roi_x_ratio,
                    va.roi_y_ratio,
                    va.roi_width_ratio,
                    va.roi_height_ratio,
                    va.anchor_type AS data_type,
                    0 AS user_selectable,
                    0 AS default_selected,
                    1 AS use_for_verification,
                    va.expected_text,
                    va.match_type,
                    va.required AS required_for_verification,
                    'fixed_roi' AS extraction_method,
                    'fix' AS roi_mode,
                    NULL AS expected_content,
                    NULL AS anchor_text,
                    va.regex_pattern,
                    NULL AS roi_padding,
                    va.weight AS verification_weight,
                    COALESCE(ivc.value, va.image_category_id) AS image_category,
                    va.sort_order,
                    va.created_at,
                    va.updated_at
                FROM verification_anchors va
                JOIN template_pages tp ON tp.id = va.template_page_id
                LEFT JOIN image_verification_categories ivc ON ivc.id = va.image_category_id
                WHERE tp.template_version_id = ?
                ORDER BY tp.page_number ASC, va.sort_order ASC, va.created_at ASC
                """,
                (template_id,),
            ).fetchall()

        return [_template_field_row_to_api(row) for row in rows]

    def _normalize_text(self, value: Optional[str]) -> str:
        normalized = unicodedata.normalize("NFKC", value or "")
        for char in self.ZERO_WIDTH_CHARS:
            normalized = normalized.replace(char, "")  
        normalized = "".join(normalized.lower().split())
        return normalized

    def _normalize_for_similarity(self, value: Optional[str]) -> str:
        normalized = self._normalize_text(value)
        normalized = re.sub(r"[^\w]", "", normalized, flags=re.UNICODE)
        return normalized

    def _similarity(self, left: str, right: str) -> float:
        if not left and not right:
            return 1.0
        if not left or not right:
            return 0.0
        return SequenceMatcher(None, left, right).ratio()

    def _score_match(
        self,
        expected_text: Optional[str],
        actual_text: Optional[str],
        match_type: Optional[str],
        ocr_confidence: float,
        verification_threshold: Optional[float] = None,
    ) -> Dict[str, Any]:
        expected = self._normalize_text(expected_text)
        actual = self._normalize_text(actual_text)
        expected_for_similarity = self._normalize_for_similarity(expected_text)
        actual_for_similarity = self._normalize_for_similarity(actual_text)
        base_similarity = self._similarity(expected_for_similarity, actual_for_similarity)
        normalized_match_type = (match_type or "contains").strip().lower()
        threshold = verification_threshold or self.DEFAULT_VERIFICATION_THRESHOLD

        if not expected:
            text_similarity_score = 0.0
        elif not actual:
            text_similarity_score = 0.0
        elif normalized_match_type == "exact":
            threshold = max(threshold, 0.95)
            text_similarity_score = 1.0 if actual == expected else base_similarity
        elif normalized_match_type == "regex":
            try:
                text_similarity_score = 1.0 if re.search(expected, actual, flags=re.IGNORECASE) else 0.0
            except re.error:
                text_similarity_score = 0.0
        elif normalized_match_type == "fuzzy":
            text_similarity_score = base_similarity
        else:
            normalized_match_type = "contains"
            if expected in actual:
                text_similarity_score = 1.0
            elif actual in expected:
                length_ratio = len(actual_for_similarity) / max(len(expected_for_similarity), 1)
                if length_ratio >= 0.75:
                    text_similarity_score = max(base_similarity, 0.90)
                elif length_ratio >= 0.50:
                    text_similarity_score = max(base_similarity, 0.70)
                else:
                    text_similarity_score = base_similarity
            elif base_similarity >= 0.70:
                text_similarity_score = max(base_similarity, 0.75)
            else:
                text_similarity_score = base_similarity

        text_similarity_score = round(float(text_similarity_score), 4)
        if text_similarity_score < self.LOW_TEXT_SIMILARITY_GUARD:
            field_score = 0.0
            failure_reason = "low_text_similarity"
        else:
            field_score = round(text_similarity_score, 4)

        passed = field_score >= threshold
        if passed:
            failure_reason = "passed"
        elif text_similarity_score >= self.LOW_TEXT_SIMILARITY_GUARD:
            failure_reason = "below_threshold"

        return {
            "match_type": normalized_match_type,
            "normalized_expected": expected,
            "normalized_actual": actual,
            "text_similarity_score": text_similarity_score,
            "text_match_score": field_score,
            "ocr_confidence": round(float(ocr_confidence or 0.0), 4),
            "field_score": field_score,
            "verification_threshold": round(float(threshold), 4),
            "score": field_score,
            "passed": passed,
            "failure_reason": failure_reason,
        }

    def _score_image_anchor(self, field: Dict[str, Any], image_path: str) -> Dict[str, Any]:
        crop_path = _storage_root() / "verification_query_anchor_crops" / field["template_id"] / f"{field['id']}_{uuid4().hex[:8]}.png"
        cropped = _crop_anchor_roi(image_path, field["roi"], crop_path, field.get("roi_padding") or 6)
        category_values = _image_category_values(field.get("image_category"))
        category_value = category_values[0] if category_values else ""
        active_categories = _active_image_category_payloads()
        category_infos = [_image_category_api(value) for value in category_values]
        valid_category_values = [
            value for value, info in zip(category_values, category_infos) if not info.get("error")
        ]
        category_info = category_infos[0] if category_infos else _image_category_api(category_value)
        category_error = category_info.get("error") if not valid_category_values else None
        if category_error:
            return {
                "score": 0.0,
                "field_score": 0.0,
                "evidence_score": 0.0,
                "passed": False,
                "status": "error",
                "failure_reason": category_error,
                "verification_threshold": category_info.get("match_threshold", 0.0),
                "margin_threshold": category_info.get("margin_threshold", 0.0),
                "image_category": ", ".join(category_values) or category_value,
                "image_category_label": _image_category_display(category_values) or category_info.get("label") or category_value,
                "image_category_prompt": " | ".join(str(info.get("prompt") or "") for info in category_infos if info.get("prompt")),
                "predicted_image_category": "",
                "predicted_image_category_label": "",
                "predicted_image_category_prompt": "",
                "reference_crop_preview_data_url": None,
                "current_crop_preview_data_url": _image_path_to_data_url(cropped) if cropped else None,
                "siglip_similarity_score": 0.0,
                "image_category_score": 0.0,
                "raw_logit": 0.0,
                "raw_pair_score": 0.0,
                "relative_percentage": 0.0,
                "siglip_target_rank": 0,
                "siglip_score_margin": 0.0,
                "siglip_labels": [],
                "siglip_ui_percentages": [],
            }
        if not cropped:
            return {
                "score": 0.0,
                "field_score": 0.0,
                "evidence_score": 0.0,
                "passed": False,
                "status": "error",
                "failure_reason": "roi_crop_failed",
                "image_category": ", ".join(category_values) or category_value,
                "image_category_label": _image_category_display(category_values) or category_info.get("label") or category_value,
                "image_category_prompt": " | ".join(str(info.get("prompt") or "") for info in category_infos if info.get("prompt")),
                "reference_crop_preview_data_url": None,
                "current_crop_preview_data_url": None,
            }

        results = [verify_image_category(cropped, value, active_categories) for value in (valid_category_values or category_values)]
        result = next((item for item in results if item.passed), None) or (max(results, key=lambda item: float(item.evidence_score)) if results else verify_image_category(cropped, category_value, active_categories))
        score = round(float(result.evidence_score), 4)
        threshold = result.verification_threshold
        return {
            "score": score,
            "field_score": score,
            "evidence_score": score,
            "passed": result.passed,
            "status": result.status,
            "failure_reason": result.failure_reason,
            "verification_threshold": round(float(threshold), 4),
            "margin_threshold": round(float(result.margin_threshold), 4),
            "model_version": result.model_version,
            "scoring_version": result.scoring_version,
            "siglip_similarity_score": score,
            "image_category_score": score,
            "raw_logit": result.raw_logit,
            "raw_pair_score": result.raw_pair_score,
            "relative_percentage": result.relative_percentage,
            "image_category": result.image_category,
            "image_category_label": result.image_category_label,
            "image_category_prompt": result.prompt,
            "predicted_image_category": result.predicted_category,
            "predicted_image_category_label": result.predicted_label,
            "predicted_image_category_prompt": result.predicted_prompt,
            "siglip_target_rank": result.target_rank,
            "siglip_score_margin": result.score_margin,
            "siglip_labels": result.labels,
            "siglip_ui_percentages": result.ui_percentages,
            "reference_crop_preview_data_url": None,
            "current_crop_preview_data_url": _image_path_to_data_url(cropped),
            "model_name": result.model_name,
            "device": result.device,
        }

    def verify_template(self, template_id: str, page_image_paths: Optional[Dict[int, str]] = None) -> Dict[str, Any]:
        fields = self.load_verification_fields(template_id)
        if not fields:
            return {
                "template_id": template_id,
                "status": "no_verification_fields",
                "passed": True,
                "score": 1.0,
                "required_passed": True,
                "checked_fields": [],
            }

        text_ocr_cache: Dict[str, Dict[str, Any]] = {}
        text_ocr_errors: Dict[str, str] = {}
        text_fields_by_page: Dict[int, List[Dict[str, Any]]] = {}
        for field in fields:
            if field.get("data_type") == "image":
                continue
            page_number = int(field["page_number"])
            image_path = (page_image_paths or {}).get(page_number)
            if image_path:
                text_fields_by_page.setdefault(page_number, []).append(field)

        for page_number, page_fields in text_fields_by_page.items():
            image_path = (page_image_paths or {}).get(page_number)
            if not image_path:
                continue
            try:
                page_results = ocr_rois(
                    image_path,
                    [{"id": field["id"], "roi": field["roi"]} for field in page_fields],
                )
                text_ocr_cache.update(page_results)
            except OcrUnavailableError as error:
                for field in page_fields:
                    text_ocr_errors[field["id"]] = str(error)
            except Exception as error:
                for field in page_fields:
                    text_ocr_errors[field["id"]] = f"ROI OCR failed: {error}"

        checked_fields = []
        for field in fields:
            expected_text = field.get("expected_text")
            page_number = int(field["page_number"])
            image_path = (page_image_paths or {}).get(page_number)
            anchor_type = "image" if field.get("data_type") == "image" else "text"
            actual_text = ""
            ocr_confidence = 0.0
            field_error = None
            current_crop_preview_data_url = None

            if anchor_type == "image" and not image_path:
                category_info = _image_category_api(field.get("image_category"))
                checked_fields.append(
                    {
                        "field_id": field["id"],
                        "anchor_id": field["id"],
                        "field_name": field["field_name"],
                        "display_label": field["display_label"],
                        "anchor_type": "image",
                        "verification_method": "image_feature",
                        "page_number": page_number,
                        "expected_text": category_info.get("label") or field.get("image_category"),
                        "actual_text": "",
                        "normalized_expected": "",
                        "normalized_actual": "",
                        "text_similarity_score": None,
                        "ocr_confidence": None,
                        "field_score": 0.0,
                        "verification_threshold": category_info.get("match_threshold", 0.0),
                        "margin_threshold": category_info.get("margin_threshold", 0.0),
                        "match_type": "image_feature",
                        "required": bool(field["required_for_verification"]),
                        "passed": False,
                        "score": 0.0,
                        "failure_reason": "query_page_missing",
                        "roi": field["roi"],
                        "roi_padding": field.get("roi_padding") or 6,
                        "weight": float(field.get("verification_weight") or 1.0),
                        "image_category": field.get("image_category"),
                        "image_category_label": category_info.get("label") or field.get("image_category"),
                        "image_category_prompt": category_info.get("prompt") or "",
                        "reference_crop_preview_data_url": None,
                        "current_crop_preview_data_url": None,
                        "siglip_similarity_score": 0.0,
                        "image_category_score": 0.0,
                        "evidence_score": 0.0,
                        "raw_logit": 0.0,
                        "raw_pair_score": 0.0,
                        "relative_percentage": 0.0,
                        "siglip_target_rank": 0,
                        "siglip_score_margin": 0.0,
                        "siglip_labels": [],
                        "siglip_ui_percentages": [],
                        "error": f"No query page image available for page {page_number}",
                    }
                )
                continue

            if anchor_type == "image" and image_path:
                try:
                    image_match = self._score_image_anchor(field, image_path)
                except Exception as error:
                    category_values = _image_category_values(field.get("image_category"))
                    category_value = category_values[0] if category_values else ""
                    try:
                        category_info = _image_category_api(category_value)
                        category_label = _image_category_display(category_values)
                    except Exception:
                        category_info = {"label": category_value, "prompt": "", "match_threshold": 0.0, "margin_threshold": 0.0}
                        category_label = ", ".join(category_values)
                    fallback_crop_path = _storage_root() / "verification_query_anchor_crops" / field["template_id"] / f"{field['id']}_failed_{uuid4().hex[:8]}.png"
                    fallback_crop = _crop_anchor_roi(image_path, field["roi"], fallback_crop_path, field.get("roi_padding") or 6)
                    image_match = {
                        "score": 0.0,
                        "field_score": 0.0,
                        "evidence_score": 0.0,
                        "passed": False,
                        "status": "error",
                        "failure_reason": f"image_verification_error: {error}",
                        "verification_threshold": category_info.get("match_threshold", 0.0),
                        "margin_threshold": category_info.get("margin_threshold", 0.0),
                        "reference_crop_preview_data_url": None,
                        "current_crop_preview_data_url": _image_path_to_data_url(fallback_crop),
                        "siglip_similarity_score": 0.0,
                        "image_category_score": 0.0,
                        "raw_logit": 0.0,
                        "raw_pair_score": 0.0,
                        "relative_percentage": 0.0,
                        "image_category": ", ".join(category_values) or field.get("image_category"),
                        "image_category_label": category_label or category_info.get("label") or field.get("image_category"),
                        "image_category_prompt": category_info.get("prompt") or "",
                        "predicted_image_category": "",
                        "predicted_image_category_label": "",
                        "predicted_image_category_prompt": "",
                        "siglip_target_rank": 0,
                        "siglip_score_margin": 0.0,
                        "siglip_labels": [],
                        "siglip_ui_percentages": [],
                        "error": str(error),
                    }
                image_verification_threshold = image_match.get("verification_threshold")
                if image_verification_threshold is None:
                    try:
                        image_verification_threshold = _siglip_image_threshold(image_match.get("image_category"))
                    except Exception:
                        image_verification_threshold = 0.0
                checked_fields.append(
                    {
                        "field_id": field["id"],
                        "anchor_id": field["id"],
                        "field_name": field["field_name"],
                        "display_label": field["display_label"],
                        "anchor_type": "image",
                        "verification_method": "image_feature",
                        "page_number": page_number,
                        "expected_text": image_match.get("image_category_label"),
                        "actual_text": image_match.get("predicted_image_category_label", ""),
                        "normalized_expected": image_match.get("image_category_prompt", ""),
                        "normalized_actual": image_match.get("predicted_image_category_prompt", ""),
                        "text_similarity_score": None,
                        "ocr_confidence": None,
                        "field_score": image_match["score"],
                        "verification_threshold": image_verification_threshold,
                        "margin_threshold": image_match.get("margin_threshold"),
                        "match_type": "image_feature",
                        "required": bool(field["required_for_verification"]),
                        "passed": image_match["passed"],
                        "score": image_match["score"],
                        "failure_reason": image_match["failure_reason"],
                        "roi": field["roi"],
                        "roi_padding": field.get("roi_padding") or 6,
                        "weight": float(field.get("verification_weight") or 1.0),
                        "reference_crop_preview_data_url": image_match.get("reference_crop_preview_data_url"),
                        "current_crop_preview_data_url": image_match.get("current_crop_preview_data_url"),
                        "siglip_similarity_score": image_match.get("siglip_similarity_score", image_match["score"]),
                        "image_category_score": image_match.get("image_category_score", image_match["score"]),
                        "evidence_score": image_match.get("evidence_score", image_match["score"]),
                        "raw_logit": image_match.get("raw_logit"),
                        "raw_pair_score": image_match.get("raw_pair_score"),
                        "relative_percentage": image_match.get("relative_percentage"),
                        "status": image_match.get("status"),
                        "image_category": image_match.get("image_category"),
                        "image_category_label": image_match.get("image_category_label"),
                        "image_category_prompt": image_match.get("image_category_prompt"),
                        "predicted_image_category": image_match.get("predicted_image_category"),
                        "predicted_image_category_label": image_match.get("predicted_image_category_label"),
                        "predicted_image_category_prompt": image_match.get("predicted_image_category_prompt"),
                        "siglip_target_rank": image_match.get("siglip_target_rank"),
                        "siglip_score_margin": image_match.get("siglip_score_margin"),
                        "siglip_labels": image_match.get("siglip_labels"),
                        "siglip_ui_percentages": image_match.get("siglip_ui_percentages"),
                        "model_name": image_match.get("model_name"),
                        "device": image_match.get("device"),
                        "model_version": image_match.get("model_version"),
                        "scoring_version": image_match.get("scoring_version"),
                        "error": image_match.get("error"),
                    }
                )
                continue

            if image_path:
                try:
                    crop_path = _storage_root() / "template_verification_test_crops" / template_id / f"{field['id']}.png"
                    cropped = _crop_anchor_roi(image_path, field["roi"], crop_path, field.get("roi_padding") or 0)
                    current_crop_preview_data_url = _image_path_to_data_url(cropped)
                    if field["id"] in text_ocr_errors:
                        raise OcrUnavailableError(text_ocr_errors[field["id"]])
                    ocr_result = text_ocr_cache.get(field["id"])
                    if ocr_result is None:
                        ocr_result = ocr_roi(image_path, field["roi"])
                    actual_text = str(ocr_result.get("text") or "")
                    ocr_confidence = float(ocr_result.get("confidence") or 0.0)
                    if ocr_result.get("error"):
                        field_error = str(ocr_result.get("error"))
                except OcrUnavailableError as error:
                    field_error = str(error)
                except Exception as error:
                    field_error = f"ROI OCR failed: {error}"
            else:
                field_error = f"No query page image available for page {page_number}"

            verification_threshold = self.DEFAULT_VERIFICATION_THRESHOLD
            match = self._score_match(
                expected_text,
                actual_text,
                field.get("match_type"),
                ocr_confidence,
                verification_threshold,
            ) if not field_error else {
                "match_type": (field.get("match_type") or "contains").strip().lower(),
                "normalized_expected": self._normalize_text(expected_text),
                "normalized_actual": self._normalize_text(actual_text),
                "text_similarity_score": 0.0,
                "text_match_score": 0.0,
                "ocr_confidence": round(float(ocr_confidence or 0.0), 4),
                "field_score": 0.0,
                "verification_threshold": verification_threshold,
                "score": 0.0,
                "passed": False,
                "failure_reason": "ocr_error",
            }
            checked_fields.append(
                {
                    "field_id": field["id"],
                    "anchor_id": field["id"],
                    "field_name": field["field_name"],
                    "display_label": field["display_label"],
                    "anchor_type": "text",
                    "verification_method": "ocr_text",
                    "page_number": page_number,
                    "expected_text": expected_text,
                    "actual_text": actual_text,
                    "normalized_expected": match["normalized_expected"],
                    "normalized_actual": match["normalized_actual"],
                    "text_similarity_score": match["text_similarity_score"],
                    "text_match_score": match.get("text_match_score", match["field_score"]),
                    "ocr_confidence": match["ocr_confidence"],
                    "field_score": match["field_score"],
                    "verification_threshold": match["verification_threshold"],
                    "match_type": match["match_type"],
                    "required": bool(field["required_for_verification"]),
                    "passed": match["passed"],
                    "score": match["field_score"],
                    "failure_reason": match["failure_reason"],
                    "roi": field["roi"],
                    "roi_padding": field.get("roi_padding") or 0,
                    "weight": float(field.get("verification_weight") or 1.0),
                    "reference_crop_preview_data_url": None,
                    "current_crop_preview_data_url": current_crop_preview_data_url,
                    "error": field_error,
                }
            )

        required_fields = [field for field in checked_fields if field["required"]]
        required_passed = all(field["passed"] for field in required_fields)
        score_weight = sum(max(0.0, float(field.get("weight") or 1.0)) for field in checked_fields) or 1.0
        score = sum(field["score"] * max(0.0, float(field.get("weight") or 1.0)) for field in checked_fields) / score_weight
        text_fields = [field for field in checked_fields if field.get("anchor_type") == "text"]
        image_fields = [field for field in checked_fields if field.get("anchor_type") == "image"]
        text_weight = sum(max(0.0, float(field.get("weight") or 1.0)) for field in text_fields) or 1.0
        image_weight = sum(max(0.0, float(field.get("weight") or 1.0)) for field in image_fields) or 1.0
        text_score = sum(field["score"] * max(0.0, float(field.get("weight") or 1.0)) for field in text_fields) / text_weight if text_fields else 1.0
        image_score = sum(field["score"] * max(0.0, float(field.get("weight") or 1.0)) for field in image_fields) / image_weight if image_fields else 1.0
        passed = required_passed
        ocr_unavailable = any(
            field.get("error")
            and (
                "OCR verification requires" in field["error"]
                or "Paddle" in field["error"]
                or "paddleocr" in field["error"].lower()
            )
            for field in checked_fields
        )
        return {
            "template_id": template_id,
            "status": "ocr_unavailable" if ocr_unavailable else "verified" if passed else "failed",
            "passed": passed,
            "score": round(float(score), 4),
            "text_anchor_score": round(float(text_score), 4),
            "image_anchor_score": round(float(image_score), 4),
            "required_passed": required_passed,
            "checked_fields": checked_fields,
            "verification_details": checked_fields,
        }

    def verify_candidate(
        self,
        document_page_id: Optional[str] = None,
        template_page_id: Optional[str] = None,
        template_id: Optional[str] = None,
        page_image_paths: Optional[Dict[int, str]] = None,
    ) -> Dict[str, Any]:
        if template_id:
            return {
                **self.verify_template(template_id, page_image_paths),
                "document_page_id": document_page_id,
                "template_page_id": template_page_id,
            }
        return {
            "document_page_id": document_page_id,
            "template_page_id": template_page_id,
            "verification_score": None,
            "status": "template_id_required",
            "passed": False,
        }


class ConfidenceService:
    def calculate_page_confidence(self, page_number: int) -> Dict[str, Any]:
        return {
            "page_number": page_number,
            "layout_score": None,
            "verification_score": None,
            "final_score": None,
            "status": "confidence_stubbed",
        }


class TemplateDetectionService:
    def __init__(self) -> None:
        self.confidence = ConfidenceService()

    def detect_document(self, document_id: str) -> Dict[str, Any]:
        return {
            "document_id": document_id,
            "status": "detection_stubbed",
            "pages": [],
            "logs": [],
        }

    def get_detection(self, document_id: str) -> Dict[str, Any]:
        return {
            "document_id": document_id,
            "status": "detection_not_run",
            "pages": [],
        }


class ExtractionService:
    def get_selectable_fields(self, document_id: str, page_id: Optional[str] = None) -> Dict[str, Any]:
        return {
            "document_id": document_id,
            "document_page_id": page_id,
            "fields": [],
            "grouped_by_page": True,
        }

    def extract_selected_fields(self, document_id: str, payload: ExtractionRequest) -> Dict[str, Any]:
        return {
            "document_id": document_id,
            "status": "extraction_stubbed",
            "results": [
                {
                    "page_number": field.page_number,
                    "template_field_id": field.template_field_id,
                    "ocr_text": None,
                    "ocr_confidence": None,
                }
                for field in payload.fields
            ],
        }

    def get_results(self, document_id: str) -> Dict[str, Any]:
        return {"document_id": document_id, "results": [], "grouped_by_page": True}


class DocumentService:
    def __init__(self) -> None:
        self.page_split = PageSplitService()
        self.image_processing = ImageProcessingService()

    def upload(self, payload: DocumentUploadRequest) -> Dict[str, Any]:
        document_id = _stub_id("doc")
        pages = self.page_split.create_document_pages(document_id, payload)
        return {
            "id": document_id,
            "uploaded_by": payload.uploaded_by,
            "original_file_url": payload.original_file_url,
            "status": "uploaded",
            "page_count": len(pages),
            "pages": self.image_processing.normalize_pages(pages),
            "created_at": _now(),
        }

    def get_document(self, document_id: str) -> Dict[str, Any]:
        return {"id": document_id, "status": "stubbed", "pages": []}

    def get_pages(self, document_id: str) -> Dict[str, Any]:
        return {"document_id": document_id, "pages": []}

    def get_page(self, document_id: str, page_id: str) -> Dict[str, Any]:
        return {"document_id": document_id, "id": page_id, "page_number": None, "status": "stubbed"}


class StorageMaintenanceService:
    GENERATED_DIRS = [
        Path(__file__).resolve().parents[1] / "cropped_rois",
        Path(__file__).resolve().parents[1] / "storage" / "detection_queries",
        _storage_root() / "prepublish_detection_tests",
        _storage_root() / "template_extraction_test_crops",
        _storage_root() / "verification_query_anchor_crops",
        _storage_root() / "prepublish_anchor_crops",
    ]

    def cleanup_generated_files(self, max_age_hours: int = 24, dry_run: bool = True) -> Dict[str, Any]:
        max_age_hours = max(1, int(max_age_hours or 24))
        cutoff = time.time() - (max_age_hours * 3600)
        candidates: List[Dict[str, Any]] = []
        deleted_count = 0
        deleted_bytes = 0

        for directory in self.GENERATED_DIRS:
            if not directory.exists() or not directory.is_dir():
                continue
            for path in directory.rglob("*"):
                if not path.is_file():
                    continue
                try:
                    stat = path.stat()
                except OSError:
                    continue
                if stat.st_mtime > cutoff:
                    continue

                item = {
                    "path": str(path),
                    "size_bytes": stat.st_size,
                    "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
                }
                candidates.append(item)
                if dry_run:
                    continue
                try:
                    path.unlink()
                    deleted_count += 1
                    deleted_bytes += stat.st_size
                except OSError as error:
                    item["error"] = str(error)

        return {
            "dry_run": dry_run,
            "max_age_hours": max_age_hours,
            "candidate_count": len(candidates),
            "candidate_bytes": sum(int(item["size_bytes"]) for item in candidates),
            "deleted_count": deleted_count,
            "deleted_bytes": deleted_bytes,
            "scanned_directories": [str(path) for path in self.GENERATED_DIRS],
            "candidates": candidates[:200],
            "truncated": len(candidates) > 200,
        }


class DecisionService:
    MIN_RETRIEVAL_SCORE = 0.50
    HIGH_RETRIEVAL_SCORE = 0.95
    STRONG_VERIFICATION_SCORE = 0.75
    DEFAULT_FINAL_CONFIDENCE_THRESHOLD = 0.75
    DEFAULT_LAYOUT_WEIGHT = 0.50
    DEFAULT_TEXT_ANCHOR_WEIGHT = 0.35
    DEFAULT_IMAGE_ANCHOR_WEIGHT = 0.15

    def _truthy(self, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value != 0
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "pass", "passed"}
        return bool(value)

    def _required_passed_from_fields(self, verification: Dict[str, Any], fallback: bool) -> bool:
        checked_fields = verification.get("checked_fields") or verification.get("verification_details") or []
        if not isinstance(checked_fields, list):
            return fallback
        required_fields = [
            field
            for field in checked_fields
            if isinstance(field, dict) and self._truthy(field.get("required"))
        ]
        if not required_fields:
            return True
        return all(self._truthy(field.get("passed")) for field in required_fields)

    def _required_failed_fields(self, verification: Dict[str, Any]) -> List[Dict[str, Any]]:
        checked_fields = verification.get("checked_fields") or verification.get("verification_details") or []
        if not isinstance(checked_fields, list):
            return []
        failed_fields: List[Dict[str, Any]] = []
        for field in checked_fields:
            if not isinstance(field, dict):
                continue
            if not self._truthy(field.get("required")):
                continue
            if self._truthy(field.get("passed")):
                continue
            failed_fields.append(
                {
                    "field_id": field.get("field_id") or field.get("anchor_id"),
                    "field_name": field.get("field_name") or field.get("anchor_name") or field.get("display_label"),
                    "display_label": field.get("display_label"),
                    "anchor_type": field.get("anchor_type"),
                    "page_number": field.get("page_number"),
                    "score": field.get("score") if field.get("score") is not None else field.get("field_score"),
                    "expected_text": field.get("expected_text"),
                    "actual_text": field.get("actual_text"),
                    "failure_reason": field.get("failure_reason") or field.get("error"),
                }
            )
        return failed_fields

    def final_confidence_threshold(self, template: Optional[Dict[str, Any]], metadata: Dict[str, Any]) -> float:
        raw_threshold = template.get("final_confidence_threshold") if template else metadata.get("final_confidence_threshold")
        try:
            threshold = float(raw_threshold)
        except (TypeError, ValueError):
            threshold = self.DEFAULT_FINAL_CONFIDENCE_THRESHOLD
        if threshold <= 0 or threshold > 1:
            return self.DEFAULT_FINAL_CONFIDENCE_THRESHOLD
        return threshold

    def matching_weights(self, template: Optional[Dict[str, Any]], metadata: Optional[Dict[str, Any]] = None) -> Dict[str, float]:
        metadata = metadata or {}

        def read_weight(key: str, fallback: float) -> float:
            raw_value = template.get(key) if template and template.get(key) is not None else metadata.get(key)
            try:
                value = float(raw_value)
            except (TypeError, ValueError):
                value = fallback
            return max(0.0, min(1.0, value))

        weights = {
            "layout": read_weight("layout_weight", self.DEFAULT_LAYOUT_WEIGHT),
            "text_anchor": read_weight("text_anchor_weight", self.DEFAULT_TEXT_ANCHOR_WEIGHT),
            "image_anchor": read_weight("image_anchor_weight", self.DEFAULT_IMAGE_ANCHOR_WEIGHT),
        }
        total = sum(weights.values())
        if total <= 0:
            return {
                "layout": self.DEFAULT_LAYOUT_WEIGHT,
                "text_anchor": self.DEFAULT_TEXT_ANCHOR_WEIGHT,
                "image_anchor": self.DEFAULT_IMAGE_ANCHOR_WEIGHT,
            }
        return {key: round(value / total, 4) for key, value in weights.items()}

    def _effective_matching_weights(self, configured_weights: Dict[str, float], verification: Dict[str, Any]) -> Dict[str, float]:
        checked_fields = verification.get("checked_fields") or verification.get("verification_details") or []
        has_text_anchor = any(isinstance(field, dict) and field.get("anchor_type") == "text" for field in checked_fields)
        has_image_anchor = any(isinstance(field, dict) and field.get("anchor_type") == "image" for field in checked_fields)
        weights = dict(configured_weights)
        if not has_text_anchor:
            weights["text_anchor"] = 0.0
        if not has_image_anchor:
            weights["image_anchor"] = 0.0
        total = sum(weights.values())
        if total <= 0:
            return {"layout": 1.0, "text_anchor": 0.0, "image_anchor": 0.0}
        return {key: round(value / total, 4) for key, value in weights.items()}

    def decide_candidate(
        self,
        retrieval_score: float,
        verification: Dict[str, Any],
        final_confidence_threshold: float,
        matching_weights: Optional[Dict[str, float]] = None,
    ) -> Dict[str, Any]:
        retrieval_score = round(float(retrieval_score), 4)
        verification_score = round(float(verification.get("score", 0.0) or 0.0), 4)
        text_anchor_score = round(float(verification.get("text_anchor_score", verification_score) or 0.0), 4)
        image_anchor_score = round(float(verification.get("image_anchor_score", 1.0) or 0.0), 4)
        verification_passed = self._truthy(verification.get("passed"))
        raw_required_passed = self._truthy(verification.get("required_passed", verification_passed))
        required_passed = self._required_passed_from_fields(verification, raw_required_passed)
        required_failed_fields = self._required_failed_fields(verification)
        verification_status = verification.get("status")
        configured_weights = matching_weights or self.matching_weights(None, {})
        effective_weights = self._effective_matching_weights(configured_weights, verification)
        anchor_weight = effective_weights["text_anchor"] + effective_weights["image_anchor"]
        anchor_score = round(
            (
                (text_anchor_score * effective_weights["text_anchor"]) +
                (image_anchor_score * effective_weights["image_anchor"])
            ) / anchor_weight,
            4,
        ) if anchor_weight > 0 else 0.0
        final_score = round(
            (retrieval_score * effective_weights["layout"]) +
            (text_anchor_score * effective_weights["text_anchor"]) +
            (image_anchor_score * effective_weights["image_anchor"]),
            4,
        )
        final_threshold_passed = final_score >= final_confidence_threshold
        layout_passed = retrieval_score >= self.MIN_RETRIEVAL_SCORE
        final_passed = final_threshold_passed and required_passed and layout_passed
        if not required_passed:
            decision_path = "required_verification_failed"
        elif not layout_passed:
            decision_path = "layout_score_below_threshold"
        elif not final_threshold_passed:
            decision_path = "final_threshold_failed"
        else:
            decision_path = "final_threshold_passed"

        return {
            "retrieval_score": retrieval_score,
            "verification_score": verification_score,
            "text_anchor_score": text_anchor_score,
            "image_anchor_score": image_anchor_score,
            "anchor_score": anchor_score,
            "matching_weights": configured_weights,
            "effective_matching_weights": effective_weights,
            "verification_passed": verification_passed,
            "final_score": round(float(final_score), 4),
            "final_passed": final_passed,
            "decision_reason": decision_path,
            "decision_path": decision_path,
            "final_confidence_threshold": final_confidence_threshold,
            "final_threshold_passed": final_threshold_passed,
            "layout_passed": layout_passed,
            "required_passed": required_passed,
            "required_failed_fields": required_failed_fields,
        }


class TemplateRequestService:
    def _normalize_review_status(self, value: Optional[str]) -> str:
        return value if value in {"pending", "approved", "rejected"} else "pending"

    def _requested_field_rows(self, conn: Any, request_id: str) -> List[Any]:
        return conn.execute(
            """
            SELECT
                rf.*,
                trp.template_request_id,
                trp.page_number
            FROM requested_fields rf
            JOIN template_request_pages trp ON trp.id = rf.template_request_page_id
            WHERE trp.template_request_id = ?
            ORDER BY trp.page_number ASC, rf.created_at ASC
            """,
            (request_id,),
        ).fetchall()

    def create(self, payload: TemplateRequestCreate) -> Dict[str, Any]:
        request_id = _stub_id("tpl_req")
        source_pages = payload.pages or (
            [
                {
                    "page_number": 1,
                    "page_name": "Page 1",
                    "sample_image_url": payload.sample_file_url,
                    "source_file_name": payload.request_title,
                }
            ]
            if payload.sample_file_url
            else []
        )
        with _connect() as conn:
            conn.execute(
                """
                INSERT INTO template_requests (
                    id, requested_by, request_title, document_type,
                    request_mode, status, user_note, created_at, reviewed_at
                )
                VALUES (?, ?, ?, ?, ?, 'draft', ?, CURRENT_TIMESTAMP, NULL)
                """,
                (
                    request_id,
                    payload.requested_by,
                    payload.request_title,
                    payload.document_type,
                    payload.request_mode,
                    payload.user_note,
                ),
            )
            for page in source_pages:
                page_number = page.page_number if hasattr(page, "page_number") else page.get("page_number", 1)
                sample_image_url = (
                    page.normalized_image_url or page.original_image_url
                    if hasattr(page, "normalized_image_url")
                    else page.get("normalized_image_url") or page.get("original_image_url") or page.get("sample_image_url")
                )
                source_file_name = (
                    page.source_file_name if hasattr(page, "source_file_name") else page.get("source_file_name")
                ) or payload.request_title
                source_file_id = (
                    page.source_file_id if hasattr(page, "source_file_id") else page.get("source_file_id")
                ) or source_file_name or request_id
                conn.execute(
                    """
                    INSERT INTO template_request_pages (
                        id, template_request_id, page_number, page_name, sample_image_url,
                        source_file_id, source_file_name, image_source, review_status,
                        is_canonical, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'user_request', 'pending', FALSE, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    """,
                    (_stub_id("tpl_req_page"), request_id, page_number, f"Page {page_number}", sample_image_url, source_file_id, source_file_name),
                )
            conn.commit()
        return self.get(request_id)

    def list(self) -> Dict[str, Any]:
        with _connect() as conn:
            request_rows = conn.execute("SELECT * FROM template_requests ORDER BY created_at DESC").fetchall()
            page_rows = conn.execute(
                """
                SELECT * FROM template_request_pages
                ORDER BY template_request_id ASC, page_number ASC
                """
            ).fetchall()
            field_rows = conn.execute(
                """
                SELECT rf.*, trp.template_request_id, trp.page_number
                FROM requested_fields rf
                JOIN template_request_pages trp ON trp.id = rf.template_request_page_id
                ORDER BY trp.template_request_id ASC, trp.page_number ASC, rf.created_at ASC
                """
            ).fetchall()

        pages_by_request: Dict[str, List[Dict[str, Any]]] = {}
        for page_row in page_rows:
            page = _page_row_to_api(page_row)
            pages_by_request.setdefault(page["template_request_id"], []).append(page)

        fields_by_request: Dict[str, List[Dict[str, Any]]] = {}
        for field_row in field_rows:
            field = _field_row_to_api(field_row)
            fields_by_request.setdefault(field["template_request_id"], []).append(field)

        requests = []
        for row in request_rows:
            request = _request_row_to_api(row)
            request["pages"] = pages_by_request.get(request["id"], [])
            request["requested_fields"] = fields_by_request.get(request["id"], [])
            requests.append(request)
        return {"template_requests": requests}

    def get(self, request_id: str) -> Dict[str, Any]:
        with _connect() as conn:
            request_row = conn.execute("SELECT * FROM template_requests WHERE id = ?", (request_id,)).fetchone()
            if request_row is None:
                return {"id": request_id, "status": "not_found", "pages": [], "requested_fields": []}
            page_rows = conn.execute(
                """
                SELECT * FROM template_request_pages
                WHERE template_request_id = ?
                ORDER BY page_number ASC
                """,
                (request_id,),
            ).fetchall()
            field_rows = self._requested_field_rows(conn, request_id)
        return {
            **_request_row_to_api(request_row),
            "pages": [_page_row_to_api(row) for row in page_rows],
            "requested_fields": [_field_row_to_api(row) for row in field_rows],
        }

    def update(self, request_id: str, payload: TemplateRequestUpdate) -> Dict[str, Any]:
        patch = payload.model_dump(exclude_unset=True)
        allowed_columns = {"request_title", "document_type", "request_mode", "status", "user_note", "admin_note"}
        column_values = {key: value for key, value in patch.items() if key in allowed_columns}
        if not column_values:
            return self.get(request_id)
        assignments = ", ".join(f"{column} = ?" for column in column_values)
        with _connect() as conn:
            current = conn.execute("SELECT id FROM template_requests WHERE id = ?", (request_id,)).fetchone()
            if current is None:
                raise HTTPException(status_code=404, detail="Template request not found.")
            conn.execute(
                f"UPDATE template_requests SET {assignments}, reviewed_at = CURRENT_TIMESTAMP WHERE id = ?",
                [*column_values.values(), request_id],
            )
            conn.commit()
        return self.get(request_id)

    def delete(self, request_id: str) -> Dict[str, Any]:
        with _connect() as conn:
            request_row = conn.execute("SELECT * FROM template_requests WHERE id = ?", (request_id,)).fetchone()
            if request_row is None:
                raise HTTPException(status_code=404, detail="Template request not found.")
            deleted_fields = conn.execute(
                """
                DELETE FROM requested_fields
                WHERE template_request_page_id IN (
                    SELECT id FROM template_request_pages WHERE template_request_id = ?
                )
                """,
                (request_id,),
            ).rowcount
            deleted_pages = conn.execute("DELETE FROM template_request_pages WHERE template_request_id = ?", (request_id,)).rowcount
            deleted_requests = conn.execute("DELETE FROM template_requests WHERE id = ?", (request_id,)).rowcount
            conn.commit()
        return {
            "id": request_id,
            "deleted": True,
            "converted_template_id": request_row.get("converted_template_version_id") if hasattr(request_row, "get") else None,
            "deleted_records": {
                "template_requests": deleted_requests,
                "template_request_pages": deleted_pages,
                "requested_fields": deleted_fields,
            },
        }

    def submit(self, request_id: str) -> Dict[str, Any]:
        with _connect() as conn:
            conn.execute(
                """
                UPDATE template_requests
                SET status = 'submitted', reviewed_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (request_id,),
            )
            conn.commit()
        return self.get(request_id)

    def pages(self, request_id: str) -> Dict[str, Any]:
        with _connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM template_request_pages
                WHERE template_request_id = ?
                ORDER BY page_number ASC
                """,
                (request_id,),
            ).fetchall()
        return {"template_request_id": request_id, "pages": [_page_row_to_api(row) for row in rows]}

    def add_image(self, request_id: str, payload: TemplateRequestImageCreate) -> Dict[str, Any]:
        image_id = _stub_id("tpl_req_page")
        review_status = self._normalize_review_status(payload.review_status)
        with _connect() as conn:
            request_row = conn.execute("SELECT * FROM template_requests WHERE id = ?", (request_id,)).fetchone()
            if request_row is None:
                raise HTTPException(status_code=404, detail="Template request not found.")
            max_page = conn.execute(
                "SELECT MAX(page_number) AS max_page_number FROM template_request_pages WHERE template_request_id = ?",
                (request_id,),
            ).fetchone()
            page_number = int(max_page["max_page_number"] if max_page and max_page["max_page_number"] else 0) + 1
            if payload.is_canonical:
                conn.execute(
                    "UPDATE template_request_pages SET is_canonical = FALSE, updated_at = CURRENT_TIMESTAMP WHERE template_request_id = ?",
                    (request_id,),
                )
            conn.execute(
                """
                INSERT INTO template_request_pages (
                    id, template_request_id, page_number, page_name, sample_image_url,
                    source_file_id, source_file_name, image_source, review_status,
                    is_canonical, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (
                    image_id,
                    request_id,
                    page_number,
                    f"Page {page_number}",
                    payload.sample_image_url,
                    payload.source_file_id or payload.source_file_name or image_id,
                    payload.source_file_name or f"Uploaded image {page_number}",
                    payload.image_source or "admin_upload",
                    review_status,
                    bool(payload.is_canonical),
                ),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM template_request_pages WHERE id = ?", (image_id,)).fetchone()
        return _page_row_to_api(row)

    def update_image(self, request_id: str, image_id: str, payload: TemplateRequestImageUpdate) -> Dict[str, Any]:
        patch = payload.model_dump(exclude_unset=True)
        column_values: Dict[str, Any] = {}
        if "sample_image_url" in patch:
            column_values["sample_image_url"] = patch["sample_image_url"]
        if "review_status" in patch:
            column_values["review_status"] = self._normalize_review_status(patch["review_status"])
        if "source_file_name" in patch:
            column_values["source_file_name"] = patch["source_file_name"]
        if "source_file_id" in patch:
            column_values["source_file_id"] = patch["source_file_id"]
        if "image_source" in patch:
            column_values["image_source"] = patch["image_source"] or "admin_upload"
        if "is_canonical" in patch:
            column_values["is_canonical"] = bool(patch["is_canonical"])
        if not column_values:
            return self.get(request_id)
        column_values["updated_at"] = _now()
        assignments = ", ".join(f"{column} = ?" for column in column_values)
        with _connect() as conn:
            row = conn.execute(
                "SELECT * FROM template_request_pages WHERE id = ? AND template_request_id = ?",
                (image_id, request_id),
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Template request image not found.")
            if column_values.get("is_canonical") is True:
                conn.execute(
                    "UPDATE template_request_pages SET is_canonical = FALSE, updated_at = CURRENT_TIMESTAMP WHERE template_request_id = ?",
                    (request_id,),
                )
            conn.execute(
                f"UPDATE template_request_pages SET {assignments} WHERE id = ? AND template_request_id = ?",
                [*column_values.values(), image_id, request_id],
            )
            conn.commit()
            updated = conn.execute("SELECT * FROM template_request_pages WHERE id = ?", (image_id,)).fetchone()
        return _page_row_to_api(updated)

    def delete_image(self, request_id: str, image_id: str) -> Dict[str, Any]:
        with _connect() as conn:
            row = conn.execute(
                "SELECT * FROM template_request_pages WHERE id = ? AND template_request_id = ?",
                (image_id, request_id),
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Template request image not found.")
            conn.execute("DELETE FROM requested_fields WHERE template_request_page_id = ?", (image_id,))
            conn.execute("DELETE FROM template_request_pages WHERE id = ? AND template_request_id = ?", (image_id, request_id))
            remaining = conn.execute(
                """
                SELECT id FROM template_request_pages
                WHERE template_request_id = ?
                ORDER BY page_number ASC
                """,
                (request_id,),
            ).fetchall()
            for index, page in enumerate(remaining, start=1):
                conn.execute("UPDATE template_request_pages SET page_number = ? WHERE id = ?", (index, page["id"]))
            conn.commit()
        return {"id": image_id, "template_request_id": request_id, "deleted": True}

    def add_requested_field(self, request_id: str, payload: RequestedFieldCreate) -> Dict[str, Any]:
        field_id = _stub_id("req_field")
        with _connect() as conn:
            page_row = conn.execute(
                """
                SELECT id FROM template_request_pages
                WHERE template_request_id = ? AND (id = ? OR page_number = ?)
                ORDER BY CASE WHEN id = ? THEN 0 ELSE 1 END
                LIMIT 1
                """,
                (request_id, payload.template_request_page_id, payload.page_number, payload.template_request_page_id),
            ).fetchone()
            if page_row is None:
                page_id = _stub_id("tpl_req_page")
                conn.execute(
                    """
                    INSERT INTO template_request_pages (
                        id, template_request_id, page_number, page_name, sample_image_url, created_at
                    )
                    VALUES (?, ?, ?, ?, NULL, CURRENT_TIMESTAMP)
                    """,
                    (page_id, request_id, payload.page_number, f"Page {payload.page_number}"),
                )
            else:
                page_id = page_row["id"]
            conn.execute(
                """
                INSERT INTO requested_fields (
                    id, template_request_page_id, field_name, display_label, data_type, extraction_method,
                    roi_x_ratio, roi_y_ratio, roi_width_ratio, roi_height_ratio, user_note, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    field_id,
                    page_id,
                    payload.field_name,
                    payload.display_label,
                    _normalize_data_type(payload.data_type),
                    _normalize_extraction_method(payload.extraction_method),
                    payload.roi.x_ratio,
                    payload.roi.y_ratio,
                    payload.roi.width_ratio,
                    payload.roi.height_ratio,
                    payload.user_note,
                ),
            )
            conn.commit()
            row = self._requested_field_rows(conn, request_id)
        selected = next((item for item in row if item["id"] == field_id), None)
        return _field_row_to_api(selected) if selected else {"id": field_id, "template_request_id": request_id, "status": "not_found"}

    def update_requested_field(self, request_id: str, field_id: str, payload: RequestedFieldUpdate) -> Dict[str, Any]:
        patch = payload.model_dump(exclude_unset=True)
        column_values: Dict[str, Any] = {}
        direct_columns = {
            "field_name": "field_name",
            "display_label": "display_label",
            "data_type": "data_type",
            "extraction_method": "extraction_method",
            "user_note": "user_note",
        }
        for key, column in direct_columns.items():
            if key in patch:
                value = patch[key]
                if key == "data_type":
                    value = _normalize_data_type(value)
                if key == "extraction_method":
                    value = _normalize_extraction_method(value)
                column_values[column] = value
        if payload.roi is not None:
            column_values.update({
                "roi_x_ratio": payload.roi.x_ratio,
                "roi_y_ratio": payload.roi.y_ratio,
                "roi_width_ratio": payload.roi.width_ratio,
                "roi_height_ratio": payload.roi.height_ratio,
            })
        with _connect() as conn:
            if column_values:
                set_clause = ", ".join(f"{column} = ?" for column in column_values)
                conn.execute(
                    f"""
                    UPDATE requested_fields
                    SET {set_clause}
                    WHERE id = ?
                      AND template_request_page_id IN (
                          SELECT id FROM template_request_pages WHERE template_request_id = ?
                      )
                    """,
                    [*column_values.values(), field_id, request_id],
                )
                conn.commit()
            rows = self._requested_field_rows(conn, request_id)
        selected = next((item for item in rows if item["id"] == field_id), None)
        if selected is None:
            return {"id": field_id, "template_request_id": request_id, "status": "not_found"}
        return _field_row_to_api(selected)

    def delete_requested_field(self, request_id: str, field_id: str) -> Dict[str, Any]:
        with _connect() as conn:
            deleted = conn.execute(
                """
                DELETE FROM requested_fields
                WHERE id = ?
                  AND template_request_page_id IN (
                      SELECT id FROM template_request_pages WHERE template_request_id = ?
                  )
                """,
                (field_id, request_id),
            ).rowcount
            conn.commit()
        return {"id": field_id, "template_request_id": request_id, "deleted": bool(deleted)}

    def reject(self, request_id: str, reason: Optional[str]) -> Dict[str, Any]:
        with _connect() as conn:
            conn.execute(
                """
                UPDATE template_requests
                SET status = 'rejected', admin_note = ?, reviewed_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (reason, request_id),
            )
            conn.commit()
        return self.get(request_id)


class AdminTemplateService:
    def _template_base_query(self) -> str:
        return """
            SELECT tv.id, tg.name, tg.document_type, tg.category, tv.status,
                   tv.version_number AS version, tv.template_group_id, tv.version_number,
                   tv.created_from_version_id AS base_template_id, tg.description,
                   'new_version' AS creation_type, tv.detection_mode, tv.main_page_number,
                   (SELECT COUNT(*) FROM template_pages tp WHERE tp.template_version_id = tv.id) AS page_count,
                   tv.similarity_threshold, tv.final_confidence_threshold,
                   tv.layout_weight, tv.text_anchor_weight, tv.image_anchor_weight,
                   NULL AS rejection_reason, tv.created_at, tv.updated_at
            FROM template_versions tv
            JOIN template_groups tg ON tg.id = tv.template_group_id
        """

    def dashboard(self) -> Dict[str, Any]:
        with _connect() as conn:
            template_status_rows = conn.execute("SELECT status, COUNT(*) AS count FROM template_versions GROUP BY status").fetchall()
            request_status_rows = conn.execute("SELECT status, COUNT(*) AS count FROM template_requests GROUP BY status").fetchall()
            latest_request_rows = conn.execute("SELECT * FROM template_requests ORDER BY COALESCE(reviewed_at, created_at) DESC, created_at DESC LIMIT 4").fetchall()
            latest_template_rows = conn.execute(f"{self._template_base_query()} ORDER BY tv.updated_at DESC, tv.created_at DESC LIMIT 4").fetchall()
        template_counts = {row["status"]: row["count"] for row in template_status_rows}
        request_counts = {row["status"]: row["count"] for row in request_status_rows}
        return {
            "template_count": sum(template_counts.values()),
            "pending_request_count": sum(request_counts.get(status, 0) for status in ("submitted", "in_review")),
            "draft_template_count": template_counts.get("draft", 0),
            "active_template_count": template_counts.get("active", 0),
            "rejected_request_count": request_counts.get("rejected", 0),
            "template_status_counts": template_counts,
            "request_status_counts": request_counts,
            "latest_requests": [_request_row_to_api(row) for row in latest_request_rows],
            "latest_templates": [_template_row_to_api(row) for row in latest_template_rows],
            "status": "live",
        }

    def create_template(self, payload: TemplateCreate) -> Dict[str, Any]:
        group_id = _stub_id("tgrp")
        template_id = _stub_id("tpl")
        code = _template_group_code()
        with _connect() as conn:
            conn.execute(
                """
                INSERT INTO template_groups (id, template_code, name, document_type, category, description, created_by, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (group_id, code, payload.document_type or payload.name, payload.document_type, payload.category, payload.description, payload.created_by),
            )
            conn.execute(
                """
                INSERT INTO template_versions (
                    id, template_group_id, version_number, version_name, status, detection_mode, main_page_number,
                    similarity_threshold, final_confidence_threshold, layout_weight, text_anchor_weight, image_anchor_weight,
                    created_by, created_at, updated_at
                )
                VALUES (?, ?, 1, ?, 'draft', ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (
                    template_id, group_id, payload.name,
                    _normalize_detection_mode(getattr(payload, "detection_mode", None)),
                    _normalize_main_page_number(getattr(payload, "main_page_number", None)),
                    payload.similarity_threshold, payload.final_confidence_threshold,
                    payload.layout_weight, payload.text_anchor_weight, payload.image_anchor_weight,
                    payload.created_by,
                ),
            )
            conn.commit()
        return self.get_template(template_id)

    def list_templates(self) -> Dict[str, Any]:
        with _connect() as conn:
            rows = conn.execute(f"{self._template_base_query()} ORDER BY tv.created_at DESC").fetchall()
            page_rows = conn.execute("SELECT * FROM template_pages ORDER BY template_version_id ASC, page_number ASC").fetchall()
        pages_by_template: Dict[str, List[Dict[str, Any]]] = {}
        for page_row in page_rows:
            page = _template_page_row_to_api(page_row)
            pages_by_template.setdefault(page["template_id"], []).append(page)
        templates = []
        for row in rows:
            template = _template_row_to_api(row)
            template["pages"] = pages_by_template.get(template["id"], [])
            templates.append(template)
        return {"templates": templates}

    def get_template(self, template_id: str) -> Dict[str, Any]:
        with _connect() as conn:
            template_row = conn.execute(f"{self._template_base_query()} WHERE tv.id = ?", (template_id,)).fetchone()
            if template_row is None:
                return {"id": template_id, "status": "not_found", "pages": [], "fields": [], "ignore_regions": [], "layout_references": []}
            page_rows = conn.execute("SELECT * FROM template_pages WHERE template_version_id = ? ORDER BY page_number ASC", (template_id,)).fetchall()
            extraction_rows = conn.execute(
                """
                SELECT tp.template_version_id AS template_id, ef.template_page_id, tp.page_number,
                       ef.id, ef.field_name, ef.display_label,
                       ef.roi_x_ratio, ef.roi_y_ratio, ef.roi_width_ratio, ef.roi_height_ratio,
                       ef.data_type, 1 AS user_selectable, 1 AS default_selected, 0 AS use_for_verification,
                       NULL AS expected_text, NULL AS match_type, 0 AS required_for_verification,
                       ef.extraction_method, ef.roi_mode, ef.expected_content,
                       NULL AS anchor_text, NULL AS regex_pattern, NULL AS roi_padding,
                       1.0 AS verification_weight, NULL AS image_category,
                       ef.sort_order, ef.created_at, ef.updated_at
                FROM extraction_fields ef
                JOIN template_pages tp ON tp.id = ef.template_page_id
                WHERE tp.template_version_id = ?
                ORDER BY tp.page_number ASC, ef.sort_order ASC, ef.created_at ASC
                """,
                (template_id,),
            ).fetchall()
            anchor_rows = conn.execute(
                """
                SELECT tp.template_version_id AS template_id, va.template_page_id, tp.page_number,
                       va.id, va.anchor_name AS field_name, va.anchor_name AS display_label,
                       va.roi_x_ratio, va.roi_y_ratio, va.roi_width_ratio, va.roi_height_ratio,
                       va.anchor_type AS data_type, 0 AS user_selectable, 0 AS default_selected, 1 AS use_for_verification,
                       va.expected_text, va.match_type, va.required AS required_for_verification,
                       'fixed_roi' AS extraction_method, 'fix' AS roi_mode, NULL AS expected_content,
                       NULL AS anchor_text, va.regex_pattern, NULL AS roi_padding,
                       va.weight AS verification_weight, COALESCE(ivc.value, va.image_category_id) AS image_category,
                       va.sort_order, va.created_at, va.updated_at
                FROM verification_anchors va
                JOIN template_pages tp ON tp.id = va.template_page_id
                LEFT JOIN image_verification_categories ivc ON ivc.id = va.image_category_id
                WHERE tp.template_version_id = ?
                ORDER BY tp.page_number ASC, va.sort_order ASC, va.created_at ASC
                """,
                (template_id,),
            ).fetchall()
            field_rows = sorted(
                [*extraction_rows, *anchor_rows],
                key=lambda row: (
                    int(row["page_number"] or 1),
                    int(row["sort_order"] or 0),
                    str(row["created_at"] or ""),
                ),
            )
            ignore_rows = conn.execute(
                """
                SELECT ir.*, tp.template_version_id AS template_id, tp.page_number
                FROM ignore_regions ir
                JOIN template_pages tp ON tp.id = ir.template_page_id
                WHERE tp.template_version_id = ?
                ORDER BY tp.page_number ASC, ir.created_at ASC
                """,
                (template_id,),
            ).fetchall()
        return {
            **_template_row_to_api(template_row),
            "pages": [_template_page_row_to_api(row) for row in page_rows],
            "fields": [_template_field_row_to_api(row) for row in field_rows],
            "ignore_regions": [_ignore_region_row_to_api(row) for row in ignore_rows],
            "layout_references": [],
        }

    def _template_page_image_paths(self, template_id: str, pages: List[Dict[str, Any]]) -> Dict[int, str]:
        output_dir = _storage_root() / "prepublish_template_pages" / template_id
        output_dir.mkdir(parents=True, exist_ok=True)
        paths: Dict[int, str] = {}
        for page in pages:
            source = page.get("normalized_image_url") or page.get("sample_image_url")
            image = _load_image_source(source)
            if image is None:
                continue
            page_number = int(page.get("page_number") or 1)
            output_path = output_dir / f"page_{page_number}.png"
            image.save(output_path, format="PNG")
            paths[page_number] = str(output_path)
        return paths

    def _template_id_from_vector_candidate(self, candidate: Dict[str, Any]) -> Optional[str]:
        template_id = (candidate.get("metadata") or {}).get("template_id") or (candidate.get("metadata") or {}).get("id")
        return str(template_id) if template_id else None

    def _layout_signature_for_page_paths(self, page_paths: Dict[int, str], page_number: int = 1) -> Dict[str, Any]:
        image_path = page_paths.get(page_number) or next(iter(page_paths.values()), None)
        signature = _generate_layout_signature_for_source(image_path)
        if signature is None:
            raise HTTPException(status_code=409, detail="Unable to generate layout signature for template matching")
        return signature

    def _layout_signatures_for_page_paths(self, page_paths: Dict[int, str]) -> Dict[int, Dict[str, Any]]:
        signatures = {int(page): self._layout_signature_for_page_paths(page_paths, int(page)) for page in sorted(page_paths)}
        if not signatures:
            raise HTTPException(status_code=409, detail="Unable to generate layout signatures for template matching")
        return signatures

    def _draft_layout_reference_match(self, template_id: str, query_signature: Dict[str, Any]) -> Dict[str, Any]:
        with _connect() as conn:
            _refresh_template_layout_signatures(conn, template_id)
            rows = conn.execute(
                """
                SELECT tp.id, tp.id AS template_page_id, tp.page_number,
                       COALESCE(tp.normalized_image_url, tp.sample_image_url) AS image_url,
                       'template_page' AS image_source,
                       CASE
                         WHEN tv.detection_mode = 'main_page' AND tp.page_number = tv.main_page_number THEN 1
                         WHEN tv.detection_mode != 'main_page' AND tp.page_number = 1 THEN 1
                         ELSE 0
                       END AS is_canonical,
                       tp.layout_signature_json
                FROM template_pages tp
                JOIN template_versions tv ON tv.id = tp.template_version_id
                WHERE tp.template_version_id = ? AND tp.layout_signature_json IS NOT NULL
                ORDER BY is_canonical DESC, tp.page_number ASC, tp.created_at ASC
                """,
                (template_id,),
            ).fetchall()
        best_score = 0.0
        best_reference = None
        compared = []
        for row in rows:
            signature = signature_from_json(row["layout_signature_json"])
            if not signature:
                continue
            comparison = compare_layout_signatures(query_signature, signature)
            score = float(comparison.get("score") or 0.0)
            reference = {
                "template_layout_reference_id": row["id"],
                "template_page_id": row["template_page_id"],
                "page_number": row["page_number"],
                "image_url": row["image_url"],
                "image_source": row["image_source"],
                "is_canonical": bool(row["is_canonical"]),
                "reference_role": "main" if row["is_canonical"] else "reference_only",
                "score": round(score, 4),
            }
            compared.append(reference)
            if best_reference is None or score > best_score:
                best_score = score
                best_reference = {**reference, "layout_debug": comparison}
        if best_reference is None:
            raise HTTPException(status_code=409, detail="Unable to compare draft layout references")
        return {"score": best_score, "best_reference": best_reference, "reference_count": len(compared), "references": compared}

    def _draft_layout_reference_matches_for_pages(self, template_id: str, query_signatures_by_page: Dict[int, Dict[str, Any]]) -> Dict[str, Any]:
        page_matches = []
        for page_number, query_signature in sorted(query_signatures_by_page.items()):
            match = self._draft_layout_reference_match(template_id, query_signature)
            same_page = [item for item in match["references"] if int(item.get("page_number") or 0) == int(page_number)]
            best = max(same_page, key=lambda item: float(item.get("score") or 0.0), default=None) or match["best_reference"]
            page_matches.append({"query_page_number": page_number, "template_page_number": best.get("page_number"), "score": float(best.get("score") or 0.0), "best_reference": best, "reference_count": match.get("reference_count", 0), "same_page_reference_count": len(same_page), "fallback_cross_page": not same_page})
        scores = [float(item.get("score") or 0.0) for item in page_matches]
        best_page = max(page_matches, key=lambda item: float(item.get("score") or 0.0), default=None)
        return {"score": sum(scores) / len(scores) if scores else 0.0, "best_reference": best_page.get("best_reference") if best_page else None, "reference_count": sum(int(item.get("reference_count") or 0) for item in page_matches), "page_matches": page_matches}

    def _search_layout_candidates_for_pages(self, query_signatures_by_page: Dict[int, Dict[str, Any]], template_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        grouped: Dict[str, Dict[str, Any]] = {}
        for page_number, query_signature in sorted(query_signatures_by_page.items()):
            candidates = search_layout_candidates(query_signature, page_number=page_number, limit=limit, include_template_id=template_id)
            for candidate in candidates:
                candidate_template_id = self._template_id_from_vector_candidate(candidate) or str(candidate.get("template_id") or candidate.get("id") or "")
                if not candidate_template_id:
                    continue
                metadata = candidate.get("metadata") or {}
                bucket = grouped.setdefault(
                    candidate_template_id,
                    {
                        **candidate,
                        "score": 0.0,
                        "matched_pages": 0,
                        "page_match_details": [],
                    },
                )
                detail = {
                    "query_page_number": page_number,
                    "template_page_number": metadata.get("matched_layout_reference_page_number") or metadata.get("page_number"),
                    "score": float(candidate.get("score") or 0.0),
                    "vector_id": candidate.get("vector_id"),
                    "metadata": metadata,
                }
                bucket["page_match_details"].append(detail)
                if float(candidate.get("score") or 0.0) > float(bucket.get("_best_score") or -1.0):
                    bucket.update({**candidate, "page_match_details": bucket["page_match_details"]})
                    bucket["_best_score"] = float(candidate.get("score") or 0.0)
        aggregated: List[Dict[str, Any]] = []
        for bucket in grouped.values():
            details = bucket.get("page_match_details") or []
            scores = [float(item.get("score") or 0.0) for item in details]
            bucket.pop("_best_score", None)
            bucket["matched_pages"] = len(details)
            bucket["score"] = round(sum(scores) / len(scores), 4) if scores else 0.0
            bucket["layout_score"] = bucket["score"]
            aggregated.append(bucket)
        return sorted(aggregated, key=lambda item: float(item.get("score") or 0.0), reverse=True)[:limit]

    def _align_query_pages_for_candidate(self, candidate_template: Dict[str, Any], query_page_paths: Dict[int, str], allow_alignment: bool = True) -> Dict[str, Any]:
        page_paths = dict(query_page_paths)
        alignments = [
            {
                "page_number": page_number,
                "alignment_status": "skipped" if not allow_alignment else "not_required",
                "verification_source_used": "original",
            }
            for page_number in sorted(page_paths)
        ]
        return {"page_paths": page_paths, "verification_page_paths": page_paths, "alignments": alignments}

    def run_prepublish_simulation(self, template_id: str) -> Dict[str, Any]:
        draft = self.get_template(template_id)
        if draft.get("status") == "not_found":
            raise HTTPException(status_code=404, detail="Template not found")
        with _connect() as conn:
            pages = _refresh_template_layout_signatures(conn, template_id)
            conn.commit()
        return {"template_id": template_id, "status": "completed", "passed": bool(pages), "layout_signature_pages": pages, "page_count": len(draft.get("pages") or []), "timestamp": _now()}

    def run_prepublish_detection_test(self, template_id: str, file_bytes: bytes) -> Dict[str, Any]:
        draft = self.get_template(template_id)
        if draft.get("status") == "not_found":
            raise HTTPException(status_code=404, detail="Template not found")
        from .detection_service import detect_template_dev

        detection = detect_template_dev(file_bytes, include_template_id=template_id)
        candidates = [
            {
                **candidate,
                "rank": index,
                "is_current_draft": candidate.get("template_id") == template_id,
                "source": "draft" if candidate.get("template_id") == template_id else "published",
                "source_label": "Draft Template" if candidate.get("template_id") == template_id else "Published Template",
            }
            for index, candidate in enumerate(detection.get("candidates") or [], start=1)
            if isinstance(candidate, dict)
        ]
        best_candidate = detection.get("best_candidate")
        if not best_candidate and candidates:
            best_candidate = max(
                candidates,
                key=lambda item: (
                    bool(item.get("final_passed")),
                    float(item.get("final_score") or item.get("score") or 0.0),
                    float(item.get("retrieval_score") or 0.0),
                ),
            )
        elif isinstance(best_candidate, dict):
            matching_candidate = next((candidate for candidate in candidates if candidate.get("template_id") == best_candidate.get("template_id")), None)
            if matching_candidate:
                best_candidate = matching_candidate

        draft_rank = next(
            (index for index, candidate in enumerate(candidates, start=1) if candidate.get("template_id") == template_id),
            None,
        )
        selected_template_id = (best_candidate or {}).get("template_id") if isinstance(best_candidate, dict) else None
        matched = bool(best_candidate)
        selected_passed_final_gate = bool((best_candidate or {}).get("final_passed")) if isinstance(best_candidate, dict) else False
        final_confidence = float((best_candidate or {}).get("final_score") or (best_candidate or {}).get("score") or 0.0) if isinstance(best_candidate, dict) else 0.0
        decision_reason = (
            (best_candidate or {}).get("decision_reason")
            or detection.get("message")
            or ("matched" if matched else "no_matching_template")
        ) if isinstance(best_candidate, dict) or detection.get("message") else "no_matching_template"

        return {
            "test_id": detection.get("query_id"),
            "template_id": template_id,
            "status": "completed",
            "matched": matched,
            "selected_template": best_candidate if matched else None,
            "selected_template_type": "draft" if selected_template_id == template_id else "published" if selected_template_id else None,
            "final_confidence": final_confidence,
            "decision_reason": decision_reason,
            "draft_template_rank": draft_rank,
            "passed": bool(selected_passed_final_gate and selected_template_id == template_id and draft_rank == 1),
            "warning": bool(matched and selected_template_id != template_id),
            "candidates": candidates,
            "separation_result": {
                "draft_template_rank": draft_rank,
                "draft_final_score": next(
                    (
                        float(candidate.get("final_score") or candidate.get("score") or 0.0)
                        for candidate in candidates
                        if candidate.get("template_id") == template_id
                    ),
                    0.0,
                ),
                "closest_published_template": selected_template_id if selected_template_id != template_id else None,
                "closest_published_score": final_confidence if selected_template_id and selected_template_id != template_id else None,
                "conflict_level": "none" if selected_template_id == template_id else "warning" if matched else "not_ready",
                "recommendation": "publish" if selected_template_id == template_id and draft_rank == 1 else "review_detection_result",
            },
            "debug": detection.get("debug") or {},
            "pages": detection.get("pages") or [],
        }

    def confirm_publish_template(self, template_id: str) -> Dict[str, Any]:
        template = self.get_template(template_id)
        if template.get("status") == "not_found":
            raise HTTPException(status_code=404, detail="Template not found")

        # 1. ให้ผ่านสถานะก่อนสร้าง publish job
        with _connect() as conn:
            conn.execute(
                """
                UPDATE template_versions
                SET status = 'validated',
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (template_id,),
            )
            conn.commit()

        embedding_service = EmbeddingService()

        # 2. สร้าง Publish Job จริง
        created = embedding_service.create_embedding_job(template_id)

        job = created.get("job") or {}
        job_id = job.get("id")
        if not job_id:
            raise HTTPException(
                status_code=500,
                detail="Publish job was not created",
            )

        # 3. รัน Publish Job ให้เสร็จทันที
        completed = embedding_service.run_job_dev(job_id)

        completed_job = completed.get("job") or {}
        completed_template = completed.get("template") or {}

        if (
            completed_job.get("status") != "completed"
            or completed_template.get("status") != "active"
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "Template publish did not complete successfully",
                    "job": completed_job,
                    "template": completed_template,
                },
            )

        # ถึงตรงนี้คือ Publish จริงและ active แล้ว
        return {
            "template_id": template_id,
            "status": "published",
            "job": completed_job,
            "template": completed_template,
        }

    def test_extraction_fields(self, template_id: str) -> Dict[str, Any]:
        template = self.get_template(template_id)
        if template.get("status") == "not_found":
            raise HTTPException(status_code=404, detail="Template not found")
        fields = [field for field in template.get("fields") or [] if not field.get("use_for_verification")]
        pages_by_number = {
            int(page.get("page_number") or 1): page
            for page in template.get("pages") or []
        }
        tested_fields: List[Dict[str, Any]] = []
        for field in fields:
            page_number = int(field.get("page_number") or (field.get("roi") or {}).get("page_number") or 1)
            page = pages_by_number.get(page_number)
            image_source = (page or {}).get("normalized_image_url") or (page or {}).get("sample_image_url")
            crop_image = _crop_template_field_image(image_source, field.get("roi") or {})
            crop_preview_data_url = _pil_image_to_data_url(crop_image)
            data_type = _normalize_data_type(field.get("data_type"))
            result_item: Dict[str, Any] = {
                **field,
                "field_id": field.get("id"),
                "field_name": field.get("field_name"),
                "display_label": field.get("display_label"),
                "page_number": page_number,
                "data_type": data_type,
                "crop_preview_data_url": crop_preview_data_url,
                "current_crop_preview_data_url": crop_preview_data_url,
                "status": "failed",
                "passed": False,
                "failure_reason": None,
            }
            if crop_image is None:
                result_item["failure_reason"] = "template_page_image_or_roi_unavailable"
                tested_fields.append(result_item)
                continue
            if data_type == "image":
                result_item.update(
                    {
                        "status": "completed",
                        "passed": True,
                        "actual_text": "",
                        "ocr_text": "",
                        "confidence": 1.0,
                    }
                )
                tested_fields.append(result_item)
                continue
            crop_bgr = _pil_image_to_bgr_array(crop_image)
            if crop_bgr is None:
                result_item["failure_reason"] = "crop_image_conversion_failed"
                tested_fields.append(result_item)
                continue
            try:
                if data_type == "table":
                    ocr_result = recognize_table_v2(crop_bgr)
                else:
                    ocr_result = recognize_text_roi(crop_bgr)
            except (OcrUnavailableError, TableRecognitionV2UnavailableError) as error:
                result_item["failure_reason"] = str(error)
                tested_fields.append(result_item)
                continue
            except Exception as error:
                result_item["failure_reason"] = str(error)
                tested_fields.append(result_item)
                continue

            text = str(ocr_result.get("text") or "").strip()
            table_rows = ocr_result.get("table_rows")
            table_structured = ocr_result.get("table_structured")
            has_table = data_type == "table" and (
                bool(table_rows)
                or bool((table_structured or {}).get("cells") if isinstance(table_structured, dict) else None)
            )
            passed = bool(text) or has_table
            result_item.update(
                {
                    "status": "completed" if passed else "failed",
                    "passed": passed,
                    "failure_reason": None if passed else "ocr_returned_empty_result",
                    "actual_text": text,
                    "ocr_text": text,
                    "confidence": float(ocr_result.get("confidence") or 0.0),
                    "raw_segments": ocr_result.get("raw_segments") or ocr_result.get("segments") or [],
                    "resolved_blocks": ocr_result.get("resolved_blocks") or [],
                    "table_rows": table_rows,
                    "table_structured": table_structured,
                    "table_sections": ocr_result.get("table_sections"),
                    "table_html": ocr_result.get("table_html"),
                    "table_debug": ocr_result.get("table_debug"),
                }
            )
            tested_fields.append(result_item)
        passed_count = sum(1 for item in tested_fields if item.get("passed"))
        return {
            "template_id": template_id,
            "status": "completed",
            "tested_count": len(tested_fields),
            "passed_count": passed_count,
            "failed_count": len(tested_fields) - passed_count,
            "fields": tested_fields,
        }

    def test_verification_anchors(self, template_id: str) -> Dict[str, Any]:
        template = self.get_template(template_id)
        page_paths = self._template_page_image_paths(template_id, template.get("pages") or [])
        verification = VerificationService().verify_template(template_id, page_paths)
        checked = verification.get("checked_fields", [])
        return {"template_id": template_id, "status": verification.get("status"), "passed": verification.get("passed"), "score": verification.get("score"), "tested_count": len(checked), "passed_count": sum(1 for item in checked if item.get("passed")), "failed_count": sum(1 for item in checked if not item.get("passed")), "anchors": checked}

    def update_template(self, template_id: str, payload: TemplateUpdate) -> Dict[str, Any]:
        patch = payload.model_dump(exclude_unset=True)
        version_columns = {"status", "similarity_threshold", "final_confidence_threshold", "layout_weight", "text_anchor_weight", "image_anchor_weight", "detection_mode", "main_page_number"}
        group_columns = {"name", "document_type", "category", "description"}
        with _connect() as conn:
            version_updates = []
            for key in version_columns:
                if key in patch:
                    value = _normalize_detection_mode(patch[key]) if key == "detection_mode" else (_normalize_main_page_number(patch[key]) if key == "main_page_number" else patch[key])
                    version_updates.append((key, value))
            if version_updates:
                conn.execute(f"UPDATE template_versions SET {', '.join(f'{c} = ?' for c, _ in version_updates)}, updated_at = CURRENT_TIMESTAMP WHERE id = ?", [*(v for _, v in version_updates), template_id])
            group_updates = [(key, patch[key]) for key in group_columns if key in patch]
            if group_updates:
                conn.execute(f"UPDATE template_groups SET {', '.join(f'{c} = ?' for c, _ in group_updates)}, updated_at = CURRENT_TIMESTAMP WHERE id = (SELECT template_group_id FROM template_versions WHERE id = ?)", [*(v for _, v in group_updates), template_id])
            conn.commit()
        return self.get_template(template_id)

    def delete_template(self, template_id: str) -> Dict[str, Any]:
        with _connect() as conn:
            if conn.execute("SELECT id FROM template_versions WHERE id = ?", (template_id,)).fetchone() is None:
                raise HTTPException(status_code=404, detail="Template not found")
            counts = {"publish_jobs": conn.execute("SELECT COUNT(*) AS count FROM publish_jobs WHERE template_version_id = ?", (template_id,)).fetchone()["count"], "template_versions": 1}
            conn.execute("UPDATE template_requests SET converted_template_version_id = NULL WHERE converted_template_version_id = ?", (template_id,))
            conn.execute("DELETE FROM publish_jobs WHERE template_version_id = ?", (template_id,))
            conn.execute("DELETE FROM template_versions WHERE id = ?", (template_id,))
            conn.commit()
        return {"id": template_id, "deleted": True, "deleted_records": counts}

    def list_template_pages(self, template_id: str) -> Dict[str, Any]:
        with _connect() as conn:
            rows = conn.execute("SELECT * FROM template_pages WHERE template_version_id = ? ORDER BY page_number ASC", (template_id,)).fetchall()
        return {"template_id": template_id, "pages": [_template_page_row_to_api(row) for row in rows]}

    def create_template_page(self, template_id: str, payload: TemplatePageCreate) -> Dict[str, Any]:
        with _connect() as conn:
            if conn.execute("SELECT id FROM template_versions WHERE id = ?", (template_id,)).fetchone() is None:
                raise HTTPException(status_code=404, detail="Template not found.")
            conn.execute("INSERT INTO template_pages (id, template_version_id, page_number, page_name, sample_image_url, normalized_image_url, layout_signature_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)", (_stub_id("tpl_page"), template_id, payload.page_number, payload.page_name, payload.sample_image_url, payload.normalized_image_url, payload.layout_signature_json))
            conn.commit()
        return self.get_template(template_id)

    def update_template_page(self, template_id: str, page_id: str, payload: TemplatePageUpdate) -> Dict[str, Any]:
        patch = payload.model_dump(exclude_unset=True)
        column_map = {"page_number": "page_number", "page_name": "page_name", "sample_image_url": "sample_image_url", "normalized_image_url": "normalized_image_url", "layout_signature_json": "layout_signature_json"}
        updates = [(column_map[key], value) for key, value in patch.items() if key in column_map]
        if updates:
            with _connect() as conn:
                conn.execute(f"UPDATE template_pages SET {', '.join(f'{c} = ?' for c, _ in updates)}, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND template_version_id = ?", [*(v for _, v in updates), page_id, template_id])
                conn.commit()
        return self.get_template(template_id)

    def delete_template_page(self, template_id: str, page_id: str) -> Dict[str, Any]:
        with _connect() as conn:
            conn.execute("DELETE FROM template_pages WHERE id = ? AND template_version_id = ?", (page_id, template_id))
            conn.commit()
        return self.get_template(template_id)

    def create_template_field(self, template_id: str, payload: TemplateFieldCreate) -> Dict[str, Any]:
        field_id = _stub_id("tpl_field")
        with _connect() as conn:
            page_row = conn.execute(
                "SELECT id FROM template_pages WHERE id = ? AND template_version_id = ?",
                (payload.template_page_id, template_id),
            ).fetchone()
            if page_row is None:
                raise HTTPException(status_code=404, detail="Template page not found.")
            if payload.use_for_verification:
                image_category_id = _resolve_image_category_id(conn, payload.image_category)
                conn.execute(
                    """
                    INSERT INTO verification_anchors (
                        id, template_page_id, anchor_name, anchor_type,
                        roi_x_ratio, roi_y_ratio, roi_width_ratio, roi_height_ratio,
                        required, weight, expected_text, match_type, regex_pattern,
                        image_category_id, sort_order, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    """,
                    (
                        field_id,
                        payload.template_page_id,
                        payload.field_name,
                        _normalize_data_type(payload.data_type),
                        payload.roi.x_ratio,
                        payload.roi.y_ratio,
                        payload.roi.width_ratio,
                        payload.roi.height_ratio,
                        bool(payload.required_for_verification),
                        payload.verification_weight or 1.0,
                        payload.expected_text,
                        payload.match_type,
                        payload.regex_pattern,
                        image_category_id,
                        payload.sort_order,
                    ),
                )
            else:
                conn.execute("INSERT INTO extraction_fields (id, template_page_id, field_name, display_label, data_type, extraction_method, roi_x_ratio, roi_y_ratio, roi_width_ratio, roi_height_ratio, roi_mode, expected_content, required, sort_order, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, FALSE, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)", (field_id, payload.template_page_id, payload.field_name, payload.display_label, _normalize_data_type(payload.data_type), _normalize_extraction_method(payload.extraction_method), payload.roi.x_ratio, payload.roi.y_ratio, payload.roi.width_ratio, payload.roi.height_ratio, _normalize_roi_mode(payload.roi_mode), _normalize_expected_content(payload.expected_content), payload.sort_order))
            conn.commit()
        return self.get_template(template_id)

    def _get_template_field_for_update(self, conn: Any, template_id: str, field_id: str) -> Optional[Dict[str, Any]]:
        row = conn.execute(
            """
            SELECT tp.template_version_id AS template_id, ef.template_page_id, tp.page_number,
                   ef.id, ef.field_name, ef.display_label,
                   ef.roi_x_ratio, ef.roi_y_ratio, ef.roi_width_ratio, ef.roi_height_ratio,
                   ef.data_type, 1 AS user_selectable, 1 AS default_selected, 0 AS use_for_verification,
                   NULL AS expected_text, NULL AS match_type, 0 AS required_for_verification,
                   ef.extraction_method, ef.roi_mode, ef.expected_content,
                   NULL AS anchor_text, NULL AS regex_pattern, NULL AS roi_padding,
                   1.0 AS verification_weight, NULL AS image_category,
                   ef.sort_order, ef.created_at, ef.updated_at
            FROM extraction_fields ef
            JOIN template_pages tp ON tp.id = ef.template_page_id
            WHERE tp.template_version_id = ? AND ef.id = ?
            """,
            (template_id, field_id),
        ).fetchone()
        if row is not None:
            return _template_field_row_to_api(row)
        row = conn.execute(
            """
            SELECT tp.template_version_id AS template_id, va.template_page_id, tp.page_number,
                   va.id, va.anchor_name AS field_name, va.anchor_name AS display_label,
                   va.roi_x_ratio, va.roi_y_ratio, va.roi_width_ratio, va.roi_height_ratio,
                   va.anchor_type AS data_type, 0 AS user_selectable, 0 AS default_selected, 1 AS use_for_verification,
                   va.expected_text, va.match_type, va.required AS required_for_verification,
                   'fixed_roi' AS extraction_method, 'fix' AS roi_mode, NULL AS expected_content,
                   NULL AS anchor_text, va.regex_pattern, NULL AS roi_padding,
                   va.weight AS verification_weight, COALESCE(ivc.value, va.image_category_id) AS image_category,
                   va.sort_order, va.created_at, va.updated_at
            FROM verification_anchors va
            JOIN template_pages tp ON tp.id = va.template_page_id
            LEFT JOIN image_verification_categories ivc ON ivc.id = va.image_category_id
            WHERE tp.template_version_id = ? AND va.id = ?
            """,
            (template_id, field_id),
        ).fetchone()
        return _template_field_row_to_api(row) if row is not None else None

    def update_template_field(self, template_id: str, field_id: str, payload: TemplateFieldUpdate) -> Dict[str, Any]:
        patch = payload.model_dump(exclude_unset=True)
        with _connect() as conn:
            current = self._get_template_field_for_update(conn, template_id, field_id)
        if current is None:
            raise HTTPException(status_code=404, detail="Template field not found.")
        roi = patch.get("roi") or current["roi"]
        merged = {
            "template_page_id": patch.get("template_page_id", current["template_page_id"]),
            "page_number": patch.get("page_number", current["page_number"]),
            "field_name": patch.get("field_name", current["field_name"]),
            "display_label": patch.get("display_label", current["display_label"]),
            "roi": roi,
            "data_type": patch.get("data_type", current["data_type"]),
            "user_selectable": patch.get("user_selectable", current["user_selectable"]),
            "default_selected": patch.get("default_selected", current["default_selected"]),
            "use_for_verification": patch.get("use_for_verification", current["use_for_verification"]),
            "expected_text": patch.get("expected_text", current.get("expected_text")),
            "match_type": patch.get("match_type", current.get("match_type")),
            "required_for_verification": patch.get("required_for_verification", current["required_for_verification"]),
            "extraction_method": patch.get("extraction_method", current["extraction_method"]),
            "roi_mode": patch.get("roi_mode", current.get("roi_mode") or "fix"),
            "expected_content": patch.get("expected_content", current.get("expected_content")),
            "anchor_text": patch.get("anchor_text", current.get("anchor_text")),
            "regex_pattern": patch.get("regex_pattern", current.get("regex_pattern")),
            "roi_padding": patch.get("roi_padding", current.get("roi_padding")),
            "verification_weight": patch.get("verification_weight", current.get("verification_weight")),
            "image_category": patch.get("image_category", current.get("image_category")),
            "sort_order": patch.get("sort_order", current["sort_order"]),
        }
        self.delete_template_field(template_id, field_id)
        return self.create_template_field(template_id, TemplateFieldCreate(**merged))

    def delete_template_field(self, template_id: str, field_id: str) -> Dict[str, Any]:
        with _connect() as conn:
            conn.execute("DELETE FROM extraction_fields WHERE id = ? AND template_page_id IN (SELECT id FROM template_pages WHERE template_version_id = ?)", (field_id, template_id))
            conn.execute("DELETE FROM verification_anchors WHERE id = ? AND template_page_id IN (SELECT id FROM template_pages WHERE template_version_id = ?)", (field_id, template_id))
            conn.commit()
        return self.get_template(template_id)

    def create_ignore_region(self, template_id: str, payload: IgnoreRegionCreate) -> Dict[str, Any]:
        with _connect() as conn:
            page_row = conn.execute(
                "SELECT id FROM template_pages WHERE id = ? AND template_version_id = ?",
                (payload.template_page_id, template_id),
            ).fetchone()
            if page_row is None:
                raise HTTPException(status_code=404, detail="Template page not found.")
            conn.execute("INSERT INTO ignore_regions (id, template_page_id, region_name, roi_x_ratio, roi_y_ratio, roi_width_ratio, roi_height_ratio, reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, CURRENT_TIMESTAMP)", (_stub_id("ignore_region"), payload.template_page_id, payload.field_name, payload.roi.x_ratio, payload.roi.y_ratio, payload.roi.width_ratio, payload.roi.height_ratio))
            conn.commit()
        return self.get_template(template_id)

    def update_ignore_region(self, template_id: str, region_id: str, payload: IgnoreRegionUpdate) -> Dict[str, Any]:
        patch = payload.model_dump(exclude_unset=True)
        with _connect() as conn:
            row = conn.execute(
                """
                SELECT ir.*, tp.template_version_id AS template_id, tp.page_number
                FROM ignore_regions ir
                JOIN template_pages tp ON tp.id = ir.template_page_id
                WHERE ir.id = ? AND tp.template_version_id = ?
                """,
                (region_id, template_id),
            ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Ignore region not found.")
        current = _ignore_region_row_to_api(row)
        merged = {
            "template_page_id": patch.get("template_page_id", current["template_page_id"]),
            "page_number": patch.get("page_number", current["page_number"]),
            "field_name": patch.get("field_name", current["field_name"]),
            "roi": patch.get("roi", current["roi"]),
        }
        self.delete_ignore_region(template_id, region_id)
        return self.create_ignore_region(template_id, IgnoreRegionCreate(**merged))

    def delete_ignore_region(self, template_id: str, region_id: str) -> Dict[str, Any]:
        with _connect() as conn:
            conn.execute("DELETE FROM ignore_regions WHERE id = ? AND template_page_id IN (SELECT id FROM template_pages WHERE template_version_id = ?)", (region_id, template_id))
            conn.commit()
        return self.get_template(template_id)

    def start_review(self, request_id: str) -> Dict[str, Any]:
        with _connect() as conn:
            conn.execute("UPDATE template_requests SET status = 'in_review', reviewed_at = CURRENT_TIMESTAMP WHERE id = ?", (request_id,))
            conn.commit()
        return TemplateRequestService().get(request_id)

    def suggest_base_version_for_request(self, request_id: str, template_id: str, similarity_threshold: float = 0.72) -> Dict[str, Any]:
        with _connect() as conn:
            selected = conn.execute("SELECT * FROM template_versions WHERE id = ?", (template_id,)).fetchone()
            if selected is None:
                raise HTTPException(status_code=404, detail="Template not found")
            version_count = conn.execute("SELECT COUNT(*) AS count FROM template_versions WHERE template_group_id = ?", (selected["template_group_id"],)).fetchone()["count"]
        return {"template_id": template_id, "template_group_id": selected["template_group_id"], "version_count": version_count, "suggested_base_version": None, "reuse_roi": False, "similarity_threshold": similarity_threshold}

    def create_version_from_request(
        self,
        request_id: str,
        payload: TemplateVersionFromRequestCreate,
        created_by: Optional[str] = None,
    ) -> Dict[str, Any]:
        with _connect() as conn:
            base = conn.execute("SELECT * FROM template_versions WHERE id = ?", (payload.base_template_id,)).fetchone()
            if base is None:
                raise HTTPException(status_code=404, detail="Base template version not found")
            pages = [_row_to_dict(row) for row in conn.execute("SELECT * FROM template_request_pages WHERE template_request_id = ? AND review_status IN ('approved', 'pending') AND sample_image_url IS NOT NULL ORDER BY page_number ASC", (request_id,)).fetchall()]
            if not pages:
                raise HTTPException(status_code=409, detail="At least one request page is required")
            next_version = int(conn.execute("SELECT COALESCE(MAX(version_number), 0) AS max_version FROM template_versions WHERE template_group_id = ?", (base["template_group_id"],)).fetchone()["max_version"]) + 1
            template_id = _stub_id("tpl")
            similarity_threshold = payload.similarity_threshold if payload.similarity_threshold is not None else base["similarity_threshold"]
            final_confidence_threshold = payload.final_confidence_threshold if payload.final_confidence_threshold is not None else base["final_confidence_threshold"]
            conn.execute("INSERT INTO template_versions (id, template_group_id, version_number, version_name, status, detection_mode, main_page_number, similarity_threshold, final_confidence_threshold, layout_weight, text_anchor_weight, image_anchor_weight, created_from_version_id, created_by, created_at, updated_at) VALUES (?, ?, ?, ?, 'draft', ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)", (template_id, base["template_group_id"], next_version, payload.version_name or f"Version {next_version}", _normalize_detection_mode(payload.detection_mode), _normalize_main_page_number(payload.main_page_number), similarity_threshold, final_confidence_threshold, base["layout_weight"], base["text_anchor_weight"], base["image_anchor_weight"], payload.base_template_id, created_by))
            for index, page in enumerate(pages, start=1):
                conn.execute("INSERT INTO template_pages (id, template_version_id, page_number, page_name, sample_image_url, normalized_image_url, layout_signature_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)", (_stub_id("tpl_page"), template_id, index, page.get("page_name") or f"Page {index}", page.get("sample_image_url"), page.get("sample_image_url")))
            conn.execute("UPDATE template_requests SET status = 'converted', converted_template_group_id = ?, converted_template_version_id = ?, reviewed_at = CURRENT_TIMESTAMP WHERE id = ?", (base["template_group_id"], template_id, request_id))
            conn.commit()
        return {"id": request_id, "status": "converted", "converted_template_id": template_id, "template_id": template_id, "template_group_id": base["template_group_id"], "created_records": {"template_versions": 1, "template_pages": len(pages)}}

def convert_request_to_template(
    self,
    request_id: str,
    payload: Optional[TemplateRequestConvert] = None,
    created_by: Optional[str] = None,
) -> Dict[str, Any]:
    with _connect() as conn:
        request_row = conn.execute(
            "SELECT * FROM template_requests WHERE id = ?", (request_id,)
        ).fetchone()
        if request_row is None:
            return {"id": request_id, "status": "not_found", "created_records": {}}

        pages = [
            _row_to_dict(row)
            for row in conn.execute(
                """
                SELECT * FROM template_request_pages
                WHERE template_request_id = ?
                  AND review_status IN ('approved', 'pending')
                  AND sample_image_url IS NOT NULL
                ORDER BY page_number ASC
                """,
                (request_id,),
            ).fetchall()
        ]
        if not pages:
            raise HTTPException(status_code=409, detail="At least one request page is required")

        group_id, template_id = _stub_id("tgrp"), _stub_id("tpl")
        template_name = (
            payload.template_name if payload and payload.template_name
            else request_row["request_title"]
        ).strip()
        similarity_threshold = (
            payload.similarity_threshold
            if payload and payload.similarity_threshold is not None else 0.75
        )

        conn.execute(
            """
            INSERT INTO template_groups
            (id, template_code, name, document_type, category, description, created_by, created_at, updated_at)
            VALUES (?, ?, ?, ?, NULL, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            (
                group_id, _template_group_code(), template_name,
                request_row["document_type"],
                payload.description if payload else None, created_by,
            ),
        )

        conn.execute(
            """
            INSERT INTO template_versions
            (id, template_group_id, version_number, version_name, status,
             detection_mode, main_page_number, similarity_threshold,
             final_confidence_threshold, layout_weight, text_anchor_weight,
             image_anchor_weight, created_by, created_at, updated_at)
            VALUES (?, ?, 1, ?, 'draft', ?, ?, ?, 0.75, 0.40, 0.30, 0.30, ?,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            (
                template_id, group_id, template_name,
                _normalize_detection_mode(payload.detection_mode if payload else None),
                _normalize_main_page_number(payload.main_page_number if payload else None),
                similarity_threshold, created_by,
            ),
        )

        # request page id -> template page id
        page_id_map: Dict[str, str] = {}
        for index, page in enumerate(pages, start=1):
            page_id = _stub_id("tpl_page")
            page_id_map[str(page["id"])] = page_id

            conn.execute(
                """
                INSERT INTO template_pages
                (id, template_version_id, page_number, page_name,
                 sample_image_url, normalized_image_url, layout_signature_json,
                 created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (
                    page_id, template_id, index,
                    page.get("page_name") or f"Page {index}",
                    page.get("sample_image_url"), page.get("sample_image_url"),
                ),
            )

        # Copy User ROI -> Extraction ROI
        fields = [
            _row_to_dict(row)
            for row in conn.execute(
                """
                SELECT rf.* FROM requested_fields rf
                JOIN template_request_pages rp ON rp.id = rf.template_request_page_id
                WHERE rp.template_request_id = ?
                ORDER BY rp.page_number, rf.created_at
                """,
                (request_id,),
            ).fetchall()
        ]

        field_count = 0
        page_orders: Dict[str, int] = {}

        for field in fields:
            page_id = page_id_map.get(str(field["template_request_page_id"]))
            if not page_id:
                continue

            page_orders[page_id] = page_orders.get(page_id, 0) + 1
            order = page_orders[page_id]

            data_type = str(field.get("data_type") or "text").lower()
            if data_type not in {"text", "table", "image"}:
                data_type = "text"

            conn.execute(
                """
                INSERT INTO extraction_fields
                (id, template_page_id, field_name, display_label, data_type,
                 extraction_method, roi_x_ratio, roi_y_ratio, roi_width_ratio,
                 roi_height_ratio, roi_mode, expected_content, required,
                 sort_order, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'fixed_roi', ?, ?, ?, ?, 'fix',
                        NULL, FALSE, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (
                    _stub_id("field"), page_id,
                    field.get("field_name") or f"field_{order}",
                    field.get("display_label") or field.get("field_name") or f"Field {order}",
                    data_type,
                    field["roi_x_ratio"], field["roi_y_ratio"],
                    field["roi_width_ratio"], field["roi_height_ratio"],
                    order,
                ),
            )
            field_count += 1

        conn.execute(
            """
            UPDATE template_requests
            SET status = 'converted',
                converted_template_group_id = ?,
                converted_template_version_id = ?,
                reviewed_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (group_id, template_id, request_id),
        )
        conn.commit()

    return {
        "id": request_id,
        "status": "converted",
        "converted_template_id": template_id,
        "template_id": template_id,
        "template_group_id": group_id,
        "created_records": {
            "template_groups": 1,
            "template_versions": 1,
            "template_pages": len(pages),
            "extraction_fields": field_count,
        },
    }
    
    def reject_request(self, request_id: str, reason: Optional[str]) -> Dict[str, Any]:
        return TemplateRequestService().reject(request_id, reason)

    def approve_template(self, template_id: str) -> Dict[str, Any]:
        with _connect() as conn:
            conn.execute("UPDATE template_versions SET status = 'active', published_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (template_id,))
            conn.commit()
        return self.get_template(template_id)

    def reject_template(self, template_id: str, reason: Optional[str]) -> Dict[str, Any]:
        with _connect() as conn:
            conn.execute("UPDATE template_versions SET status = 'rejected', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (template_id,))
            conn.commit()
        return {"id": template_id, "status": "rejected", "rejection_reason": reason}

    def test_template(self, template_id: str, payload: TemplateTestRequest) -> Dict[str, Any]:
        return {"template_id": template_id, "status": "test_mode_stubbed", "pages": [{"page_number": page.page_number, "layout_preview": None, "layout_overlay_preview": None, "top_k_candidates": [], "verification": None, "confidence": None} for page in payload.pages]}
