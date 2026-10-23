import base64
import binascii
import math
from pathlib import Path

import cv2
import numpy as np
from fastapi import HTTPException

from .config import settings


def decode_image(data_url: str) -> np.ndarray:
    if not isinstance(data_url, str) or not data_url.startswith("data:image/") or ";base64," not in data_url:
        raise HTTPException(422, "Expected a base64 image data URL")
    mime, encoded = data_url.split(";base64,", 1)
    if mime not in {"data:image/png", "data:image/jpeg", "data:image/webp", "data:image/bmp"}:
        raise HTTPException(422, "Unsupported image format")
    if len(encoded) > math.ceil(settings.max_image_bytes / 3) * 4 + 4:
        raise HTTPException(413, "Image too large")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as error:
        raise HTTPException(422, "Invalid base64 image") from error
    if len(raw) > settings.max_image_bytes:
        raise HTTPException(413, "Image too large")
    image = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if image is None or image.size == 0:
        raise HTTPException(422, "Invalid image")
    if image.shape[0] * image.shape[1] > settings.max_image_pixels:
        raise HTTPException(413, "Image dimensions too large")
    return image


def json_safe(value):
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if hasattr(value, "json"):
        resolved = value.json() if callable(value.json) else value.json
        return json_safe(resolved)
    return value
