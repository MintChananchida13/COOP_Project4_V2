# Model Runtime Architecture

Source review: 2026-11-10. Describes the checked-out Backend; external Gateway/Leaf
implementations and live inference were not verified in this documentation review.

## Ownership

Frontend -> Backend/Process Service -> Model API Gateway -> Leaf services.
Backend owns template decisions, staged verification, ROI geometry, crops, OCR
cleanup, table reconstruction, SigLIP decisions, database, caches and Maintenance.
Leaf services own model loading and raw inference. Backend must not load model
weights, call Leaf URLs directly or fall back to local inference.

## Configuration and routes

Set DATABASE_URL, GATEWAY_URL and MODEL_GATEWAY_API_KEY. GATEWAY_URL is a host base
such as http://127.0.0.1:8080 without /paddle. Requests use
`Authorization: Bearer <MODEL_GATEWAY_API_KEY>`. Never expose the key to Browser.

Source: app/core/model_runtime_client.py and app/core/ocr_model_config.py.

| Operation | Default Gateway path |
| --- | --- |
| Layout | /paddle/api/v1/document-layouts |
| DET single | /paddle/api/v1/text-detections?version=v6 |
| DET batch | /paddle/api/v1/text-detection-batches?version=v6 |
| REC single | /paddle/api/v1/text-recognitions |
| REC batch | /paddle/api/v1/text-recognition-batches |
| Table | /paddle/api/v1/table-model-results?det_version=6&rec_version=5&det_model=baseline&rec_model=thai_ft_v3 |
| SigLIP single | /paddle/api/v1/image-verifications |
| SigLIP batch | /paddle/api/v1/image-classification-batches |

DET/REC paths can change with active saved settings. Legacy paths starting
/api/v1/ are prefixed with /paddle by the client. Do not duplicate this in services.
Defaults are ocr_det_v6_medium and thai_ocr_v5_mobile. Adding configuration does
not install weights. Admin add/edit/check sends a synthetic image through both
single and batch APIs and checks identity, including model_selection.model_name
in leaf-inference-v1 responses. No /ocr-models registry/warmup is performed.

Backend GET /health/models checks URL configuration only, not authentication,
reachability, inference, response contracts or accuracy.

## Layout and detection

User matching uses page-count/orientation/aspect prefilters followed by staged
Text/Image Anchors. Publish and prepublish simulation do not generate or require
Layout Signatures. Removed embedding-generation routes must not be reintroduced;
embedding-jobs aliases now refer to Publish jobs.

Auto ROI still uses Layout. analyze_layout(use_text_detection=False) requests
layout_only=true; its default use_text_detection=True requests layout_only=false.
Raw-layout cache keys separate these modes. Do not add redundant full-page DET
to User search.

The retained diagnostic analyze_layout_signature() requests layout_only=false
and expects raw.layout and raw.text_detection. This compatibility helper is not
the current publish pipeline. Run Simulation does not regenerate signatures.

## DET / REC

Single requests send image; batch requests send images. Backend validates result
count/order/boundaries, rejects ambiguous or missing results and normalizes
supported legacy/new containers independently of model version. See
[TEXT_RESPONSE_CONTRACT.md](TEXT_RESPONSE_CONTRACT.md). Backend owns crop mapping,
reading order, segment merge and cleanup. Wrapper changes must not add inference.

## Table

The Table adapter inventories remote output, applies Backend structure rules,
maps OCR tokens and formats rows/HTML/Markdown/structured cells. See
[TABLE_POSTPROCESSING.md](TABLE_POSTPROCESSING.md) and [TABLE_MEMORY.md](TABLE_MEMORY.md).
HTTP wait and local processing are nested spans, not independent inference times.

## Image verification / SigLIP

The active Backend-local V2.4 policy is app/model_runtime/siglip_policy.py,
configured by siglip_v24.py. Prompts come from siglip_v23.py: eight Positive plus
seven Negative prompts, 15 ordered finite logits; numbers is absent.

V2.4 keeps Positive Rank 1 when the highest Negative is no more than 0.8 logit
units above it and configured relative gates pass. Optional gates default to
unset. Expected category must equal final prediction. V2.3 guards are comparison
helpers only. Sigmoid/softmax displays are not calibrated confidence.

A supplied trace must match vector/config/policy fingerprint; supported raw-only
responses are evaluated locally with V2.4. The fingerprint is a consistency check,
not a security signature. See [V2.4 details](../SIGLIP_V24_IMPLEMENTATION.md).

This checkout has no API_Service_Kaggle directory and no sibling policy loader.
External Gateway/Leaf services must provide compatible routes, prompt order,
cardinality and contracts; their implementation cannot be verified from this repo.

## Runtime limits and validation

Published cache, verification settings and detection/prepublish jobs use
process-local state. Shared invalidation/job storage is required before scaling
workers. PostgreSQL remains persistent source of truth.

Gateway authentication is separate from application authentication. User/Admin
auth currently includes mock/permissive behavior; see section 15 of the
[Developer Guide](../../DEVELOPER_GUIDE_TH.md) before exposing APIs.

Log counts/timing, not image payloads/secrets. Validate single/batch order, empty
results, identity and errors against the deployed Gateway. Fixtures do not
establish live compatibility or accuracy.
