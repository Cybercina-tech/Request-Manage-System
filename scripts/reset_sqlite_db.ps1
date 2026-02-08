# Iraniu — Safe SQLite reset for Windows (clean slate after corruption).
# Run from project root: .\scripts\reset_sqlite_db.ps1
# Then create a superuser: python manage.py createsuperuser

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path (Join-Path $projectRoot "manage.py"))) {
    $projectRoot = Get-Location
}
Set-Location $projectRoot

$dbPath = Join-Path $projectRoot "db.sqlite3"
$dbShm = Join-Path $projectRoot "db.sqlite3-shm"
$dbWal = Join-Path $projectRoot "db.sqlite3-wal"

Write-Host "Project root: $projectRoot"
Write-Host "Stopping any process that might lock the database..."
# Optional: close Django runserver if running in another terminal

if (Test-Path $dbPath) {
    Remove-Item $dbPath -Force
    Write-Host "Removed db.sqlite3"
}
if (Test-Path $dbShm) {
    Remove-Item $dbShm -Force
    Write-Host "Removed db.sqlite3-shm"
}
if (Test-Path $dbWal) {
    Remove-Item $dbWal -Force
    Write-Host "Removed db.sqlite3-wal"
}

Write-Host "Running migrations..."
python manage.py migrate
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ""
Write-Host "Database reset complete. Create a superuser to log in:"
Write-Host "  python manage.py createsuperuser"
Write-Host ""
