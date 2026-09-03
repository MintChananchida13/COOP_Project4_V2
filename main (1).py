from __future__ import annotations

import asyncio
import os
import time
from typing import Any

from fastapi import Request

from shared.api import BATCH_IMAGE_REQUEST_OPENAPI, IMAGE_REQUEST_OPENAPI, create_app, parse_image_request
from shared.contracts import ModelAPIError, request_id, success_response
from shared.upstream import get_readiness, post_images


SERVICE_NAME = "model-api-gateway"
MODEL_NAME = "pipeline-router-v1"
LAYOUT_PIPELINE_URL = os.getenv("LAYOUT_PIPELINE_URL", "http://localhost:8010")
DET_V5_URL = os.getenv("DET_V5_URL", "http://localhost:8002")
DET_V6_URL = os.getenv("DET_V6_URL", "http://localhost:8003")
OCR_CUSTOM_URL = os.getenv("OCR_CUSTOM_URL", "http://localhost:8005")
OCR_PADDLE_URL = os.getenv("OCR_PADDLE_URL", "http://localhost:8006")
TABLE_PIPELINE_URL = os.getenv("TABLE_PIPELINE_URL", "http://localhost:8011")
TABLE_MODEL_URL = os.getenv("TABLE_MODEL_URL", "http://localhost:8013")
IMAGE_VERIFICATION_URL = os.getenv("IMAGE_VERIFICATION_URL", "http://localhost:8012")
app = create_app(
    "Model API Gateway",
    MODEL_NAME,
    service_name=SERVICE_NAME,
    auth_token_env="MODEL_GATEWAY_API_KEY",
    required_auth_token_envs=("INTERNAL_API_TOKEN",),
    public_api=True,
)

PIPELINES = {
    "layout": LAYOUT_PIPELINE_URL,
    "ocr-custom": OCR_CUSTOM_URL,
    "ocr-paddle": OCR_PADDLE_URL,
    "table": TABLE_PIPELINE_URL,
    "table-model": TABLE_MODEL_URL,
    "image-verification": IMAGE_VERIFICATION_URL,
}


def _pipeline_names(variable: str, *, default_all: bool = False) -> set[str]:
    raw = os.getenv(variable, "all" if default_all else "").strip().lower()
    if raw in {"all", "*"}:
        return set(PIPELINES)
    return {item.strip() for item in raw.split(",") if item.strip()}


def _readiness_timeout() -> float:
    try:
        return max(0.1, float(os.getenv("GATEWAY_READINESS_TIMEOUT_SECONDS", "10")))
    except ValueError:
        return 10.0


def _text_detector_upstream(version: str | None) -> str:
    normalized = (version or "").strip().lower()
    detector_urls = {"v5": DET_V5_URL, "v6": DET_V6_URL}
    if normalized and normalized not in detector_urls:
        raise ModelAPIError(
            422,
            "VALIDATION_ERROR",
            "version must be v5 or v6.",
            details=[{"field": "version", "received": normalized}],
        )
    return detector_urls.get(normalized, LAYOUT_PIPELINE_URL)


async def _probe_pipeline(name: str, url: str, *, current_request_id: str, timeout: float) -> dict[str, Any]:
    started_at = time.perf_counter()
    try:
        await asyncio.to_thread(get_readiness, url, request_id=current_request_id, timeout=timeout)
        return {
            "name": name,
            "status": "ready",
            "duration_ms": round((time.perf_counter() - started_at) * 1000, 2),
        }
    except ModelAPIError as exc:
        return {
            "name": name,
            "status": "not_ready",
            "duration_ms": round((time.perf_counter() - started_at) * 1000, 2),
            "error": {"code": exc.code, "message": exc.message},
        }
    except Exception as exc:  # Defensive: a readiness endpoint must still return a useful report.
        return {
            "name": name,
            "status": "not_ready",
            "duration_ms": round((time.perf_counter() - started_at) * 1000, 2),
            "error": {"code": "READINESS_CHECK_FAILED", "message": "The readiness check failed."},
        }


