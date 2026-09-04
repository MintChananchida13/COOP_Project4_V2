import base64
import io
import logging
import os
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Tuple

import cv2
import numpy as np
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from PIL import Image
from pydantic import BaseModel

from app.core.local_env import load_local_env

load_local_env()

from app.api.routes import router as blueprint_router
from app.model_runtime.layout_analysis_service import (
    AUTO_ROI_EXPAND_BOTTOM_PX,
    AUTO_ROI_EXPAND_LEFT_PX,
    AUTO_ROI_EXPAND_RIGHT_PX,
    AUTO_ROI_EXPAND_TOP_PX,
    LayoutAnalysisUnavailableError,
    analyze_layout,
    detect_text_boxes,
)
from app.processing.ocr_adapter import recognize_text_roi
from app.processing.ocr_postprocess import normalize_ocr_text, normalize_table_rows
from app.model_runtime.paddle_thai_ocr_adapter import PaddleThaiOcrUnavailableError, run_paddle_thai_ocr, run_paddle_thai_ocr_batch
from app.model_runtime.table_recognition_v2_adapter import TableRecognitionV2UnavailableError, recognize_table_v2
from app.core.db import connect as db_connect
from app.core.json_utils import jsonb_dump, jsonb_load
from app.core.model_runtime_client import configured_runtimes

# Force UTF-8 console output on Windows.
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

OUTPUT_DIR = "cropped_rois"
logger = logging.getLogger(__name__)


def _cropped_roi_path(filename: str) -> str:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    return os.path.join(OUTPUT_DIR, filename)


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


class ROIModel(BaseModel):
    fieldName: str
    x: float
    y: float
    width: float
    height: float
    roiId: int | None = None
    type: str | None = None
    extractionMethod: str | None = None
    roiMode: str | None = None
    expectedContent: str | None = None


class DocumentPayload(BaseModel):
    image: str
    rois: List[ROIModel]
    async_mode: bool = False


class LayoutImagePayload(BaseModel):
    page_index: int
    image: str


class LayoutAnalysisPayload(BaseModel):
    images: List[LayoutImagePayload]
    auto_roi_mode: str = "text_line"
    context: str | None = None


app = FastAPI(title="OCR AI Engine")
DETECTION_DEBUG_DIR = Path(__file__).resolve().parent / "storage" / "detection_queries"

FRONTEND_URL = os.getenv("FRONTEND_URL", "").strip().rstrip("/")

allowed_origins = [
    "http://localhost:3000",
]

if FRONTEND_URL:
    allowed_origins.append(FRONTEND_URL)

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount(
    "/debug/detection-queries",
    StaticFiles(directory=str(DETECTION_DEBUG_DIR), check_dir=False),
    name="detection_debug",
)

app.include_router(blueprint_router)


def _env_flag(name: str, default: str = "true") -> bool:
    return os.getenv(name, default).strip().lower() not in {"0", "false", "no", "off"}


@app.on_event("startup")
async def startup_warmup() -> None:
    print(f"Using external model runtimes: {configured_runtimes()}")
    print("Main backend model warm-up skipped; backend owns process logic and calls model runtimes via HTTP.")


def decode_base64_image(image_str: str) -> Tuple[Image.Image, np.ndarray]:
    _, encoded = image_str.split(",", 1) if "," in image_str else ("", image_str)
    image_data = base64.b64decode(encoded)
    pil_image = Image.open(io.BytesIO(image_data))
    if pil_image.mode != "RGB":
        pil_image = pil_image.convert("RGB")
    opencv_img = cv2.cvtColor(np.array(pil_image), cv2.COLOR_RGB2BGR)
    return pil_image, opencv_img


def crop_opencv_region(opencv_img: np.ndarray, x: int, y: int, w: int, h: int) -> np.ndarray:
    h_img, w_img = opencv_img.shape[:2]
    x = max(0, x)
    y = max(0, y)
    x_end = min(x + max(1, w), w_img)
    y_end = min(y + max(1, h), h_img)
    return opencv_img[y:y_end, x:x_end]


