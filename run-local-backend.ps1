$ErrorActionPreference = "Stop"

$backend = Join-Path $PSScriptRoot "project_backend"
Set-Location $backend

# =========================
# Backend
# =========================

if (-not (Test-Path ".\.env.local")) {
    Write-Host "Missing project_backend\.env.local. Create it from project_backend\env.local.example and add your database/Kaggle URLs."
}

if (-not (Test-Path ".\venv\Scripts\python.exe")) {
    python -m venv venv
}

.\venv\Scripts\python.exe -m pip install -r requirements.txt

.\venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload
