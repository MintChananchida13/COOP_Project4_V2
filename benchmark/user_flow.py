"""Run local browser user flow against existing services and export timing to Excel."""

import argparse
import json
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent
MODES = {"standard", "strict", "text_only", "text_image", "image_only", "layout_only"}
EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".pdf"}
METRICS = (
    "uploadJobMs", "jobPollingMs", "detectionJobMs", "end_to_end_ms",
    "backend_runtime_ms", "backend_to_model_ms", "layout_runtime_ms",
    "text_detection_runtime_ms", "text_recognition_runtime_ms", "image_siglip_runtime_ms",
    "table_runtime_ms", "layout_wait_ms", "text_detection_wait_ms",
    "text_recognition_wait_ms", "image_siglip_wait_ms", "table_wait_ms",
    "model_wall_clock_ms", "model_wait_union_ms", "total_runtime_ms",
    "frontend_to_backend_ms", "backend_to_frontend_ms", "frontend_render_ms",
)
HEADERS = ("file", "expected_mode", "actual_mode", "status", "matched", "query_id", "ocr_status", "ocr_wall_ms", "error", "model_intervals_overlap", "model_call_count", "model_timing_missing_reason", *METRICS)
CAPTURE_TABLES = """(() => {
  window.__ocrBenchmarkTables = [];
  const original = console.table.bind(console);
  console.table = (...args) => {
    const rows = args[0];
    if (Array.isArray(rows) && rows.some(row => row && (row.metric === 'end_to_end_ms' || row.stage === 'detectionJobMs'))) {
      window.__ocrBenchmarkTables.push(rows);
    }
    return original(...args);
  };
})();"""


def input_cases(directory, expected_mode):
    cases = []
    for path in sorted(directory.rglob("*")) if directory.exists() else []:
        if not path.is_file() or path.suffix.lower() not in EXTENSIONS:
            continue
        folder_mode = path.parent.name if path.parent != directory else None
        if folder_mode and folder_mode not in MODES:
            raise ValueError(f"Unsupported mode folder: {path.parent}")
        if expected_mode and folder_mode and expected_mode != folder_mode:
            raise ValueError(f"Mode conflict for {path}: {folder_mode} vs {expected_mode}")
        cases.append((path, folder_mode or expected_mode))
    return cases


def detection_result(body):
    if not isinstance(body, dict):
        return None
    data = body.get("data")
    if not isinstance(data, dict):
        return None
    if data.get("status") == "completed":
        return data.get("result") if isinstance(data.get("result"), dict) else None
    if "matched" in data or "best_candidate" in data:
        return data
    return None


def actual_mode(result):
    candidate = result.get("best_candidate") or result.get("bestCandidate") or {}
    debug = result.get("debug") or {}
    return (candidate.get("verification_strategy") or candidate.get("verificationStrategy")
            or debug.get("verification_strategy") or result.get("verification_strategy"))


def timing_values(tables, result):
    values = {}
    for rows in tables:
        for row in rows:
            key = row.get("metric") or row.get("stage")
            if key in METRICS:
                values[key] = row.get("valueMs", row.get("elapsedMs"))
    timing = (result.get("debug") or {}).get("timing") or {}
    for key in METRICS:
        if key not in values and key in timing:
            values[key] = timing[key]
    if "total_runtime_ms" not in values:
        values["total_runtime_ms"] = timing.get("total_detection_ms")
    return {key: values.get(key) for key in METRICS}


def excel_value(value):
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False)
    if isinstance(value, str) and value[:1] in {"=", "+", "-", "@"}:
        return "'" + value
    return value


def save_excel(path, rows):
    from openpyxl import Workbook

    path.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "User Flow Timing"
    sheet.append(HEADERS)
    sheet.freeze_panes = "A2"
    for row in rows:
        sheet.append([excel_value(row.get(header)) for header in HEADERS])
    sheet.auto_filter.ref = sheet.dimensions
    calls_sheet = workbook.create_sheet("Model Calls")
    call_keys = ("call_id", "kind", "http_request_ms", "model_load_ms", "queue_wait_ms",
                 "inference_lock_wait_ms", "inference_ms", "serialization_ms", "service_total_runtime_ms")
    calls_sheet.append(("file", "actual_mode", *call_keys))
    for row in rows:
        for call in row.get("_model_calls", []):
            calls_sheet.append([excel_value(row.get("file")), excel_value(row.get("actual_mode")),
                                *(excel_value(call.get(key)) for key in call_keys)])
    calls_sheet.freeze_panes = "A2"
    workbook.save(path)


