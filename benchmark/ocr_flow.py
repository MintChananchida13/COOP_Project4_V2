"""Standalone selected-ROI OCR benchmark. Template search is unmeasured setup."""
import argparse
from datetime import datetime
import json
import math
import os
from pathlib import Path
import tempfile
import time
from urllib.parse import urlparse
from uuid import uuid4

import user_flow

ROOT = Path(__file__).resolve().parent
OCR_BUTTON = 'อ่านข้อมูลที่เลือก'
EDITOR_HEADING = 'ตรวจสอบและแก้ไขผล OCR'

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
    const post = method === 'POST' && url.pathname === '/api/ai/process';
    const poll = method === 'GET' && url.pathname.match(/^\/api\/ai\/jobs\/([^/]+)$/);
    if (!state || (!post && !poll)) return original(input, options);
    state.pending++;
    let counts = {};
    if (post) {
      state.posts++;
      try {
        const rois = JSON.parse(options.body).rois || [];
        counts = {roi_count: rois.length, text_count: 0, table_count: 0, image_count: 0, flexible_count: 0};
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


def duration(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0 else None


def page_record(job, clients):
    record = {key: job.get(key) for key in (
        'job_id', 'page_number', 'status', 'error', 'roi_count', 'text_count', 'table_count',
        'image_count', 'flexible_count', 'result_count', 'job_create_ms')}
    client = next((item for item in clients if item.get('job_id') == job.get('job_id')), {})
    record['page_number'] = client.get('pageNumber', job.get('page_number'))
    for key in ('frontend_total_ms', 'request_json_ms', 'create_request_ms', 'poll_count', 'poll_request_ms', 'poll_wait_ms'):
        record[key] = client.get(key)
    timing = job.get('timing') or {}
    for key in ('processing_ms', 'model_call_count', 'model_wait_union_ms', 'model_timing_missing_reason'):
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


def run_ocr(page, timeout_ms):
    token = uuid4().hex
    page.evaluate(CAPTURE)
    page.evaluate('token => {window.__ocrOnlyRun = {token, pending:0, posts:0, jobs:{}, client:[], errors:[]};}', token)
    started = time.monotonic()
    failure = None
    snapshot = {}
    try:
        page.get_by_role('button', name=OCR_BUTTON, exact=True).click(timeout=timeout_ms)
        deadline = started + timeout_ms / 1000
        while True:
            snapshot = page.evaluate(SNAPSHOT)
            if not snapshot or snapshot.get('token') != token:
                raise RuntimeError('OCR capture lost or replaced')
            jobs = list(snapshot.get('jobs', {}).values())
            terminal = jobs and all(job.get('status') in ('completed', 'failed') for job in jobs)
            ready = page.get_by_role('heading', name=EDITOR_HEADING, exact=True).is_visible()
            if terminal and snapshot['pending'] == 0 and ready:
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
    incomplete = any(row['capture_missing_reasons'] for row in records)
    status = 'FAIL' if failure else 'TIMING_INCOMPLETE' if incomplete else 'PASS'
    return {'status': status, 'error': failure, 'ocr_ui_wall_ms': round((time.monotonic() - started) * 1000, 2),
            'page_job_count': len(records), 'result_count': sum(row.get('result_count') or 0 for row in records),
            'capture_errors': snapshot.get('errors') or [], '_pages': records}


def cell(value):
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False)
    if isinstance(value, str) and value[:1] in ('=', '+', '-', '@'):
        return "'" + value
    return value


def save_report(path, rows):
    from openpyxl import Workbook
    workbook = Workbook()
    workbook.remove(workbook.active)
    pages, stages, calls = [], [], []
    for row in rows:
        identity = {key: row.get(key) for key in ('file', 'run_number', 'template_id')}
        for page in row.get('_pages', []):
            pages.append({**identity, **page})
            page_identity = {**identity, 'job_id': page.get('job_id'), 'page_number': page.get('page_number')}
            stages.extend({**page_identity, 'stage': stage, **values} for stage, values in page.get('_stages', {}).items())
            calls.extend({**page_identity, **call} for call in page.get('_model_calls', []))
    for name, records, required in (
        ('OCR Runs', rows, ['file', 'run_number', 'status', 'ocr_ui_wall_ms', 'page_job_count', 'result_count', 'error']),
        ('OCR Pages', pages, ['file', 'run_number', 'page_number', 'job_id', 'status', 'processing_ms']),
        ('OCR Stages', stages, ['file', 'run_number', 'page_number', 'job_id', 'stage', 'elapsed_ms', 'count']),
        ('OCR Model Calls', calls, ['file', 'run_number', 'page_number', 'job_id', 'call_id', 'kind', 'http_request_ms', 'inference_ms'])):
        sheet = workbook.create_sheet(name)
        headers = list(dict.fromkeys(required + [key for row in records for key in row if not key.startswith('_')]))
        sheet.append(headers)
        for row in records:
            sheet.append([cell(duration(row.get(key)) if key.endswith('_ms') else row.get(key)) for key in headers])
        sheet.freeze_panes = 'A2'
        sheet.auto_filter.ref = sheet.dimensions
    notes = workbook.create_sheet('Metric Notes')
    notes.append(['metric', 'meaning'])
    for metric, meaning in (
        ('scope', 'Selected-ROI OCR only. Upload/AdjustZone/template search are setup and excluded.'),
        ('selection', 'Uses enabled/default-selected ROIs from the matched template; no fields are toggled by this runner.'),
        ('ocr_ui_wall_ms', 'Click to observed result editor, including all page jobs. Observation interval is 100 ms.'),
        ('frontend_total_ms', 'One page JSON serialization through result receipt, before rendering.'),
        ('processing_ms', 'Backend OCR processing only; excludes job load, queue and final result persistence.'),
        ('job_create_ms', 'Backend payload serialization and database job creation.'),
        ('result_store_ms', 'Available in Backend logs only; not in the stored result.'),
        ('poll_wait_ms', 'Accumulated polling sleeps, including while OCR is running; not pure overhead.'),
        ('http_request_ms', 'Model HTTP round trip; includes Gateway work, not network-only latency.'),
        ('inference_ms', 'Gateway-reported inference; blank if unavailable. Missing values are never replaced by zero.'),
        ('stages', 'Stage totals include nested model work; do not add to model time.'),
        ('accuracy', 'Timing report only. Success does not assert OCR text correctness.'),
        ('privacy', 'No recognized text or source image data are exported.'),
    ):
        notes.append([metric, meaning])
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, suffix='.xlsx', delete=False) as handle:
            temporary = Path(handle.name)
        workbook.save(temporary)
        try:
            os.replace(temporary, path)
        except PermissionError:
            path = path.with_name(f'{path.stem}_recovery_{uuid4().hex[:8]}.xlsx')
            os.replace(temporary, path)
        return path
    finally:
        workbook.close()
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, default=ROOT / 'input')
    parser.add_argument('--output', type=Path, default=ROOT / 'output' / f'ocr_flow_{datetime.now():%Y%m%d_%H%M%S}.xlsx')
    parser.add_argument('--frontend', default='http://localhost:3000')
    parser.add_argument('--repeats', type=int, default=2)
    parser.add_argument('--timeout-seconds', type=int, default=1200)
    args = parser.parse_args()
    if args.repeats < 1 or args.timeout_seconds < 1:
        parser.error('Repeats and timeout must be positive')
    url = urlparse(args.frontend)
    if not ((url.scheme == 'http' and url.netloc in ('localhost:3000', '127.0.0.1:3000')) or
            (url.scheme == 'https' and url.netloc == 'coop-project4-v2.vercel.app')) or url.username or url.password:
        parser.error('Use the local Frontend or https://coop-project4-v2.vercel.app')
    cases = user_flow.input_cases(args.input, None)
    if not cases:
        parser.error('No supported input files')
    from playwright.sync_api import sync_playwright
    rows = []
    try:
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(str(ROOT / 'ocr-browser-profile'), headless=False)
            try:
                page = context.pages[0] if context.pages else context.new_page()
                user_flow.login_user(page, args.frontend)
                for path, _ in cases:
                    for run_number in range(1, args.repeats + 1):
                        setup = user_flow.run_case(page, path, None, args.timeout_seconds * 1000)
                        identity = {'file': str(path), 'run_number': run_number, 'template_id': setup.get('template_id')}
                        if setup['status'] != 'PASS':
                            row = {'status': 'SETUP_FAILED', 'error': setup.get('error')}
                        elif not setup.get('matched'):
                            row = {'status': 'SKIP_NO_TEMPLATE', 'error': 'No matched template; selected-ROI OCR not run'}
                        else:
                            row = run_ocr(page, args.timeout_seconds * 1000)
                        rows.append({**identity, **row})
                        args.output = save_report(args.output, rows)
                        print(f'{path.name} [{run_number}/{args.repeats}]: {row["status"]}')
                        # A timed-out OCR request must not contaminate a later run.
                        if row['status'] in ('FAIL', 'SETUP_FAILED'):
                            return 1
                        page.evaluate('() => {window.__ocrOnlyRun = null;}')
                        user_flow.reset_document(page)
            finally:
                context.close()
    finally:
        if rows:
            print(f'OCR report: {args.output}')
    return 1 if any(row['status'] == 'TIMING_INCOMPLETE' for row in rows) else 0


if __name__ == '__main__':
    raise SystemExit(main())
