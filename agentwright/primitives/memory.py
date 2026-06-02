"""Primitive 2: Memory — what the agent knows across runs.

Config mirrors the v2.0 memory schema shared by both design docs: short-term
window, long-term store toggles (TinyDB / Chroma / LadybugDB / SurrealDB),
KV stores (LMDB + Mem0), structured state, and search tuning.

This layer only models and validates the config. The runtime infrastructure
layer is what actually backs each toggle with a store. Invariant 7 (structured
state requires the universal SurrealDB store) is enforced by the validator.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .base import PrimitiveTemplate


class ShortTermConfig(BaseModel):
    """Recent-turn context window included raw in the prompt."""

    window_turns: int = 20
    include_tool_outputs: bool = True


class LongTermConfig(BaseModel):
    """Which long-term stores this agent enables.

    universal_store (SurrealDB) is recommended always-true because it
    consolidates the other stores for cross-model queries.
    """

    document_store: bool = False  # TinyDB
    vector_store: bool = False  # Chroma
    graph_store: bool = False  # LadybugDB
    universal_store: bool = True  # SurrealDB
    compaction_strategy: Literal["summarize", "discard_oldest", "hierarchical"] = "summarize"
    max_storage_mb: int = 1024


class LmdbConfig(BaseModel):
    """Bare-metal KV for hot-path tracing and dedup. map_size is fixed at open."""

    map_size_mb: int = 512  # 512 for most agents, 2048 for computer-use
    trace_tool_calls: bool = True
    trace_run_state: bool = True


class Mem0Config(BaseModel):
    """LLM-synthesized preference store. Cheap model recommended."""

    enabled: bool = False
    llm_model: str = "claude-haiku-4-5-20251001"
    context_decay_days: int | None = None  # null = no decay


class KvConfig(BaseModel):
    """Key-value layer: LMDB (always available) and optional Mem0."""

    lmdb: LmdbConfig = Field(default_factory=LmdbConfig)
    mem0: Mem0Config = Field(default_factory=Mem0Config)


class StructuredStateField(BaseModel):
    """One typed field in a SurrealDB SCHEMAFULL structured-state table."""

    name: str
    type: Literal["string", "int", "bool", "json", "timestamp"]
    description: str = ""


class StructuredStateConfig(BaseModel):
    """Typed cross-run state, backed by SurrealDB SCHEMAFULL tables.

    The schema field is exposed under the design's YAML key ``schema`` via an
    alias, while the Python attribute is ``state_schema`` to avoid shadowing
    pydantic's reserved ``schema`` attribute.
    """

    model_config = ConfigDict(populate_by_name=True)

    enabled: bool = False
    state_schema: list[StructuredStateField] = Field(default_factory=list, alias="schema")


class SearchConfig(BaseModel):
    """Tuning for semantic (Chroma) and graph (LadybugDB) recall."""

    semantic_top_k: int = 5
    graph_hop_depth: int = 3


class MemoryConfig(BaseModel):
    """Full config schema for the memory primitive (design Primitive 2)."""

    short_term: ShortTermConfig = Field(default_factory=ShortTermConfig)
    long_term: LongTermConfig = Field(default_factory=LongTermConfig)
    kv: KvConfig = Field(default_factory=KvConfig)
    structured_state: StructuredStateConfig = Field(default_factory=StructuredStateConfig)
    search: SearchConfig = Field(default_factory=SearchConfig)


TEMPLATE = PrimitiveTemplate(
    name="memory",
    version="2.0",
    config_model=MemoryConfig,
    dependencies=("identity",),
    runtime_contract=(
        "context_snapshot: object",
        "recall(query): list",
        "graph_query(cypher): list",
        "surql(query, params): any",
        "write_state(key, value)",
        "read_state(key): any",
        "kv_put(key, value)",
        "kv_get(key): bytes",
        "mem_add(messages)",
        "mem_search(query): list",
        "append_artifact(path, content)",
        "load_artifact(path): string",
    ),
)
