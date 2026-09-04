# Project Memory

# Intelligent Document Template Management System

เอกสารนี้เป็น memory ระดับโปรเจกต์ ใช้เตือนหลักคิด สถาปัตยกรรม และขอบเขตที่ไม่ควรเปลี่ยนโดยไม่ตั้งใจ

---

## 1. Project Vision

สร้างระบบ Document Intelligence Platform ที่พร้อมต่อ production โดยรองรับ:

- ตรวจจับเอกสารว่าเข้ากับ Template ใด
- จัดการ Template และ ROI ผ่าน User/Admin workflow
- Extract เฉพาะข้อมูลที่ผู้ใช้เลือก
- รองรับเอกสารหลายหน้า
- ตรวจสอบข้อมูลด้วยหลาย evidence ไม่พึ่งคะแนนเดียว
- เพิ่ม Template และ Model Services ได้ในอนาคตโดยไม่ redesign ระบบหลัก

ระบบนี้ไม่ใช่ OCR app อย่างเดียว แต่เป็น platform สำหรับจัดการความรู้ของเอกสารผ่าน Template, ROI, Verification และ Extraction pipeline

---

## 2. Target Architecture

```text
Frontend
  -> Backend / Process Service
      -> Gateway :8080
          -> Leaf Model Services
```

Backend ต้องเป็น Process Service เท่านั้น:

- ไม่ load local model
- ไม่ใช้ PaddleOCR/Paddle/Torch/Transformers ใน backend
- ไม่เรียก `model.predict()` ใน backend
- ไม่ fallback ไป local inference
- เรียก model inference ผ่าน Gateway เท่านั้น

Leaf Model Services เป็นเจ้าของ model loading และ raw inference:

| Model | Leaf Service | Gateway Endpoint |
| --- | --- | --- |
| Layout | PP-DocLayoutV3 `:8001` | `/api/v1/document-layouts` |
| Text Detection | PP-OCRv5 `:8002` | `/api/v1/text-detections?version=v5` |
| Text Recognition | Thai Recognition `:8004` | `/api/v1/text-recognitions` |
| Text Recognition Batch | Thai Recognition `:8004` | `/api/v1/text-recognition-batches` |
| Table | TableRecognitionPipelineV2 `:8013` | `/api/v1/table-model-results` |
| Image Verification | SigLIP `:8009` | `/api/v1/image-verifications` |

---

## 3. Ownership Boundary

Backend owns:

- Template detection workflow
- Database access
- ROI definition and ROI ratios
- Crop orchestration
- Auto ROI post-processing
- Reading order
- OCR text merge, normalize, and cleanup
- Table parsing, normalization, quality gates, fallback, and final shaping
- Image verification decision logic after raw SigLIP logits
- Template verification
- Export shaping

Gateway owns:

- Authentication boundary between Backend and Model Services
- Routing requests to leaf model services
- Keeping leaf endpoints hidden from Backend callers

Leaf Model Services own:

- Model loading
- GPU/CPU runtime setup
- Raw inference
- JSON-serializable inference response

Do not move project/business logic into Model Services.

---

## 4. Core Principles

### Relative ROI

Store persistent ROI as ratios, not pixels.

Every ROI should keep:

- `page_number`
- `x_ratio`
- `y_ratio`
- `width_ratio`
- `height_ratio`

Pixel coordinates are allowed only as temporary runtime geometry.

### Multi-page Native

Every pipeline must preserve page context.

Supported inputs:

- image
- multiple images
- PDF

Do not assume user page order always equals template page order.

### Template First

Templates are the source of document knowledge.

A template may include:

- pages
- extraction fields
- verification anchors
- ignore regions
- layout signature
- detection mode
- confidence thresholds
- version metadata

### Confidence-driven Workflow

Important stages should expose confidence/evidence. Final decisions should combine multiple signals rather than relying on one model score.

### Replaceable AI

Models must be replaceable behind Gateway without changing backend business logic.

---

## 5. Detection Pipeline

```text
Upload document
-> Split into pages
-> Normalize image
-> Generate layout signature
-> Retrieve candidate templates
-> Verify candidates
-> Match pages
-> Align document
-> Project ROI
-> Extract fields
-> Validate and score
-> Return result
```

Candidate retrieval is not the final decision. Final confirmation must use multiple evidence sources.

---

## 6. Extraction Pipeline

Extraction begins after template confirmation.

```text
Template
-> Page matching
-> Alignment
-> ROI projection
-> Crop final ROI
-> Model inference through Gateway
-> Backend post-processing
-> Validation
-> Result
```

---

## 7. User Workflow

```text
Upload
-> Adjust
-> Detect Template
-> Select fields
-> OCR / Table / Image processing
-> Review Ground Truth
-> Export
```

If no template is confirmed, user can still use Custom OCR / ROI workflow and optionally send a Template Request.

---

## 8. Admin Workflow

```text
Template Request or Manual Create
-> Adjust sample pages
-> Define Extraction ROI
-> Define Verification ROI
-> Configure thresholds and weights
-> Pre-Publish validation
-> Test with new document
-> Publish
```

Admin flow must preserve draft/published version behavior.

---

## 9. ROI Types

### Fix ROI

ใช้กับข้อมูลตำแหน่งคงที่

```text
Template ROI
-> Align / map document
-> Crop ROI
-> OCR / Table / Image pipeline
-> Field result
```

### Flexible ROI

Flexible ROI คือ search boundary ไม่ใช่กรอบ OCR สุดท้ายโดยตรง

