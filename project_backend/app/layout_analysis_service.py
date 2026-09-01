import logging
import tempfile
from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Literal

import cv2
import numpy as np

from .model_runtime_client import (
    ModelRuntimeKind,
    ModelRuntimeUnavailableError,
    is_runtime_configured,
    remote_analyze_layout,
    remote_detect_text_boxes,
)

logger = logging.getLogger(__name__)

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_PADDLEX_CACHE_HOME = _BACKEND_ROOT / "storage" / "paddlex_cache"
os.environ.setdefault("PADDLE_PDX_CACHE_HOME", str(_PADDLEX_CACHE_HOME))
os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
os.environ.setdefault("PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT", "False")
os.environ.setdefault("PADDLE_PDX_USE_PIR_TRT", "False")
os.environ.setdefault("FLAGS_use_mkldnn", "0")
os.environ.setdefault("FLAGS_json_format_model", "False")


class LayoutAnalysisUnavailableError(RuntimeError):
    pass


@dataclass
class LayoutRegion:
    region_type: str
    x_ratio: float
    y_ratio: float
    width_ratio: float
    height_ratio: float
    confidence: float = 0.0


_LAYOUT_MODEL: Any = None
_TEXT_DETECTOR: Any = None
_LAYOUT_MODEL_NAME = "PP-DocLayoutV3"
_TEXT_DETECTION_MODEL_NAME = "PP-OCRv5_server_det"
AUTO_ROI_EXPAND_TOP_PX = float(os.getenv("AUTO_ROI_EXPAND_TOP_PX", "8"))
AUTO_ROI_EXPAND_BOTTOM_PX = float(os.getenv("AUTO_ROI_EXPAND_BOTTOM_PX", "8"))
AUTO_ROI_EXPAND_LEFT_PX = float(os.getenv("AUTO_ROI_EXPAND_LEFT_PX", "8"))
AUTO_ROI_EXPAND_RIGHT_PX = float(os.getenv("AUTO_ROI_EXPAND_RIGHT_PX", "8"))
AUTO_ROI_TABLE_EXPAND_TOP_PX = float(os.getenv("AUTO_ROI_TABLE_EXPAND_TOP_PX", "2"))
AUTO_ROI_TABLE_EXPAND_BOTTOM_PX = float(os.getenv("AUTO_ROI_TABLE_EXPAND_BOTTOM_PX", "2"))
AUTO_ROI_TABLE_EXPAND_LEFT_PX = float(os.getenv("AUTO_ROI_TABLE_EXPAND_LEFT_PX", "2"))
AUTO_ROI_TABLE_EXPAND_RIGHT_PX = float(os.getenv("AUTO_ROI_TABLE_EXPAND_RIGHT_PX", "2"))
AUTO_ROI_MAX_NEIGHBOR_OVERLAP_RATIO = float(os.getenv("AUTO_ROI_MAX_NEIGHBOR_OVERLAP_RATIO", "0.15"))

AutoRoiMode = Literal["text_line"]


def _require_runtime(kind: ModelRuntimeKind) -> None:
    if not is_runtime_configured(kind):
        raise LayoutAnalysisUnavailableError(f"{kind.value} model runtime URL is not configured.")


def _common_model_kwargs() -> Dict[str, Any]:
    return {
        "device": "cpu",
        "enable_mkldnn": False,
        "enable_cinn": False,
        "use_tensorrt": False,
    }


def _load_layout_model() -> Any:
    raise LayoutAnalysisUnavailableError("Backend no longer loads layout models. Set LAYOUT_MODEL_URL.")


def _load_text_detector() -> Any:
    raise LayoutAnalysisUnavailableError("Backend no longer loads text detection models. Set TEXT_DETECTION_MODEL_URL.")


def _clamp_ratio(value: float) -> float:
    return min(1.0, max(0.0, float(value)))


