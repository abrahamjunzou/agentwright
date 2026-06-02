# AGENTS — Integration Guide for AI Agents

**Audience: you are an AI agent (or an agent-building system) that wants to
create, validate, persist, and run agents on top of `agentwright`.**
This document is the contract. It tells you exactly which calls to make, in what
order, what data to pass, what you get back, and what will reject you and why.

Read this top to bottom once. Then use the **Recipes** section as your runbook.

---

## 0. TL;DR — the one path that always works

```python
from agentwright import DomainBrief, compose
from agentwright.runtime import AgentRuntime, ReasoningEngine
import asyncio

# 1. Describe intent (what, not how).
brief = DomainBrief(name="My Agent", goal="Summarize inbound leads daily.",
                    domain="sales", long_lived=True)

# 2. Compose -> a validated AgentDefinition (Layer 2 picks + configures primitives).
definition = compose(brief, owner_id="usr_1", created_by="usr_1")
assert not definition.validation_errors      # if non-empty, you built something illegal

# 3. Boot the runtime (Layer 0) and provision this agent's stores.
rt = AgentRuntime()                           # zero-daemon, all in-process, no API key
rt.instantiate(definition)

# 4. Run it.
engine = ReasoningEngine(rt)
result = asyncio.run(engine.run(definition, task="Summarize today's leads."))
print(result.output, result.stop_reason, result.cost_usd)
rt.close()
```

If you do nothing else, do that. Everything below is detail and variation.

---

## 0.5. Before you build: design guidance you are expected to apply

`agentwright` ships **design guidance** in [`guide/`](guide/) — best-practice
documents written for *you* to read and apply when you design an agent. They are
not optional background; treat them as part of this contract. The sections below
tell you *how to execute* an agent; these guides tell you *how to decide what to
build*. Before composing any non-trivial agent, consult them and commit to a choice:

- [`guide/agent_design_patterns.md`](guide/agent_design_patterns.md) — pick **one**
  design pattern (planner-orchestrator, sequential pipeline, supervisor-worker,
  blackboard, critique-revision) from its selection table and run it consistently.
- [`guide/agent_task_decomposition_guidelines.md`](guide/agent_task_decomposition_guidelines.md)
  — decompose the goal into validated, right-sized tasks instead of free-forming one
  giant prompt.
- [`guide/context_engineering_strategies.md`](guide/context_engineering_strategies.md)
  — choose **one** context strategy that matches the agent's purpose (its "Use when"
  sections) and follow it top to bottom.

Each guide is self-selecting: read its selection table / "Use when" section, choose
a single option, and **do not mix architectures mid-run**. Map the design you choose
onto the primitives and runtime documented below — that is where the decision becomes
a runnable agent.

---

## 1. Mental model: three layers, one direction

```
   YOU (an agent / agent system)
        │  express intent
        ▼
   ┌─────────────────────────────────────────────────────┐
   │ Layer 2 — Domain Orchestration                       │
   │   DomainBrief ─▶ select ─▶ configure ─▶ validate ─▶  │  produces an
   │                                          register    │  AgentDefinition
   └─────────────────────────────────────────────────────┘
        │  an AgentDefinition references...
        ▼
   ┌─────────────────────────────────────────────────────┐
   │ Layer 1 — Primitives (the vocabulary)                │
   │   9 typed building blocks + a discovery/registry API │
   └─────────────────────────────────────────────────────┘
        │  ...which the runtime provisions + executes
        ▼
   ┌─────────────────────────────────────────────────────┐
   │ Layer 0 — Runtime Infrastructure                     │
   │   AgentRuntime (stores, vault, bus, scheduler) +     │
   │   ReasoningEngine (drives the agentic loop)          │
   └─────────────────────────────────────────────────────┘
```

**Direction of control:** the `AgentDefinition` *drives* the infrastructure.
Layer 0 provisions **only** the stores the definition's primitives declare — it
never assumes. Do not hardcode infra; describe needs in the brief and let the
layers resolve them.

**The boundary object** is `AgentDefinition`. It round-trips losslessly:

