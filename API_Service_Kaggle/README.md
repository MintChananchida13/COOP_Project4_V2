# Kaggle CPU model runtime

One FastAPI process serves the backend's eight model routes. Models load lazily and remain cached in the process. The backend retains ROI, reading order, OCR normalization, table reconstruction and SigLIP decisions.

## Contract

All POST routes require `Authorization: Bearer <MODEL_GATEWAY_API_KEY>` and base64 image data URLs. Responses use `{ "success": true, "model": "...", "result": {...}, "timing": {...} }`. Raw Paddle result dictionaries are preserved. Batch `result.results` follows input order. DET accepts only `version=v6`.

| Route | Request | Result |
|---|---|---|
| `/api/v1/document-layouts` | `image`, optional `layout_only` | `raw.layout` and, for full mode, `raw.text_detection` |
| `/api/v1/text-detections?version=v6` | `image` | Raw `res.dt_polys`, `res.dt_scores` |
| `/api/v1/text-detection-batches?version=v6` | `images` | `results` with raw detection dictionaries |
| `/api/v1/text-recognitions` | `image` | Raw `res.rec_text`, `res.rec_score` |
| `/api/v1/text-recognition-batches` | `images` | `results` with raw recognition dictionaries |
| `/api/v1/table-model-results` | `image` | `raw_output` containing `table_res_list`, `cell_box_list`, `table_ocr_pred` |
| `/api/v1/image-verifications` | `image`, `categories`, optional `image_category` | `logits`, `device` |
| `/api/v1/image-classification-batches` | `images`, `categories` | `results` with ordered raw logits |

`GET /health` reports status and loaded model names. It does not force a model download.

OCR variants are defined in `config/model_registry.json`. Detection and recognition single and batch endpoints accept optional `version` and `model_id` query parameters. Omitting them keeps the existing v6 detection and v5 Thai recognition defaults. Unknown combinations fail rather than selecting another model. The authenticated registry and model-check APIs provide metadata and perform a real load check; loaded instances are cached until the service restarts. An unchanged directory with replaced weights requires a service restart.

The Admin model form registers new local-weight models through the Backend and the authenticated registry API. Registration does not load the model or change the active selection. New entries are written to `config/model_registry.json` on the service host and survive a service restart; on temporary Kaggle storage they are lost if the checkout is recreated unless the registry and weights are persisted separately. The service account needs write access to the registry directory. Registry-managed models are not edited or deleted in the Admin form.

Recognition batches use Paddle's `predict` with `OCR_RECOGNITION_BATCH_SIZE` images per call (default 4, positive integer). Set it to 1 before restarting the service to disable multi-image inference. The separate `BATCH_CHUNK_SIZE` applies to SigLIP. To compare single-image and batch recognition on the target machine, run `python kaggle/benchmark_recognition_batch.py --version v5 --model-id thai_ft_v3 crop1.png crop2.png` with representative text crops and `MODEL_WEIGHTS_ROOT` set; timings and text/score comparisons are printed without storing image data. Test other recognition variants with the matching version and model ID.

Fine-tuned model paths are paths on the model-service host. The registry uses `/workspace/API_service/weights/...`; for Kaggle or another mount set `MODEL_WEIGHTS_ROOT` to the actual directory containing `det-v6` and `rec-th`/`rec-v6`. Each inference directory must contain `inference.yml`, `inference.pdiparams`, and either `inference.json` or `inference.pdmodel`. Missing or incompatible files fail that model's load independently. The registry does not install or supply model weights.

## Models and setup

Python 3.10 or 3.11 is recommended. The pinned requirements are an installation starting point and require validation on the target Windows or Kaggle image. Install `requirements.txt`, set `MODEL_GATEWAY_API_KEY` in the environment, then run `python start.py` or `python -m uvicorn app.main:app --host 0.0.0.0 --port 8080 --workers 1` from this directory. Set backend `GATEWAY_URL` to this server's base URL and use the same API key. Run tests with `pip install -r requirements-test.txt` and `pytest`.

The service uses PP-DocLayoutV3, PP-OCRv6_medium_det, th_PP-OCRv5_mobile_rec, TableRecognitionPipelineV2 with SLANeXt wired and wireless models plus PP-OCRv5 internal DET, and google/siglip-so400m-patch14-384. Paddle and Hugging Face download model weights on first use into their normal caches. Set their cache environment variables before startup when persistent storage is needed. A public HTTPS tunnel, if needed, is a separate deployment step; keep the API key in environment variables.

For Kaggle CPU notebooks: enable internet for the first model download, install the pinned requirements in a fresh Python session, set `MODEL_GATEWAY_API_KEY` through notebook secrets or environment, set `MODEL_DEVICE=cpu`, and launch with `python start.py`. Expose port 8080 through an HTTPS tunnel separately. Kaggle RAM varies; monitor process RSS and CPU during real inference. `MAX_CONCURRENT_INFERENCE` defaults to 1, `INFERENCE_QUEUE_LIMIT` to 8, `MODEL_CPU_THREADS` to 2, and `BATCH_CHUNK_SIZE` to 16. Large batches can still exceed RAM because input images are decoded before inference. SigLIP SO400M and the table pipeline are especially memory intensive.

## Verification status

The API contract tests use fake runtime calls. Real model downloads, real inference, backend to service integration, and Kaggle CPU memory measurements must be run in an environment with the pinned dependencies and model access. No production readiness claim follows from unit tests alone.
