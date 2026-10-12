import logging
import tempfile
import time
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Optional

import cv2
import numpy as np

from app.core.model_runtime_client import (
    ModelRuntimeKind,
    ModelRuntimeUnavailableError,
    is_runtime_configured,
    remote_recognize_table_raw,
)
from app.processing.ocr_postprocess import normalize_ocr_text, normalize_table_rows
from test_structure import extract_recognized_items, reconstruct_table_from_ocr


class TableRecognitionV2UnavailableError(RuntimeError):
    pass


logger = logging.getLogger(__name__)

_TABLE_MODEL_NAME = "remote_table_runtime"


def _use_remote_runtime() -> bool:
    return is_runtime_configured(ModelRuntimeKind.TABLE)


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


def _as_dict(value: Any) -> Optional[Dict[str, Any]]:
    if isinstance(value, dict):
        return value
    json_attr = getattr(value, "json", None)
    if isinstance(json_attr, dict):
        return json_attr
    res_attr = getattr(value, "res", None)
    if isinstance(res_attr, dict):
        return res_attr
    return None


def _collect_dicts(value: Any) -> List[Dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, dict):
        nested = [value]
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


class _TableHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: List[List[str]] = []
        self._row: Optional[List[str]] = None
        self._cell: Optional[List[str]] = None

    def handle_starttag(self, tag: str, attrs: List[tuple[str, Optional[str]]]) -> None:
        if tag.lower() == "tr":
            self._row = []
        elif tag.lower() in {"td", "th"} and self._row is not None:
            self._cell = []

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag_name = tag.lower()
        if tag_name in {"td", "th"} and self._row is not None and self._cell is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag_name == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None


def _rows_from_html(table_html: str) -> List[List[str]]:
    parser = _TableHtmlParser()
    try:
        parser.feed(table_html or "")
        parser.close()
    except Exception:
        return []
    return normalize_table_rows(parser.rows)


def _markdown_table(rows: List[List[str]]) -> str:
    rows = normalize_table_rows(rows)
    if not rows:
        return ""
    column_count = max(len(row) for row in rows)
    normalized = [row + [""] * (column_count - len(row)) for row in rows]

    def render(row: List[str]) -> str:
        return "| " + " | ".join(str(cell).strip().replace("|", "/") for cell in row) + " |"

    return "\n".join([render(normalized[0]), render(["---"] * column_count), *[render(row) for row in normalized[1:]]])


def _backend_bbox(bbox: Any) -> Optional[Dict[str, float]]:
    if not isinstance(bbox, list) or len(bbox) < 4:
        return None
    try:
        x0, y0, x1, y1 = float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])
    except (TypeError, ValueError):
        return None
    return {"x": x0, "y": y0, "width": max(1.0, x1 - x0), "height": max(1.0, y1 - y0)}


