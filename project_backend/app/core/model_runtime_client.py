import base64
import json
import logging
import os
import time
import urllib.error
import urllib.request
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

import cv2
import numpy as np

from app.core.config import GATEWAY_URL
from app.core.ocr_model_config import DEFAULT_OCR_MODEL_SETTINGS, normalize_saved_ocr_settings
from app.core.db import connect as connect_db
from app.core.json_utils import jsonb_load
from app.processing import memory_usage


logger = logging.getLogger(__name__)


class ModelRuntimeUnavailableError(RuntimeError):
    pass


class ModelRuntimeKind(str, Enum):
    LAYOUT = "layout"
    TEXT_DETECTION = "text_detection"
    TEXT_RECOGNITION = "text_recognition"
    TABLE = "table"
    IMAGE_VERIFICATION = "image_verification"


MODEL_RUNTIME_GATEWAY_PATH: Dict[ModelRuntimeKind, str] = {
    ModelRuntimeKind.LAYOUT: "/paddle/api/v1/layout-prediction-batches",
    ModelRuntimeKind.TEXT_DETECTION: "/paddle/api/v1/text-detections?version=v6",
    ModelRuntimeKind.TEXT_RECOGNITION: "/paddle/api/v1/text-recognitions",
    ModelRuntimeKind.TABLE: "/paddle/api/v1/table-model-results?det_version=6&rec_version=5&det_model=baseline&rec_model=thai_ft_v3",
    ModelRuntimeKind.IMAGE_VERIFICATION: "/paddle/api/v1/image-verifications",
}

OCR_MODEL_SETTINGS_KEY = "ocr_model_settings"


def _active_ocr_model(kind: str) -> Dict[str, Any]:
    settings = DEFAULT_OCR_MODEL_SETTINGS
    try:
        with connect_db() as conn:
            row = conn.execute(
                "SELECT value FROM app_settings WHERE key = ?",
                (OCR_MODEL_SETTINGS_KEY,),
            ).fetchone()
        loaded = jsonb_load(row["value"] if row else None, None)
        if isinstance(loaded, dict):
            settings = normalize_saved_ocr_settings(loaded)
        elif row is not None:
            raise ValueError("Invalid OCR model settings")
    except Exception as error:
        raise ModelRuntimeUnavailableError("Unable to load active OCR model settings") from error

    models = ((settings.get("models") if isinstance(settings, dict) else {}) or {}).get(kind)
    active_id = ((settings.get("active") if isinstance(settings, dict) else {}) or {}).get(kind)
    if isinstance(models, list):
        for model in models:
            if isinstance(model, dict) and model.get("id") == active_id:
                return model
    raise ModelRuntimeUnavailableError(f"Configured active {kind} model {active_id!r} was not found")


def _gateway_api_path(path: str) -> str:
    # Existing OCR settings can still contain the original API prefix.
    if path == "/api/v1" or path.startswith("/api/v1/"):
        return f"/paddle{path}"
    return path


def _active_ocr_path(kind: str, mode: str) -> str:
    model = _active_ocr_model(kind)
    key = "batch_api_path" if mode == "batch" else "single_api_path"
    path = str(model.get(key) or "").strip()
    if path.startswith("/"):
        return _gateway_api_path(path)
    raise ModelRuntimeUnavailableError(f"Configured {kind} model {model.get('id')!r} has an invalid {key}")


def runtime_url(kind: ModelRuntimeKind) -> Optional[str]:
    if os.getenv("MODEL_RUNTIME_ROLE", "").strip().lower() == "service":
        return None
    gateway_url = GATEWAY_URL.strip().rstrip("/")
    return f"{gateway_url}{MODEL_RUNTIME_GATEWAY_PATH[kind]}" if gateway_url else None


def is_runtime_configured(kind: ModelRuntimeKind) -> bool:
    return bool(runtime_url(kind))


def configured_runtimes() -> Dict[str, Optional[str]]:
    return {kind.value: runtime_url(kind) for kind in ModelRuntimeKind}


