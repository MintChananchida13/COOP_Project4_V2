# Local Development

## Backend Environment

Create `project_backend/.env.local` from `project_backend/env.local.example`.

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

The backend calls model inference through Gateway only.

The backend starts with two built-in OCR models: `ocr_det_v6_medium`
(PP-OCRv6 Medium) and `thai_ocr_v5_mobile` (Thai PP-OCRv5 Mobile).
Settings and inference share `app/core/ocr_model_config.py`. Additional models
and active selections are stored in backend settings and used by inference.
The obsolete `ocr_det_v5_server` default is not restored automatically.
It does not fetch, register, or warm up models through Gateway `/ocr-models`
endpoints. Adding or editing a model sends a synthetic image through both its
single and batch processing APIs before saving. The Admin model check performs
the same real requests. Failed requests or a mismatched model identity prevent
saving. The service supports both the original response model field and
`model_selection.model_name` metadata from `leaf-inference-v1` responses.
Models must already be installed and served by the external model service;
adding a backend configuration does not deploy model weights.

Inference continues through the five model APIs under `/paddle/api/v1`:
`document-layouts`, `text-detections`, `text-recognitions`,
`table-model-results`, and `image-verifications`, with the existing batch APIs.
Keep `GATEWAY_URL` as the host base URL, without adding `/paddle` to it.

## Template verification and publishing

Only `standard` and `strict` are supported. Both use Text/Image Anchors:

- `standard` runs Text OCR and Image verification concurrently, evaluates all
  templates, and selects the highest-scoring passing template.
- `strict` finishes Text checks before Image checks for each template. A failed
  Text check skips that template's Image verification.

`POST /admin/templates/{id}/prepublish-simulation` checks stored page images,
anchor ROIs, expected text, and enabled image categories. It returns `passed`,
`readiness_pages`, `anchor_readiness_results`, and `issues`. This is a
configuration check; testing a new document still performs model inference.

The prepublish document test compares the selected draft with all active
templates, excludes inactive templates and other drafts, and evaluates the
entire pool using the current verification mode. Its result includes Top 5
and the draft's rank (the draft remains visible outside Top 5). The test passes
only when the draft passes verification and ranks first.

Publish jobs use `/admin/templates/{id}/publish-jobs` and
`/admin/publish-jobs/{job_id}/run-dev` or `complete-dev`. The old
`embedding-jobs` paths remain deprecated aliases. Both completion paths recheck
stored anchor readiness before activation and preserve Maintenance deferral.
Publishing no longer generates or requires Layout Signatures. The old
`generate-layout-embedding` endpoints have been removed.

Layout analysis remains available for Auto ROI and flexible OCR. A live model
test requires a configured `DATABASE_URL`, a reachable authenticated Gateway,
a stored template with anchors, and a representative test document. Automated
tests use isolated fixtures and do not establish live model accuracy.

## PostgreSQL

```powershell
docker run --name ocr-postgres `
  -e POSTGRES_DB=ocr_studio `
  -e POSTGRES_USER=postgres `
  -e POSTGRES_PASSWORD=postgres `
  -p 5432:5432 `
  -d postgres:16
```

## Backend

```powershell
cd project_backend
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

Backend URL:

```text
http://localhost:8000
```

Health checks:

```powershell
Invoke-WebRequest http://localhost:8000/health -UseBasicParsing
Invoke-WebRequest http://localhost:8000/health/db -UseBasicParsing
Invoke-WebRequest http://localhost:8000/health/models -UseBasicParsing
```

Useful compile checks:

```powershell
python -m py_compile main.py
python -m py_compile app\business\services.py
python -m py_compile app\processing\detection_service.py
python -m py_compile app\processing\layout_template_matcher.py
python -m py_compile app\processing\published_template_cache.py
```

## Frontend

Create `project_frontend/.env.local` from `project_frontend/env.local.example`.

```env
NEXT_PUBLIC_API_URL=http://localhost:8000
```

Run:

```powershell
cd project_frontend
npm install
npm run dev
```

Frontend URL:

```text
http://localhost:3000
```

Checks:

```powershell
npx tsc --noEmit --pretty false
npm run build
```

## Gateway And Leaf Services

For local server testing, run Gateway on:

```text
http://127.0.0.1:8080
```

Gateway forwards to the leaf services:

- Layout: `127.0.0.1:8001`
- Text Detection: `127.0.0.1:8002`
- Text Recognition: `127.0.0.1:8004`
- Image Verification: `127.0.0.1:8009`
- Table: `127.0.0.1:8013`

Backend endpoints should call only Gateway endpoints, not leaf service URLs.

## Smoke Test Checklist

After starting services:

1. Login as user and upload one document.
2. Confirm detect-dev returns a `queryId` and persists `storage/detection_queries/{queryId}/page_1.png`.
3. Verify matched and no-match flows can reach OCR.
4. Save/visit Processing Log detail in Admin.
5. Toggle System Maintenance schedule in Admin Settings.
6. Confirm User processing receives maintenance block while active and resets to Upload after maintenance ends.
7. Publish or update a template during Maintenance enforcement and confirm the change is pending until maintenance start.
