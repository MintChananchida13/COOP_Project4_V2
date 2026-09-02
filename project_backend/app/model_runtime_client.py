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


logger = logging.getLogger(__name__)


class ModelRuntimeUnavailableError(RuntimeError):
    pass


class ModelRuntimeKind(str, Enum):
    LAYOUT = "layout"
    TEXT_DETECTION = "text_detection"
    TEXT_RECOGNITION = "text_recognition"
    TABLE = "table"
    IMAGE_VERIFICATION = "image_verification"


MODEL_RUNTIME_ENV: Dict[ModelRuntimeKind, str] = {
    ModelRuntimeKind.LAYOUT: "LAYOUT_MODEL_URL",
    ModelRuntimeKind.TEXT_DETECTION: "TEXT_DETECTION_MODEL_URL",
    ModelRuntimeKind.TEXT_RECOGNITION: "TEXT_RECOGNITION_MODEL_URL",
    ModelRuntimeKind.TABLE: "TABLE_MODEL_URL",
    ModelRuntimeKind.IMAGE_VERIFICATION: "IMAGE_VERIFICATION_MODEL_URL",
}


def runtime_url(kind: ModelRuntimeKind) -> Optional[str]:
    if os.getenv("MODEL_RUNTIME_ROLE", "").strip().lower() == "service":
        return None
    value = os.getenv(MODEL_RUNTIME_ENV[kind], "").strip().rstrip("/")
    return value or None


def is_runtime_configured(kind: ModelRuntimeKind) -> bool:
    return bool(runtime_url(kind))


def configured_runtimes() -> Dict[str, Optional[str]]:
    return {kind.value: runtime_url(kind) for kind in ModelRuntimeKind}


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


def _post_predict(kind: ModelRuntimeKind, payload: Dict[str, Any], timeout: float = 120.0) -> Dict[str, Any]:
    base_url = runtime_url(kind)
    if not base_url:
        raise ModelRuntimeUnavailableError(f"{MODEL_RUNTIME_ENV[kind]} is not configured.")

    started = time.perf_counter()
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}/predict",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
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
        parsed = json.loads(raw)
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

    result = parsed.get("result")
    if result is None and "data" in parsed:
        result = parsed.get("data")
    if kind == ModelRuntimeKind.TEXT_RECOGNITION and isinstance(result, dict) and parsed.get("model") and "model" not in result:
        result = {**result, "model": parsed.get("model")}
    return result if isinstance(result, dict) else parsed


def remote_analyze_layout(image: np.ndarray) -> Optional[Dict[str, Any]]:
    if not is_runtime_configured(ModelRuntimeKind.LAYOUT):
        return None
    return _post_predict(ModelRuntimeKind.LAYOUT, {"image": _image_to_data_url(image)})


def remote_detect_text_boxes(image_path: str) -> Optional[Dict[str, Any]]:
    if not is_runtime_configured(ModelRuntimeKind.TEXT_DETECTION):
        return None
    return _post_predict(ModelRuntimeKind.TEXT_DETECTION, {"image": _path_to_data_url(image_path)})


def remote_recognize_image(image: np.ndarray) -> Optional[Dict[str, Any]]:
    if not is_runtime_configured(ModelRuntimeKind.TEXT_RECOGNITION):
        return None
    return _post_predict(ModelRuntimeKind.TEXT_RECOGNITION, {"image": _image_to_data_url(image)})


def remote_recognize_images(images: List[np.ndarray]) -> Optional[Dict[str, Any]]:
    if not is_runtime_configured(ModelRuntimeKind.TEXT_RECOGNITION):
        return None
    return _post_predict(
        ModelRuntimeKind.TEXT_RECOGNITION,
        {"images": [_image_to_data_url(image) for image in images]},
        timeout=240.0,
    )


def remote_recognize_table_raw(image: np.ndarray) -> Optional[Dict[str, Any]]:
    if not is_runtime_configured(ModelRuntimeKind.TABLE):
        return None
    return _post_predict(ModelRuntimeKind.TABLE, {"image": _image_to_data_url(image)}, timeout=240.0)


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
