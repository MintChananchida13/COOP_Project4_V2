# OCR Template Management Project

ระบบ OCR Template Management สำหรับอัปโหลดเอกสาร ค้นหา Template ที่ตรงกับเอกสาร กำหนด ROI ผ่าน User/Admin UI อ่านข้อมูลด้วย OCR/Table Recognition/Image Extraction และตรวจสอบผลก่อน Export

## Current Snapshot

- Frontend: `project_frontend` ใช้ Next.js + TypeScript
- Backend: `project_backend` ใช้ FastAPI + PostgreSQL + OpenCV/process logic; model inference เรียกผ่าน external Model Runtime URLs
- Database: PostgreSQL ผ่าน `DATABASE_URL` และใช้ Schema V2 เป็น source of truth
- Model Runtime: backend เรียก Model API แยกตามชนิดผ่าน `LAYOUT_MODEL_URL`, `TEXT_DETECTION_MODEL_URL`, `TEXT_RECOGNITION_MODEL_URL`, `TABLE_MODEL_URL`, และ `IMAGE_VERIFICATION_MODEL_URL`
- Auth ปัจจุบัน: Mock Login ชั่วคราว ใช้ role เพื่อ redirect/แสดงหน้า User หรือ Admin เท่านั้น ยังไม่บังคับ Bearer token
- User flow: Upload -> Adjust -> Detect Template -> ROI/OCR -> Ground Truth -> Export
- Admin flow: Template Request/Manual Create -> Adjust -> Extraction ROI -> Verification ROI -> Pre-Publish/Test/Publish หรือ Update

## Project Structure

```text
COOP_Project4/
  README.md
  PROJECT_MEMORY.md
  project_frontend/
    src/
      app/
      user/components/
      admin/
      admin/workspace/
      shared/workspace/
      types/ocr.ts
  project_backend/
    main.py
    app/
      db.py
      routes.py
      schemas.py
      services.py
      detection_service.py
      layout_analysis_service.py
      layout_signature_service.py
      layout_template_matcher.py
      ocr_adapter.py
      paddle_thai_ocr_adapter.py
      table_recognition_v2_adapter.py
      table_grid_analyzer.py
      ocr_postprocess.py
      model_runtime_client.py
      siglip_image_verification_adapter.py
    docs/
      database_schema_v2.md
    tests/
    requirements.txt
```

## Login

ระบบยังใช้ Mock Login เพื่อแยกหน้าใช้งาน:

- User: `user@ocr.com` / `user123`
- Admin: `admin@ocr.com` / `admin123`

Backend endpoint ปัจจุบันไม่บังคับ `Authorization: Bearer` และ user-related FK ใน Schema V2 รองรับ `NULL` ชั่วคราวเพื่อให้ flow หลักทำงานก่อนเปิด Full Auth จริง

## User Flow

1. ผู้ใช้อัปโหลดเอกสารได้ครั้งละ 1 ไฟล์
2. เข้า `AdjustZone` เพื่อตรวจภาพและครอปเอกสาร
3. Backend เรียก Template Detection
4. ถ้า `matched=true`
   - โหลด Template bundle
   - แสดง `MatchedTemplateWorkspaceZone`
   - แสดง ROI จาก Template
   - ถ้าเป็น Flexible ROI จะซ่อนกรอบแม่และแสดงเฉพาะ ROI ย่อยที่ตรวจพบจริง
5. ผู้ใช้เลือก field ที่ต้องการ OCR แก้ชื่อ และจัดลำดับได้
6. Backend ประมวลผล OCR ผ่าน `/api/ai/process`
7. แสดงผลใน `GroundTruthEditorZone`
8. Ground Truth auto-update ไม่มีปุ่มบันทึกแยก
9. Export ผ่าน popup เดียว รองรับ Word, Excel, JSON และ Images ZIP

## Admin Flow

Admin สร้าง Template ได้ 2 ช่องทาง:

- รับ Template Request จาก User
- สร้าง Template เองจากหน้า Template Library

Flow หลัก:

1. เลือก `Create New Template` หรือ `Add New Version`
2. อัปโหลดไฟล์ต้นทาง
3. สร้าง Template Group/Version ตาม Schema V2
4. เข้า `AdminTemplateEditPage`
5. เตรียม Template ตามขั้น:
   - `2.0` ปรับภาพ
   - `2.1` กำหนด Extraction ROI
   - `2.2` กำหนด Verification ROI
   - `2.3` ตั้งค่า Final Score/Matching Weights เฉพาะกรณีอัปเดต Template ที่ publish แล้ว
