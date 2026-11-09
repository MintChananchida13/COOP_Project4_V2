# คู่มือสำหรับนักพัฒนา COOP_Project4_New

ปรับปรุงวันที่ 10 พฤศจิกายน 2569 (2026-11-10)

คู่มือนี้ใช้สำหรับรับช่วงพัฒนา: เตรียมเครื่อง รันระบบ เข้าใจงานหลัก เลือกไฟล์ที่ต้องแก้ และตรวจปัญหา เนื้อหาอิง implementation ใน repository ส่วนรายละเอียดภายในและรายการ declaration ทั้งหมดอยู่ใน [เอกสารอ้างอิงเชิงลึก](DEVELOPER_REFERENCE_TH.md)

| ส่วน | ใช้เมื่อ |
| --- | --- |
| 1. ภาพรวมระบบ | ต้องการเข้าใจว่าแต่ละส่วนทำหน้าที่อะไร |
| 2. ติดตั้งและรัน | เริ่มทำงานบนเครื่องพัฒนา |
| 3. ตั้งค่าที่จำเป็น | เปลี่ยน DB, Backend URL หรือ Gateway |
| 4. โครงสร้างโค้ด | เลือกจุดที่ต้องอ่านและแก้ |
| 5. Workflow หลัก | พัฒนา User flow หรือ Template flow |
| 6. Database และ API | เชื่อมต่อข้อมูลหรือเพิ่มความสามารถ |
| 7. ขั้นตอนแก้ไขและตรวจงาน | ตรวจว่าการเปลี่ยนแปลงกระทบส่วนใด |
| 8. แก้ปัญหาและข้อจำกัดสำคัญ | ระบบไม่ทำงานหรือเตรียมติดตั้งใช้งาน |

## 1. ภาพรวมระบบ

ระบบรับเอกสาร ตรวจหา Template ที่ตรงกับเอกสาร แล้วอ่านข้อมูลจากพื้นที่ ROI ผู้ใช้ตรวจทานผลและส่งออกข้อมูลได้ ฝั่ง Admin จัดการ Template, เงื่อนไขตรวจสอบ และดู Processing Logs

```text
Browser / Next.js
    -> FastAPI Backend
         -> PostgreSQL: Template, settings, jobs และ logs
         -> Model Gateway: Layout, Text DET/REC, Table และ SigLIP
```

| ส่วน | หน้าที่ |
| --- | --- |
| Frontend | เตรียมภาพ/PDF, จัดการ ROI, แสดงผล OCR, ตรวจทาน และ export |
| Backend | API, เลือก Template, ตรวจ Anchor, ประมวลผล ROI และจัดเก็บข้อมูล |
| PostgreSQL | เก็บข้อมูลหลักและสถานะ OCR/Publish jobs |
| Model Gateway | รับคำขอโมเดลจาก Backend ผ่าน HTTP |

Frontend เรียก Backend ผ่าน NEXT_PUBLIC_API_URL การเรียกโมเดลอยู่ฝั่ง Backend ใน checkout นี้ไม่มี implementation ของ Gateway ภายนอกให้ติดตั้งจาก main.py

เทคโนโลยีหลักที่ระบุใน dependency: Next.js 16.2.9, React 19.2.4, FastAPI 0.110.0, Pydantic 2.6.4 และ psycopg2 สำหรับ PostgreSQL ดูรายการจริงที่ [package.json](project_frontend/package.json) และ [requirements.txt](project_backend/requirements.txt)

## 2. ติดตั้งและรัน

### 2.1 สิ่งที่ต้องเตรียม

- Python พร้อม pip และ venv
- Node.js พร้อม npm
- PostgreSQL database ที่เชื่อมต่อได้ บัญชี DB ต้องมีสิทธิ์สร้าง/ปรับ schema ตาม startup
- Model Gateway ที่มี endpoint ตรงกับ client ของโครงการ เพื่อทดสอบงานโมเดล

repository ไม่ได้ pin รุ่น runtime ของ Python, Node และ PostgreSQL ครบ จึงไม่ระบุรุ่นขั้นต่ำที่ยังไม่มีหลักฐานรับรอง

### 2.2 ติดตั้ง Backend

ตัวอย่าง PowerShell จาก root ของโครงการ:

```powershell
cd D:\coop\COOP_Project4_New
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r project_backend\requirements.txt
```

