# Model Runtime Architecture

## Ownership

The local backend owns system and process logic:

- Template detection
- Fix and flexible ROI handling
- Crop orchestration
- Auto ROI filtering and expansion
- Reading order
- Text merge, normalize, and cleanup
- Table quality gates
- Semi-table processing
- Summary and key-value processing
- Result merge and post-process
- Database access

The Gateway routes backend requests to leaf model services. Leaf model services
own model loading and raw inference only.

## Environment Variables

Set the Gateway URL and API key:

```powershell
$env:GATEWAY_URL="http://127.0.0.1:8080"
$env:MODEL_GATEWAY_API_KEY="replace-with-model-gateway-api-key"
```

Local server, staging, and future permanent URLs must be swapped by changing
this environment variable only. The backend calls the Gateway endpoints below;
the Gateway forwards to the leaf services. Do not hard-code leaf service URLs in
process or service code.

Every backend request to the Gateway includes:

```text
Authorization: Bearer <MODEL_GATEWAY_API_KEY>
```

## Runtime API Contract

The Gateway should expose:

```text
GET /health
POST /api/v1/layout-predictions
POST /api/v1/text-detections
POST /api/v1/text-recognitions
POST /api/v1/table-model-results
POST /api/v1/image-classifications
```

Standard response:

```json
{
  "success": true,
  "model": "model-name",
  "result": {}
}
```

The backend also accepts legacy `data` in place of `result` while older demo
runtimes are being migrated.

## Runtime Result Expectations

- Layout runtime: raw PP-DocLayoutV3 layout detections.
- Text detection runtime: raw OCR detection polygons or boxes.
- Text recognition runtime: raw recognition text/score output for one image, or
  `{ "results": [...] }` for batch input.
- Table runtime: raw Table Recognition / SLANeXt output under `raw_output` or
  `output`; backend runs table post-processing and quality gates.
- Image verification runtime: raw SigLIP logits matching the category prompt
  order sent by the backend.

## Exact Result Schemas

### Layout

`GATEWAY_URL /api/v1/layout-predictions` forwards to PP-DocLayoutV3 on the
Layout leaf service. It returns raw layout items. The backend accepts nested
objects/lists, but each detected item must contain one box and one label.

```json
{
  "success": true,
  "model": "PP-DocLayoutV3",
  "result": {
    "items": [
      {
        "bbox": [20, 10, 120, 60],
        "label": "table",
        "score": 0.95
      }
    ]
  }
}
```

Accepted box keys: `bbox`, `box`, `layout_bbox`, `coordinate`, `coordinates`,
`dt_polys`, `poly`, `points`, or `{ "x", "y", "width", "height" }`.

Accepted label keys: `type`, `label`, `category`, `layout_type`, `block_type`,
or `region_type`. Labels are normalized in backend to `text`, `table`, or
`image`.

Accepted confidence keys: `score`, `confidence`, or `prob`.

### Text Detection

`GATEWAY_URL /api/v1/text-detections` forwards to PP-OCRv5 on the Text Detection
leaf service. It returns raw text detection polygons or boxes. Do not group
paragraphs or apply reading order in the runtime.

```json
{
  "success": true,
  "model": "PP-OCRv5_server_det",
  "result": {
    "items": [
      {
        "dt_polys": [[10, 10], [80, 10], [80, 24], [10, 24]],
        "score": 0.93,
        "label": "text"
      }
    ]
  }
}
```

The same accepted box and confidence keys as Layout are supported. Backend
converts polygons to boxes, filters noisy fragments, and computes ROI ratios.

### Text Recognition

`GATEWAY_URL /api/v1/text-recognitions` forwards to the Thai Recognition leaf
service. It supports single and batch input.

Single response:

```json
{
  "success": true,
  "model": "th_PP-OCRv5_mobile_rec",
  "result": {
    "rec_text": "ABC123",
    "rec_score": 0.98
  }
}
```

Batch response:

```json
{
  "success": true,
  "model": "th_PP-OCRv5_mobile_rec",
  "result": {
    "results": [
      { "rec_text": "A", "rec_score": 0.91 },
      { "rec_text": "B", "rec_score": 0.92 }
    ]
  }
}
```

Accepted text keys: `rec_text`, `text`, or `label`.

Accepted confidence keys: `rec_score`, `confidence`, `score`, or `prob`.

Backend keeps crop ownership, reading order, merge, normalize, and cleanup.

### Table Recognition

`GATEWAY_URL /api/v1/table-model-results` forwards to TableRecognitionPipelineV2
on the Table leaf service. It must only run Table Recognition / SLANeXt
inference and JSON serialization.

```json
{
  "success": true,
  "model": "SLANeXt_wired/SLANeXt_wireless",
  "result": {
    "raw_output": [
      {
        "html": "<table><tr><td>A</td><td>B</td></tr></table>",
        "structure_model": "SLANeXt_wired",
        "score": 0.88
      }
    ]
  }
}
```

`raw_output` may be any JSON-serializable dict/list shape produced from the
model. The backend currently extracts:

- HTML from `html`, `pred_html`, `table_html`, or `structure_html`
- rows from common row/table fields
- structured cells from common cell/table fields
- raw debug fields such as `table_type`, `model_name`, `structure_model`,
  `score`, and `confidence`

Runtime must not apply quality gates, semi-table reconstruction, structure
recovery, OCR assignment, or final table post-processing.

### Image Verification / SigLIP

`GATEWAY_URL /api/v1/image-classifications` forwards to SigLIP on the Image
Verification leaf service. It receives categories in the exact prompt order that
backend expects. The runtime must return logits in the same order.

```json
{
  "success": true,
  "model": "google/siglip-so400m-patch14-384",
  "result": {
    "logits": [2.0, 0.0, -1.0],
    "device": "cuda:0"
  }
}
```

The backend owns thresholding, ranking, UI percentages, `passed`, status, and
failure reason.

## Request Shapes

Layout, text detection, text recognition, table:

```json
{
  "image": "data:image/png;base64,..."
}
```

Batch text recognition:

```json
{
  "images": ["data:image/png;base64,..."]
}
```

Image verification:

```json
{
  "image": "data:image/png;base64,...",
  "categories": [
    {
      "value": "qr_code",
      "label": "QR Code",
      "prompt": "..."
    }
  ]
}
```
