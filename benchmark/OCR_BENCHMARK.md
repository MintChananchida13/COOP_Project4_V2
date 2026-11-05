# Separate OCR benchmark report

This runner writes a new `ocr_flow_YYYYMMDD_HHMMSS.xlsx` workbook. It does not
change or write the Template-search benchmark or its reports.

It logs in, uploads each document, confirms AdjustZone and finds a Template as
setup. Then it clicks **อ่านข้อมูลที่เลือก** and measures selected-ROI OCR through
the result editor. Template-search time is excluded. Enabled/default-selected
ROIs are used unchanged when matched. When no Template matches, the runner clicks
**ตีกรอบ ROI อัตโนมัติ** once (the existing action analyzes all pages), waits for
workspace ROIs, then clicks **อ่านข้อมูลที่เลือก**. Auto ROI preparation is separate
from OCR timing. No regions gives `SKIP_NO_ROI`; a failed Auto ROI gives
`AUTO_ROI_FAILED` and stops the runner.

## Run from the repository root (PowerShell)

```powershell
.\benchmark\.venv\Scripts\python.exe benchmark/ocr_flow.py --repeats 2
```

For the deployed Frontend, deploy the OCR timing changes to Backend and Frontend
first, then run:

```powershell
.\benchmark\.venv\Scripts\python.exe benchmark/ocr_flow.py --frontend https://coop-project4-v2.vercel.app --repeats 2
```

Options: `--input DIRECTORY`, `--output FILE.xlsx`, `--timeout-seconds 1200`.
Default input is `benchmark/input`. Like the existing runner, input may be flat
or use its recognized mode folders. Folder names do not change active settings.
Credentials use `BENCHMARK_EMAIL`/`BENCHMARK_PASSWORD`, with the existing local
benchmark defaults. A separate `benchmark/ocr-browser-profile` is used; do not
run multiple OCR benchmark processes against that profile simultaneously.

The existing benchmark environment needs Playwright, its Chromium browser and
openpyxl (`benchmark/requirements.txt`). No additional packages are required.

## Workbook

| Sheet | Scope |
| --- | --- |
| OCR Runs | One row per file/repetition: OCR click-to-editor wall time, result count, status |
| OCR Pages | One row per actual OCR job: ROI types/counts, client wait, backend processing and inference |
| OCR Stages | Decode, Crop, Crop Save, Fix Text Batch, Flexible, Table, Image durations and counts |
| OCR Model Calls | Only actual calls made during Extraction; no Template-search calls |
| Metric Notes | Definitions and interpretation limits |

`roi_source` identifies `template` or `auto_roi`. For the Auto ROI path,
`auto_roi_setup_ms`, `auto_roi_page_count` and `auto_roi_region_count` describe
preparation. Its layout calls are excluded from OCR Model Calls and OCR wall time.

After the Table optimization, `table_roi` rows in OCR Stages also include
`remote_call_ms`, `output_scan_ms`, `structure_rules_ms`, `format_results_ms` and
`total_ms` when reported. They are nested measurements, not additional elapsed
time. They separate adapter/Gateway work from local table reconstruction.
`remote_non_http_ms`, `image_encode_ms`, `json_serialize_ms`, `json_parse_ms`,
`response_diagnostics_ms` and `ocr_audit_ms` further identify client overhead.
HTTP read/decode time is already inside HTTP round-trip time. Queue/inference
metrics are Gateway-reported subsets, not additive to HTTP wait. Blank Gateway
values mean unavailable, not zero. Source OCR-to-cell traces remain in the job's
`extracted_data[].table_debug.ocr_mapping_audit`; the timing workbook deliberately
does not export OCR content.

Timing fields remain blank when not measured; a missing Gateway inference is not
zero. `TIMING_INCOMPLETE` means OCR completed but the Frontend/Backend extraction
capture is absent; update both services. A missing inference alone is documented
by `model_timing_missing_reason`. This report measures timing, not OCR accuracy.

`poll_wait_ms` includes sleeps while OCR is running, not just delay after it
finishes. Stage time includes nested model work: do not add stage totals and
model totals. The runner checks editor visibility every 100 ms, so click-to-editor
time is an observed wall time, not a browser render measurement. Final database
result-store time remains in Backend logs, not in this workbook.

Results are saved after each repetition. A failed/timed-out run stops the runner
to avoid contamination from a late job. If Excel locks the output, a new recovery
filename is used. No OCR text or images are exported.

## Tests (no Model API calls)

```powershell
cd benchmark
.\.venv\Scripts\python.exe -m unittest test_ocr_flow
node test_ocr_capture.cjs
```

The tests cover multiple pages, job mapping, partial failures, missing telemetry,
Excel export, locked-file recovery, OCR-only capture and isolation of late results.

## Offline Table scan comparison

From the repository root:

```powershell
python benchmark/table_scan_perf.py --repeats 5
```

This separate tool compares the original and optimized Output Scan against
identical JSON. It writes `benchmark/output/table_scan_performance.json`, makes
no model/network calls, and checks the full Table response except timing fields.
JSON parsing uses the same standard-library decoder on both sides. Use
`--response-json PATH --width WIDTH --height HEIGHT` to replay saved unwrapped
Table API data with the ROI's original dimensions. Its default data is synthetic;
it does not claim to reproduce deployed HTTP/queue/inference latency.
