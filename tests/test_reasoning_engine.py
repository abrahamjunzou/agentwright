"""Tests for the reasoning-loop execution engine (no API key required).

A ``ScriptedGateway`` implements the LLMGateway protocol and returns a
pre-programmed sequence of completions, so the full agentic loop — tool calls,
permission gating, approval flow, and every circuit breaker — is exercised
deterministically and offline.
"""

import asyncio

import pytest

from agentwright import DomainBrief, compose
from agentwright.primitives.permission import ActionOverride
from agentwright.runtime import AgentRuntime, ReasoningEngine
from agentwright.runtime.interfaces import CompletionResult


class ScriptedGateway:
    """LLMGateway that replays a fixed list of CompletionResults and bills the
    ledger for each, so budget accounting works like the real gateway."""

    def __init__(self, system_db, script: list[CompletionResult]) -> None:
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


def _agent(tmp_path, **brief_kwargs):
    base = dict(name="A", goal="follow the user's instructions", domain="d")
    base.update(brief_kwargs)
    d = compose(DomainBrief(**base), "usr_1", "usr_1")
    rt = AgentRuntime(tmp_path)
    rt.instantiate(d)
    return d, rt


def _tool_call(name, **inp):
    return {"id": f"call_{name}", "name": name, "input": inp}


# --- basic completion ----------------------------------------------------


def test_simple_run_no_tools(tmp_path):
    d, rt = _agent(tmp_path)
    rt.llm_gateway = ScriptedGateway(
        rt.system_db, [CompletionResult(content="the answer is 4", cost_usd=0.001,
                                        input_tokens=10, output_tokens=5)]
    )
    res = asyncio.run(ReasoningEngine(rt).run(d, "what is 2+2?"))
    assert res.stop_reason == "done"
    assert res.output == "the answer is 4"
    assert res.cost_usd == pytest.approx(0.001)
    assert res.tokens_used == 15
    assert rt.system_db.get_run(res.run_id)["status"] == "done"
    assert rt.file_store.exists(d.agent_id, f"history/{res.run_id}.md")


# --- tool execution ------------------------------------------------------


def test_tool_call_executes_and_audits(tmp_path):
    d, rt = _agent(tmp_path, available_connections=["api"])
    d.primitives.permission.action_policy.default = "allow"
    rt.llm_gateway = ScriptedGateway(rt.system_db, [
        CompletionResult(content="", tool_calls=[_tool_call("search", q="acme")], cost_usd=0.001),
        CompletionResult(content="found acme corp", cost_usd=0.001),
    ])
    tools = {"search": lambda inp: {"hits": [inp["q"]]}}
    res = asyncio.run(ReasoningEngine(rt).run(d, "find acme", tools=tools))
    assert res.stop_reason == "done"
    assert res.output == "found acme corp"
    assert len(res.tool_calls) == 1
    audit = rt.system_db.list_actions(res.run_id)
    assert audit[0]["tool_name"] == "search" and audit[0]["outcome"] == "success"


def test_tool_denied_by_policy(tmp_path):
    d, rt = _agent(tmp_path, available_connections=["api"])
    d.primitives.permission.action_policy.default = "allow"
    d.primitives.permission.action_policy.overrides = [
        ActionOverride(action_pattern="danger.*", policy="deny")
    ]
    rt.llm_gateway = ScriptedGateway(rt.system_db, [
        CompletionResult(content="", tool_calls=[_tool_call("danger.delete")], cost_usd=0.0),
        CompletionResult(content="stopped safely", cost_usd=0.0),
    ])
    res = asyncio.run(ReasoningEngine(rt).run(d, "delete everything", tools={}))
    assert res.stop_reason == "done"  # model recovers after the denial
    assert rt.system_db.list_actions(res.run_id)[0]["outcome"] == "denied"


def test_missing_executor_is_audited_failed(tmp_path):
    d, rt = _agent(tmp_path, available_connections=["api"])
    d.primitives.permission.action_policy.default = "allow"
    rt.llm_gateway = ScriptedGateway(rt.system_db, [
        CompletionResult(content="", tool_calls=[_tool_call("unknown_tool")], cost_usd=0.0),
        CompletionResult(content="done", cost_usd=0.0),
    ])
    res = asyncio.run(ReasoningEngine(rt).run(d, "go", tools={}))
    assert rt.system_db.list_actions(res.run_id)[0]["outcome"] == "failed"


