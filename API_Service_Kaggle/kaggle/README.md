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

Kaggle Python 3.13.15 may provide pip without `ensurepip`. Install `virtualenv` with the working base pip first:

```bash
/usr/bin/python3 -m pip --version
/usr/bin/python3 -m pip install virtualenv
```

`install.py` also installs `virtualenv` automatically if absent. It never calls `ensurepip`. Use `/usr/bin/python3` for the following commands on the reported Kaggle image:

```bash
/usr/bin/python3 install.py --report-only
/usr/bin/python3 install.py
```

The installer targets **Python 3.13**. `--report-only` reads both original requirements files, prints the effective dependency plan, and runs import/API smoke checks without installing anything. `virtualenv` creates an isolated backend environment and an API environment with `--system-site-packages`, so the API can see existing Kaggle PaddlePaddle and PyTorch. The installer checks Python version, `pyvenv.cfg`, and `python -m pip --version` for each environment. It retains complete environments and recreates only incomplete or broken ones under the runtime directory. Effective pins are generated from the original files plus [py313_overrides.json](py313_overrides.json); the original requirements are never edited. Both environments live in `/kaggle/working/ocr_runtime` by default. Override `KAGGLE_RUNTIME_DIR` to use another working directory.

For CPU thread control, set `MODEL_CPU_THREADS` before `manage.py start` (default `2`). The manager passes `OMP_NUM_THREADS` and `MKL_NUM_THREADS` to both child processes before import. PaddleOCR models explicitly use `device="cpu"`; the service does not call PaddlePaddle's unsupported `FLAGS_cpu_num_threads` setter. SigLIP retains `torch.set_num_threads()`. Restart both processes after changing thread settings.

On the tested Kaggle image, Python is 3.13.15, PaddlePaddle is 3.2.1, PaddleOCR is 3.7.0, PaddleX is 3.7.2, PyTorch is 2.11.0+cpu, Transformers is 5.16.1, and NumPy is 2.1.3. PP-OCRv6 Medium DET and Thai PP-OCRv5 Mobile REC have run on that CPU image. Preflight still checks the **current** session before reusing them. The PaddleOCR `doc-parser` extra is resolved with pip so missing optional dependencies are installed without forcing a new PaddleOCR wheel.

Dependency differences found in source:

| Package | Backend pin | API pin | Strategy |
|---|---|---|---|
| FastAPI | 0.110.0 → 0.115.12 | 0.115.12 | Separate environments; backend override |
| Pydantic | 2.6.4 → 2.11.4 | 2.11.4 | Separate environments; backend override |
| OpenCV headless | 4.9.0.80 → 4.11.0.86 | 4.11.0.86 | Separate environments; backend override |
| NumPy | 1.26.4 → 2.1.3 | 1.26.4 → 2.1.3 | Python 3.13 compatible override in both |
| PaddlePaddle | None | 3.2.1 | Reuse working Kaggle copy |
| PyTorch | None | 2.6.0 | Reuse working Kaggle CPU copy, including 2.11.0+cpu |
| PaddleOCR | None | 3.7.0 with `doc-parser` extra | Retain working wheel; ensure extra dependencies |
| Transformers | None | 4.51.3 | Reuse 5.16.1 **only if** SigLIP API smoke passes; otherwise install pin and test again |

NumPy 1.26.4 has no Python 3.13 support; NumPy 2.1.3 does. Backend compatibility with NumPy 2 is checked by importing its real `main` module together with CV2 and database/text dependencies. This catches import and binary ABI failures, but a full backend workflow remains to be tested. Both virtual environments must pass `pip check`; dependency conflicts or failed imports stop installation. The installer never uses blanket `--no-deps`. The SigLIP smoke runs a tiny `SiglipModel` forward pass and verifies `logits_per_image` plus `AutoProcessor` availability; it does not prove the full SO400M checkpoint works. Run a real SigLIP request separately before claiming image classification works.

## 3. Start and verify

```bash
python manage.py start
python manage.py status
python manage.py check
python manage.py integration
python real_siglip.py
```

`start` launches API Service first, waits for `GET http://127.0.0.1:8080/health`, then launches backend and waits for `GET http://127.0.0.1:18081/health`. `check` also calls the real backend `/health/db` (`SELECT 1` against Neon) and `/health/models` (configuration). Backend startup initializes schema, so failure can occur before health responds. `integration` uses the backend's actual `remote_analyze_layout()` client to send a small image to the API Service with `layout_only=true`; this downloads/loads the real layout model. `real_siglip.py` downloads/loads the full SigLIP model and checks that two ordered raw logits return. It uses a synthetic image to test runtime compatibility, not classification accuracy. Both model tests can take minutes and require substantial RAM. They do not test all eight models.

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
