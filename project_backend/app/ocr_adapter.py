import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Tuple

import cv2

from .layout_analysis_service import LayoutAnalysisUnavailableError, detect_text_boxes
from .ocr_postprocess import normalize_ocr_text
from .paddle_thai_ocr_adapter import PaddleThaiOcrUnavailableError, run_paddle_thai_ocr, run_paddle_thai_ocr_batch
from .table_recognition_v2_adapter import TableRecognitionV2UnavailableError, recognize_table_v2


class OcrUnavailableError(RuntimeError):
    pass


TEXT_DETECTION_MIN_BOX_SIZE = 2
TEXT_DETECTION_LINE_Y_TOLERANCE = 0.6
TEXT_DETECTION_DUPLICATE_OVERLAP_RATIO = 0.82


def _load_image():
    try:
        import numpy as np
        from PIL import Image
    except ImportError as error:
        raise OcrUnavailableError("OCR verification requires Pillow and numpy.") from error
    return Image, np


def ocr_roi(image_path: str, roi: Dict[str, Any]) -> Dict[str, Any]:
    Image, np = _load_image()
    path = Path(image_path)
    if not path.exists():
        raise ValueError(f"Verification image not found: {image_path}")

    image = Image.open(path).convert("RGB")
    image_width, image_height = image.size
    x_ratio = float(roi.get("x_ratio", 0) or 0)
    y_ratio = float(roi.get("y_ratio", 0) or 0)
    width_ratio = float(roi.get("width_ratio", 0) or 0)
    height_ratio = float(roi.get("height_ratio", 0) or 0)

    x = max(0, min(image_width - 1, int(round(x_ratio * image_width))))
    y = max(0, min(image_height - 1, int(round(y_ratio * image_height))))
    width = max(1, int(round(width_ratio * image_width)))
    height = max(1, int(round(height_ratio * image_height)))
    right = min(image_width, x + width)
    bottom = min(image_height, y + height)
    if right <= x or bottom <= y:
        raise ValueError("ROI crop is outside the image bounds")

    crop = image.crop((x, y, right, bottom))
    try:
        bgr_crop = cv2.cvtColor(np.array(crop), cv2.COLOR_RGB2BGR)
        result = recognize_text_roi(bgr_crop)
    except PaddleThaiOcrUnavailableError as error:
        raise OcrUnavailableError(str(error)) from error

    return {
        "text": result.get("text") or "",
        "confidence": round(float(result.get("confidence") or 0.0), 4),
        "preprocessing": result.get("preprocessing") or "paddle_text_recognition",
        "segments": result.get("segments") or [],
        "raw_segments": result.get("raw_segments") or result.get("segments") or [],
        "engine": result.get("engine") or "paddle_thai_ocr",
        "model": result.get("model"),
        "text_detection": result.get("text_detection"),
    }


def _temp_png_from_crop(bgr_crop) -> str:
    temp = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
    temp.close()
    if not cv2.imwrite(temp.name, bgr_crop):
        raise OcrUnavailableError("Failed to prepare ROI crop for text detection.")
    return temp.name


def _region_to_box(region: Dict[str, Any], image_width: int, image_height: int) -> Dict[str, int] | None:
    bbox = region.get("bbox") or {}
    x = max(0, min(image_width - 1, int(round(float(bbox.get("x") or 0)))))
    y = max(0, min(image_height - 1, int(round(float(bbox.get("y") or 0)))))
    width = max(0, int(round(float(bbox.get("width") or 0))))
    height = max(0, int(round(float(bbox.get("height") or 0))))
    right = min(image_width, x + width)
    bottom = min(image_height, y + height)
    width = right - x
    height = bottom - y
    if width < TEXT_DETECTION_MIN_BOX_SIZE or height < TEXT_DETECTION_MIN_BOX_SIZE:
        return None
    return {"x": x, "y": y, "width": width, "height": height}


def _box_area(box: Dict[str, int]) -> float:
    return float(max(0, box["width"]) * max(0, box["height"]))


def _intersection_area(left_box: Dict[str, int], right_box: Dict[str, int]) -> float:
    left = max(left_box["x"], right_box["x"])
    top = max(left_box["y"], right_box["y"])
    right = min(left_box["x"] + left_box["width"], right_box["x"] + right_box["width"])
    bottom = min(left_box["y"] + left_box["height"], right_box["y"] + right_box["height"])
    return float(max(0, right - left) * max(0, bottom - top))


def _deduplicate_detected_boxes(boxes: List[Dict[str, int]]) -> List[Dict[str, int]]:
    kept: List[Dict[str, int]] = []
    for box in sorted(boxes, key=lambda item: (_box_area(item), item["y"], item["x"])):
        box_area = max(1.0, _box_area(box))
        duplicate = False
        for existing in kept:
            existing_area = max(1.0, _box_area(existing))
            overlap = _intersection_area(box, existing)
            if overlap / min(box_area, existing_area) >= TEXT_DETECTION_DUPLICATE_OVERLAP_RATIO:
                duplicate = True
                break
        if not duplicate:
            kept.append(box)
    return kept


