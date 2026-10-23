# Temporary Kaggle CPU end-to-end test

Place `project_backend/` and `API_Service_Kaggle/` side by side in a writable Kaggle checkout. Run these commands from `API_Service_Kaggle/kaggle/`. This setup starts one API worker on localhost port 8080 and one backend worker on localhost port 18081. Only the backend gets an optional HTTPS tunnel. Windows Next.js remains on port 3000.

## One-cell startup after a Kaggle restart

Enable Notebook Internet and configure the two required Kaggle Secrets below. Run this **single Python cell**; do not paste secrets into it:

```python
import pathlib
import subprocess
import sys

repo = pathlib.Path("/kaggle/working/COOP_Project4_Kaggle")
if not repo.exists():
    subprocess.run([
        "git", "clone", "--branch", "kaggle-cpu-runtime", "--single-branch",
        "https://github.com/MintChananchida13/COOP_Project4_V2.git", str(repo),
    ], check=True)
subprocess.run([sys.executable, str(repo / "API_Service_Kaggle/kaggle/bootstrap.py")], check=True)
```

`bootstrap.py` also clones the requested branch when invoked from an existing copy of the script and the Kaggle checkout is absent. It checks the repository origin and branch, verifies both virtual environments with Python/pip, `pip check`, OCR/Backend/SigLIP smoke tests, retrieves secrets, runs `manage.py start`, `status`, and `check`, and starts a Cloudflare quick tunnel. It downloads a SHA256-verified, pinned Linux amd64 `cloudflared` binary into `/kaggle/working/ocr_runtime` only when needed. It waits for a newly emitted tunnel URL and verifies public `/health` before marking the tunnel ready. Network policy or model/database startup can still prevent success; the summary then shows the failed stage and log paths. Existing healthy services and tunnel are reused, not duplicated.

The clone cell can use `bootstrap.py` only **after these local changes are pushed** to the named branch. This task does not commit or push them.

By default an existing checkout is **not updated**. To fast-forward a clean checkout only, run `python /kaggle/working/COOP_Project4_Kaggle/API_Service_Kaggle/kaggle/bootstrap.py --update`. A dirty checkout is left untouched; this script never resets or force-checkouts local changes. A different origin/branch fails safely. After a successful run, use the printed `NEXT_PUBLIC_API_URL=https://...` in Windows `project_frontend/.env.local` and restart Next.js. Never use an unverified tunnel URL as a successful startup signal.

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

NumPy 1.26.4 has no Python 3.13 support; NumPy 2.1.3 does. Backend compatibility with NumPy 2 is checked by importing its real `main` module together with CV2 and database/text dependencies. This catches import and binary ABI failures, but a full backend workflow remains to be tested. The isolated backend must pass `pip check`; API conflicts are handled as described below. The installer never uses blanket `--no-deps`. The SigLIP smoke runs a tiny `SiglipModel` forward pass and verifies `logits_per_image` plus `AutoProcessor` availability; it does not prove the full SO400M checkpoint works. Run a real SigLIP request separately before claiming image classification works.

### Reusing environments despite Kaggle system conflicts

The isolated backend must pass full `pip check`. The API environment uses `--system-site-packages`, so `pip check` can also report unrelated Kaggle packages such as `torchvision`, `google-adk`, or `gradio`. Those are printed as non-blocking warnings; conflicts owned by PaddlePaddle, PaddleOCR, PaddleX, Torch, Transformers, FastAPI, Uvicorn, Pydantic, or NumPy remain failures. The installer also checks effective package versions, API and backend imports, CPU Torch operation, and a tiny SigLIP forward pass. This forward pass does **not** verify the full SO400M checkpoint; run `python real_siglip.py` or `python test_all_models.py --model siglip` for that. Generic Torch installation is refused when working Kaggle CPU Torch is absent, to avoid CUDA dependency downloads.

After the changed scripts are present in the Kaggle checkout, rerun the one-cell startup above or run `python /kaggle/working/COOP_Project4_Kaggle/API_Service_Kaggle/kaggle/bootstrap.py`. Healthy environments are reused. If a critical check fails, inspect `/kaggle/working/ocr_runtime/bootstrap_install.log`, `api.log`, and `backend.log`. Do not remove healthy environments to silence system-package warnings.

## 3. Start and verify

```bash
python manage.py start
python manage.py status
python manage.py check
python manage.py integration
python real_siglip.py
python test_all_models.py
python test_all_models.py --model layout
python test_all_models.py --model det
python test_all_models.py --model rec
python test_all_models.py --model table
python test_all_models.py --model siglip
```

`start` launches API Service first, waits for `GET http://127.0.0.1:8080/health`, then launches backend and waits for `GET http://127.0.0.1:18081/health`. `check` also calls the real backend `/health/db` (`SELECT 1` against Neon) and `/health/models` (configuration). Backend startup initializes schema, so failure can occur before health responds. `integration` uses the backend's actual `remote_analyze_layout()` client to send a small image to the API Service with `layout_only=true`; this downloads/loads the real layout model. `real_siglip.py` downloads/loads the full SigLIP model and checks that two ordered raw logits return. It uses a synthetic image to test runtime compatibility, not classification accuracy. Both model tests can take minutes and require substantial RAM. They do not test all eight models.

`test_all_models.py` calls all eight API routes over HTTP, one model group at a time. It creates a document page, separate text crops, and a gridded invoice table; checks authentication, envelope, raw output fields, batch count and input order; and saves PASS/FAIL/SKIP, service timing, wall time, and API process RSS before/after each model to `all_models_result.json`. The default timeout is 900 seconds per request for first downloads; use `--timeout 1200` if needed. Use `--output /kaggle/working/ocr_runtime/my_results.json` to choose the report path. RSS is unavailable when the API PID file or Linux `/proc` cannot be read. Results validate runtime output structure, not OCR accuracy. A non-PASS result exits with code 1; inspect `api.log` for model errors.

The model groups run sequentially, but the API Service keeps loaded models cached. If RAM is insufficient for the full suite, run one `--model` at a time and restart the API Service between groups. First-load timing includes download and initialization; subsequent calls in the same group reuse the model.

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
