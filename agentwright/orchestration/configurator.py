"""Instance Configurator — fills each selected primitive's config (design 2.3).

Takes a domain brief plus the selected primitive set and produces an
AgentDefinition with every primitive configured from the brief and sensible
defaults. Where a required value cannot be derived from the brief (credential
references, approver ids, cron expressions, monitored addresses, ...), the
configurator inserts a safe placeholder and records the field in
``human_input_required`` so a human (or a later step) can fill it in.

Determinism note: values the design's example fills via model judgement — e.g.
enabling structured-state dedup, or choosing a permissive default action policy
— are NOT inferred here. This configurator is rule-based and conservative; such
inferences belong to the optional LLM selector/configurator extension.
"""

from __future__ import annotations

from ..primitives.compute import (
    BrowserConfig,
    ComputeConfig,
    ShellConfig,
    VmConfig,
)
from ..primitives.generated_ui import GeneratedUIConfig
from ..primitives.identity import IdentityConfig
from ..primitives.memory import MemoryConfig
from ..primitives.observability import CaptureConfig, ObservabilityConfig
from ..primitives.permission import (
    ActionOverride,
    ActionPolicy,
    CostPolicy,
    DataPolicy,
    OwnerConfig,
    PermissionConfig,
)
from ..primitives.reasoning_loop import CostControlsConfig, ReasoningLoopConfig
from ..primitives.registry import PRIMITIVE_REGISTRY
from ..primitives.tool_connection import AuthConfig, Connection, ToolConnectionConfig
from ..primitives.trigger import (
    EmailSpec,
    ScheduleSpec,
    SlackSpec,
    TaskInjection,
    TriggerConfig,
    TriggerDef,
    WebhookSpec,
)
from .agent_definition import _BUILTIN_NAMES, AgentDefinition, PrimitiveSet
from .domain_brief import DomainBrief


def configure(
    brief: DomainBrief,
    selected: set[str],
    agent_id: str,
    owner_id: str,
    created_by: str,
) -> AgentDefinition:
    """Build a draft AgentDefinition from ``brief`` and the ``selected`` set."""
    pending: list[str] = []  # fields that still need a human value

    identity = IdentityConfig(
        name=brief.name,
        description=brief.goal,
        instructions=brief.goal,
        owner_id=owner_id,
        tags=[brief.domain] if brief.domain else [],
    )

    # Reasoning loop: cap per-run cost at the brief's budget.
    reasoning_loop = ReasoningLoopConfig(
        cost_controls=CostControlsConfig(
            max_cost_per_run_usd=brief.constraints.cost_per_run_usd_max
        )
    )

    permission = _build_permission(brief, owner_id, pending)
    observability = _build_observability(brief)

    memory = _build_memory(brief) if "memory" in selected else None
    tool_connection = (
        _build_tool_connection(brief, pending) if "tool_connection" in selected else None
    )
    compute = _build_compute(brief) if "compute" in selected else None
    trigger = _build_trigger(brief, pending) if "trigger" in selected else None
    generated_ui = GeneratedUIConfig() if "generated_ui" in selected else None
    custom = _build_custom(brief, selected)

    primitives = PrimitiveSet(
        identity=identity,
        reasoning_loop=reasoning_loop,
        permission=permission,
        observability=observability,
        memory=memory,
        tool_connection=tool_connection,
        compute=compute,
        trigger=trigger,
        generated_ui=generated_ui,
        custom=custom,
    )

    # Pin the version of each primitive in force at instantiation (design Versioning).
    versions = {name: PRIMITIVE_REGISTRY[name].version for name in primitives.present_names()}

    return AgentDefinition(
        agent_id=agent_id,
        name=brief.name,
        domain=brief.domain,
        created_by=created_by,
        primitives=primitives,
        primitive_versions=versions,
        status="draft",
        human_input_required=pending,
    )


def _build_custom(brief: DomainBrief, selected: set[str]) -> dict:
    """Configure every selected primitive that is not one of the nine built-ins
    (design "Extension: Custom Primitives").

    Each such name must be a registered template; its config is built from the
    template's own ``config_model`` using any overrides the brief supplies in
    ``custom_config`` and that model's defaults otherwise. This is what makes a
    custom primitive first-class — it is selected, configured, version-pinned,
    and validated through the same pipeline as the built-ins, with no per-type
    code here. The registry is the single source of truth.
    """
    custom: dict = {}
    for name in sorted(selected - set(_BUILTIN_NAMES)):
        template = PRIMITIVE_REGISTRY.get(name)
        if template is None:
            raise KeyError(
                f"brief requests custom primitive '{name}', which is not "
                f"registered; call register_primitive(template) first"
            )
        custom[name] = template.config_model(**brief.custom_config.get(name, {}))
    return custom


