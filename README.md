# OCR Template Management Project

ระบบ OCR Template Management สำหรับอัปโหลดเอกสาร, ตรวจจับ Template, จัดการ ROI, อ่านข้อมูลด้วย OCR/Table Recognition, ตรวจสอบรูปภาพด้วย SigLIP และ export ผลลัพธ์

## Architecture

```text
Frontend -> Backend / Process Service -> Gateway :8080 -> Leaf Model Services
```

Backend เป็น Process Service เท่านั้น:

- ไม่ load PaddleOCR, Paddle, Torch, Transformers หรือ local model
- ไม่เรียก `model.predict()` ใน backend
- เรียก model inference ผ่าน Gateway เท่านั้น
- เก็บ business/process logic ไว้ใน backend เช่น ROI, crop, reading order, OCR/table post-processing, template matching, verification, database และ export

Leaf Model Services ที่ใช้งานจริง:

| Model | Leaf Service | Gateway Endpoint |
| --- | --- | --- |
| Layout | PP-DocLayoutV3 `:8001` | `/api/v1/document-layouts` |
| Text Detection | PP-OCRv5 `:8002` | `/api/v1/text-detections?version=v5` |
| Text Recognition | Thai Recognition `:8004` | `/api/v1/text-recognitions` |
| Text Recognition Batch | Thai Recognition `:8004` | `/api/v1/text-recognition-batches` |
| Table | TableRecognitionPipelineV2 `:8013` | `/api/v1/table-model-results` |
| Image Verification | SigLIP `:8009` | `/api/v1/image-verifications` |

## Project Structure

```text
COOP_Project4_Server/
  README.md
  LOCAL_DEVELOPMENT.md
  project_backend/
    main.py
    requirements.txt
    env.local.example
    app/
      api/              HTTP routes and schemas
      auth/             auth helpers
      business/         service layer and database-backed workflows
      core/             config, db, runtime client, shared helpers
      model_runtime/    backend adapters around remote model results
      processing/       ROI, OCR, detection, alignment, post-processing
    docs/
  project_frontend/
    src/
    package.json
    env.local.example
```

## Environment Variables

Backend:

```env
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/ocr_studio
GATEWAY_URL=http://127.0.0.1:8080
MODEL_GATEWAY_API_KEY=replace-with-model-gateway-api-key
```

Frontend:

```env
NEXT_PUBLIC_API_URL=http://localhost:8000
```

Every backend request to Gateway sends:

```text
Authorization: Bearer <MODEL_GATEWAY_API_KEY>
```

Do not use legacy `LAYOUT_MODEL_URL`, `TEXT_DETECTION_MODEL_URL`, `TEXT_RECOGNITION_MODEL_URL`, `TABLE_MODEL_URL`, or `IMAGE_VERIFICATION_MODEL_URL` in backend runtime routing.

## Local Development

Start PostgreSQL:

```powershell
docker run --name ocr-postgres `
  -e POSTGRES_DB=ocr_studio `
  -e POSTGRES_USER=postgres `
  -e POSTGRES_PASSWORD=postgres `
  -p 5432:5432 `
  -d postgres:16
```

Start backend:

```powershell
cd project_backend
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

Start frontend:

```powershell
cd project_frontend
npm install
npm run dev
```

URLs:

- Frontend: `http://localhost:3000`
- Backend: `http://localhost:8000`
- Gateway: `http://127.0.0.1:8080`

## Main Flows

User flow:

```text
Upload -> Adjust -> Detect Template -> ROI/OCR -> Ground Truth -> Export
```

Admin flow:

```text
Template Request / Manual Create -> Adjust -> Extraction ROI -> Verification ROI -> Pre-Publish/Test/Publish
```

## Auto ROI

Auto ROI uses Layout and Text Detection results from Gateway. Backend owns ROI post-processing:

- filters text inside table regions
- filters image regions that contain text
- merges tiny `text_line` fragments such as Thai tone marks, dots, and character pieces into nearby text lines when they overlap or are close
- discards isolated tiny text fragments when no nearby text line exists
- preserves table/image ROI behavior
- keeps reading order and ROI normalization in backend

## Model Runtime Contracts

Standard response wrapper:

```json
{
  "success": true,
  "model": "model-name",
  "result": {}
}
```

Request shapes:

```json
{ "image": "data:image/png;base64,..." }
```

Batch text recognition:

```json
{ "images": ["data:image/png;base64,..."] }
```

Image verification:

```json
{
  "image": "data:image/png;base64,...",
  "categories": [
    {
      "value": "signature",
      "label": "ลายเซ็น",
      "prompt": "This is a photo of a handwritten signature.",
      "match_threshold": 0.45,
      "margin_threshold": 0.04,
      "evidence_temperature": 1.0,
      "enabled": true
    }
  ]
}
```

Image verification must return raw SigLIP logits in the same order as categories. Backend owns ranking, thresholding, `passed`, status, failure reason, and UI percentages.

## Validation

Backend syntax check:

```powershell
cd project_backend
python -m py_compile main.py
python -m py_compile app\core\model_runtime_client.py
python -m py_compile app\model_runtime\layout_analysis_service.py
python -m py_compile app\model_runtime\table_recognition_v2_adapter.py
python -m py_compile app\model_runtime\siglip_image_verification_adapter.py
```

Backend health checks:

```powershell
Invoke-WebRequest http://localhost:8000/health -UseBasicParsing
Invoke-WebRequest http://localhost:8000/health/db -UseBasicParsing
Invoke-WebRequest http://localhost:8000/health/models -UseBasicParsing
```

Frontend checks:

```powershell
cd project_frontend
npx tsc --noEmit --pretty false
npm run build
```

## Important Files

Backend:

- `project_backend/main.py`
- `project_backend/app/core/model_runtime_client.py`
- `project_backend/app/core/config.py`
- `project_backend/app/core/db.py`
- `project_backend/app/business/services.py`
- `project_backend/app/business/image_verification_category_service.py`
- `project_backend/app/model_runtime/layout_analysis_service.py`
- `project_backend/app/model_runtime/paddle_thai_ocr_adapter.py`
- `project_backend/app/model_runtime/table_recognition_v2_adapter.py`
- `project_backend/app/model_runtime/siglip_image_verification_adapter.py`
- `project_backend/app/processing/ocr_adapter.py`
- `project_backend/app/processing/ocr_postprocess.py`
- `project_backend/app/processing/detection_service.py`

Frontend:

- `project_frontend/src/app/page.tsx`
- `project_frontend/src/user/components/UploadZone.tsx`
- `project_frontend/src/user/components/AdjustZone.tsx`
- `project_frontend/src/user/components/MatchedTemplateWorkspaceZone.tsx`
- `project_frontend/src/user/components/GroundTruthEditorZone.tsx`
- `project_frontend/src/shared/workspace/WorkspaceCustomEditor.tsx`
- `project_frontend/src/admin/AdminDashboard.tsx`
- `project_frontend/src/admin/AdminTemplateEditPage.tsx`
- `project_frontend/src/admin/AdminTemplateTestPage.tsx`
- `project_frontend/src/admin/adminApi.ts`

## Deploy Notes

Before production deploy:

- run Gateway on the same server, normally `127.0.0.1:8080`
- run all Leaf Model Services behind Gateway
- set `GATEWAY_URL` and `MODEL_GATEWAY_API_KEY`
- verify `/health`, `/health/db`, and `/health/models`
- smoke test document upload, template detection, OCR, table extraction, image verification, and export
- keep model inference out of backend
