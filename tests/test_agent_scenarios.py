"""Agent-archetype scenario tests (Layer 2 -> Layer 0).

Each test builds a realistic agent from a domain brief, instantiates it on the
runtime, and drives it end to end. Collectively the archetypes cover all nine
primitives and every pattern: each trigger type (schedule/webhook/email/slack/
event/manual), each compute backend (shell/browser), generated_ui, sub-agents,
the full memory stack (document/vector/graph/universal/structured-state), the
permission approval flow (granted/rejected/timeout), multi-tool multi-iteration
reasoning, and concurrent agents.

A ``ScriptedGateway`` (implements the LLMGateway protocol) replays programmed
completions so the whole loop runs deterministically and offline.
"""

import asyncio

import pytest

from agentwright import BriefConstraints, DomainBrief, compose
from agentwright.primitives.permission import ActionOverride
from agentwright.runtime import AgentRuntime, ReasoningEngine
from agentwright.runtime.interfaces import CompletionResult


# --- helpers -------------------------------------------------------------


class ScriptedGateway:
    """Replays a fixed list of completions, billing the ledger for each."""

    def __init__(self, system_db, script):
        self._db = system_db
        self._script = list(script)
        self._i = 0

    def complete(self, messages, model_id, max_tokens, temperature, tools,
                 cost_budget_usd, run_id):
        res = self._script[min(self._i, len(self._script) - 1)]
        self._i += 1
        if self._db is not None and res.cost_usd > 0:
            run = self._db.get_run(run_id)
            if run is not None:
                self._db.record_cost(run["agent_id"], run_id, "scripted", model_id,
                                     res.input_tokens, res.output_tokens, res.cost_usd)
        return res


def _say(content, cost=0.001):
    return CompletionResult(content=content, cost_usd=cost, input_tokens=8, output_tokens=4)


def _call(*names, cost=0.001):
    return CompletionResult(
        content="",
        tool_calls=[{"id": f"c_{n}", "name": n, "input": {}} for n in names],
        cost_usd=cost,
    )


def _allow(definition):
    definition.primitives.permission.action_policy.default = "allow"
    return definition


# --- Archetype 1: scheduled sales reviewer (memory+tools+trigger+UI) ------


def test_scheduled_sales_reviewer(tmp_path):
    brief = DomainBrief(
        name="Weekly Sales Pipeline Reviewer",
        goal="every Monday summarize the pipeline and post a report",
        domain="sales",
        constraints=BriefConstraints(requires_human_approval_for=["crm.delete_contact"]),
        available_connections=["gmail", "hubspot", "slack"],
        triggers_needed=["schedule"],
        ui_output_needed=True,
        long_lived=True,
    )
    d = _allow(compose(brief, "usr_1", "usr_1"))
    assert d.status == "validated", d.validation_errors
    # 8 of 9 primitives (no compute).
    assert d.primitives.present_names() == {
        "identity", "memory", "reasoning_loop", "tool_connection",
        "trigger", "generated_ui", "permission", "observability",
    }

    rt = AgentRuntime(tmp_path)
    rt.instantiate(d)
    assert rt.scheduler.list_jobs(d.agent_id)  # schedule trigger -> job

    rt.llm_gateway = ScriptedGateway(rt.system_db, [
        _call("hubspot.read_pipeline"),
        _say("Pipeline: 12 open deals, $340k."),
    ])
    res = asyncio.run(ReasoningEngine(rt).run(
        d, "summarize the pipeline", tools={"hubspot.read_pipeline": lambda i: {"deals": 12}}))
    assert res.stop_reason == "done" and "12 open deals" in res.output
    assert rt.system_db.list_actions(res.run_id)[0]["outcome"] == "success"
    rt.close()


# --- Archetype 2: web research agent (compute: shell + browser) ----------


