# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Status

Scaffolded, not implemented. Every seam is wired and runs; the business logic is not written.

**Written and expected to work**: infrastructure, configuration, the twelve-table schema with its row level security policies, the tenant session, request context and telemetry, the error envelope, the HTTP surface, queue plumbing, the storage adapter, password authentication, and the test harness.

**Not written, and failing on purpose**: the four gates, the classification cascade, the descriptor normaliser, money conversion, and every service method body. These raise `NotImplementedError` and have failing tests that define correct behaviour. **A red suite is the expected state.** Endpoints whose service is unwritten return 501, which distinguishes them from broken ones.

The design comes from the project's Notion workspace (Junior Dev SG Group 1 - MakeLifeGooder). Treat those pages as canonical and this file as their summary. When the two disagree, the Architecture Decision Records page wins on technology choices.

Source pages: Goal page, DB Schema Diagram, User Flows, C4 Architecture & Capacity Model, Architecture Decision Records, API Reference, Sequence Diagrams by User Flow, Test Cases.

## Commands

```bash
docker compose up -d                    # postgres, two poolers, minio
cd backend && uv sync                   # uv installs Python 3.12 itself
uv run alembic upgrade head
uv run pytest                           # plumbing green, logic red
uv run pytest -m p0                     # integrity, money, isolation, privacy
uv run pytest -m security
uv run pytest -k TC_REC                 # one area by test case id
uv run uvicorn app.main:app --reload --port 8000
uv run procrastinate --app app.worker.app.app worker
uv run python -m tests.fixtures.generate   # the four failure-shape PDFs

cd frontend && npm install
npm run generate:client                 # regenerate from /openapi.json
npm run dev
```

`scripts/dev.ps1` and `scripts/dev.sh` wrap these. No task runner is required.

## Things that look like details and are not

- **The application connects as `ledger_app`, which owns nothing.** Row level security does not apply to a table's owner. Connecting as the migration role removes isolation while every test keeps passing. `tests/security/` asserts this directly.
- **`app.user_id` is set with `set_config(..., true)`, never a bare `SET`.** Under transaction pooling a session-level `SET` can persist on a pooled backend and be read by another user's request. A source scan test enforces this.
- **The worker connects directly to Postgres, the API through the pooler on 6432.** The queue uses LISTEN/NOTIFY, which a transaction-mode pooler does not carry. Through the pooler jobs still run, just late.
- **Port 6433 is a session-mode pooler that exists only as a test fixture.** Nothing in the application may point at it.
- **Presigned URLs are signed with `S3_PUBLIC_ENDPOINT`.** The signature covers the host, so a URL signed for an internal address cannot be repaired by rewriting it.
- **Two tables are not on the Notion schema page**: `rules` and `exports`. Both are required by specified endpoints and written test cases. Worth reconciling upstream.
- **The traceparent rides in the job's arguments**, not a bespoke column, because the queue library owns its schema.

## Build order

The plan lives in the code. Each unwritten function carries a `TODO:` list in
its docstring, and each module that owns a step carries a `BUILD STEP` header
giving its number, what it depends on, and the command that verifies it.

List them in order:

```bash
grep -rn "BUILD STEP" backend/app
```

The sequence is ordered so **no step depends on a later one**, and every step
ends at a runnable test. Start from the failing test, not the file.