def _markdown_table(rows: List[List[str]]) -> str:
    if not rows:
        return ""
    max_columns = max(len(row) for row in rows)
    normalized = [row + [""] * (max_columns - len(row)) for row in rows]
    header = normalized[0]
    separator = ["---"] * max_columns
    body = normalized[1:]

    def fmt(row: List[str]) -> str:
        return "| " + " | ".join(cell.strip() for cell in row) + " |"

    return "\n".join([fmt(header), fmt(separator), *[fmt(row) for row in body]])


def _structured_table_from_rows(rows: List[List[str]], regions: List[Dict[str, Any]] | None = None) -> Dict[str, Any] | None:
    if not rows:
        return None
    max_columns = max((len(row) for row in rows), default=0)
    normalized_rows = [row + [""] * (max_columns - len(row)) for row in rows]
    source_regions = regions or []
    cells: List[Dict[str, Any]] = []
    for row_index, row in enumerate(normalized_rows):
        for col_index, text in enumerate(row):
            flat_index = row_index * max_columns + col_index
            source_region = source_regions[flat_index] if flat_index < len(source_regions) else {}
            normalized_text = normalize_ocr_text(text)
            cell: Dict[str, Any] = {
                "row": row_index,
                "col": col_index,
                "text": normalized_text,
                "rowSpan": 1,
                "colSpan": 1,
                "ocrText": normalized_text,
                "groundTruth": normalized_text,
            }
            bbox = source_region.get("bbox") if isinstance(source_region, dict) else None
            if bbox is not None:
                cell["bbox"] = bbox
            cells.append(cell)
    return {
        "rows": normalized_rows,
        "cells": cells,
        "headerRowCount": 1,
    }


def _group_table_cells(regions: List[Dict[str, Any]], recognitions: List[Dict[str, Any]]) -> List[List[str]]:
    cells: List[Dict[str, Any]] = []
    for region, recognized in zip(regions, recognitions):
        text = normalize_ocr_text(recognized.get("text"))
        if not text:
            continue
        bbox = region.get("bbox") or {}
        x = float(bbox.get("x") or 0)
        y = float(bbox.get("y") or 0)
        width = float(bbox.get("width") or 0)
        height = float(bbox.get("height") or 0)
        cells.append(
            {
                "text": text,
                "x": x,
                "y": y,
                "height": max(1.0, height),
                "center_y": y + height / 2,
            }
        )

    if not cells:
        return []

    median_height = float(np.median([cell["height"] for cell in cells])) if cells else 12.0
    line_threshold = max(8.0, median_height * 0.65)
    rows: List[List[Dict[str, Any]]] = []

    for cell in sorted(cells, key=lambda item: (item["center_y"], item["x"])):
        target_row = None
        for row in rows:
            row_center = sum(item["center_y"] for item in row) / len(row)
            if abs(cell["center_y"] - row_center) <= line_threshold:
                target_row = row
                break
        if target_row is None:
            rows.append([cell])
        else:
            target_row.append(cell)

    grouped_rows: List[List[str]] = []
    for row in rows:
        sorted_row = sorted(row, key=lambda item: item["x"])
        grouped_rows.append([item["text"] for item in sorted_row])
    return normalize_table_rows(grouped_rows)


