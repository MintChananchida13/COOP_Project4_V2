import os
import tempfile
import logging
import time
import hashlib
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Optional

import cv2
import numpy as np

from .model_runtime_client import ModelRuntimeUnavailableError, remote_recognize_table
from .ocr_postprocess import normalize_ocr_text, normalize_table_rows, parse_table_html_with_bs4
from .layout_analysis_service import LayoutAnalysisUnavailableError, detect_text_boxes
from .table_grid_analyzer import analyze_table_regions


class TableRecognitionV2UnavailableError(RuntimeError):
    pass


logger = logging.getLogger(__name__)

_TABLE_MODEL: Any = None
_TABLE_MODEL_KIND = ""
_TABLE_WIRED_MODEL_NAME = (
    os.getenv("PADDLE_TABLE_WIRED_MODEL_NAME")
    or os.getenv("PADDLE_TABLE_MODEL_NAME")
    or os.getenv("PADDLE_TABLE_RECOGNITION_MODEL_NAME")
    or "SLANeXt_wired"
)
_TABLE_WIRELESS_MODEL_NAME = (
    os.getenv("PADDLE_TABLE_WIRELESS_MODEL_NAME")
    or os.getenv("PADDLE_TABLE_MODEL_NAME")
    or os.getenv("PADDLE_TABLE_RECOGNITION_MODEL_NAME")
    or "SLANeXt_wireless"
)
_TABLE_MODEL_NAME = f"{_TABLE_WIRED_MODEL_NAME}/{_TABLE_WIRELESS_MODEL_NAME}"
_TABLE_TEXT_RECOGNITION_MODEL_NAME = os.getenv("PADDLE_TABLE_TEXT_RECOGNITION_MODEL_NAME", "th_PP-OCRv5_mobile_rec")
_TABLE_DEVICE = "cpu"
_BORDERLESS_MIN_COLUMNS = 2
_BORDERLESS_MIN_ROWS = 2
_TABLE_BORDERLESS_FINAL_CONFIDENCE_THRESHOLD = 0.72
_TABLE_BORDERLESS_FILL_RATIO_THRESHOLD = 0.20
_TABLE_BORDERLESS_COLUMN_CONSISTENCY_THRESHOLD = 0.45
_TABLE_BORDERLESS_SPARSE_ROW_RATIO_THRESHOLD = 0.70
_TABLE_CANDIDATE_TIE_EPSILON = 0.03
_TABLE_LOW_OCR_CONFIDENCE_THRESHOLD = 0.65
_SEMI_TABLE_MIN_CONFIDENCE = 0.72
_SEMI_TABLE_MIN_TOPOLOGY_CHANGE_RATIO = 0.33
_TABLE_DEBUG_RAW_MODEL_FIELDS = (
    "table_type",
    "model_name",
    "structure_model",
    "structure_model_name",
    "table_structure_model",
    "table_structure_model_name",
    "cls_result",
    "class_result",
    "classifier_result",
    "classification",
    "score",
    "confidence",
)


def _model_service_url() -> str:
    return os.getenv("MODEL_SERVICE_URL", "").strip()


def _use_remote_runtime() -> bool:
    return bool(_model_service_url())


def _table_debug_trace_enabled() -> bool:
    return os.getenv("TABLE_DEBUG_TRACE", "").strip().lower() in {"1", "true", "yes", "on"}


def _json_safe(value: Any, depth: int = 0) -> Any:
    if depth > 8:
        return "<max_depth>"
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return {
            "type": "ndarray",
            "shape": [int(item) for item in value.shape],
            "dtype": str(value.dtype),
        }
    if isinstance(value, dict):
        return {str(key): _json_safe(item, depth + 1) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item, depth + 1) for item in value]
    return str(value)


def _table_snapshot(result: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "table_rows": _json_safe(result.get("table_rows")),
        "table_structured": _json_safe(result.get("table_structured")),
        "table_html": result.get("table_html"),
    }


