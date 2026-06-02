"""Tests for the SQLite-backed instance registry."""

import pytest

from agentwright.orchestration.configurator import configure
from agentwright.orchestration.domain_brief import DomainBrief
from agentwright.orchestration.instance_registry import InstanceRegistry
from agentwright.orchestration.selector import select_primitives


def _definition(name="A", domain="d", status="validated"):
    brief = DomainBrief(name=name, goal="g", domain=domain)
    selected = select_primitives(brief)
    d = configure(brief, selected, f"agt_{name}", "usr_o", "usr_c")
    d.status = status
    return d


def test_create_and_get_roundtrip():
    reg = InstanceRegistry(":memory:")
    d = _definition()
    agent_id = reg.create(d)
    fetched = reg.get(agent_id)
    assert fetched is not None
    assert fetched.name == d.name
    assert fetched.primitives.identity.owner_id == "usr_o"


def test_get_missing_returns_none():
    reg = InstanceRegistry(":memory:")
    assert reg.get("agt_nope") is None


def test_duplicate_create_raises():
    reg = InstanceRegistry(":memory:")
    d = _definition()
    reg.create(d)
    with pytest.raises(ValueError):
        reg.create(d)


def test_list_filters_by_domain_and_status():
    reg = InstanceRegistry(":memory:")
    reg.create(_definition(name="sales1", domain="sales", status="active"))
    reg.create(_definition(name="ops1", domain="ops", status="active"))
    reg.create(_definition(name="sales2", domain="sales", status="draft"))

    assert {d.name for d in reg.list(domain="sales")} == {"sales1", "sales2"}
    assert {d.name for d in reg.list(status="active")} == {"sales1", "ops1"}
    assert {d.name for d in reg.list(domain="sales", status="draft")} == {"sales2"}
    assert len(reg.list()) == 3


def test_update_status():
    reg = InstanceRegistry(":memory:")
    d = _definition(status="validated")
    reg.create(d)
    reg.update_status(d.agent_id, "active")
    assert reg.get(d.agent_id).status == "active"


def test_update_unknown_status_raises():
    reg = InstanceRegistry(":memory:")
    with pytest.raises(KeyError):
        reg.update_status("agt_nope", "active")


def test_persists_to_file(tmp_path):
    db = tmp_path / "nested" / "system.db"
    reg = InstanceRegistry(db)
    d = _definition(name="persist")
    reg.create(d)
    reg.close()

    reopened = InstanceRegistry(db)
    assert reopened.get(d.agent_id).name == "persist"