def process_table_roi_with_engine(crop_img: np.ndarray) -> Dict[str, Any]:
    if crop_img is None or crop_img.size == 0:
        return {
            "text": "",
            "confidence": 0.0,
            "segments": [],
            "attempts": [],
            "preprocessing": "table_empty_image",
            "engine": "paddle_table_roi",
            "model": None,
        }

    h_img, w_img = crop_img.shape[:2]
    working_img = crop_img
    scale_factor = 1.0
    longest_side = max(w_img, h_img)
    if longest_side < 1400:
        scale_factor = min(4.0, max(2.0, 1400.0 / max(longest_side, 1)))
        working_img = cv2.resize(
            crop_img,
            (max(1, int(w_img * scale_factor)), max(1, int(h_img * scale_factor))),
            interpolation=cv2.INTER_CUBIC,
        )

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp_file:
        temp_path = temp_file.name
    try:
        cv2.imwrite(temp_path, working_img)
        text_detection = detect_text_boxes(temp_path)
    finally:
        Path(temp_path).unlink(missing_ok=True)

    regions = text_detection.get("regions") or []
    crops: List[np.ndarray] = []
    valid_regions: List[Dict[str, Any]] = []
    h_working, w_working = working_img.shape[:2]
    for region in regions:
        bbox = region.get("bbox") or {}
        x = max(0, int(float(bbox.get("x") or 0)))
        y = max(0, int(float(bbox.get("y") or 0)))
        width = max(1, int(float(bbox.get("width") or 1)))
        height = max(1, int(float(bbox.get("height") or 1)))
        width = min(width, w_working - x)
        height = min(height, h_working - y)
        if width <= 0 or height <= 0:
            continue
        cell_crop = working_img[y : y + height, x : x + width]
        if cell_crop.size == 0:
            continue
        valid_regions.append(region)
        crops.append(cell_crop)

    if not crops:
        fallback_result = run_paddle_thai_ocr(working_img)
        fallback_text = str(fallback_result.get("text") or "").strip()
        return {
            "text": fallback_text,
            "confidence": float(fallback_result.get("confidence") or 0.0),
            "segments": [],
            "attempts": [{"step": "whole_table_fallback", "text": fallback_text}],
            "preprocessing": "table_text_detection_empty_whole_crop_fallback",
            "engine": "paddle_table_roi",
            "model": fallback_result.get("model") or text_detection.get("model"),
            "table_debug": {
                "detected_boxes": 0,
                "scale_factor": scale_factor,
                "input_size": [w_img, h_img],
                "working_size": [w_working, h_working],
            },
        }

    recognitions = run_paddle_thai_ocr_batch(crops)
    table_rows = _group_table_cells(valid_regions, recognitions)
    text = _markdown_table(table_rows)
    table_structured = _structured_table_from_rows(table_rows, valid_regions)
    confidence_values = [
        float(item.get("confidence") or 0.0)
        for item in recognitions
        if str(item.get("text") or "").strip()
    ]
    confidence = sum(confidence_values) / len(confidence_values) if confidence_values else 0.0
    if not text:
        fallback_result = run_paddle_thai_ocr(working_img)
        text = str(fallback_result.get("text") or "").strip()
        confidence = float(fallback_result.get("confidence") or 0.0)

    return {
        "text": text,
        "confidence": float(confidence),
        "segments": [
            {
                "text": str(recognized.get("text") or ""),
                "confidence": float(recognized.get("confidence") or 0.0),
                "bbox": region.get("bbox"),
            }
            for region, recognized in zip(valid_regions, recognitions)
        ],
        "attempts": [],
        "preprocessing": "table_text_detection_then_paddle_recognition",
        "engine": "paddle_table_roi",
        "model": "PP-OCRv5_server_det+th_PP-OCRv5_mobile_rec",
        "table_rows": table_rows,
        "table_structured": table_structured,
        "table_debug": {
            "detected_boxes": len(regions),
            "recognized_cells": len(confidence_values),
            "row_count": len(table_rows),
            "scale_factor": scale_factor,
            "input_size": [w_img, h_img],
            "working_size": [w_working, h_working],
        },
    }


def process_table_roi_v2_with_fallback(crop_img: np.ndarray) -> Dict[str, Any]:
    try:
        return recognize_table_v2(crop_img)
    except TableRecognitionV2UnavailableError as error:
        raise error
    except Exception as error:
        raise TableRecognitionV2UnavailableError(str(error)) from error


def process_roi_with_engine(crop_img: np.ndarray, roi: ROIModel) -> Dict[str, Any]:
    field_type = (roi.type or "text").lower()
    extraction_method = (roi.extractionMethod or "paddle_thai_ocr").lower()
    if extraction_method == "typhoon_ocr":
        extraction_method = "paddle_thai_ocr"

    if extraction_method == "extract_image" or field_type == "image":
        return {
            "text": "",
            "confidence": 1.0,
            "segments": [],
            "attempts": [],
            "preprocessing": "image_crop_only",
            "engine": "extract_image",
            "model": None,
        }

    if field_type == "table" or extraction_method == "table_recognition_v2":
        return process_table_roi_v2_with_fallback(crop_img)

    if extraction_method == "ocr_table":
        return process_table_roi_with_engine(crop_img)

    return recognize_text_roi(crop_img)


