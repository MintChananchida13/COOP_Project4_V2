# SigLIP Single Prompt V2.3 end-to-end audit

## Scope and state

**Historical audit snapshot:** the following describes V2.3 before implementation
of V2.4. Current production changes and validation are documented in
`SIGLIP_V24_IMPLEMENTATION.md`. The V2.3 characterization tests now invoke the
explicit legacy implementation; they do not select the active production policy.

Production remains **`siglip-single-prompt-v2.3`**. The uncommitted V2.4 draft
created under the earlier instruction was withdrawn, together with its draft
acceptance tests. Only this report and V2.3 characterization tests are added for
the current audit. No model, prompts, categories, thresholds, weights, OCR,
Layout, Table, retrieval or production decision logic is changed by the audit.

The source and synthetic-logit tests are verified locally. The live Kaggle
revision/process, Neon category values, actual image crops, and the complete
16-logit responses for Cases A/B were not supplied/queried: **Not Verified**.
Reconstructing the supplied rounded scores is not replaying the actual query.
No browser, benchmark or real inference was run; no commit/push was performed.

## 1. Source map and actual flow

| Stage | File / function | Actual responsibility |
| --- | --- | --- |
| Fixed prompts/order | `app/model_runtime/siglip_v23.py:35`, `runtime_categories` | 8 Positive then 8 Negative, one prompt per category |
| Model forward | `../API_Service_Kaggle/app/models.py:75`, `siglip_logits` | Model/processor checkpoint `google/siglip-so400m-patch14-384`; text prompts in request order; `logits_per_image` |
| Single HTTP route | `../API_Service_Kaggle/app/main.py:181`, `verification` | Returns raw logits, device, model and timing; does NOT make final category decision |
| Batch HTTP route | `../API_Service_Kaggle/app/main.py:188`, `classification_batch` | Chunked inference, ordered `results`; one logit vector per image |
| Transport | `app/core/model_runtime_client.py`, `_post_predict`, `remote_verify_image_logits`, `remote_verify_image_logits_batch` | Bearer auth, encode/decode JSON, extract result/timing; no softmax, ranking or class decision |
| Classification policy | `app/model_runtime/siglip_v23.py:40`, `classify` | Validates length/finite numeric values; global competition followed by ordered category guards |
| Scores/target verification | `app/model_runtime/siglip_image_verification_adapter.py:175`, `_result_from_logits` | Positive-only softmax/sigmoid display, raw rank/margin, binary evidence from guarded prediction |
| Single/batch adaptation | Same file:293 / :331, `verify_image_category`, `verify_image_categories_batch` | Canonical prompts, target eligibility, exact batch count/order checks; same classification policy |
| Anchor mapping | `app/business/services.py:3777` / :3880, `_score_image_anchor`, `_image_match_from_siglip_result` | Maps decision/evidence to image match; currently drops rule/negative diagnostics |
| Field mapping/cache | Same file:4615, `_image_anchor_check` | Copies authoritative `passed`/score; per-runtime-cache/precomputed match support; no new sigmoid/relative-score threshold gate |
| Score summary | Same file:4487, `_verification_summary` | Weighted field/image/text evidence summaries |
| Ordered Standard verification | Same file:4788, `verify_template_strict` | Despite historical name, reports Standard; text then image; first failed anchor returns failure |
| Final decision | Same file:5198 / :5262, `DecisionService.decide_candidate` / `decide_candidate_strict` | Weighted legacy versus ordered Standard final gates; see section 5 |
| Frontend mapping/display | `../project_frontend/src/admin/adminApi.ts`, `mapTemplateStepTestItem`; `admin/workspace/WorkspaceTemplateEditorV2.tsx` | Passes prediction, raw rank, margins, percentages and version to UI. Displays prediction/rank independently; does not run V2.3 decision logic |

Source reference lines above are from the restored V2.3 tree. Paths beginning
`app/` are relative to `project_backend/`.

### Category inventory and order

