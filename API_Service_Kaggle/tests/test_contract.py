import base64
from dataclasses import replace

import cv2
import numpy as np
from fastapi.testclient import TestClient

from app import main


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
