import io
import os
import base64
import json
import cv2
import logging
import shutil
import time
import math
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context

from fastapi import HTTPException

from app.core.db import connect as connect_db
from app.processing.image_normalization import ImageNormalizationService
from app.processing.detection_requirements import verification_anchor_types
from app.processing.detection_work_policy import detection_work_scope, include_detection_diagnostics, compact_detection_candidates
from app.model_runtime.layout_analysis_service import analyze_layout
from app.processing import published_template_cache
from app.processing.ocr_adapter import OcrUnavailableError, ocr_rois
from app.core.pipeline_core import get_pipeline_core_config
from app.business.services import (
    DEFAULT_FINAL_CONFIDENCE_THRESHOLD,
    GlobalSettingsService,
    VerificationService,
    VERIFICATION_STRATEGY_STANDARD,
    VERIFICATION_STRATEGY_STRICT,
    DETECTION_BATCH_MODE_GLOBAL,
    normalize_verification_strategy,
)


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
PIPELINE_CONFIG = get_pipeline_core_config()
DETECTION_VERSION = PIPELINE_CONFIG.version
PDF_RENDER_SCALE = 2.0
SAVE_DEBUG_ARTIFACTS = os.getenv("SAVE_DEBUG_ARTIFACTS", "false").strip().lower() in {"1", "true", "yes", "on"}
verification_service = VerificationService()
global_settings_service = GlobalSettingsService()
normalization_service = ImageNormalizationService()


def _connect() -> Any:
    return connect_db()


class DetectionRequestCache:
    def __init__(self) -> None:
        self.templates: Dict[str, Optional[Dict[str, Any]]] = {}
        self.field_counts: Dict[str, Optional[int]] = {}
        self.verification_fields: Dict[Any, List[Dict[str, Any]]] = {}
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


def _runtime_ms(value: Any) -> Optional[float]:
    """Keep model-runtime timing in milliseconds; do not apply seconds conversion."""
    if not isinstance(value, (int, float)):
        return None
    return round(float(value), 2)


def _timing_ms_map(timing: Optional[Dict[str, Any]]) -> Dict[str, Optional[float]]:
    if not isinstance(timing, dict):
        return {}
    result: Dict[str, Any] = {}
    for key, value in timing.items():
        if isinstance(value, (int, float)):
            if key.endswith(("_count", "_hits", "_misses", "_size")):
                result[key] = int(value)
            elif key.endswith(("_ms", "_monotonic")):
                result[key] = value
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


def _storage_path() -> Path:
    return Path(__file__).resolve().parents[2] / "storage" / "detection_queries"


def _load_pillow():
    try:
        from PIL import Image
    except ImportError:
        return None
    return Image


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


def _fetch_verification_fields_cached(template_id: str, request_cache: Optional[DetectionRequestCache], anchor_types: Optional[set[str]] = None) -> tuple[List[Dict[str, Any]], bool, Dict[str, Any]]:
    if anchor_types is not None and not anchor_types:
        return [], False, {}
    cache_key = template_id if anchor_types is None else (template_id, tuple(sorted(anchor_types)))
    if request_cache is not None and cache_key in request_cache.verification_fields:
        request_cache.stats["verification_fields_cache_hits"] += 1
        return request_cache.verification_fields[cache_key], True, {}
    cached_fields = published_template_cache.get_verification_fields(template_id, anchor_types=anchor_types)
    if cached_fields is not None:
        if request_cache is not None:
            request_cache.verification_fields[cache_key] = cached_fields
            request_cache.stats["published_verification_fields_cache_hits"] += 1
        return cached_fields, True, {}
    fields, db_timing = verification_service.load_verification_fields_with_db_timing(template_id, anchor_types=anchor_types)
    if request_cache is not None:
        request_cache.verification_fields[cache_key] = fields
        request_cache.stats["verification_fields_db_fetches"] += 1
    return fields, False, db_timing


def _image_to_data_url(path: Path, *, required: bool = False) -> Optional[str]:
    if not required and not include_detection_diagnostics():
        return None
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


def _image_aspect(dimensions: Optional[List[int]]) -> Optional[float]:
    if not dimensions or len(dimensions) < 2:
        return None
    width, height = dimensions[:2]
    if not width or not height:
        return None
    return round(float(width) / max(1.0, float(height)), 4)


def _copy_debug_image(source: Path, target: Path) -> Optional[str]:
    if not include_detection_diagnostics():
        return None
    try:
        if not source.exists():
            return None
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        return str(target)
    except Exception:
        return None


def _save_debug_crop(source: Path, crop_box: Any, target: Path) -> Optional[str]:
    if not include_detection_diagnostics():
        return None
    if not isinstance(crop_box, list) or len(crop_box) != 4:
        return None
    image = cv2.imread(str(source))
    if image is None:
        return None
    height, width = image.shape[:2]
    try:
        left = max(0, min(width, int(round(float(crop_box[0])))))
        top = max(0, min(height, int(round(float(crop_box[1])))))
        right = max(0, min(width, int(round(float(crop_box[2])))))
        bottom = max(0, min(height, int(round(float(crop_box[3])))))
    except (TypeError, ValueError):
        return None
    if right <= left or bottom <= top:
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    if cv2.imwrite(str(target), image[top:bottom, left:right]):
        return str(target)
    return None


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
    if not include_detection_diagnostics():
        return None
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


def _save_query_image(query_id: str, image_bytes: bytes, page_index: int = 1, *, confirmed: bool = False) -> Path:
    Image = _load_pillow()
    if Image is None:
        raise HTTPException(status_code=400, detail="Image validation is unavailable because Pillow is not installed")
    if not image_bytes:
        raise HTTPException(status_code=400, detail="Uploaded image is empty")

    try:
        image = Image.open(io.BytesIO(image_bytes))
        if confirmed:
            # Match the browser preview's EXIF orientation and white backdrop.
            from PIL import ImageOps
            image = ImageOps.exif_transpose(image)
            if "A" in image.getbands() or "transparency" in image.info:
                rgba = image.convert("RGBA")
                image = Image.alpha_composite(Image.new("RGBA", rgba.size, "white"), rgba).convert("RGB")
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


def _prepare_query_pages(query_id: str, file_bytes: bytes | List[bytes], timing: Optional[Dict[str, float]] = None) -> List[Path]:
    step_started = time.perf_counter()
    if isinstance(file_bytes, list):
        if not file_bytes:
            raise HTTPException(status_code=400, detail="No confirmed pages")
        pages = [_save_query_image(query_id, page, index, confirmed=True) for index, page in enumerate(file_bytes, start=1)]
        if timing is not None:
            timing["prepare_image_save"] = timing.get("prepare_image_save", 0.0) + (time.perf_counter() - step_started)
        return pages
    if file_bytes.lstrip().startswith(b"%PDF"):
        pages = _convert_pdf_to_page_images(query_id, file_bytes)
        if timing is not None:
            timing["prepare_pdf_convert"] = timing.get("prepare_pdf_convert", 0.0) + (time.perf_counter() - step_started)
        return pages
    pages = [_save_query_image(query_id, file_bytes, 1)]
    if timing is not None:
        timing["prepare_image_save"] = timing.get("prepare_image_save", 0.0) + (time.perf_counter() - step_started)
    return pages


