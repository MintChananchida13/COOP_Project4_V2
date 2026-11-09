# Project Memory

Source review: 2026-11-10 against this checkout. Documentation review only; no live database, model or deployment validation was performed.

# Intelligent Document Template Management System

This file is project-level memory. It records architecture, invariants, and rules that should not be changed casually.

## 1. Product Direction

This project is a Document Intelligence platform, not only an OCR app. It supports:

- User document upload and processing
- Template detection and matching
- Template lifecycle and versioning
- Extraction ROI and verification anchor management
- OCR, table, and image extraction
- Ground Truth review and export
- Processing Logs for Admin audit
- System Maintenance with pending settings/template changes

## 2. Architecture Boundary

```text
Frontend
  -> Backend / Process Service
      -> Model API Gateway :8080
          -> Leaf Model Services
```

Backend is the stable owner of process and business logic.

Backend must not:

- load local AI model weights
- import or use PaddleOCR/Paddle/Torch/Transformers for inference
- call `model.predict()`
- fallback to local inference
- call leaf service URLs directly

Backend must call Gateway only.

## 3. Leaf Model Services

| Capability | Gateway Endpoint |
| --- | --- |
| Layout / PP-DocLayoutV3 | `/paddle/api/v1/document-layouts` |
| Text Detection | `/paddle/api/v1/text-detections` |
| Text Recognition | `/paddle/api/v1/text-recognitions`, `/paddle/api/v1/text-recognition-batches` |
| Table Recognition | `/paddle/api/v1/table-model-results` |
| Image Verification / SigLIP | `/paddle/api/v1/image-verifications` |

Leaf services own raw inference only. Backend owns interpretation, filtering, scoring, quality gates, and final output shaping.

## 4. Persistent Data Principles

Persistent ROI should be ratios, not pixels.

Every persisted ROI-like field should preserve:

- page context
- coordinate space
- x/y/width/height ratios or equivalent points
- source/reference image relationship when needed

Pixel coordinates are runtime geometry only unless explicitly stored as a display/reference snapshot.

## 5. Template Data

Templates are the source of document knowledge.

A template version may include:

- pages
- normalized/sample page images
- optional legacy layout signatures (not required by current matching/publish)
- extraction fields
- verification anchors
- ignore regions
- detection mode
- main page number
- thresholds and weights
- status and version metadata

Published template data is loaded into process-level RAM cache for detection hot paths. PostgreSQL remains the source of truth.

Current published cache covers:

- optional legacy layout signatures (not required by current matching/publish)
- template metadata and thresholds
- field count
- verification fields and anchors
- page image source used by alignment

Process-local cache is acceptable for the current deployment. Multi-worker/multi-server deployment will require shared invalidation.

## 6. Detection Pipeline

```text
Upload/confirm image
-> prepare pages
-> prepare/normalize according to confirmed-image context
-> published template retrieval
-> page-count/orientation/aspect gates
-> staged Text/Image Anchor verification
-> final decision
-> ROI load/projection or Auto ROI
```

Important current optimizations:

- Matching and publish do not require Layout Signatures. The retained diagnostic signature analyzer requests combined Layout+DET (`layout_only=false`). Auto ROI supports Layout-only requests independently.
- Published Template Cache removes stable template reads from the hot path.
- Verification strategy is cached in process memory.
- Request-scoped cache reuses template metadata, field count, and verification fields inside one detection request.
- Preserve image/coordinate references for ROI projection; do not restore signature generation as a publish requirement.
- User detection no longer runs redundant full-page adaptive Text Detection for ROI projection.
- Coordinate debug is disabled unless `DETECTION_COORDINATE_DEBUG=true`.

Candidate retrieval is not the final decision. Final confirmation still uses verification and decision services.

## 7. Detection Modes

`all_pages`:

- Matches pages by page number.
- Uses page-count gate.
- Candidate can be rejected if query page count differs from template page count.

`main_page`:

- Uses one configured `main_page_number`.
- Backend supports any page number >= 1.
- Current Admin UI presents this as first page/page 1 only.
- Non-main pages continue through Auto ROI / processing and are not fully template-verified.

## 8. Verification Modes

Internal values:

- `standard`
- `strict`

User-facing labels:

- `standard` -> `แบบมาตรฐาน (standard)`
- `strict` -> `แบบเข้มงวด (strict)`

Do not rename internal values without migration and API changes.

