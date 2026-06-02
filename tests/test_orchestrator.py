"""Tests for the end-to-end orchestration pipeline."""

import pytest

from agentwright.orchestration.domain_brief import BriefConstraints, DomainBrief
from agentwright.orchestration.instance_registry import InstanceRegistry
from agentwright.orchestration.orchestrator import (
    compose,
    compose_and_register,
    register,
)


def _lead_enrichment_brief():
    return DomainBrief(
        name="Daily Lead Enrichment Agent",
        goal="Each morning, pull new inbound leads, enrich them, update CRM, post a summary.",
        domain="sales",
        constraints=BriefConstraints(
            cost_per_run_usd_max=2.0,
            requires_human_approval_for=["crm.delete_contact"],
            pii_handling="redact",
            data_retention_days=90,
        ),
        available_connections=["gmail", "hubspot", "slack", "clearbit_api"],
        triggers_needed=["schedule"],
        long_lived=True,
    )


def test_compose_reproduces_design_example_selection():
    d = compose(_lead_enrichment_brief(), owner_id="usr_1", created_by="usr_1")
    assert d.status == "validated"
    assert d.validation_errors == []
    assert d.primitives.present_names() == {
        "identity",
        "memory",
        "reasoning_loop",
        "tool_connection",
        "trigger",
        "permission",
        "observability",
    }
    assert d.agent_id.startswith("agt_")


def test_compose_and_register_persists_and_activates():
    reg = InstanceRegistry(":memory:")
    d = compose_and_register(_lead_enrichment_brief(), "usr_1", "usr_1", reg)
    assert d.status == "active"
    assert reg.get(d.agent_id) is not None


def test_invalid_definition_is_not_registered():
    # vm compute has no backing service -> validation error -> not registered.
    reg = InstanceRegistry(":memory:")
    brief = DomainBrief(name="VM Agent", goal="g", domain="d", available_compute=["vm"])
    d = compose_and_register(brief, "usr_1", "usr_1", reg)
    assert d.status == "draft"
    assert d.validation_errors
    assert reg.get(d.agent_id) is None


def test_register_refuses_definition_with_errors():
    reg = InstanceRegistry(":memory:")
    brief = DomainBrief(name="VM Agent", goal="g", domain="d", available_compute=["vm"])
    d = compose(brief, "usr_1", "usr_1")
    with pytest.raises(ValueError):
        register(d, reg)


def test_email_trigger_with_gmail_connection_composes_clean():
    brief = DomainBrief(
        name="Inbox Agent",
        goal="watch the inbox",
        domain="ops",
        triggers_needed=["email"],
        available_connections=["gmail"],
    )
    d = compose(brief, "usr_1", "usr_1")
    assert d.status == "validated", d.validation_errors


def test_email_trigger_without_connection_fails_composition():
    # triggers_needed=email selects tool_connection, but with no gmail connection
    # invariant 11 fires.
    brief = DomainBrief(
        name="Inbox Agent", goal="watch the inbox", domain="ops", triggers_needed=["email"]
    )
    d = compose(brief, "usr_1", "usr_1")
    assert d.status == "draft"
    assert any("invariant 11" in e for e in d.validation_errors)


def test_event_trigger_composes():
    brief = DomainBrief(
        name="Event Agent", goal="react to events", domain="ops", triggers_needed=["event"]
    )
    d = compose(brief, "usr_1", "usr_1")
    assert d.status == "validated", d.validation_errors
    assert d.primitives.trigger.triggers[0].type == "event"


def test_sub_agent_with_trigger_fails_composition():
    brief = DomainBrief(
        name="Sub", goal="g", domain="d", triggers_needed=["schedule"], sub_agent=True
    )
    d = compose(brief, "usr_1", "usr_1")
    assert d.status == "draft"
    assert any("invariant 10" in e for e in d.validation_errors)
