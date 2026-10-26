"""Run local browser user flow against existing services and export timing to Excel."""

import argparse
import json
import os
import math
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent
MODES = {"standard", "strict", "text_only", "text_image", "image_only", "layout_only"}
EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".pdf"}
UPLOAD_INPUT = 'input[type="file"][accept="image/*,application/pdf"]'
METRICS = (
    "uploadJobMs", "jobPollingMs", "detectionJobMs", "end_to_end_ms",
    "backend_runtime_ms", "backend_to_model_ms", "layout_runtime_ms",
    "text_detection_runtime_ms", "text_recognition_runtime_ms", "image_siglip_runtime_ms",
    "table_runtime_ms", "layout_wait_ms", "text_detection_wait_ms",
    "text_recognition_wait_ms", "image_siglip_wait_ms", "table_wait_ms",
    "model_wall_clock_ms", "model_wait_union_ms", "total_runtime_ms",
    "frontend_to_backend_ms", "backend_to_frontend_ms", "frontend_render_ms",
)
HEADERS = ("file", "run_number", "runs_per_file", "expected_mode", "actual_mode", "status", "matched", "query_id", "ocr_status", "ocr_wall_ms", "error", "model_intervals_overlap", "model_call_count", "model_timing_missing_reason", *METRICS, "timing_missing_reasons")
CALL_KEYS = ("call_id", "kind", "http_request_ms", "model_load_ms", "queue_wait_ms",
             "inference_lock_wait_ms", "inference_ms", "serialization_ms", "service_total_runtime_ms",
             "runs_per_file", "endpoint", "model_call_start_monotonic", "model_call_end_monotonic", "model_timings", "conflicting_record")
MODEL_CALL_HEADERS = ("file", "run_number", "actual_mode", *CALL_KEYS)
UNMEASURABLE = {
    "frontend_to_backend_ms": "one_way_network_not_measurable",
    "backend_to_frontend_ms": "one_way_network_not_measurable",
    "frontend_render_ms": "browser_paint_not_instrumented",
    "backend_to_model_ms": "http_duration_is_not_network_only",
}
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


