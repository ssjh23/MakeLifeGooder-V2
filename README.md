# Ledger

## About this project

Built in 2026 as **MakeLifeGooder**, Group 1's project in the
[Junior Dev SG](https://app.notion.com/p/3827ff4c3854820b854981985c2ec254)
mentorship programme.

**Who is it for:** people who want to upload their bank statement and have it
categorised automatically, and see where their money goes without building
their own Excel sheet.

- Documentation (Notion): [Junior Dev SG Group 1 - MakeLifeGooder](https://activerecall.notion.site/Junior-Dev-SG-Group-1-MakeLifeGooder-3827ff4c3854820b854981985c2ec254)
- [System Architecture Diagram](https://claude.ai/artifact/UowKnvvb4CfkLj8GSqbCRU)
- [Local Development Architecture Diagram](https://claude.ai/artifact/LMptduGe6AK2GEF2wttsu6)

A bank statement classifier for Singapore. Upload a statement PDF, the rows are
extracted and checked against the printed total, merchants are resolved and
categorised, and spend is reported by month, category and card.

No bank connection. No card numbers. The only input is a PDF you already have.

The design lives in Notion; `CLAUDE.md` summarises it. This file only covers
getting it running.

## Status

Implemented, uncommitted. The four gates, the classification cascade, the
descriptor normaliser and the service methods are written. `git log` on this
checkout still shows only the initial commit — everything described below is
in the working tree, not on a branch yet.

`uv run pytest` currently reports **388 passed, 3 failed, 1 skipped**:

- One real bug: a tampered session cookie is accepted (200) instead of
  rejected (401) — `TC_AUTH_010`, worth fixing before this commits.
- Two status-code mismatches in the OIDC-not-configured tests (expect 503,
  get 401) — looks like a route/dependency ordering issue, not a design
  disagreement.

Two things remain genuinely unwritten, both already called out as deferred:
manual row entry for unreadable scans, and password reset delivery. A third
gap is narrower than it looks — `pdfplumber_parser.py` handles
password-protection, missing-text-layer and non-statement detection, then
stops before column inference, because that step needs real bank statement
fixtures (see Fixture statements below) and nothing later depends on it.
`monopoly-core` is a hard dependency now, not optional, and covers the
supported-bank happy path without needing that fallback.

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

Upload a real statement PDF from a bank `monopoly-core` supports (DBS/POSB,
OCBC, UOB, Standard Chartered, HSBC, Citibank, Maybank, Trust) and the whole
path runs end to end: presigned upload to MinIO, extract, reconcile against
the printed total, classify through the cascade, review, commit, and the
dashboard reflects it. That path is what the 388 passing tests exercise.

The one path that still fails on purpose is a PDF from a bank Monopoly
doesn't cover, since the pdfplumber fallback parser isn't wired into the
registry yet:

1. The browser uploads straight to MinIO with a presigned URL. The API never
   sees the bytes.
2. A statement row appears as `pending` and a job is enqueued in the same
   transaction.
3. The worker claims it and logs `statement.extract.started`.
4. The parser registry has nowhere left to route to, the job dead-letters,
   and the row becomes `failed` with `last_error`.
5. The UI polls to `failed` and shows a request id.

Every log line in steps 2 to 4 carries the same `trace_id` the API returned in
step 2. If that holds, the async hop is instrumented correctly, which is the
one piece of observability that cannot be retrofitted cheaply.

Auth0 env vars (`AUTH0_DOMAIN`, `AUTH0_CLIENT_ID`, `AUTH0_CLIENT_SECRET`,
`AUTH0_REDIRECT_URI` in `.env.example`) are optional for local dev — leave
them blank and `/auth/register` + `/auth/login` (password auth) still work;
the `/auth/oidc/*` routes return a clean error rather than a crash until
they're set.

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