async def _forward(
    request: Request,
    *,
    upstream: str,
    endpoint: str,
    extra_fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    image = await parse_image_request(request)
    try:
        fields = {**image.fields, **(extra_fields or {})}
        data = await asyncio.to_thread(
            post_images,
            upstream,
            endpoint,
            [image.path],
            fields=fields,
            request_id=request_id(request),
        )
        return success_response(request, data, service=SERVICE_NAME, model=MODEL_NAME)
    finally:
        image.cleanup()


async def _forward_multiple(
    request: Request,
    *,
    upstream: str,
    endpoint: str,
) -> dict[str, Any]:
    images = await parse_image_request(request, multiple=True)
    try:
        data = await asyncio.to_thread(
            post_images,
            upstream,
            endpoint,
            images.paths,
            fields=images.fields,
            request_id=request_id(request),
            multiple=True,
        )
        return success_response(request, data, service=SERVICE_NAME, model=MODEL_NAME)
    finally:
        images.cleanup()


@app.get("/api/v1/services", tags=["Discovery"])
def services(request: Request) -> dict[str, Any]:
    return success_response(
        request,
        {
            "document_layouts": "/api/v1/document-layouts",
            "ocr_results": "/api/v1/ocr-results",
            "text_detections": "/api/v1/text-detections",
            "text_detections_v5": "/api/v1/text-detections?version=v5",
            "text_detections_v6": "/api/v1/text-detections?version=v6",
            "text_detection_batches": "/api/v1/text-detection-batches",
            "text_recognitions": "/api/v1/text-recognitions",
            "text_recognition_batches": "/api/v1/text-recognition-batches",
            "table_results": "/api/v1/table-results",
            "table_model_results": "/api/v1/table-model-results",
            "image_verifications": "/api/v1/image-verifications",
        },
        service=SERVICE_NAME,
        model=MODEL_NAME,
    )


@app.post("/api/v1/document-layouts", tags=["Public pipelines"], openapi_extra=IMAGE_REQUEST_OPENAPI)
async def document_layouts(request: Request) -> dict[str, Any]:
    return await _forward(
        request,
        upstream=LAYOUT_PIPELINE_URL,
        endpoint="/api/v1/document-layouts",
    )


@app.post("/api/v1/ocr-results", tags=["Public pipelines"], openapi_extra=IMAGE_REQUEST_OPENAPI)
async def ocr_results(request: Request) -> dict[str, Any]:
    engine = request.query_params.get("engine", "custom").strip().lower()
    if engine not in {"custom", "paddle"}:
        raise ModelAPIError(
            422,
            "VALIDATION_ERROR",
            "engine must be custom or paddle.",
            details=[{"field": "engine", "received": engine}],
        )
    upstream = OCR_CUSTOM_URL if engine == "custom" else OCR_PADDLE_URL
    return await _forward(
        request,
        upstream=upstream,
        endpoint="/api/v1/ocr-results",
    )


@app.post("/api/v1/text-detections", tags=["Public pipelines"], openapi_extra=IMAGE_REQUEST_OPENAPI)
async def text_detections(request: Request, version: str | None = None) -> dict[str, Any]:
    return await _forward(
        request,
        upstream=_text_detector_upstream(version),
        endpoint="/api/v1/text-detections",
    )


@app.post("/api/v1/text-detection-batches", tags=["Public pipelines"], openapi_extra=BATCH_IMAGE_REQUEST_OPENAPI)
async def text_detection_batches(request: Request, version: str | None = None) -> dict[str, Any]:
    return await _forward_multiple(
        request,
        upstream=_text_detector_upstream(version),
        endpoint="/api/v1/text-detection-batches",
    )


@app.post("/api/v1/text-recognitions", tags=["Public pipelines"], openapi_extra=IMAGE_REQUEST_OPENAPI)
async def text_recognitions(request: Request) -> dict[str, Any]:
    return await _forward(
        request,
        upstream=OCR_CUSTOM_URL,
        endpoint="/api/v1/text-recognitions",
    )


@app.post("/api/v1/text-recognition-batches", tags=["Public pipelines"], openapi_extra=IMAGE_REQUEST_OPENAPI)
async def text_recognition_batches(request: Request) -> dict[str, Any]:
    return await _forward_multiple(
        request,
        upstream=OCR_CUSTOM_URL,
        endpoint="/api/v1/text-recognition-batches",
    )


@app.post("/api/v1/table-results", tags=["Public pipelines"], openapi_extra=IMAGE_REQUEST_OPENAPI)
async def table_results(request: Request) -> dict[str, Any]:
    return await _forward(
        request,
        upstream=TABLE_PIPELINE_URL,
        endpoint="/api/v1/table-results",
    )


@app.post("/api/v1/table-model-results", tags=["Public model inference"], openapi_extra=IMAGE_REQUEST_OPENAPI)
async def table_model_results(request: Request) -> dict[str, Any]:
    """Expose the notebook-compatible TableRecognitionPipelineV2 leaf output."""
    return await _forward(
        request,
        upstream=TABLE_MODEL_URL,
        endpoint="/api/v1/table-model-results",
    )


@app.post("/api/v1/image-verifications", tags=["Public pipelines"], openapi_extra=IMAGE_REQUEST_OPENAPI)
async def image_verifications(request: Request) -> dict[str, Any]:
    return await _forward(
        request,
        upstream=IMAGE_VERIFICATION_URL,
        endpoint="/api/v1/image-verifications",
    )


@app.get("/api/v1/readiness", tags=["Operations"])
async def readiness(request: Request) -> dict[str, Any]:
    enabled = _pipeline_names("GATEWAY_ENABLED_PIPELINES", default_all=True)
    required = _pipeline_names("GATEWAY_REQUIRED_PIPELINES")
    unknown = sorted((enabled | required) - set(PIPELINES))
    required_but_disabled = sorted(required - enabled)
    if unknown or required_but_disabled or not enabled:
        raise ModelAPIError(
            503,
            "GATEWAY_CONFIGURATION_ERROR",
            "Gateway pipeline readiness configuration is invalid.",
            details=[
                {
                    "unknown_pipelines": unknown,
                    "required_but_disabled": required_but_disabled,
                    "supported_pipelines": sorted(PIPELINES),
                }
            ],
        )

    timeout = _readiness_timeout()
    results = await asyncio.gather(
        *(
            _probe_pipeline(name, PIPELINES[name], current_request_id=request_id(request), timeout=timeout)
            for name in PIPELINES
            if name in enabled
        )
    )
    for result in results:
        result["required"] = result["name"] in required

    ready_count = sum(result["status"] == "ready" for result in results)
    failed_required = [result["name"] for result in results if result["required"] and result["status"] != "ready"]
    summary = {
        "enabled": len(results),
        "ready": ready_count,
        "not_ready": len(results) - ready_count,
        "required_not_ready": failed_required,
    }

    if failed_required:
        raise ModelAPIError(
            503,
            "REQUIRED_UPSTREAMS_NOT_READY",
            "One or more required pipeline services are not ready.",
            details=[{"summary": summary, "services": results}],
        )
    if ready_count == 0:
        raise ModelAPIError(
            503,
            "NO_UPSTREAMS_READY",
            "No enabled pipeline service is ready.",
            details=[{"summary": summary, "services": results}],
        )

    status = "ready" if ready_count == len(results) else "degraded"
    return success_response(
        request,
        {"status": status, "summary": summary, "services": results},
        service=SERVICE_NAME,
        model=MODEL_NAME,
    )