def test_tool_executor_exception_is_audited_failed(tmp_path):
    # A tool that raises must be caught: the action is audited "failed" and the
    # error is fed back to the model rather than crashing the whole run.
    d, rt = _agent(tmp_path, available_connections=["api"])
    d.primitives.permission.action_policy.default = "allow"
    rt.llm_gateway = ScriptedGateway(rt.system_db, [
        CompletionResult(content="", tool_calls=[_tool_call("search", q="x")], cost_usd=0.0),
        CompletionResult(content="recovered", cost_usd=0.0),
    ])

    def boom(inp):
        raise RuntimeError("tool exploded")

    res = asyncio.run(ReasoningEngine(rt).run(d, "go", tools={"search": boom}))
    assert res.stop_reason == "done"  # the run survives the tool error
    assert res.output == "recovered"
    assert rt.system_db.list_actions(res.run_id)[0]["outcome"] == "failed"


# --- approval flow -------------------------------------------------------


def test_tool_requires_approval_granted(tmp_path):
    # The configurator's default policy is require_approval, so any tool call
    # escalates through the event bus.
    d, rt = _agent(tmp_path, available_connections=["api"])
    rt.llm_gateway = ScriptedGateway(rt.system_db, [
        CompletionResult(content="", tool_calls=[_tool_call("send")], cost_usd=0.0),
        CompletionResult(content="message sent", cost_usd=0.0),
    ])

    async def go():
        async def approver():
            req = await rt.event_bus.consume("approval.requested", timeout=1)
            await rt.event_bus.publish(
                "approval.resolved",
                {"approval_id": req["approval_id"], "decision": "approved"},
            )

        _, res = await asyncio.gather(
            approver(),
            ReasoningEngine(rt).run(d, "send it", tools={"send": lambda i: {"ok": True}}),
        )
        return res

    res = asyncio.run(go())
    assert res.stop_reason == "done"
    assert rt.system_db.list_actions(res.run_id)[0]["outcome"] == "success"


def test_tool_approval_timeout_halts_run(tmp_path):
    d, rt = _agent(tmp_path, available_connections=["api"])
    rt.llm_gateway = ScriptedGateway(rt.system_db, [
        CompletionResult(content="", tool_calls=[_tool_call("send")], cost_usd=0.0),
    ])
    res = asyncio.run(
        ReasoningEngine(rt).run(d, "send", tools={"send": lambda i: {}}, approval_timeout=0.05)
    )
    assert res.stop_reason == "human_required"
    assert rt.system_db.get_run(res.run_id)["status"] == "failed"


# --- circuit breakers ----------------------------------------------------


def test_budget_exceeded_stops_run(tmp_path):
    d, rt = _agent(tmp_path)
    rt.llm_gateway = ScriptedGateway(
        rt.system_db, [CompletionResult(content="", stop_reason="budget_exceeded")]
    )
    res = asyncio.run(ReasoningEngine(rt).run(d, "x"))
    assert res.stop_reason == "budget_exceeded"
    assert rt.system_db.get_run(res.run_id)["status"] == "failed"


def test_iteration_limit_stops_run(tmp_path):
    d, rt = _agent(tmp_path, available_connections=["api"])
    d.primitives.permission.action_policy.default = "allow"
    d.primitives.reasoning_loop.loop.max_iterations = 2
    # Model never stops asking for a tool -> the loop limit trips.
    rt.llm_gateway = ScriptedGateway(rt.system_db, [
        CompletionResult(content="", tool_calls=[_tool_call("noop")], cost_usd=0.0)
    ])
    res = asyncio.run(ReasoningEngine(rt).run(d, "loop forever", tools={"noop": lambda i: {}}))
    assert res.stop_reason == "iteration_limit"


def test_tool_call_limit_stops_run(tmp_path):
    d, rt = _agent(tmp_path, available_connections=["api"])
    d.primitives.permission.action_policy.default = "allow"
    d.primitives.reasoning_loop.loop.tool_call_limit = 1
    rt.llm_gateway = ScriptedGateway(rt.system_db, [
        CompletionResult(
            content="",
            tool_calls=[_tool_call("a"), _tool_call("b")],  # two in one turn
            cost_usd=0.0,
        )
    ])
    res = asyncio.run(ReasoningEngine(rt).run(d, "go", tools={"a": lambda i: {}, "b": lambda i: {}}))
    assert res.stop_reason == "iteration_limit"  # tool-call cap is a loop breaker


# --- memory integration --------------------------------------------------


def test_memory_recall_and_remember(tmp_path):
    d, rt = _agent(tmp_path, long_lived=True)
    rt.vector_store.upsert(d.agent_id, "d1", "acme corp is a key sales lead", {})
    rt.llm_gateway = ScriptedGateway(
        rt.system_db, [CompletionResult(content="acme is a lead", cost_usd=0.0)]
    )
    res = asyncio.run(ReasoningEngine(rt).run(d, "tell me about acme"))
    assert res.stop_reason == "done"
    # The interaction was written back to the preference store.
    prefs = rt.preference_store.get_all(d.agent_id)
    assert any("acme is a lead" in m.memory for m in prefs)
