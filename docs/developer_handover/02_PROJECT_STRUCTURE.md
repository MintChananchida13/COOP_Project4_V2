# 02 — โครงสร้างโครงการและจุดเริ่มพัฒนา

## 1. โครงสร้างที่ใช้ทำงาน

```text
COOP_Project4_New/
├─ project_frontend/
│  ├─ src/app/                 Next.js routes และ User orchestration
│  ├─ src/admin/               Admin pages, API client และ types
│  ├─ src/auth/                Browser session และ AuthGate
│  ├─ src/user/                User components และ crop worker
│  ├─ src/shared/              PDF, ROI, Canvas และ helpers
│  ├─ src/types/               OCR types
│  ├─ tests/                   Frontend regression/unit tests
│  └─ public/                  Browser assets รวม OpenCV ที่ postinstall copy
├─ project_backend/
│  ├─ main.py                  App, startup, OCR/Layout APIs และ job processing
│  ├─ app/api/                 Routes, schemas และ response shaping
│  ├─ app/business/            Domain services และ Maintenance events
│  ├─ app/core/                DB, config, cache และ runtime HTTP client
│  ├─ app/auth/                Backend user/password/token helpers
│  ├─ app/processing/          Detection และ ROI/OCR orchestration
│  ├─ app/model_runtime/       HTTP-backed model adapters และ contracts
│  ├─ scripts/                Processing Log image migration
│  └─ tests/                   Backend tests
├─ API_Service/
│  ├─ services/*/main.py       Gateway, pipeline และ leaf FastAPI apps
│  ├─ inference/              Model selection/loading/predict
│  ├─ pipelines/              Layout, OCR, table และ verification composition
│  ├─ core/ และ shared/       Auth, contracts, parsing, queues และ telemetry
│  ├─ clients/                HTTPModelClient สำหรับ pipeline-to-leaf
│  ├─ model_variants.json     Catalog ของ DET/REC variants และ paths
│  ├─ scripts/                Windows/Linux setup และ process manager
│  └─ tests/                  Model API contract/infrastructure tests
└─ docs/developer_handover/    เอกสารชุดนี้
```

ชื่อโฟลเดอร์ model_runtime ไม่ได้หมายถึงมี model weights รันใน Backend ทุก adapter ต้องดูเส้นทาง HTTP ที่เรียกจริง

## 2. Entry Points

