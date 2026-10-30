import logging
import threading
import time
import json
import os
from pathlib import Path

import cv2

from .config import settings
from .serialization import json_safe


logger = logging.getLogger(__name__)
REGISTRY_PATH = Path(__file__).resolve().parent.parent / "config" / "model_registry.json"
registry_lock = threading.RLock()


def registry():
    with registry_lock:
        with REGISTRY_PATH.open(encoding="utf-8") as stream:
            return json.load(stream)


def register_ocr_model(kind, entry):
    category = "detection" if kind == "det" else "recognition"
    with registry_lock:
        data = registry()
        if any(item.get("model_id") == entry["model_id"] for item in data[category]):
            raise ValueError("Model ID already exists")
        data[category].append(entry)
        import tempfile
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=REGISTRY_PATH.parent, delete=False) as output:
                temporary = Path(output.name)
                json.dump(data, output, ensure_ascii=False, indent=2)
                output.write("\n")
            os.replace(temporary, REGISTRY_PATH)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()


def resolve_ocr(kind, version, model_id):
    entries = registry()["detection" if kind == "det" else "recognition"]
    version = version or ("v6" if kind == "det" else "v5")
    model_id = model_id or "default"
    matches = [item for item in entries if item["version"] == version and item["model_id"] == model_id]
    if len(matches) != 1:
        raise ValueError(f"Unknown {kind} model version={version} model_id={model_id}")
    return matches[0]


def local_weights_path(entry):
    path = Path(entry.get("model_dir") or "")
    root = os.getenv("MODEL_WEIGHTS_ROOT", "").strip()
    prefix = "/workspace/API_service/weights/"
    if root and str(path).replace("\\", "/").startswith(prefix):
        path = Path(root) / str(path).replace("\\", "/")[len(prefix):]
    if not path.is_dir():
        raise FileNotFoundError(f"Model directory not found: {path}")
    if not (path / "inference.yml").is_file():
        raise FileNotFoundError(f"Missing inference.yml in {path}")
    if not ((path / "inference.json").is_file() or (path / "inference.pdmodel").is_file()):
        raise FileNotFoundError(f"Missing inference model in {path}")
    if not (path / "inference.pdiparams").is_file():
        raise FileNotFoundError(f"Missing inference.pdiparams in {path}")
    return path
NAMES = {
    "layout": "PP-DocLayoutV3",
    "det": "PP-OCRv6_medium_det",
    "rec": "th_PP-OCRv5_mobile_rec",
    "table": "TableRecognitionPipelineV2",
    "siglip": "google/siglip-so400m-patch14-384",
}


class ModelManager:
    def __init__(self):
        self.models = {}
        self.locks = {name: threading.Lock() for name in NAMES}
        self.inference_locks = {name: threading.Lock() for name in NAMES}
        self.ocr_models = {}
        self.ocr_lock = threading.Lock()

    def get_ocr(self, kind, entry):
        key = (kind, entry["version"], entry["model_id"])
        if entry["model_id"] == "default":
            return self.get(kind)
        with self.ocr_lock:
            if key in self.ocr_models:
                return self.ocr_models[key], 0.0
            model_dir = local_weights_path(entry) if entry.get("requires_local_weights") else None
            started = time.perf_counter()
            import paddle
            from paddleocr import TextDetection, TextRecognition
            paddle.set_device("cpu")
            kwargs = {"model_name": entry["model_name"], "device": "cpu"}
            if model_dir is not None:
                kwargs["model_dir"] = str(model_dir)
            model = (TextDetection if kind == "det" else TextRecognition)(**kwargs)
            self.ocr_models[key] = model
            return model, round((time.perf_counter() - started) * 1000, 2)

    def get(self, name):
        with self.locks[name]:
            if name in self.models:
                return self.models[name], 0.0
            started = time.perf_counter()
            logger.info("Loading %s on CPU", NAMES[name])
            if name == "siglip":
                import torch
                from transformers import AutoProcessor, SiglipModel

                torch.set_num_threads(settings.cpu_threads)
                model = SiglipModel.from_pretrained(NAMES[name]).to("cpu").eval()
                instance = (model, AutoProcessor.from_pretrained(NAMES[name]))
            else:
                import paddle
                from paddleocr import LayoutDetection, TableRecognitionPipelineV2, TextDetection, TextRecognition

                paddle.set_device("cpu")
                if name == "layout":
                    instance = LayoutDetection(model_name=NAMES[name], device="cpu")
                elif name == "det":
                    instance = TextDetection(model_name=NAMES[name], device="cpu")
                elif name == "rec":
                    instance = TextRecognition(model_name=NAMES[name], device="cpu")
                else:
                    instance = TableRecognitionPipelineV2(
                        wired_table_structure_recognition_model_name="SLANeXt_wired",
                        wireless_table_structure_recognition_model_name="SLANeXt_wireless",
                        text_detection_model_name="PP-OCRv5_server_det",
                        text_recognition_model_name=NAMES["rec"],
                        use_doc_orientation_classify=False,
                        use_doc_unwarping=False,
                        device="cpu",
                    )
            elapsed = round((time.perf_counter() - started) * 1000, 2)
            self.models[name] = instance
            logger.info("Loaded %s in %.2f ms", NAMES[name], elapsed)
            return instance, elapsed


manager = ModelManager()


def predict(name, image, model):
    output = model.predict(image)
    if name == "layout":
        # PaddleX result objects inherit dict and retain the original image.
        # Omit it before json_safe expands the pixel array into JSON lists.
        output = [{key: value for key, value in item.items() if key != "input_img"}
                  if isinstance(item, dict) else item for item in output]
    return json_safe(list(output))


def siglip_logits(images, categories, instance):
    import torch
    from PIL import Image

    model, processor = instance
    prompts = [category["prompt"] for category in categories]
    pil_images = [Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB)) for image in images]
    inputs = processor(text=prompts, images=pil_images, padding="max_length", truncation=True, return_tensors="pt")
    with torch.inference_mode():
        logits = model(**inputs).logits_per_image.float().cpu().tolist()
    return logits
