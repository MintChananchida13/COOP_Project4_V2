import logging
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .config import settings
from .models import NAMES, manager, predict, siglip_logits, registry, resolve_ocr
from .serialization import decode_image, json_safe
from .timing import build_timing
from .siglip_policy import POSITIVE, NEGATIVE, decide


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
app = FastAPI(title="Kaggle CPU Model Runtime")
executor = ThreadPoolExecutor(max_workers=settings.max_concurrent)
slots = threading.BoundedSemaphore(settings.max_concurrent + settings.queue_limit)


class SingleImage(BaseModel):
    image: str


class LayoutImage(SingleImage):
    layout_only: bool = False


class BatchImages(BaseModel):
    images: list[str] = Field(min_length=1)


class Category(BaseModel):
    value: str
    prompt: str
    label: str = ""
    enabled: bool = True


class SiglipDecisionConfig(BaseModel):
    model_config = {"extra": "forbid"}
    confidence_min_relative_score: float | None = Field(default=None, ge=0, le=1, strict=True)
    margin_min_relative_score: float | None = Field(default=None, ge=0, le=1, strict=True)


class VerifyImage(SingleImage):
    decision_config: SiglipDecisionConfig | None = None
    image_category: str = ""
    categories: list[Category] = Field(min_length=1)


class ClassifyImages(BatchImages):
    decision_config: SiglipDecisionConfig | None = None
    categories: list[Category] = Field(min_length=1)


def authenticate(request: Request):
    if not settings.api_key:
        raise HTTPException(503, "MODEL_GATEWAY_API_KEY is not configured")
    token = request.headers.get("Authorization", "")
    if not secrets.compare_digest(token, f"Bearer {settings.api_key}"):
        raise HTTPException(401, "Invalid bearer token")


def run(name, operation, model_timings=None, ocr_entry=None):
    started = time.perf_counter()
    if not slots.acquire(blocking=False):
        raise HTTPException(503, "Inference queue is full")
    try:
        submitted = time.perf_counter()

        def execute():
            queue_wait_ms = round((time.perf_counter() - submitted) * 1000, 2)
            model, model_load_ms = manager.get_ocr(name, ocr_entry) if ocr_entry else manager.get(name)
            lock_started = time.perf_counter()
            with manager.inference_locks[name]:
                lock_wait_ms = round((time.perf_counter() - lock_started) * 1000, 2)
                inference_started = time.perf_counter()
                result = operation(model)
                inference_ms = round((time.perf_counter() - inference_started) * 1000, 2)
            return result, queue_wait_ms, model_load_ms, lock_wait_ms, inference_ms

        result, queue_wait_ms, model_load_ms, lock_wait_ms, inference_ms = executor.submit(execute).result()
        serialize_started = time.perf_counter()
        safe_result = json_safe(result)
        serialization_ms = round((time.perf_counter() - serialize_started) * 1000, 2)
        if model_timings:
            model_timings["layout"].update(model_load_ms=model_load_ms, inference_lock_wait_ms=lock_wait_ms)
        timing = build_timing(
            name, queue_wait_ms, model_load_ms, lock_wait_ms, inference_ms,
            serialization_ms, round((time.perf_counter() - started) * 1000, 2), model_timings=model_timings,
        )
        return {"success": True, "model": f"{ocr_entry['model_name']}:{ocr_entry['model_id']}" if ocr_entry else NAMES[name], "result": safe_result, "timing": timing}
    except HTTPException:
        raise
    except Exception as error:
        logger.exception("%s inference failed", name)
        raise HTTPException(503, f"{name} inference failed: {error}") from error
    finally:
        slots.release()


def paddle_result(model, image):
    output = predict("", image, model)
    if len(output) != 1 or not isinstance(output[0], dict):
        raise RuntimeError("Unexpected Paddle result count or shape")
    return output[0]


@app.get("/health")
def health():
    return {"status": "ok", "device": settings.device, "loaded_models": list(manager.models), "authentication_configured": bool(settings.api_key)}


@app.post("/api/v1/document-layouts", dependencies=[Depends(authenticate)])
def layout(request: LayoutImage):
    image = decode_image(request.image)
    model_timings = {}

    def operation(model):
        layout_started = time.perf_counter()
        layout_raw = paddle_result(model, image)
        model_timings["layout"] = {"inference_ms": round((time.perf_counter() - layout_started) * 1000, 2)}
        if request.layout_only:
            return {"raw": {"layout": [layout_raw]}}
        detector, detector_load_ms = manager.get("det")
        detector_lock_started = time.perf_counter()
        with manager.inference_locks["det"]:
            detector_lock_ms = round((time.perf_counter() - detector_lock_started) * 1000, 2)
            detector_started = time.perf_counter()
            detection_raw = paddle_result(detector, image)
            model_timings["text_detection"] = {
                "inference_ms": round((time.perf_counter() - detector_started) * 1000, 2),
                "model_load_ms": detector_load_ms,
                "inference_lock_wait_ms": detector_lock_ms,
            }
        return {"raw": {"layout": [layout_raw], "text_detection": [detection_raw]}}

    return run("layout", operation, model_timings=model_timings)


