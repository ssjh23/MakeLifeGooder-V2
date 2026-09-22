"""Enqueueing a job from inside the caller's own transaction.

``app/worker/app.py``'s ``App`` talks to Postgres on its own connection,
direct rather than through the pooler, because that is what LISTEN/NOTIFY
needs. Deferring a job through that ``App`` from the API would mean a
*second* connection and a *second* transaction -- and the entire argument in
ADR-004 is that the statement insert and the job insert commit or roll back
together, never one without the other (TC-IMP-005).

So this module does not go through the ``App`` at all. It calls
``procrastinate_defer_jobs_v1``, the SQL function procrastinate's own schema
installs and its client uses internally, directly through the caller's
``AsyncSession``. Same connection, same transaction, same commit as whatever
else that session is doing -- which for the import flow is the statement row
itself. The NOTIFY trigger the worker listens on fires from the table write
regardless of which client performed it, so a job deferred this way is picked
up exactly as promptly as one deferred through the ``App``.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from procrastinate.tasks import Task
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_DEFER_QUERY = text(
    "SELECT procrastinate_defer_jobs_v1("
    "ARRAY[ROW(:queue_name, :task_name, :priority, :lock, :queueing_lock, "
    "CAST(:args AS jsonb), :scheduled_at)::procrastinate_job_to_defer_v1]"
    ") AS job_ids"
)


async def enqueue(
    session: AsyncSession,
    task: Task[Any, Any, Any],
    *,
    lock: str | None = None,
    queueing_lock: str | None = None,
    priority: int = 0,
    scheduled_at: datetime | None = None,
    **kwargs: Any,
) -> int:
    """Defer ``task`` with ``kwargs``, in ``session``'s transaction.

    Returns the new job's id. Commits (or rolls back) whenever the caller's
    transaction does -- nothing here opens or closes one of its own.
    """
    result = await session.execute(
        _DEFER_QUERY,
        {
            "queue_name": task.queue,
            "task_name": task.name,
            "priority": priority,
            "lock": lock,
            "queueing_lock": queueing_lock,
            "args": json.dumps(kwargs),
            "scheduled_at": scheduled_at,
        },
    )
    (job_ids,) = result.one()
    job_id: int = job_ids[0]
    return job_id


__all__ = ["enqueue"]
