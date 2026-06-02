"""Runtime data-directory resolution (Layer 0).

The design pins all runtime data under ``~/.local/share/agent-runtime/``. We
keep that default but allow an override via the ``AGENT_RUNTIME_HOME``
environment variable so tests (and portable bundles) can point the whole stack
at a temporary directory. Every service takes its root from here, so a single
env var relocates the entire runtime.
"""

from __future__ import annotations

import os
from pathlib import Path

_ENV_VAR = "AGENT_RUNTIME_HOME"
_DEFAULT = Path.home() / ".local" / "share" / "agent-runtime"


def runtime_root() -> Path:
    """Return the runtime data root, honoring ``AGENT_RUNTIME_HOME`` if set."""
    override = os.environ.get(_ENV_VAR)
    return Path(override).expanduser() if override else _DEFAULT


def ensure_dir(path: Path) -> Path:
    """Create ``path`` (and parents) if missing and return it."""
    path.mkdir(parents=True, exist_ok=True)
    return path