def _region_type(region: Dict[str, Any]) -> str:
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
    region_type = _region_type(region).replace("_", " ").replace("-", " ")
    if any(token in region_type for token in ("header", "footer", "page number")):
        return False
    return bool(region.get("roi")) or bool(region.get("bbox"))


def _resolved_layout_region_type(region: Dict[str, Any]) -> str:
    region_type = _region_type(region).replace("_", " ").replace("-", " ")
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
    pad_x = 4.0 / max(float(image_width), 1.0)
    pad_y = 4.0 / max(float(image_height), 1.0)
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


def _region_crop_box(region: Dict[str, Any], image_width: int, image_height: int) -> Tuple[int, int, int, int] | None:
    roi = region.get("roi") if isinstance(region, dict) else None
    bbox = region.get("bbox") if isinstance(region, dict) else None
    try:
        if isinstance(roi, dict):
            x = int(float(roi.get("x_ratio") or 0.0) * image_width)
            y = int(float(roi.get("y_ratio") or 0.0) * image_height)
            w = int(float(roi.get("width_ratio") or 0.0) * image_width)
            h = int(float(roi.get("height_ratio") or 0.0) * image_height)
        elif isinstance(bbox, dict):
            x = int(float(bbox.get("x") or 0.0))
            y = int(float(bbox.get("y") or 0.0))
            w = int(float(bbox.get("width") or 0.0))
            h = int(float(bbox.get("height") or 0.0))
        else:
            return None
    except (TypeError, ValueError):
        return None
    x = max(0, min(x, image_width - 1))
    y = max(0, min(y, image_height - 1))
    w = max(1, min(w, image_width - x))
    h = max(1, min(h, image_height - y))
    return (x, y, w, h)


def _region_roi(region: Dict[str, Any], image_width: int, image_height: int) -> Dict[str, float] | None:
    box = _region_crop_box(region, image_width, image_height)
    if not box:
        return None
    x, y, w, h = box
    return {
        "x_ratio": x / max(float(image_width), 1.0),
        "y_ratio": y / max(float(image_height), 1.0),
        "width_ratio": w / max(float(image_width), 1.0),
        "height_ratio": h / max(float(image_height), 1.0),
    }


def _median_float(values: List[float], fallback: float = 0.0) -> float:
    prepared = sorted(float(value) for value in values if np.isfinite(float(value)))
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


def _text_line_regions_from_image(image: np.ndarray) -> List[Dict[str, Any]]:
    h_img, w_img = image.shape[:2]
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp_file:
        temp_path = temp_file.name
    try:
        cv2.imwrite(temp_path, image)
        detection = detect_text_boxes(temp_path)
    except Exception:
        return []
    finally:
        Path(temp_path).unlink(missing_ok=True)

    lines: List[Dict[str, Any]] = []
    for index, region in enumerate(_layout_regions_from_analysis(detection), start=1):
        roi = _region_roi(region, w_img, h_img)
        if not roi or _roi_area(roi) <= 0:
            continue
        lines.append(
            {
                "type": "text",
                "data_type": "text",
                "layout_type": "text_line",
                "source": "paddle_text_detection_line",
                "confidence": region.get("confidence", 0.0),
                "roi": roi,
                "image_width": w_img,
                "image_height": h_img,
                "_line_index": index,
            }
        )
    return lines


def _paragraph_regions_from_text_lines(lines: List[Dict[str, Any]], debug_scope: str = "flexible") -> List[Dict[str, Any]]:
    prepared = [line for line in lines if isinstance(line.get("roi"), dict) and _roi_area(line["roi"]) > 0]
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

    paragraphs: List[Dict[str, Any]] = []
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
        paragraphs.append(
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
    return paragraphs


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
        roi = _region_roi(region, image_width, image_height)
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
        float((_region_roi(region, image_width, image_height) or {}).get("y_ratio") or 0.0),
        float((_region_roi(region, image_width, image_height) or {}).get("x_ratio") or 0.0),
    ))
    return kept_regions