def _normalize_region_type(value: Any) -> str:
    label = str(value or "text").lower()
    if "table" in label and "title" not in label and "caption" not in label:
        return "table"
    if any(token in label for token in ("image", "figure", "pic", "seal", "logo", "chart")):
        return "image"
    return "text"


def _box_from_points(points: Any) -> Optional[List[float]]:
    if points is None:
        return None
    if isinstance(points, np.ndarray):
        points = points.tolist()
    if isinstance(points, (list, tuple)) and len(points) == 0:
        return None
    if isinstance(points, (list, tuple)) and len(points) == 4 and all(isinstance(item, (int, float)) for item in points):
        x1, y1, x2, y2 = [float(item) for item in points]
        return [x1, y1, x2, y2]
    if isinstance(points, (list, tuple)):
        xs: List[float] = []
        ys: List[float] = []
        for point in points:
            if isinstance(point, (list, tuple)) and len(point) >= 2:
                try:
                    xs.append(float(point[0]))
                    ys.append(float(point[1]))
                except (TypeError, ValueError):
                    continue
        if xs and ys:
            return [min(xs), min(ys), max(xs), max(ys)]
    return None


def _extract_box(item: Dict[str, Any]) -> Optional[List[float]]:
    for key in ("bbox", "box", "layout_bbox", "coordinate", "coordinates", "dt_polys", "poly", "points"):
        if key in item:
            box = _box_from_points(item.get(key))
            if box:
                return box

    if all(key in item for key in ("x", "y", "width", "height")):
        try:
            x = float(item["x"])
            y = float(item["y"])
            return [x, y, x + float(item["width"]), y + float(item["height"])]
        except (TypeError, ValueError):
            return None
    return None


def _extract_label(item: Dict[str, Any]) -> str:
    for key in ("type", "label", "category", "layout_type", "block_type", "region_type"):
        if item.get(key):
            return str(item[key])
    return "text"


def _extract_score(item: Dict[str, Any]) -> float:
    for key in ("score", "confidence", "prob"):
        value = item.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return 0.0


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
            pass
    return None


def _walk_layout_items(value: Any) -> List[Dict[str, Any]]:
    found: List[Dict[str, Any]] = []
    item_dict = _as_dict(value)
    if item_dict is not None:
        if _extract_box(item_dict):
            found.append(item_dict)
        for child in item_dict.values():
            found.extend(_walk_layout_items(child))
        return found
    if isinstance(value, (list, tuple)):
        for child in value:
            found.extend(_walk_layout_items(child))
    return found


def _run_layout_detection(image_path: str) -> List[Dict[str, Any]]:
    pipeline = _load_layout_model()
    predict = getattr(pipeline, "predict", None)
    result = predict(input=image_path, batch_size=1) if callable(predict) else pipeline(image_path)
    items = _walk_layout_items(result)
    return [
        item
        for item in items
        if _normalize_region_type(_extract_label(item)) in {"table", "image"}
    ]


def _run_text_detection(image_path: str) -> List[Dict[str, Any]]:
    detector = _load_text_detector()
    predict = getattr(detector, "predict", None)
    result = predict(input=image_path, batch_size=1) if callable(predict) else detector(image_path)
    text_items: List[Dict[str, Any]] = []
    for item in result if isinstance(result, (list, tuple)) else [result]:
        item_dict = _as_dict(item) or {}
        data = item_dict.get("res") if isinstance(item_dict.get("res"), dict) else item_dict
        polygons = data.get("dt_polys") if isinstance(data, dict) else None
        scores = data.get("dt_scores") if isinstance(data, dict) else None
        if isinstance(polygons, np.ndarray):
            polygons = polygons.tolist()
        if isinstance(scores, np.ndarray):
            scores = scores.tolist()
        if not isinstance(polygons, (list, tuple)):
            continue
        for index, polygon in enumerate(polygons):
            score = 0.0
            if isinstance(scores, (list, tuple)) and index < len(scores):
                try:
                    score = float(scores[index])
                except (TypeError, ValueError):
                    score = 0.0
            text_items.append(
                {
                    "label": "text",
                    "dt_polys": polygon,
                    "score": score,
                    "source": "text_detection",
                }
            )
    return text_items