def test_web_research_agent_uses_compute(tmp_path):
    brief = DomainBrief(
        name="Web Research Agent",
        goal="research a topic across the web and write a brief",
        domain="research",
        available_connections=["search_api"],
        available_compute=["shell", "browser"],
        triggers_needed=["manual"],
        long_lived=True,
    )
    d = _allow(compose(brief, "usr_1", "usr_1"))
    assert d.primitives.compute.shell.enabled and d.primitives.compute.browser.enabled
    # Invariant 9 satisfied by construction.
    assert d.primitives.observability.capture.browser_actions is True

    rt = AgentRuntime(tmp_path)
    agent_id = rt.instantiate(d)

    # Real shell + (stub) browser usage.
    shell = rt.shell_runner.exec("echo researching", tmp_path / "ws")
    assert shell.exit_code == 0 and "researching" in shell.stdout
    page = rt.browser_pool.browse(agent_id, "https://example.com")
    assert page["status"] == 200

    rt.llm_gateway = ScriptedGateway(rt.system_db, [
        _call("browse"), _say("Found 3 relevant sources."),
    ])
    res = asyncio.run(ReasoningEngine(rt).run(
        d, "research embedded databases",
        tools={"browse": lambda i: rt.browser_pool.browse(agent_id, "https://x.com")}))
    assert res.stop_reason == "done"
    assert rt.system_db.list_actions(res.run_id)[0]["tool_name"] == "browse"
    rt.close()


# --- Archetype 3: email + slack monitor (channel triggers) ---------------


def test_email_slack_monitor_agent(tmp_path):
    brief = DomainBrief(
        name="Support Inbox Monitor",
        goal="watch the support inbox and channel and triage",
        domain="support",
        available_connections=["gmail", "slack"],
        triggers_needed=["email", "slack"],
        long_lived=True,
    )
    d = compose(brief, "usr_1", "usr_1")
    assert d.status == "validated", d.validation_errors  # invariant 11 satisfied
    trigger_types = {t.type for t in d.primitives.trigger.triggers}
    assert trigger_types == {"email", "slack"}

    rt = AgentRuntime(tmp_path)
    rt.instantiate(d)
    # email/slack triggers are polled (not scheduler jobs) — none enqueued.
    assert rt.scheduler.list_jobs(d.agent_id) == []
    rt.close()


# --- Archetype 4: webhook + event driven agent ---------------------------


def test_webhook_and_event_agent(tmp_path):
    brief = DomainBrief(
        name="CI Notifier",
        goal="react to CI webhooks and internal events",
        domain="engineering",
        available_connections=["github"],
        triggers_needed=["webhook", "event"],
    )
    d = compose(brief, "usr_1", "usr_1")
    assert d.status == "validated", d.validation_errors
    types = {t.type for t in d.primitives.trigger.triggers}
    assert types == {"webhook", "event"}
    rt = AgentRuntime(tmp_path)
    rt.instantiate(d)
    rt.close()


# --- Archetype 5: approval-gated finance agent (approve/reject/timeout) ---


def _finance_agent(tmp_path):
    brief = DomainBrief(
        name="Payments Agent",
        goal="process vendor payments",
        domain="finance",
        constraints=BriefConstraints(
            requires_human_approval_for=["payment.send"], pii_handling="deny"
        ),
        available_connections=["ledger"],
        long_lived=True,
    )
    d = compose(brief, "usr_1", "usr_1")
    assert d.primitives.permission.data_policy.pii_handling == "deny"
    rt = AgentRuntime(tmp_path)
    rt.instantiate(d)
    rt.llm_gateway = ScriptedGateway(rt.system_db, [
        _call("payment.send"), _say("Payment processed."),
    ])
    return d, rt


def test_finance_agent_approval_granted(tmp_path):
    d, rt = _finance_agent(tmp_path)

    async def go():
        async def approver():
            req = await rt.event_bus.consume("approval.requested", timeout=1)
            assert req["action"] == "payment.send"
            await rt.event_bus.publish("approval.resolved",
                                       {"approval_id": req["approval_id"], "decision": "approved"})
        _, res = await asyncio.gather(
            approver(),
            ReasoningEngine(rt).run(d, "pay vendor", tools={"payment.send": lambda i: {"ok": True}}),
        )
        return res

    res = asyncio.run(go())
    assert res.stop_reason == "done"
    assert rt.system_db.list_actions(res.run_id)[0]["outcome"] == "success"
    rt.close()


