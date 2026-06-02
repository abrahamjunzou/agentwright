"""Storage Service 2: TinyDB Document Store (Layer 0).

Schema-less per-agent scratchpad for flexible states, step tracking, tool
outputs, and pipeline traces. Persists to human-readable JSON files. TinyDB uses
file locking and tolerates only one writer, so the design routes all writes
through a single sequential async worker.

This module provides both the synchronous store operations and an optional
``WriteWorker`` that serializes writes through an ``asyncio.Queue`` (Operational
Commitment 1). Reads may happen directly.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from tinydb import Query, TinyDB

from .paths import runtime_root

# Standard tables from the design.
TABLES = ("states", "step_tracking", "tool_outputs", "pipeline_traces")


class DocumentStore:
    """TinyDB-backed document store, one database directory per agent."""

    def __init__(self, db_dir: str | Path | None = None) -> None:
        self._dir = Path(db_dir) if db_dir is not None else runtime_root() / "tinydb"
        self._dbs: dict[tuple[str, str], TinyDB] = {}

    def _db(self, agent_id: str, table: str) -> TinyDB:
        key = (agent_id, table)
        if key not in self._dbs:
            path = self._dir / agent_id
            path.mkdir(parents=True, exist_ok=True)
            self._dbs[key] = TinyDB(path / f"{table}.json")
        return self._dbs[key]

    def init_agent(self, agent_id: str) -> None:
        """Create empty table files for an agent."""
        for table in TABLES:
            self._db(agent_id, table)

    def insert(self, agent_id: str, table: str, doc: dict) -> int:
        """Insert a document; returns its doc_id."""
        return self._db(agent_id, table).insert(doc)

    def get(self, agent_id: str, table: str, doc_id: int) -> dict | None:
        """Fetch a document by id, or None."""
        result = self._db(agent_id, table).get(doc_id=doc_id)
        return dict(result) if result is not None else None

    def search(self, agent_id: str, table: str, field: str, value) -> list[dict]:
        """Return docs where ``field == value`` (a common-case query helper)."""
        q = Query()
        return [dict(d) for d in self._db(agent_id, table).search(q[field] == value)]

    def all(self, agent_id: str, table: str) -> list[dict]:
        """Return all documents in a table."""
        return [dict(d) for d in self._db(agent_id, table).all()]

    def update(self, agent_id: str, table: str, fields: dict, field: str, value) -> None:
        """Update ``fields`` on docs where ``field == value``."""
        q = Query()
        self._db(agent_id, table).update(fields, q[field] == value)

    def remove(self, agent_id: str, table: str, field: str, value) -> None:
        """Remove docs where ``field == value``."""
        q = Query()
        self._db(agent_id, table).remove(q[field] == value)

    def close(self) -> None:
        for db in self._dbs.values():
            db.close()
        self._dbs.clear()


class WriteWorker:
    """Serializes DocumentStore writes through a single async queue.

    Enforces Operational Commitment 1 (TinyDB has one writer). Enqueue write
    callables; the worker runs them one at a time.
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue = asyncio.Queue()
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        while True:
            op = await self._queue.get()
            try:
                op()  # synchronous TinyDB write
            finally:
                self._queue.task_done()

    async def submit(self, op) -> None:
        """Enqueue a zero-arg write callable and wait until it has been applied."""
        done = asyncio.get_event_loop().create_future()

        def wrapped():
            try:
                result = op()
                done.set_result(result)
            except Exception as exc:  # propagate to the caller
                done.set_exception(exc)

        await self._queue.put(wrapped)
        return await done

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