Positive canonical order: `emblem`, `thai_garuda`, `company_logo`,
`document_stamp`, `portrait`, `signature`, `barcode`, `qr_code`.

Negative canonical order: `printed_text`, `numbers`, `table`, `blank`,
`background`, `illustration`, `pattern`, `bar_chart`.

API validates active request categories and keeps their order; the processor uses
that same ordered prompt list. Backend always sends the fixed canonical 16 prompts,
even if DB configuration is reordered. It rebuilds the 8 Positive score positions
in canonical order before attaching labels/configuration. No demonstrated index
or prompt-order bug was found in this path. The response does not echo a label
fingerprint: a different external runtime silently reordering logits remains a
contract/integration risk that length validation alone cannot detect.

## 2. V2.3 decision rules, in exact order

Let `z[c]` be raw logits; `P` the highest Positive and `N` the highest Negative.
Ties use canonical insertion order; Positives precede Negatives.

| Order | Condition | Output / reason | Scope / units |
| --- | --- | --- | --- |
| 0 | Not exactly 16 finite numeric logits (bools excluded) | Validation error; adapter returns error/zero evidence | All categories; no invented scores |
| 1 | Argmax over all 16 is Positive | Set baseline to that Positive | All; raw-logit ordering |
| 1b | Argmax over all 16 is Negative | Set baseline `other` | All; strictly higher Negative beats Positive; equal Positive wins tie |
| 2 | `P=thai_garuda`, `z[garuda]-z[emblem] < 2.0`, AND `z[emblem] >= z[N]` | Immediately return `emblem`, `garuda_emblem_ambiguity` | Garuda -> Emblem override; raw-logit difference |
| 3 | `P=thai_garuda`, `z[P]-z[N] < 2.0` | Immediately return `other`, `weak_garuda_vs_negative` | Garuda only; Positive-vs-Negative raw-logit difference |
| 4 | `P=emblem`, `z[emblem] < -6.3` | Immediately return `other`, `weak_emblem_score` | Raw-logit absolute threshold |
| 5 | None of the guards fired | Return baseline, `unchanged` | `unchanged` can still mean Negative won and output is `other` |

Exactly 2.0 is NOT rejected by a `<2.0` guard. Exactly -6.3 is NOT rejected by
`<-6.3`. The first guard returns before the absolute Emblem guard: e.g. Garuda -5,
Emblem -6.5 and best Negative -7 returns Emblem, although -6.5 is below -6.3. This
matches the original experiment; it is a policy/order risk, not a transcription bug.

There is no default generic minimum sigmoid score, Positive softmax score,
Positive-vs-Positive margin, or Positive-vs-Negative gap gate for the other six
classes. Optional `--min-logit` / `--min-margin` experiment CLI gates are NOT
production options here. Existing tests verify guarded-policy parity against the
original `siglip_single_v2_3.py` over 1,000 synthetic vectors; this does not prove
parity with every optional experiment CLI setting or classification accuracy.

## 3. Threshold inventory by category

`image_verification_category_service.py` seeds all eight with match=0.50,
margin=0.05, temperature=1.0. SQL column/adapter fallback for match is 0.70;
margin fallback is 0.05. Existing DB rows are not reset by seeding: conflict update
changes the prompt only. Actual stored thresholds per category: **Not Verified**.

| Category | Seed match / margin | Adapter fallback | Effective V2.3 gate |
| --- | --- | --- | --- |
| `thai_garuda` | 0.50 / 0.05 | 0.70 / 0.05 | Garuda-Emblem gap <2.0 override, then Garuda-Negative gap <2.0 rejection |
| `emblem` | 0.50 / 0.05 | 0.70 / 0.05 | Absolute raw logit <-6.3 when it is best Positive; can also be selected by Garuda override |
| `document_stamp` | 0.50 / 0.05 | 0.70 / 0.05 | Global Negative competition only |
| `company_logo` | 0.50 / 0.05 | 0.70 / 0.05 | Global Negative competition only |
| `portrait` | 0.50 / 0.05 | 0.70 / 0.05 | Global Negative competition only |
| `signature` | 0.50 / 0.05 | 0.70 / 0.05 | Global Negative competition only |
| `qr_code` | 0.50 / 0.05 | 0.70 / 0.05 | Global Negative competition only |
| `barcode` | 0.50 / 0.05 | 0.70 / 0.05 | Global Negative competition only |
| All 8 Negative classes | No configurable target gate | Not eligible as expected image-anchor categories | Compete via raw logit and become `other` when winning |

