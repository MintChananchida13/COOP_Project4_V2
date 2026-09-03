from typing import Any, Dict, List, Optional

from pydantic import AliasChoices, BaseModel, Field


class ApiResponse(BaseModel):
    ok: bool = True
    data: Dict[str, Any]
    error: Optional[Dict[str, Any]] = None


class AuthRegisterRequest(BaseModel):
    email: str
    password: str = Field(..., min_length=6)
    role: str = "user"


class AuthLoginRequest(BaseModel):
    email: str
    password: str


class RoiRatio(BaseModel):
    page_number: int = Field(..., ge=1)
    x_ratio: float = Field(..., ge=0, le=1)
    y_ratio: float = Field(..., ge=0, le=1)
    width_ratio: float = Field(..., gt=0, le=1)
    height_ratio: float = Field(..., gt=0, le=1)


class PageInput(BaseModel):
    page_number: int = Field(..., ge=1)
    original_image_url: Optional[str] = None
    normalized_image_url: Optional[str] = None
    source_file_id: Optional[str] = Field(default=None, validation_alias=AliasChoices("source_file_id", "sourceFileId"))
    source_file_name: Optional[str] = Field(default=None, validation_alias=AliasChoices("source_file_name", "sourceFileName"))


class DocumentUploadRequest(BaseModel):
    uploaded_by: Optional[str] = None
    original_file_url: Optional[str] = None
    pages: List[PageInput] = Field(default_factory=list)


class ExtractFieldSelection(BaseModel):
    template_field_id: str
    page_number: int = Field(..., ge=1)


class ExtractionRequest(BaseModel):
    fields: List[ExtractFieldSelection] = Field(default_factory=list)


class CustomOcrField(BaseModel):
    field_name: str
    display_label: str
    roi: RoiRatio


class CustomOcrRequest(BaseModel):
    document_page_id: Optional[str] = None
    fields: List[CustomOcrField] = Field(default_factory=list)


class TemplateRequestCreate(BaseModel):
    requested_by: Optional[str] = None
    request_title: str
    document_type: Optional[str] = None
    sample_file_url: Optional[str] = None
    request_mode: str = "image_only"
    page_count: int = Field(default=1, ge=1)
    user_note: Optional[str] = None
    pages: List[PageInput] = Field(default_factory=list)


class TemplateRequestUpdate(BaseModel):
    request_title: Optional[str] = None
    document_type: Optional[str] = None
    sample_file_url: Optional[str] = None
    request_mode: Optional[str] = None
    status: Optional[str] = None
    user_note: Optional[str] = None
    admin_note: Optional[str] = None
    page_count: Optional[int] = Field(default=None, ge=1)


class TemplateRequestImageCreate(BaseModel):
    sample_image_url: str
    image_source: str = "admin_upload"
    review_status: str = "pending"
    is_canonical: bool = False
    source_file_id: Optional[str] = Field(default=None, validation_alias=AliasChoices("source_file_id", "sourceFileId"))
    source_file_name: Optional[str] = Field(default=None, validation_alias=AliasChoices("source_file_name", "sourceFileName"))


class TemplateRequestImageUpdate(BaseModel):
    sample_image_url: Optional[str] = None
    image_source: Optional[str] = None
    review_status: Optional[str] = None
    is_canonical: Optional[bool] = None
    source_file_id: Optional[str] = Field(default=None, validation_alias=AliasChoices("source_file_id", "sourceFileId"))
    source_file_name: Optional[str] = Field(default=None, validation_alias=AliasChoices("source_file_name", "sourceFileName"))


class RequestedFieldCreate(BaseModel):
    template_request_page_id: str
    page_number: int = Field(..., ge=1)
    field_name: str
    display_label: str
    roi: RoiRatio
    data_type: Optional[str] = Field(default=None, validation_alias=AliasChoices("data_type", "dataType"))
    extraction_method: Optional[str] = Field(default=None, validation_alias=AliasChoices("extraction_method", "extractionMethod"))
    user_note: Optional[str] = None


