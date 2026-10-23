# Temporary Kaggle CPU end-to-end test

Place `project_backend/` and `API_Service_Kaggle/` side by side in a writable Kaggle checkout. Run these commands from `API_Service_Kaggle/kaggle/`. This setup starts one API worker on localhost port 8080 and one backend worker on localhost port 18081. Only the backend gets an optional HTTPS tunnel. Windows Next.js remains on port 3000.

## 1. Configure Kaggle Secrets

In Kaggle Notebook **Add-ons → Secrets**, create:

| Name | Required | Source / purpose |
|---|---|---|
| `DATABASE_URL` | Yes | Neon PostgreSQL connection URL. Use a disposable test database if possible. |
| `MODEL_GATEWAY_API_KEY` | Yes | Shared random bearer token for backend → API Service. |
| `FRONTEND_URL` | Optional | Additional exact CORS origin; backend already allows `http://localhost:3000`. |

The scripts retrieve these through `kaggle_secrets.UserSecretsClient` (or existing environment variables), pass them only to child processes, and never print their values. Do not paste them into notebooks or Git. `GATEWAY_URL=http://127.0.0.1:8080` and `MODEL_DEVICE=cpu` are set by the startup script. The backend reads `DATABASE_URL` and initializes PostgreSQL schema on startup; this can write tables and seed users. Use a test Neon database.

## 2. Inspect and install

```bash
python install.py --report-only
python install.py
```

The report prints Python and package **versions only** before installation. The backend gets an isolated virtual environment using `project_backend/requirements.txt`. The API gets a separate virtual environment with system packages visible, so existing Kaggle `torch` and `paddlepaddle` are reused if present. Their installed versions are reported even if they differ from service pins; verify imports and real inference before drawing a compatibility conclusion. Missing heavy packages are installed from `API_Service_Kaggle/requirements.txt`. Both environments live in `/kaggle/working/ocr_runtime` by default. Override `KAGGLE_RUNTIME_DIR` to use another working directory.

Dependency differences found in source:

| Package | Backend pin | API pin | Strategy |
|---|---|---|---|
| FastAPI | 0.110.0 | 0.115.12 | Separate environments |
| Pydantic | 2.6.4 | 2.11.4 | Separate environments |
| OpenCV headless | 4.9.0.80 | 4.11.0.86 | Separate environments |
| NumPy | 1.26.4 | 1.26.4 | Install per environment |
| PaddlePaddle | None | 3.2.1 | Reuse Kaggle copy when present |
| PyTorch | None | 2.6.0 | Reuse Kaggle copy when present |
| PaddleOCR | None | 3.7.0 with `doc-parser` extra | Install into API environment |
| Transformers | None | 4.51.3 | Install into API environment |

Package resolution and wheel support depend on the Kaggle Python image. `pip check` and import smoke tests run after installation. The requested OCR and SigLIP weights download on first real inference and can consume substantial RAM and disk.

## 3. Start and verify

```bash
python manage.py start
python manage.py status
python manage.py check
python manage.py integration
```

`start` launches API Service first, waits for `GET http://127.0.0.1:8080/health`, then launches backend and waits for `GET http://127.0.0.1:18081/health`. `check` also calls the real backend `/health/db` (`SELECT 1` against Neon) and `/health/models` (configuration). Backend startup initializes schema, so failure can occur before health responds. `integration` uses the backend's actual `remote_analyze_layout()` client to send a small image to the API Service with `layout_only=true`; this downloads/loads the real layout model and is the meaningful backend → API inference check. It can take minutes and does not test all eight models.

Logs are separate at `/kaggle/working/ocr_runtime/api.log` and `backend.log`; PIDs are stored beside them. Inspect with `tail -n 80 /kaggle/working/ocr_runtime/api.log` and the corresponding backend file. `status` shows PIDs and ports; install `psutil` in the notebook environment for per-process RSS and CPU. Kaggle system tools such as `ps`, `free -h`, and `ss -ltn` can also inspect resources. Stop with:

```bash
python manage.py stop
```

## 4. Optional HTTPS tunnel

Install `cloudflared` separately if Kaggle networking permits, then run:

```bash
python tunnel.py check
python tunnel.py start
python tunnel.py stop
```

`check` verifies local backend and HTTPS reachability; it cannot prove Cloudflare tunnel transport works. `start` opens a temporary Cloudflare quick tunnel to **backend port 18081 only**. Read its generated HTTPS URL in `tunnel.log`. Kaggle may block tunnel traffic or terminate notebook processes, so verify the public `/health` URL from Windows before configuring the frontend.

In the Windows `project_frontend/.env.local`, set the **existing** variable `NEXT_PUBLIC_API_URL=https://<tunnel-host>` and restart the Next.js dev server. The backend already allows browser origin `http://localhost:3000`; if the frontend uses a different origin, set the exact `FRONTEND_URL` Kaggle secret and restart backend. The frontend's `next.config.ts` also contains a fixed `/backend/:path*` rewrite to an unrelated address; direct API clients use `NEXT_PUBLIC_API_URL`, but any feature calling `/backend/...` may still follow that rewrite. This setup does not modify frontend source.

## Validation status

Local syntax and small management unit tests can run without model packages. Kaggle installation, tunnel availability, Neon access, model downloads, real inference, RAM/CPU measurements, and frontend end-to-end behavior are **Not Tested** until the notebook steps succeed. No production deployment is implied.