def _cleanup_query_artifacts(query_id: str) -> None:
    query_dir = _storage_path() / query_id
    try:
        root = _storage_path().resolve()
        resolved = query_dir.resolve()
        resolved.relative_to(root)
    except (ValueError, OSError):
        return
    if resolved == root or not resolved.exists() or not resolved.is_dir():
        return
    shutil.rmtree(resolved, ignore_errors=True)


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
    source_type: str = "image",
    timing: Optional[Dict[str, float]] = None,
    disable_pdf_subdocument_crop: bool = False,
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
                        "reason": "query_page_used_without_image_normalization",
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
        if source_type == "pdf":
            info = {
                "normalized_image_path": str(page_path),
                "document_detected": False,
                "crop_applied": False,
                "perspective_applied": False,
                "normalization_status": "skipped",
                "validation_passed": True,
                "fallback_used": False,
                "fallback_reason": "pdf_rendered_page_used_for_card_crop_detection",
                "original_size": _image_dimensions(str(page_path)),
                "normalized_size": _image_dimensions(str(page_path)),
                "output_size": _image_dimensions(str(page_path)),
                "resize_policy": "none",
                "normalization_debug": {
                    "normalization_status": "skipped",
                    "reason": "pdf_rendered_page_used_for_card_crop_detection",
                    "transform_validation": {},
                },
            }
        else:
            info = normalization_service.normalize_document(
                str(page_path),
                str(normalized_path),
                allow_subdocument_aspect_change=False,
            )
        if timing is not None:
            timing["prepare_normalization"] = timing.get("prepare_normalization", 0.0) + (time.perf_counter() - step_started)
        matching_path = info["normalized_image_path"]
        matching_reason = "template_matching_uses_normalized_image"
        matching_path_source = "normalized"
        matching_dimensions = _image_dimensions(matching_path)
        pdf_document_mode = None
        pdf_crop_area_ratio = None
        pdf_debug: Dict[str, Any] = {}
        matching_layout_signature = None
        if source_type == "pdf":
            debug_dir = (_storage_path() / query_id / "pdf_normalization_debug")
            normalization_debug = info.get("normalization_debug") if isinstance(info.get("normalization_debug"), dict) else {}
            transform_validation = normalization_debug.get("transform_validation") if isinstance(normalization_debug.get("transform_validation"), dict) else {}
            try:
                pdf_crop_area_ratio = float(transform_validation.get("area_ratio"))
            except (TypeError, ValueError):
                pdf_crop_area_ratio = None
            if disable_pdf_subdocument_crop or len(page_paths) > 1:
                pdf_layout_assisted_crop = {
                    "applied": False,
                    "crop_accepted": False,
                    "reason": ("multipage_pdf" if len(page_paths) > 1 else "pdf_subdocument_crop_disabled_for_prepublish"),
                    "rejection_reason": ("multipage_pdf" if len(page_paths) > 1 else "pdf_subdocument_crop_disabled_for_prepublish"),
                }
                pdf_boundary = {
                    "attempted": False,
                    "passed": False,
                    "reason": ("multipage_pdf" if len(page_paths) > 1 else "pdf_subdocument_crop_disabled_for_prepublish"),
                }
                if timing is not None:
                    timing.setdefault("physical_crop_attempts", []).append(
                        {
                            "page_index": index,
                            "attempt": 1,
                            "elapsed_ms": None,
                            "detector_timing": {},
                            "crop_applied": False,
                            "crop_rejected": True,
                            "reason": ("multipage_pdf" if len(page_paths) > 1 else "pdf_subdocument_crop_disabled_for_prepublish"),
                            "confidence": None,
                            "area_ratio": None,
                            "aspect_ratio": None,
                            "candidate_source": None,
                        }
                    )
            else:
                pdf_layout_assisted_crop = {
                    "applied": False,
                    "crop_accepted": False,
                    "reason": "layout_assisted_crop_disabled_pdf_uses_physical_card_crop",
                    "rejection_reason": "layout_assisted_crop_disabled_pdf_uses_physical_card_crop",
                }
                pdf_boundary_path = normalized_dir / f"page_{index}_pdf_subdocument.png"
                physical_crop_started = time.perf_counter()
                pdf_boundary = normalization_service.detect_pdf_subdocument_boundary(str(page_path), str(pdf_boundary_path), str(debug_dir) if include_detection_diagnostics() else None)
                physical_crop_elapsed = time.perf_counter() - physical_crop_started
                if timing is not None:
                    timing["physical_crop_total"] = timing.get("physical_crop_total", 0.0) + physical_crop_elapsed
                    boundary_timing = pdf_boundary.get("timing") if isinstance(pdf_boundary, dict) else {}
                    if isinstance(boundary_timing, dict):
                        for source_key, target_key in (
                            ("detection_ms", "physical_crop_detection"),
                            ("validation_ms", "physical_crop_validation"),
                            ("apply_ms", "physical_crop_apply"),
                        ):
                            value = boundary_timing.get(source_key)
                            if isinstance(value, (int, float)):
                                timing[target_key] = timing.get(target_key, 0.0) + (float(value) / 1000.0)
                    timing.setdefault("physical_crop_attempts", []).append(
                        {
                            "page_index": index,
                            "attempt": 1,
                            "elapsed_ms": _ms(physical_crop_elapsed),
                            "detector_timing": boundary_timing if isinstance(boundary_timing, dict) else {},
                            "crop_applied": bool(pdf_boundary.get("passed")) and bool(pdf_boundary.get("output_path")),
                            "crop_rejected": not (bool(pdf_boundary.get("passed")) and bool(pdf_boundary.get("output_path"))),
                            "reason": pdf_boundary.get("reason"),
                            "confidence": pdf_boundary.get("confidence"),
                            "area_ratio": pdf_boundary.get("area_ratio"),
                            "aspect_ratio": pdf_boundary.get("aspect_ratio"),
                            "candidate_source": pdf_boundary.get("candidate_source"),
                        }
                    )
            physical_sub_document = bool(pdf_boundary.get("passed")) and bool(pdf_boundary.get("output_path"))
            crop_rejection_reason = None if physical_sub_document else (pdf_boundary.get("reason") or "physical_card_crop_not_detected")
            matching_layout_signature = None
            document_boundary_source = "physical_boundary" if physical_sub_document else "full_page"
            if physical_sub_document:
                pdf_document_mode = "sub_document"
                matching_path = str(pdf_boundary["output_path"])
                matching_path_source = "pdf_physical_subdocument"
                matching_reason = "pdf_physical_subdocument_crop_used_for_template_matching_and_processing"
            else:
                pdf_document_mode = "full_page"
                matching_path = str(page_path)
                matching_path_source = "rendered_pdf_page"
                matching_reason = "pdf_full_page_render_used_for_template_matching"
            matching_dimensions = _image_dimensions(matching_path)
            original_dimensions = _image_dimensions(str(page_path))
            original_preview = _copy_debug_image(page_path, debug_dir / f"page_{index}_original_rendered.png")
            pdf_boundary_crop_box = pdf_boundary.get("crop_box") if isinstance(pdf_boundary, dict) else None
            crop_preview = None if disable_pdf_subdocument_crop else _save_debug_crop(page_path, pdf_boundary_crop_box, debug_dir / f"page_{index}_detected_crop_before_validation.png")
            boundary_preview = _copy_debug_image(Path(str(pdf_boundary.get("output_path"))), debug_dir / f"page_{index}_physical_boundary_crop.png") if pdf_boundary.get("output_path") else None
            matching_preview = _copy_debug_image(Path(matching_path), debug_dir / f"page_{index}_final_matching.png")
            pdf_debug = {
                "source_type": source_type,
                "pdf_subdocument_crop_disabled": bool(disable_pdf_subdocument_crop or len(page_paths) > 1),
                "pdf_total_pages": len(page_paths),
                "original_image_size": original_dimensions,
                "original_aspect_ratio": _image_aspect(original_dimensions),
                "document_detection_method": "physical_card_crop",
                "detected_crop_box": pdf_boundary_crop_box,
                "crop_area_ratio": pdf_boundary.get("area_ratio"),
                "crop_accepted": bool(physical_sub_document),
                "rejection_reason": crop_rejection_reason,
                "layout_assisted_crop_enabled": False,
                "physical_subdocument_confirmed": bool(physical_sub_document),
                "physical_detector_confidence": pdf_boundary.get("confidence"),
                "physical_detector_aspect_ratio": pdf_boundary.get("aspect_ratio"),
                "physical_detector_area_ratio": pdf_boundary.get("area_ratio"),
                "physical_detector_edge_support": pdf_boundary.get("edge_support"),
                "physical_detector_strong_edge_side_count": pdf_boundary.get("strong_edge_side_count"),
                "physical_detector_rectangularity": pdf_boundary.get("rectangularity"),
                "physical_crop_timing": pdf_boundary.get("timing"),
                "crop_rejection_reason": crop_rejection_reason,
                "final_matching_path_source": matching_path_source,
                "normalization_crop_area_ratio": round(pdf_crop_area_ratio, 4) if pdf_crop_area_ratio is not None else None,
                "aspect_change_ratio": transform_validation.get("aspect_change_ratio"),
                "validation_passed": transform_validation.get("passed"),
                "validation_reason": transform_validation.get("reason"),
                "crop_applied": info.get("crop_applied"),
                "document_boundary_source": document_boundary_source,
                "layout_assisted_crop": pdf_layout_assisted_crop,
                "layout_boundary_box": None,
                "layout_boundary_area_ratio": None,
                "layout_boundary_width_ratio": None,
                "layout_boundary_height_ratio": None,
                "layout_boundary_region_count": None,
                "layout_crop_applied": False,
                "layout_crop_area_ratio": None,
                "layout_crop_accepted": False,
                "layout_crop_rejection_reason": pdf_layout_assisted_crop.get("rejection_reason"),
                "pdf_subdocument_detector": pdf_boundary,
                "pdf_subdocument_detector_attempted": not disable_pdf_subdocument_crop,
                "pdf_subdocument_detector_passed": bool(pdf_boundary.get("passed")),
                "pdf_subdocument_detector_reason": pdf_boundary.get("reason"),
                "pdf_subdocument_detector_allowed_by_layout": True,
                "pdf_subdocument_detector_blocked_by_layout_reason": None,
                "layout_policy_would_block_physical_detector": False,
                "layout_policy_block_reason": None,
                "pdf_subdocument_crop_box": pdf_boundary.get("crop_box"),
                "pdf_subdocument_confidence": pdf_boundary.get("confidence"),
                "pdf_subdocument_edge_support": pdf_boundary.get("edge_support"),
                "pdf_subdocument_rectangularity": pdf_boundary.get("rectangularity"),
                "aspect_validation_bypass_called": bool(transform_validation.get("aspect_change_validation_bypassed")),
                "aspect_validation_bypass_reason": transform_validation.get("aspect_change_validation_bypass_reason"),
                "pdf_document_mode": pdf_document_mode,
                "matching_path_source": matching_path_source,
                "matching_image_path": matching_path,
                "matching_image_size": matching_dimensions,
                "matching_aspect_ratio": _image_aspect(matching_dimensions),
                "pre_match_image_path": matching_path,
                "matching_layout_signature_source": None,
                "matching_signature_source": "matching_image_path_layout_analysis",
                "matching_layout_signature_created": bool(matching_layout_signature),
                "preview_original_rendered_path": original_preview,
                "preview_layout_boundary_path": None,
                "preview_layout_crop_before_template_matching_path": None,
                "preview_detected_crop_before_validation_path": crop_preview,
                "preview_physical_boundary_crop_path": boundary_preview,
                "preview_final_matching_path": matching_preview,
                "normalization_debug": normalization_debug,
            }
        effective_normalized_path = matching_path if source_type == "pdf" else info["normalized_image_path"]
        effective_normalization = dict(info)
        if source_type == "pdf":
            effective_normalization.update(
                {
                    "normalized_image_path": effective_normalized_path,
                    "pdf_matching_path_promoted_to_normalized": not disable_pdf_subdocument_crop and len(page_paths) == 1,
                    "pdf_matching_path_source": matching_path_source,
                    "pdf_matching_reason": matching_reason,
                    "pdf_matching_crop_box": pdf_boundary_crop_box,
                    "pdf_subdocument_crop_disabled": bool(disable_pdf_subdocument_crop or len(page_paths) > 1),
                "pdf_total_pages": len(page_paths),
                }
            )
        normalized_pages.append(
            {
                "page_index": index,
                "original_path": str(page_path),
                "normalized_path": effective_normalized_path,
                "matching_path": matching_path,
                "normalization": effective_normalization,
                "matching_normalization": {
                    "normalization_status": effective_normalization.get("normalization_status"),
                    "reason": matching_reason,
                    "matching_image_path": matching_path,
                    "matching_image_size": matching_dimensions,
                    "matching_aspect_ratio": _image_aspect(matching_dimensions),
                    "matching_path_source": matching_path_source,
                    "matching_layout_signature": matching_layout_signature,
                    "pdf_document_mode": pdf_document_mode,
                    "pdf_crop_area_ratio": round(pdf_crop_area_ratio, 4) if pdf_crop_area_ratio is not None else None,
                    "pdf_runtime_debug": pdf_debug,
                    "crop_applied": effective_normalization.get("crop_applied"),
                    "perspective_applied": effective_normalization.get("perspective_applied"),
                    "fallback_used": effective_normalization.get("fallback_used"),
                },
            }
        )
    return normalized_pages


