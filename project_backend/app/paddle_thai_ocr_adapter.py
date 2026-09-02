import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

from .ocr_postprocess import normalize_ocr_text
from .model_runtime_client import (
    ModelRuntimeKind,
    ModelRuntimeUnavailableError,
    is_runtime_configured,
    remote_recognize_image,
    remote_recognize_images,
)


class PaddleThaiOcrUnavailableError(RuntimeError):
    pass


logger = logging.getLogger(__name__)

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PADDLE_CACHE_DIR = BACKEND_ROOT / "storage" / "paddlex_cache"
PADDLE_TEMP_DIR = BACKEND_ROOT / "storage" / "paddle_tmp"
PADDLE_THAI_OCR_MODEL_NAME = os.environ.get("PADDLE_THAI_OCR_MODEL_NAME", "th_PP-OCRv5_mobile_rec")
PADDLE_THAI_OCR_MODEL_DIR = os.environ.get("PADDLE_THAI_OCR_MODEL_DIR")

PADDLE_TEMP_DIR.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("PADDLE_PDX_CACHE_HOME", str(PADDLE_CACHE_DIR))
os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
os.environ.setdefault("PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT", "False")
os.environ.setdefault("PADDLE_PDX_USE_PIR_TRT", "False")
os.environ.setdefault("FLAGS_use_mkldnn", "0")
os.environ.setdefault("FLAGS_json_format_model", "False")
os.environ.setdefault("TMP", str(PADDLE_TEMP_DIR))
os.environ.setdefault("TEMP", str(PADDLE_TEMP_DIR))


_TEXT_RECOGNIZER: Any = None


def _require_text_recognition_runtime() -> None:
    if not is_runtime_configured(ModelRuntimeKind.TEXT_RECOGNITION):
        raise PaddleThaiOcrUnavailableError("TEXT_RECOGNITION_MODEL_URL is not configured.")


def _env_flag(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _common_model_kwargs() -> Dict[str, Any]:
    return {
        "device": "cpu",
        "enable_mkldnn": False,
        "enable_cinn": False,
        "use_tensorrt": False,
    }


def _load_text_recognizer() -> Any:
    raise PaddleThaiOcrUnavailableError("Backend no longer loads text recognition models. Set TEXT_RECOGNITION_MODEL_URL.")


def _as_dict(value: Any) -> Optional[Dict[str, Any]]:
    if isinstance(value, dict):
        return value
    json_value = getattr(value, "json", None)
    if isinstance(json_value, dict):
        return json_value
    if callable(json_value):
        try:
            resolved = json_value()
            if isinstance(resolved, dict):
                return resolved
        except Exception:
            pass
    return None


def _walk_values(value: Any) -> List[Any]:
    found = [value]
    value_dict = _as_dict(value)
    if value_dict is not None:
        for child in value_dict.values():
            found.extend(_walk_values(child))
        return found
    if isinstance(value, (list, tuple)):
        for child in value:
            found.extend(_walk_values(child))
    return found


def _extract_text_confidence(value: Any) -> Tuple[str, float]:
    text_candidates: List[str] = []
    confidence_candidates: List[float] = []

    for item in _walk_values(value):
        item_dict = _as_dict(item)
        if not item_dict:
            continue

        for key in ("rec_text", "text", "label"):
            candidate = item_dict.get(key)
            if isinstance(candidate, str) and candidate.strip():
                text_candidates.append(candidate.strip())

        for key in ("rec_score", "confidence", "score", "prob"):
            candidate_score = item_dict.get(key)
            if isinstance(candidate_score, (int, float)):
                confidence_candidates.append(float(candidate_score))

    text = " ".join(dict.fromkeys(text_candidates)).strip()
    confidence = sum(confidence_candidates) / len(confidence_candidates) if confidence_candidates else 0.0
    return text, confidence


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return str(value)


def _result_from_output(output: Any) -> Dict[str, Any]:
    text, confidence = _extract_text_confidence(output)
    normalized_text = normalize_ocr_text(text)
    output_dict = _as_dict(output) or {}
    runtime_model = output_dict.get("model") or PADDLE_THAI_OCR_MODEL_NAME
    result = {
        "text": normalized_text,
        "confidence": float(confidence),
        "engine": "paddle_thai_ocr",
        "model": runtime_model,
        "configured_model": PADDLE_THAI_OCR_MODEL_NAME,
        "raw_text": text,
        "normalized_text": normalized_text,
        "segments": [],
        "attempts": [],
        "preprocessing": "paddle_text_recognition",
    }
    if _env_flag("PADDLE_OCR_INCLUDE_RAW_OUTPUT", "false"):
        result["raw_output"] = _json_safe([_as_dict(output) or str(output)])
    return result


def run_paddle_thai_ocr(opencv_img: np.ndarray) -> Dict[str, Any]:
    if opencv_img is None or opencv_img.size == 0:
        return {
            "text": "",
            "confidence": 0.0,
            "engine": "paddle_thai_ocr",
            "model": PADDLE_THAI_OCR_MODEL_NAME,
            "error": "empty_image",
        }

    _require_text_recognition_runtime()
    logger.info("Using remote OCR runtime")
    try:
        remote_result = remote_recognize_image(opencv_img)
    except ModelRuntimeUnavailableError as error:
        raise PaddleThaiOcrUnavailableError(str(error)) from error
    except Exception as error:
        raise PaddleThaiOcrUnavailableError(str(error)) from error
    if remote_result is None:
        raise PaddleThaiOcrUnavailableError("Remote OCR runtime returned no result.")
    if not isinstance(remote_result, dict):
        raise PaddleThaiOcrUnavailableError("Remote OCR runtime returned an invalid response.")
    return _result_from_output(remote_result)


def run_paddle_thai_ocr_batch(opencv_images: List[np.ndarray]) -> List[Dict[str, Any]]:
    if not opencv_images:
        return []

    _require_text_recognition_runtime()
    logger.info("Using remote OCR runtime")
    try:
        remote_result = remote_recognize_images(opencv_images)
    except ModelRuntimeUnavailableError as error:
        raise PaddleThaiOcrUnavailableError(str(error)) from error
    except Exception as error:
        raise PaddleThaiOcrUnavailableError(str(error)) from error
    if remote_result is None:
        raise PaddleThaiOcrUnavailableError("Remote OCR runtime returned no result.")
    if not isinstance(remote_result, dict):
        raise PaddleThaiOcrUnavailableError("Remote OCR runtime returned an invalid response.")
    results = remote_result.get("results")
    if not isinstance(results, list):
        raise PaddleThaiOcrUnavailableError("Remote OCR runtime returned an invalid batch response.")
    return [
        _result_from_output(item) if isinstance(item, dict) else {"text": "", "confidence": 0.0, "error": "invalid_batch_item"}
        for item in results
    ]
