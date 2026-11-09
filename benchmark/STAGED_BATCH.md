# Staged anchor verification comparison

Source review: 2026-11-10. Scheduling/winner rules checked against app/processing/staged_verification.py. Dated live/simulated results below remain historical observations, not newly repeated performance results.

Search keeps page-count/orientation/aspect prefilters. One next anchor from each
surviving template enters each stage, in stored page/anchor order. Text ROI OCR
uses Detection -> Recognition; Image ROI uses SigLIP. Any failed/error/missing
anchor result rejects that template. Completed templates are not scheduled again.
Every in-scope anchor is mandatory. Main-page templates retain main-page scope.
Winner: most anchors among complete passes, then ascending template_id. Weights
and final-confidence thresholds do not select the winner.

Standard runs the text/image batches concurrently within a stage. Strict runs
the text batch before the image batch within a stage. Both have identical gates
and tie-breaking. Composite batch keys encode template ID, anchor ID, page and
anchor index, and results restore original IDs. Missing batch results cannot
trigger single-image fallback. Prepublish enrichment does not rerun skipped
staged candidates. Extraction ROI after selection is unchanged.

## Running the comparison

From repository root, using Python with backend dependencies:

```powershell
python benchmark/staged_batch.py --repeats 3
python benchmark/staged_batch.py --live --manifest benchmark/output/staged_live_fixture.json --repeats 3 --output benchmark/output/staged_batch_live.json
```

Default transport is explicitly simulated, including a fixed per-request overhead
and per-ROI cost. It demonstrates early rejection versus an all-pass worst case;
its timings are not Gateway/model performance measurements.

Live mode calls the existing Gateway adapters. Manifest fields:

- `pages`: page number to final AdjustZone image path, relative to the manifest.
- `templates`: eligible (already prefiltered) template metadata, each with
  `verification_anchors` in stored order. Anchors use the existing backend schema
  (`id`, `page_number`, `data_type`, `roi`, `expected_text`,
  `field_name`, `display_label`, `required_for_verification`, `verification_weight`,
  and `image_category` for image anchors). Legacy `match_type`/`regex_pattern` fields are ignored; expected text is literal and uses normalized Levenshtein matching.
- `strategy`: standard or strict (default standard).
- Optional `ocr_paths`: text_detection/text_recognition, each with single/batch
  paths. Explicit fixture paths avoid reading the production settings database.
  Otherwise the current configured models/categories are read as in production.

Full Batch baseline crops/inferences every anchor up front with the original
batch adapters, then applies the **new** all-anchor/count/ID decision rule. This
isolates scheduling from the intentional decision-policy change. It does not
claim equivalence to the old optional/weighted winner policy.

Output records backend verification end-to-end wall time, reported model
inference runtime when available, Anchor ROI crop count (not text-line crops),
actual HTTP model-call count, stages and matching agreement. Order alternates
between repetitions; model/service caches are not reset. Error runs fail the
comparison rather than being counted as successful no-matches.

## Measurements in this workspace, 2026-11-04

Live Gateway, synthetic text image, 8 templates x 3 anchors, standard mode.
One complete pair succeeded and selected `template-00` in both executions:

| Backend verification metric | Full Batch | Staged Batch |
|---|---:|---:|
| End-to-end wall time | 26,445.49 ms | 11,232.45 ms |
| Reported model inference runtime | unavailable/null | unavailable/null |
| Anchor ROI crops | 24 | 10 |
| Model HTTP calls | 2 | 6 |
| Stages | 1 | 3 |

These are single-run observations, not stable medians. A subsequent alternating
three-repeat run failed with a text-recognition read timeout and is excluded.
The successful JSON is `benchmark/output/staged_batch_live.json` (local ignored
artifact). Real SigLIP/multipage browser performance is not established by this
text-only Gateway sample; mixed text/image and multipage correctness are covered
by tests using real VerificationService rules with mocked inference.

Simulated 24-template x 4-anchor workload, median of 3 runs:

| Scenario / metric | Full Batch | Staged Batch |
|---|---:|---:|
| 23 templates fail first anchor: ROI crops | 96 | 27 |
| Model calls / stages | 3 / 1 | 6 / 4 |
| All templates pass: ROI crops | 96 | 96 |
| Model calls / stages | 3 / 1 | 6 / 4 |

Staging can save work but can increase request overhead; it is not always faster.

## Browser end-to-end

The comparison above excludes browser preparation/upload/rendering, prefilter,
and extraction. `benchmark/user_flow.py` continues to measure true user-flow
paint-readiness end-to-end and now appends `stage_count` and `roi_crop_count` to
Excel without shifting previous columns. Existing Model Calls sheets retain
actual service timing. After deploying the changed backend, run the same input
set against that deployment to measure complete user-flow performance. No full
browser end-to-end comparison was performed in this change.

Validation: backend staged tests cover early rejection, mixed models, optional
failure, missing batch output, unequal anchor counts, multiple pages, duplicate
anchor IDs across templates, deterministic selection, and no prepublish reruns.
Historical note: the earlier report observed a reset-delay test expecting 2000 ms. Current user_flow.py uses 5000 ms; this documentation review did not rerun that test or establish its present pass/fail status.
