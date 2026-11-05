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
