"""Service interfaces for the heavy Layer-0 stores and gateways.

Per the chosen strategy, the heavy services (vector, graph, universal, and
preference stores, the LLM gateway, and the browser pool) are defined here as
``Protocol`` interfaces. Working in-memory implementations live in
``memory_backends.py`` / ``llm_gateway.py`` / ``browser_pool.py`` so the whole
runtime is exercisable offline. Real backends (Chroma, SurrealDB, LadybugDB,
Mem0, Anthropic, Playwright) implement the same protocols and drop in unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class SearchResult:
    """A vector-store hit."""

    id: str
    text: str
    score: float
    metadata: dict = field(default_factory=dict)


@dataclass
class Memory:
    """A synthesized preference fact from the preference store."""

    id: str
    memory: str
    score: float = 0.0
    metadata: dict = field(default_factory=dict)


@dataclass
class CompletionResult:
    """The result of one LLM completion."""

    content: str
    tool_calls: list = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    cache_hit_tokens: int = 0
    stop_reason: str = "done"


@runtime_checkable
class VectorStore(Protocol):
    """Semantic store (Chroma in production)."""

    def upsert(self, agent_id: str, doc_id: str, text: str, metadata: dict) -> None: ...
    def query(
        self, agent_id: str, query_text: str, top_k: int, filters: dict | None = None
    ) -> list[SearchResult]: ...
    def delete(self, agent_id: str, doc_id: str) -> None: ...


@runtime_checkable
class GraphStore(Protocol):
    """Entity/relationship graph (LadybugDB in production)."""

    def upsert_node(self, agent_id: str, label: str, node_id: str, props: dict) -> None: ...
    def upsert_edge(
        self, agent_id: str, rel: str, from_id: str, to_id: str, props: dict
    ) -> None: ...
    def neighbors(
        self, agent_id: str, node_id: str, rel_type: str | None, depth: int
    ) -> list[str]: ...


@runtime_checkable
class UniversalStore(Protocol):
    """Cross-model consolidation + structured state (SurrealDB in production)."""

    def create(self, agent_id: str, table: str, data: dict) -> dict: ...
    def select(self, agent_id: str, table: str) -> list[dict]: ...
    def write_state(self, agent_id: str, key: str, value: Any) -> None: ...
    def read_state(self, agent_id: str, key: str) -> Any: ...


@runtime_checkable
class PreferenceStore(Protocol):
    """Synthesized preference memory (Mem0 in production)."""

    def add(self, agent_id: str, messages: list, metadata: dict | None = None) -> None: ...
    def search(self, agent_id: str, query: str, limit: int) -> list[Memory]: ...
    def get_all(self, agent_id: str) -> list[Memory]: ...


@runtime_checkable
class LLMGateway(Protocol):
    """Model provider gateway (Anthropic in production)."""

    def complete(
        self,
        messages: list,
        model_id: str,
        max_tokens: int,
        temperature: float,
        tools: list | None,
        cost_budget_usd: float,
        run_id: str,
    ) -> CompletionResult: ...


@runtime_checkable
class BrowserPool(Protocol):
    """Per-agent browser contexts (Playwright in production)."""

    def browse(self, agent_id: str, url: str) -> dict: ...
    def screenshot(self, agent_id: str) -> str: ...
