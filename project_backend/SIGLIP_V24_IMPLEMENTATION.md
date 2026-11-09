# SigLIP Single Prompt V2.4 implementation

Source review: 2026-11-10. This is the current Backend contract, not a new live
inference, accuracy, benchmark or external-service validation report.

## Current sources and prompt registry

- `app/model_runtime/siglip_policy.py`: pure decision policy and trace validation.
- `app/model_runtime/siglip_v24.py`: environment configuration.
- `app/model_runtime/siglip_v23.py`: fixed prompts and V2.3 comparison helper.
- `app/model_runtime/siglip_image_verification_adapter.py`: crop result adaptation.
- `app/core/model_runtime_client.py`: authenticated Gateway single/batch calls.
- `app/business/image_verification_category_service.py`: selectable categories.

The policy is local to Backend. This checkout has no `API_Service_Kaggle` sibling
and no `src/shared/SiglipDecisionDetails.tsx`; do not require those files to deploy
this Backend. External Gateway/Leaf implementations must be checked separately.

Canonical order is **8 Positive + 7 Negative = 15 finite ordered logits**:

| Positive order | Negative order |
| --- | --- |
| emblem | printed_text |
| thai_garuda | table |
| company_logo | blank |
| document_stamp | background |
| portrait | illustration |
| signature | pattern |
| barcode | bar_chart |
| qr_code | |

`numbers` is removed. Stale 16-logit vectors are rejected. Negatives are not
selectable image Anchor categories. All eight positives compete even when only
one target category is requested; disabled/invalid targets cannot pass.

## Decision order

Scoring version: `siglip-single-prompt-v2.4`.

1. Validate 15 finite numeric logits, excluding booleans.
2. Rank positives and find the strongest negative; ties preserve canonical order.
3. Reject as `other` if `negative_top > positive_top + 0.8`.
4. If configured, require positive relative share >= confidence threshold.
5. If configured and the preceding gates pass, require positive top1-minus-top2
   relative share >= margin threshold.
6. Accept only the original Positive Rank 1; never substitute another positive.

The comparison uses unrounded values. A Negative lead of exactly 0.8 is allowed.
The V2.3 Garuda/Emblem substitution, Garuda gap guard and Emblem -6.3 floor do not
apply to active V2.4.

| Gate | Default | Failure reason |
| --- | --- | --- |
| Negative competition | Enabled, tolerance 0.8 raw-logit units | negative_category_won |
| Relative confidence | Disabled | confidence_gate_failed |
| Relative margin | Disabled | margin_gate_failed |

Disabled gates report `not_applied`; enabled gates skipped due to earlier failure
report `not_evaluated`. Default acceptance reports `positive_rank1_accepted`.

## Configuration and score units

Optional environment variables:

- `SIGLIP_V24_CONFIDENCE_MIN_RELATIVE_SCORE`
- `SIGLIP_V24_MARGIN_MIN_RELATIVE_SCORE`

Leave unset/blank to disable. Explicit zero is a valid configured threshold;
finite numbers in [0,1] are required. Old category match_threshold/margin_threshold
are compatibility metadata, not these V2.4 gates.

Sigmoid(raw logit) is display metadata. Positive softmax share is computed only
across eight positives; it is not a calibrated confidence probability. Raw
positive_negative_margin and positive_top1_top2_margin use logit units, while
positive_relative_margin is a dimensionless share difference. Do not interchange
them or recalculate the final prediction from rounded UI percentages.

## Gateway and trace contract

Host base GATEWAY_URL has no /paddle suffix. Current routes are
`/paddle/api/v1/image-verifications` and
`/paddle/api/v1/image-classification-batches`, authenticated with
MODEL_GATEWAY_API_KEY. Requests include canonical prompts and optional
`decision_config`; preserve batch order and cardinality.

`consume_decision` checks a supplied trace against policy/vector/config and its
fingerprint. Inconsistency fails with a contract error. A supported raw-only
response is evaluated locally with V2.4, never V2.3. The fingerprint checks
consistency, not security or model identity. No extra inference is performed.

Backend exposes `siglip_decision` plus ranking/reason/gate metadata. Expected
category must match final prediction for the image Anchor to pass. Template
acceptance additionally requires every in-scope Anchor; complete passes are
ranked by Anchor count then template ID. Legacy weights/confidence thresholds
do not select the Template winner.

## Synthetic boundary examples

With every unspecified logit very low and optional gates disabled:

| Positive top | Strongest Negative | Result |
| --- | --- | --- |
| portrait = -8.1219 | -8.0 | portrait (lead 0.1219 is within tolerance) |
| portrait = -8.1219 | -7.0 | other (lead 1.1219 exceeds tolerance) |
| thai_garuda = 8.1; emblem = 6.5818 | -20 | thai_garuda, no substitution |

These explain policy boundaries, not model predictions on actual documents.

## Deployment and validation

Deploy Backend policy/registry/adapter consistently and ensure the external
Gateway accepts the same 15 prompts/order and optional config. Restart Backend
and rebuild Frontend when its mapping/UI changes. No sibling API source loader
is required. Fixed category initialization can delete legacy values and set
existing foreign keys to null; review [category behavior](docs/SIGLIP_V23.md)
and back up the DB before rollout.

From `project_backend`, use the installed environment:

```powershell
python -m unittest discover -s tests -p 'test_siglip*.py'
```

These tests use fixtures/mocked logits; they do not establish live Gateway/model
accuracy. For production, validate single/batch ordering, malformed and empty
responses, optional gates and trace mismatch, then evaluate a labeled set of all
positive classes and unknowns. No accuracy improvement or resource budget is
claimed by this documentation review. Previous test counts/deploy claims from
other checkouts are not current verification evidence.
