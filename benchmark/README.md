# Local Frontend user-flow benchmark

This standalone test tool drives the real `http://localhost:3000` user interface: automatically log in, choose one file, confirm document boundary, and wait for a found/no-match Template result. It saves one Excel row per run, waits **2 seconds**, clicks **เอกสารใหม่**, then uploads the next file/run in the same tab. It stops before Extraction ROI OCR. Verification (including any text/image model calls required by the active mode) remains part of the Template search being measured. The application and global verification settings are unchanged; detection jobs and Processing Logs may still be created by the normal flow.

## Setup on Windows

Each input file runs **twice consecutively** by default, with a separate Excel row for each run. Both sheets include `run_number`, and the summary includes `runs_per_file`. Excel is saved after every completed run. Use `--repeats 1` for a single run or another positive number to change the count. Model and application caches are not reset between runs, so do not assume the first run is cold or the second is the only warm run.

One browser tab stays open throughout the benchmark; no test tab is closed between runs. If the browser is closed manually, the script stops with a failure status and retains completed rows in Excel. If New Document cannot return the page to the upload screen, the benchmark stops instead of mixing documents.

Start the existing Frontend, Kaggle Backend/API Service, and tunnel as usual. Set the Frontend's existing `NEXT_PUBLIC_API_URL` to the verified tunnel URL. In a **separate local Python environment** install only benchmark dependencies:

```powershell
python -m venv benchmark/.venv
benchmark/.venv/Scripts/python.exe -m pip install -r benchmark/requirements.txt
benchmark/.venv/Scripts/python.exe -m playwright install chromium
```

Put images/PDFs into `benchmark/input/`. Supported extensions: PNG, JPG, JPEG, WEBP, PDF. The input folder, browser profile, and Excel output are Git-ignored. Each invocation logs in through the real login form using the requested demo defaults: email `user@ocr.com`, password `user123`. Optional `BENCHMARK_EMAIL` and `BENCHMARK_PASSWORD` environment variables override those defaults. Credentials are not printed or written to Excel.

```powershell
benchmark/.venv/Scripts/python.exe benchmark/user_flow.py --expected-mode standard
benchmark/.venv/Scripts/python.exe benchmark/user_flow.py --input benchmark/input --output benchmark/output/strict.xlsx --expected-mode strict
benchmark/.venv/Scripts/python.exe benchmark/user_flow.py --no-ocr
benchmark/.venv/Scripts/python.exe benchmark/user_flow.py --expected-mode standard --repeats 2
```

You may group files as `benchmark/input/standard/a.png`, `benchmark/input/strict/b.png`, etc. Folder names are **expected mode labels**, not a command to switch modes. The User page has no per-run verification-mode selector; the Backend setting is global. For safety this tool never changes it. If the observed mode differs, the row is `SKIP` with a reason; run each group when that mode is active, or use a separate test Backend/database for fully automatic cross-mode comparison. Do not use the Admin global-setting endpoint on a shared system just for benchmarking.

The Excel file is saved after every run, before the two-second pause and New Document action. `matched=true/false` distinguishes found/no-match; both are successful completed searches (`PASS`), not accuracy assertions. Existing OCR columns remain for compatibility: `ocr_status=NOT_RUN`, `ocr_wall_ms` blank. `--no-ocr` is retained as a compatibility flag; this tool always measures Template search only. `end_to_end_ms` comes from the Browser's detection flow and excludes the pause/reset action. A run can take many minutes on Kaggle CPU; increase `--timeout-seconds` if necessary.

`backend_to_model_ms`, `frontend_to_backend_ms`, and `backend_to_frontend_ms` remain blank because one-way network time is not measured. `frontend_render_ms` remains blank because actual render/paint is not instrumented. For measured HTTP durations use the model `*_wait_ms` or Model Calls `http_request_ms`, and for browser transport use `uploadJobMs`/`jobPollingMs`. SigLIP runtime is available only when image verification actually runs and the API returns inference timing. Null console values no longer hide a real Backend measurement in Excel.

Run unit checks with `benchmark/.venv/Scripts/python.exe -m unittest discover -s benchmark -p 'test_*.py'`. The optional Excel round-trip test needs openpyxl. Fourteen local tests passed, covering default auto-login, credential-safe errors, found/no-match completion without OCR, response-listener cleanup, two-second reset, repeated runs, and Excel run numbers. A headless Chromium test with an isolated HTML/HTTP fixture also passed two runs (found and no-match), two New Document actions, zero OCR clicks, and Excel export. Full Frontend/Kaggle model integration after this change is **Not Tested** until run against the user's services.
