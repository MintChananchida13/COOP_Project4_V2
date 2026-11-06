import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

from app.processing.ocr_postprocess import normalize_ocr_text
from app.model_runtime.text_response_contract import normalize_batch, normalize_single, TextResponseContractError
from app.core.model_runtime_client import (
    ModelRuntimeKind,
    ModelRuntimeUnavailableError,
    is_runtime_configured,
    remote_recognize_images,
)


class PaddleThaiOcrUnavailableError(RuntimeError):
    pass


logger = logging.getLogger(__name__)

TEXT_RECOGNITION_MODEL_NAME = "th_PP-OCRv5_mobile_rec"
OCR_BATCH_CHUNK_SIZE = 60


def _require_text_recognition_runtime() -> None:
    if not is_runtime_configured(ModelRuntimeKind.TEXT_RECOGNITION):
        raise PaddleThaiOcrUnavailableError("GATEWAY_URL is not configured.")


def _env_flag(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


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


def _extract_text_confidence(value: Any) -> Tuple[str, float]:
    result = normalize_single(value, 'rec')
    return result['rec_text'].strip(), result['rec_score']


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
    parsed = normalize_single(output, 'rec')
    text, confidence = parsed['rec_text'].strip(), parsed['rec_score']
    normalized_text = normalize_ocr_text(text)
    runtime_model = parsed.get("model") or TEXT_RECOGNITION_MODEL_NAME
    result = {
        "text": normalized_text,
        "confidence": float(confidence),
        "engine": "paddle_thai_ocr",
        "model": runtime_model,
        "configured_model": TEXT_RECOGNITION_MODEL_NAME,
        "raw_text": text,
        "normalized_text": normalized_text,
        "segments": [],
        "attempts": [],
        "preprocessing": "paddle_text_recognition",
    }
    if _env_flag("BACKEND_OCR_INCLUDE_RAW_OUTPUT", "false"):
        result["raw_output"] = _json_safe([_as_dict(output) or str(output)])
    return result


def run_paddle_thai_ocr(opencv_img: np.ndarray, timing: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if opencv_img is None or opencv_img.size == 0:
        return {
            "text": "",
            "confidence": 0.0,
            "engine": "paddle_thai_ocr",
            "model": TEXT_RECOGNITION_MODEL_NAME,
            "error": "empty_image",
        }

    return run_paddle_thai_ocr_batch([opencv_img], timing=timing)[0]


def run_paddle_thai_ocr_batch(
    opencv_images: List[np.ndarray],
    timing: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    if not opencv_images:
        return []

    _require_text_recognition_runtime()
    logger.info("Using remote OCR runtime")
    ordered_results: List[Any] = []
    chunks = [opencv_images[index : index + OCR_BATCH_CHUNK_SIZE] for index in range(0, len(opencv_images), OCR_BATCH_CHUNK_SIZE)]
    for chunk_index, chunk in enumerate(chunks, start=1):
        logger.info("Using remote OCR runtime batch chunk %s/%s size=%s", chunk_index, len(chunks), len(chunk))
        try:
            runtime_timing: Dict[str, Any] = {}
            remote_result = remote_recognize_images(chunk, timing=runtime_timing)
            if isinstance(timing, dict):
                timing.setdefault("model_calls", []).append(runtime_timing)
                intervals = timing.setdefault("model_call_intervals", [])
                if isinstance(intervals, list) and isinstance(runtime_timing.get("model_call_start_monotonic"), (int, float)) and isinstance(runtime_timing.get("model_call_end_monotonic"), (int, float)):
                    intervals.append({
                        "kind": "text_recognition",
                        "start_monotonic": runtime_timing["model_call_start_monotonic"],
                        "end_monotonic": runtime_timing["model_call_end_monotonic"],
                    })
            gateway_timing = runtime_timing.get("gateway_timing")
            if isinstance(timing, dict) and isinstance(gateway_timing, dict):
                value = gateway_timing.get("text_rec_inference_ms")
                if isinstance(value, (int, float)):
                    timing["text_rec_inference_ms"] = float(timing.get("text_rec_inference_ms") or 0.0) + float(value)
        except ModelRuntimeUnavailableError as error:
            raise PaddleThaiOcrUnavailableError(str(error)) from error
        except Exception as error:
            raise PaddleThaiOcrUnavailableError(str(error)) from error
        if remote_result is None:
            raise PaddleThaiOcrUnavailableError("Remote OCR runtime returned no result.")
        try:
            ordered_results.extend(normalize_batch(remote_result, 'rec', len(chunk)))
        except TextResponseContractError as error:
            raise PaddleThaiOcrUnavailableError(f"Invalid REC response contract: {error}") from error
    return [_result_from_output(item) for item in ordered_results]
