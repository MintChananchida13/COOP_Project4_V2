import base64
from pathlib import Path
from typing import Any, Dict, Optional

import cv2
import numpy as np


class AlignmentService:
    MIN_GOOD_MATCHES = 5
    MAX_GOOD_MATCHES = 80
    ORB_FEATURES = 3000
    MIN_INLIERS = 20
    MAX_REPROJECTION_ERROR = 5.0
    ASPECT_RATIO_TOLERANCE = 0.04

    def alignment_precheck(
        self,
        query_image_path: str,
        template_image_source: str,
        normalization_info: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        query = cv2.imread(str(query_image_path))
        template = self._load_template_image(template_image_source)
        normalization_info = normalization_info or {}
        if query is None:
            return {
                "should_run_orb": False,
                "reason": "query_image_unreadable",
                "query_size": None,
                "template_size": None,
            }
        if template is None:
            return {
                "should_run_orb": False,
                "reason": "template_image_unreadable",
                "query_size": self._image_size(query),
                "template_size": None,
            }

        query_height, query_width = query.shape[:2]
        template_height, template_width = template.shape[:2]
        query_aspect = query_width / max(1, query_height)
        template_aspect = template_width / max(1, template_height)
        aspect_delta = abs(query_aspect - template_aspect) / max(query_aspect, template_aspect, 1e-6)
        query_orientation = self._orientation(query_width, query_height)
        template_orientation = self._orientation(template_width, template_height)
        orientation_matches = query_orientation == template_orientation
        normalization_fallback_used = bool(normalization_info.get("fallback_used"))
        crop_applied = bool(normalization_info.get("crop_applied"))
        perspective_applied = bool(normalization_info.get("perspective_applied"))
        geometry_matches = orientation_matches and aspect_delta <= self.ASPECT_RATIO_TOLERANCE
        should_skip = geometry_matches and not normalization_fallback_used
        reason = "normalized_geometry_matches_template" if should_skip else "geometry_mismatch_alignment_may_help"
        if normalization_fallback_used:
            reason = "normalization_fallback_alignment_may_help"

        return {
            "should_run_orb": not should_skip,
            "reason": reason,
            "query_size": [query_width, query_height],
            "template_size": [template_width, template_height],
            "query_aspect_ratio": round(float(query_aspect), 4),
            "template_aspect_ratio": round(float(template_aspect), 4),
            "aspect_ratio_delta": round(float(aspect_delta), 4),
            "query_orientation": query_orientation,
            "template_orientation": template_orientation,
            "orientation_matches": orientation_matches,
            "aspect_ratio_tolerance": self.ASPECT_RATIO_TOLERANCE,
            "geometry_matches": geometry_matches,
            "normalization_status": normalization_info.get("normalization_status"),
            "normalization_fallback_used": normalization_fallback_used,
            "crop_applied": crop_applied,
            "perspective_applied": perspective_applied,
        }

    def align_to_template(
        self,
        query_image_path: str,
        template_image_source: str,
        output_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        query = cv2.imread(str(query_image_path))
        template = self._load_template_image(template_image_source)

        if output_path is None:
            query_path = Path(query_image_path)
            output_path = str(query_path.with_name(f"{query_path.stem}_aligned.png"))
        match_output_path = self._match_visualization_path(output_path)

        if query is None:
            return self._result("failed", "query_image_unreadable", output_path, match_output_path, error="Unable to read query image")
        if template is None:
            return self._result("failed", "template_image_unreadable", output_path, match_output_path, error="Unable to read template image")

        query_gray = cv2.cvtColor(query, cv2.COLOR_BGR2GRAY)
        template_gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)

        orb = cv2.ORB_create(nfeatures=self.ORB_FEATURES)
        query_keypoints, query_descriptors = orb.detectAndCompute(query_gray, None)
        template_keypoints, template_descriptors = orb.detectAndCompute(template_gray, None)

        keypoints_query = len(query_keypoints or [])
        keypoints_template = len(template_keypoints or [])

        if query_descriptors is None or template_descriptors is None:
            return self._result(
                "failed",
                "insufficient_descriptors",
                output_path,
                None,
                keypoints_query=keypoints_query,
                keypoints_template=keypoints_template,
            )

        matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
        matches = sorted(matcher.match(query_descriptors, template_descriptors), key=lambda item: item.distance)
        good_matches = matches[: min(self.MAX_GOOD_MATCHES, len(matches))]
        if len(good_matches) < self.MIN_GOOD_MATCHES:
            return self._result(
                "failed",
                "insufficient_good_matches",
                output_path,
                None,
                keypoints_query=keypoints_query,
                keypoints_template=keypoints_template,
                matches=len(matches),
                good_matches=len(good_matches),
            )

        query_points = np.float32([query_keypoints[match.queryIdx].pt for match in good_matches]).reshape(-1, 1, 2)
        template_points = np.float32([template_keypoints[match.trainIdx].pt for match in good_matches]).reshape(-1, 1, 2)
        homography, mask = cv2.findHomography(query_points, template_points, cv2.RANSAC, 5.0)

        if homography is None or mask is None:
            return self._result(
                "failed",
                "homography_not_found",
                output_path,
                None,
                keypoints_query=keypoints_query,
                keypoints_template=keypoints_template,
                matches=len(matches),
                good_matches=len(good_matches),
                homography_found=False,
            )

        inliers = int(mask.ravel().sum())
        outliers = len(good_matches) - inliers
        inlier_ratio = inliers / max(1, len(good_matches))
        reprojection_error = self._mean_reprojection_error(
            homography,
            query_points,
            template_points,
            mask,
        )
        alignment_score = self._alignment_score(reprojection_error, inlier_ratio)
        inlier_matches = self._inlier_matches(good_matches, mask)
        match_path = (
            self._save_match_visualization(
                template,
                query,
                template_keypoints or [],
                query_keypoints or [],
                inlier_matches,
                match_output_path,
            )
            if inliers > 0
            else None
        )
        if inliers < self.MIN_INLIERS:
            return self._result(
                "failed",
                "insufficient_inliers",
                output_path,
                match_path,
                keypoints_query=keypoints_query,
                keypoints_template=keypoints_template,
                matches=len(matches),
                good_matches=len(good_matches),
                inliers=inliers,
                outliers=outliers,
                inlier_ratio=inlier_ratio,
                reprojection_error=reprojection_error,
                homography_found=True,
                homography=homography.tolist(),
                alignment_score=alignment_score,
            )
        if reprojection_error > self.MAX_REPROJECTION_ERROR:
            return self._result(
                "failed",
                "reprojection_error_too_high",
                output_path,
                match_path,
                keypoints_query=keypoints_query,
                keypoints_template=keypoints_template,
                matches=len(matches),
                good_matches=len(good_matches),
                inliers=inliers,
                outliers=outliers,
                inlier_ratio=inlier_ratio,
                reprojection_error=reprojection_error,
                homography_found=True,
                homography=homography.tolist(),
                alignment_score=alignment_score,
            )
        template_height, template_width = template.shape[:2]
        aligned = cv2.warpPerspective(
            query,
            homography,
            (template_width, template_height),
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=(255, 255, 255),
        )

        target_path = Path(output_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        warp_applied = bool(cv2.imwrite(str(target_path), aligned))
        if not warp_applied:
            return self._result(
                "failed",
                "warp_write_failed",
                output_path,
                match_path,
                keypoints_query=keypoints_query,
                keypoints_template=keypoints_template,
                matches=len(matches),
                good_matches=len(good_matches),
                inliers=inliers,
                outliers=outliers,
                inlier_ratio=inlier_ratio,
                reprojection_error=reprojection_error,
                homography_found=True,
                homography=homography.tolist(),
                alignment_score=alignment_score,
                error="Unable to write aligned image",
            )

        return self._result(
            "aligned",
            "aligned",
            output_path,
            match_path,
            keypoints_query=keypoints_query,
            keypoints_template=keypoints_template,
            matches=len(matches),
            good_matches=len(good_matches),
            inliers=inliers,
            outliers=outliers,
            inlier_ratio=inlier_ratio,
            reprojection_error=reprojection_error,
            homography_found=True,
            warp_applied=True,
            homography=homography.tolist(),
            alignment_score=alignment_score,
        )

    def _load_template_image(self, source: str):
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

        if not source_path.exists():
            return None

        return cv2.imread(str(source_path))

    def _image_size(self, image) -> list:
        height, width = image.shape[:2]
        return [width, height]

    def _orientation(self, width: int, height: int) -> str:
        if abs(width - height) / max(width, height, 1) <= 0.02:
            return "square"
        return "landscape" if width > height else "portrait"

    def _match_visualization_path(self, output_path: str) -> str:
        path = Path(output_path)
        return str(path.with_name(f"{path.stem}_matches.jpg"))

    def _save_match_visualization(
        self,
        template,
        query,
        template_keypoints,
        query_keypoints,
        query_to_template_matches,
        output_path: str,
    ) -> Optional[str]:
        try:
            display_matches = [
                cv2.DMatch(
                    _queryIdx=match.trainIdx,
                    _trainIdx=match.queryIdx,
                    _imgIdx=match.imgIdx,
                    _distance=match.distance,
                )
                for match in query_to_template_matches
            ]
            visualization = cv2.drawMatches(
                template,
                template_keypoints,
                query,
                query_keypoints,
                display_matches,
                None,
                flags=cv2.DrawMatchesFlags_NOT_DRAW_SINGLE_POINTS,
            )
            target_path = Path(output_path)
            target_path.parent.mkdir(parents=True, exist_ok=True)
            if cv2.imwrite(str(target_path), visualization):
                return str(target_path)
        except Exception:
            return None
        return None

    def _inlier_matches(self, matches, mask) -> list:
        inlier_mask = mask.ravel().astype(bool)
        return [match for match, keep in zip(matches, inlier_mask) if keep]

    def _mean_reprojection_error(
        self,
        homography,
        query_points,
        template_points,
        mask,
    ) -> float:
        inlier_mask = mask.ravel().astype(bool)
        if not inlier_mask.any():
            return float("inf")
        inlier_query_points = query_points[inlier_mask]
        inlier_template_points = template_points[inlier_mask]
        try:
            projected_query_points = cv2.perspectiveTransform(inlier_query_points, homography)
            distances = np.linalg.norm(projected_query_points.reshape(-1, 2) - inlier_template_points.reshape(-1, 2), axis=1)
            return round(float(distances.mean()), 4)
        except Exception:
            return float("inf")

    def _quality_from_error(self, reprojection_error: Optional[float]) -> float:
        if reprojection_error is None or not np.isfinite(reprojection_error):
            return 0.0
        if reprojection_error <= 1:
            return 1.0
        if reprojection_error <= 2:
            return 0.90
        if reprojection_error <= 3:
            return 0.80
        if reprojection_error <= 5:
            return 0.65
        if reprojection_error <= 8:
            return 0.45
        return 0.20

    def _alignment_score(self, reprojection_error: Optional[float], inlier_ratio: float) -> float:
        quality_from_error = self._quality_from_error(reprojection_error)
        score = (0.7 * quality_from_error) + (0.3 * max(0.0, min(1.0, inlier_ratio)))
        return round(float(max(0.0, min(1.0, score))), 4)

    def _result(
        self,
        status: str,
        reason: str,
        output_path: Optional[str],
        match_image_path: Optional[str],
        keypoints_query: int = 0,
        keypoints_template: int = 0,
        matches: int = 0,
        good_matches: int = 0,
        inliers: int = 0,
        outliers: Optional[int] = None,
        inlier_ratio: float = 0.0,
        reprojection_error: Optional[float] = None,
        homography_found: bool = False,
        warp_applied: bool = False,
        homography: Optional[list] = None,
        alignment_score: Optional[float] = None,
        error: Optional[str] = None,
    ) -> Dict[str, Any]:
        score = self._alignment_score(reprojection_error, inlier_ratio) if alignment_score is None else alignment_score
        safe_reprojection_error = (
            round(float(reprojection_error), 4)
            if reprojection_error is not None and np.isfinite(reprojection_error)
            else None
        )
        outlier_count = max(0, good_matches - inliers) if outliers is None else outliers
        debug = {
            "method": "ORB",
            "query_keypoints": keypoints_query,
            "template_keypoints": keypoints_template,
            "raw_matches": matches,
            "good_matches": good_matches,
            "inliers": inliers,
            "outliers": outlier_count,
            "inlier_ratio": round(float(inlier_ratio), 4),
            "reprojection_error": safe_reprojection_error,
            "reprojection_error_px": safe_reprojection_error,
            "homography_found": homography_found,
            "warp_applied": warp_applied,
            "alignment_score": score,
            "reason": reason,
        }
        return {
            "alignment_status": status,
            "alignment_success": status == "aligned",
            "aligned_image_path": output_path if status == "aligned" else None,
            "alignment_match_image_path": match_image_path,
            "alignment_debug": debug,
            "method": "ORB",
            "keypoints_query": keypoints_query,
            "keypoints_template": keypoints_template,
            "matches": matches,
            "good_matches": good_matches,
            "inliers": inliers,
            "outliers": outlier_count,
            "inlier_ratio": debug["inlier_ratio"],
            "reprojection_error_px": safe_reprojection_error,
            "homography": homography,
            "homography_found": homography_found,
            "warp_applied": warp_applied,
            "alignment_score": score,
            "error": error,
        }
