# Local Development

Use local frontend and backend, with model inference hosted on Kaggle.

## Backend

Create `project_backend/.env.local` from `project_backend/env.local.example`
and set the internal model service URLs:

```env
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/ocr_studio
LAYOUT_MODEL_URL=http://127.0.0.1:8101
TEXT_DETECTION_MODEL_URL=http://127.0.0.1:8102
TEXT_RECOGNITION_MODEL_URL=http://127.0.0.1:8103
TABLE_MODEL_URL=http://127.0.0.1:8104
IMAGE_VERIFICATION_MODEL_URL=http://127.0.0.1:8105
```

Run:

```powershell
.\run-local-backend.ps1
```

Backend URL:

```text
http://localhost:8000
```

Local checks:

```powershell
Invoke-WebRequest http://localhost:8000/health -UseBasicParsing
Invoke-WebRequest http://localhost:8000/health/db -UseBasicParsing
Invoke-WebRequest http://localhost:8000/health/models -UseBasicParsing
```

## Frontend

Create `project_frontend/.env.local` from
`project_frontend/env.local.example`:

```env
NEXT_PUBLIC_API_URL=http://localhost:8000
```

Run:

```powershell
.\run-local-frontend.ps1
```

Frontend URL:

```text
http://localhost:3000
```

The frontend talks to the local backend. The local backend talks to Kaggle model
runtimes through the five model URL environment variables.
