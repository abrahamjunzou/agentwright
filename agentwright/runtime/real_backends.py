"""Real backend adapters for the Tier-B protocols (Layer 0).

Each adapter implements the same protocol as its in-memory counterpart and
imports its third-party package **lazily** (inside ``__init__``/methods), so this
module imports cleanly even when none of the heavy packages are installed.
Install the relevant optional extra and pass the adapter to ``AgentRuntime(...)``
to swap a real backend in — nothing else changes.

Availability in this build:
- SurrealUniversalStore  — `surrealdb` installed; embedded `mem://` works offline.
- ChromaVectorStore      — needs `chromadb` (+ an embedding fn or model download).
- LadybugGraphStore      — needs `ladybugdb` (no PyPI wheel available yet).
- AnthropicGateway       — needs `anthropic` + ANTHROPIC_API_KEY at call time.
- Mem0PreferenceStore    — needs `mem0ai` + ANTHROPIC_API_KEY (LLM synthesis).
- PlaywrightBrowserPool  — needs `playwright` + `playwright install chromium`.
"""

from __future__ import annotations

import time

from .interfaces import CompletionResult, Memory, SearchResult
from .system_db import SystemDB

# Illustrative prices ($ per 1K tokens) per provider; (input, output). Unknown
# models fall back to (0, 0) — cost is recorded as 0 rather than failing.
_ANTHROPIC_PRICES = {
    "claude-opus-4-8": (0.015, 0.075),
    "claude-haiku-4-5-20251001": (0.001, 0.005),
}
_OPENAI_PRICES = {
    "gpt-4o-mini": (0.00015, 0.0006),
    "gpt-4o": (0.0025, 0.01),
}
_GOOGLE_PRICES = {
    "gemini-2.5-flash": (0.0003, 0.0025),
    "gemini-2.0-flash": (0.0001, 0.0004),
    "gemini-1.5-flash": (0.000075, 0.0003),
}


def _price(table: dict, model_id: str, in_tok: int, out_tok: int) -> float:
    """Compute cost from a provider price table; 0 for unknown models."""
    pi, po = table.get(model_id, (0.0, 0.0))
    return in_tok / 1000 * pi + out_tok / 1000 * po


# --- SurrealDB (universal store) -----------------------------------------


class SurrealUniversalStore:
    """UniversalStore backed by embedded SurrealDB (SurrealKV / in-memory).

    ``path=None`` uses an in-memory database; a path uses on-disk SurrealKV. Each
    agent gets its own database within the namespace for isolation.
    """

    def __init__(self, path: str | None = None, namespace: str = "agent_runtime") -> None:
        self._path = path
        self._ns = namespace
        self._conns: dict[str, object] = {}

    def _db(self, agent_id: str):
        if agent_id not in self._conns:
            from surrealdb import Surreal

            url = "mem://" if self._path is None else f"surrealkv://{self._path}/{agent_id}"
            db = Surreal(url)
            db.use(self._ns, agent_id)
            self._conns[agent_id] = db
        return self._conns[agent_id]

    def create(self, agent_id: str, table: str, data: dict) -> dict:
        res = self._db(agent_id).create(table, dict(data))
        return res[0] if isinstance(res, list) else res

    def select(self, agent_id: str, table: str) -> list[dict]:
        res = self._db(agent_id).select(table)
        if res is None:
            return []
        return res if isinstance(res, list) else [res]

    def write_state(self, agent_id: str, key: str, value) -> None:
        db = self._db(agent_id)
        for row in self.select(agent_id, "state"):
            if row.get("k") == key:
                db.delete(row["id"])
        db.create("state", {"k": key, "v": value})

    def read_state(self, agent_id: str, key: str):
        for row in self.select(agent_id, "state"):
            if row.get("k") == key:
                return row.get("v")
        return None


# --- Chroma (vector store) -----------------------------------------------


