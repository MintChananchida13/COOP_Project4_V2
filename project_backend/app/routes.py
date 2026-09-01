import io

from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request

from .auth_service import authenticate_user, create_access_token, create_user
from .db import connect as connect_db
from .schemas import (
    ApiResponse,
    AuthLoginRequest,
    AuthRegisterRequest,
    IgnoreRegionCreate,
    IgnoreRegionUpdate,
    ImageVerificationCategoryCreate,
    ImageVerificationCategoryUpdate,
    RejectRequest,
    RequestedFieldCreate,
    RequestedFieldUpdate,
    TemplateCreate,
    TemplateFieldCreate,
    TemplateFieldUpdate,
    TemplatePageCreate,
    TemplatePageUpdate,
    TemplateRequestCreate,
    TemplateRequestImageCreate,
    TemplateRequestImageUpdate,
    TemplateRequestUpdate,
    TemplateRequestConvert,
    TemplateTestRequest,
    TemplateUpdate,
    TemplateVersionCreate,
    TemplateVersionFromRequestCreate,
)
from .detection_service import detect_template_dev
from .services import (
    AdminTemplateService,
    EmbeddingService,
    StorageMaintenanceService,
    TemplateRequestService,
    ImageVerificationCategoryService,
)

router = APIRouter()

template_requests = TemplateRequestService()
admin_templates = AdminTemplateService()
embeddings = EmbeddingService()
storage_maintenance = StorageMaintenanceService()
image_categories = ImageVerificationCategoryService()


def ok(data: dict) -> ApiResponse:
    return ApiResponse(data=data)


@router.post("/auth/register", response_model=ApiResponse)
def register(payload: AuthRegisterRequest) -> ApiResponse:
    user = create_user(payload.email, payload.password, payload.role)
    return ok({"user": user, "access_token": create_access_token(user["id"]), "token_type": "bearer"})


@router.post("/auth/login", response_model=ApiResponse)
def login(payload: AuthLoginRequest) -> ApiResponse:
    user = authenticate_user(payload.email, payload.password)
    return ok({"user": user, "access_token": create_access_token(user["id"]), "token_type": "bearer"})


@router.get("/auth/me", response_model=ApiResponse)
def me() -> ApiResponse:
    return ok({"user": None, "auth_mode": "mock"})


@router.get("/health", response_model=ApiResponse)
def health() -> ApiResponse:
    return ok({"status": "ok"})


@router.get("/health/db", response_model=ApiResponse)
def database_health() -> ApiResponse:
    try:
        with connect_db() as conn:
            conn.execute("SELECT 1").fetchone()
        return ok(
            {
                "status": "ok",
                "engine": "postgresql",
            }
        )
    except Exception as error:
        raise HTTPException(
            status_code=503,
            detail={
                "status": "unavailable",
                "engine": "postgresql",
                "message": str(error),
            },
        ) from error


def _images_to_pdf_bytes(files: list[bytes]) -> bytes:
    try:
        from PIL import Image
    except ImportError as error:
        raise HTTPException(status_code=501, detail="Multi-page detection requires Pillow") from error

    images = []
    for file_bytes in files:
        if file_bytes.lstrip().startswith(b"%PDF"):
            if len(files) == 1:
                return file_bytes
            raise HTTPException(status_code=400, detail="Multi-file detection accepts image pages, not mixed PDF files")
        try:
            image = Image.open(io.BytesIO(file_bytes))
            if image.mode != "RGB":
                image = image.convert("RGB")
            images.append(image.copy())
        except Exception as error:
            raise HTTPException(status_code=400, detail="Uploaded page is not a valid image") from error

    if not images:
        raise HTTPException(status_code=400, detail="No uploaded image file found")

    output = io.BytesIO()
    first, rest = images[0], images[1:]
    first.save(output, format="PDF", save_all=True, append_images=rest)
    return output.getvalue()


