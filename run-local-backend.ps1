$ErrorActionPreference = "Stop"

$backend = Join-Path $PSScriptRoot "project_backend"
Set-Location $backend

# =========================
# Backend
# =========================

$envFile = ".\.env.local"

if (-not (Test-Path $envFile)) {
    Write-Host "Missing project_backend\.env.local"
    exit 1
}

# Load .env.local into environment variables
Get-Content $envFile | ForEach-Object {
    $line = $_.Trim()

    if (
        $line -and
        -not $line.StartsWith("#") -and
        $line.Contains("=")
    ) {
        $name, $value = $line -split "=", 2
        $name = $name.Trim()
        $value = $value.Trim().Trim('"').Trim("'")

        [System.Environment]::SetEnvironmentVariable(
            $name,
            $value,
            "Process"
        )
    }
}

Write-Host "DATABASE_URL loaded:" ([bool]$env:DATABASE_URL)
Write-Host "LAYOUT_MODEL_URL:" $env:LAYOUT_MODEL_URL
Write-Host "TEXT_DETECTION_MODEL_URL:" $env:TEXT_DETECTION_MODEL_URL
Write-Host "TEXT_RECOGNITION_MODEL_URL:" $env:TEXT_RECOGNITION_MODEL_URL
Write-Host "TABLE_MODEL_URL:" $env:TABLE_MODEL_URL
Write-Host "IMAGE_VERIFICATION_MODEL_URL:" $env:IMAGE_VERIFICATION_MODEL_URL

if (-not (Test-Path ".\venv\Scripts\python.exe")) {
    python -m venv venv
}

.\venv\Scripts\python.exe -m pip install -r requirements.txt

.\venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload