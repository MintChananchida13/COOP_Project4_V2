# 11 — Testing และ Deployment

## 1. ขอบเขตการทดสอบ

| ระดับ | ตรวจอะไร | สิ่งที่ต้องพร้อม |
| --- | --- | --- |
| Unit/regression | กฎ Matching, contracts, ROI mapping และ helpers | Python/Node dependencies ของ tests |
| API integration | schema → route → service → DB/job/result | Backend และ DB แยกสำหรับทดสอบ |
| Model integration | batch count, identity, outputs และ errors | Gateway ที่เข้าถึงได้ |
| Model API source contracts | Gateway routing, leaf adapters, auth, queues และ upstream translation | API_Service dev dependencies/mocks ตาม test imports |
| Browser flow | upload → detect → ROI → OCR → review/export/log | Frontend/Backend/DB/Gateway |

ชุด tests ใช้ mocks หลายจุด การผ่าน unit tests จึงไม่รับรอง Gateway availability หรือ accuracy ของเอกสารจริง การจัดทำเอกสารนี้ตรวจ source และ examples โดยไม่ได้เริ่ม integration services

## 2. คำสั่ง Unit/Regression

Backend tests ที่ยกตัวอย่างใช้ unittest จาก project_backend หลังติดตั้ง requirements:

```powershell
cd D:\coop\COOP_Project4_New\project_backend
..\.venv\Scripts\python.exe -m unittest discover -s tests -p test_staged_verification.py
..\.venv\Scripts\python.exe -m unittest discover -s tests -p test_text_anchor_matching.py
..\.venv\Scripts\python.exe -m unittest discover -s tests -p test_text_response_contract.py
```

Frontend จาก project_frontend หลัง npm ci:

```powershell
npm run lint
npm run build
node --test tests/templatePollingCancellation.test.cjs
```

