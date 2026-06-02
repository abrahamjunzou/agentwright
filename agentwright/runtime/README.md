# Runtime Module (Layer 0)

The Runtime Infrastructure Layer from
`design/agent_runtime_infrastructure.md`: the in-process, **zero-daemon**
services that back the primitive layer. Constructing the runtime starts no OS
services, sockets, or database daemons — everything lives in the application's
own process.

## Two tiers

**Tier A — real implementations, fully tested offline:**

| Service | File | Backed by |
|---|---|---|
| System DB (agents/runs/cost_ledger/action_audit) | `system_db.py` | SQLite (stdlib) |
| File Store | `file_store.py` | filesystem |
| Fernet Vault | `vault.py` | `cryptography` |
| Event Bus | `event_bus.py` | `asyncio` |
| Scheduler | `scheduler.py` | `asyncio` + `croniter` |
| Document Store | `document_store.py` | `tinydb` |
| KV Store | `kv_store.py` | `lmdb` |
| Shell Runner | `shell_runner.py` | `subprocess` + `resource` |

**Tier B — protocol + in-memory implementation now, real backend later:**

| Protocol (`interfaces.py`) | In-memory now | Real backend later |
|---|---|---|
| `VectorStore` | `InMemoryVectorStore` (cosine over hashing embedding) | Chroma |
| `GraphStore` | `InMemoryGraphStore` (BFS traversal) | LadybugDB |
| `UniversalStore` | `InMemoryUniversalStore` | SurrealDB |
| `PreferenceStore` | `InMemoryPreferenceStore` | Mem0 |
| `LLMGateway` | `FakeLLMGateway` (deterministic, costed) | Anthropic |
| `BrowserPool` | `StubBrowserPool` | Playwright |

The in-memory backends are behaviourally faithful (real similarity ranking, real
bounded graph traversal, real cost accounting) so the whole stack runs and is
testable with no downloads or API keys. A real backend implements the same
protocol and is passed to `AgentRuntime(...)` — nothing else changes.

## AgentRuntime facade

`runtime.py` wires every service together and implements the design's
**Per-Agent Instantiation Sequence**:

```python
from agentwright import DomainBrief, compose
from agentwright.runtime import AgentRuntime

definition = compose(DomainBrief(name="A", goal="g", domain="sales",
                                 long_lived=True), "usr_1", "usr_1")

rt = AgentRuntime("/path/to/runtime-root")     # or default ~/.local/share/agent-runtime
agent_id = rt.instantiate(definition)          # provisions only what the definition needs

run_id = rt.system_db.start_run(agent_id)
res = rt.llm_gateway.complete([...], "claude-opus-4-8", 1024, 0.2, None, 2.0, run_id)
rt.kv_store.put(agent_id, "processed:lead:1", b"1")
rt.system_db.end_run(run_id, "done", res.stop_reason, res.cost_usd, 0)
```

`instantiate()` is **definition-driven**: it provisions stores only for the
primitives the agent actually carries, and runs the vm pre-flight check
(composition invariant 12) before creating anything.

## ReasoningEngine — running a full agent

`reasoning_engine.py` is the piece that actually *runs* an agent (the
reasoning_loop primitive's `run(task) -> RunResult` contract). Per iteration it
assembles context (instructions + memory recall), calls the LLM gateway with the
remaining budget, executes requested tools — each gated by a permission check,
escalated through the event-bus approval flow when required, and written to the
action audit — then loops until the model is done or a circuit breaker trips
(`budget_exceeded`, `iteration_limit`, `human_required`).

```python
from agentwright.runtime import AgentRuntime, ReasoningEngine

rt = AgentRuntime()
rt.instantiate(definition)
engine = ReasoningEngine(rt)
result = await engine.run(definition, "enrich the new leads",
                          tools={"hubspot.update": my_executor})
print(result.stop_reason, result.cost_usd)   # done | budget_exceeded | human_required | ...
```

It depends only on the service **protocols**, so it runs unchanged against the
in-memory backends + `FakeLLMGateway` (no API key) or the real backends.

## Real backends

`real_backends.py` holds adapters that implement the Tier-B protocols against the
production packages, each with **lazy imports** (so the module loads with none
installed). Swap one in via the `AgentRuntime` constructor:

```python
from agentwright.runtime import AgentRuntime
from agentwright.runtime.real_backends import SurrealUniversalStore, ChromaVectorStore

rt = AgentRuntime(universal_store=SurrealUniversalStore(),
                  vector_store=ChromaVectorStore())
```

Install via optional extras: `uv sync --extra surreal --extra chroma --extra
anthropic --extra mem0 --extra browser`. SurrealDB (embedded `mem://`) and Chroma
are tested for real and need no API key. LadybugDB has no PyPI wheel yet (install
`ladybugdb` manually). The Anthropic gateway and Mem0 store need
`ANTHROPIC_API_KEY` at call time; Playwright needs `playwright install chromium`.

## Operational commitments honored

- TinyDB writes serialize through `WriteWorker` (one writer).
- LMDB `map_size` is fixed at `open_agent()` time; reads are lock-free.
- The LLM gateway records every call to the cost ledger and enforces the per-run
  budget (`stop_reason=budget_exceeded`).
- Vault blob and master key are separate; the blob is ciphertext at rest.
- No external daemons — every service is in the asyncio process.

## Configuration

`paths.py` resolves the data root from `AGENT_RUNTIME_HOME` (env) or defaults to
`~/.local/share/agent-runtime`. Tests point it at a temp dir. The vault master
key comes from `AGENT_RUNTIME_MASTER_KEY` or a `chmod 600` key file.
