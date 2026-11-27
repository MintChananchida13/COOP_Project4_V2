"""Benchmark Template Search and selected-ROI OCR through the real user interface."""

import argparse
import json
import os
import math
import re
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
                    "frontend_result_visible_ms", "frontend_preview_ready_ms", "frontend_ui_ready_ms", "frontend_commit_end_to_end_ms",
                    "frontend_observer_to_first_ui_ms", "frontend_preview_decode_wait_ms", "frontend_observer_max_frame_gap_ms")
SEARCH_METRICS = ("initial_response_headers_ms", "initial_server_maintenance_ms", "initial_server_maintenance_read_ms",
                  "initial_server_pending_settings_ms", "initial_server_asgi_to_route_ms", "initial_server_asgi_to_response_headers_ms", "initial_server_request_body_ms",
                  "initial_server_job_setup_ms", "initial_server_total_ms", "frontend_preparation_ms", "request_network_ms", "request_round_trip_ms", "template_search_ms", "model_queue_wait_ms", "search_model_queue_wait_ms",
                  "backend_search_ms", "backend_post_search_ms", "backend_total_active_ms",
                  "result_processing_ms", "polling_wait_ms", "result_fetch_ms", "result_ready_ms",
                  "layout_queue_wait_ms", "layout_inference_ms", "layout_total_ms",
                  "text_detection_queue_wait_ms", "text_detection_inference_ms", "text_detection_total_ms",
                  "text_recognition_queue_wait_ms", "text_recognition_inference_ms", "text_recognition_total_ms",
                  "siglip_queue_wait_ms", "siglip_inference_ms", "siglip_total_ms")