Configured match/margin are clamped to [0,1], but are currently returned as
metadata, NOT compared with sigmoid, softmax or raw margins. Temperature is
normalized to >=0.01 but not applied to the scoring formulas. Thus the current
code does not numerically compare the 1.5182 raw margin with the configured 0.05;
it simply never enforces that configured margin. UI/config naming implies more
control than production currently provides. This is an inactive-configuration /
product-contract issue, not permission to silently activate a new rejection gate.

## 4. Scores, ranking, decision and UI: separate concepts

| Field / stage | Formula / units | Meaning / consumers |
| --- | --- | --- |
| Raw logit `z[c]` | Model output, unbounded real | All 16 compete here; model-dependent, not a probability |
| `raw_pair_score` | `sigmoid(z)=1/(1+exp(-z))`, [0,1] | Independent pair score; not calibrated class confidence; not a V2.3 gate |
| Positive `relative_score` | `exp(z-max_positive)/sum_positive(exp(z-max_positive))`, [0,1] | Relative distribution over 8 Positive prompts ONLY; excludes Negatives; NOT confidence probability |
| `relative_percentage` | Target relative score *100, rounded to 2 decimals | 0..100 display only |
| `siglip_labels` | 8 Positive entries sorted by descending raw logit | Same raw ranking as unrounded softmax/sigmoid (monotonic); includes raw scores, target, category config, binary decision evidence |
| `siglip_ui_percentages` | Positive softmax *100 rounded to 1 decimal; rounding residual applied to largest rounded entry | Fixed canonical Positive order, NOT sorted by final prediction or raw rank; total ~100 |
| `siglip_target_rank` | Position of expected category in raw Positive ordering | 1..8, not a pass flag |
| `siglip_score_margin` | If target rank=1: top Positive logit - second Positive; else target logit - top Positive | Raw-logit units, positive/zero at top, negative otherwise. Not Relative Margin and not the Negative gap |
| Internal `positive_negative_margin` | `max_positive(z)-max_negative(z)` | Raw-logit units; only Garuda uses a minimum gap guard |
| `predicted_image_category` | Guarded V2.3 decision, or `other` | Authoritative classification, not `siglip_labels[0]` |
| `passed` | Final predicted category == requested expected category | Additional target eligibility/error handling can fail first |
| `evidence_score`, adapter `score` | 1 if passed, otherwise 0 | Binary verification evidence, NOT raw/relative confidence |
| Label entry evidence/field score | 1 for the final predicted Positive, else 0 | Can give Raw Rank 2 Emblem evidence=1 while expected Rank 1 Garuda has evidence=0; all zero for `other` |
| Backend `field_score`, `image_category_score`, `siglip_similarity_score` | Copied binary evidence | Historical names do not mean cosine similarity/softmax probability |
| `failure_reason` | `passed` or generic `predicted_category_mismatch` | Does not currently explain Negative winner versus category guard |
| `scoring_version` | `siglip-single-prompt-v2.3` | Policy identifier, distinct from unchanged model checkpoint name/version |

Decisions use raw unrounded logits; rounding is for returned/display values.
Configurations can be shuffled without changing score alignment. A disabled or
missing target is rejected, but missing competitors are filled from fixed SINGLE
definitions: they still compete among the eight. Whether disabling means only
anchor eligibility or removal from the classification universe needs a product
decision; the current fixed-universe behavior is covered by audit tests.