def _extract_multipart_file(content_type: str, body: bytes) -> bytes:
    boundary_token = "boundary="
    if boundary_token not in content_type:
        raise HTTPException(status_code=400, detail="Multipart upload is missing boundary")
    boundary = content_type.split(boundary_token, 1)[1].split(";", 1)[0].strip().strip('"')
    if not boundary:
        raise HTTPException(status_code=400, detail="Multipart upload is missing boundary")

    delimiter = f"--{boundary}".encode("utf-8")
    files: list[bytes] = []
    for part in body.split(delimiter):
        if b"Content-Disposition" not in part or b"filename=" not in part:
            continue
        if b"\r\n\r\n" not in part:
            continue
        _, data = part.split(b"\r\n\r\n", 1)
        data = data.rsplit(b"\r\n", 1)[0]
        if data.endswith(b"--"):
            data = data[:-2]
        if data:
            files.append(data)
    if not files:
        raise HTTPException(status_code=400, detail="No uploaded image file found")
    if len(files) == 1:
        return files[0]
    return _images_to_pdf_bytes(files)


async def _read_dev_detection_image(request: Request) -> bytes:
    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="Uploaded image is empty")

    content_type = request.headers.get("content-type", "")
    content_type_lower = content_type.lower()
    if "multipart/form-data" in content_type_lower:
        return _extract_multipart_file(content_type, body)
    if content_type_lower.startswith("image/") or content_type_lower.startswith("application/pdf") or content_type_lower in {"application/octet-stream", ""}:
        return body
    raise HTTPException(status_code=400, detail="Upload must be multipart/form-data, image, or PDF")


@router.post("/api/templates/detect-dev")
async def detect_template_dev_route(request: Request) -> dict:
    image_bytes = await _read_dev_detection_image(request)
    try:
        return {"status": "success", "data": detect_template_dev(image_bytes)}
    except HTTPException:
        raise
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@router.post("/template-requests", response_model=ApiResponse)
def create_template_request(payload: TemplateRequestCreate) -> ApiResponse:
    return ok(template_requests.create(payload.model_copy(update={"requested_by": None})))


@router.get("/template-requests", response_model=ApiResponse)
def list_template_requests() -> ApiResponse:
    return ok(template_requests.list())


@router.get("/template-requests/{request_id}", response_model=ApiResponse)
def get_template_request(request_id: str) -> ApiResponse:
    return ok(template_requests.get(request_id))


@router.put("/template-requests/{request_id}", response_model=ApiResponse)
def update_template_request(
    request_id: str,
    payload: TemplateRequestUpdate,
) -> ApiResponse:
    return ok(template_requests.update(request_id, payload))


@router.delete("/template-requests/{request_id}", response_model=ApiResponse)
def delete_template_request(request_id: str) -> ApiResponse:
    return ok(template_requests.delete(request_id))


@router.post("/template-requests/{request_id}/submit", response_model=ApiResponse)
def submit_template_request(request_id: str) -> ApiResponse:
    return ok(template_requests.submit(request_id))


@router.get("/template-requests/{request_id}/pages", response_model=ApiResponse)
def get_template_request_pages(request_id: str) -> ApiResponse:
    return ok(template_requests.pages(request_id))


@router.post("/template-requests/{request_id}/requested-fields", response_model=ApiResponse)
def create_requested_field(
    request_id: str,
    payload: RequestedFieldCreate,
) -> ApiResponse:
    return ok(template_requests.add_requested_field(request_id, payload))


@router.put("/template-requests/{request_id}/requested-fields/{field_id}", response_model=ApiResponse)
def update_requested_field(
    request_id: str,
    field_id: str,
    payload: RequestedFieldUpdate,
) -> ApiResponse:
    return ok(template_requests.update_requested_field(request_id, field_id, payload))


@router.delete("/template-requests/{request_id}/requested-fields/{field_id}", response_model=ApiResponse)
def delete_requested_field(
    request_id: str,
    field_id: str,
) -> ApiResponse:
    return ok(template_requests.delete_requested_field(request_id, field_id))


@router.get("/admin/dashboard", response_model=ApiResponse)
def admin_dashboard() -> ApiResponse:
    return ok(admin_templates.dashboard())


@router.post("/admin/storage/cleanup-generated", response_model=ApiResponse)
def cleanup_generated_storage(
    max_age_hours: int = 24,
    dry_run: bool = True,
) -> ApiResponse:
    return ok(storage_maintenance.cleanup_generated_files(max_age_hours=max_age_hours, dry_run=dry_run))


