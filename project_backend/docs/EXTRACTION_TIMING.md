# OCR extraction timing

Source review: 2026-11-10. Timing boundaries checked against app/processing/extraction_timing.py, main.py and the Frontend client. No inference or latency measurements were performed in this review.

Run the normal selected-ROI OCR flow. No extra model request is made for timing.
The completed `/api/ai/jobs/{job_id}` response contains
`result.extraction_timing`; synchronous `/api/ai/process` responses contain it
directly. It is persisted with the existing job result, with no database migration.

- `processing_ms`: processing after loading the job request, through response construction.
- `stages`: cumulative elapsed milliseconds and call counts for image decode,
  rectangular/polygon crop, crop save, fixed-text batch, flexible ROI, table ROI,
  image ROI, and full-page detection/recognition when applicable.
- `fixed_text_counts`: detection ROI/chunk counts and recognition image counts
  from the existing OCR adapter. Null means not reported, not zero.
- `model_calls`: actual calls, endpoint, HTTP wait and available Gateway timing.
- `inferences`: inference milliseconds by model kind. Missing measurements remain null.
- `model_call_count`, `model_wait_union_ms`, `model_timing_missing_reason`:
  existing model timing summary semantics.

Stages are measured around existing functions; they can include preparation and
model time, so do not add them to model timings. Multiple table/flexible regions
accumulate in their stage. `crop_save` measures the save helper; files are only
written if the existing SAVE_CROPPED_ROIS setting enables saving.

Backend logs also contain `job_create_ms` (payload serialization/database create)
and `result_store_ms` (result serialization/database completion update). The latter
is logged after completion and is not written back with a second database update.

Browser DevTools Console shows `[OCR timing] extraction completed` per processed
page: JSON encoding, create-request round trip, backend job-create time, poll count,
poll-request time, accumulated poll sleep, client total and backend timing.
The page loop is unchanged; OCR job polling waits 500 ms between requests.

`poll_wait_ms` includes sleeping while OCR is still running; it is NOT a pure
post-completion delay. `create_request_ms` includes upload, network and backend
work; it is NOT pure network latency. Browser and backend totals have different
boundaries and must not be added together. Processing time excludes initial job
queue/load and final database persistence. These measurements do not separately
measure browser rendering time. No OCR text, image bytes or API keys are added
to timing logs.

For comparison, use the same image, selected fields and model settings. Compare
one-page fixed text separately from documents containing tables/flexible regions.
Actual latency requires running against the deployed Gateway; unit tests mock
inference and verify results/call counts without making model calls.
