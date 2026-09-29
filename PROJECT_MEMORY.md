# Project Memory

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
| Layout / PP-DocLayoutV3 | `/api/v1/document-layouts` |
| Text Detection | `/api/v1/text-detections` |
| Text Recognition | `/api/v1/text-recognitions`, `/api/v1/text-recognition-batches` |
| Table Recognition | `/api/v1/table-model-results` |
| Image Verification / SigLIP | `/api/v1/image-verifications` |

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
- layout signatures
- extraction fields
- verification anchors
- ignore regions
- detection mode
- main page number
- thresholds and weights
- status and version metadata

Published template data is loaded into process-level RAM cache for detection hot paths. PostgreSQL remains the source of truth.

Current published cache covers:

- layout signatures
- template metadata and thresholds
- field count
- verification fields and anchors
- page image source used by alignment

Process-local cache is acceptable for the current deployment. Multi-worker/multi-server deployment will require shared invalidation.

## 6. Detection Pipeline

```text
Upload/confirm image
-> prepare pages
-> normalize
-> layout analysis
-> layout signature
-> layout retrieval from published templates
-> candidate gate
-> verification
-> final decision
-> ROI load/projection or Auto ROI
```

Important current optimizations:

- Layout signature path calls Layout with `layout_only=true`.
- Published Template Cache removes stable template reads from the hot path.
- Verification strategy is cached in process memory.
- Request-scoped cache reuses template metadata, field count, and verification fields inside one detection request.
- Alignment reuses precomputed query/template layout signatures.
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

- `standard` -> `การตรวจสอบแบบถ่วงน้ำหนักคะแนน`
- `strict` -> `การตรวจสอบแบบลำดับเงื่อนไข`

Do not rename internal values without migration and API changes.

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
- page image files under processing log storage

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

Processing Logs copy required pages into:

```text
app/storage/processing_logs/{logId}/...
```

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
- Layout signature
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
