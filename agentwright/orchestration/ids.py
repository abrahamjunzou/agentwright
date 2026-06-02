"""Agent ID generation.

The design uses ``agt_...`` style identifiers. We keep it simple and dependency
-free: ``agt_`` followed by a uuid4 hex. Stable, unique, and sortable enough for
this layer (the runtime infrastructure layer can swap in ULIDs later).
"""

from __future__ import annotations

from uuid import uuid4


def new_agent_id() -> str:
    """Return a fresh agent id like ``agt_3f9c...``."""
    return "agt_" + uuid4().hex