| จุดเข้า | การทำงาน |
| --- | --- |
| project_frontend/src/app/layout.tsx | Global CSS, font และ root layout |
| project_frontend/src/app/page.tsx | User state และ handlers ของ workflow หลัก |
| project_frontend/src/app/admin/layout.tsx | ครอบ AdminShell ด้วย AuthGate role admin |
| project_frontend/src/app/login/page.tsx | LoginForm ตรวจ mockAccounts |
| project_frontend/src/proxy.ts | Redirect จาก role cookie ตาม path |
| project_backend/main.py | FastAPI app, router/middleware/static mount, startup/shutdown |
| project_backend/app/api/routes.py | HTTP APIs ด้าน Admin, Detection, settings และ logs |
| API_Service/services/gateway/main.py | Gateway ที่ Backend ต้องชี้ GATEWAY_URL มาเรียก |
| API_Service/services/*/main.py | Apps แยก process; module/พอร์ตอยู่ในคู่มือ 13 |
| API_Service/app.py | Compatibility entry ของ PaddleOCR pipeline ไม่ใช่ Gateway |

## 3. แผนที่ Backend

| งาน | Module / Symbol หลัก |
| --- | --- |
| Request validation | api/schemas.py; ROIModel/DocumentPayload ใน main.py |
| Template CRUD/Version | business/services.py: AdminTemplateService |
| Request review | business/services.py: TemplateRequestService |
| Readiness/Publish | business/services.py: _template_anchor_readiness, TemplatePublishService |
| Matching | processing/detection_service.py: detect_template_dev |
| Anchor scheduling | processing/staged_verification.py: run, candidate_order |
| Anchor scoring | business/services.py: VerificationService |
| Extraction | main.py: process_document_payload |
| Serialized heavy work | processing/memory_usage.py: enqueue, serialized |
| HTTP models | core/model_runtime_client.py: _post_predict และ remote wrappers |
| PostgreSQL | core/db.py: connect, ensure_database_ready, _POSTGRES_SCHEMA |

services.py รวมหลาย domain ในไฟล์เดียว ควรเริ่มอ่านที่ class ของงานและไล่ helper/transaction/cache ที่ method นั้นใช้ ไม่เปลี่ยนชื่อหรือย้าย helper ร่วมโดยดู caller เพียงแห่งเดียว

## 4. แผนที่ Frontend

| งาน | Component/Module |
| --- | --- |
| Upload/adjust | user/components/UploadZone.tsx, AdjustZone.tsx |
| Matched/custom workspace | MatchedTemplateWorkspaceZone.tsx, WorkspaceZone.tsx และ shared/workspace |
| ตรวจทาน | user/components/GroundTruthEditorZone.tsx |
| API calls/mapping/cache | admin/adminApi.ts |
| Admin Template | AdminTemplatesPage.tsx, AdminTemplateEditPage.tsx, AdminTemplateTestPage.tsx |
| Admin Requests | AdminRequestsPage.tsx, AdminRequestDetailPage.tsx |
| Settings/Logs | AdminSettingsPage.tsx, AdminProcessingLogsPage.tsx, AdminProcessingLogDetailPage.tsx |
| Shared ROI operations | shared/workspace/roiGeometry.ts, roiSelection.ts |
| PDF conversion | shared/pdfImages.ts |

ไฟล์ใน src/app/admin ส่วนใหญ่เป็น route wrapper ที่ render component ใน src/admin ตรวจ wrapper และ import ก่อนแก้ layout หรือ data flow

## 5. เส้นทางพัฒนาตามประเภทงาน

| งานใหม่/แก้ไข | ลำดับที่ควรตรวจ |
| --- | --- |
| เพิ่มข้อมูล Field | schema → route → service/DB → adminApi mapping → editor/User mapper |
| เปลี่ยน Matching | detection_service → staged_verification → VerificationService → candidate resolver/UI |
| เปลี่ยน OCR result | adapter → main response mapping → types/handler → review/export/log snapshot |
| เปลี่ยน Model API contract | Backend runtime client/adapter → Gateway route → service/inference adapter → consumer normalizer; แนวทางปรับ SigLIP ให้รองรับ service ปัจจุบันอยู่คู่มือ 13 |
| เปลี่ยน settings | GlobalSettingsService → pending apply → cache invalidation → AdminSettingsPage |
| เปลี่ยน Log images | Frontend pageFiles → ProcessingLogService → processing_log_images → preview API |

อ่าน implementation เต็ม body ของส่วนที่เปลี่ยน รวม exception และ finally แล้วเลือก tests ตาม workflow ใน [11](11_TESTING_DEPLOYMENT.md)

## 6. Generated assets และขอบเขตที่ไม่ใช่ซอร์สหลัก

- public/opencv.js สร้างจาก dependency ผ่าน scripts_copy_opencv.cjs ควรแก้ต้นทาง/ขั้นตอน copy ไม่แก้ generated library เพื่อเพิ่มฟีเจอร์
- cropped_rois และ storage/detection_queries เป็น artifacts ต้องตรวจ working directory และ cleanup ก่อนอ้าง path ถาวร
- .next, node_modules, virtualenv และ __pycache__ ไม่ใช่แหล่งกำหนด domain behavior
- project_frontend/prisma มีไฟล์ schema/migrations รุ่นอื่น แต่ Backend runtime ใช้ DDL ของ db.py เป็นจุดที่ต้องตรวจสำหรับระบบนี้
- การมีไฟล์ legacy ไม่ยืนยันว่าถูกเรียกใน current User flow

API_Service มี dependencies และ process manager ของตนเอง แยก virtualenv จาก application Backend การมี weights/ directory ไม่ยืนยันว่ามี weights ที่ใช้งานได้ ดู [Model API Service](13_MODEL_API_SERVICE.md) ก่อนย้าย model paths หรือเพิ่ม workers

## 7. Source References

| Source | ใช้ตรวจ |
| --- | --- |
| [Frontend routes](../../project_frontend/src/app/page.tsx) | User entry และ component imports |
| [Admin layout](../../project_frontend/src/app/admin/layout.tsx) | AdminShell/AuthGate |
| [Backend app](../../project_backend/main.py) | app และ route registration |
| [Services](../../project_backend/app/business/services.py) | Domain classes |
| [OpenCV copy](../../project_frontend/scripts_copy_opencv.cjs) | การสร้าง public asset |
| [Frontend rules](../../project_frontend/AGENTS.md) | กติกาการพัฒนา Frontend |
| [Model entry](../../API_Service/app.py) / [Gateway](../../API_Service/services/gateway/main.py) | ความต่างของ default entry กับ Gateway |
| [Model launchers](../../API_Service/scripts/model-stack.ps1) | Service/profile mapping |