package.json ไม่มี npm test script ไม่ควรใช้คำสั่งนั้นโดยสมมติว่ามี Test files บางตัวมีวิธี compile/load ต่างกัน ให้เปิดไฟล์ก่อนเลือก runner รวม ไม่อ้างว่า node --test tests/* ใช้ได้กับทุกไฟล์

| งานที่แก้ | Test files ที่เกี่ยวข้อง |
| --- | --- |
| Matching stages/winner | test_staged_verification.py, test_admin_anchor_detection.py |
| Confirmed images | test_confirmed_images_detection.py, templatePollingCancellation.test.cjs |
| Text contract | test_text_response_contract.py |
| Text matching | test_text_anchor_matching.py |
| ROI/Template mapping | templateRoiLoading.test.cjs, roiSelection.test.mjs |
| Publish | test_anchor_publish.py, test_prepublish_active_ranking.py |
| Logs/images | test_processing_log_images.py |

ไฟล์ที่ตารางย่อไม่ได้ใส่ path เต็มอยู่ใน project_backend/tests หรือ project_frontend/tests ตามนามสกุล ไม่ใช้ผลเก่าจากเอกสารอื่นแทนผลรันหลังแก้ไข

API_Service ใช้ pytest/httpx จาก requirements-dev.txt แยกจาก Backend tests ตัวอย่างชุดที่อ้างถึงอยู่ tests/test_public_api_contract.py, test_inference_adapters.py, test_service_topology.py และ test_upstream_security.py วิธีรันด้วย model API virtualenv อยู่ [คู่มือ 13](13_MODEL_API_SERVICE.md) ใช้ test/development environment และตรวจ imports/fixtures ก่อนรัน ไม่ใช้ readiness/inference แทน unit test

## 3. Integration และ Smoke Test ตามลำดับ

1. ตรวจ /health, /health/db และ config ของ /health/models
2. เปิดหน้า Login ด้วย development account จาก session.ts ตรวจ route User/Admin
3. เพิ่ม/ใช้ draft ใน DB ทดสอบ ตรวจภาพ Page และ ROI รวมทั้ง Text/Image Anchors
4. รัน readiness และ prepublish detection ดู draft rank/active conflict ก่อน Publish
5. Upload เอกสารที่ตรงและไม่ตรง ตรวจ Detection acknowledgement, polling และผลเลือก Template
6. ตรวจ ROI บนภาพ แล้ว OCR text/table/image ตามชนิดที่แก้ไข
7. แก้ Ground Truth และ export ตรวจเนื้อหาของไฟล์
8. เปิด Processing Log ของ run เดียวกัน ตรวจภาพและ original/edited snapshots
9. หากแก้ Maintenance/pending ให้ทดสอบ scheduled/active/apply และผลหลัง refresh cache

ใช้ข้อมูลทดสอบที่อนุญาตให้เก็บใน DB/logs และแยกจากข้อมูลใช้งานจริง การทดสอบที่สร้าง Template, jobs และ logs มีการเขียนข้อมูล ไม่ใช่ read-only smoke ทุกข้อ

## 4. เตรียม Deployment

repository มี application commands พร้อม Model API launchers และ Nginx example ใน API_Service สำหรับติดตั้งแยก process บน Windows/Linux ขั้นตอนต่อไปใช้ค่าของ environment ที่ส่งมอบและไม่เปลี่ยน Model Service เพื่อแก้ SigLIP ส่วน topology/supervisor/TLS/backup จริงให้ยืนยันกับผู้ดูแลระบบ

1. เตรียม PostgreSQL และ backup/restore procedure ของ environment
2. ตั้ง Backend env และ URL/key ของ Gateway โดยเก็บ secrets ฝั่ง server
3. ตรวจ auth/role enforcement ก่อนเปิด API ให้ผู้ใช้จริง ดูข้อจำกัดใน [05](05_BACKEND_API.md)
4. เริ่ม Backend จาก project_backend และเฝ้า startup/DDL/cache errors
5. ตั้ง NEXT_PUBLIC_API_URL ก่อน build Frontend และ FRONTEND_URL ให้ตรง Browser origin
6. build/start Frontend ตรวจ runtime rewrite, public assets และ CORS
7. รัน integration smoke ด้วย environment ที่ติดตั้งแล้ว

ตัวอย่าง application process commands ไม่ได้ติดตั้ง service manager ให้:

```powershell
# Backend: จาก project_backend, ไม่มี development reload
..\.venv\Scripts\python.exe -m uvicorn main:app --host 0.0.0.0 --port 8000

# Frontend: จาก project_frontend
npm ci
npm run build
npm run start
```

### 4.1 Model API Deployment

ใช้ virtualenv แยก API/Paddle/SigLIP ตาม setup scripts และเริ่ม Gateway ผ่าน services.gateway.main:app ไม่ใช่ app.py Linux manager รองรับ private binding, PID/log/cache/temp และหนึ่ง worker ต่อ service ส่วน Windows runner ใช้ environment names ต่างจาก Linux ตามคู่มือ 13

core-stack ไม่เปิด Table V2 ที่ Backend เรียกจึงต้องวาง profile/process ให้ครบ แต่ไม่ควรเปิด all เพื่อแก้ทุกอย่างโดยไม่ประเมิน VRAM เพราะ integrated PaddleOCR/Table V2 โหลด submodels ซ้ำกับ leaves ได้

ผู้ดูแลยืนยัน Table V2 และ Batch ทำงานปกติ ส่วน Model Selection และ Layout ไม่มีปัญหาที่รายงาน ให้ใช้เส้นทางเหล่านี้เป็น regression cases เมื่อปรับ Backend ด้าน SigLIP โดยไม่เปลี่ยน Model Service ดู [ประเด็นที่ต้องดำเนินการต่อ](13_MODEL_API_SERVICE.md#13-ประเด็นที่ต้องดำเนินการต่อ)

Model API health/discovery ไม่โหลดโมเดล ส่วน readiness อาจโหลด weights/probe upstream จึงเป็น warm-up operation ที่ต้องได้รับอนุญาตและเผื่อ timeout ไม่ควรใช้เป็น liveness probe ถี่ ๆ แล้วคาดว่าไม่มีผลต่อ resource

## 5. Scale, Restart และ Recovery

single-process state ของ Detection jobs, published cache และ Maintenance events ทำให้เพิ่ม workers โดยตรงอาจเกิด poll 404 หรือ cache ต่างกัน ไม่รับรอง multi-worker consistency จากโค้ดนี้

OCR queue จำกัด heavy-work thread หนึ่งต่อ process การเพิ่ม processes เพิ่มทั้ง worker และ connection pools ต้องคำนวณ DB limits และ memory ใหม่ OCR job อยู่ DB แต่ไม่มี startup recovery loop ใน current startup สำหรับ requeue งานที่ค้าง

API_Service มี rate limiter/caches/REC fair queue ต่อ process เพิ่ม workers จึงเพิ่ม model copies/queue limits ด้วย Native inference ไม่ถูกยกเลิกเมื่อ HTTP client timeout โดยอัตโนมัติ ต้องพิจารณา Gateway→pipeline→leaf timeout ทั้ง chain ไม่ใช้ Backend timeout เดียวรับรอง recovery

ก่อน restart ให้รู้ว่ามีงานค้างใดบ้างและเก็บ job IDs/logs ที่จำเป็น shutdown รอ memory worker แต่ force termination ไม่ให้หลักประกันเดียวกัน Rollback source อย่างเดียวไม่ย้อน DDL/UPDATE ที่ startup ทำไปแล้ว ต้องมีแผน database rollback/restore ที่ตรวจจริง

## 6. Source References

| Source | Symbol/บทบาท |
| --- | --- |
| [Staged tests](../../project_backend/tests/test_staged_verification.py) | StagedVerificationTests |
| [Text contract tests](../../project_backend/tests/test_text_response_contract.py) | TextResponseContractTests |
| [Polling tests](../../project_frontend/tests/templatePollingCancellation.test.cjs) | node:test และ TypeScript extraction |
| [Package](../../project_frontend/package.json) | build/start/lint |
| [Startup/shutdown](../../project_backend/main.py) | startup_warmup, shutdown_db_pool |
| [Model tests](../../API_Service/tests/test_public_api_contract.py) / [Model adapters tests](../../API_Service/tests/test_inference_adapters.py) | Contract fixtures และ mocks |
| [Model deployment](../../API_Service/scripts/model-stack.sh) / [Nginx](../../API_Service/deploy/nginx/nginx.conf) | Deployment implementations/examples |
