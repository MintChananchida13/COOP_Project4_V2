# Local Frontend user-flow benchmark

This is a standalone **test tool**. It does not change Frontend, Backend, API Service, OCR logic, or global verification settings. It drives the real `http://localhost:3000` user interface: choose one file, confirm document boundary, wait for Template detection/job polling, then run the matched-template OCR action when enabled. It writes one Excel row per file with observed mode, result status, Browser timing, Backend timing, and OCR wall duration. It does not export user documents or modify their ROI/ground truth. The normal application still creates detection/OCR jobs and Processing Logs, so use a test account/database if those records must not mix with normal usage.

## Setup on Windows

Start the existing Frontend, Kaggle Backend/API Service, and tunnel as usual. Set the Frontend's existing `NEXT_PUBLIC_API_URL` to the verified tunnel URL. In a **separate local Python environment** install only benchmark dependencies:

```powershell
python -m venv benchmark/.venv
benchmark/.venv/Scripts/python.exe -m pip install -r benchmark/requirements.txt
benchmark/.venv/Scripts/python.exe -m playwright install chromium
```

Put images/PDFs into `benchmark/input/`. Supported extensions: PNG, JPG, JPEG, WEBP, PDF. The input folder, browser profile, and Excel output are Git-ignored. Login happens manually once in a dedicated browser profile; the script never accepts or prints credentials.

```powershell
benchmark/.venv/Scripts/python.exe benchmark/user_flow.py --expected-mode standard
benchmark/.venv/Scripts/python.exe benchmark/user_flow.py --input benchmark/input --output benchmark/output/strict.xlsx --expected-mode strict
benchmark/.venv/Scripts/python.exe benchmark/user_flow.py --no-ocr
```

You may group files as `benchmark/input/standard/a.png`, `benchmark/input/strict/b.png`, etc. Folder names are **expected mode labels**, not a command to switch modes. The User page has no per-run verification-mode selector; the Backend setting is global. For safety this tool never changes it. If the observed mode differs, the row is `SKIP` with a reason; run each group when that mode is active, or use a separate test Backend/database for fully automatic cross-mode comparison. Do not use the Admin global-setting endpoint on a shared system just for benchmarking.

The Excel file is saved after every image. `PASS` means the Template detection response has a valid matched/no-match result, not that OCR text is accurate. OCR status is separate: `PASS`, `SKIP_NO_MATCH`, `SKIP_NO_ENABLED_ROI`, or `NOT_RUN`. A run can take many minutes on Kaggle CPU; increase `--timeout-seconds` if necessary. One-way network and actual paint timings remain blank because the application does not measure them. `ocr_wall_ms` is measured by the automation from OCR button click until the result editor appears; it is **not** a model-only duration. `end_to_end_ms` comes from the Browser's existing detection timing.

Run unit checks with `python -m unittest discover -s benchmark -p 'test_*.py'`. These tests do not require Playwright or a running system. Browser and model integration is **Not Tested** until run against the user's local Frontend and Kaggle services.
