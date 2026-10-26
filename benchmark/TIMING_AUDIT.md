# Timing audit and measurement contract

Frontend boundary update: see [Result Visibility](RESULT_VISIBILITY.md).
`end_to_end_ms` now stops at Result Visibility Ready (paint-ready estimate, NOT
Actual Paint). `frontend_flow_complete_ms` and `frontend_commit_end_to_end_ms`
preserve handler/commit boundaries. The original frontend definitions below are
historical; backend/model timing definitions are unchanged.

Code/tests only: no browser, login, benchmark, real inference or Kaggle runtime
was executed for this change.

## Root causes confirmed in source

- Backend `app/processing/model_timing.py::summarize_model_calls` recognized raw
  `model_call_id`, but not its own exported `call_id` records, which also lost
  intervals. Partial duplicate records hid later measurements. Incomplete waits
  and nonfinite durations were accepted. Deduplication now merges by Call ID,
  rejects conflicting measurements, and preserves intervals on export.
- `app/processing/detection_service.py::_detection_timing_debug` mixed candidate/
  global legacy counters with Call ID totals. Cached/reused candidate timing could
  be duplicated or hidden by a missing global value. Every model runtime now uses
  the same unique-call summary, including SigLIP single and batch calls.
- API `app/main.py::layout` runs Layout AND DET by default. Its Layout runtime
  formerly included DET load/lock/inference. Additive `model_timings` now records
  actual components without changing results or the enclosing `inference_ms`.
- Frontend `page.tsx::logTemplateDetectionTiming` used null-coalescing fallbacks,
  resurrecting stale values despite explicit null. Union/count/overlap/reasons
  were absent from its table. Canonical null now stays null; metadata is not ms.
- `benchmark/user_flow.py::timing_values` preferred non-null console data to backend
  measurements, ignored nested benchmark fallback, and blocked legacy total when
  console contained a null total key. Producer/source precedence is now explicit.
- `save_excel` already mapped summary rows by header: no confirmed source-level
  column shift. Model Calls lacked repeat count and interval/component metadata.
  In-memory Excel round-trip tests now check every header and value on both sheets.
- Four blank metrics below reflect measurement limits, not lost measurements.

## Metrics

Backend paths below are relative to `project_backend/app/`.

| Metric | Source / start and end | Calculation / status |
| --- | --- | --- |
| `end_to_end_ms` | Frontend `page.tsx`, boundary confirmation to detection/template preparation completion log | Browser `performance.now()` delta; not actual paint |
| `total_runtime_ms` | `processing/detection_service.py`, worker start to timing snapshot | `perf_counter()` delta; same as `total_detection_ms` |
| `backend_runtime_ms` | Detection summary | Total minus complete HTTP interval union; derived wall time outside waits, NOT CPU time |
| `frontend_to_backend_ms` | Browser/backend | null; one-way latency unknown |
| `backend_to_frontend_ms` | Backend/browser | null; one-way latency unknown |
| `frontend_render_ms` | Browser | null; paint not instrumented |
| `backend_to_model_ms` | Backend/API | null; HTTP minus inference is NOT network-only duration |
| `layout_runtime_ms` | API Layout component operation start/end | Sum unique component inference; excludes nested DET |
| `text_detection_runtime_ms` | API DET operation/component start/end | Sum standalone and nested DET, counted once |
| `text_recognition_runtime_ms` | API REC operation start/end | Sum unique single/batch operations, not per-image multiplication |
| `image_siglip_runtime_ms` | API SigLIP operation start/end | Sum unique single/batch operations; includes logits adaptation |
| `table_runtime_ms` | API Table operation start/end | Sum unique Table operations; internal Table models not separately fabricated |
| `layout_wait_ms` | Backend HTTP start/end | Sum Layout round trips, including nested DET |
| `text_detection_wait_ms` | Backend HTTP start/end | Sum standalone DET; null if DET shares a Layout HTTP call |
| `text_recognition_wait_ms` | Backend HTTP start/end | Sum REC round trips |
| `image_siglip_wait_ms` | Backend HTTP start/end | Sum SigLIP round trips; runtime can still be unknown |
| `table_wait_ms` | Backend HTTP start/end | Sum Table round trips |
| `model_wall_clock_ms` | `processing/model_timing.py` | Last HTTP end minus first start; includes gaps |
| `model_wait_union_ms` | Same | Union of unique monotonic HTTP intervals, excluding gaps |
| `model_intervals_overlap` | Same | Strict interval overlap; touching intervals do not overlap; boolean |
| `model_call_count` | Same | Unique HTTP Call IDs, not image/category/component count |
| `model_timing_missing_reason` | Same | Interval/inference/wait incompleteness; per-metric detail in `timing_missing_reasons` |