| Step | Where | Depends on |
| --- | --- | --- |
| 0.1 Money conversion | `app/money.py` | nothing |
| 0.2 Descriptor normaliser | `app/classify/normalise.py` | nothing |
| 0.3 Transfer filter | `app/classify/transfer.py` | nothing |
| 0.4 Dedupe guard | `app/extract/dedupe.py` | nothing |
| 1.1 Reconciler | `app/extract/reconcile.py` | 0.1 |
| 2.1 Parser failures | `app/extract/pdfplumber_parser.py` | nothing |
| 2.2 Row extraction | `app/extract/*_parser.py` | 0.1, your fixture PDFs |
| 3.1 Statement repository | `app/db/repositories.py` | nothing |
| 3.2 Transaction repository | `app/db/repositories.py` | 0.4 |
| 4.1 Upload and register | `app/services/statement.py` | 3.1 |
| 4.2 Extract handler | `app/worker/tasks.py` | 1.1, 2.1, 3.1, 3.2 |
| 4.3 Row mutations | `app/services/statement.py` | 1.1, 3.2 |
| 4.4 Commit gate | `app/services/statement.py` | 1.1, 3.1 |
| 5.1 Merchant repository | `app/db/repositories.py` | 0.2 |
| 5.2 Alias writer | `app/classify/alias.py` | 5.1 |
| 5.3 Cascade rungs 1-3 | `app/classify/cascade.py` | 0.2, 0.3, 5.1, 5.2 |
| 5.4 Claude adapter | `app/classify/llm/claude.py` | nothing |
| 5.5 Rung 4 and handler | `app/classify/cascade.py`, `app/worker/tasks.py` | 5.3, 5.4 |
| 6.1 Review board | `app/services/review.py` | 5.5 |
| 6.2 Duplicates | `app/services/review.py` | 3.2 |
| 6.3 Classify and finish | `app/services/review.py` | 5.5, 6.1 |
| 7.1 Aggregate refresher | `app/aggregate/refresh.py` | 6.3 |
| 7.2 Dashboard | `app/services/dashboard.py` | 7.1 |
| 8.1 Rules preview and create | `app/services/rules.py` | 6.3 |
| 8.2 Rules reapply | `app/services/rules.py`, `app/worker/tasks.py` | 7.1, 8.1 |
| 9.1 Cards | `app/services/account.py` | 3.1 |
| 9.2 Categories | `app/services/account.py` | 6.3 |
| 9.3 Account and purge | `app/services/account.py`, `app/worker/tasks.py` | 7.1 |

Three milestones. After 1.1 every pure-logic P0 case passes. After 4.1 a PDF
travels the whole architecture and fails honestly at the unwritten parser, with
one trace id across the API and worker. After 4.4 a statement commits and the
navigation unlocks. After 7.2 the product works end to end.

Step 2.2 is blocked on redacted bank statements and **nothing depends on it**.
Later steps seed rows directly, so skip it if the files are not ready.

Deferred, and flagged in place rather than invented: manual row entry,
multi-currency, rule precedence past two overlapping patterns, password reset
delivery, and the OIDC provider.

## What the product is

Ledger is a bank statement classifier for Singapore. A user uploads statement PDFs, the system extracts transaction rows, checks them against the printed total, resolves each merchant, assigns categories, and shows spend by month, category and card. There is no bank connection and no sensitive card data. The only input is a PDF the user already has.

Classification is suggest-then-confirm. The user answers one question per new merchant, not per transaction, so each month needs less input than the last.

## Intended stack

- Backend: Python 3.12, FastAPI, SQLAlchemy 2.0 async.
- PDF extraction: `monopoly-core` (AGPL-3.0), with `pdfplumber` as the fallback parser.
- Database: PostgreSQL 16 with row-level security, `pg_trgm` for fuzzy matching.
- Queue: `procrastinate`, backed by the same Postgres instance.
- Frontend: React 18, Vite, TypeScript, client generated from the FastAPI OpenAPI schema.
- LLM: Claude behind a provider-agnostic adapter, prompt version pinned in config.
- Telemetry: structlog JSON to stdout, OpenTelemetry trace IDs, no hosted APM.
- Hosting: AWS Lightsail Containers, Singapore region. Fly.io or Railway is the documented fallback.

Ledger is licensed AGPL-3.0 because `monopoly-core` is AGPL and is imported as a library. Source must be offered to users of the hosted instance. The documented exit is dropping Monopoly for pdfplumber parsers, which is why that fallback path stays alive.

## Architecture

Three deployable pieces: a React SPA on a CDN, a FastAPI modular monolith, and a pool of processing workers. The API and workers share one Postgres. PDFs live in S3-compatible object storage and never pass through the API tier.

