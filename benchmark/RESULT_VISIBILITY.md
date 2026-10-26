# Detection result visibility readiness

## Meaning and boundaries

**Paint-ready estimate is NOT Actual Paint.** DOM commit, successful image decode
and animation-frame callbacks do not prove that pixels were presented or that a
person saw them. The clock intervals are measured; the final endpoint is a
readiness proxy. No estimated duration is substituted for an unavailable reading.

All timestamps use browser `performance.now()`. "Completed Response" below means
the completed poll's fetch resolved in frontend code (headers available), NOT a
server-side timestamp or proof the whole response body has arrived.

| Metric | Start -> end | Interpretation |
| --- | --- | --- |
| `frontend_response_to_commit_ms` | Completed Response -> post-DOM-commit layout-effect checkpoint | Existing measured interval, unchanged |
| `frontend_preview_ready_ms` | Completed Response -> observation of required image load/decode completion | Actual readiness observation; not decode CPU time or paint; null if no preview is required |
| `frontend_ui_ready_ms` | Completed Response -> second frame with result summary present, CSS-visible/in viewport and overlay absent | UI-only paint-ready estimate; does not wait for preview decoding |
| `frontend_render_ms` | Post-DOM-commit checkpoint -> final readiness checkpoint | Paint-ready estimate including image wait/frames, NOT React render CPU time or Actual Paint |
| `frontend_result_visible_ms` | Completed Response -> final readiness checkpoint | Combined UI+preview paint-ready estimate, not guaranteed visibility |
| `end_to_end_ms` | Boundary confirmation handler starts -> final readiness checkpoint | End-to-end to combined paint readiness; excludes console logging, Excel save and reset |
| `frontend_commit_end_to_end_ms` | Boundary confirmation -> commit checkpoint | Preserves the previous commit-boundary end-to-end measurement |
| `frontend_flow_complete_ms` | Boundary confirmation -> final handler logging request | Preserves the original pre-commit end-to-end boundary |

JSON, mapping, preparation, backend/model metrics remain available. These are
nested/overlapping intervals: do not sum them to reconstruct end-to-end time.
Missing clocks or unsuccessful readiness produce null with reasons. Zero is a
valid measured/rounded duration. No required preview means preview timing is null
with `preview_not_required`, not zero and not an indefinite wait.

## Actual frontend flow

1. Existing `adminApi.ts::detectTemplateDev` parses the completed polling response
   and maps it. Existing match/no-match processing and state updates are unchanged.
2. `page.tsx` assigns a monotonically increasing frontend Run Token at confirmation.
   `WorkspaceCustomEditor` renders that token on its existing root, marks the main
   workspace image and whether it is required. No wrapper or UI layout is added.
3. Match and no-match result summary elements are explicitly marked. The page's
   existing layout-effect completion signal runs after this result subtree has
   committed and the template-decision state is closed.
4. `resultVisibility.ts::observeResultVisibility` starts a bounded observer. It
   checks the current Run Token, DOM attachment, summary/root and main-preview
   viewport intersection, CSS/ancestor visibility and document foreground state.
   It also requires the detection loading overlay to be absent.
5. A required image must have `complete=true` and `naturalWidth>0`; `decode()` is
   awaited when available. An image node/source change resets its readiness; an
   old image's delayed decode cannot complete a new image/run. No preview means
   no image/decode wait. Only the main current-page image is required, not every
   thumbnail or page. Actual rendering of overlays/ROIs is not separately profiled.
6. UI readiness requires two eligible animation frames; combined readiness requires
   two consecutive eligible frames after preview readiness. This observes rendering
   opportunities, not a fixed sleep and not a guarantee of presentation.
7. The final console table includes status, reasons, frontend and benchmark Run
   Tokens and `frontend_timing_kind = paint-ready estimate; not actual paint`.
   Timers/frame handles are cleaned up on completion, timeout or cancellation.
   New Document, another run or unmount aborts the old observer. Uncancellable
   decode promises have guarded continuations and cannot emit old-run timing.

### PDF handling verified in source