def _run_pipeline(image: np.ndarray, image_path: str) -> List[Dict[str, Any]]:
    return [*_run_text_detection(image_path), *_run_layout_detection(image_path)]


def _intersection_area(box_a: List[float], box_b: List[float]) -> float:
    left = max(min(box_a[0], box_a[2]), min(box_b[0], box_b[2]))
    top = max(min(box_a[1], box_a[3]), min(box_b[1], box_b[3]))
    right = min(max(box_a[0], box_a[2]), max(box_b[0], box_b[2]))
    bottom = min(max(box_a[1], box_a[3]), max(box_b[1], box_b[3]))
    return max(0.0, right - left) * max(0.0, bottom - top)


def _box_area(box: List[float]) -> float:
    return max(0.0, abs(float(box[2]) - float(box[0]))) * max(0.0, abs(float(box[3]) - float(box[1])))


def _box_width(box: List[float]) -> float:
    return max(0.0, abs(float(box[2]) - float(box[0])))


def _box_height(box: List[float]) -> float:
    return max(0.0, abs(float(box[3]) - float(box[1])))


def _median(values: List[float], fallback: float = 0.0) -> float:
    prepared = sorted(float(value) for value in values if np.isfinite(float(value)) and float(value) > 0)
    if not prepared:
        return fallback
    middle = len(prepared) // 2
    if len(prepared) % 2:
        return prepared[middle]
    return (prepared[middle - 1] + prepared[middle]) / 2.0


def _clip_box_to_image(box: List[float], image_width: int, image_height: int) -> List[float]:
    left = max(0.0, min(float(image_width), min(float(box[0]), float(box[2]))))
    top = max(0.0, min(float(image_height), min(float(box[1]), float(box[3]))))
    right = max(0.0, min(float(image_width), max(float(box[0]), float(box[2]))))
    bottom = max(0.0, min(float(image_height), max(float(box[1]), float(box[3]))))
    return [left, top, right, bottom]


def _overlap_ratio_against_smaller_box(box_a: List[float], box_b: List[float]) -> float:
    smaller_area = max(1.0, min(_box_area(box_a), _box_area(box_b)))
    return _intersection_area(box_a, box_b) / smaller_area


def _expand_text_roi_box(box: List[float], image_width: int, image_height: int) -> List[float]:
    left, top, right, bottom = _clip_box_to_image(box, image_width, image_height)
    expanded = [
        left - AUTO_ROI_EXPAND_LEFT_PX,
        top - AUTO_ROI_EXPAND_TOP_PX,
        right + AUTO_ROI_EXPAND_RIGHT_PX,
        bottom + AUTO_ROI_EXPAND_BOTTOM_PX,
    ]
    return _clip_box_to_image(expanded, image_width, image_height)


def _expand_table_roi_box(box: List[float], image_width: int, image_height: int) -> List[float]:
    left, top, right, bottom = _clip_box_to_image(box, image_width, image_height)
    expanded = [
        left - AUTO_ROI_TABLE_EXPAND_LEFT_PX,
        top - AUTO_ROI_TABLE_EXPAND_TOP_PX,
        right + AUTO_ROI_TABLE_EXPAND_RIGHT_PX,
        bottom + AUTO_ROI_TABLE_EXPAND_BOTTOM_PX,
    ]
    return _clip_box_to_image(expanded, image_width, image_height)


