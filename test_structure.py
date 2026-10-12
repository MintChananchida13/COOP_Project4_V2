import argparse
import difflib
import html
import json
import os
import statistics
import sys
import tempfile
import traceback
from dataclasses import dataclass
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

os.environ["FLAGS_use_mkldnn"] = "0"
os.environ["FLAGS_enable_pir_api"] = "0"

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT = BASE_DIR / "input"
DEFAULT_OUTPUT = BASE_DIR / "output"
DETECTION_MODEL = "PP-OCRv6_medium_det"
RECOGNITION_MODEL = "th_PP-OCRv5_mobile_rec"
IMAGE_SUFFIXES = {".bmp", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}


@dataclass(frozen=True)
class TableStructureRules:
    minimum_pixel_tolerance: float = 3.0
    edge_cluster_image_ratio: float = 0.01
    ocr_column_cluster_image_ratio: float = 0.025
    row_cluster_text_height_ratio: float = 0.75
    short_cell_image_width_ratio: float = 0.9
    full_width_cell_ratio: float = 0.95
    vertical_rule_dark_threshold: int = 110
    table_rule_dark_threshold: int = 130
    vertical_rule_minimum_coverage: float = 0.65
    horizontal_rule_minimum_coverage: float = 0.45
    material_overlap_ratio: float = 0.05
    tiny_box_width_ratio: float = 0.25
    tiny_box_height_ratio: float = 0.5
    tiny_box_area_ratio: float = 0.08
    grid_neighbor_overlap_ratio: float = 0.25
    same_axis_overlap_ratio: float = 0.5
    full_width_artifact_height_ratio: float = 0.75
    trailing_artifact_height_ratio: float = 0.65
    row_boundary_cell_height_ratio: float = 0.1
    trailing_artifact_width_ratio: float = 0.35
    sparse_grid_cell_to_ocr_ratio: float = 1.0


STRUCTURE_RULES = TableStructureRules()


def box_from_values(values: Any) -> list[float] | None:
    if not isinstance(values, (list, tuple)) or len(values) < 4:
        return None

    try:
        numbers = [float(value) for value in values]
    except (TypeError, ValueError):
        return None

    if len(numbers) >= 8:
        xs = numbers[0::2]
        ys = numbers[1::2]
        box = [min(xs), min(ys), max(xs), max(ys)]
    else:
        box = numbers[:4]

    if box[2] <= box[0] or box[3] <= box[1]:
        return None
    return box


def intersection_area(first: list[float], second: list[float]) -> float:
    width = max(0.0, min(first[2], second[2]) - max(first[0], second[0]))
    height = max(0.0, min(first[3], second[3]) - max(first[1], second[1]))
    return width * height


def is_nested_ocr_fragment(
    fragment: dict[str, Any],
    primary: dict[str, Any],
) -> bool:
    fragment_box = fragment["bbox"]
    primary_box = primary["bbox"]
    fragment_area = (fragment_box[2] - fragment_box[0]) * (
        fragment_box[3] - fragment_box[1]
    )
    primary_area = (primary_box[2] - primary_box[0]) * (
        primary_box[3] - primary_box[1]
    )
    center_x = (fragment_box[0] + fragment_box[2]) / 2
    center_y = (fragment_box[1] + fragment_box[3]) / 2
    return (
        fragment_area <= primary_area * 0.25
        and primary_box[0] - 2 <= center_x <= primary_box[2] + 2
        and primary_box[1] - 2 <= center_y <= primary_box[3] + 2
    )


def row_major_cells(cell_boxes: list[list[float]]) -> list[dict[str, Any]]:
    valid_boxes = [
        (index, box)
        for index, raw_box in enumerate(cell_boxes)
        if (box := box_from_values(raw_box)) is not None
    ]
    if not valid_boxes:
        return []

    heights = [box[3] - box[1] for _, box in valid_boxes]
    row_tolerance = max(3.0, statistics.median(heights) * 0.25)
    valid_boxes.sort(key=lambda item: (item[1][1], item[1][0]))

    rows: list[dict[str, Any]] = []
    for original_index, box in valid_boxes:
        if not rows or abs(box[1] - rows[-1]["top"]) > row_tolerance:
            rows.append({"top": box[1], "cells": []})
        rows[-1]["cells"].append((original_index, box))

    cells: list[dict[str, Any]] = []
    for row_index, row in enumerate(rows):
        row["cells"].sort(key=lambda item: item[1][0])
        for column_index, (original_index, box) in enumerate(row["cells"]):
            cells.append(
                {
                    "cell_index": original_index,
                    "row_index": row_index,
                    "column_index": column_index,
                    "bbox": [round(value, 2) for value in box],
                    "texts": [],
                    "scores": [],
                }
            )
    return cells


def remove_nested_cell_boxes(cell_boxes: list[list[float]]) -> list[list[float]]:
    detected_boxes = [
        box
        for raw_box in cell_boxes
        if (box := box_from_values(raw_box)) is not None
    ]
    return [
        box
        for box_index, box in enumerate(detected_boxes)
        if not any(
            other_index != box_index
            and (box[2] - box[0]) * (box[3] - box[1])
            < (other[2] - other[0]) * (other[3] - other[1]) * 0.1
            and other[0] - 2 <= box[0]
            and other[1] - 2 <= box[1]
            and other[2] + 2 >= box[2]
            and other[3] + 2 >= box[3]
            for other_index, other in enumerate(detected_boxes)
        )
    ]


def has_material_cell_box_overlap(cell_boxes: list[list[float]]) -> bool:
    boxes = remove_nested_cell_boxes(cell_boxes)
    for index, first in enumerate(boxes):
        for second in boxes[index + 1 :]:
            overlap = intersection_area(first, second)
            first_area = (first[2] - first[0]) * (first[3] - first[1])
            second_area = (second[2] - second[0]) * (second[3] - second[1])
            if (
                overlap / max(1.0, min(first_area, second_area))
                > STRUCTURE_RULES.material_overlap_ratio
            ):
                return True
    return False


