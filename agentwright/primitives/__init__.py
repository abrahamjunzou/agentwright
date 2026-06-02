"""Layer 1: the Primitive Layer.

Nine typed, templated building blocks (identity, memory, reasoning_loop,
tool_connection, compute, trigger, generated_ui, permission, observability),
each defining a config schema, a runtime contract, and its dependencies.

See ``registry.py`` for the registry and dependency helpers.
"""

from .base import PrimitiveTemplate
from .registry import (
    PRIMITIVE_REGISTRY,
    catalogue,
    describe_primitive,
    get_template,
    missing_dependencies,
    register,
    resolve_dependencies,
)

# Public alias: at the top-level package ``register`` already names the
# orchestration "persist a definition" call, so custom-template registration is
# exported there as ``register_primitive``. Both point at the same function.
register_primitive = register

__all__ = [
    "PrimitiveTemplate",
    "PRIMITIVE_REGISTRY",
    "get_template",
    "register",
    "register_primitive",
    "resolve_dependencies",
    "missing_dependencies",
    "describe_primitive",
    "catalogue",
]
