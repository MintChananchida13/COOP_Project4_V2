# 12 — Troubleshooting และ Handover

## 1. วิธีเริ่มตรวจปัญหา

เริ่มจาก Browser Network/Console และ Backend traceback จด processing_run_id, Detection/OCR job_id, Template Version ID และเวลาที่เกิดเหตุ แล้วเทียบสถานะ API กับข้อมูล DB ให้ตรงเอกสารเดียวกัน ไม่คัดลอกภาพเอกสาร/credentials ลง ticket โดยไม่จำเป็น

ลำดับตรวจที่ใช้ร่วมกันคือ config → health/DB → request payload → job state → domain/model error → response mapping → UI/cache

## 2. Troubleshooting Matrix

| อาการ | จุดตรวจ | วิธีดำเนินการ |
| --- | --- | --- |
| Backend startup ไม่สำเร็จ | import traceback, DATABASE_URL, DDL/category initialization | ตรวจ dependencies, DB connectivity/privilege และ statement ที่ล้มก่อนแก้ |
| /health/db เป็น 503 | detail.message และ DB connection | ยืนยัน server/database/user/network ตาม environment |
| Frontend request ไม่ถึง API | NEXT_PUBLIC_API_URL, Network tab, CORS, rewrite | ตรวจ URL จาก Browser จริงและ restart/build หลังแก้ config |
| health/models=ok แต่ OCR ล้ม | Gateway URL/key, active model, contract | ใช้ model check/probe และ traceback; health นี้ไม่ยิง inference |
| PDF upload ล้ม | PDF.js/worker CDN และ Browser console | ตรวจ asset loading/network; helper ใช้ CDN |
| OpenCV/Adjust ใช้ไม่ได้ | npm install/postinstall, public/opencv.js | ตรวจ copy step และ asset request ก่อนแก้ crop algorithm |
| Detection ตอบ 400 | confirmedImages และ multipart content | User ส่งภาพยืนยันทุกหน้า; ไม่ส่ง PDF ใน confirmed mode |
| Job poll เป็น 404 หลัง restart | process-local dictionary และ request routing | แยกจาก ID ผิด; เริ่มงานใหม่ตาม flow และประเมิน worker topology |
| ไม่พบ Template | active/published pool, page/aspect gates, Anchors | ดู reject diagnostics ก่อนแก้ rules/threshold |
| Template ที่ชนะไม่ตรงคาด | complete pass, Anchor count, tie order | อย่าใช้ final_score=1 ตัดสินว่าแม่นยำ 100%; ตรวจเนื้อหา Anchor |
| ROI crop ผิดพื้นที่ | source/processing image, ratios/pixels, page index | ตรวจ coordinate conversion และภาพที่ส่ง OCR |
| OCR ค้าง | queued/processing, worker/Gateway, error_message | ตรวจ elapsed และ stale-on-poll; timeout ไม่ cancel worker |
| Table มี rows แต่ confidence=0 | raw score availability และ mapping | แยก structure correctness จากคะแนน ไม่สรุปว่าผลว่าง |
| เปลี่ยน Template/setting แล้วไม่แสดง | enforcement/pending, Backend/Browser cache | ยืนยัน pending/apply และ invalidation ตาม operation |
| Log ไม่มีภาพ | page_files, DB image rows, temporary query cleanup | ใช้ persisted images; ไม่พึ่ง query directory หลัง Detection |
| แก้รหัส DB แต่ Login ไม่เปลี่ยน | mockAccounts ใน Browser | พัฒนา auth flow ให้ครบก่อนใช้ DB users เป็นตัวจริง |

### Model API Service