def _debug_input_trace(image: np.ndarray) -> Dict[str, Any]:
    height, width = image.shape[:2]
    encoded_ok, encoded = cv2.imencode(".png", image)
    sha256 = "not_available"
    if encoded_ok:
        sha256 = hashlib.sha256(encoded.tobytes()).hexdigest()
    trace: Dict[str, Any] = {
        "image_size": {"width": int(width), "height": int(height)},
        "sha256": sha256,
        "debug_png_path": "not_saved",
    }
    debug_dir = os.getenv("TABLE_DEBUG_TRACE_DIR", "").strip()
    if debug_dir and encoded_ok:
        try:
            output_dir = Path(debug_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = output_dir / f"table_roi_{sha256[:16]}.png"
            output_path.write_bytes(encoded.tobytes())
            trace["debug_png_path"] = str(output_path)
        except Exception as error:
            trace["debug_png_path"] = f"save_failed:{error}"
    return trace


def _extract_raw_model_fields(dicts: List[Dict[str, Any]]) -> Dict[str, Any]:
    fields: Dict[str, Any] = {key: "not_available" for key in _TABLE_DEBUG_RAW_MODEL_FIELDS}
    for item in dicts:
        for key in _TABLE_DEBUG_RAW_MODEL_FIELDS:
            if fields[key] == "not_available" and key in item:
                fields[key] = _json_safe(item.get(key))
    return fields


def _source_structure_model_from_raw_fields(raw_fields: Dict[str, Any]) -> str:
    for key in (
        "structure_model",
        "structure_model_name",
        "table_structure_model",
        "table_structure_model_name",
        "model_name",
        "table_type",
    ):
        value = raw_fields.get(key)
        if value and value != "not_available":
            return str(value)
    return "not_available"


def _paddle_raw_trace(dicts: List[Dict[str, Any]], html: str, rows: List[List[str]], structured_table: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    raw_keys = sorted({str(key) for item in dicts for key in item.keys()})
    raw_cells: List[Any] = []
    for item in dicts:
        for key in ("cells", "table_cells", "cell_bbox", "cell_bboxes", "bbox", "boxes"):
            if key in item:
                raw_cells.append({key: _json_safe(item.get(key))})
    return {
        "keys": raw_keys,
        "html": html or "not_available",
        "rows": _json_safe(rows) if rows else "not_available",
        "structured": _json_safe(structured_table) if structured_table else "not_available",
        "cells_bbox": raw_cells if raw_cells else "not_available",
        "model_fields": _extract_raw_model_fields(dicts),
    }


def _ensure_table_trace(result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    debug = result.get("table_debug")
    if not isinstance(debug, dict):
        return None
    trace = debug.get("table_recognition_trace")
    if isinstance(trace, dict):
        return trace
    trace = {}
    debug["table_recognition_trace"] = trace
    return trace


def _copy_slanext_trace(target: Dict[str, Any], source_trace: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not _table_debug_trace_enabled() or not isinstance(source_trace, dict):
        return target
    debug = target.get("table_debug")
    if not isinstance(debug, dict):
        debug = {}
    debug.setdefault("table_recognition_trace", source_trace)
    target["table_debug"] = debug
    return target


def _set_final_table_trace(result: Dict[str, Any]) -> Dict[str, Any]:
    if not _table_debug_trace_enabled():
        return result
    trace = _ensure_table_trace(result)
    if isinstance(trace, dict):
        debug = result.get("table_debug") if isinstance(result.get("table_debug"), dict) else {}
        trace["final"] = {
            "table_selected_method": result.get("table_selected_method"),
            "table_rows": _json_safe(result.get("table_rows")),
            "table_structured": _json_safe(result.get("table_structured")),
            "semi_skipped_reason": debug.get("semi_skipped_reason", "not_available"),
        }
    return result


def _common_model_kwargs() -> Dict[str, Any]:
    return {
        "device": _TABLE_DEVICE,
        "enable_mkldnn": False,
        "enable_cinn": False,
        "use_tensorrt": False,
    }


def _load_table_model() -> Any:
    global _TABLE_MODEL, _TABLE_MODEL_KIND
    if _TABLE_MODEL is not None:
        logger.info("Reusing cached TableRecognitionPipelineV2 (device=%s)", _TABLE_DEVICE)
        return _TABLE_MODEL

    try:
        from paddleocr import TableRecognitionPipelineV2  # type: ignore
    except ImportError as import_error:
        raise TableRecognitionV2UnavailableError(
            "table_recognition_v2 requires paddleocr 3.x with TableRecognitionPipelineV2 installed."
        ) from import_error

    try:
        logger.info("Loading TableRecognitionPipelineV2 (device=%s)", _TABLE_DEVICE)
        _TABLE_MODEL = TableRecognitionPipelineV2(
            wired_table_structure_recognition_model_name=_TABLE_WIRED_MODEL_NAME,
            wireless_table_structure_recognition_model_name=_TABLE_WIRELESS_MODEL_NAME,
            text_recognition_model_name=_TABLE_TEXT_RECOGNITION_MODEL_NAME,
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_layout_detection=False,
            use_ocr_model=True,
            **_common_model_kwargs(),
        )
        _TABLE_MODEL_KIND = "pipeline_v2"
        return _TABLE_MODEL
    except Exception as init_error:
        raise TableRecognitionV2UnavailableError(
            f"Failed to initialize PaddleOCR table_recognition_v2 model {_TABLE_MODEL_NAME}: {init_error}"
        ) from init_error


def table_recognition_runtime_summary() -> Dict[str, Any]:
    _load_table_model()
    return {
        "enabled": True,
        "structure_model": _TABLE_MODEL_NAME,
        "wired_structure_model": _TABLE_WIRED_MODEL_NAME,
        "wireless_structure_model": _TABLE_WIRELESS_MODEL_NAME,
        "text_recognition_model": _TABLE_TEXT_RECOGNITION_MODEL_NAME,
        "device": _TABLE_DEVICE,
    }


def _as_dict(value: Any) -> Optional[Dict[str, Any]]:
    if isinstance(value, dict):
        return value
    json_value = getattr(value, "json", None)
    if isinstance(json_value, dict):
        return json_value
    if callable(json_value):
        try:
            resolved = json_value()
            if isinstance(resolved, dict):
                return resolved
        except Exception:
            return None
    res_value = getattr(value, "res", None)
    if isinstance(res_value, dict):
        return res_value
    return None


def _collect_dicts(value: Any) -> List[Dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, dict):
        nested: List[Dict[str, Any]] = [value]
        for item in value.values():
            nested.extend(_collect_dicts(item))
        return nested
    if isinstance(value, (list, tuple)):
        rows: List[Dict[str, Any]] = []
        for item in value:
            rows.extend(_collect_dicts(item))
        return rows
    item = _as_dict(value)
    return [item] if item else []


def _extract_html(result: Dict[str, Any]) -> str:
    for key in ("html", "pred_html", "table_html", "structure_html"):
        value = result.get(key)
        if isinstance(value, str) and "<table" in value.lower():
            return value
    structure = result.get("structure")
    if isinstance(structure, list) and structure:
        value = "".join(str(item) for item in structure)
        if "<table" in value.lower():
            return value
    return ""


class _TableHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: List[List[str]] = []
        self.cells: List[Dict[str, Any]] = []
        self._current_row: Optional[List[str]] = None
        self._current_cell: Optional[List[str]] = None
        self._cell_colspan = 1
        self._cell_rowspan = 1
        self._current_row_index = -1
        self._current_col_index = 0
        self._occupied: set[str] = set()

    def handle_starttag(self, tag: str, attrs: List[tuple[str, Optional[str]]]) -> None:
        tag_name = tag.lower()
        if tag_name == "tr":
            self._current_row = []
            self._current_row_index += 1
            self._current_col_index = 0
        if tag_name in {"td", "th"} and self._current_row is not None:
            while f"{self._current_row_index}:{self._current_col_index}" in self._occupied:
                self._current_row.append("")
                self._current_col_index += 1
            self._current_cell = []
            attrs_map = {key.lower(): value for key, value in attrs}
            try:
                self._cell_colspan = max(1, int(attrs_map.get("colspan") or 1))
            except (TypeError, ValueError):
                self._cell_colspan = 1
            try:
                self._cell_rowspan = max(1, int(attrs_map.get("rowspan") or 1))
            except (TypeError, ValueError):
                self._cell_rowspan = 1

    def handle_data(self, data: str) -> None:
        if self._current_cell is not None:
            self._current_cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag_name = tag.lower()
        if tag_name in {"td", "th"} and self._current_row is not None and self._current_cell is not None:
            text = " ".join("".join(self._current_cell).split())
            row_index = max(0, self._current_row_index)
            col_index = self._current_col_index
            self._current_row.append(text)
            for _ in range(self._cell_colspan - 1):
                self._current_row.append("")
            self.cells.append(
                {
                    "row": row_index,
                    "col": col_index,
                    "text": text,
                    "rowSpan": self._cell_rowspan,
                    "colSpan": self._cell_colspan,
                    "ocrText": text,
                    "groundTruth": text,
                }
            )
            for row_offset in range(self._cell_rowspan):
                for col_offset in range(self._cell_colspan):
                    self._occupied.add(f"{row_index + row_offset}:{col_index + col_offset}")
                    if row_offset != 0 or col_offset != 0:
                        self.cells.append(
                            {
                                "row": row_index + row_offset,
                                "col": col_index + col_offset,
                                "text": "",
                                "rowSpan": 1,
                                "colSpan": 1,
                                "ocrText": "",
                                "groundTruth": "",
                                "hidden": True,
                            }
                        )
            self._current_col_index += self._cell_colspan
            self._current_cell = None
            self._cell_colspan = 1
            self._cell_rowspan = 1
        if tag_name == "tr" and self._current_row is not None:
            self.rows.append(self._current_row)
            self._current_row = None


def _rows_from_html(html: str) -> List[List[str]]:
    if not html:
        return []
    bs4_result = parse_table_html_with_bs4(html)
    if bs4_result:
        return bs4_result.get("rows") or []
    parser = _TableHtmlParser()
    try:
        parser.feed(html)
    except Exception:
        return []
    return parser.rows


def _rows_from_structured_cells_preserve_grid(cells: Any) -> List[List[str]]:
    normalized_cells = _normalize_cell_dicts(cells)
    if not normalized_cells:
        return []
    max_row = 0
    max_col = 0
    for cell in normalized_cells:
        row = int(cell.get("row") or 0)
        col = int(cell.get("col") or 0)
        row_span = max(1, int(cell.get("rowSpan") or cell.get("rowspan") or cell.get("row_span") or 1))
        col_span = max(1, int(cell.get("colSpan") or cell.get("colspan") or cell.get("col_span") or 1))
        max_row = max(max_row, row + row_span - 1)
        max_col = max(max_col, col + col_span - 1)
    rows = [["" for _ in range(max_col + 1)] for _ in range(max_row + 1)]
    for cell in normalized_cells:
        if cell.get("hidden"):
            continue
        row = int(cell.get("row") or 0)
        col = int(cell.get("col") or 0)
        if cell.get("groundTruth") is not None:
            rows[row][col] = normalize_ocr_text(cell.get("groundTruth"), cleanup_noise=False)
        else:
            rows[row][col] = normalize_ocr_text(cell.get("text") or cell.get("ocrText") or "")
    return normalize_table_rows(rows)


def _row_grid_shape(rows: List[List[Any]]) -> tuple[int, int]:
    if not rows:
        return (0, 0)
    return (len(rows), max((len(row) for row in rows if isinstance(row, list)), default=0))


def _prefer_larger_grid_rows(primary: List[List[str]], candidate: List[List[str]]) -> List[List[str]]:
    primary_shape = _row_grid_shape(primary)
    candidate_shape = _row_grid_shape(candidate)
    if candidate_shape[0] > primary_shape[0] or candidate_shape[1] > primary_shape[1]:
        return candidate
    return primary


def _structured_from_html(html: str) -> Optional[Dict[str, Any]]:
    if not html:
        return None
    bs4_result = parse_table_html_with_bs4(html)
    if bs4_result:
        return {
            "rows": bs4_result["rows"],
            "cells": bs4_result["cells"],
            "headerRowCount": bs4_result.get("headerRowCount", 1),
            "postProcessing": bs4_result.get("parser"),
        }
    parser = _TableHtmlParser()
    try:
        parser.feed(html)
    except Exception:
        return None
    rows = parser.rows
    if not rows:
        return None
    max_columns = max((len(row) for row in rows), default=0)
    normalized_rows = [row + [""] * (max_columns - len(row)) for row in rows]
    header_row_count = 1
    return {
        "rows": normalized_rows,
        "cells": parser.cells or _cells_from_rows(normalized_rows),
        "headerRowCount": header_row_count,
    }


def _cells_from_rows(rows: List[List[str]], source_cells: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    source_by_position = {
        (int(cell.get("row", 0)), int(cell.get("col", 0))): cell
        for cell in source_cells or []
        if isinstance(cell, dict)
    }
    cells: List[Dict[str, Any]] = []
    for row_index, row in enumerate(rows):
        for col_index, text in enumerate(row):
            source = source_by_position.get((row_index, col_index), {})
            bbox = source.get("bbox") or source.get("box")
            cell: Dict[str, Any] = {
                "row": row_index,
                "col": col_index,
                "text": normalize_ocr_text(text),
                "rowSpan": int(source.get("rowSpan") or source.get("rowspan") or source.get("row_span") or 1),
                "colSpan": int(source.get("colSpan") or source.get("colspan") or source.get("col_span") or 1),
                "ocrText": normalize_ocr_text(source.get("ocrText") or source.get("ocr_text") or source.get("text") or text or ""),
                "groundTruth": normalize_ocr_text(text),
            }
            if bbox is not None:
                cell["bbox"] = bbox
            cells.append(cell)
    return cells


def _structured_from_rows(rows: List[List[str]], source_cells: Optional[List[Dict[str, Any]]] = None) -> Optional[Dict[str, Any]]:
    if not rows:
        return None
    max_columns = max((len(row) for row in rows), default=0)
    normalized_rows = [row + [""] * (max_columns - len(row)) for row in rows]
    return {
        "rows": normalized_rows,
        "cells": _cells_from_rows(normalized_rows, source_cells),
        "headerRowCount": 1,
    }


def _table_shape(rows: List[List[str]]) -> tuple[int, int]:
    if not rows:
        return (0, 0)
    return (len(rows), max((len(row) for row in rows), default=0))


def _has_usable_table_shape(rows: List[List[str]]) -> bool:
    row_count, column_count = _table_shape(rows)
    non_empty_rows = sum(1 for row in rows if any(str(cell).strip() for cell in row))
    return row_count >= _BORDERLESS_MIN_ROWS and column_count >= _BORDERLESS_MIN_COLUMNS and non_empty_rows >= _BORDERLESS_MIN_ROWS


def _region_bbox(region: Dict[str, Any], scale_factor: float = 1.0) -> Optional[Dict[str, float]]:
    bbox = region.get("bbox") if isinstance(region, dict) else None
    if not isinstance(bbox, dict):
        return None
    try:
        x = float(bbox.get("x") or 0) / scale_factor
        y = float(bbox.get("y") or 0) / scale_factor
        width = float(bbox.get("width") or 0) / scale_factor
        height = float(bbox.get("height") or 0) / scale_factor
    except (TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    return {"x": x, "y": y, "width": width, "height": height}


def _recognize_text_crops_with_core(crops: List[np.ndarray], status_prefix: str) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    if not crops:
        return ([], {"status": f"{status_prefix}_no_crops", "ocr_core": "recognize_text_roi", "crop_count": 0})
    try:
        recognitions = run_paddle_thai_ocr_batch(crops)
    except Exception as error:
        logger.info("%s OCR core failed: %s", status_prefix, error)
        return (
            [{"text": "", "confidence": 0.0, "error": str(error)} for _ in crops],
            {"status": f"{status_prefix}_ocr_core_failed", "reason": str(error), "ocr_core": "recognize_text_roi", "crop_count": len(crops)},
        )
    return (
        recognitions,
        {
            "status": status_prefix,
            "ocr_core": "recognize_text_roi",
            "crop_count": len(crops),
            "failure_count": sum(1 for item in recognitions if isinstance(item, dict) and item.get("error")),
        },
    )


def run_paddle_thai_ocr_batch(crops: List[np.ndarray]) -> List[Dict[str, Any]]:
    """Compatibility seam for tests; production routes table text crops through the shared OCR core."""
    try:
        from .ocr_adapter import recognize_text_roi
    except Exception as error:
        return [{"text": "", "confidence": 0.0, "error": str(error)} for _ in crops]

    recognitions: List[Dict[str, Any]] = []
    for crop in crops:
        try:
            result = recognize_text_roi(crop)
            recognitions.append(result if isinstance(result, dict) else {"text": "", "confidence": 0.0})
        except Exception as error:
            recognitions.append({"text": "", "confidence": 0.0, "error": str(error)})
    return recognitions


def _merge_bboxes(boxes: List[Dict[str, float]]) -> Optional[Dict[str, float]]:
    if not boxes:
        return None
    left = min(box["x"] for box in boxes)
    top = min(box["y"] for box in boxes)
    right = max(box["x"] + box["width"] for box in boxes)
    bottom = max(box["y"] + box["height"] for box in boxes)
    return {"x": left, "y": top, "width": max(1.0, right - left), "height": max(1.0, bottom - top)}


def _cluster_positions(values: List[float], tolerance: float) -> List[float]:
    if not values:
        return []
    groups: List[List[float]] = []
    for value in sorted(values):
        if not groups or abs(value - groups[-1][-1]) > tolerance:
            groups.append([value])
        else:
            groups[-1].append(value)
    return [sum(group) / len(group) for group in groups]


def _line_positions_from_mask(mask: np.ndarray, orientation: str, threshold_ratio: float) -> List[float]:
    if mask.size == 0:
        return []
    axis = 0 if orientation == "vertical" else 1
    length = mask.shape[0] if orientation == "vertical" else mask.shape[1]
    projection = np.sum(mask > 0, axis=axis) / max(1, length)
    positions = [float(index) for index, value in enumerate(projection) if value >= threshold_ratio]
    tolerance = max(2.0, min(mask.shape[:2]) * 0.006)
    return _cluster_positions(positions, tolerance)


def _line_boundaries(line_positions: List[float], limit: int) -> List[float]:
    if len(line_positions) >= 2:
        return sorted(set(round(max(0.0, min(float(limit), value)), 3) for value in line_positions))
    return [0.0, float(limit)]


def _semi_line_masks(image: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 else image
    binary = cv2.adaptiveThreshold(
        gray,
        255,
        cv2.ADAPTIVE_THRESH_MEAN_C,
        cv2.THRESH_BINARY_INV,
        31,
        12,
    )
    height, width = gray.shape[:2]
    horizontal_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(8, width // 30), 1))
    vertical_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(8, height // 30)))
    horizontal = cv2.morphologyEx(binary, cv2.MORPH_OPEN, horizontal_kernel, iterations=1)
    vertical = cv2.morphologyEx(binary, cv2.MORPH_OPEN, vertical_kernel, iterations=1)
    return (
        cv2.dilate(horizontal, horizontal_kernel, iterations=1),
        cv2.dilate(vertical, vertical_kernel, iterations=1),
    )


def _infer_boundaries_from_text(cells: List[Dict[str, Any]], axis: str, limit: int) -> List[float]:
    if not cells:
        return [0.0, float(limit)]
    centers = [float(cell["center_x" if axis == "x" else "center_y"]) for cell in cells]
    sizes = [float(cell["width" if axis == "x" else "height"]) for cell in cells]
    tolerance = max(6.0, (float(np.median(sizes)) if sizes else 12.0) * 0.8)
    clusters = _cluster_positions(centers, tolerance)
    if not clusters:
        return [0.0, float(limit)]
    edges = [0.0]
    for left, right in zip(clusters, clusters[1:]):
        edges.append((left + right) / 2.0)
    edges.append(float(limit))
    return sorted(set(max(0.0, min(float(limit), edge)) for edge in edges))


def _combined_boundaries(
    line_positions: List[float],
    text_cells: List[Dict[str, Any]],
    axis: str,
    limit: int,
) -> List[float]:
    text_boundaries = _infer_boundaries_from_text(text_cells, axis, limit)
    min_gap = max(4.0, float(limit) * 0.012)
    filtered_line_positions = [pos for pos in line_positions if min_gap <= pos <= float(limit) - min_gap]
    line_boundaries = _line_boundaries(filtered_line_positions, limit)
    if len(line_boundaries) >= 3 and len(line_boundaries) >= max(3, int(len(text_boundaries) * 0.6)):
        return sorted(set(round(value, 3) for value in line_boundaries))
    return sorted(set(round(value, 3) for value in text_boundaries))


def _has_near_position(values: List[float], target: float, tolerance: float) -> bool:
    return any(abs(float(value) - float(target)) <= tolerance for value in values)


def _infer_repeated_alignment_lines(cells: List[Dict[str, Any]], axis: str, limit: int) -> List[float]:
    if len(cells) < 4:
        return []
    size_key = "width" if axis == "x" else "height"
    center_key = "center_x" if axis == "x" else "center_y"
    sizes = [float(cell.get(size_key) or 0.0) for cell in cells if float(cell.get(size_key) or 0.0) > 0]
    median_size = float(np.median(sizes)) if sizes else max(8.0, float(limit) * 0.02)
    tolerance = max(6.0, median_size * 0.75)
    centers = [float(cell.get(center_key) or 0.0) for cell in cells]
    clusters = _cluster_positions(centers, tolerance)
    if len(clusters) < 2:
        return []

    inferred: List[float] = []
    min_support = 2
    for left, right in zip(clusters, clusters[1:]):
        left_support = sum(1 for cell in cells if abs(float(cell.get(center_key) or 0.0) - left) <= tolerance)
        right_support = sum(1 for cell in cells if abs(float(cell.get(center_key) or 0.0) - right) <= tolerance)
        if left_support >= min_support and right_support >= min_support:
            boundary = (left + right) / 2.0
            if tolerance <= boundary <= float(limit) - tolerance:
                inferred.append(boundary)
    return inferred


def _draw_synthetic_grid_lines(image: np.ndarray, horizontal_lines: List[float], vertical_lines: List[float]) -> np.ndarray:
    normalized = image.copy()
    height, width = normalized.shape[:2]
    color = (0, 0, 0) if len(normalized.shape) == 3 else 0
    thickness = max(1, int(round(min(width, height) * 0.0025)))
    for y_value in horizontal_lines:
        y = int(round(max(0, min(height - 1, y_value))))
        cv2.line(normalized, (0, y), (width - 1, y), color, thickness=thickness)
    for x_value in vertical_lines:
        x = int(round(max(0, min(width - 1, x_value))))
        cv2.line(normalized, (x, 0), (x, height - 1), color, thickness=thickness)
    return normalized


def _normalize_semi_table_grid(image: np.ndarray, analysis: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    phase_started = time.perf_counter()
    height, width = image.shape[:2]
    horizontal_mask, vertical_mask = _semi_line_masks(image)
    hard_horizontal = _line_positions_from_mask(horizontal_mask, "horizontal", 0.18)
    hard_vertical = _line_positions_from_mask(vertical_mask, "vertical", 0.15)
    try:
        ocr_cells, _, ocr_debug = _ocr_cells_from_text_detection(image, "grid_normalizer")
    except Exception as error:
        return {"ok": False, "reason": f"ocr_geometry_failed:{error}"}
    if len(ocr_cells) < 4:
        return {"ok": False, "reason": "insufficient_ocr_alignment", "ocr_debug": ocr_debug}

    inferred_horizontal = _infer_repeated_alignment_lines(ocr_cells, "y", height)
    inferred_vertical = _infer_repeated_alignment_lines(ocr_cells, "x", width)
    line_tolerance = max(4.0, min(width, height) * 0.01)
    missing_horizontal = [line for line in inferred_horizontal if not _has_near_position(hard_horizontal, line, line_tolerance)]
    missing_vertical = [line for line in inferred_vertical if not _has_near_position(hard_vertical, line, line_tolerance)]
    if len(missing_horizontal) + len(missing_vertical) < 1:
        return {
            "ok": False,
            "reason": "no_confident_missing_lines",
            "ocr_debug": ocr_debug,
            "hard_lines": {"horizontal": len(hard_horizontal), "vertical": len(hard_vertical)},
        }

    normalized_image = _draw_synthetic_grid_lines(image, missing_horizontal, missing_vertical)
    confidence = min(0.92, max(float(analysis.get("confidence") or 0.0), 0.72) + min(0.18, 0.03 * (len(missing_horizontal) + len(missing_vertical))))
    return {
        "ok": True,
        "image": normalized_image,
        "debug": {
            "status": "grid_normalized",
            "role": "grid_normalizer",
            "synthetic_grid": True,
            "confidence": round(confidence, 4),
            "hard_lines": {"horizontal": len(hard_horizontal), "vertical": len(hard_vertical)},
            "inferred_lines": {"horizontal": len(inferred_horizontal), "vertical": len(inferred_vertical)},
            "drawn_lines": {"horizontal": len(missing_horizontal), "vertical": len(missing_vertical)},
            "ocr_box_count": len(ocr_cells),
            "ocr_debug": ocr_debug,
            "elapsed_seconds": round(time.perf_counter() - phase_started, 3),
        },
    }


def _row_boundaries_with_logical_subrows(
    hard_boundaries: List[float],
    text_cells: List[Dict[str, Any]],
    image_height: int,
) -> List[float]:
    if not text_cells:
        return hard_boundaries if len(hard_boundaries) >= 2 else [0.0, float(image_height)]
    median_height = float(np.median([cell.get("height", 12.0) for cell in text_cells])) if text_cells else 12.0
    y_tolerance = max(6.0, median_height * 0.65)
    min_gap = max(3.0, median_height * 0.45)
    source_boundaries = hard_boundaries if len(hard_boundaries) >= 2 else [0.0, float(image_height)]
    boundaries: List[float] = [float(source_boundaries[0])]
    for band_top, band_bottom in zip(source_boundaries, source_boundaries[1:]):
        band_cells = sorted([
            cell
            for cell in text_cells
            if float(band_top) <= float(cell["center_y"]) <= float(band_bottom)
        ], key=lambda item: float(item["center_y"]))
        center_groups: List[List[Dict[str, Any]]] = []
        for cell in band_cells:
            cell_top = float(cell.get("y") or 0.0)
            cell_bottom = cell_top + float(cell.get("height") or 0.0)
            if not center_groups:
                center_groups.append([cell])
                continue
            previous = center_groups[-1]
            prev_top = min(float(item.get("y") or 0.0) for item in previous)
            prev_bottom = max(float(item.get("y") or 0.0) + float(item.get("height") or 0.0) for item in previous)
            overlap = _interval_overlap(cell_top, cell_bottom, prev_top, prev_bottom)
            smaller_height = max(1.0, min(cell_bottom - cell_top, prev_bottom - prev_top))
            center_gap = abs(float(cell["center_y"]) - sum(float(item["center_y"]) for item in previous) / len(previous))
            if center_gap <= y_tolerance and overlap / smaller_height >= 0.28:
                previous.append(cell)
            else:
                center_groups.append([cell])
        centers = [
            sum(float(cell["center_y"]) for cell in group) / len(group)
            for group in center_groups
        ]
        inner_boundaries: List[float] = []
        if len(centers) > 1:
            for upper, lower in zip(centers, centers[1:]):
                boundary = (upper + lower) / 2.0
                if boundary - float(band_top) >= min_gap and float(band_bottom) - boundary >= min_gap:
                    inner_boundaries.append(boundary)
        boundaries.extend(inner_boundaries)
        boundaries.append(float(band_bottom))
    merged = _cluster_positions(boundaries, max(2.0, min_gap * 0.5))
    return sorted(set(round(max(0.0, min(float(image_height), value)), 3) for value in merged))


def _column_boundaries_with_logical_subcolumns(
    hard_boundaries: List[float],
    text_cells: List[Dict[str, Any]],
    row_boundaries: List[float],
    image_width: int,
) -> List[float]:
    if not text_cells:
        return hard_boundaries if len(hard_boundaries) >= 2 else [0.0, float(image_width)]
    median_width = float(np.median([cell.get("width", 40.0) for cell in text_cells])) if text_cells else 40.0
    source_boundaries = hard_boundaries if len(hard_boundaries) >= 2 else [0.0, float(image_width)]
    min_gap = max(6.0, median_width * 0.75)
    final_boundaries: List[float] = [float(source_boundaries[0])]

    for band_left, band_right in zip(source_boundaries, source_boundaries[1:]):
        band_width = max(1.0, float(band_right) - float(band_left))
        band_cells = [
            cell
            for cell in text_cells
            if float(band_left) <= float(cell["center_x"]) <= float(band_right)
        ]
        proposals: List[tuple[float, int]] = []
        row_center_values: Dict[int, List[float]] = {}
        for row_index in range(max(1, len(row_boundaries) - 1)):
            row_top = row_boundaries[row_index]
            row_bottom = row_boundaries[row_index + 1]
            row_cells = sorted(
                [cell for cell in band_cells if float(row_top) <= float(cell["center_y"]) <= float(row_bottom)],
                key=lambda item: float(item["center_x"]),
            )
            centers = [float(cell["center_x"]) for cell in row_cells]
            if centers:
                row_center_values[row_index] = centers
            for left, right in zip(row_cells, row_cells[1:]):
                left_edge = float(left.get("x") or 0.0) + float(left.get("width") or 0.0)
                right_edge = float(right.get("x") or 0.0)
                gap = right_edge - left_edge
                edge_gap_threshold = max(6.0, median_width * 0.45, band_width * 0.045)
                if gap >= edge_gap_threshold:
                    proposals.append(((left_edge + right_edge) / 2.0, row_index))

        centers = [center for values in row_center_values.values() for center in values]
        center_clusters = _cluster_positions(centers, max(median_width * 0.55, band_width * 0.035))
        repeated_center_count = 0
        for center in center_clusters:
            supporting_rows = {
                row_index
                for row_index, values in row_center_values.items()
                if any(abs(value - center) <= max(median_width * 0.55, band_width * 0.035) for value in values)
            }
            if len(supporting_rows) >= 2:
                repeated_center_count += 1

        inferred: List[float] = []
        proposal_tolerance = max(median_width * 0.65, band_width * 0.035)
        proposal_centers = _cluster_positions([proposal for proposal, _ in proposals], proposal_tolerance)
        for proposal_center in proposal_centers:
            supporting_rows = {row_index for proposal, row_index in proposals if abs(proposal - proposal_center) <= proposal_tolerance}
            spanning_rows = {
                row_index
                for row_index in range(max(1, len(row_boundaries) - 1))
                for cell in band_cells
                if float(row_boundaries[row_index]) <= float(cell["center_y"]) <= float(row_boundaries[row_index + 1])
                and _text_overlaps_boundary(cell, proposal_center, "x", max(median_width * 0.35, band_width * 0.02))
                and float(cell.get("width") or 0.0) >= median_width * 1.35
            }
            has_repeated_pattern = (
                len(supporting_rows) >= 2
                or (len(supporting_rows) >= 1 and repeated_center_count >= 1)
                or len(supporting_rows | spanning_rows) >= 2
            )
            if has_repeated_pattern and float(band_left) + min_gap <= proposal_center <= float(band_right) - min_gap:
                inferred.append(proposal_center)

        final_boundaries.extend(inferred)
        final_boundaries.append(float(band_right))

    merged = _cluster_positions(final_boundaries, max(2.0, median_width * 0.2))
    return sorted(set(round(max(0.0, min(float(image_width), value)), 3) for value in merged))


def _interval_index(center: float, boundaries: List[float]) -> int:
    if len(boundaries) <= 1:
        return 0
    for index, (start, end) in enumerate(zip(boundaries, boundaries[1:])):
        if start <= center <= end:
            return index
    return max(0, min(len(boundaries) - 2, min(range(len(boundaries) - 1), key=lambda idx: abs(((boundaries[idx] + boundaries[idx + 1]) / 2.0) - center))))


def _covered_column_indices(cell: Dict[str, Any], col_boundaries: List[float], tolerance: float) -> List[int]:
    bbox = cell.get("bbox")
    if not isinstance(bbox, dict):
        return []
    left = float(bbox.get("x") or 0.0)
    right = left + float(bbox.get("width") or 0.0)
    return [
        index
        for index in range(max(0, len(col_boundaries) - 1))
        if left <= ((col_boundaries[index] + col_boundaries[index + 1]) / 2.0) + tolerance
        and right >= ((col_boundaries[index] + col_boundaries[index + 1]) / 2.0) - tolerance
    ]


def _interval_overlap(start_a: float, end_a: float, start_b: float, end_b: float) -> float:
    return max(0.0, min(float(end_a), float(end_b)) - max(float(start_a), float(start_b)))


def _dominant_interval_index(
    start: float,
    end: float,
    boundaries: List[float],
    tolerance: float,
    candidate_indices: Optional[List[int]] = None,
) -> int:
    if len(boundaries) <= 1:
        return 0
    indices = candidate_indices if candidate_indices else list(range(len(boundaries) - 1))
    best_index = indices[0] if indices else 0
    best_score = float("-inf")
    center = (float(start) + float(end)) / 2.0
    span = max(1.0, float(end) - float(start))
    for index in indices:
        if index < 0 or index >= len(boundaries) - 1:
            continue
        left = float(boundaries[index])
        right = float(boundaries[index + 1])
        overlap = _interval_overlap(start, end, left, right)
        interval_center = (left + right) / 2.0
        alignment = max(0.0, 1.0 - abs(center - interval_center) / max(span, right - left, 1.0))
        score = (overlap / span) * 0.72 + alignment * 0.28
        if start < left - tolerance or end > right + tolerance:
            score -= 0.08
        if score > best_score:
            best_score = score
            best_index = index
    return max(0, min(len(boundaries) - 2, best_index))


def _hard_region_column_indices(
    cell: Dict[str, Any],
    col_boundaries: List[float],
    hard_col_boundaries: List[float],
    tolerance: float,
) -> List[int]:
    if len(col_boundaries) <= 1:
        return [0]
    if len(hard_col_boundaries) < 2:
        return list(range(len(col_boundaries) - 1))
    left = float(cell.get("x") or 0.0)
    right = left + float(cell.get("width") or 0.0)
    hard_index = _dominant_interval_index(left, right, hard_col_boundaries, tolerance)
    hard_left = hard_col_boundaries[hard_index]
    hard_right = hard_col_boundaries[hard_index + 1]
    candidates = [
        index
        for index in range(len(col_boundaries) - 1)
        if hard_left - tolerance <= (col_boundaries[index] + col_boundaries[index + 1]) / 2.0 <= hard_right + tolerance
    ]
    return candidates or list(range(len(col_boundaries) - 1))


def _assign_ocr_cell_to_grid(
    cell: Dict[str, Any],
    row_boundaries: List[float],
    col_boundaries: List[float],
    hard_col_boundaries: List[float],
    x_tolerance: float,
    y_tolerance: float,
    row_peer_count: int = 1,
) -> tuple[int, int]:
    left = float(cell.get("x") or 0.0)
    top = float(cell.get("y") or 0.0)
    right = left + float(cell.get("width") or 0.0)
    bottom = top + float(cell.get("height") or 0.0)
    row_index = _dominant_interval_index(top, bottom, row_boundaries, y_tolerance)
    col_candidates = _hard_region_column_indices(cell, col_boundaries, hard_col_boundaries, x_tolerance)
    crossed_boundaries = [
        index
        for index in range(1, max(1, len(col_boundaries) - 1))
        if _text_overlaps_boundary(cell, col_boundaries[index], "x", x_tolerance)
        and (index - 1 in col_candidates or index in col_candidates)
    ]
    if row_index == 0 and row_peer_count <= 1 and crossed_boundaries:
        return (row_index, max(0, min(crossed_boundaries) - 1))
    col_index = _dominant_interval_index(left, right, col_boundaries, x_tolerance, col_candidates)
    return (row_index, col_index)


def _merge_assigned_ocr_cells(cells: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    grouped: Dict[tuple[int, int], List[Dict[str, Any]]] = {}
    for cell in cells:
        grouped.setdefault((int(cell["row"]), int(cell["col"])), []).append(cell)
    merged: List[Dict[str, Any]] = []
    for (row_index, col_index), group in grouped.items():
        ordered = sorted(group, key=lambda item: (float(item.get("y") or 0.0), float(item.get("x") or 0.0)))
        texts = [str(item.get("text") or "").strip() for item in ordered if str(item.get("text") or "").strip()]
        boxes = [item.get("bbox") for item in ordered if isinstance(item.get("bbox"), dict)]
        bbox = _merge_bboxes(boxes)
        confidence_values = [float(item.get("confidence") or 0.0) for item in ordered]
        base = dict(ordered[0])
        text = normalize_ocr_text(" ".join(texts))
        base.update({
            "row": row_index,
            "col": col_index,
            "text": text,
            "ocrText": text,
            "groundTruth": text,
            "confidence": sum(confidence_values) / len(confidence_values) if confidence_values else 0.0,
        })
        if bbox:
            base.update({
                "bbox": bbox,
                "x": bbox["x"],
                "y": bbox["y"],
                "width": bbox["width"],
                "height": bbox["height"],
                "center_x": bbox["x"] + bbox["width"] / 2,
                "center_y": bbox["y"] + bbox["height"] / 2,
            })
        merged.append(base)
    return sorted(merged, key=lambda item: (int(item["row"]), int(item["col"]), float(item.get("y") or 0.0), float(item.get("x") or 0.0)))


def _bbox_contains(inner: Optional[Dict[str, Any]], outer: Optional[Dict[str, Any]], tolerance: float = 2.0) -> bool:
    if not isinstance(inner, dict) or not isinstance(outer, dict):
        return False
    inner_left = float(inner.get("x") or 0.0)
    inner_top = float(inner.get("y") or 0.0)
    inner_right = inner_left + float(inner.get("width") or 0.0)
    inner_bottom = inner_top + float(inner.get("height") or 0.0)
    outer_left = float(outer.get("x") or 0.0)
    outer_top = float(outer.get("y") or 0.0)
    outer_right = outer_left + float(outer.get("width") or 0.0)
    outer_bottom = outer_top + float(outer.get("height") or 0.0)
    return (
        inner_left >= outer_left - tolerance
        and inner_top >= outer_top - tolerance
        and inner_right <= outer_right + tolerance
        and inner_bottom <= outer_bottom + tolerance
    )


def _deduplicate_assigned_table_cells(cells: List[Dict[str, Any]]) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    if not cells:
        return ([], {"removed": 0, "preferred_per_cell": 0})
    kept: List[Dict[str, Any]] = []
    removed = 0
    preferred_per_cell = 0
    for cell in sorted(
        cells,
        key=lambda item: (
            0 if item.get("assignment_source") == "per_cell_ocr" else 1,
            int(item.get("row") or 0),
            int(item.get("col") or 0),
            float(item.get("y") or 0.0),
            float(item.get("x") or 0.0),
        ),
    ):
        text = normalize_ocr_text(cell.get("text") or cell.get("ocrText") or "")
        if not text:
            continue
        duplicate_index: Optional[int] = None
        for index, existing in enumerate(kept):
            existing_text = normalize_ocr_text(existing.get("text") or existing.get("ocrText") or "")
            if existing_text != text:
                continue
            same_position = int(existing.get("row") or 0) == int(cell.get("row") or 0) and int(existing.get("col") or 0) == int(cell.get("col") or 0)
            existing_box = existing.get("bbox")
            cell_box = cell.get("bbox")
            per_cell_contains_existing = cell.get("assignment_source") == "per_cell_ocr" and _bbox_contains(existing_box, cell_box)
            existing_per_cell_contains_cell = existing.get("assignment_source") == "per_cell_ocr" and _bbox_contains(cell_box, existing_box)
            if same_position or per_cell_contains_existing or existing_per_cell_contains_cell:
                duplicate_index = index
                break
        if duplicate_index is None:
            kept.append(cell)
            continue
        existing = kept[duplicate_index]
        if cell.get("assignment_source") == "per_cell_ocr" and existing.get("assignment_source") != "per_cell_ocr":
            kept[duplicate_index] = cell
            preferred_per_cell += 1
        removed += 1
    return (
        sorted(kept, key=lambda item: (int(item.get("row") or 0), int(item.get("col") or 0), float(item.get("y") or 0.0), float(item.get("x") or 0.0))),
        {"removed": removed, "preferred_per_cell": preferred_per_cell},
    )


def _coordinate_assignment_diagnostics(
    rows: List[List[str]],
    source_cells: List[Dict[str, Any]],
    row_boundaries: List[float],
    col_boundaries: List[float],
    hard_col_boundaries: List[float],
) -> Dict[str, Any]:
    row_count = len(rows)
    col_count = max((len(row) for row in rows), default=0)
    row_non_empty = [sum(1 for value in row if str(value).strip()) for row in rows]
    col_non_empty = [
        sum(1 for row in rows if col_index < len(row) and str(row[col_index]).strip())
        for col_index in range(col_count)
    ]
    row_overlap_scores: List[float] = []
    col_overlap_scores: List[float] = []
    hard_region_violations = 0
    for cell in source_cells:
        bbox = cell.get("bbox")
        if not isinstance(bbox, dict):
            continue
        row_index = max(0, min(row_count - 1, int(cell.get("row") or 0))) if row_count else 0
        col_index = max(0, min(col_count - 1, int(cell.get("col") or 0))) if col_count else 0
        left = float(bbox.get("x") or 0.0)
        top = float(bbox.get("y") or 0.0)
        right = left + float(bbox.get("width") or 0.0)
        bottom = top + float(bbox.get("height") or 0.0)
        width = max(1.0, right - left)
        height = max(1.0, bottom - top)
        if row_index < len(row_boundaries) - 1:
            row_overlap_scores.append(_interval_overlap(top, bottom, row_boundaries[row_index], row_boundaries[row_index + 1]) / height)
        if col_index < len(col_boundaries) - 1:
            col_overlap_scores.append(_interval_overlap(left, right, col_boundaries[col_index], col_boundaries[col_index + 1]) / width)
        if len(hard_col_boundaries) >= 2 and col_index < len(col_boundaries) - 1:
            hard_index = _dominant_interval_index(left, right, hard_col_boundaries, 0.0)
            hard_left = hard_col_boundaries[hard_index]
            hard_right = hard_col_boundaries[hard_index + 1]
            assigned_center = (col_boundaries[col_index] + col_boundaries[col_index + 1]) / 2.0
            if not (hard_left <= assigned_center <= hard_right):
                hard_region_violations += 1

    empty_rows = sum(1 for count in row_non_empty if count == 0)
    empty_cols = sum(1 for count in col_non_empty if count == 0)
    avg_row_overlap = sum(row_overlap_scores) / len(row_overlap_scores) if row_overlap_scores else 0.0
    avg_col_overlap = sum(col_overlap_scores) / len(col_overlap_scores) if col_overlap_scores else 0.0
    min_row_overlap = min(row_overlap_scores) if row_overlap_scores else 0.0
    min_col_overlap = min(col_overlap_scores) if col_overlap_scores else 0.0
    return {
        "row_count": row_count,
        "column_count": col_count,
        "assigned_cell_count": len(source_cells),
        "row_non_empty_counts": row_non_empty,
        "column_non_empty_counts": col_non_empty,
        "empty_row_ratio": round(empty_rows / row_count, 4) if row_count else 1.0,
        "empty_column_ratio": round(empty_cols / col_count, 4) if col_count else 1.0,
        "average_row_overlap": round(_clamp01(avg_row_overlap), 4),
        "average_column_overlap": round(_clamp01(avg_col_overlap), 4),
        "minimum_row_overlap": round(_clamp01(min_row_overlap), 4),
        "minimum_column_overlap": round(_clamp01(min_col_overlap), 4),
        "hard_region_violation_count": hard_region_violations,
    }


def _compact_empty_inferred_columns(
    rows: List[List[str]],
    source_cells: List[Dict[str, Any]],
    col_boundaries: List[float],
    hard_col_boundaries: List[float],
) -> tuple[List[List[str]], List[Dict[str, Any]], List[float], Dict[str, Any]]:
    if len(col_boundaries) <= 2 or not rows:
        return (rows, source_cells, col_boundaries, {"enabled": False, "removed_columns": 0, "reason": "not_needed"})
    col_count = max((len(row) for row in rows), default=0)
    if col_count <= 1:
        return (rows, source_cells, col_boundaries, {"enabled": False, "removed_columns": 0, "reason": "single_column"})
    hard_tolerance = max(2.0, (col_boundaries[-1] - col_boundaries[0]) * 0.004)

    def is_hard_boundary(value: float) -> bool:
        return any(abs(float(value) - float(hard)) <= hard_tolerance for hard in hard_col_boundaries)

    keep_columns: List[int] = []
    for col_index in range(col_count):
        has_text = any(col_index < len(row) and str(row[col_index]).strip() for row in rows)
        hard_interval = (
            col_index < len(col_boundaries) - 1
            and is_hard_boundary(col_boundaries[col_index])
            and is_hard_boundary(col_boundaries[col_index + 1])
        )
        if has_text or hard_interval:
            keep_columns.append(col_index)
    if len(keep_columns) == col_count or not keep_columns:
        return (rows, source_cells, col_boundaries, {"enabled": False, "removed_columns": 0, "reason": "no_empty_inferred_columns"})

    remap = {old_col: new_col for new_col, old_col in enumerate(keep_columns)}
    compacted_rows = [
        [row[col_index] if col_index < len(row) else "" for col_index in keep_columns]
        for row in rows
    ]
    compacted_cells: List[Dict[str, Any]] = []
    for cell in source_cells:
        old_col = int(cell.get("col") or 0)
        if old_col not in remap:
            continue
        next_cell = dict(cell)
        next_cell["col"] = remap[old_col]
        compacted_cells.append(next_cell)

    compacted_boundaries = [float(col_boundaries[0])]
    for old_col in keep_columns[1:]:
        compacted_boundaries.append(float(col_boundaries[old_col]))
    compacted_boundaries.append(float(col_boundaries[-1]))
    compacted_boundaries = sorted(set(round(value, 3) for value in compacted_boundaries))
    if len(compacted_boundaries) != len(keep_columns) + 1:
        compacted_boundaries = [float(col_boundaries[0])]
        for old_col in keep_columns:
            compacted_boundaries.append(float(col_boundaries[old_col + 1]))
        compacted_boundaries = sorted(set(round(value, 3) for value in compacted_boundaries))
    return (
        compacted_rows,
        compacted_cells,
        compacted_boundaries,
        {
            "enabled": True,
            "removed_columns": col_count - len(keep_columns),
            "kept_columns": keep_columns,
            "reason": "empty_inferred_columns_removed",
        },
    )


def _line_evidence(mask: np.ndarray, orientation: str, position: float, start: float, end: float) -> float:
    height, width = mask.shape[:2]
    if orientation == "vertical":
        x = max(0, min(width - 1, int(round(position))))
        top = max(0, min(height - 1, int(round(start))))
        bottom = max(top + 1, min(height, int(round(end))))
        band = mask[top:bottom, max(0, x - 1):min(width, x + 2)]
        return float(np.mean(band > 0)) if band.size else 0.0
    y = max(0, min(height - 1, int(round(position))))
    left = max(0, min(width - 1, int(round(start))))
    right = max(left + 1, min(width, int(round(end))))
    band = mask[max(0, y - 1):min(height, y + 2), left:right]
    return float(np.mean(band > 0)) if band.size else 0.0


def _text_overlaps_boundary(cell: Dict[str, Any], boundary: float, axis: str, tolerance: float) -> bool:
    if axis == "x":
        return float(cell["x"]) - tolerance <= boundary <= float(cell["x"] + cell["width"]) + tolerance
    return float(cell["y"]) - tolerance <= boundary <= float(cell["y"] + cell["height"]) + tolerance


def _structured_from_coordinate_grid(
    rows: List[List[str]],
    source_cells: List[Dict[str, Any]],
    row_boundaries: List[float],
    col_boundaries: List[float],
    horizontal_mask: np.ndarray,
    vertical_mask: np.ndarray,
    hard_col_boundaries: Optional[List[float]] = None,
) -> Dict[str, Any]:
    row_count = len(rows)
    col_count = max((len(row) for row in rows), default=0)
    source_by_position: Dict[tuple[int, int], List[Dict[str, Any]]] = {}
    for cell in source_cells:
        source_by_position.setdefault((int(cell["row"]), int(cell["col"])), []).append(cell)

    cells: List[Dict[str, Any]] = []
    hidden_positions: set[tuple[int, int]] = set()
    median_height = float(np.median([cell.get("height", 12.0) for cell in source_cells])) if source_cells else 12.0
    median_width = float(np.median([cell.get("width", 40.0) for cell in source_cells])) if source_cells else 40.0
    x_tolerance = max(4.0, median_width * 0.12)
    y_tolerance = max(4.0, median_height * 0.35)

    def span_from_source_boxes(position_cells: List[Dict[str, Any]], start_col: int) -> int:
        covered: List[int] = []
        for source_cell in position_cells:
            covered.extend(_covered_column_indices(source_cell, col_boundaries, x_tolerance))
        if not covered:
            return 1
        unique_covered = sorted(set(covered))
        if len(unique_covered) <= 1:
            return 1
        if min(unique_covered) != start_col:
            return 1
        return max(1, max(unique_covered) - start_col + 1)

    for row_index in range(row_count):
        for col_index in range(col_count):
            if (row_index, col_index) in hidden_positions:
                continue
            texts = [cell["text"] for cell in source_by_position.get((row_index, col_index), []) if str(cell.get("text") or "").strip()]
            text = normalize_ocr_text(" ".join(texts))
            boxes = [cell["bbox"] for cell in source_by_position.get((row_index, col_index), []) if isinstance(cell.get("bbox"), dict)]
            bbox = _merge_bboxes(boxes)
            row_span = 1
            anchor_sources = source_by_position.get((row_index, col_index), [])
            col_span = span_from_source_boxes(anchor_sources, col_index) if text else 1
            for hidden_col in range(col_index + 1, min(col_count, col_index + col_span)):
                hidden_positions.add((row_index, hidden_col))

            while col_index + col_span < col_count and text and anchor_sources:
                boundary = col_boundaries[col_index + col_span]
                evidence = _line_evidence(vertical_mask, "vertical", boundary, row_boundaries[row_index], row_boundaries[row_index + row_span])
                crosses = any(_text_overlaps_boundary(cell, boundary, "x", x_tolerance) for cell in anchor_sources)
                next_empty = not any(str(item.get("text") or "").strip() for item in source_by_position.get((row_index, col_index + col_span), []))
                if evidence >= 0.12 or not (crosses or next_empty):
                    break
                hidden_positions.add((row_index, col_index + col_span))
                col_span += 1

            while row_index + row_span < row_count and text and anchor_sources:
                boundary = row_boundaries[row_index + row_span]
                evidence = _line_evidence(horizontal_mask, "horizontal", boundary, col_boundaries[col_index], col_boundaries[min(col_count, col_index + col_span)])
                crosses = any(_text_overlaps_boundary(cell, boundary, "y", y_tolerance) for cell in anchor_sources)
                next_empty = all(
                    not any(str(item.get("text") or "").strip() for item in source_by_position.get((row_index + row_span, next_col), []))
                    for next_col in range(col_index, min(col_count, col_index + col_span))
                )
                if evidence >= 0.12 or not (crosses or next_empty):
                    break
                for next_col in range(col_index, min(col_count, col_index + col_span)):
                    hidden_positions.add((row_index + row_span, next_col))
                row_span += 1

            cell: Dict[str, Any] = {
                "row": row_index,
                "col": col_index,
                "text": text,
                "rowSpan": row_span,
                "colSpan": col_span,
                "ocrText": text,
                "groundTruth": text,
            }
            if bbox:
                cell["bbox"] = bbox
            cells.append(cell)

    for row_index, col_index in sorted(hidden_positions):
        if row_index < row_count and col_index < col_count:
            cells.append({
                "row": row_index,
                "col": col_index,
                "text": "",
                "rowSpan": 1,
                "colSpan": 1,
                "ocrText": "",
                "groundTruth": "",
                "hidden": True,
            })

    return {
        "rows": rows,
        "cells": sorted(cells, key=lambda item: (int(item["row"]), int(item["col"]), bool(item.get("hidden")))),
        "headerRowCount": 1,
        "postProcessing": "coordinate_based_semi_reconstruction",
        "rowBoundaries": row_boundaries,
        "columnBoundaries": col_boundaries,
        "hardColumnBoundaries": hard_col_boundaries or [],
    }


def _cluster_text_cells(cells: List[Dict[str, Any]]) -> tuple[List[List[str]], List[Dict[str, Any]]]:
    if not cells:
        return ([], [])

    median_height = float(np.median([cell["height"] for cell in cells])) if cells else 12.0
    row_threshold = max(8.0, median_height * 0.75)
    row_groups: List[List[Dict[str, Any]]] = []
    for cell in sorted(cells, key=lambda item: (item["center_y"], item["x"])):
        target_row = None
        for row in row_groups:
            row_center = sum(item["center_y"] for item in row) / len(row)
            if abs(cell["center_y"] - row_center) <= row_threshold:
                target_row = row
                break
        if target_row is None:
            row_groups.append([cell])
        else:
            target_row.append(cell)

    row_groups = [sorted(row, key=lambda item: item["x"]) for row in row_groups]
    x_centers = sorted(cell["center_x"] for row in row_groups for cell in row)
    if not x_centers:
        return ([], [])
    median_width = float(np.median([cell["width"] for cell in cells])) if cells else 40.0
    column_threshold = max(14.0, median_width * 0.75)
    column_centers: List[float] = []
    for center in x_centers:
        if not column_centers or abs(center - column_centers[-1]) > column_threshold:
            column_centers.append(center)
        else:
            column_centers[-1] = (column_centers[-1] + center) / 2

    if len(column_centers) < _BORDERLESS_MIN_COLUMNS:
        return ([], [])

    rows: List[List[str]] = []
    source_cells: List[Dict[str, Any]] = []
    for row_index, row in enumerate(row_groups):
        values = ["" for _ in column_centers]
        grouped_boxes: List[List[Dict[str, float]]] = [[] for _ in column_centers]
        grouped_texts: List[List[str]] = [[] for _ in column_centers]
        for cell in row:
            col_index = min(range(len(column_centers)), key=lambda index: abs(cell["center_x"] - column_centers[index]))
            grouped_texts[col_index].append(cell["text"])
            grouped_boxes[col_index].append(cell["bbox"])
        for col_index, texts in enumerate(grouped_texts):
            text = normalize_ocr_text(" ".join(texts))
            values[col_index] = text
            bbox = _merge_bboxes(grouped_boxes[col_index])
            source_cell: Dict[str, Any] = {
                "row": row_index,
                "col": col_index,
                "text": text,
                "rowSpan": 1,
                "colSpan": 1,
                "ocrText": text,
                "groundTruth": text,
            }
            if bbox:
                source_cell["bbox"] = bbox
            source_cells.append(source_cell)
        rows.append(values)

    rows = normalize_table_rows(rows)
    if not _has_usable_table_shape(rows):
        return ([], [])
    return (rows, source_cells)


def _cluster_raw_ocr_geometry_cells(cells: List[Dict[str, Any]]) -> tuple[List[List[str]], List[Dict[str, Any]]]:
    if not cells:
        return ([], [])

    median_height = float(np.median([cell["height"] for cell in cells])) if cells else 12.0
    row_threshold = max(8.0, median_height * 0.75)
    row_groups: List[List[Dict[str, Any]]] = []
    for cell in sorted(cells, key=lambda item: (item["center_y"], item["x"])):
        target_row = None
        for row in row_groups:
            row_center = sum(item["center_y"] for item in row) / len(row)
            if abs(cell["center_y"] - row_center) <= row_threshold:
                target_row = row
                break
        if target_row is None:
            row_groups.append([cell])
        else:
            target_row.append(cell)

    rows: List[List[str]] = []
    source_cells: List[Dict[str, Any]] = []
    for row_index, row in enumerate(row_groups):
        sorted_row = sorted(row, key=lambda item: item["x"])
        values: List[str] = []
        for col_index, cell in enumerate(sorted_row):
            text = normalize_ocr_text(cell["text"])
            values.append(text)
            source_cell: Dict[str, Any] = {
                "row": row_index,
                "col": col_index,
                "text": text,
                "rowSpan": 1,
                "colSpan": 1,
                "ocrText": text,
                "groundTruth": text,
                "confidence": cell.get("confidence", 0.0),
                "bbox": cell.get("bbox"),
            }
            source_cells.append(source_cell)
        rows.append(values)

    return (normalize_table_rows(rows), source_cells)


def _recognize_borderless_table(image: np.ndarray) -> Optional[Dict[str, Any]]:
    phase_started = time.perf_counter()
    if image is None or image.size == 0:
        return None

    input_height, input_width = image.shape[:2]
    working_img = image
    scale_factor = 1.0
    longest_side = max(input_width, input_height)
    if longest_side < 1400:
        scale_factor = min(4.0, max(2.0, 1400.0 / max(longest_side, 1)))
        working_img = cv2.resize(
            image,
            (max(1, int(input_width * scale_factor)), max(1, int(input_height * scale_factor))),
            interpolation=cv2.INTER_CUBIC,
        )

    temp = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
    temp.close()
    try:
        if not cv2.imwrite(temp.name, working_img):
            return None
        detect_started = time.perf_counter()
        text_detection = detect_text_boxes(temp.name)
        logger.info(
            "Table Recognition phase timing: phase=Geometry Reconstruction text_detection elapsed=%.3fs",
            time.perf_counter() - detect_started,
        )
    except (LayoutAnalysisUnavailableError, Exception) as error:
        logger.info("Borderless table text detection failed: %s", error)
        return None
    finally:
        Path(temp.name).unlink(missing_ok=True)

    regions = text_detection.get("regions") if isinstance(text_detection, dict) else []
    if not isinstance(regions, list) or not regions:
        return None

    crops: List[np.ndarray] = []
    valid_regions: List[Dict[str, Any]] = []
    h_working, w_working = working_img.shape[:2]
    for region in regions:
        bbox = region.get("bbox") if isinstance(region, dict) else None
        if not isinstance(bbox, dict):
            continue
        try:
            x = max(0, int(float(bbox.get("x") or 0)))
            y = max(0, int(float(bbox.get("y") or 0)))
            width = max(1, int(float(bbox.get("width") or 1)))
            height = max(1, int(float(bbox.get("height") or 1)))
        except (TypeError, ValueError):
            continue
        width = min(width, w_working - x)
        height = min(height, h_working - y)
        if width <= 0 or height <= 0:
            continue
        crop = working_img[y : y + height, x : x + width]
        if crop.size == 0:
            continue
        valid_regions.append(region)
        crops.append(crop)

    if len(crops) < _BORDERLESS_MIN_ROWS * _BORDERLESS_MIN_COLUMNS:
        return None

    ocr_started = time.perf_counter()
    recognitions, ocr_core_debug = _recognize_text_crops_with_core(crops, "borderless_table")
    logger.info(
        "Table Recognition phase timing: phase=Geometry Reconstruction OCR core crops=%s elapsed=%.3fs",
        len(crops),
        time.perf_counter() - ocr_started,
    )

    cluster_started = time.perf_counter()
    cells: List[Dict[str, Any]] = []
    confidence_values: List[float] = []
    for region, recognition in zip(valid_regions, recognitions):
        text = normalize_ocr_text(recognition.get("text") if isinstance(recognition, dict) else "")
        if not text:
            continue
        bbox = _region_bbox(region, scale_factor)
        if not bbox:
            continue
        confidence = float(recognition.get("confidence") or 0.0) if isinstance(recognition, dict) else 0.0
        confidence_values.append(confidence)
        cells.append(
            {
                "text": text,
                "confidence": confidence,
                "bbox": bbox,
                "x": bbox["x"],
                "y": bbox["y"],
                "width": bbox["width"],
                "height": bbox["height"],
                "center_x": bbox["x"] + bbox["width"] / 2,
                "center_y": bbox["y"] + bbox["height"] / 2,
            }
        )

    rows, source_cells = _cluster_text_cells(cells)
    logger.info(
        "Table Recognition phase timing: phase=Geometry Reconstruction clustering boxes=%s elapsed=%.3fs",
        len(cells),
        time.perf_counter() - cluster_started,
    )
    if not rows:
        return None
    structured = _structured_from_rows(rows, source_cells)
    confidence = sum(confidence_values) / len(confidence_values) if confidence_values else 0.0
    return {
        "text": _markdown_table(rows),
        "confidence": float(confidence),
        "segments": [
            {
                "text": cell["text"],
                "confidence": cell["confidence"],
                "bbox": cell["bbox"],
            }
            for cell in cells
        ],
        "attempts": [{"step": "borderless_text_detection_clustering", "row_count": len(rows)}],
        "preprocessing": "borderless_table_text_detection_clustering",
        "engine": "table_recognition_v2",
        "model": _TABLE_MODEL_NAME,
        "table_rows": rows,
        "table_structured": structured,
        "table_debug": {
            "status": "borderless_fallback",
            "borderless_fallback_used": True,
            "detected_boxes": len(regions),
            "recognized_cells": len(cells),
            "row_count": len(rows),
            "column_count": max((len(row) for row in rows), default=0),
            "scale_factor": scale_factor,
            "input_size": [int(input_width), int(input_height)],
            "working_size": [int(working_img.shape[1]), int(working_img.shape[0])],
            "ocr_core": ocr_core_debug,
            "elapsed_seconds": round(time.perf_counter() - phase_started, 3),
        },
    }


def _recognize_raw_ocr_geometry_table(image: np.ndarray) -> Optional[Dict[str, Any]]:
    phase_started = time.perf_counter()
    if image is None or image.size == 0:
        return None

    input_height, input_width = image.shape[:2]
    temp = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
    temp.close()
    try:
        if not cv2.imwrite(temp.name, image):
            return None
        detect_started = time.perf_counter()
        text_detection = detect_text_boxes(temp.name)
        logger.info(
            "Table Recognition phase timing: phase=Raw OCR Geometry text_detection elapsed=%.3fs",
            time.perf_counter() - detect_started,
        )
    except (LayoutAnalysisUnavailableError, Exception) as error:
        logger.info("Raw OCR geometry table text detection failed: %s", error)
        return None
    finally:
        Path(temp.name).unlink(missing_ok=True)

    regions = text_detection.get("regions") if isinstance(text_detection, dict) else []
    if not isinstance(regions, list) or not regions:
        return None

    crops: List[np.ndarray] = []
    valid_regions: List[Dict[str, Any]] = []
    for region in regions:
        bbox = region.get("bbox") if isinstance(region, dict) else None
        if not isinstance(bbox, dict):
            continue
        try:
            x = max(0, int(float(bbox.get("x") or 0)))
            y = max(0, int(float(bbox.get("y") or 0)))
            width = max(1, int(float(bbox.get("width") or 1)))
            height = max(1, int(float(bbox.get("height") or 1)))
        except (TypeError, ValueError):
            continue
        width = min(width, input_width - x)
        height = min(height, input_height - y)
        if width <= 0 or height <= 0:
            continue
        crop = image[y : y + height, x : x + width]
        if crop.size == 0:
            continue
        valid_regions.append(region)
        crops.append(crop)

    if not crops:
        return None

    ocr_started = time.perf_counter()
    recognitions, ocr_core_debug = _recognize_text_crops_with_core(crops, "raw_ocr_geometry_table")
    logger.info(
        "Table Recognition phase timing: phase=Raw OCR Geometry OCR core crops=%s elapsed=%.3fs",
        len(crops),
        time.perf_counter() - ocr_started,
    )

    cells: List[Dict[str, Any]] = []
    confidence_values: List[float] = []
    for region, recognition in zip(valid_regions, recognitions):
        text = normalize_ocr_text(recognition.get("text") if isinstance(recognition, dict) else "")
        if not text:
            continue
        bbox = _region_bbox(region)
        if not bbox:
            continue
        confidence = float(recognition.get("confidence") or 0.0) if isinstance(recognition, dict) else 0.0
        confidence_values.append(confidence)
        cells.append(
            {
                "text": text,
                "confidence": confidence,
                "bbox": bbox,
                "x": bbox["x"],
                "y": bbox["y"],
                "width": bbox["width"],
                "height": bbox["height"],
                "center_x": bbox["x"] + bbox["width"] / 2,
                "center_y": bbox["y"] + bbox["height"] / 2,
            }
        )

    rows, source_cells = _cluster_raw_ocr_geometry_cells(cells)
    if not rows or not any(str(cell).strip() for row in rows for cell in row):
        return None
    structured = _structured_from_rows(rows, source_cells)
    confidence = sum(confidence_values) / len(confidence_values) if confidence_values else 0.0
    return {
        "text": _markdown_table(rows),
        "confidence": float(confidence),
        "segments": [
            {
                "text": cell["text"],
                "confidence": cell["confidence"],
                "bbox": cell["bbox"],
            }
            for cell in cells
        ],
        "attempts": [{"step": "raw_ocr_geometry_table", "row_count": len(rows), "box_count": len(cells)}],
        "preprocessing": "raw_ocr_geometry_table",
        "engine": "table_recognition_v2",
        "model": _TABLE_MODEL_NAME,
        "table_rows": rows,
        "table_structured": structured,
        "table_selected_method": "raw_ocr_geometry_table",
        "table_debug": {
            "status": "raw_ocr_geometry_table",
            "detected_boxes": len(regions),
            "recognized_cells": len(cells),
            "row_count": len(rows),
            "column_count": max((len(row) for row in rows), default=0),
            "input_size": [int(input_width), int(input_height)],
            "ocr_core": ocr_core_debug,
            "elapsed_seconds": round(time.perf_counter() - phase_started, 3),
        },
    }


def _ocr_cells_from_text_detection(image: np.ndarray, status_prefix: str) -> tuple[List[Dict[str, Any]], List[float], Dict[str, Any]]:
    input_height, input_width = image.shape[:2]
    temp = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
    temp.close()
    try:
        if not cv2.imwrite(temp.name, image):
            return ([], [], {"status": f"{status_prefix}_image_write_failed"})
        text_detection = detect_text_boxes(temp.name)
    except Exception as error:
        logger.info("%s text detection failed: %s", status_prefix, error)
        return ([], [], {"status": f"{status_prefix}_text_detection_failed", "reason": str(error)})
    finally:
        Path(temp.name).unlink(missing_ok=True)

    regions = text_detection.get("regions") if isinstance(text_detection, dict) else []
    if not isinstance(regions, list) or not regions:
        return ([], [], {"status": f"{status_prefix}_no_text_boxes", "detected_boxes": 0})

    crops: List[np.ndarray] = []
    valid_regions: List[Dict[str, Any]] = []
    for region in regions:
        bbox = region.get("bbox") if isinstance(region, dict) else None
        if not isinstance(bbox, dict):
            continue
        try:
            x = max(0, int(float(bbox.get("x") or 0)))
            y = max(0, int(float(bbox.get("y") or 0)))
            width = max(1, int(float(bbox.get("width") or 1)))
            height = max(1, int(float(bbox.get("height") or 1)))
        except (TypeError, ValueError):
            continue
        width = min(width, input_width - x)
        height = min(height, input_height - y)
        if width <= 0 or height <= 0:
            continue
        crop = image[y : y + height, x : x + width]
        if crop.size == 0:
            continue
        valid_regions.append(region)
        crops.append(crop)

    if not crops:
        return ([], [], {"status": f"{status_prefix}_no_valid_crops", "detected_boxes": len(regions)})

    recognitions, ocr_core_debug = _recognize_text_crops_with_core(crops, status_prefix)
    cells: List[Dict[str, Any]] = []
    confidence_values: List[float] = []
    for region, recognition in zip(valid_regions, recognitions):
        text = normalize_ocr_text(recognition.get("text") if isinstance(recognition, dict) else "")
        if not text:
            continue
        bbox = _region_bbox(region)
        if not bbox:
            continue
        confidence = float(recognition.get("confidence") or 0.0) if isinstance(recognition, dict) else 0.0
        confidence_values.append(confidence)
        cells.append({
            "text": text,
            "confidence": confidence,
            "bbox": bbox,
            "x": bbox["x"],
            "y": bbox["y"],
            "width": bbox["width"],
            "height": bbox["height"],
            "center_x": bbox["x"] + bbox["width"] / 2,
            "center_y": bbox["y"] + bbox["height"] / 2,
        })
    return (cells, confidence_values, {"status": status_prefix, "detected_boxes": len(regions), "recognized_cells": len(cells), "ocr_core": ocr_core_debug})


def _ocr_cells_from_grid_boundaries(
    image: np.ndarray,
    rows: List[List[str]],
    row_boundaries: List[float],
    col_boundaries: List[float],
    status_prefix: str,
) -> tuple[List[List[str]], List[Dict[str, Any]], List[float], Dict[str, Any]]:
    input_height, input_width = image.shape[:2]
    crops: List[np.ndarray] = []
    crop_positions: List[tuple[int, int, Dict[str, float]]] = []
    for row_index in range(max(0, len(row_boundaries) - 1)):
        for col_index in range(max(0, len(col_boundaries) - 1)):
            existing = rows[row_index][col_index] if row_index < len(rows) and col_index < len(rows[row_index]) else ""
            if str(existing).strip():
                continue
            left = max(0, int(round(col_boundaries[col_index])))
            right = min(input_width, int(round(col_boundaries[col_index + 1])))
            top = max(0, int(round(row_boundaries[row_index])))
            bottom = min(input_height, int(round(row_boundaries[row_index + 1])))
            if right - left <= 2 or bottom - top <= 2:
                continue
            inset_x = 1 if right - left > 6 else 0
            inset_y = 1 if bottom - top > 6 else 0
            crop = image[top + inset_y : bottom - inset_y, left + inset_x : right - inset_x]
            if crop.size == 0:
                continue
            crops.append(crop)
            crop_positions.append((
                row_index,
                col_index,
                {
                    "x": float(left),
                    "y": float(top),
                    "width": float(max(1, right - left)),
                    "height": float(max(1, bottom - top)),
                },
            ))

    if not crops:
        return (rows, [], [], {"status": f"{status_prefix}_no_empty_grid_cells", "cell_crops": 0, "filled_cells": 0})

    recognitions, ocr_core_debug = _recognize_text_crops_with_core(crops, status_prefix)

    source_cells: List[Dict[str, Any]] = []
    confidence_values: List[float] = []
    filled = 0
    for (row_index, col_index, bbox), recognition in zip(crop_positions, recognitions):
        text = normalize_ocr_text(recognition.get("text") if isinstance(recognition, dict) else "")
        if not text:
            continue
        confidence = float(recognition.get("confidence") or 0.0) if isinstance(recognition, dict) else 0.0
        rows[row_index][col_index] = text
        confidence_values.append(confidence)
        filled += 1
        source_cells.append({
            "text": text,
            "confidence": confidence,
            "assignment_source": "per_cell_ocr",
            "bbox": bbox,
            "x": bbox["x"],
            "y": bbox["y"],
            "width": bbox["width"],
            "height": bbox["height"],
            "center_x": bbox["x"] + bbox["width"] / 2,
            "center_y": bbox["y"] + bbox["height"] / 2,
            "row": row_index,
            "col": col_index,
            "rowSpan": 1,
            "colSpan": 1,
            "ocrText": text,
            "groundTruth": text,
        })
    return (
        rows,
        source_cells,
        confidence_values,
        {"status": status_prefix, "cell_crops": len(crops), "filled_cells": filled, "ocr_core": ocr_core_debug},
    )


def _recognize_coordinate_based_semi_table(image: np.ndarray, analysis: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    phase_started = time.perf_counter()
    if image is None or image.size == 0:
        return None

    input_height, input_width = image.shape[:2]
    horizontal_mask, vertical_mask = _semi_line_masks(image)
    horizontal_positions = _line_positions_from_mask(horizontal_mask, "horizontal", threshold_ratio=0.18)
    vertical_positions = _line_positions_from_mask(vertical_mask, "vertical", threshold_ratio=0.16)

    try:
        ocr_cells, confidence_values, ocr_debug = _ocr_cells_from_text_detection(image, "coordinate_based_semi")
    except (LayoutAnalysisUnavailableError, RuntimeError) as error:
        logger.info("Coordinate-based semi OCR failed: %s", error)
        return None

    hard_row_boundaries = _combined_boundaries(horizontal_positions, ocr_cells, "y", input_height)
    row_boundaries = _row_boundaries_with_logical_subrows(hard_row_boundaries, ocr_cells, input_height)
    min_col_gap = max(4.0, float(input_width) * 0.012)
    hard_col_boundaries = _line_boundaries(
        [pos for pos in vertical_positions if min_col_gap <= pos <= float(input_width) - min_col_gap],
        input_width,
    )
    hard_col_boundaries = sorted(set(round(value, 3) for value in hard_col_boundaries))
    col_boundaries = _column_boundaries_with_logical_subcolumns(hard_col_boundaries, ocr_cells, row_boundaries, input_width)
    if len(row_boundaries) < 2:
        row_boundaries = _infer_boundaries_from_text(ocr_cells, "y", input_height)
    if len(col_boundaries) < 2:
        col_boundaries = _infer_boundaries_from_text(ocr_cells, "x", input_width)
    row_count = max(1, len(row_boundaries) - 1)
    col_count = max(1, len(col_boundaries) - 1)
    rows = [["" for _ in range(col_count)] for _ in range(row_count)]
    source_cells: List[Dict[str, Any]] = []
    median_width = float(np.median([cell.get("width", 40.0) for cell in ocr_cells])) if ocr_cells else 40.0
    median_height = float(np.median([cell.get("height", 12.0) for cell in ocr_cells])) if ocr_cells else 12.0
    x_tolerance = max(4.0, median_width * 0.12)
    y_tolerance = max(4.0, median_height * 0.35)
    assigned_cells: List[Dict[str, Any]] = []
    row_peer_counts: Dict[int, int] = {}
    for cell in ocr_cells:
        row_index = _dominant_interval_index(
            float(cell.get("y") or 0.0),
            float(cell.get("y") or 0.0) + float(cell.get("height") or 0.0),
            row_boundaries,
            y_tolerance,
        )
        row_peer_counts[row_index] = row_peer_counts.get(row_index, 0) + 1
    for cell in sorted(ocr_cells, key=lambda item: (item["center_y"], item["center_x"])):
        provisional_row = _dominant_interval_index(
            float(cell.get("y") or 0.0),
            float(cell.get("y") or 0.0) + float(cell.get("height") or 0.0),
            row_boundaries,
            y_tolerance,
        )
        row_index, col_index = _assign_ocr_cell_to_grid(
            cell,
            row_boundaries,
            col_boundaries,
            hard_col_boundaries,
            x_tolerance,
            y_tolerance,
            row_peer_counts.get(provisional_row, 1),
        )
        text = normalize_ocr_text(cell["text"])
        assigned_cells.append({
            **cell,
            "row": row_index,
            "col": col_index,
            "rowSpan": 1,
            "colSpan": 1,
            "assignment_source": "text_detection",
            "ocrText": text,
            "groundTruth": text,
            "text": text,
        })
    source_cells = _merge_assigned_ocr_cells(assigned_cells)
    for cell in source_cells:
        row_index = int(cell["row"])
        col_index = int(cell["col"])
        text = str(cell.get("text") or "").strip()
        if not text:
            continue
        rows[row_index][col_index] = f"{rows[row_index][col_index]} {text}".strip() if rows[row_index][col_index] else text

    grid_cell_debug: Dict[str, Any] = {"status": "coordinate_based_semi_per_cell_not_needed", "cell_crops": 0, "filled_cells": 0}
    empty_cell_count = sum(1 for row in rows for value in row if not str(value).strip())
    total_cell_count = sum(len(row) for row in rows)
    should_run_cell_ocr = (
        len(row_boundaries) >= 2
        and len(col_boundaries) >= 3
        and (
            not ocr_cells
            or len(ocr_cells) < max(1, min(total_cell_count or 1, col_count))
        )
    )
    if should_run_cell_ocr:
        rows, grid_source_cells, grid_confidences, grid_cell_debug = _ocr_cells_from_grid_boundaries(
            image,
            rows,
            row_boundaries,
            col_boundaries,
            "coordinate_based_semi_per_cell",
        )
        source_cells.extend(grid_source_cells)
        confidence_values.extend(grid_confidences)

    rows = normalize_table_rows(rows)
    if not any(str(value).strip() for row in rows for value in row):
        return None
    source_cells, dedupe_debug = _deduplicate_assigned_table_cells(source_cells)
    rows = [["" for _ in range(col_count)] for _ in range(row_count)]
    for cell in source_cells:
        row_index = max(0, min(row_count - 1, int(cell.get("row") or 0)))
        col_index = max(0, min(col_count - 1, int(cell.get("col") or 0)))
        text = str(cell.get("text") or "").strip()
        if not text:
            continue
        rows[row_index][col_index] = f"{rows[row_index][col_index]} {text}".strip() if rows[row_index][col_index] else text
    rows = normalize_table_rows(rows)
    rows, source_cells, col_boundaries, column_compaction_debug = _compact_empty_inferred_columns(
        rows,
        source_cells,
        col_boundaries,
        hard_col_boundaries,
    )
    assignment_diagnostics = _coordinate_assignment_diagnostics(rows, source_cells, row_boundaries, col_boundaries, hard_col_boundaries)
    structured = _structured_from_coordinate_grid(rows, source_cells, row_boundaries, col_boundaries, horizontal_mask, vertical_mask, hard_col_boundaries)
    confidence = sum(confidence_values) / len(confidence_values) if confidence_values else 0.0
    semi_analysis = dict(analysis)
    semi_analysis["merge_status"] = "coordinate_reconstructed"
    semi_analysis["region_processing"] = "coordinate_based"
    semi_analysis["model_reuse"] = {"enabled": False, "model_inference_count": 0}
    semi_analysis["ocr_geometry"] = {
        "detected_boxes": int(ocr_debug.get("detected_boxes") or 0) if isinstance(ocr_debug, dict) else len(ocr_cells),
        "recognized_cells": len(source_cells),
        "row_boundary_count": len(row_boundaries),
        "hard_column_boundary_count": len(hard_col_boundaries),
        "logical_column_boundary_count": len(col_boundaries),
        "uses_text_alignment": len(col_boundaries) > len(hard_col_boundaries) or len(row_boundaries) > len(hard_row_boundaries),
        "assignment": assignment_diagnostics,
        "column_compaction": column_compaction_debug,
        "dedupe": dedupe_debug,
    }
    return {
        "text": _markdown_table(rows),
        "confidence": float(confidence),
        "segments": [{"text": cell["text"], "confidence": cell["confidence"], "bbox": cell["bbox"]} for cell in ocr_cells],
        "attempts": [{"step": "coordinate_based_semi_reconstruction", "row_count": len(rows), "column_count": max((len(row) for row in rows), default=0)}],
        "preprocessing": "coordinate_based_semi_reconstruction",
        "engine": "table_recognition_v2",
        "model": "coordinate_based_semi",
        "table_rows": rows,
        "table_structured": structured,
        "table_selected_method": "coordinate_based_semi",
        "table_debug": {
            "status": "coordinate_based_semi_reconstructed",
            "row_count": len(rows),
            "column_count": max((len(row) for row in rows), default=0),
            "horizontal_line_count": len(horizontal_positions),
            "vertical_line_count": len(vertical_positions),
            "hard_row_boundary_count": len(hard_row_boundaries),
            "logical_row_boundary_count": len(row_boundaries),
            "hard_column_boundary_count": len(hard_col_boundaries),
            "logical_column_boundary_count": len(col_boundaries),
            "hard_column_boundaries": hard_col_boundaries,
            "logical_column_boundaries": col_boundaries,
            "ocr": ocr_debug,
            "per_cell_ocr": grid_cell_debug,
            "assignment": assignment_diagnostics,
            "column_compaction": column_compaction_debug,
            "region_processing": "coordinate_based",
            "model_reuse": {"enabled": False, "model_inference_count": 0},
            "elapsed_seconds": round(time.perf_counter() - phase_started, 3),
        },
        "table_semi_analysis": semi_analysis,
    }


def _has_usable_structured_cells(structured: Any) -> bool:
    if not isinstance(structured, dict):
        return False
    cells = structured.get("cells")
    if not isinstance(cells, list):
        return False
    return any(isinstance(cell, dict) and not cell.get("hidden") for cell in cells)


def _has_usable_table_result(candidate: Dict[str, Any]) -> bool:
    rows = normalize_table_rows(candidate.get("table_rows") or [])
    if rows and _has_usable_table_shape(rows):
        return True
    return _has_usable_structured_cells(candidate.get("table_structured"))


def _recognize_ocr_table_fallback(image: np.ndarray) -> Optional[Dict[str, Any]]:
    result = _recognize_borderless_table(image)
    if not result:
        return None
    rows = normalize_table_rows(result.get("table_rows") or [])
    structured = result.get("table_structured") if isinstance(result.get("table_structured"), dict) else _structured_from_rows(rows)
    if not rows and not _has_usable_structured_cells(structured):
        return None
    debug = result.get("table_debug") if isinstance(result.get("table_debug"), dict) else {}
    debug.update(
        {
            "status": "ocr_table_fallback",
            "ocr_table_fallback_used": True,
            "borderless_fallback_used": True,
        }
    )
    return {
        **result,
        "text": _markdown_table(rows) if rows else str(result.get("text") or ""),
        "table_rows": rows,
        "table_structured": structured,
        "table_debug": debug,
        "preprocessing": "ocr_table_fallback_text_detection_clustering",
    }


def _normalize_cell_dicts(cells: Any) -> List[Dict[str, Any]]:
    if not isinstance(cells, list):
        return []

    normalized: List[Dict[str, Any]] = []
    for cell in cells:
        if not isinstance(cell, dict):
            continue
        text = normalize_ocr_text(cell.get("text") or cell.get("content") or cell.get("value") or "")
        row = cell.get("row") if cell.get("row") is not None else cell.get("row_index") if cell.get("row_index") is not None else cell.get("start_row")
        col = cell.get("col") if cell.get("col") is not None else cell.get("col_index") if cell.get("col_index") is not None else cell.get("start_col")
        if row is None or col is None:
            continue
        try:
            normalized.append({**cell, "row": int(row), "col": int(col), "text": text})
        except (TypeError, ValueError):
            continue

    return normalized


def _rows_from_cells(cells: Any) -> List[List[str]]:
    normalized = _normalize_cell_dicts(cells)
    if not normalized:
        return []

    min_row = min(item["row"] for item in normalized)
    min_col = min(item["col"] for item in normalized)
    max_row = max(item["row"] for item in normalized)
    max_col = max(item["col"] for item in normalized)
    rows = [["" for _ in range(max_col - min_col + 1)] for _ in range(max_row - min_row + 1)]
    for item in normalized:
        rows[item["row"] - min_row][item["col"] - min_col] = item["text"]
    return rows


def _markdown_table(rows: List[List[str]]) -> str:
    if not rows:
        return ""
    max_columns = max(len(row) for row in rows)
    normalized = [row + [""] * (max_columns - len(row)) for row in rows]
    header = normalized[0]
    separator = ["---"] * max_columns

    def fmt(row: List[str]) -> str:
        return "| " + " | ".join(str(cell).strip().replace("|", "/") for cell in row) + " |"

    return "\n".join([fmt(header), fmt(separator), *[fmt(row) for row in normalized[1:]]])


def _extract_rows(result: Dict[str, Any]) -> List[List[str]]:
    structured = result.get("table_structured")
    if isinstance(structured, dict) and isinstance(structured.get("rows"), list):
        rows = structured.get("rows")
        if rows and all(isinstance(row, list) for row in rows):
            normalized_rows = normalize_table_rows(rows)
            if isinstance(structured.get("cells"), list):
                cell_rows = _rows_from_structured_cells_preserve_grid(structured.get("cells"))
                if cell_rows:
                    normalized_rows = _prefer_larger_grid_rows(normalized_rows, cell_rows)
            return normalized_rows
    if isinstance(structured, dict) and isinstance(structured.get("cells"), list):
        rows = _rows_from_structured_cells_preserve_grid(structured.get("cells"))
        if rows:
            return rows
    for key in ("rows", "table_rows", "cells"):
        rows = _rows_from_cells(result.get(key))
        if rows:
            return rows
    for key in ("rows", "table_rows"):
        value = result.get(key)
        if isinstance(value, list) and value and all(isinstance(row, list) for row in value):
            return normalize_table_rows(value)
    return []


def _extract_structured_table(result: Dict[str, Any], rows: List[List[str]], html: str) -> Optional[Dict[str, Any]]:
    structured = result.get("table_structured")
    if isinstance(structured, dict):
        return structured
    for key in ("cells", "table_cells"):
        source_cells = _normalize_cell_dicts(result.get(key))
        if source_cells:
            return _structured_from_rows(rows or _rows_from_cells(source_cells), source_cells)
    if html:
        structured = _structured_from_html(html)
        if structured:
            return structured
    return _structured_from_rows(rows)


def _postprocess_table_result(result: Dict[str, Any]) -> Dict[str, Any]:
    processed = dict(result)
    html = str(processed.get("table_html") or processed.get("html") or "")
    rows = processed.get("table_rows")
    if isinstance(rows, list) and rows and all(isinstance(row, list) for row in rows):
        normalized_rows = normalize_table_rows(rows)
    else:
        normalized_rows = _rows_from_html(html)

    structured = processed.get("table_structured")
    if isinstance(structured, dict) and isinstance(structured.get("cells"), list):
        cell_rows = _rows_from_structured_cells_preserve_grid(structured.get("cells"))
        if cell_rows:
            normalized_rows = _prefer_larger_grid_rows(normalized_rows, cell_rows)
    if not isinstance(structured, dict):
        structured = _extract_structured_table(processed, normalized_rows, html)
    elif normalized_rows and not isinstance(structured.get("rows"), list):
        structured = _structured_from_rows(normalized_rows, _normalize_cell_dicts(structured.get("cells")))
    elif normalized_rows and isinstance(structured.get("rows"), list):
        structured_rows = normalize_table_rows(structured.get("rows"))
        if _row_grid_shape(normalized_rows) != _row_grid_shape(structured_rows):
            structured = dict(structured)
            structured["rows"] = normalized_rows
    elif not normalized_rows and isinstance(structured.get("cells"), list):
        normalized_rows = _rows_from_cells(structured.get("cells"))
        if normalized_rows and not isinstance(structured.get("rows"), list):
            structured = _structured_from_rows(normalized_rows, _normalize_cell_dicts(structured.get("cells")))

    if normalized_rows:
        processed["table_rows"] = normalized_rows
        processed["text"] = _markdown_table(normalized_rows)
    elif processed.get("text") is not None:
        processed["text"] = normalize_ocr_text(processed.get("text"))

    if structured:
        processed["table_structured"] = structured

    debug = processed.get("table_debug")
    if isinstance(debug, dict):
        debug.setdefault("post_processing", "beautifulsoup4+lxml")
    else:
        processed["table_debug"] = {"post_processing": "beautifulsoup4+lxml"}
    return processed


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _to_confidence_score(value: Any) -> Optional[float]:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(numeric) or numeric < 0:
        return None
    if numeric > 1.0:
        numeric = numeric / 100.0
    return _clamp01(numeric)


def _cell_has_text(cell: Dict[str, Any]) -> bool:
    return bool(str(cell.get("text") or cell.get("ocrText") or cell.get("ocr_text") or cell.get("groundTruth") or "").strip())


def _first_confidence_value(record: Dict[str, Any], keys: List[str]) -> Optional[float]:
    for key in keys:
        if key in record:
            score = _to_confidence_score(record.get(key))
            if score is not None:
                return score
    return None


def _calculate_table_quality(rows: List[List[str]], structured: Optional[Dict[str, Any]], method: str) -> Dict[str, Any]:
    normalized_rows = normalize_table_rows(rows) if rows else []
    row_count, column_count = _table_shape(normalized_rows)
    total_cells = row_count * column_count if row_count and column_count else 0
    non_empty_by_row = [sum(1 for cell in row if str(cell).strip()) for row in normalized_rows]
    non_empty_cell_count = sum(non_empty_by_row)
    non_empty_rows = sum(1 for count in non_empty_by_row if count > 0)
    fill_ratio = non_empty_cell_count / total_cells if total_cells else 0.0
    non_empty_row_ratio = non_empty_rows / row_count if row_count else 0.0
    active_counts = [count for count in non_empty_by_row if count > 0]
    if column_count <= 0 or not active_counts:
        column_consistency = 0.0
    else:
        average_count = sum(active_counts) / len(active_counts)
        variance = sum((count - average_count) ** 2 for count in active_counts) / len(active_counts)
        normalized_std = (variance ** 0.5) / max(column_count, 1)
        column_consistency = _clamp01(1.0 - normalized_std)
    sparse_rows = sum(1 for count in non_empty_by_row if column_count > 0 and count > 0 and (count / column_count) < 0.35)
    sparse_row_ratio = sparse_rows / row_count if row_count else 0.0

    structured_cells = []
    if isinstance(structured, dict) and isinstance(structured.get("cells"), list):
        structured_cells = [cell for cell in structured.get("cells") or [] if isinstance(cell, dict)]
    visible_structured_cells = [cell for cell in structured_cells if not cell.get("hidden")]
    has_structured_cells = bool(visible_structured_cells)
    merged_cells = [
        cell
        for cell in visible_structured_cells
        if int(cell.get("rowSpan") or cell.get("rowspan") or cell.get("row_span") or 1) > 1
        or int(cell.get("colSpan") or cell.get("colspan") or cell.get("col_span") or 1) > 1
    ]
    merged_cell_ratio = len(merged_cells) / len(visible_structured_cells) if visible_structured_cells else 0.0
    usable_shape = _has_usable_table_shape(normalized_rows)
    penalties: List[str] = []
    if not normalized_rows:
        penalties.append("no_rows")
    if not usable_shape:
        penalties.append("unusable_shape")
    if column_count < _BORDERLESS_MIN_COLUMNS:
        penalties.append("too_few_columns")
    if fill_ratio < _TABLE_BORDERLESS_FILL_RATIO_THRESHOLD:
        penalties.append("low_fill_ratio")
    if column_consistency < _TABLE_BORDERLESS_COLUMN_CONSISTENCY_THRESHOLD:
        penalties.append("low_column_consistency")
    if sparse_row_ratio > _TABLE_BORDERLESS_SPARSE_ROW_RATIO_THRESHOLD:
        penalties.append("too_many_sparse_rows")

    shape_score = 0.0
    if row_count >= _BORDERLESS_MIN_ROWS and column_count >= _BORDERLESS_MIN_COLUMNS:
        shape_score = 1.0
    elif row_count > 0 and column_count > 0:
        shape_score = 0.35
    structure_bonus = 0.08 if has_structured_cells else 0.0
    merged_adjustment = 0.04 if 0.0 < merged_cell_ratio <= 0.35 else (-0.04 if merged_cell_ratio > 0.65 else 0.0)
    score = (
        shape_score * 0.30
        + fill_ratio * 0.24
        + non_empty_row_ratio * 0.16
        + column_consistency * 0.18
        + (1.0 - sparse_row_ratio) * 0.08
        + structure_bonus
        + merged_adjustment
    )
    if not usable_shape:
        score *= 0.65
    if not normalized_rows:
        score = 0.0

    return {
        "score": round(_clamp01(score), 4),
        "row_count": row_count,
        "column_count": column_count,
        "non_empty_cell_count": non_empty_cell_count,
        "fill_ratio": round(_clamp01(fill_ratio), 4),
        "non_empty_row_ratio": round(_clamp01(non_empty_row_ratio), 4),
        "column_consistency": round(_clamp01(column_consistency), 4),
        "sparse_row_ratio": round(_clamp01(sparse_row_ratio), 4),
        "has_structured_cells": has_structured_cells,
        "merged_cell_ratio": round(_clamp01(merged_cell_ratio), 4),
        "usable_shape": usable_shape,
        "penalties": penalties,
        "method": method,
    }


def _collect_confidence_values(value: Any, include_empty: bool = False) -> List[float]:
    values: List[float] = []
    if isinstance(value, dict):
        if include_empty or _cell_has_text(value):
            for key in ("confidence", "score", "rec_score", "text_score", "ocr_confidence"):
                score = _to_confidence_score(value.get(key))
                if score is not None:
                    values.append(score)
            for key in ("rec_scores", "text_scores", "scores", "confidences"):
                nested = value.get(key)
                if isinstance(nested, (list, tuple)):
                    values.extend(score for score in (_to_confidence_score(item) for item in nested) if score is not None)
        for nested_value in value.values():
            values.extend(_collect_confidence_values(nested_value, include_empty=include_empty))
    elif isinstance(value, (list, tuple)):
        for item in value:
            values.extend(_collect_confidence_values(item, include_empty=include_empty))
    return values


def _calculate_ocr_confidence(result: Dict[str, Any]) -> Dict[str, Any]:
    values: List[float] = []
    segments = result.get("segments")
    if isinstance(segments, list):
        for segment in segments:
            if isinstance(segment, dict) and _cell_has_text(segment):
                score = _first_confidence_value(segment, ["confidence", "score", "rec_score", "text_score", "ocr_confidence"])
                if score is not None:
                    values.append(score)

    structured = result.get("table_structured")
    if isinstance(structured, dict) and isinstance(structured.get("cells"), list):
        for cell in structured.get("cells") or []:
            if isinstance(cell, dict) and _cell_has_text(cell):
                for key in ("confidence", "score", "rec_score", "text_score", "ocr_confidence"):
                    score = _to_confidence_score(cell.get(key))
                    if score is not None:
                        values.append(score)

    raw_sources = [
        result.get("raw_result"),
        result.get("raw_results"),
        result.get("table_debug", {}).get("raw_result") if isinstance(result.get("table_debug"), dict) else None,
    ]
    for source in raw_sources:
        values.extend(_collect_confidence_values(source, include_empty=False))

    if not values:
        return {
            "available": False,
            "score": 0.0,
            "average": 0.0,
            "minimum": 0.0,
            "recognized_count": 0,
            "low_confidence_count": 0,
        }

    average = sum(values) / len(values)
    minimum = min(values)
    return {
        "available": True,
        "score": round(_clamp01(average), 4),
        "average": round(_clamp01(average), 4),
        "minimum": round(_clamp01(minimum), 4),
        "recognized_count": len(values),
        "low_confidence_count": sum(1 for value in values if value < _TABLE_LOW_OCR_CONFIDENCE_THRESHOLD),
    }


def _build_table_candidate(result: Dict[str, Any], method: str) -> Dict[str, Any]:
    candidate = _postprocess_table_result(result)
    rows = normalize_table_rows(candidate.get("table_rows") or [])
    structured = candidate.get("table_structured") if isinstance(candidate.get("table_structured"), dict) else None
    quality = _calculate_table_quality(rows, structured, method)
    ocr_confidence = _calculate_ocr_confidence(candidate)
    structure_score = float(quality["score"])
    if ocr_confidence["available"]:
        final_confidence = structure_score * 0.65 + float(ocr_confidence["score"]) * 0.35
    else:
        final_confidence = structure_score * 0.85
    final_confidence = round(_clamp01(final_confidence), 4)

    candidate["confidence"] = final_confidence
    debug = candidate.get("table_debug")
    if not isinstance(debug, dict):
        debug = {}
    debug["quality"] = quality
    debug["ocr_confidence"] = ocr_confidence
    debug["final_confidence"] = final_confidence
    debug["candidate_method"] = method
    candidate["table_debug"] = debug
    candidate["table_selected_method"] = method
    if _table_debug_trace_enabled() and method == "slanext":
        trace = _ensure_table_trace(candidate)
        if isinstance(trace, dict):
            trace["postprocessed"] = {
                "table_rows": _json_safe(candidate.get("table_rows")),
                "table_structured": _json_safe(candidate.get("table_structured")),
                "quality": _json_safe(quality),
                "final_confidence": final_confidence,
            }
    return candidate


def _should_try_borderless_candidate(quality: Dict[str, Any], final_confidence: float) -> bool:
    return (
        not bool(quality.get("usable_shape"))
        or int(quality.get("row_count") or 0) <= 0
        or int(quality.get("column_count") or 0) < _BORDERLESS_MIN_COLUMNS
        or float(final_confidence or 0.0) < _TABLE_BORDERLESS_FINAL_CONFIDENCE_THRESHOLD
        or float(quality.get("fill_ratio") or 0.0) < _TABLE_BORDERLESS_FILL_RATIO_THRESHOLD
        or float(quality.get("column_consistency") or 0.0) < _TABLE_BORDERLESS_COLUMN_CONSISTENCY_THRESHOLD
        or float(quality.get("sparse_row_ratio") or 0.0) > _TABLE_BORDERLESS_SPARSE_ROW_RATIO_THRESHOLD
    )


def _semi_result_reliability(candidate: Dict[str, Any], analysis: Dict[str, Any]) -> Dict[str, Any]:
    debug = candidate.get("table_debug") if isinstance(candidate.get("table_debug"), dict) else {}
    quality = debug.get("quality") if isinstance(debug.get("quality"), dict) else {}
    ocr_debug = debug.get("ocr") if isinstance(debug.get("ocr"), dict) else {}
    per_cell_ocr = debug.get("per_cell_ocr") if isinstance(debug.get("per_cell_ocr"), dict) else {}
    assignment = debug.get("assignment") if isinstance(debug.get("assignment"), dict) else {}
    line_summary = analysis.get("line_summary") if isinstance(analysis.get("line_summary"), dict) else {}

    row_count = int(quality.get("row_count") or 0)
    column_count = int(quality.get("column_count") or 0)
    non_empty = int(quality.get("non_empty_cell_count") or 0)
    fill_ratio = float(quality.get("fill_ratio") or 0.0)
    column_consistency = float(quality.get("column_consistency") or 0.0)
    sparse_row_ratio = float(quality.get("sparse_row_ratio") or 0.0)
    final_confidence = float(debug.get("final_confidence") or candidate.get("confidence") or 0.0)
    detected_boxes = int(ocr_debug.get("detected_boxes") or 0)
    recognized_cells = int(ocr_debug.get("recognized_cells") or 0) + int(per_cell_ocr.get("filled_cells") or 0)
    hard_columns = int(debug.get("hard_column_boundary_count") or 0)
    logical_columns = int(debug.get("logical_column_boundary_count") or 0)
    line_verticals = int(line_summary.get("vertical") or debug.get("vertical_line_count") or 0)
    line_horizontals = int(line_summary.get("horizontal") or debug.get("horizontal_line_count") or 0)

    reasons: List[str] = []
    if row_count <= 0 or column_count <= 0 or non_empty <= 0:
        reasons.append("empty_reconstruction")
    if line_verticals >= 3 and column_count <= 1:
        reasons.append("opencv_columns_collapsed_to_one")
    if hard_columns >= 3 and logical_columns < hard_columns:
        reasons.append("logical_grid_lost_hard_boundaries")
    if detected_boxes > 0 and recognized_cells < max(1, int(detected_boxes * 0.55)):
        reasons.append("ocr_boxes_dropped")
    if row_count >= 2 and column_count >= 2 and fill_ratio < 0.12:
        reasons.append("low_fill_ratio")
    if column_count >= 2 and column_consistency < 0.35 and sparse_row_ratio > 0.55:
        reasons.append("irregular_assignment")
    if float(assignment.get("average_row_overlap") or 1.0) < 0.45 or float(assignment.get("average_column_overlap") or 1.0) < 0.45:
        reasons.append("low_assignment_overlap")
    if int(assignment.get("hard_region_violation_count") or 0) > 0:
        reasons.append("hard_region_assignment_violation")
    if column_count >= 2 and float(assignment.get("empty_column_ratio") or 0.0) > 0.65 and recognized_cells >= column_count:
        reasons.append("too_many_empty_columns_after_assignment")
    if final_confidence < 0.38:
        reasons.append("low_final_confidence")

    return {
        "passed": not reasons,
        "reasons": reasons,
        "row_count": row_count,
        "column_count": column_count,
        "non_empty_cell_count": non_empty,
        "fill_ratio": round(_clamp01(fill_ratio), 4),
        "column_consistency": round(_clamp01(column_consistency), 4),
        "sparse_row_ratio": round(_clamp01(sparse_row_ratio), 4),
        "detected_boxes": detected_boxes,
        "recognized_cells": recognized_cells,
        "hard_column_boundary_count": hard_columns,
        "logical_column_boundary_count": logical_columns,
        "line_summary": {"horizontal": line_horizontals, "vertical": line_verticals},
        "assignment": assignment,
        "final_confidence": round(_clamp01(final_confidence), 4),
    }


def _candidate_has_content(candidate: Dict[str, Any]) -> bool:
    return bool(candidate.get("table_rows") or str(candidate.get("text") or "").strip())


def _region_candidate_has_usable_content(candidate: Dict[str, Any], quality: Dict[str, Any]) -> bool:
    if not _candidate_has_content(candidate):
        return False
    if bool(quality.get("usable_shape")):
        return True
    row_count = int(quality.get("row_count") or 0)
    column_count = int(quality.get("column_count") or 0)
    non_empty_cell_count = int(quality.get("non_empty_cell_count") or 0)
    return row_count > 0 and column_count > 0 and non_empty_cell_count > 0


def _candidate_summary(candidate: Dict[str, Any]) -> Dict[str, Any]:
    debug = candidate.get("table_debug") if isinstance(candidate.get("table_debug"), dict) else {}
    quality = debug.get("quality") if isinstance(debug.get("quality"), dict) else {}
    ocr_confidence = debug.get("ocr_confidence") if isinstance(debug.get("ocr_confidence"), dict) else {}
    return {
        "method": debug.get("candidate_method") or candidate.get("table_selected_method") or "",
        "structure_score": float(quality.get("score") or 0.0),
        "ocr_score": float(ocr_confidence.get("score") or 0.0),
        "ocr_available": bool(ocr_confidence.get("available")),
        "final_confidence": float(debug.get("final_confidence") or candidate.get("confidence") or 0.0),
        "row_count": int(quality.get("row_count") or 0),
        "column_count": int(quality.get("column_count") or 0),
        "usable_shape": bool(quality.get("usable_shape")),
        "has_structured_cells": bool(quality.get("has_structured_cells")),
        "non_empty_cell_count": int(quality.get("non_empty_cell_count") or 0),
        "penalties": quality.get("penalties") if isinstance(quality.get("penalties"), list) else [],
    }


def _select_best_table_candidate(candidates: List[Dict[str, Any]]) -> tuple[Dict[str, Any], str]:
    valid_candidates = [candidate for candidate in candidates if _candidate_has_content(candidate)]
    if not valid_candidates:
        return (candidates[0] if candidates else {}, "no_valid_candidate")
    if len(valid_candidates) == 1:
        return valid_candidates[0], "only_valid_candidate"

    sorted_candidates = sorted(valid_candidates, key=lambda item: float(item.get("confidence") or 0.0), reverse=True)
    best = sorted_candidates[0]
    runner_up = sorted_candidates[1]
    best_score = float(best.get("confidence") or 0.0)
    runner_score = float(runner_up.get("confidence") or 0.0)
    if best_score - runner_score > _TABLE_CANDIDATE_TIE_EPSILON:
        if _candidate_summary(best)["method"] == "borderless_text_clustering" and _candidate_summary(runner_up)["method"] == "slanext":
            return best, "borderless_improved_low_quality_slanext"
        return best, "higher_final_confidence"

    def tie_key(candidate: Dict[str, Any]) -> tuple[int, int, int, int, int]:
        summary = _candidate_summary(candidate)
        return (
            1 if summary["usable_shape"] else 0,
            1 if summary["has_structured_cells"] else 0,
            1 if summary["column_count"] > 1 else 0,
            summary["non_empty_cell_count"],
            1 if summary["method"] == "slanext" else 0,
        )

    selected = sorted(valid_candidates, key=lambda item: (tie_key(item), float(item.get("confidence") or 0.0)), reverse=True)[0]
    if _candidate_summary(selected)["method"] == "slanext" and _candidate_summary(selected)["has_structured_cells"]:
        return selected, "tie_preferred_structured_slanext"
    return selected, "tie_breaker"


def _attach_candidate_competition(selected: Dict[str, Any], candidates: List[Dict[str, Any]], reason: str) -> Dict[str, Any]:
    selected_debug = selected.get("table_debug")
    if not isinstance(selected_debug, dict):
        selected_debug = {}
    selected_method = _candidate_summary(selected)["method"]
    selected_debug["candidate_competition"] = {
        "selected_method": selected_method,
        "selection_reason": reason,
        "candidate_count": len(candidates),
        "candidates": [_candidate_summary(candidate) for candidate in candidates],
    }
    selected["table_debug"] = selected_debug
    selected["table_selected_method"] = selected_method
    selected["table_candidates"] = [_candidate_summary(candidate) for candidate in candidates]
    return selected


def _predict_table_model(model: Any, image: np.ndarray) -> Any:
    started = time.perf_counter()
    temp = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
    temp.close()
    try:
        if not cv2.imwrite(temp.name, image):
            raise TableRecognitionV2UnavailableError("Unable to prepare table image for table_recognition_v2.")
        if _TABLE_MODEL_KIND == "pipeline_v2":
            return model.predict(
                input=temp.name,
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_layout_detection=False,
                use_ocr_model=True,
            )
        return model.predict(input=temp.name, batch_size=1)
    finally:
        logger.info("Table Recognition phase timing: phase=SLANeXt inference elapsed=%.3fs", time.perf_counter() - started)
        Path(temp.name).unlink(missing_ok=True)


def _slanext_result_from_output(output: Any, image: np.ndarray, started: float, region_debug: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    input_height, input_width = image.shape[:2]
    dicts = _collect_dicts(output)
    html = ""
    rows: List[List[str]] = []
    structured_table: Optional[Dict[str, Any]] = None
    for item in dicts:
        if not html:
            html = _extract_html(item)
        if not rows:
            rows = _extract_rows(item)
        if not rows and html:
            rows = _rows_from_html(html)
        if structured_table is None:
            structured_table = _extract_structured_table(item, rows, html)

    rows = normalize_table_rows(rows)
    raw_model_fields = _extract_raw_model_fields(dicts)
    source_structure_model = _source_structure_model_from_raw_fields(raw_model_fields)
    text = _markdown_table(rows)
    structured_table = structured_table or _structured_from_rows(rows)
    debug: Dict[str, Any] = {
        "status": "recognized" if text or html else "structure_empty",
        "row_count": len(rows),
        "column_count": max((len(row) for row in rows), default=0),
        "raw_result_count": len(dicts),
        "model_kind": _TABLE_MODEL_KIND,
        "source_structure_model": source_structure_model,
        "text_recognition_model": _TABLE_TEXT_RECOGNITION_MODEL_NAME,
        "runtime_called": True,
        "input_size": [int(input_width), int(input_height)],
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }
    if region_debug:
        debug["region"] = region_debug
    result = {
        "text": text,
        "confidence": 0.0,
        "segments": [],
        "attempts": [],
        "preprocessing": "paddle_table_recognition_v2",
        "engine": "table_recognition_v2",
        "model": _TABLE_MODEL_NAME,
        "table_html": html or None,
        "table_rows": rows,
        "table_structured": structured_table,
        "table_debug": debug,
        "raw_results": dicts,
    }
    if _table_debug_trace_enabled() and not region_debug:
        debug["table_recognition_trace"] = {
            "paddle_raw": {
                **_paddle_raw_trace(dicts, html, rows, structured_table),
                "source_structure_model": source_structure_model,
            },
            "parsed": _table_snapshot(result),
        }
    return result


def _remap_bbox_value(value: Any, offset_x: float, offset_y: float) -> Any:
    if isinstance(value, dict):
        remapped = dict(value)
        if "x" in remapped:
            remapped["x"] = float(remapped.get("x") or 0.0) + offset_x
        if "y" in remapped:
            remapped["y"] = float(remapped.get("y") or 0.0) + offset_y
        return remapped
    if isinstance(value, list) and len(value) >= 4:
        remapped_list = list(value)
        try:
            remapped_list[0] = float(remapped_list[0]) + offset_x
            remapped_list[1] = float(remapped_list[1]) + offset_y
        except (TypeError, ValueError):
            return value
        return remapped_list
    return value


def _remap_candidate_to_roi(candidate: Dict[str, Any], offset_x: float, offset_y: float, row_offset: int) -> Dict[str, Any]:
    remapped = dict(candidate)
    structured = remapped.get("table_structured")
    if isinstance(structured, dict):
        next_structured = dict(structured)
        cells = []
        for cell in structured.get("cells") or []:
            if not isinstance(cell, dict):
                continue
            next_cell = dict(cell)
            next_cell["row"] = int(next_cell.get("row") or 0) + row_offset
            for bbox_key in ("bbox", "box"):
                if bbox_key in next_cell:
                    next_cell[bbox_key] = _remap_bbox_value(next_cell[bbox_key], offset_x, offset_y)
            cells.append(next_cell)
        next_structured["cells"] = cells
        if "bbox" in next_structured:
            next_structured["bbox"] = _remap_bbox_value(next_structured["bbox"], offset_x, offset_y)
        remapped["table_structured"] = next_structured
    segments = []
    for segment in remapped.get("segments") or []:
        if not isinstance(segment, dict):
            continue
        next_segment = dict(segment)
        if "bbox" in next_segment:
            next_segment["bbox"] = _remap_bbox_value(next_segment["bbox"], offset_x, offset_y)
        segments.append(next_segment)
    remapped["segments"] = segments
    return remapped


def _bbox_center(cell: Dict[str, Any]) -> Optional[tuple[float, float]]:
    bbox = cell.get("bbox") or cell.get("box")
    if not isinstance(bbox, dict):
        return None
    try:
        x = float(bbox.get("x") or 0.0)
        y = float(bbox.get("y") or 0.0)
        width = float(bbox.get("width") or 0.0)
        height = float(bbox.get("height") or 0.0)
    except (TypeError, ValueError):
        return None
    return (x + width / 2.0, y + height / 2.0)


def _cluster_values(values: List[float], tolerance: float) -> List[float]:
    if not values:
        return []
    clusters: List[List[float]] = []
    for value in sorted(values):
        if not clusters or abs(value - (sum(clusters[-1]) / len(clusters[-1]))) > tolerance:
            clusters.append([value])
        else:
            clusters[-1].append(value)
    return [sum(cluster) / len(cluster) for cluster in clusters]


def _bbox_edges(cell: Dict[str, Any]) -> Optional[tuple[float, float, float, float]]:
    bbox = cell.get("bbox") or cell.get("box")
    if not isinstance(bbox, dict):
        return None
    try:
        x = float(bbox.get("x") or 0.0)
        y = float(bbox.get("y") or 0.0)
        width = float(bbox.get("width") or 0.0)
        height = float(bbox.get("height") or 0.0)
    except (TypeError, ValueError):
        return None
    return (x, y, x + width, y + height)


def _column_anchors_from_cells(cells: List[Dict[str, Any]]) -> List[Dict[str, float]]:
    positioned = []
    widths = []
    for cell in cells:
        center = _bbox_center(cell)
        edges = _bbox_edges(cell)
        if center is None or edges is None:
            continue
        try:
            col = int(cell.get("col"))
        except (TypeError, ValueError):
            col = -1
        widths.append(max(1.0, edges[2] - edges[0]))
        positioned.append({
            "col": col,
            "colSpan": float(max(1, int(cell.get("colSpan") or cell.get("colspan") or cell.get("col_span") or 1))),
            "center": center[0],
            "left": edges[0],
            "right": edges[2],
        })
    if not positioned:
        return []

    anchors_by_col: Dict[int, List[Dict[str, float]]] = {}
    for item in positioned:
        if item["col"] >= 0 and int(item["colSpan"]) == 1:
            anchors_by_col.setdefault(int(item["col"]), []).append(item)

    anchors: List[Dict[str, float]] = []
    if anchors_by_col:
        for next_col in sorted(anchors_by_col):
            items = anchors_by_col[next_col]
            center = sum(item["center"] for item in items) / len(items)
            anchors.append({"col": float(next_col), "center": center, "left": min(item["left"] for item in items), "right": max(item["right"] for item in items)})
    else:
        tolerance = max(10.0, (sum(widths) / len(widths)) * 0.35 if widths else 10.0)
        for index, center in enumerate(_cluster_values([item["center"] for item in positioned], tolerance)):
            anchors.append({"col": float(index), "center": center, "left": center, "right": center})

    anchors = sorted(anchors, key=lambda item: item["center"])
    if not anchors:
        return []
    centers = [anchor["center"] for anchor in anchors]
    median_gap = float(np.median([right - left for left, right in zip(centers, centers[1:])])) if len(centers) > 1 else (float(np.median(widths)) if widths else 40.0)
    for index, anchor in enumerate(anchors):
        left_bound = (centers[index - 1] + anchor["center"]) / 2.0 if index > 0 else anchor["center"] - median_gap / 2.0
        right_bound = (anchor["center"] + centers[index + 1]) / 2.0 if index < len(anchors) - 1 else anchor["center"] + median_gap / 2.0
        anchor["left"] = min(anchor["left"], left_bound)
        anchor["right"] = max(anchor["right"], right_bound)
        anchor["col"] = float(index)
    return anchors


def _nearest_anchor_index(center_x: float, anchors: List[Dict[str, float]], tolerance: float) -> int:
    for index, anchor in enumerate(anchors):
        if anchor["left"] - tolerance <= center_x <= anchor["right"] + tolerance:
            return index
    return min(range(len(anchors)), key=lambda index: abs(anchors[index]["center"] - center_x))


def _span_for_bbox(left: float, right: float, center_x: float, anchors: List[Dict[str, float]], tolerance: float) -> tuple[int, int]:
    anchor_index = _nearest_anchor_index(center_x, anchors, tolerance)
    covered = [
        index
        for index, anchor in enumerate(anchors)
        if left <= anchor["center"] + tolerance and right >= anchor["center"] - tolerance
    ]
    if len(covered) <= 1:
        return (anchor_index, 1)
    start = min(covered)
    end = max(covered)
    return (start, end - start + 1)


def _box_area(edges: tuple[float, float, float, float]) -> float:
    return max(0.0, edges[2] - edges[0]) * max(0.0, edges[3] - edges[1])


def _edge_overlap(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))


def _axis_overlap_ratio(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    overlap = max(0.0, min(a_end, b_end) - max(a_start, b_start))
    return overlap / max(1.0, min(a_end - a_start, b_end - b_start))


def _bbox_contained_in(inner: tuple[float, float, float, float], outer: tuple[float, float, float, float], tolerance: float) -> bool:
    return (
        inner[0] >= outer[0] - tolerance
        and inner[1] >= outer[1] - tolerance
        and inner[2] <= outer[2] + tolerance
        and inner[3] <= outer[3] + tolerance
    )


def _cell_text_value(cell: Dict[str, Any]) -> str:
    if cell.get("groundTruth") is not None:
        return normalize_ocr_text(cell.get("groundTruth"), cleanup_noise=False)
    return normalize_ocr_text(cell.get("text") or cell.get("ocrText") or "")


def _structured_assignment_quality(structured: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    cells = [cell for cell in (structured or {}).get("cells", []) if isinstance(cell, dict)]
    visible = [cell for cell in cells if not cell.get("hidden")]
    text_cells = [cell for cell in visible if _cell_text_value(cell)]
    owners = [cell for cell in visible if _bbox_edges(cell) is not None]
    if not text_cells:
        return {
            "passed": False,
            "assignment_consistency": 0.0,
            "cross_boundary_ratio": 0.0,
            "unassigned_ratio": 1.0,
            "text_coverage": 0.0,
            "assigned_text_boxes": 0,
            "text_box_count": 0,
            "reason": "no_text_boxes",
        }
    if not owners:
        return {
            "passed": True,
            "assignment_consistency": 1.0,
            "cross_boundary_ratio": 0.0,
            "unassigned_ratio": 0.0,
            "text_coverage": 1.0,
            "assigned_text_boxes": len(text_cells),
            "text_box_count": len(text_cells),
            "reason": "no_cell_geometry",
        }

    assigned = 0
    cross_boundary = 0
    consistency_scores: List[float] = []
    for source in text_cells:
        source_edges = _bbox_edges(source)
        if source_edges is None:
            continue
        source_area = max(1.0, _box_area(source_edges))
        overlaps = []
        for owner in owners:
            owner_edges = _bbox_edges(owner)
            if owner_edges is None:
                continue
            overlap = _edge_overlap(source_edges, owner_edges)
            if overlap <= 0:
                continue
            overlaps.append((overlap / source_area, owner))
        if not overlaps:
            continue
        overlaps.sort(key=lambda item: item[0], reverse=True)
        best_ratio, best_owner = overlaps[0]
        same_position = int(source.get("row") or 0) == int(best_owner.get("row") or 0) and int(source.get("col") or 0) == int(best_owner.get("col") or 0)
        if best_ratio >= 0.2:
            assigned += 1
            consistency_scores.append(best_ratio if same_position else best_ratio * 0.75)
            second_ratio = overlaps[1][0] if len(overlaps) > 1 else 0.0
            if second_ratio > 0.18 or not same_position:
                cross_boundary += 1

    unassigned_ratio = 1.0 - assigned / max(1, len(text_cells))
    cross_boundary_ratio = cross_boundary / max(1, assigned)
    assignment_consistency = sum(consistency_scores) / len(consistency_scores) if consistency_scores else 0.0
    text_coverage = assigned / max(1, len(text_cells))
    passed = (
        text_coverage >= 0.82
        and unassigned_ratio <= 0.18
        and cross_boundary_ratio <= 0.32
        and assignment_consistency >= 0.58
    )
    return {
        "passed": passed,
        "assignment_consistency": round(assignment_consistency, 4),
        "cross_boundary_ratio": round(cross_boundary_ratio, 4),
        "unassigned_ratio": round(unassigned_ratio, 4),
        "text_coverage": round(text_coverage, 4),
        "assigned_text_boxes": assigned,
        "text_box_count": len(text_cells),
        "reason": "passed" if passed else "assignment_quality_failed",
    }


def _owner_cell_for_ocr_bbox(
    source_edges: tuple[float, float, float, float],
    owners: List[Dict[str, Any]],
    tolerance: float,
) -> tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    source_area = max(1.0, _box_area(source_edges))
    ranked: List[tuple[float, Dict[str, Any], Dict[str, float]]] = []
    for owner in owners:
        owner_edges = _bbox_edges(owner)
        if owner_edges is None:
            continue
        overlap_area = _edge_overlap(source_edges, owner_edges)
        overlap_ratio = overlap_area / source_area
        row_alignment = _axis_overlap_ratio(source_edges[1], source_edges[3], owner_edges[1], owner_edges[3])
        col_alignment = _axis_overlap_ratio(source_edges[0], source_edges[2], owner_edges[0], owner_edges[2])
        containment = _bbox_contained_in(source_edges, owner_edges, tolerance)
        if overlap_ratio <= 0 and not containment:
            continue
        score = overlap_ratio * 0.62 + row_alignment * 0.2 + col_alignment * 0.18 + (0.35 if containment else 0.0)
        ranked.append((
            score,
            owner,
            {
                "overlap_ratio": overlap_ratio,
                "row_alignment": row_alignment,
                "col_alignment": col_alignment,
                "containment": 1.0 if containment else 0.0,
            },
        ))
    if not ranked:
        return None, {"reason": "no_overlap"}
    ranked.sort(key=lambda item: item[0], reverse=True)
    best_score, best_owner, best_metrics = ranked[0]
    second_score = ranked[1][0] if len(ranked) > 1 else 0.0
    dominant = best_score >= second_score * 1.12 or best_metrics["containment"] > 0
    usable = best_metrics["overlap_ratio"] >= 0.18 and best_metrics["row_alignment"] >= 0.35 and best_metrics["col_alignment"] >= 0.18
    if not usable or not dominant:
        return None, {
            **best_metrics,
            "score": round(best_score, 4),
            "second_score": round(second_score, 4),
            "reason": "ambiguous_owner",
        }
    return best_owner, {
        **best_metrics,
        "score": round(best_score, 4),
        "second_score": round(second_score, 4),
        "reason": "assigned",
    }


def _infer_axis_boundaries_from_cells(
    cells: List[Dict[str, Any]],
    axis: str,
    count: int,
) -> List[float]:
    if count <= 0:
        return []
    starts: Dict[int, List[float]] = {}
    ends: Dict[int, List[float]] = {}
    for cell in cells:
        edges = _bbox_edges(cell)
        if edges is None:
            continue
        try:
            index = int(cell.get("col" if axis == "x" else "row") or 0)
            span = max(1, int(cell.get("colSpan" if axis == "x" else "rowSpan") or cell.get("colspan" if axis == "x" else "rowspan") or cell.get("col_span" if axis == "x" else "row_span") or 1))
        except (TypeError, ValueError):
            continue
        if span != 1 or index < 0 or index >= count:
            continue
        start_value = edges[0] if axis == "x" else edges[1]
        end_value = edges[2] if axis == "x" else edges[3]
        starts.setdefault(index, []).append(start_value)
        ends.setdefault(index, []).append(end_value)

    centers: List[Optional[float]] = [None for _ in range(count)]
    widths: List[float] = []
    for index in range(count):
        if starts.get(index) and ends.get(index):
            left = float(np.median(starts[index]))
            right = float(np.median(ends[index]))
            centers[index] = (left + right) / 2.0
            widths.append(max(1.0, right - left))

    known = [(index, center) for index, center in enumerate(centers) if center is not None]
    if not known:
        return []
    if len(known) == 1:
        median_width = float(np.median(widths)) if widths else 40.0
        only_index, only_center = known[0]
        for index in range(count):
            centers[index] = float(only_center) + (index - only_index) * median_width
    else:
        for index in range(count):
            if centers[index] is not None:
                continue
            left_known = [item for item in known if item[0] < index]
            right_known = [item for item in known if item[0] > index]
            if left_known and right_known:
                left_index, left_center = left_known[-1]
                right_index, right_center = right_known[0]
                step = (right_center - left_center) / max(1, right_index - left_index)
                centers[index] = left_center + step * (index - left_index)
            elif left_known:
                gaps = [right - left for (_, left), (_, right) in zip(known, known[1:])]
                step = float(np.median(gaps)) if gaps else (float(np.median(widths)) if widths else 40.0)
                left_index, left_center = left_known[-1]
                centers[index] = left_center + step * (index - left_index)
            elif right_known:
                gaps = [right - left for (_, left), (_, right) in zip(known, known[1:])]
                step = float(np.median(gaps)) if gaps else (float(np.median(widths)) if widths else 40.0)
                right_index, right_center = right_known[0]
                centers[index] = right_center - step * (right_index - index)

    numeric_centers = [float(center if center is not None else 0.0) for center in centers]
    boundaries: List[float] = []
    for index, center in enumerate(numeric_centers):
        if index == 0:
            next_gap = numeric_centers[1] - center if len(numeric_centers) > 1 else (float(np.median(widths)) if widths else 40.0)
            boundaries.append(center - next_gap / 2.0)
        if index < len(numeric_centers) - 1:
            boundaries.append((center + numeric_centers[index + 1]) / 2.0)
        else:
            prev_gap = center - numeric_centers[index - 1] if index > 0 else (float(np.median(widths)) if widths else 40.0)
            boundaries.append(center + prev_gap / 2.0)
    return boundaries


def _logical_owner_cells_from_structured(cells: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    visible = [cell for cell in cells if not cell.get("hidden")]
    if not visible:
        return []
    row_count = max(
        int(cell.get("row") or 0) + max(1, int(cell.get("rowSpan") or cell.get("rowspan") or cell.get("row_span") or 1))
        for cell in visible
    )
    col_count = max(
        int(cell.get("col") or 0) + max(1, int(cell.get("colSpan") or cell.get("colspan") or cell.get("col_span") or 1))
        for cell in visible
    )
    row_boundaries = _infer_axis_boundaries_from_cells(visible, "y", row_count)
    col_boundaries = _infer_axis_boundaries_from_cells(visible, "x", col_count)
    if len(row_boundaries) < row_count + 1 or len(col_boundaries) < col_count + 1:
        return [cell for cell in visible if _bbox_edges(cell) is not None]

    owners: List[Dict[str, Any]] = []
    emitted: set[tuple[int, int]] = set()
    for cell in visible:
        try:
            row = int(cell.get("row") or 0)
            col = int(cell.get("col") or 0)
            row_span = max(1, int(cell.get("rowSpan") or cell.get("rowspan") or cell.get("row_span") or 1))
            col_span = max(1, int(cell.get("colSpan") or cell.get("colspan") or cell.get("col_span") or 1))
        except (TypeError, ValueError):
            continue
        key = (row, col)
        if key in emitted:
            continue
        emitted.add(key)
        next_cell = dict(cell)
        next_cell["bbox"] = {
            "x": col_boundaries[col],
            "y": row_boundaries[row],
            "width": max(1.0, col_boundaries[min(col + col_span, len(col_boundaries) - 1)] - col_boundaries[col]),
            "height": max(1.0, row_boundaries[min(row + row_span, len(row_boundaries) - 1)] - row_boundaries[row]),
        }
        next_cell["assignmentOwnerGeometry"] = "logical_grid"
        owners.append(next_cell)
    return owners


def _reassign_ocr_text_to_slanext_cells(candidate: Dict[str, Any]) -> tuple[Dict[str, Any], Dict[str, Any]]:
    structured = candidate.get("table_structured") if isinstance(candidate.get("table_structured"), dict) else None
    source_cells = [cell for cell in (structured or {}).get("cells", []) if isinstance(cell, dict)]
    visible_cells = [cell for cell in source_cells if not cell.get("hidden")]
    owners = _logical_owner_cells_from_structured(visible_cells)
    if not structured or not source_cells or not owners:
        quality = _structured_assignment_quality(structured)
        return candidate, {"attempted": False, "selected": False, "quality": quality, "reason": "missing_structured_cell_geometry"}

    text_sources = [cell for cell in visible_cells if _cell_text_value(cell) and _bbox_edges(cell) is not None]
    if not text_sources:
        quality = _structured_assignment_quality(structured)
        return candidate, {"attempted": False, "selected": False, "quality": quality, "reason": "no_ocr_text_geometry"}

    edge_heights = [max(1.0, (_bbox_edges(cell) or (0, 0, 0, 1))[3] - (_bbox_edges(cell) or (0, 0, 0, 1))[1]) for cell in owners]
    tolerance = max(2.0, float(np.median(edge_heights)) * 0.18 if edge_heights else 2.0)
    grouped: Dict[tuple[int, int], List[Dict[str, Any]]] = {}
    unassigned = 0
    ambiguous = 0
    assignment_metrics: List[Dict[str, Any]] = []
    for source in text_sources:
        source_edges = _bbox_edges(source)
        if source_edges is None:
            unassigned += 1
            continue
        owner, metrics = _owner_cell_for_ocr_bbox(source_edges, owners, tolerance)
        assignment_metrics.append(metrics)
        if owner is None:
            unassigned += 1
            if metrics.get("reason") == "ambiguous_owner":
                ambiguous += 1
            continue
        key = (int(owner.get("row") or 0), int(owner.get("col") or 0))
        grouped.setdefault(key, []).append(source)

    next_cells: List[Dict[str, Any]] = []
    emitted_visible_positions: set[tuple[int, int]] = set()
    for cell in source_cells:
        next_cell = dict(cell)
        if not next_cell.get("hidden"):
            key = (int(next_cell.get("row") or 0), int(next_cell.get("col") or 0))
            if key in emitted_visible_positions:
                continue
            emitted_visible_positions.add(key)
            assigned_sources = grouped.get(key, [])
            if assigned_sources:
                ordered = sorted(assigned_sources, key=lambda item: ((_bbox_edges(item) or (0, 0, 0, 0))[1], (_bbox_edges(item) or (0, 0, 0, 0))[0]))
                text = normalize_ocr_text(" ".join(_cell_text_value(item) for item in ordered if _cell_text_value(item)))
                boxes = [_bbox_edges(item) for item in ordered if _bbox_edges(item) is not None]
                bbox = _merge_bboxes([
                    {"x": edge[0], "y": edge[1], "width": edge[2] - edge[0], "height": edge[3] - edge[1]}
                    for edge in boxes
                    if edge is not None
                ])
                next_cell["text"] = text
                next_cell["ocrText"] = text
                next_cell["groundTruth"] = text
                next_cell["assignmentSource"] = "slanext_geometry_reassignment"
                if bbox:
                    next_cell["bbox"] = bbox
            else:
                next_cell["text"] = ""
                next_cell["ocrText"] = ""
                next_cell["groundTruth"] = ""
        next_cells.append(next_cell)

    next_structured = dict(structured)
    next_structured["cells"] = next_cells
    next_rows = _rows_from_structured_cells_preserve_grid(next_cells)
    next_structured["rows"] = next_rows
    reassigned = dict(candidate)
    reassigned["table_structured"] = next_structured
    reassigned["table_rows"] = next_rows
    reassigned["text"] = _markdown_table(next_rows)
    debug = reassigned.get("table_debug") if isinstance(reassigned.get("table_debug"), dict) else {}
    reassigned["table_debug"] = dict(debug)
    quality = _structured_assignment_quality(next_structured)
    reassignment_debug = {
        "attempted": True,
        "selected": bool(quality.get("passed")),
        "quality": quality,
        "source_text_boxes": len(text_sources),
        "assigned_text_boxes": len(text_sources) - unassigned,
        "unassigned_text_boxes": unassigned,
        "ambiguous_text_boxes": ambiguous,
        "average_overlap": round(
            sum(float(item.get("overlap_ratio") or 0.0) for item in assignment_metrics) / max(1, len(assignment_metrics)),
            4,
        ),
        "reason": "passed" if quality.get("passed") else "quality_gate_failed",
    }
    reassigned["table_debug"]["ocr_cell_assignment"] = reassignment_debug
    return reassigned, reassignment_debug


def _structured_row_count(structured: Dict[str, Any]) -> int:
    cells = [cell for cell in structured.get("cells") or [] if isinstance(cell, dict)]
    if cells:
        return max(
            int(cell.get("row") or 0) + max(1, int(cell.get("rowSpan") or cell.get("rowspan") or cell.get("row_span") or 1))
            for cell in cells
        )
    rows = structured.get("rows")
    return len(rows) if isinstance(rows, list) else 0


def _structured_col_count(structured: Dict[str, Any]) -> int:
    cells = [cell for cell in structured.get("cells") or [] if isinstance(cell, dict)]
    if cells:
        return max(
            int(cell.get("col") or 0) + max(1, int(cell.get("colSpan") or cell.get("colspan") or cell.get("col_span") or 1))
            for cell in cells
        )
    rows = structured.get("rows")
    if isinstance(rows, list):
        return max((len(row) for row in rows if isinstance(row, list)), default=0)
    return 0


def _tail_summary_start(structured: Dict[str, Any], header_row_count: int, row_count: int, col_count: int) -> int:
    cells = [cell for cell in structured.get("cells") or [] if isinstance(cell, dict) and not cell.get("hidden")]
    text_counts: Dict[int, int] = {}
    span_rows: set[int] = set()
    for cell in cells:
        try:
            row = int(cell.get("row") or 0)
            col_span = max(1, int(cell.get("colSpan") or cell.get("colspan") or cell.get("col_span") or 1))
            row_span = max(1, int(cell.get("rowSpan") or cell.get("rowspan") or cell.get("row_span") or 1))
        except (TypeError, ValueError):
            continue
        if row < header_row_count:
            continue
        if _cell_text_value(cell):
            text_counts[row] = text_counts.get(row, 0) + 1
        if col_span > 1 or row_span > 1:
            span_rows.add(row)

    summary_start = row_count
    for row in range(row_count - 1, header_row_count - 1, -1):
        populated = text_counts.get(row, 0)
        sparse = col_count >= 3 and 0 < populated <= max(1, int(col_count * 0.45))
        has_span = row in span_rows
        if sparse or has_span:
            summary_start = row
            continue
        break
    return summary_start


def _cluster_ocr_rows_by_y(ocr_cells: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    if not ocr_cells:
        return []
    heights = [float(cell.get("height") or 0.0) for cell in ocr_cells if float(cell.get("height") or 0.0) > 0]
    median_height = float(np.median(heights)) if heights else 12.0
    tolerance = max(4.0, median_height * 0.62)
    clusters: List[List[Dict[str, Any]]] = []
    for cell in sorted(ocr_cells, key=lambda item: (float(item.get("center_y") or 0.0), float(item.get("center_x") or 0.0))):
        center_y = float(cell.get("center_y") or 0.0)
        if not clusters:
            clusters.append([cell])
            continue
        previous_center = sum(float(item.get("center_y") or 0.0) for item in clusters[-1]) / len(clusters[-1])
        if abs(center_y - previous_center) <= tolerance:
            clusters[-1].append(cell)
        else:
            clusters.append([cell])
    return [sorted(cluster, key=lambda item: float(item.get("center_x") or 0.0)) for cluster in clusters]


def _row_cluster_alignment_support(
    clusters: List[List[Dict[str, Any]]],
    col_boundaries: List[float],
    x_tolerance: float,
) -> tuple[int, float]:
    if len(col_boundaries) < 3:
        return (0, 0.0)
    supporting_columns = 0
    column_scores: List[float] = []
    for col_index in range(len(col_boundaries) - 1):
        hits = 0
        for cluster in clusters:
            for cell in cluster:
                left = float(cell.get("x") or 0.0)
                right = left + float(cell.get("width") or 0.0)
                dominant = _dominant_interval_index(left, right, col_boundaries, x_tolerance)
                if dominant == col_index:
                    hits += 1
                    break
        score = hits / max(1, len(clusters))
        if hits >= 2 and score >= 0.45:
            supporting_columns += 1
        column_scores.append(score)
    alignment_score = sum(column_scores) / len(column_scores) if column_scores else 0.0
    return (supporting_columns, round(_clamp01(alignment_score), 4))


def _cluster_ocr_columns_by_x(ocr_cells: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    if not ocr_cells:
        return []
    widths = [float(cell.get("width") or 0.0) for cell in ocr_cells if float(cell.get("width") or 0.0) > 0]
    median_width = float(np.median(widths)) if widths else 24.0
    tolerance = max(6.0, median_width * 0.72)
    clusters: List[List[Dict[str, Any]]] = []
    for cell in sorted(ocr_cells, key=lambda item: (float(item.get("center_x") or 0.0), float(item.get("center_y") or 0.0))):
        center_x = float(cell.get("center_x") or 0.0)
        if not clusters:
            clusters.append([cell])
            continue
        previous_center = sum(float(item.get("center_x") or 0.0) for item in clusters[-1]) / len(clusters[-1])
        if abs(center_x - previous_center) <= tolerance:
            clusters[-1].append(cell)
        else:
            clusters.append([cell])
    return [sorted(cluster, key=lambda item: float(item.get("center_y") or 0.0)) for cluster in clusters]


def _column_cluster_alignment_support(
    clusters: List[List[Dict[str, Any]]],
    row_boundaries: List[float],
    y_tolerance: float,
) -> tuple[int, float]:
    if len(row_boundaries) < 3:
        return (0, 0.0)
    supporting_rows = 0
    row_scores: List[float] = []
    for row_index in range(len(row_boundaries) - 1):
        hits = 0
        for cluster in clusters:
            for cell in cluster:
                top = float(cell.get("y") or 0.0)
                bottom = top + float(cell.get("height") or 0.0)
                dominant = _dominant_interval_index(top, bottom, row_boundaries, y_tolerance)
                if dominant == row_index:
                    hits += 1
                    break
        score = hits / max(1, len(clusters))
        if hits >= 2 and score >= 0.45:
            supporting_rows += 1
        row_scores.append(score)
    alignment_score = sum(row_scores) / len(row_scores) if row_scores else 0.0
    return (supporting_rows, round(_clamp01(alignment_score), 4))


def _boundaries_from_cluster_centers(centers: List[float], fallback_start: float, fallback_end: float) -> List[float]:
    if not centers:
        return [fallback_start, fallback_end]
    ordered = sorted(centers)
    boundaries: List[float] = []
    for index, center in enumerate(ordered):
        if index == 0:
            next_gap = ordered[1] - center if len(ordered) > 1 else max(1.0, fallback_end - fallback_start)
            boundaries.append(max(fallback_start, center - next_gap / 2.0))
        if index < len(ordered) - 1:
            boundaries.append((center + ordered[index + 1]) / 2.0)
        else:
            prev_gap = center - ordered[index - 1] if index > 0 else max(1.0, fallback_end - fallback_start)
            boundaries.append(min(fallback_end, center + prev_gap / 2.0))
    boundaries[0] = min(boundaries[0], fallback_start)
    boundaries[-1] = max(boundaries[-1], fallback_end)
    return sorted(boundaries)


def _ocr_clusters_for_existing_rows(
    ocr_cells: List[Dict[str, Any]],
    row_boundaries: List[float],
    header_row_count: int,
    summary_start: int,
    y_tolerance: float,
) -> List[List[Dict[str, Any]]]:
    clusters: List[List[Dict[str, Any]]] = []
    for row in range(header_row_count, summary_start):
        row_cells = []
        for cell in ocr_cells:
            top = float(cell.get("y") or 0.0)
            bottom = top + float(cell.get("height") or 0.0)
            if _dominant_interval_index(top, bottom, row_boundaries, y_tolerance) == row:
                row_cells.append(cell)
        clusters.append(sorted(row_cells, key=lambda item: float(item.get("center_x") or 0.0)))
    return clusters


def _recover_slanext_structure_collapse(candidate: Dict[str, Any], image: np.ndarray) -> tuple[Dict[str, Any], Dict[str, Any]]:
    candidate_debug = candidate.get("table_debug") if isinstance(candidate.get("table_debug"), dict) else {}
    source_structure_model = str(candidate_debug.get("source_structure_model") or "not_available")
    base_debug: Dict[str, Any] = {
        "attempted": False,
        "source_structure_model": source_structure_model,
        "row_collapse": False,
        "column_collapse": False,
        "x_cluster_count": 0,
        "y_cluster_count": 0,
        "supporting_columns": 0,
        "supporting_rows": 0,
        "alignment_score": 0.0,
        "recovery_axis": "none",
        "recovery_success": False,
        "selected": False,
        "recovered_row_count": 0,
        "recovered_column_count": 0,
    }
    structured = candidate.get("table_structured") if isinstance(candidate.get("table_structured"), dict) else None
    if image is None or image.size == 0 or not structured:
        return candidate, {**base_debug, "reason": "missing_structured_or_image"}

    source_cells = [cell for cell in structured.get("cells") or [] if isinstance(cell, dict)]
    visible_cells = [cell for cell in source_cells if not cell.get("hidden")]
    if not source_cells or not visible_cells:
        return candidate, {**base_debug, "reason": "missing_cells"}

    row_count = _structured_row_count(structured)
    col_count = _structured_col_count(structured)
    header_row_count = max(1, int(structured.get("headerRowCount") or structured.get("header_row_count") or 1))
    summary_start = _tail_summary_start(structured, header_row_count, row_count, col_count)
    body_row_count = max(0, summary_start - header_row_count)
    if body_row_count <= 0 or col_count < 2:
        return candidate, {
            **base_debug,
            "attempted": True,
            "body_row_count": body_row_count,
            "body_column_count": col_count,
            "recovered_row_count": row_count,
            "recovered_column_count": col_count,
            "reason": "no_body_region",
        }

    row_boundaries = _infer_axis_boundaries_from_cells(visible_cells, "y", row_count)
    col_boundaries = _infer_axis_boundaries_from_cells(visible_cells, "x", col_count)
    if len(row_boundaries) < row_count + 1 or len(col_boundaries) < col_count + 1:
        return candidate, {
            **base_debug,
            "attempted": True,
            "body_row_count": body_row_count,
            "body_column_count": col_count,
            "recovered_row_count": row_count,
            "recovered_column_count": col_count,
            "reason": "missing_boundaries",
        }

    body_top = row_boundaries[header_row_count]
    body_bottom = row_boundaries[summary_start] if summary_start < len(row_boundaries) else row_boundaries[-1]
    try:
        ocr_cells, _, ocr_debug = _ocr_cells_from_text_detection(image, "slanext_row_collapse")
    except Exception as error:
        return candidate, {
            **base_debug,
            "attempted": True,
            "body_row_count": body_row_count,
            "body_column_count": col_count,
            "recovered_row_count": row_count,
            "recovered_column_count": col_count,
            "reason": f"ocr_geometry_failed:{error}",
        }

    ocr_heights = [float(cell.get("height") or 0.0) for cell in ocr_cells if float(cell.get("height") or 0.0) > 0]
    body_margin = max(2.0, (float(np.median(ocr_heights)) if ocr_heights else 12.0) * 0.85)
    body_ocr_cells = [
        cell
        for cell in ocr_cells
        if body_top - body_margin <= float(cell.get("center_y") or 0.0) <= body_bottom + body_margin
    ]
    clusters = _cluster_ocr_rows_by_y(body_ocr_cells)
    y_cluster_count = len(clusters)
    column_clusters = _cluster_ocr_columns_by_x(body_ocr_cells)
    x_cluster_count = len(column_clusters)
    widths = [float(cell.get("width") or 0.0) for cell in body_ocr_cells if float(cell.get("width") or 0.0) > 0]
    heights = [float(cell.get("height") or 0.0) for cell in body_ocr_cells if float(cell.get("height") or 0.0) > 0]
    x_tolerance = max(4.0, (float(np.median(widths)) if widths else 24.0) * 0.16)
    y_tolerance = max(4.0, (float(np.median(heights)) if heights else 12.0) * 0.35)
    supporting_columns, row_alignment_score = _row_cluster_alignment_support(clusters, col_boundaries, x_tolerance)
    supporting_rows, column_alignment_score = _column_cluster_alignment_support(column_clusters, row_boundaries, y_tolerance)
    row_collapse = (
        y_cluster_count >= body_row_count + 2
        or (body_row_count > 0 and y_cluster_count / max(1, body_row_count) >= 1.45 and y_cluster_count > body_row_count)
    ) and supporting_columns >= 2 and row_alignment_score >= 0.45
    column_collapse = (
        x_cluster_count >= col_count + 2
        or (col_count > 0 and x_cluster_count / max(1, col_count) >= 1.45 and x_cluster_count > col_count)
    ) and supporting_rows >= 2 and column_alignment_score >= 0.45
    alignment_score = round(max(row_alignment_score if row_collapse else 0.0, column_alignment_score if column_collapse else 0.0), 4)
    recovery_axis = "both" if row_collapse and column_collapse else "row" if row_collapse else "column" if column_collapse else "none"
    debug: Dict[str, Any] = {
        **base_debug,
        "attempted": True,
        "body_row_count": body_row_count,
        "body_column_count": col_count,
        "y_cluster_count": y_cluster_count,
        "x_cluster_count": x_cluster_count,
        "supporting_columns": supporting_columns,
        "supporting_rows": supporting_rows,
        "alignment_score": alignment_score,
        "row_alignment_score": row_alignment_score,
        "column_alignment_score": column_alignment_score,
        "row_collapse": row_collapse,
        "column_collapse": column_collapse,
        "suspected_row_collapse": row_collapse,
        "suspected_column_collapse": column_collapse,
        "recovery_axis": recovery_axis,
        "recovery_success": False,
        "recovered_row_count": row_count,
        "recovered_column_count": col_count,
        "summary_start_row": summary_start if summary_start < row_count else None,
        "ocr": ocr_debug,
    }
    if not row_collapse and not column_collapse:
        debug["reason"] = "structure_collapse_not_supported"
        return candidate, debug

    final_row_clusters = clusters if row_collapse else _ocr_clusters_for_existing_rows(body_ocr_cells, row_boundaries, header_row_count, summary_start, y_tolerance)
    if column_collapse:
        x_centers = [sum(float(item.get("center_x") or 0.0) for item in cluster) / max(1, len(cluster)) for cluster in column_clusters]
        final_col_boundaries = _boundaries_from_cluster_centers(x_centers, col_boundaries[0], col_boundaries[-1])
    else:
        final_col_boundaries = col_boundaries
    recovered_col_count = max(1, len(final_col_boundaries) - 1)
    recovered_body_rows = [["" for _ in range(recovered_col_count)] for _ in final_row_clusters]
    recovered_cells: List[Dict[str, Any]] = []
    confidence_values: List[float] = []
    for recovered_row, cluster in enumerate(final_row_clusters):
        grouped: Dict[int, List[Dict[str, Any]]] = {}
        for cell in cluster:
            left = float(cell.get("x") or 0.0)
            right = left + float(cell.get("width") or 0.0)
            col_index = _dominant_interval_index(left, right, final_col_boundaries, x_tolerance)
            grouped.setdefault(col_index, []).append(cell)
        for col_index in range(recovered_col_count):
            parts = sorted(grouped.get(col_index, []), key=lambda item: (float(item.get("y") or 0.0), float(item.get("x") or 0.0)))
            text = normalize_ocr_text(" ".join(str(item.get("text") or "").strip() for item in parts if str(item.get("text") or "").strip()))
            recovered_body_rows[recovered_row][col_index] = text
            boxes = [item.get("bbox") for item in parts if isinstance(item.get("bbox"), dict)]
            bbox = _merge_bboxes(boxes) if boxes else {
                "x": final_col_boundaries[col_index],
                "y": float(sum(float(item.get("center_y") or 0.0) for item in cluster) / max(1, len(cluster))) if cluster else body_top,
                "width": max(1.0, final_col_boundaries[col_index + 1] - final_col_boundaries[col_index]),
                "height": 1.0,
            }
            confidences = [float(item.get("confidence") or 0.0) for item in parts if str(item.get("text") or "").strip()]
            if confidences:
                confidence_values.extend(confidences)
            recovered_cells.append({
                "row": header_row_count + recovered_row,
                "col": col_index,
                "text": text,
                "rowSpan": 1,
                "colSpan": 1,
                "ocrText": text,
                "groundTruth": text,
                "confidence": sum(confidences) / len(confidences) if confidences else 0.0,
                "bbox": bbox,
                "assignmentSource": "structure_collapse_recovery",
            })

    header_cells = []
    for cell in source_cells:
        try:
            row = int(cell.get("row") or 0)
            col = int(cell.get("col") or 0)
            col_span = max(1, int(cell.get("colSpan") or cell.get("colspan") or cell.get("col_span") or 1))
        except (TypeError, ValueError):
            continue
        if row >= header_row_count:
            continue
        next_cell = dict(cell)
        if column_collapse and col == 0 and col_span >= col_count:
            next_cell["colSpan"] = recovered_col_count
        header_cells.append(next_cell)
    summary_cells: List[Dict[str, Any]] = []
    row_shift = len(final_row_clusters) - body_row_count
    for cell in source_cells:
        try:
            row = int(cell.get("row") or 0)
            col = int(cell.get("col") or 0)
            col_span = max(1, int(cell.get("colSpan") or cell.get("colspan") or cell.get("col_span") or 1))
        except (TypeError, ValueError):
            continue
        if row < summary_start:
            continue
        next_cell = dict(cell)
        next_cell["row"] = row + row_shift
        if column_collapse and col == 0 and col_span >= col_count:
            next_cell["colSpan"] = recovered_col_count
        summary_cells.append(next_cell)

    next_cells = [*header_cells, *recovered_cells, *summary_cells]
    next_structured = dict(structured)
    next_structured["cells"] = sorted(next_cells, key=lambda item: (int(item.get("row") or 0), int(item.get("col") or 0), bool(item.get("hidden"))))
    next_rows = _rows_from_structured_cells_preserve_grid(next_structured["cells"])
    next_structured["rows"] = next_rows
    recovered = dict(candidate)
    recovered["table_structured"] = next_structured
    recovered["table_rows"] = next_rows
    recovered["text"] = _markdown_table(next_rows)
    if confidence_values:
        recovered["confidence"] = max(float(candidate.get("confidence") or 0.0), sum(confidence_values) / len(confidence_values))
    recovered_debug = recovered.get("table_debug") if isinstance(recovered.get("table_debug"), dict) else {}
    recovered["table_debug"] = dict(recovered_debug)
    debug["recovered_row_count"] = len(next_rows)
    debug["recovered_column_count"] = max((len(row) for row in next_rows), default=0)
    debug["recovered_body_row_count"] = len(final_row_clusters)
    recovery_quality = _calculate_table_quality(next_rows, next_structured, "slanext_structure_collapse_recovery")
    recovery_assignment_quality = _structured_assignment_quality(next_structured)
    recovery_confident = (
        bool(recovery_quality.get("usable_shape"))
        and float(recovery_quality.get("score") or 0.0) >= 0.48
        and bool(recovery_assignment_quality.get("passed"))
        and alignment_score >= 0.45
    )
    debug["quality"] = recovery_quality
    debug["assignment_quality"] = recovery_assignment_quality
    debug["selected"] = recovery_confident
    debug["recovery_success"] = recovery_confident
    if not recovery_confident:
        debug["reason"] = "recovery_quality_gate_failed"
        return candidate, debug
    debug["final_column_boundaries"] = final_col_boundaries
    recovered["table_debug"]["structure_collapse_recovery"] = debug
    recovered["table_debug"]["row_collapse_recovery"] = debug
    return recovered, debug


def _recover_slanext_row_collapse(candidate: Dict[str, Any], image: np.ndarray) -> tuple[Dict[str, Any], Dict[str, Any]]:
    return _recover_slanext_structure_collapse(candidate, image)


def _geometry_table_from_cells(cells: List[Dict[str, Any]], fallback_rows: List[List[str]]) -> Optional[tuple[List[List[str]], List[Dict[str, Any]], List[Dict[str, float]]]]:
    positioned = []
    heights = []
    for cell in cells:
        center = _bbox_center(cell)
        edges = _bbox_edges(cell)
        if center is None or edges is None:
            continue
        heights.append(max(1.0, edges[3] - edges[1]))
        positioned.append((center[0], center[1], edges, cell))
    if not positioned:
        return None

    y_tolerance = max(8.0, (sum(heights) / len(heights)) * 0.55 if heights else 8.0)
    row_centers = _cluster_values([item[1] for item in positioned], y_tolerance)
    anchors = _column_anchors_from_cells(cells)
    if not row_centers or not anchors:
        return None

    anchor_gap = float(np.median([right["center"] - left["center"] for left, right in zip(anchors, anchors[1:])])) if len(anchors) > 1 else 40.0
    x_tolerance = max(8.0, anchor_gap * 0.18)
    rows = [["" for _ in anchors] for _ in row_centers]
    assigned_cells: List[Dict[str, Any]] = []
    for center_x, center_y, edges, cell in positioned:
        row_index = min(range(len(row_centers)), key=lambda index: abs(row_centers[index] - center_y))
        col_index, inferred_col_span = _span_for_bbox(edges[0], edges[2], center_x, anchors, x_tolerance)
        if cell.get("groundTruth") is not None:
            text = normalize_ocr_text(cell.get("groundTruth"), cleanup_noise=False)
        else:
            text = normalize_ocr_text(cell.get("text") or cell.get("ocrText") or "")
        if not text:
            continue
        rows[row_index][col_index] = f"{rows[row_index][col_index]} {text}".strip() if rows[row_index][col_index] else text
        source_col_span = max(1, int(cell.get("colSpan") or cell.get("colspan") or cell.get("col_span") or 1))
        source_row_span = max(1, int(cell.get("rowSpan") or cell.get("rowspan") or cell.get("row_span") or 1))
        col_span = inferred_col_span if inferred_col_span > 1 else source_col_span
        assigned_cells.append({
            **cell,
            "row": row_index,
            "col": col_index,
            "text": text,
            "ocrText": normalize_ocr_text(cell.get("ocrText") or cell.get("text") or text),
            "groundTruth": text,
            "rowSpan": source_row_span,
            "colSpan": col_span,
        })

    rows = normalize_table_rows(rows)
    if not rows:
        return None
    return (rows, assigned_cells, anchors)


def _section_from_region_candidate(candidate: Dict[str, Any], region: Dict[str, Any], region_id: str) -> Dict[str, Any]:
    structured = candidate.get("table_structured") if isinstance(candidate.get("table_structured"), dict) else {}
    source_cells = [dict(cell) for cell in (structured.get("cells") if isinstance(structured, dict) else []) or [] if isinstance(cell, dict)]
    for cell in source_cells:
        cell["regionId"] = region_id
    source_rows = (
        _rows_from_structured_cells_preserve_grid(source_cells)
        or normalize_table_rows(structured.get("rows") if isinstance(structured, dict) else [])
        or normalize_table_rows(candidate.get("table_rows") or [])
    )

    geometry_table = _geometry_table_from_cells(source_cells, source_rows)
    geometry_rows = geometry_table[0] if geometry_table else None
    geometry_cells = geometry_table[1] if geometry_table else None
    column_anchors = geometry_table[2] if geometry_table else []
    local_rows = geometry_rows or source_rows
    local_structured = (
        {
            "rows": local_rows,
            "cells": geometry_cells,
            "headerRowCount": int(structured.get("headerRowCount") or structured.get("header_row_count") or 1) if isinstance(structured, dict) else 1,
            "columnAnchors": column_anchors,
        }
        if geometry_cells
        else (_structured_from_rows(local_rows, source_cells) if local_rows else None)
    )
    if isinstance(local_structured, dict):
        for cell in local_structured.get("cells") or []:
            if isinstance(cell, dict):
                cell["regionId"] = region_id

    local_column_count = max((len(row) for row in local_rows), default=0)
    local_columns = [
        {
            "col": index,
            "label": f"Column {index + 1}",
            **(
                {
                    "center": column_anchors[index].get("center"),
                    "left": column_anchors[index].get("left"),
                    "right": column_anchors[index].get("right"),
                }
                if index < len(column_anchors)
                else {}
            ),
        }
        for index in range(local_column_count)
    ]
    return {
        "regionId": region_id,
        "type": region.get("type") or "grid",
        "bbox": region.get("bbox"),
        "confidence": candidate.get("confidence", 0.0),
        "columns": local_columns,
        "rows": local_rows,
        "cells": (local_structured or {}).get("cells", []),
        "table_structured": local_structured,
        "table_html": candidate.get("table_html"),
        "reconstruction": {
            "method": "column_anchor_reconstruction" if geometry_rows else "slanext_region_structure",
            "used_geometry": bool(geometry_rows),
            "local_column_count": local_column_count,
            "column_anchor_count": len(column_anchors),
            "source_row_count": len(source_rows),
            "row_count": len(local_rows),
        },
    }


def _merge_region_candidates(region_candidates: List[Dict[str, Any]], semi_analysis: Dict[str, Any]) -> Dict[str, Any]:
    merge_started = time.perf_counter()
    merged_rows: List[List[str]] = []
    merged_cells: List[Dict[str, Any]] = []
    merged_segments: List[Dict[str, Any]] = []
    attempts: List[Dict[str, Any]] = []
    html_parts: List[str] = []
    candidates_for_competition: List[Dict[str, Any]] = []
    header_row_count = 0
    for section_index, candidate in enumerate(region_candidates):
        candidate_region = candidate.get("table_debug", {}).get("region") if isinstance(candidate.get("table_debug"), dict) else {}
        region_id = str(candidate_region.get("regionId") or candidate_region.get("region_id") or f"region_{section_index + 1}")
        section = _section_from_region_candidate(candidate, candidate_region if isinstance(candidate_region, dict) else {}, region_id)
        structured = section.get("table_structured") if isinstance(section.get("table_structured"), dict) else candidate.get("table_structured")
        rows = normalize_table_rows(section.get("rows") or [])
        row_offset = len(merged_rows)
        merged_rows.extend(rows)
        if isinstance(structured, dict):
            header_row_count += int(structured.get("headerRowCount") or structured.get("header_row_count") or 0)
            for cell in structured.get("cells") or []:
                if isinstance(cell, dict):
                    next_cell = dict(cell)
                    next_cell["row"] = int(next_cell.get("row") or 0) + row_offset
                    next_cell["regionId"] = region_id
                    merged_cells.append(next_cell)
        merged_segments.extend(segment for segment in candidate.get("segments") or [] if isinstance(segment, dict))
        attempts.extend(attempt for attempt in candidate.get("attempts") or [] if isinstance(attempt, dict))
        if candidate.get("table_html"):
            html_parts.append(str(candidate.get("table_html")))
        candidates_for_competition.append(candidate)

    structured = {
        "rows": merged_rows,
        "cells": merged_cells,
        "headerRowCount": header_row_count or 1,
        "postProcessing": "semi_structured_region_merge",
    } if merged_rows or merged_cells else None
    result = {
        "text": _markdown_table(merged_rows),
        "confidence": 0.0,
        "segments": merged_segments,
        "attempts": attempts or [{"step": "semi_structured_region_merge", "region_count": len(region_candidates)}],
        "preprocessing": "semi_structured_table_regions",
        "engine": "table_recognition_v2",
        "model": _TABLE_MODEL_NAME,
        "table_html": "\n".join(html_parts) if html_parts else None,
        "table_rows": merged_rows,
        "table_structured": structured,
        "table_debug": {
            "status": "semi_structured_merged" if merged_rows else "semi_structured_empty",
            "region_count": len(region_candidates),
        },
        "table_semi_analysis": semi_analysis,
    }
    merged_candidate = _build_table_candidate(result, "semi_structured_regions")
    selected, reason = _select_best_table_candidate([merged_candidate, *candidates_for_competition])
    if selected is not merged_candidate:
        selected = merged_candidate
        reason = "semi_structured_region_merge"
    merged = _attach_candidate_competition(selected, [merged_candidate, *candidates_for_competition], reason)
    logger.info(
        "Table Recognition phase timing: phase=Merge regions=%s rows=%s cells=%s elapsed=%.3fs",
        len(region_candidates),
        len(merged_rows),
        len(merged_cells),
        time.perf_counter() - merge_started,
    )
    return merged


def _try_semi_structured_table(
    image: np.ndarray,
    model: Any,
    started: float,
    analysis: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    semi_started = time.perf_counter()
    analysis = analysis if isinstance(analysis, dict) else analyze_table_regions(image)
    is_forced_whole_roi = bool(analysis.get("forced"))
    topology_change_ratio = float(analysis.get("topology_change_ratio") or 0.0)
    if (
        not analysis.get("detected")
        or (
            not is_forced_whole_roi
            and (
                float(analysis.get("confidence") or 0.0) < _SEMI_TABLE_MIN_CONFIDENCE
                or topology_change_ratio < _SEMI_TABLE_MIN_TOPOLOGY_CHANGE_RATIO
            )
        )
    ):
        logger.info(
            "Table Recognition phase timing: phase=Region Inference skipped reason=%s confidence=%s topology_change_ratio=%s elapsed=%.3fs",
            analysis.get("reason") if isinstance(analysis, dict) else "not_detected",
            analysis.get("confidence") if isinstance(analysis, dict) else None,
            analysis.get("topology_change_ratio") if isinstance(analysis, dict) else None,
            time.perf_counter() - semi_started,
        )
        return None
    regions = [region for region in analysis.get("regions") or [] if isinstance(region, dict)]
    if len(regions) < 2 and not is_forced_whole_roi:
        logger.info(
            "Table Recognition phase timing: phase=Region Inference skipped reason=not_enough_regions regions=%s elapsed=%.3fs",
            len(regions),
            time.perf_counter() - semi_started,
        )
        return None

    normalizer = _normalize_semi_table_grid(image, analysis)
    if isinstance(normalizer, dict) and normalizer.get("ok") and isinstance(normalizer.get("image"), np.ndarray):
        normalized_image = normalizer["image"]
        normalizer_debug = normalizer.get("debug") if isinstance(normalizer.get("debug"), dict) else {}
        try:
            model = model or _load_table_model()
            output = _predict_table_model(model, normalized_image)
            result = _slanext_result_from_output(
                output,
                normalized_image,
                semi_started,
                {
                    "type": "synthetic_grid",
                    "bbox": {"x": 0, "y": 0, "width": int(image.shape[1]), "height": int(image.shape[0])},
                    "grid_normalizer": normalizer_debug,
                },
            )
            result["table_selected_method"] = "grid_normalized_slanext"
            result.setdefault("table_semi_analysis", dict(analysis))
            if isinstance(result["table_semi_analysis"], dict):
                result["table_semi_analysis"].update({
                    "detected": True,
                    "merge_status": "grid_normalized_slanext",
                    "region_processing": "grid_normalizer",
                    "grid_normalizer": normalizer_debug,
                    "model_reuse": {"enabled": True, "model_inference_count": 1},
                })
            result.setdefault("table_debug", {})
            if isinstance(result["table_debug"], dict):
                result["table_debug"]["status"] = "grid_normalized_slanext"
                result["table_debug"]["grid_normalizer"] = normalizer_debug
                result["table_debug"]["model_inference_count"] = 1
            logger.info(
                "Table Recognition phase timing: phase=Grid Normalizer complete drawn_h=%s drawn_v=%s model_inferences=1 elapsed=%.3fs",
                normalizer_debug.get("drawn_lines", {}).get("horizontal") if isinstance(normalizer_debug.get("drawn_lines"), dict) else None,
                normalizer_debug.get("drawn_lines", {}).get("vertical") if isinstance(normalizer_debug.get("drawn_lines"), dict) else None,
                time.perf_counter() - semi_started,
            )
            return result
        except Exception as error:
            logger.info("Grid-normalized SLANeXt failed, falling back to coordinate semi flow: %s", error)
    else:
        logger.info(
            "Table Recognition phase timing: phase=Grid Normalizer skipped reason=%s elapsed=%.3fs",
            normalizer.get("reason") if isinstance(normalizer, dict) else "not_available",
            time.perf_counter() - semi_started,
        )

    result = _recognize_coordinate_based_semi_table(image, analysis)
    if not result:
        logger.info(
            "Table Recognition phase timing: phase=Region Inference no_coordinate_result regions=%s model_inferences=0 elapsed=%.3fs",
            len(regions),
            time.perf_counter() - semi_started,
        )
        return None
    logger.info(
        "Table Recognition phase timing: phase=Region Inference complete path=coordinate_based regions=%s model_inferences=0 elapsed=%.3fs",
        len(regions),
        time.perf_counter() - semi_started,
    )
    return result


def _whole_roi_semi_analysis(analysis: Optional[Dict[str, Any]], merge_status: str = "whole_roi_fallback") -> Dict[str, Any]:
    if isinstance(analysis, dict):
        result = dict(analysis)
    else:
        result = {"detected": False, "confidence": 0.0, "regions": [], "reason": "not_analyzed"}
    result.setdefault("detected", False)
    result.setdefault("confidence", 0.0)
    result.setdefault("regions", [])
    result.setdefault("merge_status", merge_status)
    return result


def _forced_whole_roi_semi_analysis(image: np.ndarray, previous_analysis: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    height, width = image.shape[:2]
    base = _whole_roi_semi_analysis(previous_analysis, merge_status="forced_after_empty_slanext")
    base.update(
        {
            "detected": True,
            "confidence": max(0.72, float(base.get("confidence") or 0.0)),
            "reason": "forced_after_empty_slanext",
            "regions": [
                {
                    "type": "grid",
                    "bbox": {"x": 0, "y": 0, "width": int(width), "height": int(height)},
                    "confidence": max(0.72, float(base.get("confidence") or 0.0)),
                    "forced": True,
                }
            ],
            "forced": True,
        }
    )
    return base


def _try_forced_semi_after_empty_slanext(
    image: np.ndarray,
    previous_analysis: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    forced_analysis = _forced_whole_roi_semi_analysis(image, previous_analysis)
    try:
        result = _try_semi_structured_table(image, None, time.perf_counter(), forced_analysis)
    except Exception as error:
        logger.info("Forced grid-normalized semi failed before coordinate fallback: %s", error)
        result = None
    if not result:
        result = _recognize_coordinate_based_semi_table(image, forced_analysis)
    if not result:
        return None
    candidate = _build_table_candidate(result, "coordinate_based_semi_forced")
    reliability = _semi_result_reliability(candidate, forced_analysis)
    candidate.setdefault("table_debug", {})
    if isinstance(candidate["table_debug"], dict):
        candidate["table_debug"]["semi_reliability"] = reliability
        candidate["table_debug"]["forced_after_empty_slanext"] = True
    candidate.setdefault("table_semi_analysis", forced_analysis)
    if isinstance(candidate["table_semi_analysis"], dict):
        candidate["table_semi_analysis"]["reliability"] = reliability
    if not reliability["passed"]:
        return candidate
    return _attach_candidate_competition(candidate, [candidate], "forced_semi_after_empty_slanext")


def recognize_table_v2_local(image: np.ndarray) -> Dict[str, Any]:
    started = time.perf_counter()
    model_inference_count = 0
    ocr_inference_count = 0
    if image is None or image.size == 0:
        return {
            "text": "",
            "confidence": 0.0,
            "segments": [],
            "attempts": [],
            "preprocessing": "table_v2_empty_image",
            "engine": "table_recognition_v2",
            "model": _TABLE_MODEL_NAME,
            "table_debug": {"status": "empty_image", "runtime_called": True},
        }

    logger.info("Using local Table Recognition runtime")
    semi_analysis: Optional[Dict[str, Any]] = None

    whole_started = time.perf_counter()
    model = _load_table_model()
    model_inference_count += 1
    input_trace = _debug_input_trace(image) if _table_debug_trace_enabled() else None
    output = _predict_table_model(model, image)
    logger.info(
        "Table Recognition phase timing: phase=Whole ROI SLANeXt elapsed=%.3fs",
        time.perf_counter() - whole_started,
    )
    slanext_result = _slanext_result_from_output(output, image, started)
    if _table_debug_trace_enabled():
        slanext_trace = _ensure_table_trace(slanext_result)
        if isinstance(slanext_trace, dict):
            slanext_trace["input"] = input_trace
    slanext_candidate = _build_table_candidate(slanext_result, "slanext")
    recovered_candidate, structure_collapse_debug = _recover_slanext_structure_collapse(slanext_candidate, image)
    if bool(structure_collapse_debug.get("selected")):
        slanext_candidate = _build_table_candidate(recovered_candidate, "slanext")
    slanext_candidate.setdefault("table_debug", {})
    if isinstance(slanext_candidate["table_debug"], dict):
        slanext_candidate["table_debug"]["structure_collapse_recovery"] = structure_collapse_debug
        slanext_candidate["table_debug"]["row_collapse_recovery"] = structure_collapse_debug
    slanext_assignment_quality = _structured_assignment_quality(
        slanext_candidate.get("table_structured") if isinstance(slanext_candidate.get("table_structured"), dict) else None
    )
    if _table_debug_trace_enabled():
        slanext_trace = _ensure_table_trace(slanext_candidate)
        if isinstance(slanext_trace, dict):
            slanext_trace["ocr_assignment"] = {
                "before": _table_snapshot(slanext_candidate),
                "after": _table_snapshot(slanext_candidate),
                "changed": False,
                "quality": _json_safe(slanext_assignment_quality),
                "assigned_text_boxes": 0,
                "unassigned_text_boxes": 0,
                "ambiguous_text_boxes": 0,
                "attempted": False,
            }
    slanext_candidate.setdefault("table_debug", {})
    if isinstance(slanext_candidate["table_debug"], dict):
        slanext_candidate["table_debug"]["ocr_cell_assignment"] = {
            "attempted": False,
            "selected": False,
            "quality": slanext_assignment_quality,
            "reason": "initial_quality_gate",
        }
    if not bool(slanext_assignment_quality.get("passed")):
        assignment_before = _table_snapshot(slanext_candidate) if _table_debug_trace_enabled() else None
        reassigned_candidate, reassignment_debug = _reassign_ocr_text_to_slanext_cells(slanext_candidate)
        if _table_debug_trace_enabled():
            assignment_trace_target = reassigned_candidate if bool(reassignment_debug.get("selected")) else slanext_candidate
            trace = _ensure_table_trace(assignment_trace_target)
            if isinstance(trace, dict):
                trace["ocr_assignment"] = {
                    "before": assignment_before,
                    "after": _table_snapshot(reassigned_candidate),
                    "changed": _json_safe(assignment_before) != _json_safe(_table_snapshot(reassigned_candidate)),
                    "quality": _json_safe(reassignment_debug.get("quality")),
                    "assigned_text_boxes": int(reassignment_debug.get("assigned_text_boxes") or 0),
                    "unassigned_text_boxes": int(reassignment_debug.get("unassigned_text_boxes") or 0),
                    "ambiguous_text_boxes": int(reassignment_debug.get("ambiguous_text_boxes") or 0),
                    "attempted": bool(reassignment_debug.get("attempted")),
                    "selected": bool(reassignment_debug.get("selected")),
                    "reason": reassignment_debug.get("reason"),
                }
        if bool(reassignment_debug.get("selected")):
            slanext_candidate = _build_table_candidate(reassigned_candidate, "slanext")
            slanext_candidate.setdefault("table_debug", {})
            if isinstance(slanext_candidate["table_debug"], dict):
                slanext_candidate["table_debug"]["ocr_cell_assignment"] = reassignment_debug
                slanext_candidate["table_debug"]["assignment_repaired_before_semi"] = True
        elif isinstance(slanext_candidate.get("table_debug"), dict):
            slanext_candidate["table_debug"]["ocr_cell_assignment"] = reassignment_debug
    candidates = [slanext_candidate]
    slanext_debug = slanext_candidate.get("table_debug") if isinstance(slanext_candidate.get("table_debug"), dict) else {}
    slanext_quality = slanext_debug.get("quality") if isinstance(slanext_debug.get("quality"), dict) else {}
    slanext_assignment = slanext_debug.get("ocr_cell_assignment") if isinstance(slanext_debug.get("ocr_cell_assignment"), dict) else {}
    slanext_confidence = float(slanext_candidate.get("confidence") or 0.0)
    slanext_usable = _has_usable_table_result(slanext_candidate)
    slanext_has_structured_grid = bool(slanext_quality.get("has_structured_cells")) and int(slanext_quality.get("row_count") or 0) > 0 and int(slanext_quality.get("column_count") or 0) > 0
    slanext_needs_fallback = _should_try_borderless_candidate(slanext_quality, slanext_confidence)
    slanext_assignment_needs_fallback = bool(slanext_has_structured_grid) and not bool((slanext_assignment.get("quality") or {}).get("passed"))
    slanext_trace_for_final = (
        slanext_debug.get("table_recognition_trace")
        if _table_debug_trace_enabled() and isinstance(slanext_debug.get("table_recognition_trace"), dict)
        else None
    )

    if slanext_usable and not slanext_needs_fallback and not slanext_assignment_needs_fallback:
        selected = _attach_candidate_competition(slanext_candidate, candidates, "slanext_passed_quality_gate")
        selected.setdefault("table_semi_analysis", _whole_roi_semi_analysis(None, merge_status="not_needed_slanext_confident"))
        selected_debug = selected.get("table_debug")
        if isinstance(selected_debug, dict):
            selected_debug["timing_total_seconds"] = round(time.perf_counter() - started, 3)
            selected_debug["model_inference_count"] = model_inference_count
            selected_debug["ocr_inference_count"] = ocr_inference_count
            selected_debug["semi_skipped_reason"] = "slanext_passed_quality_gate"
        logger.info(
            "Table Recognition phase timing: phase=Total path=slanext selected=%s model_inferences=%s ocr_inferences=%s elapsed=%.3fs",
            selected.get("table_selected_method"),
            model_inference_count,
            ocr_inference_count,
            time.perf_counter() - started,
        )
        return _set_final_table_trace(selected)

    try:
        grid_started = time.perf_counter()
        semi_analysis = analyze_table_regions(image)
        logger.info(
            "Table Recognition phase timing: phase=Grid Analyzer after SLANeXt detected=%s confidence=%s regions=%s elapsed=%.3fs",
            bool(semi_analysis.get("detected")) if isinstance(semi_analysis, dict) else False,
            semi_analysis.get("confidence") if isinstance(semi_analysis, dict) else None,
            len(semi_analysis.get("regions") or []) if isinstance(semi_analysis, dict) else 0,
            time.perf_counter() - grid_started,
        )
        semi_result = _try_semi_structured_table(image, model, started, semi_analysis)
        if semi_result:
            semi_candidate = _build_table_candidate(semi_result, "coordinate_based_semi")
            reliability = _semi_result_reliability(semi_candidate, semi_analysis if isinstance(semi_analysis, dict) else {})
            semi_candidate.setdefault("table_debug", {})
            if isinstance(semi_candidate["table_debug"], dict):
                semi_candidate["table_debug"]["semi_reliability"] = reliability
            if not reliability["passed"]:
                logger.info(
                    "Table Recognition phase timing: phase=Semi Quality rejected reasons=%s elapsed=%.3fs",
                    reliability["reasons"],
                    time.perf_counter() - grid_started,
                )
                semi_analysis = _whole_roi_semi_analysis(semi_analysis, merge_status="coordinate_reconstruction_rejected")
                semi_analysis["reliability"] = reliability
            else:
                candidates.append(semi_candidate)
    except Exception as error:
        logger.info("Semi-structured table analysis after SLANeXt fell back to whole ROI: %s", error)
        semi_analysis = {"detected": False, "confidence": 0.0, "regions": [], "reason": str(error)}

    if not slanext_usable:
        try:
            forced_started = time.perf_counter()
            forced_semi_candidate = _try_forced_semi_after_empty_slanext(image, semi_analysis)
            logger.info(
                "Table Recognition phase timing: phase=Forced Semi after empty SLANeXt elapsed=%.3fs used=%s",
                time.perf_counter() - forced_started,
                bool(forced_semi_candidate),
            )
            if forced_semi_candidate:
                candidates.append(forced_semi_candidate)
                forced_debug = forced_semi_candidate.get("table_debug") if isinstance(forced_semi_candidate.get("table_debug"), dict) else {}
                forced_reliability = forced_debug.get("semi_reliability") if isinstance(forced_debug.get("semi_reliability"), dict) else {}
                if bool(forced_reliability.get("passed")) and _has_usable_table_result(forced_semi_candidate):
                    selected = _attach_candidate_competition(
                        forced_semi_candidate,
                        candidates,
                        "forced_semi_after_empty_slanext",
                    )
                    selected.setdefault("table_semi_analysis", forced_semi_candidate.get("table_semi_analysis") or _forced_whole_roi_semi_analysis(image, semi_analysis))
                    selected_debug = selected.get("table_debug")
                    if isinstance(selected_debug, dict):
                        selected_debug["status"] = "coordinate_based_semi_forced"
                        selected_debug["timing_total_seconds"] = round(time.perf_counter() - started, 3)
                        selected_debug["model_inference_count"] = model_inference_count
                        selected_debug["ocr_inference_count"] = ocr_inference_count
                    logger.info(
                        "Table Recognition phase timing: phase=Total path=forced_semi model_inferences=%s ocr_inferences=%s elapsed=%.3fs",
                        model_inference_count,
                        ocr_inference_count,
                        time.perf_counter() - started,
                    )
                    selected = _copy_slanext_trace(selected, slanext_trace_for_final)
                    return _set_final_table_trace(selected)
        except Exception as error:
            logger.info("Forced Semi Table after empty SLANeXt failed: %s", error)

    if not slanext_has_structured_grid and _should_try_borderless_candidate(slanext_quality, slanext_confidence):
        try:
            geometry_started = time.perf_counter()
            fallback_result = _recognize_ocr_table_fallback(image)
            if fallback_result:
                ocr_inference_count += 2
                fallback_debug = fallback_result.get("table_debug")
                if isinstance(fallback_debug, dict):
                    slanext_rows = normalize_table_rows(slanext_result.get("table_rows") or [])
                    fallback_debug["slan_rows_before_fallback"] = len(slanext_rows)
                    fallback_debug["slan_columns_before_fallback"] = max((len(row) for row in slanext_rows), default=0)
                    fallback_debug["slan_status_before_fallback"] = "structure_empty" if not slanext_rows else "low_quality_candidate"
                fallback_candidate = _build_table_candidate(fallback_result, "ocr_table_fallback")
                candidates.append(fallback_candidate)
                if not slanext_usable:
                    selected = _attach_candidate_competition(fallback_candidate, candidates, "ocr_table_fallback_after_unusable_slanext")
                    selected.setdefault("table_semi_analysis", _whole_roi_semi_analysis(semi_analysis))
                    selected_debug = selected.get("table_debug")
                    if isinstance(selected_debug, dict):
                        selected_debug["status"] = "ocr_table_fallback"
                        selected_debug["timing_total_seconds"] = round(time.perf_counter() - started, 3)
                        selected_debug["model_inference_count"] = model_inference_count
                        selected_debug["ocr_inference_count"] = ocr_inference_count
                    logger.info(
                        "Table Recognition phase timing: phase=Total path=ocr_table_fallback model_inferences=%s ocr_inferences=%s elapsed=%.3fs",
                        model_inference_count,
                        ocr_inference_count,
                        time.perf_counter() - started,
                    )
                    selected = _copy_slanext_trace(selected, slanext_trace_for_final)
                    return _set_final_table_trace(selected)
            logger.info(
                "Table Recognition phase timing: phase=Geometry Reconstruction elapsed=%.3fs used=%s",
                time.perf_counter() - geometry_started,
                bool(fallback_result),
            )
        except Exception as error:
            logger.warning("OCR table fallback failed: %s", error)

    selected, selection_reason = _select_best_table_candidate(candidates)
    selected = _attach_candidate_competition(selected, candidates, selection_reason)
    selected = _copy_slanext_trace(selected, slanext_trace_for_final)
    selected.setdefault("table_semi_analysis", _whole_roi_semi_analysis(semi_analysis))
    if not _has_usable_table_result(selected):
        try:
            raw_started = time.perf_counter()
            raw_result = _recognize_raw_ocr_geometry_table(image)
            logger.info(
                "Table Recognition phase timing: phase=Raw OCR Geometry elapsed=%.3fs used=%s",
                time.perf_counter() - raw_started,
                bool(raw_result),
            )
            if raw_result:
                ocr_inference_count += 2
                raw_candidate = _build_table_candidate(raw_result, "raw_ocr_geometry_table")
                selected = _attach_candidate_competition(
                    raw_candidate,
                    [*candidates, raw_candidate],
                    "raw_ocr_geometry_after_unusable_structure",
                )
                selected = _copy_slanext_trace(selected, slanext_trace_for_final)
                selected.setdefault("table_semi_analysis", _whole_roi_semi_analysis(semi_analysis))
        except Exception as error:
            logger.warning("Raw OCR geometry table fallback failed: %s", error)
    selected_debug = selected.get("table_debug")
    if isinstance(selected_debug, dict):
        selected_debug["timing_total_seconds"] = round(time.perf_counter() - started, 3)
        selected_debug["model_inference_count"] = model_inference_count
        selected_debug["ocr_inference_count"] = ocr_inference_count
    logger.info(
        "Table Recognition phase timing: phase=Total path=whole_roi selected=%s model_inferences=%s ocr_inferences=%s elapsed=%.3fs",
        selected.get("table_selected_method"),
        model_inference_count,
        ocr_inference_count,
        time.perf_counter() - started,
    )
    return _set_final_table_trace(selected)


def recognize_table_v2(image: np.ndarray) -> Dict[str, Any]:
    if _use_remote_runtime():
        logger.info("Using remote Table Recognition runtime")
        try:
            remote_result = remote_recognize_table(image)
        except ModelRuntimeUnavailableError as error:
            raise TableRecognitionV2UnavailableError(str(error)) from error
        except Exception as error:
            raise TableRecognitionV2UnavailableError(str(error)) from error

        if remote_result is None:
            raise TableRecognitionV2UnavailableError("Remote Table Recognition runtime returned no result.")
        if not isinstance(remote_result, dict):
            raise TableRecognitionV2UnavailableError("Remote Table Recognition runtime returned an invalid response.")
        remote_debug = remote_result.get("table_debug")
        if isinstance(remote_debug, dict):
            remote_debug.setdefault("remote_runtime_called", True)
        else:
            remote_result["table_debug"] = {"remote_runtime_called": True}
        if isinstance(remote_result.get("table_debug"), dict) and isinstance(
            remote_result["table_debug"].get("candidate_competition"),
            dict,
        ):
            return _postprocess_table_result(remote_result)
        remote_method = str(remote_result.get("table_selected_method") or remote_result["table_debug"].get("candidate_method") or "remote_runtime")
        return _build_table_candidate(remote_result, remote_method)

    return recognize_table_v2_local(image)