def _auto_roi_region_items(page_info: Dict[str, Any]) -> Dict[str, Any]:
    page_index = int(page_info["page_index"])
    image_path = str(page_info.get("original_path") or page_info["normalized_path"])
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
        "image_source": "original_path" if page_info.get("original_path") else "normalized_path",
        "image_path": image_path,
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


def _load_anchor_strategy_templates(anchor_types: Optional[set[str]] = None, request_cache: Optional[DetectionRequestCache] = None, *, force_refresh: bool = False) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    anchor_types = {"text", "image"} if anchor_types is None else set(anchor_types)
    if not anchor_types:
        return [], {"source": "not_required", "anchor_types": [], "anchor_count": 0, "template_count": 0}
    total_started = time.perf_counter()
    cache_lookup_started = time.perf_counter()
    cached_templates = None if force_refresh else published_template_cache.get_verification_anchor_templates(anchor_types)
    cache_lookup_elapsed = time.perf_counter() - cache_lookup_started
    if cached_templates is not None:
        anchor_processing_started = time.perf_counter()
        templates = list(cached_templates)
        template_count = len(templates)
        anchor_count = sum(len(template.get("verification_anchors") or template.get("text_anchors") or []) for template in templates)
        anchor_processing_elapsed = time.perf_counter() - anchor_processing_started
        if request_cache is not None:
            request_cache.stats["published_template_cache_hits"] += template_count
            request_cache.stats["published_verification_fields_cache_hits"] += template_count
        return templates, {
            "source": "published_template_cache",
            "cache_hit": True,
            "anchor_types": sorted(anchor_types),
            "status_filter": ["active", "published"],
            "cache_lookup_ms": _ms(cache_lookup_elapsed),
            "db_connect_ms": None,
            "db_query_ms": None,
            "cursor_create_ms": None,
            "execute_ms": None,
            "fetch_ms": None,
            "anchor_processing_ms": _ms(anchor_processing_elapsed),
            "total_ms": _ms(time.perf_counter() - total_started),
            "template_count": template_count,
            "anchor_count": anchor_count,
            "duplicate_template_load_count": 0,
            "db_fallback": False,
        }
    connect_started = time.perf_counter()
    conn = _connect()
    connect_elapsed = time.perf_counter() - connect_started
    with conn:
        query_started = time.perf_counter()
        anchor_params = tuple(sorted(anchor_types))
        anchor_placeholders = ",".join("?" for _ in anchor_params)
        category_join = "LEFT JOIN image_verification_categories ivc ON ivc.id = va.image_category_id" if "image" in anchor_types else ""
        category_column = "COALESCE(ivc.value, va.image_category_id)" if "image" in anchor_types else "NULL"
        cursor, execute_timing = conn.execute_timed(
            f"""
            SELECT
                tv.id AS template_id,
                tg.name AS template_name,
                tg.document_type,
                tg.category,
                tv.status AS template_status,
                tv.version_name,
                tv.version_number,
                tv.detection_mode,
                tv.main_page_number,
                tv.final_confidence_threshold,
                tv.layout_weight,
                tv.text_anchor_weight,
                tv.image_anchor_weight,
                (
                    SELECT COUNT(*)
                    FROM template_pages tp_count
                    WHERE tp_count.template_version_id = tv.id
                ) AS page_count,
                (
                    SELECT COUNT(*)
                    FROM extraction_fields ef
                    JOIN template_pages tp_fields ON tp_fields.id = ef.template_page_id
                    WHERE tp_fields.template_version_id = tv.id
                ) AS field_count,
                va.id AS anchor_id,
                va.anchor_type AS data_type,
                {category_column} AS image_category,
                va.template_page_id,
                tp.page_number,
                va.anchor_name AS field_name,
                va.anchor_name AS display_label,
                va.expected_text,
                va.match_type,
                va.required AS required_for_verification,
                va.weight AS verification_weight,
                va.roi_x_ratio,
                va.roi_y_ratio,
                va.roi_width_ratio,
                va.roi_height_ratio,
                va.roi_points_json,
                va.sort_order,
                va.created_at
            FROM template_versions tv
            JOIN template_groups tg ON tg.id = tv.template_group_id
            LEFT JOIN template_pages tp ON tp.template_version_id = tv.id
            LEFT JOIN verification_anchors va ON va.template_page_id = tp.id AND va.anchor_type IN ({anchor_placeholders})
            {category_join}
            WHERE tv.status IN ('active', 'published')
            ORDER BY tv.updated_at DESC, tv.id ASC, tp.page_number ASC, va.sort_order ASC, va.created_at ASC
            """,
            anchor_params,
        )
        fetch_started = time.perf_counter()
        rows = cursor.fetchall()
        fetch_elapsed = time.perf_counter() - fetch_started
        db_query_elapsed = time.perf_counter() - query_started

    anchor_processing_started = time.perf_counter()
    templates_by_id: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        template_id = row["template_id"]
        if template_id not in templates_by_id:
            templates_by_id[template_id] = {
                "template_id": template_id,
                "template_name": row["template_name"],
                "document_type": row["document_type"],
                "category": row["category"],
                "template_status": row["template_status"],
                "version_name": row["version_name"],
                "version_number": row["version_number"],
                "detection_mode": row["detection_mode"],
                "main_page_number": row["main_page_number"],
                "final_confidence_threshold": row["final_confidence_threshold"],
                "layout_weight": row["layout_weight"],
                "text_anchor_weight": row["text_anchor_weight"],
                "image_anchor_weight": row["image_anchor_weight"],
                "page_count": row["page_count"],
                "field_count": row["field_count"],
                "text_anchors": [],
                "verification_anchors": [],
            }
        if row["anchor_id"] is None:
            continue
        roi = {
            "page_number": row["page_number"],
            "x_ratio": row["roi_x_ratio"],
            "y_ratio": row["roi_y_ratio"],
            "width_ratio": row["roi_width_ratio"],
            "height_ratio": row["roi_height_ratio"],
        }
        raw_points = row["roi_points_json"]
        if raw_points:
            try:
                points = json.loads(raw_points) if isinstance(raw_points, str) else raw_points
                if isinstance(points, list) and len(points) > 2:
                    roi["points"] = points
            except Exception:
                pass
        templates_by_id[template_id]["verification_anchors"].append(
            {
                "id": row["anchor_id"],
                "template_id": template_id,
                "template_page_id": row["template_page_id"],
                "page_number": row["page_number"],
                "field_name": row["field_name"],
                "display_label": row["display_label"],
                "roi": roi,
                "data_type": row["data_type"],
                "image_category": row["image_category"],
                "expected_text": row["expected_text"],
                "match_type": row["match_type"],
                "required_for_verification": bool(row["required_for_verification"]),
                "verification_weight": row["verification_weight"],
            }
        )
    for template in templates_by_id.values():
        template["text_anchors"] = [field for field in template["verification_anchors"]
                                    if field["data_type"] == "text" and field.get("expected_text")]
    anchor_processing_elapsed = time.perf_counter() - anchor_processing_started
    anchor_count = sum(len(template.get("verification_anchors") or template.get("text_anchors") or []) for template in templates_by_id.values())
    if request_cache is not None:
        request_cache.stats["verification_fields_db_fetches"] += len(templates_by_id)

    return list(templates_by_id.values()), {
        "source": "database",
        "anchor_types": sorted(anchor_types),
        "cache_hit": False,
        "status_filter": ["active", "published"],
        "cache_lookup_ms": _ms(cache_lookup_elapsed),
        "db_connect_ms": _ms(connect_elapsed),
        "db_query_ms": _ms(db_query_elapsed),
        "execute_ms": _ms(float(execute_timing.get("execute") or 0.0)),
        "cursor_create_ms": _ms(float(execute_timing.get("cursor_create") or 0.0)),
        "fetch_ms": _ms(fetch_elapsed),
        "anchor_processing_ms": _ms(anchor_processing_elapsed),
        "total_ms": _ms(time.perf_counter() - total_started),
        "template_count": len(templates_by_id),
        "anchor_count": anchor_count,
        "duplicate_template_load_count": max(0, len(rows) - len(templates_by_id)),
        "db_fallback": True,
    }


