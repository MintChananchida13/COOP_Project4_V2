import base64
import json
import logging
import os
import time
import urllib.error
import urllib.request
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

import cv2
import numpy as np

from app.core.config import GATEWAY_URL
from app.core.db import connect as connect_db
from app.core.json_utils import jsonb_load


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
    ModelRuntimeKind.LAYOUT: "/api/v1/document-layouts",
    ModelRuntimeKind.TEXT_DETECTION: "/api/v1/text-detections?version=v6",
    ModelRuntimeKind.TEXT_RECOGNITION: "/api/v1/text-recognitions",
    ModelRuntimeKind.TABLE: "/api/v1/table-model-results",
    ModelRuntimeKind.IMAGE_VERIFICATION: "/api/v1/image-classifications",
}

OCR_MODEL_SETTINGS_KEY = "ocr_model_settings"
DEFAULT_OCR_MODEL_SETTINGS: Dict[str, Any] = {
    "active": {
        "text_detection": "ocr_det_v6_medium",
        "text_recognition": "thai_ocr_v5_mobile",
    },
    "models": {
        "text_detection": [
            {
                "id": "ocr_det_v6_medium",
                "display_name": "PP-OCRv6 Medium",
                "single_api_path": "/api/v1/text-detections?version=v6",
                "batch_api_path": "/api/v1/text-detection-batches?version=v6",
            },
        ],
        "text_recognition": [
            {
                "id": "thai_ocr_v5_mobile",
                "display_name": "Thai PP-OCRv5 Mobile",
                "single_api_path": "/api/v1/text-recognitions",
                "batch_api_path": "/api/v1/text-recognition-batches",
            },
        ],
    },
}


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
            settings = loaded
    except Exception as error:
        logger.warning("Unable to load OCR model settings; using defaults: %s", error)

    models = ((settings.get("models") if isinstance(settings, dict) else {}) or {}).get(kind)
    active_id = ((settings.get("active") if isinstance(settings, dict) else {}) or {}).get(kind)
    if isinstance(models, list):
        for model in models:
            if isinstance(model, dict) and model.get("id") == active_id:
                return model
        for model in models:
            if isinstance(model, dict):
                return model
    return DEFAULT_OCR_MODEL_SETTINGS["models"][kind][0]


def _active_ocr_path(kind: str, mode: str) -> str:
    model = _active_ocr_model(kind)
    key = "batch_api_path" if mode == "batch" else "single_api_path"
    path = str(model.get(key) or "").strip()
    if path.startswith("/"):
        return path
    return DEFAULT_OCR_MODEL_SETTINGS["models"][kind][0][key]


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
    if image is None or image.size == 0:
        raise ValueError("Invalid image for model runtime request.")
    ok, encoded = cv2.imencode(".png", image)
    if not ok:
        raise ValueError("Unable to encode image for model runtime request.")
    return "data:image/png;base64," + base64.b64encode(encoded.tobytes()).decode("ascii")


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


def _field_size_summary(value: Any, limit: int = 12) -> List[Dict[str, Any]]:
    if not isinstance(value, dict):
        return []
    items = []
    for key, field_value in value.items():
        size_bytes = _json_size_bytes(field_value)
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


