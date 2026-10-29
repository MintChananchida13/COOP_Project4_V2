# Template search process audit

Date: 2026-10-29 (Asia/Bangkok).

This is a source review and isolated automated-test audit. It is not a live
browser/model benchmark. No live settings were changed and no documents were
submitted to model services. Timings, real document accuracy and production
behavior remain unverified.

## Branch coverage in the source review

| Case | Path and assessment |
| --- | --- |
| standard / strict | Layout retrieval and anchor verification remain. User detection skips unused extraction tests; Admin keeps diagnostic extraction tests. |
| layout_only | Layout retrieval and decision; no verification anchors loaded. Extraction ROI metadata remains, but extraction-test OCR is skipped. |
| text_only, global batch | Loads template anchors, batches text OCR and evaluates candidates. Upfront OCR across templates is a batch strategy tradeoff, not automatically redundant work. |
| text_only, other batch modes | Delegates to the shared anchor-strategy path. Removed the unused anchor load before delegation. |
| text_image / image_only | Shared anchor-strategy path with the corresponding anchor types. Extraction tests are not run in the candidate builder for this path. |
| found template, fixed ROI | Prepares the chosen processing image and maps field ratios. Changed image loading to reference only pages used by extraction fields. |
| found template, no extraction fields | No page-image loading is now required merely to return an empty ROI list. |
| flexible ROI | Crops each flexible boundary and submits layout analysis sequentially. This generates the child ROIs used by the workspace. Do not remove without replacing that behavior. |
| all-pages documents | Uses per-page matching and existing candidate/page aggregation. No matching decision changes were made. |
| main-page documents | Maps the main template page to the matched query page and prepares extra-page auto ROIs. Existing fallback to whole-page layout analysis remains. |
| no template match | User flow returns to Custom OCR; it does not fetch a template bundle or prepare template ROIs. No processing image preview is returned by the user response filter. |
| missing image / empty ROI | Existing terminal loading and fallback behavior remains. |
| failed image load | Field mapping retains the existing default-height coordinate fallback; auto mapping skips unavailable images. |
| API/model failure / maintenance response | Existing error handling remains. Late image commit is suppressed when the maintenance-error handler has reset the workspace. |
| reset, logout, unmount or replacement during a pending request | Detection POST/polling and preparation layout calls now accept a separate AbortSignal. Stage completion and terminal state writes check the active run. Processing-log saves use no detection signal and retain their captured run ID. |
| PDF versus image upload | PDF detection still uses the original PDF when available; image uploads use confirmed page images. PDF conversion, normalization and crop behavior were not changed. |

## Changes made in this audit

1. `templateFieldsToWorkspaceRois` loads only distinct pages referenced by
   non-verification fields. It preserves field ordering, main-page remapping,
   coordinate scaling and failure fallback.
2. `buildDetectionAutoRois` loads only pages containing supplied regions. Empty
   region lists still return no ROIs and allow the existing fallback to run.
3. `_detect_template_text_only` delegates non-global batches before loading
   unused text-only anchors. Global-batch processing is unchanged.

## Remaining confirmed opportunities

The three previously confirmed unnecessary User-path costs are now addressed:

- User detection skips `_run_extraction_test` work but retains anchor OCR and
  image verification required to decide the match. Admin diagnostics retain
  their original behavior by default.
- A request-local `ContextVar` policy prevents diagnostic base64 encoding,
  preview-only text crops and debug-image copies. Only a selected, passing,
  changed page is encoded for the User workspace. Finalization runs before
  query-artifact cleanup in all six modes. The scope resets on success/error
  and is isolated across concurrent User/Admin requests.
- User clients request `candidateFormat=pool-v1`; candidate variants are interned
  without merging page-specific and aggregate scores. The frontend resolves and
  maps each record once, restoring the prior UI/log object contract. Legacy
  clients receive the legacy shape and new clients accept either shape.

The following are remaining functional work / separate review areas:

- **Repeated PDF boundary detection:** PDF preparation and some post-match paths
  both detect physical boundaries on the original page. Post-match processing
  expands the crop to retain layout content, so removing that stage outright is
  unsafe. Reusing the raw boundary result needs a separate review.
- **Flexible ROI calls are sequential:** they are functional work, not redundant
  renders. Batching or bounded concurrency requires checking model capacity,
  error handling and deterministic field numbering.
- **Backend job cancellation is separate:** aborting client requests does not
  stop an OCR/model job already accepted by the backend. Shared template-bundle
  cache requests may also finish; their stale results cannot commit UI state.

## Verification

- Frontend: 24 isolated tests passed across ROI page loading, template
  preparation/fallback, loading status, image/crop caching, cancellation of
  delay/in-flight polling, stale success/error suppression and in-flight logs.
- Backend: 9 isolated tests passed across Text Only dispatch and user response
  filtering.
- TypeScript `--noEmit`, Python compilation of the changed detection module and
  `git diff --check` passed.
- Tests use fixtures/stubs at DB/model/network boundaries. They do not establish
  end-to-end latency or accuracy for every document, batch mode or deployment.

## Next measurement

Measure one real run per relevant branch using the existing timing fields:
`canvasPreparationMs`, `roiMappingMs`, `flexibleRoiMs`, `detectionAutoRoiMs`,
`wholePageAutoRoiMs`, extraction-test timing and model calls. Separate backend
job duration, response encoding/parsing, preparation and visible UI time. Do not
infer that a long loading message represents React rendering time alone.

## Mode-specific anchor loading follow-up

All six modes now use an explicit verification-anchor policy: standard/strict
and text_image need text+image; text_only needs text; image_only needs image;
layout_only needs neither. Both the published cache and database query filter
before returning anchor records. Request-cache entries are keyed by anchor
types, avoiding reuse of a text-only result as an image-only result.

The DB fallback formerly loaded only text anchors for image-based anchor
strategies. It now selects the requested types and resolves image categories
when needed. This corrects cache/fallback inconsistency; inference/scoring
algorithms and thresholds were not changed. Layout Only skips extraction-test
OCR while preserving extraction fields needed by the subsequent workspace.

This policy concerns template-search inputs. It does not disable the user's
later OCR, flexible-region analysis, or shared startup cache used by other modes.

Follow-up verification: 8 loading-policy tests (including executable in-memory
SQL), Text Only dispatch, 2 timing-output tests, image timing scope, 8 user
response tests, 17 SigLIP v2.4 tests and 8 SigLIP v2.3 tests passed (45 total).
No live model or production PostgreSQL session was exercised.


## User-only diagnostic and transport follow-up

Regression checks passed: 53 Backend tests and 28 Frontend tests, including
concurrent User/Admin isolation, extraction-test skipping versus Admin retention,
lazy final-image encoding, candidate round-trip equality (ROI/verification/log
metadata), old/new transport compatibility, all-six-mode finalization before
cleanup, cancellation and existing SigLIP policies. TypeScript, Python compilation,
new-helper ESLint and whitespace validation passed. Live inference and real browser
end-to-end performance remain unverified. No matching formula, required-anchor
rule, threshold or user-triggered OCR behavior was changed.