6. Draft Template เข้า Pre-Publish Template Validation
7. Published Template ที่แก้ไขแล้วใช้ flow Update Template

## Pre-Publish Validation

Pre-Publish ใช้กับ Draft Template ก่อน Publish:

1. `Step 1 Review ROI & OCR`
   - แสดง ROI/OCR Preview
   - ตั้งค่า Final Confidence Threshold และ Matching Weights
   - ค่าเริ่มต้น Final Confidence Threshold คือ `0.75`
2. `Step 2 Layout Simulation`
   - สร้าง Layout Signature จริงจาก `template_pages`
   - บันทึกลง `template_pages.layout_signature_json`
   - ใช้ `layout_signature_pages` จาก backend เป็นผลหลัก
   - ถ้า required pages มีสถานะ `generated` และ simulation `passed=true` จะปลด Step 3
3. `Step 3 New Document Test`
   - เรียก Template Detection pipeline กลาง
   - ใช้ `include_template_id` เพื่อให้ draft/current version ที่กำลังทดสอบอยู่ใน candidate ranking เสมอ
   - ตารางผลการจัดอันดับแสดงทั้ง active templates และ draft/current template ที่ include มา
   - ถ้า draft ไม่ผ่าน threshold จะยังแสดงในตารางพร้อมสถานะ fail ไม่หายไปเฉยๆ
4. `Step 4 Publish Review`
   - ตรวจสถานะรวมก่อน Publish
   - เมื่อ Publish สำเร็จจะแสดง popup

## Detection Mode

Template Version รองรับ `detection_mode`:

- `all_pages`: เทียบทุกหน้าตาม flow เดิม
- `main_page`: ใช้หน้าแรกเป็นหน้าหลักสำหรับค้นหา Template เท่านั้น จำนวนหน้าของ PDF ฝั่ง user ไม่กระทบคะแนน match

หลักการ Layout Signature:

- Fix ROI สามารถเป็นส่วนหนึ่งของ stable layout
- Flexible ROI ถูกเก็บเป็น search boundary สำหรับ runtime แต่ layout/content ภายใน Flexible boundary ไม่ควรทำให้คะแนน Template Match ลดลง
- หน้าอื่นที่ไม่ใช่ main page ไม่ใช้ค้นหา Template ซ้ำ หลัง match แล้วสามารถใช้ auto ROI ตามทั้งหน้าได้

## ROI Types

### Fix ROI

ใช้สำหรับข้อมูลที่ตำแหน่งคงที่

```text
Template ROI
-> Align/Map document
-> Crop ROI
-> Existing OCR/Table/Image pipeline
-> Field result
```

### Flexible ROI

Flexible ROI คือ Search Boundary ไม่ใช่กรอบ OCR โดยตรง

```text
Search Boundary
-> PP-DocLayoutV3 หา region ภายใน boundary
-> แยก Text/Table/Image ROI ย่อยตาม type
-> Text ใช้ Paragraph Grouper จาก line geometry
-> Crop Final ROI
-> OCR/Table/Image pipeline ตาม type
-> Field result
```

กติกาปัจจุบัน:

- ซ่อนกรอบแม่ Flexible ในฝั่ง user
- แสดงเฉพาะ ROI ย่อยที่ระบบตรวจพบจริง
- ROI ย่อยถูกนับเป็น field ให้ user เลือก แก้ชื่อ และจัดลำดับได้
- Paragraph Grouper ใช้ geometry เท่านั้น ไม่ใช้ keyword หรือ OCR text
- ถ้า evidence ไม่ชัด จะ merge ไว้ก่อนเพื่อลด false split

## Auto ROI

Auto ROI ใช้ `PP-DocLayoutV3` และ `PP-OCRv5_server_det`

การกรองหลัก:

- กรอง text ROI ที่ซ้อนใน text ROI ใหญ่กว่า
- กรอง text fragment เล็กผิดปกติ เช่น วรรณยุกต์หรือเศษตัวอักษร
- กรอง text ที่อยู่ใน table region
- กรอง image region ที่มี text ภายใน
- Table ROI มี padding เล็กน้อยเพื่อป้องกันตัดเส้นขอบตาราง

User และ Admin ใช้ backend auto ROI/filter กลางผ่าน `layout_analysis_service.py`

## Text OCR Pipeline

Fix ROI และ Flexible ROI ต่างกันเฉพาะวิธีหา Final ROI หลังจากนั้นต้องใช้ OCR core เดียวกัน:

```text
Document
-> Layout / ROI Resolution
-> Crop Final ROI
-> PP-OCRv5_server_det
-> Text Line Polygons
-> Crop/Rectify text line
-> th_PP-OCRv5_mobile_rec
-> Reading Order
-> Merge Segments
-> normalize_ocr_text()
-> cleanup_ocr_noise()
-> Final Result
```

Output หลัก:

- `text`
- `confidence`
- `segments`
- `raw_segments`

Post-process ใช้ `ocr_postprocess.py` เพื่อลด noise เช่น dotted line, punctuation ขยะ และตัวอักษรอังกฤษเดี่ยวที่หลุดมาแบบ conservative โดยไม่ post-process ซ้ำกับ `groundTruth` ที่ผู้ใช้แก้เอง

## Table Recognition

สำหรับ field type `table` ระบบต้องพยายามคืนข้อมูลตารางเสมอถ้ามี OCR text

Flow หลัก:

```text
Table ROI
-> TABLE_MODEL_URL /predict
-> raw SLANeXt_wired / SLANeXt_wireless output
-> Backend parses SLANeXt structure
-> Structure Collapse Detection/Recovery
-> Quality Gate
-> Semi/Fallback ถ้าจำเป็น
```

Fallback order:

1. Remote SLANeXt raw inference result
2. Structure Collapse Recovery
3. Semi Table / geometry path เมื่อ SLANeXt ไม่มั่นใจ
4. OCR-to-Table
5. Raw OCR Geometry Table

หลักการสำคัญ:

- ตารางปกติให้ SLANeXt เป็นหลัก
- Semi Table ต้องเป็น fallback ไม่ใช่ default สำหรับตารางมีเส้นครบ
- Structure Collapse ตรวจหลัง SLANeXt ทั้ง wired/wireless โดยใช้ OCR bbox geometry
- ตรวจเฉพาะ body/data region เพื่อรักษา header, merged cells และ summary ที่ SLANeXt อ่านถูก
- ห้ามลดทอน row/column ว่างที่ model อ่านโครงสร้างมาได้
- Ground Truth Table Editor ต้องแสดง `rowSpan`, `colSpan`, hidden cells และ empty rows ตาม structured data

Schema ตารางกลางต้องรักษา:

- `row`
- `col`
- `rowSpan`
- `colSpan`
- `hidden`
- `bbox`
- `text`
- `ocrText`
- `groundTruth`

Debug trace สำหรับ Table Recognition เปิดด้วย:

```powershell
$env:TABLE_DEBUG_TRACE="1"
```

เมื่อเปิด จะเก็บ trace ใต้ `table_debug` เช่น input image hash, Paddle raw output, parsed structure, postprocessed snapshot, OCR assignment และ final result

## Model Runtime

Backend เรียก Model Runtime แยกตามชนิดผ่าน Environment Variables:

- `LAYOUT_MODEL_URL`
- `TEXT_DETECTION_MODEL_URL`
- `TEXT_RECOGNITION_MODEL_URL`
- `TABLE_MODEL_URL`
- `IMAGE_VERIFICATION_MODEL_URL`

Backend เป็นเจ้าของ process logic ทั้งหมด และ Model Runtime ทำเฉพาะ model loading + inference ผ่าน `POST /predict` และ `GET /health`.

ถ้า URL ของ model ที่จำเป็นว่าง backend จะไม่ fallback ไป local PaddleOCR/SigLIP และจะ error ชัดเจน.

โมเดล/เครื่องมือหลัก:

- `PP-DocLayoutV3`: Layout, Auto ROI, Layout Signature
- `PP-OCRv5_server_det`: Text Detection
- `th_PP-OCRv5_mobile_rec`: Thai OCR
- `TableRecognitionPipelineV2` / SLANeXt in external Table Model Runtime only
  - `SLANeXt_wired`
  - `SLANeXt_wireless`
- `SigLIP`: Image Verification
- `OpenCV`: geometry, line detection, table utilities

## Export

Export อยู่ใน popup เดียวใน `GroundTruthEditorZone`

Formats:

- Word
- Excel
- JSON
- Images ZIP

Content จะแสดงเฉพาะ type ที่มีอยู่จริงในเอกสาร:

- Text
- Tables
- Images

Table export modes:

- `structure`: ส่งออกตามโครงสร้างตารางเดิม
- `key_value`: ใช้ resolved multi-level header เป็น key และ data rows เป็น records