def _post_predict(
    kind: ModelRuntimeKind,
    payload: Dict[str, Any],
    timeout: float = 120.0,
    path_override: Optional[str] = None,
    timing: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if path_override:
        gateway_url = GATEWAY_URL.strip().rstrip("/")
        endpoint_url = f"{gateway_url}{path_override}" if gateway_url else None
    else:
        endpoint_url = runtime_url(kind)
    if not endpoint_url:
        raise ModelRuntimeUnavailableError("GATEWAY_URL is not configured.")

    started = time.perf_counter()
    serialize_started = time.perf_counter()
    body = json.dumps(payload).encode("utf-8")
    if timing is not None:
        timing["json_serialize_ms"] = round((time.perf_counter() - serialize_started) * 1000.0, 2)
        timing["payload_bytes"] = len(body)
        timing["endpoint_path"] = urllib.request.urlparse(endpoint_url).path
    request = urllib.request.Request(
        endpoint_url,
        data=body,
        headers=_gateway_headers(),
        method="POST",
    )
    try:
        http_started = time.perf_counter()
        with urllib.request.urlopen(request, timeout=timeout) as response:
            read_started = time.perf_counter()
            raw_bytes = response.read()
            read_elapsed = time.perf_counter() - read_started
            decode_started = time.perf_counter()
            raw = raw_bytes.decode("utf-8")
            decode_elapsed = time.perf_counter() - decode_started
        if timing is not None:
            timing["http_request_ms"] = round((time.perf_counter() - http_started) * 1000.0, 2)
            timing["response_read_ms"] = round(read_elapsed * 1000.0, 2)
            timing["response_decode_ms"] = round(decode_elapsed * 1000.0, 2)
            timing["response_bytes"] = len(raw_bytes)
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        logger.info(
            "Model Runtime timing: kind=%s status=%s payload_bytes=%s elapsed=%.3fs",
            kind.value,
            error.code,
            len(body),
            time.perf_counter() - started,
        )
        raise ModelRuntimeUnavailableError(f"{kind.value} runtime HTTP {error.code}: {detail}") from error
    except OSError as error:
        logger.info(
            "Model Runtime timing: kind=%s error=%s payload_bytes=%s elapsed=%.3fs",
            kind.value,
            error,
            len(body),
            time.perf_counter() - started,
        )
        raise ModelRuntimeUnavailableError(f"{kind.value} runtime unavailable: {error}") from error

    try:
        parse_started = time.perf_counter()
        parsed = json.loads(raw)
        if timing is not None:
            timing["json_parse_ms"] = round((time.perf_counter() - parse_started) * 1000.0, 2)
    except json.JSONDecodeError as error:
        raise ModelRuntimeUnavailableError(f"{kind.value} runtime returned invalid JSON.") from error

    if not parsed.get("success", True):
        detail = parsed.get("detail") or parsed.get("error") or "Model runtime request failed."
        raise ModelRuntimeUnavailableError(f"{kind.value} runtime failed: {detail}")

    logger.info(
        "Model Runtime timing: kind=%s model=%s payload_bytes=%s elapsed=%.3fs",
        kind.value,
        parsed.get("model"),
        len(body),
        time.perf_counter() - started,
    )

    if timing is not None:
        gateway_timing = parsed.get("timing") or parsed.get("debug_timing")
        debug = parsed.get("debug")
        if not gateway_timing and isinstance(debug, dict):
            gateway_timing = debug.get("timing")
        if isinstance(gateway_timing, dict):
            timing["gateway_timing"] = gateway_timing
        timing["response_field_sizes"] = _field_size_summary(parsed)
        result_value = parsed.get("result")
        if result_value is None and "data" in parsed:
            result_value = parsed.get("data")
        if isinstance(result_value, dict):
            timing["result_field_sizes"] = _field_size_summary(result_value)
            raw_value = result_value.get("raw")
            if isinstance(raw_value, dict):
                timing["raw_field_sizes"] = _field_size_summary(raw_value)
                layout_items = raw_value.get("layout")
                if (
                    isinstance(layout_items, list)
                    and layout_items
                    and isinstance(layout_items[0], dict)
                ):
                    timing["layout_item_field_sizes"] = _field_size_summary(layout_items[0])
        timing["model"] = parsed.get("model")
        timing["total_runtime_client_ms"] = round((time.perf_counter() - started) * 1000.0, 2)

    result = parsed.get("result")
    if result is None and "data" in parsed:
        result = parsed.get("data")
    if kind == ModelRuntimeKind.TEXT_RECOGNITION and isinstance(result, dict) and parsed.get("model") and "model" not in result:
        result = {**result, "model": parsed.get("model")}
    return result if isinstance(result, dict) else parsed


def remote_analyze_layout(
    image: np.ndarray,
    timing: Optional[Dict[str, Any]] = None,
    layout_only: bool = False,
) -> Optional[Dict[str, Any]]:
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
    return _post_predict(
        ModelRuntimeKind.LAYOUT,
        {"image": image_data_url, "layout_only": layout_only},
        timing=timing,
    )


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
    image_data_urls = [_image_to_data_url(image) for image in images]
    if timing is not None:
        timing["image_encode_ms"] = round((time.perf_counter() - encode_started) * 1000.0, 2)
        timing["image_count"] = len(images)
        timing["image_data_url_bytes"] = sum(len(item) for item in image_data_urls)
        timing["image_shapes"] = [
            [int(image.shape[1]), int(image.shape[0])]
            for image in images
            if getattr(image, "shape", None) is not None and len(image.shape) >= 2
        ]
    return _post_predict(
        ModelRuntimeKind.TEXT_DETECTION,
        {"images": image_data_urls},
        timeout=240.0,
        path_override=_active_ocr_path("text_detection", "batch"),
        timing=timing,
    )


def remote_recognize_image(image: np.ndarray) -> Optional[Dict[str, Any]]:
    if not is_runtime_configured(ModelRuntimeKind.TEXT_RECOGNITION):
        return None
    return _post_predict(
        ModelRuntimeKind.TEXT_RECOGNITION,
        {"image": _image_to_data_url(image)},
        path_override=_active_ocr_path("text_recognition", "single"),
    )


def remote_recognize_images(images: List[np.ndarray]) -> Optional[Dict[str, Any]]:
    if not is_runtime_configured(ModelRuntimeKind.TEXT_RECOGNITION):
        return None
    return _post_predict(
        ModelRuntimeKind.TEXT_RECOGNITION,
        {"images": [_image_to_data_url(image) for image in images]},
        timeout=240.0,
        path_override=_active_ocr_path("text_recognition", "batch"),
    )


def remote_recognize_table_raw(image: np.ndarray) -> Optional[Dict[str, Any]]:
    if not is_runtime_configured(ModelRuntimeKind.TABLE):
        return None

    return _post_predict(
        ModelRuntimeKind.TABLE,
        {"image": _image_to_data_url(image)},
        timeout=240.0,
    )

def remote_verify_image_logits(
    image_path: str,
    categories: Optional[List[Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    if not is_runtime_configured(ModelRuntimeKind.IMAGE_VERIFICATION):
        return None
    return _post_predict(
        ModelRuntimeKind.IMAGE_VERIFICATION,
        {"image": _path_to_data_url(image_path), "categories": categories or []},
        timeout=240.0,
    )
