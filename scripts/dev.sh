#!/usr/bin/env bash
# Same commands as dev.ps1, for a POSIX shell. See README.md.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

case "${1:-help}" in
  up)
    docker compose -f "$ROOT/docker-compose.yml" up -d
    echo "postgres 5432 | pgbouncer 6432 (transaction) 6433 (session) | minio 9000"
    ;;
  down)    docker compose -f "$ROOT/docker-compose.yml" down ;;
  reset)   docker compose -f "$ROOT/docker-compose.yml" down -v ;;
  migrate) cd "$ROOT/backend" && uv run alembic upgrade head ;;
  api)     cd "$ROOT/backend" && uv run uvicorn app.main:app --reload --port 8000 ;;
  worker)  cd "$ROOT/backend" && uv run procrastinate --app app.worker.app.app worker ;;
  test)    shift; cd "$ROOT/backend" && uv run pytest "$@" ;;
  *)       sed -n '2,4p' "${BASH_SOURCE[0]}"; echo "up | down | reset | migrate | api | worker | test" ;;
esac