def _build_flexible_paragraph_regions(image: np.ndarray, analysis: Dict[str, Any]) -> List[Dict[str, Any]]:
    h_img, w_img = image.shape[:2]
    layout_regions = [
        region
        for region in _layout_regions_from_analysis(analysis)
        if _is_supported_layout_region(region)
    ]
    non_text_regions = [region for region in layout_regions if _resolved_layout_region_type(region) != "text"]
    text_regions = [region for region in layout_regions if _resolved_layout_region_type(region) == "text"]
    blockers = [
        _region_roi(region, w_img, h_img)
        for region in non_text_regions
        if _resolved_layout_region_type(region) in {"table", "image"}
    ]
    blockers = [roi for roi in blockers if roi]
    line_regions: List[Dict[str, Any]] = []
    for line in _text_line_regions_from_image(image):
        line_roi = _region_roi(line, w_img, h_img)
        if not line_roi:
            continue
        line_area = _roi_area(line_roi)
        if any(_roi_intersection_area(line_roi, blocker) / max(line_area, 1e-9) >= 0.55 for blocker in blockers):
            continue
        line_regions.append(line)
    paragraph_regions: List[Dict[str, Any]] = []
    used_line_ids: set[int] = set()
    for text_region_index, text_region in enumerate(text_regions, start=1):
        text_roi = _region_roi(text_region, w_img, h_img)
        if not text_roi:
            continue
        region_lines = []
        for line in line_regions:
            line_roi = _region_roi(line, w_img, h_img)
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
    return _filter_nested_flexible_regions(regions, w_img, h_img)


