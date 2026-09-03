# OCR Template Management Frontend

Next.js + TypeScript frontend for the OCR Template Management system.

## Environment

Create `project_frontend/.env.local`:

```env
NEXT_PUBLIC_API_URL=http://localhost:8000
```

## Run Locally

```powershell
cd project_frontend
npm install
npm run dev
```

Open:

```text
http://localhost:3000
```

The frontend talks to the backend through `NEXT_PUBLIC_API_URL`. Model inference is not called from the frontend directly; backend sends model requests to Gateway.

## Checks

```powershell
npx tsc --noEmit --pretty false
npm run build
```
