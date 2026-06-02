"""Domain Brief — the intent-level input to the orchestration layer (design 2.2).

A brief describes *what* an agent should do and under which constraints, not
*how* it is built. The orchestration pipeline turns it into a concrete
AgentDefinition.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class BriefConstraints(BaseModel):
    """Operating constraints carried by the brief."""

    cost_per_run_usd_max: float = 2.0
    requires_human_approval_for: list[str] = Field(default_factory=list)  # action patterns
    pii_handling: Literal["allow", "redact", "deny"] = "redact"
    data_retention_days: int = 90


class DomainBrief(BaseModel):
    """Full domain brief schema (design 2.2)."""

    name: str
    goal: str
    domain: str
    constraints: BriefConstraints = Field(default_factory=BriefConstraints)
    available_connections: list[str] = Field(default_factory=list)
    available_compute: list[Literal["shell", "browser", "vm"]] = Field(default_factory=list)
    triggers_needed: list[
        Literal["schedule", "webhook", "email", "slack", "event", "manual"]
    ] = Field(default_factory=list)
    ui_output_needed: bool = False
    long_lived: bool = False  # needs persistent identity/memory across runs
    sub_agent: bool = False  # spawned by a parent agent, not a user

    # Custom primitives (design "Extension: Custom Primitives"). Names of
    # registered custom templates to include beyond the nine built-ins, plus an
    # optional per-primitive config override (field -> value) passed to that
    # template's config_model. A name here must already be in PRIMITIVE_REGISTRY.
    extra_primitives: list[str] = Field(default_factory=list)
    custom_config: dict[str, dict[str, Any]] = Field(default_factory=dict)
