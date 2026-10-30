"""Load and warm all configured models in the running Model Service process."""

import json
from urllib.parse import urlencode

from kaggle_secrets_loader import load_secrets
from test_all_models import image_url, layout_image, request, table_image, text_image


def warmup():
    token = load_secrets(("MODEL_GATEWAY_API_KEY",)).get("MODEL_GATEWAY_API_KEY")
    if not token:
        raise RuntimeError("MODEL_GATEWAY_API_KEY is not configured")
    status, data, _ = request("/api/v1/ocr-models", token=token, timeout=30)
    if status != 200 or not isinstance(data.get("models"), dict):
        raise RuntimeError(f"Unable to read model registry (HTTP {status}): {data.get('detail', 'invalid response')}")
    text = image_url(text_image("MODEL WARMUP"))
    document = image_url(layout_image())
    table = image_url(table_image())
    calls = [
        ("layout", "/api/v1/document-layouts", {"image": document, "layout_only": True}),
    ]
    for category, api in (("detection", "text-detections"), ("recognition", "text-recognitions")):
        for entry in data["models"].get(category, []):
            query = urlencode({"version": entry["version"], "model_id": entry["model_id"]})
            calls.append((f"{category} {entry['version']}/{entry['model_id']}", f"/api/v1/{api}?{query}", {"image": text}))
    calls += [
        ("table", "/api/v1/table-model-results", {"image": table}),
        ("siglip", "/api/v1/image-verifications", {"image": document, "categories": [{"value": "document", "prompt": "a document"}]}),
    ]
    outcomes = []
    for name, path, payload in calls:
        try:
            code, result, elapsed = request(path, payload=payload, token=token, timeout=900)
            ok = code == 200 and result.get("success") is True
            detail = None if ok else str(result.get("detail") or result.get("error") or f"HTTP {code}")
        except Exception as error:
            ok, elapsed, detail = False, None, str(error)
        item = {"model": name, "ready": ok, "elapsed_ms": elapsed, "error": detail}
        outcomes.append(item)
        print(json.dumps(item, ensure_ascii=False), flush=True)
    failed = sum(not item["ready"] for item in outcomes)
    print(f"Warmup: {len(outcomes) - failed}/{len(outcomes)} models ready; {failed} failed", flush=True)
    return outcomes


if __name__ == "__main__":
    warmup()
