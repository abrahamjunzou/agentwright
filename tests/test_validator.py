"""Tests for the composition validator — every invariant + infra gaps."""

from agentwright.orchestration.agent_definition import AgentDefinition, PrimitiveSet
from agentwright.orchestration.configurator import configure
from agentwright.orchestration.domain_brief import DomainBrief
from agentwright.orchestration.selector import select_primitives
from agentwright.orchestration.validator import apply, validate
from agentwright.primitives.identity import IdentityConfig
from agentwright.primitives.observability import ObservabilityConfig
from agentwright.primitives.permission import OwnerConfig, PermissionConfig
from agentwright.primitives.reasoning_loop import ReasoningLoopConfig
from agentwright.primitives.tool_connection import (
    Connection,
    ToolConnectionConfig,
    ToolDef,
)


def _valid_definition(**brief_kwargs) -> AgentDefinition:
    """Build a definition from a brief through the real configurator."""
    base = dict(name="A", goal="g", domain="d")
    base.update(brief_kwargs)
    brief = DomainBrief(**base)
    selected = select_primitives(brief)
    return configure(brief, selected, "agt_x", "usr_o", "usr_c")


def _minimal_primitives() -> PrimitiveSet:
    """The four always-required primitives, configured minimally and validly."""
    return PrimitiveSet(
        identity=IdentityConfig(name="A", instructions="g", owner_id="usr_o"),
        reasoning_loop=ReasoningLoopConfig(),
        permission=PermissionConfig(owner=OwnerConfig(user_id="usr_o")),
        observability=ObservabilityConfig(),
    )


def _bare_definition(prims: PrimitiveSet) -> AgentDefinition:
    return AgentDefinition(
        agent_id="agt_x", name="A", domain="d", created_by="usr_c", primitives=prims
    )


# --- happy path ----------------------------------------------------------


def test_minimal_definition_is_valid():
    result = validate(_bare_definition(_minimal_primitives()))
    assert result.ok
    assert result.errors == []


def test_lead_enrichment_example_validates_clean():
    d = _valid_definition(
        available_connections=["gmail", "hubspot", "slack", "clearbit_api"],
        triggers_needed=["schedule"],
        long_lived=True,
    )
    result = validate(d)
    assert result.ok, result.errors


# --- invariants 1-4: always-required primitives --------------------------


def test_missing_dependency_is_error():
    # tool_connection present but permission removed -> dependency gap + invariant 3.
    # model_construct bypasses validation so we can build the invalid state the
    # validator is meant to catch.
    base = _minimal_primitives()
    prims_no_perm = PrimitiveSet.model_construct(
        identity=base.identity,
        reasoning_loop=base.reasoning_loop,
        permission=None,
        observability=base.observability,
        tool_connection=ToolConnectionConfig(connections=[Connection(id="x")]),
        memory=None,
        compute=None,
        trigger=None,
        generated_ui=None,
    )
    result = validate(_bare_definition(prims_no_perm))
    assert not result.ok
    assert any("permission" in e for e in result.errors)


# --- invariant 5: tool approval policy -----------------------------------


def test_tool_requiring_approval_without_policy_fails():
    prims = _minimal_primitives()
    prims.permission.action_policy.default = "allow"  # nothing gates it
    prims.tool_connection = ToolConnectionConfig(
        connections=[
            Connection(
                id="gmail",
                tools=[ToolDef(name="send_email", requires_approval=True)],
            )
        ]
    )
    result = validate(_bare_definition(prims))
    assert not result.ok
    assert any("invariant 5" in e for e in result.errors)


def test_tool_approval_covered_by_override_passes():
    prims = _minimal_primitives()
    prims.permission.action_policy.default = "allow"
    from agentwright.primitives.permission import ActionOverride

    prims.permission.action_policy.overrides = [
        ActionOverride(action_pattern="gmail.send_email", policy="require_approval")
    ]
    prims.tool_connection = ToolConnectionConfig(
        connections=[
            Connection(id="gmail", tools=[ToolDef(name="send_email", requires_approval=True)])
        ]
    )
    assert validate(_bare_definition(prims)).ok


# --- invariant 7: structured state requires universal store --------------


def test_structured_state_without_universal_store_fails():
    prims = _minimal_primitives()
    from agentwright.primitives.memory import (
        MemoryConfig,
        StructuredStateConfig,
        StructuredStateField,
    )

    prims.memory = MemoryConfig(
        structured_state=StructuredStateConfig(
            enabled=True, state_schema=[StructuredStateField(name="x", type="json")]
        )
    )
    prims.memory.long_term.universal_store = False
    result = validate(_bare_definition(prims))
    assert any("invariant 7" in e for e in result.errors)


