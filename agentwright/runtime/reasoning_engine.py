"""The reasoning-loop execution engine (Layer 0).

This is the piece that actually *runs* an agent: given a validated
``AgentDefinition`` and a task, it drives the agentic loop on top of the
``AgentRuntime`` services, implementing the reasoning_loop primitive's runtime
contract (``run(task, context) -> RunResult``).

Per iteration it:
  1. assembles context (identity instructions + memory recall, when present),
  2. calls the LLM gateway with the remaining cost budget,
  3. executes any requested tools — each gated by a permission check, escalated
     through the event-bus approval flow when required, and written to the
     action audit,
  4. feeds tool results back and loops until the model is done or a circuit
     breaker (cost, iterations, tool-call limit, missing approval) trips.

It depends only on the service *protocols*, so it runs unchanged against the
in-memory backends + FakeLLMGateway (no API key) or the real backends.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from uuid import uuid4

from ..orchestration.agent_definition import AgentDefinition
from ..telemetry import get_tracer
from . import policy
from .runtime import AgentRuntime

_tracer = get_tracer()

# A tool executor maps a tool name to a callable taking its input dict.
ToolExecutor = Callable[[dict], object]


@dataclass
class RunResult:
    """Outcome of one agent run (reasoning_loop primitive contract)."""

    run_id: str
    output: str
    tool_calls: list = field(default_factory=list)
    tokens_used: int = 0
    cost_usd: float = 0.0
    # done | budget_exceeded | iteration_limit | tool_error | human_required
    stop_reason: str = "done"


class ReasoningEngine:
    """Executes agent runs against an AgentRuntime."""

    def __init__(self, runtime: AgentRuntime) -> None:
        self._rt = runtime

    async def run(
        self,
        definition: AgentDefinition,
        task: str,
        *,
        tools: dict[str, ToolExecutor] | None = None,
        trigger_id: str | None = None,
        approval_timeout: float | None = 30.0,
    ) -> RunResult:
        """Run ``task`` as ``definition``'s agent. ``tools`` maps tool names to
        executors the agent may call. Returns a populated RunResult and records
        the run, its cost, and its actions to the system DB."""
        rt = self._rt
        prims = definition.primitives
        agent_id = definition.agent_id
        tools = tools or {}

        run_id = rt.system_db.start_run(agent_id, trigger_id)
        with _tracer.start_as_current_span("reasoning.run") as span:
            span.set_attribute("agent_id", agent_id)
            span.set_attribute("run_id", run_id)

            messages = self._assemble_context(definition, task)
            budget = prims.reasoning_loop.cost_controls.max_cost_per_run_usd
            model = prims.reasoning_loop.model
            loop = prims.reasoning_loop.loop
            tool_schemas = self._tool_schemas(definition)

            output = ""
            all_tool_calls: list = []
            tokens = 0
            stop_reason = "iteration_limit"

            for _ in range(loop.max_iterations):
                remaining = budget - rt.system_db.run_cost(run_id)
                completion = rt.llm_gateway.complete(
                    messages=messages,
                    model_id=model.model_id,
                    max_tokens=model.max_tokens,
                    temperature=model.temperature,
                    tools=tool_schemas,
                    cost_budget_usd=remaining,
                    run_id=run_id,
                )
                tokens += completion.input_tokens + completion.output_tokens

                if completion.stop_reason == "budget_exceeded":
                    stop_reason = "budget_exceeded"
                    break

                if not completion.tool_calls:
                    output = completion.content
                    stop_reason = "done"
                    break

                # Execute each requested tool call.
                messages.append({"role": "assistant", "content": completion.content})
                broke = False
                for call in completion.tool_calls:
                    all_tool_calls.append(call)
                    if len(all_tool_calls) > loop.tool_call_limit:
                        stop_reason = "iteration_limit"
                        broke = True
                        break
                    result, halted = await self._execute_tool(
                        definition, run_id, call, tools, approval_timeout
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.get("id", ""),
                            "content": json.dumps(result, default=str),
                        }
                    )
                    if halted:
                        stop_reason = "human_required"
                        broke = True
                        break
                if broke:
                    break

            cost = rt.system_db.run_cost(run_id)
            status = "done" if stop_reason == "done" else "failed"
            rt.system_db.end_run(run_id, status, stop_reason, cost, tokens)
            self._persist_transcript(agent_id, run_id, messages, output)
            self._remember(definition, task, output)

            span.set_attribute("stop_reason", stop_reason)
            span.set_attribute("cost_usd", cost)
            return RunResult(
                run_id=run_id,
                output=output,
                tool_calls=all_tool_calls,
                tokens_used=tokens,
                cost_usd=cost,
                stop_reason=stop_reason,
            )

    # --- context ---------------------------------------------------------

    def _assemble_context(self, definition: AgentDefinition, task: str) -> list[dict]:
        """Build the initial message list: instructions + recalled memory + task."""
        prims = definition.primitives
        messages: list[dict] = [
            {"role": "system", "content": prims.identity.instructions}
        ]
        if prims.memory is not None:
            recalled = self._recall(definition, task)
            if recalled:
                messages.append(
                    {"role": "system", "content": "Relevant memory:\n" + "\n".join(recalled)}
                )
        messages.append({"role": "user", "content": task})
        return messages

    def _recall(self, definition: AgentDefinition, task: str) -> list[str]:
        """Pull semantic hits and synthesized preferences for the task."""
        rt = self._rt
        agent_id = definition.agent_id
        top_k = definition.primitives.memory.search.semantic_top_k
        out = [h.text for h in rt.vector_store.query(agent_id, task, top_k=top_k)]
        out += [m.memory for m in rt.preference_store.search(agent_id, task, limit=top_k)]
        return out

    def _remember(self, definition: AgentDefinition, task: str, output: str) -> None:
        """Record the interaction to the preference store (best effort)."""
        if definition.primitives.memory is None or not output:
            return
        self._rt.preference_store.add(
            definition.agent_id,
            [{"role": "user", "content": task}, {"role": "assistant", "content": output}],
        )

    # --- tools -----------------------------------------------------------

    def _tool_schemas(self, definition: AgentDefinition) -> list | None:
        """Tool schemas exposed to the model, from the tool_connection primitive."""
        tc = definition.primitives.tool_connection
        if tc is None:
            return None
        schemas = []
        for conn in tc.connections:
            for tool in conn.tools:
                schemas.append(
                    {
                        "name": tool.name,
                        "description": tool.description,
                        "input_schema": tool.input_schema,
                    }
                )
        return schemas or None

    async def _execute_tool(
        self,
        definition: AgentDefinition,
        run_id: str,
        call: dict,
        tools: dict[str, ToolExecutor],
        approval_timeout: float | None,
    ) -> tuple[object, bool]:
        """Run one tool call subject to permission policy and approval.

        Returns ``(result, halted)``; ``halted`` is True when a required approval
        was not granted and the run must stop (``human_required``).
        """
        rt = self._rt
        agent_id = definition.agent_id
        name = call.get("name", "")
        inputs = call.get("input", {})
        decision = policy.evaluate(definition.primitives.permission, name)

        # Escalate for approval when the policy demands it.
        if decision.requires_approval:
            try:
                approval = await rt.event_bus.request_approval(
                    {
                        "approval_id": str(uuid4()),
                        "agent_id": agent_id,
                        "run_id": run_id,
                        "action": name,
                        "inputs": inputs,
                        "approvers": decision.approvers,
                    },
                    timeout=approval_timeout,
                )
            except asyncio.TimeoutError:
                # No approver responded in time -> the run halts (human_required).
                rt.system_db.record_action(
                    agent_id, run_id, name, json.dumps(inputs), "approval_pending"
                )
                return ({"error": f"{name} approval timed out"}, True)
            granted = approval.get("decision") == "approved"
        else:
            granted = decision.allowed

        if not granted:
            outcome = "approval_pending" if decision.requires_approval else "denied"
            rt.system_db.record_action(agent_id, run_id, name, json.dumps(inputs), outcome)
            # A hard policy deny lets the model react; a missing approval halts.
            halted = decision.requires_approval
            return ({"error": f"{name} {outcome}"}, halted)

        # Allowed — execute via the provided executor (if any).
        if name not in tools:
            rt.system_db.record_action(agent_id, run_id, name, json.dumps(inputs), "failed")
            return ({"error": f"no executor for tool {name}"}, False)
        try:
            result = tools[name](inputs)
            rt.system_db.record_action(agent_id, run_id, name, json.dumps(inputs), "success")
            return (result, False)
        except Exception as exc:  # tool raised — audit and report, don't crash the run
            rt.system_db.record_action(agent_id, run_id, name, json.dumps(inputs), "failed")
            return ({"error": str(exc)}, False)

    # --- persistence -----------------------------------------------------

    def _persist_transcript(
        self, agent_id: str, run_id: str, messages: list[dict], output: str
    ) -> None:
        """Write a markdown transcript of the run to the file store history."""
        lines = [f"# Run {run_id}", ""]
        for m in messages:
            lines.append(f"**{m['role']}**: {m.get('content', '')}")
        lines += ["", f"## Output", output]
        self._rt.file_store.write(agent_id, f"history/{run_id}.md", "\n".join(lines))