def _candidate_from_anchor_strategy(
    template: Dict[str, Any],
    verification: Dict[str, Any],
    fields: List[Dict[str, Any]],
    page_image_paths: Dict[int, str],
    strategy: str,
    timing: Dict[str, Any],
) -> Dict[str, Any]:
    template_id = str(template.get("template_id"))
    checked_fields = verification.get("checked_fields", [])
    threshold = template.get("final_confidence_threshold")
    final_threshold = float(threshold if threshold is not None else DEFAULT_FINAL_CONFIDENCE_THRESHOLD)
    verification_score = float(verification.get("score", 0.0) or 0.0)
    final_passed = bool(verification.get("passed")) and bool(fields) and verification_score >= final_threshold
    text_score = verification.get("text_anchor_score")
    image_score = 0.0 if verification.get("image_verification_skipped") else verification.get("image_anchor_score")
    main_page = int(template.get("main_page_number") or 1) if template.get("detection_mode") == "main_page" else 1
    image_count = sum(field.get("data_type") == "image" for field in fields)
    text_count = sum(field.get("data_type") == "text" for field in fields)
    text_weight = max(0.0, float(template.get("text_anchor_weight") or 0.0)) if text_count else 0.0
    image_weight = max(0.0, float(template.get("image_anchor_weight") or 0.0)) if image_count else 0.0
    if text_weight + image_weight <= 0:
        text_weight, image_weight = float(bool(text_count)), float(bool(image_count))
    total_weight = text_weight + image_weight or 1.0
    weights = {"layout": 0.0, "text_anchor": text_weight / total_weight,
               "image_anchor": image_weight / total_weight}
    verification_score = (float(text_score or 0.0) * weights["text_anchor"]
                          + float(image_score or 0.0) * weights["image_anchor"])
    final_passed = bool(verification.get("passed")) and bool(fields) and verification_score >= final_threshold
    return {
        "template_id": template_id,
        "vector_id": f"{strategy}:{template_id}",
        "score": round(verification_score, 4),
        "retrieval_score": 0.0,
        "layout_score": 0.0,
        "layout_debug": {"skipped": True, "reason": f"{strategy}_strategy"},
        "average_score": 0.0,
        "matched_pages": len({field.get("page_number") for field in checked_fields if isinstance(field, dict) and field.get("passed")}),
        "template_name": template.get("template_name"),
        "version_name": template.get("version_name"),
        "version_number": template.get("version_number"),
        "template_status": template.get("template_status"),
        "page_count": template.get("page_count"),
        "field_count": template.get("field_count"),
        "model_name": strategy,
        "vector_store_engine": strategy,
        "retrieval_engine": strategy,
        "query_page_index": main_page,
        "template_page_number": main_page,
        "alignment_status": "skipped",
        "alignment": {"alignment_status": "skipped", "alignment_reason": f"{strategy}_strategy"},
        "alignment_debug": {"alignment_status": "skipped", "reason": f"{strategy}_strategy"},
        "alignment_score": 0.0,
        "alignment_passed": False,
        "alignment_fallback_used": True,
        "alignment_reason": f"{strategy}_strategy",
        "normalized_verification_score": round(verification_score, 4),
        "aligned_verification_score": None,
        "verification_source_used": f"{strategy}_original_roi",
        "before_alignment_verification": round(verification_score, 4),
        "after_alignment_verification": None,
        "verification_improvement": None,
        "normalized_image_path": page_image_paths.get(main_page),
        "normalized_image_preview_url": _detection_preview_url(page_image_paths.get(main_page)) if page_image_paths.get(main_page) else None,
        "extraction_image_path": page_image_paths.get(main_page),
        "extraction_image_preview_url": _detection_preview_url(page_image_paths.get(main_page)) if page_image_paths.get(main_page) else None,
        "selected_processing_source": f"{strategy}_original_roi",
        "selected_processing_path": page_image_paths.get(main_page),
        "roi_coordinate_space": "normalized",
        "verification": verification,
        "verification_details": checked_fields,
        "verification_score": round(verification_score, 4),
        "text_anchor_score": text_score,
        "image_anchor_score": image_score,
        "anchor_score": round(verification_score, 4),
        "matching_weights": weights,
        "effective_matching_weights": weights,
        "verification_passed": bool(verification.get("passed")),
        "final_score": round(verification_score, 4),
        "final_passed": final_passed,
        "decision_reason": f"{strategy}_passed" if final_passed else f"{strategy}_anchors_failed",
        "decision_path": f"{strategy}_passed" if final_passed else f"{strategy}_anchors_failed",
        "required_passed": verification.get("required_passed"),
        "required_failed_fields": [field for field in checked_fields if isinstance(field, dict) and field.get("required") and not field.get("passed")],
        "final_confidence_threshold": final_threshold,
        "layout_similarity_threshold": None,
        "template_rois": [],
        "projection": {"status": "skipped", "reason": f"{strategy}_strategy"},
        "projected_fields": [],
        "extraction_test": None,
        "coordinate_debug": None,
        "metadata": {
            "template_id": template_id,
            "template_name": template.get("template_name"),
            "document_type": template.get("document_type"),
            "category": template.get("category"),
            "page_count": template.get("page_count"),
            "detection_mode": template.get("detection_mode"),
        },
        "verification_strategy": strategy,
        "evaluation_status": "full",
        "alignment_evaluated": False,
        "candidate_rejection_reason": None if final_passed else f"{strategy}_anchors_failed",
        "timing": {
            "text_verification_ms": _ms((verification.get("timing") or {}).get("text_verification")),
            "image_verification_ms": _ms((verification.get("timing") or {}).get("image_verification")),
            "text_ocr_inference_ms": _ms((verification.get("timing") or {}).get("text_ocr_inference")),
            "image_model_inference_ms": _ms((verification.get("timing") or {}).get("image_model_inference")),
            "text_det_inference_ms": _runtime_ms((verification.get("timing") or {}).get("text_det_inference_ms")),
            "text_rec_inference_ms": _runtime_ms((verification.get("timing") or {}).get("text_rec_inference_ms")),
            "table_runtime_ms": _runtime_ms((verification.get("timing") or {}).get("table_runtime_ms")),
            "model_calls": list((verification.get("timing") or {}).get("model_calls") or []),
            "siglip_preprocess_ms": _runtime_ms(((verification.get("timing") or {}).get("image_batch") or {}).get("siglip_preprocess_ms")),
            "siglip_inference_ms": _runtime_ms(((verification.get("timing") or {}).get("image_batch") or {}).get("siglip_inference_ms")),
            "siglip_postprocess_ms": _runtime_ms(((verification.get("timing") or {}).get("image_batch") or {}).get("siglip_postprocess_ms")),
            "image_batch_total_ms": _runtime_ms(((verification.get("timing") or {}).get("image_batch") or {}).get("image_batch_total_ms")),
            "siglip_batch_size": ((verification.get("timing") or {}).get("image_batch") or {}).get("siglip_batch_size"),
            "anchor_strategy_matching_ms": _ms(timing.get("anchor_strategy_matching")),
        },
    }


def _anchor_strategy_template_fields(template: Dict[str, Any], anchor_types: set[str]) -> List[Dict[str, Any]]:
    return [
        field for field in (template.get("verification_anchors") or [])
        if str(field.get("data_type") or "").lower() in anchor_types
        and (str(field.get("data_type") or "").lower() != "text" or field.get("expected_text"))
    ]


def _ordered_anchor_templates(
    templates: List[Dict[str, Any]],
    anchor_types: set[str],
) -> tuple[List[tuple[Dict[str, Any], List[Dict[str, Any]]]], List[Dict[str, Any]]]:
    ordered: List[tuple[Dict[str, Any], List[Dict[str, Any]]]] = []
    debug_order: List[Dict[str, Any]] = []
    for template in templates:
        fields = _anchor_strategy_template_fields(template, anchor_types)
        required_count = sum(1 for field in fields if bool(field.get("required_for_verification")))
        total_count = len(fields)
        template_id = str(template.get("template_id") or "")
        ordered.append((template, fields))
        debug_order.append({
            "template_id": template_id,
            "required_anchor_count": required_count,
            "anchor_count": total_count,
        })
    ordered.sort(key=lambda item: (
        sum(1 for field in item[1] if bool(field.get("required_for_verification"))),
        len(item[1]),
        str(item[0].get("template_id") or ""),
    ))
    debug_order.sort(key=lambda item: (
        int(item.get("required_anchor_count") or 0),
        int(item.get("anchor_count") or 0),
        str(item.get("template_id") or ""),
    ))
    return ordered, debug_order


def _run_global_text_anchor_batch(
    ordered_templates: List[tuple[Dict[str, Any], List[Dict[str, Any]]]],
    page_image_paths: Dict[int, str],
    timing: Dict[str, Any],
    timing_prefix: str,
) -> tuple[Dict[str, Dict[str, Any]], Dict[str, str], Dict[str, Any]]:
    batch_started = time.perf_counter()
    roi_items_by_page: Dict[int, List[Dict[str, Any]]] = {}
    text_anchor_count = 0
    for _, fields in ordered_templates:
        for field in fields:
            if str(field.get("data_type") or "").lower() != "text":
                continue
            page_number = int(field.get("page_number") or 1)
            if page_number not in page_image_paths:
                continue
            text_anchor_count += 1
            roi_items_by_page.setdefault(page_number, []).append({"id": field["id"], "roi": field["roi"]})

    ocr_results: Dict[str, Dict[str, Any]] = {}
    ocr_errors: Dict[str, str] = {}
    ocr_timing: Dict[str, Any] = {}
    model_request_count = 0
    for page_number, roi_items in roi_items_by_page.items():
        image_path = page_image_paths.get(page_number)
        if not image_path:
            continue
        model_request_count += 1
        try:
            page_results = ocr_rois(image_path, roi_items, timing=ocr_timing)
            ocr_results.update(page_results)
        except OcrUnavailableError as error:
            for item in roi_items:
                ocr_errors[str(item.get("id"))] = str(error)
        except Exception as error:
            for item in roi_items:
                ocr_errors[str(item.get("id"))] = f"ROI OCR failed: {error}"
    timing[f"{timing_prefix}_global_text_batch"] = time.perf_counter() - batch_started
    return ocr_results, ocr_errors, {
        "batch_mode": DETECTION_BATCH_MODE_GLOBAL,
        "text_roi_count": text_anchor_count,
        "text_batch_count": len([items for items in roi_items_by_page.values() if items]),
        "text_model_request_count": model_request_count,
        "text_det_inference_ms": _runtime_ms(ocr_timing.get("text_det_inference_ms")),
        "text_rec_inference_ms": _runtime_ms(ocr_timing.get("text_rec_inference_ms")),
        "table_runtime_ms": _runtime_ms(ocr_timing.get("table_runtime_ms")),
        "model_calls": list(ocr_timing.get("model_calls") or []),
        "model_call_intervals": list(ocr_timing.get("model_call_intervals") or []),
        "ocr_timing": _timing_ms_map(ocr_timing),
        "elapsed_ms": _ms(timing.get(f"{timing_prefix}_global_text_batch")),
    }