class ChromaVectorStore:
    """VectorStore backed by Chroma. One collection per agent.

    ``embedding_function`` is passed through to Chroma; if None, Chroma's default
    (an ONNX MiniLM that downloads ~80MB on first use) is used.
    """

    def __init__(self, persist_dir: str | None = None, embedding_function=None) -> None:
        import chromadb

        self._client = (
            chromadb.PersistentClient(path=persist_dir)
            if persist_dir
            else chromadb.EphemeralClient()
        )
        self._ef = embedding_function
        self._collections: dict[str, object] = {}

    def _coll(self, agent_id: str):
        if agent_id not in self._collections:
            self._collections[agent_id] = self._client.get_or_create_collection(
                name=f"agent_{agent_id}", embedding_function=self._ef
            )
        return self._collections[agent_id]

    def upsert(self, agent_id: str, doc_id: str, text: str, metadata: dict) -> None:
        self._coll(agent_id).upsert(
            ids=[doc_id], documents=[text], metadatas=[metadata or {"_": "_"}]
        )

    def query(
        self, agent_id: str, query_text: str, top_k: int, filters: dict | None = None
    ) -> list[SearchResult]:
        res = self._coll(agent_id).query(
            query_texts=[query_text], n_results=top_k, where=filters or None
        )
        out: list[SearchResult] = []
        ids = res.get("ids", [[]])[0]
        docs = res.get("documents", [[]])[0]
        dists = res.get("distances", [[]])[0]
        metas = res.get("metadatas", [[]])[0]
        for i, doc_id in enumerate(ids):
            # Chroma returns distance (lower = closer); convert to a similarity score.
            score = 1.0 - dists[i] if i < len(dists) else 0.0
            out.append(SearchResult(id=doc_id, text=docs[i], score=score, metadata=metas[i]))
        return out

    def delete(self, agent_id: str, doc_id: str) -> None:
        self._coll(agent_id).delete(ids=[doc_id])


# --- LadybugDB (graph store) ---------------------------------------------


class LadybugGraphStore:
    """GraphStore backed by LadybugDB (Cypher). One database per agent."""

    def __init__(self, db_dir: str) -> None:
        import ladybugdb  # noqa: F401 — validated lazily; raises if unavailable

        self._db_dir = db_dir
        self._dbs: dict[str, object] = {}

    def _conn(self, agent_id: str):
        if agent_id not in self._dbs:
            import ladybugdb

            db = ladybugdb.Database(f"{self._db_dir}/{agent_id}")
            self._dbs[agent_id] = ladybugdb.Connection(db)
        return self._dbs[agent_id]

    def upsert_node(self, agent_id: str, label: str, node_id: str, props: dict) -> None:
        conn = self._conn(agent_id)
        cols = ", ".join(f"{k}: ${k}" for k in {"id": node_id, **props})
        conn.execute(f"MERGE (n:{label} {{{cols}}})", {"id": node_id, **props})

    def upsert_edge(self, agent_id: str, rel: str, from_id: str, to_id: str, props: dict) -> None:
        conn = self._conn(agent_id)
        conn.execute(
            f"MATCH (a), (b) WHERE a.id=$f AND b.id=$t MERGE (a)-[:{rel}]->(b)",
            {"f": from_id, "t": to_id},
        )

    def neighbors(self, agent_id: str, node_id: str, rel_type: str | None, depth: int) -> list[str]:
        rel = f":{rel_type}" if rel_type else ""
        res = self._conn(agent_id).execute(
            f"MATCH (a)-[{rel}*1..{depth}]->(b) WHERE a.id=$id RETURN DISTINCT b.id",
            {"id": node_id},
        )
        return [row[0] for row in res]


# --- Anthropic (LLM gateway) ---------------------------------------------


class AnthropicGateway:
    """LLMGateway backed by the Anthropic Messages API.

    Enforces the per-run cost budget, retries on overload/rate-limit with
    exponential backoff, and records every call to the cost ledger. Requires the
    ``anthropic`` package and ``ANTHROPIC_API_KEY`` (read by the SDK from the
    environment).
    """

    def __init__(self, system_db: SystemDB | None = None, max_attempts: int = 3) -> None:
        import anthropic

        self._client = anthropic.Anthropic()
        self._errors = (anthropic.RateLimitError, anthropic.InternalServerError,
                        anthropic.APIStatusError)
        self._db = system_db
        self._max_attempts = max_attempts

    def complete(self, messages, model_id, max_tokens, temperature, tools,
                 cost_budget_usd, run_id) -> CompletionResult:
        if cost_budget_usd <= 0:
            return CompletionResult(content="", stop_reason="budget_exceeded")

        system = "\n".join(m["content"] for m in messages if m.get("role") == "system")
        convo = [m for m in messages if m.get("role") in ("user", "assistant", "tool")]
        kwargs = dict(model=model_id, max_tokens=max_tokens, temperature=temperature,
                      messages=convo)
        if system:  # omit entirely when empty — the API rejects system=None
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = [
                {"name": t["name"], "description": t.get("description", ""),
                 "input_schema": t.get("input_schema") or {"type": "object"}}
                for t in tools
            ]

        resp = self._with_retries(lambda: self._client.messages.create(**kwargs))

        text_parts, tool_calls = [], []
        for block in resp.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append({"id": block.id, "name": block.name, "input": block.input})

        in_tok, out_tok = resp.usage.input_tokens, resp.usage.output_tokens
        pi, po = _ANTHROPIC_PRICES.get(model_id, (0.0, 0.0))
        cost = in_tok / 1000 * pi + out_tok / 1000 * po

        if cost > cost_budget_usd:
            return CompletionResult(content="", input_tokens=in_tok, output_tokens=out_tok,
                                    stop_reason="budget_exceeded")

        if self._db is not None:
            run = self._db.get_run(run_id)
            if run is not None:
                self._db.record_cost(run["agent_id"], run_id, "anthropic", model_id,
                                     in_tok, out_tok, cost)

        stop = "done" if resp.stop_reason != "tool_use" else "tool_use"
        return CompletionResult(
            content="".join(text_parts), tool_calls=tool_calls, input_tokens=in_tok,
            output_tokens=out_tok, cost_usd=cost, stop_reason=stop,
        )

    def _with_retries(self, fn):
        for attempt in range(self._max_attempts):
            try:
                return fn()
            except self._errors:
                if attempt == self._max_attempts - 1:
                    raise
                time.sleep(2 ** attempt)


