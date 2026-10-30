# Local Frontend user-flow benchmark

This standalone test tool drives the real `http://localhost:3000` user interface: automatically log in, choose one file, confirm document boundary, and wait for a found/no-match Template result. It saves one Excel row per run, waits **2 seconds**, clicks **เอกสารใหม่**, then uploads the next file/run in the same tab. It stops before Extraction ROI OCR. Verification (including any text/image model calls required by the active mode) remains part of the Template search being measured. The application and global verification settings are unchanged; detection jobs and Processing Logs may still be created by the normal flow.

## Setup on Windows

Each input file runs **twice consecutively** by default, with a separate Excel row for each run. Both sheets include `run_number`, and the summary includes `runs_per_file`. Excel is saved after every completed run. Use `--repeats 1` for a single run or another positive number to change the count. Model and application caches are not reset between runs, so do not assume the first run is cold or the second is the only warm run.

One browser tab stays open throughout the benchmark; no test tab is closed between runs. If the browser is closed manually, the script stops with a failure status and retains completed rows in Excel. If New Document cannot return the page to the upload screen, the benchmark stops instead of mixing documents.

Start the existing Frontend, Kaggle Backend/API Service, and tunnel as usual. Set the Frontend's existing `NEXT_PUBLIC_API_URL` to the verified tunnel URL. In a **separate local Python environment** install only benchmark dependencies:

```powershell
python -m venv benchmark/.venv
benchmark/.venv/Scripts/python.exe -m pip install -r benchmark/requirements.txt
benchmark/.venv/Scripts/python.exe -m playwright install chromium
```

Put images/PDFs into `benchmark/input/`. Supported extensions: PNG, JPG, JPEG, WEBP, PDF. The input folder, browser profile, and Excel output are Git-ignored. Each invocation logs in through the real login form using the requested demo defaults: email `user@ocr.com`, password `user123`. Optional `BENCHMARK_EMAIL` and `BENCHMARK_PASSWORD` environment variables override those defaults. Credentials are not printed or written to Excel.

```powershell
benchmark/.venv/Scripts/python.exe benchmark/user_flow.py --expected-mode standard
benchmark/.venv/Scripts/python.exe benchmark/user_flow.py --input benchmark/input --output benchmark/output/strict.xlsx --expected-mode strict
benchmark/.venv/Scripts/python.exe benchmark/user_flow.py --no-ocr
benchmark/.venv/Scripts/python.exe benchmark/user_flow.py --expected-mode standard --repeats 2
```

You may group files as `benchmark/input/standard/a.png`, `benchmark/input/strict/b.png`, etc. Folder names are **expected mode labels**, not a command to switch modes. The User page has no per-run verification-mode selector; the Backend setting is global. For safety this tool never changes it. If the observed mode differs, the row is `SKIP` with a reason; run each group when that mode is active, or use a separate test Backend/database for fully automatic cross-mode comparison. Do not use the Admin global-setting endpoint on a shared system just for benchmarking.

The Excel file is saved after every run, before the two-second pause and New Document action. `matched=true/false` distinguishes found/no-match; both are successful completed searches (`PASS`), not accuracy assertions. Existing OCR columns remain for compatibility: `ocr_status=NOT_RUN`, `ocr_wall_ms` blank. `--no-ocr` is retained as a compatibility flag; this tool always measures Template search only. `end_to_end_ms` comes from the Browser's detection flow and excludes the pause/reset action. A run can take many minutes on Kaggle CPU; increase `--timeout-seconds` if necessary.

If Excel locks the output file, the script saves to a unique `*_recovery_XXXXXXXX.xlsx`
and uses that path for subsequent runs. The console reports the actual path; the
locked file is not updated. Prefer opening results after the benchmark finishes.
If Excel output cannot be written at all, processing stops without retrying the
same failure in cleanup. Completed rows (including Model Calls) are backed up to
`ocr_benchmark_recovery_*.json` in the system temporary directory when writable;
the console reports the recovery path or explicitly reports recovery failure.

`backend_to_model_ms`, `frontend_to_backend_ms`, and `backend_to_frontend_ms` remain blank because network-only time is not measured. `frontend_render_ms` now measures commit-to-paint-readiness as an explicitly labeled **estimate**, not Actual Paint. For measured HTTP durations use model `*_wait_ms` or Model Calls `http_request_ms`; browser transport uses `uploadJobMs`/`jobPollingMs`. SigLIP runtime requires an actual recorded call and inference timing. Backend metrics use canonical Backend values; frontend metrics use Browser values. See [Timing audit](TIMING_AUDIT.md) and the current [Visibility contract](RESULT_VISIBILITY.md).

Run unit checks with `benchmark/.venv/Scripts/python.exe -m unittest discover -s benchmark -p 'test_*.py'`.
The Excel round-trip tests need the existing openpyxl dependency. Isolated JavaScript
capture tests use Node (skipped explicitly if unavailable), not a browser. Full
Frontend/Kaggle integration of the sequential capture changes is **Not Tested**.

### Sequential job capture

