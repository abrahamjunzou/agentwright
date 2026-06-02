"""Tests for the instance configurator (design 2.3)."""

from agentwright.orchestration.configurator import configure
from agentwright.orchestration.domain_brief import BriefConstraints, DomainBrief
from agentwright.orchestration.selector import select_primitives


def _configure(**kwargs):
    base = dict(name="Agent X", goal="do the thing", domain="ops")
    base.update(kwargs)
    brief = DomainBrief(**base)
    selected = select_primitives(brief)
    return brief, configure(brief, selected, "agt_test", "usr_owner", "usr_creator")


def test_identity_filled_from_brief():
    _, d = _configure()
    assert d.primitives.identity.name == "Agent X"
    assert d.primitives.identity.instructions == "do the thing"
    assert d.primitives.identity.owner_id == "usr_owner"
    assert d.primitives.identity.tags == ["ops"]


def test_cost_cap_mirrors_brief_budget_across_primitives():
    # Invariant 8 should hold by construction: run cap == permission spend cap.
    _, d = _configure(constraints=BriefConstraints(cost_per_run_usd_max=5.0))
    assert d.primitives.reasoning_loop.cost_controls.max_cost_per_run_usd == 5.0
    assert d.primitives.permission.cost_policy.max_per_run_spend_usd == 5.0


def test_approval_patterns_become_overrides_and_flag_approvers():
    _, d = _configure(
        constraints=BriefConstraints(requires_human_approval_for=["crm.delete_contact"])
    )
    overrides = d.primitives.permission.action_policy.overrides
    assert overrides[0].action_pattern == "crm.delete_contact"
    assert overrides[0].policy == "require_approval"
    assert any("crm.delete_contact" in h for h in d.human_input_required)


def test_connections_built_and_flagged():
    _, d = _configure(available_connections=["gmail", "slack"])
    ids = {c.id for c in d.primitives.tool_connection.connections}
    assert ids == {"gmail", "slack"}
    assert any("gmail.auth" in h for h in d.human_input_required)


def test_browser_compute_sets_observability_capture():
    # Keeps invariant 9 satisfied by construction.
    _, d = _configure(available_compute=["browser"])
    assert d.primitives.compute.browser.enabled is True
    assert d.primitives.observability.capture.browser_actions is True


def test_data_policy_from_constraints():
    _, d = _configure(
        constraints=BriefConstraints(pii_handling="deny", data_retention_days=30)
    )
    assert d.primitives.permission.data_policy.pii_handling == "deny"
    assert d.primitives.permission.data_policy.data_retention_days == 30


def test_versions_pinned_for_present_primitives():
    _, d = _configure(long_lived=True)
    assert d.primitive_versions["memory"] == "2.0"
    assert d.primitive_versions["identity"] == "1.0"
    # Unselected primitives are not pinned.
    assert "compute" not in d.primitive_versions


def test_long_lived_enables_vector_and_universal_stores():
    # Design 2.4.1 deterministic subset.
    _, d = _configure(long_lived=True)
    assert d.primitives.memory.long_term.universal_store is True
    assert d.primitives.memory.long_term.vector_store is True


def test_event_trigger_type_is_configured():
    _, d = _configure(triggers_needed=["event"])
    triggers = d.primitives.trigger.triggers
    assert len(triggers) == 1
    assert triggers[0].type == "event"  # not silently turned into "manual"


def test_manual_trigger_type_is_configured():
    _, d = _configure(triggers_needed=["manual"])
    assert d.primitives.trigger.triggers[0].type == "manual"


def test_unselected_primitives_are_none():
    _, d = _configure()  # minimal brief
    assert d.primitives.memory is None
    assert d.primitives.tool_connection is None
    assert d.primitives.compute is None
    assert d.primitives.trigger is None
    assert d.primitives.generated_ui is None