| อาการ | จุดตรวจ/แนวทาง |
| --- | --- |
| Gateway 401 หรือ process start ไม่ผ่าน | MODEL_GATEWAY_API_KEY ของ Backend/Gateway และ INTERNAL_API_TOKEN ระหว่าง model services; production ต้อง non-placeholder >=32 characters |
| Gateway health ผ่านแต่ model request ไม่ได้ | health ไม่โหลด weights; services แสดง configured inventory; readiness อาจ degraded และไม่ required บริการนั้น |
| SigLIP raw_logits_required ทั้งที่ HTTP 200 | C1: ปรับ Backend ให้อ่าน output จริง ใช้ single decision หรือ batch scoring แบบเดียวกับ service โดยไม่แก้ Model Service |
| REC timeout แล้วยังใช้ resource | Fair queue ไม่ interrupt native inference; cleanup หลัง call กลับ |

ประเด็น Model API integration ที่ต้องดำเนินการต่อเหลือ SigLIP ตาม C1 โดยปรับ Backend ให้รองรับ output/scoring ของ service เดิม ดู [แนวทางดำเนินการต่อ](13_MODEL_API_SERVICE.md#13-ประเด็นที่ต้องดำเนินการต่อ) ไม่แก้ Model Service หรือข้าม validator เพื่อให้ผลผ่าน

## 3. ตัวอย่างตรวจ DB แบบไม่แก้ข้อมูล

แทน OCR_JOB_ID/PROCESSING_RUN_ID ด้วยค่าจากงานจริง:

```sql
SELECT id, status, error_message, requested_at, started_at, completed_at
FROM ocr_jobs
WHERE id = 'OCR_JOB_ID';
```

```sql
SELECT pl.id, pl.processing_run_id,
       pli.page_number, pli.kind, pli.mime_type
FROM processing_logs pl
LEFT JOIN processing_log_images pli ON pli.log_id = pl.id
WHERE pl.processing_run_id = 'PROCESSING_RUN_ID'
ORDER BY pli.page_number, pli.kind;
```

ไม่ต้อง SELECT ภาพ Base64 เพื่อดูว่ามี record หรือไม่ หากต้องตรวจ settings ให้เลือกเฉพาะ key ของปัญหาและระวังข้อมูลภายใน value ไม่ dump app_settings ทั้งตารางลง ticket

## 4. แนวทางแก้ไขอย่างต่อเนื่อง

| ส่วนที่แก้ | สิ่งที่ต้องตรวจหลังแก้ |
| --- | --- |
| DTO/serializer | API examples, Frontend mapper/types และ error response |
| ROI/image preprocessing | ภาพที่ยืนยัน, Template ratios, natural pixels และ polygon |
| Matching rules | missing pages/results, all-pass rule, tie order และ prepublish ranking |
| Model adapter | Identity, output shape, count/order และ unavailable errors |
| Template/settings writes | Transaction, pending apply และ cache invalidation |
| OCR response | Review/export/Processing Log original-edited split |

อ่านฟังก์ชันเต็ม body และ helpers ที่เปลี่ยนผลจริงก่อนแก้ ไม่เพิ่ม defaults ให้กลบ contract error และไม่ใช้ DELETE/reset DB เป็นการทดลองขั้นแรก การแก้ schema ต้องมีข้อมูลเก่าทดสอบด้วย

## 5. สิ่งที่ต้องส่งต่อให้ Developer/Operator

รายการนี้เป็นงานส่งมอบที่ต้องเตรียมจาก environment จริง ไม่ใช่ข้อมูลที่ยืนยันแล้วว่ามีใน repository:

- สิทธิ์ repository, environment และ DB ตามบทบาท พร้อมวิธีรับ secrets ผ่านช่องทางของทีม
- รุ่น runtime ที่ทีมใช้งานจริง และคำสั่ง/ผู้รับผิดชอบเริ่มหยุดแต่ละบริการ
- Gateway owner, endpoint contract, model selections และวิธีดู service logs
- ชุดเอกสารทดสอบที่อนุญาตใช้งาน พร้อม expected Template/ข้อมูลอ่านที่ถูกต้อง
- Backup/restore และ retention policy สำหรับ jobs, logs, ภาพและ artifacts
- Deployment topology, reverse proxy/TLS และขั้นตอน rollback
- ผู้รับผิดชอบ auth, Maintenance/pending operations และเหตุขัดข้อง

สำหรับ Model API ให้ส่งมอบ model_variants.json ที่ตรง environment, paths/mounts ของ weights, selected services/profiles, token rotation procedure และวิธีดู model-side logs เพิ่มเติม ซอร์สมีแล้วแต่สิ่งเหล่านี้ยังต้องยืนยันจากผู้ดูแล environment

API_Service ปัจจุบันเป็น Git working tree แยกและยัง untracked ใน parent repository ต้องส่งต่อวิธี checkout subproject ด้วย ไม่ถือว่าการส่ง commit ของเอกสารจะรวม Model API source โดยอัตโนมัติ

## 6. สิ่งที่ยืนยันได้จาก Source Code

| ส่วน | ขอบเขตที่ตรวจ |
| --- | --- |
| App startup/API | main.py, router registration, routes, DTOs และ error branches ที่อธิบาย |
| Frontend workflow | Entry routes, User handlers, API mapping, session, ROI helpers และ export path |
| Database | PostgreSQL wrapper, DDL, FK/constraints และ SQL ของ services |
| Template | CRUD, readiness, prepublish ranking, Publish และ request conversion |
| Detection | Prepared images, page/aspect gates, staged Text/Image verification และ winner order |
| OCR/model client | Pipeline selection, DET/REC contract, Table mapping และ outbound Gateway paths |
| Jobs/Logs | Persistence vs memory state, stale check, snapshots, image storage/cleanup |
| Testing | Unit/regression files, test runners, package scripts และ integration smoke workflow |
| API_Service | Gateway/leaf/pipeline entry points, model loaders, contracts, queues, auth, telemetry, setup/launch scripts และ deployment examples |
| Backend ↔ Model API | Routes/methods, image envelopes, selectors และ batch normalization; งานต่อด้าน SigLIP อยู่ใน C1 |

การตรวจนี้เป็น source-based handover ไม่เท่ากับการรันทดสอบทุก branch เอกสารอธิบาย workflow/จุดพัฒนา ไม่ใช่รายการ audit ทุก symbol ใน repository

### การตรวจเอกสารส่งมอบ

เอกสารมีครบ 13 ไฟล์ โดย API catalogue แสดง 96 route declarations ที่อยู่ในขอบเขตส่งมอบ ตัวอย่าง request/response แยกจากผลทดสอบจริง และ source references ใช้ค้น implementation ที่เกี่ยวข้อง ตรวจลิงก์ ตาราง JSON และโครงสร้าง Mermaid หลังปรับเนื้อหา โดยไม่แก้ Source Code, Database หรือ Configuration

### สถานะการใช้งานที่ผู้ดูแลระบบยืนยัน

| ส่วน | สถานะสำหรับการส่งมอบ |
| --- | --- |
| Model Selection | ไม่พบปัญหาในการใช้งานปัจจุบัน; คงคำอธิบาย selectors/settings ตาม source |
| Layout | ไม่พบปัญหาในการใช้งานปัจจุบัน; คงรายละเอียด Layout/DET workflow |
| Table V2 | ผู้ดูแลยืนยันว่าทดสอบแล้วทำงานปกติ |
| Batch processing | ผู้ดูแลยืนยันว่าทดสอบแล้วทำงานปกติ |
| SigLIP | ยังต้องปรับ Backend ให้รองรับ Model Service ปัจจุบัน; ยังไม่ถือว่าแก้ไขแล้ว |

สถานะข้างต้นเป็นข้อมูลการใช้งานที่ผู้ดูแลให้ ไม่ใช่ผลรันทดสอบใหม่จากการแก้เอกสาร การส่งมอบครอบคลุม application และ Model API ที่เปิดแยก process บน Windows/Linux

## 7. ส่วนที่ยังขาดข้อมูลหรือไม่สามารถยืนยันได้

| ส่วน | สถานะ/ข้อมูลที่ต้องขอหรือทดสอบ |
| --- | --- |
| Production topology และสถานะ deploy ปัจจุบัน | Not Verified: ไม่มีข้อมูล runtime จากเครื่องใช้งานจริง |
| รุ่น runtime ที่รับรอง | API_Service Windows setup ระบุ Python 3.12 และ Linux 3.10–3.12; application Node/PostgreSQL และ installed versions จริงยังไม่ยืนยัน |
| Gateway/Leaf implementation | Implemented ใน API_Service; งานต่อด้าน SigLIP คือปรับ Backend โดยไม่แก้ service |
| GPU/checkpoints/driver และ model artifacts จริง | External Dependency / Not Verified: ตรวจ loader/catalog ได้ แต่ไม่โหลดโมเดลหรือยืนยัน availability |
| Active DB records/model settings จริง | Not Verified: ไม่เชื่อม DB หรืออ่าน private environment เพื่อยืนยัน |
| Auth สำหรับใช้งานจริง | Mock/Incomplete: Browser mock, Backend role dependencies/token import issues ตามคู่มือ 05 |
| Recovery หลัง force termination/multi-worker consistency | ยังไม่มีหลักประกันจาก process-local state/current startup ต้องออกแบบและทดสอบเพิ่ม |
| Backup/restore, retention, TLS, monitoring alerts | Not Verified: ต้องมี runbook และผลทดสอบจาก environment |
| Live integration และ Browser acceptance ของเอกสารชุดนี้ | Not Verified: ครั้งนี้ไม่รันระบบที่เขียน DB/เรียกโมเดล |
| Mermaid renderer ของระบบปลายทาง | Not Verified: ตรวจ syntax/structure แต่ยังไม่ได้ render กับ engine/version ที่ผู้รับช่วงใช้ |

## 8. ลำดับรับช่วงงานที่แนะนำ

1. อ่าน [ภาพรวม](01_SYSTEM_OVERVIEW.md) และ [โครงสร้าง](02_PROJECT_STRUCTURE.md)
2. เตรียม environment ตาม [การติดตั้ง](03_INSTALLATION_CONFIGURATION.md)
3. ไล่ User flow ด้วย [Frontend](04_FRONTEND_GUIDE.md) และ [API](05_BACKEND_API.md)
4. ใช้ [Database](06_DATABASE_DESIGN.md), [Template](07_TEMPLATE_MANAGEMENT.md), [Detection](08_TEMPLATE_DETECTION.md) และ [OCR](09_OCR_AI_PIPELINE.md) เมื่อเริ่มแก้ domain
5. ตรวจสถานะงานและภาพด้วย [Jobs/Logs](10_PROCESSING_JOBS_LOGS.md)
6. รันการตรวจที่สัมพันธ์กับงานตาม [Testing/Deployment](11_TESTING_DEPLOYMENT.md) แล้วบันทึกผลจริงแยกจากตัวอย่างในคู่มือ
7. ใช้ [Model API Service](13_MODEL_API_SERVICE.md) ดูแล services/weights ตาม environment ที่ส่งมอบ และดำเนินงาน C1 ฝั่ง Backend พร้อมผลทดสอบก่อนปิดงาน SigLIP

## 9. Source References

| Source | ใช้วิเคราะห์ |
| --- | --- |
| [Main](../../project_backend/main.py) | startup_warmup, get_ai_process_job, _mark_stale_ocr_job_failed |
| [Routes](../../project_backend/app/api/routes.py) | Health, Detection polling และ log image routes |
| [Services](../../project_backend/app/business/services.py) | pending operations, template/log persistence |
| [DB](../../project_backend/app/core/db.py) | connection/schema |
| [Runtime client](../../project_backend/app/core/model_runtime_client.py) | Gateway errors/identity |
| [Frontend API](../../project_frontend/src/admin/adminApi.ts) | URL, polling, mapping และ caches |
| [Browser session](../../project_frontend/src/auth/session.ts) | mockAccounts |
| [Model Gateway](../../API_Service/services/gateway/main.py) / [Model contracts](../../API_Service/shared/inference_adapters.py) | Routing และ producer output |
| [Service settings](../../API_Service/core/service_settings.py) | URLs สำหรับ Gateway/pipelines/leaves |
