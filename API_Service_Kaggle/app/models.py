import logging
import threading
import time

import cv2

from .config import settings
from .serialization import json_safe


logger = logging.getLogger(__name__)
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
                paddle.set_flags({"FLAGS_cpu_num_threads": settings.cpu_threads})
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
    return json_safe(list(output))


def siglip_logits(images, categories, instance):
    import torch
    from PIL import Image

    model, processor = instance
    prompts = [category["prompt"] for category in categories]
    pil_images = [Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB)) for image in images]
    inputs = processor(text=prompts, images=pil_images, padding="max_length", return_tensors="pt")
    with torch.inference_mode():
        logits = model(**inputs).logits_per_image.cpu().tolist()
    return logits