```python
blob = definition.model_dump_json()                 # persist / send anywhere
from agentwright import AgentDefinition
definition = AgentDefinition.model_validate_json(blob)  # load and run elsewhere
```

---

## 2. The 9 primitives (Layer 1 vocabulary)

Every agent is built from these. Four are **always present**; the rest are
selected only when the brief implies them.

| Primitive        | Always? | What it provides                                   | Depends on            |
|------------------|---------|----------------------------------------------------|-----------------------|
| `identity`       | ✅      | Name, instructions/persona, model defaults         | —                     |
| `reasoning_loop` | ✅      | The agentic loop: model, iteration & cost controls | `identity`            |
| `permission`     | ✅      | Action policy (allow / deny / require_approval)    | —                     |
| `observability`  | ✅      | Logs / metrics / traces / export                   | —                     |
| `memory`         | ⬜      | document + vector + graph + universal + KV state   | `identity`            |
| `tool_connection`| ⬜      | External tools/APIs the agent may call             | `permission`          |
| `compute`        | ⬜      | `shell` / `browser` sandboxes (`vm` is rejected)   | `permission`          |
| `trigger`        | ⬜      | schedule / webhook / email / slack / event / manual| `identity`            |
| `generated_ui`   | ⬜      | Agent-produced UI output                           | `identity`            |

**Discover them programmatically — do not hardcode schemas.** The registry is
your source of truth and reflects custom-registered primitives too:

```python
from agentwright import catalogue, describe_primitive
from agentwright.primitives import resolve_dependencies, missing_dependencies

catalogue()                       # list[dict]: every primitive, fully described
describe_primitive("memory")      # one primitive: name, version, dependencies,
                                  #   runtime_contract, config_schema (JSON Schema)
resolve_dependencies({"reasoning_loop"})      # -> {"reasoning_loop", "identity"}
missing_dependencies({"tool_connection"})     # -> {"tool_connection": ["permission"]}
```

`describe_primitive(name)["config_schema"]` is a standard JSON Schema. If you are
an LLM-driven builder, feed that schema to yourself to produce valid config.

---

## 3. Layer 2 — composing an agent from intent

### 3.1 `DomainBrief` — your input

```python
from agentwright import DomainBrief, BriefConstraints

DomainBrief(
    name: str,                       # required
    goal: str,                       # required — plain language, what success is
    domain: str,                     # required — e.g. "sales", "research", "finance"
    constraints: BriefConstraints = BriefConstraints(),
    available_connections: list[str] = [],   # e.g. ["gmail","hubspot","slack"]
    available_compute: list["shell"|"browser"|"vm"] = [],
    triggers_needed: list["schedule"|"webhook"|"email"|"slack"|"event"|"manual"] = [],
    ui_output_needed: bool = False,
    long_lived: bool = False,        # persistent identity/memory across runs
    sub_agent: bool = False,         # spawned by a parent agent (relaxes invariant 10)
)

BriefConstraints(
    cost_per_run_usd_max: float = 2.0,
    requires_human_approval_for: list[str] = [],   # action patterns, e.g. ["crm.delete_*"]
    pii_handling: "allow"|"redact"|"deny" = "redact",
    data_retention_days: int = 90,
)
```

