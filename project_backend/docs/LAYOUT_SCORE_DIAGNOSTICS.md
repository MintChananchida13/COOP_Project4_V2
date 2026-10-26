# Layout score: identical source image investigation

The prepublish New Document Test sends the original uploaded file to `detect_template_dev`. Image queries pass through `_normalize_query_pages` and `normalization_service.normalize_document` automatically, even when no UI adjustment was made. Template signatures are generated from `normalized_image_url` (or `sample_image_url`) directly by `_refresh_template_layout_signatures`. Consequently the same original file does not prove that both PP-DocLayout inputs have the same pixels, dimensions, or predicted regions.

One independently reproduced scoring bug was fixed in `_spatial_similarity`: it matched only the first 20 regions per label but divided the sum by the full region count. As requested, it now considers every query and template region, without a 20-region limit, and divides by the full region count. Matching remains greedy and one-to-one within each label; unmatched regions reduce the score. The separate count component also uses all regions. Identical signatures with 1, 20, 21, and 100 regions per label score 1.0, including a stored JSON round trip, and changes beyond region 20 affect the score. Matching cost is now quadratic per label rather than bounded by 20 regions, so dense-document CPU performance needs runtime measurement. This does not force a score based on matching filenames or image hashes.

This bug alone cannot explain 0.47 for truly identical, complete signatures: aspect, count, area, and grid components would already contribute 0.75. The user's exact 0.47 runtime result has not been reproduced locally. Do not increase weights or lower thresholds to conceal it.

After deploying the Backend changes, run Layout Simulation again to refresh stored reference signatures, then repeat New Document Test with the original file. In the completed prepublish job's response inspect:

`data.result.debug.timing.template_matching_breakdown[].compared_all[]`

Select the intended Template/reference page and compare `aspect_score`, `label_count_score`, `area_distribution_score`, `grid_score`, `spatial_score`, `query_region_count`, `template_region_count`, and `normalization_debug`.

`normalization_debug.source_pixels_identical` is `true` only when source BGR pixel hashes and original dimensions match, `false` when they differ, and `null` when either stored fingerprint is unavailable. Source dimensions and fingerprints are included in `query_before` and `template_before`. These diagnostics do not affect scoring. Hashes reuse the raw-layout cache key to avoid additional model calls and repeated hashing on the signature path.

If pixels differ, inspect query normalization/crop/resize and the actual selected reference image. If pixels match but regions differ, check stale signatures/model versions and raw detections. If all five components are 1.0 but the displayed Layout differs, inspect the candidate/page aggregation and frontend mapping using the same completed response.

Local validation: Python syntax, 27 Backend tests, and `git diff --check` passed. New tests verify full-region identity, changes after region 20, JSON round trip, full-count penalties, diagnostic-only hashes, and cache reuse. No real Kaggle image inference or reproduction of the reported 0.47 was performed.
