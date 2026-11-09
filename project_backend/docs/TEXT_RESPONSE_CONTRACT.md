# DET / REC response normalization

Source review: 2026-11-10. Container validation checked against app/model_runtime/text_response_contract.py and current fixtures. No new live single/batch captures were collected; historical capture/validation statements below retain their original scope.

## Scope

Only Backend DET/REC adapters normalize OCR payload containers. Model selection,
configured endpoint paths, request bodies, inference, batch chunk size, crop/ROI
mapping, text cleanup and thresholds remain unchanged. No new Model API calls,
registries, dependencies or Frontend changes are introduced. Layout, Table and
SigLIP responses do not enter this normalization layer.

The implementation does not branch on model names, `version`, `variant` or
`contract_version`. Existing defaults are unchanged; an additional configured
model can use the same verified payload shapes regardless of its version label.

## Contract evidence

The supplied actual DET Single response is preserved in
`tests/fixtures/det_leaf_inference_v1.json`: `data.raw_output[0]` contains
`dt_polys` and `dt_scores`; `data.model_selection` identifies the actual detector,
while `meta.model` identifies the router and is not an OCR result.

The following additional paths were confirmed by the user from Gateway results:

| Operation | Confirmed result location |
| --- | --- |
| DET Batch, PP-OCRv5_server_det | `data.results[]`, each item has `regions[]` and `raw_predictions[]` |
| REC Single, th_PP-OCRv5_mobile_rec | `data.raw_output[0]` with `rec_text`, `rec_score` |
| REC Batch, th_PP-OCRv5_mobile_rec | `data.raw_output[]` with `rec_text`, `rec_score` |

Only one-image Batch samples were available. The full REC/Batch payloads were
described by their containers, not supplied as captured JSON fixtures. Structural
unit fixtures for those paths are test data, not additional live captures.

## Parsing rules

`app/model_runtime/text_response_contract.py` recognizes only payload containers
`data`, `result`, `results`, `raw_output`, `predictions`, `raw_predictions`, and
the legacy Paddle `res` wrapper. It does not search arbitrary dictionary values,
input images, `meta`, `timing` or `model_selection` for text or detection geometry.

- DET reads paired `dt_polys` and `dt_scores`, validates matching lengths,
  finite numeric scores/coordinates and polygon shape, and passes them to the
  existing DET geometry/filtering code. `raw_predictions` is the payload in the
  confirmed DET Batch item; parallel `regions` metadata is not counted again.
- REC reads one `rec_text` / `rec_score` pair. The old adapter's explicit
  `text`/`label` and `confidence`/`score`/`prob` aliases remain supported at the
  result leaf, with canonical fields taking precedence. It no longer concatenates
  metadata labels into OCR text or averages unrelated nested scores.
- Runtime model metadata is propagated to the internal result. The HTTP client
  now preserves the actual detector model name just as it already did for REC.
- A single-image response must resolve to exactly one image result.
- Batch parsing preserves API array order and per-image container boundaries.
  The number of returned image results must equal the number of input images.
  It never flattens several predictions from one item into another image's slot,
  truncates extra results, inserts missing results or guesses index semantics.
- Ambiguous simultaneous payload wrappers, missing fields, malformed scores,
  null items and count mismatches raise a contract error through the existing
  `LayoutAnalysisUnavailableError` / `PaddleThaiOcrUnavailableError` interfaces.
  No single-image retry is introduced by the parser. Existing upstream fallback
  policies are unchanged and continue to receive an explicit error.

Explicit empty leaf fields (`dt_polys=[]`, `dt_scores=[]`; or `rec_text=""`
with an actual numeric `rec_score`) are supported. A missing result or an empty
outer output list for a nonempty image request is not silently converted into
such a leaf. The actual new Gateway's empty-image wire behavior remains unverified.

## Compatibility and verification limits

The internal DET output still has `engine`, `model`, `image_width`, `image_height`
and `regions`. REC output still has `text`, `confidence`, `engine`, `model`,
`raw_text`, `normalized_text` and the existing diagnostic fields. Text cleanup,
reading order, ROI mapping and batch boundaries are not changed.

Unit tests parameterize version metadata (`v3`, `v4`, `v5`, `v6` and a custom
label) to prove the parser has no version whitelist; this does not claim that
all those external models have been invoked or provisioned in this deployment.

Tests cover the supplied DET fixture, confirmed container paths, legacy wrappers,
explicit empty leaves, malformed/ambiguous responses, model identity, positional
mapping in the existing internal multi-image contract, unchanged 60-item REC
chunking, no parser retries, and Layout/Table/SigLIP isolation. HTTP client calls
are mocked; no Model API is contacted.

Before asserting end-to-end multi-image compatibility for each Gateway version,
capture a real Batch with at least two distinct images and a valid empty middle
result, then verify input/result ordering and per-image boundaries. The Backend
preserves the supplied array order; it cannot prove that an unverified Gateway
itself returned that order. Unknown structures fail explicitly rather than being
guessed from model/version names.

Run:

```powershell
cd project_backend
python -m unittest discover -s tests -p "test_text_response_contract.py"
python -m unittest discover -s tests -p "test_ocr_compact_response.py"
python -m unittest discover -s tests
```
