"""Tests for the real backend adapters.

Adapters whose package + offline operation are available here (SurrealDB) are
tested for real. The rest are gated with ``importorskip`` (skipped when the
optional package is not installed) and, for the API-key-dependent ones, further
gated on the relevant environment variable. The module itself must import with
no heavy package installed (lazy imports).
"""

import os
import tempfile

import pytest

from agentwright.runtime import real_backends as rb
from agentwright.runtime.interfaces import (
    BrowserPool,
    GraphStore,
    LLMGateway,
    PreferenceStore,
    UniversalStore,
    VectorStore,
)


def test_module_imports_without_heavy_packages():
    # Every adapter class is importable even though chromadb/ladybugdb/mem0/
    # playwright are not installed — imports are lazy inside the adapters.
    for name in ("SurrealUniversalStore", "ChromaVectorStore", "LadybugGraphStore",
                 "AnthropicGateway", "Mem0PreferenceStore", "PlaywrightBrowserPool"):
        assert hasattr(rb, name)


def test_adapters_declare_protocol_methods():
    # Structural conformance without constructing (some need packages/keys).
    assert hasattr(rb.ChromaVectorStore, "query")
    assert hasattr(rb.LadybugGraphStore, "neighbors")
    assert hasattr(rb.AnthropicGateway, "complete")
    assert hasattr(rb.Mem0PreferenceStore, "search")
    assert hasattr(rb.PlaywrightBrowserPool, "browse")


# --- SurrealDB: real, offline ------------------------------------------------


def test_surreal_universal_store_real():
    pytest.importorskip("surrealdb")
    store = rb.SurrealUniversalStore()  # in-memory mem://
    assert isinstance(store, UniversalStore)

    store.create("a", "processed_leads", {"email": "bob@acme.com"})
    assert store.select("a", "processed_leads")[0]["email"] == "bob@acme.com"

    store.write_state("a", "cursor", 42)
    assert store.read_state("a", "cursor") == 42
    store.write_state("a", "cursor", 99)  # overwrite
    assert store.read_state("a", "cursor") == 99


def test_surreal_isolates_agents():
    pytest.importorskip("surrealdb")
    store = rb.SurrealUniversalStore()
    store.write_state("a", "k", "a-val")
    assert store.read_state("b", "k") is None
    assert store.select("b", "processed_leads") == []


def test_surreal_swaps_into_runtime():
    pytest.importorskip("surrealdb")
    from agentwright import DomainBrief, compose
    from agentwright.runtime import AgentRuntime

    rt = AgentRuntime(tempfile.mkdtemp(), universal_store=rb.SurrealUniversalStore())
    d = compose(DomainBrief(name="A", goal="g", domain="d", long_lived=True), "u", "u")
    agent_id = rt.instantiate(d)
    rt.universal_store.create(agent_id, "notes", {"text": "hello"})
    assert rt.universal_store.select(agent_id, "notes")[0]["text"] == "hello"
    rt.close()


# --- Chroma: skipped unless installed ---------------------------------------


def test_chroma_vector_store_real():
    pytest.importorskip("chromadb")
    store = rb.ChromaVectorStore()
    assert isinstance(store, VectorStore)
    store.upsert("a", "d1", "acme corp sales lead", {"kind": "lead"})
    store.upsert("a", "d2", "weather forecast", {"kind": "misc"})
    hits = store.query("a", "acme company", top_k=1)
    assert hits and hits[0].id == "d1"


# --- Anthropic: needs package (installed) + API key for a live call ----------


def test_anthropic_gateway_constructs_only_with_key():
    pytest.importorskip("anthropic")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set — gateway construction requires it")
    gw = rb.AnthropicGateway()
    assert isinstance(gw, LLMGateway)


# (live Anthropic completion is covered by test_anthropic_gateway_live below)


# --- provider gateways: routing (no network) + live (key-gated) --------------


def test_make_gateway_routes_by_provider(monkeypatch):
    monkeypatch.setattr(rb, "AnthropicGateway", lambda db=None: ("anthropic", db))
    monkeypatch.setattr(rb, "OpenAIGateway", lambda db=None: ("openai", db))
    monkeypatch.setattr(rb, "GoogleGateway", lambda db=None: ("google", db))
    assert rb.make_gateway("anthropic")[0] == "anthropic"
    assert rb.make_gateway("openai")[0] == "openai"
    assert rb.make_gateway("google")[0] == "google"
    assert rb.make_gateway("gemini")[0] == "google"


def test_make_gateway_unknown_provider_raises():
    with pytest.raises(ValueError):
        rb.make_gateway("bedrock")


def _live(provider_env, gateway_cls, model):
    """Shared live smoke: a tiny completion that should bill the ledger."""
    if not any(os.environ.get(k) for k in provider_env):
        pytest.skip(f"{'/'.join(provider_env)} not set — live call skipped")
    from agentwright.runtime.system_db import SystemDB

    db = SystemDB(":memory:")
    run_id = db.start_run("agt_live")
    res = gateway_cls(db).complete(
        [{"role": "user", "content": "Reply with exactly the word: OK"}],
        model, 16, 0.0, None, 1.0, run_id,
    )
    assert res.stop_reason in ("done", "tool_use")
    assert "OK" in res.content.upper()
    assert db.run_cost(run_id) == pytest.approx(res.cost_usd)


def test_anthropic_gateway_live():
    pytest.importorskip("anthropic")
    _live(["ANTHROPIC_API_KEY"], rb.AnthropicGateway, "claude-haiku-4-5-20251001")


def test_openai_gateway_live():
    pytest.importorskip("openai")
    _live(["OPENAI_API_KEY"], rb.OpenAIGateway, "gpt-4o-mini")


def test_google_gateway_live():
    pytest.importorskip("google.generativeai")
    _live(["GOOGLE_API_KEY", "GEMINI_API_KEY"], rb.GoogleGateway, "gemini-2.5-flash")


# --- Mem0 / LadybugDB / Playwright: skipped unless installed -----------------


def test_mem0_preference_store_available():
    pytest.importorskip("mem0")
    # A live add/search needs ANTHROPIC_API_KEY for synthesis; here just confirm
    # the adapter exposes the PreferenceStore surface.
    for method in ("add", "search", "get_all"):
        assert hasattr(rb.Mem0PreferenceStore, method)


def test_ladybug_graph_store_available():
    pytest.importorskip("ladybugdb")
    store = rb.LadybugGraphStore(tempfile.mkdtemp())
    assert isinstance(store, GraphStore)


def test_playwright_browser_pool_available():
    pytest.importorskip("playwright")
    # Construction launches Chromium (needs `playwright install chromium`); only
    # assert the class conforms structurally here.
    assert issubclass(rb.PlaywrightBrowserPool, object)
    assert hasattr(rb.PlaywrightBrowserPool, "browse")
