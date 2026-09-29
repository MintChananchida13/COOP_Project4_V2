# OCR Template Management Project

Document Intelligence platform for template-based OCR, ROI management, template detection, verification, extraction, review, processing logs, and export.

## Current Architecture

```text
Frontend -> Backend / Process Service -> Model API Gateway :8080 -> Leaf Model Services
```

The backend is a process and business service. It must not load PaddleOCR, Paddle, Torch, Transformers, or local model weights. All model inference goes through the Gateway.

The backend owns:

- Template lifecycle, versioning, publish review, and pending maintenance changes
- User upload, normalization, layout signature, template retrieval, verification, ROI projection, extraction, and processing logs
- ROI geometry, crop orchestration, OCR/table/image post-processing, scoring, and final decisions
- PostgreSQL access, request-scoped caches, process-level published template cache, and system settings

Leaf model services own only model loading and raw inference.

## Model Services

| Model | Leaf Service | Gateway Endpoint |
| --- | --- | --- |
| Layout | PP-DocLayoutV3 `:8001` | `/api/v1/document-layouts` |
| Text Detection | PP-OCRv6 medium det `:8002` | `/api/v1/text-detections` |
| Text Recognition | Thai Recognition `:8004` | `/api/v1/text-recognitions`, `/api/v1/text-recognition-batches` |
| Table | TableRecognitionPipelineV2 `:8013` | `/api/v1/table-model-results` |
| Image Verification | SigLIP `:8009` | `/api/v1/image-verifications` |

Layout signature calls use `layout_only=true` so the Layout pipeline runs PP-DocLayoutV3 only and does not run Text Detection.

## Project Structure

```text
COOP_Project4_Server/
  README.md
  PROJECT_MEMORY.md
  LOCAL_DEVELOPMENT.md
  project_backend/
    main.py
    requirements.txt
    env.local.example
    app/
      api/              HTTP routes and schemas
      business/         service layer and database-backed workflows
      core/             config, db, runtime client, shared helpers
      model_runtime/    backend adapters around remote model results
      processing/       detection, OCR, ROI, alignment, published template cache
    docs/
  project_frontend/
    src/
    package.json
    env.local.example
```

## Environment

Backend:

```env
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/ocr_studio
GATEWAY_URL=http://127.0.0.1:8080
MODEL_GATEWAY_API_KEY=replace-with-model-gateway-api-key
DB_POOL_MIN_CONNECTIONS=1
DB_POOL_MAX_CONNECTIONS=10
DB_POOL_RECYCLE_SECONDS=300
DB_POOL_MAX_IDLE_SECONDS=120
DETECTION_COORDINATE_DEBUG=false
```

Frontend:

```env
NEXT_PUBLIC_API_URL=http://localhost:8000
```

Do not use legacy leaf-service URLs in backend routing. Backend-to-Gateway requests use:

```text
Authorization: Bearer <MODEL_GATEWAY_API_KEY>
```

## User Flow

```text
Upload document
-> Adjust/confirm page images
-> /api/templates/detect-dev
-> Normalize pages
-> Layout Analysis
-> Layout Signature
-> Published Template Retrieval
-> Candidate Gate
-> Verification
-> Matched / No Match
```

Matched:

```text
Reuse matched template
-> Optional alignment decision using precomputed layout signatures
-> ROI projection
-> Extraction test
-> Text/Table/Image processing
-> Ground Truth review
-> Export
-> Processing Log save
```

No Match:

```text
Use mainPageAutoRoiPages / Auto ROI
-> Custom OCR workspace
-> Optional Template Request
-> Ground Truth review
-> Export
-> Processing Log save
```

Verification strategy labels in UI:

- `standard` = `การตรวจสอบแบบถ่วงน้ำหนักคะแนน`
- `strict` = `การตรวจสอบแบบลำดับเงื่อนไข`

Internal values remain `standard` and `strict`.

## Admin Flow

```text
Create Template / Template Request
-> Upload reference pages
-> Define Extraction ROI
-> Define Verification Anchors
-> Test Template
-> Review / Validate
-> Publish
-> Refresh published template cache
```