@router.get("/admin/image-verification-categories", response_model=ApiResponse)
def list_image_verification_categories(
    enabled_only: bool = False,
) -> ApiResponse:
    return ok(image_categories.list(enabled_only=enabled_only))


@router.post("/admin/image-verification-categories", response_model=ApiResponse)
def create_image_verification_category(
    payload: ImageVerificationCategoryCreate,
) -> ApiResponse:
    return ok(image_categories.create(payload.model_dump()))


@router.put("/admin/image-verification-categories/{category_value}", response_model=ApiResponse)
def update_image_verification_category(
    category_value: str,
    payload: ImageVerificationCategoryUpdate,
) -> ApiResponse:
    return ok(image_categories.update(category_value, payload.model_dump(exclude_unset=True)))


@router.delete("/admin/image-verification-categories/{category_value}", response_model=ApiResponse)
def delete_image_verification_category(
    category_value: str,
) -> ApiResponse:
    return ok(image_categories.delete(category_value))


@router.get("/admin/template-requests", response_model=ApiResponse)
def admin_list_template_requests() -> ApiResponse:
    return ok(template_requests.list())


@router.get("/admin/template-requests/{request_id}", response_model=ApiResponse)
def admin_get_template_request(request_id: str) -> ApiResponse:
    return ok(template_requests.get(request_id))


@router.delete("/admin/template-requests/{request_id}", response_model=ApiResponse)
def admin_delete_template_request(request_id: str) -> ApiResponse:
    return ok(template_requests.delete(request_id))


@router.post("/admin/template-requests/{request_id}/start-review", response_model=ApiResponse)
def admin_start_review(request_id: str) -> ApiResponse:
    return ok(admin_templates.start_review(request_id))


@router.post("/admin/template-requests/{request_id}/images", response_model=ApiResponse)
def admin_add_template_request_image(
    request_id: str,
    payload: TemplateRequestImageCreate,
) -> ApiResponse:
    return ok(template_requests.add_image(request_id, payload))


@router.patch("/admin/template-requests/{request_id}/images/{image_id}", response_model=ApiResponse)
def admin_update_template_request_image(
    request_id: str,
    image_id: str,
    payload: TemplateRequestImageUpdate,
) -> ApiResponse:
    return ok(template_requests.update_image(request_id, image_id, payload))


@router.delete("/admin/template-requests/{request_id}/images/{image_id}", response_model=ApiResponse)
def admin_delete_template_request_image(
    request_id: str,
    image_id: str,
) -> ApiResponse:
    return ok(template_requests.delete_image(request_id, image_id))


@router.post("/admin/template-requests/{request_id}/images/{image_id}/approve", response_model=ApiResponse)
def admin_approve_template_request_image(
    request_id: str,
    image_id: str,
) -> ApiResponse:
    return ok(template_requests.update_image(request_id, image_id, TemplateRequestImageUpdate(review_status="approved")))


@router.post("/admin/template-requests/{request_id}/images/{image_id}/reject", response_model=ApiResponse)
def admin_reject_template_request_image(
    request_id: str,
    image_id: str,
) -> ApiResponse:
    return ok(template_requests.update_image(request_id, image_id, TemplateRequestImageUpdate(review_status="rejected", is_canonical=False)))


@router.post("/admin/template-requests/{request_id}/images/{image_id}/canonical", response_model=ApiResponse)
def admin_set_template_request_canonical_image(
    request_id: str,
    image_id: str,
) -> ApiResponse:
    return ok(template_requests.update_image(request_id, image_id, TemplateRequestImageUpdate(review_status="approved", is_canonical=True)))


@router.post("/admin/template-requests/{request_id}/convert-to-template", response_model=ApiResponse)
def admin_convert_request_to_template(
    request_id: str,
    payload: TemplateRequestConvert | None = None,
) -> ApiResponse:
    return ok(admin_templates.convert_request_to_template(request_id, payload, created_by=None))