def map_ocr_to_cells(
    cell_boxes: list[list[float]],
    recognized_items: list[dict[str, Any]],
    policy: str,
    minimum_coverage: float = 0.15,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if policy not in {"center", "overlap"}:
        raise ValueError(f"Unsupported cell mapping policy: {policy}")

    cells = row_major_cells(remove_nested_cell_boxes(cell_boxes))
    unmatched: list[dict[str, Any]] = []

    for item in recognized_items:
        ocr_box = box_from_values(item.get("bbox"))
        text = str(item.get("text", "")).strip()
        if ocr_box is None or not text:
            continue

        ocr_area = (ocr_box[2] - ocr_box[0]) * (ocr_box[3] - ocr_box[1])
        center_x = (ocr_box[0] + ocr_box[2]) / 2
        center_y = (ocr_box[1] + ocr_box[3]) / 2
        candidates: list[tuple[float, float, dict[str, Any]]] = []

        for cell in cells:
            cell_box = cell["bbox"]
            overlap = intersection_area(ocr_box, cell_box)
            coverage = overlap / ocr_area
            contains_center = (
                cell_box[0] <= center_x <= cell_box[2]
                and cell_box[1] <= center_y <= cell_box[3]
            )
            if policy == "center" and not contains_center:
                continue
            if policy == "overlap" and coverage < minimum_coverage:
                continue
            candidates.append((coverage, overlap, cell))

        if not candidates and policy == "center":
            for cell in cells:
                overlap = intersection_area(ocr_box, cell["bbox"])
                coverage = overlap / ocr_area
                if coverage >= minimum_coverage:
                    candidates.append((coverage, overlap, cell))

        if not candidates:
            unmatched.append(
                {
                    "bbox": [round(value, 2) for value in ocr_box],
                    "text": text,
                    "score": item.get("score"),
                }
            )
            continue

        coverage, _, selected = max(
            candidates,
            key=lambda candidate: (
                candidate[0],
                candidate[1],
                -(
                    (candidate[2]["bbox"][2] - candidate[2]["bbox"][0])
                    * (candidate[2]["bbox"][3] - candidate[2]["bbox"][1])
                ),
            ),
        )
        selected["texts"].append(
            {
                "text": text,
                "score": item.get("score"),
                "bbox": [round(value, 2) for value in ocr_box],
                "cell_coverage": round(coverage, 4),
            }
        )

    for cell in cells:
        cell["texts"].sort(
            key=lambda entry: (entry["bbox"][1], entry["bbox"][0])
        )
        cell["scores"] = [
            entry["score"]
            for entry in cell["texts"]
            if isinstance(entry["score"], (int, float))
        ]
        cell["text"] = " ".join(entry["text"] for entry in cell["texts"])
        cell["ocr_count"] = len(cell["texts"])

    return cells, unmatched


def extract_recognized_items(table_result: dict[str, Any]) -> list[dict[str, Any]]:
    ocr_result = table_result.get("table_ocr_pred") or {}
    boxes = ocr_result.get("rec_boxes") or ocr_result.get("rec_polys") or []
    texts = ocr_result.get("rec_texts") or []
    scores = ocr_result.get("rec_scores") or []

    items = []
    for index, text in enumerate(texts):
        items.append(
            {
                "bbox": boxes[index] if index < len(boxes) else None,
                "text": text,
                "score": scores[index] if index < len(scores) else None,
            }
        )
    return items


@lru_cache(maxsize=2)
def cell_crop_recognizer(device: str) -> Any:
    from paddleocr import TextRecognition

    return TextRecognition(
        model_name=RECOGNITION_MODEL,
        device=device,
        enable_mkldnn=False,
    )


def split_ocr_items_at_cell_boundaries(
    recognized_items: list[dict[str, Any]],
    cell_boxes: list[list[float]],
    image_path: Path,
    device: str,
    recognizer: Any | None = None,
) -> list[dict[str, Any]]:
    from PIL import Image
    import numpy as np

    cells = row_major_cells(remove_nested_cell_boxes(cell_boxes))
    split_plans: dict[int, list[dict[str, Any]]] = {}
    for item_index, item in enumerate(recognized_items):
        ocr_box = box_from_values(item.get("bbox"))
        if ocr_box is None or not str(item.get("text", "")).strip():
            continue

        ocr_width = ocr_box[2] - ocr_box[0]
        ocr_height = ocr_box[3] - ocr_box[1]
        candidate_rows: dict[int, list[dict[str, Any]]] = {}
        for cell in cells:
            cell_box = cell["bbox"]
            horizontal_overlap = max(
                0.0,
                min(ocr_box[2], cell_box[2]) - max(ocr_box[0], cell_box[0]),
            )
            vertical_overlap = max(
                0.0,
                min(ocr_box[3], cell_box[3]) - max(ocr_box[1], cell_box[1]),
            )
            if (
                horizontal_overlap >= ocr_width * 0.1
                and vertical_overlap >= ocr_height * 0.5
            ):
                candidate_rows.setdefault(cell["row_index"], []).append(cell)

        crossing_cells = max(
            candidate_rows.values(),
            key=len,
            default=[],
        )
        crossing_cells.sort(key=lambda cell: cell["bbox"][0])
        if len(crossing_cells) < 2:
            continue

        crop_regions = []
        for index, cell in enumerate(crossing_cells):
            cell_box = cell["bbox"]
            left = max(ocr_box[0], cell_box[0])
            right = min(ocr_box[2], cell_box[2])
            if index:
                previous_box = crossing_cells[index - 1]["bbox"]
                left = max(left, (previous_box[2] + cell_box[0]) / 2)
            if index + 1 < len(crossing_cells):
                next_box = crossing_cells[index + 1]["bbox"]
                right = min(right, (cell_box[2] + next_box[0]) / 2)
            vertical_padding = max(1.0, ocr_height * 0.15)
            top = max(cell_box[1], ocr_box[1] - vertical_padding)
            bottom = min(cell_box[3], ocr_box[3] + vertical_padding)
            if right > left and bottom > top:
                crop_regions.append(
                    (
                        cell,
                        [
                            round(left, 2),
                            round(ocr_box[1], 2),
                            round(right, 2),
                            round(ocr_box[3], 2),
                        ],
                        (
                            max(0, round(left)),
                            max(0, round(top)),
                            round(right),
                            round(bottom),
                        ),
                    )
                )
        if len(crop_regions) >= 2:
            split_plans[item_index] = crop_regions

    if not split_plans:
        return recognized_items

    if recognizer is None:
        recognizer = cell_crop_recognizer(device)

    with Image.open(image_path) as source:
        image = np.asarray(source.convert("RGB"))
    refined_items: list[dict[str, Any]] = []
    for item_index, item in enumerate(recognized_items):
        regions = split_plans.get(item_index)
        if regions is None:
            refined_items.append(item)
            continue

        split_items = []
        for cell, bbox, crop_box in regions:
            left, top, right, bottom = crop_box
            crop = image[top:bottom, left:right]
            predictions = recognizer.predict(crop)
            if not predictions:
                split_items = []
                break
            prediction = predictions[0]
            prediction_data = (
                prediction.json
                if hasattr(prediction, "json")
                else prediction
            )
            result = prediction_data.get("res", prediction_data)
            text = str(result.get("rec_text", "")).strip()
            if not text:
                split_items = []
                break
            split_items.append(
                {
                    **item,
                    "bbox": bbox,
                    "text": text,
                    "score": result.get("rec_score"),
                    "recognition_source": "cell_crop",
                    "original_text": item.get("text", ""),
                    "refinement_status": "split_by_detected_cell_boundary",
                }
            )

        if split_items:
            refined_items.extend(split_items)
        else:
            refined_items.append(
                {
                    **item,
                    "recognition_source": "table_ocr",
                    "refinement_status": "cell_crop_recognition_empty_fallback",
                }
            )
    return refined_items


def split_ocr_items_at_inferred_column_gaps(
    recognized_items: list[dict[str, Any]],
    column_boundaries: list[float],
    image_path: Path,
    device: str,
    recognizer: Any | None = None,
) -> list[dict[str, Any]]:
    from PIL import Image
    import numpy as np

    if len(column_boundaries) < 3:
        return recognized_items

    with Image.open(image_path) as source:
        grayscale = np.asarray(source.convert("L"))

    split_plans: dict[int, list[tuple[list[float], tuple[int, int, int, int]]]] = {}
    for item_index, item in enumerate(recognized_items):
        ocr_box = box_from_values(item.get("bbox"))
        if ocr_box is None or not str(item.get("text", "")).strip():
            continue

        left, top, right, bottom = ocr_box
        x_start = max(0, round(left))
        x_end = min(grayscale.shape[1], round(right))
        y_start = max(0, round(top))
        y_end = min(grayscale.shape[0], round(bottom))
        if x_end <= x_start or y_end <= y_start:
            continue

        text_height = y_end - y_start
        minimum_segment_width = max(6.0, (right - left) * 0.15)
        minimum_gap_width = max(2, round(text_height * 0.25))
        maximum_ink_per_column = max(1, round(text_height * 0.08))
        projection = np.count_nonzero(
            grayscale[y_start:y_end, x_start:x_end] < 160,
            axis=0,
        )
        gaps: list[tuple[float, float]] = []
        gap_start: int | None = None
        for offset, ink_count in enumerate(projection):
            is_gap = ink_count <= maximum_ink_per_column
            if is_gap and gap_start is None:
                gap_start = offset
            elif not is_gap and gap_start is not None:
                if offset - gap_start >= minimum_gap_width:
                    gaps.append((x_start + gap_start, x_start + offset))
                gap_start = None
        if (
            gap_start is not None
            and len(projection) - gap_start >= minimum_gap_width
        ):
            gaps.append((x_start + gap_start, x_end))

        crossed_boundaries = [
            boundary
            for boundary in column_boundaries[1:-1]
            if left < boundary < right
        ]
        accepted_gaps: list[tuple[float, float]] = []
        for boundary in crossed_boundaries:
            candidates = [
                gap
                for gap in gaps
                if abs((gap[0] + gap[1]) / 2 - boundary)
                <= max(4.0, text_height * 1.5)
                and gap not in accepted_gaps
            ]
            if not candidates:
                continue
            nearest_gap = min(
                candidates,
                key=lambda gap: (
                    abs((gap[0] + gap[1]) / 2 - boundary)
                    - (gap[1] - gap[0])
                ),
            )
            if (
                nearest_gap[0] - left >= minimum_segment_width
                and right - nearest_gap[1] >= minimum_segment_width
            ):
                accepted_gaps.append(nearest_gap)

        if not accepted_gaps:
            continue
        accepted_gaps.sort()
        regions: list[tuple[list[float], tuple[int, int, int, int]]] = []
        region_left = left
        for gap_left, gap_right in accepted_gaps:
            if gap_left - region_left < minimum_segment_width:
                continue
            regions.append(
                (
                    [round(region_left, 2), top, round(gap_left, 2), bottom],
                    (
                        max(0, round(region_left)),
                        max(0, round(top - text_height * 0.25)),
                        round(gap_left),
                        min(grayscale.shape[0], round(bottom + text_height * 0.25)),
                    ),
                )
            )
            region_left = gap_right
        if right - region_left >= minimum_segment_width:
            regions.append(
                (
                    [round(region_left, 2), top, right, bottom],
                    (
                        max(0, round(region_left)),
                        max(0, round(top - text_height * 0.25)),
                        min(grayscale.shape[1], round(right)),
                        min(grayscale.shape[0], round(bottom + text_height * 0.25)),
                    ),
                )
            )
        if len(regions) >= 2:
            split_plans[item_index] = regions

    if not split_plans:
        return recognized_items

    if recognizer is None:
        recognizer = cell_crop_recognizer(device)

    with Image.open(image_path) as source:
        image = np.asarray(source.convert("RGB"))
    refined_items: list[dict[str, Any]] = []
    for item_index, item in enumerate(recognized_items):
        regions = split_plans.get(item_index)
        if regions is None:
            refined_items.append(item)
            continue

        split_items = []
        for bbox, crop_box in regions:
            crop_left, crop_top, crop_right, crop_bottom = crop_box
            prediction = recognizer.predict(
                image[crop_top:crop_bottom, crop_left:crop_right]
            )
            if not prediction:
                split_items = []
                break
            prediction_data = (
                prediction[0].json
                if hasattr(prediction[0], "json")
                else prediction[0]
            )
            result = prediction_data.get("res", prediction_data)
            text = str(result.get("rec_text", "")).strip()
            if not text:
                split_items = []
                break
            split_items.append(
                {
                    **item,
                    "bbox": bbox,
                    "text": text,
                    "score": result.get("rec_score"),
                    "recognition_source": "inferred_column_crop",
                    "original_text": item.get("text", ""),
                    "refinement_status": "split_at_visual_column_gap",
                }
            )

        original_compact = "".join(str(item.get("text", "")).split()).casefold()
        refined_compact = "".join(
            "".join(entry["text"].split()) for entry in split_items
        ).casefold()
        if (
            len(split_items) >= 2
            and difflib.SequenceMatcher(
                None,
                original_compact,
                refined_compact,
            ).ratio()
            >= 0.6
        ):
            refined_items.extend(split_items)
        else:
            refined_items.append(item)
    return refined_items


def _cluster_values(values: list[float], tolerance: float) -> list[list[float]]:
    groups: list[list[float]] = []
    for value in sorted(values):
        if not groups or value - statistics.mean(groups[-1]) > tolerance:
            groups.append([value])
        else:
            groups[-1].append(value)
    return groups


def exclude_ocr_outside_cell_bounds(
    cell_boxes: list[list[float]],
    recognized_items: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    boxes = [
        box
        for raw_box in cell_boxes
        if (box := box_from_values(raw_box)) is not None
    ]
    if len(boxes) < 3 or not recognized_items:
        return recognized_items, []

    left = min(box[0] for box in boxes)
    top = min(box[1] for box in boxes)
    right = max(box[2] for box in boxes)
    bottom = max(box[3] for box in boxes)
    text_heights = [
        box[3] - box[1]
        for item in recognized_items
        if (box := box_from_values(item.get("bbox"))) is not None
    ]
    tolerance = max(
        2.0,
        statistics.median(text_heights) * 0.1 if text_heights else 2.0,
    )

    retained = []
    excluded = []
    for item in recognized_items:
        box = box_from_values(item.get("bbox"))
        if box is None:
            retained.append(item)
            continue
        center_x = (box[0] + box[2]) / 2
        center_y = (box[1] + box[3]) / 2
        if (
            left - tolerance <= center_x <= right + tolerance
            and top - tolerance <= center_y <= bottom + tolerance
        ):
            retained.append(item)
        else:
            excluded.append(item)
    return retained, excluded


def remove_low_confidence_ocr_artifacts(
    recognized_items: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    item_heights = [
        box[3] - box[1]
        for item in recognized_items
        if (box := box_from_values(item.get("bbox"))) is not None
        and (
            not isinstance(item.get("score"), (int, float))
            or item["score"] >= 0.5
        )
    ]
    if not item_heights:
        item_heights = [
            box[3] - box[1]
            for item in recognized_items
            if (box := box_from_values(item.get("bbox"))) is not None
        ]
    typical_height = statistics.median(item_heights) if item_heights else 0.0
    retained = []
    excluded = []
    for item in recognized_items:
        box = box_from_values(item.get("bbox"))
        text = str(item.get("text", "")).strip()
        score = item.get("score")
        if (
            box is not None
            and len(text) == 1
            and text.isascii()
            and text.isalpha()
            and isinstance(score, (int, float))
            and score < 0.2
            and box[3] - box[1] <= typical_height * 0.6
            and any(
                other is not item
                and (other_box := box_from_values(other.get("bbox"))) is not None
                and is_nested_ocr_fragment(
                    {"bbox": box},
                    {"bbox": other_box},
                )
                and intersection_area(box, other_box) > 0
                for other in recognized_items
            )
        ):
            excluded.append(
                {
                    **item,
                    "mapping_warning": (
                        "Ignored a tiny, low-confidence OCR fragment "
                        "overlapping another text detection"
                    ),
                }
            )
        else:
            retained.append(item)
    return retained, excluded


def add_excluded_ocr_warnings(
    cells: list[dict[str, Any]],
    excluded_items: list[dict[str, Any]],
) -> None:
    if not cells or not excluded_items:
        return
    warnings = []
    for item in excluded_items:
        reason = item.get("mapping_warning")
        if reason is None:
            reason = "Ignored OCR detection outside detected table bounds"
        warnings.append(
            f"{reason}: {item['text']!r} at "
            f"{[round(value, 2) for value in item['bbox']]}"
        )
    cells[0].setdefault("mapping_warnings", []).extend(warnings)


def infer_column_boundaries(
    cell_boxes: list[list[float]],
    recognized_items: list[dict[str, Any]],
    image_width: int,
    image_path: Path | None = None,
) -> list[float]:
    vertical_boundaries = (
        detect_vertical_rules(image_path, cell_boxes)
        if image_path is not None
        else None
    )

    edge_values = []
    for raw_box in cell_boxes:
        box = box_from_values(raw_box)
        if (
            box is not None
            and box[2] - box[0]
            < image_width * STRUCTURE_RULES.short_cell_image_width_ratio
        ):
            edge_values.extend((box[0], box[2]))

    column_tolerance = max(
        STRUCTURE_RULES.minimum_pixel_tolerance,
        image_width * STRUCTURE_RULES.ocr_column_cluster_image_ratio,
    )
    edge_groups = _cluster_values(edge_values, tolerance=column_tolerance)
    repeated_edges = [
        statistics.mean(group) for group in edge_groups if len(group) >= 2
    ]
    edge_boundaries = repeated_edges if len(repeated_edges) >= 3 else None

    valid_items = [
        (box, item)
        for item in recognized_items
        if (box := box_from_values(item.get("bbox"))) is not None
        and str(item.get("text", "")).strip()
    ]
    if not valid_items:
        if (
            vertical_boundaries is not None
            and edge_boundaries is not None
            and not has_material_cell_box_overlap(cell_boxes)
        ):
            return edge_boundaries
        if vertical_boundaries is not None:
            return vertical_boundaries
        if edge_boundaries is not None:
            return edge_boundaries
        raise ValueError("Cannot infer table columns: no valid OCR boxes.")

    median_text_height = statistics.median(
        box[3] - box[1] for box, _ in valid_items
    )
    row_groups: list[list[tuple[list[float], dict[str, Any]]]] = []
    for entry in sorted(valid_items, key=lambda item: (item[0][1] + item[0][3]) / 2):
        center_y = (entry[0][1] + entry[0][3]) / 2
        if (
            not row_groups
            or abs(
                center_y
                - statistics.mean(
                    (box[1] + box[3]) / 2 for box, _ in row_groups[-1]
                )
            )
            > max(
                STRUCTURE_RULES.minimum_pixel_tolerance,
                median_text_height * STRUCTURE_RULES.row_cluster_text_height_ratio,
            )
        ):
            row_groups.append([])
        row_groups[-1].append(entry)

    anchor_row = max(row_groups, key=len)
    centers = sorted((box[0] + box[2]) / 2 for box, _ in anchor_row)
    center_groups = _cluster_values(centers, tolerance=column_tolerance)
    column_centers = [statistics.mean(group) for group in center_groups]
    if len(column_centers) < 2:
        if vertical_boundaries is not None:
            return vertical_boundaries
        if edge_boundaries is not None:
            return edge_boundaries
        raise ValueError("Cannot infer table columns from OCR positions.")

    boundaries = [
        column_centers[0] - (column_centers[1] - column_centers[0]) / 2
    ]
    boundaries.extend(
        (left + right) / 2
        for left, right in zip(column_centers, column_centers[1:])
    )
    boundaries.append(
        column_centers[-1]
        + (column_centers[-1] - column_centers[-2]) / 2
    )
    column_boxes: list[list[list[float]]] = [
        [] for _ in column_centers
    ]
    for box, _ in valid_items:
        center = (box[0] + box[2]) / 2
        column_index = min(
            range(len(column_centers)),
            key=lambda index: abs(center - column_centers[index]),
        )
        column_boxes[column_index].append(box)
    reliable_column_boxes = []
    for boxes_in_column in column_boxes:
        if not boxes_in_column:
            reliable_column_boxes.append([])
            continue
        typical_text_width = statistics.median(
            box[2] - box[0] for box in boxes_in_column
        )
        reliable_column_boxes.append(
            [
                box
                for box in boxes_in_column
                if box[2] - box[0] <= max(typical_text_width * 2.2, typical_text_width + 12)
            ]
        )
    if len(column_centers) >= 3 and all(reliable_column_boxes):
        profile_gaps = [
            (
                max(box[2] for box in reliable_column_boxes[column_index]),
                min(box[0] for box in reliable_column_boxes[column_index + 1]),
            )
            for column_index in range(len(column_centers) - 1)
        ]
        table_boxes = [
            box
            for raw_box in cell_boxes
            if (box := box_from_values(raw_box)) is not None
        ] + [box for box, _ in valid_items]
        gap_boundaries = [
            min(box[0] for box in table_boxes),
            *((left + right) / 2 for left, right in profile_gaps),
            max(box[2] for box in table_boxes),
        ]
        if (
            all(left < right for left, right in profile_gaps)
            and len(gap_boundaries) == len(column_centers) + 1
            and all(
                left < right
                for left, right in zip(gap_boundaries, gap_boundaries[1:])
            )
        ):
            boundaries = gap_boundaries
    left_edge_groups = _cluster_values(
        [box[0] for box, _ in valid_items],
        tolerance=column_tolerance,
    )
    aligned_left_columns = []
    for group in left_edge_groups:
        anchor = statistics.mean(group)
        aligned_rows = [
            row
            for row in row_groups
            if any(
                abs(box[0] - anchor) <= column_tolerance
                for box, _ in row
            )
        ]
        if len(aligned_rows) >= 2:
            aligned_left_columns.append(
                (
                    anchor,
                    [
                        (box, item)
                        for box, item in valid_items
                        if abs(box[0] - anchor)
                        <= column_tolerance
                    ],
                )
            )

    aligned_left_boundaries = None
    if len(aligned_left_columns) >= 2:
        table_boxes = [
            box
            for raw_box in cell_boxes
            if (box := box_from_values(raw_box)) is not None
        ] + [box for box, _ in valid_items]
        candidate = [
            min(box[0] for box in table_boxes),
        ]
        for (anchor, entries), (next_anchor, _) in zip(
            aligned_left_columns,
            aligned_left_columns[1:],
        ):
            last_right = max(box[2] for box, _ in entries)
            candidate.append(
                (last_right + next_anchor) / 2
                if last_right < next_anchor
                else (anchor + next_anchor) / 2
            )
        candidate.append(max(box[2] for box in table_boxes))
        if all(left < right for left, right in zip(candidate, candidate[1:])):
            aligned_left_boundaries = candidate

    def crossing_count(candidate: list[float]) -> int:
        return sum(
            any(box[0] < boundary < box[2] for boundary in candidate[1:-1])
            for box, _ in valid_items
        )

    if (
        vertical_boundaries is not None
        and edge_boundaries is not None
        and not has_material_cell_box_overlap(cell_boxes)
    ):
        return edge_boundaries
    if vertical_boundaries is not None:
        vertical_column_count = len(vertical_boundaries) - 1
        repeated_ocr_columns = sum(
            sum(
                any(
                    abs((box[0] + box[2]) / 2 - center)
                    <= column_tolerance
                    for box, _ in row
                )
                for row in row_groups
            )
            >= 2
            for center in column_centers
        )
        if (
            len(column_centers) == vertical_column_count + 1
            and not has_material_cell_box_overlap(cell_boxes)
            and repeated_ocr_columns / len(column_centers) >= 0.8
        ):
            return boundaries
        return vertical_boundaries
    if aligned_left_boundaries is not None:
        left_column_count = len(aligned_left_boundaries) - 1
        if edge_boundaries is not None:
            edge_column_count = len(edge_boundaries) - 1
            if left_column_count > edge_column_count and (
                crossing_count(aligned_left_boundaries)
                <= crossing_count(edge_boundaries)
            ):
                return aligned_left_boundaries
            return edge_boundaries
        if (
            left_column_count == len(boundaries) - 1
            and crossing_count(aligned_left_boundaries) < crossing_count(boundaries)
        ):
            return aligned_left_boundaries
    if edge_boundaries is not None:
        edge_column_count = len(edge_boundaries) - 1
        repeated_ocr_columns = sum(
            sum(
                any(
                    abs((box[0] + box[2]) / 2 - center)
                    <= column_tolerance
                    for box, _ in row
                )
                for row in row_groups
            )
            >= 2
            for center in column_centers
        )
        if (
            len(column_centers) > edge_column_count * 1.25
            and repeated_ocr_columns / len(column_centers) >= 0.8
        ):
            return boundaries
        return edge_boundaries
    return boundaries


def detect_vertical_rules(
    image_path: Path,
    cell_boxes: list[list[float]],
) -> list[float] | None:
    from PIL import Image

    valid_boxes = [
        box
        for raw_box in cell_boxes
        if (box := box_from_values(raw_box)) is not None
    ]
    if not valid_boxes:
        return None

    with Image.open(image_path) as source:
        grayscale = source.convert("L")
        pixels = grayscale.load()
        line_columns = []
        top = max(0, round(grayscale.height * 0.02))
        bottom = min(grayscale.height, round(grayscale.height * 0.98))
        for x in range(grayscale.width):
            coverage = sum(
                pixels[x, y] < STRUCTURE_RULES.vertical_rule_dark_threshold
                for y in range(top, bottom)
            ) / max(1, bottom - top)
            if coverage >= STRUCTURE_RULES.vertical_rule_minimum_coverage:
                line_columns.append(x)

    line_groups = _cluster_values(
        [float(x) for x in line_columns],
        tolerance=2.0,
    )
    separators = [statistics.mean(group) for group in line_groups]
    left = min(box[0] for box in valid_boxes)
    right = max(box[2] for box in valid_boxes)
    has_reliable_outer_bounds = False
    edge_groups = _cluster_values(
        [edge for box in valid_boxes for edge in (box[0], box[2])],
        tolerance=max(
            STRUCTURE_RULES.minimum_pixel_tolerance,
            grayscale.width * STRUCTURE_RULES.edge_cluster_image_ratio,
        ),
    )
    repeated_edges = [
        statistics.mean(group) for group in edge_groups if len(group) >= 2
    ]
    if len(repeated_edges) >= 2:
        repeated_left = min(repeated_edges)
        repeated_right = max(repeated_edges)
        if (
            repeated_left - left <= grayscale.width * 0.05
            and right - repeated_right <= grayscale.width * 0.05
        ):
            left, right = repeated_left, repeated_right
            has_reliable_outer_bounds = True
    separators = [
        x for x in separators
        if left + 3 < x < right - 3
    ]
    if len(separators) < (1 if has_reliable_outer_bounds else 2):
        return None
    return [left, *separators, right]


def detect_horizontal_rules(
    image_path: Path,
    x_boundaries: list[float],
    row_centers: list[float],
) -> list[list[bool]]:
    from PIL import Image

    with Image.open(image_path) as source:
        grayscale = source.convert("L")
        pixels = grayscale.load()
        rules: list[list[bool]] = []

        for upper_center, lower_center in zip(row_centers, row_centers[1:]):
            gap = lower_center - upper_center
            midpoint = (upper_center + lower_center) / 2
            radius = max(2, round(gap * 0.45))
            top = max(0, round(midpoint - radius))
            bottom = min(grayscale.height - 1, round(midpoint + radius))
            left = max(0, round(x_boundaries[0]))
            right = min(grayscale.width, round(x_boundaries[-1]))
            if right <= left:
                rules.append([False] * (len(x_boundaries) - 1))
                continue

            peak_y = max(
                range(top, bottom + 1),
                key=lambda y: sum(
                    pixels[x, y] < STRUCTURE_RULES.table_rule_dark_threshold
                    for x in range(left, right)
                ),
            )
            boundary_rules = []
            for column_left, column_right in zip(
                x_boundaries, x_boundaries[1:]
            ):
                x0 = max(0, round(column_left) + 2)
                x1 = min(grayscale.width, round(column_right) - 2)
                width = max(1, x1 - x0)
                coverage = max(
                    sum(
                        pixels[x, y] < STRUCTURE_RULES.table_rule_dark_threshold
                        for x in range(x0, x1)
                    )
                    / width
                    for y in range(
                        max(0, peak_y - 1),
                        min(grayscale.height - 1, peak_y + 1) + 1,
                    )
                )
                boundary_rules.append(
                    coverage >= STRUCTURE_RULES.horizontal_rule_minimum_coverage
                )
            rules.append(boundary_rules)
    return rules


def has_vertical_rule(
    image_path: Path,
    x: float,
    top: float,
    bottom: float,
) -> bool:
    from PIL import Image

    with Image.open(image_path) as source:
        grayscale = source.convert("L")
        pixels = grayscale.load()
        x_center = round(x)
        y_start = max(0, round(top))
        y_end = min(grayscale.height, round(bottom))
        if y_end <= y_start:
            return False
        x_positions = range(
            max(0, x_center - 2),
            min(grayscale.width, x_center + 3),
        )
        dark_rows = sum(
            any(
                pixels[x_position, y]
                < STRUCTURE_RULES.table_rule_dark_threshold
                for x_position in x_positions
            )
            for y in range(y_start, y_end)
        )
        return (
            dark_rows / (y_end - y_start)
            >= STRUCTURE_RULES.horizontal_rule_minimum_coverage
        )


def remove_detector_cell_box_artifacts(
    cell_boxes: list[list[float]],
    recognized_items: list[dict[str, Any]],
    image_size: tuple[int, int],
) -> list[list[float]]:
    image_width, image_height = image_size
    valid_boxes = [
        box
        for raw_box in cell_boxes
        if (box := box_from_values(raw_box)) is not None
    ]
    regular_boxes = [
        box
        for box in valid_boxes
        if box[2] - box[0]
        < image_width * STRUCTURE_RULES.short_cell_image_width_ratio
    ]
    if len(regular_boxes) < 2:
        return valid_boxes

    typical_width = statistics.median(
        box[2] - box[0] for box in regular_boxes
    )
    typical_height = statistics.median(
        box[3] - box[1] for box in regular_boxes
    )
    typical_area = statistics.median(
        (box[2] - box[0]) * (box[3] - box[1])
        for box in regular_boxes
    )
    structural_boxes = [
        box
        for box in regular_boxes
        if not (
            box[2] - box[0]
            <= typical_width * STRUCTURE_RULES.tiny_box_width_ratio
            and box[3] - box[1]
            <= typical_height * STRUCTURE_RULES.tiny_box_height_ratio
            and (box[2] - box[0]) * (box[3] - box[1])
            <= typical_area * STRUCTURE_RULES.tiny_box_area_ratio
        )
    ]
    if len(structural_boxes) < 2:
        structural_boxes = regular_boxes
    table_left = min(box[0] for box in structural_boxes)
    table_right = max(box[2] for box in structural_boxes)
    table_bottom = max(box[3] for box in structural_boxes)
    tolerance = max(
        STRUCTURE_RULES.minimum_pixel_tolerance,
        image_height * STRUCTURE_RULES.edge_cluster_image_ratio,
    )
    text_centers = [
        (
            (box[0] + box[2]) / 2,
            (box[1] + box[3]) / 2,
        )
        for item in recognized_items
        if (box := box_from_values(item.get("bbox"))) is not None
        and str(item.get("text", "")).strip()
    ]

    def has_grid_neighbor(box: list[float]) -> bool:
        box_width = box[2] - box[0]
        box_height = box[3] - box[1]
        for other in regular_boxes:
            if other is box:
                continue
            overlap_width = max(0.0, min(box[2], other[2]) - max(box[0], other[0]))
            overlap_height = max(0.0, min(box[3], other[3]) - max(box[1], other[1]))
            overlap_area = overlap_width * overlap_height
            if (
                overlap_area
                >= box_width
                * box_height
                * STRUCTURE_RULES.grid_neighbor_overlap_ratio
            ):
                return True
            if (
                min(box_height, other[3] - other[1])
                * STRUCTURE_RULES.same_axis_overlap_ratio
                <= overlap_height
                and min(abs(box[2] - other[0]), abs(box[0] - other[2])) <= tolerance
            ):
                return True
            if (
                min(box_width, other[2] - other[0])
                * STRUCTURE_RULES.same_axis_overlap_ratio
                <= overlap_width
                and min(abs(box[3] - other[1]), abs(box[1] - other[3])) <= tolerance
            ):
                return True
        return False

    retained_boxes = []
    for box_index, box in enumerate(valid_boxes):
        box_width = box[2] - box[0]
        box_height = box[3] - box[1]
        spans_table_width = (
            box[0]
            <= table_left
            + (table_right - table_left)
            * (1 - STRUCTURE_RULES.full_width_cell_ratio)
            and box[2]
            >= table_right
            - (table_right - table_left)
            * (1 - STRUCTURE_RULES.full_width_cell_ratio)
        )
        contains_text = any(
            box[0] <= center_x <= box[2]
            and box[1] <= center_y <= box[3]
            for center_x, center_y in text_centers
        )
        is_isolated_tiny_artifact = (
            box_width
            <= typical_width * STRUCTURE_RULES.tiny_box_width_ratio
            and box_height
            <= typical_height * STRUCTURE_RULES.tiny_box_height_ratio
            and box_width * box_height
            <= typical_area * STRUCTURE_RULES.tiny_box_area_ratio
            and not contains_text
            and not has_grid_neighbor(box)
        )
        overlapping_row_peers = [
            other
            for other_index, other in enumerate(valid_boxes)
            if other_index != box_index
            and other[2] - other[0]
            < (table_right - table_left)
            * STRUCTURE_RULES.short_cell_image_width_ratio
            and min(other[3], box[3]) - max(other[1], box[1])
            >= min(other[3] - other[1], box_height)
            * STRUCTURE_RULES.same_axis_overlap_ratio
        ]
        is_overlapping_full_width_artifact = (
            spans_table_width
            and box_height
            <= typical_height * STRUCTURE_RULES.full_width_artifact_height_ratio
            and not contains_text
            and len(overlapping_row_peers) >= 2
        )
        is_trailing_detector_artifact = (
            spans_table_width
            and box_height
            <= typical_height * STRUCTURE_RULES.tiny_box_height_ratio
            and box[1] >= table_bottom - tolerance
            and box[3] >= image_height - tolerance
            and not contains_text
        )
        previous_boxes = [
            other
            for other_index, other in enumerate(valid_boxes)
            if other_index != box_index and other[3] <= box[1] + tolerance
        ]
        trailing_row_peers = [
            other
            for other_index, other in enumerate(valid_boxes)
            if other_index != box_index
            and abs(other[1] - box[1]) <= tolerance
            and min(other[3], box[3]) > max(other[1], box[1])
        ]
        previous_bottom = max(
            (other[3] for other in previous_boxes),
            default=float("-inf"),
        )
        is_isolated_trailing_artifact = (
            box_width
            < (table_right - table_left)
            * STRUCTURE_RULES.trailing_artifact_width_ratio
            and box_height
            <= typical_height * STRUCTURE_RULES.trailing_artifact_height_ratio
            and box[3] >= image_height - tolerance
            and not contains_text
            and not trailing_row_peers
            and abs(box[1] - previous_bottom) <= tolerance
        )
        if not (
            is_isolated_tiny_artifact
            or is_overlapping_full_width_artifact
            or is_trailing_detector_artifact
            or is_isolated_trailing_artifact
        ):
            retained_boxes.append(box)
    return retained_boxes


def cell_box_y_boundaries(
    cell_boxes: list[list[float]],
    image_size: tuple[int, int],
    image_path: Path | None,
) -> list[float]:
    if not cell_boxes:
        return []
    _, image_height = image_size
    cell_heights = [box[3] - box[1] for box in cell_boxes]
    typical_height = statistics.median(cell_heights)
    tolerance = max(
        STRUCTURE_RULES.minimum_pixel_tolerance,
        image_height * STRUCTURE_RULES.edge_cluster_image_ratio,
        typical_height * STRUCTURE_RULES.row_boundary_cell_height_ratio,
    )
    groups = _cluster_values(
        [edge for box in cell_boxes for edge in (box[1], box[3])],
        tolerance=tolerance,
    )
    boundaries = [statistics.mean(group) for group in groups]
    if image_path is None or len(boundaries) < 3:
        return boundaries

    from PIL import Image

    with Image.open(image_path) as source:
        grayscale = source.convert("L")
        pixels = grayscale.load()
        retained_boundaries = [boundaries[0]]
        for boundary in boundaries[1:-1]:
            ending_boxes = [
                box for box in cell_boxes if abs(box[3] - boundary) <= tolerance
            ]
            starting_boxes = [
                box for box in cell_boxes if abs(box[1] - boundary) <= tolerance
            ]
            crossing_boxes = [
                box
                for box in cell_boxes
                if box[1] + tolerance < boundary < box[3] - tolerance
            ]
            has_drawn_rule = False
            if ending_boxes and not starting_boxes and crossing_boxes:
                for box in ending_boxes:
                    left = max(0, round(box[0]) + 2)
                    right = min(grayscale.width, round(box[2]) - 2)
                    if right <= left:
                        continue
                    width = right - left
                    coverage = max(
                        sum(
                            pixels[x, y]
                            < STRUCTURE_RULES.table_rule_dark_threshold
                            for x in range(left, right)
                        )
                        / width
                        for y in range(
                            max(0, round(boundary) - 1),
                            min(grayscale.height - 1, round(boundary) + 1) + 1,
                        )
                    )
                    if (
                        coverage
                        >= STRUCTURE_RULES.horizontal_rule_minimum_coverage
                    ):
                        has_drawn_rule = True
                        break
            if (
                ending_boxes
                and not starting_boxes
                and crossing_boxes
                and not has_drawn_rule
            ):
                continue
            retained_boundaries.append(boundary)
        retained_boundaries.append(boundaries[-1])
    return retained_boundaries


def reconstruct_table_from_cell_boxes(
    cell_boxes: list[list[float]],
    recognized_items: list[dict[str, Any]],
    image_size: tuple[int, int],
    image_path: Path | None = None,
) -> tuple[list[dict[str, Any]], str]:
    image_width, image_height = image_size
    valid_boxes = remove_detector_cell_box_artifacts(
        remove_nested_cell_boxes(cell_boxes),
        recognized_items,
        image_size,
    )

    x_edge_values = [
        edge
        for box in valid_boxes
        if box[2] - box[0]
        < image_width * STRUCTURE_RULES.short_cell_image_width_ratio
        for edge in (box[0], box[2])
    ]
    x_groups = _cluster_values(
        x_edge_values,
        tolerance=max(
            STRUCTURE_RULES.minimum_pixel_tolerance,
            image_width * STRUCTURE_RULES.edge_cluster_image_ratio,
        ),
    )
    x_boundaries = [statistics.mean(group) for group in x_groups]
    y_boundaries = cell_box_y_boundaries(
        valid_boxes,
        image_size,
        image_path,
    )
    if len(x_boundaries) < 2 or len(y_boundaries) < 2:
        raise ValueError("Cannot build a table grid from detected cell boxes.")

    column_count = len(x_boundaries) - 1
    row_count = len(y_boundaries) - 1
    occupied: dict[tuple[int, int], dict[str, Any]] = {}
    cells: list[dict[str, Any]] = []

    def boundary_index(value: float, boundaries: list[float]) -> int:
        return min(
            range(len(boundaries)),
            key=lambda index: abs(boundaries[index] - value),
        )

    for box_index, box in enumerate(valid_boxes):
        if box[0] <= x_boundaries[0]:
            column_start = 0
        else:
            column_start = boundary_index(box[0], x_boundaries)
        if box[2] >= x_boundaries[-1]:
            column_end = column_count
        else:
            column_end = boundary_index(box[2], x_boundaries)
        row_start = boundary_index(box[1], y_boundaries)
        row_end = boundary_index(box[3], y_boundaries)
        if (
            column_end <= column_start
            or row_end <= row_start
            or row_end > row_count
        ):
            raise ValueError(
                f"Invalid detected cell box #{box_index}: {box!r}"
            )
        cell = {
            "cell_index": box_index,
            "row_index": row_start,
            "column_index": column_start,
            "rowspan": row_end - row_start,
            "colspan": column_end - column_start,
            "bbox": [round(value, 2) for value in box],
            "bbox_grid": [
                round(x_boundaries[column_start], 2),
                round(y_boundaries[row_start], 2),
                round(x_boundaries[column_end], 2),
                round(y_boundaries[row_end], 2),
            ],
            "texts": [],
            "scores": [],
            "text": "",
            "ocr_count": 0,
        }
        for row_offset in range(cell["rowspan"]):
            for column_offset in range(cell["colspan"]):
                position = (row_start + row_offset, column_start + column_offset)
                if position in occupied:
                    raise ValueError(
                        "Overlapping detected table cells at grid position "
                        f"{position}: {box!r}"
                    )
                occupied[position] = cell
        cells.append(cell)

    for item in recognized_items:
        box = box_from_values(item.get("bbox"))
        text = str(item.get("text", "")).strip()
        if box is None or not text:
            continue
        center_x = (box[0] + box[2]) / 2
        center_y = (box[1] + box[3]) / 2
        matching_cells = [
            cell
            for cell in cells
            if cell["bbox_grid"][0] <= center_x <= cell["bbox_grid"][2]
            and cell["bbox_grid"][1] <= center_y <= cell["bbox_grid"][3]
        ]
        if not matching_cells:
            raise ValueError(
                f"OCR text is outside detected table cells: {text!r} at {box!r}"
            )
        target_cell = min(
            matching_cells,
            key=lambda cell: (
                (cell["bbox_grid"][2] - cell["bbox_grid"][0])
                * (cell["bbox_grid"][3] - cell["bbox_grid"][1])
            ),
        )
        target_cell["texts"].append(
            {
                "text": text,
                "score": item.get("score"),
                "bbox": [round(value, 2) for value in box],
                "recognition_source": item.get(
                    "recognition_source",
                    "table_ocr",
                ),
                "original_text": item.get("original_text"),
                "refinement_status": item.get("refinement_status"),
            }
        )

    for cell in cells:
        cell["texts"].sort(
            key=lambda entry: (entry["bbox"][1], entry["bbox"][0])
        )
        cell["text"] = "\n".join(entry["text"] for entry in cell["texts"])
        cell["scores"] = [
            entry["score"]
            for entry in cell["texts"]
            if isinstance(entry["score"], (int, float))
        ]
        cell["ocr_count"] = len(cell["texts"])

    row_slices: list[list[tuple[float, float]]] = []
    for row_index in range(row_count):
        spanning_cells = [
            cell
            for cell in cells
            if cell["row_index"] <= row_index
            < cell["row_index"] + cell["rowspan"]
        ]
        explicitly_partitioned_columns = {
            cell["column_index"]
            for cell in spanning_cells
            if cell["colspan"] == 1
        }
        has_vertical_merge = any(
            cell["rowspan"] > 1 for cell in spanning_cells
        )
        if has_vertical_merge or (
            row_count >= 4
            and len(explicitly_partitioned_columns) >= max(
                2, column_count * 0.7
            )
        ):
            row_slices.append(
                [(y_boundaries[row_index], y_boundaries[row_index + 1])]
            )
            continue

        should_split = any(
            len(cell["texts"]) > 1
            and cell["bbox_grid"][3] - cell["bbox_grid"][1]
            > statistics.median(
                item["bbox"][3] - item["bbox"][1]
                for item in cell["texts"]
            )
            for cell in spanning_cells
        )
        if not should_split:
            row_slices.append(
                [(y_boundaries[row_index], y_boundaries[row_index + 1])]
            )
            continue

        line_entries = sorted(
            (
                (
                    (item["bbox"][1] + item["bbox"][3]) / 2,
                    cell,
                    item,
                )
                for cell in spanning_cells
                for item in cell["texts"]
                if y_boundaries[row_index]
                <= (item["bbox"][1] + item["bbox"][3]) / 2
                <= y_boundaries[row_index + 1]
            ),
            key=lambda entry: entry[0],
        )
        center_groups: list[
            list[tuple[float, dict[str, Any], dict[str, Any]]]
        ] = []
        for entry in line_entries:
            if (
                not center_groups
                or entry[0] - statistics.mean(
                    center for center, _, _ in center_groups[-1]
                )
                > max(6.0, image_height * 0.012)
            ):
                center_groups.append([])
            center_groups[-1].append(entry)

        aligned_groups = [
            group
            for group in center_groups
            if len({id(cell) for _, cell, _ in group}) > 1
        ]
        aligned_centers = [
            statistics.mean(center for center, _, _ in group)
            for group in aligned_groups
        ]
        continuation_group_indexes: set[int] = set()
        for index, (first_center, next_center) in enumerate(
            zip(aligned_centers, aligned_centers[1:])
        ):
            shared_cells = {
                id(cell) for _, cell, _ in aligned_groups[index]
            } & {
                id(cell) for _, cell, _ in aligned_groups[index + 1]
            }
            intervening_indexes = [
                group_index
                for group_index, group in enumerate(center_groups)
                if first_center
                < statistics.mean(center for center, _, _ in group)
                < next_center
            ]
            intervening_cells = {
                id(cell)
                for group_index in intervening_indexes
                for _, cell, _ in center_groups[group_index]
            }
            intervening_text_is_contained = all(
                item["bbox"][0]
                >= cell["bbox_grid"][0] - max(2.0, image_width * 0.005)
                and item["bbox"][2]
                <= cell["bbox_grid"][2] + max(2.0, image_width * 0.005)
                for group_index in intervening_indexes
                for _, cell, item in center_groups[group_index]
            )
            if (
                shared_cells
                and intervening_indexes
                and len(intervening_cells) == 1
                and intervening_cells <= shared_cells
                and intervening_text_is_contained
            ):
                continuation_group_indexes.update(intervening_indexes)
        retained_centers = [
            statistics.mean(center for center, _, _ in group)
            for group_index, group in enumerate(center_groups)
            if group_index not in continuation_group_indexes
        ]
        if len(aligned_groups) >= 2 and continuation_group_indexes:
            slice_boundaries = [y_boundaries[row_index]]
            for first_center, next_center in zip(
                retained_centers,
                retained_centers[1:],
            ):
                continuation_centers = [
                    statistics.mean(
                        center
                        for center, _, _ in center_groups[group_index]
                    )
                    for group_index in continuation_group_indexes
                    if first_center
                    < statistics.mean(
                        center
                        for center, _, _ in center_groups[group_index]
                    )
                    < next_center
                ]
                last_continuation = max(continuation_centers, default=first_center)
                slice_boundaries.append(
                    (last_continuation + next_center) / 2
                )
            slice_boundaries.append(y_boundaries[row_index + 1])
            row_slices.append(
                list(zip(slice_boundaries, slice_boundaries[1:]))
            )
            continue

        centers = [
            statistics.mean(center for center, _, _ in group)
            for group in center_groups
        ]
        if len(centers) < 2:
            row_slices.append(
                [(y_boundaries[row_index], y_boundaries[row_index + 1])]
            )
            continue

        slice_boundaries = [y_boundaries[row_index]]
        slice_boundaries.extend(
            (first + second) / 2
            for first, second in zip(centers, centers[1:])
        )
        slice_boundaries.append(y_boundaries[row_index + 1])
        row_slices.append(
            list(zip(slice_boundaries, slice_boundaries[1:]))
        )

    expanded_cells: list[dict[str, Any]] = []
    row_offsets = [0]
    for slices in row_slices:
        row_offsets.append(row_offsets[-1] + len(slices))

    for cell in cells:
        original_start = cell["row_index"]
        original_end = original_start + cell["rowspan"]
        expanded_start = row_offsets[original_start]
        expanded_end = row_offsets[original_end]
        segment_count = expanded_end - expanded_start
        if segment_count <= 1:
            expanded_cells.append(cell)
            continue

        if len(cell["texts"]) <= 1 or all(
            len(row_slices[row_index]) == 1
            for row_index in range(original_start, original_end)
        ):
            spanning_cell = {**cell}
            spanning_cell["row_index"] = expanded_start
            spanning_cell["rowspan"] = segment_count
            spanning_cell["bbox_grid"] = [
                cell["bbox_grid"][0],
                row_slices[original_start][0][0],
                cell["bbox_grid"][2],
                row_slices[original_end - 1][-1][1],
            ]
            spanning_cell["bbox"] = spanning_cell["bbox_grid"]
            expanded_cells.append(spanning_cell)
            continue

        for original_row in range(original_start, original_end):
            for slice_index, (slice_top, slice_bottom) in enumerate(
                row_slices[original_row]
            ):
                expanded_row = row_offsets[original_row] + slice_index
                slice_cell = {
                    **cell,
                    "row_index": expanded_row,
                    "rowspan": 1,
                    "bbox_grid": [
                        cell["bbox_grid"][0],
                        slice_top,
                        cell["bbox_grid"][2],
                        slice_bottom,
                    ],
                    "bbox": [
                        cell["bbox_grid"][0],
                        slice_top,
                        cell["bbox_grid"][2],
                        slice_bottom,
                    ],
                }
                slice_cell["texts"] = [
                    item
                    for item in cell["texts"]
                    if slice_top
                    <= (item["bbox"][1] + item["bbox"][3]) / 2
                    < slice_bottom
                    or (
                        slice_index == len(row_slices[original_row]) - 1
                        and (item["bbox"][1] + item["bbox"][3]) / 2
                        == slice_bottom
                    )
                ]
                slice_cell["text"] = "\n".join(
                    item["text"] for item in slice_cell["texts"]
                )
                slice_cell["scores"] = [
                    item["score"]
                    for item in slice_cell["texts"]
                    if isinstance(item["score"], (int, float))
                ]
                slice_cell["ocr_count"] = len(slice_cell["texts"])
                expanded_cells.append(slice_cell)

    cells = expanded_cells
    row_count = row_offsets[-1]
    occupied = {}
    for cell in cells:
        for row_offset in range(cell["rowspan"]):
            for column_offset in range(cell["colspan"]):
                position = (
                    cell["row_index"] + row_offset,
                    cell["column_index"] + column_offset,
                )
                if position in occupied:
                    raise ValueError(
                        f"Overlapping reconstructed cells at grid position {position}"
                    )
                occupied[position] = cell

    for row_index in range(row_count):
        for column_index in range(column_count):
            if (row_index, column_index) in occupied:
                continue
            original_row = next(
                index
                for index in range(len(row_slices))
                if row_offsets[index] <= row_index < row_offsets[index + 1]
            )
            slice_index = row_index - row_offsets[original_row]
            slice_top, slice_bottom = row_slices[original_row][slice_index]
            cell = {
                "cell_index": len(cells),
                "row_index": row_index,
                "column_index": column_index,
                "rowspan": 1,
                "colspan": 1,
                "bbox": [
                    round(x_boundaries[column_index], 2),
                    round(slice_top, 2),
                    round(x_boundaries[column_index + 1], 2),
                    round(slice_bottom, 2),
                ],
                "bbox_grid": [
                    round(x_boundaries[column_index], 2),
                    round(slice_top, 2),
                    round(x_boundaries[column_index + 1], 2),
                    round(slice_bottom, 2),
                ],
                "texts": [],
                "scores": [],
                "text": "",
                "ocr_count": 0,
            }
            occupied[(row_index, column_index)] = cell
            cells.append(cell)

    merged_cells: set[int] = set()
    for row_index in range(row_count):
        for column_index in range(column_count - 1):
            lead_cell = occupied.get((row_index, column_index))
            value_cell = occupied.get((row_index, column_index + 1))
            if (
                lead_cell is None
                or value_cell is None
                or lead_cell["row_index"] != row_index
                or value_cell["row_index"] != row_index
                or lead_cell["colspan"] != 1
                or value_cell["colspan"] != 1
                or lead_cell["rowspan"] != 1
                or value_cell["rowspan"] != 1
                or not lead_cell["text"]
                or not value_cell["text"]
                or id(lead_cell) in merged_cells
                or id(value_cell) in merged_cells
            ):
                continue

            continuation_rows = []
            next_row = row_index + 1
            while next_row < row_count:
                blank_cell = occupied.get((next_row, column_index))
                continuation_cell = occupied.get((next_row, column_index + 1))
                continuation_is_contained = (
                    continuation_cell is not None
                    and all(
                        item["bbox"][0]
                        >= continuation_cell["bbox_grid"][0]
                        - max(2.0, image_width * 0.005)
                        and item["bbox"][2]
                        <= continuation_cell["bbox_grid"][2]
                        + max(2.0, image_width * 0.005)
                        for item in continuation_cell["texts"]
                    )
                )
                if (
                    blank_cell is None
                    or continuation_cell is None
                    or not continuation_is_contained
                    or blank_cell["row_index"] != next_row
                    or continuation_cell["row_index"] != next_row
                    or blank_cell["colspan"] != 1
                    or continuation_cell["colspan"] != 1
                    or blank_cell["rowspan"] != 1
                    or continuation_cell["rowspan"] != 1
                    or blank_cell["text"]
                    or not continuation_cell["text"]
                    or blank_cell["cell_index"] != lead_cell["cell_index"]
                    or continuation_cell["cell_index"] != value_cell["cell_index"]
                ):
                    break
                continuation_rows.append((blank_cell, continuation_cell))
                next_row += 1

            if not continuation_rows:
                continue

            final_blank, final_value = continuation_rows[-1]
            span = len(continuation_rows)
            lead_cell["rowspan"] += span
            lead_cell["bbox_grid"][3] = final_blank["bbox_grid"][3]
            lead_cell["bbox"][3] = final_blank["bbox"][3]
            value_cell["rowspan"] += span
            value_cell["texts"].extend(
                item
                for _, continuation_cell in continuation_rows
                for item in continuation_cell["texts"]
            )
            value_cell["texts"].sort(
                key=lambda item: (item["bbox"][1], item["bbox"][0])
            )
            value_cell["text"] = "\n".join(
                item["text"] for item in value_cell["texts"]
            )
            value_cell["scores"].extend(
                score
                for _, continuation_cell in continuation_rows
                for score in continuation_cell["scores"]
            )
            value_cell["ocr_count"] = len(value_cell["texts"])
            value_cell["bbox_grid"][3] = final_value["bbox_grid"][3]
            value_cell["bbox"][3] = final_value["bbox"][3]
            merged_cells.update(
                id(cell)
                for pair in continuation_rows
                for cell in pair
            )

    if merged_cells:
        cells = [cell for cell in cells if id(cell) not in merged_cells]
        occupied = {}
        for cell in cells:
            for row_offset in range(cell["rowspan"]):
                for column_offset in range(cell["colspan"]):
                    position = (
                        cell["row_index"] + row_offset,
                        cell["column_index"] + column_offset,
                    )
                    occupied[position] = cell

    html_rows = []
    for row_index in range(row_count):
        rendered_cells = []
        column_index = 0
        while column_index < column_count:
            cell = occupied[(row_index, column_index)]
            if cell["row_index"] == row_index:
                attributes = []
                if cell["rowspan"] > 1:
                    attributes.append(f' rowspan="{cell["rowspan"]}"')
                    attributes.append(' valign="top"')
                if cell["colspan"] > 1:
                    attributes.append(f' colspan="{cell["colspan"]}"')
                attributes.extend(
                    (
                        f' data-width="{cell["bbox_grid"][2] - cell["bbox_grid"][0]:.2f}"',
                        f' data-height="{cell["bbox_grid"][3] - cell["bbox_grid"][1]:.2f}"',
                    )
                )
                rendered_cells.append(
                    f"<td{''.join(attributes)}>"
                    f"{'<br>'.join(html.escape(line) for line in cell['text'].splitlines())}"
                    "</td>"
                )
            column_index += 1
            if cell["column_index"] + cell["colspan"] > column_index:
                column_index = cell["column_index"] + cell["colspan"]
        html_rows.append(f"<tr>{''.join(rendered_cells)}</tr>")

    cells.sort(key=lambda cell: (cell["row_index"], cell["column_index"]))
    return cells, f"<html><body><table>{''.join(html_rows)}</table></body></html>"


def reconstruct_table_from_ocr(
    cell_boxes: list[list[float]],
    recognized_items: list[dict[str, Any]],
    image_size: tuple[int, int],
    image_path: Path | None = None,
    device: str = "cpu",
) -> tuple[list[dict[str, Any]], str]:
    image_width, image_height = image_size
    normalized_cell_boxes = remove_nested_cell_boxes(cell_boxes)
    valid_items: list[dict[str, Any]] = []
    for item in recognized_items:
        bbox = box_from_values(item.get("bbox"))
        text = str(item.get("text", "")).strip()
        if bbox is None or not text:
            continue
        valid_items.append({**item, "bbox": bbox, "text": text})
    valid_items, low_confidence_artifacts = remove_low_confidence_ocr_artifacts(
        valid_items
    )
    valid_items, excluded_items = exclude_ocr_outside_cell_bounds(
        normalized_cell_boxes,
        valid_items,
    )
    excluded_items.extend(low_confidence_artifacts)
    if not valid_items:
        raise ValueError("Cannot reconstruct table: no valid OCR text boxes.")

    cell_box_fallback_warning: str | None = None
    normalized_cell_boxes = remove_detector_cell_box_artifacts(
        normalized_cell_boxes,
        valid_items,
        image_size,
    )
    if image_path is not None:
        valid_items = split_ocr_items_at_cell_boundaries(
            valid_items,
            normalized_cell_boxes,
            image_path,
            device,
        )
    detected_x_edges = _cluster_values(
        [
            edge
            for box in normalized_cell_boxes
            for edge in (box[0], box[2])
        ],
        tolerance=max(
            STRUCTURE_RULES.minimum_pixel_tolerance,
            image_width * STRUCTURE_RULES.edge_cluster_image_ratio,
        ),
    )
    detected_y_edges = _cluster_values(
        [
            edge
            for box in normalized_cell_boxes
            for edge in (box[1], box[3])
        ],
        tolerance=max(
            STRUCTURE_RULES.minimum_pixel_tolerance,
            image_height * STRUCTURE_RULES.edge_cluster_image_ratio,
        ),
    )
    detected_x_boundaries = [
        statistics.mean(group) for group in detected_x_edges
    ]
    detected_y_boundaries = [
        statistics.mean(group) for group in detected_y_edges
    ]
    all_ocr_centers_inside_cells = all(
        any(
            cell[0] <= (item["bbox"][0] + item["bbox"][2]) / 2 <= cell[2]
            and cell[1] <= (item["bbox"][1] + item["bbox"][3]) / 2 <= cell[3]
            for cell in normalized_cell_boxes
        )
        for item in valid_items
    )

    def nearest_boundary_index(value: float, boundaries: list[float]) -> int:
        return min(
            range(len(boundaries)),
            key=lambda index: abs(boundaries[index] - value),
        )

    has_spanning_cell = any(
        nearest_boundary_index(box[2], detected_x_boundaries)
        - nearest_boundary_index(box[0], detected_x_boundaries)
        > 1
        or nearest_boundary_index(box[3], detected_y_boundaries)
        - nearest_boundary_index(box[1], detected_y_boundaries)
        > 1
        for box in normalized_cell_boxes
    )
    has_multiple_ocr_in_cell = any(
        sum(
            box[0] <= (item["bbox"][0] + item["bbox"][2]) / 2 <= box[2]
            and box[1] <= (item["bbox"][1] + item["bbox"][3]) / 2 <= box[3]
            for item in valid_items
        )
        >= 2
        for box in normalized_cell_boxes
    )
    has_complete_detected_grid = (
        len(detected_x_edges) >= 3
        and len(detected_y_edges) >= 3
        and has_spanning_cell
        and not has_material_cell_box_overlap(normalized_cell_boxes)
        and all_ocr_centers_inside_cells
    )
    has_sparse_detected_grid = (
        len(detected_x_edges) >= 3
        and len(detected_y_edges) >= 3
        and len(normalized_cell_boxes)
        < len(valid_items) * STRUCTURE_RULES.sparse_grid_cell_to_ocr_ratio
        and has_multiple_ocr_in_cell
        and not has_material_cell_box_overlap(normalized_cell_boxes)
    )
    if (
        has_complete_detected_grid
        or has_sparse_detected_grid
    ):
        try:
            cells, reconstructed_html = reconstruct_table_from_cell_boxes(
                normalized_cell_boxes,
                valid_items,
                image_size,
                image_path,
            )
            add_excluded_ocr_warnings(cells, excluded_items)
            return cells, reconstructed_html
        except ValueError as error:
            if not str(error).startswith(
                "Overlapping reconstructed cells at grid position"
            ):
                raise
            cell_box_fallback_warning = str(error)

    x_boundaries = infer_column_boundaries(
        normalized_cell_boxes,
        valid_items,
        image_width,
        image_path,
    )
    column_count = len(x_boundaries) - 1
    if column_count < 2:
        raise ValueError(f"Invalid inferred column count: {column_count}")
    if image_path is not None:
        valid_items = split_ocr_items_at_inferred_column_gaps(
            valid_items,
            x_boundaries,
            image_path,
            device,
        )

    median_text_height = statistics.median(
        item["bbox"][3] - item["bbox"][1] for item in valid_items
    )
    row_tolerance = max(
        STRUCTURE_RULES.minimum_pixel_tolerance,
        median_text_height * STRUCTURE_RULES.row_cluster_text_height_ratio,
    )
    row_groups: list[list[dict[str, Any]]] = []
    for item in sorted(
        valid_items,
        key=lambda entry: (
            (entry["bbox"][1] + entry["bbox"][3]) / 2,
            entry["bbox"][0],
        ),
    ):
        center_y = (item["bbox"][1] + item["bbox"][3]) / 2
        if (
            not row_groups
            or abs(
                center_y
                - statistics.mean(
                    (entry["bbox"][1] + entry["bbox"][3]) / 2
                    for entry in row_groups[-1]
                )
            )
            > row_tolerance
        ):
            row_groups.append([])
        row_groups[-1].append(item)

    mapped: list[dict[str, Any]] = []
    mapped_by_cell: dict[tuple[int, int], dict[str, Any]] = {}
    row_centers: list[float] = []
    ocr_boundary_tolerance = max(
        STRUCTURE_RULES.minimum_pixel_tolerance,
        image_width * STRUCTURE_RULES.edge_cluster_image_ratio,
    )

    def append_item_to_cell(
        cell: dict[str, Any],
        item: dict[str, Any],
        mapping_warning: str | None = None,
    ) -> None:
        mapped_item = {**item}
        if mapping_warning is not None:
            mapped_item["mapping_warning"] = mapping_warning
        cell["recognized_items"].append(mapped_item)
        cell["text"] = " ".join(
            existing_item["text"]
            for existing_item in cell["recognized_items"]
        )
        cell["bbox"] = [
            min(existing_item["bbox"][0] for existing_item in cell["recognized_items"]),
            min(existing_item["bbox"][1] for existing_item in cell["recognized_items"]),
            max(existing_item["bbox"][2] for existing_item in cell["recognized_items"]),
            max(existing_item["bbox"][3] for existing_item in cell["recognized_items"]),
        ]

    for row_index, row in enumerate(row_groups):
        row_centers.append(
            statistics.mean((item["bbox"][1] + item["bbox"][3]) / 2 for item in row)
        )
        used_columns: set[int] = set()
        for item in sorted(row, key=lambda entry: entry["bbox"][0]):
            center_x = (item["bbox"][0] + item["bbox"][2]) / 2
            contained_column = next(
                (
                    column
                    for column in range(column_count)
                    if item["bbox"][0]
                    >= x_boundaries[column] - ocr_boundary_tolerance
                    and item["bbox"][2]
                    <= x_boundaries[column + 1] + ocr_boundary_tolerance
                ),
                None,
            )
            candidates = []
            if contained_column is not None:
                existing_cell = mapped_by_cell.get((row_index, contained_column))
                if existing_cell is not None:
                    retained_recognized_items = [
                        existing_item
                        for existing_item in existing_cell["recognized_items"]
                        if not is_nested_ocr_fragment(existing_item, item)
                    ]
                    if any(
                        is_nested_ocr_fragment(item, existing_item)
                        for existing_item in retained_recognized_items
                    ):
                        continue
                    existing_cell["recognized_items"] = retained_recognized_items
                    existing_cell["text"] = " ".join(
                        existing_item["text"]
                        for existing_item in retained_recognized_items
                    )
                    existing_cell["bbox"] = [
                        min(existing_item["bbox"][0] for existing_item in retained_recognized_items),
                        min(existing_item["bbox"][1] for existing_item in retained_recognized_items),
                        max(existing_item["bbox"][2] for existing_item in retained_recognized_items),
                        max(existing_item["bbox"][3] for existing_item in retained_recognized_items),
                    ]
                    append_item_to_cell(existing_cell, item)
                    continue
                candidates.append((0.0, 1, contained_column, contained_column + 1))
            else:
                center_column = min(
                    range(column_count),
                    key=lambda column: abs(
                        center_x
                        - (x_boundaries[column] + x_boundaries[column + 1]) / 2
                    ),
                )
                covered_columns = [
                    column
                    for column in range(column_count)
                    if item["bbox"][0]
                    <= (x_boundaries[column] + x_boundaries[column + 1]) / 2
                    <= item["bbox"][2]
                ]
                if len(covered_columns) >= 2 and (
                    covered_columns[0] <= center_column <= covered_columns[-1]
                ):
                    start = covered_columns[0]
                    end = covered_columns[-1] + 1
                else:
                    start = center_column
                    end = center_column + 1
                candidates.append((0.0, end - start, start, end))
            available = [
                candidate
                for candidate in candidates
                if not any(
                    column in used_columns
                    for column in range(candidate[2], candidate[3])
                )
            ]
            if not available:
                row_cells = [
                    cell for cell in mapped if cell["row_index"] == row_index
                ]
                if not row_cells:
                    raise ValueError(
                        "Unable to assign OCR text and no mapped cell exists "
                        f"in row {row_index}: {item['text']!r}"
                    )
                selected_cell = max(
                    row_cells,
                    key=lambda cell: (
                        x_boundaries[cell["column_index"]]
                        <= center_x
                        <= x_boundaries[
                            cell["column_index"] + cell["colspan"]
                        ],
                        intersection_area(item["bbox"], cell["bbox"]),
                        -abs(
                            center_x
                            - statistics.mean(
                                (
                                    x_boundaries[cell["column_index"]],
                                    x_boundaries[
                                        cell["column_index"] + cell["colspan"]
                                    ],
                                )
                            )
                        ),
                    ),
                )
                append_item_to_cell(
                    selected_cell,
                    item,
                    "no_free_grid_cell; assigned to nearest overlapping cell",
                )
                continue
            _, _, start, end = min(available)
            used_columns.update(range(start, end))
            mapped_cell = {
                    "row_index": row_index,
                    "column_index": start,
                    "colspan": end - start,
                    "rowspan": 1,
                    "text": item["text"],
                    "score": item.get("score"),
                    "bbox": [round(value, 2) for value in item["bbox"]],
                    "recognized_items": [item],
                }
            mapped.append(mapped_cell)
            if end - start == 1:
                mapped_by_cell[(row_index, start)] = mapped_cell

    row_boundaries = [0.0]
    row_boundaries.extend(
        (top + bottom) / 2
        for top, bottom in zip(row_centers, row_centers[1:])
    )
    row_boundaries.append(float(image_height))
    horizontal_rules = (
        detect_horizontal_rules(image_path, x_boundaries, row_centers)
        if image_path is not None
        else [[True] * column_count for _ in range(len(row_groups) - 1)]
    )
    gap_has_horizontal_rule = [
        any(boundary_rules) for boundary_rules in horizontal_rules
    ]
    occupied: dict[tuple[int, int], dict[str, Any]] = {}
    for index, cell in enumerate(mapped):
        column_range = range(
            cell["column_index"],
            cell["column_index"] + cell["colspan"],
        )
        while (
            cell["row_index"] + cell["rowspan"] < len(row_groups)
            and gap_has_horizontal_rule[
                cell["row_index"] + cell["rowspan"] - 1
            ]
            and not any(
                horizontal_rules[cell["row_index"] + cell["rowspan"] - 1][column]
                for column in column_range
            )
            and not any(
                continuation_cell["row_index"]
                <= cell["row_index"] + cell["rowspan"]
                < continuation_cell["row_index"] + continuation_cell["rowspan"]
                and continuation_cell["text"]
                and continuation_cell is not cell
                and continuation_cell["column_index"]
                < cell["column_index"] + cell["colspan"]
                and continuation_cell["column_index"]
                + continuation_cell["colspan"]
                > cell["column_index"]
                for continuation_cell in mapped
            )
        ):
            cell["rowspan"] += 1
        cell["cell_index"] = index
        cell["texts"] = [
            {
                "text": item["text"],
                "score": item.get("score"),
                "bbox": [round(value, 2) for value in item["bbox"]],
                "mapping_warning": item.get("mapping_warning"),
            }
            for item in cell.pop("recognized_items")
        ]
        cell["scores"] = [
            item["score"]
            for item in cell["texts"]
            if isinstance(item["score"], (int, float))
        ]
        cell["ocr_count"] = len(cell["texts"])
        cell["bbox_grid"] = [
            round(x_boundaries[cell["column_index"]], 2),
            round(row_boundaries[cell["row_index"]], 2),
            round(x_boundaries[cell["column_index"] + cell["colspan"]], 2),
            round(row_boundaries[cell["row_index"] + cell["rowspan"]], 2),
        ]
        for row_offset in range(cell["rowspan"]):
            for column_offset in range(cell["colspan"]):
                occupied[
                    (cell["row_index"] + row_offset, cell["column_index"] + column_offset)
                ] = cell

    for row_index in range(len(row_groups)):
        for column_index in range(column_count):
            if (row_index, column_index) in occupied:
                continue
            blank_cell = {
                "cell_index": len(mapped) + len(occupied),
                "row_index": row_index,
                "column_index": column_index,
                "rowspan": 1,
                "colspan": 1,
                "bbox": [
                    round(x_boundaries[column_index], 2),
                    round(row_boundaries[row_index], 2),
                    round(x_boundaries[column_index + 1], 2),
                    round(row_boundaries[row_index + 1], 2),
                ],
                "texts": [],
                "scores": [],
                "text": "",
                "ocr_count": 0,
            }
            while (
                row_index + blank_cell["rowspan"] < len(row_groups)
                and gap_has_horizontal_rule[
                    row_index + blank_cell["rowspan"] - 1
                ]
                and not horizontal_rules[
                    row_index + blank_cell["rowspan"] - 1
                ][column_index]
                and (
                    row_index + blank_cell["rowspan"],
                    column_index,
                )
                not in occupied
            ):
                blank_cell["rowspan"] += 1
            blank_cell["bbox_grid"] = [
                round(x_boundaries[column_index], 2),
                round(row_boundaries[row_index], 2),
                round(x_boundaries[column_index + 1], 2),
                round(row_boundaries[row_index + blank_cell["rowspan"]], 2),
            ]
            for row_offset in range(blank_cell["rowspan"]):
                occupied[
                    (row_index + row_offset, column_index)
                ] = blank_cell

    if image_path is not None:
        for row_index in range(len(row_groups)):
            column_index = 0
            while column_index < column_count:
                cell = occupied[(row_index, column_index)]
                if (
                    cell["row_index"] != row_index
                    or cell["column_index"] != column_index
                    or cell["ocr_count"] != 0
                ):
                    column_index += 1
                    continue

                while column_index + cell["colspan"] < column_count:
                    next_column = column_index + cell["colspan"]
                    adjacent = occupied[(row_index, next_column)]
                    if (
                        adjacent["row_index"] != row_index
                        or adjacent["column_index"] != next_column
                        or adjacent["ocr_count"] != 0
                        or adjacent["rowspan"] != cell["rowspan"]
                        or has_vertical_rule(
                            image_path,
                            x_boundaries[next_column],
                            row_boundaries[row_index],
                            row_boundaries[row_index + cell["rowspan"]],
                        )
                    ):
                        break

                    cell["colspan"] += adjacent["colspan"]
                    cell["bbox_grid"][2] = round(
                        x_boundaries[next_column + adjacent["colspan"]],
                        2,
                    )
                    cell["bbox_grid"][0:4] = [
                        round(value, 2) for value in cell["bbox_grid"]
                    ]
                    cell["bbox"] = cell["bbox_grid"]
                    for row_offset in range(cell["rowspan"]):
                        for column_offset in range(
                            cell["column_index"],
                            cell["column_index"] + cell["colspan"],
                        ):
                            occupied[(row_index + row_offset, column_offset)] = cell

                column_index += cell["colspan"]

    html_rows = []
    for row_index in range(len(row_groups)):
        rendered_cells = []
        column_index = 0
        while column_index < column_count:
            cell = occupied.get((row_index, column_index))
            if cell is not None:
                if cell["row_index"] == row_index and cell["column_index"] == column_index:
                    attrs = []
                    if cell["rowspan"] > 1:
                        attrs.append(f' rowspan="{cell["rowspan"]}"')
                    if cell["colspan"] > 1:
                        attrs.append(f' colspan="{cell["colspan"]}"')
                    attrs.extend(
                        (
                            f' data-width="{cell["bbox_grid"][2] - cell["bbox_grid"][0]:.2f}"',
                            f' data-height="{cell["bbox_grid"][3] - cell["bbox_grid"][1]:.2f}"',
                        )
                    )
                    rendered_cells.append(
                        f"<td{''.join(attrs)}>{html.escape(cell['text'])}</td>"
                    )
                column_index += 1
                continue
            rendered_cells.append("<td></td>")
            column_index += 1
        html_rows.append(f"<tr>{''.join(rendered_cells)}</tr>")

    cells = []
    for row_index in range(len(row_groups)):
        for column_index in range(column_count):
            cell = occupied.get((row_index, column_index))
            if cell is not None and (
                cell["row_index"] == row_index
                and cell["column_index"] == column_index
            ):
                cells.append(
                    {
                        key: value
                        for key, value in cell.items()
                        if key != "bbox"
                    }
                    | {"bbox": cell["bbox_grid"]}
                )
            elif cell is None:
                cells.append(
                    {
                        "cell_index": len(cells),
                        "row_index": row_index,
                        "column_index": column_index,
                        "rowspan": 1,
                        "colspan": 1,
                        "bbox": [
                            round(x_boundaries[column_index], 2),
                            round(row_boundaries[row_index], 2),
                            round(x_boundaries[column_index + 1], 2),
                            round(row_boundaries[row_index + 1], 2),
                        ],
                        "texts": [],
                        "scores": [],
                        "text": "",
                        "ocr_count": 0,
                    }
                )

    if cell_box_fallback_warning and cells:
        cells[0]["mapping_warnings"] = [
            "Detected cell grid was inconsistent; reconstructed from OCR "
            f"positions instead ({cell_box_fallback_warning})."
        ]
    add_excluded_ocr_warnings(cells, excluded_items)
    return cells, f"<html><body><table>{''.join(html_rows)}</table></body></html>"


def normalize_result_data(result_data: Any) -> dict[str, Any]:
    if not isinstance(result_data, dict):
        raise TypeError(
            f"Unexpected PaddleOCR result type: {type(result_data).__name__}"
        )
    if "table_res_list" not in result_data and isinstance(
        result_data.get("res"), dict
    ):
        return result_data["res"]
    return result_data


class TableHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[dict[str, Any]]] = []
        self._row: list[dict[str, Any]] | None = None
        self._cell: dict[str, Any] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "tr":
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = {
                "text": "",
                "colspan": max(1, int(attributes.get("colspan") or 1)),
                "rowspan": max(1, int(attributes.get("rowspan") or 1)),
                "width": (
                    float(attributes["data-width"])
                    if attributes.get("data-width")
                    else None
                ),
                "height": (
                    float(attributes["data-height"])
                    if attributes.get("data-height")
                    else None
                ),
            }
        elif tag == "br" and self._cell is not None:
            self._cell["text"] += "\n"

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._cell is not None:
            self._cell["text"] = "\n".join(
                " ".join(line.split())
                for line in self._cell["text"].splitlines()
                if line.strip()
            )
            if self._row is not None:
                self._row.append(self._cell)
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell["text"] += data


def layout_html_cells(
    rows: list[list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], int]:
    occupied: set[tuple[int, int]] = set()
    cells: list[dict[str, Any]] = []
    column_count = 0

    for row_index, row in enumerate(rows):
        column_index = 0
        for cell in row:
            colspan = max(1, int(cell["colspan"]))
            rowspan = max(1, int(cell["rowspan"]))
            while any(
                (row_index, column)
                in occupied
                for column in range(column_index, column_index + colspan)
            ):
                column_index += 1

            placed = {
                **cell,
                "row_index": row_index,
                "column_index": column_index,
            }
            cells.append(placed)
            for row_offset in range(rowspan):
                for column_offset in range(colspan):
                    occupied.add(
                        (row_index + row_offset, column_index + column_offset)
                    )
            column_index += colspan
            column_count = max(column_count, column_index)

    return cells, column_count


@lru_cache(maxsize=32)
def _font_for_size(size: int) -> Any:
    from PIL import ImageFont

    font_candidates = [
        Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "tahoma.ttf",
        Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "arial.ttf",
        Path("/usr/share/fonts/truetype/noto/NotoSansThai-Regular.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ]
    for font_path in font_candidates:
        if font_path.is_file():
            return ImageFont.truetype(str(font_path), size=size)
    return ImageFont.load_default()


def render_table_comparison(
    original_path: Path,
    predicted_html: str,
    output_dir: Path,
    table_index: int,
    cell_map: list[dict[str, Any]] | None = None,
    output_stem: str | None = None,
) -> Path:
    from PIL import Image, ImageDraw

    parser = TableHTMLParser()
    parser.feed(predicted_html)
    parser.close()
    if not parser.rows:
        raise ValueError("Predicted table HTML contains no table rows.")

    cells, column_count = layout_html_cells(parser.rows)
    if column_count == 0:
        raise ValueError("Predicted table HTML contains no cells.")
    cell_geometry = {
        (cell["row_index"], cell["column_index"]): cell["bbox_grid"]
        for cell in cell_map or []
        if isinstance(cell.get("bbox_grid"), list)
    }

    with Image.open(original_path) as source_image:
        original = source_image.convert("RGB")
    width, height = original.size
    title_height = max(30, min(48, height // 8))
    margin = max(8, min(18, width // 40))
    table_top = title_height + margin
    table_bottom = height - margin
    table_width = width - 2 * margin
    table_height = table_bottom - table_top
    if table_width <= 0 or table_height <= 0:
        raise ValueError(
            f"Image is too small to render a comparison: {width}x{height}"
        )

    reconstructed = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(reconstructed)
    row_count = max(
        len(parser.rows),
        max(cell["row_index"] + cell["rowspan"] for cell in cells),
    )
    column_sizes = [0.0] * column_count
    row_sizes = [0.0] * row_count
    for cell in cells:
        bbox_grid = cell_geometry.get(
            (cell["row_index"], cell["column_index"])
        )
        cell_width = (
            bbox_grid[2] - bbox_grid[0]
            if bbox_grid is not None
            else cell.get("width")
        )
        if cell_width and cell["colspan"] == 1:
            column_sizes[cell["column_index"]] = max(
                column_sizes[cell["column_index"]],
                cell_width,
            )
        cell_height = (
            bbox_grid[3] - bbox_grid[1]
            if bbox_grid is not None
            else cell.get("height")
        )
        if cell_height and cell["rowspan"] == 1:
            row_sizes[cell["row_index"]] = max(
                row_sizes[cell["row_index"]],
                cell_height,
            )

    if not any(column_sizes):
        column_sizes = [1.0] * column_count
    else:
        fallback_column_size = statistics.median(
            size for size in column_sizes if size > 0
        )
        column_sizes = [
            size if size > 0 else fallback_column_size
            for size in column_sizes
        ]
    if not any(row_sizes):
        row_sizes = [1.0] * row_count
    else:
        fallback_row_size = statistics.median(size for size in row_sizes if size > 0)
        row_sizes = [
            size if size > 0 else fallback_row_size
            for size in row_sizes
        ]

    column_positions = [float(margin)]
    for size in column_sizes:
        column_positions.append(
            column_positions[-1] + table_width * size / sum(column_sizes)
        )
    row_positions = [float(table_top)]
    for size in row_sizes:
        row_positions.append(
            row_positions[-1] + table_height * size / sum(row_sizes)
        )
    line_width = max(1, round(min(width, height) / 500))

    for cell in cells:
        x0 = round(column_positions[cell["column_index"]])
        y0 = round(row_positions[cell["row_index"]])
        x1 = round(column_positions[cell["column_index"] + cell["colspan"]])
        y1 = round(row_positions[cell["row_index"] + cell["rowspan"]])
        draw.rectangle((x0, y0, x1, y1), outline="#333333", width=line_width)

        text = cell["text"]
        if not text:
            continue
        cell_height = (y1 - y0) / cell["rowspan"]
        font_scale = 0.6 if cell["rowspan"] > 1 else 0.42
        font_size = max(10, min(28, round(cell_height * font_scale)))
        while font_size > 9:
            font = _font_for_size(font_size)
            text_box = draw.multiline_textbbox(
                (0, 0),
                text,
                font=font,
                spacing=2,
                align="center",
            )
            if (
                text_box[2] - text_box[0] <= max(1, x1 - x0 - 8)
                and text_box[3] - text_box[1] <= max(1, y1 - y0 - 6)
            ):
                break
            font_size -= 1
        font = _font_for_size(font_size)
        text_box = draw.multiline_textbbox(
            (0, 0),
            text,
            font=font,
            spacing=2,
            align="center",
        )
        text_x = x0 + ((x1 - x0) - (text_box[2] - text_box[0])) / 2
        text_y = (
            y0 + 3
            if cell["rowspan"] > 1
            else y0 + ((y1 - y0) - (text_box[3] - text_box[1])) / 2
        )
        draw.multiline_text(
            (round(text_x - text_box[0]), round(text_y - text_box[1])),
            text,
            fill="#111111",
            font=font,
            spacing=2,
            align="center",
        )

    panel_gap = max(12, margin)
    heading_height = title_height
    comparison = Image.new(
        "RGB",
        (width * 2 + panel_gap, height + heading_height),
        "#e8edf2",
    )
    comparison_draw = ImageDraw.Draw(comparison)
    heading_font = _font_for_size(max(12, min(20, heading_height // 2)))
    comparison_draw.text(
        (margin, max(4, (heading_height - 20) // 2)),
        "Original",
        fill="#111111",
        font=heading_font,
    )
    comparison_draw.text(
        (width + panel_gap + margin, max(4, (heading_height - 20) // 2)),
        "Reconstructed from OCR grid",
        fill="#111111",
        font=heading_font,
    )
    comparison.paste(original, (0, heading_height))
    comparison.paste(reconstructed, (width + panel_gap, heading_height))
    comparison_name = (
        f"{output_stem}_comparison.png"
        if output_stem is not None
        else f"table_{table_index}_comparison.png"
    )
    comparison_path = output_dir / comparison_name
    comparison.save(comparison_path)
    return comparison_path


def image_inputs(input_path: Path) -> list[Path]:
    if input_path.is_file():
        if input_path.suffix.lower() not in IMAGE_SUFFIXES:
            raise ValueError(f"Unsupported image file: {input_path}")
        return [input_path]
    if input_path.is_dir():
        return sorted(
            path
            for path in input_path.iterdir()
            if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
        )
    raise FileNotFoundError(f"Input path does not exist: {input_path}")


def run_pipeline(input_path: Path, output_root: Path, device: str) -> None:
    from paddleocr import TableRecognitionPipelineV2

    images = image_inputs(input_path)
    if not images:
        raise FileNotFoundError(f"No supported images found in: {input_path}")

    print("=" * 68)
    print("PaddleOCR Table Recognition Pipeline")
    print(f"Detector   : {DETECTION_MODEL}")
    print(f"Recognizer : {RECOGNITION_MODEL}")
    print(f"Device     : {device}")
    print(f"Images     : {len(images)}")
    print("=" * 68)

    pipeline = TableRecognitionPipelineV2(
        device=device,
        enable_mkldnn=False,
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_layout_detection=False,
        use_ocr_model=True,
        text_detection_model_name=DETECTION_MODEL,
        text_recognition_model_name=RECOGNITION_MODEL,
    )

    output_root.mkdir(parents=True, exist_ok=True)
    for image_path in images:
        print(f"\nProcessing: {image_path}")

        results = pipeline.predict(
            str(image_path),
            use_ocr_model=True,
            use_ocr_results_with_table_cells=True,
            use_wired_table_cells_trans_to_html=True,
            use_wireless_table_cells_trans_to_html=True,
        )
        result_count = 0

        for result_count, result in enumerate(results, start=1):
            print(f"\nResult #{result_count}")
            result.print()
            result_data = normalize_result_data(result.json)

            for table_index, table_result in enumerate(
                result_data.get("table_res_list") or [], start=1
            ):
                from PIL import Image

                with Image.open(image_path) as source_image:
                    image_size = source_image.size
                cells, reconstructed_html = reconstruct_table_from_ocr(
                    table_result.get("cell_box_list") or [],
                    extract_recognized_items(table_result),
                    image_size,
                    image_path,
                    device,
                )
                comparison_path = render_table_comparison(
                    image_path,
                    reconstructed_html,
                    output_root,
                    table_index,
                    cells,
                    f"{image_path.stem}_table_{table_index}",
                )
                print(f"  Original/result comparison: {comparison_path.name}")

        if result_count == 0:
            print("WARNING: No table results were returned.")
    print(f"Output: {output_root}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run table recognition with PP-OCRv6 medium detection and "
            "Thai PP-OCRv5 mobile recognition."
        )
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT,
        help="Image path or directory of images (default: input directory).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Output root directory (default: output).",
    )
    parser.add_argument(
        "--device",
        choices=("cpu", "gpu"),
        default="cpu",
        help="Paddle inference device (default: cpu).",
    )
    return parser.parse_args()


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(errors="backslashreplace")
    args = parse_args()
    run_pipeline(args.input, args.output, args.device)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print("\nERROR: Table Recognition Pipeline Failed", file=sys.stderr)
        traceback.print_exc()
        raise SystemExit(1)
