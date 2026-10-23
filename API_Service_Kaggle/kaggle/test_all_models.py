"""Run real HTTP inference against every Kaggle CPU model endpoint."""

import argparse
import base64
import json
import math
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from kaggle_secrets_loader import load_secrets


BASE_URL = "http://127.0.0.1:8080"
MODEL_NAMES = {
    "layout": "PP-DocLayoutV3",
    "det": "PP-OCRv6_medium_det",
    "rec": "th_PP-OCRv5_mobile_rec",
    "table": "TableRecognitionPipelineV2",
    "siglip": "google/siglip-so400m-patch14-384",
}
DEFAULT_OUTPUT = Path(os.environ.get("KAGGLE_RUNTIME_DIR", "/kaggle/working/ocr_runtime")) / "all_models_result.json"


class TestFailure(RuntimeError):
    pass


def request(path, *, payload=None, token=None, timeout=900):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(urllib.request.Request(BASE_URL + path, data=body, headers=headers), timeout=timeout) as response:
            status = response.status
            raw = response.read()
    except urllib.error.HTTPError as error:
        status = error.code
        raw = error.read()
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    try:
        parsed = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        parsed = {"detail": "Non-JSON HTTP response"}
    return status, parsed, elapsed_ms


def image_url(image):
    import cv2

    ok, encoded = cv2.imencode(".png", image)
    if not ok:
        raise TestFailure("Unable to encode test image")
    return "data:image/png;base64," + base64.b64encode(encoded.tobytes()).decode("ascii")


def text_image(text, *, width=720, height=112):
    import cv2
    import numpy as np

    image = np.full((height, width, 3), 255, dtype=np.uint8)
    cv2.putText(image, text, (28, min(height - 25, 76)), cv2.FONT_HERSHEY_SIMPLEX, 1.7, (0, 0, 0), 3, cv2.LINE_AA)
    return image


def table_image():
    import cv2
    import numpy as np

    image = np.full((850, 1200, 3), 255, dtype=np.uint8)
    cv2.putText(image, "INVOICE ITEMS", (170, 95), cv2.FONT_HERSHEY_SIMPLEX, 1.8, (0, 0, 0), 4, cv2.LINE_AA)
    x_positions = (80, 540, 850, 1120)
    y_positions = (150, 275, 400, 525, 650, 775)
    for position in x_positions:
        cv2.line(image, (position, y_positions[0]), (position, y_positions[-1]), (0, 0, 0), 4)
    for position in y_positions:
        cv2.line(image, (x_positions[0], position), (x_positions[-1], position), (0, 0, 0), 4)
    rows = (("ITEM", "QTY", "PRICE"), ("PAPER", "2", "120"), ("PEN", "4", "40"), ("BOOK", "1", "250"), ("TOTAL", "7", "410"))
    for index, row in enumerate(rows):
        for column, value in enumerate(row):
            cv2.putText(image, value, (x_positions[column] + 24, y_positions[index] + 82), cv2.FONT_HERSHEY_SIMPLEX, 1.3, (0, 0, 0), 3, cv2.LINE_AA)
    return image


def layout_image():
    import cv2

    image = table_image()
    cv2.putText(image, "DOCUMENT REPORT", (170, 45), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 0, 0), 2, cv2.LINE_AA)
    return image


def rss_mb():
    runtime_root = DEFAULT_OUTPUT.parent
    pid_file = runtime_root / "api.pid"
    try:
        process_id = int(pid_file.read_text(encoding="utf-8").strip())
        status = Path(f"/proc/{process_id}/status").read_text(encoding="utf-8")
        for line in status.splitlines():
            if line.startswith("VmRSS:"):
                return round(int(line.split()[1]) / 1024, 2)
    except (OSError, ValueError):
        pass
    return None


def envelope(status, response, model):
    if status != 200:
        detail = response.get("detail", response) if isinstance(response, dict) else response
        raise TestFailure(f"HTTP {status}: {str(detail)[:350]}")
    if not isinstance(response, dict) or response.get("success") is not True or response.get("model") != MODEL_NAMES[model]:
        raise TestFailure("Missing success envelope or unexpected model name")
    result = response.get("result")
    timing = response.get("timing")
    if not isinstance(result, dict) or not isinstance(timing, dict):
        raise TestFailure("Result or timing is not an object")
    for key in ("model_load_ms", "inference_ms", "total_runtime_ms"):
        if not isinstance(timing.get(key), (int, float)) or timing[key] < 0:
            raise TestFailure(f"Invalid timing.{key}")
    return result


def paddle_raw(value):
    if not isinstance(value, dict):
        raise TestFailure("Paddle result is not an object")
    raw = value.get("res", value)
    if not isinstance(raw, dict):
        raise TestFailure("Paddle res is not an object")
    return raw


