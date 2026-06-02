"""Layer 2: the Domain Orchestration Layer.

Turns an intent-level :class:`DomainBrief` into a validated, persistable
:class:`AgentDefinition` via the pipeline:

    select_primitives -> configure -> validate -> register

Public entry points live on the orchestrator module.
"""

from .agent_definition import AgentDefinition, PrimitiveSet
from .domain_brief import BriefConstraints, DomainBrief
from .instance_registry import InstanceRegistry
from .orchestrator import compose, compose_and_register, register
from .selector import select_primitives
from .validator import ValidationResult, validate

__all__ = [
    "DomainBrief",
    "BriefConstraints",
    "AgentDefinition",
    "PrimitiveSet",
    "InstanceRegistry",
    "select_primitives",
    "validate",
    "ValidationResult",
    "compose",
    "register",
    "compose_and_register",
]
