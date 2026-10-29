"""Run local browser user flow against existing services and export timing to Excel."""

import argparse
import json
import os
import math
import tempfile
import time
from uuid import uuid4
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent
MODES = {"standard", "strict", "text_only", "text_image", "image_only", "layout_only"}
EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".pdf"}
UPLOAD_INPUT = 'input[type="file"][accept="image/*,application/pdf"]'
LEGACY_METRICS = (
    "uploadJobMs", "jobPollingMs", "detectionJobMs", "end_to_end_ms",
    "backend_runtime_ms", "backend_to_model_ms", "layout_runtime_ms",
    "text_detection_runtime_ms", "text_recognition_runtime_ms", "image_siglip_runtime_ms",
    "table_runtime_ms", "layout_wait_ms", "text_detection_wait_ms",
    "text_recognition_wait_ms", "image_siglip_wait_ms", "table_wait_ms",
    "model_wall_clock_ms", "model_wait_union_ms", "total_runtime_ms",
    "frontend_to_backend_ms", "backend_to_frontend_ms", "frontend_render_ms",
)
FRONTEND_METRICS = ("frontend_response_json_ms", "frontend_response_processing_ms", "frontend_result_preparation_ms",
                    "frontend_state_to_commit_ms", "frontend_response_to_commit_ms", "frontend_flow_complete_ms",
                    "frontend_result_visible_ms", "frontend_preview_ready_ms", "frontend_ui_ready_ms", "frontend_commit_end_to_end_ms")
METRICS = (*LEGACY_METRICS, *FRONTEND_METRICS)
HEADERS = ("file", "run_number", "runs_per_file", "expected_mode", "actual_mode", "status", "matched", "query_id", "ocr_status", "ocr_wall_ms", "error", "model_intervals_overlap", "model_call_count", "model_timing_missing_reason", *LEGACY_METRICS, "timing_missing_reasons", "job_id", "error_type", "job_status", "capture_errors", *FRONTEND_METRICS,
           "frontend_visibility_status", "frontend_visibility_reason", "frontend_timing_kind", "frontend_run_token", "benchmark_run_token")
CALL_KEYS = ("call_id", "kind", "http_request_ms", "model_load_ms", "queue_wait_ms",
             "inference_lock_wait_ms", "inference_ms", "serialization_ms", "service_total_runtime_ms",
             "runs_per_file", "endpoint", "model_call_start_monotonic", "model_call_end_monotonic", "model_timings", "conflicting_record")
