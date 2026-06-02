"""Tests for the Layer-2 -> Layer-1 consumer journey.

These simulate how an agent system (e.g. an LLM-driven domain orchestration
agent) actually uses this package end to end:

  1. DISCOVER  the primitive catalogue (names, versions, deps, JSON schemas).
  2. REASON    over dependencies to assemble a valid primitive set by hand,
               independent of the built-in selector matrix.
  3. CONFIGURE primitives directly from JSON (as an LLM would emit config).
  4. ASSEMBLE  an AgentDefinition and VALIDATE it.
  5. HANDOFF   serialize to JSON and reload it intact — the boundary a Layer-0
               runtime crosses when it loads a stored definition to run it.
  6. EXTEND    register a custom primitive and find it through discovery.

If these pass, an external agent can drive the whole layer through the public
API without reaching into internals.
"""

import json

import pytest

from agentwright import catalogue, describe_primitive
from agentwright.orchestration import AgentDefinition, validate
from agentwright.orchestration.agent_definition import PrimitiveSet
from agentwright.orchestration.ids import new_agent_id
from agentwright.orchestration.instance_registry import InstanceRegistry
from agentwright.primitives import (
    PRIMITIVE_REGISTRY,
    PrimitiveTemplate,
    missing_dependencies,
    register,
    resolve_dependencies,
)
from agentwright.primitives.identity import IdentityConfig
from agentwright.primitives.observability import ObservabilityConfig
from agentwright.primitives.permission import OwnerConfig, PermissionConfig
from agentwright.primitives.reasoning_loop import ReasoningLoopConfig


# --- 1. DISCOVER ---------------------------------------------------------


def test_catalogue_is_complete_and_json_serializable():
    cat = catalogue()
    assert len(cat) == len(PRIMITIVE_REGISTRY)
    # An agent must be able to ship this over the wire as JSON.
    text = json.dumps(cat)
    assert text  # serializes without raising
    names = {entry["name"] for entry in cat}
    assert names == set(PRIMITIVE_REGISTRY)


def test_describe_primitive_exposes_everything_an_agent_needs():
    d = describe_primitive("permission")
    assert d["name"] == "permission"
    assert d["version"] == "1.0"
    assert d["dependencies"] == ["identity"]
    assert d["runtime_contract"]  # non-empty contract description
    # The config schema is real JSON Schema with the top-level config fields.
    props = d["config_schema"]["properties"]
    assert {"owner", "action_policy", "data_policy", "cost_policy"} <= set(props)


def test_agent_can_read_required_config_fields_from_schema():
    # An LLM deciding how to fill identity reads which fields are required.
    schema = describe_primitive("identity")["config_schema"]
    assert set(schema["required"]) >= {"name", "instructions", "owner_id"}


# --- 2. REASON over dependencies (without the selector) -------------------


def test_agent_assembles_dependency_complete_set_by_hand():
    # Agent decides it wants a tool-using agent. It must discover that
    # tool_connection pulls in permission.
    wanted = {"identity", "reasoning_loop", "observability", "tool_connection"}
    gaps = missing_dependencies(wanted)
    assert gaps == {"tool_connection": ["permission"]}
    # Resolve the closure and confirm it is now complete.
    complete = resolve_dependencies(wanted)
    assert "permission" in complete
    assert missing_dependencies(complete) == {}


# --- 3 & 4. CONFIGURE from JSON + ASSEMBLE + VALIDATE ---------------------


def _core_primitive_set() -> PrimitiveSet:
    """The four always-required primitives, built from raw dicts the way an
    agent emitting JSON config would."""
    return PrimitiveSet(
        identity=IdentityConfig.model_validate(
            {"name": "Researcher", "instructions": "research topics", "owner_id": "usr_a"}
        ),
        reasoning_loop=ReasoningLoopConfig.model_validate(
            {"model": {"model_id": "claude-opus-4-8", "max_tokens": 4096}}
        ),
        permission=PermissionConfig.model_validate({"owner": {"user_id": "usr_a"}}),
        observability=ObservabilityConfig.model_validate({"trace_level": "verbose"}),
    )


def test_agent_built_definition_validates():
    d = AgentDefinition(
        agent_id=new_agent_id(),
        name="Researcher",
        domain="research",
        created_by="usr_a",
        primitives=_core_primitive_set(),
    )
    result = validate(d)
    assert result.ok, result.errors
    # Config the runtime layer would read back out:
    assert d.primitives.reasoning_loop.model.max_tokens == 4096
    assert d.primitives.observability.trace_level == "verbose"


def test_agent_built_definition_surfaces_invariant_violations():
    # Agent sets a run budget above the permission cap -> invariant 8.
    ps = _core_primitive_set()
    ps.reasoning_loop.cost_controls.max_cost_per_run_usd = 50.0
    ps.permission.cost_policy.max_per_run_spend_usd = 2.0
    d = AgentDefinition(
        agent_id=new_agent_id(),
        name="X",
        domain="research",
        created_by="usr_a",
        primitives=ps,
    )
    result = validate(d)
    assert not result.ok
    assert any("invariant 8" in e for e in result.errors)


# --- 5. HANDOFF: serialize -> reload (Layer 1 -> Layer 0 boundary) --------


def test_definition_survives_json_handoff_to_runtime():
    d = AgentDefinition(
        agent_id=new_agent_id(),
        name="Researcher",
        domain="research",
        created_by="usr_a",
        primitives=_core_primitive_set(),
        primitive_versions={"identity": "1.0"},
        human_input_required=["permission.action_policy.override[x].approvers"],
    )
    # What a runtime would store and later load:
    blob = d.model_dump_json()
    reloaded = AgentDefinition.model_validate_json(blob)
    assert reloaded.agent_id == d.agent_id
    assert reloaded.primitives.identity.instructions == "research topics"
    assert reloaded.primitive_versions == {"identity": "1.0"}
    assert reloaded.human_input_required == d.human_input_required
    # Reloaded definition still validates — no information lost across the boundary.
    assert validate(reloaded).ok


def test_full_handoff_through_registry(tmp_path):
    # Layer-2 agent persists; a fresh process (Layer-0 runtime) reloads and runs.
    db = tmp_path / "system.db"
    d = AgentDefinition(
        agent_id=new_agent_id(),
        name="Researcher",
        domain="research",
        created_by="usr_a",
        primitives=_core_primitive_set(),
        status="validated",
    )
    InstanceRegistry(db).create(d)

    runtime_view = InstanceRegistry(db).get(d.agent_id)
    assert runtime_view is not None
    assert runtime_view.primitives.permission.owner.user_id == "usr_a"


# --- 6. EXTEND: custom primitive discoverable through the catalogue -------


def test_custom_primitive_is_discoverable_after_registration():
    template = PrimitiveTemplate(
        name="research_synthesis",
        version="0.1",
        config_model=IdentityConfig,  # reuse a model for the test
        dependencies=("identity", "memory"),
        runtime_contract=("synthesize(threads): Report",),
    )
    register(template)
    try:
        names = {entry["name"] for entry in catalogue()}
        assert "research_synthesis" in names
        d = describe_primitive("research_synthesis")
        assert d["dependencies"] == ["identity", "memory"]
        assert d["runtime_contract"] == ["synthesize(threads): Report"]
    finally:
        del PRIMITIVE_REGISTRY["research_synthesis"]


def test_unknown_primitive_describe_raises():
    with pytest.raises(KeyError):
        describe_primitive("not_a_primitive")