All durations are milliseconds. Union <= envelope, and union <= detection total
is required for backend residual. Overlap does not invalidate a complete union.
Summed HTTP durations may exceed union with concurrency. Inference is never added
again to HTTP waits or total. These metrics are nested views, not additive stages.

Browser `uploadJobMs` includes POST/acknowledgement. `jobPollingMs` includes polling
sleeps and requests. `detectionJobMs` is a browser workflow stage. They overlap
backend work and must not be added to backend total. Browser E2E excludes login,
initial file/PDF preparation, the reset pause and ROI OCR. Backend total excludes
final response serialization and frontend processing/polling.

## Per-call fields and compatibility

`core/model_runtime_client.py::_post_predict` records a Call ID and HTTP start/end
using the same monotonic clock, including HTTP failures. Queue/load/lock/inference/
serialization/service-total are exported separately in Model Calls. Layout component
inference/load/lock timings are JSON in `model_timings`; there is only one parent
HTTP interval. Shared DET HTTP duration cannot be separated without inventing data.

API total starts at queue admission and ends after `json_safe`; it excludes auth,
image decoding, final HTTP JSON encoding and transfer. Model load measures the
manager's load duration, not its load-lock wait. Failed upstream collectors can
abort before producing a complete detection result: failed rows are not complete
performance samples.

Canonical `debug.timing` wins over nested `benchmark`; legacy aliases apply only
when a canonical key is absent. Explicit null stays null. Public summary metrics
and reasons in `debug.timing.benchmark` are copied from the same final summary.
Historical candidate durations without Call IDs cannot be reliably deduplicated.

- null: unknown/incomplete/not called/cached/not applicable; inspect reasons.
- 0: measured zero, including rounded sub-resolution timing and cached model load.
- missing: older producer; compatible aliases are allowed, estimates are not.
- absent model call: not proof of zero work; cache/no-call cannot always be distinguished.
- conflicting duplicate: reported unknown rather than silently accepted.

Standard and Text-Only share the same collector. Unused models remain null in
Text-Only. Model Calls retains file, run number, repeat count and actual mode.
Old column positions remain; new columns append. `timing_missing_reasons` is JSON;
metadata remains bool/int/string; durations are finite nonnegative numbers or blank.
Formula-like strings are escaped. No dependencies or model logic changed.

## Validation limits

Local checks cover aggregation, source-extracted detection output (not a running
backend), HTTP failure timing, fake-runtime API contracts, frontend calculations,
Excel round trips, TypeScript and Python syntax. Kaggle CPU/network behavior, real
model inference timings, real Standard/Text-Only sessions and post-deployment
benchmark files remain **Not Verified**.

Local results: Backend 37 tests, benchmark/export 16 tests, API contract/timing 9
tests plus 4 subtests, frontend timing 4 tests: all passed. Python AST syntax,
TypeScript `tsc --noEmit --incremental false`, and `git diff --check` passed.
Backend tests logged missing local PostgreSQL settings (default configuration),
and API tests emitted an existing Starlette/AnyIO deprecation warning. No database
connection or model inference was required by these tests.

## Changed files

Follow-up inspection of existing `output/user_flow_20261026_224912.xlsx` (read-only):
summary X (`image_siglip_runtime_ms`) was blank, AC (`image_siglip_wait_ms`) was
13387.98 ms. Model Calls contained `image_verification` with inference 13383.1 ms
and service total 13383.41 ms. Thus this sample does contain service inference
timing; the missing summary is a propagation/aggregation discrepancy, not evidence
of an unmeasured model. The exact deployed backend revision is not established by
the workbook. Current source aggregation tests pass; deployments still require
verification. AJ (`frontend_render_ms`) was blank and AK (`timing_missing_reasons`)
held JSON: no column shift in this workbook. A new contract regression test uses
the real API timing wrapper with stubbed model computation for both SigLIP routes;
it does not execute real inference or validate a live Kaggle service.

- `API_Service_Kaggle/app/main.py`
- `API_Service_Kaggle/app/timing.py`
- `API_Service_Kaggle/tests/test_contract.py`
- `API_Service_Kaggle/tests/test_timing.py`
- `project_backend/app/business/services.py`
- `project_backend/app/core/model_runtime_client.py`
- `project_backend/app/processing/detection_service.py`
- `project_backend/app/processing/model_timing.py`
- `project_backend/tests/test_detection_timing_output.py`
- `project_backend/tests/test_model_runtime_timing.py`
- `project_backend/tests/test_model_timing_summary.py`
- `project_frontend/src/app/page.tsx`
- `project_frontend/src/shared/browserTiming.ts`
- `project_frontend/tests/browserTiming.test.mjs`
- `benchmark/user_flow.py`
- `benchmark/test_user_flow.py`
- `benchmark/README.md`
- `benchmark/TIMING_AUDIT.md`
