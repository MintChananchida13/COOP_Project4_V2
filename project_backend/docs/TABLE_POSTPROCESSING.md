# Table post-processing optimization

The adapter now scans remote output once on the successful reconstruction path,
reuses that dictionary inventory for candidate selection, and avoids recursive
dictionary probing of primitive values inside raw numeric arrays. The dictionary
order, candidate order and reconstruction rules are unchanged.

The unused internal `raw_results` copy was removed from adapter results, including
the fallback path. The public OCR endpoint never forwarded this field. Public
text, confidence, table rows, HTML, structured cells and mapping warnings remain.
There are no new model calls and no changes to Table API parameters or Auto ROI
classification.

Timing appears under `table_debug.post_processing_timing` and is accumulated in
the OCR benchmark's `OCR Stages` sheet for `table_roi`:

- `remote_call_ms`: complete adapter remote call, including encoding/client work.
- `output_scan_ms`: scanning the returned output.
- `structure_rules_ms`: table reconstruction rules.
- `format_results_ms`: formatting rows and cells through debug construction.
- `total_ms`: local post-processing through debug construction.

These are nested measurements; do not add them to table_roi elapsed_ms. Fallback
results currently expose remote_call_ms but not the successful reconstruction
phase breakdown. HTTP wait remains separately available in OCR Model Calls.

A local simulated comparison (20 x 6 cells and a 600 x 600 RGB numeric array,
three repetitions) measured median reconstruction time 1698.71 ms before and
1199.82 ms after (about 1.42x). Text, confidence, HTML, rows, structured cells and
selected method were identical. This is not a replay of the deployed Demo PDFs;
the live improvement must be measured by rerunning the same OCR benchmark.

The existing report also routed Demo 2 to Table despite the document having no
table. That is a separate Auto ROI classification issue and is not changed here.

## Remote timing and OCR correctness audit

`remote_call_ms` wraps `_predict_table_model`, before reconstruction starts.
`http_request_ms` starts just before urlopen and ends after response read/decode.
Remote-minus-HTTP therefore includes encoding, request JSON, response JSON,
validation and client diagnostics. It cannot be labeled model inference or
reconstruction. In the reported sample, 17790.66 - 7803.54 = 9987.12 ms is
unattributed non-HTTP time until the additional phases are recorded.

Table field-size diagnostics previously called json.dumps over nested raw output
at several levels. They now record shallow type/count metadata without computing
serialized sizes (`bytes=null`, `field_size_measurement=metadata_only`). Other
runtime kinds keep the original sizing. Actual payload/response byte counts remain
measured. The model timing summary exports encode/parse/diagnostic/client spans;
read/decode, queue and inference are nested spans and must not be summed with HTTP.
Missing Gateway inference/queue stays unknown. Calls are deduplicated by call ID.

A synthetic replay of an identical 3,961,305-byte response, with mocked HTTP and
600 x 600 RGB raw pixels (median of 3), measured client time 266.12 -> 69.81 ms and
field-sizing overhead 194.68 -> 0.02 ms. Returned output was identical and call
count remained one. This does not establish the exact deployed cause of all
9987.12 ms; rerun the real document to measure the new phase fields.

`raw_result_count` counts nested dictionaries, not OCR detections. The additional
`raw_result_count_kind` makes that explicit. `recognized_item_count` is rec_texts
length. A regression fixture demonstrates 43 dictionaries and 33 OCR tokens with
all 33 retained. Their difference alone does not imply ten dropped OCR tokens.

Nested rec_polys ([[x,y], ...]) are now accepted by bbox conversion; individual
invalid/missing rec_boxes can fall back to rec_polys at the same index, preserving
text/score alignment. Table formatting preserves literal normalized text instead
of applying generic noise cleanup to rows, cells or Markdown. Non-table OCR is
unchanged. No extra detection/recognition or runtime calls are introduced.

`table_debug.ocr_mapping_audit` records each source token's index/text/score/bbox
and its assigned row/column, including unassigned tokens. Compare this with each
cell's existing recognizedItems to distinguish raw recognition errors from cell
mapping/formatting. Prefixes such as `9เอกสารข้อความ`, `99Oทดสอบ` and suffixes such
as `นตารางข้อมูลa` are never guessed away. If already present in a raw OCR token,
their spelling cannot be corrected reliably by geometry-only reconstruction.
The latest deployed raw result was unavailable during this change, so its exact
token-level cause remains unconfirmed. Trace data is returned with the existing
debug object, not added to timing logs/workbooks.