# --- OpenAI (LLM gateway) ------------------------------------------------


class OpenAIGateway:
    """LLMGateway backed by the OpenAI Chat Completions API.

    Same contract as the Anthropic gateway: budget enforcement, retries, cost
    ledger. Requires ``openai`` and ``OPENAI_API_KEY``. Supports function/tool
    calling; tool-result messages from the engine are folded into user turns so
    the conversation stays valid without replaying tool_call ids.
    """

    def __init__(self, system_db: SystemDB | None = None, max_attempts: int = 3) -> None:
        import openai

        self._client = openai.OpenAI()
        self._errors = (openai.RateLimitError, openai.InternalServerError,
                        openai.APIStatusError)
        self._db = system_db
        self._max_attempts = max_attempts

    def complete(self, messages, model_id, max_tokens, temperature, tools,
                 cost_budget_usd, run_id) -> CompletionResult:
        if cost_budget_usd <= 0:
            return CompletionResult(content="", stop_reason="budget_exceeded")

        oai_messages = []
        for m in messages:
            role = m.get("role")
            if role == "tool":
                oai_messages.append({"role": "user", "content": "Tool result: " + m.get("content", "")})
            else:
                oai_messages.append({"role": role, "content": m.get("content", "")})

        kwargs = dict(model=model_id, messages=oai_messages,
                      max_completion_tokens=max_tokens, temperature=temperature)
        if tools:
            kwargs["tools"] = [
                {"type": "function",
                 "function": {"name": t["name"], "description": t.get("description", ""),
                              "parameters": t.get("input_schema") or {"type": "object"}}}
                for t in tools
            ]

        resp = self._with_retries(lambda: self._client.chat.completions.create(**kwargs))
        choice = resp.choices[0].message

        tool_calls = []
        for tc in (choice.tool_calls or []):
            import json as _json
            tool_calls.append({"id": tc.id, "name": tc.function.name,
                               "input": _json.loads(tc.function.arguments or "{}")})

        in_tok, out_tok = resp.usage.prompt_tokens, resp.usage.completion_tokens
        cost = _price(_OPENAI_PRICES, model_id, in_tok, out_tok)
        if cost > cost_budget_usd:
            return CompletionResult(content="", input_tokens=in_tok, output_tokens=out_tok,
                                    stop_reason="budget_exceeded")
        if self._db is not None:
            run = self._db.get_run(run_id)
            if run is not None:
                self._db.record_cost(run["agent_id"], run_id, "openai", model_id,
                                     in_tok, out_tok, cost)

        stop = "tool_use" if tool_calls else "done"
        return CompletionResult(content=choice.content or "", tool_calls=tool_calls,
                                input_tokens=in_tok, output_tokens=out_tok, cost_usd=cost,
                                stop_reason=stop)

    def _with_retries(self, fn):
        for attempt in range(self._max_attempts):
            try:
                return fn()
            except self._errors:
                if attempt == self._max_attempts - 1:
                    raise
                time.sleep(2 ** attempt)


# --- Google Gemini (LLM gateway) -----------------------------------------