def selected_ocr(kind, version, model_id):
    try:
        return resolve_ocr(kind, version, model_id)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@app.get("/api/v1/ocr-models", dependencies=[Depends(authenticate)])
def ocr_models():
    result = registry()
    for category, kind in (("detection", "det"), ("recognition", "rec")):
        for entry in result[category]:
            key = (kind, entry["version"], entry["model_id"])
            entry["loaded"] = kind in manager.models if entry["model_id"] == "default" else key in manager.ocr_models
    return {"success": True, "models": result}


class OcrModelCheck(BaseModel):
    kind: str
    version: str
    model_id: str


@app.post("/api/v1/ocr-models/check", dependencies=[Depends(authenticate)])
def check_ocr_model(request: OcrModelCheck):
    if request.kind not in ("det", "rec"):
        raise HTTPException(422, "Invalid model kind")
    entry = selected_ocr(request.kind, request.version, request.model_id)
    try:
        manager.get_ocr(request.kind, entry)
    except Exception as error:
        raise HTTPException(503, f"Model load failed: {error}") from error
    return {"success": True, "loaded": True}


@app.post("/api/v1/text-detections", dependencies=[Depends(authenticate)])
def detection(request: SingleImage, version: str = "v6", model_id: str = "default"):
    image = decode_image(request.image)
    entry = selected_ocr("det", version, model_id)
    return run("det", lambda model: paddle_result(model, image), **({} if model_id == "default" else {"ocr_entry": entry}))


@app.post("/api/v1/text-detection-batches", dependencies=[Depends(authenticate)])
def detection_batch(request: BatchImages, version: str = "v6", model_id: str = "default"):
    images = [decode_image(item) for item in request.images]
    entry = selected_ocr("det", version, model_id)
    return run("det", lambda model: {"results": [paddle_result(model, image) for image in images]}, **({} if model_id == "default" else {"ocr_entry": entry}))


@app.post("/api/v1/text-recognitions", dependencies=[Depends(authenticate)])
def recognition(request: SingleImage, version: str = "v5", model_id: str = "default"):
    image = decode_image(request.image)
    entry = selected_ocr("rec", version, model_id)
    return run("rec", lambda model: paddle_result(model, image), **({} if model_id == "default" else {"ocr_entry": entry}))


@app.post("/api/v1/text-recognition-batches", dependencies=[Depends(authenticate)])
def recognition_batch(request: BatchImages, version: str = "v5", model_id: str = "default"):
    images = [decode_image(item) for item in request.images]
    entry = selected_ocr("rec", version, model_id)
    return run("rec", lambda model: {"results": [paddle_result(model, image) for image in images]}, **({} if model_id == "default" else {"ocr_entry": entry}))


@app.post("/api/v1/table-model-results", dependencies=[Depends(authenticate)])
def table(request: SingleImage):
    image = decode_image(request.image)
    return run("table", lambda model: {"raw_output": paddle_result(model, image)})


def validate_categories(categories):
    active = [category.model_dump() for category in categories if category.enabled]
    if not active or any(not item["value"].strip() or not item["prompt"].strip() for item in active):
        raise HTTPException(422, "Active categories require value and prompt")
    if len({item["value"] for item in active}) != len(active):
        raise HTTPException(422, "Duplicate category values")
    return active


def siglip_result(logits, categories, config):
    result = {"logits": logits, "device": "cpu"}
    names = [category["value"] for category in categories]
    if names == [*POSITIVE, *NEGATIVE]:
        result["decision"] = decide(logits, config.model_dump() if config else None)
    return result


@app.post("/api/v1/image-verifications", dependencies=[Depends(authenticate)])
def verification(request: VerifyImage):
    image = decode_image(request.image, composite_alpha=True)
    categories = validate_categories(request.categories)
    return run("siglip", lambda instance: siglip_result(siglip_logits([image], categories, instance)[0], categories, request.decision_config))


@app.post("/api/v1/image-classification-batches", dependencies=[Depends(authenticate)])
def classification_batch(request: ClassifyImages):
    images = [decode_image(item, composite_alpha=True) for item in request.images]
    categories = validate_categories(request.categories)

    def operation(instance):
        results = []
        for start in range(0, len(images), settings.batch_chunk_size):
            logits = siglip_logits(images[start:start + settings.batch_chunk_size], categories, instance)
            results.extend(siglip_result(item, categories, request.decision_config) for item in logits)
        return {"results": results, "device": "cpu"}

    return run("siglip", operation)