สร้าง project_backend/.env.local จาก [env.local.example](project_backend/env.local.example) หากยังไม่มี แล้วแก้ DATABASE_URL, GATEWAY_URL และ key ให้ตรงบริการที่ใช้ ดูส่วน 3 ก่อนรัน ไม่คัดลอกทับไฟล์ตั้งค่าเดิม

เปิด Backend โดยใช้ project_backend เป็น working directory:

```powershell
cd D:\coop\COOP_Project4_New\project_backend
..\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

startup เรียก ensure_database_ready เพื่อสร้าง/ปรับ runtime schema และ seed บัญชีที่ยังไม่มี email นั้น จึงต้องเตรียม DB ให้พร้อมก่อน ไม่ต้องใช้ Prisma push สำหรับขั้นตอนนี้

### 2.3 ติดตั้ง Frontend

สร้าง project_frontend/.env.local จาก [env.local.example](project_frontend/env.local.example) หากยังไม่มี แล้วเปิดอีก terminal:

```powershell
cd D:\coop\COOP_Project4_New\project_frontend
npm ci
npm run dev
```

postinstall จะ copy OpenCV.js ไป public/opencv.js หากติดตั้งล้มเหลวให้ตรวจ terminal ก่อนทดสอบการปรับภาพ

### 2.4 ตรวจว่าเริ่มทำงานได้

```powershell
Invoke-RestMethod http://localhost:8000/health
Invoke-RestMethod http://localhost:8000/health/db
Invoke-RestMethod http://localhost:8000/health/models
```

| จุดตรวจ | ผลที่ใช้ยืนยัน |
| --- | --- |
| /health | Backend ตอบ HTTP ได้ |
| /health/db | เปิด DB connection และ SELECT 1 ได้ |
| /health/models | อ่านการตั้งค่า runtime URL ได้; ยังไม่ได้ทดสอบ inference |
| http://localhost:8000/docs | ดู API และ request schema ของ app ที่รันอยู่ |
| http://localhost:3000/login | เปิดหน้า Login ได้เมื่อ Next dev server ใช้พอร์ต 3000 |

บัญชีหน้าเว็บปัจจุบัน: User `user@ocr.com / user123`, Admin `admin@ocr.com / admin123` หน้า Login ตรวจ mockAccounts ใน Browser และเก็บ session ใน localStorage/cookie ไม่ได้เรียก Backend login การแก้รหัสผ่านใน DB จึงไม่เปลี่ยนบัญชีหน้าเว็บนี้

หลักฐาน: [startup และ app](project_backend/main.py), [health routes](project_backend/app/api/routes.py), [Login](project_frontend/src/app/login/page.tsx), [session](project_frontend/src/auth/session.ts)

## 3. ตั้งค่าที่จำเป็น

### 3.1 Backend: project_backend/.env.local

ตัวอย่างค่าบนเครื่องพัฒนา ต้องเปลี่ยน credentials/ชื่อ DB ตามเครื่องจริง:

```dotenv
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/ocr_studio
GATEWAY_URL=http://127.0.0.1:8080
MODEL_GATEWAY_API_KEY=replace-with-model-gateway-api-key
```

| ตัวแปร | ใช้ทำอะไร |
| --- | --- |
| DATABASE_URL | PostgreSQL connection; รองรับ postgres:// และ postgresql:// |
| GATEWAY_URL | ที่อยู่ Model Gateway; ค่า default ใน config คือ http://localhost:8080 |
| MODEL_GATEWAY_API_KEY | ส่ง Bearer key เมื่อกำหนดค่า; ใช้ key ที่ Gateway นั้นรับจริง |
| FRONTEND_URL | เพิ่ม origin ใน CORS เมื่อใช้ Frontend นอก http://localhost:3000 |

loader อ่าน project_backend/.env.local ก่อน project_backend/app/.env.local และไม่ทับ key ที่มีใน process environment อยู่แล้ว หากแก้ไฟล์แล้วค่าไม่เปลี่ยนให้ตรวจ process environment และ restart Backend

### 3.2 Frontend: project_frontend/.env.local

```dotenv
NEXT_PUBLIC_API_URL=http://localhost:8000
```

ใช้ที่อยู่ Backend ที่ Browser เข้าถึงได้ และ restart dev server หลังเปลี่ยนค่า หากใช้ /backend/... ต้องตรวจ [next.config.ts](project_frontend/next.config.ts) ด้วย เพราะ rewrite destination ปัจจุบันเขียนตายตัว ไม่เปลี่ยนตาม NEXT_PUBLIC_API_URL

ค่าปรับแต่ง pool/cache/diagnostics ไม่จำเป็นสำหรับการเริ่มรัน ดูเฉพาะเมื่อมีปัญหาในเอกสารอ้างอิง Part 03

หลักฐาน: [env loader](project_backend/app/core/local_env.py), [Gateway config](project_backend/app/core/config.py), [Frontend API client](project_frontend/src/admin/adminApi.ts)

## 4. โครงสร้างโค้ดที่ใช้จริง

| ต้องการแก้อะไร | ไฟล์ที่เริ่มอ่าน |
| --- | --- |
| User workflow ตั้งแต่ upload ถึง export | [src/app/page.tsx](project_frontend/src/app/page.tsx) |
| เรียก API / แปลงข้อมูลให้ Frontend | [src/admin/adminApi.ts](project_frontend/src/admin/adminApi.ts) |
| Login และ Browser session | [login/page.tsx](project_frontend/src/app/login/page.tsx), [auth/session.ts](project_frontend/src/auth/session.ts) |
| การแปลงพิกัด ROI | [roiGeometry.ts](project_frontend/src/shared/workspace/roiGeometry.ts) |
| HTTP routes: Template, Detection, Settings, Logs | [app/api/routes.py](project_backend/app/api/routes.py) |
| Request/response schemas ของ Admin APIs | [app/api/schemas.py](project_backend/app/api/schemas.py) |
| Template CRUD, Publish, Settings และ Logs | [app/business/services.py](project_backend/app/business/services.py) |
| ตรวจหา Template | [detection_service.py](project_backend/app/processing/detection_service.py) |
| ลำดับตรวจ Anchor และเลือกผู้ชนะ | [staged_verification.py](project_backend/app/processing/staged_verification.py) |
| OCR API, jobs และการประมวลผล ROI | [main.py](project_backend/main.py) |
| เตรียม Text DET/REC | [ocr_adapter.py](project_backend/app/processing/ocr_adapter.py) |
| เรียกโมเดลผ่าน HTTP | [model_runtime_client.py](project_backend/app/core/model_runtime_client.py) |
| Connection และ runtime schema | [db.py](project_backend/app/core/db.py) |

ไฟล์ใหญ่ควรเริ่มจาก handler ของงานนั้น แล้วอ่านจนจบพร้อม helper/ผู้เรียกที่มีผลต่อพฤติกรรม โดยเฉพาะ early return, exception และ finally ก่อนแก้ ไม่ใช้ชื่อฟังก์ชันหรือเอกสารรุ่นเก่าแทน implementation

## 5. Workflow หลักที่ต้องเข้าใจ

### 5.1 User: เอกสาร -> Template -> OCR -> ตรวจทาน

1. Upload ภาพหรือ PDF ฝั่ง Browser แปลง PDF เป็นภาพสำหรับทำงาน
2. ผู้ใช้ปรับและยืนยันภาพ แล้ว Frontend ส่งภาพแต่ละหน้าไป Detection
3. Backend คัด Template ตามจำนวนหน้า/สัดส่วน และตรวจ Text/Image Anchors
4. Frontend โหลด Template ที่เลือกและแปลง ROI ลงบนภาพ ผู้ใช้ปรับ/เลือกพื้นที่ได้
5. Frontend ส่งภาพพร้อม ROI ไป OCR API แล้ว poll จนงานเสร็จ
6. ผู้ใช้ตรวจทานข้อความ/ตาราง ส่งออกข้อมูล และ Frontend ส่ง Processing Log ตามจุดบันทึกของ flow

Export ปัจจุบันสร้างใน Browser: JSON, Excel .xlsx, Word แบบ HTML ที่ดาวน์โหลดเป็น .doc และ ZIP ภาพ ไม่ใช่ .docx

### 5.2 Detection: สิ่งที่ต้องรักษาเมื่อแก้โค้ด

- User request ใช้ภาพที่ยืนยันแล้วและ confirmedImages=1 เพื่อข้ามการ normalize ซ้ำในเส้นทางนี้
- Template ที่ค้นตามปกติมาจาก active/published pool
- ตรวจ Anchor ทีละ stage: Template ที่ไม่ผ่านหยุดตรวจตัวถัดไป ส่วนที่ผ่านครบจึงเป็นผู้สมัครผู้ชนะ
- ทุก Anchor ใน staged matching ต้องผ่าน เมื่อหลาย Template ผ่านเรียง Anchor count มากก่อน แล้ว template_id
- standard รัน Text/Image batch คู่กันใน stage; strict รัน Text แล้ว Image ของ stage
- Text matching ใช้ normalized Levenshtein ส่วน Image matching ใช้ SigLIP V2.4 ไม่ใช้ weighted layout score เลือกผู้ชนะในเส้นทางปัจจุบัน

หากเปลี่ยนเงื่อนไขจับคู่ให้อ่าน detect_template_dev, _detect_template_anchor_strategy และ staged_verification.run พร้อม VerificationService ใน services.py รายละเอียดสูตรอยู่เอกสารอ้างอิง Part 08/15

### 5.3 Admin: สร้าง Template -> ทดสอบ -> Publish

1. สร้าง Template จะได้ Group และ Version สถานะ draft
2. เพิ่ม Page/ภาพตัวอย่าง แล้วสร้าง extraction Fields และ verification Anchors
3. ตรวจ readiness: มีภาพ/หน้าที่จำเป็น, ROI ใช้ได้, Text Anchor มี expected text และ Image Anchor มี category ที่เปิดใช้
4. รัน prepublish detection test ด้วยเอกสารทดสอบ ดูว่า draft ที่เลือกผ่านและได้อันดับ 1
5. เรียก confirm-publish ซึ่งตรวจ readiness และรัน Publish job; ถ้ามี Maintenance enforcement อาจได้ pending publish

Extraction Field คือพื้นที่อ่านข้อมูล ส่วน Anchor ใช้ยืนยันว่าเป็น Template ใด ทั้งสองใช้ Field API ร่วมกันแต่แยกตารางตาม use_for_verification

ขั้นทดสอบก่อน Publish จำเป็นสำหรับตรวจพฤติกรรม แต่ Backend confirm-publish ปัจจุบันไม่ได้บังคับว่าต้องมีผล prepublish อันดับ 1 ที่บันทึกไว้ก่อน จึงต้องรักษาขั้นทดสอบนี้ใน workflow ของทีม

### 5.4 OCR และหน่วยพิกัด

| ตำแหน่ง | หน่วยที่ใช้ |
| --- | --- |
| Template ROI ใน DB/API | ratio 0..1; page_number เริ่ม 1 |
| Workspace | พิกเซลบนภาพแสดงผลอ้างอิงความกว้าง 750; pageIndex เริ่ม 0 |
| /api/ai/process | พิกเซลบนภาพที่ส่งจริง รวม polygon points |

ก่อนส่ง OCR ต้องแปลง ROI จากภาพแสดงผลเป็นขนาดภาพจริง การส่ง ratio ไปแทน pixel ทำให้ crop ผิดพื้นที่

Backend เลือก pipeline ตามชนิด ROI: image เก็บพื้นที่ภาพ, table อ่านตาราง, flexible text แยกพื้นที่ภายในด้วย Layout และ text ใช้ DET/REC ส่วน rois=[] มีความหมายว่า full-page detection/OCR

### 5.5 Maintenance และ Logs

Maintenance มีผลต่อการอนุญาตประมวลผลและการพักการแก้ settings/Template บางชนิดไว้เป็น pending จึงต้องดูทั้ง enforcement, ช่วงเวลา และ pending state ก่อนสรุปว่าบันทึกแล้วไม่สำเร็จ ไม่ใช่ทุก mutation ถูก defer เหมือนกัน

Processing Log ใช้ processing_run_id สำหรับ upsert เก็บผล OCR ต้นฉบับแยกจากผลตรวจทาน ภาพถาวรเก็บใน processing_log_images ส่วนภาพ detection ชั่วคราวปกติถูกลบหลังงานจบ หากแก้การส่ง Log ต้องรักษา pageFiles สำหรับบันทึกภาพด้วย

## 6. Database และ API ที่จำเป็น

### 6.1 ข้อมูลหลัก

| กลุ่มข้อมูล | ตาราง |
| --- | --- |
| Template และรุ่น | template_groups -> template_versions -> template_pages |
| พื้นที่อ่านและตรวจสอบ | extraction_fields, verification_anchors, ignore_regions |
| คำขอ Template | template_requests, template_request_pages, requested_fields |
| สถานะงาน | ocr_jobs, publish_jobs |
| ประวัติและภาพ | processing_logs, processing_log_images |
| ค่าระบบ | app_settings, image_verification_categories |

ใน Admin API ค่า template_id ส่วนใหญ่หมายถึง template_versions.id ต้องตรวจ ID นี้ก่อนเขียน query หรือเรียก API runtime schema อยู่ใน _POSTGRES_SCHEMA ของ db.py การแก้ schema ต้องปรับให้เข้ากับข้อมูลเดิมด้วย อย่าใช้ schema.prisma เก่ามา push/reset ทับ schema นี้

### 6.2 API ที่ใช้ใน flow หลัก

| Method / Path | ใช้ทำอะไร |
| --- | --- |
| POST /api/templates/detect-dev?confirmedImages=1 | ส่งภาพยืนยันแบบ multipart เพื่อเริ่ม Detection |
| GET /api/templates/detect-dev/jobs/{job_id} | อ่านสถานะ/ผล Detection |
| GET /admin/templates/{template_id}?context=user | โหลดข้อมูล Template สำหรับ User |
| POST /api/layout/analyze | วิเคราะห์ Layout เพื่อสร้างพื้นที่อัตโนมัติ |
| POST /api/ai/process | ส่งภาพและ ROI; ใช้ async_mode=true สำหรับ job flow |
| GET /api/ai/jobs/{job_id} | อ่านสถานะ/ผล OCR |
| POST /processing-logs | บันทึก snapshot ตาม processing_run_id |
| GET /system/maintenance | อ่านสถานะ Maintenance สำหรับ User |

Template CRUD อยู่กลุ่ม /admin/templates รวม pages และ fields ส่วนก่อน Publish ใช้ /admin/templates/{template_id}/prepublish-simulation, /prepublish-detection-test และ /confirm-publish ดู method และ payload เต็มจาก /docs ของ Backend ที่รัน หรือ [schema](project_backend/app/api/schemas.py)

### 6.3 รูปแบบ response ที่ต้องแยก

| กลุ่ม | รูปแบบ |
| --- | --- |
| Admin APIs ส่วนใหญ่ | ok / data / error |
| Detection | status=success และ data; ตอนเริ่มงาน HTTP 202 |
| OCR | success=true; ตอนเริ่ม async job มี job_id และ status=queued |

เมื่อ poll ให้ตรวจ status ภายใน body: completed จึงอ่าน result, failed จึงอ่าน error การได้ HTTP 200 ของ poll ไม่ได้แปลว่างานประมวลผลสำเร็จ และการ abort ใน Browser ไม่ได้ยกเลิก worker ที่ Backend เริ่มไปแล้ว

Detection/prepublish jobs เก็บสถานะใน RAM ส่วน OCR jobs เก็บใน PostgreSQL ต้องคำนึงถึงความต่างนี้เวลา debug หลัง restart หรือเพิ่ม Backend workers

## 7. ขั้นตอนพัฒนาและตรวจงาน

### 7.1 เลือกเส้นทางที่ต้องแก้

| ประเภทงาน | อ่าน/แก้ให้ครบ |
| --- | --- |
| เพิ่ม API หรือ Field | schema -> route -> service -> DB -> adminApi mapping -> UI |
| เปลี่ยนเงื่อนไข Detection | detection_service -> staged_verification -> VerificationService -> UI แสดงผล |
| เปลี่ยน OCR | main.py -> adapter ของชนิด ROI -> Gateway contract -> result mapping ใน page.tsx |
| เปลี่ยน Template/Settings | service -> pending operations -> cache invalidation -> หน้าจอที่โหลดข้อมูล |

Frontend ต้องเรียกโมเดลผ่าน Backend และใช้ Maintenance ของ Backend เป็นแหล่งสถานะหลัก ดูกติกา Frontend เพิ่มเติมใน [AGENTS.md](project_frontend/AGENTS.md)

### 7.2 คำสั่งตรวจที่มีอยู่

จาก project_frontend:

```powershell
npm run lint
npm run build
```

จาก root ตัวอย่างชุดทดสอบที่เลือกตามงาน:

```powershell
python -m pytest project_backend/tests/test_staged_verification.py
python -m pytest project_backend/tests/test_text_anchor_matching.py
python -m pytest project_backend/tests/test_text_response_contract.py
```

ติดตั้ง dependencies ที่ชุดทดสอบใช้ก่อนรัน Frontend package.json ไม่มี npm test script คำสั่งข้างต้นเป็นวิธีตรวจ ไม่ใช่รายงานผลทดสอบของการแก้คู่มือครั้งนี้

### 7.3 ตรวจ flow หลังเปลี่ยน behavior

1. ใช้เอกสารที่มี Template ตรงกัน ตรวจผลเลือก Template และตำแหน่ง ROI
2. ใช้เอกสารที่ไม่ตรง ตรวจว่าไม่มีการเลือก Template ผิดโดยไม่สังเกต
3. รัน OCR กับชนิดข้อมูลที่ได้รับผลกระทบ ตรวจทั้งค่าที่อ่านและตำแหน่ง crop
4. แก้ผลตรวจทานแล้ว export ตรวจว่าไฟล์ใช้ค่าที่ต้องการ
5. เปิด Processing Log ตรวจผลต้นฉบับ ผลแก้ไข และภาพของ run เดียวกัน

ถ้าแก้ Template Publish หรือ Maintenance ให้ทดสอบ pending/apply เพิ่มด้วย ไม่ใช้ผลชุดทดสอบที่ไม่แตะเส้นทางนั้นรับรองทั้งหมด

## 8. แก้ปัญหาและข้อจำกัดสำคัญ

### 8.1 ตรวจตามอาการ

| อาการ | เริ่มตรวจที่ |
| --- | --- |
| Backend เปิดไม่ได้ | traceback ใน terminal, DATABASE_URL, DB connectivity และสิทธิ์ DDL |
| Frontend ติดต่อ Backend ไม่ได้ | NEXT_PUBLIC_API_URL, Network tab, พอร์ต และ CORS/FRONTEND_URL |
| health/models ปกติแต่ OCR ล้ม | Gateway URL/key, active model และ response contract; health นี้ไม่ยิง inference |
| ไม่พบ Template | active/published state, cache ready/degraded, จำนวนหน้า/สัดส่วน และ Anchor diagnostics |
| ROI อ่านผิดตำแหน่ง | ภาพที่ยืนยันกับภาพที่ส่ง, ratio/pixel และ page_number/pageIndex |
| OCR รอนานหรือไม่คืนผล | poll response status/error, Backend log และ Gateway log ที่เข้าถึงได้ |
| แก้ Template/Settings แล้วไม่เปลี่ยน | pending operations, Maintenance และ cache invalidation |
| Log ไม่มีภาพ | pageFiles ใน request และภาพใน processing_log_images; query directory อาจถูกลบแล้ว |
| บัญชี DB เปลี่ยนแต่หน้า Login ยังใช้รหัสเดิม | หน้าเว็บยังตรวจ mockAccounts ใน src/auth/session.ts |

### 8.2 สิ่งที่ต้องรู้ก่อนนำไปติดตั้งใช้งาน

- Authentication หน้าเว็บยังเป็น mock session และ Backend ไม่ได้ enforce role ผ่าน dependency ครบทุก route ต้องปรับก่อนใช้เป็นระบบควบคุมสิทธิ์จริง
- Detection/prepublish jobs, caches และ Maintenance event hub บางส่วนอยู่ใน process การเพิ่มหลาย workers อาจทำให้ poll ไม่พบ job หรือ state ต่างกัน
- OCR job อยู่ DB แต่ไม่ได้หมายความว่างานที่ค้างจะถูกนำกลับมารันหลัง process ตายโดยอัตโนมัติ
- Gateway เป็นบริการภายนอก ต้องยืนยัน endpoint, model contract และสิทธิ์เข้าถึงกับบริการที่ใช้จริง

### 8.3 เปิดเอกสารเพิ่มเติมเมื่อจำเป็น

| งาน | เอกสารอ้างอิงที่เกี่ยวข้อง |
| --- | --- |
| เปลี่ยน DB/schema | [Database schema](project_backend/docs/database_schema_v2.md) |
| เปลี่ยน runtime/model contract | [Model runtime architecture](project_backend/docs/model_runtime_architecture.md), [Text response contract](project_backend/docs/TEXT_RESPONSE_CONTRACT.md) |
| แก้ Text matching | [Text Anchor matching](project_backend/docs/TEXT_ANCHOR_MATCHING.md) |
| แก้ภาพใน Log | [Processing Log images](project_backend/docs/PROCESSING_LOG_IMAGES.md) |
| วิเคราะห์ Timing/Benchmark | [Benchmark README](benchmark/README.md) |
| ตรวจ API/schema/env ทั้งหมดหรือหา symbol | [เอกสารอ้างอิงเชิงลึก](DEVELOPER_REFERENCE_TH.md) |

คู่มือนี้ตรวจจาก source ไม่ได้รับรองผล production หรือความแม่นยำโมเดลที่ยังไม่ได้วัด การแก้เอกสารครั้งถัดไปต้องเปิดอ่าน implementation ของส่วนที่อธิบายจนจบ และระบุสิ่งที่ยังไม่มีหลักฐานให้ชัดเจน