def _build_permission(
    brief: DomainBrief, owner_id: str, pending: list[str]
) -> PermissionConfig:
    """Permission config from the brief's constraints.

    Each ``requires_human_approval_for`` pattern becomes a require_approval
    override; approver ids are not in the brief, so they are flagged for human
    input. The per-run spend cap mirrors the reasoning-loop budget so
    composition invariant 8 holds by construction.
    """
    overrides = []
    for pattern in brief.constraints.requires_human_approval_for:
        overrides.append(
            ActionOverride(action_pattern=pattern, policy="require_approval", approvers=[])
        )
        pending.append(f"permission.action_policy.override[{pattern}].approvers")

    return PermissionConfig(
        owner=OwnerConfig(user_id=owner_id),
        action_policy=ActionPolicy(default="require_approval", overrides=overrides),
        data_policy=DataPolicy(
            pii_handling=brief.constraints.pii_handling,
            data_retention_days=brief.constraints.data_retention_days,
        ),
        cost_policy=CostPolicy(max_per_run_spend_usd=brief.constraints.cost_per_run_usd_max),
    )


def _build_memory(brief: DomainBrief) -> MemoryConfig:
    """Configure memory stores from the brief (design 2.4.1 — Memory Store
    Configuration Guidance).

    Only the deterministic signals available in a brief are applied here: a
    long-lived / history-accumulating agent gets the universal store (default
    true) plus the vector store for semantic recall. LMDB is always active as
    the operational-tracing backbone (it has no enable flag — it is part of the
    KV config). The richer signals in the 2.4.1 table (learned preferences ->
    mem0, entities -> graph_store, flexible outputs -> document_store) require
    inferring intent from the goal text and belong to the LLM configurator
    extension, so they are left at their defaults here.
    """
    cfg = MemoryConfig()
    if brief.long_lived:
        cfg.long_term.universal_store = True
        cfg.long_term.vector_store = True
    return cfg


def _build_observability(brief: DomainBrief) -> ObservabilityConfig:
    """Observability defaults, capturing browser actions when browser compute is
    requested (keeps composition invariant 9 satisfied by construction)."""
    capture = CaptureConfig(browser_actions="browser" in brief.available_compute)
    return ObservabilityConfig(capture=capture)


def _build_tool_connection(brief: DomainBrief, pending: list[str]) -> ToolConnectionConfig:
    """One native connection per available connection id. The auth method and
    credential reference are unknown from the brief, so they are flagged."""
    connections = []
    for conn_id in brief.available_connections:
        connections.append(
            Connection(id=conn_id, type="native", auth=AuthConfig(method="none"))
        )
        pending.append(f"tool_connection.{conn_id}.auth")
    return ToolConnectionConfig(connections=connections)


def _build_compute(brief: DomainBrief) -> ComputeConfig:
    """Enable the compute backends named in the brief. VM is enabled if asked
    for, but the validator rejects it (no VM service in the infra layer)."""
    return ComputeConfig(
        shell=ShellConfig(enabled="shell" in brief.available_compute),
        browser=BrowserConfig(enabled="browser" in brief.available_compute),
        vm=VmConfig(enabled="vm" in brief.available_compute),
    )


def _build_trigger(brief: DomainBrief, pending: list[str]) -> TriggerConfig:
    """One trigger per requested type, with placeholder specifics flagged for
    human input."""
    task = TaskInjection(template=f"Run: {brief.goal}")
    triggers: list[TriggerDef] = []

    for ttype in brief.triggers_needed:
        tid = f"{ttype}_trigger"
        if ttype == "schedule":
            triggers.append(
                TriggerDef(
                    id=tid,
                    type="schedule",
                    schedule=ScheduleSpec(cron="0 9 * * *"),
                    task_injection=task,
                )
            )
            pending.append(f"trigger.{tid}.schedule.cron")
        elif ttype == "webhook":
            triggers.append(
                TriggerDef(
                    id=tid,
                    type="webhook",
                    webhook=WebhookSpec(endpoint_path=f"/hooks/{tid}"),
                    task_injection=task,
                )
            )
            pending.append(f"trigger.{tid}.webhook.secret_ref")
        elif ttype == "email":
            triggers.append(
                TriggerDef(
                    id=tid,
                    type="email",
                    email=EmailSpec(monitored_address=""),
                    task_injection=task,
                )
            )
            pending.append(f"trigger.{tid}.email.monitored_address")
        elif ttype == "slack":
            triggers.append(
                TriggerDef(
                    id=tid, type="slack", slack=SlackSpec(channel=""), task_injection=task
                )
            )
            pending.append(f"trigger.{tid}.slack.channel")
        elif ttype == "event":
            # Event triggers carry no sub-spec; they are fired by an in-process
            # event the runtime publishes onto the event bus.
            triggers.append(TriggerDef(id=tid, type="event", task_injection=task))
        else:  # manual
            triggers.append(TriggerDef(id=tid, type="manual", task_injection=task))

    return TriggerConfig(triggers=triggers)
