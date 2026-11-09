# 03 — การติดตั้งและ Configuration

## 1. สิ่งที่ต้องเตรียม

| รายการ | ใช้งาน |
| --- | --- |
| Python, pip และ venv | ติดตั้ง Backend dependencies |
| Node.js และ npm | Frontend dependency/build/dev server |
| PostgreSQL database | ต้องเชื่อมต่อได้และมีสิทธิ์ DDL ตาม bootstrap |
| Model Gateway | External Dependency สำหรับทดสอบ inference |
| Browser ที่โหลด assets ภายนอกได้ | PDF.js ผ่าน CDN; root layout ใช้ next/font/google |

รุ่น dependency หลักที่ประกาศ: FastAPI 0.110.0, Uvicorn 0.28.0, Pydantic 2.6.4, OpenCV 4.9.0.80, NumPy 1.26.4, Next.js 16.2.9, React 19.2.4 รุ่น Python/Node/PostgreSQL ที่ทีมรับรองยัง Not Verified เพราะไม่มี runtime pin ครบในไฟล์เหล่านี้

## 2. ติดตั้งตามลำดับ

ตัวอย่างต่อไปนี้ใช้ PowerShell และตำแหน่ง workspace นี้ ปรับ root path หาก clone ไปตำแหน่งอื่น ขั้นตอนเป็นคำสั่งสำหรับผู้รับช่วง ไม่ได้ถูกรันเพื่อเปลี่ยนระบบในการจัดทำเอกสาร

### 2.1 สร้าง Python environment

```powershell
cd D:\coop\COOP_Project4_New
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r project_backend\requirements.txt
```

### 2.2 เตรียม Backend environment

จาก root คัดลอกเฉพาะเมื่อไฟล์ยังไม่มี:

```powershell
if (!(Test-Path project_backend/.env.local)) {
    Copy-Item project_backend/env.local.example project_backend/.env.local
}
```

แก้ค่าภายในไฟล์ให้ตรง DB/Gateway ของตน ตัวอย่างนี้ใช้ placeholder ไม่มี credentials จริง:

```dotenv
DATABASE_URL=postgresql://DB_USER:DB_PASSWORD@127.0.0.1:5432/DB_NAME
GATEWAY_URL=http://127.0.0.1:8080
MODEL_GATEWAY_API_KEY=YOUR_GATEWAY_KEY
```

เตรียม database ก่อน Backend startup ตัว Backend สร้าง/ปรับตารางใน database ที่ URL ชี้ แต่ไม่ได้สร้าง PostgreSQL server ให้ และไม่ได้แทนที่ด้วย SQLite เมื่อเชื่อมต่อไม่ได้

### 2.3 เตรียม Frontend environment และ dependencies

```powershell
if (!(Test-Path project_frontend/.env.local)) {
    Copy-Item project_frontend/env.local.example project_frontend/.env.local
}
cd project_frontend
npm ci
```

กำหนด project_frontend/.env.local:

```dotenv
NEXT_PUBLIC_API_URL=http://localhost:8000
```

postinstall copy @techstark/opencv-js/dist/opencv.js ไป public/opencv.js ตรวจขั้นตอนนี้เมื่อหน้า Adjust ใช้ OpenCV ไม่ได้

### 2.4 เริ่มบริการ

Terminal Backend:

```powershell
cd D:\coop\COOP_Project4_New\project_backend
..\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

Terminal Frontend:

```powershell
cd D:\coop\COOP_Project4_New\project_frontend
npm run dev
```

เปิด URL ที่ Next dev server แสดง โดยปกติเริ่มทดสอบที่ http://localhost:3000/login หากใช้พอร์ตอื่นต้องตั้ง CORS ให้ตรง

## 3. Environment ที่ใช้ประจำ

| ตัวแปร | Default/พฤติกรรม | ตั้งเมื่อ |
| --- | --- | --- |
| DATABASE_URL | รองรับ postgres:// หรือ postgresql:// | ทุก environment ที่ใช้ DB |
| GATEWAY_URL | http://localhost:8080 | ชี้ Gateway ที่ให้บริการจริง |
| MODEL_GATEWAY_API_KEY | ว่าง; เติม Bearer header เฉพาะเมื่อมีค่า | Gateway ใช้ key |
| FRONTEND_URL | ว่าง; เพิ่ม origin ต่อจาก http://localhost:3000 | Browser origin ต่างจาก local default |
| NEXT_PUBLIC_API_URL | http://localhost:8000 ใน adminApi | ที่อยู่ Backend ที่ Browser เข้าถึงได้ |
| MODEL_RUNTIME_ROLE | service ทำให้ runtime_url คืน None | ต้องเข้าใจบทบาทก่อนตั้ง; ไม่ตั้งเป็น service สำหรับ orchestrating Backend โดยไม่มีเหตุผล |
| SAVE_DEBUG_ARTIFACTS | false | ต้องเก็บ detection artifacts เพื่อวิเคราะห์ |
| SAVE_CROPPED_ROIS | false | ต้องการเขียน crop artifacts จาก main.py |
| OCR_JOB_PROCESSING_TIMEOUT_SECONDS | 900; อย่างน้อย 60 | ปรับเกณฑ์ stale processing job ตอน poll |
| DB_POOL_MIN_CONNECTIONS / DB_POOL_MAX_CONNECTIONS | 1 / 10 | ปรับ pool หลังประเมินจำนวน processes และ DB limit |

Backend โหลด project_backend/.env.local ก่อน project_backend/app/.env.local และไม่ทับ key ที่มีใน process environment แล้ว ไฟล์ .env.local ที่ root repository ไม่ถูก loader นี้อ่าน GATEWAY_URL ถูกอ่านตอน import จึงควร restart Backend หลังเปลี่ยนค่า

NEXT_PUBLIC_API_URL ใช้ใน Browser bundle จึงต้องมีค่าที่ถูกต้องในขั้น build/restart ที่เกี่ยวข้อง หากใช้เส้นทาง /backend/... ให้ตรวจ next.config.ts ซึ่งมี rewrite destination เขียนตายตัว ไม่อ่าน env ตัวนี้

## 4. ตรวจความพร้อม

```powershell
Invoke-RestMethod http://localhost:8000/health
Invoke-RestMethod http://localhost:8000/health/db
Invoke-RestMethod http://localhost:8000/health/models
```

| จุดตรวจ | ความหมาย |
| --- | --- |
| /health | API process ตอบได้ |
| /health/db | เปิด connection และ SELECT 1 สำเร็จ |
| /health/models | URL ของแต่ละ runtime ถูกตั้งค่า; ไม่ยิง inference |
| /docs และ /openapi.json | API/schema ของ app ที่เปิดอยู่ |

ต่อจากนั้นใช้ภาพทดสอบใน User flow เพื่อยืนยัน Gateway contract ตาม [09](09_OCR_AI_PIPELINE.md) การได้ health/models=ok อย่างเดียวไม่รับรอง model availability

## 5. Startup และข้อผิดพลาดที่พบบ่อย

startup ทำ DB bootstrap/seed → category initialization/geometry backfill → Maintenance/pending check → strategy/published cache load ไม่ warm up โมเดล DB/category initialization บางช่วงล้มแล้วทำให้ startup ไม่สำเร็จ ส่วน cache load บางช่วงจับ error แล้วใช้ fallback

ถ้ารันไม่ได้ให้แก้ตาม traceback: connection URL/สิทธิ์ DB, dependencies, Gateway configuration และ CORS ตามลำดับ ไม่ใช้การลบ DB หรือ reset schema เป็นวิธีแก้เริ่มต้น

หน้า Login ปัจจุบันตรวจ mockAccounts ใน src/auth/session.ts หากต้องทดสอบใช้บัญชี development ที่ประกาศในซอร์สนั้น การแก้ users ใน DB ไม่เปลี่ยน mock account ของ Browser

## 6. Source References

| Source | Symbol/บทบาท |
| --- | --- |
| [Backend requirements](../../project_backend/requirements.txt) | dependencies |
| [Frontend package](../../project_frontend/package.json) | dependencies/scripts |
| [local_env.py](../../project_backend/app/core/local_env.py) | load_local_env |
| [db.py](../../project_backend/app/core/db.py) | ensure_database_ready และ pool |
| [main.py](../../project_backend/main.py) | startup_warmup, CORS |
| [runtime client](../../project_backend/app/core/model_runtime_client.py) | runtime_url, _gateway_headers |
| [next.config.ts](../../project_frontend/next.config.ts) | rewrites |
| [Backend example](../../project_backend/env.local.example) / [Frontend example](../../project_frontend/env.local.example) | รูปแบบ config ตัวอย่าง |