def valid_duration(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0 else None


def response_timing(result):
    timing = (result.get("debug") or {}).get("timing") or {}
    return {**(timing.get("benchmark") or {}), **timing}


def timing_values(tables, result):
    values = {}
    for rows in tables:
        for row in rows:
            key = row.get("metric") or row.get("stage")
            if key in METRICS:
                values[key] = row.get("valueMs", row.get("elapsedMs"))
    timing = response_timing(result)
    browser_keys = {"uploadJobMs", "jobPollingMs", "detectionJobMs", "end_to_end_ms"}
    for key in METRICS:
        if key not in browser_keys and key in timing:
            values[key] = timing[key]
    for key, alias in (("image_siglip_runtime_ms", "siglip_inference_ms"),
                       ("text_detection_runtime_ms", "text_det_inference_ms"),
                       ("text_recognition_runtime_ms", "text_rec_inference_ms"),
                       ("total_runtime_ms", "total_detection_ms")):
        if key not in timing and alias in timing:
            values[key] = timing[alias]
    values.update({key: None for key in UNMEASURABLE})
    return {key: valid_duration(values.get(key)) for key in METRICS}


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
    calls_sheet.append(MODEL_CALL_HEADERS)
    for row in rows:
        for call in row.get("_model_calls", []):
            record = {**call, **{key: row.get(key) for key in ("file", "run_number", "actual_mode", "runs_per_file")}}
            calls_sheet.append([excel_value(valid_duration(record.get(key)) if key.endswith("_ms") else record.get(key)) for key in MODEL_CALL_HEADERS])
    calls_sheet.freeze_panes = "A2"
    calls_sheet.auto_filter.ref = calls_sheet.dimensions
    workbook.save(path)


def login_user(page, frontend_url):
    page.add_init_script(CAPTURE_TABLES)
    try:
        page.goto(frontend_url.rstrip("/") + "/login?next=/", wait_until="domcontentloaded", timeout=30000)
        page.locator('input[type="email"]').fill(os.environ.get("BENCHMARK_EMAIL", "user@ocr.com"))
        page.locator('input[type="password"]').fill(os.environ.get("BENCHMARK_PASSWORD", "user123"))
        page.get_by_role("button", name="เข้าสู่ระบบ", exact=True).click()
        page.locator(UPLOAD_INPUT).wait_for(timeout=120000)
    except Exception:
        raise RuntimeError("Automatic login failed. Check the local Frontend and benchmark account credentials.") from None


def reset_document(page):
    page.wait_for_timeout(2000)
    page.get_by_role("button", name="เอกสารใหม่", exact=True).click(timeout=30000)
    page.locator(UPLOAD_INPUT).wait_for(timeout=30000)


def run_case(page, path, expected_mode, timeout_ms):
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

    try:
        page.on("response", on_response)
        page.evaluate("window.__ocrBenchmarkTables = []")
        page.locator(UPLOAD_INPUT).set_input_files(str(path), timeout=timeout_ms)
        page.get_by_role("button", name="ยืนยันเอกสารและค้นหา Template").click(timeout=timeout_ms)
        page.wait_for_function(
            "window.__ocrBenchmarkTables.some(rows => rows.some(row => row.metric === 'end_to_end_ms'))",
            timeout=timeout_ms,
        )
        result = capture["result"] or {}
        tables = page.evaluate("window.__ocrBenchmarkTables")
        row.update(timing_values(tables, result))
        timing = response_timing(result)
        row.update({key: timing.get(key) for key in ("model_intervals_overlap", "model_call_count", "model_timing_missing_reason")})
        reasons = dict(timing.get("timing_missing_reasons") or {})
        reasons.update(UNMEASURABLE)
        reasons.pop("end_to_end_ms", None)
        row["timing_missing_reasons"] = {key: reasons.get(key, "measurement_missing_or_invalid") for key in METRICS if row[key] is None}
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
    except Exception as error:
        row["status"] = "FAIL"
        row["error"] = str(error)[:500]
    finally:
        page.remove_listener("response", on_response)
    return row


def run_cases(page, arguments, cases, rows, browser_state):
    for path, mode in cases:
        for run_number in range(1, arguments.repeats + 1):
            if browser_state["closed"]:
                print("Benchmark stopped: browser was closed. Completed results are saved; rerun the command to start again.")
                return False
            row = run_case(page, path, mode, arguments.timeout_seconds * 1000)
            row.update(run_number=run_number, runs_per_file=arguments.repeats)
            rows.append(row)
            save_excel(arguments.output, rows)
            outcome = "FOUND" if row.get("matched") is True else "NOT_FOUND" if row.get("matched") is False else "UNKNOWN"
            print(f"{path.name} [{run_number}/{arguments.repeats}]: {row['status']} mode={row.get('actual_mode') or 'unknown'} Template={outcome}")
            if browser_state["closed"]:
                print("Benchmark stopped: browser was closed. Completed results are saved; rerun the command to start again.")
                return False
            try:
                reset_document(page)
            except Exception:
                print("Benchmark stopped: unable to reset with New Document. Completed results are saved.")
                return False
    return True


def main():
    parser = argparse.ArgumentParser(description="Benchmark real local Frontend user flow without changing application code")
    parser.add_argument("--input", type=Path, default=ROOT / "input")
    parser.add_argument("--output", type=Path, default=ROOT / "output" / f"user_flow_{datetime.now():%Y%m%d_%H%M%S}.xlsx")
    parser.add_argument("--frontend", default="http://localhost:3000")
    parser.add_argument("--expected-mode", choices=sorted(MODES))
    parser.add_argument("--timeout-seconds", type=int, default=1200)
    parser.add_argument("--no-ocr", action="store_true", help="Compatibility option; the benchmark always stops after Template detection")
    parser.add_argument("--repeats", type=int, default=2, help="Runs per file (default: 2)")
    arguments = parser.parse_args()
    if arguments.repeats < 1:
        parser.error("--repeats must be at least 1")
    cases = input_cases(arguments.input, arguments.expected_mode)
    if not cases:
        parser.error(f"No supported images/PDFs in {arguments.input}")
    if urlparse(arguments.frontend).hostname not in {"localhost", "127.0.0.1"}:
        parser.error("Frontend must be local; use http://localhost:3000")
    from playwright.sync_api import Error, sync_playwright

    rows = []
    completed = False
    try:
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(str(ROOT / "browser-profile"), headless=False)
            browser_state = {"closed": False}
            context.on("close", lambda closed_context: browser_state.update(closed=True))
            try:
                login_page = context.pages[0] if context.pages else context.new_page()
                login_user(login_page, arguments.frontend)
                print("Logged in. Measuring Template detection only; each result waits 2 seconds before New Document.")
                completed = run_cases(login_page, arguments, cases, rows, browser_state)
            finally:
                if not browser_state["closed"]:
                    context.close()
    except (Error, RuntimeError) as error:
        completed = False
        print(f"Browser failed: {str(error)[:500]}")
    finally:
        if rows:
            save_excel(arguments.output, rows)
            print(f"Excel: {arguments.output}")
    return 1 if not completed or any(row["status"] == "FAIL" for row in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
