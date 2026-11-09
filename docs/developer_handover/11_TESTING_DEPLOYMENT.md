# 11 — Testing และ Deployment

## 1. ขอบเขตการทดสอบ

| ระดับ | ตรวจอะไร | สิ่งที่ต้องพร้อม |
| --- | --- | --- |
| Unit/regression | กฎ Matching, contracts, ROI mapping, timing และ helpers | Python/Node dependencies ของ tests |
| API integration | schema → route → service → DB/job/result | Backend และ DB แยกสำหรับทดสอบ |
| Model integration | batch count, identity, outputs และ errors | Gateway ที่เข้าถึงได้ |
| Browser flow | upload → detect → ROI → OCR → review/export/log | Frontend/Backend/DB/Gateway |
| Benchmark | Detection flow และ measured timings ตาม script | Playwright, input และผลคาดหวัง |

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
| Model timing | test_model_timing_summary.py, test_model_runtime_timing.py |

ไฟล์ที่ตารางย่อไม่ได้ใส่ path เต็มอยู่ใน project_backend/tests หรือ project_frontend/tests ตามนามสกุล ไม่ใช้ผลเก่าจากเอกสารอื่นแทนผลรันหลังแก้ไข

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

## 4. Browser Benchmark

ติดตั้ง dependencies ของ benchmark แยกจาก requirements หลัก ตัวอย่างจาก root:

```powershell
.\.venv\Scripts\python.exe -m pip install -r benchmark\requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe benchmark\user_flow.py --input benchmark\input --frontend http://localhost:3000 --expected-mode standard --repeats 2
```

ต้องเตรียม input ภาพ/PDF จริง script เปิด persistent headed Chromium และเขียน Excel UserFlowTiming/ModelCalls การจับผลผูกกับ run/job ของเอกสารนั้นและรอ result visibility ก่อน reset

script นี้หยุดหลัง Template Detection เสมอ --no-ocr เป็น compatibility option ไม่ใช่สวิตช์เพิ่ม OCR ได้ CLI ยังมีชื่อ mode ทดลองเดิม แต่ current Backend ใช้ standard/strict ตรวจ actual_mode ของผลด้วย script ไม่ได้เปลี่ยน strategy ของ Backend ให้ตาม expected-mode อัตโนมัติ

ตัวอย่าง ground truth mapping สำหรับ option --ground-truth (ชื่อ key อ้าง relative input path):

```json
{
  "sample.png":"TEMPLATE_VERSION_ID",
  "unknown.png":null,
  "presence_only.png":true
}
```

PASS ของการเดิน flow ไม่ใช่ accuracy PASS ต้องดู match_correctness/matching_correctness ตาม ground truth และ errors ในแต่ละแถว URL ของ --frontend ถูก allowlist ใน script ไม่ใช่ domain ใดก็ได้

## 5. การอ่าน Timing

| Measurement | ใช้ตีความ |
| --- | --- |
| Client model HTTP timing | รวมช่วง request/response ตามที่วัด ไม่ใช่ pure inference |
| Gateway inference fields | ใช้เมื่อ response ให้ค่าที่ valid; ถ้าไม่มีไม่แทนด้วย 0 |
| model_wait_union_ms | Union ของช่วง calls; ต่างจาก sum เมื่อทำงานซ้อน |
| model_wall_clock_ms | ตั้งแต่ model call แรกถึงสุดท้าย |
| result visibility | DOM/control/preview checks และ animation frames; ไม่ใช่ physical display measurement |
| Extraction timing | ช่วง process_document_payload; ไม่รวมทุกคิว/DB/log stage |

ชื่อ backend_runtime_ms บาง object หมายถึง search elapsed แต่ helper backend_runtime_from_union หมายถึง total ลบ union ต้องระบุ source/path ของ metric ก่อนนำมาเปรียบเทียบ ไม่มี Benchmark results ใหม่ที่จัดทำเอกสารนี้รับรอง

## 6. เตรียม Deployment

repository ยืนยัน application commands ได้ แต่ topology, supervisor, reverse proxy, TLS, backup schedule และ Gateway deployment จริงยัง Not Verified ขั้นต่อไปเป็นแนวทางประกอบการติดตั้งที่ต้องปรับกับ environment จริง

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

## 7. Scale, Restart และ Recovery

single-process state ของ Detection jobs, published cache และ Maintenance events ทำให้เพิ่ม workers โดยตรงอาจเกิด poll 404 หรือ cache ต่างกัน ไม่รับรอง multi-worker consistency จากโค้ดนี้

OCR queue จำกัด heavy-work thread หนึ่งต่อ process การเพิ่ม processes เพิ่มทั้ง worker และ connection pools ต้องคำนวณ DB limits และ memory ใหม่ OCR job อยู่ DB แต่ไม่มี startup recovery loop ใน current startup สำหรับ requeue งานที่ค้าง

ก่อน restart ให้รู้ว่ามีงานค้างใดบ้างและเก็บ job IDs/logs ที่จำเป็น shutdown รอ memory worker แต่ force termination ไม่ให้หลักประกันเดียวกัน Rollback source อย่างเดียวไม่ย้อน DDL/UPDATE ที่ startup ทำไปแล้ว ต้องมีแผน database rollback/restore ที่ตรวจจริง

## 8. Source References

| Source | Symbol/บทบาท |
| --- | --- |
| [Staged tests](../../project_backend/tests/test_staged_verification.py) | StagedVerificationTests |
| [Text contract tests](../../project_backend/tests/test_text_response_contract.py) | TextResponseContractTests |
| [Polling tests](../../project_frontend/tests/templatePollingCancellation.test.cjs) | node:test และ TypeScript extraction |
| [Benchmark](../../benchmark/user_flow.py) | main, run_case, run_cases |
| [Benchmark requirements](../../benchmark/requirements.txt) | Playwright/openpyxl |
| [Timing](../../project_backend/app/processing/model_timing.py) | summarize_model_calls, backend_runtime_from_union |
| [Visibility](../../project_frontend/src/shared/resultVisibility.ts) | observeResultVisibility |
| [Package](../../project_frontend/package.json) | build/start/lint |
| [Startup/shutdown](../../project_backend/main.py) | startup_warmup, shutdown_db_pool |

