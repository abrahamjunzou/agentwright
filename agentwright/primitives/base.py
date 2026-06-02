"""Primitive template metadata.

Each primitive in the design (identity, memory, reasoning_loop, ...) is a
template with three parts: a typed config schema, a runtime contract (what it
exposes during a run), and a dependency list. The config schema is a pydantic
model defined in the primitive's own module; this file defines the lightweight
metadata wrapper that ties the schema together with its version, dependencies,
and the human-readable runtime contract.

The :class:`PrimitiveTemplate` objects are collected in ``registry.py`` to form
the primitive registry that the domain orchestration layer reasons over.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel


@dataclass(frozen=True)
class PrimitiveTemplate:
    """Immutable description of one primitive type.

    Attributes:
        name: primitive identifier, e.g. ``"identity"``.
        version: semantic version string of the template (design "Versioning").
        config_model: pydantic model class describing the config schema.
        dependencies: names of other primitives this one requires.
        runtime_contract: human-readable list of what the primitive provides at
            runtime. This layer does not execute primitives, so the contract is
            documentation that the runtime infrastructure layer implements.
    """

    name: str
    version: str
    config_model: type[BaseModel]
    dependencies: tuple[str, ...] = ()
    runtime_contract: tuple[str, ...] = field(default_factory=tuple)
