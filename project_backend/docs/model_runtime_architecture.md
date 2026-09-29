# Model Runtime Architecture

## Ownership

The backend owns system and process logic:

- Template detection and final candidate decisions
- Fixed and flexible ROI handling
- Crop orchestration
- Auto ROI post-processing
- Reading order
- Text merge, normalization, and cleanup
- Table quality gates and fallback shaping
- Image verification decision logic after raw SigLIP output
- Database access and persistent state
- System Maintenance enforcement

The Gateway routes backend requests to leaf model services. Leaf services own model loading and raw inference only.

## Environment Variables

```powershell
$env:GATEWAY_URL="http://127.0.0.1:8080"
$env:MODEL_GATEWAY_API_KEY="replace-with-model-gateway-api-key"
```

Every backend request to Gateway includes:

```text
Authorization: Bearer <MODEL_GATEWAY_API_KEY>
```

Do not hard-code leaf service URLs in backend process or service code.

## Gateway Endpoints

The backend expects Gateway to expose:

```text
GET  /health
POST /api/v1/document-layouts
POST /api/v1/text-detections
POST /api/v1/text-recognitions
POST /api/v1/text-recognition-batches
POST /api/v1/table-model-results
POST /api/v1/image-verifications
```

Standard response:

```json
{
  "success": true,
  "model": "model-name",
  "result": {}
}
```

The backend may tolerate older wrapper shapes in some adapters, but new runtimes should use `result`.

## Layout

Endpoint:

```text
POST /api/v1/document-layouts
```

Request:

```json
{
  "image": "data:image/png;base64,...",
  "layout_only": true
}
```

`layout_only` defaults to `false`.

- `layout_only=true`: run PP-DocLayoutV3 only. Do not call Text Detection in the Layout pipeline.
- `layout_only=false`: preserve full document-layout pipeline behavior.

Layout Signature generation calls with `layout_only=true`. Normal `analyze_layout()` behavior must remain unchanged unless explicitly requested.

Expected result contains layout items with labels and boxes. Accepted box keys include `bbox`, `box`, `layout_bbox`, `coordinate`, `coordinates`, `dt_polys`, `poly`, `points`, or `{ "x", "y", "width", "height" }`.

Backend normalizes labels to `text`, `table`, or `image`.

## Text Detection

Endpoint:

```text
POST /api/v1/text-detections
```

Request:

```json
{
  "image": "data:image/png;base64,..."
}
```

Runtime returns raw detection polygons or boxes. Backend owns filtering, conversion to ROI geometry, crop planning, and reading order.

Text Detection is still used for extraction inside ROI crops and OCR flows. It should not be reintroduced as a redundant full-page adaptive projection pass in User Detection.

## Text Recognition

Endpoints:

```text
POST /api/v1/text-recognitions
POST /api/v1/text-recognition-batches
```

Single request:

```json
{
  "image": "data:image/png;base64,..."
}
```

Batch request:

```json
{
  "images": ["data:image/png;base64,..."]
}
```

Backend owns crop ownership, segment merge, normalization, cleanup, and final text result shaping.

## Table Recognition

Endpoint:

```text
POST /api/v1/table-model-results
```

Runtime must only run table model inference and serialize raw output. Backend owns:

- table quality gates
- semi-table reconstruction
- OCR assignment
- structure recovery
- final table post-processing
- export shaping

## Image Verification / SigLIP

Endpoint:

```text
POST /api/v1/image-verifications
```

Request:

```json
{
  "image": "data:image/png;base64,...",
  "categories": [
    {
      "value": "signature",
      "label": "Signature",
      "prompt": "This is a photo of a handwritten signature.",
      "match_threshold": 0.45,
      "margin_threshold": 0.04,
      "evidence_temperature": 1.0,
      "enabled": true
    }
  ]
}
```

Runtime must return raw logits or equivalent scores in the same order as categories. Backend owns ranking, thresholding, percentages, `passed`, status, and failure reason.

## Performance Notes

Keep model/runtime instrumentation metadata-only. Do not log image payloads or large raw model artifacts.

Known performance-sensitive paths:

- Layout response size and serialization
- Text Detection latency for crop OCR
- Template retrieval hot path
- Verification field/anchor loading
- Alignment image loading and signature comparison

Published Template Cache and request-scoped caches are backend concerns, not model runtime concerns.