def test_finance_agent_approval_rejected_halts(tmp_path):
    d, rt = _finance_agent(tmp_path)

    async def go():
        async def approver():
            req = await rt.event_bus.consume("approval.requested", timeout=1)
            await rt.event_bus.publish("approval.resolved",
                                       {"approval_id": req["approval_id"], "decision": "rejected"})
        _, res = await asyncio.gather(
            approver(),
            ReasoningEngine(rt).run(d, "pay vendor", tools={"payment.send": lambda i: {"ok": True}}),
        )
        return res

    res = asyncio.run(go())
    assert res.stop_reason == "human_required"  # human declined -> run halts
    rt.close()


def test_finance_agent_approval_timeout_halts(tmp_path):
    d, rt = _finance_agent(tmp_path)
    res = asyncio.run(ReasoningEngine(rt).run(
        d, "pay vendor", tools={"payment.send": lambda i: {}}, approval_timeout=0.05))
    assert res.stop_reason == "human_required"
    rt.close()


# --- Archetype 6: knowledge agent (full memory stack) --------------------


def test_knowledge_agent_memory_stack(tmp_path):
    pytest.importorskip("surrealdb")
    from agentwright.runtime.real_backends import SurrealUniversalStore

    brief = DomainBrief(name="Knowledge Base Agent", goal="answer questions from the KB",
                        domain="ops", long_lived=True)
    d = _allow(compose(brief, "usr_1", "usr_1"))
    # Enable the richer memory stores (an LLM configurator would infer these).
    d.primitives.memory.long_term.graph_store = True
    d.primitives.memory.structured_state.enabled = True
    from agentwright.primitives.memory import StructuredStateField
    d.primitives.memory.structured_state.state_schema = [
        StructuredStateField(name="indexed_docs", type="json")
    ]

    rt = AgentRuntime(tmp_path, universal_store=SurrealUniversalStore())
    agent_id = rt.instantiate(d)

    # Vector recall, graph traversal, structured state via the universal store.
    rt.vector_store.upsert(agent_id, "kb1", "embedded databases run in-process", {})
    rt.graph_store.upsert_node(agent_id, "Topic", "edb", {"name": "embedded db"})
    rt.graph_store.upsert_node(agent_id, "Topic", "lmdb", {"name": "lmdb"})
    rt.graph_store.upsert_edge(agent_id, "RELATED_TO", "edb", "lmdb", {})
    rt.universal_store.write_state(agent_id, "indexed_docs", ["kb1"])

    assert rt.graph_store.neighbors(agent_id, "edb", None, 1) == ["lmdb"]
    assert rt.universal_store.read_state(agent_id, "indexed_docs") == ["kb1"]

    rt.llm_gateway = ScriptedGateway(rt.system_db, [_say("Embedded DBs run in-process.")])
    res = asyncio.run(ReasoningEngine(rt).run(d, "what are embedded databases"))
    assert res.stop_reason == "done"
    # The recalled vector doc was injected into context (transcript shows it).
    transcript = rt.file_store.read(agent_id, f"history/{res.run_id}.md")
    assert "in-process" in transcript
    rt.close()


# --- Archetype 7: sub-agent (activated by a parent, no trigger) ----------


def test_sub_agent_has_no_trigger_and_runs(tmp_path):
    brief = DomainBrief(
        name="Enrichment Sub-Agent",
        goal="enrich a single lead when asked by the parent",
        domain="sales",
        available_connections=["clearbit"],
        long_lived=True,
        sub_agent=True,
    )
    d = _allow(compose(brief, "usr_1", "usr_1"))
    assert d.status == "validated", d.validation_errors
    assert d.primitives.trigger is None  # sub-agents are parent-activated

    rt = AgentRuntime(tmp_path)
    rt.instantiate(d)
    rt.llm_gateway = ScriptedGateway(rt.system_db, [_say("Lead enriched.")])
    # The "parent" activates it with a direct run.
    res = asyncio.run(ReasoningEngine(rt).run(d, "enrich bob@acme.com"))
    assert res.stop_reason == "done"
    rt.close()


# --- Archetype 8: minimal manual agent (only the 4 required primitives) ---


