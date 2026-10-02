import io
import os
import base64
import json
import cv2
import re
import shutil
import time
import numpy as np
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from fastapi import HTTPException

from app.processing.alignment_service import AlignmentService
from app.processing.anchor_projection_service import AnchorProjectionService
from app.core.db import connect as connect_db
from app.processing.image_normalization import ImageNormalizationService
from app.model_runtime.layout_analysis_service import analyze_layout, analyze_layout_signature
from app.processing.layout_alignment_service import LayoutAlignmentService
from app.processing.layout_signature_service import build_layout_signature
from app.processing.layout_template_matcher import search_layout_candidates
from app.processing import published_template_cache
from app.processing.ocr_adapter import OcrUnavailableError, ocr_rois
from app.core.pipeline_core import get_pipeline_core_config
from app.business.services import (
    DecisionService,
    GlobalSettingsService,
    VerificationService,
    VERIFICATION_STRATEGY_STRICT,
    normalize_verification_strategy,
)


DETECTION_THRESHOLD = 0.50
PIPELINE_CONFIG = get_pipeline_core_config()
DETECTION_VERSION = PIPELINE_CONFIG.version
PDF_RENDER_SCALE = 2.0
DETECTION_TOP_K_LIMIT = 5
DETECTION_RETRIEVAL_LIMIT = DETECTION_TOP_K_LIMIT
USER_DETECTION_RETRIEVAL_LIMIT = 3
DETECTION_VERIFICATION_CANDIDATE_LIMIT = 3
DETECTION_FULL_EVAL_LIMIT = max(1, int(os.getenv("DETECTION_FULL_EVAL_LIMIT", str(DETECTION_RETRIEVAL_LIMIT))))
DETECTION_ALIGNMENT_LIMIT = max(0, int(os.getenv("DETECTION_ALIGNMENT_LIMIT", "1")))
SAVE_DEBUG_ARTIFACTS = os.getenv("SAVE_DEBUG_ARTIFACTS", "false").strip().lower() in {"1", "true", "yes", "on"}
DETECTION_COORDINATE_DEBUG = os.getenv("DETECTION_COORDINATE_DEBUG", "false").strip().lower() in {"1", "true", "yes", "on"}
LAYOUT_REFERENCE_CROP_ENABLED = os.getenv("LAYOUT_REFERENCE_CROP_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
LAYOUT_REFERENCE_CROP_MAX_INSET_RATIO = float(os.getenv("LAYOUT_REFERENCE_CROP_MAX_INSET_RATIO", "0.35"))
LAYOUT_REFERENCE_CROP_MIN_COVERAGE_RATIO = float(os.getenv("LAYOUT_REFERENCE_CROP_MIN_COVERAGE_RATIO", "0.55"))
LAYOUT_REFERENCE_CROP_MIN_DELTA_RATIO = float(os.getenv("LAYOUT_REFERENCE_CROP_MIN_DELTA_RATIO", "0.015"))
OLD_LAYOUT_CROP_PADDING_X_RATIO = 0.30
OLD_LAYOUT_CROP_PADDING_TOP_RATIO = 0.38
OLD_LAYOUT_CROP_PADDING_BOTTOM_RATIO = 0.38
OLD_LAYOUT_CROP_MIN_IMAGE_PADDING_RATIO = 0.015
OLD_LAYOUT_CROP_MAX_INSET_X_RATIO = 0.15
OLD_LAYOUT_CROP_MAX_INSET_Y_RATIO = 0.15
OLD_LAYOUT_CROP_MIN_WIDTH_RATIO = 0.70
OLD_LAYOUT_CROP_MIN_HEIGHT_RATIO = 0.70
LAYOUT_REFERENCE_PROJECTED_MIN_COVERAGE_RATIO = float(os.getenv("LAYOUT_REFERENCE_PROJECTED_MIN_COVERAGE_RATIO", "0.20"))
LAYOUT_REFERENCE_PROJECTED_MAX_COVERAGE_RATIO = float(os.getenv("LAYOUT_REFERENCE_PROJECTED_MAX_COVERAGE_RATIO", "0.98"))
LAYOUT_REFERENCE_PROJECTED_MAX_SCALE_RATIO = float(os.getenv("LAYOUT_REFERENCE_PROJECTED_MAX_SCALE_RATIO", "8.0"))
LAYOUT_REFERENCE_PROJECTED_MIN_SAFETY_MARGIN_RATIO = float(os.getenv("LAYOUT_REFERENCE_PROJECTED_MIN_SAFETY_MARGIN_RATIO", "0.005"))
LAYOUT_REFERENCE_PROJECTED_MAX_SAFETY_MARGIN_RATIO = float(os.getenv("LAYOUT_REFERENCE_PROJECTED_MAX_SAFETY_MARGIN_RATIO", "0.10"))
LAYOUT_REFERENCE_PROJECTED_TEMPLATE_MARGIN_WEIGHT = float(os.getenv("LAYOUT_REFERENCE_PROJECTED_TEMPLATE_MARGIN_WEIGHT", "0.50"))
GENERIC_LAYOUT_UNION_CROP_MARGIN_RATIO = float(os.getenv("GENERIC_LAYOUT_UNION_CROP_MARGIN_RATIO", "0.02"))
verification_service = VerificationService()
decision_service = DecisionService()
global_settings_service = GlobalSettingsService()
normalization_service = ImageNormalizationService()
alignment_service = AlignmentService()
layout_alignment_service = LayoutAlignmentService()
projection_service = AnchorProjectionService()


def _connect() -> Any:
    return connect_db()


class DetectionRequestCache:
    def __init__(self) -> None:
        self.templates: Dict[str, Optional[Dict[str, Any]]] = {}
        self.field_counts: Dict[str, Optional[int]] = {}
        self.verification_fields: Dict[str, List[Dict[str, Any]]] = {}
        self.verification_runtime: Dict[str, Dict[str, Any]] = {}
        self.stats: Dict[str, int] = {
            "template_cache_hits": 0,
            "template_db_fetches": 0,
            "field_count_cache_hits": 0,
            "field_count_db_fetches": 0,
            "verification_fields_cache_hits": 0,
            "verification_fields_db_fetches": 0,
            "published_template_cache_hits": 0,
            "published_field_count_cache_hits": 0,
            "published_verification_fields_cache_hits": 0,
            "verification_text_ocr_cache_hits": 0,
            "verification_text_ocr_cache_misses": 0,
            "verification_image_model_cache_hits": 0,
            "verification_image_model_cache_misses": 0,
        }


def _ms(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    return round(float(value) * 1000.0, 2)


def _timing_ms_map(timing: Optional[Dict[str, Any]]) -> Dict[str, Optional[float]]:
    if not isinstance(timing, dict):
        return {}
    result: Dict[str, Any] = {}
    for key, value in timing.items():
        if isinstance(value, (int, float)):
            if key.endswith(("_count", "_hits", "_misses", "_size")):
                result[key] = int(value)
            else:
                result[f"{key}_ms"] = _ms(float(value))
        elif isinstance(value, dict):
            result[key] = _timing_ms_map(value)
        elif isinstance(value, list):
            result[key] = [
                _timing_ms_map(item) if isinstance(item, dict) else item
                for item in value
            ]
    return result


def _debug_timing_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _debug_timing_value(nested) for key, nested in value.items()}
    if isinstance(value, list):
        return [_debug_timing_value(item) for item in value]
    return value


_TEMPLATE_MATCHING_TIMING_KEYS = {
    "connect",
    "pool_getconn",
    "ensure_schema",
    "pragma_foreign_keys",
    "image_category_schema_setup",
    "cursor_create",
    "execute",
    "fetch",
    "db_total",
    "processing",
    "total",
    "active_filter",
    "signature_parse",
    "layout_compare",
    "ignored_region_filter",
    "aspect",
    "prefilter_components",
    "prefilter_gate",
    "spatial",
    "final_score",
    "total",
    "candidate_build",
    "best_by_template_update",
    "sort",
    "top_k_slice",
    "include_template_append",
    "serialization",
}


def _template_matching_timing_ms(item: Dict[str, Any]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, value in item.items():
        if isinstance(value, dict):
            result[key] = _template_matching_timing_ms(value)
        elif key in _TEMPLATE_MATCHING_TIMING_KEYS and isinstance(value, (int, float)) and not isinstance(value, bool):
            result[f"{key}_ms"] = _ms(float(value))
        else:
            result[key] = value
    return result


def _template_matching_timing_ms_list(items: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    if not isinstance(items, list):
        return []
    return [_template_matching_timing_ms(item) for item in items if isinstance(item, dict)]


def _db_timing_ms(timing: Optional[Dict[str, Any]]) -> Dict[str, Optional[float]]:
    return _timing_ms_map(timing)


def _storage_path() -> Path:
    return Path(__file__).resolve().parents[2] / "storage" / "detection_queries"


def _load_pillow():
    try:
        from PIL import Image
    except ImportError:
        return None
    return Image


def _template_id_from_metadata(metadata: Dict[str, Any], vector_id: str) -> Optional[str]:
    if metadata.get("template_id"):
        return str(metadata["template_id"])
    if vector_id.startswith("vec_"):
        return vector_id.replace("vec_", "", 1)
    return None


def _fetch_template(template_id: Optional[str]) -> Optional[Dict[str, Any]]:
    template, _ = _fetch_template_with_db_timing(template_id)
    return template


def _fetch_template_with_db_timing(template_id: Optional[str]) -> tuple[Optional[Dict[str, Any]], Dict[str, float]]:
    if not template_id:
        return None, {}
    total_started = time.perf_counter()
    connect_started = time.perf_counter()
    conn = _connect()
    connect_elapsed = time.perf_counter() - connect_started
    with conn:
        cursor, execute_timing = conn.execute_timed(
            """
            SELECT
                tv.id,
                tg.name,
                tg.name AS template_group_name,
                tv.version_name,
                tg.document_type,
                tg.category,
                tv.status,
                tv.version_number AS version,
                tv.template_group_id,
                tv.version_number,
                tv.created_from_version_id AS base_template_id,
                tg.description,
                'new_version' AS creation_type,
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
                NULL AS rejection_reason,
                tv.created_at,
                tv.updated_at
            FROM template_versions tv
            JOIN template_groups tg ON tg.id = tv.template_group_id
            WHERE tv.id = ?
            """,
            (template_id,),
        )
        fetch_started = time.perf_counter()
        row = cursor.fetchone()
        fetch_elapsed = time.perf_counter() - fetch_started
        processing_started = time.perf_counter()
        template = dict(row) if row else None
        processing_elapsed = time.perf_counter() - processing_started
        timing = {
            "connect": connect_elapsed,
            "cursor_create": float(execute_timing.get("cursor_create") or 0.0),
            "execute": float(execute_timing.get("execute") or 0.0),
            "fetch": fetch_elapsed,
            "processing": processing_elapsed,
            "total_db_operation": time.perf_counter() - total_started,
        }
    return template, timing


def _fetch_template_cached(template_id: Optional[str], request_cache: Optional[DetectionRequestCache]) -> tuple[Optional[Dict[str, Any]], bool, Dict[str, float]]:
    if not template_id:
        return None, False, {}
    if request_cache is not None and template_id in request_cache.templates:
        request_cache.stats["template_cache_hits"] += 1
        return request_cache.templates[template_id], True, {}
    template = published_template_cache.get_template(template_id)
    if template is not None:
        if request_cache is not None:
            request_cache.templates[template_id] = template
            request_cache.stats["published_template_cache_hits"] += 1
        return template, True, {}
    template, db_timing = _fetch_template_with_db_timing(template_id)
    if request_cache is not None:
        request_cache.templates[template_id] = template
        request_cache.stats["template_db_fetches"] += 1
    return template, False, db_timing


def _fetch_field_count_cached(template_id: str, request_cache: Optional[DetectionRequestCache]) -> tuple[Optional[int], bool, Dict[str, float]]:
    if request_cache is not None and template_id in request_cache.field_counts:
        request_cache.stats["field_count_cache_hits"] += 1
        return request_cache.field_counts[template_id], True, {}
    cached_count = published_template_cache.get_field_count(template_id)
    if cached_count is not None:
        if request_cache is not None:
            request_cache.field_counts[template_id] = cached_count
            request_cache.stats["published_field_count_cache_hits"] += 1
        return cached_count, True, {}
    total_started = time.perf_counter()
    connect_started = time.perf_counter()
    conn = _connect()
    connect_elapsed = time.perf_counter() - connect_started
    with conn:
        cursor, execute_timing = conn.execute_timed(
            """
            SELECT COUNT(*) as count
            FROM extraction_fields ef
            JOIN template_pages tp ON tp.id = ef.template_page_id
            WHERE tp.template_version_id = ?
            """,
            (template_id,),
        )
        fetch_started = time.perf_counter()
        row = cursor.fetchone()
        fetch_elapsed = time.perf_counter() - fetch_started
        processing_started = time.perf_counter()
        count = row["count"]
        processing_elapsed = time.perf_counter() - processing_started
        db_timing = {
            "connect": connect_elapsed,
            "cursor_create": float(execute_timing.get("cursor_create") or 0.0),
            "execute": float(execute_timing.get("execute") or 0.0),
            "fetch": fetch_elapsed,
            "processing": processing_elapsed,
            "total_db_operation": time.perf_counter() - total_started,
        }
    if request_cache is not None:
        request_cache.field_counts[template_id] = count
        request_cache.stats["field_count_db_fetches"] += 1
    return count, False, db_timing


def _fetch_verification_fields_cached(template_id: str, request_cache: Optional[DetectionRequestCache]) -> tuple[List[Dict[str, Any]], bool, Dict[str, Any]]:
    if request_cache is not None and template_id in request_cache.verification_fields:
        request_cache.stats["verification_fields_cache_hits"] += 1
        return request_cache.verification_fields[template_id], True, {}
    cached_fields = published_template_cache.get_verification_fields(template_id)
    if cached_fields is not None:
        if request_cache is not None:
            request_cache.verification_fields[template_id] = cached_fields
            request_cache.stats["published_verification_fields_cache_hits"] += 1
        return cached_fields, True, {}
    fields, db_timing = verification_service.load_verification_fields_with_db_timing(template_id)
    if request_cache is not None:
        request_cache.verification_fields[template_id] = fields
        request_cache.stats["verification_fields_db_fetches"] += 1
    return fields, False, db_timing


def _fetch_template_page_image_source(
    template_id: str,
    page_number: int,
    timing: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    total_started = time.perf_counter()
    connect_started = time.perf_counter()
    conn = _connect()
    connect_elapsed = time.perf_counter() - connect_started
    if timing is not None:
        timing["connect_ms"] = _ms(connect_elapsed)
    with conn:
        sql = """
        SELECT normalized_image_url, sample_image_url
        FROM template_pages
        WHERE template_version_id = ? AND page_number = ?
        LIMIT 1
        """
        params = (template_id, page_number)
        if hasattr(conn, "execute_timed"):
            cursor, execute_timing = conn.execute_timed(sql, params)
            if timing is not None:
                timing["cursor_create_ms"] = _ms(execute_timing.get("cursor_create"))
                timing["execute_ms"] = _ms(execute_timing.get("execute"))
        else:
            execute_started = time.perf_counter()
            cursor = conn.execute(sql, params)
            if timing is not None:
                timing["cursor_create_ms"] = None
                timing["execute_ms"] = _ms(time.perf_counter() - execute_started)
        fetch_started = time.perf_counter()
        row = cursor.fetchone()
        if timing is not None:
            timing["fetch_ms"] = _ms(time.perf_counter() - fetch_started)
    if timing is not None:
        timing["total_ms"] = _ms(time.perf_counter() - total_started)
    if row is None:
        return None
    return row["normalized_image_url"] or row["sample_image_url"]


def _fetch_template_fields(template_id: str) -> List[Dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT
                ef.id,
                tp.template_version_id AS template_id,
                ef.template_page_id,
                tp.page_number,
                ef.field_name,
                ef.display_label,
                ef.data_type,
                FALSE AS use_for_verification,
                NULL AS expected_text,
                NULL AS match_type,
                FALSE AS required_for_verification,
                ef.extraction_method,
                NULL AS roi_padding,
                1.0 AS verification_weight,
                ef.roi_mode,
                ef.expected_content,
                ef.roi_x_ratio,
                ef.roi_y_ratio,
                ef.roi_width_ratio,
                ef.roi_height_ratio,
                ef.roi_points_json,
                ef.sort_order,
                ef.created_at
            FROM extraction_fields ef
            JOIN template_pages tp ON tp.id = ef.template_page_id
            WHERE tp.template_version_id = ?
            UNION ALL
            SELECT
                va.id,
                tp.template_version_id AS template_id,
                va.template_page_id,
                tp.page_number,
                va.anchor_name AS field_name,
                va.anchor_name AS display_label,
                va.anchor_type AS data_type,
                TRUE AS use_for_verification,
                va.expected_text,
                va.match_type,
                va.required AS required_for_verification,
                'fixed_roi' AS extraction_method,
                NULL AS roi_padding,
                va.weight AS verification_weight,
                'fix' AS roi_mode,
                NULL AS expected_content,
                va.roi_x_ratio,
                va.roi_y_ratio,
                va.roi_width_ratio,
                va.roi_height_ratio,
                va.roi_points_json,
                va.sort_order,
                va.created_at
            FROM verification_anchors va
            JOIN template_pages tp ON tp.id = va.template_page_id
            WHERE tp.template_version_id = ?
            ORDER BY page_number ASC, sort_order ASC, created_at ASC
            """,
            (template_id, template_id),
        ).fetchall()

    fields: List[Dict[str, Any]] = []
    for row in rows:
        roi = {
            "page_number": row["page_number"],
            "x_ratio": row["roi_x_ratio"],
            "y_ratio": row["roi_y_ratio"],
            "width_ratio": row["roi_width_ratio"],
            "height_ratio": row["roi_height_ratio"],
        }
        raw_points = row["roi_points_json"] if "roi_points_json" in row.keys() else None
        if raw_points:
            try:
                points = json.loads(raw_points) if isinstance(raw_points, str) else raw_points
                if isinstance(points, list) and len(points) > 2:
                    roi["points"] = points
            except Exception:
                pass
        fields.append(
            {
                "id": row["id"],
                "template_id": row["template_id"],
                "template_page_id": row["template_page_id"],
                "page_number": row["page_number"],
                "field_name": row["field_name"],
                "display_label": row["display_label"],
                "roi": roi,
                "data_type": row["data_type"],
                "use_for_verification": bool(row["use_for_verification"]),
                "expected_text": row["expected_text"],
                "match_type": row["match_type"],
                "required_for_verification": bool(row["required_for_verification"]),
                "extraction_method": row["extraction_method"],
                "roi_padding": row["roi_padding"],
                "verification_weight": row["verification_weight"] if "verification_weight" in row.keys() else 1.0,
                "roi_mode": row["roi_mode"] if "roi_mode" in row.keys() else "fix",
                "expected_content": row["expected_content"] if "expected_content" in row.keys() else None,
            }
        )
    return fields


def _image_to_data_url(path: Path) -> str:
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _image_dimensions(path_value: Optional[str]) -> Optional[List[int]]:
    if not path_value:
        return None
    try:
        image = cv2.imread(str(path_value))
        if image is None:
            return None
        height, width = image.shape[:2]
        return [int(width), int(height)]
    except Exception:
        return None


def _image_source_dimensions(source: Optional[str]) -> Optional[List[int]]:
    if not source:
        return None
    try:
        if source.startswith("data:image"):
            _, encoded = source.split(",", 1)
            data = base64.b64decode(encoded)
            array = np.frombuffer(data, dtype=np.uint8)
            image = cv2.imdecode(array, cv2.IMREAD_COLOR)
        else:
            source_path = Path(source)
            if not source_path.is_absolute() and not source_path.exists():
                backend_root = Path(__file__).resolve().parents[1]
                candidate = backend_root / source_path
                if candidate.exists():
                    source_path = candidate
            image = cv2.imread(str(source_path))
        if image is None:
            return None
        height, width = image.shape[:2]
        return [int(width), int(height)]
    except Exception:
        return None


def _signature_content_bounds(signature: Optional[Dict[str, Any]]) -> Optional[Dict[str, float]]:
    regions = signature.get("regions") if isinstance(signature, dict) else None
    if not isinstance(regions, list) or not regions:
        return None
    left = 1.0
    top = 1.0
    right = 0.0
    bottom = 0.0
    valid_count = 0
    for region in regions:
        if not isinstance(region, dict):
            continue
        bbox = region.get("bbox") if isinstance(region.get("bbox"), dict) else {}
        try:
            x = float(bbox.get("x_ratio") or 0.0)
            y = float(bbox.get("y_ratio") or 0.0)
            box_width = float(bbox.get("width_ratio") or 0.0)
            box_height = float(bbox.get("height_ratio") or 0.0)
        except (TypeError, ValueError):
            continue
        if box_width <= 0.0 or box_height <= 0.0:
            continue
        left = min(left, max(0.0, min(1.0, x)))
        top = min(top, max(0.0, min(1.0, y)))
        right = max(right, max(0.0, min(1.0, x + box_width)))
        bottom = max(bottom, max(0.0, min(1.0, y + box_height)))
        valid_count += 1
    if valid_count <= 0 or right <= left or bottom <= top:
        return None
    return {
        "left": left,
        "top": top,
        "right": right,
        "bottom": bottom,
        "width": right - left,
        "height": bottom - top,
        "region_count": valid_count,
    }


def _signature_layout_space_source_bounds(signature: Optional[Dict[str, Any]]) -> Optional[Dict[str, float]]:
    if not isinstance(signature, dict):
        return None
    normalization = signature.get("layout_space_normalization")
    if not isinstance(normalization, dict) or not normalization.get("applied"):
        return None
    bounds = normalization.get("bounds")
    if not isinstance(bounds, dict):
        return None
    try:
        left = float(bounds.get("left"))
        top = float(bounds.get("top"))
        right = float(bounds.get("right"))
        bottom = float(bounds.get("bottom"))
    except (TypeError, ValueError):
        return None
    if right <= left or bottom <= top:
        return None
    return {
        "left": max(0.0, min(1.0, left)),
        "top": max(0.0, min(1.0, top)),
        "right": max(0.0, min(1.0, right)),
        "bottom": max(0.0, min(1.0, bottom)),
        "width": max(0.0, min(1.0, right) - max(0.0, min(1.0, left))),
        "height": max(0.0, min(1.0, bottom) - max(0.0, min(1.0, top))),
        "region_count": int(normalization.get("region_count") or signature.get("region_count") or 0),
    }


def _bounds_from_boxes(boxes: List[Dict[str, float]]) -> Optional[Dict[str, float]]:
    if not boxes:
        return None
    left = min(box["left"] for box in boxes)
    top = min(box["top"] for box in boxes)
    right = max(box["right"] for box in boxes)
    bottom = max(box["bottom"] for box in boxes)
    if right <= left or bottom <= top:
        return None
    return {
        "left": round(float(left), 6),
        "top": round(float(top), 6),
        "right": round(float(right), 6),
        "bottom": round(float(bottom), 6),
        "width": round(float(right - left), 6),
        "height": round(float(bottom - top), 6),
        "region_count": len(boxes),
    }


def _quantile(values: List[float], ratio: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * ratio))))
    return float(ordered[index])


def _signature_robust_content_bounds(signature: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    raw_bounds = _signature_layout_space_source_bounds(signature) or _signature_content_bounds(signature)
    result: Dict[str, Any] = {
        "raw_content_bounds": raw_bounds,
        "robust_content_bounds": None,
        "regions_used": 0,
        "regions_excluded": 0,
        "fallback_reason": None,
    }
    regions = signature.get("regions") if isinstance(signature, dict) else None
    if not isinstance(regions, list) or len(regions) < 6:
        result["fallback_reason"] = "insufficient_regions_for_robust_bounds"
        return result

    boxes: List[Dict[str, float]] = []
    for index, region in enumerate(regions):
        if not isinstance(region, dict):
            continue
        bbox = region.get("source_bbox") if isinstance(region.get("source_bbox"), dict) else region.get("bbox")
        if not isinstance(bbox, dict):
            continue
        try:
            x = max(0.0, min(1.0, float(bbox.get("x_ratio") or 0.0)))
            y = max(0.0, min(1.0, float(bbox.get("y_ratio") or 0.0)))
            width = max(0.0, min(1.0, float(bbox.get("width_ratio") or 0.0)))
            height = max(0.0, min(1.0, float(bbox.get("height_ratio") or 0.0)))
        except (TypeError, ValueError):
            continue
        if width <= 0.0 or height <= 0.0:
            continue
        area = width * height
        boxes.append(
            {
                "index": float(index),
                "left": x,
                "top": y,
                "right": min(1.0, x + width),
                "bottom": min(1.0, y + height),
                "center_x": min(1.0, x + width / 2.0),
                "center_y": min(1.0, y + height / 2.0),
                "area": area,
            }
        )
    if len(boxes) < 6:
        result["fallback_reason"] = "insufficient_valid_boxes_for_robust_bounds"
        return result

    median_area = _quantile([box["area"] for box in boxes], 0.5)
    min_area = max(0.000005, median_area * 0.10)
    area_filtered = [box for box in boxes if box["area"] >= min_area]
    if len(area_filtered) < max(4, int(len(boxes) * 0.45)):
        area_filtered = boxes

    low_x = _quantile([box["center_x"] for box in area_filtered], 0.03)
    high_x = _quantile([box["center_x"] for box in area_filtered], 0.97)
    low_y = _quantile([box["center_y"] for box in area_filtered], 0.03)
    high_y = _quantile([box["center_y"] for box in area_filtered], 0.97)
    center_filtered = [
        box
        for box in area_filtered
        if low_x <= box["center_x"] <= high_x and low_y <= box["center_y"] <= high_y
    ]
    if len(center_filtered) < max(4, int(len(boxes) * 0.45)):
        result["fallback_reason"] = "robust_filter_removed_too_many_regions"
        return result

    robust_bounds = _bounds_from_boxes(center_filtered)
    if not robust_bounds:
        result["fallback_reason"] = "robust_bounds_empty"
        return result

    normalization = signature.get("layout_space_normalization") if isinstance(signature, dict) else None
    source_bounds = None
    if isinstance(normalization, dict) and normalization.get("applied") and isinstance(normalization.get("bounds"), dict):
        source = _signature_layout_space_source_bounds(signature)
        if source:
            source_bounds = {
                "left": source["left"] + robust_bounds["left"] * source["width"],
                "top": source["top"] + robust_bounds["top"] * source["height"],
                "right": source["left"] + robust_bounds["right"] * source["width"],
                "bottom": source["top"] + robust_bounds["bottom"] * source["height"],
            }
            source_bounds["width"] = source_bounds["right"] - source_bounds["left"]
            source_bounds["height"] = source_bounds["bottom"] - source_bounds["top"]
            source_bounds["region_count"] = robust_bounds["region_count"]

    final_bounds = source_bounds or robust_bounds
    raw_area = float((raw_bounds or {}).get("width") or 0.0) * float((raw_bounds or {}).get("height") or 0.0)
    robust_area = float(final_bounds.get("width") or 0.0) * float(final_bounds.get("height") or 0.0)
    if raw_area > 0 and robust_area < raw_area * 0.35:
        result["fallback_reason"] = "robust_bounds_too_small_vs_raw_bounds"
        return result

    result.update(
        {
            "robust_content_bounds": {
                key: round(float(value), 6) if isinstance(value, float) else value
                for key, value in final_bounds.items()
            },
            "regions_used": len(center_filtered),
            "regions_excluded": max(0, len(boxes) - len(center_filtered)),
            "fallback_reason": None,
        }
    )
    return result


def _signature_metrics_for_regions(regions: List[Dict[str, Any]]) -> Dict[str, Any]:
    labels = ("text", "table", "image")
    grid_size = 4
    label_counts = {label: 0 for label in labels}
    area_by_label = {label: 0.0 for label in labels}
    grid_counts = {label: [0 for _ in range(grid_size * grid_size)] for label in labels}
    grid_area = {label: [0.0 for _ in range(grid_size * grid_size)] for label in labels}
    for region in regions:
        label = str(region.get("label") or "text")
        if label not in label_counts:
            label = "text"
        label_counts[label] += 1
        area = max(0.0, min(1.0, float(region.get("area_ratio") or 0.0)))
        area_by_label[label] += area
        center = region.get("center") if isinstance(region.get("center"), list) else [0.0, 0.0]
        try:
            cx = max(0.0, min(1.0, float(center[0])))
            cy = max(0.0, min(1.0, float(center[1])))
        except (TypeError, ValueError, IndexError):
            cx = 0.0
            cy = 0.0
        gx = min(grid_size - 1, max(0, int(cx * grid_size)))
        gy = min(grid_size - 1, max(0, int(cy * grid_size)))
        cell = gy * grid_size + gx
        grid_counts[label][cell] += 1
        grid_area[label][cell] += area
    return {
        "region_count": len(regions),
        "label_counts": label_counts,
        "area_by_label": {label: round(max(0.0, min(1.0, value)), 6) for label, value in area_by_label.items()},
        "grid_counts": grid_counts,
        "grid_area": {
            label: [round(max(0.0, min(1.0, value)), 6) for value in values]
            for label, values in grid_area.items()
        },
    }


def _layout_bounds_to_pixel_box(bounds: Dict[str, float], image_width: int, image_height: int) -> List[int]:
    return [
        max(0, int(round(float(bounds.get("left") or 0.0) * image_width))),
        max(0, int(round(float(bounds.get("top") or 0.0) * image_height))),
        min(image_width, int(round(float(bounds.get("right") or 1.0) * image_width))),
        min(image_height, int(round(float(bounds.get("bottom") or 1.0) * image_height))),
    ]


def _old_safe_layout_crop_box(
    crop_left: int,
    crop_top: int,
    crop_right: int,
    crop_bottom: int,
    image_width: int,
    image_height: int,
) -> Dict[str, Any]:
    original = [int(crop_left), int(crop_top), int(crop_right), int(crop_bottom)]
    max_left = int(round(image_width * OLD_LAYOUT_CROP_MAX_INSET_X_RATIO))
    max_top = int(round(image_height * OLD_LAYOUT_CROP_MAX_INSET_Y_RATIO))
    min_right = int(round(image_width * (1.0 - OLD_LAYOUT_CROP_MAX_INSET_X_RATIO)))
    min_bottom = int(round(image_height * (1.0 - OLD_LAYOUT_CROP_MAX_INSET_Y_RATIO)))
    safe_left = min(max(0, crop_left), max_left)
    safe_top = min(max(0, crop_top), max_top)
    safe_right = max(min(image_width, crop_right), min_right)
    safe_bottom = max(min(image_height, crop_bottom), min_bottom)
    min_width = int(round(image_width * OLD_LAYOUT_CROP_MIN_WIDTH_RATIO))
    min_height = int(round(image_height * OLD_LAYOUT_CROP_MIN_HEIGHT_RATIO))
    if safe_right - safe_left < min_width:
        missing = min_width - (safe_right - safe_left)
        safe_left = max(0, safe_left - ((missing + 1) // 2))
        safe_right = min(image_width, safe_right + (missing // 2))
    if safe_bottom - safe_top < min_height:
        missing = min_height - (safe_bottom - safe_top)
        safe_top = max(0, safe_top - ((missing + 1) // 2))
        safe_bottom = min(image_height, safe_bottom + (missing // 2))
    return {
        "left": int(safe_left),
        "top": int(safe_top),
        "right": int(safe_right),
        "bottom": int(safe_bottom),
        "original_box": original,
        "final_box": [int(safe_left), int(safe_top), int(safe_right), int(safe_bottom)],
        "applied": [int(safe_left), int(safe_top), int(safe_right), int(safe_bottom)] != original,
        "max_inset_x_ratio": OLD_LAYOUT_CROP_MAX_INSET_X_RATIO,
        "max_inset_y_ratio": OLD_LAYOUT_CROP_MAX_INSET_Y_RATIO,
        "min_width_ratio": OLD_LAYOUT_CROP_MIN_WIDTH_RATIO,
        "min_height_ratio": OLD_LAYOUT_CROP_MIN_HEIGHT_RATIO,
    }


def _old_layout_crop_box_from_bounds(
    bounds: Dict[str, float],
    image_width: int,
    image_height: int,
) -> Dict[str, Any]:
    left = float(bounds.get("left") or 0.0) * image_width
    top = float(bounds.get("top") or 0.0) * image_height
    right = float(bounds.get("right") or 1.0) * image_width
    bottom = float(bounds.get("bottom") or 1.0) * image_height
    content_width = max(1.0, right - left)
    content_height = max(1.0, bottom - top)
    pad_left = max(image_width * OLD_LAYOUT_CROP_MIN_IMAGE_PADDING_RATIO, content_width * OLD_LAYOUT_CROP_PADDING_X_RATIO)
    pad_right = max(image_width * OLD_LAYOUT_CROP_MIN_IMAGE_PADDING_RATIO, content_width * OLD_LAYOUT_CROP_PADDING_X_RATIO)
    pad_top = max(image_height * OLD_LAYOUT_CROP_MIN_IMAGE_PADDING_RATIO, content_height * OLD_LAYOUT_CROP_PADDING_TOP_RATIO)
    pad_bottom = max(image_height * OLD_LAYOUT_CROP_MIN_IMAGE_PADDING_RATIO, content_height * OLD_LAYOUT_CROP_PADDING_BOTTOM_RATIO)
    requested = [
        int(max(0, np.floor(left - pad_left))),
        int(max(0, np.floor(top - pad_top))),
        int(min(image_width, np.ceil(right + pad_right))),
        int(min(image_height, np.ceil(bottom + pad_bottom))),
    ]
    safe_crop = _old_safe_layout_crop_box(requested[0], requested[1], requested[2], requested[3], image_width, image_height)
    final_box = safe_crop["final_box"]
    return {
        "passed": final_box[2] > final_box[0] and final_box[3] > final_box[1],
        "reason": "old_layout_crop_box_valid" if final_box[2] > final_box[0] and final_box[3] > final_box[1] else "old_layout_crop_zero_area",
        "content_box": [round(left, 2), round(top, 2), round(right, 2), round(bottom, 2)],
        "requested_expanded_box": requested,
        "final_box": final_box,
        "expanded_box": final_box,
        "padding": {
            "left": round(float(pad_left), 2),
            "right": round(float(pad_right), 2),
            "top": round(float(pad_top), 2),
            "bottom": round(float(pad_bottom), 2),
        },
        "safe_crop": safe_crop,
        "source": "old_layout_assisted_crop_from_layout_bounds",
    }


def _generic_layout_union_crop_box_from_bounds(
    bounds: Dict[str, float],
    image_width: int,
    image_height: int,
) -> Dict[str, Any]:
    left = float(bounds.get("left") or 0.0) * image_width
    top = float(bounds.get("top") or 0.0) * image_height
    right = float(bounds.get("right") or 1.0) * image_width
    bottom = float(bounds.get("bottom") or 1.0) * image_height
    margin_x = max(0.0, image_width * GENERIC_LAYOUT_UNION_CROP_MARGIN_RATIO)
    margin_y = max(0.0, image_height * GENERIC_LAYOUT_UNION_CROP_MARGIN_RATIO)
    final_box = [
        int(max(0, np.floor(left - margin_x))),
        int(max(0, np.floor(top - margin_y))),
        int(min(image_width, np.ceil(right + margin_x))),
        int(min(image_height, np.ceil(bottom + margin_y))),
    ]
    crop_width = max(0, final_box[2] - final_box[0])
    crop_height = max(0, final_box[3] - final_box[1])
    passed = crop_width > 0 and crop_height > 0
    coverage = round(float((crop_width / max(1, image_width)) * (crop_height / max(1, image_height))), 6)
    return {
        "passed": passed,
        "reason": "generic_layout_union_crop_box_valid" if passed else "generic_layout_union_crop_zero_area",
        "layout_union_box": [round(left, 2), round(top, 2), round(right, 2), round(bottom, 2)],
        "content_box": [round(left, 2), round(top, 2), round(right, 2), round(bottom, 2)],
        "margin_x_px": round(float(margin_x), 2),
        "margin_y_px": round(float(margin_y), 2),
        "expanded_crop_box": final_box,
        "final_crop_box": final_box,
        "final_crop_coverage": coverage,
        "final_box": final_box,
        "expanded_box": final_box,
        "padding": {
            "left": round(float(margin_x), 2),
            "right": round(float(margin_x), 2),
            "top": round(float(margin_y), 2),
            "bottom": round(float(margin_y), 2),
        },
        "source": "generic_layout_union_crop_from_raw_layout_bounds",
    }


def _union_region_boxes(boxes: List[Dict[str, float]]) -> Optional[Dict[str, float]]:
    valid = [box for box in boxes if isinstance(box, dict) and float(box.get("width") or 0.0) > 0.0 and float(box.get("height") or 0.0) > 0.0]
    if not valid:
        return None
    left = min(float(box.get("left") or 0.0) for box in valid)
    top = min(float(box.get("top") or 0.0) for box in valid)
    right = max(float(box.get("right") or 0.0) for box in valid)
    bottom = max(float(box.get("bottom") or 0.0) for box in valid)
    return {
        "left": max(0.0, min(1.0, left)),
        "top": max(0.0, min(1.0, top)),
        "right": max(0.0, min(1.0, right)),
        "bottom": max(0.0, min(1.0, bottom)),
        "width": max(0.0, min(1.0, right) - max(0.0, left)),
        "height": max(0.0, min(1.0, bottom) - max(0.0, top)),
    }


def _retrieval_precrop_signature(
    signature: Dict[str, Any],
    image_path: Optional[str] = None,
    output_dir: Optional[Path] = None,
    page_number: int = 1,
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    debug: Dict[str, Any] = {
        "retrieval_precrop_attempted": True,
        "retrieval_precrop_applied": False,
        "retrieval_precrop_box": None,
        "retrieval_precrop_coverage": None,
        "retrieval_precrop_image_path": None,
        "retrieval_precrop_preview_url": None,
        "retrieval_crop": {
            "applied": False,
            "layout_bounds": None,
            "expanded_bounds": None,
            "safety_margin": None,
            "crop_box": None,
            "crop_size": None,
        },
        "retrieval_source": "original_layout_signature",
        "reason": None,
    }
    if not isinstance(signature, dict):
        debug["reason"] = "invalid_signature"
        return signature, debug
    robust_debug = _signature_robust_content_bounds(signature)
    bounds = _signature_content_bounds(signature) or robust_debug.get("raw_content_bounds")
    if not isinstance(bounds, dict):
        debug["reason"] = "missing_content_bounds"
        return signature, debug
    width = float(bounds.get("width") or 0.0)
    height = float(bounds.get("height") or 0.0)
    if width <= 0.0 or height <= 0.0:
        debug["reason"] = "invalid_content_bounds"
        return signature, debug
    coverage = round(float(width * height), 6)
    edge_inset = max(
        0.0,
        float(bounds.get("left") or 0.0),
        float(bounds.get("top") or 0.0),
        1.0 - float(bounds.get("right") or 1.0),
        1.0 - float(bounds.get("bottom") or 1.0),
    )
    debug["retrieval_precrop_coverage"] = coverage
    debug["retrieval_crop"]["layout_bounds"] = {key: round(float(value), 6) for key, value in bounds.items()}
    debug["retrieval_crop"]["raw_content_bounds"] = robust_debug.get("raw_content_bounds")
    debug["retrieval_crop"]["robust_content_bounds"] = robust_debug.get("robust_content_bounds")
    full_frame_like = width >= 0.92 and height >= 0.92 and edge_inset <= 0.04
    if full_frame_like:
        debug["reason"] = "content_already_full_frame"
        return signature, debug
    if coverage >= 0.88 and width >= 0.90 and height >= 0.90:
        debug["reason"] = "content_coverage_too_large_for_precrop"
        return signature, debug
    image_width = max(float(signature.get("image_width") or 1.0), 1.0)
    image_height = max(float(signature.get("image_height") or 1.0), 1.0)
    generic_crop_debug = _generic_layout_union_crop_box_from_bounds(bounds, int(round(image_width)), int(round(image_height)))
    if not generic_crop_debug.get("passed"):
        debug["reason"] = generic_crop_debug.get("reason") or "generic_layout_precrop_unavailable"
        debug["retrieval_crop"]["generic_layout_crop"] = generic_crop_debug
        return signature, debug
    crop_box = generic_crop_debug["final_box"]
    crop = {
        "left": crop_box[0] / image_width,
        "top": crop_box[1] / image_height,
        "right": crop_box[2] / image_width,
        "bottom": crop_box[3] / image_height,
    }
    crop["width"] = crop["right"] - crop["left"]
    crop["height"] = crop["bottom"] - crop["top"]
    if crop["width"] <= 0.05 or crop["height"] <= 0.05:
        debug["reason"] = "precrop_box_too_small"
        return signature, debug

    rebased_regions: List[Dict[str, Any]] = []
    for region in signature.get("regions", []) if isinstance(signature.get("regions"), list) else []:
        if not isinstance(region, dict):
            continue
        bbox = _region_bbox(region, "source_bbox")
        if not bbox:
            continue
        left = max(0.0, min(1.0, (bbox["left"] - crop["left"]) / crop["width"]))
        top = max(0.0, min(1.0, (bbox["top"] - crop["top"]) / crop["height"]))
        right = max(0.0, min(1.0, (bbox["right"] - crop["left"]) / crop["width"]))
        bottom = max(0.0, min(1.0, (bbox["bottom"] - crop["top"]) / crop["height"]))
        if right <= left or bottom <= top:
            continue
        next_bbox = {
            "x_ratio": round(left, 6),
            "y_ratio": round(top, 6),
            "width_ratio": round(right - left, 6),
            "height_ratio": round(bottom - top, 6),
        }
        rebased_regions.append(
            {
                **region,
                "bbox": next_bbox,
                "source_bbox": region.get("source_bbox") if isinstance(region.get("source_bbox"), dict) else region.get("bbox"),
                "center": [round(left + (right - left) / 2.0, 6), round(top + (bottom - top) / 2.0, 6)],
                "area_ratio": round(max(0.0, min(1.0, (right - left) * (bottom - top))), 6),
            }
        )
    if len(rebased_regions) < 2:
        debug["reason"] = "insufficient_precrop_regions"
        return signature, debug
    metrics = _signature_metrics_for_regions(rebased_regions)
    next_signature = {
        **signature,
        **metrics,
        "page_aspect_ratio": round((image_width * crop["width"]) / max(1.0, image_height * crop["height"]), 6),
        "image_width": int(round(image_width * crop["width"])),
        "image_height": int(round(image_height * crop["height"])),
        "regions": rebased_regions,
        "layout_space_normalization": {
            "applied": True,
            "source": "retrieval_precrop_content_bounds",
            "bounds": {key: round(float(value), 6) for key, value in crop.items()},
            "original_image_width": int(image_width),
            "original_image_height": int(image_height),
        },
    }
    debug.update(
        {
            "retrieval_precrop_applied": True,
            "retrieval_precrop_box": {key: round(float(value), 6) for key, value in crop.items()},
            "retrieval_precrop_coverage": round(float(crop["width"] * crop["height"]), 6),
            "retrieval_crop": {
                "applied": True,
                "layout_bounds": {key: round(float(value), 6) for key, value in bounds.items()},
                "expanded_bounds": {key: round(float(value), 6) for key, value in crop.items()},
                "safety_margin": generic_crop_debug.get("padding"),
                "layout_union_box": generic_crop_debug.get("layout_union_box"),
                "margin_x_px": generic_crop_debug.get("margin_x_px"),
                "margin_y_px": generic_crop_debug.get("margin_y_px"),
                "expanded_crop_box": generic_crop_debug.get("expanded_crop_box"),
                "final_crop_box": generic_crop_debug.get("final_crop_box"),
                "final_crop_coverage": generic_crop_debug.get("final_crop_coverage"),
                "crop_box": {key: round(float(value), 6) for key, value in crop.items()},
                "crop_size": [int(next_signature["image_width"]), int(next_signature["image_height"])],
                "generic_layout_crop": generic_crop_debug,
            },
            "retrieval_source": "retrieval_precrop_image_signature",
            "reason": "background_content_bounds_rebased_for_retrieval",
        }
    )
    if image_path and output_dir is not None:
        image = cv2.imread(str(image_path))
        if image is not None:
            image_height_px, image_width_px = image.shape[:2]
            left_px = max(0, int(round(crop["left"] * image_width_px)))
            top_px = max(0, int(round(crop["top"] * image_height_px)))
            right_px = min(image_width_px, int(round(crop["right"] * image_width_px)))
            bottom_px = min(image_height_px, int(round(crop["bottom"] * image_height_px)))
            if right_px > left_px and bottom_px > top_px:
                output_dir.mkdir(parents=True, exist_ok=True)
                output_path = output_dir / f"page_{page_number}_retrieval_crop.png"
                if cv2.imwrite(str(output_path), image[top_px:bottom_px, left_px:right_px].copy()):
                    debug["retrieval_precrop_image_path"] = str(output_path)
                    debug["retrieval_precrop_preview_url"] = _detection_preview_url(str(output_path))
    return next_signature, debug


def _rebase_layout_signature_to_crop(
    signature: Optional[Dict[str, Any]],
    crop: Dict[str, float],
    image_width: int,
    image_height: int,
) -> Optional[Dict[str, Any]]:
    if not isinstance(signature, dict):
        return None
    crop_width = float(crop.get("width") or 0.0)
    crop_height = float(crop.get("height") or 0.0)
    if crop_width <= 0.0 or crop_height <= 0.0:
        return None
    rebased_regions: List[Dict[str, Any]] = []
    for region in signature.get("regions", []) if isinstance(signature.get("regions"), list) else []:
        if not isinstance(region, dict):
            continue
        bbox = _region_bbox(region, "source_bbox")
        if not bbox:
            continue
        left = max(0.0, min(1.0, (bbox["left"] - float(crop["left"])) / crop_width))
        top = max(0.0, min(1.0, (bbox["top"] - float(crop["top"])) / crop_height))
        right = max(0.0, min(1.0, (bbox["right"] - float(crop["left"])) / crop_width))
        bottom = max(0.0, min(1.0, (bbox["bottom"] - float(crop["top"])) / crop_height))
        if right <= left or bottom <= top:
            continue
        next_bbox = {
            "x_ratio": round(left, 6),
            "y_ratio": round(top, 6),
            "width_ratio": round(right - left, 6),
            "height_ratio": round(bottom - top, 6),
        }
        rebased_regions.append(
            {
                **region,
                "bbox": next_bbox,
                "source_bbox": region.get("source_bbox") if isinstance(region.get("source_bbox"), dict) else region.get("bbox"),
                "center": [round(left + (right - left) / 2.0, 6), round(top + (bottom - top) / 2.0, 6)],
                "area_ratio": round(max(0.0, min(1.0, (right - left) * (bottom - top))), 6),
            }
        )
    if len(rebased_regions) < 2:
        return None
    metrics = _signature_metrics_for_regions(rebased_regions)
    return {
        **signature,
        **metrics,
        "page_aspect_ratio": round(image_width / max(1.0, float(image_height)), 6),
        "image_width": int(image_width),
        "image_height": int(image_height),
        "regions": rebased_regions,
        "layout_space_normalization": {
            "applied": True,
            "source": "layout_reference_crop",
            "bounds": {key: round(float(value), 6) for key, value in crop.items()},
            "original_image_width": int(image_width),
            "original_image_height": int(image_height),
        },
    }


def _solve_reference_crop_axis(
    query_min: float,
    query_max: float,
    template_min: float,
    template_max: float,
    image_size: int,
) -> Optional[tuple[int, int]]:
    query_span = query_max - query_min
    template_span = template_max - template_min
    if query_span <= 0.01 or template_span <= 0.01 or image_size <= 0:
        return None
    crop_size_ratio = query_span / template_span
    crop_start_ratio = query_min - (template_min * crop_size_ratio)
    crop_end_ratio = crop_start_ratio + crop_size_ratio
    return int(round(crop_start_ratio * image_size)), int(round(crop_end_ratio * image_size))


def _clamp_reference_crop_box(left: int, top: int, right: int, bottom: int, image_width: int, image_height: int) -> Dict[str, Any]:
    original = [left, top, right, bottom]
    max_inset_x = int(round(image_width * LAYOUT_REFERENCE_CROP_MAX_INSET_RATIO))
    max_inset_y = int(round(image_height * LAYOUT_REFERENCE_CROP_MAX_INSET_RATIO))
    min_width = int(round(image_width * LAYOUT_REFERENCE_CROP_MIN_COVERAGE_RATIO))
    min_height = int(round(image_height * LAYOUT_REFERENCE_CROP_MIN_COVERAGE_RATIO))
    safe_left = min(max(0, left), max_inset_x)
    safe_top = min(max(0, top), max_inset_y)
    safe_right = max(min(image_width, right), image_width - max_inset_x)
    safe_bottom = max(min(image_height, bottom), image_height - max_inset_y)
    if safe_right - safe_left < min_width:
        missing = min_width - (safe_right - safe_left)
        safe_left = max(0, safe_left - ((missing + 1) // 2))
        safe_right = min(image_width, safe_right + (missing // 2))
    if safe_bottom - safe_top < min_height:
        missing = min_height - (safe_bottom - safe_top)
        safe_top = max(0, safe_top - ((missing + 1) // 2))
        safe_bottom = min(image_height, safe_bottom + (missing // 2))
    if safe_right <= safe_left or safe_bottom <= safe_top:
        return {"passed": False, "reason": "invalid_reference_crop_box", "original_box": original}
    final_box = [safe_left, safe_top, safe_right, safe_bottom]
    return {
        "passed": True,
        "original_box": original,
        "final_box": final_box,
        "clamped": final_box != original,
        "max_inset_ratio": LAYOUT_REFERENCE_CROP_MAX_INSET_RATIO,
        "min_coverage_ratio": LAYOUT_REFERENCE_CROP_MIN_COVERAGE_RATIO,
    }


def _reference_crop_safety_margins(
    crop_width: int,
    crop_height: int,
    image_width: int,
    image_height: int,
    projected_box: List[int],
    query_bounds: Dict[str, float],
    template_bounds: Dict[str, float],
    query_raw_bounds: Optional[Dict[str, float]] = None,
    template_raw_bounds: Optional[Dict[str, float]] = None,
    projection_confidence: float = 1.0,
) -> Dict[str, Any]:
    min_ratio = max(0.0, LAYOUT_REFERENCE_PROJECTED_MIN_SAFETY_MARGIN_RATIO)
    max_ratio = max(min_ratio, LAYOUT_REFERENCE_PROJECTED_MAX_SAFETY_MARGIN_RATIO)
    weight = max(0.0, LAYOUT_REFERENCE_PROJECTED_TEMPLATE_MARGIN_WEIGHT)
    template_width = max(float(template_bounds.get("width") or 0.0), 1e-6)
    template_height = max(float(template_bounds.get("height") or 0.0), 1e-6)
    base_ratios = {
        "left": (max(0.0, float(template_bounds.get("left") or 0.0)) / template_width) * weight,
        "right": (max(0.0, 1.0 - float(template_bounds.get("right") or 1.0)) / template_width) * weight,
        "top": (max(0.0, float(template_bounds.get("top") or 0.0)) / template_height) * weight,
        "bottom": (max(0.0, 1.0 - float(template_bounds.get("bottom") or 1.0)) / template_height) * weight,
    }
    content_box = {
        "left": float(query_bounds.get("left") or 0.0) * image_width,
        "right": float(query_bounds.get("right") or 0.0) * image_width,
        "top": float(query_bounds.get("top") or 0.0) * image_height,
        "bottom": float(query_bounds.get("bottom") or 0.0) * image_height,
    }
    left, top, right, bottom = projected_box
    edge_gaps = {
        "left": max(0.0, content_box["left"] - left) / max(1.0, float(crop_width)),
        "right": max(0.0, right - content_box["right"]) / max(1.0, float(crop_width)),
        "top": max(0.0, content_box["top"] - top) / max(1.0, float(crop_height)),
        "bottom": max(0.0, bottom - content_box["bottom"]) / max(1.0, float(crop_height)),
    }
    edge_uncertainty = {
        side: max(0.0, min_ratio - gap)
        for side, gap in edge_gaps.items()
    }
    def _shrink_uncertainty(raw_bounds: Optional[Dict[str, float]], robust_bounds: Dict[str, float]) -> Dict[str, float]:
        if not isinstance(raw_bounds, dict):
            return {"left": 0.0, "right": 0.0, "top": 0.0, "bottom": 0.0}
        robust_width = max(float(robust_bounds.get("width") or 0.0), 1e-6)
        robust_height = max(float(robust_bounds.get("height") or 0.0), 1e-6)
        return {
            "left": max(0.0, float(robust_bounds.get("left") or 0.0) - float(raw_bounds.get("left") or 0.0)) / robust_width,
            "right": max(0.0, float(raw_bounds.get("right") or 0.0) - float(robust_bounds.get("right") or 0.0)) / robust_width,
            "top": max(0.0, float(robust_bounds.get("top") or 0.0) - float(raw_bounds.get("top") or 0.0)) / robust_height,
            "bottom": max(0.0, float(raw_bounds.get("bottom") or 0.0) - float(robust_bounds.get("bottom") or 0.0)) / robust_height,
        }
    query_shrink = _shrink_uncertainty(query_raw_bounds, query_bounds)
    template_shrink = _shrink_uncertainty(template_raw_bounds, template_bounds)
    shrink_uncertainty = {
        side: min(max_ratio, max(query_shrink[side], template_shrink[side]) * 0.50)
        for side in ("left", "right", "top", "bottom")
    }
    confidence = max(0.0, min(1.0, float(projection_confidence)))
    geometry_uncertainty = 1.0 - confidence
    base_side_ratios = {
        side: min(max_ratio, max(min_ratio, base_ratios[side]))
        for side in ("left", "right", "top", "bottom")
    }
    edge_margin_ratios = {
        side: edge_uncertainty[side]
        for side in ("left", "right", "top", "bottom")
    }
    robust_shrink_margin_ratios = {
        side: min(
            max_ratio,
            shrink_uncertainty[side]
            * min(1.0, (geometry_uncertainty * 4.0) + (edge_uncertainty[side] / max(min_ratio, 1e-6))),
        )
        for side in ("left", "right", "top", "bottom")
    }
    confidence_margin_ratios = {
        side: geometry_uncertainty * base_side_ratios[side]
        for side in ("left", "right", "top", "bottom")
    }
    side_ratios = {
        side: min(
            max_ratio,
            base_side_ratios[side]
            + edge_margin_ratios[side]
            + robust_shrink_margin_ratios[side]
            + confidence_margin_ratios[side],
        )
        for side in ("left", "right", "top", "bottom")
    }
    adaptive_safety_margin = {
        "left": int(round(crop_width * side_ratios["left"])),
        "right": int(round(crop_width * side_ratios["right"])),
        "top": int(round(crop_height * side_ratios["top"])),
        "bottom": int(round(crop_height * side_ratios["bottom"])),
    }
    return {
        "base_margin": {
            "left": int(round(crop_width * base_side_ratios["left"])),
            "right": int(round(crop_width * base_side_ratios["right"])),
            "top": int(round(crop_height * base_side_ratios["top"])),
            "bottom": int(round(crop_height * base_side_ratios["bottom"])),
        },
        "base_safety_margin": {
            "left": int(round(crop_width * base_side_ratios["left"])),
            "right": int(round(crop_width * base_side_ratios["right"])),
            "top": int(round(crop_height * base_side_ratios["top"])),
            "bottom": int(round(crop_height * base_side_ratios["bottom"])),
        },
        "adaptive_safety_margin": adaptive_safety_margin,
        "final_adaptive_margin": adaptive_safety_margin,
        "edge_margin_contribution": {
            "left": int(round(crop_width * edge_margin_ratios["left"])),
            "right": int(round(crop_width * edge_margin_ratios["right"])),
            "top": int(round(crop_height * edge_margin_ratios["top"])),
            "bottom": int(round(crop_height * edge_margin_ratios["bottom"])),
        },
        "robust_shrink_margin_contribution": {
            "left": int(round(crop_width * robust_shrink_margin_ratios["left"])),
            "right": int(round(crop_width * robust_shrink_margin_ratios["right"])),
            "top": int(round(crop_height * robust_shrink_margin_ratios["top"])),
            "bottom": int(round(crop_height * robust_shrink_margin_ratios["bottom"])),
        },
        "confidence_margin_contribution": {
            "left": int(round(crop_width * confidence_margin_ratios["left"])),
            "right": int(round(crop_width * confidence_margin_ratios["right"])),
            "top": int(round(crop_height * confidence_margin_ratios["top"])),
            "bottom": int(round(crop_height * confidence_margin_ratios["bottom"])),
        },
        "edge_uncertainty": {
            side: round(float(value), 6)
            for side, value in edge_uncertainty.items()
        },
        "robust_shrink_uncertainty": {
            side: round(float(value), 6)
            for side, value in shrink_uncertainty.items()
        },
        "projection_confidence": round(float(confidence), 6),
        "edge_gaps": {
            side: round(float(value), 6)
            for side, value in edge_gaps.items()
        },
        "side_ratios": {
            side: round(float(value), 6)
            for side, value in side_ratios.items()
        },
    }


def _projected_reference_crop_box(
    left: int,
    top: int,
    right: int,
    bottom: int,
    image_width: int,
    image_height: int,
    query_bounds: Dict[str, float],
    template_bounds: Dict[str, float],
    query_raw_bounds: Optional[Dict[str, float]] = None,
    template_raw_bounds: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    original = [left, top, right, bottom]
    if image_width <= 0 or image_height <= 0:
        return {"passed": False, "reason": "invalid_source_image_size", "original_box": original}
    crop_left = max(0, min(image_width, left))
    crop_top = max(0, min(image_height, top))
    crop_right = max(0, min(image_width, right))
    crop_bottom = max(0, min(image_height, bottom))
    if crop_right <= crop_left or crop_bottom <= crop_top:
        return {"passed": False, "reason": "projected_document_box_empty", "original_box": original}
    crop_width = crop_right - crop_left
    crop_height = crop_bottom - crop_top
    template_width = max(float(template_bounds.get("width") or 0.0), 1e-6)
    template_height = max(float(template_bounds.get("height") or 0.0), 1e-6)
    query_width = max(float(query_bounds.get("width") or 0.0), 1e-6)
    query_height = max(float(query_bounds.get("height") or 0.0), 1e-6)
    scale_x = query_width / template_width
    scale_y = query_height / template_height
    projection_confidence = max(0.0, min(1.0, min(scale_x, scale_y) / max(scale_x, scale_y, 1e-6)))
    projected_box_before_margin = [crop_left, crop_top, crop_right, crop_bottom]
    safety_debug = _reference_crop_safety_margins(
        crop_width,
        crop_height,
        image_width,
        image_height,
        projected_box_before_margin,
        query_bounds,
        template_bounds,
        query_raw_bounds=query_raw_bounds,
        template_raw_bounds=template_raw_bounds,
        projection_confidence=projection_confidence,
    )
    safety_margin = safety_debug["adaptive_safety_margin"]
    crop_left = max(0, crop_left - safety_margin["left"])
    crop_top = max(0, crop_top - safety_margin["top"])
    crop_right = min(image_width, crop_right + safety_margin["right"])
    crop_bottom = min(image_height, crop_bottom + safety_margin["bottom"])
    if crop_right <= crop_left or crop_bottom <= crop_top:
        return {
            "passed": False,
            "reason": "projected_document_box_empty_after_margin",
            "original_box": original,
            "projected_box_before_margin": projected_box_before_margin,
            "safety_margin": safety_margin,
            "base_margin": safety_debug["base_margin"],
            "base_safety_margin": safety_debug["base_safety_margin"],
            "edge_margin_contribution": safety_debug["edge_margin_contribution"],
            "robust_shrink_margin_contribution": safety_debug["robust_shrink_margin_contribution"],
            "confidence_margin_contribution": safety_debug["confidence_margin_contribution"],
            "adaptive_safety_margin": safety_debug["adaptive_safety_margin"],
            "final_adaptive_margin": safety_debug["final_adaptive_margin"],
            "edge_uncertainty": safety_debug["edge_uncertainty"],
            "robust_shrink_uncertainty": safety_debug["robust_shrink_uncertainty"],
            "projection_confidence": safety_debug["projection_confidence"],
            "projected_box_after_margin": [crop_left, crop_top, crop_right, crop_bottom],
        }
    crop_width = crop_right - crop_left
    crop_height = crop_bottom - crop_top
    coverage_x = crop_width / max(1, image_width)
    coverage_y = crop_height / max(1, image_height)
    if (
        coverage_x < LAYOUT_REFERENCE_PROJECTED_MIN_COVERAGE_RATIO
        or coverage_y < LAYOUT_REFERENCE_PROJECTED_MIN_COVERAGE_RATIO
    ):
        return {
            "passed": False,
            "reason": "projected_document_box_too_small",
            "original_box": original,
            "final_box": [crop_left, crop_top, crop_right, crop_bottom],
            "projected_box_before_margin": projected_box_before_margin,
            "safety_margin": safety_margin,
            "base_margin": safety_debug["base_margin"],
            "base_safety_margin": safety_debug["base_safety_margin"],
            "edge_margin_contribution": safety_debug["edge_margin_contribution"],
            "robust_shrink_margin_contribution": safety_debug["robust_shrink_margin_contribution"],
            "confidence_margin_contribution": safety_debug["confidence_margin_contribution"],
            "adaptive_safety_margin": safety_debug["adaptive_safety_margin"],
            "final_adaptive_margin": safety_debug["final_adaptive_margin"],
            "edge_uncertainty": safety_debug["edge_uncertainty"],
            "robust_shrink_uncertainty": safety_debug["robust_shrink_uncertainty"],
            "projection_confidence": safety_debug["projection_confidence"],
            "projected_box_after_margin": [crop_left, crop_top, crop_right, crop_bottom],
            "coverage": [round(float(coverage_x), 6), round(float(coverage_y), 6)],
        }
    if (
        coverage_x > LAYOUT_REFERENCE_PROJECTED_MAX_COVERAGE_RATIO
        or coverage_y > LAYOUT_REFERENCE_PROJECTED_MAX_COVERAGE_RATIO
    ):
        return {
            "passed": False,
            "reason": "projected_document_box_too_large",
            "original_box": original,
            "final_box": [crop_left, crop_top, crop_right, crop_bottom],
            "projected_box_before_margin": projected_box_before_margin,
            "safety_margin": safety_margin,
            "base_margin": safety_debug["base_margin"],
            "base_safety_margin": safety_debug["base_safety_margin"],
            "edge_margin_contribution": safety_debug["edge_margin_contribution"],
            "robust_shrink_margin_contribution": safety_debug["robust_shrink_margin_contribution"],
            "confidence_margin_contribution": safety_debug["confidence_margin_contribution"],
            "adaptive_safety_margin": safety_debug["adaptive_safety_margin"],
            "final_adaptive_margin": safety_debug["final_adaptive_margin"],
            "edge_uncertainty": safety_debug["edge_uncertainty"],
            "robust_shrink_uncertainty": safety_debug["robust_shrink_uncertainty"],
            "projection_confidence": safety_debug["projection_confidence"],
            "projected_box_after_margin": [crop_left, crop_top, crop_right, crop_bottom],
            "coverage": [round(float(coverage_x), 6), round(float(coverage_y), 6)],
        }
    if (
        scale_x <= 0
        or scale_y <= 0
        or scale_x > LAYOUT_REFERENCE_PROJECTED_MAX_SCALE_RATIO
        or scale_y > LAYOUT_REFERENCE_PROJECTED_MAX_SCALE_RATIO
        or max(scale_x, scale_y) / max(min(scale_x, scale_y), 1e-6) > LAYOUT_REFERENCE_PROJECTED_MAX_SCALE_RATIO
    ):
        return {
            "passed": False,
            "reason": "projected_document_scale_unreliable",
            "original_box": original,
            "final_box": [crop_left, crop_top, crop_right, crop_bottom],
            "projected_box_before_margin": projected_box_before_margin,
            "safety_margin": safety_margin,
            "base_margin": safety_debug["base_margin"],
            "base_safety_margin": safety_debug["base_safety_margin"],
            "edge_margin_contribution": safety_debug["edge_margin_contribution"],
            "robust_shrink_margin_contribution": safety_debug["robust_shrink_margin_contribution"],
            "confidence_margin_contribution": safety_debug["confidence_margin_contribution"],
            "adaptive_safety_margin": safety_debug["adaptive_safety_margin"],
            "final_adaptive_margin": safety_debug["final_adaptive_margin"],
            "edge_uncertainty": safety_debug["edge_uncertainty"],
            "robust_shrink_uncertainty": safety_debug["robust_shrink_uncertainty"],
            "projection_confidence": safety_debug["projection_confidence"],
            "projected_box_after_margin": [crop_left, crop_top, crop_right, crop_bottom],
            "scale": [round(float(scale_x), 6), round(float(scale_y), 6)],
        }
    return {
        "passed": True,
        "reason": "projected_document_box_valid",
        "original_box": original,
        "final_box": [crop_left, crop_top, crop_right, crop_bottom],
        "projected_box_before_margin": projected_box_before_margin,
        "safety_margin": safety_margin,
        "base_margin": safety_debug["base_margin"],
        "base_safety_margin": safety_debug["base_safety_margin"],
        "edge_margin_contribution": safety_debug["edge_margin_contribution"],
        "robust_shrink_margin_contribution": safety_debug["robust_shrink_margin_contribution"],
        "confidence_margin_contribution": safety_debug["confidence_margin_contribution"],
        "adaptive_safety_margin": safety_debug["adaptive_safety_margin"],
        "final_adaptive_margin": safety_debug["final_adaptive_margin"],
        "edge_uncertainty": safety_debug["edge_uncertainty"],
        "robust_shrink_uncertainty": safety_debug["robust_shrink_uncertainty"],
        "projection_confidence": safety_debug["projection_confidence"],
        "projected_box_after_margin": [crop_left, crop_top, crop_right, crop_bottom],
        "clamped": [crop_left, crop_top, crop_right, crop_bottom] != original,
        "coverage": [round(float(coverage_x), 6), round(float(coverage_y), 6)],
        "scale": [round(float(scale_x), 6), round(float(scale_y), 6)],
    }


def _region_bbox(region: Dict[str, Any], key: str = "bbox") -> Optional[Dict[str, float]]:
    bbox = region.get(key) if isinstance(region.get(key), dict) else region.get("bbox")
    if not isinstance(bbox, dict):
        return None
    try:
        x = max(0.0, min(1.0, float(bbox.get("x_ratio") or 0.0)))
        y = max(0.0, min(1.0, float(bbox.get("y_ratio") or 0.0)))
        width = max(0.0, min(1.0, float(bbox.get("width_ratio") or 0.0)))
        height = max(0.0, min(1.0, float(bbox.get("height_ratio") or 0.0)))
    except (TypeError, ValueError):
        return None
    if width <= 0.0 or height <= 0.0:
        return None
    return {
        "left": x,
        "top": y,
        "right": min(1.0, x + width),
        "bottom": min(1.0, y + height),
        "width": width,
        "height": height,
        "center_x": min(1.0, x + width / 2.0),
        "center_y": min(1.0, y + height / 2.0),
        "aspect": width / max(height, 1e-6),
        "area": width * height,
    }


def _layout_correspondence_crop_box(
    query_signature: Optional[Dict[str, Any]],
    template_signature: Optional[Dict[str, Any]],
    image_width: int,
    image_height: int,
) -> Dict[str, Any]:
    debug: Dict[str, Any] = {
        "passed": False,
        "fallback_reason": None,
        "correspondence_count": 0,
        "inlier_count": 0,
        "inlier_ratio": 0.0,
        "scale_x": None,
        "scale_y": None,
        "translate_x": None,
        "translate_y": None,
        "rmse": None,
        "confidence": 0.0,
        "template_page_size": list(_signature_template_page_size(template_signature) or []),
        "projected_document_box": None,
        "final_crop_box": None,
        "document_layout_bounds": None,
        "expanded_layout_bounds": None,
        "matched_region_count": 0,
    }
    if not isinstance(query_signature, dict) or not isinstance(template_signature, dict):
        debug["fallback_reason"] = "missing_signature_for_correspondence_crop"
        return debug
    query_regions = [item for item in query_signature.get("regions", []) if isinstance(item, dict)]
    template_regions = [item for item in template_signature.get("regions", []) if isinstance(item, dict)]
    if len(query_regions) < 4 or len(template_regions) < 4:
        debug["fallback_reason"] = "insufficient_regions_for_correspondence_crop"
        return debug

    candidates: List[Dict[str, Any]] = []
    for qi, query_region in enumerate(query_regions):
        q_match_box = _region_bbox(query_region, "bbox")
        q_source_box = _region_bbox(query_region, "source_bbox")
        if not q_match_box or not q_source_box:
            continue
        q_label = str(query_region.get("label") or "text")
        for ti, template_region in enumerate(template_regions):
            if str(template_region.get("label") or "text") != q_label:
                continue
            t_match_box = _region_bbox(template_region, "bbox")
            t_source_box = _region_bbox(template_region, "source_bbox")
            if not t_match_box or not t_source_box:
                continue
            center_dist = abs(q_match_box["center_x"] - t_match_box["center_x"]) + abs(q_match_box["center_y"] - t_match_box["center_y"])
            size_delta = abs(q_match_box["width"] - t_match_box["width"]) + abs(q_match_box["height"] - t_match_box["height"])
            aspect_delta = abs(q_match_box["aspect"] - t_match_box["aspect"]) / max(t_match_box["aspect"], 1e-6)
            area_delta = abs(q_match_box["area"] - t_match_box["area"]) / max(t_match_box["area"], 1e-6)
            score = 1.0 - min(1.0, (center_dist * 1.8) + (size_delta * 1.4) + (aspect_delta * 0.20) + (area_delta * 0.08))
            if score < 0.45:
                continue
            candidates.append(
                {
                    "score": score,
                    "query_index": qi,
                    "template_index": ti,
                    "query_box": q_source_box,
                    "template_box": t_source_box,
                }
            )
    candidates.sort(key=lambda item: float(item["score"]), reverse=True)
    matches: List[Dict[str, Any]] = []
    used_query: set[int] = set()
    used_template: set[int] = set()
    for item in candidates:
        if int(item["query_index"]) in used_query or int(item["template_index"]) in used_template:
            continue
        matches.append(item)
        used_query.add(int(item["query_index"]))
        used_template.add(int(item["template_index"]))
        if len(matches) >= 30:
            break
    debug["correspondence_count"] = len(matches)
    if len(matches) < 4:
        debug["fallback_reason"] = "insufficient_correspondences"
        return debug

    def _fit_axis(template_values: np.ndarray, query_values: np.ndarray) -> tuple[float, float]:
        matrix = np.vstack([template_values, np.ones(len(template_values))]).T
        scale, translate = np.linalg.lstsq(matrix, query_values, rcond=None)[0]
        return float(scale), float(translate)

    template_x = np.array([float(item["template_box"]["center_x"]) for item in matches], dtype=np.float64)
    template_y = np.array([float(item["template_box"]["center_y"]) for item in matches], dtype=np.float64)
    query_x = np.array([float(item["query_box"]["center_x"]) for item in matches], dtype=np.float64)
    query_y = np.array([float(item["query_box"]["center_y"]) for item in matches], dtype=np.float64)
    scale_x, translate_x = _fit_axis(template_x, query_x)
    scale_y, translate_y = _fit_axis(template_y, query_y)
    pred_x = scale_x * template_x + translate_x
    pred_y = scale_y * template_y + translate_y
    residuals = np.sqrt(np.square(pred_x - query_x) + np.square(pred_y - query_y))
    median_residual = float(np.median(residuals)) if len(residuals) else 1.0
    inlier_threshold = max(0.025, median_residual * 2.5)
    inlier_mask = residuals <= inlier_threshold
    inlier_count = int(np.count_nonzero(inlier_mask))
    if inlier_count < 4:
        debug["fallback_reason"] = "insufficient_correspondence_inliers"
        return debug
    inlier_matches = [item for item, keep in zip(matches, inlier_mask) if bool(keep)]
    if inlier_count < len(matches):
        scale_x, translate_x = _fit_axis(template_x[inlier_mask], query_x[inlier_mask])
        scale_y, translate_y = _fit_axis(template_y[inlier_mask], query_y[inlier_mask])
        pred_x = scale_x * template_x[inlier_mask] + translate_x
        pred_y = scale_y * template_y[inlier_mask] + translate_y
        residuals = np.sqrt(np.square(pred_x - query_x[inlier_mask]) + np.square(pred_y - query_y[inlier_mask]))
    rmse = float(np.sqrt(np.mean(np.square(residuals)))) if len(residuals) else 1.0
    inlier_ratio = inlier_count / max(len(matches), 1)
    if scale_x <= 0.05 or scale_y <= 0.05 or scale_x > 3.0 or scale_y > 3.0:
        debug["fallback_reason"] = "correspondence_scale_unreliable"
        return debug
    scale_consistency = min(scale_x, scale_y) / max(scale_x, scale_y, 1e-6)
    confidence = max(0.0, min(1.0, inlier_ratio * scale_consistency * (1.0 - min(1.0, rmse / 0.08))))
    if confidence < 0.45 or rmse > 0.08:
        debug["fallback_reason"] = "correspondence_confidence_too_low"
        debug.update({"rmse": round(rmse, 6), "confidence": round(confidence, 6), "inlier_count": inlier_count, "inlier_ratio": round(inlier_ratio, 6)})
        return debug

    left_ratio = translate_x
    top_ratio = translate_y
    right_ratio = scale_x + translate_x
    bottom_ratio = scale_y + translate_y
    projected = [
        int(round(left_ratio * image_width)),
        int(round(top_ratio * image_height)),
        int(round(right_ratio * image_width)),
        int(round(bottom_ratio * image_height)),
    ]
    document_bounds = _union_region_boxes([item["query_box"] for item in inlier_matches])
    if not document_bounds:
        debug["fallback_reason"] = "correspondence_document_bounds_unavailable"
        return debug
    old_crop_debug = _old_layout_crop_box_from_bounds(document_bounds, image_width, image_height)
    if not old_crop_debug.get("passed"):
        debug["fallback_reason"] = old_crop_debug.get("reason") or "old_layout_correspondence_crop_unavailable"
        debug["old_layout_crop"] = old_crop_debug
        return debug
    final_box = old_crop_debug["final_box"]
    expanded_bounds = {
        "left": final_box[0] / max(1, image_width),
        "top": final_box[1] / max(1, image_height),
        "right": final_box[2] / max(1, image_width),
        "bottom": final_box[3] / max(1, image_height),
    }
    expanded_bounds["width"] = expanded_bounds["right"] - expanded_bounds["left"]
    expanded_bounds["height"] = expanded_bounds["bottom"] - expanded_bounds["top"]
    if final_box[2] <= final_box[0] or final_box[3] <= final_box[1]:
        debug["fallback_reason"] = "correspondence_crop_empty"
        return debug
    coverage_x = (final_box[2] - final_box[0]) / max(1, image_width)
    coverage_y = (final_box[3] - final_box[1]) / max(1, image_height)
    if coverage_x < LAYOUT_REFERENCE_PROJECTED_MIN_COVERAGE_RATIO or coverage_y < LAYOUT_REFERENCE_PROJECTED_MIN_COVERAGE_RATIO:
        debug["fallback_reason"] = "correspondence_crop_too_small"
        return debug
    if coverage_x > LAYOUT_REFERENCE_PROJECTED_MAX_COVERAGE_RATIO or coverage_y > LAYOUT_REFERENCE_PROJECTED_MAX_COVERAGE_RATIO:
        debug["fallback_reason"] = "correspondence_crop_too_large"
        return debug
    debug.update(
        {
            "passed": True,
            "reason": "layout_correspondence_crop_valid",
            "inlier_count": inlier_count,
            "inlier_ratio": round(inlier_ratio, 6),
            "scale_x": round(float(scale_x), 6),
            "scale_y": round(float(scale_y), 6),
            "translate_x": round(float(translate_x), 6),
            "translate_y": round(float(translate_y), 6),
            "rmse": round(float(rmse), 6),
            "confidence": round(float(confidence), 6),
            "projected_document_box": projected,
            "final_crop_box": final_box,
            "document_layout_bounds": {key: round(float(value), 6) for key, value in document_bounds.items()},
            "expanded_layout_bounds": {key: round(float(value), 6) for key, value in expanded_bounds.items()},
            "matched_region_count": int(inlier_count),
            "safety_margin": old_crop_debug.get("padding"),
            "old_layout_crop": old_crop_debug,
            "coverage": [round(float(coverage_x), 6), round(float(coverage_y), 6)],
        }
    )
    return debug


def _template_guided_layout_bounds(
    query_signature: Optional[Dict[str, Any]],
    template_signature: Optional[Dict[str, Any]],
    raw_bounds: Optional[Dict[str, float]],
) -> Dict[str, Any]:
    debug: Dict[str, Any] = {
        "attempted": True,
        "used": False,
        "fallback_reason": None,
        "correspondence_count": 0,
        "inlier_count": 0,
        "inlier_ratio": 0.0,
        "rmse": None,
        "confidence": 0.0,
        "document_layout_bounds": raw_bounds,
        "raw_layout_bounds": raw_bounds,
    }
    if not isinstance(query_signature, dict) or not isinstance(template_signature, dict):
        debug["fallback_reason"] = "missing_signature_for_template_guided_bounds"
        return debug
    query_regions = [item for item in query_signature.get("regions", []) if isinstance(item, dict)]
    template_regions = [item for item in template_signature.get("regions", []) if isinstance(item, dict)]
    if len(query_regions) < 4 or len(template_regions) < 4:
        debug["fallback_reason"] = "insufficient_regions_for_template_guided_bounds"
        return debug

    candidates: List[Dict[str, Any]] = []
    for qi, query_region in enumerate(query_regions):
        q_match_box = _region_bbox(query_region, "bbox")
        q_source_box = _region_bbox(query_region, "source_bbox")
        if not q_match_box or not q_source_box:
            continue
        q_label = str(query_region.get("label") or "text")
        for ti, template_region in enumerate(template_regions):
            if str(template_region.get("label") or "text") != q_label:
                continue
            t_match_box = _region_bbox(template_region, "bbox")
            if not t_match_box:
                continue
            center_dist = abs(q_match_box["center_x"] - t_match_box["center_x"]) + abs(q_match_box["center_y"] - t_match_box["center_y"])
            size_delta = abs(q_match_box["width"] - t_match_box["width"]) + abs(q_match_box["height"] - t_match_box["height"])
            aspect_delta = abs(q_match_box["aspect"] - t_match_box["aspect"]) / max(t_match_box["aspect"], 1e-6)
            area_delta = abs(q_match_box["area"] - t_match_box["area"]) / max(t_match_box["area"], 1e-6)
            score = 1.0 - min(1.0, (center_dist * 1.8) + (size_delta * 1.4) + (aspect_delta * 0.20) + (area_delta * 0.08))
            if score < 0.45:
                continue
            candidates.append(
                {
                    "score": score,
                    "query_index": qi,
                    "template_index": ti,
                    "query_box": q_source_box,
                    "template_box": t_match_box,
                }
            )
    candidates.sort(key=lambda item: float(item["score"]), reverse=True)
    matches: List[Dict[str, Any]] = []
    used_query: set[int] = set()
    used_template: set[int] = set()
    for item in candidates:
        if int(item["query_index"]) in used_query or int(item["template_index"]) in used_template:
            continue
        matches.append(item)
        used_query.add(int(item["query_index"]))
        used_template.add(int(item["template_index"]))
        if len(matches) >= 30:
            break
    debug["correspondence_count"] = len(matches)
    if len(matches) < 4:
        debug["fallback_reason"] = "insufficient_template_guided_correspondences"
        return debug

    def _fit_axis(template_values: np.ndarray, query_values: np.ndarray) -> tuple[float, float]:
        matrix = np.vstack([template_values, np.ones(len(template_values))]).T
        scale, translate = np.linalg.lstsq(matrix, query_values, rcond=None)[0]
        return float(scale), float(translate)

    template_x = np.array([float(item["template_box"]["center_x"]) for item in matches], dtype=np.float64)
    template_y = np.array([float(item["template_box"]["center_y"]) for item in matches], dtype=np.float64)
    query_x = np.array([float(item["query_box"]["center_x"]) for item in matches], dtype=np.float64)
    query_y = np.array([float(item["query_box"]["center_y"]) for item in matches], dtype=np.float64)
    scale_x, translate_x = _fit_axis(template_x, query_x)
    scale_y, translate_y = _fit_axis(template_y, query_y)
    pred_x = scale_x * template_x + translate_x
    pred_y = scale_y * template_y + translate_y
    residuals = np.sqrt(np.square(pred_x - query_x) + np.square(pred_y - query_y))
    median_residual = float(np.median(residuals)) if len(residuals) else 1.0
    inlier_threshold = max(0.025, median_residual * 2.5)
    inlier_mask = residuals <= inlier_threshold
    inlier_count = int(np.count_nonzero(inlier_mask))
    if inlier_count < 4:
        debug["fallback_reason"] = "insufficient_template_guided_inliers"
        return debug
    inlier_matches = [item for item, keep in zip(matches, inlier_mask) if bool(keep)]
    if inlier_count < len(matches):
        scale_x, translate_x = _fit_axis(template_x[inlier_mask], query_x[inlier_mask])
        scale_y, translate_y = _fit_axis(template_y[inlier_mask], query_y[inlier_mask])
        pred_x = scale_x * template_x[inlier_mask] + translate_x
        pred_y = scale_y * template_y[inlier_mask] + translate_y
        residuals = np.sqrt(np.square(pred_x - query_x[inlier_mask]) + np.square(pred_y - query_y[inlier_mask]))
    rmse = float(np.sqrt(np.mean(np.square(residuals)))) if len(residuals) else 1.0
    inlier_ratio = inlier_count / max(len(matches), 1)
    if scale_x <= 0.05 or scale_y <= 0.05 or scale_x > 3.0 or scale_y > 3.0:
        debug["fallback_reason"] = "template_guided_scale_unreliable"
        return debug
    scale_consistency = min(scale_x, scale_y) / max(scale_x, scale_y, 1e-6)
    confidence = max(0.0, min(1.0, inlier_ratio * scale_consistency * (1.0 - min(1.0, rmse / 0.08))))
    debug.update(
        {
            "inlier_count": int(inlier_count),
            "inlier_ratio": round(float(inlier_ratio), 6),
            "scale_x": round(float(scale_x), 6),
            "scale_y": round(float(scale_y), 6),
            "translate_x": round(float(translate_x), 6),
            "translate_y": round(float(translate_y), 6),
            "rmse": round(float(rmse), 6),
            "confidence": round(float(confidence), 6),
        }
    )
    if confidence < 0.45 or rmse > 0.08:
        debug["fallback_reason"] = "template_guided_confidence_too_low"
        return debug

    inlier_bounds = _union_region_boxes([item["query_box"] for item in inlier_matches])
    if not inlier_bounds:
        debug["fallback_reason"] = "template_guided_bounds_unavailable"
        return debug
    include_pad_x = max(0.04, float(inlier_bounds.get("width") or 0.0) * 0.12)
    include_pad_y = max(0.04, float(inlier_bounds.get("height") or 0.0) * 0.12)
    include_bounds = {
        "left": max(0.0, float(inlier_bounds["left"]) - include_pad_x),
        "top": max(0.0, float(inlier_bounds["top"]) - include_pad_y),
        "right": min(1.0, float(inlier_bounds["right"]) + include_pad_x),
        "bottom": min(1.0, float(inlier_bounds["bottom"]) + include_pad_y),
    }
    query_boxes: List[Dict[str, float]] = []
    for region in query_regions:
        box = _region_bbox(region, "source_bbox")
        if not box:
            continue
        if (
            include_bounds["left"] <= box["center_x"] <= include_bounds["right"]
            and include_bounds["top"] <= box["center_y"] <= include_bounds["bottom"]
        ):
            query_boxes.append(box)
    guided_bounds = _union_region_boxes(query_boxes) or inlier_bounds
    if not guided_bounds:
        debug["fallback_reason"] = "template_guided_union_unavailable"
        return debug
    raw_area = float((raw_bounds or {}).get("width") or 0.0) * float((raw_bounds or {}).get("height") or 0.0)
    guided_area = float(guided_bounds.get("width") or 0.0) * float(guided_bounds.get("height") or 0.0)
    if raw_area > 0.0 and guided_area < raw_area * 0.55:
        debug["fallback_reason"] = "template_guided_bounds_too_small"
        debug["guided_bounds"] = guided_bounds
        return debug
    debug.update(
        {
            "used": True,
            "reason": "template_guided_layout_bounds",
            "document_layout_bounds": guided_bounds,
            "inlier_layout_bounds": inlier_bounds,
            "include_bounds": include_bounds,
            "included_region_count": len(query_boxes),
        }
    )
    return debug


def _signature_template_page_size(signature: Optional[Dict[str, Any]]) -> Optional[tuple[int, int]]:
    if not isinstance(signature, dict):
        return None
    normalization = signature.get("layout_space_normalization")
    if isinstance(normalization, dict):
        try:
            width = int(float(normalization.get("original_image_width") or 0))
            height = int(float(normalization.get("original_image_height") or 0))
        except (TypeError, ValueError):
            width = 0
            height = 0
        if width > 0 and height > 0:
            return width, height
    try:
        width = int(float(signature.get("image_width") or 0))
        height = int(float(signature.get("image_height") or 0))
    except (TypeError, ValueError):
        return None
    if width > 0 and height > 0:
        return width, height
    return None


def _detect_full_frame_document(
    query_bounds: Optional[Dict[str, float]],
    template_bounds: Optional[Dict[str, float]],
    query_signature: Optional[Dict[str, Any]],
    template_signature: Optional[Dict[str, Any]],
    query_raw_bounds: Optional[Dict[str, float]] = None,
    template_raw_bounds: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    debug: Dict[str, Any] = {
        "full_frame_document_detected": False,
        "full_frame_confidence": 0.0,
        "crop_required": True,
        "crop_skip_reason": None,
    }
    if not isinstance(query_bounds, dict) or not isinstance(template_bounds, dict):
        debug["crop_skip_reason"] = "missing_bounds_for_full_frame_check"
        return debug
    query_page_size = _signature_template_page_size(query_signature)
    template_page_size = _signature_template_page_size(template_signature)
    if not query_page_size or not template_page_size:
        debug["crop_skip_reason"] = "missing_page_size_for_full_frame_check"
        return debug
    query_w, query_h = query_page_size
    template_w, template_h = template_page_size
    if query_w <= 0 or query_h <= 0 or template_w <= 0 or template_h <= 0:
        debug["crop_skip_reason"] = "invalid_page_size_for_full_frame_check"
        return debug

    query_aspect = query_w / max(1.0, float(query_h))
    template_aspect = template_w / max(1.0, float(template_h))
    aspect_delta = abs(query_aspect - template_aspect) / max(template_aspect, 1e-6)
    query_coverage_x = float(query_bounds.get("width") or 0.0)
    query_coverage_y = float(query_bounds.get("height") or 0.0)
    template_coverage_x = float(template_bounds.get("width") or 0.0)
    template_coverage_y = float(template_bounds.get("height") or 0.0)
    raw_query_coverage_x = float((query_raw_bounds or {}).get("width") or query_coverage_x)
    raw_query_coverage_y = float((query_raw_bounds or {}).get("height") or query_coverage_y)
    raw_template_coverage_x = float((template_raw_bounds or {}).get("width") or template_coverage_x)
    raw_template_coverage_y = float((template_raw_bounds or {}).get("height") or template_coverage_y)
    coverage_ratio_x = query_coverage_x / max(template_coverage_x, 1e-6)
    coverage_ratio_y = query_coverage_y / max(template_coverage_y, 1e-6)
    edge_delta = max(
        abs(float(query_bounds.get("left") or 0.0) - float(template_bounds.get("left") or 0.0)),
        abs(float(query_bounds.get("right") or 0.0) - float(template_bounds.get("right") or 0.0)),
        abs(float(query_bounds.get("top") or 0.0) - float(template_bounds.get("top") or 0.0)),
        abs(float(query_bounds.get("bottom") or 0.0) - float(template_bounds.get("bottom") or 0.0)),
    )
    coverage_penalty = max(
        0.0,
        abs(1.0 - coverage_ratio_x),
        abs(1.0 - coverage_ratio_y),
    )
    raw_frame_coverage = min(raw_query_coverage_x, raw_query_coverage_y)
    robust_frame_coverage = min(query_coverage_x, query_coverage_y)
    raw_template_frame_coverage = min(raw_template_coverage_x, raw_template_coverage_y)
    raw_edge_inset = max(
        0.0,
        float((query_raw_bounds or query_bounds).get("left") or 0.0),
        float((query_raw_bounds or query_bounds).get("top") or 0.0),
        1.0 - float((query_raw_bounds or query_bounds).get("right") or 1.0),
        1.0 - float((query_raw_bounds or query_bounds).get("bottom") or 1.0),
    )
    raw_full_frame_evidence = raw_frame_coverage >= 0.96 and raw_edge_inset <= 0.04 and aspect_delta <= 0.06
    robust_geometry_evidence = edge_delta <= 0.055 and 0.90 <= coverage_ratio_x <= 1.12 and 0.90 <= coverage_ratio_y <= 1.12
    confidence = max(
        0.0,
        min(
            1.0,
            max(
                1.0 - max(aspect_delta / 0.10, edge_delta / 0.08, coverage_penalty / 0.15),
                0.85 if raw_full_frame_evidence and raw_template_frame_coverage >= 0.90 else 0.0,
            ),
        ),
    )
    full_frame = raw_full_frame_evidence
    debug.update(
        {
            "full_frame_document_detected": bool(full_frame),
            "full_frame_confidence": round(float(confidence), 6),
            "crop_required": not bool(full_frame),
            "crop_skip_reason": "full_frame_document_matches_template_geometry" if full_frame else None,
            "full_frame_geometry": {
                "query_page_size": [int(query_w), int(query_h)],
                "template_page_size": [int(template_w), int(template_h)],
                "query_aspect": round(float(query_aspect), 6),
                "template_aspect": round(float(template_aspect), 6),
                "aspect_delta": round(float(aspect_delta), 6),
                "coverage_ratio": [round(float(coverage_ratio_x), 6), round(float(coverage_ratio_y), 6)],
                "edge_delta": round(float(edge_delta), 6),
            },
            "raw_frame_coverage": round(float(raw_frame_coverage), 6),
            "robust_frame_coverage": round(float(robust_frame_coverage), 6),
            "raw_edge_inset": round(float(raw_edge_inset), 6),
            "full_frame_evidence": {
                "raw_full_frame": bool(raw_full_frame_evidence),
                "robust_geometry": bool(robust_geometry_evidence),
                "robust_geometry_ignored_for_full_frame": True,
                "raw_template_frame_coverage": round(float(raw_template_frame_coverage), 6),
            },
        }
    )
    return debug


def _layout_reference_adjusted_image(
    image_path: str,
    query_signature: Optional[Dict[str, Any]],
    template_signature: Optional[Dict[str, Any]],
    output_dir: Path,
    template_id: Optional[str],
    page_number: int,
) -> Dict[str, Any]:
    debug: Dict[str, Any] = {
        "enabled": LAYOUT_REFERENCE_CROP_ENABLED,
        "applied": False,
        "reason": "not_attempted",
        "full_frame_document_detected": False,
        "full_frame_confidence": 0.0,
        "crop_required": True,
        "crop_skip_reason": None,
    }
    if not LAYOUT_REFERENCE_CROP_ENABLED:
        debug["reason"] = "disabled"
        return debug
    query_robust_debug = _signature_robust_content_bounds(query_signature)
    template_robust_debug = _signature_robust_content_bounds(template_signature)
    query_raw_bounds = _signature_content_bounds(query_signature) or query_robust_debug.get("raw_content_bounds")
    template_raw_bounds = template_robust_debug.get("raw_content_bounds")
    query_bounds = query_raw_bounds
    template_bounds = template_robust_debug.get("robust_content_bounds") or template_raw_bounds
    debug["query_bounds"] = query_bounds
    debug["template_bounds"] = template_bounds
    debug["query_content_bounds"] = query_bounds
    debug["template_content_bounds"] = template_bounds
    debug["raw_content_bounds"] = {
        "query": query_raw_bounds,
        "template": template_raw_bounds,
    }
    debug["robust_content_bounds"] = {
        "query": query_robust_debug.get("robust_content_bounds"),
        "template": template_robust_debug.get("robust_content_bounds"),
    }
    debug["regions_used"] = {
        "query": query_robust_debug.get("regions_used"),
        "template": template_robust_debug.get("regions_used"),
    }
    debug["regions_excluded"] = {
        "query": query_robust_debug.get("regions_excluded"),
        "template": template_robust_debug.get("regions_excluded"),
    }
    robust_fallback_reasons = {
        "query": query_robust_debug.get("fallback_reason"),
        "template": template_robust_debug.get("fallback_reason"),
    }
    if robust_fallback_reasons["query"] or robust_fallback_reasons["template"]:
        debug["robust_bounds_fallback_reason"] = robust_fallback_reasons
    if not query_bounds or not template_bounds:
        debug["reason"] = "missing_signature_bounds"
        debug["fallback_reason"] = "missing_signature_bounds"
        return debug
    image = cv2.imread(str(image_path))
    if image is None:
        debug["reason"] = "image_unreadable"
        debug["fallback_reason"] = "image_unreadable"
        return debug
    image_height, image_width = image.shape[:2]
    debug["multi_region_attempted"] = False
    debug["multi_region_passed"] = False
    debug["layout_correspondence_crop"] = {
        "attempted": False,
        "used": False,
        "reason": "disabled_for_generic_layout_union_crop",
    }
    debug["correspondence_count"] = None
    debug["inlier_count"] = None
    debug["inlier_ratio"] = None
    debug["scale_x"] = None
    debug["scale_y"] = None
    debug["translate_x"] = None
    debug["translate_y"] = None
    debug["rmse"] = None
    debug["confidence"] = None
    debug["projected_document_box"] = None
    debug["classified_full_frame"] = False
    full_frame_debug = _detect_full_frame_document(
        query_bounds,
        template_bounds,
        query_signature,
        template_signature,
        query_raw_bounds=query_raw_bounds if isinstance(query_raw_bounds, dict) else None,
        template_raw_bounds=template_raw_bounds if isinstance(template_raw_bounds, dict) else None,
    )
    debug.update(full_frame_debug)
    if full_frame_debug.get("full_frame_document_detected"):
        debug["reason"] = "full_frame_document_no_crop_required"
        debug["fallback_reason"] = None
        return debug

    template_guided_debug = _template_guided_layout_bounds(query_signature, template_signature, query_bounds)
    debug["template_guided_layout_bounds"] = template_guided_debug
    crop_bounds = (
        template_guided_debug.get("document_layout_bounds")
        if template_guided_debug.get("used") and isinstance(template_guided_debug.get("document_layout_bounds"), dict)
        else query_bounds
    )
    debug["template_guided_crop_used"] = bool(template_guided_debug.get("used"))
    debug["template_guided_fallback_reason"] = template_guided_debug.get("fallback_reason")

    generic_crop_debug = _generic_layout_union_crop_box_from_bounds(crop_bounds, image_width, image_height)
    debug["generic_layout_union_crop"] = generic_crop_debug
    if not generic_crop_debug.get("passed"):
        debug["reason"] = generic_crop_debug.get("reason") or "generic_layout_union_crop_unavailable"
        debug["fallback_reason"] = debug["reason"]
        return debug
    final_box = generic_crop_debug.get("final_box")
    expanded_layout_bounds = None
    if isinstance(final_box, list) and len(final_box) == 4:
        expanded_layout_bounds = {
            "left": final_box[0] / max(1, image_width),
            "top": final_box[1] / max(1, image_height),
            "right": final_box[2] / max(1, image_width),
            "bottom": final_box[3] / max(1, image_height),
            "width": (final_box[2] - final_box[0]) / max(1, image_width),
            "height": (final_box[3] - final_box[1]) / max(1, image_height),
        }
    crop_debug = {
        "passed": True,
        "reason": "layout_union_crop_applied",
        "method": "template_guided_layout_union_bounds_crop" if template_guided_debug.get("used") else "generic_layout_union_bounds_crop",
        "final_box": generic_crop_debug.get("final_box"),
        "document_layout_bounds": crop_bounds,
        "raw_document_layout_bounds": query_bounds,
        "expanded_layout_bounds": expanded_layout_bounds,
        "matched_region_count": (
            template_guided_debug.get("included_region_count")
            if template_guided_debug.get("used")
            else query_bounds.get("region_count")
        ),
        "safety_margin": generic_crop_debug.get("padding"),
        "layout_union_box": generic_crop_debug.get("layout_union_box"),
        "margin_x_px": generic_crop_debug.get("margin_x_px"),
        "margin_y_px": generic_crop_debug.get("margin_y_px"),
        "expanded_crop_box": generic_crop_debug.get("expanded_crop_box"),
        "final_crop_coverage": generic_crop_debug.get("final_crop_coverage"),
        "generic_layout_crop": generic_crop_debug,
        "template_guided_layout_bounds": template_guided_debug,
    }
    debug["crop_required"] = True
    debug["fallback_reason"] = None
    debug["crop"] = crop_debug
    if not crop_debug.get("passed"):
        debug["reason"] = str(crop_debug.get("reason") or "reference_crop_invalid")
        debug["fallback_reason"] = debug.get("fallback_reason") or debug["reason"]
        return debug

    crop_left, crop_top, crop_right, crop_bottom = crop_debug["final_box"]
    debug["final_crop_box"] = [int(crop_left), int(crop_top), int(crop_right), int(crop_bottom)]
    debug["crop_size"] = [int(crop_right - crop_left), int(crop_bottom - crop_top)]
    debug["crop_image_size"] = [int(crop_right - crop_left), int(crop_bottom - crop_top)]
    debug["final_crop"] = {
        "matched_region_count": crop_debug.get("matched_region_count"),
        "document_layout_bounds": crop_debug.get("document_layout_bounds") or debug.get("query_content_bounds"),
        "expanded_layout_bounds": crop_debug.get("expanded_layout_bounds"),
        "safety_margin": crop_debug.get("safety_margin") or debug.get("safety_margin"),
        "final_crop_box": [int(crop_left), int(crop_top), int(crop_right), int(crop_bottom)],
        "final_crop_size": [int(crop_right - crop_left), int(crop_bottom - crop_top)],
        "template_page_size": debug.get("template_page_size"),
        "selected_processing_source": "layout_reference_crop",
        "layout_union_box": crop_debug.get("layout_union_box"),
        "margin_x_px": crop_debug.get("margin_x_px"),
        "margin_y_px": crop_debug.get("margin_y_px"),
        "expanded_crop_box": crop_debug.get("expanded_crop_box"),
        "final_crop_coverage": crop_debug.get("final_crop_coverage"),
        "generic_layout_crop": crop_debug.get("generic_layout_crop"),
    }
    crop_ratio = {
        "left": crop_left / max(1, image_width),
        "top": crop_top / max(1, image_height),
        "right": crop_right / max(1, image_width),
        "bottom": crop_bottom / max(1, image_height),
        "width": (crop_right - crop_left) / max(1, image_width),
        "height": (crop_bottom - crop_top) / max(1, image_height),
    }
    delta = max(
        crop_left / max(1, image_width),
        crop_top / max(1, image_height),
        (image_width - crop_right) / max(1, image_width),
        (image_height - crop_bottom) / max(1, image_height),
    )
    debug["max_delta_ratio"] = round(float(delta), 6)

    cropped = image[crop_top:crop_bottom, crop_left:crop_right].copy()
    if cropped.size == 0:
        debug["reason"] = "reference_crop_empty"
        debug["fallback_reason"] = debug["reason"]
        return debug
    template_page_size = _signature_template_page_size(template_signature)
    debug["template_page_size"] = list(template_page_size) if template_page_size else None
    if isinstance(debug.get("final_crop"), dict):
        debug["final_crop"]["template_page_size"] = debug["template_page_size"]
    output_image = cropped
    resize_applied = False
    if template_page_size:
        target_width, target_height = template_page_size
        crop_height, crop_width = cropped.shape[:2]
        if crop_width > 0 and crop_height > 0 and (crop_width != target_width or crop_height != target_height):
            interpolation = cv2.INTER_AREA if crop_width > target_width or crop_height > target_height else cv2.INTER_LINEAR
            output_image = cv2.resize(cropped, (target_width, target_height), interpolation=interpolation)
            resize_applied = True
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{_safe_file_token(template_id)}_page_{page_number}_layout_reference_crop.png"
    if not cv2.imwrite(str(output_path), output_image):
        debug["reason"] = "reference_crop_write_failed"
        debug["fallback_reason"] = debug["reason"]
        return debug
    output_height, output_width = output_image.shape[:2]
    adjusted_signature = _rebase_layout_signature_to_crop(query_signature, crop_ratio, output_width, output_height)
    debug["adjusted_query_signature_source"] = "layout_reference_crop_rebased" if adjusted_signature else None
    debug["adjusted_query_signature_region_count"] = int((adjusted_signature or {}).get("region_count") or 0) if adjusted_signature else 0
    if adjusted_signature:
        debug["_adjusted_query_signature"] = adjusted_signature
    debug.update(
        {
            "applied": True,
            "reason": "layout_reference_crop_applied",
            "image_path": str(output_path),
            "preview_url": _detection_preview_url(str(output_path)),
            "source_image_size": [int(image_width), int(image_height)],
            "crop_image_size": [int(crop_right - crop_left), int(crop_bottom - crop_top)],
            "output_image_size": [int(output_width), int(output_height)],
            "processing_image_size": [int(output_width), int(output_height)],
            "resize_applied": resize_applied,
        }
    )
    return debug


def _layout_signature_for_image_path(image_path: str, timing: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    Image = _load_pillow()
    if Image is None:
        raise HTTPException(status_code=500, detail="Layout signature generation requires Pillow")
    breakdown: Dict[str, Any] = {"image_path": str(image_path)}
    try:
        read_started = time.perf_counter()
        image = Image.open(image_path).convert("RGB")
        breakdown["image_read_ms"] = _ms(time.perf_counter() - read_started)
        convert_started = time.perf_counter()
        opencv_img = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
        breakdown["opencv_convert_ms"] = _ms(time.perf_counter() - convert_started)
        breakdown["image_size"] = [int(image.width), int(image.height)]
    except Exception as error:
        raise HTTPException(status_code=400, detail="Unable to read image for layout signature") from error
    step_started = time.perf_counter()
    layout_runtime_timing: Dict[str, Any] = {}
    layout_items = analyze_layout_signature(opencv_img, timing=layout_runtime_timing)
    breakdown["analyze_layout_signature_ms"] = _ms(time.perf_counter() - step_started)
    breakdown["analyze_layout_signature"] = layout_runtime_timing
    if timing is not None:
        timing["layout_analysis"] = timing.get("layout_analysis", 0.0) + (time.perf_counter() - step_started)
        timing.setdefault("layout_analysis_breakdown", []).append(breakdown)
    step_started = time.perf_counter()
    signature = build_layout_signature(layout_items)
    if timing is not None:
        timing["signature_build"] = timing.get("signature_build", 0.0) + (time.perf_counter() - step_started)
        breakdown["signature_build_ms"] = _ms(time.perf_counter() - step_started)
    return signature


def _detection_debug_url(path_value: Optional[str]) -> Optional[str]:
    if not SAVE_DEBUG_ARTIFACTS:
        return None
    if not path_value:
        return None
    try:
        path = Path(path_value).resolve()
        root = _storage_path().resolve()
        relative = path.relative_to(root)
    except (ValueError, OSError):
        return None
    return f"/debug/detection-queries/{relative.as_posix()}"


def _detection_preview_url(path_value: Optional[str]) -> Optional[str]:
    debug_url = _detection_debug_url(path_value)
    if debug_url:
        return debug_url
    if not path_value:
        return None
    try:
        path = Path(path_value)
        if not path.exists():
            return None
        return _image_to_data_url(path)
    except Exception:
        return None


def _save_query_image(query_id: str, image_bytes: bytes, page_index: int = 1) -> Path:
    Image = _load_pillow()
    if Image is None:
        raise HTTPException(status_code=400, detail="Image validation is unavailable because Pillow is not installed")
    if not image_bytes:
        raise HTTPException(status_code=400, detail="Uploaded image is empty")

    try:
        image = Image.open(io.BytesIO(image_bytes))
        if image.mode != "RGB":
            image = image.convert("RGB")
    except Exception as error:
        raise HTTPException(status_code=400, detail="Uploaded file is not a valid image") from error

    output_dir = _storage_path() / query_id
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"page_{page_index}.png"
    image.save(output_path, format="PNG")
    return output_path


def _convert_pdf_to_page_images(query_id: str, pdf_bytes: bytes) -> List[Path]:
    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="Uploaded PDF is empty")

    try:
        import fitz
    except ImportError as error:
        raise HTTPException(
            status_code=501,
            detail="PDF detection requires PyMuPDF. Install the 'pymupdf' package on the backend.",
        ) from error

    output_dir = _storage_path() / query_id
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        document = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as error:
        raise HTTPException(status_code=400, detail="Uploaded file is not a valid PDF") from error

    if document.page_count == 0:
        document.close()
        raise HTTPException(status_code=400, detail="Uploaded PDF has no pages")

    page_paths = []
    try:
        for index in range(document.page_count):
            page = document.load_page(index)
            pixmap = page.get_pixmap(matrix=fitz.Matrix(PDF_RENDER_SCALE, PDF_RENDER_SCALE), alpha=False)
            output_path = output_dir / f"page_{index + 1}.png"
            pixmap.save(str(output_path))
            page_paths.append(output_path)
    finally:
        document.close()
    return page_paths


def _prepare_query_pages(query_id: str, file_bytes: bytes, timing: Optional[Dict[str, float]] = None) -> List[Path]:
    step_started = time.perf_counter()
    if file_bytes.lstrip().startswith(b"%PDF"):
        pages = _convert_pdf_to_page_images(query_id, file_bytes)
        if timing is not None:
            timing["prepare_pdf_convert"] = timing.get("prepare_pdf_convert", 0.0) + (time.perf_counter() - step_started)
        return pages
    pages = [_save_query_image(query_id, file_bytes, 1)]
    if timing is not None:
        timing["prepare_image_save"] = timing.get("prepare_image_save", 0.0) + (time.perf_counter() - step_started)
    return pages


def _is_persisted_query_page(path: Path) -> bool:
    return bool(re.match(r"^(?:page|processing_page)_\d+\.(png|jpg|jpeg|webp)$", path.name, re.IGNORECASE))


def _cleanup_transient_query_artifacts(query_id: str) -> None:
    query_dir = _storage_path() / query_id
    if not query_dir.exists():
        return
    for child in query_dir.iterdir():
        try:
            if child.is_file() and _is_persisted_query_page(child):
                continue
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)
        except OSError:
            pass


def _persist_selected_processing_pages(
    query_id: str,
    pages: List[Dict[str, Any]],
    best_candidate: Optional[Dict[str, Any]],
    normalized_pages: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    if not isinstance(best_candidate, dict) or not best_candidate.get("final_passed"):
        return []
    template_id = best_candidate.get("template_id")
    if not template_id:
        return []

    original_paths = {
        int(page.get("page_index") or 0): str(page.get("original_path") or "")
        for page in normalized_pages
        if isinstance(page, dict)
    }
    query_dir = _storage_path() / query_id
    query_dir.mkdir(parents=True, exist_ok=True)
    processing_pages: List[Dict[str, Any]] = []

    for page in pages:
        page_index = int(page.get("page_index") or 0)
        page_candidate = next(
            (
                candidate
                for candidate in page.get("candidates", [])
                if isinstance(candidate, dict)
                and candidate.get("template_id") == template_id
                and candidate.get("final_passed")
            ),
            None,
        )
        if page_candidate is None:
            continue

        candidate_page_index = int(page_candidate.get("query_page_index") or page_index or 0)
        if candidate_page_index <= 0:
            continue

        source_path_text = original_paths.get(candidate_page_index) or ""
        source_path = Path(source_path_text).resolve() if source_path_text else None
        source_reference = source_path.name if source_path and source_path.exists() else f"page_{candidate_page_index}.png"
        extraction_path_text = str(page_candidate.get("extraction_image_path") or "").strip()
        extraction_path = Path(extraction_path_text).resolve() if extraction_path_text else None
        processing_reference: Optional[str] = None
        requires_processing_page = False

        if extraction_path and extraction_path.exists() and (source_path is None or extraction_path != source_path):
            suffix = extraction_path.suffix.lower() if extraction_path.suffix else ".png"
            if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
                suffix = ".png"
            destination = (query_dir / f"processing_page_{candidate_page_index}{suffix}").resolve()
            if query_dir.resolve() == destination.parent:
                shutil.copyfile(extraction_path, destination)
                processing_reference = destination.name
                requires_processing_page = True

        roi_storage_space = "processing" if processing_reference else "source"
        page_metadata = {
            "pageNumber": candidate_page_index,
            "sourceImageReference": source_reference,
            "processingImageReference": processing_reference,
            "roiCoordinateSpace": roi_storage_space,
            "detectionRoiCoordinateSpace": page_candidate.get("roi_coordinate_space"),
        }
        page_candidate["source_image_query_reference"] = source_reference
        page_candidate["processing_image_query_reference"] = processing_reference
        page_candidate["processing_page_required"] = requires_processing_page
        page_candidate["processing_log_roi_coordinate_space"] = roi_storage_space
        processing_pages.append(page_metadata)

    return processing_pages


def _normalize_query_pages(
    query_id: str,
    page_paths: List[Path],
    skip_normalization: bool = False,
    timing: Optional[Dict[str, float]] = None,
) -> List[Dict[str, Any]]:
    normalize_started = time.perf_counter()
    normalized_dir = _storage_path() / query_id / "normalized"
    normalized_dir.mkdir(parents=True, exist_ok=True)
    if timing is not None:
        timing["prepare_normalized_dir"] = timing.get("prepare_normalized_dir", 0.0) + (time.perf_counter() - normalize_started)
    normalized_pages = []
    for index, page_path in enumerate(page_paths, start=1):
        if skip_normalization:
            step_started = time.perf_counter()
            normalized_pages.append(
                {
                    "page_index": index,
                    "original_path": str(page_path),
                    "normalized_path": str(page_path),
                    "matching_path": str(page_path),
                    "normalization": {
                        "normalization_status": "skipped",
                        "reason": "pdf_rendered_page_used_without_image_normalization",
                        "normalized_image_path": str(page_path),
                        "perspective_applied": False,
                        "crop_applied": False,
                        "fallback_used": False,
                    },
                }
            )
            if timing is not None:
                timing["prepare_normalization_skipped"] = timing.get("prepare_normalization_skipped", 0.0) + (time.perf_counter() - step_started)
            continue
        normalized_path = normalized_dir / f"page_{index}_normalized.png"
        step_started = time.perf_counter()
        info = normalization_service.normalize_document(str(page_path), str(normalized_path))
        if timing is not None:
            timing["prepare_normalization"] = timing.get("prepare_normalization", 0.0) + (time.perf_counter() - step_started)
        normalized_pages.append(
            {
                "page_index": index,
                "original_path": str(page_path),
                "normalized_path": info["normalized_image_path"],
                "matching_path": str(page_path),
                "normalization": info,
                "matching_normalization": {
                    "normalization_status": "layout_space_signature",
                    "reason": "template_matching_uses_original_layout_space_signature",
                    "matching_image_path": str(page_path),
                    "crop_applied": False,
                    "perspective_applied": False,
                    "fallback_used": False,
                },
            }
        )
    return normalized_pages


def _safe_file_token(value: str) -> str:
    return "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in value)


def _alignment_result(
    status: str,
    reason: str,
    error: Optional[str] = None,
    orb_executed: bool = False,
    precheck: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    debug = {
        "method": "ORB",
        "orb_executed": orb_executed,
        "precheck": precheck or {},
        "query_keypoints": 0,
        "template_keypoints": 0,
        "raw_matches": 0,
        "good_matches": 0,
        "inliers": 0,
        "inlier_ratio": 0.0,
        "homography_found": False,
        "warp_applied": False,
        "alignment_score": 0.0,
        "reason": reason,
    }
    return {
        "alignment_status": status,
        "alignment_success": False,
        "aligned_image_path": None,
        "alignment_match_image_path": None,
        "aligned_image_preview_url": None,
        "alignment_match_image_preview_url": None,
        "alignment_debug": debug,
        "method": "ORB",
        "keypoints_query": 0,
        "keypoints_template": 0,
        "matches": 0,
        "good_matches": 0,
        "inliers": 0,
        "inlier_ratio": 0.0,
        "homography_found": False,
        "warp_applied": False,
        "alignment_score": 0.0,
        "homography": None,
        "error": error,
    }


def _alignment_reason(
    alignment_status: str,
    alignment: Dict[str, Any],
    alignment_debug: Dict[str, Any],
) -> str:
    if alignment_status == "skipped":
        return str(alignment_debug.get("reason") or "alignment_skipped_geometry_already_matches_template")
    if alignment_status == "aligned":
        return str(alignment_debug.get("reason") or "alignment_succeeded_and_aligned_image_used")
    if alignment_status == "fallback":
        return str(alignment_debug.get("reason") or alignment.get("error") or "alignment_attempted_but_normalized_image_used")
    return str(alignment.get("error") or alignment_debug.get("reason") or "alignment_failed_unexpectedly_normalized_image_used")


def _template_canvas_projection(
    template_id: str,
    fields: List[Dict[str, Any]],
    page_number: int,
    alignment_status: str,
    alignment_reason: str,
    extraction_image_path: str,
    extraction_image_preview_url: Optional[str],
) -> Dict[str, Any]:
    timing: Dict[str, Any] = {
        "field_filter": 0.0,
        "projected_field_build": 0.0,
        "adaptive_refinement": 0.0,
        "total": 0.0,
    }
    total_started = time.perf_counter()
    step_started = time.perf_counter()
    extraction_fields = [
        field
        for field in fields
        if not field.get("use_for_verification") and int(field.get("page_number") or 1) == int(page_number)
    ]
    timing["field_filter"] = time.perf_counter() - step_started
    step_started = time.perf_counter()
    projected_fields = []
    for field in extraction_fields:
        roi = field.get("roi") or {}
        projected_fields.append(
            {
                "field_id": field.get("id"),
                "field_name": field.get("field_name"),
                "display_label": field.get("display_label"),
                "page_number": field.get("page_number"),
                "template_roi": roi,
                "projected_polygon_before_clip": [],
                "projected_polygon": [],
                "projected_roi_before_clip": roi,
                "projected_roi": roi,
                "adaptive_roi": roi,
                "adaptive_search_region": None,
                "adaptive_word_boxes": [],
                "adaptive_word_groups": [],
                "adaptive_ranked_word_groups": [],
                "adaptive_status": "not_run",
                "adaptive_confidence": None,
                "adaptive_word_count": 0,
                "adaptive_coverage": None,
                "adaptive_ocr_confidence": None,
                "adaptive_validation_result": {
                    "passed": True,
                    "errors": [],
                    "warnings": ["adaptive_roi_pending"],
                },
                "adaptive_fallback_reason": None,
                "projection_method": "template_canvas",
                "projection_valid": True,
                "projection_validation_result": {
                    "passed": True,
                    "errors": [],
                    "warnings": [],
                    "reason": "roi_uses_template_canvas_after_alignment",
                },
                "fallback_used": False,
            }
        )
    timing["projected_field_build"] = time.perf_counter() - step_started

    step_started = time.perf_counter()
    projected_fields, adaptive_debug = projection_service.disable_adaptive_refinement_for_detection(projected_fields)
    timing["adaptive_refinement"] = time.perf_counter() - step_started
    timing["total"] = time.perf_counter() - total_started

    return {
        "template_id": template_id,
        "status": "success",
        "method": "template_canvas",
        "anchors_expected": 0,
        "anchors_matched": 0,
        "inliers": 0,
        "reprojection_error": None,
        "confidence": 1.0,
        "fallback_reason": None,
        "matched_anchors": [],
        "adaptive_refinement": {
            **adaptive_debug,
            "reason": adaptive_debug.get("reason") or "disabled_for_user_detection_projection",
        },
        "projected_fields": projected_fields,
        "roi_coordinate_space": "template_canvas",
        "extraction_image_path": extraction_image_path,
        "extraction_image_preview_url": extraction_image_preview_url,
        "alignment_status": alignment_status,
        "alignment_reason": alignment_reason,
        "timing": timing,
    }


def _template_roi_items(fields: List[Dict[str, Any]], page_number: int) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for field in fields:
        if field.get("use_for_verification"):
            continue
        if int(field.get("page_number") or 1) != int(page_number):
            continue
        roi = field.get("roi")
        if not roi:
            continue
        items.append(
            {
                "field_id": field.get("id"),
                "field_name": field.get("field_name"),
                "display_label": field.get("display_label"),
                "page_number": field.get("page_number"),
                "data_type": field.get("data_type"),
                "extraction_method": field.get("extraction_method"),
                "roi": roi,
                "source": "admin_template_roi",
            }
        )
    return items


def _run_extraction_test(
    template_id: str,
    fields: List[Dict[str, Any]],
    projected_fields: List[Dict[str, Any]],
    image_path: str,
    page_number: int,
    roi_coordinate_space: str,
) -> Dict[str, Any]:
    timing: Dict[str, Any] = {
        "field_index": 0.0,
        "roi_item_build": 0.0,
        "ocr_rois": 0.0,
        "result_apply": 0.0,
        "total": 0.0,
    }
    total_started = time.perf_counter()
    step_started = time.perf_counter()
    fields_by_id = {str(field.get("id")): field for field in fields}
    timing["field_index"] = time.perf_counter() - step_started
    results: List[Dict[str, Any]] = []
    roi_items: List[Dict[str, Any]] = []
    roi_sources: Dict[str, str] = {}

    step_started = time.perf_counter()
    for projected in projected_fields:
        field_id = str(projected.get("field_id") or "")
        source_field = fields_by_id.get(field_id) or {}
        if source_field.get("use_for_verification"):
            continue
        if int(source_field.get("page_number") or projected.get("page_number") or 1) != int(page_number):
            continue

        data_type = source_field.get("data_type") or "text"
        extraction_method = source_field.get("extraction_method") or ("table_recognition_v2" if data_type == "table" else "paddle_thai_ocr")
        roi_source = "template_roi" if roi_coordinate_space == "template_canvas" else "projected_roi"
        roi = projected.get("template_roi") if roi_source == "template_roi" else projected.get("projected_roi")
        if not roi:
            roi = projected.get("projected_roi") or projected.get("template_roi")
            roi_source = "projected_roi" if projected.get("projected_roi") else "template_roi"

        base = {
            "field_id": field_id,
            "field_name": source_field.get("field_name") or projected.get("field_name"),
            "display_label": source_field.get("display_label") or projected.get("display_label"),
            "page_number": page_number,
            "data_type": data_type,
            "extraction_method": extraction_method,
            "roi_mode": source_field.get("roi_mode") or "fix",
            "expected_content": source_field.get("expected_content"),
            "roi_source": roi_source,
            "roi": roi,
            "passed": False,
            "status": "failed",
            "ocr_text": "",
            "confidence": 0.0,
            "failure_reason": None,
        }

        if data_type == "image" or extraction_method == "extract_image":
            base.update(
                {
                    "passed": bool(roi),
                    "status": "passed" if roi else "failed",
                    "ocr_text": "(image crop)",
                    "confidence": 1.0 if roi else 0.0,
                    "failure_reason": None if roi else "roi_missing",
                }
            )
            results.append(base)
            continue

        if not roi:
            base["failure_reason"] = "roi_missing"
            results.append(base)
            continue

        roi_items.append({"id": field_id, "roi": roi, "data_type": data_type, "extraction_method": extraction_method})
        roi_sources[field_id] = roi_source
        results.append(base)
    timing["roi_item_build"] = time.perf_counter() - step_started

    if roi_items:
        try:
            ocr_timing: Dict[str, Any] = {}
            step_started = time.perf_counter()
            ocr_results = ocr_rois(image_path, roi_items, timing=ocr_timing)
            timing["ocr_rois"] = time.perf_counter() - step_started
            timing["ocr_rois_breakdown"] = ocr_timing
            step_started = time.perf_counter()
            for item in results:
                field_id = str(item.get("field_id") or "")
                if field_id not in ocr_results:
                    continue
                ocr_result = ocr_results[field_id]
                text = str(ocr_result.get("text") or "")
                confidence = float(ocr_result.get("confidence") or 0.0)
                error = ocr_result.get("error")
                item.update(
                    {
                        "passed": bool(text.strip()) and not error,
                        "status": "passed" if text.strip() and not error else "failed",
                        "ocr_text": text,
                        "confidence": round(confidence, 4),
                        "failure_reason": None if text.strip() and not error else str(error or "ocr_empty"),
                        "engine": ocr_result.get("engine"),
                        "model": ocr_result.get("model"),
                        "table_rows": ocr_result.get("table_rows"),
                        "table_html": ocr_result.get("table_html"),
                        "table_debug": ocr_result.get("table_debug"),
                        "roi_source": roi_sources.get(field_id) or item.get("roi_source"),
                    }
                )
            timing["result_apply"] = time.perf_counter() - step_started
        except OcrUnavailableError as error:
            for item in results:
                if item.get("data_type") == "image" or item.get("extraction_method") == "extract_image":
                    continue
                item.update({"status": "failed", "passed": False, "failure_reason": "ocr_unavailable", "error": str(error)})
        except Exception as error:
            for item in results:
                if item.get("data_type") == "image" or item.get("extraction_method") == "extract_image":
                    continue
                item.update({"status": "failed", "passed": False, "failure_reason": "ocr_error", "error": str(error)})

    timing["total"] = time.perf_counter() - total_started
    return {
        "template_id": template_id,
        "status": "completed",
        "tested_count": len(results),
        "passed_count": sum(1 for item in results if item.get("passed")),
        "failed_count": sum(1 for item in results if not item.get("passed")),
        "image_path": image_path,
        "image_preview_url": _detection_debug_url(image_path),
        "roi_coordinate_space": roi_coordinate_space,
        "fields": results,
        "timing": timing,
    }


def _auto_roi_region_items(page_info: Dict[str, Any]) -> Dict[str, Any]:
    page_index = int(page_info["page_index"])
    image_path = str(page_info["normalized_path"])
    image = cv2.imread(image_path)
    if image is None:
        return {
            "page_index": page_index,
            "page_number": page_index,
            "status": "failed",
            "reason": "image_unavailable",
            "regions": [],
        }

    analysis = analyze_layout(image, expand_text_rois=True, auto_roi_mode="text_line", use_text_detection=False)
    regions = []
    for index, region in enumerate(analysis.get("regions") or [], start=1):
        region_type = str(region.get("type") or "text").lower()
        extraction_method = (
            "extract_image"
            if region_type == "image"
            else "table_recognition_v2"
            if region_type == "table"
            else "paddle_thai_ocr"
        )
        regions.append(
            {
                "field_id": f"auto_page_{page_index}_{index}",
                "field_name": f"auto_page_{page_index}_field_{index}",
                "display_label": f"Auto ROI {index}",
                "page_number": page_index,
                "data_type": region_type,
                "type": region_type,
                "extraction_method": extraction_method,
                "roi_mode": "fix",
                "expected_content": "text" if region_type == "text" else None,
                "confidence": float(region.get("confidence") or 0.0),
                "roi": {
                    "page_number": page_index,
                    **(region.get("roi") or {}),
                },
                "roi_source": "whole_page_auto_roi",
                "roi_coordinate_space": "whole_page_auto_roi",
                "roi_expansion": region.get("roi_expansion"),
                "auto_roi_group": region.get("auto_roi_group"),
            }
        )

    regions = _dedupe_overlapping_auto_roi_regions(regions)

    return {
        "page_index": page_index,
        "page_number": page_index,
        "status": "completed",
        "engine": analysis.get("engine"),
        "model": analysis.get("model"),
        "image_width": analysis.get("image_width"),
        "image_height": analysis.get("image_height"),
        "image_preview_data_url": _image_to_data_url(Path(image_path)),
        "regions": regions,
    }


def _auto_roi_area(region: Dict[str, Any]) -> float:
    roi = region.get("roi") if isinstance(region.get("roi"), dict) else {}
    return max(0.0, float(roi.get("width_ratio") or 0.0)) * max(0.0, float(roi.get("height_ratio") or 0.0))


def _auto_roi_intersection_area(left: Dict[str, Any], right: Dict[str, Any]) -> float:
    left_roi = left.get("roi") if isinstance(left.get("roi"), dict) else {}
    right_roi = right.get("roi") if isinstance(right.get("roi"), dict) else {}
    left_x = float(left_roi.get("x_ratio") or 0.0)
    left_y = float(left_roi.get("y_ratio") or 0.0)
    left_right = left_x + float(left_roi.get("width_ratio") or 0.0)
    left_bottom = left_y + float(left_roi.get("height_ratio") or 0.0)
    right_x = float(right_roi.get("x_ratio") or 0.0)
    right_y = float(right_roi.get("y_ratio") or 0.0)
    right_right = right_x + float(right_roi.get("width_ratio") or 0.0)
    right_bottom = right_y + float(right_roi.get("height_ratio") or 0.0)
    width = max(0.0, min(left_right, right_right) - max(left_x, right_x))
    height = max(0.0, min(left_bottom, right_bottom) - max(left_y, right_y))
    return width * height


def _auto_roi_type_priority(region: Dict[str, Any]) -> int:
    region_type = str(region.get("type") or region.get("data_type") or "").lower()
    return {"table": 3, "image": 2, "text": 1}.get(region_type, 0)


def _dedupe_overlapping_auto_roi_regions(regions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    prepared = [
        {"index": index, "region": region, "area": _auto_roi_area(region)}
        for index, region in enumerate(regions)
        if isinstance(region.get("roi"), dict)
    ]
    prepared.sort(key=lambda item: (-_auto_roi_type_priority(item["region"]), -item["area"], item["index"]))

    accepted: List[Dict[str, Any]] = []
    for item in prepared:
        if item["area"] <= 0:
            continue
        overlaps_existing = False
        for existing in accepted:
            smaller_area = max(min(item["area"], existing["area"]), 1e-9)
            if _auto_roi_intersection_area(item["region"], existing["region"]) / smaller_area >= 0.82:
                overlaps_existing = True
                break
        if not overlaps_existing:
            accepted.append(item)

    accepted.sort(key=lambda item: item["index"])
    return [item["region"] for item in accepted]


def _attach_main_page_auto_roi_pages(
    candidates: List[Dict[str, Any]],
    pages: List[Dict[str, Any]],
    auto_pages_cache: Optional[Dict[int, Dict[str, Any]]] = None,
) -> None:
    auto_pages_cache = dict(auto_pages_cache or {})
    for candidate in candidates:
        metadata = candidate.get("metadata") if isinstance(candidate.get("metadata"), dict) else {}
        detection_mode = str(candidate.get("detection_mode") or metadata.get("detection_mode") or "")
        if detection_mode != "main_page" or not candidate.get("final_passed"):
            candidate["main_page_auto_roi_pages"] = []
            continue

        matched_query_page = int(candidate.get("query_page_index") or 1)
        auto_pages: List[Dict[str, Any]] = []
        for page_info in pages:
            page_index = int(page_info.get("page_index") or 1)
            if page_index == matched_query_page:
                continue
            if page_index not in auto_pages_cache:
                try:
                    auto_pages_cache[page_index] = _auto_roi_region_items(page_info)
                except Exception as error:
                    auto_pages_cache[page_index] = {
                        "page_index": page_index,
                        "page_number": page_index,
                        "status": "failed",
                        "reason": f"auto_roi_failed: {error}",
                        "regions": [],
                    }
            auto_pages.append(auto_pages_cache[page_index])

        candidate["main_page_auto_roi_pages"] = auto_pages
        candidate["main_page_auto_roi_total_pages"] = len(auto_pages)
        candidate["main_page_auto_roi_total_regions"] = sum(len(page.get("regions") or []) for page in auto_pages)


def _build_request_auto_roi_pages(pages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    auto_pages: List[Dict[str, Any]] = []
    for page_info in pages:
        page_index = int(page_info.get("page_index") or 1)
        try:
            auto_pages.append(_auto_roi_region_items(page_info))
        except Exception as error:
            auto_pages.append(
                {
                    "page_index": page_index,
                    "page_number": page_index,
                    "status": "failed",
                    "reason": f"auto_roi_failed: {error}",
                    "regions": [],
                }
            )
    return auto_pages


def _auto_roi_pages_by_index(auto_pages: List[Dict[str, Any]]) -> Dict[int, Dict[str, Any]]:
    result: Dict[int, Dict[str, Any]] = {}
    for page in auto_pages:
        try:
            result[int(page.get("page_index") or page.get("page_number") or 1)] = page
        except Exception:
            continue
    return result


def _align_candidate_page(
    template_id: str,
    page_number: int,
    query_image_path: str,
    normalization_info: Optional[Dict[str, Any]] = None,
    query_signature: Optional[Dict[str, Any]] = None,
    template_signature: Optional[Dict[str, Any]] = None,
    template_image_source: Optional[str] = None,
) -> Dict[str, Any]:
    align_candidate_started = time.perf_counter()
    alignment_timing: Dict[str, Any] = {}
    fetch_db_timing: Dict[str, Any] = {}
    if template_image_source:
        alignment_timing["template_image_source_source"] = "candidate_metadata"
        alignment_timing["fetch_template_page_image_source_ms"] = 0.0
        alignment_timing["fetch_template_page_image_source_db"] = fetch_db_timing
    else:
        alignment_timing["template_image_source_source"] = "db_fallback"
        fetch_started = time.perf_counter()
        template_image_source = _fetch_template_page_image_source(template_id, page_number, timing=fetch_db_timing)
        alignment_timing["fetch_template_page_image_source_ms"] = _ms(time.perf_counter() - fetch_started)
        alignment_timing["fetch_template_page_image_source_db"] = fetch_db_timing
    if not template_image_source:
        alignment_timing["align_candidate_page_total_ms"] = _ms(time.perf_counter() - align_candidate_started)
        result = _alignment_result(
            "fallback",
            f"template_page_image_unavailable_page_{page_number}",
            error=f"Template page image is unavailable for page {page_number}",
        )
        result_debug = result.get("alignment_debug") or {}
        result_debug.update(alignment_timing)
        result["alignment_debug"] = result_debug
        return result

    query_path = Path(query_image_path)
    output_root = query_path.parent.parent if query_path.parent.name == "normalized" else query_path.parent
    output_dir = output_root / "aligned"
    output_path = output_dir / f"{_safe_file_token(template_id)}_page_{page_number}_aligned.png"

    try:
        align_started = time.perf_counter()
        layout_alignment = layout_alignment_service.align_to_template(
            query_image_path,
            template_image_source,
            str(output_path),
            query_signature=query_signature,
            template_signature=template_signature,
        )
        alignment_timing["align_to_template_ms"] = _ms(time.perf_counter() - align_started)
        post_started = time.perf_counter()
        layout_status = str(layout_alignment.get("alignment_status") or "")
        layout_alignment["aligned_image_preview_url"] = _detection_preview_url(layout_alignment.get("aligned_image_path"))
        layout_alignment["alignment_match_image_preview_url"] = _detection_preview_url(layout_alignment.get("alignment_match_image_path"))
        layout_debug = layout_alignment.get("alignment_debug") or {}
        layout_debug["layout_alignment_executed"] = layout_status != "skipped"
        layout_debug["orb_executed"] = False
        layout_debug["verification_source_used"] = "aligned" if layout_status == "aligned" else "normalized"
        alignment_timing["post_layout_alignment_ms"] = _ms(time.perf_counter() - post_started)
        layout_debug.update(alignment_timing)
        layout_alignment["alignment_debug"] = layout_debug
        if layout_status in {"aligned", "skipped"}:
            layout_debug["align_candidate_page_total_ms"] = _ms(time.perf_counter() - align_candidate_started)
            layout_alignment["alignment_debug"] = layout_debug
            return layout_alignment

        precheck = alignment_service.alignment_precheck(query_image_path, template_image_source, normalization_info)
        if not precheck.get("should_run_orb"):
            precheck["layout_alignment"] = layout_debug
            if precheck.get("reason") == "normalized_geometry_matches_template":
                result = _alignment_result("skipped", str(precheck["reason"]), precheck=precheck)
            else:
                result = _alignment_result("fallback", str(precheck.get("reason") or "alignment_precheck_unavailable"), precheck=precheck)
            result_debug = result.get("alignment_debug") or {}
            result_debug.update(alignment_timing)
            result_debug["align_candidate_page_total_ms"] = _ms(time.perf_counter() - align_candidate_started)
            result["alignment_debug"] = result_debug
            return result

        alignment = alignment_service.align_to_template(query_image_path, template_image_source, str(output_path))
        service_status = str(alignment.get("alignment_status") or "")
        alignment_status = "aligned" if alignment.get("aligned_image_path") and service_status == "aligned" else "fallback"
        alignment["alignment_status"] = alignment_status
        alignment["aligned_image_preview_url"] = _detection_preview_url(alignment.get("aligned_image_path"))
        alignment["alignment_match_image_preview_url"] = _detection_preview_url(alignment.get("alignment_match_image_path"))
        alignment_debug = alignment.get("alignment_debug") or {}
        alignment_debug["orb_executed"] = True
        alignment_debug["precheck"] = precheck
        alignment_debug["layout_alignment"] = layout_debug
        alignment_debug["layout_alignment_status"] = layout_status
        if alignment_status == "fallback" and alignment_debug.get("reason") == "aligned":
            alignment_debug["reason"] = "alignment_output_unavailable"
        alignment_debug.update(alignment_timing)
        alignment_debug["align_candidate_page_total_ms"] = _ms(time.perf_counter() - align_candidate_started)
        alignment["alignment_debug"] = alignment_debug
        return alignment
    except Exception as error:
        alignment_timing["align_candidate_page_total_ms"] = _ms(time.perf_counter() - align_candidate_started)
        result = _alignment_result("failed", "alignment_runtime_error", error=f"Alignment failed: {error}")
        result_debug = result.get("alignment_debug") or {}
        result_debug.update(alignment_timing)
        result["alignment_debug"] = result_debug
        return result


def _candidate_from_result(
    result: Dict[str, Any],
    page_image_paths: Dict[int, str],
    page_index: int,
    query_image_path: str,
    normalization_info: Optional[Dict[str, Any]] = None,
    query_signature: Optional[Dict[str, Any]] = None,
    allow_alignment: bool = True,
    include_template_id: Optional[str] = None,
    verification_strategy: str = "standard",
    request_cache: Optional[DetectionRequestCache] = None,
) -> Optional[Dict[str, Any]]:
    metadata = result.get("metadata") or {}
    template_signature = result.get("_layout_signature")
    if not isinstance(template_signature, dict):
        template_signature = metadata.get("_layout_signature")
    if not isinstance(template_signature, dict):
        template_signature = None
    vector_id = str(result.get("vector_id") or "")
    template_id = _template_id_from_metadata(metadata, vector_id)
    candidate_timing: Dict[str, Optional[float]] = {
        "template_fetch": None,
        "field_count_query": None,
        "verification_fields_load": None,
        "candidate_setup": None,
        "normalized_verification": None,
        "alignment": None,
        "aligned_verification": None,
        "decision": None,
        "roi_field_load": None,
        "roi_items": None,
        "projection": None,
        "extraction_test": None,
        "coordinate_debug": None,
        "text_verification": 0.0,
        "image_verification": 0.0,
        "text_ocr_inference": 0.0,
        "image_model_inference": 0.0,
    }
    candidate_cache_debug: Dict[str, bool] = {
        "template_cache_hit": False,
        "field_count_cache_hit": False,
        "verification_fields_cache_hit": False,
        "verification_fields_preloaded": False,
        "verification_fields_reused_for_aligned": False,
        "text_ocr_cache_hit": False,
        "image_model_cache_hit": False,
    }
    candidate_db_debug: Dict[str, Dict[str, Optional[float]]] = {}
    step_started = time.perf_counter()
    template, template_cache_hit, template_db_timing = _fetch_template_cached(template_id, request_cache)
    candidate_cache_debug["template_cache_hit"] = template_cache_hit
    candidate_db_debug["template_fetch"] = _db_timing_ms(template_db_timing)
    candidate_timing["template_fetch"] = time.perf_counter() - step_started

    if template_id and template is None:
        return None

    step_started = time.perf_counter()
    if template:
        template_status = template.get("status")
        template_name = template.get("name")
        page_count = template.get("page_count")
        final_confidence_threshold = decision_service.final_confidence_threshold(template, metadata)
        candidate_timing["candidate_setup"] = time.perf_counter() - step_started
        step_started = time.perf_counter()
        field_count, field_count_cache_hit, field_count_db_timing = _fetch_field_count_cached(template_id, request_cache)
        candidate_cache_debug["field_count_cache_hit"] = field_count_cache_hit
        candidate_db_debug["field_count"] = _db_timing_ms(field_count_db_timing)
        candidate_timing["field_count_query"] = time.perf_counter() - step_started
    else:
        template_status = metadata.get("template_status")
        template_name = metadata.get("template_name")
        page_count = metadata.get("page_count")
        field_count = metadata.get("field_count")
        final_confidence_threshold = decision_service.final_confidence_threshold(None, metadata)
        candidate_timing["candidate_setup"] = time.perf_counter() - step_started

    if template_status != "active" and template_id != include_template_id:
        return None

    step_started = time.perf_counter()
    verification_strategy = normalize_verification_strategy(verification_strategy)
    matching_weights = decision_service.matching_weights(template, metadata)
    template_page_number = int(
        metadata.get("matched_layout_reference_page_number")
        or metadata.get("page_number")
        or page_index
        or 1
    )
    detection_mode = str(metadata.get("detection_mode") or (template or {}).get("detection_mode") or "all_pages")
    layout_reference_crop_debug: Dict[str, Any] = {
        "enabled": LAYOUT_REFERENCE_CROP_ENABLED,
        "applied": False,
        "reason": "not_attempted",
    }
    verification_query_image_path = query_image_path
    step_started = time.perf_counter()
    query_path = Path(query_image_path)
    output_root = query_path.parent.parent if query_path.parent.name == "normalized" else query_path.parent
    layout_reference_crop_debug = _layout_reference_adjusted_image(
        query_image_path,
        query_signature,
        template_signature,
        output_root / "layout_reference",
        template_id,
        template_page_number,
    )
    candidate_timing["layout_reference_crop"] = time.perf_counter() - step_started
    if layout_reference_crop_debug.get("applied") and layout_reference_crop_debug.get("image_path"):
        verification_query_image_path = str(layout_reference_crop_debug["image_path"])
    alignment_query_signature = (
        layout_reference_crop_debug.get("_adjusted_query_signature")
        if isinstance(layout_reference_crop_debug.get("_adjusted_query_signature"), dict)
        else query_signature
    )
    alignment_query_signature_source = (
        "layout_reference_crop_rebased"
        if alignment_query_signature is not query_signature
        else "original_query_signature"
    )
    layout_reference_crop_debug.pop("_adjusted_query_signature", None)
    step_started = time.perf_counter()
    candidate_page_image_paths = dict(page_image_paths)
    candidate_page_image_paths[template_page_number] = verification_query_image_path
    verification_page_image_paths = (
        {template_page_number: verification_query_image_path}
        if detection_mode == "main_page"
        else candidate_page_image_paths
    )
    candidate_timing["candidate_setup"] = float(candidate_timing.get("candidate_setup") or 0.0) + (time.perf_counter() - step_started)

    # 1) Verify จาก normalized ก่อน
    verify_template_for_strategy = (
        verification_service.verify_template_strict
        if verification_strategy == VERIFICATION_STRATEGY_STRICT
        else verification_service.verify_template
    )
    verification_fields = None
    if template_id:
        step_started = time.perf_counter()
        verification_fields, verification_fields_cache_hit, verification_fields_db_timing = _fetch_verification_fields_cached(template_id, request_cache)
        candidate_cache_debug["verification_fields_cache_hit"] = verification_fields_cache_hit
        candidate_cache_debug["verification_fields_preloaded"] = True
        candidate_db_debug["verification_fields"] = _db_timing_ms(verification_fields_db_timing)
        candidate_timing["verification_fields_load"] = time.perf_counter() - step_started
    step_started = time.perf_counter()
    verification_runtime_cache = request_cache.verification_runtime if request_cache is not None else None
    normalized_verification = (
        verify_template_for_strategy(
            template_id,
            verification_page_image_paths,
            verification_fields,
            verification_runtime_cache,
        )
        if template_id and verification_strategy == VERIFICATION_STRATEGY_STRICT
        else verify_template_for_strategy(
            template_id,
            verification_page_image_paths,
            verification_fields,
        ) if template_id else {
        "status": "failed",
        "passed": False,
        "score": 0.0,
        "required_passed": False,
        "checked_fields": [],
        }
    )
    candidate_timing["normalized_verification"] = time.perf_counter() - step_started
    normalized_internal_timing = normalized_verification.get("timing") if isinstance(normalized_verification, dict) else {}
    if isinstance(normalized_internal_timing, dict):
        candidate_timing["text_verification"] = float(candidate_timing.get("text_verification") or 0.0) + float(normalized_internal_timing.get("text_verification") or 0.0)
        candidate_timing["image_verification"] = float(candidate_timing.get("image_verification") or 0.0) + float(normalized_internal_timing.get("image_verification") or 0.0)
        candidate_timing["text_ocr_inference"] = float(candidate_timing.get("text_ocr_inference") or 0.0) + float(normalized_internal_timing.get("text_ocr_inference") or 0.0)
        candidate_timing["image_model_inference"] = float(candidate_timing.get("image_model_inference") or 0.0) + float(normalized_internal_timing.get("image_model_inference") or 0.0)
        candidate_cache_debug["text_ocr_cache_hit"] = bool(int(normalized_internal_timing.get("text_ocr_cache_hits") or 0))
        candidate_cache_debug["image_model_cache_hit"] = bool(int(normalized_internal_timing.get("image_model_cache_hits") or 0))
        if request_cache is not None:
            request_cache.stats["verification_text_ocr_cache_hits"] += int(normalized_internal_timing.get("text_ocr_cache_hits") or 0)
            request_cache.stats["verification_text_ocr_cache_misses"] += int(normalized_internal_timing.get("text_ocr_cache_misses") or 0)
            request_cache.stats["verification_image_model_cache_hits"] += int(normalized_internal_timing.get("image_model_cache_hits") or 0)
            request_cache.stats["verification_image_model_cache_misses"] += int(normalized_internal_timing.get("image_model_cache_misses") or 0)
    normalized_internal_timing_ms = _timing_ms_map(normalized_internal_timing)

    normalized_score = float(normalized_verification.get("score") or 0.0)
    verification = normalized_verification
    base_verification_source = "layout_reference_crop" if layout_reference_crop_debug.get("applied") else "normalized"
    verification_source_used = base_verification_source
    layout_reference_crop_debug["selected_processing_source"] = base_verification_source
    pre_alignment_processing_source = base_verification_source
    post_alignment_processing_source = base_verification_source
    alignment_required = not bool(normalized_verification.get("passed"))
    alignment_skip_reason = None if alignment_required else "pre_alignment_verification_already_passed"
    alignment_processing_image_selected = False

    # ค่าเริ่มต้น: ยังไม่ align
    alignment = _alignment_result(
        "skipped",
        "layout_reference_verification_checked_first",
        precheck={"reason": "alignment_deferred_until_needed", "base_verification_source": base_verification_source},
    )

    aligned_verification = None
    aligned_score = None
    aligned_internal_timing_ms: Dict[str, Optional[float]] = {}

    # 2) Template alignment is part of the production path.
    # The alignment service precheck skips ORB when geometry already matches.
    should_try_alignment = template_id is not None and allow_alignment

    if should_try_alignment:
        step_started = time.perf_counter()
        alignment = _align_candidate_page(
            template_id,
            template_page_number,
            verification_query_image_path,
            normalization_info,
            query_signature=alignment_query_signature,
            template_signature=template_signature,
            template_image_source=metadata.get("matched_layout_reference_image_url"),
        )
        candidate_timing["alignment"] = time.perf_counter() - step_started

        if alignment.get("alignment_status") == "aligned" and alignment.get("aligned_image_path"):
            aligned_page_image_paths = dict(candidate_page_image_paths)
            aligned_page_image_paths[template_page_number] = str(alignment["aligned_image_path"])
            aligned_verification_paths = (
                {template_page_number: str(alignment["aligned_image_path"])}
                if detection_mode == "main_page"
                else aligned_page_image_paths
            )

            step_started = time.perf_counter()
            candidate_cache_debug["verification_fields_reused_for_aligned"] = verification_fields is not None
            aligned_verification = (
                verify_template_for_strategy(
                    template_id,
                    aligned_verification_paths,
                    verification_fields,
                    verification_runtime_cache,
                )
                if verification_strategy == VERIFICATION_STRATEGY_STRICT
                else verify_template_for_strategy(
                    template_id,
                    aligned_verification_paths,
                    verification_fields,
                )
            )
            candidate_timing["aligned_verification"] = time.perf_counter() - step_started
            aligned_internal_timing = aligned_verification.get("timing") if isinstance(aligned_verification, dict) else {}
            if isinstance(aligned_internal_timing, dict):
                candidate_timing["text_verification"] = float(candidate_timing.get("text_verification") or 0.0) + float(aligned_internal_timing.get("text_verification") or 0.0)
                candidate_timing["image_verification"] = float(candidate_timing.get("image_verification") or 0.0) + float(aligned_internal_timing.get("image_verification") or 0.0)
                candidate_timing["text_ocr_inference"] = float(candidate_timing.get("text_ocr_inference") or 0.0) + float(aligned_internal_timing.get("text_ocr_inference") or 0.0)
                candidate_timing["image_model_inference"] = float(candidate_timing.get("image_model_inference") or 0.0) + float(aligned_internal_timing.get("image_model_inference") or 0.0)
                candidate_cache_debug["text_ocr_cache_hit"] = candidate_cache_debug["text_ocr_cache_hit"] or bool(int(aligned_internal_timing.get("text_ocr_cache_hits") or 0))
                candidate_cache_debug["image_model_cache_hit"] = candidate_cache_debug["image_model_cache_hit"] or bool(int(aligned_internal_timing.get("image_model_cache_hits") or 0))
                if request_cache is not None:
                    request_cache.stats["verification_text_ocr_cache_hits"] += int(aligned_internal_timing.get("text_ocr_cache_hits") or 0)
                    request_cache.stats["verification_text_ocr_cache_misses"] += int(aligned_internal_timing.get("text_ocr_cache_misses") or 0)
                    request_cache.stats["verification_image_model_cache_hits"] += int(aligned_internal_timing.get("image_model_cache_hits") or 0)
                    request_cache.stats["verification_image_model_cache_misses"] += int(aligned_internal_timing.get("image_model_cache_misses") or 0)
            aligned_internal_timing_ms = _timing_ms_map(aligned_internal_timing)
            aligned_score = float(aligned_verification.get("score") or 0.0)
            aligned_improvement = aligned_score - normalized_score

            # 3) Once a template is known, prefer the template-reference image
            # when alignment is accepted and verification is preserved. This
            # keeps ROI/OCR in template canvas space without changing the
            # verification algorithm itself.
            aligned_verification_preserved = bool(aligned_verification.get("passed")) and aligned_score >= (normalized_score - 0.0001)
            template_reference_processing_required = bool(layout_reference_crop_debug.get("applied"))
            if (alignment_required and aligned_improvement > 0.0001) or (
                template_reference_processing_required and aligned_verification_preserved
            ):
                verification = aligned_verification
                verification_source_used = "aligned"
                post_alignment_processing_source = "aligned"
                alignment_processing_image_selected = True
                if template_reference_processing_required and not alignment_required:
                    alignment_skip_reason = "template_reference_alignment_selected"
                alignment_debug = alignment.get("alignment_debug") or {}
                alignment_debug["aligned_verification_preserved"] = aligned_verification_preserved
                alignment_debug["template_reference_processing_required"] = template_reference_processing_required
                alignment_debug["alignment_selection_reason"] = alignment_skip_reason or "aligned_verification_improved"
                alignment["alignment_debug"] = alignment_debug
            else:
                alignment["alignment_status"] = "fallback"
                alignment_debug = alignment.get("alignment_debug") or {}
                alignment_debug["reason"] = (
                    "aligned_verification_not_preserved"
                    if template_reference_processing_required
                    else "pre_alignment_verification_already_passed"
                    if not alignment_required
                    else "aligned_verification_did_not_improve_base_verification"
                )
                alignment_debug["aligned_verification_preserved"] = aligned_verification_preserved
                alignment_debug["template_reference_processing_required"] = template_reference_processing_required
                alignment_debug["alignment_status"] = "fallback"
                alignment_debug["verification_source_used"] = base_verification_source
                alignment["alignment_debug"] = alignment_debug
                verification = normalized_verification
                verification_source_used = base_verification_source
                post_alignment_processing_source = base_verification_source
                alignment_processing_image_selected = False
                if alignment_skip_reason is None:
                    alignment_skip_reason = alignment_debug["reason"]

    alignment_debug = alignment.get("alignment_debug") or {}
    alignment_score = float(alignment.get("alignment_score") or alignment_debug.get("alignment_score") or 0.0)
    alignment_status = str(alignment.get("alignment_status") or "fallback")

    normalized_verification_score = normalized_score
    aligned_verification_score = aligned_score
    verification_improvement = (
        round(aligned_score - normalized_score, 4)
        if aligned_score is not None
        else None
    )

    alignment_debug["before_alignment_verification"] = round(normalized_score, 4)
    alignment_debug["normalized_verification_score"] = round(normalized_score, 4)
    alignment_debug["after_alignment_verification"] = round(aligned_score, 4) if aligned_score is not None else None
    alignment_debug["aligned_verification_score"] = round(aligned_score, 4) if aligned_score is not None else None
    alignment_debug["verification_improvement"] = verification_improvement
    alignment_debug["verification_image_used"] = verification_source_used
    alignment_debug["verification_source_used"] = verification_source_used
    alignment_debug["alignment_required"] = alignment_required
    alignment_debug["alignment_skip_reason"] = alignment_skip_reason
    alignment_debug["pre_alignment_processing_source"] = pre_alignment_processing_source
    alignment_debug["post_alignment_processing_source"] = post_alignment_processing_source
    alignment_debug["alignment_processing_image_selected"] = alignment_processing_image_selected
    alignment_debug["layout_reference_crop"] = layout_reference_crop_debug
    alignment_debug["alignment_query_signature_source"] = alignment_query_signature_source
    alignment_debug["alignment_query_image_path"] = verification_query_image_path
    if layout_reference_crop_debug.get("applied"):
        alignment_debug["layout_reference_crop_applied"] = True
        alignment_debug["layout_reference_crop_reason"] = layout_reference_crop_debug.get("reason")

    alignment_reason = _alignment_reason(alignment_status, alignment, alignment_debug)
    alignment_debug["alignment_status"] = alignment_status
    alignment_debug["alignment_reason"] = alignment_reason

    alignment["alignment_debug"] = alignment_debug
    alignment["alignment_status"] = alignment_status
    alignment["alignment_reason"] = alignment_reason

    step_started = time.perf_counter()
    retrieval_score = float(result.get("score", 0.0) or 0.0)
    if verification_strategy == VERIFICATION_STRATEGY_STRICT:
        decision = decision_service.decide_candidate_strict(
            retrieval_score,
            verification,
            final_confidence_threshold,
            matching_weights,
        )
    else:
        decision = decision_service.decide_candidate(
            retrieval_score,
            verification,
            final_confidence_threshold,
            matching_weights,
        )
    layout_threshold = float((template or {}).get("similarity_threshold") or metadata.get("similarity_threshold") or DETECTION_THRESHOLD)
    if retrieval_score < layout_threshold:
        decision = {
            **decision,
            "final_passed": False,
            "decision_reason": "คะแนนรวมต่ำกว่าเกณฑ์",
            "decision_path": "คะแนนรวมต่ำกว่าเกณฑ์",
        }
    candidate_rejection_reason = None if decision.get("final_passed") else (
        decision.get("decision_reason")
        or decision.get("decision_path")
        or "candidate_failed_final_decision"
    )
    candidate_timing["decision"] = time.perf_counter() - step_started
    extraction_image_path = str(alignment.get("aligned_image_path") or verification_query_image_path) if verification_source_used == "aligned" else verification_query_image_path
    extraction_image_preview_url = _detection_preview_url(extraction_image_path)
    selected_processing_source = verification_source_used
    selected_processing_path = extraction_image_path
    processing_image_size = _image_dimensions(extraction_image_path)
    template_page_size = layout_reference_crop_debug.get("template_page_size")
    if isinstance(layout_reference_crop_debug.get("final_crop"), dict):
        layout_reference_crop_debug["final_crop"]["selected_processing_source"] = selected_processing_source
    roi_coordinate_space = "template_canvas" if alignment_status in {"aligned", "skipped"} else "projected"

    template_fields: List[Dict[str, Any]] = []
    template_rois: List[Dict[str, Any]] = []
    extraction_test = {
        "template_id": template_id,
        "status": "not_run",
        "tested_count": 0,
        "passed_count": 0,
        "failed_count": 0,
        "fields": [],
        "reason": "candidate_did_not_pass_final_decision",
    }

    projection = {
        "template_id": template_id,
        "status": "skipped",
        "method": "not_run",
        "anchors_expected": 0,
        "anchors_matched": 0,
        "inliers": 0,
        "reprojection_error": None,
        "confidence": 0.0,
        "fallback_reason": "candidate_did_not_pass_final_decision",
        "matched_anchors": [],
        "projected_fields": [],
        "roi_coordinate_space": roi_coordinate_space,
        "extraction_image_path": extraction_image_path,
        "extraction_image_preview_url": extraction_image_preview_url,
    }
    if template_id and decision["final_passed"]:
        step_started = time.perf_counter()
        template_fields = _fetch_template_fields(template_id)
        candidate_timing["roi_field_load"] = time.perf_counter() - step_started
        step_started = time.perf_counter()
        template_rois = _template_roi_items(template_fields, template_page_number)
        candidate_timing["roi_items"] = time.perf_counter() - step_started
        step_started = time.perf_counter()
        if roi_coordinate_space == "template_canvas":
            projection = _template_canvas_projection(
                template_id,
                template_fields,
                template_page_number,
                alignment_status,
                alignment_reason,
                extraction_image_path,
                extraction_image_preview_url,
            )
        else:
            try:
                projection_page_paths = dict(candidate_page_image_paths)
                projection_page_paths[template_page_number] = extraction_image_path
                projection = projection_service.project(
                    template_id,
                    template_fields,
                    projection_page_paths,
                )
                projection["roi_coordinate_space"] = roi_coordinate_space
                projection["extraction_image_path"] = extraction_image_path
                projection["extraction_image_preview_url"] = extraction_image_preview_url
            except Exception as error:
                projection = {
                    "template_id": template_id,
                    "status": "failed",
                    "method": "ratio_fallback",
                    "anchors_expected": 0,
                    "anchors_matched": 0,
                    "inliers": 0,
                    "reprojection_error": None,
                    "confidence": 0.0,
                    "fallback_reason": f"projection_runtime_error: {error}",
                    "matched_anchors": [],
                    "projected_fields": [],
                    "roi_coordinate_space": roi_coordinate_space,
                    "extraction_image_path": extraction_image_path,
                    "extraction_image_preview_url": extraction_image_preview_url,
                }
        candidate_timing["projection"] = time.perf_counter() - step_started
        step_started = time.perf_counter()
        extraction_test = _run_extraction_test(
            template_id,
            template_fields,
            projection.get("projected_fields", []),
            extraction_image_path,
            template_page_number,
            str(projection.get("roi_coordinate_space") or roi_coordinate_space),
        )
        candidate_timing["extraction_test"] = time.perf_counter() - step_started

    step_started = time.perf_counter()
    coordinate_debug = None
    if DETECTION_COORDINATE_DEBUG:
        template_image_source = _fetch_template_page_image_source(template_id, template_page_number) if template_id else None
        first_template_roi = template_rois[0].get("roi") if template_rois else None
        first_projected_field = (projection.get("projected_fields") or [None])[0]
        coordinate_debug = {
            "query_page_index": page_index,
            "template_page_number": template_page_number,
            "roi_coordinate_space": projection.get("roi_coordinate_space") or roi_coordinate_space,
            "verification_source_used": verification_source_used,
            "template_image_size": _image_source_dimensions(template_image_source),
            "normalized_image_size": _image_dimensions(query_image_path),
            "aligned_image_size": _image_dimensions(alignment.get("aligned_image_path")),
            "extraction_image_size": _image_dimensions(extraction_image_path),
            "first_template_roi": first_template_roi,
            "first_projected_roi": first_projected_field.get("projected_roi") if isinstance(first_projected_field, dict) else None,
        }
        print(
            "[detection-coordinate] "
            f"template={template_id} query_page={page_index} template_page={template_page_number} space={coordinate_debug['roi_coordinate_space']} "
            f"source={verification_source_used} template_size={coordinate_debug['template_image_size']} "
            f"extraction_size={coordinate_debug['extraction_image_size']} first_template_roi={first_template_roi}"
        )
    candidate_timing["coordinate_debug"] = time.perf_counter() - step_started

    return {
        "template_id": template_id,
        "vector_id": vector_id,
        "score": decision["final_score"],
        "retrieval_score": decision["retrieval_score"],
        "layout_score": decision["retrieval_score"],
        "layout_debug": metadata.get("layout_debug") or result.get("layout_debug"),
        "average_score": decision["retrieval_score"],
        "matched_pages": 1 if decision["final_passed"] else 0,
        "template_name": template_name,
        "template_status": template_status,
        "page_count": page_count,
        "field_count": field_count,
        "model_name": metadata.get("model_name") or metadata.get("layout_signature_version"),
        "vector_store_engine": metadata.get("vector_store_engine") or "layout-signature",
        "retrieval_engine": metadata.get("retrieval_engine") or "layout_signature",
        "query_page_index": page_index,
        "template_page_number": template_page_number,

        "alignment_status": alignment_status,
        "alignment": alignment,
        "alignment_debug": alignment_debug,
        "alignment_score": alignment_score,
        "alignment_passed": alignment_status == "aligned",
        "alignment_fallback_used": verification_source_used != "aligned",
        "alignment_reason": alignment_reason,
        "alignment_required": alignment_required,
        "alignment_skip_reason": alignment_skip_reason,
        "pre_alignment_processing_source": pre_alignment_processing_source,
        "post_alignment_processing_source": post_alignment_processing_source,
        "alignment_processing_image_selected": alignment_processing_image_selected,

        "normalized_verification_score": round(normalized_verification_score, 4),
        "aligned_verification_score": round(aligned_verification_score, 4) if aligned_verification_score is not None else None,
        "verification_source_used": verification_source_used,
        "candidate_rejection_reason": candidate_rejection_reason,
        "full_frame_document_detected": layout_reference_crop_debug.get("full_frame_document_detected"),
        "full_frame_confidence": layout_reference_crop_debug.get("full_frame_confidence"),
        "crop_required": layout_reference_crop_debug.get("crop_required"),
        "crop_skip_reason": layout_reference_crop_debug.get("crop_skip_reason"),
        "before_alignment_verification": round(normalized_verification_score, 4),
        "after_alignment_verification": round(aligned_verification_score, 4) if aligned_verification_score is not None else None,
        "verification_improvement": verification_improvement,

        "alignment_match_image_path": alignment.get("alignment_match_image_path"),
        "alignment_match_image_preview_url": alignment.get("alignment_match_image_preview_url"),
        "aligned_image_path": alignment.get("aligned_image_path"),
        "aligned_image_preview_url": alignment.get("aligned_image_preview_url"),
        "normalized_image_path": query_image_path,
        "normalized_image_preview_url": _detection_preview_url(query_image_path),
        "extraction_image_path": extraction_image_path,
        "extraction_image_preview_url": extraction_image_preview_url,
        "selected_processing_source": selected_processing_source,
        "selected_processing_path": selected_processing_path,
        "projected_document_box": layout_reference_crop_debug.get("projected_document_box"),
        "processing_image_size": processing_image_size,
        "template_page_size": template_page_size,
        "roi_coordinate_space": roi_coordinate_space,
        "layout_reference_crop": layout_reference_crop_debug,
        "final_crop": layout_reference_crop_debug.get("final_crop"),

        "verification": verification,
        "verification_details": (
            verification.get("checked_fields", [])
            if isinstance(verification, dict)
            else []
        ),
        "verification_score": decision["verification_score"],
        "text_anchor_score": decision.get("text_anchor_score"),
        "image_anchor_score": decision.get("image_anchor_score"),
        "anchor_score": decision.get("anchor_score"),
        "matching_weights": decision.get("matching_weights"),
        "effective_matching_weights": decision.get("effective_matching_weights"),
        "verification_passed": decision["verification_passed"],
        "final_score": decision["final_score"],
        "final_passed": decision["final_passed"],
        "decision_reason": decision["decision_reason"],
        "decision_path": decision["decision_path"],
        "required_passed": decision.get("required_passed"),
        "required_failed_fields": decision.get("required_failed_fields", []),
        "final_confidence_threshold": decision["final_confidence_threshold"],
        "layout_similarity_threshold": layout_threshold,
        "template_rois": template_rois,
        "projection": projection,
        "projected_fields": projection.get("projected_fields", []),
        "extraction_test": extraction_test,
        "coordinate_debug": coordinate_debug,
        "metadata": metadata,
        "verification_strategy": verification_strategy,
        "evaluation_status": "full",
        "alignment_evaluated": bool(allow_alignment),
        "timing": {
            "normalized_verification_ms": _ms(candidate_timing.get("normalized_verification")),
            "alignment_ms": _ms(candidate_timing.get("alignment")),
            "aligned_verification_ms": _ms(candidate_timing.get("aligned_verification")),
            "text_verification_ms": _ms(candidate_timing.get("text_verification")),
            "image_verification_ms": _ms(candidate_timing.get("image_verification")),
            "text_ocr_inference_ms": _ms(candidate_timing.get("text_ocr_inference")),
            "image_model_inference_ms": _ms(candidate_timing.get("image_model_inference")),
            "normalized_verification_breakdown": normalized_internal_timing_ms,
            "aligned_verification_breakdown": aligned_internal_timing_ms,
            "projection_breakdown": _timing_ms_map(projection.get("timing") if isinstance(projection, dict) else {}),
            "extraction_test_breakdown": _timing_ms_map(extraction_test.get("timing") if isinstance(extraction_test, dict) else {}),
            "candidate_overhead_breakdown": {
                "template_fetch_ms": _ms(candidate_timing.get("template_fetch")),
                "field_count_query_ms": _ms(candidate_timing.get("field_count_query")),
                "verification_fields_load_ms": _ms(candidate_timing.get("verification_fields_load")),
                "candidate_setup_ms": _ms(candidate_timing.get("candidate_setup")),
                "decision_ms": _ms(candidate_timing.get("decision")),
                "roi_field_load_ms": _ms(candidate_timing.get("roi_field_load")),
                "roi_items_ms": _ms(candidate_timing.get("roi_items")),
                "projection_ms": _ms(candidate_timing.get("projection")),
                "extraction_test_ms": _ms(candidate_timing.get("extraction_test")),
                "layout_reference_crop_ms": _ms(candidate_timing.get("layout_reference_crop")),
                "coordinate_debug_ms": _ms(candidate_timing.get("coordinate_debug")),
            },
            "cache": candidate_cache_debug,
            "db": candidate_db_debug,
        },
    }


def _lightweight_candidate_from_result(result: Dict[str, Any], include_template_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    metadata = result.get("metadata") or {}
    vector_id = str(result.get("vector_id") or "")
    template_id = _template_id_from_metadata(metadata, vector_id)

    template_status = metadata.get("template_status")
    if template_status != "active" and template_id != include_template_id:
        return None

    template_name = metadata.get("template_name")
    page_count = metadata.get("page_count")
    final_confidence_threshold = decision_service.final_confidence_threshold(None, metadata)
    field_count = metadata.get("field_count")

    retrieval_score = round(float(result.get("score", 0.0) or 0.0), 4)
    verification = {
        "template_id": template_id,
        "status": "not_evaluated_fast_path",
        "passed": False,
        "score": 0.0,
        "text_anchor_score": 0.0,
        "image_anchor_score": 0.0,
        "required_passed": False,
        "checked_fields": [],
    }
    return {
        "template_id": template_id,
        "vector_id": vector_id,
        "score": retrieval_score,
        "retrieval_score": retrieval_score,
        "layout_score": retrieval_score,
        "layout_debug": metadata.get("layout_debug") or result.get("layout_debug"),
        "average_score": retrieval_score,
        "matched_pages": 0,
        "template_name": template_name,
        "template_status": template_status,
        "page_count": page_count,
        "field_count": field_count,
        "model_name": metadata.get("model_name") or metadata.get("layout_signature_version"),
        "vector_store_engine": metadata.get("vector_store_engine") or "layout-signature",
        "retrieval_engine": metadata.get("retrieval_engine") or "layout_signature",
        "query_page_index": metadata.get("query_page_index"),
        "template_page_number": metadata.get("matched_layout_reference_page_number") or metadata.get("page_number"),
        "alignment_status": "not_evaluated",
        "alignment": _alignment_result("skipped", "candidate_not_fully_evaluated_fast_path"),
        "alignment_debug": {"reason": "candidate_not_fully_evaluated_fast_path"},
        "alignment_score": 0.0,
        "alignment_passed": False,
        "alignment_fallback_used": True,
        "alignment_reason": "candidate_not_fully_evaluated_fast_path",
        "normalized_verification_score": None,
        "aligned_verification_score": None,
        "verification_source_used": None,
        "before_alignment_verification": None,
        "after_alignment_verification": None,
        "verification_improvement": None,
        "alignment_match_image_path": None,
        "alignment_match_image_preview_url": None,
        "aligned_image_path": None,
        "aligned_image_preview_url": None,
        "normalized_image_path": None,
        "normalized_image_preview_url": None,
        "extraction_image_path": None,
        "extraction_image_preview_url": None,
        "roi_coordinate_space": None,
        "verification": verification,
        "verification_details": [],
        "verification_score": 0.0,
        "text_anchor_score": 0.0,
        "image_anchor_score": 0.0,
        "anchor_score": 0.0,
        "verification_passed": False,
        "final_score": retrieval_score,
        "final_passed": False,
        "decision_reason": "not_evaluated_fast_path",
        "decision_path": "not_evaluated_fast_path",
        "required_passed": False,
        "required_failed_fields": [],
        "final_confidence_threshold": final_confidence_threshold,
        "layout_similarity_threshold": float(metadata.get("similarity_threshold") or DETECTION_THRESHOLD),
        "projection": {
            "template_id": template_id,
            "status": "not_evaluated",
            "method": "fast_path_skipped",
            "projected_fields": [],
        },
        "projected_fields": [],
        "metadata": metadata,
        "evaluation_status": "lightweight",
        "alignment_evaluated": False,
    }


def _detect_page(
    page_info: Dict[str, Any],
    page_image_paths: Dict[int, str],
    include_template_id: Optional[str] = None,
    timing: Optional[Dict[str, float]] = None,
    retrieval_limit: int = DETECTION_RETRIEVAL_LIMIT,
    verification_strategy: str = "standard",
    verification_candidate_limit: int = DETECTION_VERIFICATION_CANDIDATE_LIMIT,
    full_evaluation_limit_override: Optional[int] = None,
    query_page_count: Optional[int] = None,
    request_cache: Optional[DetectionRequestCache] = None,
) -> Dict[str, Any]:
    page_index = int(page_info["page_index"])
    normalized_image_path = str(page_info["normalized_path"])
    processing_image_path = str(page_info.get("original_path") or normalized_image_path)
    matching_image_path = str(page_info.get("matching_path") or normalized_image_path)
    query_signature = _layout_signature_for_image_path(matching_image_path, timing=timing)
    matching_path_obj = Path(matching_image_path)
    retrieval_output_root = matching_path_obj.parent.parent if matching_path_obj.parent.name == "normalized" else matching_path_obj.parent
    retrieval_signature, retrieval_precrop_debug = _retrieval_precrop_signature(
        query_signature,
        image_path=matching_image_path,
        output_dir=retrieval_output_root / "retrieval_crop",
        page_number=page_index,
    )
    step_started = time.perf_counter()
    raw_results = search_layout_candidates(
        retrieval_signature,
        page_number=page_index,
        limit=retrieval_limit,
        include_template_id=include_template_id,
        timing=timing,
    )
    if timing is not None:
        timing["template_matching"] = timing.get("template_matching", 0.0) + (time.perf_counter() - step_started)
    candidates = []
    full_evaluation_count = 0
    early_reject_count = 0
    early_accept_rank = None
    early_accept_enabled = verification_strategy == VERIFICATION_STRATEGY_STRICT
    candidate_verification_limit = max(1, int(verification_candidate_limit or DETECTION_VERIFICATION_CANDIDATE_LIMIT))
    configured_full_evaluation_limit = (
        max(1, int(full_evaluation_limit_override))
        if full_evaluation_limit_override is not None
        else DETECTION_FULL_EVAL_LIMIT
    )
    full_evaluation_limit = min(configured_full_evaluation_limit, candidate_verification_limit)
    for index, result in enumerate(raw_results, start=1):
        metadata = result.get("metadata") or {}
        result_template_id = str(metadata.get("template_id") or "")
        is_included_template = bool(include_template_id and result_template_id == include_template_id)
        result_detection_mode = str(metadata.get("detection_mode") or "all_pages")
        result_main_page_number = int(metadata.get("main_page_number") or 1)
        main_page_auto_roi_only = result_detection_mode == "main_page" and page_index != result_main_page_number
        template_page_count = int(metadata.get("page_count") or 0)
        all_pages_page_count_mismatch = (
            result_detection_mode != "main_page"
            and bool(query_page_count)
            and bool(template_page_count)
            and int(query_page_count or 0) != template_page_count
        )
        layout_score = float(result.get("layout_score", result.get("score", 0.0)) or 0.0)
        layout_similarity_threshold = _layout_similarity_threshold(metadata)
        layout_confident = layout_score >= DecisionService.MIN_RETRIEVAL_SCORE
        early_rejected = layout_score < layout_similarity_threshold
        should_fully_evaluate = (
            layout_confident
            and not early_rejected
            and not all_pages_page_count_mismatch
            and index <= candidate_verification_limit
            and not main_page_auto_roi_only
            and (
                is_included_template
                or (
                    (not early_accept_enabled or early_accept_rank is None)
                    and full_evaluation_count < full_evaluation_limit
                )
            )
        )
        if should_fully_evaluate:
            full_evaluation_count += 1
            step_started = time.perf_counter()
            candidate = _candidate_from_result(
                result,
                page_image_paths,
                page_index,
                processing_image_path,
                page_info.get("normalization"),
                query_signature=query_signature,
                allow_alignment=index <= DETECTION_ALIGNMENT_LIMIT,
                include_template_id=include_template_id,
                verification_strategy=verification_strategy,
                request_cache=request_cache,
            )
            verification_elapsed = time.perf_counter() - step_started
            if timing is not None:
                timing["verification"] = timing.get("verification", 0.0) + verification_elapsed
            if isinstance(candidate, dict):
                candidate_timing = candidate.get("timing") if isinstance(candidate.get("timing"), dict) else {}
                candidate["timing"] = {
                    "candidate_total_ms": _ms(verification_elapsed),
                    **candidate_timing,
                }
        else:
            candidate = _lightweight_candidate_from_result(result, include_template_id=include_template_id)
        if candidate is not None:
            candidate["verification_strategy"] = verification_strategy
            candidate["query_page_index"] = page_index
            candidate["template_page_number"] = candidate.get("template_page_number") or metadata.get("matched_layout_reference_page_number") or metadata.get("page_number")
            candidate["retrieval_rank"] = index
            candidate["layout_confident"] = layout_confident
            candidate["top_k_limit"] = retrieval_limit
            if not layout_confident:
                candidate["final_passed"] = False
                candidate["decision_reason"] = "คะแนนรวมต่ำกว่าเกณฑ์"
                candidate["decision_path"] = "คะแนนรวมต่ำกว่าเกณฑ์"
                candidate["evaluation_status"] = "layout_rejected"
            if early_rejected and layout_confident:
                early_reject_count += 1
                candidate["final_passed"] = False
                candidate["decision_reason"] = "layout_similarity_threshold_failed"
                candidate["decision_path"] = "layout_similarity_threshold_failed"
                candidate["evaluation_status"] = "layout_threshold_rejected"
            if all_pages_page_count_mismatch:
                candidate["final_passed"] = False
                candidate["decision_reason"] = "all_pages_page_count_mismatch"
                candidate["decision_path"] = "all_pages_page_count_mismatch"
                candidate["evaluation_status"] = "page_count_rejected"
                candidate["query_page_count"] = int(query_page_count or 0)
                candidate["template_page_count"] = template_page_count
            if main_page_auto_roi_only:
                candidate["final_passed"] = False
                candidate["decision_reason"] = "main_page_detection_uses_auto_roi_for_non_main_pages"
                candidate["decision_path"] = "main_page_detection_uses_auto_roi_for_non_main_pages"
                candidate["evaluation_status"] = "main_page_auto_roi_only"
            candidates.append(candidate)
            if should_fully_evaluate and candidate["final_passed"] and early_accept_rank is None:
                early_accept_rank = index
                if early_accept_enabled and not include_template_id:
                    break

    candidates = sorted(
        candidates,
        key=lambda item: (
            bool(item["final_passed"]),
            item.get("evaluation_status") == "full",
            item["final_score"],
            item["retrieval_score"],
        ),
        reverse=True,
    )
    passing_candidates = [candidate for candidate in candidates if candidate["final_passed"]]
    best_candidate = passing_candidates[0] if passing_candidates else None
    matched = best_candidate is not None
    confident_layout_count = sum(1 for candidate in candidates if candidate.get("layout_confident"))
    selected_processing_path = (
        str(best_candidate.get("extraction_image_path") or "")
        if isinstance(best_candidate, dict)
        else ""
    )
    if not selected_processing_path:
        selected_processing_path = processing_image_path
    selected_processing_preview_url = _detection_preview_url(selected_processing_path)
    selected_processing_source = (
        str(best_candidate.get("verification_source_used") or "matched_candidate")
        if isinstance(best_candidate, dict)
        else "original"
    )
    return {
        "page_index": page_index,
        "matched": matched,
        "best_candidate": best_candidate,
        "candidates": candidates,
        "image_preview_data_url": _image_to_data_url(Path(selected_processing_path)),
        "original_image_preview_url": _detection_preview_url(str(page_info["original_path"])),
        "normalized_image_preview_url": _detection_preview_url(normalized_image_path),
        "selected_processing_preview_url": selected_processing_preview_url,
        "matching_image_preview_url": _detection_preview_url(matching_image_path),
        "original_image_path": str(page_info["original_path"]),
        "normalized_image_path": normalized_image_path,
        "processing_image_path": processing_image_path,
        "selected_processing_source": selected_processing_source,
        "selected_processing_path": selected_processing_path,
        "selected_processing_image_size": _image_dimensions(selected_processing_path),
        "matching_image_path": matching_image_path,
        "normalization": page_info["normalization"],
        "matching_normalization": page_info.get("matching_normalization"),
        "debug": {
            "query_image_path": str(page_info["original_path"]),
            "normalized_query_image_path": normalized_image_path,
            "processing_query_image_path": processing_image_path,
            "selected_processing_source": selected_processing_source,
            "selected_processing_path": selected_processing_path,
            "selected_processing_preview_url": selected_processing_preview_url,
            "selected_processing_image_size": _image_dimensions(selected_processing_path),
            "matching_query_image_path": matching_image_path,
            "original_image_preview_url": _detection_preview_url(str(page_info["original_path"])),
            "normalized_image_preview_url": _detection_preview_url(normalized_image_path),
            "matching_image_preview_url": _detection_preview_url(matching_image_path),
            "query_engine": "layout_signature",
            "query_signature_source": (
                "retrieval_precrop_layout_space"
                if retrieval_precrop_debug.get("retrieval_precrop_applied")
                else "original_layout_space" if matching_image_path != normalized_image_path else "normalized_image"
            ),
            "retrieval_precrop": retrieval_precrop_debug,
            "retrieval_crop": retrieval_precrop_debug.get("retrieval_crop"),
            "retrieval_precrop_attempted": retrieval_precrop_debug.get("retrieval_precrop_attempted"),
            "retrieval_precrop_applied": retrieval_precrop_debug.get("retrieval_precrop_applied"),
            "retrieval_precrop_box": retrieval_precrop_debug.get("retrieval_precrop_box"),
            "retrieval_precrop_coverage": retrieval_precrop_debug.get("retrieval_precrop_coverage"),
            "retrieval_precrop_image_path": retrieval_precrop_debug.get("retrieval_precrop_image_path"),
            "retrieval_precrop_preview_url": retrieval_precrop_debug.get("retrieval_precrop_preview_url"),
            "retrieval_source": retrieval_precrop_debug.get("retrieval_source"),
            "query_version": query_signature.get("version"),
            "query_model_name": query_signature.get("model"),
            "query_vector_dimension": 0,
            "query_input_count": 1,
            "query_layout_signature": query_signature,
            "raw_candidate_count": len(raw_results),
            "active_candidate_count": len(candidates),
            "confident_layout_candidate_count": confident_layout_count,
            "layout_confidence_threshold": DecisionService.MIN_RETRIEVAL_SCORE,
            "top_k_limit": retrieval_limit,
            "retrieval_limit": retrieval_limit,
            "full_evaluation_limit": full_evaluation_limit,
            "verification_candidate_limit": candidate_verification_limit,
            "full_evaluation_count": full_evaluation_count,
            "early_reject_count": early_reject_count,
            "early_reject_rule": "layout_score_below_template_similarity_threshold",
            "early_accept_enabled": early_accept_enabled,
            "early_accept_rank": early_accept_rank,
            "early_accept_reason": "top_candidate_final_passed" if early_accept_rank else None,
            "standard_evaluates_all_eligible_top_k": verification_strategy != VERIFICATION_STRATEGY_STRICT,
            "verification_strategy": verification_strategy,
            "alignment_limit": DETECTION_ALIGNMENT_LIMIT,
            "fast_path_enabled": early_accept_rank is not None or full_evaluation_limit < retrieval_limit or DETECTION_ALIGNMENT_LIMIT < full_evaluation_limit,
            "aligned_candidate_paths": [
                candidate["alignment"]["aligned_image_path"]
                for candidate in candidates
                if candidate.get("alignment", {}).get("aligned_image_path")
            ],
            "alignment_match_image_paths": [
                candidate["alignment"]["alignment_match_image_path"]
                for candidate in candidates
                if candidate.get("alignment", {}).get("alignment_match_image_path")
            ],
        },
    }


def _aggregate_candidates(pages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_template: Dict[str, List[Dict[str, Any]]] = {}
    for page in pages:
        for candidate in page["candidates"]:
            template_id = candidate.get("template_id")
            if template_id:
                by_template.setdefault(template_id, []).append(candidate)

    aggregated = []
    for template_id, page_candidates in by_template.items():
        best_page_cand = max(
            page_candidates,
            key=lambda c: (
                bool(c.get("final_passed")),
                c.get("evaluation_status") == "full",
                float(c.get("final_score") or 0.0),
                float(c.get("retrieval_score") or 0.0),
            ),
        )
        retrieval_scores = [c["retrieval_score"] for c in page_candidates]
        max_retrieval_score = max(retrieval_scores)
        avg_retrieval_score = sum(retrieval_scores) / len(retrieval_scores)
        matched_pages_count = sum(1 for candidate in page_candidates if candidate["final_passed"])
        verification = best_page_cand.get("verification") or {
            "status": "failed",
            "passed": False,
            "score": 0.0,
            "required_passed": False,
            "checked_fields": [],
        }
        verification_strategy = normalize_verification_strategy(best_page_cand.get("verification_strategy"))
        if verification_strategy == VERIFICATION_STRATEGY_STRICT:
            decision = decision_service.decide_candidate_strict(
                max_retrieval_score,
                verification,
                float(best_page_cand.get("final_confidence_threshold") or DecisionService.DEFAULT_FINAL_CONFIDENCE_THRESHOLD),
                best_page_cand.get("matching_weights"),
            )
        else:
            decision = decision_service.decide_candidate(
                max_retrieval_score,
                verification,
                float(best_page_cand.get("final_confidence_threshold") or DecisionService.DEFAULT_FINAL_CONFIDENCE_THRESHOLD),
                best_page_cand.get("matching_weights"),
            )

        aggregated.append({
            "template_id": template_id,
            "vector_id": best_page_cand["vector_id"],
            "score": decision["final_score"],
            "retrieval_score": decision["retrieval_score"],
            "layout_score": decision["retrieval_score"],
            "layout_debug": best_page_cand.get("layout_debug"),
            "average_score": avg_retrieval_score,
            "matched_pages": matched_pages_count,
            "template_name": best_page_cand["template_name"],
            "template_status": best_page_cand["template_status"],
            "page_count": best_page_cand["page_count"],
            "field_count": best_page_cand["field_count"],
            "model_name": best_page_cand["model_name"],
            "vector_store_engine": best_page_cand["vector_store_engine"],
            "retrieval_engine": best_page_cand.get("retrieval_engine"),
            "query_page_index": best_page_cand.get("query_page_index"),
            "template_page_number": best_page_cand.get("template_page_number"),
            "alignment_status": best_page_cand.get("alignment_status"),
            "alignment": best_page_cand.get("alignment"),
            "alignment_debug": best_page_cand.get("alignment_debug"),
            "alignment_score": best_page_cand.get("alignment_score"),
            "alignment_passed": best_page_cand.get("alignment_passed"),
            "alignment_fallback_used": best_page_cand.get("alignment_fallback_used"),
            "alignment_reason": best_page_cand.get("alignment_reason"),
            "normalized_verification_score": best_page_cand.get("normalized_verification_score"),
            "aligned_verification_score": best_page_cand.get("aligned_verification_score"),
            "verification_source_used": best_page_cand.get("verification_source_used"),
            "before_alignment_verification": best_page_cand.get("before_alignment_verification"),
            "after_alignment_verification": best_page_cand.get("after_alignment_verification"),
            "verification_improvement": best_page_cand.get("verification_improvement"),
            "alignment_match_image_path": best_page_cand.get("alignment_match_image_path"),
            "alignment_match_image_preview_url": best_page_cand.get("alignment_match_image_preview_url"),
            "aligned_image_path": best_page_cand.get("aligned_image_path"),
            "aligned_image_preview_url": best_page_cand.get("aligned_image_preview_url"),
            "normalized_image_path": best_page_cand.get("normalized_image_path"),
            "normalized_image_preview_url": best_page_cand.get("normalized_image_preview_url"),
            "extraction_image_path": best_page_cand.get("extraction_image_path"),
            "extraction_image_preview_url": best_page_cand.get("extraction_image_preview_url"),
            "selected_processing_source": best_page_cand.get("selected_processing_source"),
            "selected_processing_path": best_page_cand.get("selected_processing_path"),
            "projected_document_box": best_page_cand.get("projected_document_box"),
            "processing_image_size": best_page_cand.get("processing_image_size"),
            "template_page_size": best_page_cand.get("template_page_size"),
            "roi_coordinate_space": best_page_cand.get("roi_coordinate_space"),
            "layout_reference_crop": best_page_cand.get("layout_reference_crop"),
            "final_crop": best_page_cand.get("final_crop"),
            "verification": best_page_cand.get("verification"),
            "verification_details": (
                best_page_cand.get("verification_details")
                or (best_page_cand.get("verification") or {}).get("checked_fields", [])
            ),
            "verification_score": decision["verification_score"],
            "text_anchor_score": decision.get("text_anchor_score"),
            "image_anchor_score": decision.get("image_anchor_score"),
            "anchor_score": decision.get("anchor_score"),
            "matching_weights": decision.get("matching_weights"),
            "effective_matching_weights": decision.get("effective_matching_weights"),
            "verification_passed": decision["verification_passed"],
            "final_score": decision["final_score"],
            "final_passed": decision["final_passed"],
            "decision_reason": decision["decision_reason"],
            "decision_path": decision["decision_path"],
            "required_passed": decision.get("required_passed"),
            "required_failed_fields": decision.get("required_failed_fields", []),
            "final_confidence_threshold": decision["final_confidence_threshold"],
            "layout_similarity_threshold": best_page_cand.get("layout_similarity_threshold"),
            "template_rois": best_page_cand.get("template_rois", []),
            "projection": best_page_cand.get("projection"),
            "projected_fields": best_page_cand.get("projected_fields", []),
            "extraction_test": best_page_cand.get("extraction_test"),
            "coordinate_debug": best_page_cand.get("coordinate_debug"),
            "metadata": best_page_cand.get("metadata", {}),
            "verification_strategy": verification_strategy,
        })

    return sorted(aggregated, key=lambda item: (item["final_score"], item["retrieval_score"]), reverse=True)


def _detection_engine(pages: List[Dict[str, Any]]) -> str:
    return "layout_signature"


def _is_confirmed_main_page_candidate(candidate: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(candidate, dict) or not candidate.get("final_passed"):
        return False
    metadata = candidate.get("metadata") if isinstance(candidate.get("metadata"), dict) else {}
    detection_mode = str(candidate.get("detection_mode") or metadata.get("detection_mode") or "")
    return detection_mode == "main_page"


def _layout_similarity_threshold(metadata: Dict[str, Any]) -> float:
    return float(metadata.get("similarity_threshold") or DETECTION_THRESHOLD)


def _no_match_message(candidates: List[Dict[str, Any]]) -> str:
    if not candidates:
        return "ไม่พบ Template ที่เปิดใช้งานใน 5 อันดับแรกจากการค้นหา Layout"
    confident_layout_count = sum(
        1 for candidate in candidates
        if float(candidate.get("layout_score", candidate.get("retrieval_score", 0.0)) or 0.0) >= DecisionService.MIN_RETRIEVAL_SCORE
    )
    if confident_layout_count == 0:
        return "ไม่มี Template ใน 5 อันดับแรกที่มีคะแนน Layout ถึงเกณฑ์ 0.50"
    return "ไม่มี Template ที่ผ่านเกณฑ์การตรวจสอบและคะแนนความมั่นใจ"


def _detection_timing_debug(
    timing: Dict[str, float],
    pages: List[Dict[str, Any]],
    total_started: float,
    request_cache: Optional[DetectionRequestCache] = None,
) -> Dict[str, Any]:
    debug_started = time.perf_counter()
    candidate_timings: List[Dict[str, Any]] = []
    for page in pages:
        for candidate in page.get("candidates", []):
            if not isinstance(candidate, dict):
                continue
            candidate_timing = candidate.get("timing")
            if not isinstance(candidate_timing, dict):
                continue
            candidate_timings.append(
                {
                    "page_index": candidate.get("query_page_index") or page.get("page_index"),
                    "retrieval_rank": candidate.get("retrieval_rank"),
                    "template_id": candidate.get("template_id"),
                    "template_name": candidate.get("template_name"),
                    "evaluation_status": candidate.get("evaluation_status"),
                    "verification_strategy": candidate.get("verification_strategy"),
                    "verification_source_used": candidate.get("verification_source_used"),
                    "alignment_status": candidate.get("alignment_status"),
                    **candidate_timing,
                }
            )

    total_elapsed = time.perf_counter() - total_started
    top_level_keys = [
        "prepare_pages",
        "layout_analysis",
        "signature_build",
        "template_matching",
        "verification",
        "candidate_aggregation",
        "request_auto_roi",
        "auto_roi",
    ]
    top_level_measured = sum(float(timing.get(key) or 0.0) for key in top_level_keys)
    detect_pages_inner_keys = [
        "layout_analysis",
        "signature_build",
        "template_matching",
        "verification",
    ]
    detect_pages_inner_measured = sum(float(timing.get(key) or 0.0) for key in detect_pages_inner_keys)
    boundary_keys = [
        "source_type_detection",
        "post_prepare_setup",
        "verification_strategy_load",
        "include_template_lookup",
        "detect_pages_total",
        "prepublish_logging",
        "final_selection",
        "best_candidate_patch",
    ]
    boundary_measured = sum(float(timing.get(key) or 0.0) for key in boundary_keys)
    detect_pages_overhead = max(0.0, float(timing.get("detect_pages_total") or 0.0) - detect_pages_inner_measured)

    return {
        "total_detection_ms": _ms(total_elapsed),
        "prepare_pages_ms": _ms(timing.get("prepare_pages")),
        "prepare_pages_breakdown": {
            "pdf_convert_ms": _ms(timing.get("prepare_pdf_convert")),
            "image_save_ms": _ms(timing.get("prepare_image_save")),
            "normalized_dir_ms": _ms(timing.get("prepare_normalized_dir")),
            "normalization_ms": _ms(timing.get("prepare_normalization")),
            "normalization_skipped_ms": _ms(timing.get("prepare_normalization_skipped")),
        },
        "layout_analysis_ms": _ms(timing.get("layout_analysis")),
        "layout_analysis_breakdown": _debug_timing_value(timing.get("layout_analysis_breakdown") or []),
        "signature_build_ms": _ms(timing.get("signature_build")),
        "template_matching_ms": _ms(timing.get("template_matching")),
        "template_matching_breakdown": _template_matching_timing_ms_list(timing.get("layout_candidate_searches")),
        "verification_ms": _ms(timing.get("verification")),
        "verification_strategy_cache_hit": bool(
            (timing.get("verification_strategy_load_breakdown") or {}).get("verification_strategy_cache_hit")
        ),
        "candidate_aggregation_ms": _ms(timing.get("candidate_aggregation")),
        "request_auto_roi_ms": _ms(timing.get("request_auto_roi")),
        "auto_roi_ms": _ms(timing.get("auto_roi")),
        "boundary_breakdown": {
            "source_type_detection_ms": _ms(timing.get("source_type_detection")),
            "post_prepare_setup_ms": _ms(timing.get("post_prepare_setup")),
            "verification_strategy_load_ms": _ms(timing.get("verification_strategy_load")),
            "verification_strategy_load_breakdown": _template_matching_timing_ms(timing.get("verification_strategy_load_breakdown") or {}),
            "include_template_lookup_ms": _ms(timing.get("include_template_lookup")),
            "detect_pages_total_ms": _ms(timing.get("detect_pages_total")),
            "first_page_detection_total_ms": _ms(timing.get("first_page_detection_total")),
            "remaining_pages_decision_ms": _ms(timing.get("remaining_pages_decision")),
            "remaining_pages_detection_total_ms": _ms(timing.get("remaining_pages_detection_total")),
            "detect_pages_inner_measured_ms": _ms(detect_pages_inner_measured),
            "detect_pages_overhead_ms": _ms(detect_pages_overhead),
            "prepublish_logging_ms": _ms(timing.get("prepublish_logging")),
            "final_selection_ms": _ms(timing.get("final_selection")),
            "best_candidate_patch_ms": _ms(timing.get("best_candidate_patch")),
        },
        "boundary_measured_ms": _ms(boundary_measured),
        "top_level_measured_ms": _ms(top_level_measured),
        "unaccounted_ms": _ms(max(0.0, total_elapsed - top_level_measured)),
        "unaccounted_after_boundaries_ms": _ms(max(0.0, total_elapsed - top_level_measured - boundary_measured + detect_pages_inner_measured)),
        "debug_timing_build_ms": _ms(time.perf_counter() - debug_started),
        "request_cache": dict(request_cache.stats) if request_cache is not None else {},
        "candidates": candidate_timings,
    }


def detect_template_dev(
    file_bytes: bytes,
    include_template_id: Optional[str] = None,
    cleanup_generated: bool = True,
    prepublish_timing: bool = False,
    prepublish_total_started: Optional[float] = None,
    verification_strategy_override: Optional[str] = None,
    retrieval_limit_override: Optional[int] = None,
    verification_candidate_limit_override: Optional[int] = None,
    full_evaluation_limit_override: Optional[int] = None,
) -> Dict[str, Any]:
    query_id = f"detq_{uuid4().hex[:12]}"
    timing: Dict[str, float] = {}
    request_cache = DetectionRequestCache()
    total_started = prepublish_total_started or time.perf_counter()
    try:
        step_started = time.perf_counter()
        source_type = "pdf" if file_bytes.lstrip().startswith(b"%PDF") else "image"
        timing["source_type_detection"] = time.perf_counter() - step_started
        step_started = time.perf_counter()
        page_paths = _prepare_query_pages(query_id, file_bytes, timing=timing)
        skip_normalization = source_type == "pdf"
        normalized_pages = _normalize_query_pages(query_id, page_paths, skip_normalization=skip_normalization, timing=timing)
        timing["prepare_pages"] = time.perf_counter() - step_started
        if prepublish_timing:
            print(f"[PREPUBLISH] prepare pages done: {timing['prepare_pages']:.2f}s")
        step_started = time.perf_counter()
        page_image_paths = {page["page_index"]: page.get("original_path") or page["normalized_path"] for page in normalized_pages}
        query_page_count = len(normalized_pages)
        retrieval_limit = (
            max(1, int(retrieval_limit_override))
            if retrieval_limit_override is not None
            else DETECTION_RETRIEVAL_LIMIT if include_template_id else USER_DETECTION_RETRIEVAL_LIMIT
        )
        verification_candidate_limit = (
            max(1, int(verification_candidate_limit_override))
            if verification_candidate_limit_override is not None
            else DETECTION_VERIFICATION_CANDIDATE_LIMIT
        )
        timing["post_prepare_setup"] = time.perf_counter() - step_started
        step_started = time.perf_counter()
        if verification_strategy_override is not None:
            verification_strategy_setting = {"verification_strategy": verification_strategy_override}
            timing["verification_strategy_load_breakdown"] = {"source": "override"}
        else:
            verification_strategy_setting, verification_strategy_timing = global_settings_service.get_verification_strategy_with_timing()
            timing["verification_strategy_load_breakdown"] = verification_strategy_timing
        verification_strategy = normalize_verification_strategy(verification_strategy_setting.get("verification_strategy"))
        timing["verification_strategy_load"] = time.perf_counter() - step_started
        pages: List[Dict[str, Any]] = []
        confirmed_main_page_candidate: Optional[Dict[str, Any]] = None
        step_started = time.perf_counter()
        included_template, _, _ = _fetch_template_cached(include_template_id, request_cache)
        included_template_detection_mode = str((included_template or {}).get("detection_mode") or "all_pages")
        included_template_main_page_only = bool(include_template_id and included_template_detection_mode == "main_page")
        timing["include_template_lookup"] = time.perf_counter() - step_started
        detect_pages_started = time.perf_counter()
        if normalized_pages:
            first_page = normalized_pages[0]
            step_started = time.perf_counter()
            first_detected_page = _detect_page(
                first_page,
                page_image_paths,
                include_template_id=include_template_id,
                timing=timing,
                retrieval_limit=retrieval_limit,
                verification_strategy=verification_strategy,
                verification_candidate_limit=verification_candidate_limit,
                full_evaluation_limit_override=full_evaluation_limit_override,
                query_page_count=query_page_count,
                request_cache=request_cache,
            )
            timing["first_page_detection_total"] = time.perf_counter() - step_started
            pages.append(first_detected_page)
            step_started = time.perf_counter()
            first_best_candidate = first_detected_page.get("best_candidate")
            if _is_confirmed_main_page_candidate(first_best_candidate):
                confirmed_main_page_candidate = first_best_candidate

            should_detect_remaining_pages = False
            if confirmed_main_page_candidate is None and not included_template_main_page_only and isinstance(first_best_candidate, dict):
                first_metadata = first_best_candidate.get("metadata") if isinstance(first_best_candidate.get("metadata"), dict) else {}
                first_detection_mode = str(first_best_candidate.get("detection_mode") or first_metadata.get("detection_mode") or "all_pages")
                first_template_page_count = int(first_best_candidate.get("template_page_count") or first_metadata.get("page_count") or 0)
                should_detect_remaining_pages = (
                    first_detection_mode != "main_page"
                    and first_template_page_count == query_page_count
                    and query_page_count > 1
                )
            timing["remaining_pages_decision"] = time.perf_counter() - step_started

            if should_detect_remaining_pages:
                step_started = time.perf_counter()
                for page in normalized_pages[1:]:
                    detected_page = _detect_page(
                        page,
                        page_image_paths,
                        include_template_id=include_template_id,
                        timing=timing,
                        retrieval_limit=retrieval_limit,
                        verification_strategy=verification_strategy,
                        verification_candidate_limit=verification_candidate_limit,
                        full_evaluation_limit_override=full_evaluation_limit_override,
                        query_page_count=query_page_count,
                        request_cache=request_cache,
                    )
                    pages.append(detected_page)
                timing["remaining_pages_detection_total"] = time.perf_counter() - step_started
        timing["detect_pages_total"] = time.perf_counter() - detect_pages_started
        step_started = time.perf_counter()
        if prepublish_timing:
            print(f"[PREPUBLISH] layout analysis done: {timing.get('layout_analysis', 0.0):.2f}s")
            print(f"[PREPUBLISH] template matching done: {timing.get('template_matching', 0.0):.2f}s")
            print(f"[PREPUBLISH] verification done: {timing.get('verification', 0.0):.2f}s")
        timing["prepublish_logging"] = time.perf_counter() - step_started
        step_started = time.perf_counter()
        candidates = _aggregate_candidates(pages)
        timing["candidate_aggregation"] = time.perf_counter() - step_started
        if prepublish_timing:
            print(f"[PREPUBLISH] candidate aggregation done: {timing['candidate_aggregation']:.2f}s")
        step_started = time.perf_counter()
        request_auto_roi_pages = _build_request_auto_roi_pages(normalized_pages)
        timing["request_auto_roi"] = time.perf_counter() - step_started
        step_started = time.perf_counter()
        _attach_main_page_auto_roi_pages(candidates, normalized_pages, _auto_roi_pages_by_index(request_auto_roi_pages))
        timing["auto_roi"] = time.perf_counter() - step_started
        if prepublish_timing:
            print(f"[PREPUBLISH] auto roi done: {timing['auto_roi']:.2f}s")
        step_started = time.perf_counter()
        passing_candidates = sorted(
            [candidate for candidate in candidates if candidate["final_passed"]],
            key=lambda item: (item["final_score"], item["retrieval_score"]),
            reverse=True,
        )
        best_candidate = passing_candidates[0] if passing_candidates else None
        matched = best_candidate is not None
        timing["final_selection"] = time.perf_counter() - step_started
        step_started = time.perf_counter()
        processing_pages = _persist_selected_processing_pages(query_id, pages, best_candidate, normalized_pages)
        if best_candidate is not None:
            best_candidate["processing_pages"] = processing_pages
            if processing_pages:
                best_page_number = int(best_candidate.get("query_page_index") or 0)
                best_page_metadata = next(
                    (item for item in processing_pages if int(item.get("pageNumber") or 0) == best_page_number),
                    processing_pages[0],
                )
                best_candidate["source_image_query_reference"] = best_page_metadata.get("sourceImageReference")
                best_candidate["processing_image_query_reference"] = best_page_metadata.get("processingImageReference")
                best_candidate["processing_page_required"] = bool(best_page_metadata.get("processingImageReference"))
                best_candidate["processing_log_roi_coordinate_space"] = best_page_metadata.get("roiCoordinateSpace")
        timing["processing_page_persist"] = time.perf_counter() - step_started
        step_started = time.perf_counter()
        if best_candidate:
            for page in pages:
                page_candidate = next(
                    (
                        candidate
                        for candidate in page.get("candidates", [])
                        if candidate.get("template_id") == best_candidate.get("template_id")
                    ),
                    None,
                )
                if page_candidate is not None:
                    page_candidate["main_page_auto_roi_pages"] = best_candidate.get("main_page_auto_roi_pages", [])
                    page_candidate["main_page_auto_roi_total_pages"] = best_candidate.get("main_page_auto_roi_total_pages", 0)
                    page_candidate["main_page_auto_roi_total_regions"] = best_candidate.get("main_page_auto_roi_total_regions", 0)
                    if (page.get("best_candidate") or {}).get("template_id") == best_candidate.get("template_id"):
                        page["best_candidate"] = page_candidate
        timing["best_candidate_patch"] = time.perf_counter() - step_started

        return {
            "query_id": query_id,
            "engine": _detection_engine(pages),
            "version": DETECTION_VERSION,
            "threshold": DETECTION_THRESHOLD,
            "matched": matched,
            "best_candidate": best_candidate,
            "candidates": candidates,
            "pages": pages,
            "main_page_auto_roi_pages": request_auto_roi_pages,
            "main_page_auto_roi_total_pages": len(request_auto_roi_pages),
            "main_page_auto_roi_total_regions": sum(len(page.get("regions") or []) for page in request_auto_roi_pages),
            "message": None if matched else _no_match_message(candidates),
            "debug": {
                "pipeline_core": PIPELINE_CONFIG.to_debug_dict(),
                "retrieval_engine": "layout_signature",
                "image_verification_engine": "siglip_image_category",
                "source_type": source_type,
                "normalization_skipped": skip_normalization,
                "input_page_count": len(page_paths),
                "converted_page_count": len(page_paths) if source_type == "pdf" else 0,
                "query_page_paths": [str(path) for path in page_paths] if SAVE_DEBUG_ARTIFACTS else [],
                "normalized_query_page_paths": [page["normalized_path"] for page in normalized_pages] if SAVE_DEBUG_ARTIFACTS else [],
                "matching_query_page_paths": [page.get("matching_path") for page in normalized_pages] if SAVE_DEBUG_ARTIFACTS else [],
                "include_template_id": include_template_id,
                "verification_strategy": verification_strategy,
                "retrieval_limit": retrieval_limit,
                "verification_candidate_limit": verification_candidate_limit,
                "full_evaluation_limit_override": full_evaluation_limit_override,
                "timing": _detection_timing_debug(timing, pages, total_started, request_cache=request_cache),
            },
        }
    finally:
        if cleanup_generated and not SAVE_DEBUG_ARTIFACTS:
            _cleanup_transient_query_artifacts(query_id)
