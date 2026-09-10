# Ledger

A bank statement classifier for Singapore. Upload a statement PDF, the rows are
extracted and checked against the printed total, merchants are resolved and
categorised, and spend is reported by month, category and card.

No bank connection. No card numbers. The only input is a PDF you already have.

The design lives in Notion; `CLAUDE.md` summarises it. This file only covers
getting it running.

## Status

Scaffold. Every seam is wired and the plumbing tests pass. The business logic
is not written: the four gates, the classification cascade, the descriptor
normaliser and the service methods raise `NotImplementedError` and have failing
tests that define what correct looks like. **A red suite is the expected state**,
and the count of red tests is the work remaining.

## Prerequisites

| Tool | Version | Notes |
| --- | --- | --- |
| Docker Desktop | any recent | Postgres, pgbouncer, MinIO |
| uv | any | Installs and manages Python 3.12 itself |
| Node | 20 or newer | Frontend and client generation |

`uv` is not bundled with Windows. Install it once:

```powershell
winget install --id astral-sh.uv
```

You do not need a separate Python install. `uv sync` fetches 3.12.

## Running it

```bash
cp .env.example .env          # PowerShell: Copy-Item .env.example .env
docker compose up -d

cd backend
uv sync
uv run alembic upgrade head
uv run pytest                 # plumbing green, logic red, by design
uv run uvicorn app.main:app --reload --port 8000
```

In a second terminal, the worker:

```bash
cd backend
uv run procrastinate --app app.worker.app.app worker
```

In a third, the frontend:

```bash
cd frontend
npm install
npm run generate:client       # reads http://localhost:8000/openapi.json
npm run dev
```

Then open http://localhost:5173.

## What "working" looks like right now

Upload any PDF on the import screen. With no logic written, the correct outcome
is a **failure**, and the path it takes is the proof that the architecture is
wired:

1. The browser uploads straight to MinIO with a presigned URL. The API never
   sees the bytes.
2. A statement row appears as `pending` and a job is enqueued in the same
   transaction.
3. The worker claims it and logs `statement.extract.started`.
4. Extraction raises `NotImplementedError`, the job dead-letters, and the row
   becomes `failed` with `last_error`.
5. The UI polls to `failed` and shows a request id.

Every log line in steps 2 to 4 carries the same `trace_id` the API returned in
step 2. If that holds, the async hop is instrumented correctly, which is the
one piece of observability that cannot be retrofitted cheaply.

## Ports

| Service | Port | Notes |
| --- | --- | --- |
| API | 8000 | `/docs` for the OpenAPI page |
| Frontend | 5173 | |
| Postgres | 5432 | Direct. Migrations and the worker only |
| pgbouncer, transaction mode | 6432 | **What the API uses** |
| pgbouncer, session mode | 6433 | Test fixture. Nothing may point here |
| MinIO | 9000 | Console on 9001, `minioadmin` / `minioadmin` |

## Two things that look like details and are not

**The application connects as `ledger_app`, which owns nothing.** Row level
security does not apply to a table's owner. Connect as the migration role and
isolation silently disappears while every test still passes. If you add a
connection string anywhere, check which role it uses.

**Presigned URLs are signed for a specific host.** `S3_ENDPOINT` is where this
process reaches storage; `S3_PUBLIC_ENDPOINT` is where the browser will. Only
the second is used for signing, because the signature covers the host and
rewriting it afterwards invalidates it.

## Tests

```bash
uv run pytest                      # everything
uv run pytest -m p0                # data integrity, money, isolation, privacy
uv run pytest -m security          # the isolation and redaction suite
uv run pytest -k TC_REC            # one area, by test case id
uv run pytest backend/tests/unit/classify/test_normalise.py::test_x  # one test
```

Every test carries the `TC-` identifier from the Test Cases page in its name,
so the traceability table stays checkable.

### Fixture statements

Parser tests need real bank PDFs, which cannot be generated. Drop redacted
statements into `backend/tests/fixtures/pdfs/banks/` and describe each one in
`manifest.toml`. They are gitignored. Until then those tests skip with a
message rather than passing silently.

The four failure shapes are generated and need no input from you:

```bash
uv run python -m tests.fixtures.generate
```

## Licence

AGPL-3.0. See `LICENSE`, which currently needs the verbatim text pasted in.