### Diagnostic propagation gap (confirmed)

The adapter computes `decision_rule`, `negative_scores`, and
`positive_negative_margin`. Both `_score_image_anchor` and
`_image_match_from_siglip_result` omit these when constructing their dictionaries;
subsequent checked-field mappings and `mapTemplateStepTestItem` therefore cannot
explain the decision to the UI. Frontend shows prediction and raw target rank as
separate values, but no guard/rejection reason beyond generic mismatch. This is
a confirmed observability/data-propagation defect, not evidence of mixed scores.

No score-index, UI-percentage-driven decision, or wrong-category-name mapping bug
was demonstrated by the current source/tests. Single multi-allowed-category anchors
may re-infer the same crop per target; batch derives multiple target results from
one vector. Runtime/precomputed caches map by image/ROI/padding/category and field;
no evidence proves a wrong-run cache hit for either reported case. Full request /
crop / logits provenance is needed before claiming such a runtime bug.

## 5. Image Anchor and final template result

`_image_anchor_check` copies the decision's `passed` and binary evidence; it does
not reapply configured threshold to raw/relative scores. Category threshold
fallback fills metadata. For field weights `w_i=max(0,float(weight_i or 1))`:

- `image_anchor_score = sum(w_i * image_field_score_i) / sum(w_i)` for image
  anchors; same form for text and overall verification score. With no image
  anchors the summary reports 1.0 as a neutral value, not a measured classification.
- An explicit zero weight becomes 1 via existing `or 1` fallback, so zero cannot
  exclude an anchor in this summary. This is an existing weighting/configuration
  risk outside this audit's permitted fixes; no weights were changed.
- Ordered Standard `verify_template_strict` stops at the first failed text/image
  anchor and explicitly sets passed/required_passed false, including anchors not
  marked required. Its partial summary is not a full all-anchors evaluation.
- `decide_candidate_strict` (current Standard path) requires verification pass,
  required-fields pass and retrieval >=0.50. On success final_score is retrieval;
  otherwise it is verification score. It reports `standard_image_anchor_failed`
  for an image-stage failure. Its final-confidence parameter is not a Standard
  gate (`final_threshold_passed=None`).
- Legacy `decide_candidate` computes normalized active weights and weighted
  `layout + text_anchor + image_anchor` final score, checks final confidence,
  required fields and retrieval floor. Default weights are 0.50/0.35/0.15 and
  default final threshold 0.75; active-anchor absence renormalizes weights.

Thus a new classification acceptance would not guarantee a template match:
other anchors, missing pages/crops, retrieval and the selected strategy still gate
the final result. Audit tests exercise the real extracted Standard workflow,
summary and DecisionService with synthetic field results, including retrieval failure.

## 6. Case analysis

### A: Garuda Rank 1, final Emblem

Reported query: `detq_ab223c07a46e`; no complete runtime payload for this ID was
replayed during the audit.

Synthetic vector Garuda=8.1, Emblem=6.5818, other scores low reproduces relative
0.8203, sigmoid 0.9997, raw Positive margin 1.5182 and final Emblem. Guard #2 fires
because Garuda-Emblem <2 and Emblem >= best Negative. Expected Garuda then receives
binary evidence=0 and mismatch, so Standard image verification can fail.

This is **correct under the existing policy**, not a demonstrated rank/index bug.
For the real query, its 1.5182 top-two margin alone does not identify Rank 2 as
Emblem or reveal Negative scores. If the supplied version/prediction/vector are
consistent with this source, that guard is the path returning Emblem. Full raw
logits/decision diagnostics are still needed to verify that exact runtime instance.

### B: Portrait Rank 1, low sigmoid, final Other

Portrait has no category-specific override/absolute cutoff. Under consistent V2.3
source, Portrait best Positive becomes Other when a Negative raw logit beats it.
Neither relative 0.6463 nor sigmoid 0.0003 is compared to configured match 0.50;
the 1.5995 raw margin is not compared to configured margin 0.05.