def _reduce_box_overlap(
    original_box: List[float],
    expanded_box: List[float],
    neighbor_boxes: List[List[float]],
) -> List[float]:
    adjusted = expanded_box[:]
    for neighbor in neighbor_boxes:
        if _box_area(neighbor) <= 0:
            continue
        if _overlap_ratio_against_smaller_box(adjusted, neighbor) <= AUTO_ROI_MAX_NEIGHBOR_OVERLAP_RATIO:
            continue

        original_left, original_top, original_right, original_bottom = original_box
        neighbor_left, neighbor_top, neighbor_right, neighbor_bottom = neighbor

        if original_right <= neighbor_left:
            adjusted[2] = min(adjusted[2], neighbor_left)
        elif original_left >= neighbor_right:
            adjusted[0] = max(adjusted[0], neighbor_right)

        if original_bottom <= neighbor_top:
            adjusted[3] = min(adjusted[3], neighbor_top)
        elif original_top >= neighbor_bottom:
            adjusted[1] = max(adjusted[1], neighbor_bottom)

        if adjusted[2] <= adjusted[0] or adjusted[3] <= adjusted[1]:
            return original_box[:]
        if _overlap_ratio_against_smaller_box(adjusted, neighbor) > AUTO_ROI_MAX_NEIGHBOR_OVERLAP_RATIO:
            return original_box[:]
    return adjusted


def _prepare_auto_roi_box(
    box: List[float],
    region_type: str,
    image_width: int,
    image_height: int,
    neighbor_boxes: List[List[float]],
) -> Dict[str, Any]:
    original_box = _clip_box_to_image(box, image_width, image_height)
    if region_type == "table":
        expanded_box = _expand_table_roi_box(original_box, image_width, image_height)
        return {
            "box": expanded_box,
            "expansion": {
                "enabled": True,
                "reason": "table_edge_guard_padding",
                "original_box": original_box,
                "expanded_box": expanded_box,
                "final_box": expanded_box,
                "padding": {
                    "unit": "px",
                    "top": AUTO_ROI_TABLE_EXPAND_TOP_PX,
                    "bottom": AUTO_ROI_TABLE_EXPAND_BOTTOM_PX,
                    "left": AUTO_ROI_TABLE_EXPAND_LEFT_PX,
                    "right": AUTO_ROI_TABLE_EXPAND_RIGHT_PX,
                },
            },
        }

    if region_type != "text":
        return {
            "box": original_box,
            "expansion": {
                "enabled": False,
                "reason": "non_expandable_region",
                "original_box": original_box,
                "expanded_box": original_box,
            },
        }

    expanded_box = _expand_text_roi_box(original_box, image_width, image_height)
    adjusted_box = _reduce_box_overlap(original_box, expanded_box, neighbor_boxes)
    return {
        "box": adjusted_box,
        "expansion": {
            "enabled": True,
            "original_box": original_box,
            "expanded_box": expanded_box,
            "final_box": adjusted_box,
            "padding": {
                "unit": "px",
                "top": AUTO_ROI_EXPAND_TOP_PX,
                "bottom": AUTO_ROI_EXPAND_BOTTOM_PX,
                "left": AUTO_ROI_EXPAND_LEFT_PX,
                "right": AUTO_ROI_EXPAND_RIGHT_PX,
            },
            "max_neighbor_overlap": AUTO_ROI_MAX_NEIGHBOR_OVERLAP_RATIO,
            "overlap_adjusted": adjusted_box != expanded_box,
        },
    }


def _box_center_inside(inner_box: List[float], outer_box: List[float]) -> bool:
    cx = (float(inner_box[0]) + float(inner_box[2])) / 2
    cy = (float(inner_box[1]) + float(inner_box[3])) / 2
    left = min(float(outer_box[0]), float(outer_box[2]))
    right = max(float(outer_box[0]), float(outer_box[2]))
    top = min(float(outer_box[1]), float(outer_box[3]))
    bottom = max(float(outer_box[1]), float(outer_box[3]))
    return left <= cx <= right and top <= cy <= bottom


def _text_box_belongs_to_table(text_box: List[float], table_boxes: List[List[float]]) -> bool:
    text_area = max(_box_area(text_box), 1.0)
    for table_box in table_boxes:
        if _box_center_inside(text_box, table_box):
            return True
        if _intersection_area(text_box, table_box) / text_area >= 0.25:
            return True
    return False