MODEL_CALL_HEADERS = ("file", "run_number", "actual_mode", *CALL_KEYS)
UNMEASURABLE = {
    "frontend_to_backend_ms": "one_way_network_not_measurable",
    "backend_to_frontend_ms": "one_way_network_not_measurable",
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
CAPTURE_FETCH = r"""(() => {
  if (window.__ocrBenchmarkFetchInstalled) return;
  window.__ocrBenchmarkFetchInstalled = true;
  const original = window.fetch.bind(window);
  window.fetch = async (input, options) => {
    const state = window.__ocrBenchmarkRun;
    const url = new URL(typeof input === 'string' || input instanceof URL ? input : input.url, location.href);
    const method = (options?.method || input?.method || 'GET').toUpperCase();
    const post = method === 'POST' && url.pathname === '/api/templates/detect-dev';
    const poll = method === 'GET' && url.pathname.match(/^\/api\/templates\/detect-dev\/jobs\/([^/]+)$/);
    if (!state || (!post && !poll)) return original(input, options);
    state.pending++;
    if (post && ++state.posts > 1) state.errors.push('Multiple detection submissions in one run');
    const finish = () => { state.pending--; };
    try {
      const response = await original(input, options);
      try {
        const copy = response.clone();
        Promise.resolve().then(() => copy.json()).then(body => {
          if (!response.ok || !body || typeof body.data !== 'object' || !body.data) {
            throw new Error('Invalid detection response');
          }
          const data = body.data;
          if (post) {
            if (typeof data.job_id !== 'string' || !data.job_id) throw new Error('Missing submission job_id');
            state.job_id = data.job_id;
          } else {
            const jobId = decodeURIComponent(poll[1]);
            if (data.job_id !== jobId) throw new Error('Polling job_id mismatch');
            if (!['processing', 'completed', 'failed'].includes(data.status)) throw new Error('Invalid job state');
            const previous = state.jobs[jobId];
            if (!previous || !['completed', 'failed'].includes(previous.status)) state.jobs[jobId] = data;
          }
        }).catch(() => state.errors.push('Detection response capture/JSON failed')).finally(finish);
      } catch {
        state.errors.push('Detection response clone failed');
        finish();
      }
      return response;
    } catch (error) {
      state.errors.push('Detection fetch failed');
      finish();
      throw error;
    }
  };
})();"""

RUN_SNAPSHOT = """() => {
  const state = window.__ocrBenchmarkRun;
  return state ? {token: state.token, job_id: state.job_id, pending: state.pending,
    posts: state.posts, errors: state.errors, job: state.jobs[state.job_id] || null,
    tables: window.__ocrBenchmarkTables || []} : null;
}"""


class CaptureFailure(RuntimeError):
    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind


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
    candidate = candidate if isinstance(candidate, dict) else {}
    debug = debug if isinstance(debug, dict) else {}
    return (candidate.get("verification_strategy") or candidate.get("verificationStrategy")
            or debug.get("verification_strategy") or result.get("verification_strategy"))


def valid_duration(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0 else None


def response_timing(result):
    debug = result.get("debug")
    timing = debug.get("timing") if isinstance(debug, dict) else {}
    timing = timing if isinstance(timing, dict) else {}
    benchmark = timing.get("benchmark")
    return {**(benchmark if isinstance(benchmark, dict) else {}), **timing}


def timing_values(tables, result):
    values = {}
    for rows in tables:
        for row in rows:
            key = row.get("metric") or row.get("stage")
            if key in METRICS:
                values[key] = row.get("valueMs", row.get("elapsedMs"))
    timing = response_timing(result)
    browser_keys = {"uploadJobMs", "jobPollingMs", "detectionJobMs", "end_to_end_ms", "frontend_render_ms", *FRONTEND_METRICS}
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
    try:
        try:
            workbook.save(path)
        except PermissionError:
            alternative = path.with_name(f"{path.stem}_recovery_{uuid4().hex[:8]}{path.suffix}")
            workbook.save(alternative)
            print(f"Excel target is locked or not writable: {path}. Saved to: {alternative}")
            path = alternative
    finally:
        workbook.close()
    return path


def save_results(arguments, rows):
    try:
        saved = save_excel(arguments.output, rows)
        if saved is not None:
            arguments.output = saved
        return True
    except OSError as error:
        arguments.output_failed = True
        print(f"Excel save failed ({type(error).__name__}). Benchmark stopped; close Excel and check output permissions.")
        recovery = Path(tempfile.gettempdir()) / f"ocr_benchmark_recovery_{uuid4().hex}.json"
        try:
            recovery.write_text(json.dumps(rows, ensure_ascii=False, default=str), encoding="utf-8")
            print(f"Completed results recovered as JSON: {recovery}")
        except OSError:
            print("JSON recovery also failed. Completed results could not be saved.")
        return False


def login_user(page, frontend_url):
    page.add_init_script(CAPTURE_TABLES)
    page.add_init_script(CAPTURE_FETCH)
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
    token = uuid4().hex
    snapshot = None
    result = {}
    tables = []
    row["_ready_for_next"] = False
    try:
        page.evaluate(CAPTURE_FETCH)
        page.evaluate("token => { window.__ocrBenchmarkTables = []; window.__ocrBenchmarkRun = {token, pending: 0, posts: 0, job_id: null, jobs: {}, errors: []}; }", token)
        page.locator(UPLOAD_INPUT).set_input_files(str(path), timeout=timeout_ms)
        deadline = time.monotonic() + timeout_ms / 1000
        page.get_by_role("button", name="ยืนยันเอกสารและค้นหา Template").click(timeout=timeout_ms)
        while True:
            snapshot = page.evaluate(RUN_SNAPSHOT)
            if not isinstance(snapshot, dict) or snapshot.get("token") != token:
                raise CaptureFailure("RESPONSE_CAPTURE_ERROR", "Run capture was lost or replaced (possible navigation)")
            tables = snapshot.get("tables") or []
            job = snapshot.get("job") or {}
            row.update(job_id=snapshot.get("job_id"), job_status=job.get("status"), capture_errors=snapshot.get("errors") or [])
            terminal = job.get("status") in ("completed", "failed")
            frontend = {item.get("metric"): item.get("value", item.get("valueMs")) for table in tables for item in table if item.get("metric")}
            frontend_status = frontend.get("frontend_visibility_status")
            for key in ("frontend_visibility_status", "frontend_visibility_reason", "frontend_timing_kind", "frontend_run_token", "benchmark_run_token"):
                row[key] = frontend.get(key)
            if frontend_status is not None and row.get("benchmark_run_token") != token:
                raise CaptureFailure("RESPONSE_CAPTURE_ERROR", "Frontend visibility belongs to another benchmark run")
            frontend_done = "end_to_end_ms" in frontend and frontend_status in ("READY", "PREVIEW_ERROR", "VISIBILITY_TIMEOUT", "VISIBILITY_ERROR")
            if job.get("status") == "completed":
                result = job.get("result") if isinstance(job.get("result"), dict) else {}
            drained = snapshot.get("pending") == 0
            if terminal and drained and frontend_done:
                if snapshot.get("posts") != 1:
                    raise CaptureFailure("RESPONSE_CAPTURE_ERROR", "Ambiguous detection submission count")
                row["_ready_for_next"] = True
                if job["status"] == "failed":
                    raise CaptureFailure("DETECTION_FAILED", str(job.get("error") or "Detection job failed"))
                if frontend_status != "READY":
                    raise CaptureFailure(frontend_status, str(frontend.get("frontend_visibility_reason") or "Result visibility not ready"))
                break
            if not terminal and drained and snapshot.get("errors") and not snapshot.get("job_id"):
                raise CaptureFailure("RESPONSE_CAPTURE_ERROR", "; ".join(snapshot["errors"]))
            if time.monotonic() >= deadline:
                kind = "BENCHMARK_ERROR" if terminal else "RESPONSE_CAPTURE_ERROR" if snapshot.get("errors") else "POLLING_TIMEOUT"
                if terminal and not frontend_done:
                    kind = "VISIBILITY_TIMEOUT"
                if snapshot.get("posts") == 0:
                    kind = "BENCHMARK_ERROR"
                if job.get("status") == "failed":
                    kind = "DETECTION_FAILED"
                raise CaptureFailure(kind, "Timed out waiting for terminal job, drained capture and frontend completion")
            page.wait_for_timeout(min(100, max(1, (deadline - time.monotonic()) * 1000)))
        if not result:
            raise CaptureFailure("RESPONSE_CAPTURE_ERROR", "Completed job lacks detection result")
        if not isinstance(result.get("matched"), bool):
            raise CaptureFailure("RESPONSE_CAPTURE_ERROR", "Detection response lacks boolean matched result")
        if any(item.get("status") == "failed" for table in tables for item in table):
            raise CaptureFailure("BENCHMARK_ERROR", "Frontend reported failed Template detection/preparation")
        if expected_mode and actual_mode(result) != expected_mode:
            row["status"] = "SKIP"
            row["error"] = f"Active mode is {actual_mode(result) or 'unknown'}, expected {expected_mode}; global setting was not changed"
        else:
            row["status"] = "PASS"
    except Exception as error:
        row["status"] = "FAIL"
        row["error_type"] = error.kind if isinstance(error, CaptureFailure) else "BENCHMARK_ERROR"
        row["error"] = str(error)[:500]
    finally:
        row.update(timing_values(tables, result))
        timing = response_timing(result)
        row.update({key: timing.get(key) for key in ("model_intervals_overlap", "model_call_count", "model_timing_missing_reason")})
        reasons = dict(timing.get("timing_missing_reasons") or {})
        for table in tables:
            for item in table:
                if item.get("metric") and item.get("reason"):
                    reasons[item["metric"]] = item["reason"]
        reasons.update(UNMEASURABLE)
        if row["end_to_end_ms"] is not None:
            reasons.pop("end_to_end_ms", None)
        row["timing_missing_reasons"] = {key: reasons.get(key, "measurement_missing_or_invalid") for key in METRICS if row[key] is None}
        row["_model_calls"] = timing.get("model_calls") or []
        row["actual_mode"] = actual_mode(result)
        row["matched"] = result.get("matched")
        row["query_id"] = result.get("query_id") or result.get("queryId")
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
            if not save_results(arguments, rows):
                return False
            outcome = "FOUND" if row.get("matched") is True else "NOT_FOUND" if row.get("matched") is False else "UNKNOWN"
            print(f"{path.name} [{run_number}/{arguments.repeats}]: {row['status']} mode={row.get('actual_mode') or 'unknown'} Template={outcome}")
            if row.get("_ready_for_next") is False:
                print("Benchmark stopped safely: run capture is incomplete; no New Document or next upload.")
                return False
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
        if rows and not getattr(arguments, "output_failed", False):
            if save_results(arguments, rows):
                print(f"Excel: {arguments.output}")
            else:
                completed = False
    return 1 if not completed or any(row["status"] == "FAIL" for row in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