def run_case(context, frontend_url, path, expected_mode, timeout_ms, run_ocr):
    page = context.new_page()
    page.add_init_script(CAPTURE_TABLES)
    row = {"file": str(path), "expected_mode": expected_mode, "status": "FAIL", "ocr_status": "NOT_RUN"}
    capture = {"result": None, "error": None}

    def on_response(response):
        route = urlparse(response.url).path
        if "/api/templates/detect-dev" not in route or ("/jobs/" not in route and response.request.method != "POST"):
            return
        try:
            body = response.json()
            result = detection_result(body)
            if result is not None:
                capture["result"] = result
            elif isinstance(body, dict) and isinstance(body.get("data"), dict) and body["data"].get("status") == "failed":
                capture["error"] = str(body["data"].get("error") or "Detection job failed")
        except (ValueError, TypeError):
            return

    page.on("response", on_response)
    try:
        page.goto(frontend_url, wait_until="domcontentloaded", timeout=30000)
        page.locator('input[type="file"][accept="image/*,application/pdf"]').set_input_files(str(path), timeout=timeout_ms)
        page.get_by_role("button", name="ยืนยันเอกสารและค้นหา Template").click(timeout=timeout_ms)
        page.wait_for_function(
            "window.__ocrBenchmarkTables.some(rows => rows.some(row => row.metric === 'end_to_end_ms'))",
            timeout=timeout_ms,
        )
        result = capture["result"] or {}
        tables = page.evaluate("window.__ocrBenchmarkTables")
        row.update(timing_values(tables, result))
        timing = (result.get("debug") or {}).get("timing") or {}
        row.update({key: timing.get(key) for key in ("model_intervals_overlap", "model_call_count", "model_timing_missing_reason")})
        row["_model_calls"] = timing.get("model_calls") or []
        row["actual_mode"] = actual_mode(result)
        row["matched"] = result.get("matched")
        row["query_id"] = result.get("query_id") or result.get("queryId")
        if capture["error"]:
            raise RuntimeError(capture["error"])
        if not result:
            raise RuntimeError("No completed detection response captured")
        if any(item.get("status") == "failed" for table in tables for item in table):
            raise RuntimeError("Frontend reported failed Template detection/preparation")
        if expected_mode and row["actual_mode"] != expected_mode:
            row["status"] = "SKIP"
            row["error"] = f"Active mode is {row['actual_mode'] or 'unknown'}, expected {expected_mode}; global setting was not changed"
            return row
        row["status"] = "PASS" if isinstance(row["matched"], bool) else "FAIL"
        if row["status"] != "PASS":
            row["error"] = "Detection response lacks matched result"
        if run_ocr and row["matched"] is True:
            button = page.get_by_role("button", name="อ่านข้อมูลที่เลือก")
            if button.count() and button.is_enabled():
                row["ocr_status"] = "RUNNING"
                started = time.perf_counter()
                button.click()
                page.get_by_text("ตรวจสอบและแก้ไขผล OCR").first.wait_for(timeout=timeout_ms)
                row["ocr_wall_ms"] = round((time.perf_counter() - started) * 1000, 2)
                row["ocr_status"] = "PASS"
            else:
                row["ocr_status"] = "SKIP_NO_ENABLED_ROI"
        elif run_ocr:
            row["ocr_status"] = "SKIP_NO_MATCH"
    except Exception as error:
        row["status"] = "FAIL"
        if row["ocr_status"] == "RUNNING":
            row["ocr_status"] = "FAIL"
        row["error"] = str(error)[:500]
    finally:
        page.close()
    return row


def main():
    parser = argparse.ArgumentParser(description="Benchmark real local Frontend user flow without changing application code")
    parser.add_argument("--input", type=Path, default=ROOT / "input")
    parser.add_argument("--output", type=Path, default=ROOT / "output" / f"user_flow_{datetime.now():%Y%m%d_%H%M%S}.xlsx")
    parser.add_argument("--frontend", default="http://localhost:3000")
    parser.add_argument("--expected-mode", choices=sorted(MODES))
    parser.add_argument("--timeout-seconds", type=int, default=1200)
    parser.add_argument("--no-ocr", action="store_true")
    arguments = parser.parse_args()
    cases = input_cases(arguments.input, arguments.expected_mode)
    if not cases:
        parser.error(f"No supported images/PDFs in {arguments.input}")
    if urlparse(arguments.frontend).hostname not in {"localhost", "127.0.0.1"}:
        parser.error("Frontend must be local; use http://localhost:3000")
    from playwright.sync_api import sync_playwright

    rows = []
    try:
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(str(ROOT / "browser-profile"), headless=False)
            try:
                login_page = context.pages[0] if context.pages else context.new_page()
                login_page.goto(arguments.frontend, wait_until="domcontentloaded")
                print("If login is shown, sign in manually in this dedicated browser window; waiting for upload input...")
                login_page.locator('input[type="file"][accept="image/*,application/pdf"]').wait_for(timeout=120000)
                login_page.close()
                for path, mode in cases:
                    row = run_case(context, arguments.frontend, path, mode, arguments.timeout_seconds * 1000, not arguments.no_ocr)
                    rows.append(row)
                    save_excel(arguments.output, rows)
                    print(f"{path.name}: {row['status']} mode={row.get('actual_mode') or 'unknown'} OCR={row['ocr_status']}")
            finally:
                context.close()
    finally:
        if rows:
            save_excel(arguments.output, rows)
            print(f"Excel: {arguments.output}")
    return 1 if any(row["status"] == "FAIL" for row in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