Audit fixtures use Portrait=-8.1219, Barcode=-9.7214 and six lower Positives chosen
to yield relative 0.6463. With best Negative=-8.0, prediction is Other; with every
Negative=-30, prediction is Portrait and passes. Both fixtures reproduce the
reported Positive scores, proving those fields alone cannot establish actual
Negative competition. The exact real winning Negative is **Not Verified**.
If the real response instead shows all Negatives below Portrait while claiming
Other under this version, investigate deployed revision, vector/crop identity or
mapping; do not invent a missing confidence gate as the explanation.

## 7. Findings classified

| Type | Finding | Action priority |
| --- | --- | --- |
| Confirmed observability defect | Decision rule/Negative evidence dropped before UI | P0: additive decision diagnostics, no policy change required |
| Confirmed inactive configuration | Match/margin/temperature stored and displayed but not enforced | P0: make active-policy/units explicit; design gating only after approval/validation |
| Correct V2.3 behavior | Raw Rank 1 can differ from guarded prediction; evidence becomes binary zero | Explain raw ranking versus final decision, not force PASS |
| Correct V2.3 behavior | Negative wins can reject every Positive category | Preserve explicit open-set rejection and reasons |
| Design risk | Asymmetric Garuda and Emblem rules, guard ordering promotes weak Emblem | Compare policy alternatives on held-out data |
| Design risk | Tiny sigmoid / tiny positive margin can still pass six generic categories | Global rejection needs calibration; not proof either reported image is misclassified |
| Design risk | All-zero 16-logit vector selects Emblem and passes expected Emblem, despite relative 0.125 | Current policy cannot reliably identify unknown images from flat vectors |
| Design risk | Seed 0.50 versus fallback 0.70 and prompt-only upsert; actual DB values unknown | Audit effective threshold provenance before activating controls |
| Design risk | Fixed 8-way competition even for inactive competitors; rounding/ties can obscure ordering | Define universe/ties, keep raw decision values and prompt fingerprint |
| Separate configuration risk | Zero anchor weight is treated as 1 in summary; Standard early stop differs from required-only summary | Separate scoped review, not a SigLIP scoring patch |
| Not demonstrated | Cross-run cache, wrong index, duplicate prediction source or label swap causing A/B | Need full actual logs/vectors before attributing causality |

## 8. Alternatives for V2.4 (proposal only)

| Approach | Advantages | Risks / validation needs |
| --- | --- | --- |
| A: Positive Rank 1 + relative score/margin gates | No forced category substitution; simple, interpretable, uniform | Positive-only softmax can be large for irrelevant content and low absolute scores; depends on number/prompts of categories; cannot replace Negative competition; not calibrated confidence probability |
| B: Rank 1 + Negative competition + category thresholds | Explicit open-set rejection; can accommodate different score distributions per category | Per-class thresholds overfit small data; Negative prompts are incomplete; raw-gap versus relative units must be distinct; expansion needs recalibration |
| C: V2.3 minus the Garuda->Emblem override | Smallest change, easy ablation, keeps existing Negative behavior | Still asymmetric Garuda/Emblem rejection; inactive config remains; Garuda precision may fall while recall improves; not a full confidence solution |

Recommended candidate to evaluate, NOT deploy automatically: A's no-substitution
decision with B's explicit all-category Negative competition, explicit tie policy,
and configurable relative-score/Positive-relative-margin gates. The user's earlier
relative-score preference is recorded, but neither existing DB numbers nor these
two examples validate the new cutoffs. Per-category gates should be enabled only
with adequate independent data; otherwise use a validated common baseline. C is
an essential low-change ablation, not assumed worse/better without evaluation.

Proposed decision sequence for evaluation:

1. Validate vector/prompt fingerprint and active classification universe.
2. Record raw Positive rank 1/2, best Negative and raw gap. Never substitute a
   runner-up class merely because two Positive scores are close.
