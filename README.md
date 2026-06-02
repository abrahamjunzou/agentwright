# agentwright

**agentwright.** A small set of typed, composable **primitives** for building,
validating, and running AI agents — and a zero-daemon runtime that executes them.
You describe an agent's *intent*; the system selects the right primitives,
configures them, validates the composition against hard invariants, and gives you
a runnable, auditable agent.

No framework lock-in, no training, no always-on services. Pure Python, in-process,
runs offline with **no API key** — real LLM and database backends drop in behind
the same interfaces when you want them.

```bash
pip install agentwright            # or: uv add agentwright
```

---

## Quickstart — the one path that always works

```python
from agentwright import DomainBrief, compose
from agentwright.runtime import AgentRuntime, ReasoningEngine
import asyncio

# 1. Describe intent (what, not how).
brief = DomainBrief(name="My Agent", goal="Summarize inbound leads daily.",
                    domain="sales", long_lived=True)

# 2. Compose -> a validated AgentDefinition (the OS picks + configures primitives).
definition = compose(brief, owner_id="usr_1", created_by="usr_1")
assert not definition.validation_errors      # non-empty => you described something illegal

# 3. Boot the runtime and provision exactly this agent's stores.
rt = AgentRuntime()                           # zero-daemon, all in-process, no API key
rt.instantiate(definition)

# 4. Run it.
engine = ReasoningEngine(rt)
result = asyncio.run(engine.run(definition, task="Summarize today's leads."))
print(result.output, result.stop_reason, result.cost_usd)
rt.close()
```

If you do nothing else, do that. Everything below is detail and variation. The
full agent-facing contract is [`AGENTS.md`](AGENTS.md).

---

## Mental model: three layers, one direction

```
   YOU (an app / agent / agent system)
        │  express intent as a DomainBrief
        ▼
   Layer 2 — Domain Orchestration
        DomainBrief ─▶ select ─▶ configure ─▶ validate ─▶ register   ─▶ AgentDefinition
        │
        │  an AgentDefinition references…
        ▼
   Layer 1 — Primitives (the vocabulary)
        9 typed building blocks + a discovery/registry API
        │
        │  …which the runtime provisions + executes
        ▼
   Layer 0 — Runtime Infrastructure
        AgentRuntime (stores, vault, bus, scheduler) + ReasoningEngine (the agentic loop)
```

**Direction of control:** the `AgentDefinition` *drives* the infrastructure.
Layer 0 provisions **only** the stores a definition's primitives declare — it
never assumes. You describe needs in the brief; the layers resolve them.

**The boundary object** is `AgentDefinition`. It round-trips losslessly, so you
can compose in one place and run in another:

```python
blob = definition.model_dump_json()                      # persist / send anywhere
from agentwright import AgentDefinition
definition = AgentDefinition.model_validate_json(blob)   # load and run elsewhere
```

---

## Two kinds of material: execution code vs. agent guidance

Everything above — the three layers, the primitives, the runtime — is **execution
code**: typed, deterministic machinery that *runs* an agent. It is the *how*.

Alongside it, [`guide/`](guide/) holds **agent guidance**: prose an agent reads to
*design itself* before it builds. It is the *judgment* — open-ended best practice,
not code:

| Guide | The agent uses it to decide… |
|---|---|
| [`agent_design_patterns.md`](guide/agent_design_patterns.md) | which design pattern to run (planner-orchestrator, sequential pipeline, supervisor-worker, blackboard, critique-revision) |
| [`agent_task_decomposition_guidelines.md`](guide/agent_task_decomposition_guidelines.md) | how to break a goal into validated, right-sized tasks |
| [`context_engineering_strategies.md`](guide/context_engineering_strategies.md) | what to keep in context and how to manage it over a run's lifetime |

**The split:** `agentwright/` is the vocabulary an agent *executes through*; `guide/`
is how it *decides what to build*. The primitives stay fixed and typed; the guidance
lets the agent self-organize within them. A consuming agent is expected to pick this
guidance up automatically — [`AGENTS.md`](AGENTS.md) makes it part of the contract.

---

## The 9 primitives (Layer 1 vocabulary)

Four are **always present**; the rest are selected only when the brief implies them.

| Primitive | Always? | Provides | Depends on |
|---|---|---|---|
| `identity` | ✅ | Name, instructions/persona, model defaults | — |
| `reasoning_loop` | ✅ | The agentic loop: model, iteration & cost controls | `identity` |
| `permission` | ✅ | Action policy (allow / deny / require_approval) | — |
| `observability` | ✅ | Logs / metrics / traces / export (OpenTelemetry) | — |
| `memory` | ⬜ | document + vector + graph + universal + KV state | `identity` |
| `tool_connection` | ⬜ | External tools/APIs the agent may call | `permission` |
| `compute` | ⬜ | Shell / browser execution | `permission` |
| `trigger` | ⬜ | schedule / webhook / email / slack / event / manual | `identity` |
| `generated_ui` | ⬜ | Agent-produced UI surfaces | `identity` |

Discover them at runtime — every primitive exposes its JSON config schema:

