# Frontend response, commit and visibility audit

Historical commit-boundary audit: the current result-visibility instrumentation
and end-to-end boundary supersede this report's earlier null render/visible metrics.
See [Result Visibility](RESULT_VISIBILITY.md) for current definitions, error states,
paint-ready estimate limitations and preserved historical boundary measurements.

This is a source audit and isolated automated-test result. No browser, benchmark,
real inference, or Kaggle session was run. Runtime bottlenecks and paint remain
**Not Verified**. No model, OCR, matching, verification or backend logic changed.

## Actual flow

1. `project_frontend/src/admin/adminApi.ts::detectTemplateDev` submits a POST,
   parses its acknowledgement, then polls once per second using `fetchWithAuth`.
   A resolved fetch means response headers are available, NOT that the entire
   response body has arrived. `pollResponse.json()` awaits the body and parses it.
   Only the terminal completed response populates the new response measurements.
2. `mapDetectionDevResult` synchronously maps pages, candidates, best candidate and
   debug/timing fields. Its duration is measured independently. Processing polls
   do not accumulate into the terminal JSON/mapping metrics.
3. `project_frontend/src/app/page.tsx::handleBatchConfirm` receives the mapped
   result, builds the detection snapshot and queues React state updates. A no-match
   branch prepares the notice. A match continues with `fetchTemplateBundle`,
   `buildTemplateCanvasImages`, ROI mapping, flexible ROI resolution, and optional
   extra-page Auto ROI processing. It queues selected template, images, ROIs and
   classification state. `finally` closes the template-decision overlay.
4. `fetchTemplateBundle` uses a cache/pending-promise cache; a cache miss requests
   `/admin/templates/{templateId}`. Cached data is cloned. This is additional
   post-detection I/O, not backend detection runtime.
5. `buildTemplateCanvasImages` prepares pages with `Promise.all`.
   `imageUrlToCanvasSafeSrc` returns existing data/blob URLs directly, otherwise
   fetches the preview, reads a Blob and converts it to a data URL. Original PDF
   preview preparation precedes detection; post-detection extraction previews may
   still need to be loaded. Existing stage timers cover bundle, canvas and ROI work.
6. The studio renders `MatchedTemplateWorkspaceZone` for a match or `WorkspaceZone`
   with `NoTemplateDetectionCard` otherwise. Both use
   `shared/workspace/WorkspaceCustomEditor`. Its workspace `<img onLoad>` fits the
   viewport/reports image metrics. Thumbnail images and workspace image loading
   may continue after React has committed their DOM nodes.
7. A page `useLayoutEffect` consumes the pending final completion exactly once,
   after DOM mutation and when the decision overlay is closed. It records a
   monotonic checkpoint and queues the existing console logging in a microtask.
   No extra render/state update, polling request or animation-frame wait is added.
   This is a post-DOM-commit checkpoint, NOT paint, image decode completion, or
   proof that the page is foreground/visible.

## Measured boundaries

All clocks below are browser `performance.now()`. No server timestamp subtraction.

| Metric | Start -> end | Classification / limitation |
| --- | --- | --- |
| `frontend_response_json_ms` | Completed-poll fetch resolves -> its `json()` resolves | Actual wall duration; remaining body download + decode/parse + scheduling, NOT pure JSON CPU time |
| `frontend_response_processing_ms` | Entry -> exit of `mapDetectionDevResult` | Actual synchronous mapping duration, including debug/timing mapping |
| `frontend_result_preparation_ms` | Mapping exits -> final flow logging request | Actual elapsed preparation; includes additional API/image waiting, not CPU-only time |
| `frontend_state_to_commit_ms` | Final completion logging request -> page layout-effect checkpoint | Actual final queued-state-to-post-commit wall duration, not all renders or pure React CPU time |
| `frontend_response_to_commit_ms` | Completed-poll headers available -> layout-effect checkpoint | Actual enclosing browser duration, includes body read/mapping/preparation/commit |
| `frontend_flow_complete_ms` | Boundary-confirm handler begins -> final flow logging request | Actual former end-to-end boundary, preserved for comparisons |
| `end_to_end_ms` | Boundary-confirm handler begins -> layout-effect checkpoint | Actual end-to-end to DOM commit; changed from pre-commit handler boundary; NOT time to visible paint |
| `frontend_render_ms` | Not instrumented | null: React render CPU/paint not measured |
| `frontend_result_visible_ms` | Not instrumented | null: preview decode/paint/foreground visibility not confirmed |
| `uploadJobMs` | POST dispatch -> acknowledgement JSON read | Actual round trip including body parsing; not one-way network |
| `jobPollingMs` | Begin polling -> terminal response parsed | Actual includes polling sleeps, requests and backend work |
| `detectionJobMs` | `detectTemplateDev` call -> mapped return | Actual enclosing duration, overlaps transport/mapping |
| `backend_runtime_ms`, `total_runtime_ms` | Existing backend clocks/collector | Unchanged; must not be added to browser enclosing durations |