def validate_layout(result):
    raw = result.get("raw")
    items = raw.get("layout") if isinstance(raw, dict) else None
    if not isinstance(items, list) or len(items) != 1:
        raise TestFailure("Layout raw.layout must contain one page")
    page = paddle_raw(items[0])
    boxes = page.get("boxes")
    if not isinstance(boxes, list) or not boxes:
        raise TestFailure("Layout returned no boxes for the structured test page")
    for box in boxes:
        if not isinstance(box, dict) or not isinstance(box.get("label"), str) or not isinstance(box.get("score"), (int, float)) or not isinstance(box.get("coordinate"), list) or len(box["coordinate"]) != 4:
            raise TestFailure("Invalid layout label, score, or coordinates")
    return {"box_count": len(boxes)}


def validate_det(value):
    raw = paddle_raw(value)
    polygons = raw.get("dt_polys")
    scores = raw.get("dt_scores")
    if not isinstance(polygons, list) or not polygons or not isinstance(scores, list) or len(polygons) != len(scores):
        raise TestFailure("DET polygons/scores missing or mismatched")
    for polygon, score in zip(polygons, scores):
        if not isinstance(polygon, list) or len(polygon) < 4 or not all(isinstance(point, list) and len(point) == 2 for point in polygon):
            raise TestFailure("Invalid DET polygon coordinates")
        if not isinstance(score, (int, float)) or not math.isfinite(score):
            raise TestFailure("Invalid DET confidence score")
    return {"polygon_count": len(polygons)}


def validate_rec(value):
    raw = paddle_raw(value)
    text = raw.get("rec_text")
    score = raw.get("rec_score")
    if not isinstance(text, str) or not text.strip() or not isinstance(score, (int, float)) or not math.isfinite(score):
        raise TestFailure("REC text or score missing")
    return {"text": text, "score": score}


def validate_table(result):
    raw = paddle_raw(result.get("raw_output"))
    tables = raw.get("table_res_list")
    if not isinstance(tables, list) or not tables:
        raise TestFailure("Table output has no table_res_list")
    usable = [item for item in tables if isinstance(item, dict) and isinstance(item.get("cell_box_list"), list) and item["cell_box_list"] and isinstance(item.get("table_ocr_pred"), dict)]
    if not usable:
        raise TestFailure("Table has no cell_box_list with table_ocr_pred")
    return {"table_count": len(tables), "cell_count": len(usable[0]["cell_box_list"])}


def validate_logits(value, expected=2):
    logits = value.get("logits") if isinstance(value, dict) else None
    if not isinstance(logits, list) or len(logits) != expected or any(not isinstance(item, (int, float)) or not math.isfinite(item) for item in logits):
        raise TestFailure("SigLIP raw logits missing, invalid, or out of category order")
    if value.get("device") != "cpu":
        raise TestFailure("SigLIP device is not cpu")
    return logits


def batch_results(result, count):
    items = result.get("results")
    if not isinstance(items, list) or len(items) != count:
        raise TestFailure(f"Batch returned {len(items) if isinstance(items, list) else 'no'} results; expected {count}")
    return items


def call(path, payload, token, model, calls, timeout):
    status, response, wall_ms = request(path, payload=payload, token=token, timeout=timeout)
    result = envelope(status, response, model)
    calls.append({"path": path, "wall_ms": wall_ms, "timing": response["timing"]})
    return result


def test_layout(token, calls, timeout):
    result = call("/api/v1/document-layouts", {"image": image_url(layout_image()), "layout_only": True}, token, "layout", calls, timeout)
    return validate_layout(result)


def test_det(token, calls, timeout):
    first = image_url(text_image("INVOICE 123"))
    second = image_url(text_image("TOTAL 456", width=620))
    single_a = call("/api/v1/text-detections?version=v6", {"image": first}, token, "det", calls, timeout)
    single_b = call("/api/v1/text-detections?version=v6", {"image": second}, token, "det", calls, timeout)
    validate_det(single_a)
    validate_det(single_b)
    batch = call("/api/v1/text-detection-batches?version=v6", {"images": [first, second]}, token, "det", calls, timeout)
    items = batch_results(batch, 2)
    for item, single in zip(items, (single_a, single_b)):
        validate_det(item)
        if paddle_raw(item).get("dt_polys") != paddle_raw(single).get("dt_polys"):
            raise TestFailure("DET batch order/output differs from corresponding single result")
    return {"single_and_batch": True, "polygon_counts": [len(paddle_raw(item)["dt_polys"]) for item in items]}


def test_rec(token, calls, timeout):
    first = image_url(text_image("HELLO 123"))
    second = image_url(text_image("WORLD 456"))
    single_a = call("/api/v1/text-recognitions", {"image": first}, token, "rec", calls, timeout)
    single_b = call("/api/v1/text-recognitions", {"image": second}, token, "rec", calls, timeout)
    validate_rec(single_a)
    validate_rec(single_b)
    batch = call("/api/v1/text-recognition-batches", {"images": [first, second]}, token, "rec", calls, timeout)
    items = batch_results(batch, 2)
    for item, single in zip(items, (single_a, single_b)):
        validate_rec(item)
        if paddle_raw(item)["rec_text"] != paddle_raw(single)["rec_text"]:
            raise TestFailure("REC batch order/output differs from corresponding single result")
    return {"single_and_batch": True, "texts": [paddle_raw(item)["rec_text"] for item in items]}


