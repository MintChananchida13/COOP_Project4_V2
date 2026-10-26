# SigLIP Single Prompt V2.3

## Source audit and previous flow

The reference is the user-provided root `siglip_single_v2_3.py`, specifically `SINGLE`, `NEGATIVE`, `score_image`, `guarded_decision`, and the default `main` policy (`guarded`, no optional min-logit/min-margin overrides). Its filename/ground-truth tables are used only for experiment evaluation and are not imported into production.

Previously `app/business/image_verification_category_service.py` seeded eight categories and read their editable prompts from PostgreSQL. `app/business/services.py` passed active categories and a cropped image to `app/model_runtime/siglip_image_verification_adapter.py`. Its single/batch clients in `app/core/model_runtime_client.py` sent category prompts to the authenticated API Service. `API_Service_Kaggle/app/models.py` loaded `google/siglip-so400m-patch14-384` on CPU and returned `logits_per_image`. The Backend selected the largest positive logit, returned binary target evidence, and computed sigmoid/positive-only softmax for display. It had no negative prompts or Other rejection policy.

## V2.3 flow and exact rule order

`app/model_runtime/siglip_v23.py` owns the eight fixed positive prompts, eight single negative prompts, and pure decision function. Both Backend single/batch calls always send these 16 descriptions in insertion order. Negatives are not stored as selectable categories. The API continues to return one raw logit per supplied description. All eight positives compete even if the requested target is only one class; a disabled target is still rejected before verification.

1. Find the greatest score across all 16 descriptions, preserving insertion order for ties. Baseline is that positive category or `Other` when a negative wins.
2. Find the best positive and best negative separately. If the best positive is `thai_garuda`, its lead over `emblem` is **less than 2.0**, and `emblem >= best negative`, return `emblem` immediately.
3. If the best positive is still `thai_garuda` and its lead over the best negative is **less than 2.0**, return `Other`.
4. If the best positive is `emblem` and its raw logit is **less than -6.3**, return `Other`.
5. Otherwise keep the baseline.

The early return in step 2 deliberately precedes step 4, even for an emblem score below -6.3. No new threshold is applied to the other six classes. Softmax percentages and sigmoid pair scores are retained for display, not decisions or calibrated confidence. `target_rank` and `score_margin` retain their raw-positive-ranking meaning; an emblem chosen by the guard can pass with raw rank 2. Binary `passed`/`score`/`evidence_score` follow the final V2.3 decision.

## Contracts and category replacement

The authenticated endpoints remain `POST /api/v1/image-verifications` and `POST /api/v1/image-classification-batches`. Request fields, response envelope (`success`, `model`, `result`, `timing`), raw logits, batch order/cardinality, and CPU model are unchanged. Arbitrary supplied prompt counts remain supported by the API; the Backend V2.3 integration supplies 16. Backend result fields remain present, with optional `decision_rule`, `negative_scores`, and `positive_negative_margin` added to the adapter result. `predicted_category="other"` and `predicted_label="Other"` represent rejection; no ninth positive category is created. Missing/malformed raw logits fail validation instead of falling back to old preclassified responses.

As explicitly requested, on Backend category initialization PostgreSQL rows outside the new eight values are **deleted**, including `official_stamp`, `government_emblem`, and `thailand_symbol`. The eight fixed prompts are upserted for existing values; old labels/threshold metadata for retained keys remain. Existing category foreign keys use `ON DELETE SET NULL`; references in old Template JSON are not remapped. Recreate Templates with `emblem`, `thai_garuda`, `company_logo`, `document_stamp`, `portrait`, `signature`, `barcode`, and `qr_code`. This source change does not run against a live database until deployed/restarted. Admin category endpoints retain their schemas but reject additional classes and prompt changes that would deviate from V2.3.

Only SigLIP routes composite RGBA/palette transparency onto white before RGB conversion. Other OCR image decoding is unchanged. This is a deliberate preprocessing improvement over the experiment's direct `.convert('RGB')`, which discarded alpha; transparent-image results must therefore be revalidated.

## Validation

Changed files: `project_backend/app/model_runtime/siglip_v23.py`, `project_backend/app/model_runtime/siglip_image_verification_adapter.py`, `project_backend/app/business/image_verification_category_service.py`, `API_Service_Kaggle/app/main.py`, `API_Service_Kaggle/app/models.py`, `API_Service_Kaggle/app/serialization.py`, `project_backend/tests/test_siglip_v23.py`, `project_backend/tests/test_siglip_v23_categories.py`, `API_Service_Kaggle/tests/test_contract.py`, and this report. Final local checks passed: 21 Backend tests, 7 API tests (plus 4 pytest subtests), Python syntax, and `git diff --check`.

From `project_backend`: `python -m unittest discover -s tests -p 'test_siglip_v23*.py'`.
From `API_Service_Kaggle`: `python -m pytest tests/test_contract.py` in an environment with the service dependencies installed.

Policy tests verify all positive/negative classes, threshold equality, guard order, malformed/extreme logits, single/batch prompt order and target results, legacy-category rejection, and 1,000 deterministic score sets against the original experiment function extracted through AST. SQLite tests exercise category deletion, fixed prompts, FK behavior, and repeat initialization; real Neon migration still requires deployment verification. API tests exercise envelope/cardinality and transparency with test inference, not real model accuracy.

Local validation used an isolated environment under the Git-ignored `benchmark/output/siglip-validation/.venv`; production dependency files were not changed. Backend tests passed (21 tests). API tests passed, including RGB pixel preservation, alpha compositing, existing routes, and single/batch response shape. CPU Torch/Transformers imports and a tiny-config random-weight SigLIP CPU forward (1 image x 16 texts) passed; this is a compatibility smoke test, not full checkpoint inference. The full cached checkpoint is approximately 3.5 GB, while available RAM during validation was approximately 3.5 GiB, so full checkpoint inference was not run. No labeled image test set is present in the checkout. Kaggle model inference, full HTTP end-to-end behavior, real Neon migration, and before/after accuracy remain **Not Verified**. The experiment's reported 58/60 (96.67%) is not claimed as production accuracy.