No new metric is an estimate. Unmeasurable values remain null, not zero. `0` is a
valid measured/rounded duration. Missing completed-response timestamps (including
failures/legacy non-job responses) stay unknown. Metrics are nested views, not
independent additive stages. JSON body wait is not network-only latency. Summed
polling + backend + preparation + E2E would double-count work.

## Bottleneck candidates, not measured findings

- Large terminal JSON with previews/debug/candidates: body transfer, parsing and
  synchronous mapping can be costly. New JSON and mapping timings separate these
  boundaries but cannot isolate network from parse CPU.
- Template cache misses and extraction-preview downloads delay matched results
  after detection. `templateBundleMs`, `canvasPreparationMs` and preparation timing
  reveal this; do not attribute it to model inference.
- Blob-to-data-URL conversion and repeated image decoding increase memory/GC;
  multiple page preparations run concurrently. Thumbnail/workspace image loads
  occur after DOM insertion, so DOM commit alone cannot certify preview readiness.
- ROI mapping, snapshot construction, canvas/image sizing and cloning cached
  bundles perform synchronous work. How much blocks the main thread is unverified.
- Many state updates exist, but source alone cannot establish redundant renders:
  React batching, child effects and memoization require a runtime profile.
- The benchmark fetch-clone observer and console tables add instrumentation CPU/
  memory overhead. No heavy observer, per-frame loop or production profiler was added.

For future opt-in profiling, React Profiler can measure render work (separate from
commit/paint); Long Task observation can reveal main-thread stalls. Preview load/
decode callbacks could add an explicitly named readiness checkpoint. Double rAF
is only a rendering opportunity proxy and must NOT be called actual paint. Generic
Paint Timing entries concern initial page paint, not this particular result update.

## Benchmark/export integration

The completion console table is now emitted after the layout-effect checkpoint.
The benchmark already waits for that table AND the correlated terminal Job AND
drained fetch-clone reads before saving/resetting. Thus Excel save/reset is outside
browser E2E. No additional model request or Model Call record is created.

New metrics are browser-authoritative and appended after ALL existing Excel
columns, preserving their positions. `frontend_render_ms` and the new visible
metric remain null with explicit reasons. Both User Flow Timing and Model Calls
retain their schemas/mapping; only summary columns are added. Historical files
still have the previous pre-commit E2E boundary: compare their duration with the new
`frontend_flow_complete_ms`, not blindly with the new commit-boundary E2E.

## Validation

Isolated frontend tests exercise actual extracted polling code with synthetic
responses/clocks, the extracted layout-effect callback with a synthetic lifecycle,
and pure duration calculations. They do not exercise a real React renderer/DOM.
Benchmark tests cover browser metric precedence, stable column positions, zero/
null handling, both-sheet Excel round trips and sequential capture/save barriers.
TypeScript and Python syntax are checked. Real React commit/image/paint behavior,
Standard/Text-Only UI sessions and performance overhead remain **Not Verified**.

Local results: 8 frontend timing tests and 34 benchmark/capture/export tests passed,
along with TypeScript `tsc --noEmit --incremental false`, Python AST syntax and
`git diff --check`. These checks include the existing both-sheet Excel round trips.

Files changed for this frontend instrumentation:

- `project_frontend/src/admin/adminApi.ts`
- `project_frontend/src/app/page.tsx`
- `project_frontend/src/shared/browserTiming.ts`
- `project_frontend/tests/browserTiming.test.mjs`
- `project_frontend/tests/frontendDetectionTiming.test.cjs`
- `benchmark/user_flow.py`
- `benchmark/test_user_flow.py`
- `benchmark/README.md`
- `benchmark/TIMING_AUDIT.md`
- `benchmark/FRONTEND_RENDERING_AUDIT.md`
