"""Service 9: Asyncio Priority Queue Scheduler (Layer 0).

A single in-process coroutine driving time-based triggers. Jobs live in an
``asyncio.PriorityQueue`` keyed by fire timestamp; when a job's time arrives the
scheduler publishes a ``trigger.fired`` event onto the EventBus. Cron jobs are
re-queued for their next occurrence. No systemd, no APScheduler, no HTTP server.

Job definitions are also kept in a plain dict so they can be listed/cancelled
and (in a full deployment) persisted to TinyDB for restart survival.
"""

from __future__ import annotations

import asyncio
import itertools
import time
from datetime import datetime, timezone

from croniter import croniter

from .event_bus import EventBus


def next_cron(cron_expr: str, base: float | None = None) -> float:
    """Return the next fire time (epoch seconds) for a cron expression.

    ``base`` defaults to now. Uses croniter, which supports standard 5-field
    cron including ranges, lists, and steps.
    """
    base_dt = datetime.fromtimestamp(base if base is not None else time.time(), tz=timezone.utc)
    return croniter(cron_expr, base_dt).get_next(float)


class Scheduler:
    """In-process cron/delayed-job scheduler that fires onto an EventBus."""

    def __init__(self, event_bus: EventBus) -> None:
        self._bus = event_bus
        self._queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self._jobs: dict[str, dict] = {}
        self._cancelled: set[str] = set()
        self._counter = itertools.count()  # tiebreaker so dicts are never compared
        self._worker: asyncio.Task | None = None

    def add_cron(
        self, job_id: str, cron: str, timezone_name: str, agent_id: str, task_template: str
    ) -> None:
        """Schedule a recurring cron job."""
        job = {
            "job_id": job_id,
            "agent_id": agent_id,
            "cron": cron,
            "timezone": timezone_name,
            "task_template": task_template,
        }
        self._jobs[job_id] = job
        self._cancelled.discard(job_id)
        self._enqueue(next_cron(cron), job)

    def add_delayed(
        self, job_id: str, delay_seconds: float, agent_id: str, task_template: str
    ) -> None:
        """Schedule a one-shot job ``delay_seconds`` from now."""
        job = {
            "job_id": job_id,
            "agent_id": agent_id,
            "task_template": task_template,
        }
        self._jobs[job_id] = job
        self._cancelled.discard(job_id)
        self._enqueue(time.time() + delay_seconds, job)

    def _enqueue(self, fire_at: float, job: dict) -> None:
        # (fire_at, tiebreak, job) — the counter prevents comparing dicts on ties.
        self._queue.put_nowait((fire_at, next(self._counter), job))

    def cancel(self, job_id: str) -> None:
        """Cancel a job. It stays in the heap but is skipped when it surfaces."""
        self._cancelled.add(job_id)
        self._jobs.pop(job_id, None)

    def list_jobs(self, agent_id: str | None = None) -> list[dict]:
        """Active (non-cancelled) jobs, optionally filtered by agent."""
        jobs = [j for jid, j in self._jobs.items() if jid not in self._cancelled]
        if agent_id is not None:
            jobs = [j for j in jobs if j["agent_id"] == agent_id]
        return jobs

    async def _fire_due(self) -> dict | None:
        """Pop the earliest job, wait until it is due, fire it, re-queue if cron.

        Returns the fired job (for testing), or None if the popped job was
        cancelled.
        """
        fire_at, _, job = await self._queue.get()
        delay = fire_at - time.time()
        if delay > 0:
            await asyncio.sleep(delay)
        if job["job_id"] in self._cancelled:
            return None
        await self._bus.publish("trigger.fired", dict(job))
        if "cron" in job:
            self._enqueue(next_cron(job["cron"]), job)
        return job

    async def _run(self) -> None:
        while True:
            await self._fire_due()

    def start(self) -> None:
        """Start the background worker coroutine (idempotent)."""
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run())

    async def stop(self) -> None:
        """Stop the background worker."""
        if self._worker is not None:
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass
            self._worker = None
