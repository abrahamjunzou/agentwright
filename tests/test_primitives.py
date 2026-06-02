"""Tests for Layer 1: primitive config models and the registry."""

import pytest

from agentwright.primitives import (
    PRIMITIVE_REGISTRY,
    PrimitiveTemplate,
    get_template,
    missing_dependencies,
    register,
    resolve_dependencies,
)
from agentwright.primitives.identity import IdentityConfig
from agentwright.primitives.memory import MemoryConfig
from agentwright.primitives.reasoning_loop import ReasoningLoopConfig


def test_registry_has_all_nine_primitives():
    expected = {
        "identity",
        "memory",
        "reasoning_loop",
        "tool_connection",
        "compute",
        "trigger",
        "generated_ui",
        "permission",
        "observability",
    }
    assert set(PRIMITIVE_REGISTRY) == expected


def test_every_non_identity_primitive_depends_on_identity():
    for name, template in PRIMITIVE_REGISTRY.items():
        if name == "identity":
            assert template.dependencies == ()
        else:
            assert "identity" in template.dependencies


def test_models_with_pure_defaults_instantiate():
    # These primitives have no required fields and must build from defaults.
    assert MemoryConfig().long_term.universal_store is True
    assert ReasoningLoopConfig().model.model_id == "claude-opus-4-8"


def test_identity_requires_core_fields():
    with pytest.raises(Exception):
        IdentityConfig()  # name/instructions/owner_id are required


def test_memory_structured_state_schema_alias():
    # The design's YAML key is "schema"; the Python attribute is state_schema.
    cfg = MemoryConfig.model_validate(
        {"structured_state": {"enabled": True, "schema": [{"name": "x", "type": "json"}]}}
    )
    assert cfg.structured_state.state_schema[0].name == "x"
    dumped = cfg.model_dump(by_alias=True)
    assert "schema" in dumped["structured_state"]


def test_resolve_dependencies_is_transitive():
    # trigger -> reasoning_loop -> identity (+ permission via nothing here)
    assert resolve_dependencies({"trigger"}) == {"trigger", "reasoning_loop", "identity"}


def test_missing_dependencies_detected_and_clear():
    # tool_connection needs permission; alone it is incomplete.
    gaps = missing_dependencies({"tool_connection", "identity"})
    assert gaps == {"tool_connection": ["permission"]}
    # Complete set has no gaps.
    assert missing_dependencies({"identity", "permission", "tool_connection"}) == {}


def test_register_custom_primitive():
    template = PrimitiveTemplate(
        name="custom_thing",
        version="0.1",
        config_model=IdentityConfig,
        dependencies=("identity",),
    )
    register(template)
    try:
        assert get_template("custom_thing").version == "0.1"
    finally:
        del PRIMITIVE_REGISTRY["custom_thing"]


def test_register_custom_with_unknown_dependency_fails():
    bad = PrimitiveTemplate(
        name="bad", version="0.1", config_model=IdentityConfig, dependencies=("nope",)
    )
    with pytest.raises(ValueError):
        register(bad)


def test_get_unknown_template_raises():
    with pytest.raises(KeyError):
        get_template("does_not_exist")
