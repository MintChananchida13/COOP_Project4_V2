"""User detection transport: retain workspace data, omit diagnostics."""

from typing import Any
from pathlib import Path


_OMIT = {
    "debug", "timing", "layout_debug", "alignment_debug", "coordinate_debug",
    "normalization", "matching_normalization", "alignment",
    "original_image_preview_url", "normalized_image_preview_url",
    "matching_image_preview_url", "alignment_match_image_preview_url",
    "selected_processing_preview_url", "image_preview_data_url",
    "extraction_image_preview_url", "aligned_image_preview_url",
}
_LOG_METADATA = {
    "version_name", "template_version", "template_version_name",
    "version_number", "detection_mode",
}


def without_image_payloads(value: Any) -> Any:
    """Omit previews and image assets from responses that need only metadata."""
    if isinstance(value, list):
        return [without_image_payloads(item) for item in value
                if not (isinstance(item, str) and item.startswith("data:image/"))]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        lowered = key.lower()
        image_field = ("preview" in lowered or "image" in lowered or "sample_file" in lowered)
        if image_field and (lowered.endswith(("url", "base64", "dataurl")) or lowered.endswith("data_url")):
            continue
        if isinstance(item, str) and item.startswith("data:image/"):
            continue
        result[key] = without_image_payloads(item)
    return result


def _trim(value: Any) -> Any:
    if isinstance(value, list):
        return [_trim(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        if key in _OMIT or key.endswith("_debug"):
            continue
        # decision_path is a decision label, not a server filesystem path.
        if key != "decision_path" and (key.endswith("_path") or key.endswith("_paths")):
            continue
        if key == "metadata":
            result[key] = {name: item[name] for name in _LOG_METADATA if name in item} if isinstance(item, dict) else {}
        else:
            result[key] = _trim(item)
    return result


def user_detection_response(value: dict) -> dict:
    result = without_image_payloads(_trim(value))
    selected_template = (value.get("best_candidate") or {}).get("template_id")
    for page, output in zip(value.get("pages") or [], result.get("pages") or []):
        candidate = next((item for item in page.get("candidates", [])
                          if item.get("template_id") == selected_template), None)
        candidate = candidate or page.get("best_candidate") or {}
        image = candidate.get("extraction_image_preview_url")
        image_path = candidate.get("extraction_image_path")
        if candidate.get("selected_processing_source") == "aligned":
            if candidate.get("aligned_image_preview_url"):
                image = candidate["aligned_image_preview_url"]
                image_path = candidate.get("aligned_image_path")
        if not image:
            image = candidate.get("aligned_image_preview_url")
            image_path = candidate.get("aligned_image_path")
        original_path = page.get("original_image_path")
        same_path = bool(image_path and original_path and
                         Path(image_path).resolve() == Path(original_path).resolve())
        same_preview = bool(image and image == page.get("original_image_preview_url"))
        if same_path or same_preview:
            output["processing_image_source"] = "original"
            continue
        if image:
            output["processing_image_preview_url"] = image
            output["processing_template_id"] = candidate.get("template_id")
            output["processing_image_source"] = "processed"
    # The user processing log consumes strategy and summary timing. Keep those
    # values, without per-candidate breakdowns, call traces or file artifacts.
    debug = value.get("debug") or {}
    timing = debug.get("timing") or {}
    result["debug"] = {
        "verification_strategy": debug.get("verification_strategy"),
        "timing": {
            key: item for key, item in timing.items()
            if isinstance(item, (int, float, bool)) or item is None
        },
    }
    return result
