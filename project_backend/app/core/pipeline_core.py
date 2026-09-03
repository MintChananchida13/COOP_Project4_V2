from dataclasses import asdict, dataclass
from typing import Any, Dict

from app.core.config import GATEWAY_URL
from app.core.model_runtime_client import ModelRuntimeKind, runtime_url


@dataclass(frozen=True)
class PipelineCoreConfig:
    version: str = "layout-signature-v1"
    layout_analyzer: str = "pp_doclayout_v3"
    text_detector: str = "pp_ocrv5_server_det"
    ocr_engine: str = "paddle_thai_ocr"
    ocr_model: str = "th_PP-OCRv5_mobile_rec"
    image_verification: str = "siglip_image_category"
    template_matcher: str = "layout_signature_sql"
    alignment_engine: str = "layout_signature_alignment_with_orb_fallback"
    roi_refiner: str = "adaptive_roi"
    model_runtime: str = "model_api_clients"
    layout_model_url: str = ""
    text_detection_model_url: str = ""
    text_recognition_model_url: str = ""
    table_model_url: str = ""
    image_verification_model_url: str = ""
    gateway_url: str = ""

    def to_debug_dict(self) -> Dict[str, Any]:
        return asdict(self)


def get_pipeline_core_config() -> PipelineCoreConfig:
    return PipelineCoreConfig(
        model_runtime="model_api_clients",
        layout_model_url=runtime_url(ModelRuntimeKind.LAYOUT) or "",
        text_detection_model_url=runtime_url(ModelRuntimeKind.TEXT_DETECTION) or "",
        text_recognition_model_url=runtime_url(ModelRuntimeKind.TEXT_RECOGNITION) or "",
        table_model_url=runtime_url(ModelRuntimeKind.TABLE) or "",
        image_verification_model_url=runtime_url(ModelRuntimeKind.IMAGE_VERIFICATION) or "",
        gateway_url=GATEWAY_URL.strip(),
        ocr_model="th_PP-OCRv5_mobile_rec",
    )
