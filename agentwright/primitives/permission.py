"""Primitive 8: Permission — what the agent may do and who authorizes risk.

Owner scoping, action policy (allow/deny/require_approval with overrides),
data policy (PII, retention), and cost policy. Depends on identity. Required by
tool_connection and compute, since those perform external actions.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .base import PrimitiveTemplate


class OwnerConfig(BaseModel):
    """Who owns the agent and which teams it belongs to."""

    user_id: str
    org_id: str | None = None
    team_ids: list[str] = Field(default_factory=list)


class ActionOverride(BaseModel):
    """A per-action-pattern policy override (glob or exact match)."""

    action_pattern: str
    policy: Literal["allow", "deny", "require_approval"]
    approvers: list[str] = Field(default_factory=list)


class ActionPolicy(BaseModel):
    """Default action policy plus overrides. Default is conservative."""

    default: Literal["allow", "deny", "require_approval"] = "require_approval"
    overrides: list[ActionOverride] = Field(default_factory=list)


class DataPolicy(BaseModel):
    """What data the agent may read/write and how PII is handled."""

    read_scopes: list[str] = Field(default_factory=list)
    write_scopes: list[str] = Field(default_factory=list)
    pii_handling: Literal["allow", "redact", "deny"] = "redact"
    data_retention_days: int = 90


class CostPolicy(BaseModel):
    """Spend caps. max_per_run_spend_usd bounds the reasoning loop (invariant 8)."""

    max_daily_spend_usd: float = 20.0
    max_per_run_spend_usd: float = 2.0
    alert_user_at_usd: float = 1.0


class PermissionConfig(BaseModel):
    """Full config schema for the permission primitive (design Primitive 8)."""

    owner: OwnerConfig
    action_policy: ActionPolicy = Field(default_factory=ActionPolicy)
    data_policy: DataPolicy = Field(default_factory=DataPolicy)
    cost_policy: CostPolicy = Field(default_factory=CostPolicy)


TEMPLATE = PrimitiveTemplate(
    name="permission",
    version="1.0",
    config_model=PermissionConfig,
    dependencies=("identity",),
    runtime_contract=(
        "check(action, context): PolicyDecision",
        "PolicyDecision.allowed: bool",
        "PolicyDecision.requires_approval: bool",
        "PolicyDecision.approvers: list | null",
        "PolicyDecision.reason: string",
        "request_approval(action, context): ApprovalResult",
        "log_action(action, outcome, metadata)",
    ),
)