@router.post("/admin/template-requests/{request_id}/suggest-base-version", response_model=ApiResponse)
def admin_suggest_template_request_base_version(
    request_id: str,
    payload: TemplateVersionCreate,
) -> ApiResponse:
    if not payload.base_template_id:
        raise HTTPException(status_code=400, detail="base_template_id is required")
    return ok(admin_templates.suggest_base_version_for_request(request_id, payload.base_template_id, payload.similarity_threshold))


@router.post("/admin/template-requests/{request_id}/convert-to-version", response_model=ApiResponse)
def admin_convert_request_to_template_version(
    request_id: str,
    payload: TemplateVersionFromRequestCreate,
) -> ApiResponse:
    return ok(admin_templates.create_version_from_request(request_id, payload, created_by=None))


@router.post("/admin/template-requests/{request_id}/reject", response_model=ApiResponse)
def admin_reject_template_request(
    request_id: str,
    payload: RejectRequest,
) -> ApiResponse:
    return ok(template_requests.reject(request_id, payload.reason))


@router.post("/admin/templates", response_model=ApiResponse)
def create_template(payload: TemplateCreate) -> ApiResponse:
    return ok(admin_templates.create_template(payload.model_copy(update={"created_by": None})))


@router.get("/admin/templates", response_model=ApiResponse)
def list_templates() -> ApiResponse:
    return ok(admin_templates.list_templates())


@router.get("/admin/templates/{template_id}", response_model=ApiResponse)
def get_template(template_id: str) -> ApiResponse:
    return ok(admin_templates.get_template(template_id))


@router.put("/admin/templates/{template_id}", response_model=ApiResponse)
def update_template(
    template_id: str,
    payload: TemplateUpdate,
) -> ApiResponse:
    return ok(admin_templates.update_template(template_id, payload))


@router.delete("/admin/templates/{template_id}", response_model=ApiResponse)
def delete_template(template_id: str) -> ApiResponse:
    return ok(admin_templates.delete_template(template_id))


@router.get("/admin/templates/{template_id}/pages", response_model=ApiResponse)
def list_template_pages(template_id: str) -> ApiResponse:
    return ok(admin_templates.list_template_pages(template_id))


@router.post("/admin/templates/{template_id}/pages", response_model=ApiResponse)
def create_template_page(
    template_id: str,
    payload: TemplatePageCreate,
) -> ApiResponse:
    return ok(admin_templates.create_template_page(template_id, payload))


@router.put("/admin/templates/{template_id}/pages/{page_id}", response_model=ApiResponse)
def update_template_page(
    template_id: str,
    page_id: str,
    payload: TemplatePageUpdate,
) -> ApiResponse:
    return ok(admin_templates.update_template_page(template_id, page_id, payload))


@router.delete("/admin/templates/{template_id}/pages/{page_id}", response_model=ApiResponse)
def delete_template_page(
    template_id: str,
    page_id: str,
) -> ApiResponse:
    return ok(admin_templates.delete_template_page(template_id, page_id))


@router.post("/admin/templates/{template_id}/fields", response_model=ApiResponse)
def create_template_field(
    template_id: str,
    payload: TemplateFieldCreate,
) -> ApiResponse:
    return ok(admin_templates.create_template_field(template_id, payload))


@router.put("/admin/templates/{template_id}/fields/{field_id}", response_model=ApiResponse)
def update_template_field(
    template_id: str,
    field_id: str,
    payload: TemplateFieldUpdate,
) -> ApiResponse:
    return ok(admin_templates.update_template_field(template_id, field_id, payload))


@router.delete("/admin/templates/{template_id}/fields/{field_id}", response_model=ApiResponse)
def delete_template_field(
    template_id: str,
    field_id: str,
) -> ApiResponse:
    return ok(admin_templates.delete_template_field(template_id, field_id))


@router.post("/admin/templates/{template_id}/ignore-regions", response_model=ApiResponse)
def create_ignore_region(
    template_id: str,
    payload: IgnoreRegionCreate,
) -> ApiResponse:
    return ok(admin_templates.create_ignore_region(template_id, payload))


