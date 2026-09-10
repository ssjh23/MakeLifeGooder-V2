# Thin convenience wrapper. Everything here is a command you can also type by
# hand, and README.md lists them. No task runner is introduced, because a
# missing `make` on Windows is a worse first experience than three commands.
#
#   .\scripts\dev.ps1 up        start infrastructure
#   .\scripts\dev.ps1 migrate   apply migrations
#   .\scripts\dev.ps1 api       run the API
#   .\scripts\dev.ps1 worker    run the worker
#   .\scripts\dev.ps1 test      run the suite
#   .\scripts\dev.ps1 down      stop infrastructure
#   .\scripts\dev.ps1 reset     stop and DESTROY local data

param([Parameter(Position = 0)][string]$Command = "help")

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot

switch ($Command) {
    "up" {
        docker compose -f "$Root\docker-compose.yml" up -d
        Write-Host "postgres 5432 | pgbouncer 6432 (transaction) 6433 (session) | minio 9000"
    }
    "down"    { docker compose -f "$Root\docker-compose.yml" down }
    "reset"   { docker compose -f "$Root\docker-compose.yml" down -v }
    "migrate" { Push-Location "$Root\backend"; uv run alembic upgrade head; Pop-Location }
    "api"     { Push-Location "$Root\backend"; uv run uvicorn app.main:app --reload --port 8000; Pop-Location }
    "worker"  { Push-Location "$Root\backend"; uv run procrastinate --app app.worker.app.app worker; Pop-Location }
    "test"    { Push-Location "$Root\backend"; uv run pytest @($args); Pop-Location }
    default   { Get-Content $PSCommandPath -TotalCount 12 | Select-Object -Skip 1 }
}
