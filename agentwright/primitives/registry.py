"""The primitive registry — Layer 1's catalogue of building blocks.

Collects the nine built-in :class:`PrimitiveTemplate` objects into a single
registry keyed by primitive name, and provides dependency-resolution helpers
the orchestration layer reasons over. The design's "Extension: Custom
Primitives" section is supported via :func:`register` — a custom template
participates in the same registry, dependency graph, and validation.
"""

from __future__ import annotations

from . import (
    compute,
    generated_ui,
    identity,
    memory,
    observability,
    permission,
    reasoning_loop,
    tool_connection,
    trigger,
)
from .base import PrimitiveTemplate

# Name -> template. Order mirrors the design's primitive numbering.
PRIMITIVE_REGISTRY: dict[str, PrimitiveTemplate] = {
    identity.TEMPLATE.name: identity.TEMPLATE,
    memory.TEMPLATE.name: memory.TEMPLATE,
    reasoning_loop.TEMPLATE.name: reasoning_loop.TEMPLATE,
    tool_connection.TEMPLATE.name: tool_connection.TEMPLATE,
    compute.TEMPLATE.name: compute.TEMPLATE,
    trigger.TEMPLATE.name: trigger.TEMPLATE,
    generated_ui.TEMPLATE.name: generated_ui.TEMPLATE,
    permission.TEMPLATE.name: permission.TEMPLATE,
    observability.TEMPLATE.name: observability.TEMPLATE,
}


def register(template: PrimitiveTemplate) -> None:
    """Register a custom primitive template (design "Custom Primitives").

    Its declared dependencies must already exist in the registry, mirroring the
    rule that custom dependencies "must reference existing primitives".
    """
    for dep in template.dependencies:
        if dep not in PRIMITIVE_REGISTRY:
            raise ValueError(
                f"custom primitive '{template.name}' depends on unknown "
                f"primitive '{dep}'"
            )
    PRIMITIVE_REGISTRY[template.name] = template


def describe_primitive(name: str) -> dict:
    """Return a JSON-serializable description of one primitive.

    This is the discovery surface a Layer-2 agent (or an LLM reasoning over the
    registry) consumes to decide what to select and how to configure it: the
    name, version, dependencies, runtime contract, and the config JSON Schema.
    """
    template = get_template(name)
    return {
        "name": template.name,
        "version": template.version,
        "dependencies": list(template.dependencies),
        "runtime_contract": list(template.runtime_contract),
        "config_schema": template.config_model.model_json_schema(),
    }


def catalogue() -> list[dict]:
    """Return descriptions of every registered primitive (discovery API).

    Lets a domain-orchestration agent enumerate the whole primitive layer in one
    JSON-serializable call rather than reaching into template internals.
    """
    return [describe_primitive(name) for name in PRIMITIVE_REGISTRY]


def get_template(name: str) -> PrimitiveTemplate:
    """Return the template for ``name`` or raise KeyError with a clear message."""
    try:
        return PRIMITIVE_REGISTRY[name]
    except KeyError:
        raise KeyError(f"unknown primitive: {name!r}") from None


def resolve_dependencies(names: set[str]) -> set[str]:
    """Expand ``names`` to include all transitive dependencies.

    Used to check that a selected primitive set is dependency-complete. Raises
    KeyError if any name (or dependency) is not registered.
    """
    resolved: set[str] = set()
    pending = list(names)
    while pending:
        name = pending.pop()
        if name in resolved:
            continue
        template = get_template(name)
        resolved.add(name)
        pending.extend(template.dependencies)
    return resolved


def missing_dependencies(names: set[str]) -> dict[str, list[str]]:
    """Return, per primitive in ``names``, any of its dependencies absent from
    ``names``. An empty dict means the set is dependency-complete."""
    gaps: dict[str, list[str]] = {}
    for name in names:
        template = get_template(name)
        absent = [dep for dep in template.dependencies if dep not in names]
        if absent:
            gaps[name] = absent
    return gaps
