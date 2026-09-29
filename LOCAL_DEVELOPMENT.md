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
