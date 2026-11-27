$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$backupDir = Join-Path $repoRoot 'database_seed'
$sourceSecret = Read-Host 'Neon direct DATABASE_URL' -AsSecureString
$originalUrl = [Environment]::GetEnvironmentVariable('NEON_DATABASE_URL', 'Process')

Push-Location -LiteralPath $repoRoot
try {
    $env:NEON_DATABASE_URL = [System.Net.NetworkCredential]::new('', $sourceSecret).Password
    docker compose run --rm --no-deps -e NEON_DATABASE_URL -v "${backupDir}:/backup-output" --entrypoint /bin/sh db /usr/local/bin/export-ocr-database.sh
    if ($LASTEXITCODE -ne 0) { throw 'Database export failed. See the PostgreSQL error above.' }
} finally {
    [Environment]::SetEnvironmentVariable('NEON_DATABASE_URL', $originalUrl, 'Process')
    $sourceSecret.Dispose()
    Pop-Location
}
