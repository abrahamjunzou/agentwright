"""AgentDefinition — the output of the orchestration layer (design 2.5).

A fully-specified, persistable agent instance: one config per selected
primitive, the primitive versions in force at instantiation (design
"Versioning"), a status, and any validation errors/warnings.

Extensions beyond the design's YAML, clearly marked:
- ``validation_warnings``: non-fatal issues (e.g. unbacked export destination).
- ``human_input_required``: config fields the configurator could not fill from
  the brief (e.g. credential refs, approver ids) — the design's instruction to
  "flag any config requiring human input" needs somewhere to land.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field, SerializeAsAny

from ..primitives.compute import ComputeConfig
from ..primitives.generated_ui import GeneratedUIConfig
from ..primitives.identity import IdentityConfig
from ..primitives.memory import MemoryConfig
from ..primitives.observability import ObservabilityConfig
from ..primitives.permission import PermissionConfig
from ..primitives.reasoning_loop import ReasoningLoopConfig
from ..primitives.tool_connection import ToolConnectionConfig
from ..primitives.trigger import TriggerConfig


def _now() -> datetime:
    return datetime.now(timezone.utc)


# The nine built-in primitive field names; everything else lives in `custom`.
_BUILTIN_NAMES = (
    "identity",
    "reasoning_loop",
    "permission",
    "observability",
    "memory",
    "tool_connection",
    "compute",
    "trigger",
    "generated_ui",
)


class PrimitiveSet(BaseModel):
    """The configured primitives of one agent. Always-required primitives are
    non-optional; the rest are present only when selected."""

    identity: IdentityConfig
    reasoning_loop: ReasoningLoopConfig
    permission: PermissionConfig
    observability: ObservabilityConfig
    memory: MemoryConfig | None = None
    tool_connection: ToolConnectionConfig | None = None
    compute: ComputeConfig | None = None
    trigger: TriggerConfig | None = None
    generated_ui: GeneratedUIConfig | None = None
    # Configs for registered custom primitives, keyed by primitive name (design
    # "Extension: Custom Primitives"). SerializeAsAny keeps each value's concrete
    # subclass on model_dump so the instance registry round-trips it faithfully.
    custom: dict[str, SerializeAsAny[BaseModel]] = Field(default_factory=dict)

    def present_names(self) -> set[str]:
        """Names of primitives that are actually configured on this agent.

        Checks every built-in field for None (not just the optional ones):
        pydantic keeps the four required primitives non-None under normal
        construction, but callers can bypass that with ``model_construct``, and
        the validator must report a missing required primitive rather than crash
        on it. Registered custom primitives in ``custom`` are included too."""
        present = {
            name for name in _BUILTIN_NAMES if getattr(self, name, None) is not None
        }
        return present | set(self.custom)


class AgentDefinition(BaseModel):
    """A composed agent instance (design 2.5)."""

    agent_id: str
    name: str
    domain: str
    created_by: str  # user_id or orchestrator agent_id
    primitives: PrimitiveSet
    created_at: datetime = Field(default_factory=_now)
    primitive_versions: dict[str, str] = Field(default_factory=dict)
    status: str = "draft"  # draft | validated | active | paused | archived
    validation_errors: list[str] = Field(default_factory=list)
    validation_warnings: list[str] = Field(default_factory=list)  # extension
    human_input_required: list[str] = Field(default_factory=list)  # extension
