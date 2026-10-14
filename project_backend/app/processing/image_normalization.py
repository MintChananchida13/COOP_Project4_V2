import os
import time
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
    LAYOUT_CROP_PADDING_X_RATIO = 0.08
    LAYOUT_CROP_PADDING_TOP_RATIO = 0.12
    LAYOUT_CROP_PADDING_BOTTOM_RATIO = 0.12
    PDF_SUBDOCUMENT_MIN_AREA_RATIO = 0.05
    PDF_SUBDOCUMENT_MAX_AREA_RATIO = 0.50
    PDF_SUBDOCUMENT_MIN_CONFIDENCE = 0.80
    PDF_SUBDOCUMENT_MIN_EDGE_SUPPORT = 0.66
    PDF_SUBDOCUMENT_MIN_RECTANGULARITY = 0.76
    PDF_SUBDOCUMENT_MIN_STRONG_EDGE_SIDES = 3
    PDF_SUBDOCUMENT_MIN_ASPECT_RATIO = 1.35
    PDF_SUBDOCUMENT_MAX_ASPECT_RATIO = 1.85
    PDF_SUBDOCUMENT_PADDING_RATIO = 0.03
    PDF_CARD_CROP_MAX_DETECTION_DIM = 1600
    PDF_CARD_CROP_KERNEL_SIZES = (5, 9, 15)
    PDF_CARD_CROP_CLOSE_ITERATIONS = 2
    PDF_CARD_CROP_CANNY_LOW = 35
    PDF_CARD_CROP_CANNY_HIGH = 110
    PDF_CARD_CROP_STRONG_SIDE_THRESHOLD = 0.55
    PDF_CARD_CROP_EARLY_EXIT = False
    PDF_CARD_CROP_EARLY_EXIT_EDGE = 0.90
    PDF_CARD_CROP_EARLY_EXIT_RECTANGULARITY = 0.85
    PDF_CARD_CROP_DEBUG_CANDIDATES = 30

    def normalize_document(
        self,
        image_path: str,
        output_path: Optional[str] = None,
        allow_subdocument_aspect_change: bool = False,
    ) -> Dict[str, Any]:
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
            normalized, debug = self._perspective_correct(
                image,
                allow_subdocument_aspect_change=allow_subdocument_aspect_change,
            )
            if debug.get("normalization_status") == "fallback":
                layout_normalized, layout_debug = self._layout_assisted_crop(
                    image,
                    debug.get("fallback_reason"),
                    allow_subdocument_aspect_change=allow_subdocument_aspect_change,
                )
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

    def detect_pdf_subdocument_boundary(
        self,
        image_path: str,
        output_path: Optional[str] = None,
        debug_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        image = cv2.imread(str(image_path))
        if image is None:
            return {"passed": False, "reason": "image_read_failed", "crop_box": None, "confidence": 0.0}
        card_crop = self._detect_pdf_physical_card_crop(image)
        if card_crop.get("passed"):
            crop_left, crop_top, crop_right, crop_bottom = [int(value) for value in card_crop["crop_box"]]
            crop = image[crop_top:crop_bottom, crop_left:crop_right].copy()
            if output_path:
                target = Path(output_path)
                target.parent.mkdir(parents=True, exist_ok=True)
                cv2.imwrite(str(target), self._resize_longest_side(crop, self.LONGEST_SIDE))
            return {
                "passed": True,
                "reason": "physical_card_crop_detected",
                "crop_box": card_crop.get("candidate_crop_box"),
                "padded_crop_box": card_crop.get("crop_box"),
                "padding_ratio": self.PDF_SUBDOCUMENT_PADDING_RATIO,
                "confidence": card_crop.get("confidence"),
                "area_ratio": card_crop.get("area_ratio"),
                "aspect_ratio": card_crop.get("aspect_ratio"),
                "edge_support": card_crop.get("edge_support"),
                "edge_side_support": card_crop.get("edge_side_support"),
                "strong_edge_side_count": card_crop.get("strong_edge_side_count"),
                "weakest_edge_support": card_crop.get("weakest_edge_support"),
                "rectangularity": card_crop.get("rectangularity"),
                "candidate_source": "physical_card_crop",
                "output_path": str(output_path) if output_path else None,
                "debug": card_crop.get("debug"),
            }
        height, width = image.shape[:2]
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        edges = cv2.Canny(blurred, 45, 150)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
        closed = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel, iterations=2)
        edge_contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        foreground = self._foreground_document_mask(image, blurred)
        foreground_contours, _ = cv2.findContours(foreground, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        image_area = max(1.0, float(width * height))
        candidates: List[Dict[str, Any]] = []
        top_contours_by_source: Dict[str, List[Dict[str, Any]]] = {
            "edge_morphology": [],
            "foreground_mask": [],
        }
        candidate_count_by_source: Dict[str, int] = {
            "edge_morphology": 0,
            "foreground_mask": 0,
        }
        raw_contour_count_by_source: Dict[str, int] = {
            "edge_morphology": len(edge_contours),
            "foreground_mask": len(foreground_contours),
        }
        top_preview = image.copy()
        debug_paths: Dict[str, Optional[str]] = {
            "edge_image": None,
            "morphology_image": None,
            "foreground_mask": None,
            "top_contours": None,
        }
        if debug_dir:
            target_dir = Path(debug_dir)
            target_dir.mkdir(parents=True, exist_ok=True)
            edge_path = target_dir / "pdf_subdocument_edges.png"
            morph_path = target_dir / "pdf_subdocument_morphology.png"
            foreground_path = target_dir / "pdf_subdocument_foreground_mask.png"
            if cv2.imwrite(str(edge_path), edges):
                debug_paths["edge_image"] = str(edge_path)
            if cv2.imwrite(str(morph_path), closed):
                debug_paths["morphology_image"] = str(morph_path)
            if cv2.imwrite(str(foreground_path), foreground):
                debug_paths["foreground_mask"] = str(foreground_path)

        contour_sources = [
            ("edge_morphology", edge_contours),
            ("foreground_mask", foreground_contours),
        ]
        for source_index, (candidate_source, source_contours) in enumerate(contour_sources):
            sorted_contours = sorted(source_contours, key=cv2.contourArea, reverse=True)
            for contour_index, contour in enumerate(sorted_contours[:40]):
                evaluated = self._evaluate_pdf_subdocument_contour(
                    contour=contour,
                    edge_image=edges,
                    image_width=width,
                    image_height=height,
                    image_area=image_area,
                    candidate_source=candidate_source,
                )
                if contour_index < 10:
                    top_contours_by_source[candidate_source].append(
                        {
                            "rank": contour_index + 1,
                            "candidate_source": candidate_source,
                            "contour_area": evaluated["contour_area"],
                            "area_ratio": evaluated["area_ratio"],
                            "perimeter": evaluated["perimeter"],
                            "approx_vertex_count": evaluated["approx_vertex_count"],
                            "min_area_rect_width": evaluated["min_area_rect_width"],
                            "min_area_rect_height": evaluated["min_area_rect_height"],
                            "rectangularity": evaluated["rectangularity"],
                            "crop_box": evaluated["crop_box"],
                            "reject_reason_before_scoring": evaluated["reject_reason_before_scoring"],
                        }
                    )
                    color = (
                        int((37 * (contour_index + 3 + source_index * 11)) % 255),
                        int((91 * (contour_index + 5 + source_index * 13)) % 255),
                        int((149 * (contour_index + 7 + source_index * 17)) % 255),
                    )
                    crop_left, crop_top, crop_right, crop_bottom = evaluated["crop_box"]
                    if crop_right > crop_left and crop_bottom > crop_top:
                        cv2.rectangle(top_preview, (crop_left, crop_top), (crop_right, crop_bottom), color, 3)
                        label = f"{candidate_source[:2]}{contour_index + 1}"
                        cv2.putText(top_preview, label, (crop_left, max(20, crop_top - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.9, color, 2)
                if evaluated["reject_reason_before_scoring"] is not None:
                    continue
                candidate_count_by_source[candidate_source] += 1
                candidates.append(evaluated["candidate"])
        if debug_dir:
            top_path = Path(debug_dir) / "pdf_subdocument_top_contours.png"
            if cv2.imwrite(str(top_path), top_preview):
                debug_paths["top_contours"] = str(top_path)
        best = max(candidates, key=lambda item: float(item["confidence"]), default=None)
        debug = {
            "attempted": True,
            "raw_contour_count": len(edge_contours),
            "raw_contour_count_by_source": raw_contour_count_by_source,
            "top_contours": top_contours_by_source["edge_morphology"],
            "top_contours_by_source": top_contours_by_source,
            "candidate_count": len(candidates),
            "candidate_count_by_source": candidate_count_by_source,
            "best_candidate": best,
            "selected_candidate_source": best.get("candidate_source") if best else None,
            "image_size": [width, height],
            "debug_preview_paths": debug_paths,
        }
        if not best:
            return {"passed": False, "reason": "no_rectangular_subdocument_candidate", "crop_box": None, "confidence": 0.0, "debug": debug}
        if float(best["rectangularity"]) < self.PDF_SUBDOCUMENT_MIN_RECTANGULARITY:
            return {"passed": False, "reason": "rectangularity_below_threshold", "crop_box": best["crop_box"], "confidence": best["confidence"], "debug": debug}
        if float(best.get("aspect_ratio") or 0.0) < self.PDF_SUBDOCUMENT_MIN_ASPECT_RATIO or float(best.get("aspect_ratio") or 0.0) > self.PDF_SUBDOCUMENT_MAX_ASPECT_RATIO:
            return {"passed": False, "reason": "aspect_ratio_outside_subdocument_range", "crop_box": best["crop_box"], "confidence": best["confidence"], "debug": debug}
        if float(best["edge_support"]) < self.PDF_SUBDOCUMENT_MIN_EDGE_SUPPORT:
            return {"passed": False, "reason": "edge_support_below_threshold", "crop_box": best["crop_box"], "confidence": best["confidence"], "debug": debug}
        if int(best.get("strong_edge_side_count") or 0) < self.PDF_SUBDOCUMENT_MIN_STRONG_EDGE_SIDES:
            return {"passed": False, "reason": "insufficient_strong_edge_sides", "crop_box": best["crop_box"], "confidence": best["confidence"], "debug": debug}
        if float(best["confidence"]) < self.PDF_SUBDOCUMENT_MIN_CONFIDENCE:
            return {"passed": False, "reason": "confidence_below_threshold", "crop_box": best["crop_box"], "confidence": best["confidence"], "debug": debug}
        crop_left, crop_top, crop_right, crop_bottom = [int(value) for value in best["crop_box"]]
        padded_crop_box = self._pad_crop_box(
            [crop_left, crop_top, crop_right, crop_bottom],
            width,
            height,
            self.PDF_SUBDOCUMENT_PADDING_RATIO,
        )
        padded_left, padded_top, padded_right, padded_bottom = padded_crop_box
        crop = image[padded_top:padded_bottom, padded_left:padded_right].copy()
        if output_path:
            target = Path(output_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(target), self._resize_longest_side(crop, self.LONGEST_SIDE))
        return {
            "passed": True,
            "reason": "physical_rectangular_subdocument_detected",
            "crop_box": best["crop_box"],
            "padded_crop_box": padded_crop_box,
            "padding_ratio": self.PDF_SUBDOCUMENT_PADDING_RATIO,
            "confidence": best["confidence"],
            "area_ratio": best["area_ratio"],
            "aspect_ratio": best.get("aspect_ratio"),
            "edge_support": best["edge_support"],
            "edge_side_support": best.get("edge_side_support"),
            "strong_edge_side_count": best.get("strong_edge_side_count"),
            "weakest_edge_support": best.get("weakest_edge_support"),
            "rectangularity": best["rectangularity"],
            "candidate_source": best["candidate_source"],
            "output_path": str(output_path) if output_path else None,
            "debug": debug,
        }

    def _evaluate_pdf_subdocument_contour(
        self,
        contour: np.ndarray,
        edge_image: np.ndarray,
        image_width: int,
        image_height: int,
        image_area: float,
        candidate_source: str,
    ) -> Dict[str, Any]:
        area = float(cv2.contourArea(contour))
        perimeter = cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, 0.025 * perimeter, True) if perimeter > 0 else np.array([])
        rect = cv2.minAreaRect(contour) if area > 0 else ((0, 0), (0, 0), 0)
        (_, _), (rect_width, rect_height), _ = rect
        rect_area = float(rect_width * rect_height)
        rectangularity = min(1.0, area / rect_area) if rect_area > 0 else 0.0
        box = cv2.boxPoints(rect).astype("float32") if rect_area > 0 else np.zeros((4, 2), dtype="float32")
        ordered = self._order_points(box) if rect_area > 0 else box
        crop_left = max(0, int(np.floor(ordered[:, 0].min()))) if rect_area > 0 else 0
        crop_top = max(0, int(np.floor(ordered[:, 1].min()))) if rect_area > 0 else 0
        crop_right = min(image_width, int(np.ceil(ordered[:, 0].max()))) if rect_area > 0 else 0
        crop_bottom = min(image_height, int(np.ceil(ordered[:, 1].max()))) if rect_area > 0 else 0
        area_ratio = area / image_area if area > 0 else 0.0
        reject_reason = None
        if area <= 0:
            reject_reason = "area_zero"
        elif area_ratio < self.PDF_SUBDOCUMENT_MIN_AREA_RATIO:
            reject_reason = "area_too_small"
        elif area_ratio > self.PDF_SUBDOCUMENT_MAX_AREA_RATIO:
            reject_reason = "area_too_large"
        elif perimeter <= 0:
            reject_reason = "invalid_perimeter"
        elif len(approx) < 4:
            reject_reason = "insufficient_vertices"
        elif rect_width <= 1 or rect_height <= 1 or rect_area <= 0:
            reject_reason = "invalid_rect"
        elif (crop_right - crop_left) < self.MIN_TRANSFORMED_DIMENSION or (crop_bottom - crop_top) < self.MIN_TRANSFORMED_DIMENSION:
            reject_reason = "crop_too_small"

        result: Dict[str, Any] = {
            "candidate_source": candidate_source,
            "contour_area": round(float(area), 2),
            "area_ratio": round(float(area_ratio), 4),
            "perimeter": round(float(perimeter), 2),
            "approx_vertex_count": int(len(approx)),
            "min_area_rect_width": round(float(rect_width), 2),
            "min_area_rect_height": round(float(rect_height), 2),
            "rectangularity": round(float(rectangularity), 4),
            "crop_box": [crop_left, crop_top, crop_right, crop_bottom],
            "reject_reason_before_scoring": reject_reason,
            "candidate": None,
        }
        if reject_reason is not None:
            return result

        edge_support, edge_side_support = self._pdf_rectangle_edge_support(edge_image, [crop_left, crop_top, crop_right, crop_bottom])
        strong_edge_side_count = sum(1 for value in edge_side_support.values() if float(value) >= self.PDF_SUBDOCUMENT_MIN_EDGE_SUPPORT)
        weakest_edge_support = min(edge_side_support.values()) if edge_side_support else 0.0
        aspect_ratio = max(rect_width, rect_height) / max(1.0, min(rect_width, rect_height))
        aspect_score = max(0.0, 1.0 - abs(aspect_ratio - 1.6) / 2.4)
        area_score = 1.0 - min(1.0, abs(area_ratio - 0.20) / 0.45)
        center_x = (crop_left + crop_right) / 2.0
        center_y = (crop_top + crop_bottom) / 2.0
        center_distance = ((center_x - image_width / 2.0) ** 2 + (center_y - image_height / 2.0) ** 2) ** 0.5
        center_score = 1.0 - min(1.0, center_distance / max(1.0, (image_width ** 2 + image_height ** 2) ** 0.5 / 2.0))
        confidence = (
            rectangularity * 0.35
            + edge_support * 0.35
            + aspect_score * 0.15
            + area_score * 0.10
            + center_score * 0.05
        )
        result["candidate"] = {
            "candidate_source": candidate_source,
            "crop_box": [crop_left, crop_top, crop_right, crop_bottom],
            "confidence": round(float(confidence), 4),
            "area_ratio": round(float(area_ratio), 4),
            "rectangularity": round(float(rectangularity), 4),
            "edge_support": round(float(edge_support), 4),
            "edge_side_support": {key: round(float(value), 4) for key, value in edge_side_support.items()},
            "strong_edge_side_count": int(strong_edge_side_count),
            "weakest_edge_support": round(float(weakest_edge_support), 4),
            "aspect_ratio": round(float(aspect_ratio), 4),
            "aspect_score": round(float(aspect_score), 4),
            "area_score": round(float(area_score), 4),
            "center_score": round(float(center_score), 4),
            "points": [[round(float(x), 2), round(float(y), 2)] for x, y in ordered.tolist()],
        }
        return result

    def _pad_crop_box(self, crop_box: List[int], image_width: int, image_height: int, padding_ratio: float) -> List[int]:
        left, top, right, bottom = [int(value) for value in crop_box]
        crop_width = max(1, right - left)
        crop_height = max(1, bottom - top)
        pad_x = int(round(crop_width * max(0.0, float(padding_ratio))))
        pad_y = int(round(crop_height * max(0.0, float(padding_ratio))))
        return [
            max(0, left - pad_x),
            max(0, top - pad_y),
            min(image_width, right + pad_x),
            min(image_height, bottom + pad_y),
        ]

    def _detect_pdf_physical_card_crop(self, image: np.ndarray) -> Dict[str, Any]:
        started = time.perf_counter()
        image_height, image_width = image.shape[:2]
        scale = min(1.0, self.PDF_CARD_CROP_MAX_DETECTION_DIM / max(1, max(image_height, image_width)))
        detection_width = max(1, round(image_width * scale))
        detection_height = max(1, round(image_height * scale))
        small = cv2.resize(image, (detection_width, detection_height), interpolation=cv2.INTER_AREA) if scale < 1 else image
        scale_x = image_width / max(1, detection_width)
        scale_y = image_height / max(1, detection_height)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (5, 5), 0)
        edges = cv2.Canny(gray, self.PDF_CARD_CROP_CANNY_LOW, self.PDF_CARD_CROP_CANNY_HIGH)
        candidates: List[Dict[str, Any]] = []
        seen = set()
        kernels_tried: List[int] = []
        early_stopped = False
        for kernel_size in self.PDF_CARD_CROP_KERNEL_SIZES:
            kernels_tried.append(int(kernel_size))
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_size, kernel_size))
            connected = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel, iterations=self.PDF_CARD_CROP_CLOSE_ITERATIONS)
            contours, _ = cv2.findContours(connected, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
            kernel_accepted: List[Dict[str, Any]] = []
            for contour in contours:
                x, y, box_width, box_height = cv2.boundingRect(contour)
                if box_width < 25 or box_height < 15:
                    continue
                aspect_ratio = box_width / max(1.0, float(box_height))
                area_ratio = (box_width * box_height) / max(1.0, float(detection_width * detection_height))
                if area_ratio < self.PDF_SUBDOCUMENT_MIN_AREA_RATIO * 0.5 or area_ratio > min(0.95, self.PDF_SUBDOCUMENT_MAX_AREA_RATIO * 1.4):
                    continue
                if aspect_ratio < self.PDF_SUBDOCUMENT_MIN_ASPECT_RATIO * 0.75 or aspect_ratio > self.PDF_SUBDOCUMENT_MAX_ASPECT_RATIO * 1.25:
                    continue
                key = (round(x / 5), round(y / 5), round(box_width / 5), round(box_height / 5))
                if key in seen:
                    continue
                seen.add(key)
                area = float(cv2.contourArea(contour))
                if area <= 0:
                    continue
                perimeter = cv2.arcLength(contour, True)
                polygon = cv2.approxPolyDP(contour, 0.025 * perimeter, True) if perimeter > 0 else np.array([])
                rectangularity = min(1.0, area / max(1.0, float(box_width * box_height)))
                sides = self._pdf_card_side_support(edges, x, y, box_width, box_height, max(2, round(min(detection_width, detection_height) * 0.004)))
                edge_support = sum(sides) / 4.0
                strong_sides = sum(side >= self.PDF_CARD_CROP_STRONG_SIDE_THRESHOLD for side in sides)
                reasons: List[str] = []
                if not self.PDF_SUBDOCUMENT_MIN_ASPECT_RATIO <= aspect_ratio <= self.PDF_SUBDOCUMENT_MAX_ASPECT_RATIO:
                    reasons.append("aspect_ratio")
                if not self.PDF_SUBDOCUMENT_MIN_AREA_RATIO <= area_ratio <= self.PDF_SUBDOCUMENT_MAX_AREA_RATIO:
                    reasons.append("area_ratio")
                if rectangularity < self.PDF_SUBDOCUMENT_MIN_RECTANGULARITY:
                    reasons.append("rectangularity")
                if edge_support < self.PDF_SUBDOCUMENT_MIN_EDGE_SUPPORT:
                    reasons.append("edge_support")
                if strong_sides < self.PDF_SUBDOCUMENT_MIN_STRONG_EDGE_SIDES:
                    reasons.append("strong_edge_sides")
                if len(polygon) != 4:
                    reasons.append("not_quadrilateral")
                score = 0.5 * edge_support + 0.3 * rectangularity + 0.2 * strong_sides / 4.0
                original_x = round(x * scale_x)
                original_y = round(y * scale_y)
                original_right = min(image_width, round((x + box_width) * scale_x))
                original_bottom = min(image_height, round((y + box_height) * scale_y))
                candidate_crop_box = [original_x, original_y, original_right, original_bottom]
                padded_crop_box = self._pad_crop_box(candidate_crop_box, image_width, image_height, self.PDF_SUBDOCUMENT_PADDING_RATIO)
                candidate = {
                    "crop_box": padded_crop_box,
                    "candidate_crop_box": candidate_crop_box,
                    "bbox": [original_x, original_y, max(1, original_right - original_x), max(1, original_bottom - original_y)],
                    "aspect_ratio": round(float(aspect_ratio), 4),
                    "area_ratio": round(float(area_ratio), 4),
                    "rectangularity": round(float(rectangularity), 4),
                    "edge_support": round(float(edge_support), 4),
                    "edge_side_support": [round(float(value), 4) for value in sides],
                    "strong_edge_side_count": int(strong_sides),
                    "weakest_edge_support": round(float(min(sides) if sides else 0.0), 4),
                    "polygon_vertices": int(len(polygon)),
                    "kernel_size": int(kernel_size),
                    "score": round(float(score), 4),
                    "confidence": round(float(score), 4),
                    "accepted": not reasons,
                    "rejection_reasons": reasons,
                }
                candidates.append(candidate)
                if candidate["accepted"]:
                    kernel_accepted.append(candidate)
            if self.PDF_CARD_CROP_EARLY_EXIT and any(
                float(candidate["edge_support"]) >= self.PDF_CARD_CROP_EARLY_EXIT_EDGE
                and float(candidate["rectangularity"]) >= self.PDF_CARD_CROP_EARLY_EXIT_RECTANGULARITY
                and int(candidate["strong_edge_side_count"]) == 4
                for candidate in kernel_accepted
            ):
                early_stopped = True
                break
        candidates.sort(key=lambda item: (bool(item["accepted"]), float(item["score"]), float(item["area_ratio"])), reverse=True)
        selected = next((candidate for candidate in candidates if candidate["accepted"]), None)
        debug = {
            "method": "test_card_crop",
            "original_size": [image_width, image_height],
            "detection_size": [detection_width, detection_height],
            "scale_x": round(float(scale_x), 6),
            "scale_y": round(float(scale_y), 6),
            "candidate_count": len(candidates),
            "kernels_tried": kernels_tried,
            "early_stopped": early_stopped,
            "card_detection_ms": round((time.perf_counter() - started) * 1000.0, 2),
            "selected_candidate": selected,
            "top_candidates": candidates[: self.PDF_CARD_CROP_DEBUG_CANDIDATES],
            "thresholds": {
                "max_detection_dim": self.PDF_CARD_CROP_MAX_DETECTION_DIM,
                "kernel_sizes": list(self.PDF_CARD_CROP_KERNEL_SIZES),
                "close_iterations": self.PDF_CARD_CROP_CLOSE_ITERATIONS,
                "canny_low": self.PDF_CARD_CROP_CANNY_LOW,
                "canny_high": self.PDF_CARD_CROP_CANNY_HIGH,
                "min_aspect": self.PDF_SUBDOCUMENT_MIN_ASPECT_RATIO,
                "max_aspect": self.PDF_SUBDOCUMENT_MAX_ASPECT_RATIO,
                "min_area": self.PDF_SUBDOCUMENT_MIN_AREA_RATIO,
                "max_area": self.PDF_SUBDOCUMENT_MAX_AREA_RATIO,
                "min_rectangularity": self.PDF_SUBDOCUMENT_MIN_RECTANGULARITY,
                "min_edge_support": self.PDF_SUBDOCUMENT_MIN_EDGE_SUPPORT,
                "min_strong_sides": self.PDF_SUBDOCUMENT_MIN_STRONG_EDGE_SIDES,
                "strong_side_threshold": self.PDF_CARD_CROP_STRONG_SIDE_THRESHOLD,
                "early_exit": self.PDF_CARD_CROP_EARLY_EXIT,
                "early_exit_edge": self.PDF_CARD_CROP_EARLY_EXIT_EDGE,
                "early_exit_rectangularity": self.PDF_CARD_CROP_EARLY_EXIT_RECTANGULARITY,
                "padding": self.PDF_SUBDOCUMENT_PADDING_RATIO,
            },
        }
        if not selected:
            return {"passed": False, "reason": "no_card_crop_candidate_passed_guards", "crop_box": None, "confidence": 0.0, "debug": debug}
        return {"passed": True, "reason": "physical_card_crop_detected", **selected, "debug": debug}

    def _pdf_card_side_support(self, edges: np.ndarray, x: int, y: int, width: int, height: int, band: int) -> List[float]:
        image_height, image_width = edges.shape
        x0, x1 = max(0, x), min(image_width, x + width)
        y0, y1 = max(0, y), min(image_height, y + height)

        def horizontal(yy: int) -> float:
            region = edges[max(0, yy - band):min(image_height, yy + band + 1), x0:x1]
            return float(np.mean(np.any(region > 0, axis=0))) if region.size else 0.0

        def vertical(xx: int) -> float:
            region = edges[y0:y1, max(0, xx - band):min(image_width, xx + band + 1)]
            return float(np.mean(np.any(region > 0, axis=1))) if region.size else 0.0

        return [horizontal(y0), horizontal(y1 - 1), vertical(x0), vertical(x1 - 1)]

    def _pdf_rectangle_edge_support(self, edge_image: np.ndarray, crop_box: List[int]) -> tuple[float, Dict[str, float]]:
        height, width = edge_image.shape[:2]
        left, top, right, bottom = crop_box
        band = max(2, int(round(min(max(1, right - left), max(1, bottom - top)) * 0.012)))

        def density(region: np.ndarray) -> float:
            return float(np.mean(region > 0)) if region.size else 0.0

        top_band = edge_image[max(0, top - band):min(height, top + band + 1), max(0, left):min(width, right)]
        bottom_band = edge_image[max(0, bottom - band - 1):min(height, bottom + band), max(0, left):min(width, right)]
        left_band = edge_image[max(0, top):min(height, bottom), max(0, left - band):min(width, left + band + 1)]
        right_band = edge_image[max(0, top):min(height, bottom), max(0, right - band - 1):min(width, right + band)]
        side_support = {
            "top": min(1.0, density(top_band) / 0.10),
            "bottom": min(1.0, density(bottom_band) / 0.10),
            "left": min(1.0, density(left_band) / 0.10),
            "right": min(1.0, density(right_band) / 0.10),
        }
        return max(0.0, min(1.0, sum(side_support.values()) / 4.0)), side_support

    def _perspective_correct(
        self,
        image: np.ndarray,
        allow_subdocument_aspect_change: bool = False,
    ) -> tuple[np.ndarray, Dict[str, Any]]:
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
        margin_ratio = 0.024
        center = ordered.mean(axis=0)
        expanded_ordered = center + (ordered - center) * (1.0 + margin_ratio)
        expanded_ordered[:, 0] = np.clip(expanded_ordered[:, 0], 0, image.shape[1] - 1)
        expanded_ordered[:, 1] = np.clip(expanded_ordered[:, 1], 0, image.shape[0] - 1)
        expanded_ordered = expanded_ordered.astype("float32")
        top_left, top_right, bottom_right, bottom_left = expanded_ordered
        width_a = np.linalg.norm(bottom_right - bottom_left)
        width_b = np.linalg.norm(top_right - top_left)
        height_a = np.linalg.norm(top_right - bottom_right)
        height_b = np.linalg.norm(top_left - bottom_left)
        width_distortion = (max(width_a, width_b) / max(1.0, min(width_a, width_b))) - 1.0
        height_distortion = (max(height_a, height_b) / max(1.0, min(height_a, height_b))) - 1.0
        top_vector = top_right - top_left
        left_vector = bottom_left - top_left
        corner_angle = abs(float(np.degrees(np.arctan2(
            abs(float(top_vector[0] * left_vector[1] - top_vector[1] * left_vector[0])),
            float(np.dot(top_vector, left_vector)),
        ))))
        angle_distortion = abs(90.0 - corner_angle) / 90.0
        distortion_score = max(width_distortion, height_distortion)

        crop_left = max(0, int(np.floor(expanded_ordered[:, 0].min())))
        crop_top = max(0, int(np.floor(expanded_ordered[:, 1].min())))
        crop_right = min(image.shape[1], int(np.ceil(expanded_ordered[:, 0].max())))
        crop_bottom = min(image.shape[0], int(np.ceil(expanded_ordered[:, 1].max())))
        cropped = image[crop_top:crop_bottom, crop_left:crop_right].copy()
        validation = self._validate_transformed_image(
            cropped,
            image,
            allow_subdocument_aspect_change=allow_subdocument_aspect_change,
        )
        if not validation["passed"]:
            debug.update(
                {
                    "validation_passed": False,
                    "fallback_used": True,
                    "fallback_reason": validation["reason"],
                    "normalization_status": "fallback",
                    "crop_box": [crop_left, crop_top, crop_right, crop_bottom],
                    "warped_size": [crop_right - crop_left, crop_bottom - crop_top],
                    "transform_validation": validation,
                }
            )
            return image.copy(), debug

        debug.update(
            {
                "document_detected": True,
                "crop_applied": True,
                "perspective_applied": False,
                "normalization_status": "cropped",
                "validation_passed": True,
                "fallback_used": False,
                "fallback_reason": None,
                "detected_points": [[round(float(x), 2), round(float(y), 2)] for x, y in expanded_ordered.tolist()],
                "perspective_safety_margin_ratio": margin_ratio,
                "perspective_required": False,
                "perspective_distortion_score": round(float(distortion_score), 4),
                "width_distortion": round(float(width_distortion), 4),
                "height_distortion": round(float(height_distortion), 4),
                "angle_distortion": round(float(angle_distortion), 4),
                "normalization_transform": "conservative_crop",
                "crop_box": [crop_left, crop_top, crop_right, crop_bottom],
                "warped_size": [crop_right - crop_left, crop_bottom - crop_top],
                "transform_validation": validation,
            }
        )
        return cropped, debug

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
    original: np.ndarray,
    allow_subdocument_aspect_change: bool = False,) -> Dict[str, Any]:

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
            "aspect_change_validation_bypassed": False,
            "aspect_change_validation_bypass_reason": None,
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

        elif aspect_change_ratio > 1.35 and not allow_subdocument_aspect_change:
            validation.update(
                {
                    "passed": False,
                    "reason": "transformed_aspect_ratio_changed_too_much",
                }
            )
        elif aspect_change_ratio > 1.35 and allow_subdocument_aspect_change:
            validation.update(
                {
                    "aspect_change_validation_bypassed": True,
                    "aspect_change_validation_bypass_reason": "pdf_sub_document_candidate_allows_aspect_change",
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

    def _layout_assisted_crop(
        self,
        image: np.ndarray,
        previous_fallback_reason: Optional[str] = None,
        allow_subdocument_aspect_change: bool = False,
    ) -> tuple[np.ndarray, Dict[str, Any]]:
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
        validation = self._validate_transformed_image(
            cropped,
            image,
            allow_subdocument_aspect_change=allow_subdocument_aspect_change,
        )
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
                "crop_box": [crop_left, crop_top, crop_right, crop_bottom],
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