METRICS = (*LEGACY_METRICS, *FRONTEND_METRICS, *SEARCH_METRICS, "stage_count", "roi_crop_count")
UNMEASURABLE = {
    "request_network_ms": "one_way_network_cannot_be_isolated_request_round_trip_includes_server_work",
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
    const prefix = url.pathname.startsWith('/backend/') ? '/backend' : '';
    const path = url.pathname.slice(prefix.length);
    const backendBase = url.origin + prefix;
    const post = method === 'POST' && path === '/api/templates/detect-dev';
    const poll = method === 'GET' && path.match(/^\/api\/templates\/detect-dev\/jobs\/([^/]+)$/);
    if (state && post) state.backend_origin = backendBase;
    const postSearch = state && state.postSearchStarted && state.backend_origin === backendBase &&
      (path === '/api/layout/analyze' || /^\/admin\/templates\/[^/]+$/.test(path) || path.startsWith('/debug/detection-queries/'));
    if (postSearch) {
      if (typeof input !== 'string' && !(input instanceof URL)) {
        state.post_tracking_incomplete = true;
        return original(input, options);
      }
      url.searchParams.set('benchmark_trace_id', state.token);
      state.post_requests++;
      return original(url.toString(), options);
    }
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
    backend_origin: state.backend_origin, post_requests: state.post_requests,
    preparation: state.preparation,
    post_tracking_incomplete: state.post_tracking_incomplete,
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
        folders = path.relative_to(directory).parts[:-1]
        folder_mode = next((folder for folder in reversed(folders) if folder in MODES), None)
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


def timing_values(tables, result, benchmark_timing=None):
    values = {}
    for rows in tables:
        for row in rows:
            key = row.get("metric") or row.get("stage")
            if key in METRICS:
                values[key] = row.get("valueMs", row.get("elapsedMs"))
    timing = {**response_timing(result), **(benchmark_timing or {})}
    browser_keys = {"uploadJobMs", "jobPollingMs", "detectionJobMs", "end_to_end_ms", "frontend_render_ms", "layout_runtime_ms", *FRONTEND_METRICS,
                    "frontend_preparation_ms", "request_network_ms", "request_round_trip_ms", "polling_wait_ms", "result_fetch_ms", "result_ready_ms",
                    "layout_queue_wait_ms", "layout_inference_ms", "layout_total_ms", "model_queue_wait_ms", "search_model_queue_wait_ms"}
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


SEARCH_GROUPS = (
    ("Run", (("Run ID", "run_id"), ("File name", "file"), ("Search result", "search_result"))),
    ("Image Preparation", (("Upload (ms)", "upload_ms"), ("Boundary and Auto Crop (ms)", "boundary_ms"),
                           ("Preparation result", "image_preparation"))),
    ("Template Search", (("User Time (ms)", "user_ms"), ("Backend Time (ms)", "backend_search_ms"),
                         ("Candidates before Pre-filter", "candidates_before_prefilter"),
                         ("Candidates after Pre-filter", "candidates_after_prefilter"),
                         ("Text Anchors checked", "text_anchors_checked"), ("Image Anchors checked", "image_anchors_checked"))),
)
OCR_GROUPS = (
    ("Run", (("Run ID", "run_id"), ("File name", "file"), ("Processing type", "processing_type"))),
    ("Auto ROI", (("User Time (ms)", "auto_roi_setup_ms"), ("Backend Time (ms)", "auto_roi_backend_ms"),
                  ("ROI detected", "auto_roi_region_count"), ("Layout API Calls", "auto_layout_calls"),
                  ("Layout Total Inputs", "auto_layout_inputs"), ("Layout Model Duration (ms)", "auto_layout_duration_ms"))),
    ("OCR Processing", (("User Time (ms)", "ocr_ui_wall_ms"), ("Backend Time (ms)", "ocr_backend_processing_sum_ms"),
                         ("ROI processed", "roi_count"), ("Text Boxes after DET", "detected_box_count"))),
)
MODEL_LABELS = {
    "layout": "Layout", "text_detection": "Text Detection", "text_recognition": "Text Recognition",
    "image_verification": "SigLIP", "table": "Table OCR", "openthai": "OpenThai",
}


def model_totals(calls, *, layout_incomplete=False):
    grouped = {}
    seen = set()
    for call in calls:
        kind = call.get("kind")
        call_id = call.get("call_id")
        if not kind or (call_id and call_id in seen):
            continue
        if call_id:
            seen.add(call_id)
        label = MODEL_LABELS.get(kind, kind)
        name = call.get("model")
        if name and name != label:
            label = f"{label} ({name})"
        grouped.setdefault(label, []).append(call)
    totals = {}
    for label, group in grouped.items():
        counts = [call.get("input_count") for call in group]
        times = [None if call.get("conflicting_record") else valid_duration(call.get("model_duration")) for call in group]
        incomplete = layout_incomplete and group[0].get("kind") == "layout"
        totals[label] = (
            None if incomplete else len(group),
            sum(counts) if not incomplete and all(type(count) is int and count >= 0 for count in counts) else None,
            round(sum(times), 2) if not incomplete and all(value is not None for value in times) else None,
        )
    return totals


def performance_rows(rows):
    searches, extractions = [], []
    for row in rows:
        ocr = row.get("_ocr") or {}
        identity = {"run_id": row.get("trace_id"), "file": Path(row["file"]).name if row.get("file") else None}
        matched = row.get("matched")
        waits = [valid_duration(row.get(key)) for key in ("upload_ms", "boundary_ms", "end_to_end_ms")]
        if row.get("boundary_skipped"):
            waits[1] = 0
        calls = row.get("_model_calls") or []
        if row.get("status") == "FAIL":
            calls = []
        flex_calls = [call for call in calls if call.get("phase") == "flex_roi"]
        search_calls = [call for call in calls if call.get("kind") != "layout"]
        incomplete = row.get("layout_telemetry_incomplete", False)
        searches.append({
            **row, **identity,
            "search_result": "Found" if matched is True else "Not Found" if matched is False else None,
            "user_ms": round(sum(waits), 2) if all(value is not None for value in waits) else None,
            "_models": {"Template Search API": model_totals(search_calls)},
        })
        pages = ocr.get("_pages", [])
        ocr_calls = []
        if ocr.get("status") != "FAIL" and not any(
            "backend_extraction_timing_missing" in page.get("capture_missing_reasons", []) for page in pages
        ):
            ocr_calls = [call for page in pages for call in page.get("_model_calls", [])]
        auto_calls = ocr.get("_auto_roi_model_calls", [])
        auto_layout_calls, auto_layout_inputs, auto_layout_duration = model_totals(
            [{**call, "model": None} for call in auto_calls if call.get("kind") == "layout"]
        ).get("Layout", (None, None, None))
        extractions.append({
            **ocr, **identity,
            "auto_layout_calls": auto_layout_calls,
            "auto_layout_inputs": auto_layout_inputs,
            "auto_layout_duration_ms": auto_layout_duration,
            "_models": {
                "Flex ROI API": model_totals(flex_calls, layout_incomplete=incomplete),
                "Auto ROI API": model_totals([call for call in auto_calls if call.get("kind") != "layout"]),
                "OCR API": model_totals(ocr_calls),
            },
        })
    return searches, extractions


def write_performance_sheet(sheet, base_groups, rows, *, default_models):
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    groups = list(base_groups)
    phases = dict.fromkeys([*default_models, *(phase for row in rows for phase in row["_models"])])
    for phase in phases:
        labels = {label for row in rows for label in row["_models"].get(phase, {})}
        for model in default_models.get(phase, ()):
            if not any(label == model or label.startswith(f"{model} (") for label in labels):
                labels.add(model)
        for label in sorted(labels):
            groups.append((f"{phase} / {label}", tuple(
                (heading, (phase, label, index))
                for index, heading in enumerate(("Calls", "Total Inputs", "Model Duration (ms)"))
            )))

    column = 1
    for group_index, (group, fields) in enumerate(groups):
        end = column + len(fields) - 1
        sheet.merge_cells(start_row=1, start_column=column, end_row=1, end_column=end)
        title = sheet.cell(1, column, group)
        title.fill = PatternFill("solid", fgColor="1E3A5F" if group_index % 2 == 0 else "28516D")
        title.font = Font(color="FFFFFF", bold=True)
        title.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        for heading, key in fields:
            header = sheet.cell(2, column, heading)
            header.fill = PatternFill("solid", fgColor="E7EFF7")
            header.font = Font(color="20384D", bold=True)
            header.alignment = Alignment(wrap_text=True, vertical="center")
            sheet.column_dimensions[get_column_letter(column)].width = (
                36 if key in {"run_id", "file"} else max(14, min(26, len(heading) + 3))
            )
            for row_index, row in enumerate(rows, start=3):
                if isinstance(key, tuple):
                    phase, label, metric = key
                    value = row["_models"].get(phase, {}).get(label, (None, None, None))[metric]
                else:
                    value = row.get(key)
                if heading.endswith("(ms)"):
                    value = valid_duration(value)
                cell = sheet.cell(row_index, column, excel_value(value))
                if heading.endswith("(ms)"):
                    cell.number_format = "#,##0.00"
            column += 1
    sheet.row_dimensions[1].height = 30
    sheet.row_dimensions[2].height = 28
    sheet.freeze_panes = "C3"
    sheet.auto_filter.ref = f"A2:{get_column_letter(column - 1)}{max(2, len(rows) + 2)}"


def save_excel(path, rows):
    from openpyxl import Workbook

    path.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    searches, extractions = performance_rows(rows)
    search_sheet = workbook.active
    search_sheet.title = "Template Search"
    try:
        write_performance_sheet(search_sheet, SEARCH_GROUPS, searches, default_models={})
        write_performance_sheet(
            workbook.create_sheet("OCR Processing"), OCR_GROUPS, extractions,
            default_models={"Flex ROI API": ("Layout",), "OCR API": ("Table OCR",)},
        )
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
    login_url = frontend_url.rstrip("/") + "/login?next=/"
    try:
        response = page.goto(login_url, wait_until="domcontentloaded", timeout=30000)
    except Exception:
        raise RuntimeError(f"Cannot open {login_url}. Check that the Frontend is running.") from None
    if response is not None and response.ok is False:
        raise RuntimeError(f"Frontend returned HTTP {response.status} for {login_url}.")
    try:
        page.locator('input[type="email"]').fill(os.environ.get("BENCHMARK_EMAIL", "user@ocr.com"))
        page.locator('input[type="password"]').fill(os.environ.get("BENCHMARK_PASSWORD", "user123"))
        page.get_by_role("button", name="เข้าสู่ระบบ", exact=True).click()
    except Exception:
        raise RuntimeError("Could not complete the login form. Check that the Frontend serves the expected login page.") from None
    try:
        page.locator(UPLOAD_INPUT).wait_for(timeout=120000)
    except Exception:
        raise RuntimeError("Login submitted, but the User upload screen did not open. Check the benchmark User account and Frontend logs.") from None


def reset_document(page):
    page.wait_for_timeout(5000)
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
        page.evaluate("token => { window.__ocrBenchmarkTables = []; window.__ocrBenchmarkRun = {token, preparation: {}, pending: 0, posts: 0, post_requests: 0, post_tracking_incomplete: false, job_id: null, jobs: {}, errors: []}; }", token)
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
            for key in ("frontend_visibility_status", "frontend_visibility_reason", "frontend_timing_kind", "frontend_run_token", "benchmark_run_token", "initial_server_timing_header_status"):
                row[key] = frontend.get(key)
            if frontend_status is not None and row.get("benchmark_run_token") != token:
                raise CaptureFailure("RESPONSE_CAPTURE_ERROR", "Frontend visibility belongs to another benchmark run")
            if frontend_status == "PREPARATION_ERROR":
                raise CaptureFailure("PREPARATION_ERROR", "Frontend could not prepare the confirmed images")
            frontend_done = "end_to_end_ms" in frontend and frontend_status in ("READY", "PREVIEW_ERROR", "VISIBILITY_TIMEOUT", "VISIBILITY_ERROR")
            if job.get("status") == "completed":
                result = job.get("result") if isinstance(job.get("result"), dict) else {}
            drained = snapshot.get("pending") == 0
            if terminal and drained and frontend_done:
                if snapshot.get("posts") != 1:
                    raise CaptureFailure("RESPONSE_CAPTURE_ERROR", "Ambiguous detection submission count")
                row["_ready_for_next"] = True
                if not isinstance(job.get("benchmark_timing"), dict) or job["benchmark_timing"].get("trace_id") != token:
                    raise CaptureFailure("RESPONSE_CAPTURE_ERROR", "Backend benchmark trace missing or mismatched; update Backend")
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
        measured_mode = ((snapshot or {}).get("job") or {}).get("benchmark_timing", {}).get("processing_mode") or actual_mode(result)
        if expected_mode and measured_mode != expected_mode:
            row["status"] = "SKIP"
            row["error"] = f"Active mode is {measured_mode or 'unknown'}, expected {expected_mode}; global setting was not changed"
        else:
            row["status"] = "PASS"
    except Exception as error:
        row["status"] = "FAIL"
        row["error_type"] = error.kind if isinstance(error, CaptureFailure) else "BENCHMARK_ERROR"
        row["error"] = str(error)[:500]
    finally:
        job_data = (snapshot or {}).get("job") or {}
        benchmark_timing = job_data.get("benchmark_timing") or {}
        row.update(timing_values(tables, result, benchmark_timing))
        row["backend_search_ms"] = valid_duration(benchmark_timing.get("backend_runtime_ms"))
        if (snapshot or {}).get("backend_origin") and not snapshot.get("post_tracking_incomplete"):
            try:
                spans = page.evaluate("""async ({origin, trace, expected}) => {
                  const response = await fetch(`${origin}/api/benchmark/template-search/${trace}?expected_post_requests=${expected}`, {cache:'no-store'});
                  if (!response.ok) return null;
                  return (await response.json()).data;
                }""", {"origin": snapshot["backend_origin"], "trace": token, "expected": snapshot.get("post_requests") or 0})
                if isinstance(spans, dict) and spans.get("trace_id") == token:
                    for key in ("backend_search_ms", "backend_post_search_ms", "backend_total_active_ms"):
                        row[key] = valid_duration(spans.get(key))
            except Exception:
                pass
        timing = {**response_timing(result), **benchmark_timing}
        row.update((snapshot or {}).get("preparation") or {})
        for key in ("candidates_before_prefilter", "candidates_after_prefilter", "text_anchors_checked", "image_anchors_checked"):
            row[key] = timing.get(key)
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
        row["_model_calls"] = list(timing.get("model_calls") or [])
        known_calls = {call.get("call_id") for call in row["_model_calls"] if isinstance(call, dict)}
        for table in tables:
            for item in table:
                if item.get("metric") == "layout_telemetry_incomplete":
                    row["layout_telemetry_incomplete"] = item.get("value")
                if item.get("metric") == "layout_model_calls" and isinstance(item.get("value"), list):
                    for call in item["value"]:
                        if isinstance(call, dict) and call.get("call_id") and call["call_id"] not in known_calls:
                            row["_model_calls"].append(call)
                            known_calls.add(call["call_id"])
        row["roi_count"] = row.get("roi_crop_count")
        det_counts = [call.get("detected_box_count") for call in row["_model_calls"]
                      if isinstance(call, dict) and call.get("kind") == "text_detection"]
        row["detected_box_count"] = (
            sum(det_counts) if all(count is not None for count in det_counts) else None
        ) if det_counts else timing.get("detected_box_count")
        row["actual_mode"] = benchmark_timing.get("processing_mode") or actual_mode(result)
        row["matched"] = result.get("matched")
        row["query_id"] = result.get("query_id") or result.get("queryId")
        row["trace_id"] = token
        best = result.get("best_candidate") or result.get("bestCandidate") or {}
        row["template_id"] = best.get("template_id") or best.get("templateId") if isinstance(best, dict) else None
        if result.get("matched") is False:
            row["template_id"] = None
        row["matching_score"] = (best.get("final_score") if best.get("final_score") is not None else best.get("score")) if isinstance(best, dict) else None
    return row


OCR_BUTTON = 'อ่านข้อมูลที่เลือก'
EDITOR_HEADING = 'ตรวจสอบและแก้ไขผล OCR'
AUTO_ROI_BUTTON = 'ตีกรอบ ROI อัตโนมัติ'

# Keep only OCR telemetry and counts; never copy recognized text or source images.
CAPTURE = r"""(() => {
  if (window.__ocrOnlyInstalled) return;
  window.__ocrOnlyInstalled = true;
  const info = console.info.bind(console);
  console.info = (...args) => {
    const state = window.__ocrOnlyRun;
    if (state && args[0] === '[OCR timing] extraction completed' && args[1]) {
      const {backend, ...client} = args[1];
      state.client.push(client);
    }
    return info(...args);
  };
  const original = window.fetch.bind(window);
  window.fetch = async (input, options) => {
    const state = window.__ocrOnlyRun;
    const url = new URL(typeof input === 'string' || input instanceof URL ? input : input.url, location.href);
    const method = (options?.method || input?.method || 'GET').toUpperCase();
    const path = url.pathname.replace(/^\/backend\//, '/');
    const post = method === 'POST' && path === '/api/ai/process';
    const poll = method === 'GET' && path.match(/^\/api\/ai\/jobs\/([^/]+)$/);
    if (!state || (!post && !poll)) return original(input, options);
    state.pending++;
    let counts = {};
    if (post) {
      state.posts++;
      try {
        const rois = JSON.parse(options.body).rois || [];
        counts = {submitted_roi_count: rois.length, text_count: 0, table_count: 0, image_count: 0, flexible_count: 0};
        for (const roi of rois) {
          if (roi.type === 'image' || roi.extractionMethod === 'extract_image') counts.image_count++;
          else if (roi.type === 'table') counts.table_count++;
          else if (roi.roiMode === 'flexible') counts.flexible_count++;
          else counts.text_count++;
        }
      } catch { state.errors.push('OCR submission metadata unavailable'); }
    }
    try {
      const response = await original(input, options);
      response.clone().json().then(body => {
        if (!response.ok || !body.success) throw new Error('OCR HTTP request failed');
        if (post) {
          if (!body.job_id) throw new Error('Missing OCR job ID');
          state.jobs[body.job_id] = {...state.jobs[body.job_id], job_id: body.job_id,
            page_number: state.posts, ...counts, job_create_ms: body.job_create_ms};
        } else {
          const id = decodeURIComponent(poll[1]);
          if (body.job_id !== id) throw new Error('OCR job ID mismatch');
          const previous = state.jobs[id];
          if (!previous) throw new Error('Untracked OCR job');
          if (!['completed', 'failed'].includes(previous.status)) {
            state.jobs[id] = {...previous, status: body.status, error: body.error,
              requested_at: body.requested_at, started_at: body.started_at, completed_at: body.completed_at,
              result_count: body.result?.extracted_data?.length,
              timing: body.result?.extraction_timing};
          }
        }
      }).catch(error => state.errors.push(String(error))).finally(() => state.pending--);
      return response;
    } catch (error) {
      state.pending--;
      state.errors.push('OCR fetch failed');
      throw error;
    }
  };
})();"""
SNAPSHOT = '() => window.__ocrOnlyRun'


def page_record(job, clients):
    record = {key: job.get(key) for key in (
        'job_id', 'page_number', 'status', 'error', 'submitted_roi_count', 'text_count', 'table_count',
        'image_count', 'flexible_count', 'result_count', 'job_create_ms')}
    client = next((item for item in clients if item.get('job_id') == job.get('job_id')), {})
    record['page_number'] = client.get('pageNumber', job.get('page_number'))
    record['roi_processing_mode'] = client.get('roiProcessingMode')
    for key in ('frontend_total_ms', 'request_json_ms', 'create_request_ms', 'poll_count', 'poll_request_ms', 'poll_wait_ms'):
        record[key] = client.get(key)
    timing = job.get('timing') or {}
    for key in ('processing_ms', 'roi_count', 'detected_box_count', 'model_call_count', 'model_wait_union_ms', 'model_timing_missing_reason'):
        record[key] = timing.get(key)
    record.update(timing.get('fixed_text_counts') or {})
    for kind, value in (timing.get('inferences') or {}).items():
        record[f'{kind}_inference_ms'] = value
    missing = []
    if not timing:
        missing.append('backend_extraction_timing_missing')
    if not client:
        missing.append('frontend_ocr_timing_missing')
    record['capture_missing_reasons'] = missing
    record['_stages'] = timing.get('stages') or {}
    record['_model_calls'] = timing.get('model_calls') or []
    return record


def click_timed(button, token, timeout_ms):
    button.evaluate("""(element, token) => {
        element.addEventListener('click', () => {
            window.__ocrBenchmarkClick = {token, start: performance.now()};
        }, {capture: true, once: true});
    }""", token, timeout=timeout_ms)
    button.click(timeout=timeout_ms)


def observed_click_ms(page, token):
    elapsed = valid_duration(page.evaluate("""token => {
        const click = window.__ocrBenchmarkClick;
        return click?.token === token ? performance.now() - click.start : null;
    }""", token))
    return round(elapsed, 2) if elapsed is not None else None


def run_ocr(page, timeout_ms):
    token = uuid4().hex
    page.evaluate(CAPTURE)
    page.evaluate('token => {window.__ocrOnlyRun = {token, pending:0, posts:0, jobs:{}, client:[], errors:[]};}', token)
    started = time.monotonic()
    ui_wall_ms = None
    failure = None
    snapshot = {}
    try:
        click_timed(page.get_by_role('button', name=OCR_BUTTON, exact=True), token, timeout_ms)
        deadline = started + timeout_ms / 1000
        while True:
            snapshot = page.evaluate(SNAPSHOT)
            if not snapshot or snapshot.get('token') != token:
                raise RuntimeError('OCR capture lost or replaced')
            jobs = list(snapshot.get('jobs', {}).values())
            terminal = jobs and all(job.get('status') in ('completed', 'failed') for job in jobs)
            ready = page.get_by_role('heading', name=EDITOR_HEADING, exact=True).is_visible()
            if terminal and snapshot['pending'] == 0 and ready:
                ui_wall_ms = observed_click_ms(page, token)
                break
            # If the UI has stopped loading with only failed/empty jobs, do not wait until timeout.
            button = page.get_by_role('button', name=OCR_BUTTON, exact=True)
            if terminal and snapshot['pending'] == 0 and button.count() and button.is_enabled():
                raise RuntimeError('OCR finished without opening the result editor')
            if time.monotonic() >= deadline:
                raise TimeoutError('Timed out waiting for OCR jobs and result editor')
            page.wait_for_timeout(100)
        if snapshot.get('errors'):
            raise RuntimeError('; '.join(snapshot['errors']))
    except Exception as error:
        failure = str(error)[:500]
        # Capture the last available state, including completed pages before a failure.
        try:
            snapshot = page.evaluate(SNAPSHOT) or snapshot
        except Exception:
            pass
    records = [page_record(job, snapshot.get('client') or []) for job in (snapshot.get('jobs') or {}).values()]
    if snapshot.get('posts') != len(records):
        failure = failure or 'OCR submission/job count mismatch'
    if any(row.get('status') == 'failed' for row in records):
        failure = failure or 'One or more OCR pages failed'
    incomplete = ui_wall_ms is None or any(row['capture_missing_reasons'] for row in records)
    status = 'FAIL' if failure else 'TIMING_INCOMPLETE' if incomplete else 'PASS'
    def page_sum(key):
        values = [valid_duration(row.get(key)) for row in records]
        return sum(values) if status != 'FAIL' and values and all(value is not None for value in values) else None
    backend_sum = page_sum('processing_ms')
    detected_boxes = page_sum('detected_box_count') if any(
        call.get('kind') == 'text_detection' for row in records for call in row['_model_calls']
    ) else None
    processing_modes = {row.get('roi_processing_mode') for row in records}
    processing_type = (
        'Flex ROI' if 'Flex ROI' in processing_modes else
        'Fixed ROI' if processing_modes == {'Fixed ROI'} else None
    )
    return {'status': status, 'error': failure, 'processing_type': processing_type,
            'ocr_ui_wall_ms': ui_wall_ms,
            'ocr_backend_processing_sum_ms': round(backend_sum, 2) if backend_sum is not None else None,
            'roi_count': page_sum('roi_count'), 'detected_box_count': detected_boxes,
            'page_job_count': len(records), 'result_count': sum(row.get('result_count') or 0 for row in records),
            'capture_errors': snapshot.get('errors') or [], '_pages': records}


def prepare_auto_roi(page, timeout_ms, trace_id=None):
    """The existing Auto ROI button analyzes all document pages in one action."""
    trace_id = trace_id or uuid4().hex
    page.evaluate("trace => {window.__ocrBenchmarkAutoRoiTrace = trace;}", trace_id)
    try:
        with page.expect_response(lambda response: urlparse(response.url).path in (
                                      '/api/layout/analyze', '/backend/api/layout/analyze')
                                  and response.request.method == 'POST', timeout=timeout_ms) as pending:
            click_timed(page.get_by_role('button', name=AUTO_ROI_BUTTON, exact=True), trace_id, timeout_ms)
    finally:
        page.evaluate("() => {delete window.__ocrBenchmarkAutoRoiTrace;}")
    response = pending.value
    result = response.json()
    if not response.ok or result.get('success') is not True:
        raise RuntimeError(result.get('detail') or result.get('error') or 'Auto ROI request failed')
    created = re.compile(r'^สร้าง ROI อัตโนมัติ (\d+) รายการจาก')
    message = page.get_by_text(re.compile(r'^สร้าง ROI อัตโนมัติ \d+ รายการจาก|^PaddleOCR ไม่พบ Text, Table หรือ Image Region'))
    message.wait_for(timeout=timeout_ms)
    ui_wall_ms = observed_click_ms(page, trace_id)
    match = created.match(message.inner_text())
    timing = result.get('benchmark_timing') or {}
    if timing.get('trace_id') != trace_id:
        timing = {}
    return {'auto_roi_setup_ms': ui_wall_ms,
            'auto_roi_backend_ms': timing.get('backend_runtime_ms'),
            'auto_roi_region_count': int(match.group(1)) if match else 0,
            '_auto_roi_model_calls': timing.get('model_calls') or []}


def run_after_detection(page, setup, timeout_ms):
    if setup.get('status') == 'SKIP':
        return {'status': 'SKIP_SEARCH', 'error': setup.get('error')}
    if setup.get('status') != 'PASS':
        return {'status': 'SETUP_FAILED', 'error': setup.get('error')}
    if setup.get('matched') is True:
        return {'roi_source': 'template', **run_ocr(page, timeout_ms)}
    if setup.get('matched') is not False:
        return {'status': 'SETUP_FAILED', 'error': 'Template result has no boolean matched value'}
    try:
        preparation = prepare_auto_roi(page, timeout_ms, trace_id=setup.get('trace_id'))
    except Exception as error:
        return {'status': 'AUTO_ROI_FAILED', 'processing_type': 'No Template', 'error': str(error)[:500]}
    if not preparation['auto_roi_region_count']:
        return {'status': 'SKIP_NO_ROI', 'roi_source': 'auto_roi', 'processing_type': 'No Template', **preparation,
                'error': 'Auto ROI found no regions; OCR not run'}
    return {'roi_source': 'auto_roi', **preparation, **run_ocr(page, timeout_ms), 'processing_type': 'No Template'}


def run_cases(page, arguments, cases, rows, browser_state):
    for path, mode in cases:
        for run_number in range(1, arguments.repeats + 1):
            if browser_state["closed"]:
                print("Benchmark stopped: browser was closed. Completed results are saved; rerun the command to start again.")
                return False
            row = run_case(page, path, mode, arguments.timeout_seconds * 1000)
            row.update(run_number=run_number, runs_per_file=arguments.repeats)
            truth = getattr(arguments, "ground_truth", {})
            if isinstance(truth, dict) and truth:
                try:
                    relative = str(path.relative_to(arguments.input)).replace("\\", "/")
                except ValueError:
                    relative = ""
                if relative in truth:
                    expected = truth[relative]
                    row["expected_matched"] = expected if isinstance(expected, bool) else expected is not None
                    if isinstance(row.get("matched"), bool):
                        row["match_correctness"] = row["matched"] == row["expected_matched"]
                    if not isinstance(expected, bool):
                        row["expected_template_id"] = expected
                        if isinstance(row.get("matched"), bool):
                            row["matching_correctness"] = row.get("template_id") == expected
            if not arguments.no_ocr and row["status"] == "PASS":
                page.evaluate("() => {window.__ocrBenchmarkRun = null;}")
                ocr = run_after_detection(page, row, arguments.timeout_seconds * 1000)
                row.update(_ocr=ocr, ocr_status=ocr["status"], ocr_wall_ms=ocr.get("ocr_ui_wall_ms"))
            rows.append(row)
            if not save_results(arguments, rows):
                return False
            outcome = "FOUND" if row.get("matched") is True else "NOT_FOUND" if row.get("matched") is False else "UNKNOWN"
            print(f"{path.name} [{run_number}/{arguments.repeats}]: {row['status']} mode={row.get('actual_mode') or 'unknown'} Template={outcome}")
            print(f"  Template Search UI: {row.get('end_to_end_ms')} ms | OCR UI: {row.get('ocr_wall_ms')} ms ({row.get('ocr_status', 'NOT_RUN')})")
            if row.get("error"):
                print(f"  Template Search error [{row.get('error_type') or row['status']}]: {row['error']}")
            if (row.get("_ocr") or {}).get("error"):
                print(f"  OCR error: {row['_ocr']['error']}")
            if row.get("ocr_status") in ("FAIL", "SETUP_FAILED", "AUTO_ROI_FAILED"):
                print("Benchmark stopped after an incomplete OCR run. Search and OCR results were saved.")
                return False
            if row.get("_ready_for_next") is False:
                print("Benchmark stopped safely: run capture is incomplete; no New Document or next upload.")
                return False
            if browser_state["closed"]:
                print("Benchmark stopped: browser was closed. Completed results are saved; rerun the command to start again.")
                return False
            try:
                if "_ocr" in row:
                    page.evaluate("() => {window.__ocrOnlyRun = null;}")
                reset_document(page)
            except Exception:
                print("Benchmark stopped: unable to reset with New Document. Completed results are saved.")
                return False
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "input")
    parser.add_argument("--output", type=Path, default=ROOT / "output" / f"user_flow_{datetime.now():%Y%m%d_%H%M%S}.xlsx")
    parser.add_argument("--frontend", default=os.environ.get("BENCHMARK_FRONTEND", "http://localhost:3000"))
    parser.add_argument("--headless", action="store_true", help="Run Chromium without opening a window")
    parser.add_argument("--expected-mode", choices=sorted(MODES))
    parser.add_argument("--timeout-seconds", type=int, default=1200)
    parser.add_argument("--no-ocr", action="store_true", help="Stop after Template Search")
    parser.add_argument("--repeats", type=int, default=2, help="Runs per file (default: 2)")
    parser.add_argument("--ground-truth", type=Path, default=ROOT / "ground_truth.json", help="JSON mapping filenames to expected matched booleans or Template IDs (null for no match)")
    arguments = parser.parse_args()
    if arguments.ground_truth is None:
        arguments.ground_truth = {}
    elif arguments.ground_truth.exists():
        arguments.ground_truth = json.loads(arguments.ground_truth.read_text(encoding="utf-8"))
    elif arguments.ground_truth == ROOT / "ground_truth.json":
        arguments.ground_truth = {}
    else:
        parser.error(f"Ground truth file not found: {arguments.ground_truth}")
    if not isinstance(arguments.ground_truth, dict) or any(value is not None and not isinstance(value, (str, bool)) for value in arguments.ground_truth.values()):
        parser.error("--ground-truth must map filenames to booleans, template IDs or null")
    if arguments.repeats < 1:
        parser.error("--repeats must be at least 1")
    if arguments.timeout_seconds < 1:
        parser.error("--timeout-seconds must be at least 1")
    cases = input_cases(arguments.input, arguments.expected_mode)
    if not cases:
        parser.error(f"No supported images/PDFs in {arguments.input}")
    frontend = urlparse(arguments.frontend)
    allowed_local = frontend.scheme == "http" and frontend.netloc in {"localhost:3000", "127.0.0.1:3000", "frontend:3000"}
    allowed_deployed = frontend.scheme == "https" and frontend.netloc == "coop-project4-v2.vercel.app"
    if not (allowed_local or allowed_deployed) or frontend.username or frontend.password:
        parser.error("Use http://localhost:3000, the Compose service http://frontend:3000, or https://coop-project4-v2.vercel.app")
    from playwright.sync_api import Error, sync_playwright

    rows = []
    completed = False
    try:
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(str(ROOT / "browser-profile"), headless=arguments.headless)
            browser_state = {"closed": False}
            context.on("close", lambda closed_context: browser_state.update(closed=True))
            try:
                login_page = context.pages[0] if context.pages else context.new_page()
                login_user(login_page, arguments.frontend)
                print("Logged in. Measuring Template Search only." if arguments.no_ocr else "Logged in. Measuring Template Search and OCR.")
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
    return 1 if not completed or any(row["status"] == "FAIL" or row.get("ocr_status") == "TIMING_INCOMPLETE" for row in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
