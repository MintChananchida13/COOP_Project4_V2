"""User detection transport: retain workspace data, omit diagnostics."""

from typing import Any
from pathlib import Path
import json
import filecmp


_OMIT = {
    "debug", "timing", "layout_debug", "alignment_debug", "coordinate_debug",
    "normalization", "matching_normalization", "alignment",
    "original_image_preview_url", "normalized_image_preview_url",
    "matching_image_preview_url", "alignment_match_image_preview_url",
    "selected_processing_preview_url", "image_preview_data_url",
    "extraction_image_preview_url", "aligned_image_preview_url",
    "extraction_test",
}
_LOG_METADATA = {
    "version_name", "template_version", "template_version_name",
    "version_number", "detection_mode",
}


def without_image_payloads(value: Any, _memo=None) -> Any:
    """Omit previews and image assets from responses that need only metadata."""
    if _memo is None:
        _memo = {}
    if isinstance(value, (dict, list)) and id(value) in _memo:
        return _memo[id(value)]
    if isinstance(value, list):
        result = []
        _memo[id(value)] = result
        result.extend(without_image_payloads(item, _memo) for item in value
                      if not (isinstance(item, str) and item.startswith("data:image/")))
        return result
    if not isinstance(value, dict):
        return value
    result = {}
    _memo[id(value)] = result
    for key, item in value.items():
        lowered = key.lower()
        image_field = ("preview" in lowered or "image" in lowered or "sample_file" in lowered)
        if image_field and (lowered.endswith(("url", "base64", "dataurl")) or lowered.endswith("data_url")):
            continue
        if isinstance(item, str) and item.startswith("data:image/"):
            continue
        result[key] = without_image_payloads(item, _memo)
    return result


def _trim(value: Any, _memo=None) -> Any:
    if _memo is None:
        _memo = {}
    if isinstance(value, (dict, list)) and id(value) in _memo:
        return _memo[id(value)]
    if isinstance(value, list):
        result = []
        _memo[id(value)] = result
        result.extend(_trim(item, _memo) for item in value)
        return result
    if not isinstance(value, dict):
        return value
    result = {}
    _memo[id(value)] = result
    for key, item in value.items():
        if key in _OMIT or key.endswith("_debug"):
            continue
        # decision_path is a decision label, not a server filesystem path.
        if key != "decision_path" and (key.endswith("_path") or key.endswith("_paths")):
            continue
        if key == "metadata":
            result[key] = {name: item[name] for name in _LOG_METADATA if name in item} if isinstance(item, dict) else {}
        else:
            result[key] = _trim(item, _memo)
    return result


def user_detection_response(value: dict, *, preview_loader=None, compact_candidates: bool = False) -> dict:
    result = without_image_payloads(_trim(value))
    best = value.get("best_candidate") or {}
    selected_template = best.get("template_id") if value.get("matched") and best.get("final_passed") else None
    for page, output in zip(value.get("pages") or [], result.get("pages") or []):
        if not selected_template:
            continue
        candidate = next((item for item in page.get("candidates", [])
                          if item.get("template_id") == selected_template and item.get("final_passed")), None)
        if candidate is None:
            page_best = page.get("best_candidate") or {}
            if page_best.get("template_id") == selected_template and page_best.get("final_passed"):
                candidate = page_best
        if candidate is None:
            continue
        image = candidate.get("extraction_image_preview_url")
        image_path = candidate.get("extraction_image_path")
        if candidate.get("selected_processing_source") == "aligned":
            if candidate.get("aligned_image_preview_url") or candidate.get("aligned_image_path"):
                image = candidate.get("aligned_image_preview_url")
                image_path = candidate.get("aligned_image_path")
        if not image and not image_path:
            image = candidate.get("aligned_image_preview_url")
            image_path = candidate.get("aligned_image_path")
        original_path = page.get("original_image_path")
        same_path = bool(image_path and original_path and
                         Path(image_path).resolve() == Path(original_path).resolve())
        same_preview = bool(image and image == page.get("original_image_preview_url"))
        same_file = False
        if preview_loader and image_path and original_path and not same_path:
            try:
                same_file = filecmp.cmp(image_path, original_path, shallow=False)
            except OSError:
                pass
        if same_path or same_preview or same_file:
            output["processing_image_source"] = "original"
            continue
        if not image and image_path and preview_loader:
            image = preview_loader(image_path)
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
    return compact_user_candidates(result) if compact_candidates else result


def compact_user_candidates(result: dict) -> dict:
    """Intern complete candidate variants without merging page/aggregate results."""
    pool = []
    references = {}
    identities = {}

    def reference(candidate):
        if not isinstance(candidate, dict):
            return candidate
        identity = id(candidate)
        if identity in identities:
            return {"candidate_ref": identities[identity]}
        key = json.dumps(candidate, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        index = references.get(key)
        if index is None:
            index = len(pool)
            references[key] = index
            pool.append(candidate)
        identities[identity] = index
        return {"candidate_ref": index}

    compact = dict(result)
    compact["best_candidate"] = reference(result.get("best_candidate"))
    compact["candidates"] = [reference(item) for item in result.get("candidates", [])]
    def compact_page(page):
        output = dict(page)
        if "best_candidate" in page:
            output["best_candidate"] = reference(page["best_candidate"])
        if "candidates" in page:
            output["candidates"] = [reference(item) for item in page["candidates"]]
        return output
    compact["pages"] = [compact_page(page) for page in result.get("pages", [])]
    compact["candidate_pool"] = pool
    return compact
