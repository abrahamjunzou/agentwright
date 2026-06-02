"""Primitive 3: Reasoning Loop — the core execution engine.

Defines how the agent calls the model, its loop circuit-breakers, cost
controls, and in-loop compaction.

Dependency note: Primitive 3's YAML in the design lists ``[identity, memory]``,
but the design's own dependency graph (the "Primitive Dependency Graph"
section) places reasoning_loop and memory as *siblings* under identity, and the
selection matrix makes memory required only when ``long_lived: true``. Treating
memory as a hard dependency would force every agent to carry memory, which
contradicts the matrix. We therefore follow the graph + matrix: reasoning_loop
depends on identity only, and uses memory opportunistically when present.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .base import PrimitiveTemplate


class ModelConfig(BaseModel):
    """Which model the reasoning loop calls."""

    provider: Literal["anthropic", "openai", "google", "bedrock", "custom"] = "anthropic"
    model_id: str = "claude-opus-4-8"
    temperature: float = 0.2
    max_tokens: int = 8192


class LoopConfig(BaseModel):
    """Circuit breakers for the agentic loop."""

    max_iterations: int = 40
    tool_call_limit: int = 80
    parallel_tool_calls: bool = True


class CostControlsConfig(BaseModel):
    """Per-run spend cap. Must stay within the permission cost policy
    (composition invariant 8)."""

    max_cost_per_run_usd: float = 2.0
    warn_at_pct: int = 80


class CompactionConfig(BaseModel):
    """In-loop context compaction when the window fills up."""

    strategy: Literal["none", "summarize", "trim"] = "summarize"
    trigger_at_context_pct: int = 80


class ReasoningLoopConfig(BaseModel):
    """Full config schema for the reasoning_loop primitive (design Primitive 3)."""

    model: ModelConfig = Field(default_factory=ModelConfig)
    loop: LoopConfig = Field(default_factory=LoopConfig)
    cost_controls: CostControlsConfig = Field(default_factory=CostControlsConfig)
    compaction: CompactionConfig = Field(default_factory=CompactionConfig)


TEMPLATE = PrimitiveTemplate(
    name="reasoning_loop",
    version="1.0",
    config_model=ReasoningLoopConfig,
    dependencies=("identity",),  # see module docstring: memory is optional, not hard
    runtime_contract=(
        "run(task, context): RunResult",
        "RunResult.output: string",
        "RunResult.tool_calls: list",
        "RunResult.tokens_used: int",
        "RunResult.cost_usd: float",
        "RunResult.stop_reason: enum[done, budget_exceeded, "
        "iteration_limit, tool_error, human_required]",
    ),
)
