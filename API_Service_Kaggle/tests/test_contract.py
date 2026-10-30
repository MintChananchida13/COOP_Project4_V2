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


def test_v24_api_single_batch_decisions_and_config_validation(monkeypatch):
    from app.siglip_policy import POSITIVE, NEGATIVE

    monkeypatch.setattr(main, "settings", replace(main.settings, api_key="secret"))
    monkeypatch.setattr(main.manager, "get", lambda name: (None, 0))
    scores = [8.1 if name == "thai_garuda" else 6.5818 if name == "emblem" else -30.0 for name in (*POSITIVE, *NEGATIVE)]
    monkeypatch.setattr(main, "siglip_logits", lambda images, categories, instance: [scores for image in images])
    client = TestClient(main.app)
    headers = {"Authorization": "Bearer secret"}
    categories = [{"value": name, "prompt": name} for name in (*POSITIVE, *NEGATIVE)]
    body = {"image": image_url(), "categories": categories}
    single = client.post("/api/v1/image-verifications", json=body, headers=headers)
    assert single.status_code == 200
    result = single.json()["result"]
    assert result["logits"] == scores
    trace = result["decision"]
    assert trace["scoring_version"] == "siglip-single-prompt-v2.4"
    assert trace["final_predicted_category"] == "thai_garuda"
    assert trace["confidence_gate_passed"] == "not_applied"
    assert trace["margin_gate_passed"] == "not_applied"
    batch = client.post("/api/v1/image-classification-batches", json={"images": [image_url(), image_url()], "categories": categories}, headers=headers)
    assert batch.status_code == 200
    assert all(item["decision"] == trace for item in batch.json()["result"]["results"])
    rejected = client.post("/api/v1/image-verifications", json={**body, "decision_config": {"confidence_min_relative_score": 0.99}}, headers=headers)
    assert rejected.json()["result"]["decision"]["decision_reason"] == "confidence_gate_failed"
    for invalid in (True, -0.1, 1.1, "0.5"):
        response = client.post("/api/v1/image-verifications", json={**body, "decision_config": {"confidence_min_relative_score": invalid}}, headers=headers)
        assert response.status_code == 422


def test_siglip_single_and_batch_responses_include_measured_runtime(monkeypatch):
    monkeypatch.setattr(main, "settings", replace(main.settings, api_key="secret"))
    monkeypatch.setattr(main.manager, "get", lambda name: (None, 0.0))
    monkeypatch.setattr(main, "siglip_logits", lambda images, categories, instance: [[1.0] for image in images])
    client = TestClient(main.app)
    categories = [{"value": "emblem", "prompt": "Institutional crest"}]
    for route, payload in (
        ("image-verifications", {"image": image_url()}),
        ("image-classification-batches", {"images": [image_url(), image_url()]}),
    ):
        response = client.post(f"/api/v1/{route}", json={**payload, "categories": categories},
                               headers={"Authorization": "Bearer secret"})
        assert response.status_code == 200
        body = response.json()
        assert body["model"] == "google/siglip-so400m-patch14-384"
        timing = body["timing"]
        assert isinstance(timing["inference_ms"], (int, float))
        assert timing["inference_ms"] >= 0
        assert timing["image_siglip_runtime_ms"] == timing["inference_ms"]
        assert timing["model_load_ms"] == 0
        assert timing["total_runtime_ms"] >= timing["inference_ms"]


def test_layout_timing_separates_detector_without_changing_results(monkeypatch):
    monkeypatch.setattr(main.manager, "get", lambda name: (name, 0.0))
    monkeypatch.setattr(main, "paddle_result", lambda model, image, **kwargs: {"model": model})
    for layout_only in (False, True):
        response = main.layout(main.LayoutImage(image=image_url(), layout_only=layout_only))
        timing = response["timing"]
        assert response["result"]["raw"]["layout"] == [{"model": "layout"}]
        assert timing["layout_runtime_ms"] == timing["model_timings"]["layout"]["inference_ms"]
        assert timing["model_timings"]["layout"]["model_load_ms"] == 0
        assert timing["inference_ms"] >= timing["layout_runtime_ms"]
        if layout_only:
            assert "text_detection" not in timing["model_timings"]
        else:
            assert response["result"]["raw"]["text_detection"] == [{"model": "det"}]
            assert timing["model_timings"]["text_detection"]["inference_ms"] >= 0


def test_contract_routes_with_fake_runtime(monkeypatch):
    monkeypatch.setattr(main, "settings", replace(main.settings, api_key="secret"))
    calls = []

    def fake_run(name, operation, **kwargs):
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
