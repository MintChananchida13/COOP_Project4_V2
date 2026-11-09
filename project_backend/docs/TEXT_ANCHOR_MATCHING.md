# Text verification anchors

Source review: 2026-11-10. Current literal normalization/Levenshtein rules checked against app/business/services.py. This documentation review did not run live OCR. V2.4 is the active image policy; see ../SIGLIP_V24_IMPLEMENTATION.md.

All text anchors use the same rule, including existing templates:

1. Normalize both expected and OCR text with NFKC, lowercase, whitespace removal,
   and removal of the configured zero-width characters.
2. Reject if either normalized string is empty.
3. Compute Levenshtein distance on the normalized Unicode code points, counting
   substitution, deletion and insertion at cost one each.
4. Accept only if distance <= len(normalized_expected) // 4.

There is no similarity calculation, configurable text threshold or match type.
The existing score fields (`score`, `field_score`, `text_match_score`,
`text_similarity_score`) contain only 1.0 or 0.0. The compatibility field
`verification_threshold` is 1.0. OCR confidence is diagnostic only.
Text results no longer expose `match_type`. Image verification metadata and its
decision rules are unchanged. Staged batching and rejection still use `passed`.

## Existing data and deployment

No destructive database migration or template recreation is needed. The nullable
`verification_anchors.match_type` and `regex_pattern` columns remain as archives;
runtime queries do not read them, updates leave them untouched, and new anchors
leave them NULL. Prisma ignores these legacy fields; historical migrations remain
unchanged. Deploy the backend and frontend together and restart the backend.

Create/update API schemas no longer advertise the legacy fields. Extra fields
from older clients or pending operations are ignored by Pydantic's existing
default behavior. Expected text, ROI, page mapping and image categories are kept.

Expected text is always literal. Existing templates that stored regex syntax in
Expected Text should be reviewed and given the actual expected document text;
patterns are not automatically rewritten, so stored data is not lost or guessed.
