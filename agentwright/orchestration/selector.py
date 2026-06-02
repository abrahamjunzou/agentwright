"""Primitive Selector — decides the minimal required primitive set (design 2.4).

Implements the deterministic decision matrix:

    Always                          -> identity, reasoning_loop, observability, permission
    long_lived: true                -> memory
    available_connections non-empty -> tool_connection
    available_compute non-empty     -> compute
    triggers_needed non-empty       -> trigger
    ui_output_needed: true          -> generated_ui

Plus one implied edge from the infrastructure doc: email/slack triggers are
satisfied by polling through a connection, so selecting them implies
tool_connection. (The design's optional "LLM infers implicit needs from the
goal text" is intentionally left as a future extension — see select_with_llm
stub — so selection stays deterministic and testable offline.)
"""

from __future__ import annotations

from .domain_brief import DomainBrief

# Primitives required for every agent (design 2.4, first row; invariants 1-4).
ALWAYS_REQUIRED: frozenset[str] = frozenset(
    {"identity", "reasoning_loop", "observability", "permission"}
)


def select_primitives(brief: DomainBrief) -> set[str]:
    """Return the set of primitive names required to satisfy ``brief``."""
    selected: set[str] = set(ALWAYS_REQUIRED)

    if brief.long_lived:
        selected.add("memory")
    if brief.available_connections:
        selected.add("tool_connection")
    if brief.available_compute:
        selected.add("compute")
    if brief.triggers_needed:
        selected.add("trigger")
    if brief.ui_output_needed:
        selected.add("generated_ui")

    # Infrastructure-implied edge: email/slack triggers need a connection to poll.
    if any(t in ("email", "slack") for t in brief.triggers_needed):
        selected.add("tool_connection")

    # Registered custom primitives the brief explicitly requests (design
    # "Extension: Custom Primitives"). They join the set by name; the validator
    # checks their declared dependencies are satisfied like any other primitive.
    selected.update(brief.extra_primitives)

    return selected


def select_with_llm(brief: DomainBrief) -> set[str]:  # pragma: no cover - extension point
    """Future extension: let a model infer implicit needs from the goal text.

    The design notes the selector "may also reason about implicit needs" (e.g.
    a goal mentioning "send a weekly report" implies a schedule trigger and a
    UI). That requires an LLM call and is out of scope for this offline,
    deterministic layer. For now this defers to the rule-based matrix.
    """
    return select_primitives(brief)