def _gateway_headers() -> Dict[str, str]:
    headers = {"Content-Type": "application/json"}
    api_key = os.getenv("MODEL_GATEWAY_API_KEY", "").strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _image_to_data_url(image: np.ndarray) -> str:
    return "data:image/png;base64," + base64.b64encode(_image_to_png(image)).decode("ascii")


def _image_to_png(image: np.ndarray) -> bytes:
    if image is None or image.size == 0:
        raise ValueError("Invalid image for model runtime request.")
    ok, encoded = cv2.imencode(".png", image)
    if not ok:
        raise ValueError("Unable to encode image for model runtime request.")
    return encoded.tobytes()


def _path_to_data_url(image_path: str) -> str:
    path = Path(image_path)
    if not path.exists():
        raise ValueError(f"Model runtime input image not found: {image_path}")
    suffix = path.suffix.lower().lstrip(".") or "png"
    mime = "jpeg" if suffix in {"jpg", "jpeg"} else suffix
    return f"data:image/{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def _json_size_bytes(value: Any) -> int:
    try:
        return len(json.dumps(value).encode("utf-8"))
    except Exception:
        return 0


def _field_size_summary(value: Any, limit: int = 12, *, measure_bytes: bool = True) -> List[Dict[str, Any]]:
    if not isinstance(value, dict):
        return []
    items = []
    for key, field_value in value.items():
        # Table raw output can contain full pixel arrays. Diagnostic sizing must
        # not re-serialize those arrays at each nested response level.
        size_bytes = _json_size_bytes(field_value) if measure_bytes else None
        item: Dict[str, Any] = {
            "field": str(key),
            "bytes": size_bytes,
            "type": type(field_value).__name__,
        }
        if isinstance(field_value, str):
            item["string_length"] = len(field_value)
            item["looks_like_base64_image"] = field_value.startswith("data:image") or len(field_value) > 100000
        elif isinstance(field_value, list):
            item["item_count"] = len(field_value)
        elif isinstance(field_value, dict):
            item["field_count"] = len(field_value)
        items.append(item)
    return sorted(items, key=lambda item: int(item.get("bytes") or 0), reverse=True)[:limit]