Key-Value รองรับ:

- Multi-level header
- เลือก row/column ผ่าน dropdown checkbox
- Summary Region แยกจาก Data Region
- Preview ก่อน export

Excel:

- สร้างเฉพาะ sheet ของ type ที่มีอยู่จริง
- จัดข้อมูลชิดซ้าย
- image field ใส่ภาพจริงใน cell และรักษา aspect ratio

JSON:

- ส่ง text/table ตามข้อมูลที่ผู้ใช้แก้แล้ว
- image field ส่งตาม export policy ปัจจุบันโดยไม่ผูกกับ path ภายในระบบ

## Database Schema V2

Database ปัจจุบันใช้ Schema V2 ผ่าน `project_backend/app/db.py` และเอกสารเต็มอยู่ที่:

- `project_backend/docs/database_schema_v2.md`

ตารางหลัก:

```text
users

template_groups
└── template_versions
    ├── template_pages
    │   ├── extraction_fields
    │   ├── verification_anchors
    │   └── ignore_regions
    ├── version_test_cases
    └── publish_jobs

template_requests
└── template_request_pages
    └── requested_fields

ocr_jobs

image_verification_categories
```

Developer views:

- `template_versions_view`
- `template_fields_view`
- `verification_anchors_view`

Legacy tables ที่ไม่สร้างใน Schema V2:

- `templates`
- `template_fields`
- `template_layout_references`
- `embedding_jobs`

หมายเหตุ: Schema V2 เป็น fresh database schema ไม่ใช่ migration จาก schema เก่า

## Important Backend APIs

OCR:

- `POST /api/ai/process`
- `GET /api/ai/jobs/{job_id}`

Layout:

- `POST /api/layout/analyze`

Template Detection:

- `POST /api/templates/detect-dev`

Template Requests:

- `GET /template-requests`
- `POST /template-requests`
- `GET /template-requests/{id}`
- `POST /template-requests/{id}/submit`
- `POST /template-requests/{id}/requested-fields`
- `GET /admin/template-requests`
- `GET /admin/template-requests/{id}`
- `POST /admin/template-requests/{id}/convert-to-template`
- `POST /admin/template-requests/{id}/convert-to-version`
- `DELETE /admin/template-requests/{id}`

Templates:

- `GET /admin/templates`
- `POST /admin/templates`
- `GET /admin/templates/{id}`
- `PUT /admin/templates/{id}`
- `DELETE /admin/templates/{id}`
- `POST /admin/templates/{id}/pages`
- `PUT /admin/templates/{id}/pages/{pageId}`
- `POST /admin/templates/{id}/fields`
- `PUT /admin/templates/{id}/fields/{fieldId}`
- `DELETE /admin/templates/{id}/fields/{fieldId}`

Pre-Publish:

- `POST /admin/templates/{template_id}/prepublish-simulation`
- `POST /admin/templates/{template_id}/prepublish-detection-test`
- `POST /admin/templates/{template_id}/confirm-publish`

Image Verification:

- `GET /admin/image-verification-categories`
- `POST /admin/image-verification-categories`
- `PUT /admin/image-verification-categories/{category_value}`
- `DELETE /admin/image-verification-categories/{category_value}`

## Local Setup

### PostgreSQL

```powershell
docker run --name ocr-postgres `
  -e POSTGRES_DB=ocr_studio `
  -e POSTGRES_USER=postgres `
  -e POSTGRES_PASSWORD=postgres `
  -p 5432:5432 `
  -d postgres:16
