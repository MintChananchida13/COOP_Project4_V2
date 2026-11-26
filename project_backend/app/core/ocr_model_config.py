"""Built-in OCR defaults and saved model settings shared by inference and Admin."""
import json
from copy import deepcopy
from typing import Any, Dict

DEFAULT_OCR_MODEL_SETTINGS: Dict[str, Any] = {
    "ocr_engine": "paddle",
    "table": {"mode": "follow_main", "text_detection": "", "text_recognition": ""},
    "active": {
        "text_detection": "ocr_det_v6_medium",
        "text_recognition": "thai_ocr_v5_mobile",
    },
    "models": {
        "text_detection": [
            {
                "id": "ocr_det_v6_medium",
                "display_name": "PP-OCRv6 Medium",
                "single_api_path": "/paddle/api/v1/text-detections?version=v6",
                "batch_api_path": "/paddle/api/v1/text-detection-batches?version=v6",
            },
        ],
        "text_recognition": [
            {
                "id": "thai_ocr_v5_mobile",
                "display_name": "Thai PP-OCRv5 Mobile",
                "single_api_path": "/paddle/api/v1/text-recognitions",
                "batch_api_path": "/paddle/api/v1/text-recognition-batches",
            },
        ],
    },
}


def fixed_ocr_model_settings() -> Dict[str, Any]:
    return deepcopy(DEFAULT_OCR_MODEL_SETTINGS)


def normalize_saved_ocr_settings(value: Any = None) -> Dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            value = None
    settings = fixed_ocr_model_settings()
    if not isinstance(value, dict):
        return settings
    raw_models = value.get("models") or {}
    raw_active = value.get("active") or {}
    for kind in settings["models"]:
        incoming = raw_models.get(kind) if isinstance(raw_models, dict) else None
        models = []
        seen = set()
        for item in incoming if isinstance(incoming, list) else []:
            if not isinstance(item, dict):
                continue
            model_id = str(item.get("id") or "").strip()
            if not model_id or model_id in seen or model_id == "ocr_det_v5_server":
                continue
            paths = [str(item.get(key) or "").strip() for key in ("single_api_path", "batch_api_path")]
            if not str(item.get("display_name") or "").strip() or not all(path.startswith("/") and not path.startswith("//") for path in paths):
                continue
            models.append(deepcopy(item))
            seen.add(model_id)
        if models:
            settings["models"][kind] = models
        ids = {item["id"] for item in settings["models"][kind]}
        active = raw_active.get(kind) if isinstance(raw_active, dict) else None
        if active in ids:
            settings["active"][kind] = active
        elif settings["active"][kind] not in ids:
            settings["active"][kind] = settings["models"][kind][0]["id"]
    settings["ocr_engine"] = value.get("ocr_engine") if value.get("ocr_engine") in {"paddle", "openthai", "typhoon"} else "paddle"
    table = value.get("table")
    if isinstance(table, dict):
        settings["table"] = {
            "mode": table.get("mode") if table.get("mode") in {"separate", "typhoon"} else "follow_main",
            "text_detection": str(table.get("text_detection") or ""),
            "text_recognition": str(table.get("text_recognition") or ""),
        }
    return settings


def table_ocr_query(settings: Dict[str, Any]) -> Dict[str, str]:
    """Resolve Table V2's OCR choices from the same saved model catalog."""
    from urllib.parse import parse_qs, urlsplit
    table = settings.get("table") or {}
    selected = table if table.get("mode") == "separate" else settings["active"]
    query = {}
    for kind, prefix, default_version in (("text_detection", "det", "6"), ("text_recognition", "rec", "5")):
        model = next((item for item in settings["models"][kind] if item["id"] == selected.get(kind)), None)
        if model is None:
            raise ValueError(f"Configured Table {kind} model was not found")
        single = parse_qs(urlsplit(model["single_api_path"]).query)
        batch = parse_qs(urlsplit(model["batch_api_path"]).query)
        version = (single.get("version") or batch.get("version") or [model.get("model_version") or default_version])[0]
        variant = (
            single.get("model") or single.get("model_id")
            or batch.get("model") or batch.get("model_id")
            or [model.get("runtime_model_id") or "baseline"]
        )[0]
        if variant == "default":
            variant = "baseline"
        query[f"{prefix}_version"] = str(version)
        query[f"{prefix}_model"] = str(variant)
    return query