def _extract_gateway_timing(parsed: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Read Runtime timing, preserving milliseconds and avoiding duplicates."""
    result_value = parsed.get("result")
    if result_value is None and "data" in parsed:
        result_value = parsed.get("data")

    candidates: List[Any] = [parsed.get("timing"), parsed.get("debug_timing")]
    debug = parsed.get("debug")
    if isinstance(debug, dict):
        candidates.append(debug.get("timing"))
    if isinstance(result_value, dict):
        candidates.append(result_value.get("timing"))
        result_debug = result_value.get("debug")
        if isinstance(result_debug, dict):
            candidates.append(result_debug.get("timing"))

    selected: Dict[str, Any] = {}
    for candidate in candidates:
        if isinstance(candidate, dict):
            for key, value in candidate.items():
                if key not in selected:
                    selected[key] = value

    # API Service OCR timing is sometimes a sibling of data.timing.
    # Fill only missing keys so an explicit timing object wins once.
    for key in ("text_det_inference_ms", "text_rec_inference_ms", "inference_ms", "image_siglip_runtime_ms", "siglip_inference_ms", "layout_runtime_ms", "table_runtime_ms"):
        if key in selected:
            continue
        if isinstance(result_value, dict) and isinstance(result_value.get(key), (int, float)):
            selected[key] = result_value[key]
        elif isinstance(parsed.get(key), (int, float)):
            selected[key] = parsed[key]
    return selected or None


from app.processing.search_benchmark import instrument_runtime
from app.processing.extraction_timing import instrument_runtime as instrument_extraction_runtime


@instrument_runtime
@instrument_extraction_runtime
def _post_predict(
    kind: ModelRuntimeKind,
    payload: Dict[str, Any],
    timeout: float = 120.0,
    path_override: Optional[str] = None,
    timing: Optional[Dict[str, Any]] = None,
    multipart_images: Optional[List[bytes]] = None,
) -> Any:
    if path_override:
        gateway_url = GATEWAY_URL.strip().rstrip("/")
        endpoint_url = f"{gateway_url}{_gateway_api_path(path_override)}" if gateway_url else None
    else:
        endpoint_url = runtime_url(kind)
    if not endpoint_url:
        raise ModelRuntimeUnavailableError("GATEWAY_URL is not configured.")

    started = time.perf_counter()
    serialize_started = time.perf_counter()
    headers = _gateway_headers()
    if multipart_images is None:
        body = json.dumps(payload).encode("utf-8")
    else:
        boundary = uuid4().hex
        parts = []
        for index, image in enumerate(multipart_images):
            parts.extend([
                f'--{boundary}\r\nContent-Disposition: form-data; name="images"; filename="image_{index}.png"\r\nContent-Type: image/png\r\n\r\n'.encode("ascii"),
                image,
                b"\r\n",
            ])
        parts.append(f"--{boundary}--\r\n".encode("ascii"))
        body = b"".join(parts)
        del parts, multipart_images
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    payload_bytes = len(body)
    if timing is not None:
        timing["model_call_id"] = uuid4().hex
        timing["model_kind"] = kind.value
        timing["multipart_serialize_ms" if headers["Content-Type"].startswith("multipart/") else "json_serialize_ms"] = round((time.perf_counter() - serialize_started) * 1000.0, 2)
        timing["payload_bytes"] = payload_bytes
        timing["endpoint_path"] = urllib.request.urlparse(endpoint_url).path
    request = urllib.request.Request(
        endpoint_url,
        data=body,
        headers=headers,
        method="POST",
    )
    monotonic_started = time.monotonic()
    monotonic_ended = None
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            read_started = time.perf_counter()
            raw_bytes = memory_usage.call('gateway_response_read', response.read)
            read_elapsed = time.perf_counter() - read_started
            decode_started = time.perf_counter()
            raw = memory_usage.call('gateway_response_decode', raw_bytes.decode, "utf-8")
            decode_elapsed = time.perf_counter() - decode_started
        monotonic_ended = time.monotonic()
        if timing is not None:
            timing["response_read_ms"] = round(read_elapsed * 1000.0, 2)
            timing["response_decode_ms"] = round(decode_elapsed * 1000.0, 2)
            timing["response_bytes"] = len(raw_bytes)
        # The decoded text is sufficient for the existing JSON decoder. Do not
        # hold the bytes copy through parsing and reconstruction as well.
        del raw_bytes
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        logger.info(
            "Model Runtime timing: kind=%s status=%s payload_bytes=%s elapsed=%.3fs",
            kind.value,
            error.code,
            payload_bytes,
            time.perf_counter() - started,
        )
        raise ModelRuntimeUnavailableError(f"{kind.value} runtime HTTP {error.code}: {detail}") from error
    except OSError as error:
        logger.info(
            "Model Runtime timing: kind=%s error=%s payload_bytes=%s elapsed=%.3fs",
            kind.value,
            error,
            payload_bytes,
            time.perf_counter() - started,
        )
        raise ModelRuntimeUnavailableError(f"{kind.value} runtime unavailable: {error}") from error
    finally:
        if timing is not None:
            timing["model_call_start_monotonic"] = monotonic_started
            timing["model_call_end_monotonic"] = monotonic_ended if monotonic_ended is not None else time.monotonic()
            timing["http_request_ms"] = round((timing["model_call_end_monotonic"] - monotonic_started) * 1000.0, 2)
            timing["model_call_interval_ms"] = timing["http_request_ms"]

    # Request body and image-containing payload are no longer used after HTTP.
    del body, request, payload
    memory_usage.checkpoint('gateway_buffers_released')
    try:
        parse_started = time.perf_counter()
        parsed = memory_usage.call('gateway_json_parse', json.loads, raw)
        if timing is not None:
            timing["json_parse_ms"] = round((time.perf_counter() - parse_started) * 1000.0, 2)
    except json.JSONDecodeError as error:
        raise ModelRuntimeUnavailableError(f"{kind.value} runtime returned invalid JSON.") from error
    del raw
    memory_usage.checkpoint('gateway_json_text_released')

    if kind == ModelRuntimeKind.IMAGE_VERIFICATION:
        # SigLIP's current gateway uses {data: object, meta: object}.
        # Do not accept a legacy vector, null/list data, or an error envelope
        # as a successful inference. Keep other model transports unchanged.
        if not isinstance(parsed, dict):
            raise ModelRuntimeUnavailableError("image_verification runtime returned an invalid response envelope.")
        if parsed.get("error") is not None or parsed.get("success", True) is not True:
            raise ModelRuntimeUnavailableError(f"image_verification runtime failed: {parsed.get('error') or parsed.get('detail')}")
        if not isinstance(parsed.get("data"), dict) or not isinstance(parsed.get("meta"), dict) or "result" in parsed:
            raise ModelRuntimeUnavailableError("image_verification runtime returned an invalid response envelope.")

    if not parsed.get("success", True):
        detail = parsed.get("detail") or parsed.get("error") or "Model runtime request failed."
        raise ModelRuntimeUnavailableError(f"{kind.value} runtime failed: {detail}")

    sources = [parsed, parsed.get("result"), parsed.get("data")]
    selections = [source.get("model_selection") for source in sources
                  if isinstance(source, dict) and isinstance(source.get("model_selection"), dict)]
    runtime_model = parsed.get("model") or next((selection.get("model_name") for selection in selections
                                                if selection.get("model_name")), None)
    selected_id = parse_qs(urlsplit(path_override or "").query).get("model_id", [""])[0]
    confirmed_ids = {str(selection.get("model_id") or selection.get("runtime_model_id") or "")
                     for selection in selections}
    if selected_id and selected_id != "default" and selected_id not in confirmed_ids and not str(runtime_model or "").endswith(f":{selected_id}"):
        raise ModelRuntimeUnavailableError(f"{kind.value} runtime did not confirm selected model_id={selected_id}")

    logger.info(
        "Model Runtime timing: kind=%s model=%s payload_bytes=%s elapsed=%.3fs",
        kind.value,
        runtime_model,
        payload_bytes,
        time.perf_counter() - started,
    )

    if timing is not None:
        diagnostics_started = time.perf_counter()
        result_value = parsed.get("result")
        if result_value is None and "data" in parsed:
            result_value = parsed.get("data")

        gateway_timing = _extract_gateway_timing(parsed)

        if isinstance(gateway_timing, dict):
            timing["gateway_timing"] = gateway_timing

        measure_bytes = kind != ModelRuntimeKind.TABLE
        timing["field_size_measurement"] = "serialized_bytes" if measure_bytes else "metadata_only"
        timing["response_field_sizes"] = _field_size_summary(parsed, measure_bytes=measure_bytes)

        if isinstance(result_value, dict):
            timing["result_field_sizes"] = _field_size_summary(result_value, measure_bytes=measure_bytes)
            raw_value = result_value.get("raw")
            if isinstance(raw_value, dict):
                timing["raw_field_sizes"] = _field_size_summary(raw_value, measure_bytes=measure_bytes)
                layout_items = raw_value.get("layout")
                if (
                    isinstance(layout_items, list)
                    and layout_items
                    and isinstance(layout_items[0], dict)
                ):
                    timing["layout_item_field_sizes"] = _field_size_summary(layout_items[0], measure_bytes=measure_bytes)

        timing["model"] = runtime_model
        timing["response_diagnostics_ms"] = round((time.perf_counter() - diagnostics_started) * 1000.0, 2)
        timing["total_runtime_client_ms"] = round(
            (time.perf_counter() - started) * 1000.0, 2
        )

    result = parsed.get("result")
    if result is None and "data" in parsed:
        result = parsed.get("data")
    if kind in {ModelRuntimeKind.TEXT_DETECTION, ModelRuntimeKind.TEXT_RECOGNITION} and isinstance(result, dict) and runtime_model and "model" not in result:
        result = {**result, "model": runtime_model}
    if kind == ModelRuntimeKind.LAYOUT:
        return result
    return result if isinstance(result, dict) else parsed


def remote_analyze_layout(
    image: np.ndarray,
    timing: Optional[Dict[str, Any]] = None,
    layout_only: bool = True,
) -> Optional[Dict[str, Any]]:
    """Return one raw Layout prediction from a one-image batch, without DET.

    layout_only remains accepted for existing callers; this transport always
    uses the layout-only leaf endpoint.
    """
    if not is_runtime_configured(ModelRuntimeKind.LAYOUT):
        return None
    encode_started = time.perf_counter()
    image_data_url = _image_to_data_url(image)
    if timing is not None:
        timing["image_encode_ms"] = round((time.perf_counter() - encode_started) * 1000.0, 2)
        timing["image_data_url_bytes"] = len(image_data_url)
        try:
            timing["image_shape"] = [int(image.shape[1]), int(image.shape[0])]
        except Exception:
            pass
    batch = _post_predict(
        ModelRuntimeKind.LAYOUT,
        {"images": [image_data_url]},
        timing=timing,
    )
    if not isinstance(batch, list) or len(batch) != 1:
        raise ModelRuntimeUnavailableError("Layout runtime must return exactly one result for one image.")
    layout = batch[0]
    if not isinstance(layout, dict) or layout.get("error") is not None:
        raise ModelRuntimeUnavailableError("Layout runtime returned an invalid prediction.")
    prediction = layout.get("res", layout)
    if not isinstance(prediction, dict) or not isinstance(prediction.get("boxes"), list):
        raise ModelRuntimeUnavailableError("Layout prediction must contain a boxes list.")

    return layout


def remote_detect_text_boxes(image_path: str, timing: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    if not is_runtime_configured(ModelRuntimeKind.TEXT_DETECTION):
        return None
    encode_started = time.perf_counter()
    image_data_url = _path_to_data_url(image_path)
    if timing is not None:
        timing["image_encode_ms"] = round((time.perf_counter() - encode_started) * 1000.0, 2)
        timing["image_data_url_bytes"] = len(image_data_url)
    return _post_predict(
        ModelRuntimeKind.TEXT_DETECTION,
        {"image": image_data_url},
        path_override=_active_ocr_path("text_detection", "single"),
        timing=timing,
    )


def remote_detect_text_boxes_batch(images: List[np.ndarray], timing: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    if not is_runtime_configured(ModelRuntimeKind.TEXT_DETECTION):
        return None
    encode_started = time.perf_counter()
    image_bytes = [_image_to_png(image) for image in images]
    if timing is not None:
        timing["image_encode_ms"] = round((time.perf_counter() - encode_started) * 1000.0, 2)
        timing["image_count"] = len(images)
        timing["image_png_bytes"] = sum(len(item) for item in image_bytes)
        timing["image_shapes"] = [
            [int(image.shape[1]), int(image.shape[0])]
            for image in images
            if getattr(image, "shape", None) is not None and len(image.shape) >= 2
        ]
    return _post_predict(
        ModelRuntimeKind.TEXT_DETECTION,
        {},
        timeout=240.0,
        path_override=_active_ocr_path("text_detection", "batch"),
        timing=timing,
        multipart_images=image_bytes,
    )


def remote_recognize_image(image: np.ndarray, timing: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    if not is_runtime_configured(ModelRuntimeKind.TEXT_RECOGNITION):
        return None
    return _post_predict(
        ModelRuntimeKind.TEXT_RECOGNITION,
        {"image": _image_to_data_url(image)},
        path_override=_active_ocr_path("text_recognition", "single"),
        timing=timing,
    )


def remote_recognize_images(images: List[np.ndarray], timing: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    if not is_runtime_configured(ModelRuntimeKind.TEXT_RECOGNITION):
        return None
    encode_started = time.perf_counter()
    image_bytes = [_image_to_png(image) for image in images]
    if timing is not None:
        timing["image_encode_ms"] = round((time.perf_counter() - encode_started) * 1000.0, 2)
        timing["image_count"] = len(images)
        timing["image_png_bytes"] = sum(len(item) for item in image_bytes)
    return _post_predict(
        ModelRuntimeKind.TEXT_RECOGNITION,
        {},
        timeout=240.0,
        path_override=_active_ocr_path("text_recognition", "batch"),
        timing=timing,
        multipart_images=image_bytes,
    )


def remote_recognize_table_raw(image: np.ndarray, timing: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    if not is_runtime_configured(ModelRuntimeKind.TABLE):
        return None

    encode_started = time.perf_counter()
    image_data_url = memory_usage.call('table_image_encode', _image_to_data_url, image)
    if timing is not None:
        timing["image_encode_ms"] = round((time.perf_counter() - encode_started) * 1000.0, 2)
    return _post_predict(
        ModelRuntimeKind.TABLE,
        {"image": image_data_url},
        timeout=240.0,
        timing=timing,
    )

def remote_verify_image_logits(
    image_path: str,
    image_category: str,
    categories: Optional[List[Dict[str, Any]]] = None,
    timing: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    if not is_runtime_configured(ModelRuntimeKind.IMAGE_VERIFICATION):
        return None
    return _post_predict(
        ModelRuntimeKind.IMAGE_VERIFICATION,
        {
            "image": _path_to_data_url(image_path),
            "image_category": image_category,
            "categories": categories or [],
        },
        timeout=240.0,
        timing=timing,
    )


def remote_verify_image_logits_batch(
    image_paths: List[str],
    categories: Optional[List[Dict[str, Any]]] = None,
    timing: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    if not is_runtime_configured(ModelRuntimeKind.IMAGE_VERIFICATION):
        return None
    encode_started = time.perf_counter()
    images = [image_path if isinstance(image_path, str) and image_path.startswith("data:image/") else _path_to_data_url(image_path)
              for image_path in image_paths]
    if timing is not None:
        timing["image_encode_ms"] = round((time.perf_counter() - encode_started) * 1000.0, 2)
        timing["image_count"] = len(images)
        timing["image_data_url_bytes"] = sum(len(item) for item in images)
    return _post_predict(
        ModelRuntimeKind.IMAGE_VERIFICATION,
        {
            "images": images,
            "categories": categories or [],
        },
        timeout=240.0,
        path_override="/paddle/api/v1/image-classification-batches",
        timing=timing,
    )


def probe_ocr_model(kind: str, model: Dict[str, Any]) -> Dict[str, Any]:
    """Exercise the configured single and batch inference routes, not a registry."""
    runtime_kind = ModelRuntimeKind.TEXT_DETECTION if kind == "text_detection" else ModelRuntimeKind.TEXT_RECOGNITION
    image = np.full((96, 384, 3), 255, dtype=np.uint8)
    cv2.putText(image, "OCR TEST 123", (12, 62), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 0), 2)
    encoded = _image_to_data_url(image)
    checked_paths = {}
    timings = {}
    runtime_models = {}
    for mode, key in (("single", "single_api_path"), ("batch", "batch_api_path")):
        path = _gateway_api_path(str(model.get(key) or "").strip())
        if not path.startswith("/") or path.startswith("//"):
            raise ModelRuntimeUnavailableError(f"Invalid {key}")
        timing = {}
        payload = {"image": encoded} if mode == "single" else {"images": [encoded]}
        _post_predict(runtime_kind, payload, path_override=path, timing=timing)
        if not timing.get("model"):
            raise ModelRuntimeUnavailableError(f"{mode} inference endpoint did not identify its model")
        expected_name = str(model.get("model_name") or "").strip()
        actual_name = str(timing["model"])
        if expected_name and actual_name != expected_name and not actual_name.startswith(f"{expected_name}:"):
            raise ModelRuntimeUnavailableError(f"{mode} inference endpoint returned model {actual_name!r}, expected {expected_name!r}")
        runtime_models[mode] = actual_name
        checked_paths[mode] = path
        timings[mode] = timing
    return {"model_id": model["id"], "processing_verified": True, "validation_scope": "processing_api",
            "checked_paths": checked_paths, "models": runtime_models, "timing": timings}
