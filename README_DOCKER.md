# รัน OCR Studio ด้วย Docker

## 1. เปิดเว็บบนเครื่องปลายทาง

1. คัดลอกหรือ clone repo ไปเครื่องปลายทาง
2. เปิด Docker Desktop แล้วรอให้ Docker Engine พร้อม
3. เปิด PowerShell ในโฟลเดอร์ที่มีไฟล์ `compose.yaml` แล้วรัน

   ```powershell
   docker compose up -d --build
   docker compose ps
   ```

4. เปิดเว็บที่ <http://localhost:3000>

คำสั่งนี้เปิด Frontend (Next.js), Backend (FastAPI) และ PostgreSQL ให้พร้อมกัน หน้าเว็บเรียก Backend ผ่าน `/backend` การเปิดเว็บใช้ได้ทันทีเมื่อทั้งสามบริการพร้อม ส่วน OCR ต้องเชื่อม Model Gateway ตามขั้นตอนด้านล่าง

## 2. เชื่อม Model Gateway สำหรับ OCR

### ใช้ Gateway ที่มีอยู่แล้ว

สร้างไฟล์ `.env` ไว้ข้าง `compose.yaml` แล้วแทนค่าตัวอย่างด้วยค่าของ Gateway จริง

```dotenv
GATEWAY_URL=https://gateway.example.com
MODEL_GATEWAY_API_KEY=your-gateway-key
```

ถ้า Gateway ต้องใช้คีย์ ให้ใช้ค่าเดียวกับที่ตั้งบน Gateway ตัว Backend อยู่ใน container จึงต้องใช้ URL ที่ container เชื่อมถึงได้ ถ้า Gateway อยู่บนเครื่องเดียวกันที่ใช้ Docker Desktop ให้ใช้ `host.docker.internal` พร้อมพอร์ตที่ Gateway เปิดรับ

หลังแก้ `.env` ให้รัน `docker compose up -d` อีกครั้งเพื่อให้ Backend ใช้ค่าใหม่

### รันโมเดลพร้อมเว็บบนเครื่องเดียวกัน

นำ `API_Service` และไฟล์ weights ไปด้วย โดยวางไฟล์ใน `API_Service/weights` ตามโครงสร้างของบริการโมเดล เครื่องปลายทางต้องมี NVIDIA GPU ที่ Docker ใช้งานได้

```powershell
docker compose -f compose.yaml -f compose.models.yaml --profile table-v2 --profile optional-paddle up -d --build
```

ชุดนี้ให้ Backend ใช้ `http://gateway:8000` โดยอัตโนมัติเมื่อไม่ได้กำหนด `GATEWAY_URL` ใน `.env` ใช้ชุด `-f` และ `--profile` เดิมเมื่อเรียก `ps`, `logs` หรือ `down` สำหรับชุดที่มีโมเดล

## คำสั่งที่ใช้บ่อย

| งาน | คำสั่ง |
| --- | --- |
| ดูสถานะ | `docker compose ps` |
| ดู log | `docker compose logs --tail 100 frontend backend db` |
| อัปเดตหลังแก้โค้ด | `docker compose up -d --build` |
| หยุดระบบ | `docker compose down` |

หากต้องเปิดเว็บให้เครื่องอื่นในเครือข่ายเข้าถึง ให้ตั้ง `WEB_BIND_ADDRESS=0.0.0.0` ใน `.env` แล้วรัน `docker compose up -d` อีกครั้ง พอร์ตเว็บตั้งได้ด้วย `WEB_PORT` ค่าเริ่มต้นคือ `3000`

## รัน User Flow Benchmark

วางไฟล์ทดสอบใน `benchmark/input` แล้วรันจากโฟลเดอร์หลัก

```powershell
docker compose --profile benchmark run --rm --build benchmark --repeats 1
```

Benchmark เปิด Chromium แบบไม่มีหน้าต่าง และบันทึก Excel ลง
`benchmark/output` บนเครื่อง ดูคำสั่งสำหรับชุดที่รันโมเดลด้วยใน
[คู่มือ Benchmark](benchmark/README.md)

## ข้อมูลเดิมและการย้ายเครื่อง

### ย้ายทั้งฐานข้อมูลจาก Neon

บนเครื่องใหม่ เปิด Docker Desktop แล้วรันจากโฟลเดอร์ที่ clone มา

```powershell
.\scripts\export_database.ps1
```

ใส่ Connection String แบบ Direct ของฐานข้อมูล Neon เมื่อระบบถาม คำสั่งจะ
export โครงสร้างครบทุกตารางและข้อมูลเดิมลง `database_seed/database.dump`
โดยเว้นข้อมูลใน `processing_logs` และ `processing_log_images` ตารางทั้งสอง
จะว่างหลัง restore ส่วนข้อมูลใน `ocr_jobs` และ `publish_jobs` ยังมาด้วย
Git จะข้ามไฟล์ dump ซึ่งอาจมีข้อมูลผู้ใช้ ให้เก็บไว้ในพื้นที่ส่วนตัว
หากมี dump ที่สร้างก่อนปรับเงื่อนไขนี้ ให้ย้ายไฟล์เดิมออกแล้ว export ใหม่
Compose ใช้บัญชี PostgreSQL
บนเครื่องเป็นเจ้าของตารางและใช้สิทธิ์ของเครื่องปลายทาง

ค่าเริ่มต้นใช้ PostgreSQL 16 หาก Neon ใช้รุ่นสูงกว่า ให้ตั้ง
`POSTGRES_IMAGE` ในไฟล์ `.env` ให้เป็นรุ่นเดียวกับ Neon ก่อน export และ
ก่อนสร้างฐานข้อมูลใน Docker เช่น `POSTGRES_IMAGE=postgres:17`