3. Reject to Other for Negative competition/tie according to an explicit selected
   policy. A proposed conservative Negative tie rejection must be evaluated; V2.3
   currently prefers the Positive on ties.
4. Compare unrounded Positive softmax p1 with a documented relative-score gate;
   compare p1-p2 with a separately documented Relative Margin gate. If raw
   Positive-Negative gap is gated, give it its own raw-logit parameter, never reuse
   the dimensionless margin field. No numeric cutoffs are approved by this audit.
5. Emit one immutable decision object with version, raw winner, final prediction,
   rejected/accepted state, reason, full diagnostic scores and effective thresholds.
6. Verification uses accepted AND final prediction in allowed target categories.
   Preserve binary evidence/anchor weights unless separately approved; expose raw
   ranking and UI rounding as diagnostics only.

Before choosing a version: use a held-out, independently labeled set across all 8
Positives, all Negative descriptions and realistic unknown/background/table/text
crops, resolutions, transparency, orientations, document sources and crop quality.
Split by source/template, not near-duplicate crops. Measure per-category precision,
recall, F1, macro averages, Negative/Other false acceptance and rejection, calibration
and full-template verification outcomes. Compare V2.3 and A/B/C on identical
logits; tune on a separate validation split, then freeze/evaluate test once. Adding
a category changes Positive softmax denominator and Negative coverage: revalidate.
Do not reuse the old experiment's 96.67% as evidence for any new policy.

### Files/functions proposed for a later approved change

- `siglip_v23.py`: retain historical policy/prompts; add a distinctly versioned
  policy entry point/module rather than silently relabeling changed decisions V2.3.
- `siglip_image_verification_adapter.py::_result_from_logits`: single decision
  object, explicit gates/units, no UI score as source, diagnostic rejection reasons.
- `business/services.py::_score_image_anchor`, `_image_match_from_siglip_result`,
  `_image_anchor_check` and image checked-field mapping: preserve that decision.
- `image_verification_category_service.py`: effective-config provenance, supported
  gate semantics, migration/default policy and enabled-universe definition.
- `model_runtime_client.py` / API raw-response schema only if additive prompt/
  model/vector identity metadata is needed; no model architecture/prompt changes.
- Frontend `adminApi.ts::mapTemplateStepTestItem` and verification display:
  explicit Final Decision versus Raw Rank, active reason/gates, relative (not
  probability) scores, independent raw/relative margin labels.
- Existing caches/log persistence: key/invalidate with new policy/config identity;
  retain historical version labels instead of retroactively rewriting results.

None of these production changes is implemented by this audit.

## 9. Automated validation

`tests/test_siglip_v23_audit.py` characterizes existing behavior rather than
asserting that inactive confidence gates already work. It tests synthetic A/B
reconstructions, all Positive/Negative categories, low confidence/margin,
close Negatives, overrides/order, ties/boundaries, uniform unknown-like vectors,
UI/rank/evidence mapping, reordered configuration, disabled competitor behavior,
single/batch alignment, HTTP-envelope -> real client -> adapter mapping, and real
source-extracted Standard anchor/summary/final gates with controlled field inputs.

Transport is mocked and no external network is contacted. Source-extracted
business methods avoid DB/model initialization; this is not a live service test.
The original V2.3 experiment parity and existing category tests also remain.
Results: 15 new audit tests; 25 SigLIP/category tests total; all 52 backend tests
passed. API contract/timing tests passed 10 tests plus 4 subtests using stubbed
model computation. Audit Python AST syntax and `git diff --check` passed.
`git diff --exit-code -- project_backend/app project_frontend/src API_Service_Kaggle/app`
returned success: there are no remaining tracked production changes. Only this
report and the audit test file are new. Backend tests logged absent local database
settings with default fallback; API tests emitted an existing AnyIO deprecation
warning. These tests did not connect to Neon or run real inference. Actual runtime
classification accuracy, full real query replay and calibration: **Not Verified**.
