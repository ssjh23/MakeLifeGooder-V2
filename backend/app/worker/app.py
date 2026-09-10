"""procrastinate application.

The queue lives in the same Postgres as the data. That is what makes enqueueing
a statement's extract job part of the same transaction that inserts the
statement row: a crash between the two is not something to be handled, it is
unrepresentable (ADR-004, TC-IMP-005). With a separate broker that would be a
dual write, and the failure would be a statement stuck in ``pending`` with no
job and nothing to notice it.

The worker connects **directly** to Postgres, not through the transaction-mode
pooler. procrastinate waits on LISTEN/NOTIFY to pick work up the moment it is
enqueued, and a transaction-mode pooler releases the backend at COMMIT, so a
listener registered through it is not reachable. Nothing breaks loudly: the
worker falls back to periodic polling and jobs run late. "The queue feels slow"
is a bad week of debugging, so the connection is separated here on purpose.
"""

from __future__ import annotations

from procrastinate import App, PsycopgConnector

from app.config import get_settings


def _dsn() -> str:
    """Direct DSN for the worker, with the async driver prefix stripped.

    procrastinate manages its own connections, so it wants a plain libpq DSN.
    """
    settings = get_settings()
    return str(settings.database_worker_url).replace("postgresql+asyncpg://", "postgresql://")


app = App(connector=PsycopgConnector(conninfo=_dsn()))

# Imported for the side effect of registering the tasks. Without this the
# worker starts cleanly and then claims no jobs, which looks like a queue
# problem rather than a missing import.
from app.worker import tasks  # noqa: E402,F401
