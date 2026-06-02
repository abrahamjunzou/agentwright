"""agentwright — Layer 1 (Primitive Layer) + Layer 2 (Domain Orchestration).

Implements the design in ``design/agent_primitive_layer_design.md``, constrained
by the runtime infrastructure layer in ``design/agent_runtime_infrastructure.md``.

Typical use::

    from agentwright import DomainBrief, InstanceRegistry, compose_and_register

    brief = DomainBrief(name="...", goal="...", domain="sales", long_lived=True)
    registry = InstanceRegistry("system.db")
    definition = compose_and_register(brief, owner_id="usr_1", created_by="usr_1",
                                      registry=registry)
"""

from .orchestration import (
    AgentDefinition,
    BriefConstraints,
    DomainBrief,
    InstanceRegistry,
    PrimitiveSet,
    ValidationResult,
    compose,
    compose_and_register,
    register,
    select_primitives,
    validate,
)
from .primitives import (
    PRIMITIVE_REGISTRY,
    PrimitiveTemplate,
    catalogue,
    describe_primitive,
    register_primitive,
)

__all__ = [
    "PRIMITIVE_REGISTRY",
    "PrimitiveTemplate",
    "catalogue",
    "describe_primitive",
    "register_primitive",
    "DomainBrief",
    "BriefConstraints",
    "AgentDefinition",
    "PrimitiveSet",
    "InstanceRegistry",
    "ValidationResult",
    "select_primitives",
    "validate",
    "compose",
    "register",
    "compose_and_register",
]