def _sort_boxes_reading_order(boxes: List[Dict[str, int]]) -> List[Dict[str, int]]:
    if not boxes:
        return []
    ordered = sorted(_deduplicate_detected_boxes(boxes), key=lambda box: (box["y"] + box["height"] / 2, box["x"]))
    lines: List[Dict[str, Any]] = []
    for box in ordered:
        center_y = box["y"] + box["height"] / 2
        matched_line = None
        for line in lines:
            tolerance = max(8.0, max(float(line["height"]), float(box["height"])) * TEXT_DETECTION_LINE_Y_TOLERANCE)
            if abs(center_y - float(line["center_y"])) <= tolerance:
                matched_line = line
                break
        if matched_line is None:
            lines.append({"center_y": center_y, "height": box["height"], "boxes": [box]})
        else:
            matched_line["boxes"].append(box)
            matched_line["center_y"] = sum(item["y"] + item["height"] / 2 for item in matched_line["boxes"]) / len(matched_line["boxes"])
            matched_line["height"] = max(item["height"] for item in matched_line["boxes"])

    sorted_boxes: List[Dict[str, int]] = []
    for line in sorted(lines, key=lambda item: item["center_y"]):
        sorted_boxes.extend(sorted(line["boxes"], key=lambda box: box["x"]))
    return sorted_boxes


def _detect_boxes_in_crop(bgr_crop) -> Tuple[List[Dict[str, int]], Dict[str, Any]]:
    image_height, image_width = bgr_crop.shape[:2]
    temp_path = ""
    try:
        temp_path = _temp_png_from_crop(bgr_crop)
        detection = detect_text_boxes(temp_path)
        boxes = [
            box
            for box in (_region_to_box(region, image_width, image_height) for region in detection.get("regions", []))
            if box is not None
        ]
        return _sort_boxes_reading_order(boxes), {
            "engine": "paddle_text_detection",
            "model": detection.get("model"),
            "box_count": len(boxes),
            "fallback_used": False,
            "error": None,
        }
    except (LayoutAnalysisUnavailableError, RuntimeError, OcrUnavailableError, ValueError) as error:
        return [], {
            "engine": "paddle_text_detection",
            "model": "PP-OCRv5_server_det",
            "box_count": 0,
            "fallback_used": True,
            "error": str(error),
        }
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError:
                pass


def _crop_box(bgr_crop, box: Dict[str, int]):
    y1 = box["y"]
    x1 = box["x"]
    y2 = min(bgr_crop.shape[0], y1 + box["height"])
    x2 = min(bgr_crop.shape[1], x1 + box["width"])
    return bgr_crop[y1:y2, x1:x2]


