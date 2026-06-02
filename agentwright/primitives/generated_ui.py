"""Primitive 7: Generated UI — interactive artifacts and approval flows.

Dashboards, forms, review screens, charts, plus human approval flows. Depends
on identity only.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .base import PrimitiveTemplate


class ApprovalFlows(BaseModel):
    """Human-in-the-loop approval behaviour."""

    enabled: bool = False
    default_timeout_seconds: int = 3600
    timeout_action: Literal["auto_proceed", "auto_cancel", "escalate"] = "escalate"


class Embedding(BaseModel):
    """How rendered artifacts are surfaced and what interactions are allowed."""

    mode: Literal["inline", "hosted_url", "iframe"] = "inline"
    allowed_interactions: list[Literal["view", "edit", "submit", "approve", "reject"]] = Field(
        default_factory=lambda: ["view"]
    )


class GeneratedUIConfig(BaseModel):
    """Full config schema for the generated_ui primitive (design Primitive 7)."""

    output_formats: list[
        Literal["markdown", "html", "react", "json_schema_form", "table", "chart"]
    ] = Field(default_factory=lambda: ["markdown"])
    approval_flows: ApprovalFlows = Field(default_factory=ApprovalFlows)
    embedding: Embedding = Field(default_factory=Embedding)


TEMPLATE = PrimitiveTemplate(
    name="generated_ui",
    version="1.0",
    config_model=GeneratedUIConfig,
    dependencies=("identity",),
    runtime_contract=(
        "render(spec): UIArtifact",
        "request_approval(artifact, prompt): ApprovalResult",
        "ApprovalResult.decision: enum[approved, rejected, timed_out]",
        "ApprovalResult.reviewer_id: string | null",
        "ApprovalResult.notes: string | null",
        "ApprovalResult.decided_at: timestamp",
    ),
)