def _verify_sequential_anchor_template(
    template_id: str,
    fields: List[Dict[str, Any]],
    page_image_paths: Dict[int, str],
) -> Dict[str, Any]:
    """Strict: finish Text checks before starting any Image checks."""
    checked_fields: List[Dict[str, Any]] = []
    combined_timing: Dict[str, Any] = {}
    for anchor_type in ("text", "image"):
        stage_fields = [field for field in fields if field.get("data_type") == anchor_type]
        if not stage_fields:
            continue
        stage = verification_service.verify_template(
            template_id, page_image_paths, stage_fields,
            use_image_batch=anchor_type == "image",
        )
        stage_checked = stage.get("checked_fields") or []
        checked_fields.extend(stage_checked)
        for key, value in (stage.get("timing") or {}).items():
            if isinstance(value, (int, float)):
                combined_timing[key] = combined_timing.get(key, 0.0) + value
            elif isinstance(value, list):
                combined_timing.setdefault(key, []).extend(value)
            elif value is not None:
                combined_timing[key] = value
        stage_passed = bool(stage.get("passed")) and len(stage_checked) == len(stage_fields) and all(
            field.get("passed") for field in stage_checked
        )
        if not stage_passed:
            result = verification_service._verification_summary(template_id, checked_fields)
            result.update(passed=False, timing=combined_timing,
                          image_verification_skipped=anchor_type == "text",
                          failed_stage=anchor_type)
            return result
    result = verification_service._verification_summary(template_id, checked_fields)
    result.update(passed=bool(checked_fields) and all(field.get("passed") for field in checked_fields),
                  timing=combined_timing, image_verification_skipped=False)
    return result


