"""Orchestrator — the end-to-end composition pipeline (design 2.3).

    domain_brief
        -> select_primitives
        -> configure
        -> validate
        -> (optionally) register

Each step is wrapped in an OpenTelemetry span so the orchestration boundaries
are observable (CLAUDE.md rule 3). Spans are no-ops unless a tracer provider is
installed, so this adds no runtime cost by default.
"""

from __future__ import annotations

from ..telemetry import get_tracer
from . import configurator, selector, validator
from .agent_definition import AgentDefinition
from .domain_brief import DomainBrief
from .ids import new_agent_id
from .instance_registry import InstanceRegistry

_tracer = get_tracer()


def compose(brief: DomainBrief, owner_id: str, created_by: str) -> AgentDefinition:
    """Run selection -> configuration -> validation for ``brief``.

    Returns a fully-built AgentDefinition whose status is ``validated`` when it
    passes all invariants, or ``draft`` (with ``validation_errors`` populated)
    when it does not. Does not persist — call :func:`register` for that.
    """
    with _tracer.start_as_current_span("orchestrate.compose") as span:
        span.set_attribute("brief.name", brief.name)
        span.set_attribute("brief.domain", brief.domain)

        with _tracer.start_as_current_span("orchestrate.select") as s:
            selected = selector.select_primitives(brief)
            s.set_attribute("selected", sorted(selected))

        agent_id = new_agent_id()
        with _tracer.start_as_current_span("orchestrate.configure"):
            definition = configurator.configure(
                brief, selected, agent_id, owner_id, created_by
            )

        with _tracer.start_as_current_span("orchestrate.validate") as v:
            result = validator.validate(definition, sub_agent=brief.sub_agent)
            validator.apply(definition, result)
            v.set_attribute("valid", result.ok)
            v.set_attribute("error_count", len(result.errors))
            v.set_attribute("warning_count", len(result.warnings))

        span.set_attribute("agent_id", agent_id)
        span.set_attribute("status", definition.status)
        return definition


def register(definition: AgentDefinition, registry: InstanceRegistry) -> str:
    """Persist a validated definition and mark it active.

    Refuses to register a definition that still has validation errors, matching
    the design's flow where the Instance Registry is reached only "if valid".
    """
    with _tracer.start_as_current_span("orchestrate.register") as span:
        span.set_attribute("agent_id", definition.agent_id)
        if definition.validation_errors:
            raise ValueError(
                "cannot register a definition with validation errors: "
                f"{definition.validation_errors}"
            )
        registry.create(definition)
        registry.update_status(definition.agent_id, "active")
        definition.status = "active"
        return definition.agent_id


def compose_and_register(
    brief: DomainBrief, owner_id: str, created_by: str, registry: InstanceRegistry
) -> AgentDefinition:
    """Convenience: compose, and register only if valid. Invalid definitions are
    returned unregistered with their errors for inspection."""
    definition = compose(brief, owner_id, created_by)
    if not definition.validation_errors:
        register(definition, registry)
    return definition