Template publish stores layout signatures on `template_pages`. Published template data is cached in process memory for detect hot paths:

- layout signatures
- template metadata and thresholds
- field count
- verification fields and anchors
- page image references used by alignment

PostgreSQL remains the source of truth. Process-local cache is refreshed after publish/update/unpublish/delete. Multi-worker deployments will need shared invalidation later.

## Detection Modes

`all_pages`:

- Compares pages by page number.
- Uses page-count gate.
- Candidate can be rejected if document page count does not match template page count.

`main_page`:

- Uses one configured `main_page_number` for template retrieval.
- Backend supports any page number >= 1.
- Current Admin UI presents this as page 1 / first page only.
- Non-main pages are not fully verified for template matching and continue through Auto ROI/processing.

## System Maintenance

System Maintenance is backend-enforced.

- Public/user processing endpoints return HTTP 503 with `SYSTEM_MAINTENANCE` while active.
- Admin endpoints remain available.
- User UI shows scheduled/active maintenance via the notification bell and banner.
- When maintenance starts or ends, the user workspace resets to Upload and cancels stale OCR/process state.

Pending changes are stored in `app_settings` and applied when Maintenance Start is reached:

- OCR model settings
- Verification strategy
- Template status/update/field operations

When enforcement is off, settings apply immediately. When enforcement is on, admin changes are pending until maintenance starts. Pending is cleared only after successful apply.

## Processing Logs

Processing Logs persist user run snapshots for Admin review:

- general run metadata
- source page references
- processing page references when they differ
- template detection snapshot and candidates when present
- matched template details
- ROI snapshot with coordinate metadata
- OCR original results
- Ground Truth values
- timing metadata
- stored page images under `app/storage/processing_logs/{logId}/...`

Admin Processing Log Detail supports preview, ROI overlay, result filtering, image fields, export, and manage-log actions.

## Validation

Backend:

```powershell
cd project_backend
python -m py_compile main.py
python -m py_compile app\business\services.py
python -m py_compile app\processing\detection_service.py
python -m py_compile app\processing\layout_template_matcher.py
python -m py_compile app\processing\published_template_cache.py
```

Frontend:

```powershell
cd project_frontend
npx tsc --noEmit --pretty false
npm run build
```

Health checks:

```powershell
Invoke-WebRequest http://localhost:8000/health -UseBasicParsing
Invoke-WebRequest http://localhost:8000/health/db -UseBasicParsing
Invoke-WebRequest http://localhost:8000/health/models -UseBasicParsing
```

## Important Files

Backend:

- `project_backend/main.py`
- `project_backend/app/core/db.py`
- `project_backend/app/core/model_runtime_client.py`
- `project_backend/app/business/services.py`
- `project_backend/app/processing/detection_service.py`
- `project_backend/app/processing/layout_template_matcher.py`
- `project_backend/app/processing/published_template_cache.py`
- `project_backend/app/processing/ocr_adapter.py`
- `project_backend/app/model_runtime/layout_analysis_service.py`

Frontend:

- `project_frontend/src/app/page.tsx`
- `project_frontend/src/admin/AdminSettingsPage.tsx`
- `project_frontend/src/admin/AdminTemplatesPage.tsx`
- `project_frontend/src/admin/AdminTemplateEditPage.tsx`
- `project_frontend/src/admin/AdminProcessingLogDetailPage.tsx`
- `project_frontend/src/admin/adminApi.ts`
- `project_frontend/src/user/components/MatchedTemplateWorkspaceZone.tsx`
- `project_frontend/src/user/components/GroundTruthEditorZone.tsx`
- `project_frontend/src/shared/workspace/WorkspaceCustomEditor.tsx`

## Deploy Notes

Before production deploy:

- run Gateway on the same server or private network, normally `127.0.0.1:8080`
- run all leaf model services behind Gateway
- set `GATEWAY_URL` and `MODEL_GATEWAY_API_KEY`
- verify `/health`, `/health/db`, and `/health/models`
- smoke test upload, detect, matched extraction, no-match Auto ROI, processing log save/detail, maintenance mode, template pending apply, and export
- keep model inference out of backend
