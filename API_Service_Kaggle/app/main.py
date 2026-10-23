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


class VerifyImage(SingleImage):
    image_category: str = ""
    categories: list[Category] = Field(min_length=1)


class ClassifyImages(BatchImages):
    categories: list[Category] = Field(min_length=1)


def authenticate(request: Request):
    if not settings.api_key:
        raise HTTPException(503, "MODEL_GATEWAY_API_KEY is not configured")
    token = request.headers.get("Authorization", "")
    if not secrets.compare_digest(token, f"Bearer {settings.api_key}"):
        raise HTTPException(401, "Invalid bearer token")


def run(name, operation):
    started = time.perf_counter()
    if not slots.acquire(blocking=False):
        raise HTTPException(503, "Inference queue is full")
    try:
        submitted = time.perf_counter()

        def execute():
            queue_wait_ms = round((time.perf_counter() - submitted) * 1000, 2)
            model, model_load_ms = manager.get(name)
            inference_started = time.perf_counter()
            with manager.inference_locks[name]:
                result = operation(model)
            inference_ms = round((time.perf_counter() - inference_started) * 1000, 2)
            return result, queue_wait_ms, model_load_ms, inference_ms

        result, queue_wait_ms, model_load_ms, inference_ms = executor.submit(execute).result()
        serialize_started = time.perf_counter()
        safe_result = json_safe(result)
        serialization_ms = round((time.perf_counter() - serialize_started) * 1000, 2)
        timing = {
            "queue_wait_ms": queue_wait_ms,
            "model_load_ms": model_load_ms,
            "inference_ms": inference_ms,
            "serialization_ms": serialization_ms,
            "total_runtime_ms": round((time.perf_counter() - started) * 1000, 2),
        }
        timing[{"layout": "layout_runtime_ms", "det": "text_det_inference_ms", "rec": "text_rec_inference_ms", "table": "table_runtime_ms", "siglip": "image_siglip_runtime_ms"}[name]] = inference_ms
        if name == "layout":
            timing["inference_including_load_and_adaptation"] = round(model_load_ms + inference_ms + serialization_ms, 2)
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

    def operation(model):
        layout_raw = paddle_result(model, image)
        if request.layout_only:
            return {"raw": {"layout": [layout_raw]}}
        detector, _ = manager.get("det")
        with manager.inference_locks["det"]:
            detection_raw = paddle_result(detector, image)
        return {"raw": {"layout": [layout_raw], "text_detection": [detection_raw]}}

    return run("layout", operation)


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


@app.post("/api/v1/image-verifications", dependencies=[Depends(authenticate)])
def verification(request: VerifyImage):
    image = decode_image(request.image)
    categories = validate_categories(request.categories)
    return run("siglip", lambda instance: {"logits": siglip_logits([image], categories, instance)[0], "device": "cpu"})


@app.post("/api/v1/image-classification-batches", dependencies=[Depends(authenticate)])
def classification_batch(request: ClassifyImages):
    images = [decode_image(item) for item in request.images]
    categories = validate_categories(request.categories)

    def operation(instance):
        results = []
        for start in range(0, len(images), settings.batch_chunk_size):
            logits = siglip_logits(images[start:start + settings.batch_chunk_size], categories, instance)
            results.extend({"logits": item, "device": "cpu"} for item in logits)
        return {"results": results, "device": "cpu"}

    return run("siglip", operation)
