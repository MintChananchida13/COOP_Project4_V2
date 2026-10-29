"""Read-only live Layout+DET probe. Performs one real inference, no DB updates."""
import argparse
import json
import os
from pathlib import Path
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent / "API_Service_Kaggle/kaggle"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("template_id")
    parser.add_argument("--backend-url", default="http://127.0.0.1:18081")
    parser.add_argument("--gateway-url", default="http://127.0.0.1:8080")
    args = parser.parse_args()
    from kaggle_secrets_loader import load_secrets
    os.environ.update(load_secrets(("MODEL_GATEWAY_API_KEY",)))
    os.environ["GATEWAY_URL"] = args.gateway_url.rstrip("/")
    from app.business.services import _load_image_source, _image_to_bgr_array
    from app.model_runtime import layout_analysis_service as layout
    from app.processing.layout_signature_service import build_layout_signature
    url = args.backend_url.rstrip("/") + "/admin/templates/" + args.template_id
    with urllib.request.urlopen(url, timeout=60) as response:
        template = json.load(response)["data"]
    page = template["pages"][0]
    source = page.get("normalized_image_url") or page.get("sample_image_url")
    image = _image_to_bgr_array(_load_image_source(source))
    if image is None:
        raise RuntimeError("Template image could not be decoded")
    print("MODULE:", layout.__file__, flush=True)
    print("FUNCTION:", layout.analyze_layout_signature.__code__.co_filename, flush=True)
    print("IMAGE_SIZE:", [int(image.shape[1]), int(image.shape[0])], flush=True)
    # Observe the actual HTTP response without replacing inference or its output.
    original = layout.remote_analyze_layout
    def observed(*args, **kwargs):
        print("REQUEST_LAYOUT_ONLY:", kwargs.get("layout_only", False), flush=True)
        result = original(*args, **kwargs)
        body = result.get("result", result) if isinstance(result, dict) else {}
        raw = body.get("raw") or {}
        print("RAW_BRANCHES:", list(raw), flush=True)
        if "layout" in raw:
            print("RAW_LAYOUT_BOXES:", len(layout._layout_detection_items_from_response(raw["layout"])), flush=True)
        if "text_detection" in raw:
            print("RAW_DET_BOXES:", len(layout._text_detection_items_from_response(raw["text_detection"])), flush=True)
        return result
    layout.remote_analyze_layout = observed
    try:
        layout.clear_layout_analysis_cache()
        timing = {}
        analysis = layout.analyze_layout_signature(image, timing=timing)
        signature = build_layout_signature(analysis)
        print("GENERATED_SIGNATURE:", json.dumps({key: signature.get(key) for key in
            ("model", "region_count", "label_counts", "image_fingerprint")}), flush=True)
        print("PARSED_COUNTS:", json.dumps({key: timing.get(key) for key in
            ("raw_cache_hit", "layout_item_count", "text_detection_item_count", "region_count")}), flush=True)
    finally:
        layout.remote_analyze_layout = original


if __name__ == "__main__":
    main()