```text
Search boundary
-> Layout/Text Detection inside boundary
-> Split into Text/Table/Image child ROI
-> Crop final child ROI
-> Process by type
-> Field result
```

หลักสำคัญ:

- ซ่อนกรอบแม่ใน user flow ถ้ามี ROI ย่อยที่ตรวจพบจริง
- Text paragraph grouping ใช้ geometry เป็นหลัก
- Reading order อยู่ฝั่ง Backend
- Table/Image ROI behavior ต้องไม่ถูกเปลี่ยนจาก text-line cleanup

---

## 10. Auto ROI

Auto ROI ใช้ผลจาก Layout และ Text Detection ผ่าน Gateway

Backend post-processing ต้อง:

- normalize box เป็น ROI ratio
- filter text ที่อยู่ใน table region
- filter image region ที่มี text ภายใน
- merge tiny `text_line` fragments เช่น วรรณยุกต์ จุด และเศษตัวอักษร เข้า text line หลักเมื่อ overlap หรืออยู่ใกล้
- discard tiny fragment เฉพาะกรณี isolated และไม่มี text line ใกล้
- ใช้ proximity/overlap เป็นหลัก ไม่ตัดจาก height อย่างเดียว
- sort ตาม reading order logic เดิม
- ไม่เปลี่ยน table/image ROI behavior

---

## 11. OCR Pipeline

```text
Final ROI
-> Text Detection via Gateway
-> Text line crop/rectify
-> Text Recognition via Gateway
-> Reading order
-> Merge segments
-> normalize_ocr_text()
-> cleanup_ocr_noise()
-> Final text result
```

Backend owns all OCR post-processing. Model Services return raw detection/recognition output only.

---

## 12. Table Recognition

```text
Table ROI
-> TableRecognitionPipelineV2 via Gateway
-> Raw SLANeXt/Table output
-> Backend parse and normalize
-> Structure collapse recovery
-> Quality gate
-> Semi-table fallback if needed
-> OCR-to-table fallback if needed
-> Final structured table
```

Table Model Service must not own:

- table quality gates
- semi-table reconstruction
- OCR assignment
- final table post-processing
- export shaping

Backend table result should preserve:

- `row`
- `col`
- `rowSpan`
- `colSpan`
- `hidden`
- `bbox`
- `text`
- `ocrText`
- `groundTruth`

---

## 13. Image Verification

Image Verification uses SigLIP through Gateway.

Request shape:

```json
{
  "image": "data:image/png;base64,...",
  "categories": [
    {
      "value": "signature",
      "label": "ลายเซ็น",
      "prompt": "This is a photo of a handwritten signature.",
      "match_threshold": 0.45,
      "margin_threshold": 0.04,
      "evidence_temperature": 1.0,
      "enabled": true
    }
  ]
}
```

Model Service returns raw logits in the same order as categories.

Backend owns:

- ranking
- predicted category
- target rank
- score margin
- `passed`
- status
- failure reason
- UI percentages

---

## 14. Environment Rules

Backend runtime config:

```env
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/ocr_studio
GATEWAY_URL=http://127.0.0.1:8080
MODEL_GATEWAY_API_KEY=replace-with-model-gateway-api-key
```

Frontend runtime config:

```env
NEXT_PUBLIC_API_URL=http://localhost:8000
```

Do not hardcode:

- Kaggle URL
- trycloudflare URL
- leaf model service URL inside Backend
- API tokens
- local model paths for Backend inference

Do not use `INTERNAL_API_TOKEN` in Backend-to-Gateway requests. Backend must use `MODEL_GATEWAY_API_KEY`.

---

## 15. Failure Strategy

```text
Candidate retrieval failed
-> Open Custom OCR / no-template flow

Verification failed
-> Try next candidate or return reviewable failure

Alignment failed
-> Fallback or require review

OCR confidence low
-> Return warning and allow user correction

Table confidence low
-> Try backend fallback path and expose debug metadata

Model runtime unavailable
-> Return clear runtime error

System failure
-> Return clear API error
```

---

## 16. Coding Rules

Always:

- preserve page context
- store persistent ROI as ratios
- keep model inference in Model Services
- keep process/business logic in Backend
- keep request/response contracts stable when changing routing
- prefer scoped changes over rewrites
- avoid hardcoded document types
- keep template metadata extensible

Never:

- store persistent ROI as pixels
- couple Backend business logic to local model implementations
- add local Paddle/Torch/Transformers fallback in Backend
- redesign architecture while fixing runtime routing
- change ROI/crop/reading order/table post-processing casually
- depend on a single confidence score

---

## 17. Current Production Direction

Target server layout:

```text
Backend :8000
Gateway :8080
Layout Service :8001
Text Detection Service :8002
Text Recognition Service :8004
Image Verification Service :8009
Table Service :8013
Frontend :3000 or deployed static/Next runtime
```

Backend should only need:

- database connection
- Gateway URL
- Gateway API key
- normal process dependencies from `requirements.txt`

Backend should not need model-only dependencies.

---

## 18. Never Remove Without Care

The following concepts are fundamental:

- Relative ROI
- Multi-page support
- Ignore regions
- Template lifecycle
- Template versioning
- Detection mode
- Layout signature
- Template matching
- Verification anchors
- Image verification categories
- Page matching
- Document alignment
- ROI projection
- Custom OCR
- Template request
- Admin approval
- Selectable extraction fields
- Confidence engine
- Backend/Model Service separation

---

## 19. Long-term Vision

The project should evolve by improving independent modules while preserving architecture boundaries.

Models can change. Gateway routes can change. Template metadata can grow.

The Backend must remain the stable owner of document process logic.