def _ocr_flexible_regions(search_img: np.ndarray, regions: List[Dict[str, Any]], source: str) -> Dict[str, Any]:
    h_img, w_img = search_img.shape[:2]
    texts: List[str] = []
    confidences: List[float] = []
    segments: List[Dict[str, Any]] = []
    for index, region in enumerate(regions):
        box = _region_crop_box(region, w_img, h_img)
        if not box:
            continue
        data_type = _resolved_layout_region_type(region)
        extraction_method = _extraction_method_for_resolved_type(data_type)
        if data_type == "table":
            roi = _region_roi(region, w_img, h_img)
            if roi:
                expanded_roi = _expand_table_roi(roi, w_img, h_img)
                region = {**region, "roi": expanded_roi}
                box = _region_crop_box(region, w_img, h_img)
                if not box:
                    continue
        x, y, w, h = box
        block_img = search_img[y : y + h, x : x + w]
        if block_img.size == 0:
            continue
        table_rows = None
        table_structured = None
        table_html = None
        try:
            if data_type == "image":
                text = "(image crop)"
                confidence = 1.0
                raw_segments = []
            elif data_type == "table":
                ocr_result = process_table_roi_v2_with_fallback(block_img)
                text = normalize_ocr_text(ocr_result.get("text"))
                confidence = float(ocr_result.get("confidence") or 0.0)
                raw_segments = ocr_result.get("segments", [])
                table_rows = ocr_result.get("table_rows")
                table_structured = ocr_result.get("table_structured")
                table_html = ocr_result.get("table_html")
            else:
                ocr_result = recognize_text_roi(block_img)
                text = str(ocr_result.get("text") or "")
                confidence = float(ocr_result.get("confidence") or 0.0)
                raw_segments = ocr_result.get("raw_segments") or ocr_result.get("segments", [])
            error_message = None
        except Exception as error:
            text = ""
            confidence = 0.0
            raw_segments = []
            error_message = str(error)
        if text:
            texts.append(text)
            confidences.append(confidence)
        segments.append(
            {
                "index": index,
                "text": text,
                "confidence": confidence,
                "bbox": {"x": x, "y": y, "width": w, "height": h},
                "type": data_type,
                "data_type": data_type,
                "extraction_method": extraction_method,
                "layout_type": _region_type(region) or data_type,
                "source": source,
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
    }


def process_flexible_text_roi(search_img: np.ndarray) -> Dict[str, Any]:
    if search_img.size == 0:
        return {"text": "", "confidence": 0.0, "segments": [], "attempts": [], "engine": "flexible_roi_text"}

    h_img, w_img = search_img.shape[:2]
    analysis = analyze_layout(search_img, expand_text_rois=True, auto_roi_mode="text_line")
    text_regions = _build_flexible_paragraph_regions(search_img, analysis)

    result = _ocr_flexible_regions(search_img, text_regions, "flexible_paragraph_layout_blocks")
    attempts = [{"step": "flexible_roi_paragraph_blocks", "block_count": len(text_regions), "recognized_count": len(result["segments"])}]

    return {
        "text": result.get("text") or "",
        "confidence": float(result.get("confidence") or 0.0),
        "segments": result.get("segments") or [],
        "raw_segments": result.get("raw_segments") or result.get("segments") or [],
        "attempts": attempts,
        "preprocessing": "flexible_roi_search_boundary_paragraph_blocks",
        "engine": "flexible_roi_text",
        "model": "PP-DocLayoutV3 + text_ocr_pipeline",
        "resolved_blocks": result.get("segments") or [],
    }


def _payload_to_json(payload: DocumentPayload) -> str:
    if hasattr(payload, "model_dump"):
        data = payload.model_dump()
    else:
        data = payload.dict()
    data["async_mode"] = False
    return jsonb_dump(data)


def create_ocr_job(payload: DocumentPayload, requested_by: str | None = None) -> str:
    job_id = f"ocr_{uuid.uuid4().hex}"
    with db_connect() as conn:
        conn.execute(
            """
            INSERT INTO ocr_jobs (id, requested_by, status, request_json)
            VALUES (?, ?, 'queued', ?)
            """,
            (job_id, requested_by, _payload_to_json(payload)),
        )
    return job_id


def get_ocr_job(job_id: str) -> Dict[str, Any] | None:
    with db_connect() as conn:
        row = conn.execute(
            """
            SELECT id, requested_by, status, requested_at, started_at, completed_at, error_message, result_json
            FROM ocr_jobs
            WHERE id = ?
            """,
            (job_id,),
        ).fetchone()
    if not row:
        return None
    result = dict(row)
    result_json = result.pop("result_json", None)
    if result_json:
        result["result"] = jsonb_load(result_json)
    return result


def update_ocr_job_status(job_id: str, status: str, error_message: str | None = None, result: Dict[str, Any] | None = None) -> None:
    result_json = jsonb_dump(result) if result is not None else None
    with db_connect() as conn:
        if status == "processing":
            conn.execute(
                """
                UPDATE ocr_jobs
                SET status = 'processing', started_at = CURRENT_TIMESTAMP, error_message = NULL
                WHERE id = ?
                """,
                (job_id,),
            )
        elif status == "completed":
            conn.execute(
                """
                UPDATE ocr_jobs
                SET status = 'completed', completed_at = CURRENT_TIMESTAMP, result_json = ?, error_message = NULL
                WHERE id = ?
                """,
                (result_json, job_id),
            )
        elif status == "failed":
            conn.execute(
                """
                UPDATE ocr_jobs
                SET status = 'failed', completed_at = CURRENT_TIMESTAMP, error_message = ?
                WHERE id = ?
                """,
                (error_message or "OCR job failed.", job_id),
            )


def run_ocr_job(job_id: str) -> None:
    job = get_ocr_job(job_id)
    if not job:
        return
    try:
        update_ocr_job_status(job_id, "processing")
        with db_connect() as conn:
            row = conn.execute("SELECT request_json FROM ocr_jobs WHERE id = ?", (job_id,)).fetchone()
        if not row:
            raise RuntimeError("OCR job request payload not found.")
        payload_data = jsonb_load(row["request_json"])
        if not isinstance(payload_data, dict):
            raise RuntimeError("OCR job request payload is invalid.")
        payload = DocumentPayload(**payload_data)
        result = process_document_payload(payload)
        update_ocr_job_status(job_id, "completed", result=result)
    except Exception as error:
        update_ocr_job_status(job_id, "failed", error_message=str(error))


def process_document_payload(payload: DocumentPayload) -> Dict[str, Any]:
    _, opencv_img = decode_base64_image(payload.image)
    h_img, w_img = opencv_img.shape[:2]
    results = []

    if not payload.rois:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp_file:
            temp_path = temp_file.name
        try:
            cv2.imwrite(temp_path, opencv_img)
            text_detection = detect_text_boxes(temp_path)
        finally:
            Path(temp_path).unlink(missing_ok=True)

        for idx, region in enumerate(text_detection.get("regions", [])):
            bbox = region.get("bbox") or {}
            x = max(0, int(float(bbox.get("x") or 0)))
            y = max(0, int(float(bbox.get("y") or 0)))
            w = max(1, int(float(bbox.get("width") or 1)))
            h = max(1, int(float(bbox.get("height") or 1)))
            w = min(w, w_img - x)
            h = min(h, h_img - y)

            crop_img = opencv_img[y : y + h, x : x + w]
            ocr_result = recognize_text_roi(crop_img) if crop_img.size > 0 else {"text": "", "confidence": 0.0, "segments": [], "raw_segments": []}
            text = str(ocr_result.get("text") or "")
            conf = float(ocr_result.get("confidence") or 0.0)
            filepath = ""
            if crop_img.size > 0:
                filename = f"line_{idx + 1}_{uuid.uuid4().hex[:6]}.png"
                filepath = _cropped_roi_path(filename)
                cv2.imwrite(filepath, crop_img)

            results.append(
                {
                    "fieldName": f"line_{idx + 1}",
                    "text": text,
                    "confidence": float(conf),
                    "saved_path": filepath,
                    "x": float(x),
                    "y": float(y),
                    "width": float(w),
                    "height": float(h),
                    "bbox": [
                        [float(x), float(y)],
                        [float(x + w), float(y)],
                        [float(x + w), float(y + h)],
                        [float(x), float(y + h)],
                    ],
                    "raw_segments": ocr_result.get("raw_segments") or ocr_result.get("segments", []),
                    "ocr_attempts": [],
                    "ocr_preprocessing": ocr_result.get("preprocessing", "paddle_text_detection_crop"),
                    "ocr_engine": ocr_result.get("engine", "paddle_thai_ocr"),
                    "ocr_model": ocr_result.get("model"),
                }
            )
    else:
        for idx, roi in enumerate(payload.rois):
            crop_img = crop_opencv_region(
                opencv_img,
                int(roi.x),
                int(roi.y),
                int(roi.width),
                int(roi.height),
            )
            if crop_img.size == 0:
                continue

            filename = f"{roi.fieldName}_{idx}_{uuid.uuid4().hex[:6]}.png"
            filepath = _cropped_roi_path(filename)
            cv2.imwrite(filepath, crop_img)

            roi_mode = (roi.roiMode or "fix").lower()
            expected_content = (roi.expectedContent or "").lower()
            if roi_mode == "flexible" and expected_content == "text":
                ocr_result = process_flexible_text_roi(crop_img)
            else:
                ocr_result = process_roi_with_engine(crop_img, roi)
            extracted_text = normalize_ocr_text(ocr_result.get("text"))
            confidence_score = float(ocr_result.get("confidence") or 0.0)
            if not extracted_text and (roi.type or "").lower() != "image":
                extracted_text = "(ไม่พบข้อความในพื้นที่ที่กำหนด)"
                confidence_score = 0.0

            results.append(
                {
                    "roiId": roi.roiId,
                    "fieldName": roi.fieldName,
                    "text": extracted_text,
                    "confidence": confidence_score,
                    "saved_path": filepath,
                    "type": roi.type,
                    "extraction_method": roi.extractionMethod,
                    "roi_mode": roi.roiMode or "fix",
                    "expected_content": roi.expectedContent,
                    "raw_segments": ocr_result.get("raw_segments") or ocr_result.get("segments", []),
                    "ocr_attempts": ocr_result.get("attempts", []),
                    "ocr_preprocessing": ocr_result.get("preprocessing", "none"),
                    "ocr_engine": ocr_result.get("engine", "unknown"),
                    "ocr_model": ocr_result.get("model"),
                    "table_rows": ocr_result.get("table_rows"),
                    "table_structured": ocr_result.get("table_structured"),
                    "table_sections": ocr_result.get("table_sections"),
                    "table_html": ocr_result.get("table_html"),
                    "table_debug": ocr_result.get("table_debug"),
                }
            )

    return {
        "success": True,
        "extracted_data": results,
    }


@app.get("/")
def read_root():
    return {
        "status": "OCR Engine Online",
        "framework": "FastAPI",
    }


@app.post("/api/ai/process")
async def process_document(payload: DocumentPayload, background_tasks: BackgroundTasks):
    try:
        if payload.async_mode:
            job_id = create_ocr_job(payload)
            background_tasks.add_task(run_ocr_job, job_id)
            return {"success": True, "job_id": job_id, "status": "queued"}
        return process_document_payload(payload)
    except PaddleThaiOcrUnavailableError as err:
        print("Paddle Thai OCR processing error:")
        import traceback

        traceback.print_exc()
        raise HTTPException(status_code=503, detail=str(err))
    except LayoutAnalysisUnavailableError as err:
        print("Paddle text detection error:")
        import traceback

        traceback.print_exc()
        raise HTTPException(status_code=503, detail=str(err))
    except Exception as err:
        print("OCR processing error:")
        import traceback

        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(err))


