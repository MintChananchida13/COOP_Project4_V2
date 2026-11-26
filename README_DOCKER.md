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

## ข้อมูลเดิมและการย้ายเครื่อง

Compose เก็บข้อมูล PostgreSQL และไฟล์ Backend ไว้ใน Docker volumes การรันบนเครื่องใหม่ครั้งแรกจะเริ่มด้วยฐานข้อมูลใหม่ ถ้าต้องการข้อมูลเดิม ให้กู้คืน PostgreSQL backup และย้ายไฟล์จาก `project_backend/storage` กับ `project_backend/app/storage` ไปยัง volumes `backend-storage` และ `backend-app-storage` ตามลำดับ หากข้อมูลเดิมมีคีย์ OCR ที่เข้ารหัสไว้ ให้ย้ายค่า `EXTERNAL_OCR_ENCRYPTION_KEY` เดิมมาด้วย

สำหรับเครื่องที่เปิดให้คนอื่นใช้ ให้เปลี่ยนรหัสผ่านบัญชีเริ่มต้นและตั้ง `POSTGRES_PASSWORD` ก่อนเริ่มระบบครั้งแรก หากใช้ URL ฐานข้อมูลที่ Compose สร้างให้ ให้เลือกรหัสผ่านที่ใช้ใน URL ได้โดยตรง