def _recognize_text_crops_with_detection(text_items: List[Tuple[str, Any]]) -> Dict[str, Dict[str, Any]]:
    if not text_items:
        return {}

    source_crops = {key: bgr_crop for key, bgr_crop in text_items}
    recognition_crops = []
    recognition_meta: List[Dict[str, Any]] = []
    per_key_detection: Dict[str, Dict[str, Any]] = {}

    for key, bgr_crop in text_items:
        boxes, detection_meta = _detect_boxes_in_crop(bgr_crop)
        if boxes:
            per_key_detection[key] = detection_meta
            for box in boxes:
                sub_crop = _crop_box(bgr_crop, box)
                if sub_crop.size == 0:
                    continue
                recognition_crops.append(sub_crop)
                recognition_meta.append({"key": key, "bbox": box, "fallback": False})
        else:
            fallback_meta = {**detection_meta, "fallback_used": True}
            per_key_detection[key] = fallback_meta
            recognition_crops.append(bgr_crop)
            recognition_meta.append(
                {
                    "key": key,
                    "bbox": {"x": 0, "y": 0, "width": int(bgr_crop.shape[1]), "height": int(bgr_crop.shape[0])},
                    "fallback": True,
                }
            )

    batch_results = run_paddle_thai_ocr_batch(recognition_crops)
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for meta, result in zip(recognition_meta, batch_results):
        text = normalize_ocr_text(result.get("text"))
        confidence = round(float(result.get("confidence") or 0.0), 4)
        grouped.setdefault(meta["key"], []).append(
            {
                "text": text,
                "confidence": confidence,
                "bbox": meta["bbox"],
                "engine": result.get("engine") or "paddle_thai_ocr",
                "model": result.get("model"),
                "fallback": meta["fallback"],
                "error": result.get("error"),
            }
        )

    results: Dict[str, Dict[str, Any]] = {}
    for key, _ in text_items:
        segments = grouped.get(key, [])
        text_segments = [segment["text"] for segment in segments if segment.get("text")]
        confidences = [float(segment.get("confidence") or 0.0) for segment in segments if segment.get("text")]
        detection_meta = per_key_detection.get(key, {})
        fallback_used = bool(detection_meta.get("fallback_used")) or any(segment.get("fallback") for segment in segments)
        detected_text = normalize_ocr_text(" ".join(text_segments))
        detected_confidence = round(sum(confidences) / len(confidences), 4) if confidences else 0.0
        detection_meta = {
            **detection_meta,
            "recognized_segment_count": len(text_segments),
            "empty_segment_count": len([segment for segment in segments if not segment.get("text")]),
            "detected_text_length": len(detected_text),
        }

        if not fallback_used and (not detected_text or len(detected_text) <= 2):
            full_crop = source_crops.get(key)
            if full_crop is not None and getattr(full_crop, "size", 0) > 0:
                full_result = run_paddle_thai_ocr(full_crop)
                full_text = normalize_ocr_text(full_result.get("text"))
                full_confidence = round(float(full_result.get("confidence") or 0.0), 4)
                detection_meta = {
                    **detection_meta,
                    "fallback_used": True,
                    "fallback_reason": "detected_boxes_recognized_empty_or_too_short",
                    "full_roi_confidence": full_confidence,
                    "full_roi_text_length": len(full_text),
                }
                if full_text and (not detected_text or len(full_text) > len(detected_text) or full_confidence >= detected_confidence):
                    segments.append(
                        {
                            "text": full_text,
                            "confidence": full_confidence,
                            "bbox": {
                                "x": 0,
                                "y": 0,
                                "width": int(full_crop.shape[1]),
                                "height": int(full_crop.shape[0]),
                            },
                            "engine": full_result.get("engine") or "paddle_thai_ocr",
                            "model": full_result.get("model"),
                            "fallback": True,
                            "fallback_reason": "full_roi_after_empty_detected_boxes",
                            "error": full_result.get("error"),
                        }
                    )
                    detected_text = full_text
                    detected_confidence = full_confidence
                    fallback_used = True

        results[key] = {
            "text": detected_text,
            "confidence": detected_confidence,
            "preprocessing": "paddle_text_detection_then_recognition"
            if not fallback_used
            else "paddle_text_detection_fallback_then_recognition",
            "segments": segments,
            "engine": "paddle_text_detection+paddle_thai_ocr",
            "model": segments[0].get("model") if segments else None,
            "text_detection": detection_meta,
            "error": detection_meta.get("error") if fallback_used else None,
        }
    return results


def recognize_text_crop_with_detection(bgr_crop) -> Dict[str, Any]:
    return _recognize_text_crops_with_detection([("roi", bgr_crop)])["roi"]


def recognize_text_roi(bgr_crop) -> Dict[str, Any]:
    result = recognize_text_crop_with_detection(bgr_crop)
    segments = result.get("segments") or []
    return {
        "text": normalize_ocr_text(result.get("text")),
        "confidence": round(float(result.get("confidence") or 0.0), 4),
        "preprocessing": result.get("preprocessing") or "paddle_text_detection_then_recognition",
        "segments": segments,
        "raw_segments": segments,
        "engine": result.get("engine") or "paddle_text_detection+paddle_thai_ocr",
        "model": result.get("model"),
        "text_detection": result.get("text_detection"),
        "error": result.get("error"),
    }


def _crop_roi_from_image(image, roi: Dict[str, Any]):
    image_width, image_height = image.size
    x_ratio = float(roi.get("x_ratio", 0) or 0)
    y_ratio = float(roi.get("y_ratio", 0) or 0)
    width_ratio = float(roi.get("width_ratio", 0) or 0)
    height_ratio = float(roi.get("height_ratio", 0) or 0)

    x = max(0, min(image_width - 1, int(round(x_ratio * image_width))))
    y = max(0, min(image_height - 1, int(round(y_ratio * image_height))))
    width = max(1, int(round(width_ratio * image_width)))
    height = max(1, int(round(height_ratio * image_height)))
    right = min(image_width, x + width)
    bottom = min(image_height, y + height)
    if right <= x or bottom <= y:
        raise ValueError("ROI crop is outside the image bounds")
    return image.crop((x, y, right, bottom))


def _is_table_item(item: Dict[str, Any]) -> bool:
    data_type = str(item.get("data_type") or item.get("dataType") or "").lower()
    extraction_method = str(item.get("extraction_method") or item.get("extractionMethod") or "").lower()
    return data_type == "table" or extraction_method in {"table_recognition_v2", "ocr_table"}