class GoogleGateway:
    """LLMGateway backed by Google Gemini (``google-generativeai``).

    Text completion with budget enforcement and cost ledger. Requires
    ``google-generativeai`` and ``GOOGLE_API_KEY`` (or ``GEMINI_API_KEY``).
    Tool-calling is not mapped here — Gemini returns text, which the engine
    treats as a final answer.
    """

    def __init__(self, system_db: SystemDB | None = None) -> None:
        import os

        import google.generativeai as genai

        genai.configure(api_key=os.environ.get("GOOGLE_API_KEY")
                        or os.environ.get("GEMINI_API_KEY"))
        self._genai = genai
        self._db = system_db

    def complete(self, messages, model_id, max_tokens, temperature, tools,
                 cost_budget_usd, run_id) -> CompletionResult:
        if cost_budget_usd <= 0:
            return CompletionResult(content="", stop_reason="budget_exceeded")

        system = "\n".join(m["content"] for m in messages if m.get("role") == "system")
        prompt = "\n".join(f"{m['role']}: {m.get('content', '')}"
                           for m in messages if m.get("role") != "system")
        model = self._genai.GenerativeModel(model_id, system_instruction=system or None)
        resp = model.generate_content(
            prompt,
            generation_config={"max_output_tokens": max_tokens, "temperature": temperature},
        )

        usage = getattr(resp, "usage_metadata", None)
        in_tok = getattr(usage, "prompt_token_count", 0) if usage else 0
        out_tok = getattr(usage, "candidates_token_count", 0) if usage else 0
        cost = _price(_GOOGLE_PRICES, model_id, in_tok, out_tok)
        if cost > cost_budget_usd:
            return CompletionResult(content="", input_tokens=in_tok, output_tokens=out_tok,
                                    stop_reason="budget_exceeded")
        if self._db is not None:
            run = self._db.get_run(run_id)
            if run is not None:
                self._db.record_cost(run["agent_id"], run_id, "google", model_id,
                                     in_tok, out_tok, cost)

        return CompletionResult(content=resp.text, input_tokens=in_tok, output_tokens=out_tok,
                                cost_usd=cost, stop_reason="done")


def make_gateway(provider: str, system_db: SystemDB | None = None):
    """Return the LLM gateway adapter for a provider name (the reasoning_loop
    primitive's ``model.provider``). Raises ValueError for unknown providers."""
    if provider == "anthropic":
        return AnthropicGateway(system_db)
    if provider == "openai":
        return OpenAIGateway(system_db)
    if provider in ("google", "gemini"):
        return GoogleGateway(system_db)
    raise ValueError(f"no gateway adapter for provider {provider!r}")


# --- Mem0 (preference store) ---------------------------------------------


class Mem0PreferenceStore:
    """PreferenceStore backed by Mem0 (LLM-synthesized memories).

    Requires ``mem0ai`` and an LLM for synthesis (Anthropic Haiku by default,
    needing ANTHROPIC_API_KEY). ``add`` invokes the LLM, so callers should
    dispatch it off the critical path.
    """

    def __init__(self, config: dict | None = None) -> None:
        from mem0 import Memory as Mem0Memory

        self._client = Mem0Memory.from_config(config) if config else Mem0Memory()

    def add(self, agent_id: str, messages: list, metadata: dict | None = None) -> None:
        self._client.add(messages, agent_id=agent_id, metadata=metadata)

    def search(self, agent_id: str, query: str, limit: int) -> list[Memory]:
        res = self._client.search(query, agent_id=agent_id, limit=limit)
        items = res.get("results", res) if isinstance(res, dict) else res
        return [
            Memory(id=m.get("id", ""), memory=m.get("memory", ""), score=m.get("score", 0.0),
                   metadata=m.get("metadata", {}))
            for m in items
        ]

    def get_all(self, agent_id: str) -> list[Memory]:
        res = self._client.get_all(agent_id=agent_id)
        items = res.get("results", res) if isinstance(res, dict) else res
        return [Memory(id=m.get("id", ""), memory=m.get("memory", "")) for m in items]


# --- Playwright (browser pool) -------------------------------------------


class PlaywrightBrowserPool:
    """BrowserPool backed by Playwright + Chromium, one context per agent.

    Requires ``playwright`` and ``playwright install chromium``. Screenshots are
    written to the File Store and referenced by path, never inlined.
    """

    def __init__(self, file_store, profiles_dir: str, headless: bool = True) -> None:
        from playwright.sync_api import sync_playwright

        self._fs = file_store
        self._profiles_dir = profiles_dir
        self._headless = headless
        self._pw = sync_playwright().start()
        self._contexts: dict[str, object] = {}
        self._counter = 0

    def _page(self, agent_id: str):
        if agent_id not in self._contexts:
            ctx = self._pw.chromium.launch_persistent_context(
                f"{self._profiles_dir}/{agent_id}", headless=self._headless
            )
            self._contexts[agent_id] = ctx
        ctx = self._contexts[agent_id]
        return ctx.pages[0] if ctx.pages else ctx.new_page()

    def browse(self, agent_id: str, url: str) -> dict:
        page = self._page(agent_id)
        resp = page.goto(url)
        return {"url": url, "status": resp.status if resp else None,
                "title": page.title(), "text": page.inner_text("body")}

    def screenshot(self, agent_id: str) -> str:
        self._counter += 1
        rel = f"workspace/screenshot_{self._counter}.png"
        # Write under the agent's workspace via the page, then return the path.
        self._page(agent_id).screenshot(path=str(rel))
        return rel