**How fields map to primitives (the selector's decision matrix):**
- `long_lived=True` → `memory` (with the vector store enabled).
- `available_connections` non-empty, or `email`/`slack` in `triggers_needed`
  → `tool_connection`.
- `available_compute` non-empty → `compute`.
- `triggers_needed` non-empty → `trigger`.
- `ui_output_needed=True` → `generated_ui`.
- `constraints.requires_human_approval_for` → permission overrides.

You describe needs; you do not list primitives. The selector is deterministic.

### 3.2 `compose` / `register` / `compose_and_register`

```python
from agentwright import compose, register, compose_and_register, InstanceRegistry

# Build only (no persistence). status is "validated" or "draft".
definition = compose(brief, owner_id="usr_1", created_by="usr_1")

# Persist a *valid* definition (raises ValueError if it still has errors).
registry = InstanceRegistry("system.db")     # or ":memory:" for ephemeral
register(definition, registry)               # -> agent_id, status becomes "active"

# Or do both; invalid definitions come back unregistered with errors populated.
definition = compose_and_register(brief, owner_id="usr_1", created_by="usr_1",
                                  registry=registry)
```

### 3.3 `AgentDefinition` — what you get back

```python
definition.agent_id            # "agt_..."
definition.status              # draft | validated | active | paused | archived
definition.primitives          # PrimitiveSet (typed config per selected primitive)
definition.validation_errors   # list[str] — NON-EMPTY means do not run it
definition.validation_warnings # list[str] — advisory, safe to run
definition.human_input_required# list[str] — config fields you must fill with real values
```

**Always check three things before running:**
1. `validation_errors` is empty.
2. `human_input_required` is empty *or* you have supplied the real values
   (credential refs, approver ids, etc.) — placeholders will not work against
   real backends.
3. `status` is `validated` or `active`.

---

## 4. Composition invariants — why you get rejected

`validate()` enforces these. Errors block running; warnings don't.

1–4. `identity`, `reasoning_loop`, `permission`, `observability` must all be present.
5. Every approval-gated tool must be covered by a permission policy.
6. All primitive dependency chains must be satisfied (`missing_dependencies` empty).
7–10. Design invariants (memory/trigger/identity consistency; invariant 10 requires
   a real owner unless `sub_agent=True`).
11. **email/slack trigger ⇒ a `tool_connection` reaching that channel** — else error.
12. **`compute.vm.enabled` must be `False`** — there is no VM service; error.
   This is also re-checked at `instantiate()` as a pre-flight (raises `RuntimeError_`).

Non-fatal: exporting observability to a non-local destination → **warning**.

If `validation_errors` is non-empty, read the strings — each names the primitive
and the rule. Fix the brief, recompose. Do not patch around the validator.

---

## 5. Layer 0 — running the agent

### 5.1 `AgentRuntime` — the service container

```python
from agentwright.runtime import AgentRuntime

rt = AgentRuntime(
    root=None,                  # filesystem root; default = a managed runtime dir
    vault=None,                 # Vault for secrets (needed for real credentials)
    vector_store=None,          # swap any heavy store for a real backend (see §6)
    graph_store=None,
    universal_store=None,
    preference_store=None,
    llm_gateway=None,           # default: FakeLLMGateway (deterministic, no API key)
)
```

Constructing it starts **no daemons**. Defaults are in-memory/fake, so the whole
system runs offline. Public services you can use directly:

```
rt.system_db        # SQLite: agents, runs, cost_ledger, action_audit
rt.file_store       # per-agent files (history/, artifacts/, workspace/)
rt.document_store   # TinyDB document store
rt.kv_store         # LMDB key/value (dedup, cursors, operational state)
rt.vault            # Fernet-encrypted secrets
rt.event_bus        # pub/sub + request_approval (human-in-the-loop)
rt.scheduler        # cron scheduler (fires trigger.fired events)
rt.shell_runner     # sandboxed shell exec
rt.browser_pool     # browser automation (stub by default)
rt.vector_store / rt.graph_store / rt.universal_store / rt.preference_store
rt.llm_gateway      # LLM completions; records cost to the ledger
```

### 5.2 `rt.instantiate(definition) -> agent_id`

Provisions **only** the stores the definition's primitives require, seeds
structured-state tables, and enqueues schedule triggers onto the scheduler.
Runs the vm pre-flight first. Call this once per agent before running it.

### 5.3 `ReasoningEngine.run(...)` — the agentic loop

```python
from agentwright.runtime import ReasoningEngine
engine = ReasoningEngine(rt)

result = await engine.run(
    definition,
    task: str,                          # what to do this run
    tools: dict[str, Callable[[dict], object]] | None = None,  # name -> executor
    trigger_id: str | None = None,      # which trigger fired this run
    approval_timeout: float | None = 30.0,  # seconds to wait on human approval
)
```

`run` is `async`. From sync code use `asyncio.run(engine.run(...))`.

**Per iteration the engine:** assembles context (identity instructions + memory
recall when a `memory` primitive is present) → calls the LLM with the remaining
cost budget → for each requested tool call, checks the permission policy,
escalates via the event bus when approval is required, audits the outcome, feeds
results back → loops until done or a circuit breaker trips.

### 5.4 `RunResult` — what a run returns

```python
result.run_id        # str
result.output        # final text output
result.tool_calls    # list of tool calls the agent made
result.tokens_used   # int
result.cost_usd      # float (also recorded to rt.system_db cost_ledger)
result.stop_reason   # see below
```

**`stop_reason` values — branch on these:**
- `done` — model finished normally. ✅
- `budget_exceeded` — hit `cost_per_run_usd_max`. Raise budget or shrink task.
- `iteration_limit` — hit max iterations or tool-call limit. Decompose the task.
- `tool_error` — a tool failed (the error was fed back; the model may have coped).
- `human_required` — an approval was needed and was rejected or timed out. Re-run
  after a human approves, or adjust the permission policy.

---

## 6. Tools, permissions, and human approval

### 6.1 Supplying tools

The model can only call tools declared by the agent's `tool_connection`
primitive. You provide the **executors** at run time, keyed by tool name:

```python
def crm_update(inputs: dict) -> dict:
    # ... do the work ...
    return {"status": "ok"}

result = await engine.run(definition, task="...", tools={"crm.update": crm_update})
```

If a tool is called but you supplied no executor, the engine audits it and
returns an error result to the model (it does not crash).

### 6.2 Permission policy

Every tool call is evaluated against the `permission` primitive:

```python
from agentwright.runtime import evaluate
decision = evaluate(definition.primitives.permission, "crm.delete_contact")
decision.allowed            # bool
decision.requires_approval  # bool
decision.approvers          # list[str]
```

Policy semantics: default is `allow` / `deny` / `require_approval`; overrides
match action patterns (`fnmatch`, e.g. `crm.delete_*`); **the first matching
override wins**, and a `deny` override beats the default.

### 6.3 Human-in-the-loop approval

When a tool `requires_approval`, the engine publishes an `approval.requested`
event (carrying an `approval_id`, `agent_id`, `run_id`, `action`, `inputs`,
`approvers`) and waits up to `approval_timeout` for a matching
`approval.resolved`. Your supervising process must answer it:

```python
# In another task/coroutine, watch for and answer approval requests:
req = await rt.event_bus.consume("approval.requested", timeout=...)
await rt.event_bus.publish("approval.resolved", {
    "approval_id": req["approval_id"],
    "decision": "approved",            # the engine treats only "approved" as a grant
})
```

The resolution must echo the same `approval_id`; resolutions for other ids are
re-queued, so concurrent approvals don't steal each other's answers. If no one
responds in time → the action is audited `approval_pending` and the run ends
`human_required`. Set `approval_timeout=None` only if you guarantee a responder,
or the run will hang.

---

## 7. Swapping in real backends

Defaults are in-memory/fake. Drop in real services behind the same protocols —
**your agent code does not change**:

```python
from agentwright.runtime import real_backends as rb

rt = AgentRuntime(
    universal_store=rb.SurrealUniversalStore(),         # SurrealDB (mem://), no key
    vector_store=rb.ChromaVectorStore(),                # Chroma, no key
    llm_gateway=rb.make_gateway("anthropic"),           # or "openai" / "google"/"gemini"
)
```

`make_gateway(provider)` routes to `AnthropicGateway` / `OpenAIGateway` /
`GoogleGateway`. Each records real cost to the ledger.

**What each real backend needs:**
- SurrealDB (universal), Chroma (vector): just the package, **no API key**.
- Anthropic / OpenAI / Google gateways: package **+ the provider API key** in env
  (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GOOGLE_API_KEY` or `GEMINI_API_KEY`).
- Mem0 (preference store): needs `ANTHROPIC_API_KEY` for synthesis.
- LadybugDB (graph): manual install (no PyPI wheel).
- Playwright (browser): `playwright install chromium`.

Install extras: `uv sync --extra surreal --extra chroma --extra anthropic
--extra openai --extra google`.

---

## 8. Recipes

### 8.1 Manual, single-shot agent (no key, fully offline)
```python
brief = DomainBrief(name="Echo", goal="Answer the user.", domain="general")
definition = compose(brief, "u", "u")
rt = AgentRuntime(); rt.instantiate(definition)
res = asyncio.run(ReasoningEngine(rt).run(definition, task="Say hi."))
rt.close()
```

### 8.2 Long-lived agent with memory
```python
brief = DomainBrief(name="Researcher", goal="Track a topic over time.",
                    domain="research", long_lived=True)          # -> memory enabled
definition = compose(brief, "u", "u")
rt = AgentRuntime(universal_store=rb.SurrealUniversalStore())    # real persistence
rt.instantiate(definition)
# Each run recalls prior context and remembers the new interaction automatically.
```

### 8.3 Tool-using agent with an approval gate
```python
brief = DomainBrief(
    name="CRM Bot", goal="Update CRM on request.", domain="sales",
    available_connections=["hubspot"],
    constraints=BriefConstraints(requires_human_approval_for=["crm.delete_*"]),
)
definition = compose(brief, "u", "u")
# ...supply tools + answer approval.requested on the event bus (see §6.3).
```

### 8.4 Scheduled agent
```python
brief = DomainBrief(name="Daily Report", goal="Post a daily summary.",
                    domain="ops", triggers_needed=["schedule"], long_lived=True)
definition = compose(brief, "u", "u")
rt = AgentRuntime(); rt.instantiate(definition)   # schedule trigger is enqueued
# The scheduler fires trigger.fired; your loop consumes it and calls engine.run.
```

### 8.5 Sub-agent (spawned by a parent)
```python
brief = DomainBrief(name="Sub", goal="Do a subtask.", domain="general",
                    sub_agent=True)               # relaxes the owner invariant
definition = compose(brief, owner_id=parent_agent_id, created_by=parent_agent_id)
```

---

## 9. Rules of engagement (do / don't)

**Do**
- Describe intent in the brief; let the layers choose primitives.
- Check `validation_errors`, `human_input_required`, and `stop_reason` every time.
- Use `catalogue()` / `describe_primitive()` to learn the config schemas — they
  are the source of truth and include custom primitives.
- Persist/transport agents as `model_dump_json()`; reload with
  `AgentDefinition.model_validate_json()`.
- Call `rt.close()` when done to release DB/file handles.

**Don't**
- Don't run a definition with non-empty `validation_errors`.
- Don't hardcode primitive config shapes — read the JSON Schema.
- Don't set `compute.vm.enabled=True` (invariant 12 / instantiate pre-flight).
- Don't set `approval_timeout=None` unless a responder is guaranteed.
- Don't assume stores exist that the definition didn't declare — the runtime only
  provisions what the primitives require.

---

## 10. Quick API index

```python
# Discovery (Layer 1)
from agentwright import catalogue, describe_primitive
from agentwright.primitives import resolve_dependencies, missing_dependencies, get_template, register

# Build / persist (Layer 2)
from agentwright import DomainBrief, BriefConstraints, AgentDefinition, PrimitiveSet
from agentwright import compose, register, compose_and_register, InstanceRegistry, validate

# Run (Layer 0)
from agentwright.runtime import AgentRuntime, ReasoningEngine, RunResult, evaluate
from agentwright.runtime import real_backends as rb   # SurrealUniversalStore, ChromaVectorStore, make_gateway, ...
```

Module-level detail lives in `agentwright/primitives/README.md`,
`agentwright/orchestration/README.md`, and
`agentwright/runtime/README.md`. The design rationale is in
`design/agent_primitive_layer_design.md` and
`design/agent_runtime_infrastructure.md`. The design guidance you apply when
building (§0.5) is in `guide/`.

Now you've read the damn doc. Go build.