def _is_tiny_text_fragment(box: List[float], median_height: float, median_area: float) -> bool:
    if median_height <= 0 or median_area <= 0:
        return False
    height = _box_height(box)
    area = _box_area(box)
    width = _box_width(box)
    return (
        height <= median_height * 0.42
        and area <= median_area * 0.28
        and width <= median_height * 2.5
    )


def _filter_tiny_text_fragments(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    text_boxes = [item["box"] for item in items if item.get("type") == "text"]
    if len(text_boxes) < 2:
        return items
    median_height = _median([_box_height(box) for box in text_boxes], 0.0)
    median_area = _median([_box_area(box) for box in text_boxes], 0.0)
    filtered: List[Dict[str, Any]] = []
    for item in items:
        if item.get("type") == "text" and _is_tiny_text_fragment(item["box"], median_height, median_area):
            logger.debug(
                "Auto ROI dropped tiny text fragment box=%s median_height=%.2f median_area=%.2f",
                item["box"],
                median_height,
                median_area,
            )
            continue
        filtered.append(item)
    return filtered


def _filter_nested_same_type_regions(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    ordered = sorted(items, key=lambda item: _box_area(item["box"]), reverse=True)
    kept: List[Dict[str, Any]] = []
    for item in ordered:
        box = item["box"]
        area = max(_box_area(box), 1.0)
        region_type = item.get("type")
        nested = False
        for existing in kept:
            if existing.get("type") != region_type:
                continue
            existing_box = existing["box"]
            existing_area = max(_box_area(existing_box), 1.0)
            overlap_ratio = _intersection_area(box, existing_box) / area
            area_ratio = area / existing_area
            if overlap_ratio >= 0.88 and area_ratio <= 0.72:
                nested = True
                logger.debug(
                    "Auto ROI dropped nested %s box=%s inside=%s overlap=%.3f area_ratio=%.3f",
                    region_type,
                    box,
                    existing_box,
                    overlap_ratio,
                    area_ratio,
                )
                break
        if not nested:
            kept.append(item)
    kept.sort(key=lambda item: (min(item["box"][1], item["box"][3]), min(item["box"][0], item["box"][2])))
    return kept


def _filter_auto_roi_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return _filter_nested_same_type_regions(_filter_tiny_text_fragments(items))


def _response_region_to_item(region: Dict[str, Any], image_width: int, image_height: int) -> Optional[Dict[str, Any]]:
    roi = region.get("roi") if isinstance(region.get("roi"), dict) else None
    bbox = region.get("bbox") if isinstance(region.get("bbox"), dict) else None
    try:
        if roi:
            left = float(roi.get("x_ratio") or 0.0) * image_width
            top = float(roi.get("y_ratio") or 0.0) * image_height
            right = left + float(roi.get("width_ratio") or 0.0) * image_width
            bottom = top + float(roi.get("height_ratio") or 0.0) * image_height
        elif bbox:
            left = float(bbox.get("x") or 0.0)
            top = float(bbox.get("y") or 0.0)
            right = left + float(bbox.get("width") or 0.0)
            bottom = top + float(bbox.get("height") or 0.0)
        else:
            return None
    except (TypeError, ValueError):
        return None
    return {
        "box": _clip_box_to_image([left, top, right, bottom], image_width, image_height),
        "type": _normalize_region_type(region.get("type") or region.get("data_type") or region.get("label")),
        "confidence": _extract_score(region),
        "region": region,
    }


def _filter_response_regions(regions: List[Dict[str, Any]], image_width: int, image_height: int) -> List[Dict[str, Any]]:
    items = []
    passthrough: List[Dict[str, Any]] = []
    for region in regions:
        item = _response_region_to_item(region, image_width, image_height)
        if item:
            items.append(item)
        else:
            passthrough.append(region)
    filtered_items = _filter_auto_roi_items(items)
    kept_regions = [item["region"] for item in filtered_items]
    kept_regions.extend(passthrough)
    kept_regions.sort(key=lambda region: (
        float(((region.get("roi") if isinstance(region.get("roi"), dict) else {}) or {}).get("y_ratio") or 0.0),
        float(((region.get("roi") if isinstance(region.get("roi"), dict) else {}) or {}).get("x_ratio") or 0.0),
    ))
    return kept_regions


def _image_box_contains_text(image_box: List[float], text_boxes: List[List[float]]) -> bool:
    for text_box in text_boxes:
        text_area = max(_box_area(text_box), 1.0)
        if _box_center_inside(text_box, image_box):
            return True
        if _intersection_area(text_box, image_box) / text_area >= 0.35:
            return True
    return False


def analyze_layout(image: np.ndarray, expand_text_rois: bool = False, auto_roi_mode: AutoRoiMode = "text_line") -> Dict[str, Any]:
    if image is None or image.size == 0:
        raise ValueError("Invalid image for layout analysis.")
    auto_roi_mode = "text_line"

    height, width = image.shape[:2]
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp_file:
        temp_path = temp_file.name
    try:
        cv2.imwrite(temp_path, image)
        _require_runtime(ModelRuntimeKind.LAYOUT)
        _require_runtime(ModelRuntimeKind.TEXT_DETECTION)
        logger.info("Using remote Layout and TextDetection runtimes")
        try:
            layout_result = remote_analyze_layout(image)
            text_result = remote_detect_text_boxes(temp_path)
        except ModelRuntimeUnavailableError as error:
            raise LayoutAnalysisUnavailableError(str(error)) from error
        except Exception as error:
            raise LayoutAnalysisUnavailableError(str(error)) from error
        if not isinstance(layout_result, dict):
            raise LayoutAnalysisUnavailableError("Layout runtime returned an invalid response.")
        if not isinstance(text_result, dict):
            raise LayoutAnalysisUnavailableError("TextDetection runtime returned an invalid response.")
        raw_items = [
            *_walk_layout_items(text_result.get("result", text_result)),
            *_walk_layout_items(layout_result.get("result", layout_result)),
        ]
    finally:
        Path(temp_path).unlink(missing_ok=True)

    parsed_items: List[Dict[str, Any]] = []
    for item in raw_items:
        box = _extract_box(item)
        if not box:
            continue
        region_type = _normalize_region_type(_extract_label(item))
        parsed_items.append(
            {
                "box": box,
                "type": region_type,
                "confidence": _extract_score(item),
            }
        )

    table_boxes = [item["box"] for item in parsed_items if item["type"] == "table"]
    text_boxes = [item["box"] for item in parsed_items if item["type"] == "text"]

    filtered_items: List[Dict[str, Any]] = []
    for item in parsed_items:
        box = item["box"]
        region_type = item["type"]
        if region_type == "text" and _text_box_belongs_to_table(box, table_boxes):
            continue
        if region_type == "image" and _image_box_contains_text(box, text_boxes):
            continue

        filtered_items.append(item)

    filtered_items = _filter_auto_roi_items(filtered_items)

    layout_blocker_boxes = [item["box"] for item in filtered_items if item["type"] in {"table", "image"}]
    original_boxes = [_clip_box_to_image(item["box"], width, height) for item in filtered_items]
    regions: List[Dict[str, Any]] = []
    for item_index, item in enumerate(filtered_items):
        region_type = item["type"]
        prepared = (
            _prepare_auto_roi_box(
                item["box"],
                region_type,
                width,
                height,
                [box for index, box in enumerate(original_boxes) if index != item_index],
            )
            if expand_text_rois
            else {
                "box": original_boxes[item_index],
                "expansion": {
                    "enabled": False,
                    "reason": "disabled",
                    "original_box": original_boxes[item_index],
                    "expanded_box": original_boxes[item_index],
                },
            }
        )

        left, top, right, bottom = prepared["box"]
        box_width = right - left
        box_height = bottom - top
        if box_width < 4 or box_height < 4:
            continue

        regions.append(
            {
                "type": region_type,
                "confidence": float(item["confidence"]),
                "auto_roi_group": item.get("auto_roi_group"),
                "roi": {
                    "x_ratio": _clamp_ratio(left / max(width, 1)),
                    "y_ratio": _clamp_ratio(top / max(height, 1)),
                    "width_ratio": _clamp_ratio(box_width / max(width, 1)),
                    "height_ratio": _clamp_ratio(box_height / max(height, 1)),
                },
                "roi_expansion": prepared["expansion"],
            }
        )

    regions.sort(key=lambda region: (region["roi"]["y_ratio"], region["roi"]["x_ratio"], -region["roi"]["width_ratio"] * region["roi"]["height_ratio"]))

    return {
        "engine": "paddleocr",
        "model": f"{_LAYOUT_MODEL_NAME}+{_TEXT_DETECTION_MODEL_NAME}",
        "image_width": width,
        "image_height": height,
        "regions": regions,
        "auto_roi_expansion": {
            "enabled": expand_text_rois,
            "unit": "px",
            "top": AUTO_ROI_EXPAND_TOP_PX,
            "bottom": AUTO_ROI_EXPAND_BOTTOM_PX,
            "left": AUTO_ROI_EXPAND_LEFT_PX,
            "right": AUTO_ROI_EXPAND_RIGHT_PX,
            "max_neighbor_overlap": AUTO_ROI_MAX_NEIGHBOR_OVERLAP_RATIO,
            "mode": auto_roi_mode,
        },
    }


def detect_text_boxes(image_path: str) -> Dict[str, Any]:
    image = cv2.imread(image_path)
    if image is None or image.size == 0:
        raise ValueError("Invalid image for text box detection.")

    height, width = image.shape[:2]
    _require_runtime(ModelRuntimeKind.TEXT_DETECTION)
    logger.info("Using remote TextDetection runtime")
    try:
        remote_result = remote_detect_text_boxes(image_path)
    except ModelRuntimeUnavailableError as error:
        raise LayoutAnalysisUnavailableError(str(error)) from error
    except Exception as error:
        raise LayoutAnalysisUnavailableError(str(error)) from error
    if not isinstance(remote_result, dict):
        raise LayoutAnalysisUnavailableError("TextDetection runtime returned an invalid response.")
    raw_items = _walk_layout_items(remote_result.get("result", remote_result))
    parsed_items: List[Dict[str, Any]] = []
    for item in raw_items:
        box = _extract_box(item)
        if not box:
            continue
        parsed_items.append({"box": _clip_box_to_image(box, width, height), "type": "text", "confidence": _extract_score(item)})
    parsed_items = _filter_auto_roi_items(parsed_items)
    regions: List[Dict[str, Any]] = []
    for item in parsed_items:
        box = item["box"]
        x1, y1, x2, y2 = box
        left = max(0.0, min(float(width), min(float(x1), float(x2))))
        top = max(0.0, min(float(height), min(float(y1), float(y2))))
        right = max(0.0, min(float(width), max(float(x1), float(x2))))
        bottom = max(0.0, min(float(height), max(float(y1), float(y2))))
        box_width = right - left
        box_height = bottom - top
        if box_width < 2 or box_height < 2:
            continue
        regions.append(
            {
                "text": "",
                "confidence": item.get("confidence", 0.0),
                "bbox": {
                    "x": left,
                    "y": top,
                    "width": box_width,
                    "height": box_height,
                },
                "roi": {
                    "x_ratio": _clamp_ratio(left / max(width, 1)),
                    "y_ratio": _clamp_ratio(top / max(height, 1)),
                    "width_ratio": _clamp_ratio(box_width / max(width, 1)),
                    "height_ratio": _clamp_ratio(box_height / max(height, 1)),
                },
            }
        )

    return {
        "engine": "paddleocr",
        "model": _TEXT_DETECTION_MODEL_NAME,
        "image_width": width,
        "image_height": height,
        "regions": regions,
    }