The upload path is deliberately shaped: the browser requests a presigned URL, PUTs the file straight to storage, then registers the object with the API. The API inserts the statement row and enqueues the extract job inside the same transaction, so a crash can never leave a pending statement with no job. The client then polls statement status. There is no synchronous processing path.

Worker processing runs in three separately resumable stages. Extract parses the PDF and reconciles. Classify normalises descriptors and resolves merchants. Aggregate refreshes precomputed monthly totals. Stages communicate only through the database and the job payload, so a classify failure retries without re-parsing the PDF.

### The four gates

These are the parts most likely to be implemented wrongly. Each is a hard stop, not a warning.

1. **Reconciler.** Extracted rows must sum to the printed total, or the user records an explicit gap with a reason. Committing an unreconciled statement returns 422 from the server. The disabled button in the UI is not the enforcement.
2. **Dedupe guard.** Three idempotency keys, all reachable in normal use: `file_sha256` for the same file uploaded twice, `dedupe_hash` for the same row appearing in two overlapping statements, and the job id for a worker crash mid-stage. Uniqueness is enforced by database constraints, not only by service code.
3. **Transfer filter.** Person-to-person transfers are excluded before the cascade resolver runs, not after. Ordering it after would satisfy the privacy rule on paper while still leaking a person's name to the LLM and to the logs.
4. **Redaction processor.** Log fields pass an allow-list at emit time. An unrecognised field is dropped rather than passed through.

### The classification cascade

Four rungs, cheapest first, mapping exactly to `transactions.classified_by`:

1. `override` - a user's own ruling for that descriptor.
2. `alias` - exact match in `merchant_aliases`.
3. `merchant_default` - `pg_trgm` fuzzy match above threshold.
4. `llm` - genuinely new descriptors only, batched 50 per call.

`merchant_aliases` is cross-tenant, so the cache warms once for everybody and the LLM bill tracks distinct new merchants rather than transaction volume. Only normalised descriptor strings cross the LLM boundary. Never amounts, never the PDF, never identity.

The descriptor normaliser is a pure function, fixture-tested, kept standalone so improved rules can re-run without re-parsing any PDF. It is what makes `MCDONALDS (CCP)` and `MCDONALDS-JUNCTION8` resolve to one merchant.

### Tenant isolation

Postgres row-level security keyed on `user_id`, applied per transaction. Services and repositories are written as if the system were single-tenant, and RLS makes that safe. There is no `user_id` path parameter anywhere in the API, by design.

This depends on one line: the database session dependency opens a transaction-mode connection and issues `SET LOCAL app.user_id`. Session-mode pooling would leak that variable between requests and turn isolation into a cross-tenant leak. A missing `WHERE user_id` clause under RLS returns zero rows, which is loud, rather than another tenant's rows, which is silent.

### Telemetry

Every request gets a `request_id`, returned as `X-Request-ID` and shown on error screens. A `trace_id` survives the async hop into the worker via a W3C `traceparent` stored on the job row. Without that column the trace breaks at the queue and you cannot tell where a statement is.

Never logged: raw merchant descriptors, amounts, totals, differences, card last-4, nicknames, PDF filenames and passwords, emails, names, session cookies, and presigned URLs. A logged presigned URL is a leaked file. Logged instead: a salted `descriptor_key_hash`, counts, booleans such as `reconciled` and `difference_nonzero`, parser name, duration, attempt.

## Invariants

Every one of these has at least one P0 test case in the Test Cases page. If an invariant has no failing-path test, it is not actually enforced.

- Nothing commits unreconciled. A recorded gap is carried in every view of that month.
- Dashboard totals stay locked while anything is unclassified.
- One statement per card per period.
- Reclassification moves money between categories and never changes a statement total.
- Manual overrides survive rule reruns unless explicitly discarded.
- Failed files are never partially imported.
- Removed duplicate rows leave the counted total but stay on the statement record, and removal is undoable.
- No bank connection, no full card numbers, no CVV, expiry or PIN.
- `has_statements` flips on first commit, not first upload. It gates the locked navigation on the empty-state screen.

