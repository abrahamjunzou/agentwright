"""Storage Service 7: LMDB Bare-Metal KV (Layer 0).

High-velocity exact-key store for tool traces, run state, and processed-id
deduplication. LMDB is memory-mapped, atomic, and crash-safe via MVCC.

Constraints enforced here:
- ``map_size`` is fixed at open time (512 MB default; 2 GB for computer-use).
- One writer at a time per environment — writes should route through a single
  sequential worker per agent (Operational Commitment 2). Reads are concurrent
  and lock-free.
"""

from __future__ import annotations

from pathlib import Path

import lmdb

from .paths import runtime_root

_MB = 1024 * 1024


class KVStore:
    """One LMDB environment per agent, opened lazily with a fixed map size."""

    def __init__(self, db_dir: str | Path | None = None, default_map_size_mb: int = 512) -> None:
        self._dir = Path(db_dir) if db_dir is not None else runtime_root() / "lmdb"
        self._default_map_size_mb = default_map_size_mb
        self._envs: dict[str, lmdb.Environment] = {}

    def open_agent(self, agent_id: str, map_size_mb: int | None = None) -> None:
        """Open (or create) an agent's environment. map_size is fixed here."""
        if agent_id in self._envs:
            return
        self._dir.mkdir(parents=True, exist_ok=True)
        size = (map_size_mb or self._default_map_size_mb) * _MB
        # subdir=False -> a single ``{agent_id}.lmdb`` file as in the design.
        self._envs[agent_id] = lmdb.open(
            str(self._dir / f"{agent_id}.lmdb"), map_size=size, subdir=False
        )

    def _env(self, agent_id: str) -> lmdb.Environment:
        if agent_id not in self._envs:
            self.open_agent(agent_id)
        return self._envs[agent_id]

    def put(self, agent_id: str, key: str, value: bytes) -> None:
        """Write a key (sequential-worker path in a concurrent deployment)."""
        with self._env(agent_id).begin(write=True) as txn:
            txn.put(key.encode(), value)

    def get(self, agent_id: str, key: str) -> bytes | None:
        """Read a key (concurrent, non-blocking)."""
        with self._env(agent_id).begin() as txn:
            return txn.get(key.encode())

    def exists(self, agent_id: str, key: str) -> bool:
        with self._env(agent_id).begin() as txn:
            return txn.get(key.encode()) is not None

    def delete(self, agent_id: str, key: str) -> None:
        with self._env(agent_id).begin(write=True) as txn:
            txn.delete(key.encode())

    def scan_prefix(self, agent_id: str, prefix: str) -> list[tuple[str, bytes]]:
        """Return (key, value) pairs whose key starts with ``prefix``."""
        out: list[tuple[str, bytes]] = []
        pb = prefix.encode()
        with self._env(agent_id).begin() as txn:
            cursor = txn.cursor()
            if cursor.set_range(pb):
                for key, value in cursor:
                    if not key.startswith(pb):
                        break
                    out.append((key.decode(), value))
        return out

    def count_prefix(self, agent_id: str, prefix: str) -> int:
        """Count keys with the given prefix."""
        return len(self.scan_prefix(agent_id, prefix))

    def close(self) -> None:
        for env in self._envs.values():
            env.close()
        self._envs.clear()
