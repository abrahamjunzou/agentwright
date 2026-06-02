"""Service 12: File Store (Layer 0).

Local-filesystem store for an agent's history transcripts, summaries, artifacts,
uploads, observability traces, and scratch workspace. All paths are scoped under
``files/agents/{agent_id}/`` and confined to that subtree — a traversal-safe
join rejects paths that escape the agent's directory.
"""

from __future__ import annotations

from pathlib import Path

from .paths import runtime_root

# The standard per-agent subdirectories from the design.
AGENT_SUBDIRS = ("history", "summaries", "artifacts", "uploads", "traces", "workspace")


class FileStore:
    """Filesystem-backed per-agent file store."""

    def __init__(self, root: str | Path | None = None) -> None:
        """Root defaults to ``<runtime_root>/files``."""
        self._root = Path(root) if root is not None else runtime_root() / "files"

    def _agent_dir(self, agent_id: str) -> Path:
        return self._root / "agents" / agent_id

    def init_agent(self, agent_id: str) -> None:
        """Create the standard subdirectories for an agent."""
        base = self._agent_dir(agent_id)
        for sub in AGENT_SUBDIRS:
            (base / sub).mkdir(parents=True, exist_ok=True)

    def _resolve(self, agent_id: str, path: str) -> Path:
        """Join ``path`` under the agent dir, rejecting escapes via ``..``."""
        base = self._agent_dir(agent_id).resolve()
        target = (base / path).resolve()
        if base != target and base not in target.parents:
            raise ValueError(f"path escapes agent directory: {path!r}")
        return target

    def write(self, agent_id: str, path: str, content: str) -> None:
        """Write text content, creating parent directories as needed."""
        target = self._resolve(agent_id, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    def read(self, agent_id: str, path: str) -> str:
        """Read text content. Raises FileNotFoundError if absent."""
        return self._resolve(agent_id, path).read_text()

    def list(self, agent_id: str, prefix: str = "") -> list[str]:
        """List files (relative paths) under ``prefix`` within the agent dir."""
        base = self._agent_dir(agent_id)
        start = self._resolve(agent_id, prefix) if prefix else base
        if not start.exists():
            return []
        return sorted(
            str(p.relative_to(base)) for p in start.rglob("*") if p.is_file()
        )

    def exists(self, agent_id: str, path: str) -> bool:
        return self._resolve(agent_id, path).exists()

    def delete(self, agent_id: str, path: str) -> None:
        """Delete a file if it exists (no error if already gone)."""
        target = self._resolve(agent_id, path)
        target.unlink(missing_ok=True)
