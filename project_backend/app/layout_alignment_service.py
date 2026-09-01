import base64
import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

from .layout_analysis_service import analyze_layout
from .layout_signature_service import build_layout_signature, compare_layout_signatures


class LayoutAlignmentService:
    MIN_BOX_MATCHES = 3
    MIN_MATCH_SCORE = 0.55
    MIN_AFTER_SCORE = 0.62
    MIN_IMPROVEMENT = -0.02
    SKIP_SCORE = 0.93
    SKIP_ASPECT_DELTA = 0.03
    MAX_SIGNATURE_CACHE = 64

    def __init__(self) -> None:
        self._signature_cache: Dict[str, Dict[str, Any]] = {}

    def align_to_template(
        self,
        query_image_path: str,
        template_image_source: str,
        output_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        query = cv2.imread(str(query_image_path))
        template = self._load_image(template_image_source)
        if output_path is None:
            query_path = Path(query_image_path)
            output_path = str(query_path.with_name(f"{query_path.stem}_layout_aligned.png"))
        match_output_path = str(Path(output_path).with_name(f"{Path(output_path).stem}_layout_matches.jpg"))

        if query is None:
            return self._result("failed", "query_image_unreadable", output_path, None, error="Unable to read query image")
        if template is None:
            return self._result("failed", "template_image_unreadable", output_path, None, error="Unable to read template image")

        try:
            query_signature = self._signature_for_image(query)
            template_signature = self._signature_for_image(template)
        except Exception as error:
            return self._result("failed", "layout_analysis_failed", output_path, None, error=str(error))

        before_debug = compare_layout_signatures(query_signature, template_signature)
        before_score = float(before_debug.get("score") or 0.0)
        aspect_delta = self._aspect_delta(query_signature, template_signature)
        if before_score >= self.SKIP_SCORE and aspect_delta <= self.SKIP_ASPECT_DELTA:
            return self._result(
                "skipped",
                "layout_geometry_already_matches_template",
                output_path,
                None,
                before_layout_score=before_score,
                after_layout_score=before_score,
                layout_score_improvement=0.0,
                query_signature=query_signature,
                template_signature=template_signature,
                layout_box_matches=[],
                transform_type="none",
                warp_applied=False,
            )

        box_matches = self._match_regions(query_signature.get("regions", []), template_signature.get("regions", []))
        usable_matches = [item for item in box_matches if float(item.get("score") or 0.0) >= self.MIN_MATCH_SCORE]
        if len(usable_matches) < self.MIN_BOX_MATCHES:
            return self._result(
                "fallback",
                "insufficient_layout_box_matches",
                output_path,
                self._save_match_visualization(query, template, box_matches, match_output_path),
                before_layout_score=before_score,
                after_layout_score=None,
                layout_score_improvement=None,
                query_signature=query_signature,
                template_signature=template_signature,
                layout_box_matches=box_matches,
                transform_type="none",
                warp_applied=False,
            )

        query_points, template_points = self._point_pairs(usable_matches, query.shape, template.shape)
        transform_type, matrix, inliers = self._estimate_transform(query_points, template_points)
        if matrix is None:
            return self._result(
                "fallback",
                "layout_transform_not_found",
                output_path,
                self._save_match_visualization(query, template, usable_matches, match_output_path),
                before_layout_score=before_score,
                after_layout_score=None,
                query_signature=query_signature,
                template_signature=template_signature,
                layout_box_matches=usable_matches,
                transform_type=transform_type,
                warp_applied=False,
            )

        template_height, template_width = template.shape[:2]
        try:
            if transform_type == "homography":
                warped = cv2.warpPerspective(
                    query,
                    matrix,
                    (template_width, template_height),
                    borderMode=cv2.BORDER_CONSTANT,
                    borderValue=(255, 255, 255),
                )
                homography = matrix.tolist()
            else:
                warped = cv2.warpAffine(
                    query,
                    matrix,
                    (template_width, template_height),
                    borderMode=cv2.BORDER_CONSTANT,
                    borderValue=(255, 255, 255),
                )
                homography = None
        except cv2.error as error:
            return self._result(
                "failed",
                "layout_warp_failed",
                output_path,
                self._save_match_visualization(query, template, usable_matches, match_output_path),
                error=str(error),
                before_layout_score=before_score,
                query_signature=query_signature,
                template_signature=template_signature,
                layout_box_matches=usable_matches,
                transform_type=transform_type,
                warp_applied=False,
            )

        try:
            warped_signature = self._signature_for_image(warped, use_cache=False)
            after_debug = compare_layout_signatures(warped_signature, template_signature)
            after_score = float(after_debug.get("score") or 0.0)
        except Exception as error:
            return self._result(
                "fallback",
                "layout_quality_check_failed",
                output_path,
                self._save_match_visualization(query, template, usable_matches, match_output_path),
                error=str(error),
                before_layout_score=before_score,
                query_signature=query_signature,
                template_signature=template_signature,
                layout_box_matches=usable_matches,
                transform_type=transform_type,
                warp_applied=False,
            )

        improvement = round(after_score - before_score, 4)
        quality_passed = after_score >= self.MIN_AFTER_SCORE and improvement >= self.MIN_IMPROVEMENT
        match_path = self._save_match_visualization(query, template, usable_matches, match_output_path, inliers=inliers)
        if not quality_passed:
            return self._result(
                "fallback",
                "layout_alignment_quality_not_improved",
                output_path,
                match_path,
                before_layout_score=before_score,
                after_layout_score=after_score,
                layout_score_improvement=improvement,
                query_signature=query_signature,
                template_signature=template_signature,
                layout_box_matches=usable_matches,
                transform_type=transform_type,
                warp_applied=False,
                homography=homography,
                affine=matrix.tolist() if transform_type != "homography" else None,
            )

        target_path = Path(output_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(target_path), warped):
            return self._result(
                "failed",
                "layout_warp_write_failed",
                output_path,
                match_path,
                error="Unable to write layout aligned image",
                before_layout_score=before_score,
                after_layout_score=after_score,
                layout_score_improvement=improvement,
                query_signature=query_signature,
                template_signature=template_signature,
                layout_box_matches=usable_matches,
                transform_type=transform_type,
                warp_applied=False,
            )

        return self._result(
            "aligned",
            "layout_alignment_quality_passed",
            output_path,
            match_path,
            before_layout_score=before_score,
            after_layout_score=after_score,
            layout_score_improvement=improvement,
            query_signature=query_signature,
            template_signature=template_signature,
            warped_signature=warped_signature,
            layout_box_matches=usable_matches,
            transform_type=transform_type,
            warp_applied=True,
            homography=homography,
            affine=matrix.tolist() if transform_type != "homography" else None,
            inliers=inliers,
        )

    def _load_image(self, source: str):
        if not source:
            return None
        if source.startswith("data:image"):
            try:
                _, encoded = source.split(",", 1)
                data = base64.b64decode(encoded)
                array = np.frombuffer(data, dtype=np.uint8)
                return cv2.imdecode(array, cv2.IMREAD_COLOR)
            except Exception:
                return None

        source_path = Path(source)
        if not source_path.is_absolute() and not source_path.exists():
            backend_root = Path(__file__).resolve().parents[1]
            candidate = backend_root / source_path
            if candidate.exists():
                source_path = candidate
        return cv2.imread(str(source_path)) if source_path.exists() else None

    def _signature_for_image(self, image: np.ndarray, use_cache: bool = True) -> Dict[str, Any]:
        cache_key = self._image_cache_key(image)
        if use_cache and cache_key in self._signature_cache:
            return self._signature_cache[cache_key]

        signature = build_layout_signature(analyze_layout(image))
        if use_cache:
            if len(self._signature_cache) >= self.MAX_SIGNATURE_CACHE:
                first_key = next(iter(self._signature_cache))
                self._signature_cache.pop(first_key, None)
            self._signature_cache[cache_key] = signature
        return signature

    def _image_cache_key(self, image: np.ndarray) -> str:
        height, width = image.shape[:2]
        digest = hashlib.sha1()
        digest.update(str((width, height, image.dtype)).encode("utf-8"))
        digest.update(np.ascontiguousarray(image).tobytes())
        return digest.hexdigest()

    def _aspect_delta(self, query: Dict[str, Any], template: Dict[str, Any]) -> float:
        query_aspect = float(query.get("page_aspect_ratio") or 0.0)
        template_aspect = float(template.get("page_aspect_ratio") or 0.0)
        return round(abs(query_aspect - template_aspect) / max(query_aspect, template_aspect, 1e-6), 4)

    def _match_regions(self, query_regions: List[Dict[str, Any]], template_regions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        matches: List[Dict[str, Any]] = []
        used_template_indexes: set[int] = set()
        query_order = self._reading_order(query_regions)
        template_order = self._reading_order(template_regions)

        for query_index, query_region in enumerate(query_regions):
            candidates = []
            for template_index, template_region in enumerate(template_regions):
                if template_index in used_template_indexes or query_region.get("label") != template_region.get("label"):
                    continue
                score = self._region_match_score(
                    query_region,
                    template_region,
                    query_order.get(query_index, 0.0),
                    template_order.get(template_index, 0.0),
                )
                candidates.append((template_index, template_region, score))
            if not candidates:
                continue
            template_index, template_region, score = max(candidates, key=lambda item: item[2])
            used_template_indexes.add(template_index)
            matches.append(
                {
                    "label": query_region.get("label"),
                    "query_region": query_region,
                    "template_region": template_region,
                    "score": round(float(score), 4),
                    "query_order": query_order.get(query_index, 0.0),
                    "template_order": template_order.get(template_index, 0.0),
                }
            )

        return sorted(matches, key=lambda item: item["score"], reverse=True)

    def _reading_order(self, regions: List[Dict[str, Any]]) -> Dict[int, float]:
        ordered = sorted(enumerate(regions), key=lambda item: (float((item[1].get("center") or [0, 0])[1]), float((item[1].get("center") or [0, 0])[0])))
        denominator = max(1, len(ordered) - 1)
        return {index: rank / denominator for rank, (index, _) in enumerate(ordered)}

    def _region_match_score(self, query: Dict[str, Any], template: Dict[str, Any], query_order: float, template_order: float) -> float:
        query_bbox = query.get("bbox") or {}
        template_bbox = template.get("bbox") or {}
        query_center = query.get("center") or [0.0, 0.0]
        template_center = template.get("center") or [0.0, 0.0]
        center_distance = float(((query_center[0] - template_center[0]) ** 2 + (query_center[1] - template_center[1]) ** 2) ** 0.5)
        position_score = max(0.0, min(1.0, 1.0 - center_distance / 0.75))
        size_score = self._size_similarity(query_bbox, template_bbox)
        area_score = self._ratio_similarity(float(query.get("area_ratio") or 0.0), float(template.get("area_ratio") or 0.0))
        order_score = max(0.0, min(1.0, 1.0 - abs(query_order - template_order)))
        return (position_score * 0.35) + (size_score * 0.30) + (area_score * 0.20) + (order_score * 0.15)

    def _size_similarity(self, left: Dict[str, Any], right: Dict[str, Any]) -> float:
        width_score = self._ratio_similarity(float(left.get("width_ratio") or 0.0), float(right.get("width_ratio") or 0.0))
        height_score = self._ratio_similarity(float(left.get("height_ratio") or 0.0), float(right.get("height_ratio") or 0.0))
        return (width_score + height_score) / 2

    def _ratio_similarity(self, left: float, right: float) -> float:
        if left <= 0 and right <= 0:
            return 1.0
        if left <= 0 or right <= 0:
            return 0.0
        return max(0.0, min(1.0, min(left, right) / max(left, right)))

    def _point_pairs(self, matches: List[Dict[str, Any]], query_shape: Tuple[int, int, int], template_shape: Tuple[int, int, int]):
        query_height, query_width = query_shape[:2]
        template_height, template_width = template_shape[:2]
        query_points: List[List[float]] = []
        template_points: List[List[float]] = []
        for match in matches:
            for query_point, template_point in zip(
                self._region_points(match["query_region"], query_width, query_height),
                self._region_points(match["template_region"], template_width, template_height),
            ):
                query_points.append(query_point)
                template_points.append(template_point)
        return np.float32(query_points), np.float32(template_points)

    def _region_points(self, region: Dict[str, Any], image_width: int, image_height: int) -> List[List[float]]:
        bbox = region.get("bbox") or {}
        x = float(bbox.get("x_ratio") or 0.0) * image_width
        y = float(bbox.get("y_ratio") or 0.0) * image_height
        width = float(bbox.get("width_ratio") or 0.0) * image_width
        height = float(bbox.get("height_ratio") or 0.0) * image_height
        return [
            [x, y],
            [x + width, y],
            [x + width, y + height],
            [x, y + height],
            [x + width / 2, y + height / 2],
        ]

    def _estimate_transform(self, query_points: np.ndarray, template_points: np.ndarray):
        if len(query_points) >= 12:
            homography, mask = cv2.findHomography(query_points.reshape(-1, 1, 2), template_points.reshape(-1, 1, 2), cv2.RANSAC, 5.0)
            if homography is not None and mask is not None and int(mask.ravel().sum()) >= max(8, len(query_points) * 0.35):
                return "homography", homography, int(mask.ravel().sum())
        if len(query_points) >= 6:
            affine, mask = cv2.estimateAffinePartial2D(query_points, template_points, method=cv2.RANSAC, ransacReprojThreshold=5.0)
            if affine is not None:
                return "affine", affine, int(mask.ravel().sum()) if mask is not None else 0
        return "none", None, 0

    def _save_match_visualization(
        self,
        query,
        template,
        matches: List[Dict[str, Any]],
        output_path: str,
        inliers: int = 0,
    ) -> Optional[str]:
        try:
            query_height, query_width = query.shape[:2]
            template_height, template_width = template.shape[:2]
            canvas_height = max(query_height, template_height)
            canvas_width = query_width + template_width
            canvas = np.full((canvas_height, canvas_width, 3), 255, dtype=np.uint8)
            canvas[:query_height, :query_width] = query
            canvas[:template_height, query_width:query_width + template_width] = template
            for match in matches[:30]:
                query_center = self._pixel_center(match["query_region"], query_width, query_height)
                template_center = self._pixel_center(match["template_region"], template_width, template_height)
                template_center = (template_center[0] + query_width, template_center[1])
                color = (0, 180, 0) if float(match.get("score") or 0.0) >= self.MIN_MATCH_SCORE else (0, 165, 255)
                cv2.circle(canvas, query_center, 4, color, -1)
                cv2.circle(canvas, template_center, 4, color, -1)
                cv2.line(canvas, query_center, template_center, color, 1)
            target_path = Path(output_path)
            target_path.parent.mkdir(parents=True, exist_ok=True)
            if cv2.imwrite(str(target_path), canvas):
                return str(target_path)
        except Exception:
            return None
        return None

    def _pixel_center(self, region: Dict[str, Any], image_width: int, image_height: int) -> Tuple[int, int]:
        center = region.get("center") or [0.0, 0.0]
        return (int(float(center[0]) * image_width), int(float(center[1]) * image_height))

    def _result(
        self,
        status: str,
        reason: str,
        output_path: Optional[str],
        match_image_path: Optional[str],
        error: Optional[str] = None,
        before_layout_score: Optional[float] = None,
        after_layout_score: Optional[float] = None,
        layout_score_improvement: Optional[float] = None,
        query_signature: Optional[Dict[str, Any]] = None,
        template_signature: Optional[Dict[str, Any]] = None,
        warped_signature: Optional[Dict[str, Any]] = None,
        layout_box_matches: Optional[List[Dict[str, Any]]] = None,
        transform_type: str = "none",
        warp_applied: bool = False,
        homography: Optional[List[List[float]]] = None,
        affine: Optional[List[List[float]]] = None,
        inliers: int = 0,
    ) -> Dict[str, Any]:
        matches = layout_box_matches or []
        alignment_score = after_layout_score if after_layout_score is not None else before_layout_score or 0.0
        debug = {
            "method": "layout_signature_alignment",
            "reason": reason,
            "layout_box_matches": len(matches),
            "matched_boxes": matches[:20],
            "transform_type": transform_type,
            "before_layout_score": round(float(before_layout_score), 4) if before_layout_score is not None else None,
            "after_layout_score": round(float(after_layout_score), 4) if after_layout_score is not None else None,
            "layout_score_improvement": layout_score_improvement,
            "query_region_count": int((query_signature or {}).get("region_count") or 0),
            "template_region_count": int((template_signature or {}).get("region_count") or 0),
            "warped_region_count": int((warped_signature or {}).get("region_count") or 0) if warped_signature else None,
            "warp_applied": warp_applied,
            "homography_found": homography is not None,
            "affine_found": affine is not None,
            "inliers": inliers,
            "alignment_score": round(float(alignment_score), 4),
        }
        return {
            "alignment_status": status,
            "alignment_success": status == "aligned",
            "aligned_image_path": output_path if status == "aligned" else None,
            "alignment_match_image_path": match_image_path,
            "alignment_debug": debug,
            "method": "layout_signature_alignment",
            "keypoints_query": 0,
            "keypoints_template": 0,
            "matches": len(matches),
            "good_matches": len([item for item in matches if float(item.get("score") or 0.0) >= self.MIN_MATCH_SCORE]),
            "inliers": inliers,
            "inlier_ratio": round(inliers / max(1, len(matches) * 5), 4),
            "homography_found": homography is not None,
            "warp_applied": warp_applied,
            "alignment_score": round(float(alignment_score), 4),
            "homography": homography,
            "affine": affine,
            "error": error,
        }
