"""Instance Registry — persists finalized agent definitions (design 2.1/2.3).

Backed by SQLite (Python stdlib, zero daemon), matching the runtime
infrastructure layer's ``system.db`` ``agents`` table. Stores each
AgentDefinition as JSON and exposes create/get/list/update-status.

This is intentionally the only piece of real infrastructure built in this
layer: it is required to "persist finalized agent definitions; issue agent
IDs", needs no external service, and is fully testable offline (use
``InstanceRegistry(":memory:")`` in tests).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .agent_definition import AgentDefinition

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
    agent_id    TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    domain      TEXT,
    definition  TEXT NOT NULL,   -- JSON AgentDefinition
    status      TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class InstanceRegistry:
    """SQLite-backed store of AgentDefinitions."""

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        """Open (and initialize) the registry database.

        ``:memory:`` gives an ephemeral in-process DB for tests. A filesystem
        path is created with parent directories as needed.
        """
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False keeps this usable from asyncio executors later.
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def create(self, definition: AgentDefinition) -> str:
        """Persist a new definition. Returns its agent_id.

        Raises ValueError if the agent_id already exists, to avoid silently
        overwriting an existing agent.
        """
        now = _now_iso()
        try:
            self._conn.execute(
                "INSERT INTO agents (agent_id, name, domain, definition, status, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    definition.agent_id,
                    definition.name,
                    definition.domain,
                    definition.model_dump_json(),
                    definition.status,
                    now,
                    now,
                ),
            )
        except sqlite3.IntegrityError:
            raise ValueError(f"agent_id already registered: {definition.agent_id}") from None
        self._conn.commit()
        return definition.agent_id

    def get(self, agent_id: str) -> AgentDefinition | None:
        """Return the stored definition, or None if not found."""
        row = self._conn.execute(
            "SELECT definition FROM agents WHERE agent_id = ?", (agent_id,)
        ).fetchone()
        if row is None:
            return None
        return AgentDefinition.model_validate_json(row["definition"])

    def list(
        self, domain: str | None = None, status: str | None = None
    ) -> list[AgentDefinition]:
        """List stored definitions, optionally filtered by domain and/or status."""
        query = "SELECT definition FROM agents"
        clauses: list[str] = []
        params: list[str] = []
        if domain is not None:
            clauses.append("domain = ?")
            params.append(domain)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at"
        rows = self._conn.execute(query, params).fetchall()
        return [AgentDefinition.model_validate_json(r["definition"]) for r in rows]

    def update_status(self, agent_id: str, status: str) -> None:
        """Update an agent's lifecycle status (draft/validated/active/...).

        Updates both the ``status`` column and the status field inside the
        stored definition JSON so the two never diverge (``get`` reconstructs
        from the JSON). Raises KeyError if the agent does not exist.
        """
        definition = self.get(agent_id)
        if definition is None:
            raise KeyError(f"unknown agent_id: {agent_id}")
        definition.status = status
        self._conn.execute(
            "UPDATE agents SET status = ?, definition = ?, updated_at = ? WHERE agent_id = ?",
            (status, definition.model_dump_json(), _now_iso(), agent_id),
        )
        self._conn.commit()

    def close(self) -> None:
        """Close the underlying connection."""
        self._conn.close()