หลัง export เสร็จ ให้ตรวจว่า `DATABASE_URL` ในไฟล์ `.env` หรือ PowerShell
ไม่ได้ชี้ไป Neon แล้วรัน

```powershell
docker compose up -d --build
```

PostgreSQL จะกู้คืนไฟล์โดยอัตโนมัติเมื่อสร้าง volume ฐานข้อมูลใหม่
Backend จะรอจน PostgreSQL พร้อม หากเคยเปิดระบบและมี volume อยู่แล้ว ให้ใช้
ชื่อ Compose project ใหม่ เช่น `docker compose -p ocr-local up -d --build`
หลังจากหยุดบริการเดิมที่ใช้พอร์ตเดียวกัน การใช้ชื่อใหม่จะเก็บ volume เดิมไว้

รูปที่ฝังในฐานข้อมูลจะมาพร้อม dump ส่วนไฟล์บนดิสก์ใน
`project_backend/storage` และ `project_backend/app/storage` ให้คัดลอกแยก
ไปยัง volumes `backend-storage` และ `backend-app-storage` ตามลำดับ
ข้ามโฟลเดอร์ `project_backend/app/storage/processing_logs` ถ้าไม่ต้องการ
ย้ายไฟล์ภาพจากประวัติการประมวลผล
ถ้ามีคีย์ OCR ที่เข้ารหัสไว้ ให้ใช้ `EXTERNAL_OCR_ENCRYPTION_KEY` ค่าเดิม

### ย้ายเฉพาะ Template ที่เผยแพร่แล้ว

บนเครื่องต้นทาง ตั้ง `DATABASE_URL` ให้ชี้ฐานข้อมูลที่มี Template แล้วรันจาก
`project_backend`

```powershell
cd project_backend
.\venv\Scripts\python.exe -m app.core.template_seed --output ../template_seed/templates.json
```

ไฟล์ JSON รวมเวอร์ชัน Active หรือ Published, หน้า, ROI, Anchor, หมวดหมู่ภาพ
ที่อ้างถึง และรูปอ้างอิงไว้ในไฟล์เดียว คำสั่งจะหยุดถ้าดึงรูปไม่ได้หรือพบ
รูปที่อ้างด้วย URL ภายนอก ให้ใช้ไฟล์รูปที่เข้าถึงได้บนเครื่องต้นทางหรือรูปที่
ฝังอยู่ในข้อมูล Template ก่อน export

วางไฟล์ไว้ใน `template_seed/templates.json` บนเครื่องปลายทางผ่านช่องทางส่วนตัว
Git จะข้ามไฟล์ JSON ในโฟลเดอร์นี้

### ดึง Template จาก Neon ครั้งเดียวบนเครื่องใหม่

หลัง clone ให้เปิด Docker Desktop แล้วรันจากโฟลเดอร์หลัก คำสั่งจะถาม
Connection String ของ Neon ใช้ค่า Neon เฉพาะตอน export แล้วล้างออก
ก่อนเปิดระบบปกติ

```powershell
$seedDir = (Resolve-Path -LiteralPath .\template_seed).Path
$neonExportSecret = Read-Host 'Neon DATABASE_URL' -AsSecureString
try {
    $env:DATABASE_URL = [System.Net.NetworkCredential]::new('', $neonExportSecret).Password
    docker compose run --rm --build --no-deps -e DATABASE_URL -v "${seedDir}:/export" backend python -m app.core.template_seed --output /export/templates.json
    if ($LASTEXITCODE -ne 0) { throw 'Template export failed' }
} finally {
    Remove-Item Env:DATABASE_URL -ErrorAction SilentlyContinue
    Remove-Variable neonExportSecret -ErrorAction SilentlyContinue
}
```

ขั้นตอนนี้ใช้ได้เมื่อ Neon เก็บรูปอ้างอิงฝังในข้อมูล Template และเครื่องใหม่
เชื่อม Neon ได้ ถ้ารูปเป็นไฟล์ที่อยู่เฉพาะเครื่องเดิม ให้ export บนเครื่องเดิม
ด้วยวิธีด้านบน แล้วโอน `templates.json` มาโดยตรง

เมื่อได้ไฟล์แล้ว ให้ใช้ค่าเริ่มต้นของ Compose ที่เชื่อม PostgreSQL ผ่าน `db`
เอา `DATABASE_URL` ของ Neon ออกจากไฟล์ `.env` ที่โฟลเดอร์หลักหากมีอยู่
แล้วตั้ง `TEMPLATE_SEED_FILE=/template_seed/templates.json` ในไฟล์เดียวกัน
ก่อนรันจากโฟลเดอร์หลัก

```powershell
docker compose up -d --build
```

Backend จะนำเข้าชุด Template ก่อนโหลด cache และบันทึกสถานะไว้ในฐานข้อมูล
ถ้าฐานข้อมูลมี Template อยู่แล้ว ระบบจะใช้ข้อมูลที่มีอยู่ การแก้ Template
ภายหลังจะบันทึกที่ PostgreSQL ใน Docker

สำหรับเครื่องที่เปิดให้คนอื่นใช้ ให้เปลี่ยนรหัสผ่านบัญชีเริ่มต้นและตั้ง `POSTGRES_PASSWORD` ก่อนเริ่มระบบครั้งแรก หากใช้ URL ฐานข้อมูลที่ Compose สร้างให้ ให้เลือกรหัสผ่านที่ใช้ใน URL ได้โดยตรง
