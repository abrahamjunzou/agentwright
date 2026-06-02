"""In-memory implementations of the heavy memory stores (Layer 0).

These satisfy the Tier-B protocols in ``interfaces.py`` with pure-Python,
dependency-free logic so the runtime is fully exercisable offline. They are
deliberately simple but behaviourally faithful: the vector store does real
cosine-similarity ranking over a deterministic hashing embedding, the graph
store does real bounded BFS traversal, etc. Swap in Chroma / LadybugDB /
SurrealDB / Mem0 later behind the same protocols.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from typing import Any

from .interfaces import Memory, SearchResult

_DIM = 64
_TOKEN = re.compile(r"[a-z0-9]+")


def _embed(text: str) -> list[float]:
    """Deterministic hashing bag-of-words embedding, L2-normalized.

    Not semantically rich, but stable and good enough for similarity ordering in
    tests and offline runs. Real deployments use sentence-transformers via
    Chroma; the interface and ranking semantics are identical.
    """
    vec = [0.0] * _DIM
    for tok in _TOKEN.findall(text.lower()):
        vec[hash(tok) % _DIM] += 1.0
    norm = math.sqrt(sum(x * x for x in vec))
    return [x / norm for x in vec] if norm else vec


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


class InMemoryVectorStore:
    """Cosine-similarity vector store over a hashing embedding."""

    def __init__(self) -> None:
        # agent_id -> doc_id -> (text, embedding, metadata)
        self._docs: dict[str, dict[str, tuple[str, list[float], dict]]] = defaultdict(dict)

    def upsert(self, agent_id: str, doc_id: str, text: str, metadata: dict) -> None:
        self._docs[agent_id][doc_id] = (text, _embed(text), metadata or {})

    def query(
        self, agent_id: str, query_text: str, top_k: int, filters: dict | None = None
    ) -> list[SearchResult]:
        qv = _embed(query_text)
        hits: list[SearchResult] = []
        for doc_id, (text, emb, meta) in self._docs[agent_id].items():
            if filters and any(meta.get(k) != v for k, v in filters.items()):
                continue
            hits.append(SearchResult(id=doc_id, text=text, score=_cosine(qv, emb), metadata=meta))
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:top_k]

    def delete(self, agent_id: str, doc_id: str) -> None:
        self._docs[agent_id].pop(doc_id, None)


class InMemoryGraphStore:
    """Adjacency-list graph with bounded BFS neighbor traversal."""

    def __init__(self) -> None:
        self._nodes: dict[str, dict[str, dict]] = defaultdict(dict)  # agent -> node_id -> props
        # agent -> from_id -> list[(rel, to_id, props)]
        self._edges: dict[str, dict[str, list[tuple[str, str, dict]]]] = defaultdict(
            lambda: defaultdict(list)
        )

    def upsert_node(self, agent_id: str, label: str, node_id: str, props: dict) -> None:
        self._nodes[agent_id][node_id] = {"label": label, **(props or {})}

    def upsert_edge(
        self, agent_id: str, rel: str, from_id: str, to_id: str, props: dict
    ) -> None:
        self._edges[agent_id][from_id].append((rel, to_id, props or {}))

    def neighbors(
        self, agent_id: str, node_id: str, rel_type: str | None, depth: int
    ) -> list[str]:
        """BFS up to ``depth`` hops, optionally filtering by relationship type."""
        seen: set[str] = set()
        frontier = [node_id]
        for _ in range(depth):
            nxt: list[str] = []
            for nid in frontier:
                for rel, to_id, _props in self._edges[agent_id].get(nid, []):
                    if rel_type is not None and rel != rel_type:
                        continue
                    if to_id not in seen:
                        seen.add(to_id)
                        nxt.append(to_id)
            frontier = nxt
        return list(seen)


class InMemoryUniversalStore:
    """Cross-model store: flexible tables + typed structured state."""

    def __init__(self) -> None:
        self._tables: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
        self._state: dict[str, dict[str, Any]] = defaultdict(dict)

    def create(self, agent_id: str, table: str, data: dict) -> dict:
        record = dict(data)
        self._tables[agent_id][table].append(record)
        return record

    def select(self, agent_id: str, table: str) -> list[dict]:
        return list(self._tables[agent_id][table])

    def write_state(self, agent_id: str, key: str, value: Any) -> None:
        self._state[agent_id][key] = value

    def read_state(self, agent_id: str, key: str) -> Any:
        return self._state[agent_id].get(key)


class InMemoryPreferenceStore:
    """Naive preference memory: stores facts, ranks by word overlap."""

    def __init__(self) -> None:
        self._mem: dict[str, list[Memory]] = defaultdict(list)
        self._counter = 0

    def add(self, agent_id: str, messages: list, metadata: dict | None = None) -> None:
        """Store each message's text as an atomic memory fact."""
        for msg in messages:
            text = msg.get("content") if isinstance(msg, dict) else str(msg)
            if not text:
                continue
            self._counter += 1
            self._mem[agent_id].append(
                Memory(id=f"mem_{self._counter}", memory=text, metadata=metadata or {})
            )

    def search(self, agent_id: str, query: str, limit: int) -> list[Memory]:
        qtokens = set(_TOKEN.findall(query.lower()))
        scored: list[Memory] = []
        for m in self._mem[agent_id]:
            overlap = len(qtokens & set(_TOKEN.findall(m.memory.lower())))
            if overlap:
                scored.append(Memory(id=m.id, memory=m.memory, score=float(overlap),
                                     metadata=m.metadata))
        scored.sort(key=lambda m: m.score, reverse=True)
        return scored[:limit]

    def get_all(self, agent_id: str) -> list[Memory]:
        return list(self._mem[agent_id])
