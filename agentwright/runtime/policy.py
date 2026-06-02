"""Permission policy evaluation (Layer 0 runtime side of the permission primitive).

Implements the permission primitive's ``check(action)`` runtime contract: given a
``PermissionConfig`` and an action string (e.g. ``"crm.delete_contact"``), decide
whether it is allowed, denied, or needs human approval. Override patterns are
matched with glob semantics; the first matching override wins, else the policy
default applies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fnmatch import fnmatch

from ..primitives.permission import PermissionConfig


@dataclass
class PolicyDecision:
    """Outcome of a permission check."""

    allowed: bool
    requires_approval: bool
    reason: str
    approvers: list[str] = field(default_factory=list)


def evaluate(permission: PermissionConfig, action: str) -> PolicyDecision:
    """Return the policy decision for ``action`` under ``permission``."""
    policy = permission.action_policy
    effective = policy.default
    approvers: list[str] = []
    matched: str | None = None

    for override in policy.overrides:
        if fnmatch(action, override.action_pattern):
            effective = override.policy
            approvers = override.approvers
            matched = override.action_pattern
            break

    where = f"override '{matched}'" if matched else "default policy"
    if effective == "allow":
        return PolicyDecision(True, False, f"allowed by {where}")
    if effective == "deny":
        return PolicyDecision(False, False, f"denied by {where}")
    # require_approval
    return PolicyDecision(False, True, f"approval required by {where}", approvers)
