"""Primitive 1: Identity — the agent's persistent anchor.

Holds the agent's name, standing instructions, owner, and labels. It is the
root primitive: every other primitive depends on it, and it has no
dependencies of its own.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field

from .base import PrimitiveTemplate


def _now() -> datetime:
    """Current UTC time; used as the default for created/updated stamps."""
    return datetime.now(timezone.utc)


class IdentityConfig(BaseModel):
    """Config schema for the identity primitive (design Primitive 1)."""

    name: str  # human-readable agent name
    instructions: str  # canonical system prompt / standing orders
    owner_id: str  # user or team that owns this agent
    description: str = ""  # purpose / role
    tags: list[str] = Field(default_factory=list)  # optional labels
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


TEMPLATE = PrimitiveTemplate(
    name="identity",
    version="1.0",
    config_model=IdentityConfig,
    dependencies=(),  # root primitive — no dependencies
    runtime_contract=(
        "agent_id: uuid",
        "resolved_instructions: string",
        "run_count: int",
        "last_run_at: timestamp",
    ),
)
