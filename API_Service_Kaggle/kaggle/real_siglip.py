"""Exercise the real SigLIP endpoint after starting both services."""

import base64
import json
import urllib.request

import cv2
import numpy as np

from kaggle_secrets_loader import load_secrets


def main():
    token = load_secrets(("MODEL_GATEWAY_API_KEY",)).get("MODEL_GATEWAY_API_KEY")
    if not token:
        raise RuntimeError("MODEL_GATEWAY_API_KEY is required")
    image = np.full((384, 384, 3), 240, dtype=np.uint8)
    ok, encoded = cv2.imencode(".png", image)
    if not ok:
        raise RuntimeError("Unable to encode test image")
    payload = {
        "image": "data:image/png;base64," + base64.b64encode(encoded).decode("ascii"),
        "categories": [
            {"value": "document", "prompt": "A blank paper document"},
            {"value": "photo", "prompt": "A photograph of an outdoor scene"},
        ],
    }
    request = urllib.request.Request(
        "http://127.0.0.1:8080/api/v1/image-verifications",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(request, timeout=600) as response:
        result = json.load(response)
    logits = result.get("result", {}).get("logits")
    if result.get("model") != "google/siglip-so400m-patch14-384" or not isinstance(logits, list) or len(logits) != 2:
        raise RuntimeError("SigLIP response does not match the backend contract")
    print("Real SigLIP CPU inference: PASS; two ordered raw logits returned")


if __name__ == "__main__":
    main()