def _run_parallel_anchor_batches(
    ordered_templates: List[tuple[Dict[str, Any], List[Dict[str, Any]]]],
    page_image_paths: Dict[int, str],
    timing: Dict[str, Any],
) -> tuple[Dict[str, Any], Dict[str, str], Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    """Standard: run Text OCR and Image inference concurrently for the full pool."""
    image_fields = [field for _, fields in ordered_templates for field in fields
                    if field.get("data_type") == "image"]

    def image_batch():
        started = time.perf_counter()
        result = verification_service.score_image_anchors_batch(image_fields, page_image_paths)
        timing["anchor_strategy_global_image_batch"] = time.perf_counter() - started
        return result

    with ThreadPoolExecutor(max_workers=2) as executor:
        text_future = executor.submit(copy_context().run, _run_global_text_anchor_batch,
                                      ordered_templates, page_image_paths, timing, "anchor_strategy")
        image_future = executor.submit(copy_context().run, image_batch)
        text_results, text_errors, text_debug = text_future.result()
        image_matches, image_debug = image_future.result()
    return text_results, text_errors, text_debug, image_matches, image_debug


def _detect_template_anchor_strategy(
    query_id: str,
    normalized_pages: List[Dict[str, Any]],
    timing: Dict[str, float],
    request_cache: DetectionRequestCache,
    total_started: float,
    source_type: str,
    strategy: str,
    detection_batch_mode: str,
    selected_template_id: Optional[str] = None,
    progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    user_context: bool = False,
    compare_active_templates: bool = False,
) -> Dict[str, Any]:
    started = time.perf_counter()
    strategy = normalize_verification_strategy(strategy)
    detection_batch_mode = "per_template" if strategy == VERIFICATION_STRATEGY_STRICT else DETECTION_BATCH_MODE_GLOBAL
    page_image_paths = {int(page["page_index"]): page["normalized_path"] for page in normalized_pages}
    anchor_types = set(verification_anchor_types(strategy))
    load_started = time.perf_counter()
    if selected_template_id:
        template, _, _ = _fetch_template_cached(selected_template_id, request_cache)
        if template is None:
            raise HTTPException(status_code=404, detail="Template not found")
        fields, _, _ = _fetch_verification_fields_cached(selected_template_id, request_cache, anchor_types)
        if template.get("detection_mode") == "main_page":
            main_page = int(template.get("main_page_number") or 1)
            fields = [field for field in fields if int(field.get("page_number") or 1) == main_page]
        templates = [{**template, "template_id": selected_template_id,
                      "template_name": template.get("name"), "template_status": template.get("status"),
                      "verification_anchors": fields}]
        load_debug = {"source": "selected_template", "template_count": 1}
        if compare_active_templates:
            # Prepublish compares against the current database inventory,
            # including activations made by another backend worker.
            active_templates, active_debug = _load_anchor_strategy_templates(anchor_types, request_cache, force_refresh=True)
            active_templates = [item for item in active_templates
                                if item.get("template_status") == "active"
                                and item.get("template_id") != selected_template_id]
            templates = [*active_templates, *templates]
            load_debug = {"source": "active_templates_and_selected_draft", "active_pool": active_debug,
                          "active_template_count": len(active_templates), "template_count": len(templates)}
    else:
        templates, load_debug = _load_anchor_strategy_templates(anchor_types, request_cache)
    timing["anchor_strategy_template_load"] = time.perf_counter() - load_started
    templates = [
        {**template, "verification_anchors": [
            field for field in template.get("verification_anchors") or []
            if template.get("detection_mode") != "main_page"
            or int(field.get("page_number") or 1) == int(template.get("main_page_number") or 1)
        ]} for template in templates
    ]
    ordered_templates, template_order_debug = _ordered_anchor_templates(templates, anchor_types)
    global_text_results = global_text_errors = global_text_debug = None
    global_image_matches = global_image_debug = None
    if strategy == VERIFICATION_STRATEGY_STANDARD:
        (global_text_results, global_text_errors, global_text_debug,
         global_image_matches, global_image_debug) = _run_parallel_anchor_batches(
            ordered_templates, page_image_paths, timing,
        )
        timing["anchor_strategy_global_text_batch_debug"] = global_text_debug
        timing["anchor_strategy_global_image_batch_debug"] = global_image_debug
    match_started = time.perf_counter()
    candidates: List[Dict[str, Any]] = []
    pages_by_index: Dict[int, Dict[str, Any]] = {
        int(page["page_index"]): {
            "page_index": int(page["page_index"]),
            "image_path": page["normalized_path"],
            "normalized_image_path": page["normalized_path"],
            "matching_image_path": page.get("matching_path") or page["normalized_path"],
            "candidates": [],
            "best_candidate": None,
            "debug": {"verification_strategy": strategy},
        }
        for page in normalized_pages
    }
    early_exit = False
    for template_index, (template, fields) in enumerate(ordered_templates, start=1):
        if progress_callback is not None:
            progress_callback({"stage": "candidate_evaluation", "current_rank": template_index,
                               "total_rank": len(ordered_templates), "template_id": template.get("template_id"),
                               "template_name": template.get("template_name"), "version_name": template.get("version_name")})
        if not fields:
            verification = verification_service._verification_summary(str(template.get("template_id")), [], status=f"no_{strategy}_anchors")
            verification["passed"] = False
        elif strategy == VERIFICATION_STRATEGY_STRICT:
            verification = _verify_sequential_anchor_template(str(template.get("template_id")), fields, page_image_paths)
        else:
            verification = verification_service.verify_template(
                str(template.get("template_id")),
                page_image_paths,
                fields,
                detection_batch_mode=detection_batch_mode,
                precomputed_text_ocr_cache=global_text_results if global_text_results is not None else None,
                precomputed_text_ocr_errors=global_text_errors if global_text_errors is not None else None,
                precomputed_image_matches=global_image_matches if global_image_matches is not None else None,
                use_image_batch=detection_batch_mode != DETECTION_BATCH_MODE_GLOBAL,
            )
        candidate = _candidate_from_anchor_strategy(template, verification, fields, page_image_paths, strategy, timing)
        if template.get("page_count") is not None:
            expected_pages = int(template.get("page_count") or 0)
            main_page_mode = template.get("detection_mode") == "main_page"
            page_count_valid = (
                int(template.get("main_page_number") or 1) in page_image_paths
                if main_page_mode else expected_pages == len(page_image_paths)
            )
            if not page_count_valid:
                candidate.update(final_passed=False, decision_reason="page_count_mismatch",
                                 decision_path="page_count_mismatch", candidate_rejection_reason="page_count_mismatch")
        candidates.append(candidate)
        target_page_indexes = (
            [int(template.get("main_page_number") or 1)]
            if template.get("detection_mode") == "main_page" else list(pages_by_index)
        )
        for target_page_index in target_page_indexes:
            page = pages_by_index.get(target_page_index)
            if page is None:
                continue
            if target_page_index == int(candidate.get("query_page_index") or 1):
                page_candidate = candidate
            else:
                image_path = page_image_paths[target_page_index]
                preview_url = _detection_preview_url(image_path)
                page_candidate = {**candidate, "query_page_index": target_page_index,
                                  "template_page_number": target_page_index,
                                  "normalized_image_path": image_path, "normalized_image_preview_url": preview_url,
                                  "extraction_image_path": image_path, "extraction_image_preview_url": preview_url,
                                  "selected_processing_path": image_path}
            page["candidates"].append(page_candidate)
    timing["anchor_strategy_matching"] = time.perf_counter() - match_started
    candidates.sort(key=lambda item: (bool(item.get("final_passed")), float(item.get("final_score") or 0.0)), reverse=True)
    for page in pages_by_index.values():
        page["candidates"].sort(key=lambda item: (bool(item.get("final_passed")), float(item.get("final_score") or 0.0)), reverse=True)
        page["best_candidate"] = page["candidates"][0] if page["candidates"] else None
    pages = list(pages_by_index.values())
    best_candidate = next((candidate for candidate in candidates if candidate.get("final_passed")), None)
    matched = best_candidate is not None
    if user_context and best_candidate is not None:
        _attach_main_page_auto_roi_pages([best_candidate], normalized_pages)
    processing_pages = _persist_selected_processing_pages(query_id, pages, best_candidate, normalized_pages)
    if best_candidate is not None:
        best_candidate["processing_pages"] = processing_pages
    timing["anchor_strategy_total"] = time.perf_counter() - started
    return {
        "query_id": query_id,
        "engine": strategy,
        "version": DETECTION_VERSION,
        "threshold": verification_service.DEFAULT_VERIFICATION_THRESHOLD,
        "matched": matched,
        "best_candidate": best_candidate,
        "candidates": candidates,
        "pages": pages,
        "main_page_auto_roi_pages": (best_candidate or {}).get("main_page_auto_roi_pages", []),
        "main_page_auto_roi_total_pages": (best_candidate or {}).get("main_page_auto_roi_total_pages", 0),
        "main_page_auto_roi_total_regions": (best_candidate or {}).get("main_page_auto_roi_total_regions", 0),
        "message": None if matched else f"ไม่พบ Template ที่ผ่าน {strategy}",
        "debug": {
            "pipeline_core": PIPELINE_CONFIG.to_debug_dict(),
            "retrieval_engine": strategy,
            "layout_retrieval_skipped": True,
            "layout_prefilter_skipped": True,
            "source_type": source_type,
            "verification_strategy": strategy,
            "anchor_strategy": {
                "template_anchor_load": load_debug,
                "anchor_types": sorted(anchor_types),
                "batch_mode": detection_batch_mode,
                "batch_scope": "per_template" if detection_batch_mode != DETECTION_BATCH_MODE_GLOBAL else "global_text_image",
                "image_batch_schema_available": True,
                "image_batch_schema_source": "/api/v1/image-classification-batches images[] + categories, response data.results[]",
                "template_order": template_order_debug,
                "early_exit": early_exit,
                "templates_checked": len(candidates),
                "templates_available": len(templates),
                "anchors_checked": sum(len(candidate.get("verification_details") or []) for candidate in candidates),
                "global_text_batch": global_text_debug,
                "global_image_batch": global_image_debug,
                "model_request_count": (
                    int((global_text_debug or {}).get("text_model_request_count") or 0)
                    if detection_batch_mode == DETECTION_BATCH_MODE_GLOBAL
                    else sum(
                        len({
                            int(field.get("page_number") or 1)
                            for field in candidate.get("verification_details") or []
                            if isinstance(field, dict) and field.get("anchor_type") == "text"
                        })
                        for candidate in candidates
                    )
                ) + (
                    int((global_image_debug or {}).get("image_model_request_count") or 0)
                    if detection_batch_mode == DETECTION_BATCH_MODE_GLOBAL
                    else sum(
                        int((((candidate.get("verification") or {}).get("timing") or {}).get("image_batch") or {}).get("image_model_request_count") or 0)
                        for candidate in candidates
                    )
                ),
                "total_ms": _ms(timing.get("anchor_strategy_total")),
            },
            "timing": _detection_timing_debug(timing, pages, total_started, request_cache=request_cache),
        },
    }


PDF_A4_PORTRAIT_ASPECT_RATIO = 1.0 / math.sqrt(2.0)
PDF_A4_ASPECT_RATIO_TOLERANCE = 0.04


def _benchmark_step(elapsed_ms: Optional[float], enabled: bool, reason: Optional[str] = None) -> Dict[str, Any]:
    if not enabled:
        return {"status": "skipped", "elapsed_ms": None, "reason": reason or "step_disabled"}
    if elapsed_ms is None:
        return {"status": "unknown", "elapsed_ms": None, "reason": reason or "timing_not_available"}
    return {"status": "processed", "elapsed_ms": elapsed_ms, "reason": reason}


def _sum_candidate_ms(candidate_timings: List[Dict[str, Any]], *keys: str) -> Optional[float]:
    total = 0.0
    found = False
    for timing_item in candidate_timings:
        for key in keys:
            value = timing_item.get(key)
            if value is None:
                for nested in timing_item.values():
                    if isinstance(nested, dict) and isinstance(nested.get(key), (int, float)):
                        value = nested.get(key)
                        break
            if isinstance(value, (int, float)):
                total += float(value)
                found = True
    return round(total, 2) if found else None


def _candidate_anchor_counts(candidates: List[Dict[str, Any]]) -> Dict[str, int]:
    text_anchors = 0
    image_anchors = 0
    rois = 0
    for candidate in candidates:
        details = candidate.get("verification_details")
        if not isinstance(details, list):
            verification = candidate.get("verification") if isinstance(candidate.get("verification"), dict) else {}
            details = verification.get("checked_fields", []) if isinstance(verification, dict) else []
        for field in details or []:
            if not isinstance(field, dict):
                continue
            rois += 1
            anchor_type = str(field.get("anchor_type") or field.get("verification_method") or "").lower()
            if "image" in anchor_type:
                image_anchors += 1
            elif "text" in anchor_type or "ocr" in anchor_type:
                text_anchors += 1
    return {"text_anchors": text_anchors, "image_anchors": image_anchors, "rois": rois}


def _sum_candidate_db_total_ms(candidate_timings: List[Dict[str, Any]]) -> Optional[float]:
    total = 0.0
    found = False
    for timing_item in candidate_timings:
        db_debug = timing_item.get("db")
        if not isinstance(db_debug, dict):
            continue
        for section in db_debug.values():
            if not isinstance(section, dict):
                continue
            value = section.get("db_total_ms")
            if isinstance(value, (int, float)):
                total += float(value)
                found = True
    return round(total, 2) if found else None


def _benchmark_timing_debug(
    timing: Dict[str, Any],
    pages: List[Dict[str, Any]],
    candidate_timings: List[Dict[str, Any]],
    total_elapsed_ms: Optional[float],
    request_cache: Optional[DetectionRequestCache] = None,
) -> Dict[str, Any]:
    candidates = [candidate for page in pages for candidate in page.get("candidates", []) if isinstance(candidate, dict)]
    strategy = next((str(candidate.get("verification_strategy")) for candidate in candidates if candidate.get("verification_strategy")), None)
    anchor_counts = _candidate_anchor_counts(candidates)
    physical_attempts = timing.get("physical_crop_attempts") if isinstance(timing.get("physical_crop_attempts"), list) else []
    physical_crop_enabled = bool(physical_attempts) and any(item.get("elapsed_ms") is not None for item in physical_attempts if isinstance(item, dict))
    physical_crop_applied = any(bool(item.get("crop_applied")) for item in physical_attempts if isinstance(item, dict))

    text_ocr_ms = _sum_candidate_ms(candidate_timings, "text_ocr_inference_ms")
    image_verification_ms = _sum_candidate_ms(candidate_timings, "image_verification_ms")
    # Image Only Global Batch performs the model call before candidate
    # verification, so the candidate-local timing can legitimately be zero.
    # Include the actual batch request time in the public benchmark timing.
    global_image_batch_debug = timing.get("anchor_strategy_global_image_batch_debug") if isinstance(timing.get("anchor_strategy_global_image_batch_debug"), dict) else {}
    global_text_batch_debug = timing.get("anchor_strategy_global_text_batch_debug") if isinstance(timing.get("anchor_strategy_global_text_batch_debug"), dict) else {}
    global_image_siglip_timing = {
        "siglip_preprocess_ms": global_image_batch_debug.get("siglip_preprocess_ms"),
        "siglip_inference_ms": global_image_batch_debug.get("siglip_inference_ms"),
        "siglip_postprocess_ms": global_image_batch_debug.get("siglip_postprocess_ms"),
        "image_batch_total_ms": global_image_batch_debug.get("image_batch_total_ms"),
        "siglip_batch_size": global_image_batch_debug.get("siglip_batch_size"),
    }
    # Per-template mode performs one or more image batches inside each
    # candidate. Aggregate only Runtime-provided SigLIP timings from the
    # candidates that were actually evaluated (including an early-exit match).
    if global_image_siglip_timing["siglip_inference_ms"] is None:
        for timing_key in ("siglip_preprocess_ms", "siglip_inference_ms", "siglip_postprocess_ms", "image_batch_total_ms"):
            values = [
                float(item[timing_key])
                for item in candidate_timings
                if isinstance(item.get(timing_key), (int, float))
            ]
            global_image_siglip_timing[timing_key] = round(sum(values), 2) if values else None
        batch_sizes = [
            int(item["siglip_batch_size"])
            for item in candidate_timings
            if isinstance(item.get("siglip_batch_size"), (int, float))
        ]
        global_image_siglip_timing["siglip_batch_size"] = sum(batch_sizes) if batch_sizes else None
    def _sum_runtime_field(field: str) -> Optional[float]:
        values = []
        for item in candidate_timings:
            value = item.get(field)
            if not isinstance(value, (int, float)):
                verification_timing = item.get("verification_timing")
                if isinstance(verification_timing, dict):
                    value = verification_timing.get(field)
            if isinstance(value, (int, float)):
                values.append(float(value))
            logger.info(
                "[TIMING DEBUG] benchmark candidate template_id=%s field=%s value_type=%s value=%s verification_type=%s",
                item.get("template_id"),
                field,
                type(value).__name__,
                value,
                type(item.get("verification_timing")).__name__,
            )
        return round(sum(values), 2) if values else None

    text_det_inference_ms = _sum_runtime_field("text_det_inference_ms")
    text_rec_inference_ms = _sum_runtime_field("text_rec_inference_ms")
    if global_text_batch_debug:
        text_det_inference_ms = _runtime_ms(global_text_batch_debug.get("text_det_inference_ms"))
        text_rec_inference_ms = _runtime_ms(global_text_batch_debug.get("text_rec_inference_ms"))
    siglip_inference_ms = global_image_siglip_timing.get("siglip_inference_ms")
    model_inference_values = [value for value in (text_det_inference_ms, text_rec_inference_ms, siglip_inference_ms) if isinstance(value, (int, float))]
    model_inference_total_ms = round(sum(model_inference_values), 2) if model_inference_values else None
    global_image_batch_ms = _runtime_ms(global_image_batch_debug.get("image_batch_total_ms"))
    if global_image_batch_ms is None:
        global_image_batch_ms = _ms(timing.get("anchor_strategy_global_image_batch"))
    if global_image_batch_ms is not None and (image_verification_ms is None or image_verification_ms <= 0):
        image_verification_ms = global_image_batch_ms
    alignment_ms = _sum_candidate_ms(candidate_timings, "alignment_ms")
    database_ms = _sum_candidate_db_total_ms(candidate_timings)
    if database_ms is None:
        database_ms = _sum_candidate_ms(candidate_timings, "template_fetch_ms", "field_count_query_ms", "verification_fields_load_ms")
    decision_ms = _sum_candidate_ms(candidate_timings, "decision_ms")
    final_selection_ms = _ms(timing.get("final_selection"))
    if decision_ms is not None or final_selection_ms is not None:
        matching_ms = round(float(decision_ms or 0.0) + float(final_selection_ms or 0.0), 2)
    else:
        matching_ms = None
    layout_enabled = False
    text_enabled = anchor_counts["text_anchors"] > 0 or bool(timing.get("verification"))
    image_enabled = (anchor_counts["image_anchors"] > 0 or (image_verification_ms is not None and image_verification_ms > 0))

    ocr_batches = int(global_text_batch_debug.get("text_batch_count") or 0)
    if not ocr_batches:
        ocr_batches = sum(len({field.get("page_number") for field in candidate.get("verification_details") or []
                               if isinstance(field, dict) and field.get("anchor_type") == "text"})
                          for candidate in candidates)

    return {
        "schema_version": 1,
        "total_detection_ms": total_elapsed_ms,
        "verification_strategy": strategy,
        "siglip_preprocess_ms": global_image_siglip_timing["siglip_preprocess_ms"],
        "siglip_inference_ms": global_image_siglip_timing["siglip_inference_ms"],
        "siglip_postprocess_ms": global_image_siglip_timing["siglip_postprocess_ms"],
        "image_batch_total_ms": global_image_siglip_timing["image_batch_total_ms"],
        "siglip_batch_size": global_image_siglip_timing["siglip_batch_size"],
        "text_det_inference_ms": text_det_inference_ms,
        "text_rec_inference_ms": text_rec_inference_ms,
        "model_inference_total_ms": model_inference_total_ms,
        "frontend_to_backend_ms": None,
        "backend_runtime_ms": None,
        "backend_to_model_ms": None,
        "layout_runtime_ms": _ms(timing.get("layout_runtime_ms")),
        "text_detection_runtime_ms": text_det_inference_ms,
        "text_recognition_runtime_ms": text_rec_inference_ms,
        "image_siglip_runtime_ms": siglip_inference_ms,
        "layout_wait_ms": _ms(timing.get("layout_wait_ms")),
        "text_detection_wait_ms": _ms(timing.get("text_detection_wait_ms")),
        "text_recognition_wait_ms": _ms(timing.get("text_recognition_wait_ms")),
        "image_siglip_wait_ms": _ms(timing.get("image_siglip_wait_ms")),
        "model_wall_clock_ms": _ms(timing.get("model_wall_clock_ms")),
        "model_wait_union_ms": _ms(timing.get("model_wait_union_ms")),
        "model_intervals_overlap": timing.get("model_intervals_overlap"),
        "total_runtime_ms": total_elapsed_ms,
        "backend_to_frontend_ms": None,
        "frontend_render_ms": None,
        "end_to_end_ms": None,
        "verification_breakdown": {
            key: _ms(timing.get("verification_breakdown", {}).get(key))
            if isinstance(timing.get("verification_breakdown"), dict) else None
            for key in ("database", "anchor_loading", "ocr", "image_verification", "alignment", "scoring", "candidate_verification")
        },
        "benchmark_case": "Text + Image" if text_enabled and image_enabled else "Text" if text_enabled else "Image" if image_enabled else "No Anchors",
        "physical_crop_mode": "ON" if physical_crop_enabled else "OFF",
        "model_combination": {
            "layout": layout_enabled,
            "text": text_enabled,
            "image": image_enabled,
            "physical_crop": physical_crop_enabled,
            "physical_crop_applied": physical_crop_applied,
        },
        "steps": {
            "prepare_pages": _benchmark_step(_ms(timing.get("prepare_pages")), True),
            "normalization": _benchmark_step(
                _ms(timing.get("prepare_normalization")),
                bool(timing.get("prepare_normalization")),
                "pdf_rendered_page_uses_physical_crop_detector" if timing.get("prepare_normalization_skipped") else None,
            ),
            "physical_crop": {
                "status": "processed" if physical_crop_enabled else "skipped",
                "elapsed_ms": _ms(timing.get("physical_crop_total")),
                "detection_ms": _ms(timing.get("physical_crop_detection")),
                "validation_ms": _ms(timing.get("physical_crop_validation")),
                "apply_ms": _ms(timing.get("physical_crop_apply")),
                "crop_applied": physical_crop_applied,
                "attempts": physical_attempts,
            },
            "layout_analysis": _benchmark_step(_ms(timing.get("layout_analysis")), layout_enabled, "anchor_only_matching"),
            "layout_retrieval": _benchmark_step(_ms(timing.get("template_matching")), layout_enabled, "anchor_only_matching"),
            "text_det_rec": _benchmark_step(text_ocr_ms, text_enabled, "no_text_anchors" if not text_enabled else None),
            "image_verification": _benchmark_step(image_verification_ms, image_enabled, "no_image_anchors" if not image_enabled else None),
            "siglip": global_image_siglip_timing,
            "alignment": _benchmark_step(alignment_ms, bool(alignment_ms and alignment_ms > 0), "not_required_or_skipped"),
            "database": _benchmark_step(database_ms, bool(database_ms is not None), "no_database_timing_available" if database_ms is None else None),
            "matching": _benchmark_step(matching_ms, True),
        },
        "counts": {
            "pages": len(pages),
            "templates": len({candidate.get("template_id") for candidate in candidates if candidate.get("template_id")}),
            "candidates": len(candidates),
            "text_anchors": anchor_counts["text_anchors"],
            "image_anchors": anchor_counts["image_anchors"],
            "rois": anchor_counts["rois"],
            "ocr_batches": ocr_batches,
            "request_cache": dict(request_cache.stats) if request_cache is not None else {},
        },
        "attempts": {
            "original_or_crop": [
                {
                    "page_index": page.get("page_index"),
                    "matching_path_source": page.get("matching_path_source") or (page.get("debug") or {}).get("matching_path_source"),
                    "matching_image_path": page.get("matching_image_path") or page.get("image_path") or page.get("normalized_image_path"),
                }
                for page in pages
            ],
            "candidate_verification": [
                {
                    "page_index": item.get("page_index"),
                    "retrieval_rank": item.get("retrieval_rank"),
                    "template_id": item.get("template_id"),
                    "normalized_verification_ms": item.get("normalized_verification_ms"),
                    "aligned_verification_ms": item.get("aligned_verification_ms"),
                    "alignment_ms": item.get("alignment_ms"),
                    "verification_source_used": item.get("verification_source_used"),
                    "alignment_status": item.get("alignment_status"),
                }
                for item in candidate_timings
            ],
            "physical_crop": physical_attempts,
        },
        "skipped_steps": [
            key
            for key, value in {
                "layout_analysis": layout_enabled,
                "layout_retrieval": layout_enabled,
                "image_verification": image_enabled,
                "physical_crop": physical_crop_enabled,
                "alignment": bool(alignment_ms and alignment_ms > 0),
            }.items()
            if not value
        ],
    }


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
                    "verification_timing": (candidate.get("verification") or {}).get("timing") if isinstance(candidate.get("verification"), dict) else None,
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
        "processing_page_persist",
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
        "processing_page_persist",
    ]
    boundary_measured = sum(float(timing.get(key) or 0.0) for key in boundary_keys)
    detect_pages_overhead = max(0.0, float(timing.get("detect_pages_total") or 0.0) - detect_pages_inner_measured)

    total_elapsed_ms = _ms(total_elapsed)
    global_image_batch_debug = timing.get("anchor_strategy_global_image_batch_debug") if isinstance(timing.get("anchor_strategy_global_image_batch_debug"), dict) else {}
    global_text_batch_debug = timing.get("anchor_strategy_global_text_batch_debug") if isinstance(timing.get("anchor_strategy_global_text_batch_debug"), dict) else {}
    if global_image_batch_debug.get("siglip_inference_ms") is None:
        per_template_siglip = {}
        for timing_key in ("siglip_preprocess_ms", "siglip_inference_ms", "siglip_postprocess_ms", "image_batch_total_ms"):
            values = [
                float(item[timing_key])
                for item in candidate_timings
                if isinstance(item.get(timing_key), (int, float))
            ]
            per_template_siglip[timing_key] = round(sum(values), 2) if values else None
        batch_sizes = [
            int(item["siglip_batch_size"])
            for item in candidate_timings
            if isinstance(item.get("siglip_batch_size"), (int, float))
        ]
        per_template_siglip["siglip_batch_size"] = sum(batch_sizes) if batch_sizes else None
        if per_template_siglip["siglip_inference_ms"] is not None:
            global_image_batch_debug = {**global_image_batch_debug, **per_template_siglip}
    if global_image_batch_debug.get("siglip_inference_ms") is None:
        candidate_siglip_values = [
            float(item["siglip_inference_ms"])
            for item in candidate_timings
            if isinstance(item.get("siglip_inference_ms"), (int, float))
        ]
        if candidate_siglip_values:
            global_image_batch_debug = {
                **global_image_batch_debug,
                "siglip_inference_ms": round(sum(candidate_siglip_values), 2),
            }
    def _candidate_runtime_values(field: str) -> List[float]:
        values: List[float] = []
        for item in candidate_timings:
            value = item.get(field)
            if not isinstance(value, (int, float)) and isinstance(item.get("verification_timing"), dict):
                value = item["verification_timing"].get(field)
            if isinstance(value, (int, float)):
                values.append(float(value))
        return values

    text_det_values = _candidate_runtime_values("text_det_inference_ms")
    text_rec_values = _candidate_runtime_values("text_rec_inference_ms")
    text_det_inference_ms = round(sum(text_det_values), 2) if text_det_values else None
    text_rec_inference_ms = round(sum(text_rec_values), 2) if text_rec_values else None
    if global_text_batch_debug:
        text_det_inference_ms = _runtime_ms(global_text_batch_debug.get("text_det_inference_ms"))
        text_rec_inference_ms = _runtime_ms(global_text_batch_debug.get("text_rec_inference_ms"))
    siglip_inference_ms = global_image_batch_debug.get("siglip_inference_ms")
    from app.processing.model_timing import backend_runtime_from_union, summarize_model_calls

    model_summary = summarize_model_calls([timing, candidate_timings])
    text_det_inference_ms = model_summary["inferences"].get("text_detection")
    text_rec_inference_ms = model_summary["inferences"].get("text_recognition")
    siglip_inference_ms = model_summary["inferences"].get("image_verification")
    waits = model_summary["waits"]
    layout_wait_ms = waits.get("layout")
    text_detection_wait_ms = waits.get("text_detection")
    text_recognition_wait_ms = waits.get("text_recognition")
    image_siglip_wait_ms = waits.get("image_verification")
    table_wait_ms = waits.get("table")
    model_wall_clock_ms = model_summary["model_wall_clock_ms"]
    model_wait_union_ms = model_summary["model_wait_union_ms"]
    model_intervals_overlap = model_summary["model_intervals_overlap"]
    backend_runtime_ms = backend_runtime_from_union(total_elapsed_ms, model_wait_union_ms, model_intervals_overlap)
    backend_to_model_ms = None
    layout_runtime_ms = model_summary["inferences"].get("layout")
    table_runtime_ms = model_summary["inferences"].get("table")
    model_values = list(model_summary["inferences"].values())
    model_inference_total_ms = round(sum(model_values), 2) if model_values and all(value is not None for value in model_values) else None
    verification_breakdown = {
        key: round(sum(
            float((item.get("verification_timing") or {}).get(key))
            for item in candidate_timings
            if isinstance((item.get("verification_timing") or {}).get(key), (int, float))
        ), 2) if any(isinstance((item.get("verification_timing") or {}).get(key), (int, float)) for item in candidate_timings) else None
        for key in ("database", "anchor_loading", "ocr", "image_verification", "alignment", "scoring", "candidate_verification")
    }
    timing.update({
        "layout_wait_ms": layout_wait_ms,
        "text_detection_wait_ms": text_detection_wait_ms,
        "text_recognition_wait_ms": text_recognition_wait_ms,
        "image_siglip_wait_ms": image_siglip_wait_ms,
        "model_wall_clock_ms": model_wall_clock_ms,
        "model_wait_union_ms": model_wait_union_ms,
        "model_intervals_overlap": model_intervals_overlap,
        "backend_runtime_ms": backend_runtime_ms,
        "backend_to_model_ms": backend_to_model_ms,
        "table_wait_ms": table_wait_ms,
        "model_timing_missing_reason": model_summary["model_timing_missing_reason"],
        "model_call_count": model_summary["model_call_count"],
        "model_calls": model_summary["model_calls"],
        "verification_breakdown": verification_breakdown,
    })
    result = {
        "total_detection_ms": total_elapsed_ms,
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
        "anchor_strategy_matching_ms": _ms(timing.get("anchor_strategy_matching")),
        "template_matching_breakdown": _template_matching_timing_ms_list(timing.get("layout_candidate_searches")),
        "verification_ms": _ms(timing.get("verification")),
        "siglip_preprocess_ms": global_image_batch_debug.get("siglip_preprocess_ms"),
        "siglip_inference_ms": siglip_inference_ms,
        "siglip_postprocess_ms": global_image_batch_debug.get("siglip_postprocess_ms"),
        "image_batch_total_ms": global_image_batch_debug.get("image_batch_total_ms"),
        "siglip_batch_size": global_image_batch_debug.get("siglip_batch_size"),
        "text_det_inference_ms": text_det_inference_ms,
        "text_rec_inference_ms": text_rec_inference_ms,
        "model_inference_total_ms": model_inference_total_ms,
        "frontend_to_backend_ms": None,
        "backend_runtime_ms": backend_runtime_ms,
        "backend_to_model_ms": backend_to_model_ms,
        "layout_runtime_ms": layout_runtime_ms,
        "table_runtime_ms": table_runtime_ms,
        "text_detection_runtime_ms": text_det_inference_ms,
        "text_recognition_runtime_ms": text_rec_inference_ms,
        "image_siglip_runtime_ms": siglip_inference_ms,
        "layout_wait_ms": layout_wait_ms,
        "text_detection_wait_ms": text_detection_wait_ms,
        "text_recognition_wait_ms": text_recognition_wait_ms,
        "image_siglip_wait_ms": image_siglip_wait_ms,
        "table_wait_ms": table_wait_ms,
        "model_wall_clock_ms": model_wall_clock_ms,
        "model_wait_union_ms": model_wait_union_ms,
        "model_intervals_overlap": model_intervals_overlap,
        "model_timing_missing_reason": model_summary["model_timing_missing_reason"],
        "model_call_count": model_summary["model_call_count"],
        "model_calls": model_summary["model_calls"],
        "total_runtime_ms": total_elapsed_ms,
        "backend_to_frontend_ms": None,
        "frontend_render_ms": None,
        "end_to_end_ms": None,
        "verification_breakdown": verification_breakdown,
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
        "benchmark": _benchmark_timing_debug(
            timing,
            pages,
            candidate_timings,
            total_elapsed_ms,
            request_cache=request_cache,
        ),
    }
    missing_reasons = {
        "frontend_to_backend_ms": "one_way_network_not_measurable",
        "backend_to_frontend_ms": "one_way_network_not_measurable",
        "backend_to_model_ms": "http_duration_includes_queue_load_inference_and_serialization",
        "frontend_render_ms": "browser_paint_not_instrumented",
        "end_to_end_ms": "measured_by_browser_only",
    }
    for prefix, kind in (("layout", "layout"), ("text_detection", "text_detection"),
                         ("text_recognition", "text_recognition"), ("image_siglip", "image_verification"),
                         ("table", "table")):
        for suffix, source in (("runtime_ms", "inferences"), ("wait_ms", "waits")):
            key = f"{prefix}_{suffix}"
            if result[key] is None:
                missing_reasons[key] = ("incomplete_or_shared_model_call_measurement" if kind in model_summary[source]
                                        else "no_recorded_call_cached_or_not_applicable")
    for key in ("model_wait_union_ms", "model_wall_clock_ms", "model_intervals_overlap"):
        if result[key] is None:
            missing_reasons[key] = model_summary["model_timing_missing_reason"]
    if backend_runtime_ms is None:
        missing_reasons["backend_runtime_ms"] = model_summary["model_timing_missing_reason"] or "model_union_exceeds_detection_duration"
    result["timing_missing_reasons"] = missing_reasons
    for key in (
        "total_detection_ms", "total_runtime_ms", "backend_runtime_ms", "backend_to_model_ms",
        "layout_runtime_ms", "text_detection_runtime_ms", "text_recognition_runtime_ms",
        "image_siglip_runtime_ms", "table_runtime_ms", "layout_wait_ms",
        "text_detection_wait_ms", "text_recognition_wait_ms", "image_siglip_wait_ms",
        "table_wait_ms", "model_wall_clock_ms", "model_wait_union_ms",
        "model_intervals_overlap", "model_timing_missing_reason", "model_call_count", "model_calls",
        "timing_missing_reasons", "frontend_to_backend_ms", "backend_to_frontend_ms", "frontend_render_ms", "end_to_end_ms",
        "siglip_inference_ms", "text_det_inference_ms", "text_rec_inference_ms", "model_inference_total_ms",
    ):
        result["benchmark"][key] = result[key]
    return result