class RequestedFieldUpdate(BaseModel):
    field_name: Optional[str] = None
    display_label: Optional[str] = None
    roi: Optional[RoiRatio] = None
    data_type: Optional[str] = Field(default=None, validation_alias=AliasChoices("data_type", "dataType"))
    extraction_method: Optional[str] = Field(default=None, validation_alias=AliasChoices("extraction_method", "extractionMethod"))
    user_note: Optional[str] = None


class TemplateCreate(BaseModel):
    name: str
    document_type: Optional[str] = None
    category: Optional[str] = None
    description: Optional[str] = None
    shared_fields: List[str] = Field(default_factory=list)
    page_count: int = Field(default=1, ge=1)
    similarity_threshold: float = Field(default=0.75, ge=0, le=1)
    final_confidence_threshold: float = Field(default=0.75, ge=0, le=1)
    layout_weight: float = Field(default=0.50, ge=0, le=1)
    text_anchor_weight: float = Field(default=0.35, ge=0, le=1)
    image_anchor_weight: float = Field(default=0.15, ge=0, le=1)
    detection_mode: str = Field(default="all_pages", validation_alias=AliasChoices("detection_mode", "detectionMode"))
    main_page_number: int = Field(default=1, ge=1, validation_alias=AliasChoices("main_page_number", "mainPageNumber"))
    created_by: Optional[str] = None


class TemplateUpdate(BaseModel):
    name: Optional[str] = None
    document_type: Optional[str] = None
    category: Optional[str] = None
    description: Optional[str] = None
    shared_fields: Optional[List[str]] = None
    status: Optional[str] = None
    page_count: Optional[int] = Field(default=None, ge=1)
    similarity_threshold: Optional[float] = Field(default=None, ge=0, le=1)
    final_confidence_threshold: Optional[float] = Field(default=None, ge=0, le=1)
    layout_weight: Optional[float] = Field(default=None, ge=0, le=1)
    text_anchor_weight: Optional[float] = Field(default=None, ge=0, le=1)
    image_anchor_weight: Optional[float] = Field(default=None, ge=0, le=1)
    detection_mode: Optional[str] = Field(default=None, validation_alias=AliasChoices("detection_mode", "detectionMode"))
    main_page_number: Optional[int] = Field(default=None, ge=1, validation_alias=AliasChoices("main_page_number", "mainPageNumber"))
    rejection_reason: Optional[str] = None


class TemplateVersionCreate(BaseModel):
    request_id: Optional[str] = None
    base_template_id: Optional[str] = None
    template_name: Optional[str] = None
    description: Optional[str] = None
    shared_fields: List[str] = Field(default_factory=list)
    document_type: Optional[str] = None
    similarity_threshold: float = Field(default=0.72, ge=0, le=1)
    reuse_roi: bool = True
    detection_mode: str = Field(default="all_pages", validation_alias=AliasChoices("detection_mode", "detectionMode"))
    main_page_number: int = Field(default=1, ge=1, validation_alias=AliasChoices("main_page_number", "mainPageNumber"))


class TemplateRequestConvert(BaseModel):
    template_name: Optional[str] = Field(default=None, validation_alias=AliasChoices("template_name", "templateName"))
    description: Optional[str] = None
    similarity_threshold: Optional[float] = Field(default=None, ge=0, le=1)
    detection_mode: str = Field(default="all_pages", validation_alias=AliasChoices("detection_mode", "detectionMode"))
    main_page_number: int = Field(default=1, ge=1, validation_alias=AliasChoices("main_page_number", "mainPageNumber"))


class TemplateVersionFromRequestCreate(BaseModel):
    base_template_id: str = Field(..., validation_alias=AliasChoices("base_template_id", "baseTemplateId"))
    version_name: Optional[str] = Field(default=None, validation_alias=AliasChoices("version_name", "versionName"))
    similarity_threshold: Optional[float] = Field(default=None, ge=0, le=1)
    final_confidence_threshold: Optional[float] = Field(default=None, ge=0, le=1, validation_alias=AliasChoices("final_confidence_threshold", "finalConfidenceThreshold"))
    reuse_roi: bool = Field(default=True, validation_alias=AliasChoices("reuse_roi", "reuseRoi"))
    detection_mode: str = Field(default="all_pages", validation_alias=AliasChoices("detection_mode", "detectionMode"))
    main_page_number: int = Field(default=1, ge=1, validation_alias=AliasChoices("main_page_number", "mainPageNumber"))