@router.put("/admin/templates/{template_id}/ignore-regions/{region_id}", response_model=ApiResponse)
def update_ignore_region(
    template_id: str,
    region_id: str,
    payload: IgnoreRegionUpdate,
) -> ApiResponse:
    return ok(admin_templates.update_ignore_region(template_id, region_id, payload))


@router.delete("/admin/templates/{template_id}/ignore-regions/{region_id}", response_model=ApiResponse)
def delete_ignore_region(
    template_id: str,
    region_id: str,
) -> ApiResponse:
    return ok(admin_templates.delete_ignore_region(template_id, region_id))


@router.post("/admin/templates/{template_id}/generate-layout-embedding", response_model=ApiResponse)
def generate_template_embedding(template_id: str) -> ApiResponse:
    return ok(embeddings.generate_for_template(template_id))


@router.post("/admin/templates/{template_id}/embedding-jobs", response_model=ApiResponse)
def create_embedding_job(template_id: str) -> ApiResponse:
    return ok(embeddings.create_embedding_job(template_id))


@router.get("/admin/templates/{template_id}/embedding-jobs/latest", response_model=ApiResponse)
def get_latest_embedding_job(template_id: str) -> ApiResponse:
    return ok(embeddings.latest_embedding_job(template_id))


@router.post("/admin/embedding-jobs/{job_id}/complete-dev", response_model=ApiResponse)
def complete_embedding_job_dev(job_id: str) -> ApiResponse:
    return ok(embeddings.complete_job_dev(job_id))


@router.post("/admin/embedding-jobs/{job_id}/run-dev", response_model=ApiResponse)
def run_embedding_job_dev(job_id: str) -> ApiResponse:
    return ok(embeddings.run_job_dev(job_id))


@router.post("/admin/embedding-jobs/{job_id}/fail-dev", response_model=ApiResponse)
def fail_embedding_job_dev(job_id: str) -> ApiResponse:
    return ok(embeddings.fail_job_dev(job_id))


@router.post("/admin/templates/{template_id}/pages/{page_id}/generate-layout-embedding", response_model=ApiResponse)
def generate_template_page_embedding(
    template_id: str,
    page_id: str,
) -> ApiResponse:
    return ok(embeddings.generate_for_template_page(template_id, page_id))


@router.post("/admin/templates/{template_id}/test", response_model=ApiResponse)
def test_template(
    template_id: str,
    payload: TemplateTestRequest,
) -> ApiResponse:
    return ok(admin_templates.test_template(template_id, payload))


@router.post("/admin/templates/{template_id}/test-extraction", response_model=ApiResponse)
def test_template_extraction_fields(template_id: str) -> ApiResponse:
    return ok(admin_templates.test_extraction_fields(template_id))


@router.post("/admin/templates/{template_id}/test-verification", response_model=ApiResponse)
def test_template_verification_anchors(template_id: str) -> ApiResponse:
    return ok(admin_templates.test_verification_anchors(template_id))


@router.post("/admin/templates/{template_id}/prepublish-simulation", response_model=ApiResponse)
def run_template_prepublish_simulation(template_id: str) -> ApiResponse:
    return ok(admin_templates.run_prepublish_simulation(template_id))


@router.post("/admin/templates/{template_id}/prepublish-detection-test", response_model=ApiResponse)
async def run_template_prepublish_detection_test(
    template_id: str,
    request: Request,
) -> ApiResponse:
    file_bytes = await _read_dev_detection_image(request)
    return ok(admin_templates.run_prepublish_detection_test(template_id, file_bytes))


@router.post("/admin/templates/{template_id}/confirm-publish", response_model=ApiResponse)
def confirm_template_publish(template_id: str) -> ApiResponse:
    return ok(admin_templates.confirm_publish_template(template_id))


@router.post("/admin/templates/{template_id}/approve", response_model=ApiResponse)
def approve_template(template_id: str) -> ApiResponse:
    return ok(admin_templates.approve_template(template_id))


@router.post("/admin/templates/{template_id}/reject", response_model=ApiResponse)
def reject_template(
    template_id: str,
    payload: RejectRequest,
) -> ApiResponse:
    return ok(admin_templates.reject_template(template_id, payload.reason))
