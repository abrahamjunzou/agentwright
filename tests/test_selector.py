"""Tests for the primitive selector decision matrix (design 2.4)."""

from agentwright.orchestration.domain_brief import DomainBrief
from agentwright.orchestration.selector import ALWAYS_REQUIRED, select_primitives


def _brief(**kwargs):
    base = dict(name="A", goal="g", domain="d")
    base.update(kwargs)
    return DomainBrief(**base)


def test_minimal_brief_selects_only_always_required():
    assert select_primitives(_brief()) == set(ALWAYS_REQUIRED)


def test_long_lived_adds_memory():
    assert "memory" in select_primitives(_brief(long_lived=True))


def test_connections_add_tool_connection():
    assert "tool_connection" in select_primitives(_brief(available_connections=["gmail"]))


def test_compute_adds_compute():
    assert "compute" in select_primitives(_brief(available_compute=["shell"]))


def test_triggers_add_trigger():
    assert "trigger" in select_primitives(_brief(triggers_needed=["schedule"]))


def test_ui_adds_generated_ui():
    assert "generated_ui" in select_primitives(_brief(ui_output_needed=True))


def test_email_trigger_implies_tool_connection():
    # Infra edge: email/slack triggers poll through a connection.
    selected = select_primitives(_brief(triggers_needed=["email"]))
    assert {"trigger", "tool_connection"} <= selected


def test_full_brief_selects_expected_set():
    selected = select_primitives(
        _brief(
            available_connections=["gmail"],
            triggers_needed=["schedule"],
            long_lived=True,
        )
    )
    assert selected == {
        "identity",
        "reasoning_loop",
        "observability",
        "permission",
        "memory",
        "tool_connection",
        "trigger",
    }
