import logging
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .config import settings
from .models import NAMES, manager, predict, siglip_logits
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


def run(name, operation, model_timings=None):
    started = time.perf_counter()
    if not slots.acquire(blocking=False):
        raise HTTPException(503, "Inference queue is full")
    try:
        submitted = time.perf_counter()

        def execute():
            queue_wait_ms = round((time.perf_counter() - submitted) * 1000, 2)
            model, model_load_ms = manager.get(name)
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
        return {"success": True, "model": NAMES[name], "result": safe_result, "timing": timing}
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


def require_v6(version: str = Query("v6")):
    if version != "v6":
        raise HTTPException(422, "Only version=v6 is supported")


@app.post("/api/v1/text-detections", dependencies=[Depends(authenticate), Depends(require_v6)])
def detection(request: SingleImage):
    image = decode_image(request.image)
    return run("det", lambda model: paddle_result(model, image))


@app.post("/api/v1/text-detection-batches", dependencies=[Depends(authenticate), Depends(require_v6)])
def detection_batch(request: BatchImages):
    images = [decode_image(item) for item in request.images]
    return run("det", lambda model: {"results": [paddle_result(model, image) for image in images]})


@app.post("/api/v1/text-recognitions", dependencies=[Depends(authenticate)])
def recognition(request: SingleImage):
    image = decode_image(request.image)
    return run("rec", lambda model: paddle_result(model, image))


@app.post("/api/v1/text-recognition-batches", dependencies=[Depends(authenticate)])
def recognition_batch(request: BatchImages):
    images = [decode_image(item) for item in request.images]
    return run("rec", lambda model: {"results": [paddle_result(model, image) for image in images]})


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