@app.get("/api/ai/jobs/{job_id}")
async def get_ai_process_job(job_id: str):
    job = get_ocr_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="OCR job not found.")
    response: Dict[str, Any] = {
        "success": True,
        "job_id": job["id"],
        "status": job["status"],
        "requested_at": str(job.get("requested_at")) if job.get("requested_at") is not None else None,
        "started_at": str(job.get("started_at")) if job.get("started_at") is not None else None,
        "completed_at": str(job.get("completed_at")) if job.get("completed_at") is not None else None,
    }
    if job["status"] == "completed":
        response["result"] = job.get("result") or {"success": True, "extracted_data": []}
    if job["status"] == "failed":
        response["error"] = job.get("error_message") or "OCR job failed."
    return response


@app.post("/api/layout/analyze")
async def analyze_document_layout(payload: LayoutAnalysisPayload):
    if not payload.images:
        raise HTTPException(status_code=400, detail="At least one page image is required.")

    pages: List[Dict[str, Any]] = []
    try:
        for page in payload.images:
            _, opencv_img = decode_base64_image(page.image)
            analysis = analyze_layout(opencv_img, expand_text_rois=True, auto_roi_mode="text_line")
            analysis_regions = (
                _build_flexible_paragraph_regions(opencv_img, analysis)
                if (payload.context or "").strip().lower() == "flexible"
                else analysis.get("regions", [])
            )
            regions = []
            for index, region in enumerate(analysis_regions, start=1):
                region_type = region["type"]
                extraction_method = (
                    "extract_image"
                    if region_type == "image"
                    else "table_recognition_v2"
                    if region_type == "table"
                    else "paddle_thai_ocr"
                )
                regions.append(
                    {
                        "field_name": f"{region_type}_{index}",
                        "type": region_type,
                        "data_type": region_type,
                        "extraction_method": extraction_method,
                        "confidence": region.get("confidence", 0.0),
                        "roi_expansion": region.get("roi_expansion"),
                        "auto_roi_group": region.get("auto_roi_group"),
                        "roi": {
                            "page_number": int(page.page_index) + 1,
                            **region["roi"],
                        },
                    }
                )

            pages.append(
                {
                    "page_index": page.page_index,
                    "page_number": int(page.page_index) + 1,
                    "image_width": analysis["image_width"],
                    "image_height": analysis["image_height"],
                    "engine": analysis["engine"],
                    "model": analysis["model"],
                    "regions": regions,
                    "message": None if regions else "No layout regions found on this page.",
                }
            )

        return {
            "success": True,
            "engine": "layout_model_runtime",
            "model": "PP-DocLayoutV3+PP-OCRv5",
            "auto_roi_mode": "text_line",
            "pages": pages,
        }
    except LayoutAnalysisUnavailableError as err:
        raise HTTPException(status_code=503, detail=str(err))
    except Exception as err:
        print("Layout analysis error:")
        import traceback

        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(err))
