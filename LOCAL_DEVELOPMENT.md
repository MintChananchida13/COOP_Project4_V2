# Local Development

## Backend Environment

Create `project_backend/.env.local` from `project_backend/env.local.example`:

```env
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/ocr_studio
GATEWAY_URL=http://127.0.0.1:8080
MODEL_GATEWAY_API_KEY=replace-with-model-gateway-api-key
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

## Frontend

Create `project_frontend/.env.local` from `project_frontend/env.local.example`:

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

## Gateway

For local server testing, run Gateway on:

```text
http://127.0.0.1:8080
```

Gateway forwards to the leaf model services:

- Layout: `127.0.0.1:8001`
- Text Detection: `127.0.0.1:8002`
- Text Recognition: `127.0.0.1:8004`
- Table: `127.0.0.1:8013`
- Image Verification: `127.0.0.1:8009`