class TemplatePageCreate(BaseModel):
    page_number: int = Field(..., ge=1)
    page_name: Optional[str] = None
    sample_image_url: Optional[str] = None
    normalized_image_url: Optional[str] = None
    layout_signature_json: Optional[str] = None


class TemplatePageUpdate(BaseModel):
    page_number: Optional[int] = Field(default=None, ge=1)
    page_name: Optional[str] = None
    sample_image_url: Optional[str] = None
    normalized_image_url: Optional[str] = None
    layout_signature_json: Optional[str] = None
    similarity_threshold: Optional[float] = Field(default=None, ge=0, le=1)
    final_confidence_threshold: Optional[float] = Field(default=None, ge=0, le=1)


class TemplateFieldCreate(BaseModel):
    template_page_id: str
    page_number: int = Field(..., ge=1)
    field_name: str
    display_label: str
    roi: RoiRatio
    data_type: Optional[str] = None
    user_selectable: bool = True
    default_selected: bool = False
    use_for_verification: bool = False
    expected_text: Optional[str] = None
    match_type: Optional[str] = None
    required_for_verification: bool = False
    extraction_method: str = "fixed_roi"
    roi_mode: str = Field(default="fix", validation_alias=AliasChoices("roi_mode", "roiMode"))
    expected_content: Optional[str] = Field(default=None, validation_alias=AliasChoices("expected_content", "expectedContent"))
    anchor_text: Optional[str] = None
    regex_pattern: Optional[str] = None
    roi_padding: Optional[float] = None
    verification_weight: Optional[float] = Field(default=1.0, ge=0)
    image_category: Optional[str] = None
    sort_order: int = 0


class TemplateFieldUpdate(BaseModel):
    template_page_id: Optional[str] = None
    page_number: Optional[int] = Field(default=None, ge=1)
    field_name: Optional[str] = None
    display_label: Optional[str] = None
    roi: Optional[RoiRatio] = None
    data_type: Optional[str] = None
    user_selectable: Optional[bool] = None
    default_selected: Optional[bool] = None
    use_for_verification: Optional[bool] = None
    expected_text: Optional[str] = None
    match_type: Optional[str] = None
    required_for_verification: Optional[bool] = None
    extraction_method: Optional[str] = None
    roi_mode: Optional[str] = Field(default=None, validation_alias=AliasChoices("roi_mode", "roiMode"))
    expected_content: Optional[str] = Field(default=None, validation_alias=AliasChoices("expected_content", "expectedContent"))
    anchor_text: Optional[str] = None
    regex_pattern: Optional[str] = None
    roi_padding: Optional[float] = None
    verification_weight: Optional[float] = Field(default=None, ge=0)
    image_category: Optional[str] = None
    sort_order: Optional[int] = None


class IgnoreRegionCreate(BaseModel):
    template_page_id: str
    page_number: int = Field(..., ge=1)
    field_name: str
    roi: RoiRatio


class IgnoreRegionUpdate(BaseModel):
    template_page_id: Optional[str] = None
    page_number: Optional[int] = Field(default=None, ge=1)
    field_name: Optional[str] = None
    roi: Optional[RoiRatio] = None


class RejectRequest(BaseModel):
    reason: Optional[str] = None


class TemplateTestRequest(BaseModel):
    original_file_url: Optional[str] = None
    pages: List[PageInput] = Field(default_factory=list)


class ImageVerificationCategoryCreate(BaseModel):
    value: str
    label: str
    prompt: str
    match_threshold: float = Field(default=0.70, ge=0, le=1)
    margin_threshold: float = Field(default=0.05, ge=0, le=1)
    evidence_temperature: float = Field(default=1.0, gt=0)
    enabled: bool = True


class ImageVerificationCategoryUpdate(BaseModel):
    label: Optional[str] = None
    prompt: Optional[str] = None
    match_threshold: Optional[float] = Field(default=None, ge=0, le=1)
    margin_threshold: Optional[float] = Field(default=None, ge=0, le=1)
    evidence_temperature: Optional[float] = Field(default=None, gt=0)
    enabled: Optional[bool] = None
