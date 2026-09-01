import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import cv2
import numpy as np


class ImageNormalizationService:
    LONGEST_SIDE = 1024
    DETECTION_HEIGHT = 900
    MIN_CONTOUR_AREA_RATIO = 0.06
    MAX_CONTOUR_AREA_RATIO = 0.98
    MIN_ASPECT_RATIO = 0.20
    MAX_ASPECT_RATIO = 5.0
    MIN_TRANSFORMED_DIMENSION = 80
    MIN_TRANSFORMED_AREA_RATIO = 0.03
    MAX_TRANSFORMED_ASPECT_RATIO = 8.0
    MIN_IMAGE_STDDEV = 3.0
    LAYOUT_CROP_MIN_REGIONS = 3
    LAYOUT_CROP_MIN_CONTENT_AREA_RATIO = 0.025
    LAYOUT_CROP_PADDING_X_RATIO = 0.16
    LAYOUT_CROP_PADDING_TOP_RATIO = 0.24
    LAYOUT_CROP_PADDING_BOTTOM_RATIO = 0.24

    def normalize_document(self, image_path: str, output_path: Optional[str] = None) -> Dict[str, Any]:
        source_path = Path(image_path)
        if output_path is None:
            output_path = str(source_path.with_name(f"{source_path.stem}_normalized.png"))
        target_path = Path(output_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)

        image = cv2.imread(str(source_path))
        if image is None:
            raise ValueError(f"Unable to read image for normalization: {image_path}")

        original_height, original_width = image.shape[:2]
        bypass_requested = os.getenv("IMAGE_NORMALIZATION_BYPASS", "").strip().lower() in {"1", "true", "yes"}
        if bypass_requested:
            normalized = image.copy()
            debug = {
                "document_detected": False,
                "crop_applied": False,
                "perspective_applied": False,
                "normalization_status": "bypassed",
                "validation_passed": True,
                "fallback_used": True,
                "fallback_reason": "normalization_bypassed_by_env",
                "original_size": [original_width, original_height],
                "detected_contour_area": None,
                "contour_area_ratio": None,
                "contour_source": None,
                "contour_score": None,
                "contour_aspect_ratio": None,
                "contour_center_score": None,
                "detected_points": None,
                "transform_validation": {
                    "passed": True,
                    "reason": "normalization_bypassed_by_env",
                    "width": original_width,
                    "height": original_height,
                },
            }
        else:
            normalized, debug = self._perspective_correct(image)
            if debug.get("normalization_status") == "fallback":
                layout_normalized, layout_debug = self._layout_assisted_crop(image, debug.get("fallback_reason"))
                if layout_debug.get("normalization_status") == "layout_cropped":
                    normalized = layout_normalized
                    debug = layout_debug
                else:
                    normalized = image.copy()
                    debug.update(
                        {
                            "layout_crop_attempted": True,
                            "layout_crop": layout_debug.get("layout_crop"),
                            "layout_crop_fallback_reason": layout_debug.get("fallback_reason"),
                        }
                    )

        normalized = self._resize_longest_side(normalized, self.LONGEST_SIDE)
        normalized_height, normalized_width = normalized.shape[:2]
        debug.update(
            {
                "original_size": [original_width, original_height],
                "normalized_size": [normalized_width, normalized_height],
                "output_size": [normalized_width, normalized_height],
            }
        )

        write_success = bool(cv2.imwrite(str(target_path), normalized))
        decoded_output = cv2.imread(str(target_path)) if write_success else None
        if not write_success or decoded_output is None:
            cv2.imwrite(str(target_path), image)
            debug.update(
                {
                    "normalization_status": "fallback",
                    "validation_passed": False,
                    "fallback_used": True,
                    "fallback_reason": "output_decode_failed",
                }
            )

        return {
            "normalized_image_path": str(target_path),
            "document_detected": debug["document_detected"],
            "crop_applied": debug["crop_applied"],
            "perspective_applied": debug["perspective_applied"],
            "normalization_status": debug["normalization_status"],
            "validation_passed": debug["validation_passed"],
            "fallback_used": debug["fallback_used"],
            "fallback_reason": debug["fallback_reason"],
            "original_size": [original_width, original_height],
            "normalized_size": [normalized_width, normalized_height],
            "output_size": debug["output_size"],
            "resize_policy": "longest_side",
            "longest_side": self.LONGEST_SIDE,
            "normalization_debug": debug,
        }

    def _perspective_correct(self, image: np.ndarray) -> tuple[np.ndarray, Dict[str, Any]]:
        contour_info = self._find_document_contour(image)
        contour = contour_info.get("contour") if contour_info else None
        debug = {
            "document_detected": False,
            "crop_applied": False,
            "perspective_applied": False,
            "normalization_status": "fallback",
            "validation_passed": True,
            "fallback_used": True,
            "fallback_reason": contour_info.get("fallback_reason") if contour_info else "no_document_contour_found",
            "original_size": [image.shape[1], image.shape[0]],
            "detected_contour_area": contour_info.get("area") if contour_info else None,
            "contour_area_ratio": contour_info.get("area_ratio") if contour_info else None,
            "contour_source": contour_info.get("source") if contour_info else None,
            "contour_score": contour_info.get("score") if contour_info else None,
            "contour_aspect_ratio": contour_info.get("aspect_ratio") if contour_info else None,
            "contour_center_score": contour_info.get("center_score") if contour_info else None,
            "detected_points": contour_info.get("points") if contour_info else None,
            "normalized_size": None,
            "output_size": None,
        }
        if contour is None:
            return image.copy(), debug

        ordered = self._order_points(contour.reshape(4, 2).astype("float32"))
        top_left, top_right, bottom_right, bottom_left = ordered
        width_a = np.linalg.norm(bottom_right - bottom_left)
        width_b = np.linalg.norm(top_right - top_left)
        height_a = np.linalg.norm(top_right - bottom_right)
        height_b = np.linalg.norm(top_left - bottom_left)
        max_width = max(1, int(max(width_a, width_b)))
        max_height = max(1, int(max(height_a, height_b)))

        destination = np.array(
            [
                [0, 0],
                [max_width - 1, 0],
                [max_width - 1, max_height - 1],
                [0, max_height - 1],
            ],
            dtype="float32",
        )
        matrix = cv2.getPerspectiveTransform(ordered, destination)
        warped = cv2.warpPerspective(
            image,
            matrix,
            (max_width, max_height),
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=(255, 255, 255),
        )
        validation = self._validate_transformed_image(warped, image)
        if not validation["passed"]:
            debug.update(
                {
                    "validation_passed": False,
                    "fallback_used": True,
                    "fallback_reason": validation["reason"],
                    "normalization_status": "fallback",
                    "transform_validation": validation,
                }
            )
            return image.copy(), debug

        debug.update(
            {
                "document_detected": True,
                "crop_applied": True,
                "perspective_applied": True,
                "normalization_status": "cropped",
                "validation_passed": True,
                "fallback_used": False,
                "fallback_reason": None,
                "detected_points": [[round(float(x), 2), round(float(y), 2)] for x, y in ordered.tolist()],
                "warped_size": [max_width, max_height],
                "transform_validation": validation,
            }
        )
        return warped, debug

    def _find_document_contour(self, image: np.ndarray) -> Dict[str, Any]:
        resized = self._resize_to_height(image, self.DETECTION_HEIGHT)
        ratio = image.shape[0] / float(resized.shape[0])
        gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
        contour_masks = []

        threshold = cv2.adaptiveThreshold(
            blurred,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            21,
            7,
        )
        threshold_inv = cv2.bitwise_not(threshold)
        threshold_inv = cv2.morphologyEx(threshold_inv, cv2.MORPH_CLOSE, kernel, iterations=2)
        contour_masks.append(("adaptive_threshold", threshold_inv))

        edges = cv2.Canny(blurred, 40, 140)
        edges = cv2.dilate(edges, kernel, iterations=1)
        edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel, iterations=2)
        contour_masks.append(("canny", edges))

        foreground = self._foreground_document_mask(resized, blurred)
        contour_masks.append(("foreground_mask", foreground))

        image_area = resized.shape[0] * resized.shape[1]
        image_center = np.array([resized.shape[1] / 2.0, resized.shape[0] / 2.0], dtype="float32")
        max_center_distance = np.linalg.norm(image_center)
        candidates = []
        for source, mask in contour_masks:
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:20]:
                candidate = self._quadrilateral_candidate(contour, image_area, image_center, max_center_distance, source)
                if candidate:
                    candidates.append(candidate)

        if not candidates:
            return {"contour": None, "fallback_reason": "no_document_contour_found"}

        best = max(candidates, key=lambda item: item["score"])
        scaled = (best["points_array"] * ratio).astype("float32").reshape(4, 1, 2)
        return {
            "contour": scaled,
            "area": round(float(best["area"] * (ratio ** 2)), 2),
            "area_ratio": round(float(best["area_ratio"]), 4),
            "points": [[round(float(x * ratio), 2), round(float(y * ratio), 2)] for x, y in best["points"]],
            "source": best["source"],
            "score": round(float(best["score"]), 4),
            "aspect_ratio": round(float(best["aspect_ratio"]), 4),
            "center_score": round(float(best["center_score"]), 4),
            "fallback_reason": None,
        }

    def _quadrilateral_candidate(
        self,
        contour: np.ndarray,
        image_area: int,
        image_center: np.ndarray,
        max_center_distance: float,
        source: str,
    ) -> Optional[Dict[str, Any]]:
        area = cv2.contourArea(contour)
        area_ratio = area / max(1, image_area)
        if area_ratio < self.MIN_CONTOUR_AREA_RATIO or area_ratio > self.MAX_CONTOUR_AREA_RATIO:
            return None

        perimeter = cv2.arcLength(contour, True)
        if perimeter <= 0:
            return None

        approx = None
        for epsilon_ratio in (0.015, 0.02, 0.03, 0.04, 0.06):
            candidate = cv2.approxPolyDP(contour, epsilon_ratio * perimeter, True)
            if len(candidate) == 4:
                approx = candidate
                break
        if approx is None:
            approx = self._min_area_rect_candidate(contour, area)
            if approx is None:
                return None

        ordered = self._order_points(approx.reshape(4, 2).astype("float32"))
        top_left, top_right, bottom_right, bottom_left = ordered
        width_a = np.linalg.norm(bottom_right - bottom_left)
        width_b = np.linalg.norm(top_right - top_left)
        height_a = np.linalg.norm(top_right - bottom_right)
        height_b = np.linalg.norm(top_left - bottom_left)
        width = max(width_a, width_b)
        height = max(height_a, height_b)
        if width <= 1 or height <= 1:
            return None

        aspect_ratio = width / height
        if aspect_ratio < self.MIN_ASPECT_RATIO or aspect_ratio > self.MAX_ASPECT_RATIO:
            return None

        contour_center = ordered.mean(axis=0)
        center_distance = np.linalg.norm(contour_center - image_center)
        center_score = 1.0 - min(1.0, center_distance / max(1.0, max_center_distance))
        area_score = min(1.0, area_ratio / 0.75)
        score = (0.65 * area_score) + (0.35 * center_score)

        return {
            "points_array": ordered,
            "points": ordered.tolist(),
            "area": area,
            "area_ratio": area_ratio,
            "aspect_ratio": aspect_ratio,
            "center_score": center_score,
            "source": source,
            "score": score,
        }

    def _foreground_document_mask(self, image: np.ndarray, blurred_gray: np.ndarray) -> np.ndarray:
        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
        saturation = hsv[:, :, 1]
        sat_mask = cv2.inRange(saturation, 18, 255)

        edges = cv2.Canny(blurred_gray, 30, 120)
        edges = cv2.dilate(edges, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)), iterations=1)

        mask = cv2.bitwise_or(sat_mask, edges)
        small_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, small_kernel, iterations=1)

        height, width = image.shape[:2]
        close_w = max(15, int(width * 0.045) | 1)
        close_h = max(11, int(height * 0.035) | 1)
        close_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (close_w, close_h))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, close_kernel, iterations=2)
        mask = cv2.dilate(mask, close_kernel, iterations=1)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, close_kernel, iterations=1)
        return mask

    def _min_area_rect_candidate(self, contour: np.ndarray, contour_area: float) -> Optional[np.ndarray]:
        rect = cv2.minAreaRect(contour)
        (_, _), (rect_width, rect_height), _ = rect
        if rect_width <= 1 or rect_height <= 1:
            return None

        rect_area = rect_width * rect_height
        rectangularity = contour_area / max(1.0, rect_area)
        if rectangularity < 0.35:
            return None

        box = cv2.boxPoints(rect).astype("float32")
        return box.reshape(4, 1, 2)

    # def _validate_transformed_image(self, transformed: Optional[np.ndarray], original: np.ndarray) -> Dict[str, Any]:
    #     if transformed is None or not isinstance(transformed, np.ndarray):
    #         return {"passed": False, "reason": "perspective_transform_failed"}
    #     if transformed.size == 0:
    #         return {"passed": False, "reason": "perspective_transform_empty"}

    #     height, width = transformed.shape[:2]
    #     original_height, original_width = original.shape[:2]
    #     area_ratio = (width * height) / max(1, original_width * original_height)
    #     aspect_ratio = width / max(1, height)
    #     gray = cv2.cvtColor(transformed, cv2.COLOR_BGR2GRAY) if len(transformed.shape) == 3 else transformed
    #     stddev = float(gray.std())
    #     validation = {
    #         "passed": True,
    #         "reason": None,
    #         "width": width,
    #         "height": height,
    #         "area_ratio": round(float(area_ratio), 4),
    #         "aspect_ratio": round(float(aspect_ratio), 4),
    #         "stddev": round(stddev, 4),
    #     }

    #     if width < self.MIN_TRANSFORMED_DIMENSION or height < self.MIN_TRANSFORMED_DIMENSION:
    #         validation.update({"passed": False, "reason": "transformed_image_too_small"})
    #     elif aspect_ratio > self.MAX_TRANSFORMED_ASPECT_RATIO or aspect_ratio < (1 / self.MAX_TRANSFORMED_ASPECT_RATIO):
    #         validation.update({"passed": False, "reason": "transformed_aspect_ratio_invalid"})
    #     elif area_ratio < self.MIN_TRANSFORMED_AREA_RATIO:
    #         validation.update({"passed": False, "reason": "transformed_area_too_small"})
    #     elif stddev < self.MIN_IMAGE_STDDEV:
    #         validation.update({"passed": False, "reason": "transformed_image_nearly_empty"})
    #     return validation
    
    def _validate_transformed_image(
    self,
    transformed: Optional[np.ndarray],
    original: np.ndarray,) -> Dict[str, Any]:

        if transformed is None or not isinstance(transformed, np.ndarray):
            return {"passed": False, "reason": "perspective_transform_failed"}

        if transformed.size == 0:
            return {"passed": False, "reason": "perspective_transform_empty"}

        height, width = transformed.shape[:2]
        original_height, original_width = original.shape[:2]

        area_ratio = (width * height) / max(
            1,
            original_width * original_height,
        )

        aspect_ratio = width / max(1, height)
        original_aspect_ratio = original_width / max(1, original_height)

        aspect_change_ratio = max(
            aspect_ratio / original_aspect_ratio,
            original_aspect_ratio / aspect_ratio,
        )

        gray = (
            cv2.cvtColor(transformed, cv2.COLOR_BGR2GRAY)
            if len(transformed.shape) == 3
            else transformed
        )

        stddev = float(gray.std())

        validation = {
            "passed": True,
            "reason": None,
            "width": width,
            "height": height,
            "area_ratio": round(float(area_ratio), 4),
            "aspect_ratio": round(float(aspect_ratio), 4),
            "original_aspect_ratio": round(float(original_aspect_ratio), 4),
            "aspect_change_ratio": round(float(aspect_change_ratio), 4),
            "stddev": round(stddev, 4),
        }

        if (
            width < self.MIN_TRANSFORMED_DIMENSION
            or height < self.MIN_TRANSFORMED_DIMENSION
        ):
            validation.update(
                {
                    "passed": False,
                    "reason": "transformed_image_too_small",
                }
            )

        elif (
            aspect_ratio > self.MAX_TRANSFORMED_ASPECT_RATIO
            or aspect_ratio < (1 / self.MAX_TRANSFORMED_ASPECT_RATIO)
        ):
            validation.update(
                {
                    "passed": False,
                    "reason": "transformed_aspect_ratio_invalid",
                }
            )

        elif aspect_change_ratio > 1.35:
            validation.update(
                {
                    "passed": False,
                    "reason": "transformed_aspect_ratio_changed_too_much",
                }
            )

        elif area_ratio < self.MIN_TRANSFORMED_AREA_RATIO:
            validation.update(
                {
                    "passed": False,
                    "reason": "transformed_area_too_small",
                }
            )

        elif stddev < self.MIN_IMAGE_STDDEV:
            validation.update(
                {
                    "passed": False,
                    "reason": "transformed_image_nearly_empty",
                }
            )

        return validation

    def _layout_assisted_crop(self, image: np.ndarray, previous_fallback_reason: Optional[str] = None) -> tuple[np.ndarray, Dict[str, Any]]:
        height, width = image.shape[:2]
        debug: Dict[str, Any] = {
            "document_detected": False,
            "crop_applied": False,
            "perspective_applied": False,
            "normalization_status": "fallback",
            "validation_passed": False,
            "fallback_used": True,
            "fallback_reason": "layout_crop_not_attempted",
            "previous_fallback_reason": previous_fallback_reason,
            "original_size": [width, height],
            "detected_contour_area": None,
            "contour_area_ratio": None,
            "contour_source": "paddle_layout",
            "contour_score": None,
            "contour_aspect_ratio": None,
            "contour_center_score": None,
            "detected_points": None,
            "layout_crop_attempted": True,
            "layout_crop": None,
        }

        try:
            from .layout_analysis_service import analyze_layout

            analysis = analyze_layout(image, expand_text_rois=False)
        except Exception as error:
            debug.update(
                {
                    "fallback_reason": "layout_analysis_unavailable",
                    "layout_crop": {"error": str(error)},
                }
            )
            return image.copy(), debug

        boxes = self._layout_region_boxes(analysis, width, height)
        if len(boxes) < self.LAYOUT_CROP_MIN_REGIONS:
            debug.update(
                {
                    "fallback_reason": "insufficient_layout_regions",
                    "layout_crop": {
                        "region_count": len(boxes),
                        "minimum_regions": self.LAYOUT_CROP_MIN_REGIONS,
                    },
                }
            )
            return image.copy(), debug

        left = min(box[0] for box in boxes)
        top = min(box[1] for box in boxes)
        right = max(box[2] for box in boxes)
        bottom = max(box[3] for box in boxes)
        content_width = max(1.0, right - left)
        content_height = max(1.0, bottom - top)
        content_area_ratio = (content_width * content_height) / max(1.0, width * height)
        if content_area_ratio < self.LAYOUT_CROP_MIN_CONTENT_AREA_RATIO:
            debug.update(
                {
                    "fallback_reason": "layout_content_area_too_small",
                    "layout_crop": {
                        "region_count": len(boxes),
                        "content_box": [round(left, 2), round(top, 2), round(right, 2), round(bottom, 2)],
                        "content_area_ratio": round(float(content_area_ratio), 4),
                    },
                }
            )
            return image.copy(), debug

        pad_left = max(width * 0.015, content_width * self.LAYOUT_CROP_PADDING_X_RATIO)
        pad_right = max(width * 0.015, content_width * self.LAYOUT_CROP_PADDING_X_RATIO)
        pad_top = max(height * 0.015, content_height * self.LAYOUT_CROP_PADDING_TOP_RATIO)
        pad_bottom = max(height * 0.015, content_height * self.LAYOUT_CROP_PADDING_BOTTOM_RATIO)
        crop_left = int(max(0, np.floor(left - pad_left)))
        crop_top = int(max(0, np.floor(top - pad_top)))
        crop_right = int(min(width, np.ceil(right + pad_right)))
        crop_bottom = int(min(height, np.ceil(bottom + pad_bottom)))

        if crop_right <= crop_left or crop_bottom <= crop_top:
            debug.update(
                {
                    "fallback_reason": "layout_crop_zero_area",
                    "layout_crop": {
                        "region_count": len(boxes),
                        "content_box": [round(left, 2), round(top, 2), round(right, 2), round(bottom, 2)],
                        "expanded_box": [crop_left, crop_top, crop_right, crop_bottom],
                    },
                }
            )
            return image.copy(), debug

        cropped = image[crop_top:crop_bottom, crop_left:crop_right].copy()
        validation = self._validate_transformed_image(cropped, image)
        layout_crop_debug = {
            "region_count": len(boxes),
            "content_box": [round(left, 2), round(top, 2), round(right, 2), round(bottom, 2)],
            "expanded_box": [crop_left, crop_top, crop_right, crop_bottom],
            "content_area_ratio": round(float(content_area_ratio), 4),
            "padding": {
                "left": round(float(pad_left), 2),
                "right": round(float(pad_right), 2),
                "top": round(float(pad_top), 2),
                "bottom": round(float(pad_bottom), 2),
            },
            "source": "paddle_layout_regions",
        }
        if not validation["passed"]:
            debug.update(
                {
                    "fallback_reason": validation["reason"],
                    "transform_validation": validation,
                    "layout_crop": layout_crop_debug,
                }
            )
            return image.copy(), debug

        debug.update(
            {
                "document_detected": True,
                "crop_applied": True,
                "perspective_applied": False,
                "normalization_status": "layout_cropped",
                "validation_passed": True,
                "fallback_used": False,
                "fallback_reason": None,
                "detected_contour_area": round(float((crop_right - crop_left) * (crop_bottom - crop_top)), 2),
                "contour_area_ratio": round(float(((crop_right - crop_left) * (crop_bottom - crop_top)) / max(1, width * height)), 4),
                "contour_aspect_ratio": round(float((crop_right - crop_left) / max(1, crop_bottom - crop_top)), 4),
                "detected_points": [
                    [float(crop_left), float(crop_top)],
                    [float(crop_right), float(crop_top)],
                    [float(crop_right), float(crop_bottom)],
                    [float(crop_left), float(crop_bottom)],
                ],
                "warped_size": [crop_right - crop_left, crop_bottom - crop_top],
                "transform_validation": validation,
                "layout_crop": layout_crop_debug,
            }
        )
        return cropped, debug

    def _layout_region_boxes(self, analysis: Dict[str, Any], image_width: int, image_height: int) -> List[List[float]]:
        regions = analysis.get("regions") if isinstance(analysis, dict) else None
        if not isinstance(regions, list):
            return []

        boxes: List[List[float]] = []
        image_area = max(1.0, float(image_width * image_height))
        for region in regions:
            if not isinstance(region, dict):
                continue
            roi = region.get("roi") if isinstance(region.get("roi"), dict) else {}
            try:
                x = float(roi.get("x_ratio") or 0.0) * image_width
                y = float(roi.get("y_ratio") or 0.0) * image_height
                box_width = float(roi.get("width_ratio") or 0.0) * image_width
                box_height = float(roi.get("height_ratio") or 0.0) * image_height
            except (TypeError, ValueError):
                continue
            if box_width <= 2 or box_height <= 2:
                continue
            if (box_width * box_height) / image_area < 0.00008:
                continue
            left = max(0.0, min(float(image_width), x))
            top = max(0.0, min(float(image_height), y))
            right = max(0.0, min(float(image_width), x + box_width))
            bottom = max(0.0, min(float(image_height), y + box_height))
            if right > left and bottom > top:
                boxes.append([left, top, right, bottom])
        return boxes

    def _resize_to_height(self, image: np.ndarray, height: int) -> np.ndarray:
        original_height, original_width = image.shape[:2]
        if original_height <= height:
            return image.copy()
        scale = height / float(original_height)
        width = max(1, int(original_width * scale))
        return cv2.resize(image, (width, height), interpolation=cv2.INTER_AREA)

    def _resize_longest_side(self, image: np.ndarray, longest_side: int) -> np.ndarray:
        height, width = image.shape[:2]
        current_longest = max(width, height)
        if current_longest == 0:
            return image
        scale = longest_side / float(current_longest)
        if scale == 1:
            return image.copy()
        new_width = max(1, int(round(width * scale)))
        new_height = max(1, int(round(height * scale)))
        interpolation = cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC
        return cv2.resize(image, (new_width, new_height), interpolation=interpolation)

    def _order_points(self, points: np.ndarray) -> np.ndarray:
        rect = np.zeros((4, 2), dtype="float32")
        point_sum = points.sum(axis=1)
        point_diff = np.diff(points, axis=1)
        rect[0] = points[np.argmin(point_sum)]
        rect[2] = points[np.argmax(point_sum)]
        rect[1] = points[np.argmin(point_diff)]
        rect[3] = points[np.argmax(point_diff)]
        return rect
