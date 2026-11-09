# SigLIP V2.3 comparison and current category registry

Source review: 2026-11-10. **Active production-code policy in this checkout is
V2.4**, not V2.3. See [current V2.4 contract](../SIGLIP_V24_IMPLEMENTATION.md).
This page documents the retained comparison helper and category initialization.
No live DB migration or model inference was performed in this review.

## Retained V2.3 helper

`app/model_runtime/siglip_v23.py` contains fixed prompts and `classify(logits)`.
The shared registry now contains eight Positive and seven Negative prompts;
`numbers` is absent. Thus the helper uses 15 logits and is not an exact replay
of the original 16-prompt experiment. Old accuracy numbers cannot be transferred
to this registry or to V2.4.

Positive order: emblem, thai_garuda, company_logo, document_stamp, portrait,
signature, barcode, qr_code.
Negative order: printed_text, table, blank, background, illustration, pattern,
bar_chart. Negatives are not selectable Anchor categories.

Comparison helper order:

1. Baseline is the greatest of all logits (canonical tie order); a negative yields
   `other`.
2. If positive top is thai_garuda, its lead over emblem is <2.0 and emblem is at
   least the strongest negative, return emblem immediately.
3. If positive top remains thai_garuda and its lead over the strongest negative is
   <2.0, return other.
4. If positive top is emblem and its logit is <-6.3, return other.
5. Otherwise keep baseline.

Step 2 deliberately precedes step 4. These guards are comparison rules only;
active V2.4 uses Positive Rank 1, Negative tolerance 0.8 and optional relative gates.

## Database category behavior

`app/business/image_verification_category_service.py` initializes the eight fixed
Positive categories from SINGLE/LABELS. Rows outside this registry are deleted,
including old official_stamp/government_emblem/thailand_symbol values. Fixed
prompts are upserted for retained keys. Category foreign keys use ON DELETE SET
NULL; old Template JSON is not automatically remapped.

Back up data and inspect affected Anchors before deployment/restart. Repair any
missing category references explicitly, using the intended current category.
Category APIs keep compatibility metadata but reject unsupported class/prompt
changes. Threshold fields must not be mistaken for V2.4 optional gate settings.

## Requests and preprocessing

Current Gateway paths include /paddle:

- `/paddle/api/v1/image-verifications`
- `/paddle/api/v1/image-classification-batches`

Backend sends 15 descriptions and validates finite logits, order and result
cardinality. Malformed responses do not fall back to preclassified predictions.
SigLIP preprocessing composites transparency onto white before RGB conversion;
revalidate transparent inputs rather than assuming equivalence to alpha discard.

This repo does not contain API_Service_Kaggle or the original experiment script.
References to external implementations/experiments in older reports are historical,
not executable setup instructions for this checkout.

## Verification

```powershell
cd project_backend
python -m unittest discover -s tests -p 'test_siglip_v23*.py'
python -m unittest discover -s tests -p 'test_siglip_v24.py'
```

Fixture tests cover comparison guards, active policy, malformed logits and
category behavior; they do not measure real accuracy or validate a deployed
Gateway. Previous pass counts and 58/60 experiment results are not current
production evidence.