Current `UploadZone.tsx` rasterizes PDFs before detection and awaits
`page.render(...).promise` before producing each page image. The post-detection
workspace is an `<img>`, not an embedded PDF viewer/canvas. Thus the real workspace
uses image load/decode readiness even for PDFs; no nonexistent PDF canvas promise
is assumed. The readiness helper also accepts an explicit PDF rendering promise
for a canvas producer; isolated tests exercise this path, not a live PDF viewer.

## Sequential benchmark and errors

Upload -> Confirm -> correlated Job completed/failed -> result DOM commit ->
visibility READY or explicit visibility error -> capture reads drained -> collect
timings -> workbook save/close -> New Document -> next run.

- `READY` plus an actually completed Job and boolean `matched` can PASS. No-match
  is still successful detection. Detection accuracy/business rules are unchanged.
- `PREVIEW_ERROR` and `VISIBILITY_TIMEOUT` are distinct from `DETECTION_FAILED`.
  A visibility failure retains matched/query/mode/backend/model evidence, but
  combined readiness and end-to-end-to-ready metrics stay null.
- `VISIBILITY_ERROR` reports an instrumentation/DOM-readiness error.
- An independent 30-second visibility timeout avoids indefinite waiting when
  frames stop (e.g. background tab). Browser timer throttling can delay timeout
  delivery. The benchmark also retains its overall `--timeout-seconds` deadline.
- No terminal readiness table before the benchmark deadline stops the batch
  without resetting/uploading again. An explicit visibility error is saved as FAIL
  before an otherwise drained terminal run can reset. Failed Excel save blocks
  progress. The cosmetic reset pause does not establish readiness.
- Benchmark Run Token must match the active capture before accepting readiness;
  missing/mismatched tokens cannot PASS. Update/restart the frontend alongside
  the script: older frontend completion tables are not visibility evidence.

Both Excel sheets keep their old column positions and Model Calls mapping.
New summary durations append after previous frontend columns, followed by
`frontend_visibility_status`, `frontend_visibility_reason`, `frontend_timing_kind`,
`frontend_run_token`, and `benchmark_run_token`. Existing render/visible columns
are populated with explicitly labeled proxy measurements, not renamed/reordered.

## Limits and validation

This does not guarantee visibility through arbitrary occluding windows/dialogs,
overflow clipping, GPU/compositor timing, monitor output or human attention.
CSS visibility/viewport checks and known detection overlay are proxies. No initial
Paint Timing entry or double-rAF callback is presented as a paint acknowledgement.
No new dependencies, per-thumbnail observers, model calls, OCR or matching changes.
Style/layout reads and decode observation add instrumentation overhead; its real
size is **Not Verified** without browser profiling.

Automated checks use synthetic frames/clocks/images/DOM/PDF promises and source-
extracted callbacks, not a real browser or React renderer. They cover pending
preview, successful/failed decode, PDF render promise, overlay, no required preview,
replacement/run cancellation, hidden/offscreen DOM, timeout, benchmark identity,
explicit preview errors and Excel round trips. Real UI, PDF, browser paint, Kaggle
and benchmark/inference behavior remain **Not Verified**; no browser was opened.

Local checks passed: 18 frontend timing tests (including 10 readiness tests), 37
benchmark/capture/export tests, TypeScript, Python AST syntax and `git diff --check`.
Tests use the existing local dependencies only. No commit or push was performed.

Files changed for this visibility update:

- `project_frontend/src/shared/resultVisibility.ts` (new observer/metric helpers)
- `project_frontend/src/app/page.tsx` (run/commit/readiness orchestration)
- `project_frontend/src/shared/workspace/WorkspaceCustomEditor.tsx` (DOM markers)
- `project_frontend/src/user/components/MatchedTemplateWorkspaceZone.tsx` (summary marker)
- `project_frontend/tests/resultVisibility.test.mjs` (new isolated tests)
- `benchmark/user_flow.py` (readiness barrier, identity, error and export handling)
- `benchmark/test_job_tracking.py`
- `benchmark/test_user_flow.py`
- `benchmark/README.md`
- `benchmark/TIMING_AUDIT.md`
- `benchmark/FRONTEND_RENDERING_AUDIT.md`
- `benchmark/RESULT_VISIBILITY.md`
