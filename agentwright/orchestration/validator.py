"""Composition Validator — enforces the design's invariants (design 2.3 + the
"Composition Invariants" section), plus the runtime infrastructure layer's
documented gaps.

Returns a :class:`ValidationResult` (errors + warnings). Errors make the
definition invalid; warnings are advisory. :func:`apply` writes the outcome
onto an AgentDefinition and sets its status.

Invariants 1-12 from the design are implemented in order:
- 1-10: the design's original composition invariants.
- 11: email/slack triggers need a tool_connection reaching the channel. -> error
- 12: compute.vm.enabled is rejected (no VM service exists).            -> error

Plus one non-fatal infrastructure constraint:
- observability.export to a non-local destination is unbacked.         -> warning
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fnmatch import fnmatch

from ..primitives.registry import missing_dependencies
from .agent_definition import AgentDefinition


@dataclass
class ValidationResult:
    """Outcome of validating one AgentDefinition."""

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when there are no errors (warnings are allowed)."""
        return not self.errors


def validate(definition: AgentDefinition, sub_agent: bool = False) -> ValidationResult:
    """Validate ``definition``. ``sub_agent`` comes from the originating brief
    (invariant 10) and is not stored on the definition itself."""
    result = ValidationResult()
    prims = definition.primitives
    present = prims.present_names()

    # Dependency completeness (design 2.3: "all dependency chains are satisfied").
    for name, deps in missing_dependencies(present).items():
        result.errors.append(
            f"primitive '{name}' requires {deps}, which are not in the composition"
        )

    # Invariants 1-4: the four always-required primitives. PrimitiveSet makes
    # them non-optional, so this is a belt-and-braces check.
    for required in ("identity", "reasoning_loop", "permission", "observability"):
        if required not in present:
            result.errors.append(f"invariant: '{required}' must always be present")

    # Invariant 5: every approval-gated tool must be covered by a permission policy.
    if prims.tool_connection is not None and prims.permission is not None:
        _check_tool_approval_policies(prims, result)

    # Invariant 6 is subsumed by invariant 2 (reasoning_loop always present).

    # Invariant 7: structured state requires the universal (SurrealDB) store.
    if prims.memory is not None:
        ss = prims.memory.structured_state
        if ss.enabled and not prims.memory.long_term.universal_store:
            result.errors.append(
                "invariant 7: memory.structured_state.enabled requires "
                "memory.long_term.universal_store = true"
            )

    # Invariant 8: per-run reasoning budget must fit within the permission cap.
    # Guarded: a missing required primitive is already reported above; skip the
    # numeric check rather than dereference None.
    if prims.reasoning_loop is not None and prims.permission is not None:
        run_cap = prims.reasoning_loop.cost_controls.max_cost_per_run_usd
        spend_cap = prims.permission.cost_policy.max_per_run_spend_usd
        if run_cap > spend_cap:
            result.errors.append(
                f"invariant 8: reasoning_loop.max_cost_per_run_usd ({run_cap}) exceeds "
                f"permission.cost_policy.max_per_run_spend_usd ({spend_cap})"
            )

    # Invariant 9: browser compute without browser-action capture -> warn.
    if (
        prims.compute is not None
        and prims.compute.browser.enabled
        and prims.observability is not None
    ):
        if not prims.observability.capture.browser_actions:
            result.warnings.append(
                "invariant 9: compute.browser.enabled but "
                "observability.capture.browser_actions is false"
            )

    # Invariant 10: a sub-agent must not define its own trigger.
    if sub_agent and prims.trigger is not None and prims.trigger.triggers:
        result.errors.append(
            "invariant 10: a sub_agent must not define triggers (it is activated "
            "by a parent, not a schedule)"
        )

    # Invariant 11: email/slack triggers need a tool_connection that reaches the
    # channel — the scheduler does not poll inboxes/channels directly.
    if prims.trigger is not None:
        _check_channel_trigger_connections(prims, result)

    _check_infrastructure_gaps(definition, result)
    return result


def _check_channel_trigger_connections(prims, result: ValidationResult) -> None:
    """Invariant 11: each email/slack trigger must be backed by a connection that
    provides access to that channel (gmail/email for email, slack for slack).

    Promoted from a warning to an error: an unbacked channel trigger passes every
    other invariant and then fails silently at runtime when it tries to reach a
    channel with no connection (dryrun Issue 006, High severity)."""
    conn_ids = (
        {c.id for c in prims.tool_connection.connections}
        if prims.tool_connection is not None
        else set()
    )
    for t in prims.trigger.triggers:
        if t.type == "email" and not any("gmail" in c or "email" in c for c in conn_ids):
            result.errors.append(
                f"invariant 11: email trigger '{t.id}' requires a gmail/email "
                f"connection in tool_connection to poll through"
            )
        if t.type == "slack" and not any("slack" in c for c in conn_ids):
            result.errors.append(
                f"invariant 11: slack trigger '{t.id}' requires a slack connection "
                f"in tool_connection to poll through"
            )


def _check_tool_approval_policies(prims, result: ValidationResult) -> None:
    """Invariant 5: each tool with requires_approval=true must be covered by a
    require_approval policy — either a matching override or a deny-by-default
    action policy."""
    policy = prims.permission.action_policy
    default_gates = policy.default in ("require_approval", "deny")
    approval_patterns = [
        o.action_pattern for o in policy.overrides if o.policy in ("require_approval", "deny")
    ]
    for conn in prims.tool_connection.connections:
        for tool in conn.tools:
            if not tool.requires_approval:
                continue
            covered = default_gates or any(
                fnmatch(tool.name, pat) or fnmatch(f"{conn.id}.{tool.name}", pat)
                for pat in approval_patterns
            )
            if not covered:
                result.errors.append(
                    f"invariant 5: tool '{conn.id}.{tool.name}' requires approval but no "
                    f"permission policy gates it"
                )


def _check_infrastructure_gaps(definition: AgentDefinition, result: ValidationResult) -> None:
    """Constraints imposed by the runtime infrastructure layer's current limits."""
    prims = definition.primitives

    # Invariant 12: VM compute has no backing service -> reject.
    if prims.compute is not None and prims.compute.vm.enabled:
        result.errors.append(
            "invariant 12: compute.vm.enabled must be false — VM execution is not "
            "backed by the current runtime infrastructure layer (no VM service)"
        )

    # Only local trace export is backed -> warn for anything else.
    if prims.observability is not None and prims.observability.export.enabled and (
        prims.observability.export.destination != "local"
    ):
        export = prims.observability.export
        result.warnings.append(
            f"infrastructure: observability.export.destination '{export.destination}' is "
            f"not backed yet; only 'local' is implemented"
        )

    # (email/slack channel-connection checks are enforced as Invariant 11 above.)


def apply(definition: AgentDefinition, result: ValidationResult) -> AgentDefinition:
    """Write the validation outcome onto ``definition`` and set its status to
    ``validated`` (no errors) or leave it ``draft`` (errors present)."""
    definition.validation_errors = list(result.errors)
    definition.validation_warnings = list(result.warnings)
    definition.status = "validated" if result.ok else "draft"
    return definition
