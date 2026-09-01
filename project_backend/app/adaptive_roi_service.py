import os
from statistics import median
from typing import Any, Dict, List, Optional, Tuple


class AdaptiveRoiService:
    SEARCH_PADDING_RATIO = 0.15
    MIN_SIZE_RATIO = 0.002
    MAX_EXPANSION_RATIO = 4.0
    MAX_SEARCH_COVERAGE = 0.92
    MIN_COVERAGE = 0.01
    MIN_CONFIDENCE = 0.20
    MIN_ADAPTIVE_CONFIDENCE = 0.25

    def __init__(self) -> None:
        enabled_value = os.getenv("ADAPTIVE_ROI_ENABLED", "true").strip().lower()
        self.enabled = enabled_value not in {"0", "false", "no", "off"}
        try:
            padding = float(os.getenv("ADAPTIVE_ROI_SEARCH_PADDING_RATIO", str(self.SEARCH_PADDING_RATIO)))
        except ValueError:
            padding = self.SEARCH_PADDING_RATIO
        self.search_padding_ratio = max(0.0, min(0.50, padding))

    def expand_roi(self, roi: Dict[str, Any], padding_ratio: Optional[float] = None) -> Dict[str, float]:
        padding_ratio = self.search_padding_ratio if padding_ratio is None else padding_ratio
        x = float(roi.get("x_ratio", 0.0) or 0.0)
        y = float(roi.get("y_ratio", 0.0) or 0.0)
        width = float(roi.get("width_ratio", 0.0) or 0.0)
        height = float(roi.get("height_ratio", 0.0) or 0.0)
        pad_x = width * padding_ratio
        pad_y = height * padding_ratio
        left = max(0.0, x - pad_x)
        top = max(0.0, y - pad_y)
        right = min(1.0, x + width + pad_x)
        bottom = min(1.0, y + height + pad_y)
        return {
            "x_ratio": round(left, 6),
            "y_ratio": round(top, 6),
            "width_ratio": round(max(0.0, right - left), 6),
            "height_ratio": round(max(0.0, bottom - top), 6),
        }

    def bbox_to_ratio(self, bbox: Dict[str, Any], image_width: float, image_height: float) -> Dict[str, float]:
        left = max(0.0, min(1.0, float(bbox.get("x", 0.0) or 0.0) / image_width))
        top = max(0.0, min(1.0, float(bbox.get("y", 0.0) or 0.0) / image_height))
        right = max(left, min(1.0, float(bbox.get("x", 0.0) or 0.0) / image_width + float(bbox.get("width", 0.0) or 0.0) / image_width))
        bottom = max(top, min(1.0, float(bbox.get("y", 0.0) or 0.0) / image_height + float(bbox.get("height", 0.0) or 0.0) / image_height))
        return {
            "x_ratio": round(left, 6),
            "y_ratio": round(top, 6),
            "width_ratio": round(right - left, 6),
            "height_ratio": round(bottom - top, 6),
        }

    def intersects(self, left: Dict[str, float], right: Dict[str, float]) -> bool:
        left_x2 = left["x_ratio"] + left["width_ratio"]
        left_y2 = left["y_ratio"] + left["height_ratio"]
        right_x2 = right["x_ratio"] + right["width_ratio"]
        right_y2 = right["y_ratio"] + right["height_ratio"]
        return left["x_ratio"] < right_x2 and left_x2 > right["x_ratio"] and left["y_ratio"] < right_y2 and left_y2 > right["y_ratio"]

    def _contains(self, outer: Dict[str, float], inner: Dict[str, float]) -> bool:
        return (
            inner["x_ratio"] >= outer["x_ratio"]
            and inner["y_ratio"] >= outer["y_ratio"]
            and inner["x_ratio"] + inner["width_ratio"] <= outer["x_ratio"] + outer["width_ratio"] + 1e-6
            and inner["y_ratio"] + inner["height_ratio"] <= outer["y_ratio"] + outer["height_ratio"] + 1e-6
        )

    def _center(self, bbox: Dict[str, float]) -> Tuple[float, float]:
        return (
            bbox["x_ratio"] + bbox["width_ratio"] / 2,
            bbox["y_ratio"] + bbox["height_ratio"] / 2,
        )

    def _merge_boxes(self, boxes: List[Dict[str, Any]]) -> Dict[str, float]:
        min_x = min(box["bbox"]["x_ratio"] for box in boxes)
        min_y = min(box["bbox"]["y_ratio"] for box in boxes)
        max_x = max(box["bbox"]["x_ratio"] + box["bbox"]["width_ratio"] for box in boxes)
        max_y = max(box["bbox"]["y_ratio"] + box["bbox"]["height_ratio"] for box in boxes)
        return {
            "x_ratio": round(min_x, 6),
            "y_ratio": round(min_y, 6),
            "width_ratio": round(max(0.0, max_x - min_x), 6),
            "height_ratio": round(max(0.0, max_y - min_y), 6),
        }

    def group_word_boxes(self, word_boxes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not word_boxes:
            return []

        sorted_words = sorted(word_boxes, key=lambda item: (self._center(item["bbox"])[1], item["bbox"]["x_ratio"]))
        heights = [max(0.001, box["bbox"]["height_ratio"]) for box in sorted_words]
        line_threshold = max(0.012, median(heights) * 0.65)
        lines: List[List[Dict[str, Any]]] = []

        for word in sorted_words:
            _, center_y = self._center(word["bbox"])
            if not lines:
                lines.append([word])
                continue
            last_line = lines[-1]
            line_center = sum(self._center(item["bbox"])[1] for item in last_line) / len(last_line)
            if abs(center_y - line_center) <= line_threshold:
                last_line.append(word)
            else:
                lines.append([word])

        groups: List[Dict[str, Any]] = []
        for line in lines:
            line = sorted(line, key=lambda item: item["bbox"]["x_ratio"])
            merged: List[List[Dict[str, Any]]] = []
            for word in line:
                if not merged:
                    merged.append([word])
                    continue
                previous_group = merged[-1]
                previous_bbox = self._merge_boxes(previous_group)
                gap = word["bbox"]["x_ratio"] - (previous_bbox["x_ratio"] + previous_bbox["width_ratio"])
                avg_height = sum(item["bbox"]["height_ratio"] for item in previous_group + [word]) / (len(previous_group) + 1)
                if gap <= max(0.03, avg_height * 1.5):
                    previous_group.append(word)
                else:
                    merged.append([word])

            for group in merged:
                confidences = [float(item.get("confidence") or 0.0) for item in group]
                groups.append(
                    {
                        "bbox": self._merge_boxes(group),
                        "words": group,
                        "word_count": len(group),
                        "ocr_confidence": round(sum(confidences) / len(confidences), 4) if confidences else 0.0,
                        "text": " ".join(str(item.get("text") or "") for item in group).strip(),
                    }
                )

        return groups

    def _distance_score(self, projected_roi: Dict[str, float], adaptive_roi: Dict[str, float]) -> float:
        px, py = self._center(projected_roi)
        ax, ay = self._center(adaptive_roi)
        distance = ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5
        return max(0.0, min(1.0, 1.0 - distance / 0.20))

    def _overlap_ratio(self, left: Dict[str, float], right: Dict[str, float]) -> float:
        left_x2 = left["x_ratio"] + left["width_ratio"]
        left_y2 = left["y_ratio"] + left["height_ratio"]
        right_x2 = right["x_ratio"] + right["width_ratio"]
        right_y2 = right["y_ratio"] + right["height_ratio"]
        overlap_width = max(0.0, min(left_x2, right_x2) - max(left["x_ratio"], right["x_ratio"]))
        overlap_height = max(0.0, min(left_y2, right_y2) - max(left["y_ratio"], right["y_ratio"]))
        overlap_area = overlap_width * overlap_height
        smaller_area = max(1e-6, min(left["width_ratio"] * left["height_ratio"], right["width_ratio"] * right["height_ratio"]))
        return max(0.0, min(1.0, overlap_area / smaller_area))

    def _iou(self, left: Dict[str, float], right: Dict[str, float]) -> float:
        left_x2 = left["x_ratio"] + left["width_ratio"]
        left_y2 = left["y_ratio"] + left["height_ratio"]
        right_x2 = right["x_ratio"] + right["width_ratio"]
        right_y2 = right["y_ratio"] + right["height_ratio"]
        overlap_width = max(0.0, min(left_x2, right_x2) - max(left["x_ratio"], right["x_ratio"]))
        overlap_height = max(0.0, min(left_y2, right_y2) - max(left["y_ratio"], right["y_ratio"]))
        overlap_area = overlap_width * overlap_height
        union_area = max(1e-6, (left["width_ratio"] * left["height_ratio"]) + (right["width_ratio"] * right["height_ratio"]) - overlap_area)
        return max(0.0, min(1.0, overlap_area / union_area))

    def _size_similarity(self, left: Dict[str, float], right: Dict[str, float]) -> float:
        width_score = min(left["width_ratio"], right["width_ratio"]) / max(left["width_ratio"], right["width_ratio"], 1e-6)
        height_score = min(left["height_ratio"], right["height_ratio"]) / max(left["height_ratio"], right["height_ratio"], 1e-6)
        area_left = left["width_ratio"] * left["height_ratio"]
        area_right = right["width_ratio"] * right["height_ratio"]
        area_score = min(area_left, area_right) / max(area_left, area_right, 1e-6)
        return max(0.0, min(1.0, (width_score * 0.35) + (height_score * 0.35) + (area_score * 0.30)))

    def _line_alignment_score(self, projected_roi: Dict[str, float], adaptive_roi: Dict[str, float]) -> float:
        _, projected_y = self._center(projected_roi)
        _, adaptive_y = self._center(adaptive_roi)
        projected_height = max(projected_roi["height_ratio"], 1e-6)
        normalized_delta = abs(projected_y - adaptive_y) / projected_height
        return max(0.0, min(1.0, 1.0 - normalized_delta))

    def _coverage(self, adaptive_roi: Dict[str, float], search_region: Dict[str, float]) -> float:
        adaptive_area = adaptive_roi["width_ratio"] * adaptive_roi["height_ratio"]
        search_area = max(1e-6, search_region["width_ratio"] * search_region["height_ratio"])
        return max(0.0, min(1.0, adaptive_area / search_area))

    def validate_adaptive_roi(
        self,
        projected_roi: Dict[str, float],
        search_region: Dict[str, float],
        adaptive_roi: Dict[str, float],
        word_group: Dict[str, Any],
    ) -> Dict[str, Any]:
        width = adaptive_roi["width_ratio"]
        height = adaptive_roi["height_ratio"]
        word_count = int(word_group.get("word_count") or 0)
        ocr_confidence = float(word_group.get("ocr_confidence") or 0.0)
        coverage = self._coverage(adaptive_roi, search_region)
        projected_area = max(1e-6, projected_roi["width_ratio"] * projected_roi["height_ratio"])
        adaptive_area = width * height
        validation_errors: List[str] = []
        validation_warnings: List[str] = []

        if not self._contains(search_region, adaptive_roi):
            validation_errors.append("outside_search_region")
        if width < self.MIN_SIZE_RATIO or height < self.MIN_SIZE_RATIO:
            validation_errors.append("roi_too_small")
        if adaptive_area > projected_area * self.MAX_EXPANSION_RATIO:
            validation_errors.append("roi_too_large")
        if word_count <= 0:
            validation_errors.append("word_count_zero")
        if ocr_confidence < self.MIN_CONFIDENCE:
            validation_warnings.append("ocr_confidence_low")
        if coverage < self.MIN_COVERAGE:
            validation_errors.append("coverage_too_low")
        if coverage > self.MAX_SEARCH_COVERAGE:
            validation_errors.append("coverage_too_high")

        distance_score = self._distance_score(projected_roi, adaptive_roi)
        overlap_score = self._overlap_ratio(projected_roi, adaptive_roi)
        iou_score = self._iou(projected_roi, adaptive_roi)
        size_similarity = self._size_similarity(projected_roi, adaptive_roi)
        line_alignment_score = self._line_alignment_score(projected_roi, adaptive_roi)
        word_count_score = min(1.0, word_count / 3)
        adaptive_confidence = (
            ocr_confidence * 0.20
            + min(1.0, coverage / 0.35) * 0.08
            + word_count_score * 0.07
            + distance_score * 0.20
            + overlap_score * 0.20
            + iou_score * 0.10
            + size_similarity * 0.10
            + line_alignment_score * 0.05
        )
        adaptive_confidence = max(0.0, min(1.0, adaptive_confidence))
        if adaptive_confidence < self.MIN_ADAPTIVE_CONFIDENCE:
            validation_errors.append("adaptive_confidence_too_low")

        return {
            "passed": not validation_errors,
            "errors": validation_errors,
            "warnings": validation_warnings,
            "word_count": word_count,
            "ocr_confidence": round(ocr_confidence, 4),
            "coverage": round(coverage, 4),
            "position_similarity": round(distance_score, 4),
            "distance_score": round(distance_score, 4),
            "overlap_score": round(overlap_score, 4),
            "iou_score": round(iou_score, 4),
            "size_similarity": round(size_similarity, 4),
            "line_alignment_score": round(line_alignment_score, 4),
            "adaptive_confidence": round(adaptive_confidence, 4),
        }

    def _rank_group(
        self,
        projected_roi: Dict[str, float],
        search_region: Dict[str, float],
        group: Dict[str, Any],
    ) -> Dict[str, Any]:
        validation = self.validate_adaptive_roi(projected_roi, search_region, group["bbox"], group)
        distance_score = float(validation.get("distance_score") or 0.0)
        overlap_score = float(validation.get("overlap_score") or 0.0)
        iou_score = float(validation.get("iou_score") or 0.0)
        size_similarity = float(validation.get("size_similarity") or 0.0)
        line_alignment_score = float(validation.get("line_alignment_score") or 0.0)
        ocr_confidence = float(validation.get("ocr_confidence") or 0.0)
        word_count_score = min(1.0, float(validation.get("word_count") or 0) / 3)
        rank_score = (
            distance_score * 0.25
            + overlap_score * 0.25
            + iou_score * 0.15
            + size_similarity * 0.15
            + line_alignment_score * 0.10
            + ocr_confidence * 0.05
            + word_count_score * 0.05
        )
        return {
            "group": group,
            "validation": validation,
            "rank_score": round(max(0.0, min(1.0, rank_score)), 4),
        }

    def _select_group(
        self,
        projected_roi: Dict[str, float],
        search_region: Dict[str, float],
        groups: List[Dict[str, Any]],
    ) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
        ranked = [self._rank_group(projected_roi, search_region, group) for group in groups]
        ranked = sorted(
            ranked,
            key=lambda item: (
                bool(item["validation"].get("passed")),
                float(item["validation"].get("adaptive_confidence") or 0.0),
                float(item["rank_score"] or 0.0),
            ),
            reverse=True,
        )
        selected = next((item for item in ranked if item["validation"].get("passed")), None)
        return selected, ranked

    def refine_field(
        self,
        projected_roi: Dict[str, Any],
        word_boxes: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        search_region = self.expand_roi(projected_roi)
        search_region_with_page = {
            "page_number": projected_roi.get("page_number"),
            **search_region,
        }
        candidates = [box for box in word_boxes if self.intersects(box["bbox"], search_region)]
        if not candidates:
            return {
                "status": "fallback",
                "adaptive_roi": projected_roi,
                "search_region": search_region_with_page,
                "word_boxes": [],
                "word_groups": [],
                "fallback_reason": "no_word_boxes_in_search_region",
                "validation_result": {"passed": False, "errors": ["no_word_boxes_in_search_region"]},
                "adaptive_confidence": 0.0,
                "word_count": 0,
                "coverage": 0.0,
                "ocr_confidence": 0.0,
            }

        groups = self.group_word_boxes(candidates)
        if not groups:
            return {
                "status": "fallback",
                "adaptive_roi": projected_roi,
                "search_region": search_region_with_page,
                "word_boxes": candidates,
                "word_groups": [],
                "fallback_reason": "word_grouping_failed",
                "validation_result": {"passed": False, "errors": ["word_grouping_failed"]},
                "adaptive_confidence": 0.0,
                "word_count": 0,
                "coverage": 0.0,
                "ocr_confidence": 0.0,
            }

        selected_candidate, ranked_groups = self._select_group(projected_roi, search_region, groups)
        if not selected_candidate:
            best_candidate = ranked_groups[0] if ranked_groups else None
            validation = best_candidate["validation"] if best_candidate else {"passed": False, "errors": ["no_valid_word_group"]}
            return {
                "status": "fallback",
                "adaptive_roi": projected_roi,
                "search_region": search_region_with_page,
                "word_boxes": candidates,
                "word_groups": groups,
                "ranked_word_groups": ranked_groups,
                "fallback_reason": ",".join(validation.get("errors") or ["no_valid_word_group"]),
                "validation_result": validation,
                "adaptive_confidence": validation.get("adaptive_confidence", 0.0),
                "word_count": validation.get("word_count", 0),
                "coverage": validation.get("coverage", 0.0),
                "ocr_confidence": validation.get("ocr_confidence", 0.0),
            }

        selected_group = selected_candidate["group"]
        adaptive_roi = {
            "page_number": projected_roi.get("page_number"),
            **selected_group["bbox"],
        }
        validation = selected_candidate["validation"]

        return {
            "status": "refined",
            "adaptive_roi": adaptive_roi,
            "search_region": search_region_with_page,
            "word_boxes": candidates,
            "word_groups": groups,
            "ranked_word_groups": ranked_groups,
            "fallback_reason": None,
            "validation_result": validation,
            "adaptive_confidence": validation["adaptive_confidence"],
            "word_count": validation["word_count"],
            "coverage": validation["coverage"],
            "ocr_confidence": validation["ocr_confidence"],
        }