Current rendering instrumentation is documented in the [Visibility contract](RESULT_VISIBILITY.md).
`end_to_end_ms` ends at **Result Visibility Ready**, a paint-ready estimate based
on current-run result DOM, absent overlay, preview load/decode and rendering frames.
It does not prove Actual Paint. Prior endpoints remain `frontend_flow_complete_ms`
(handler) and `frontend_commit_end_to_end_ms` (commit). Readiness errors retain
Detection evidence but are not PASS; missing required preview readiness stays null.
Update/restart the Frontend together with the benchmark: old completion tables do
not satisfy the new readiness barrier. New columns append; Model Calls are unchanged.

The old `run_case.on_response` called synchronous Playwright `Response.json()`
inside a response event. It caught only JSON/type errors, not Playwright inspector
cache/navigation errors. It also accepted any detection response without linking
it to the current submission's Job ID. A console completion marker therefore did
not prove that the result had been captured; a stale response could replace it.

The benchmark now installs a fetch observer before upload/confirmation. It clones
only detection submission/poll responses inside the page and reads the clone's
JSON without CDP body retrieval or re-entrant Playwright callbacks. It does not
replace the frontend's response, add requests, perform inference, or change its
polling. This follows the existing `fetchWithAuth` implementation in
`project_frontend/src/admin/adminApi.ts::detectTemplateDev` and the Backend's
POST-202 / GET-processing/completed/failed contract.

Every run has its own capture token/object. The POST acknowledgement supplies the
authoritative Job ID; GET results are keyed by URL/response Job ID, including
responses arriving before the POST clone has finished parsing. Terminal states
cannot be overwritten by a late processing response. Previous-run callbacks write
only to the previous object. No Playwright response listener is installed.

Order: upload -> confirm -> terminal job AND frontend completion marker AND zero
outstanding capture reads -> collect result/timings -> save and close workbook ->
New Document -> next upload. The two-second reset pause remains cosmetic only;
it is not a response/capture completion barrier. Excel save errors block progress.
If capture or frontend completion cannot drain before the configured timeout,
the failure row is saved and the batch stops without resetting/uploading again.

`status` remains PASS/FAIL/SKIP for compatibility. Appended columns are `job_id`,
`error_type`, `job_status`, and JSON `capture_errors`; old columns do not move.
Error types distinguish RESPONSE_CAPTURE_ERROR, POLLING_TIMEOUT, DETECTION_FAILED,
and BENCHMARK_ERROR. Only a completed, correlated Job with boolean `matched` and
completed frontend flow can PASS; `matched=false` means a successful no-match
search. A later valid correlated terminal poll can recover from an earlier capture
error; warnings remain recorded. No terminal evidence means no PASS.

Timing still comes from frontend console measurements and the terminal Backend
result. Browser end-to-end excludes Excel save/reset. Fetch-clone observation has
some CPU/RAM instrumentation overhead; no synthetic duration or additional model
call is added. Model Calls and repeat metadata retain their existing mapping.
# Template Search timing and ground truth

The measured start is the confirmation click, before AdjustZone prepares images. The end is the frontend's observed result readiness: the result summary is visible, the loading overlay is gone, and the required current-page preview has loaded and decoded across animation frames. This is a paint-readiness estimate, not proof that every browser paint occurred or that off-screen pages have loaded. Error and timeout runs retain null for any unavailable readiness measurement.

Run `python user_flow.py --ground-truth expected.json` to record correctness. The optional JSON maps paths relative to the input directory to expected template IDs, or `null` for a document expected to have no match, for example `{"id-card.jpg":"tpl_example", "unknown.pdf":null}`. A missing entry produces null correctness. The benchmark never sets a verification mode automatically.

Each benchmark run tags detection and relevant subsequent Template bundle, Layout, and detection-preview requests with a trace ID without enabling admin diagnostic processing. `backend_search_ms` measures the detection job, including model waits. `backend_post_search_ms` is the union of traced post-search API intervals; `backend_total_active_ms` is the union of search and post-search intervals using the Backend's `time.perf_counter()` clock. These measure elapsed service intervals, including I/O and model waits, not CPU time. POST spans begin after the request body is received and end after the response is sent. A cache hit makes no request; no post-search request yields zero. Missing or unmatched request spans return null instead of a partial total. Span collection is process-local and needs the existing single-worker deployment. `template_search_ms` measures existing matching/retrieval instrumentation and its exact scope depends on mode. `result_processing_ms` measures the Backend's final user-response preparation; frontend preparation of the selected result is a separate metric. `frontend_render_ms` is flow completion to React commit; `result_ready_ms` is commit to observed readiness. These two intervals do not overlap.

`request_round_trip_ms` includes the upload POST, Backend request processing and response; it is not network-only. `request_network_ms` remains null because one-way network cannot be isolated reliably. Polling wait includes scheduled delays and nonterminal polls, while `result_fetch_ms` is the terminal polling request. They overlap backend work and must not be summed with backend runtime. Per-model queue/inference/total are service timings for actual calls; totals across calls are aggregate work, not necessarily wall time. Layout includes calls during search and Flexible/whole-page ROI preparation, deduplicated by call ID; a cache hit does not create a model call. Shared model calls or missing runtime measurements are null. When later Layout calls exist, overall queue is null rather than adding potentially overlapping timings; `search_model_queue_wait_ms` remains the search-job-only measure. End-to-end is always measured directly, never by summing other metrics.