def test_table(token, calls, timeout):
    result = call("/api/v1/table-model-results", {"image": image_url(table_image())}, token, "table", calls, timeout)
    return validate_table(result)


def test_siglip(token, calls, timeout):
    categories = [
        {"value": "document", "prompt": "A printed paper document with a table"},
        {"value": "photo", "prompt": "An outdoor photograph of a landscape"},
    ]
    first = image_url(table_image())
    second = image_url(text_image("PHOTO 123", width=720, height=384))
    single_a = call("/api/v1/image-verifications", {"image": first, "image_category": "document", "categories": categories}, token, "siglip", calls, timeout)
    single_b = call("/api/v1/image-verifications", {"image": second, "image_category": "photo", "categories": categories}, token, "siglip", calls, timeout)
    logits_a = validate_logits(single_a)
    logits_b = validate_logits(single_b)
    batch = call("/api/v1/image-classification-batches", {"images": [first, second], "categories": categories}, token, "siglip", calls, timeout)
    items = batch_results(batch, 2)
    for item, single_logits in zip(items, (logits_a, logits_b)):
        logits = validate_logits(item)
        if any(abs(actual - expected) > 1e-3 for actual, expected in zip(logits, single_logits)):
            raise TestFailure("SigLIP batch logits/order differ from corresponding single result")
    return {"single_and_batch": True, "category_order": [item["value"] for item in categories]}


TESTS = {"layout": test_layout, "det": test_det, "rec": test_rec, "table": test_table, "siglip": test_siglip}


def run_model(model, token, timeout):
    before = rss_mb()
    started = time.perf_counter()
    calls = []
    status = "PASS"
    error = None
    output = None
    try:
        output = TESTS[model](token, calls, timeout)
    except (TestFailure, OSError, ValueError, ImportError) as failure:
        status = "FAIL"
        error = f"{type(failure).__name__}: {failure}"[:500]
    after = rss_mb()
    timing = lambda key: round(sum(call["timing"][key] for call in calls), 2) if calls else None
    return {
        "model": model,
        "model_name": MODEL_NAMES[model],
        "status": status,
        "model_load_ms": timing("model_load_ms"),
        "inference_ms": timing("inference_ms"),
        "total_runtime_ms": timing("total_runtime_ms"),
        "wall_ms": round((time.perf_counter() - started) * 1000, 2),
        "ram_before_mb": before,
        "ram_after_mb": after,
        "ram_delta_mb": round(after - before, 2) if before is not None and after is not None else None,
        "calls": calls,
        "output": output,
        "error": error,
    }


def print_summary(results):
    print(f"{'Model':<39} {'Status':<6} {'Load ms':>10} {'Infer ms':>10} {'Total ms':>10} {'RAM MB':>18}  Error")
    for row in results:
        number = lambda value: f"{value:.2f}" if isinstance(value, (int, float)) else "-"
        ram = f"{number(row['ram_before_mb'])}->{number(row['ram_after_mb'])}"
        print(f"{row['model_name']:<39} {row['status']:<6} {number(row['model_load_ms']):>10} {number(row['inference_ms']):>10} {number(row['total_runtime_ms']):>10} {ram:>18}  {row['error'] or ''}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", choices=(*TESTS, "all"), default="all")
    parser.add_argument("--timeout", type=int, default=900, help="Per-request timeout in seconds")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    arguments = parser.parse_args()
    selected = list(TESTS) if arguments.model == "all" else [arguments.model]
    token = load_secrets(("MODEL_GATEWAY_API_KEY",)).get("MODEL_GATEWAY_API_KEY")
    reason = None
    if not token:
        reason = "MODEL_GATEWAY_API_KEY is unavailable"
    else:
        try:
            status, health, _ = request("/health", timeout=10)
            if status != 200 or health.get("status") != "ok" or health.get("device") != "cpu" or health.get("authentication_configured") is not True:
                reason = "API health/device/authentication check failed"
            else:
                unauthorized, _, _ = request("/api/v1/text-recognitions", payload={"image": "invalid"}, timeout=10)
                if unauthorized != 401:
                    reason = f"Authentication check failed: expected HTTP 401, got {unauthorized}"
        except (OSError, ValueError) as failure:
            reason = f"API unavailable: {type(failure).__name__}: {failure}"
    if reason:
        results = [{"model": model, "model_name": MODEL_NAMES[model], "status": "SKIP", "model_load_ms": None, "inference_ms": None, "total_runtime_ms": None, "ram_before_mb": None, "ram_after_mb": None, "ram_delta_mb": None, "calls": [], "output": None, "error": reason} for model in selected]
    else:
        results = [run_model(model, token, arguments.timeout) for model in selected]
    print_summary(results)
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps({"timestamp_utc": datetime.now(timezone.utc).isoformat(), "base_url": BASE_URL, "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"JSON: {arguments.output}")
    return 0 if all(row["status"] == "PASS" for row in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
