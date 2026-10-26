# SigLIP Single Prompt V2.4 implementation

## Decision flow

The checkpoint, all eight Positive and eight Negative single prompts, model
forward pass, crops, and category order are unchanged. The active version is
`siglip-single-prompt-v2.4`.

1. Validate 16 finite, ordered raw logits.
2. Rank Positive categories by raw logit; find the highest Negative logit.
3. Reject as `other` when Negative wins or ties the highest Positive.
4. Apply explicitly configured relative-confidence and relative-margin gates,
   in that order. Both are disabled by default: no validated new thresholds
   were provided.
5. Accept only the original Positive Rank 1, otherwise return `other` with a
   decision reason. Never substitute another Positive category.

| Gate | Units / comparison | Default | Rejection reason |
| --- | --- | --- | --- |
| Negative competition | Positive top logit > Negative top logit | Enabled | `negative_category_won` / `negative_category_tied` |
| Relative confidence | Positive softmax top share >= configured minimum | Disabled | `confidence_gate_failed` |
| Relative margin | Positive softmax top share minus second share >= configured minimum | Disabled | `margin_gate_failed` |

Disabled gates report `not_applied`, not `true`. An enabled gate skipped because
an earlier gate failed reports `not_evaluated`. Comparisons use unrounded values.
Positive ties preserve canonical category order; with the margin gate disabled
they can be accepted if Negative loses. Positive/Negative ties reject explicitly.

The active policy removes the Garuda-to-Emblem override below 2.0, the
Garuda/Negative gap below 2.0 rejection, and the Emblem raw-logit floor -6.3.
There are no category-specific exceptions. The original implementation remains
available only through the explicitly named V2.3 comparison helper/tests.

## Score definitions

For raw logit `z`, raw pair score is `sigmoid(z)` in [0,1]. It remains display
metadata, not a new acceptance gate. Positive relative share is
`exp(z_i - max_positive) / sum_positive(exp(z_j - max_positive))` in [0,1].
It is **not a calibrated confidence probability**. UI percentages multiply it
by 100. Raw ranking and UI share ranking are monotonic for the same vector.

`positive_negative_margin` and `positive_top1_top2_margin` are raw-logit
differences. Optional margin gating instead uses `positive_relative_margin`,
the dimensionless difference between the two Positive softmax shares. These
units are not interchangeable. `raw_top1/2` refer to Positive ranks; Negative
ranking is reported separately. Legacy `siglip_score_margin` retains its
target-relative raw-logit meaning and is not silently used as a relative gate.

Backend environment variables for opt-in, independently validated thresholds:

- `SIGLIP_V24_CONFIDENCE_MIN_RELATIVE_SCORE`
- `SIGLIP_V24_MARGIN_MIN_RELATIVE_SCORE`

Leave both unset/blank for defaults. Explicit zero is not interpreted as missing.
Finite values in [0,1] are required. No new threshold is enabled by this change.
Old category `match_threshold` / `margin_threshold` fields remain compatible
metadata, not inputs to these new gates.

## API, Backend and UI

`API_Service_Kaggle/app/siglip_policy.py` is the canonical model-free policy.
Backend loads that same file via `app/model_runtime/siglip_v24.py`, supplies
the explicit optional `decision_config` in single/batch requests, and validates
the returned decision against the same policy/vector/config. This validation
does not rerun the model or apply a second set of decision rules. The fingerprint
identifies vector/order/config consistency; it is not a security signature.

For the project's canonical 16 categories, API results preserve logits/device
and add a `decision` trace. Custom category callers retain the raw-only legacy
response. A raw-only older API response is evaluated with the shared V2.4
policy, never V2.3. An inconsistent provided trace fails closed with
`decision_contract_error` rather than selecting another prediction.

Backend propagates the trace as `siglip_decision` and additive diagnostic fields,
including all requested ranking, gate, reason, version and final-category fields.
The verification editor distinguishes Raw Ranking, Final Prediction, Decision
Reason and Verification PASS/FAIL. It labels relative share as not probability
and displays disabled gates explicitly. Legacy responses without a trace remain
renderable. UI does not recalculate prediction from displayed percentages.

Expected category is compared against the final prediction. Binary evidence,
field/image-anchor score aggregation, required anchors, retrieval floors,
verification weights and final template decisions are otherwise unchanged.
A passing SigLIP classification alone does not guarantee template acceptance.

## Synthetic examples, not actual query replay

| Input | V2.4 result with default gates |
| --- | --- |
| Garuda 8.1, Emblem 6.5818, all others -30 | `thai_garuda`; raw margin 1.5182 no longer substitutes Emblem |
| Portrait -8.1219, Barcode -9.7214, best Negative -8 | `other`: `negative_category_won` |
| Same Portrait positives, all Negatives -20 | `portrait`: `positive_rank1_accepted` |

The Garuda fixture reproduces approximately 0.8203 relative share and 0.9997
sigmoid. The Portrait fixture demonstrates Negative competition, not a replay
of the incomplete supplied 16-logit vector or an assertion about its exact
0.6463 share. Low absolute sigmoid alone is not a configured default gate.

## Changes and validation

- API: `app/siglip_policy.py`, `app/main.py`, `tests/test_contract.py`.
- Backend: `app/model_runtime/siglip_v24.py`,
  `app/model_runtime/siglip_image_verification_adapter.py`,
  `app/core/model_runtime_client.py`, `app/business/services.py`.
- Backend tests/docs: `tests/test_siglip_v24.py`,
  `tests/test_siglip_v23_audit.py`, `SIGLIP_V23_AUDIT.md`, this report.
- Frontend: `src/admin/adminApi.ts`,
  `src/admin/workspace/WorkspaceTemplateEditorV2.tsx`,
  `src/shared/SiglipDecisionDetails.tsx`, `tests/siglipV24.test.cjs`.

Local automated validation: Backend unittest suite 68 passed; API contract and
timing tests 11 passed plus 4 subtests; Frontend mapping/SSR diagnostic tests
2 passed; TypeScript no-emit check passed. Tests use mock logits/model forwards,
not real inference. Coverage includes all Positive/Negative categories, ties,
gate boundaries, legacy comparison, single/batch, trace consistency, image
anchor/final-template decisions and UI diagnostics.

Deploy from a complete repository checkout: Backend's shared-policy loader
requires the sibling API directory. Restart both API and Backend and rebuild
the Frontend after updating code. No dependency changes or reinstall are needed.

**Not Verified:** real Kaggle inference, actual crops/queries, browser behavior,
benchmark, accuracy, false-positive/negative rates, threshold calibration and
resource usage. Negative competition preserves explicit rejection but cannot
guarantee rejection of every irrelevant image. Disabling the former absolute
and category-specific guards changes rejection behavior; evaluate a labeled
held-out set (all categories and unknowns) before deployment. No claim that
V2.4 is more accurate than V2.3 is supported yet. No commit/push performed.
