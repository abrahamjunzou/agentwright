"""Service 11: LLM Gateway (Layer 0).

The gateway routes completions to a provider, enforces the per-run cost budget,
and records every call to the system.db cost ledger. The default implementation
here is ``FakeLLMGateway`` — deterministic, offline, no API key — so the runtime
and its tests run without network access. A real ``AnthropicGateway`` implements
the same ``LLMGateway`` protocol and is documented at the bottom; it is the only
swap needed to talk to a live model.
"""

from __future__ import annotations

from .interfaces import CompletionResult
from .system_db import SystemDB

# Rough illustrative prices ($ per 1K tokens) for the fake gateway's accounting.
_FAKE_PRICES = {"input": 0.003, "output": 0.015}


def _count_tokens(text: str) -> int:
    """Whitespace token count — a stand-in for a real tokenizer."""
    return len(text.split())


class FakeLLMGateway:
    """Deterministic offline gateway.

    Echoes a summary of the last user message as the completion, computes token
    counts and a cost from a fixed price table, enforces the cost budget, and (if
    given a SystemDB) records the call to the cost ledger.
    """

    def __init__(self, system_db: SystemDB | None = None) -> None:
        # The agent that owns each call is derived from its run_id (the runs
        # table records agent_id), so one gateway serves all agents.
        self._db = system_db

    def complete(
        self,
        messages: list,
        model_id: str,
        max_tokens: int,
        temperature: float,
        tools: list | None,
        cost_budget_usd: float,
        run_id: str,
    ) -> CompletionResult:
        last_user = ""
        for msg in reversed(messages):
            if isinstance(msg, dict) and msg.get("role") == "user":
                last_user = str(msg.get("content", ""))
                break

        content = f"[fake:{model_id}] ack: {last_user[:120]}"
        input_tokens = sum(_count_tokens(str(m.get("content", ""))) for m in messages
                           if isinstance(m, dict))
        output_tokens = min(_count_tokens(content), max_tokens)
        cost = (
            input_tokens / 1000 * _FAKE_PRICES["input"]
            + output_tokens / 1000 * _FAKE_PRICES["output"]
        )

        if cost > cost_budget_usd:
            # Budget exceeded before producing output — report, record nothing billable.
            return CompletionResult(
                content="",
                input_tokens=input_tokens,
                output_tokens=0,
                cost_usd=0.0,
                stop_reason="budget_exceeded",
            )

        if self._db is not None:
            run = self._db.get_run(run_id)
            if run is not None:
                self._db.record_cost(
                    run["agent_id"], run_id, "fake", model_id, input_tokens,
                    output_tokens, cost,
                )

        return CompletionResult(
            content=content,
            tool_calls=[],
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            stop_reason="done",
        )


# --- Real adapter (documented; not imported unless used) ---------------------
#
# class AnthropicGateway:
#     """Production gateway. Requires `anthropic` + ANTHROPIC_API_KEY.
#
#     Implements the same `complete(...)` signature: routes to the Anthropic
#     Messages API, injects cache_control on stable prompt buckets, retries on
#     429/529/500 with exponential backoff, enforces cost_budget_usd, and writes
#     each call to the cost ledger. Drop-in replacement for FakeLLMGateway.
#     """