```python
from agentwright import catalogue, describe_primitive
catalogue()                       # [{name, version, dependencies, config_schema}, …]
describe_primitive("permission")  # one primitive, full schema
```

---

## Custom primitives are first-class

The OS is extensible by design: register your own primitive and it flows through
the **same** pipeline as the built-ins — selected, configured from its own schema,
version-pinned, dependency-checked, and persisted. No fork, no per-type glue.

```python
from agentwright import PrimitiveTemplate, register_primitive, DomainBrief, compose
from pydantic import BaseModel

class SentimentConfig(BaseModel):
    threshold: float = 0.5

register_primitive(PrimitiveTemplate(
    name="sentiment", version="1.0.0", config_model=SentimentConfig,
    dependencies=("identity",), runtime_contract=("scores text sentiment",),
))

brief = DomainBrief(name="reviews", goal="triage reviews", domain="support",
                    extra_primitives=["sentiment"],
                    custom_config={"sentiment": {"threshold": 0.8}})
defn = compose(brief, owner_id="u", created_by="u")
defn.primitives.custom["sentiment"]          # SentimentConfig(threshold=0.8)
```

An unsatisfied dependency is a normal validation error; an unregistered name
raises. See [`orchestration/README.md`](agentwright/orchestration/README.md).

---

## Layer 0 — what's real vs. interfaced

Layer 0 is **zero-daemon**: everything runs in your process.

- **Real, fully tested offline:** SQLite system DB, File Store, Fernet vault,
  Event Bus, Scheduler (cron), TinyDB document store, LMDB KV, Shell Runner, the
  `ReasoningEngine`, and in-memory implementations of every heavy store.
- **Real backends behind protocols** (`runtime/interfaces.py` ↔
  `runtime/real_backends.py`): each heavy service has a real adapter implementing
  the same protocol as its in-memory default, swappable via `AgentRuntime(...)`.
  SurrealDB (universal) and Chroma (vector) test for real with **no API key**.
  Three LLM gateways — **Anthropic, OpenAI, Google Gemini** — plus a
  `make_gateway(provider)` router. Install only what you need:

  ```bash
  pip install "agentwright[anthropic,chroma,surreal]"   # pick extras
  pip install "agentwright[all]"                        # every backend
  ```

**Bring your own backend** by implementing a protocol from `runtime/interfaces.py`
(`LLMGateway`, `VectorStore`, `GraphStore`, `Memory`, …) and passing it to
`AgentRuntime`. That protocol set is the OS's plugin ABI.

---

## Install (development)

Uses [`uv`](https://docs.astral.sh/uv/):

```bash
uv sync                 # core, offline
uv sync --extra all     # + every real backend
uv run pytest           # run the suite
```

---

## Tests

```bash
uv run pytest
```

**164 tests pass offline**; 8 live-backend tests skip unless their keys/installs
are present. Coverage spans the registry and dependency resolution, the selector
matrix, the configurator mapping, custom-primitive composition, every composition
invariant and infra gap, the SQLite instance registry, all Layer-0 runtime
services, the Layer-2→Layer-1 consumer journey, cross-layer integration, and
**agent-archetype scenario tests** that build realistic agents and run them end to
end (L2→L0) — covering all nine primitives and every pattern (each trigger type,
each compute backend, generated_ui, sub-agents, the full memory stack, the
permission approval flow, multi-tool reasoning, and concurrent agents).

---

## Observability

Orchestration boundaries (`orchestrate.select/configure/validate/compose/register`)
emit OpenTelemetry spans, no-ops until a tracer provider is installed. Call
`agentwright.telemetry.enable_console_tracing()` to print spans while
debugging. Layer 0 boundaries (run start/stop, tool calls, cost) are recorded in
the system DB and audit ledger.

---

## Docs

- [`AGENTS.md`](AGENTS.md) — the full integration contract (audience: agents).
- [`guide/`](guide/) — design patterns, task decomposition, and context-engineering
  guidance an agent reads to design itself (audience: agents).
- [`agentwright/primitives/README.md`](agentwright/primitives/README.md) — Layer 1.
- [`agentwright/orchestration/README.md`](agentwright/orchestration/README.md) — Layer 2.
- [`agentwright/runtime/README.md`](agentwright/runtime/README.md) — Layer 0.
- [`design/`](design/) — the layer designs this implements.

## Acknowledgments

This project stands on the teaching and generosity of others:

- **[Andrew Ng](https://x.com/AndrewYNg)**, whose [Agentic AI course](https://learn.deeplearning.ai/courses/agentic-ai)
  shaped much of the thinking behind these primitives and the compose-validate-run model.
- **[Andrew Lee](https://x.com/startupandrew) and the team at [Tasklet](https://tasklet.ai)**,
  for generously sharing the practices behind building an agent OS — a real source of both
  inspiration and concrete knowledge for this work.
- **[Matt Dzaman](https://stellic.com)** (Stellic), whose Configurable Workflow Platform
  project idea helped inspire the configuration-driven, composable approach taken here.

Any mistakes or rough edges here are my own.

## License

[Apache-2.0](LICENSE).
