import re
import unicodedata
import logging
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

from .adaptive_roi_service import AdaptiveRoiService
from .layout_analysis_service import LayoutAnalysisUnavailableError, detect_text_boxes
from .paddle_thai_ocr_adapter import PaddleThaiOcrUnavailableError, run_paddle_thai_ocr


Point = Tuple[float, float]
LOGGER = logging.getLogger(__name__)


class AnchorProjectionService:
    MIN_TEXT_SIMILARITY = 0.70
    MAX_REPROJECTION_ERROR_RATIO = 0.08
    MIN_PROJECTED_SIZE_RATIO = 1e-6
    MIN_ANCHOR_SPREAD_RATIO = 0.025
    MIN_TRANSFORM_SCALE = 0.25
    MAX_TRANSFORM_SCALE = 4.0

    ZERO_WIDTH_CHARS = {
        "\u200b",
        "\u200c",
        "\u200d",
        "\ufeff",
    }

    def __init__(self) -> None:
        self.adaptive_roi = AdaptiveRoiService()

    def _normalize_text(self, value: Optional[str]) -> str:
        normalized = unicodedata.normalize("NFKC", value or "")
        for char in self.ZERO_WIDTH_CHARS:
            normalized = normalized.replace(char, "")
        normalized = "".join(normalized.lower().split())
        return re.sub(r"[^\w]", "", normalized, flags=re.UNICODE)

    def _similarity(self, left: str, right: str) -> float:
        if not left and not right:
            return 1.0
        if not left or not right:
            return 0.0
        if left in right:
            return 1.0
        if right in left:
            return max(SequenceMatcher(None, left, right).ratio(), 0.9)
        return SequenceMatcher(None, left, right).ratio()

    def _template_anchor_center(self, roi: Dict[str, Any]) -> Point:
        return (
            float(roi.get("x_ratio", 0.0) or 0.0) + float(roi.get("width_ratio", 0.0) or 0.0) / 2,
            float(roi.get("y_ratio", 0.0) or 0.0) + float(roi.get("height_ratio", 0.0) or 0.0) / 2,
        )

    def _roi_corners(self, roi: Dict[str, Any]) -> np.ndarray:
        x = float(roi.get("x_ratio", 0.0) or 0.0)
        y = float(roi.get("y_ratio", 0.0) or 0.0)
        width = float(roi.get("width_ratio", 0.0) or 0.0)
        height = float(roi.get("height_ratio", 0.0) or 0.0)
        return np.array(
            [
                [x, y],
                [x + width, y],
                [x + width, y + height],
                [x, y + height],
            ],
            dtype=np.float32,
        )

    def _locate_text_anchors(
        self,
        anchors: List[Dict[str, Any]],
        page_image_paths: Dict[int, str],
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        matched: List[Dict[str, Any]] = []
        diagnostics: List[Dict[str, Any]] = []
        ocr_cache: Dict[int, Dict[str, Any]] = {}

        for anchor in anchors:
            expected = self._normalize_text(anchor.get("expected_text"))
            page_number = int(anchor.get("page_number") or 1)
            roi = anchor.get("roi") or {}
            if not expected:
                diagnostics.append(
                    {
                        "anchor_id": anchor.get("id"),
                        "type": "text",
                        "matched": False,
                        "failure_reason": "expected_text_missing",
                        "page_number": page_number,
                    }
                )
                continue

            image_path = page_image_paths.get(page_number)
            if not image_path:
                diagnostics.append(
                    {
                        "anchor_id": anchor.get("id"),
                        "type": "text",
                        "matched": False,
                        "failure_reason": "query_page_missing",
                        "page_number": page_number,
                    }
                )
                continue

            try:
                if page_number not in ocr_cache:
                    page_detection = detect_text_boxes(image_path)
                    image = cv2.imread(image_path)
                    if image is None:
                        raise ValueError(f"Unable to read image: {image_path}")
                    regions = []
                    for region in page_detection.get("regions", []):
                        bbox = region.get("bbox") or {}
                        x = max(0, int(float(bbox.get("x") or 0)))
                        y = max(0, int(float(bbox.get("y") or 0)))
                        width = max(1, int(float(bbox.get("width") or 1)))
                        height = max(1, int(float(bbox.get("height") or 1)))
                        crop = image[y : min(image.shape[0], y + height), x : min(image.shape[1], x + width)]
                        if crop.size == 0:
                            continue
                        recognized = run_paddle_thai_ocr(crop)
                        regions.append(
                            {
                                **region,
                                "text": recognized.get("text") or "",
                                "confidence": recognized.get("confidence") or region.get("confidence") or 0.0,
                                "center": {
                                    "x": round(x + width / 2, 4),
                                    "y": round(y + height / 2, 4),
                                },
                            }
                        )
                    ocr_cache[page_number] = {**page_detection, "regions": regions}
                page_ocr = ocr_cache[page_number]
            except (LayoutAnalysisUnavailableError, PaddleThaiOcrUnavailableError, ValueError, RuntimeError) as error:
                diagnostics.append(
                    {
                        "anchor_id": anchor.get("id"),
                        "type": "text",
                        "matched": False,
                        "failure_reason": "paddle_ocr_unavailable",
                        "error": str(error),
                        "page_number": page_number,
                    }
                )
                continue

            image_width = max(1.0, float(page_ocr.get("image_width") or 1.0))
            image_height = max(1.0, float(page_ocr.get("image_height") or 1.0))
            best_region = None
            best_similarity = 0.0
            for region in page_ocr.get("regions", []):
                similarity = self._similarity(expected, self._normalize_text(region.get("text")))
                if similarity > best_similarity:
                    best_region = region
                    best_similarity = similarity

            if not best_region or best_similarity < self.MIN_TEXT_SIMILARITY:
                diagnostics.append(
                    {
                        "anchor_id": anchor.get("id"),
                        "type": "text",
                        "matched": False,
                        "similarity": round(best_similarity, 4),
                        "failure_reason": "text_anchor_not_found",
                        "page_number": page_number,
                    }
                )
                continue

            center = best_region["center"]
            actual_point = (float(center["x"]) / image_width, float(center["y"]) / image_height)
            expected_point = self._template_anchor_center(roi)
            bbox = best_region["bbox"]
            actual_bbox = {
                "x": round(float(bbox["x"]), 4),
                "y": round(float(bbox["y"]), 4),
                "width": round(float(bbox["width"]), 4),
                "height": round(float(bbox["height"]), 4),
            }
            matched.append(
                {
                    "anchor_id": anchor.get("id"),
                    "field_name": anchor.get("field_name"),
                    "display_label": anchor.get("display_label"),
                    "type": "text",
                    "page_number": page_number,
                    "similarity": round(float(best_similarity), 4),
                    "expected_point": expected_point,
                    "actual_point": actual_point,
                    "expected_bbox": roi,
                    "actual_bbox": actual_bbox,
                    "center": {
                        "x": round(float(center["x"]), 4),
                        "y": round(float(center["y"]), 4),
                    },
                    "actual_text": best_region.get("text"),
                    "ocr_confidence": best_region.get("confidence"),
                }
            )
            diagnostics.append({**matched[-1], "matched": True})

        return matched, diagnostics

    def _estimate_transform(self, pairs: List[Dict[str, Any]]) -> Tuple[str, Optional[np.ndarray], Dict[str, Any]]:
        if not pairs:
            return "ratio_fallback", None, {"fallback_reason": "no_projection_anchors_matched"}

        src = np.array([pair["expected_point"] for pair in pairs], dtype=np.float32)
        dst = np.array([pair["actual_point"] for pair in pairs], dtype=np.float32)
        debug: Dict[str, Any] = {"anchors_matched": len(pairs)}
        translation_matrix = self._translation_matrix(src, dst)

        if len(pairs) >= 4:
            matrix, mask = cv2.findHomography(src, dst, cv2.RANSAC, 0.03)
            if matrix is None or not self._is_valid_homography(matrix):
                fallback_reason = "homography_not_found" if matrix is None else "homography_degenerate"
                matrix = translation_matrix
                method = "translation"
                projected = cv2.transform(src.reshape(-1, 1, 2), matrix).reshape(-1, 2)
                inliers = len(pairs)
                debug["fallback_reason"] = fallback_reason
            else:
                method = "homography"
                projected = cv2.perspectiveTransform(src.reshape(-1, 1, 2), matrix).reshape(-1, 2)
                inliers = int(mask.sum()) if mask is not None else len(pairs)
        elif len(pairs) >= 3:
            if self._point_spread(src) < self.MIN_ANCHOR_SPREAD_RATIO or self._point_spread(dst) < self.MIN_ANCHOR_SPREAD_RATIO:
                matrix = translation_matrix
                method = "translation"
                projected = cv2.transform(src.reshape(-1, 1, 2), matrix).reshape(-1, 2)
                inliers = len(pairs)
                debug["fallback_reason"] = "anchor_points_too_clustered_for_affine"
            else:
                matrix, inlier_mask = cv2.estimateAffine2D(src, dst, method=cv2.RANSAC, ransacReprojThreshold=0.03)
                if matrix is None:
                    matrix = cv2.getAffineTransform(src[:3], dst[:3])
                if matrix is None or not self._is_valid_affine(matrix):
                    matrix = translation_matrix
                    method = "translation"
                    debug["fallback_reason"] = "affine_degenerate"
                else:
                    method = "affine"
                projected = cv2.transform(src.reshape(-1, 1, 2), matrix).reshape(-1, 2)
                inliers = int(inlier_mask.sum()) if inlier_mask is not None else len(pairs)
        elif len(pairs) == 2:
            src_distance = float(np.linalg.norm(src[1] - src[0]))
            dst_distance = float(np.linalg.norm(dst[1] - dst[0]))
            if src_distance < self.MIN_ANCHOR_SPREAD_RATIO or dst_distance < self.MIN_ANCHOR_SPREAD_RATIO:
                matrix = translation_matrix
                method = "translation"
                debug["fallback_reason"] = "anchor_points_too_clustered_for_partial_affine"
            else:
                matrix, inlier_mask = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC, ransacReprojThreshold=0.03)
                if matrix is None or not self._is_valid_affine(matrix):
                    matrix = translation_matrix
                    method = "translation"
                    debug["fallback_reason"] = "partial_affine_degenerate"
                else:
                    method = "partial_affine"
            projected = cv2.transform(src.reshape(-1, 1, 2), matrix).reshape(-1, 2)
            inliers = len(pairs)
        else:
            matrix = translation_matrix
            method = "translation"
            projected = cv2.transform(src.reshape(-1, 1, 2), matrix).reshape(-1, 2)
            inliers = 1

        errors = np.linalg.norm(projected - dst, axis=1)
        reprojection_error = float(errors.mean()) if len(errors) else 0.0
        confidence = max(0.0, min(1.0, 1.0 - (reprojection_error / self.MAX_REPROJECTION_ERROR_RATIO)))
        debug.update(
            {
                "inliers": inliers,
                "reprojection_error": round(reprojection_error, 4),
                "projection_confidence": round(confidence, 4),
            }
        )
        if len(pairs) >= 2 and reprojection_error > self.MAX_REPROJECTION_ERROR_RATIO:
            return "ratio_fallback", None, {**debug, "fallback_reason": "reprojection_error_too_high"}
        return method, matrix, debug

    def _translation_matrix(self, src: np.ndarray, dst: np.ndarray) -> np.ndarray:
        delta = dst.mean(axis=0) - src.mean(axis=0)
        return np.array([[1.0, 0.0, delta[0]], [0.0, 1.0, delta[1]]], dtype=np.float32)

    def _point_spread(self, points: np.ndarray) -> float:
        if len(points) <= 1:
            return 0.0
        distances = []
        for left_index in range(len(points)):
            for right_index in range(left_index + 1, len(points)):
                distances.append(float(np.linalg.norm(points[left_index] - points[right_index])))
        return max(distances) if distances else 0.0

    def _is_valid_affine(self, matrix: Optional[np.ndarray]) -> bool:
        if matrix is None or matrix.shape != (2, 3) or not np.isfinite(matrix).all():
            return False
        linear = matrix[:, :2]
        determinant = abs(float(np.linalg.det(linear)))
        if determinant <= 1e-6:
            return False
        scale_x = float(np.linalg.norm(linear[:, 0]))
        scale_y = float(np.linalg.norm(linear[:, 1]))
        return (
            self.MIN_TRANSFORM_SCALE <= scale_x <= self.MAX_TRANSFORM_SCALE
            and self.MIN_TRANSFORM_SCALE <= scale_y <= self.MAX_TRANSFORM_SCALE
        )

    def _is_valid_homography(self, matrix: Optional[np.ndarray]) -> bool:
        if matrix is None or matrix.shape != (3, 3) or not np.isfinite(matrix).all():
            return False
        determinant = abs(float(np.linalg.det(matrix)))
        return determinant > 1e-8

    def _transform_points(self, points: np.ndarray, method: str, matrix: Optional[np.ndarray]) -> np.ndarray:
        if matrix is None:
            return points
        if method == "homography":
            return cv2.perspectiveTransform(points.reshape(-1, 1, 2), matrix).reshape(-1, 2)
        return cv2.transform(points.reshape(-1, 1, 2), matrix).reshape(-1, 2)

    def _bbox_from_polygon(self, polygon: np.ndarray) -> Dict[str, float]:
        min_x = float(polygon[:, 0].min())
        min_y = float(polygon[:, 1].min())
        max_x = float(polygon[:, 0].max())
        max_y = float(polygon[:, 1].max())
        return {
            "x_ratio": round(min_x, 6),
            "y_ratio": round(min_y, 6),
            "width_ratio": round(max_x - min_x, 6),
            "height_ratio": round(max_y - min_y, 6),
        }

    def _safe_template_roi_fallback(self, roi: Dict[str, Any]) -> Optional[Dict[str, float]]:
        try:
            x = max(0.0, min(1.0, float(roi.get("x_ratio") or 0.0)))
            y = max(0.0, min(1.0, float(roi.get("y_ratio") or 0.0)))
            width = max(0.0, min(1.0 - x, float(roi.get("width_ratio") or 0.0)))
            height = max(0.0, min(1.0 - y, float(roi.get("height_ratio") or 0.0)))
        except (TypeError, ValueError):
            return None
        if width <= self.MIN_PROJECTED_SIZE_RATIO or height <= self.MIN_PROJECTED_SIZE_RATIO:
            return None
        return {
            "x_ratio": round(x, 6),
            "y_ratio": round(y, 6),
            "width_ratio": round(width, 6),
            "height_ratio": round(height, 6),
        }

    def _validate_projected_polygon(
        self,
        raw_polygon: np.ndarray,
        clipped_polygon: np.ndarray,
    ) -> Dict[str, Any]:
        errors: List[str] = []
        warnings: List[str] = []

        if raw_polygon.shape != (4, 2):
            errors.append("polygon_invalid")
        if not np.isfinite(raw_polygon).all():
            errors.append("invalid_transform")
        if not np.isfinite(clipped_polygon).all():
            errors.append("invalid_transform")

        raw_bbox = self._bbox_from_polygon(raw_polygon) if np.isfinite(raw_polygon).all() and raw_polygon.size else {
            "x_ratio": 0.0,
            "y_ratio": 0.0,
            "width_ratio": 0.0,
            "height_ratio": 0.0,
        }
        clipped_bbox = self._bbox_from_polygon(clipped_polygon) if np.isfinite(clipped_polygon).all() and clipped_polygon.size else {
            "x_ratio": 0.0,
            "y_ratio": 0.0,
            "width_ratio": 0.0,
            "height_ratio": 0.0,
        }

        if raw_bbox["width_ratio"] < 0:
            errors.append("negative_width")
        if raw_bbox["height_ratio"] < 0:
            errors.append("negative_height")
        if clipped_bbox["width_ratio"] <= self.MIN_PROJECTED_SIZE_RATIO or clipped_bbox["height_ratio"] <= self.MIN_PROJECTED_SIZE_RATIO:
            errors.append("zero_area")

        outside_image = bool(
            np.isfinite(raw_polygon).all()
            and (
                (raw_polygon[:, 0] < 0).any()
                or (raw_polygon[:, 0] > 1).any()
                or (raw_polygon[:, 1] < 0).any()
                or (raw_polygon[:, 1] > 1).any()
            )
        )
        if outside_image:
            warnings.append("outside_image_clipped")

        blocking_errors = [error for error in errors if error not in {"outside_image"}]
        return {
            "passed": not blocking_errors,
            "errors": blocking_errors,
            "warnings": warnings,
            "outside_image": outside_image,
            "clipped": outside_image,
            "projected_roi_before_clip": raw_bbox,
            "projected_roi_after_clip": clipped_bbox,
        }

    def _project_field(self, field: Dict[str, Any], method: str, matrix: Optional[np.ndarray]) -> Dict[str, Any]:
        template_roi = field.get("roi") or {}
        corners = self._roi_corners(template_roi)
        raw_projected = self._transform_points(corners, method, matrix)
        clipped_projected = np.clip(raw_projected, 0.0, 1.0)
        validation = self._validate_projected_polygon(raw_projected, clipped_projected)
        clipped_bbox = validation["projected_roi_after_clip"]
        valid = bool(validation["passed"])
        fallback_roi = None
        if not valid:
            LOGGER.warning(
                "Projected ROI invalid for field %s: %s",
                field.get("id"),
                ",".join(validation.get("errors") or ["unknown"]),
            )
            fallback_roi = self._safe_template_roi_fallback(template_roi)
            if fallback_roi:
                clipped_bbox = fallback_roi
                validation = {
                    **validation,
                    "passed": True,
                    "original_errors": validation.get("errors", []),
                    "errors": [],
                    "warnings": [
                        *(validation.get("warnings") or []),
                        "invalid_projection_fallback_to_template_roi",
                    ],
                    "fallback_reason": "invalid_projection_fallback_to_template_roi",
                    "projected_roi_after_clip": clipped_bbox,
                }
                valid = True
        return {
            "field_id": field.get("id"),
            "field_name": field.get("field_name"),
            "display_label": field.get("display_label"),
            "page_number": field.get("page_number"),
            "template_roi": template_roi,
            "projected_polygon_before_clip": [[round(float(x), 6), round(float(y), 6)] for x, y in raw_projected.tolist()],
            "projected_polygon": [[round(float(x), 6), round(float(y), 6)] for x, y in clipped_projected.tolist()],
            "projected_roi_before_clip": {
                "page_number": field.get("page_number"),
                **validation["projected_roi_before_clip"],
            },
            "projected_roi": {
                "page_number": field.get("page_number"),
                **clipped_bbox,
            },
            "projection_method": method,
            "projection_valid": valid,
            "projection_validation_result": validation,
            "fallback_used": matrix is None or fallback_roi is not None,
            "projection_fallback_reason": "invalid_projection_fallback_to_template_roi" if fallback_roi is not None else None,
        }

    def _refine_projected_fields(
        self,
        projected_fields: List[Dict[str, Any]],
        source_fields: List[Dict[str, Any]],
        page_image_paths: Dict[int, str],
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        if not self.adaptive_roi.enabled:
            for projected in projected_fields:
                projected_roi = projected.get("projected_roi") or {}
                projected["adaptive_status"] = "disabled"
                projected["adaptive_roi"] = projected_roi
                projected["adaptive_search_region"] = None
                projected["adaptive_word_boxes"] = []
                projected["adaptive_word_groups"] = []
                projected["adaptive_ranked_word_groups"] = []
                projected["adaptive_fallback_reason"] = "disabled_by_config"
                projected["adaptive_confidence"] = 0.0
                projected["adaptive_word_count"] = 0
                projected["adaptive_coverage"] = 0.0
                projected["adaptive_ocr_confidence"] = 0.0
                projected["adaptive_validation_result"] = {"passed": False, "errors": ["disabled_by_config"]}
            return projected_fields, {
                "enabled": False,
                "reason": "disabled_by_config",
                "text_fields_refined": 0,
                "text_fields_fallback": 0,
                "average_adaptive_confidence": 0.0,
                "average_coverage": 0.0,
                "average_ocr_confidence": 0.0,
                "search_padding_ratio": self.adaptive_roi.search_padding_ratio,
            }

        fields_by_id = {field.get("id"): field for field in source_fields}
        ocr_cache: Dict[int, Dict[str, Any]] = {}
        refined_count = 0
        fallback_count = 0

        for projected in projected_fields:
            source = fields_by_id.get(projected.get("field_id")) or {}
            data_type = str(source.get("data_type") or "text").lower()
            extraction_method = str(source.get("extraction_method") or "ocr_text").lower()
            projected_roi = projected.get("projected_roi") or {}
            page_number = int(projected.get("page_number") or projected_roi.get("page_number") or 1)
            projected["adaptive_status"] = "not_applicable"
            projected["adaptive_roi"] = projected_roi
            projected["adaptive_search_region"] = None
            projected["adaptive_word_boxes"] = []
            projected["adaptive_fallback_reason"] = None

            if data_type in {"image", "table"} or extraction_method in {"extract_image", "ocr_table"}:
                continue
            if not projected.get("projection_valid", True):
                validation_result = projected.get("projection_validation_result") or {"passed": False, "errors": ["projected_roi_invalid"]}
                errors = validation_result.get("errors") if isinstance(validation_result, dict) else None
                reason = ",".join(errors) if isinstance(errors, list) and errors else "projected_roi_invalid"
                projected["adaptive_status"] = "fallback"
                projected["adaptive_fallback_reason"] = reason
                projected["adaptive_confidence"] = 0.0
                projected["adaptive_word_count"] = 0
                projected["adaptive_coverage"] = 0.0
                projected["adaptive_ocr_confidence"] = 0.0
                projected["adaptive_validation_result"] = validation_result
                fallback_count += 1
                continue

            image_path = page_image_paths.get(page_number)
            if not image_path:
                projected["adaptive_status"] = "fallback"
                projected["adaptive_fallback_reason"] = "query_page_missing"
                projected["adaptive_confidence"] = 0.0
                projected["adaptive_word_count"] = 0
                projected["adaptive_coverage"] = 0.0
                projected["adaptive_ocr_confidence"] = 0.0
                projected["adaptive_validation_result"] = {"passed": False, "errors": ["query_page_missing"]}
                fallback_count += 1
                continue

            try:
                if page_number not in ocr_cache:
                    ocr_cache[page_number] = detect_text_boxes(image_path)
                page_ocr = ocr_cache[page_number]
            except (LayoutAnalysisUnavailableError, ValueError, RuntimeError) as error:
                projected["adaptive_status"] = "fallback"
                projected["adaptive_fallback_reason"] = f"text_detection_unavailable: {error}"
                projected["adaptive_confidence"] = 0.0
                projected["adaptive_word_count"] = 0
                projected["adaptive_coverage"] = 0.0
                projected["adaptive_ocr_confidence"] = 0.0
                projected["adaptive_validation_result"] = {"passed": False, "errors": ["text_detection_unavailable"]}
                fallback_count += 1
                continue

            image_width = max(1.0, float(page_ocr.get("image_width") or 1.0))
            image_height = max(1.0, float(page_ocr.get("image_height") or 1.0))
            word_boxes = []
            for region in page_ocr.get("regions", []):
                word_boxes.append(
                    {
                        "text": region.get("text"),
                        "confidence": region.get("confidence"),
                        "bbox": self.adaptive_roi.bbox_to_ratio(region.get("bbox") or {}, image_width, image_height),
                    }
                )

            adaptive = self.adaptive_roi.refine_field(projected_roi, word_boxes)
            projected["adaptive_roi"] = adaptive["adaptive_roi"]
            projected["adaptive_search_region"] = adaptive["search_region"]
            projected["adaptive_word_boxes"] = adaptive["word_boxes"]
            projected["adaptive_word_groups"] = adaptive["word_groups"]
            projected["adaptive_ranked_word_groups"] = adaptive.get("ranked_word_groups", [])
            projected["adaptive_status"] = adaptive["status"]
            projected["adaptive_confidence"] = adaptive["adaptive_confidence"]
            projected["adaptive_word_count"] = adaptive["word_count"]
            projected["adaptive_coverage"] = adaptive["coverage"]
            projected["adaptive_ocr_confidence"] = adaptive["ocr_confidence"]
            projected["adaptive_validation_result"] = adaptive["validation_result"]
            projected["adaptive_fallback_reason"] = adaptive["fallback_reason"]
            if adaptive["status"] == "refined":
                refined_count += 1
            else:
                fallback_count += 1

        adaptive_fields = [field for field in projected_fields if field.get("adaptive_status") in {"refined", "fallback"}]
        confidence_values = [float(field.get("adaptive_confidence") or 0.0) for field in adaptive_fields]
        coverage_values = [float(field.get("adaptive_coverage") or 0.0) for field in adaptive_fields]
        ocr_confidence_values = [float(field.get("adaptive_ocr_confidence") or 0.0) for field in adaptive_fields]
        return projected_fields, {
            "enabled": True,
            "text_fields_refined": refined_count,
            "text_fields_fallback": fallback_count,
            "average_adaptive_confidence": round(sum(confidence_values) / len(confidence_values), 4) if confidence_values else 0.0,
            "average_coverage": round(sum(coverage_values) / len(coverage_values), 4) if coverage_values else 0.0,
            "average_ocr_confidence": round(sum(ocr_confidence_values) / len(ocr_confidence_values), 4) if ocr_confidence_values else 0.0,
            "search_padding_ratio": self.adaptive_roi.search_padding_ratio,
        }

    def refine_projected_fields(
        self,
        projected_fields: List[Dict[str, Any]],
        source_fields: List[Dict[str, Any]],
        page_image_paths: Dict[int, str],
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        return self._refine_projected_fields(projected_fields, source_fields, page_image_paths)

    def project(
        self,
        template_id: str,
        fields: List[Dict[str, Any]],
        page_image_paths: Dict[int, str],
    ) -> Dict[str, Any]:
        extraction_fields = [field for field in fields if not field.get("use_for_verification")]
        text_anchors = [
            field
            for field in fields
            if field.get("use_for_verification") and field.get("data_type") != "image"
        ]

        matched_anchors, anchor_diagnostics = self._locate_text_anchors(text_anchors, page_image_paths)
        method, matrix, transform_debug = self._estimate_transform(matched_anchors)
        projected_fields = [self._project_field(field, method, matrix) for field in extraction_fields]
        projected_fields, adaptive_debug = self._refine_projected_fields(projected_fields, extraction_fields, page_image_paths)

        invalid_fields = [field for field in projected_fields if not field.get("projection_valid")]
        status = "success" if matrix is not None and not invalid_fields else "fallback"
        fallback_reason = None
        if matrix is None:
            fallback_reason = transform_debug.get("fallback_reason") or "insufficient_projection_anchors"
        elif invalid_fields:
            status = "partial"
            fallback_reason = "some_projected_fields_invalid"

        confidence = float(transform_debug.get("projection_confidence") or (0.35 if method == "translation" else 0.0))
        return {
            "template_id": template_id,
            "status": status,
            "method": method,
            "anchors_expected": len(text_anchors),
            "anchors_matched": len(matched_anchors),
            "inliers": transform_debug.get("inliers", len(matched_anchors) if matrix is not None else 0),
            "reprojection_error": transform_debug.get("reprojection_error"),
            "confidence": round(confidence, 4),
            "fallback_reason": fallback_reason,
            "matched_anchors": anchor_diagnostics,
            "adaptive_refinement": adaptive_debug,
            "projected_fields": projected_fields,
        }