def _backend_cells(cells: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    for cell in cells:
        if not isinstance(cell, dict):
            continue
        text = normalize_ocr_text(cell.get("text") or "")
        next_cell: Dict[str, Any] = {
            "row": int(cell.get("row_index", cell.get("row", 0)) or 0),
            "col": int(cell.get("column_index", cell.get("col", 0)) or 0),
            "text": text,
            "ocrText": text,
            "groundTruth": text,
            "rowSpan": max(1, int(cell.get("rowspan") or cell.get("rowSpan") or 1)),
            "colSpan": max(1, int(cell.get("colspan") or cell.get("colSpan") or 1)),
            "assignmentSource": "test_structure_rules",
        }
        bbox = _backend_bbox(cell.get("bbox_grid") or cell.get("bbox"))
        if bbox is not None:
            next_cell["bbox"] = bbox
        if cell.get("texts") is not None:
            next_cell["recognizedItems"] = _json_safe(cell.get("texts"))
        if cell.get("mapping_warnings") is not None:
            next_cell["mappingWarnings"] = _json_safe(cell.get("mapping_warnings"))
        result.append(next_cell)
    return result


def _structured_from_cells(rows: List[List[str]], cells: List[Dict[str, Any]]) -> Dict[str, Any]:
    rows = normalize_table_rows(rows)
    column_count = max((len(row) for row in rows), default=0)
    normalized_rows = [row + [""] * (column_count - len(row)) for row in rows]
    return {
        "rows": normalized_rows,
        "cells": cells,
        "headerRowCount": 1 if normalized_rows else 0,
        "postProcessing": "test_structure_rules",
    }


def _table_result_candidates(output: Any) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    for item in _collect_dicts(output):
        table_res_list = item.get("table_res_list")
        if isinstance(table_res_list, list):
            candidates.extend(table for table in table_res_list if isinstance(table, dict))
        if isinstance(item.get("cell_box_list"), list):
            candidates.append(item)
    return candidates


def _unwrap_remote_result(remote_result: Dict[str, Any]) -> Any:
    if isinstance(remote_result.get("data"), dict):
        return _unwrap_remote_result(remote_result["data"])
    if "raw_output" in remote_result:
        return remote_result.get("raw_output")
    if "output" in remote_result:
        return remote_result.get("output")
    if "result" in remote_result:
        return remote_result.get("result")
    return remote_result


def _predict_table_model(image: np.ndarray) -> Any:
    started = time.perf_counter()
    if not _use_remote_runtime():
        raise TableRecognitionV2UnavailableError("GATEWAY_URL is not configured.")
    try:
        remote_result = remote_recognize_table_raw(image)
    except ModelRuntimeUnavailableError as error:
        raise TableRecognitionV2UnavailableError(str(error)) from error
    except Exception as error:
        raise TableRecognitionV2UnavailableError(str(error)) from error
    finally:
        logger.info("Table Recognition phase timing: phase=remote inference elapsed=%.3fs", time.perf_counter() - started)
    if remote_result is None:
        raise TableRecognitionV2UnavailableError("Remote Table Recognition runtime returned no result.")
    if not isinstance(remote_result, dict):
        raise TableRecognitionV2UnavailableError("Remote Table Recognition runtime returned an invalid response.")
    return _unwrap_remote_result(remote_result)


def _write_temp_image(image: np.ndarray) -> tuple[Optional[Path], bool]:
    temp_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp_image:
            temp_path = Path(temp_image.name)
        return temp_path, bool(cv2.imwrite(str(temp_path), image))
    except Exception:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        return None, False


def _reconstruct_with_test_structure_rules(output: Any, image: np.ndarray, started: float) -> Dict[str, Any]:
    height, width = image.shape[:2]
    raw_result_count = len(_collect_dicts(output))
    last_error: Optional[str] = None
    for table_result in _table_result_candidates(output):
        cell_boxes = table_result.get("cell_box_list") or []
        if not isinstance(cell_boxes, list) or not cell_boxes:
            continue
        recognized_items = extract_recognized_items(table_result)
        if not recognized_items:
            continue

        temp_path, image_path_available = _write_temp_image(image)
        try:
            cells, table_html = reconstruct_table_from_ocr(
                cell_boxes,
                recognized_items,
                (int(width), int(height)),
                temp_path if image_path_available else None,
            )
        except Exception as error:
            last_error = str(error)
            logger.info("test_structure table rules failed: %s", error)
            continue
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

        backend_cells = _backend_cells(cells)
        rows = _rows_from_html(table_html)
        structured = _structured_from_cells(rows, backend_cells)
        confidence_values = [
            float(item.get("score"))
            for item in recognized_items
            if isinstance(item, dict) and isinstance(item.get("score"), (int, float))
        ]
        debug = {
            "status": "recognized" if rows else "structure_empty",
            "post_processing": "test_structure_rules",
            "runtime_called": True,
            "model_kind": "remote",
            "input_size": [int(width), int(height)],
            "row_count": len(rows),
            "column_count": max((len(row) for row in rows), default=0),
            "raw_result_count": raw_result_count,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "central_rules": {
                "source": "test_structure.py",
                "cell_box_count": len(cell_boxes),
                "recognized_item_count": len(recognized_items),
                "reconstructed_cell_count": len(backend_cells),
                "image_path_used": image_path_available,
            },
            "adapter_rule_source": "test_structure.py",
        }
        return {
            "text": _markdown_table(rows),
            "confidence": sum(confidence_values) / len(confidence_values) if confidence_values else 0.0,
            "segments": [],
            "attempts": [{"step": "test_structure_rules_reconstruction"}],
            "preprocessing": "test_structure_rules",
            "engine": "table_recognition_v2",
            "model": _TABLE_MODEL_NAME,
            "table_html": table_html,
            "table_rows": rows,
            "table_structured": structured,
            "table_selected_method": "test_structure_rules",
            "table_debug": debug,
            "raw_results": _json_safe(_collect_dicts(output)),
        }

    raise TableRecognitionV2UnavailableError(
        "Remote table output does not contain usable cell_box_list/table_ocr_pred for test_structure.py rules."
        + (f" Last reconstruction error: {last_error}" if last_error else "")
    )


def recognize_table_v2_local(image: np.ndarray) -> Dict[str, Any]:
    started = time.perf_counter()
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
    output = _predict_table_model(image)
    result = _reconstruct_with_test_structure_rules(output, image, started)
    logger.info(
        "Table Recognition phase timing: phase=Total path=test_structure_rules elapsed=%.3fs",
        time.perf_counter() - started,
    )
    return result


def recognize_table_v2(image: np.ndarray) -> Dict[str, Any]:
    if _use_remote_runtime():
        return recognize_table_v2_local(image)
    raise TableRecognitionV2UnavailableError("GATEWAY_URL is not configured.")