# --- invariant 8: cost coherence -----------------------------------------


def test_run_cost_exceeding_permission_cap_fails():
    prims = _minimal_primitives()
    prims.reasoning_loop.cost_controls.max_cost_per_run_usd = 10.0
    prims.permission.cost_policy.max_per_run_spend_usd = 2.0
    result = validate(_bare_definition(prims))
    assert any("invariant 8" in e for e in result.errors)


# --- invariant 9: browser capture (warning) ------------------------------


def test_browser_without_capture_warns_not_errors():
    prims = _minimal_primitives()
    from agentwright.primitives.compute import BrowserConfig, ComputeConfig

    prims.compute = ComputeConfig(browser=BrowserConfig(enabled=True))
    prims.observability.capture.browser_actions = False
    result = validate(_bare_definition(prims))
    assert result.ok  # warning, not error
    assert any("invariant 9" in w for w in result.warnings)


# --- invariant 10: sub_agent must not have triggers ----------------------


def test_sub_agent_with_trigger_fails():
    d = _valid_definition(triggers_needed=["schedule"])
    result = validate(d, sub_agent=True)
    assert any("invariant 10" in e for e in result.errors)


def test_sub_agent_without_trigger_ok():
    d = _valid_definition()
    assert validate(d, sub_agent=True).ok


# --- infrastructure gaps -------------------------------------------------


def test_vm_enabled_is_rejected_invariant_12():
    prims = _minimal_primitives()
    from agentwright.primitives.compute import ComputeConfig, VmConfig

    prims.compute = ComputeConfig(vm=VmConfig(enabled=True))
    result = validate(_bare_definition(prims))
    assert any("invariant 12" in e and "compute.vm" in e for e in result.errors)


# --- invariant 11: email/slack triggers need a channel connection --------


def _with_trigger(ttype, connections=None):
    """Build a definition with one trigger of ``ttype`` and optional connections."""
    from agentwright.primitives.trigger import (
        EmailSpec,
        SlackSpec,
        TriggerConfig,
        TriggerDef,
    )

    prims = _minimal_primitives()
    spec = {}
    if ttype == "email":
        spec = {"email": EmailSpec(monitored_address="a@b.com")}
    elif ttype == "slack":
        spec = {"slack": SlackSpec(channel="#c")}
    prims.trigger = TriggerConfig(triggers=[TriggerDef(id=f"{ttype}_t", type=ttype, **spec)])
    if connections:
        prims.tool_connection = ToolConnectionConfig(
            connections=[Connection(id=c) for c in connections]
        )
    return _bare_definition(prims)


def test_email_trigger_without_connection_is_error():
    result = validate(_with_trigger("email"))
    assert not result.ok
    assert any("invariant 11" in e and "email" in e for e in result.errors)


def test_email_trigger_with_gmail_connection_ok():
    assert validate(_with_trigger("email", connections=["gmail"])).ok


def test_slack_trigger_without_connection_is_error():
    result = validate(_with_trigger("slack"))
    assert any("invariant 11" in e and "slack" in e for e in result.errors)


def test_slack_trigger_with_slack_connection_ok():
    assert validate(_with_trigger("slack", connections=["slack"])).ok


def test_schedule_trigger_needs_no_connection():
    from agentwright.primitives.trigger import (
        ScheduleSpec,
        TriggerConfig,
        TriggerDef,
    )

    prims = _minimal_primitives()
    prims.trigger = TriggerConfig(
        triggers=[TriggerDef(id="s", type="schedule", schedule=ScheduleSpec(cron="0 9 * * *"))]
    )
    assert validate(_bare_definition(prims)).ok


def test_non_local_export_warns():
    prims = _minimal_primitives()
    prims.observability.export.enabled = True
    prims.observability.export.destination = "s3"
    result = validate(_bare_definition(prims))
    assert result.ok
    assert any("export.destination" in w for w in result.warnings)


def test_apply_sets_status_and_fields():
    prims = _minimal_primitives()
    prims.reasoning_loop.cost_controls.max_cost_per_run_usd = 99.0  # break invariant 8
    d = _bare_definition(prims)
    apply(d, validate(d))
    assert d.status == "draft"
    assert d.validation_errors

    good = _bare_definition(_minimal_primitives())
    apply(good, validate(good))
    assert good.status == "validated"
