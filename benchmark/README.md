# คู่มือรัน User Flow Benchmark

`user_flow.py` เปิดเบราว์เซอร์ ล็อกอิน อัปโหลดเอกสาร ยืนยันขอบ ค้นหา Template และอ่าน OCR ให้จบในรอบเดียว ถ้าไม่พบ Template ระบบจะลองใช้ Auto ROI และบันทึกเวลาเตรียม ROI แยกไว้

วางไฟล์ PNG, JPG, JPEG, WEBP หรือ PDF ใน `benchmark/input/` แล้วเลือกวิธีรันด้านล่าง คำสั่งหลักรันจากโฟลเดอร์ `D:\coop\COOP_Project4_New`

## วิธีที่ 1 รันด้วย Docker

เปิด Docker Desktop ก่อน หากใช้ Model Gateway ที่มีอยู่แล้ว ให้ตั้ง `GATEWAY_URL` และคีย์ตามคู่มือ Docker จากนั้นรันคำสั่งนี้ [คู่มือ Docker](../README_DOCKER.md)

```powershell
docker compose --profile benchmark run --rm --build benchmark --repeats 1
```

Compose จะเปิด Frontend, Backend และ PostgreSQL ตามลำดับที่ต้องใช้ Chromium จะรันแบบไม่มีหน้าต่าง และไฟล์ Excel จะบันทึกกลับมายัง `benchmark/output/` บนเครื่อง

ถ้าจะรันบริการโมเดลใน Docker ด้วย ให้เปิดชุดโมเดลก่อน แล้วรัน Benchmark ด้วยไฟล์ Compose ชุดเดียวกัน

```powershell
docker compose -f compose.yaml -f compose.models.yaml --profile table-v2 --profile optional-paddle up -d --build
docker compose -f compose.yaml -f compose.models.yaml --profile table-v2 --profile optional-paddle --profile benchmark run --rm --build benchmark --repeats 1
```

## วิธีที่ 2 รันด้วย PowerShell บน Windows

1. เปิด Backend ใน PowerShell หน้าต่างหนึ่ง รอให้ขึ้น `Application startup complete`

   ```powershell
   cd D:\coop\COOP_Project4_New\project_backend
   .\venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000
   ```

2. เปิด Frontend ในอีกหน้าต่างหนึ่ง แล้วรอให้เว็บเปิดที่ `http://localhost:3000`

   ```powershell
   cd D:\coop\COOP_Project4_New\project_frontend
   npm run dev
   ```

3. เปิด PowerShell ที่โฟลเดอร์หลัก หากยังไม่มีสภาพแวดล้อมสำหรับ Benchmark ให้ติดตั้งครั้งแรก

   ```powershell
   cd D:\coop\COOP_Project4_New
   python -m venv benchmark/.venv
   .\benchmark\.venv\Scripts\python.exe -m pip install -r benchmark/requirements.txt
   .\benchmark\.venv\Scripts\python.exe -m playwright install chromium
   ```

4. ตรวจว่า Backend เชื่อมต่อฐานข้อมูลและเข้าถึง Model Gateway ได้ แล้วเริ่ม Benchmark

   ```powershell
   .\benchmark\.venv\Scripts\python.exe benchmark/user_flow.py --repeats 1
   ```

### ตัวเลือกที่ใช้บ่อย

| ตัวเลือก | ใช้ทำอะไร |
| --- | --- |
| `--repeats 1` | รันไฟล์ละหนึ่งรอบ ค่าเริ่มต้นคือสองรอบ |
| `--no-ocr` | วัดถึงการค้นหา Template เท่านั้น |
| `--expected-mode standard` | ตรวจว่าโหมด Verification ที่ใช้อยู่ตรงกับที่คาดไว้ |
| `--frontend https://coop-project4-v2.vercel.app` | รันกับเว็บไซต์ที่ deploy แล้ว |
| `--ground-truth <ไฟล์.json>` | ใช้ไฟล์ระบุผลค้นหา Template ที่คาดไว้ตามชื่อเอกสาร |

จัดไฟล์เป็นโฟลเดอร์ย่อย เช่น `Fixed ROI — Passport`, `Flex ROI` หรือ `No Template` ได้ สคริปต์จะอ่านไฟล์ในทุกโฟลเดอร์และรายงานประเภท ROI ตามผลที่เกิดขึ้นจริง ชื่อโฟลเดอร์ที่ตรงกับโหมด Verification เช่น `standard` หรือ `strict` ใช้ระบุโหมดที่คาดไว้ได้เช่นเดียวกับ `--expected-mode`

ตั้ง `BENCHMARK_EMAIL` และ `BENCHMARK_PASSWORD` หากต้องการใช้บัญชี User ต่างจากบัญชีทดสอบเริ่มต้น

## ผลลัพธ์และการอ่านเวลา

เปิดไฟล์ `user_flow_*.xlsx` ใน `benchmark/output/` ทั้งสองชีตใช้ **Run ID** เดียวกันเพื่อเทียบผลของไฟล์เดียวกัน

- **Template Search** เก็บเวลาเตรียมไฟล์ ตรวจขอบและ Auto Crop เวลารอผลค้นหา และเวลาประมวลผลฝั่ง Backend รวมช่วงรอ Model API จำนวน Candidate มาจากก่อนและหลัง Pre-filter ส่วน Anchor นับเฉพาะที่เข้าขั้นตรวจจริง
- **OCR Processing** เก็บเวลา Auto ROI แยกจาก OCR และแสดงประเภท Fixed ROI, Flex ROI หรือ No Template ถ้ารอบเดียวใช้ทั้ง Fixed และ Flex จะแสดงเป็น Flex ROI จำนวน ROI ของ Auto ROI มาจากกรอบที่สร้างใน workspace หลังตัดกรอบซ้ำ ส่วนจำนวน Text Box มาจาก DET ที่เรียกแยกก่อน REC
- **Model Duration** รวม `meta.duration_ms` แยกตามโมเดลและขั้นตอนที่เรียก เช่น Layout สำหรับ Flex ROI หรือ Auto ROI และ Table OCR ระหว่างอ่าน OCR ถ้าขาดเวลาของ call ใด ยอดรวมของโมเดลนั้นจะว่าง

เวลาทั้งหมดเป็นมิลลิวินาที อ่านเวลา User, Backend และ Model แยกกันเพราะช่วงเวลาซ้อนกัน เวลาพักห้าวินาทีก่อนเริ่มเอกสารถัดไปอยู่นอกเวลาที่วัด

Benchmark บันทึกผลหลังแต่ละรอบ ถ้า Excel ล็อกไฟล์ สคริปต์จะสร้างไฟล์สำรอง ถ้าเขียน Excel ไม่ได้ สคริปต์จะลองบันทึกข้อมูลเป็น JSON ในโฟลเดอร์ชั่วคราวของระบบ