## Data model

Core tables: `users`, `accounts`, `cards`, `statements`, `processing_jobs`, `transactions`, `merchants`, `merchant_aliases`, `merchant_overrides`, `categories`, `category_monthly_totals`, `audit_log`.

Points that carry real weight:

- Amounts are `amount_minor`, a signed integer in minor units. Never a float. Money in the API is a decimal string paired with a currency.
- `transactions.user_id` is denormalised so isolation and hot indexes need no join.
- `transactions.description_raw` is written once and never edited. `descriptor_key` is the normalised join key for the whole cascade.
- `category_id` null means genuinely unclassified, not "other".
- `categories.kind` is income, expense or transfer. Without it a credit card payment counts twice.
- `category_monthly_totals` is grained by user, card, category and month. Dashboard reads hit it directly and never aggregate per request.
- `audit_log.actor_user_id` deliberately carries no foreign key, so records survive account deletion.
- `cards.last4` is the only fragment of a card number stored anywhere.

## API shape

Base path `/api/v1`, JSON throughout, short-lived signed cookies over OIDC.

Status codes carry meaning here. 404 covers both not-found and owned-by-another-user. 409 is load-bearing and drives conflict-resolution screens for duplicate statements and rule collisions, with a structured payload the client renders directly. 422 means a valid shape but an invalid state transition, such as committing unreconciled or finishing review with money unassigned.

`/health` is liveness and must not touch the database. A probe failing during a brief database blip would trigger a restart loop that makes the outage worse. `/health/ready` does check dependencies.

Rule previews and reapply-to-history are separate endpoints from the operations themselves, because those screens preview before they run and a rerun can overwrite hand-made decisions.

## Screens

Twenty-seven screens numbered 00 to 08c. Auth 00 and 01, empty state 02, cards 02b to 02d, import 03 to 03d, review 04 to 05, dashboard 06 to 06c, rules 07 to 07e, account 08 to 08c. The User Flows page maps each to a flowchart, the API Reference maps each to endpoints, and the Sequence Diagrams page keys one-to-one to the flowcharts.

## Testing

Test IDs follow `TC-<AREA>-<NNN>` with areas AUTH, CARD, IMP, REC, FAIL, DUP, REV, TDUP, DASH, RULE, CAT, ACCT, TEL, SEC, RATE, PERF. P0 means data integrity, money correctness, tenant isolation or privacy.

Fixture PDFs are committed to the repo, one per supported bank plus the four failure shapes: encrypted, scanned, non-statement, malformed. Deterministic parsing is the reason these fixtures work as tests, so the same PDF must produce identical rows on every run.

Standing assumptions for suites: a seeded test user, a database reset between suites, a local S3-compatible stub, and a deterministic fake LLM adapter unless a case says otherwise.

## Deliberately omitted

Do not add these without a measurement that justifies them: microservices, Kafka or an event bus, sharding, multi-region, a vector store for merchant matching, a hosted APM, Redis at launch, a read replica at launch. Each was considered and rejected against a 10,000-user sizing model where peak load is roughly 0.05 uploads per second.

Embeddings for merchant resolution were rejected because the problem is formatting noise, not semantics, and because a wrong cosine score offers nothing to fix. Revisit only with a measured miss rate.

## Open questions

Carried forward from User Flows and Test Cases, unresolved: manual row entry has no drawn surface and no defined reconciliation behaviour when the user supplies the printed total; multi-currency conversion has no screen; rule precedence is defined for two overlapping patterns but not three or more; email verification and password reset have endpoints but no screens; restoring an archived card has no entry point in the wireframe.

## Working style

The mentorship framing is agentic engineering rather than incremental prompting. Hand the whole architecture over with a clear goal and let it scaffold to that design. The diagrams are the brief. Step back regularly and check for actual progress rather than the feeling of being close.
