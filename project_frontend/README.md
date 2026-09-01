This is a Next.js project bootstrapped with create-next-app.

Getting Started
First, run the development server:

npm run dev
# or
yarn dev
# or
pnpm dev
# or
bun dev
Open http://localhost:3000 with your browser to see the result.

You can start editing the page by modifying app/page.tsx. The page auto-updates as you edit the file.

This project uses next/font to automatically optimize and load Geist, a new font family for Vercel.

Learn More
To learn more about Next.js, take a look at the following resources:

Next.js Documentation - learn about Next.js features and API.
Learn Next.js - an interactive Next.js tutorial.
You can check out the Next.js GitHub repository - your feedback and contributions are welcome!

Deploy on Vercel
The easiest way to deploy your Next.js app is to use the Vercel Platform from the creators of Next.js.

Check out our Next.js deployment documentation for more details.

.\venv\Scripts\activate

Terminal 1: Model Runtime Service
โหลดโมเดลค้างไว้ เช่น PP-DocLayoutV3, PP-OCRv5_server_det,
th_PP-OCRv5_mobile_rec และ SigLIP สำหรับ Image Anchor
uvicorn model_server:app --host 127.0.0.1 --port 8010

Terminal 2: Main Backend
backend หลักจะเรียก model service ผ่าน HTTP และไม่ warm-up โมเดลเอง
$env:DATABASE_URL="postgresql://postgres:postgres@localhost:5432/ocr_studio"
$env:LAYOUT_MODEL_URL="https://kaggle-demo-layout.example"
$env:TEXT_DETECTION_MODEL_URL="https://kaggle-demo-text-detection.example"
$env:TEXT_RECOGNITION_MODEL_URL="https://kaggle-demo-text-recognition.example"
$env:TABLE_MODEL_URL="https://kaggle-demo-table.example"
$env:IMAGE_VERIFICATION_MODEL_URL="https://kaggle-demo-image-verification.example"

ปรับความเร็วของ Template Detection
ค้นหา Top 5 แต่ประเมินหนักเฉพาะ Top 2 และ align เฉพาะ Top 1
$env:DETECTION_RETRIEVAL_LIMIT="5" $env:DETECTION_FULL_EVAL_LIMIT="2" $env:DETECTION_ALIGNMENT_LIMIT="1"

ถ้าต้องการโหมดเร็วมากสำหรับพรีวิว ROI ให้ใช้:
$env:DETECTION_FULL_EVAL_LIMIT="1"
$env:DETECTION_ALIGNMENT_LIMIT="0"
uvicorn main:app

Backend ไม่โหลด model เองแล้ว ต้องตั้ง Model URL แยกตามชนิดก่อนรัน process จริง
uvicorn main:app --reload

cd D:\coop\COOP_Project4\project_backend
.\venv\Scripts\activate
$env:DATABASE_URL="postgresql://postgres:postgres@localhost:5432/ocr_studio"
$env:LAYOUT_MODEL_URL="https://kaggle-demo-layout.example"
$env:TEXT_DETECTION_MODEL_URL="https://kaggle-demo-text-detection.example"
$env:TEXT_RECOGNITION_MODEL_URL="https://kaggle-demo-text-recognition.example"
$env:TABLE_MODEL_URL="https://kaggle-demo-table.example"
$env:IMAGE_VERIFICATION_MODEL_URL="https://kaggle-demo-image-verification.example"
uvicorn main:app

docker start ocr-postgres-dev

.\venv\Scripts\activate
$env:DATABASE_URL="postgresql://postgres:postgres@localhost:55432/ocr_studio" 
$env:LAYOUT_MODEL_URL="https://kaggle-demo-layout.example"
$env:TEXT_DETECTION_MODEL_URL="https://kaggle-demo-text-detection.example"
$env:TEXT_RECOGNITION_MODEL_URL="https://kaggle-demo-text-recognition.example"
$env:TABLE_MODEL_URL="https://kaggle-demo-table.example"
$env:IMAGE_VERIFICATION_MODEL_URL="https://kaggle-demo-image-verification.example"
docker start ocr-postgres-dev
uvicorn main:app

.\venv\Scripts\activate
uvicorn model_server:app --host 127.0.0.1 --port 8010  

postgresql://neondb_owner:npg_9KTud6sbUNHQ@ep-damp-credit-az927w30-pooler.c-3.ap-southeast-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require

