"""Tests for first-class custom primitives (design "Extension: Custom
Primitives").

A registered custom template must flow through the whole orchestration pipeline
exactly like a built-in: requested in the brief, selected, configured from its
own ``config_model`` (with brief overrides), version-pinned, dependency-checked
by the validator, and round-tripped by serialization. Nothing in the
configurator is type-specific — the registry is the single source of truth.
"""

import pytest
from pydantic import BaseModel

from agentwright import (
    DomainBrief,
    PrimitiveTemplate,
    compose,
    register_primitive,
)
from agentwright.primitives.registry import PRIMITIVE_REGISTRY


class SentimentConfig(BaseModel):
    """Config for the test custom primitive."""

    threshold: float = 0.5
    model: str = "default"


@pytest.fixture
def clean_registry():
    """Snapshot the global registry and restore it after the test so custom
    templates registered here do not leak into other tests."""
    snapshot = dict(PRIMITIVE_REGISTRY)
    try:
        yield
    finally:
        PRIMITIVE_REGISTRY.clear()
        PRIMITIVE_REGISTRY.update(snapshot)


def _register_sentiment(dependencies=("identity",)):
    register_primitive(
        PrimitiveTemplate(
            name="sentiment",
            version="1.0.0",
            config_model=SentimentConfig,
            dependencies=dependencies,
            runtime_contract=("scores text sentiment",),
        )
    )


def test_custom_primitive_is_carried_configured_and_pinned(clean_registry):
    _register_sentiment()
    brief = DomainBrief(
        name="reviews",
        goal="triage reviews",
        domain="support",
        extra_primitives=["sentiment"],
        custom_config={"sentiment": {"threshold": 0.8}},
    )
    d = compose(brief, owner_id="u1", created_by="u1")

    assert d.status == "validated"
    assert d.validation_errors == []
    # selected + present, configured from the template's model with the override
    assert "sentiment" in d.primitives.present_names()
    cfg = d.primitives.custom["sentiment"]
    assert isinstance(cfg, SentimentConfig)
    assert cfg.threshold == 0.8  # brief override
    assert cfg.model == "default"  # template default
    # version pinned from the registry like any built-in
    assert d.primitive_versions["sentiment"] == "1.0.0"


def test_custom_config_round_trips_concrete_subclass(clean_registry):
    """SerializeAsAny must keep the subclass fields on model_dump so the instance
    registry persists the custom config faithfully."""
    _register_sentiment()
    d = compose(
        DomainBrief(name="r", goal="g", domain="s", extra_primitives=["sentiment"]),
        owner_id="u",
        created_by="u",
    )
    dumped = d.model_dump()["primitives"]["custom"]["sentiment"]
    assert dumped == {"threshold": 0.5, "model": "default"}


def test_unsatisfied_custom_dependency_fails_validation(clean_registry):
    """A custom primitive depending on a primitive that is not in the
    composition is reported by the same dependency check as the built-ins."""
    _register_sentiment(dependencies=("memory",))  # memory only when long_lived
    d = compose(
        DomainBrief(name="r", goal="g", domain="s", extra_primitives=["sentiment"]),
        owner_id="u",
        created_by="u",
    )
    assert d.status == "draft"
    assert any("sentiment" in e and "memory" in e for e in d.validation_errors)


def test_satisfied_custom_dependency_validates(clean_registry):
    """When the dependency is present (long_lived pulls in memory), it passes."""
    _register_sentiment(dependencies=("memory",))
    d = compose(
        DomainBrief(
            name="r", goal="g", domain="s", long_lived=True,
            extra_primitives=["sentiment"],
        ),
        owner_id="u",
        created_by="u",
    )
    assert d.status == "validated"
    assert "sentiment" in d.primitives.present_names()


def test_unregistered_extra_primitive_raises(clean_registry):
    with pytest.raises(KeyError, match="not.*registered"):
        compose(
            DomainBrief(name="r", goal="g", domain="s", extra_primitives=["ghost"]),
            owner_id="u",
            created_by="u",
        )


def test_no_extra_primitives_leaves_custom_empty(clean_registry):
    """Backward compatibility: a plain brief produces an empty custom map."""
    d = compose(DomainBrief(name="r", goal="g", domain="s"), owner_id="u", created_by="u")
    assert d.primitives.custom == {}
    assert d.status == "validated"