def ocr_rois(image_path: str, roi_items: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    if not roi_items:
        return {}

    Image, np = _load_image()
    path = Path(image_path)
    if not path.exists():
        raise ValueError(f"Verification image not found: {image_path}")

    image = Image.open(path).convert("RGB")
    text_items: List[Tuple[str, Any]] = []
    failed: Dict[str, Dict[str, Any]] = {}
    for index, item in enumerate(roi_items):
        key = str(item.get("id") or item.get("field_id") or index)
        try:
            crop = _crop_roi_from_image(image, item.get("roi") or item)
            bgr_crop = cv2.cvtColor(np.array(crop), cv2.COLOR_RGB2BGR)
            if _is_table_item(item):
                table_result = recognize_table_v2(bgr_crop)
                results_text = str(table_result.get("text") or "")
                failed[key] = {
                    "text": results_text,
                    "confidence": round(float(table_result.get("confidence") or 0.0), 4),
                    "preprocessing": table_result.get("preprocessing") or "paddle_table_structure_recognition",
                    "segments": table_result.get("segments") or [],
                    "engine": table_result.get("engine") or "table_recognition_v2",
                    "model": table_result.get("model"),
                    "table_html": table_result.get("table_html"),
                    "table_rows": table_result.get("table_rows"),
                    "table_structured": table_result.get("table_structured"),
                    "table_debug": table_result.get("table_debug"),
                    "error": table_result.get("error"),
                }
            else:
                text_items.append((key, bgr_crop))
        except TableRecognitionV2UnavailableError as error:
            failed[key] = {
                "text": "",
                "confidence": 0.0,
                "preprocessing": "paddle_table_structure_recognition",
                "segments": [],
                "engine": "table_recognition_v2",
                "model": None,
                "error": str(error),
            }
        except Exception as error:
            failed[key] = {
                "text": "",
                "confidence": 0.0,
                "preprocessing": "paddle_text_recognition",
                "segments": [],
                "engine": "paddle_thai_ocr",
                "model": None,
                "error": str(error),
            }

    results: Dict[str, Dict[str, Any]] = dict(failed)
    if not text_items:
        return results

    try:
        text_results = _recognize_text_crops_with_detection(text_items)
    except PaddleThaiOcrUnavailableError as error:
        raise OcrUnavailableError(str(error)) from error

    for key, result in text_results.items():
        segments = result.get("segments") or []
        results[key] = {
            "text": normalize_ocr_text(result.get("text")),
            "confidence": round(float(result.get("confidence") or 0.0), 4),
            "preprocessing": result.get("preprocessing") or "paddle_text_detection_then_recognition",
            "segments": segments,
            "raw_segments": segments,
            "engine": result.get("engine") or "paddle_text_detection+paddle_thai_ocr",
            "model": result.get("model"),
            "text_detection": result.get("text_detection"),
            "error": result.get("error"),
        }
    return results


def ocr_text_regions(image_path: str) -> Dict[str, Any]:
    path = Path(image_path)
    if not path.exists():
        raise ValueError(f"OCR image not found: {image_path}")

    image = cv2.imread(str(path))
    if image is None or image.size == 0:
        raise ValueError(f"OCR image not readable: {image_path}")

    try:
        detection = detect_text_boxes(str(path))
    except (LayoutAnalysisUnavailableError, RuntimeError) as error:
        raise OcrUnavailableError(str(error)) from error

    image_width = int(detection.get("image_width") or image.shape[1])
    image_height = int(detection.get("image_height") or image.shape[0])

    regions: List[Dict[str, Any]] = []
    for region in detection.get("regions", []):
        bbox = region.get("bbox") or {}
        x = max(0, int(float(bbox.get("x") or 0)))
        y = max(0, int(float(bbox.get("y") or 0)))
        width = max(1, int(float(bbox.get("width") or 1)))
        height = max(1, int(float(bbox.get("height") or 1)))
        crop = image[y : min(image.shape[0], y + height), x : min(image.shape[1], x + width)]
        if crop.size == 0:
            continue
        try:
            recognized = run_paddle_thai_ocr(crop)
        except PaddleThaiOcrUnavailableError as error:
            raise OcrUnavailableError(str(error)) from error
        text = str(recognized.get("text") or "").strip()
        confidence = float(recognized.get("confidence") or region.get("confidence") or 0.0)
        regions.append(
            {
                "text": text,
                "confidence": round(confidence, 4),
                "bbox": {
                    "x": round(float(x), 4),
                    "y": round(float(y), 4),
                    "width": round(float(width), 4),
                    "height": round(float(height), 4),
                },
                "center": {
                    "x": round(float(x) + float(width) / 2, 4),
                    "y": round(float(y) + float(height) / 2, 4),
                },
                "engine": recognized.get("engine") or "paddle_thai_ocr",
                "model": recognized.get("model"),
            }
        )

    return {
        "engine": "paddleocr",
        "model": detection.get("model"),
        "image_width": image_width,
        "image_height": image_height,
        "regions": regions,
    }