def _finish_detection_result(result: Dict[str, Any]) -> Dict[str, Any]:
    from time import perf_counter
    from app.processing.search_benchmark import capture_result, record_result_processing
    capture_result(result)
    if include_detection_diagnostics():
        return result
    from app.api.detection_response import user_detection_response
    def preview(path):
        try:
            return _image_to_data_url(Path(path), required=True)
        except OSError:
            return None
    result_processing_started = perf_counter()
    response = user_detection_response(
        result, preview_loader=preview,
        compact_candidates=compact_detection_candidates(),
    )
    record_result_processing((perf_counter() - result_processing_started) * 1000)
    return response


@detection_work_scope
def detect_template_dev(
    file_bytes: bytes | List[bytes],
    include_template_id: Optional[str] = None,
    cleanup_generated: bool = True,
    prepublish_timing: bool = False,
    prepublish_total_started: Optional[float] = None,
    verification_strategy_override: Optional[str] = None,
    retrieval_limit_override: Optional[int] = None,
    verification_candidate_limit_override: Optional[int] = None,
    full_evaluation_limit_override: Optional[int] = None,
    prepublish_full_evaluation: bool = False,
    disable_pdf_subdocument_crop: bool = False,
    progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    use_image_batch_for_verification: bool = False,
    user_context: bool = False,
    compact_candidates: bool = False,
    skip_query_normalization: bool = False,
    admin_context: bool = False,
    compare_active_templates: bool = False,
) -> Dict[str, Any]:
    query_id = f"detq_{uuid4().hex[:12]}"
    timing: Dict[str, float] = {}
    request_cache = DetectionRequestCache()
    total_started = prepublish_total_started or time.perf_counter()
    try:
        step_started = time.perf_counter()
        source_type = "pdf" if isinstance(file_bytes, bytes) and file_bytes.lstrip().startswith(b"%PDF") else "image"
        timing["source_type_detection"] = time.perf_counter() - step_started
        step_started = time.perf_counter()
        page_paths = _prepare_query_pages(query_id, file_bytes, timing=timing)
        skip_normalization = skip_query_normalization
        normalized_pages = _normalize_query_pages(
            query_id,
            page_paths,
            skip_normalization=skip_normalization or admin_context,
            source_type=source_type,
            timing=timing,
            disable_pdf_subdocument_crop=disable_pdf_subdocument_crop,
        )
        timing["prepare_pages"] = time.perf_counter() - step_started
        if prepublish_timing:
            print(f"[PREPUBLISH] prepare pages done: {timing['prepare_pages']:.2f}s")
        step_started = time.perf_counter()
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
        detection_batch_mode = DETECTION_BATCH_MODE_GLOBAL
        return _finish_detection_result(_detect_template_anchor_strategy(
            query_id, normalized_pages, timing, request_cache, total_started,
            source_type, verification_strategy, detection_batch_mode,
            selected_template_id=include_template_id, progress_callback=progress_callback,
            user_context=user_context,
            compare_active_templates=compare_active_templates,
        ))
    finally:
        if cleanup_generated and not SAVE_DEBUG_ARTIFACTS:
            _cleanup_query_artifacts(query_id)