Both modes require every in-scope Anchor, including previously optional ones. Each stage schedules one next Anchor per surviving template. Standard runs Text/Image batches concurrently; Strict runs Text then Image. A complete pass with the most Anchors wins, ties use ascending template_id. Legacy weights/thresholds do not select the winner. Text Anchors use normalized literal Levenshtein matching, not regex/match_type.

## 9. Extraction Pipeline

Matched flow:

```text
Template match
-> optional alignment decision
-> ROI projection
-> crop ROI
-> Text/Table/Image processing
-> OCR post-process / table shaping / image result
-> Ground Truth review
-> export
-> Processing Log snapshot
```

No-match flow:

```text
Auto ROI
-> Custom OCR workspace
-> selected ROI extraction
-> Ground Truth review
-> optional Template Request
-> Processing Log snapshot
```

Text Detection should run at crop/extraction time, not as a redundant full-page adaptive projection pass in User Detection.

## 10. Flexible ROI

Flexible ROI is a search/boundary concept, not necessarily the final OCR crop.

Current behavior to preserve unless explicitly redesigned:

- Fixed and flexible fields keep template/projected ROI metadata.
- Extraction uses the ROI selected by the extraction flow.
- Flexible child ROI behavior and LayoutV3 integration must be audited before changing.

## 11. Processing Logs

Processing Logs are real persisted audit data, not mocks.

They should preserve:

- run metadata
- source pages
- processing pages when coordinate space differs
- template detection data
- matched template data
- ROI snapshot
- OCR original
- Ground Truth
- timing
- page images in PostgreSQL `processing_log_images`, separated into source/processing; legacy local images can be backfilled

Admin detail must not fabricate missing candidates or results. If data is absent, trace persistence first.

## 12. System Maintenance

System Maintenance is backend source of truth and backend-enforced.

When active:

- user processing endpoints return HTTP 503 `SYSTEM_MAINTENANCE`
- Admin endpoints remain usable
- User UI shows maintenance state
- User workspace is reset to Upload and stale processing state is cut

Pending changes are stored in `app_settings` and applied when maintenance starts:

- OCR model settings
- Verification strategy
- Template status/update/field operations

Rules:

- Enforcement off -> apply changes immediately.
- Enforcement on -> save pending and keep active user configuration unchanged.
- Apply pending at maintenance start.
- Clear pending only after successful apply.
- If apply fails, keep pending for retry.
- Backend restart must not lose pending changes.

## 13. Storage

Detection queries should persist page images under:

```text
storage/detection_queries/{queryId}/page_N.png
```

Processing Log images are persisted in PostgreSQL `processing_log_images`. Legacy files may remain under `app/storage/processing_logs/{logId}/...` and are backfilled on preview or by the migration script. New logical references do not require a local file. Preserve source/processing coordinate spaces.

Do not send page base64 from frontend for Processing Log persistence.

## 14. Frontend Principles

UI should feel like one application across Login, User, and Admin:

- white surfaces
- slate text
- blue/indigo accent
- compact cards and subtle borders
- no unnecessary decorative hero style for operational screens

Do not reintroduce Processing Log mocks into Admin Dashboard or Log pages.

Authenticated image previews should use authenticated fetch -> blob/object URL and clean up object URLs.

## 15. Do Not Remove Without Care

- Relative ROI
- Multi-page support
- Template versioning
- Detection mode
- Main page number
- Legacy layout signature data and diagnostic helpers (no active publish dependency)
- Published Template Cache
- Verification anchors
- Image verification categories
- System Maintenance pending settings
- Processing Log persistence
- Ground Truth review
- Backend/Gateway/Leaf service separation

## 16. Validation Habits

For backend changes:

```powershell
python -m py_compile main.py
python -m py_compile app\business\services.py
python -m py_compile app\processing\detection_service.py
```

For frontend changes:

```powershell
npx tsc --noEmit --pretty false
```

## Current source boundaries

Runtime schema is `app/core/db.py` (template_groups/template_versions/extraction_fields/verification_anchors); old Frontend Prisma schema is not the runtime source. Publish jobs validate Anchor readiness and respect Maintenance pending changes. SigLIP uses the local V2.4 policy with 8 Positive + 7 Negative prompts, tolerance 0.8 and optional relative gates disabled by default. No sibling API source directory is required.

Current application auth remains mock/permissive: UI role cookies are not Backend authorization, several routes have no auth dependencies, and context=admin is not a verified privilege. See the Developer Guide before public deployment. Detection/prepublish jobs are process-local as well as caches; do not assume multi-worker polling works. Historical validation counts in older reports are not a current test run.