def test_minimal_manual_agent(tmp_path):
    brief = DomainBrief(name="One-shot Helper", goal="answer a single question",
                        domain="general", triggers_needed=["manual"])
    d = _allow(compose(brief, "usr_1", "usr_1"))
    assert d.primitives.present_names() == {
        "identity", "reasoning_loop", "permission", "observability", "trigger",
    }
    assert d.primitives.trigger.triggers[0].type == "manual"

    rt = AgentRuntime(tmp_path)
    rt.instantiate(d)
    rt.llm_gateway = ScriptedGateway(rt.system_db, [_say("42.")])
    res = asyncio.run(ReasoningEngine(rt).run(d, "what is the answer"))
    assert res.stop_reason == "done" and res.output == "42."
    rt.close()


# --- Archetype 9: multi-tool, multi-iteration reasoning ------------------


def test_multi_tool_multi_iteration_run(tmp_path):
    brief = DomainBrief(name="Research Pipeline", goal="search, fetch, summarize",
                        domain="research", available_connections=["api"], long_lived=True)
    d = _allow(compose(brief, "usr_1", "usr_1"))
    rt = AgentRuntime(tmp_path)
    rt.instantiate(d)
    rt.llm_gateway = ScriptedGateway(rt.system_db, [
        _call("search", "fetch"),   # turn 1: two tools in parallel
        _call("summarize"),          # turn 2: one tool
        _say("Final synthesis ready."),  # turn 3: done
    ])
    calls = {n: (lambda i, n=n: {"tool": n}) for n in ("search", "fetch", "summarize")}
    res = asyncio.run(ReasoningEngine(rt).run(d, "run the pipeline", tools=calls))
    assert res.stop_reason == "done"
    assert len(res.tool_calls) == 3
    audit = rt.system_db.list_actions(res.run_id)
    assert {a["tool_name"] for a in audit} == {"search", "fetch", "summarize"}
    assert all(a["outcome"] == "success" for a in audit)
    rt.close()


# --- Archetype 10: concurrent agents on one runtime ----------------------


def test_concurrent_agents_isolated(tmp_path):
    rt = AgentRuntime(tmp_path)
    agents = []
    for i in range(3):
        d = _allow(compose(DomainBrief(name=f"Agent {i}", goal="work", domain="d",
                                       long_lived=True), "usr_1", "usr_1"))
        rt.instantiate(d)
        agents.append(d)
    rt.llm_gateway = ScriptedGateway(rt.system_db, [_say("done")])

    async def go():
        engine = ReasoningEngine(rt)
        return await asyncio.gather(*(engine.run(d, "do your task") for d in agents))

    results = asyncio.run(go())
    assert all(r.stop_reason == "done" for r in results)
    # Distinct runs, each recorded under its own agent.
    run_ids = {r.run_id for r in results}
    assert len(run_ids) == 3
    for d, r in zip(agents, results):
        assert rt.system_db.get_run(r.run_id)["agent_id"] == d.agent_id
    rt.close()


# --- Coverage matrix: the archetypes touch every primitive + pattern -----


def test_archetypes_cover_all_primitives_and_patterns():
    """Meta-assertion: across a representative brief set, every primitive and
    every trigger/compute pattern is exercised."""
    briefs = [
        DomainBrief(name="a", goal="g", domain="d", long_lived=True,
                    available_connections=["x"], available_compute=["shell", "browser"],
                    triggers_needed=["schedule", "webhook", "email", "slack", "event", "manual"],
                    ui_output_needed=True),
    ]
    from agentwright.orchestration.selector import select_primitives
    covered = set()
    for b in briefs:
        covered |= select_primitives(b)
    # All nine primitives reachable through the orchestration layer.
    assert covered == {
        "identity", "memory", "reasoning_loop", "tool_connection", "compute",
        "trigger", "generated_ui", "permission", "observability",
    }
    # All six trigger types and both supported compute backends are expressible.
    d = compose(briefs[0], "u", "u")
    assert {t.type for t in d.primitives.trigger.triggers} == {
        "schedule", "webhook", "email", "slack", "event", "manual"
    }
    assert d.primitives.compute.shell.enabled and d.primitives.compute.browser.enabled