```

### Model Runtime

Model Runtime runs outside this backend, for example on Kaggle demo URLs now or permanent model services later. This backend does not load Paddle/Torch models.

Each model runtime should expose the same contract:

```text
GET /health
POST /predict
```

Response wrapper:

```json
{
  "success": true,
  "model": "model-name",
  "result": {}
}
```

### Backend

```powershell
cd project_backend
.\venv\Scripts\activate
pip install -r requirements.txt
$env:DATABASE_URL="postgresql://postgres:postgres@localhost:5432/ocr_studio"
$env:LAYOUT_MODEL_URL="https://kaggle-demo-layout.example"
$env:TEXT_DETECTION_MODEL_URL="https://kaggle-demo-text-detection.example"
$env:TEXT_RECOGNITION_MODEL_URL="https://kaggle-demo-text-recognition.example"
$env:TABLE_MODEL_URL="https://kaggle-demo-table.example"
$env:IMAGE_VERIFICATION_MODEL_URL="https://kaggle-demo-image-verification.example"
uvicorn main:app --reload
```

Backend URL:

```text
http://localhost:8000
```

### Frontend

```powershell
cd project_frontend
npm install
npm run dev
```

Frontend URL:

```text
http://localhost:3000
```

## Validation Commands

For local frontend/backend testing with Kaggle model runtimes, see `LOCAL_DEVELOPMENT.md`.

Frontend:

```powershell
cd project_frontend
npx tsc --noEmit --pretty false
npm run build
```

Backend syntax:

```powershell
cd project_backend
python -m py_compile main.py app/db.py app/schemas.py app/services.py app/routes.py app/detection_service.py app/layout_analysis_service.py app/ocr_adapter.py app/paddle_thai_ocr_adapter.py app/table_recognition_v2_adapter.py app/model_runtime_client.py app/siglip_image_verification_adapter.py
python -c "import main; print('import main ok')"
```

Backend focused tests:

```powershell
cd project_backend
python -m unittest tests.test_layout_template_matcher
python -m unittest tests.test_prepublish_multi_page_matching
python -m unittest tests.test_detection_include_draft_candidate
python -m unittest tests.test_table_recognition_v2_adapter
```

## Key Files

Frontend:

- `project_frontend/src/app/page.tsx`: User OCR Studio และ detection flow
- `project_frontend/src/user/components/UploadZone.tsx`: อัปโหลดไฟล์
- `project_frontend/src/user/components/AdjustZone.tsx`: ปรับภาพและครอปเอกสาร
- `project_frontend/src/user/components/MatchedTemplateWorkspaceZone.tsx`: Workspace หลังเจอ Template
- `project_frontend/src/user/components/GroundTruthEditorZone.tsx`: Ground Truth, Table Editor, Export Preview
- `project_frontend/src/shared/workspace/WorkspaceCustomEditor.tsx`: ROI canvas/editor กลาง
- `project_frontend/src/admin/AdminDashboard.tsx`: ภาพรวม Admin
- `project_frontend/src/admin/AdminRequestsPage.tsx`: คำขอ Template
- `project_frontend/src/admin/AdminTemplatesPage.tsx`: คลัง Template
- `project_frontend/src/admin/AdminRequestDetailPage.tsx`: รายละเอียดคำขอ/สร้าง Template
- `project_frontend/src/admin/AdminTemplateEditPage.tsx`: Admin 2.0/2.1/2.2/2.3
- `project_frontend/src/admin/AdminTemplateTestPage.tsx`: Pre-Publish Validation
- `project_frontend/src/admin/adminApi.ts`: API mapper ฝั่ง Admin/Shared

Backend:

- `project_backend/main.py`: FastAPI app และ OCR API
- `project_backend/app/db.py`: Schema V2 bootstrap
- `project_backend/app/schemas.py`: API schemas
- `project_backend/app/routes.py`: HTTP routes
- `project_backend/app/services.py`: Template/Request/Admin service layer
- `project_backend/app/detection_service.py`: Template detection pipeline
- `project_backend/app/layout_template_matcher.py`: Layout signature candidate search
- `project_backend/app/layout_signature_service.py`: Layout signature build/compare
- `project_backend/app/layout_analysis_service.py`: Layout/Auto ROI/TextDetection gateway
- `project_backend/app/ocr_adapter.py`: OCR ROI pipeline
- `project_backend/app/paddle_thai_ocr_adapter.py`: Thai OCR adapter
- `project_backend/app/table_recognition_v2_adapter.py`: Table recognition/fallback
- `project_backend/app/table_grid_analyzer.py`: Table geometry utilities
- `project_backend/app/ocr_postprocess.py`: OCR text cleanup
- `project_backend/app/model_runtime_client.py`: Remote runtime client

## Known Risks / TODO

- Full Auth ยังปิดไว้ชั่วคราว ต้องกลับมาเปิดและทดสอบ role/FK ก่อน production จริง
- `detect-dev` endpoint name ยังเป็น legacy แม้ถูกใช้ใน real flow
- Table OCR ยังขึ้นกับคุณภาพ ROI, เส้นตาราง, SLANeXt output และ OCR geometry
- Semi Table ต้องคุมไม่ให้เข้าเร็วเกินไปสำหรับตารางปกติ
- Multi-page/main-page detection ยังควรเพิ่ม regression test จากเอกสารจริงหลายรูปแบบ
- Schema V2 เป็น fresh schema ถ้าจะใช้กับ DB เก่าต้องวางแผน migration แยก
