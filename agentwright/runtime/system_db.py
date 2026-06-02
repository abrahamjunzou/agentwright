"""Storage Service 1: SQLite system database (Layer 0).

Holds system-level metadata only — agent registry, run index, cost ledger, and
action audit — exactly the four tables in the design's ``system.db``. Per-agent
data lives in the specialized stores, not here.

SQLite is Python stdlib (zero install, zero daemon). This module is the single
writer/reader for system.db; the Layer-2 ``InstanceRegistry`` persists the
agents table through the same schema (``CREATE TABLE IF NOT EXISTS`` keeps the
two compatible when they share a file).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
    agent_id    TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    domain      TEXT,
    definition  TEXT NOT NULL,
    status      TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    run_id      TEXT PRIMARY KEY,
    agent_id    TEXT NOT NULL,
    trigger_id  TEXT,
    status      TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    ended_at    TEXT,
    stop_reason TEXT,
    cost_usd    REAL,
    tokens_used INTEGER
);
CREATE TABLE IF NOT EXISTS cost_ledger (
    entry_id      TEXT PRIMARY KEY,
    agent_id      TEXT NOT NULL,
    run_id        TEXT NOT NULL,
    provider      TEXT NOT NULL,
    model_id      TEXT NOT NULL,
    input_tokens  INTEGER,
    output_tokens INTEGER,
    cost_usd      REAL,
    recorded_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS action_audit (
    audit_id    TEXT PRIMARY KEY,
    agent_id    TEXT NOT NULL,
    run_id      TEXT NOT NULL,
    tool_name   TEXT NOT NULL,
    inputs_json TEXT,
    outcome     TEXT NOT NULL,
    cost_usd    REAL,
    executed_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SystemDB:
    """The system metadata database: runs, cost ledger, and action audit."""

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        """Open and initialize system.db. ``:memory:`` for ephemeral/test use."""
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # --- runs ------------------------------------------------------------

    def start_run(self, agent_id: str, trigger_id: str | None = None) -> str:
        """Record a run as started (status=running) and return its run_id."""
        run_id = "run_" + uuid4().hex
        self._conn.execute(
            "INSERT INTO runs (run_id, agent_id, trigger_id, status, started_at) "
            "VALUES (?, ?, ?, 'running', ?)",
            (run_id, agent_id, trigger_id, _now()),
        )
        self._conn.commit()
        return run_id

    def end_run(
        self,
        run_id: str,
        status: str,
        stop_reason: str | None = None,
        cost_usd: float | None = None,
        tokens_used: int | None = None,
    ) -> None:
        """Finalize a run with its outcome. Raises KeyError if unknown."""
        cur = self._conn.execute(
            "UPDATE runs SET status=?, ended_at=?, stop_reason=?, cost_usd=?, "
            "tokens_used=? WHERE run_id=?",
            (status, _now(), stop_reason, cost_usd, tokens_used, run_id),
        )
        if cur.rowcount == 0:
            raise KeyError(f"unknown run_id: {run_id}")
        self._conn.commit()

    def get_run(self, run_id: str) -> dict | None:
        """Return a run row as a dict, or None."""
        row = self._conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        return dict(row) if row else None

    def list_runs(self, agent_id: str) -> list[dict]:
        """All runs for an agent, newest first."""
        rows = self._conn.execute(
            "SELECT * FROM runs WHERE agent_id=? ORDER BY started_at DESC", (agent_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    # --- cost ledger -----------------------------------------------------

    def record_cost(
        self,
        agent_id: str,
        run_id: str,
        provider: str,
        model_id: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
    ) -> str:
        """Append one LLM-call cost entry. Returns the entry_id."""
        entry_id = "cost_" + uuid4().hex
        self._conn.execute(
            "INSERT INTO cost_ledger (entry_id, agent_id, run_id, provider, model_id, "
            "input_tokens, output_tokens, cost_usd, recorded_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                entry_id,
                agent_id,
                run_id,
                provider,
                model_id,
                input_tokens,
                output_tokens,
                cost_usd,
                _now(),
            ),
        )
        self._conn.commit()
        return entry_id

    def run_cost(self, run_id: str) -> float:
        """Total recorded cost for a run (0.0 if none)."""
        row = self._conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0.0) AS total FROM cost_ledger WHERE run_id=?",
            (run_id,),
        ).fetchone()
        return float(row["total"])

    # --- action audit ----------------------------------------------------

    def record_action(
        self,
        agent_id: str,
        run_id: str,
        tool_name: str,
        inputs_json: str,
        outcome: str,
        cost_usd: float | None = None,
    ) -> str:
        """Append one tool-execution audit row. Returns the audit_id."""
        audit_id = "aud_" + uuid4().hex
        self._conn.execute(
            "INSERT INTO action_audit (audit_id, agent_id, run_id, tool_name, inputs_json, "
            "outcome, cost_usd, executed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (audit_id, agent_id, run_id, tool_name, inputs_json, outcome, cost_usd, _now()),
        )
        self._conn.commit()
        return audit_id

    def list_actions(self, run_id: str) -> list[dict]:
        """All audited actions for a run, in execution order."""
        rows = self._conn.execute(
            "SELECT * FROM action_audit WHERE run_id=? ORDER BY executed_at", (run_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        self._conn.close()
