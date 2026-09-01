$ErrorActionPreference = "Stop"

$frontend = Join-Path $PSScriptRoot "project_frontend"
Set-Location $frontend

if (-not (Test-Path ".\node_modules")) {
    npm install
}

npm run dev
