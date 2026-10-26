import base64
from dataclasses import replace

import cv2
import numpy as np
from fastapi.testclient import TestClient

from app import main
from app.serialization import decode_image


def image_url():
    ok, encoded = cv2.imencode(".png", np.zeros((8, 8, 3), dtype=np.uint8))
    assert ok
    return "data:image/png;base64," + base64.b64encode(encoded).decode()


def test_health_and_auth(monkeypatch):
    monkeypatch.setattr(main, "settings", replace(main.settings, api_key="secret"))
    client = TestClient(main.app)
    assert client.get("/health").status_code == 200
    assert client.post("/api/v1/text-recognitions", json={"image": image_url()}).status_code == 401


def test_contract_routes_with_fake_runtime(monkeypatch):
    monkeypatch.setattr(main, "settings", replace(main.settings, api_key="secret"))
    calls = []

    def fake_run(name, operation):
        calls.append(name)
        return {"success": True, "model": name, "result": {}, "timing": {}}

    monkeypatch.setattr(main, "run", fake_run)
    client = TestClient(main.app)
    headers = {"Authorization": "Bearer secret"}
    image = image_url()
    requests = [
        ("/api/v1/document-layouts", {"image": image, "layout_only": True}),
        ("/api/v1/text-detections?version=v6", {"image": image}),
        ("/api/v1/text-detection-batches?version=v6", {"images": [image, image]}),
        ("/api/v1/text-recognitions", {"image": image}),
        ("/api/v1/text-recognition-batches", {"images": [image]}),
        ("/api/v1/table-model-results", {"image": image}),
        ("/api/v1/image-verifications", {"image": image, "categories": [{"value": "a", "prompt": "a"}]}),
        ("/api/v1/image-classification-batches", {"images": [image], "categories": [{"value": "a", "prompt": "a"}]}),
    ]
    for path, body in requests:
        assert client.post(path, json=body, headers=headers).status_code == 200
    assert calls == ["layout", "det", "det", "rec", "rec", "table", "siglip", "siglip"]
    assert client.post("/api/v1/text-detections?version=v5", json={"image": image}, headers=headers).status_code == 422
    assert client.post("/api/v1/text-recognitions", json={"image": "bad"}, headers=headers).status_code == 422


def test_siglip_transparency_composites_on_white():
    pixels = np.array([[[0, 0, 0, 0], [0, 0, 0, 255], [0, 0, 0, 128]]], dtype=np.uint8)
    ok, encoded = cv2.imencode(".png", pixels)
    assert ok
    image = "data:image/png;base64," + base64.b64encode(encoded).decode()
    decoded = decode_image(image, composite_alpha=True)
    assert decoded[0, 0].tolist() == [255, 255, 255]
    assert decoded[0, 1].tolist() == [0, 0, 0]
    assert decoded[0, 2].tolist() == [127, 127, 127]
    assert decode_image(image)[0, 0].tolist() == [0, 0, 0]


def test_siglip_rgb_decode_keeps_existing_pixels():
    image = image_url()
    assert np.array_equal(decode_image(image, composite_alpha=True), decode_image(image))


def test_siglip_single_batch_contract_and_prompt_order(monkeypatch):
    monkeypatch.setattr(main, "settings", replace(main.settings, api_key="secret"))
    received = []

    def inference(images, categories, instance):
        received.append([category["value"] for category in categories])
        return [[float(index) for index in range(len(categories))] for image in images]

    def execute(name, operation):
        return {"success": True, "model": main.NAMES[name], "result": operation(None), "timing": {}}

    monkeypatch.setattr(main, "siglip_logits", inference)
    monkeypatch.setattr(main, "run", execute)
    client = TestClient(main.app)
    headers = {"Authorization": "Bearer secret"}
    categories = [{"value": f"category_{index}", "prompt": f"Prompt {index}"} for index in range(16)]
    image = image_url()
    single = client.post("/api/v1/image-verifications", json={"image": image, "categories": categories}, headers=headers).json()
    batch = client.post("/api/v1/image-classification-batches", json={"images": [image, image], "categories": categories}, headers=headers).json()
    assert single["success"] is True
    assert single["model"] == "google/siglip-so400m-patch14-384"
    assert single["result"]["logits"] == list(range(16))
    assert len(batch["result"]["results"]) == 2
    assert all(item["logits"] == single["result"]["logits"] for item in batch["result"]["results"])
    assert all(order == [category["value"] for category in categories] for order in received)
