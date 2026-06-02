# Agent Runtime Infrastructure Layer — Design Document

## Overview

This document defines **Layer 0: Runtime Infrastructure** — the service layer beneath the Primitive Layer described in `agent_primitive_layer_design.md`.

The design principle is a **fully in-process, zero-daemon architecture**. Every service runs inside the application's own Python asyncio event loop. There are no background OS services, no socket servers, no external database daemons. The entire runtime boots from a single Python process and terminates cleanly with it.

Technology selections follow `embedded_ai_data_tech_selections.md`. Where that document is silent, choices are made here and noted explicitly.

**Deployment target:** local Linux desktop (Ubuntu 22.04+ / Debian 12+).

---

## Full Architecture Stack

```
┌────────────────────────────────────────────────────────────────────┐
│                    Domain Orchestration Layer                       │
│            selector · configurator · validator                      │
└─────────────────────────────┬──────────────────────────────────────┘
                              │ selects + configures
┌─────────────────────────────▼──────────────────────────────────────┐
│                        Primitive Layer                              │
│  identity · memory · reasoning_loop · tool_connection               │
│  compute · trigger · generated_ui · permission · observability      │
└─────────────────────────────┬──────────────────────────────────────┘
                              │ calls into
┌─────────────────────────────▼──────────────────────────────────────┐
│                 Runtime Infrastructure Layer                        │
│                                                                     │
│         ┌────────────────────────────────────────────┐             │
│         │          Agent Orchestration Loop           │             │
│         │         [Asyncio Task Event Loop]           │             │
│         └───────────┬──────────────┬──────────┬──────┘             │
│                     │              │          │                     │
│            Document │     Semantic │    Graph │                     │
│    ┌──────────┐ ┌──────────┐ ┌──────────┐                          │
│    │  TinyDB  │ │  Chroma  │ │LadybugDB │                          │
│    │[Document]│ │ [Vector] │ │ [Graph]  │                          │
│    └────┬─────┘ └────┬─────┘ └────┬─────┘                          │
│         └────────────┼────────────┘                                │
│                      ▼                                              │
│         ┌────────────────────────┐                                 │
│         │       SurrealDB        │                                 │
│         │ [Universal Multi-Model]│                                 │
│         └───────────┬────────────┘                                 │
│                     │                                               │
│           ┌─────────┴──────────┐                                   │
│           ▼                    ▼                                    │
│     [ Mem0 ]             [ LMDB ]                                   │
│  (Preference Synthesis)  (Bare-Metal Tracing)                       │
│                               │                                     │
│                               ▼                                     │
│         ┌────────────────────────────────┐                         │
│         │    Fernet Cryptography Vault   │                         │
│         │         [Secret Store]         │                         │
│         └────────────────┬───────────────┘                         │
│                          │                                          │
│                          ▼                                          │
│         ┌────────────────────────────────┐                         │
│         │    Asyncio Priority Queue      │                         │
│         │         [Scheduler]            │                         │
│         └────────────────────────────────┘                         │
│                                                                     │
│  SQLite (system) · File Store · LLM Gateway                        │
│  Browser Pool · Shell Runner · Trace Store · Agent Registry        │
└────────────────────────────────────────────────────────────────────┘
                              │ runs on
┌─────────────────────────────▼──────────────────────────────────────┐
│                  Linux Desktop (OS + Hardware)                      │
│  Python asyncio · SQLite · SurrealDB · TinyDB · Chroma             │
│  LadybugDB · Mem0 · LMDB · Fernet · Playwright                     │
└────────────────────────────────────────────────────────────────────┘
```

---

## Service Map: Primitives → Infrastructure

| Primitive | Infrastructure services it calls |
|---|---|
| `identity` | SQLite (system.db), Agent Registry |
| `memory` | TinyDB, Chroma, LadybugDB, SurrealDB, Mem0, LMDB, File Store |
| `reasoning_loop` | LLM Gateway, SQLite (cost ledger) |
| `tool_connection` | Fernet Secret Store, SQLite (action audit) |
| `compute` | Shell Runner, Browser Pool |
| `trigger` | Asyncio Scheduler, Event Bus |
| `generated_ui` | File Store, Event Bus (approval futures) |
| `permission` | SQLite, Fernet Secret Store, Event Bus |
| `observability` | Trace Store (JSONL + File Store), LMDB |

---

## Data Flow: Read and Write Paths

### Write path

Each data type routes to its native store first, then consolidates into SurrealDB for cross-model query access.

```
Agent produces data
  │
  ├─ Flexible state / execution step → TinyDB          (sequential write worker)
  ├─ Text to embed / semantic memory → Chroma
  ├─ Entity relationship / ontology  → LadybugDB
  ├─ Operational trace / tool log    → LMDB             (sequential write worker, hot path)
  └─ User preference / learned fact  → Mem0             (background asyncio task, never blocking)
                                         │
                         All above also consolidated into SurrealDB
                         for unified cross-model query
```

### Read path

```
Agent needs data
  │
  ├─ Exact key lookup / dedup check  → LMDB             (sub-millisecond)
  ├─ Synthesized preferences / facts → Mem0             (Chroma semantic lookup)
  ├─ Semantic similarity search      → Chroma
  ├─ Graph traversal / entity lookup → LadybugDB
  ├─ Flexible document query         → TinyDB
  └─ Cross-model unified query       → SurrealDB        (SurrealQL across all types)
```

### Critical ordering rule

LMDB writes execute synchronously on the critical path. Mem0 writes invoke an LLM API call. These must never share the same coroutine execution path.

```
LMDB write  → synchronous, inline, always completes before next reasoning step
Mem0 write  → asyncio.create_task(...), fire-and-forget, never awaited on critical path
```

---

## Updated Memory Primitive Config

```yaml
primitive: memory
version: "2.0"

config:
  short_term:
    window_turns: int
    include_tool_outputs: bool

  long_term:
    document_store: bool       # TinyDB  — flexible states, execution tracking
    vector_store: bool         # Chroma  — semantic embeddings, history recall
    graph_store: bool          # LadybugDB — entity ontologies, GraphRAG
    universal_store: bool      # SurrealDB — cross-model queries; recommended always true
    compaction_strategy: enum[summarize, discard_oldest, hierarchical]
    max_storage_mb: int

  kv:
    lmdb:
      map_size_mb: int          # fixed at init; 512 for most agents, 2048 for computer-use
      trace_tool_calls: bool
      trace_run_state: bool
    mem0:
      enabled: bool
      llm_model: string         # cheap model for synthesis; claude-haiku recommended
      context_decay_days: int | null   # null = no decay

  structured_state:
    enabled: bool               # stored as SCHEMAFULL tables in SurrealDB
    schema:
      - name: string
        type: enum[string, int, bool, json, timestamp]
        description: string

  search:
    semantic_top_k: int         # results from Chroma
    graph_hop_depth: int        # max traversal depth in LadybugDB
```

---

## Storage Service 1: SQLite (System Database)

*Not mentioned in `embedded_ai_data_tech_selections.md`; chosen here. SQLite is Python stdlib — zero installation. Holds system-level metadata only; per-agent data lives in the specialized stores.*

**Backs:** `identity` (agent registry), `reasoning_loop` (cost ledger), `tool_connection` (action audit)

```
~/.local/share/agent-runtime/db/
  system.db       ← agent registry, runs, cost ledger, action audit
```

```sql
CREATE TABLE agents (
    agent_id    TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    domain      TEXT,
    definition  TEXT NOT NULL,   -- JSON AgentDefinition
    status      TEXT NOT NULL,   -- draft | validated | active | paused | archived
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE runs (
    run_id      TEXT PRIMARY KEY,
    agent_id    TEXT NOT NULL,
    trigger_id  TEXT,
    status      TEXT NOT NULL,   -- running | done | failed | cancelled
    started_at  TEXT NOT NULL,
    ended_at    TEXT,
    stop_reason TEXT,
    cost_usd    REAL,
    tokens_used INTEGER
);

CREATE TABLE cost_ledger (
    entry_id      TEXT PRIMARY KEY,
    agent_id      TEXT NOT NULL,
    run_id        TEXT NOT NULL,
    provider      TEXT NOT NULL,
    model_id      TEXT NOT NULL,
    input_tokens  INTEGER,
    output_tokens INTEGER,
    cost_usd      REAL,
    recorded_at   TEXT NOT NULL
);

CREATE TABLE action_audit (
    audit_id    TEXT PRIMARY KEY,
    agent_id    TEXT NOT NULL,
    run_id      TEXT NOT NULL,
    tool_name   TEXT NOT NULL,
    inputs_json TEXT,
    outcome     TEXT NOT NULL,   -- success | denied | approval_pending | failed
    cost_usd    REAL,
    executed_at TEXT NOT NULL
);
```

```bash
# No installation needed — Python stdlib.
python3 -c "import sqlite3; print('sqlite', sqlite3.sqlite_version)"
```

---

## Storage Service 2: TinyDB (Document Store)

**Role:** Schema-less operational scratchpad for flexible agent states, multi-agent step tracking, tool routing outputs, and raw pipeline trace logging.

**Backs:** `memory.document_store`, `observability` (step-level pipeline logs)

Runs inside the application memory space. Persists to a human-readable `.json` file. No port, no background process. **MIT License.**

```
~/.local/share/agent-runtime/tinydb/{agent_id}/
  states.json          ← transient agent variables, mid-run state
  step_tracking.json   ← multi-agent execution step records
  tool_outputs.json    ← raw tool routing outputs
  pipeline_traces.json ← pipeline-level trace logs
```

```python
tinydb_store.insert(agent_id, table: str, doc: dict) -> int
tinydb_store.get(agent_id, table: str, doc_id: int) -> dict
tinydb_store.search(agent_id, table: str, condition: Query) -> list[dict]
tinydb_store.update(agent_id, table: str, fields: dict, condition: Query)
tinydb_store.remove(agent_id, table: str, condition: Query)
tinydb_store.all(agent_id, table: str) -> list[dict]
```

**Thread safety:** TinyDB uses file locking. All writes route through a single sequential asyncio worker. Never write from two concurrent coroutines.

```python
async def tinydb_write_worker(queue: asyncio.Queue):
    while True:
        op = await queue.get()
        op.execute()           # synchronous TinyDB write
        queue.task_done()
```

```bash
pip install tinydb
```

---

## Storage Service 3: Chroma (Vector / Semantic Store)

**Role:** Semantic memory layer for embedding-based context recall, similarity search over history and summaries.

**Backs:** `memory.vector_store`, `memory.search` (semantic queries), Mem0 backend

Runs embedded in-process using a local persistence directory. No server needed. **Apache 2.0 License.**

```
~/.local/share/agent-runtime/chroma/{agent_id}/    ← agent's direct collections
~/.local/share/agent-runtime/mem0/chroma/           ← Mem0's collections (separate)
```

```python
chroma_store.upsert(agent_id, doc_id: str, text: str, metadata: dict)
chroma_store.query(agent_id, query_text: str, top_k: int, filters: dict) -> list[SearchResult]
chroma_store.delete(agent_id, doc_id: str)
# SearchResult: { id, text, score, metadata }
```

Default embedding model: `sentence-transformers/all-MiniLM-L6-v2` (~80 MB, downloaded on first use, then local).

```bash
pip install chromadb sentence-transformers
```

---

## Storage Service 4: LadybugDB (Graph Store)

**Role:** Strongly typed structural knowledge graph for entity ontologies and multi-hop analytical traversal.

**Backs:** `memory.graph_store`, GraphRAG context enrichment

LadybugDB is the active community successor to Kùzu, forked after Kùzu's core engineering team was acquired by Apple. Embedded, no server required, Cypher query language (queries written for Kùzu are portable to LadybugDB).

**Key capabilities:**
- **Multi-label nodes:** `(:Agent:ActiveContext)` — one node, multiple identities.
- **No-copy external attachments:** Query Parquet, Arrow, or DuckDB datasets in-place without ETL.
- **Native MCP server:** Exposes the graph directly as an MCP tool for LLM agents (Claude Code, Cursor).
- **Vectorized OLAP:** Columnar memory layout for fast analytical graph traversal.

```
~/.local/share/agent-runtime/ladybug/{agent_id}/    ← per-agent graph database
```

Schema is defined at agent instantiation from the domain brief:

```cypher
CREATE NODE TABLE Person  (id STRING PRIMARY KEY, name STRING, role STRING);
CREATE NODE TABLE Company (id STRING PRIMARY KEY, name STRING, domain STRING);
CREATE NODE TABLE Task    (id STRING PRIMARY KEY, description STRING, status STRING);

CREATE REL TABLE EMPLOYS    (FROM Company TO Person);
CREATE REL TABLE EXECUTED   (FROM Agent   TO Task);
CREATE REL TABLE DEPENDS_ON (FROM Task    TO Task,  weight DOUBLE);
CREATE REL TABLE RELATED_TO (FROM Topic   TO Topic, strength DOUBLE);
```

```python
ladybug_store.execute(agent_id, cypher: str, params: dict) -> QueryResult
ladybug_store.upsert_node(agent_id, label: str, props: dict)
ladybug_store.upsert_edge(agent_id, rel: str, from_id: str, to_id: str, props: dict)
ladybug_store.neighbors(agent_id, node_id: str, rel_type: str, depth: int) -> list
ladybug_store.path(agent_id, from_id: str, to_id: str) -> list
ladybug_store.subgraph(agent_id, node_ids: list[str]) -> Graph
```

```bash
pip install ladybugdb
```

---

## Storage Service 5: SurrealDB (Universal Multi-Model Core)

**Role:** Unified cross-model query layer. All data written to TinyDB, Chroma, and LadybugDB is also consolidated here, enabling SurrealQL queries across document, vector, graph, and relational data in a single statement.

**Backs:** `memory.universal_store`, `memory.structured_state`, all cross-model agent queries

Runs embedded in-process via `SurrealKV`. No server process, no port.

**License:** BSL 1.1, with a rolling 4-year automatic conversion to Apache 2.0 for every release. WASM-ready. Built-in row-level security and multi-tenant isolation.

```
~/.local/share/agent-runtime/surreal/{agent_id}/    ← SurrealKV embedded data per agent
```

### What SurrealDB consolidates

| Specialized store | SurrealDB equivalent |
|---|---|
| TinyDB documents | `SCHEMALESS TABLE` |
| Chroma vectors | `DEFINE INDEX ... HNSW` on embedding field |
| LadybugDB graph | `RELATE` + `->` traversal |
| Structured state | `SCHEMAFULL TABLE` with typed fields |
| Full-text search | `DEFINE SEARCH INDEX` (BM25 / TF-IDF) |

### SurrealQL examples

```sql
-- Structured state: track processed leads
DEFINE TABLE processed_leads SCHEMAFULL;
DEFINE FIELD email       ON processed_leads TYPE string;
DEFINE FIELD enriched_at ON processed_leads TYPE datetime;

CREATE processed_leads SET email = "bob@acme.com", enriched_at = time::now();

-- Flexible memory document:
CREATE memory SET
  content    = "User prefers weekly digest, not daily",
  category   = "preference",
  embedding  = [0.12, 0.34, ...],
  created_at = time::now();

-- Semantic vector search:
SELECT content, vector::similarity::cosine(embedding, $query_vec) AS score
FROM memory ORDER BY score DESC LIMIT 10;

-- Graph: relate entities
RELATE company:acme->employs->person:bob SET since = "2023-01-01";

-- Graph traversal:
SELECT ->employs->person->authored->topic.label AS topics
FROM company WHERE name = "Acme";

-- Cross-model: companies whose employees wrote topics near a query embedding
SELECT company.name, topic.label
FROM company->employs->person->authored->topic
WHERE vector::similarity::cosine(topic.embedding, $query_vec) > 0.8;
```

```python
surreal_store.query(agent_id, surql: str, params: dict) -> QueryResult
surreal_store.create(agent_id, table: str, data: dict) -> dict
surreal_store.select(agent_id, thing: str) -> dict | list
surreal_store.update(agent_id, thing: str, data: dict)
surreal_store.delete(agent_id, thing: str)
surreal_store.relate(agent_id, from_: str, rel: str, to: str, data: dict)
```

```bash
pip install surrealdb
# CLI for inspection and migrations:
curl -sSf https://install.surrealdb.com | sh
echo 'export PATH="$HOME/.surrealdb:$PATH"' >> ~/.bashrc && source ~/.bashrc
```

---

## Storage Service 6: Mem0 (Cognitive KV — Preference Synthesis)

**Role:** LLM-driven distillation of agent history into compact, searchable user preference facts.

**Backs:** `memory.kv.mem0` — surfaces relevant preferences at run start without injecting raw history

Mem0 dissects conversation history into atomic user preferences, deduplicates overlapping memories, resolves temporal conflicts, and enforces context decay. Runs locally with Chroma as its vector backend and a cheap LLM for synthesis (Claude Haiku by default).

**Why agent-native:** Instead of injecting 40 turns of raw history into context, the agent retrieves 5 synthesized preference facts from Mem0. Context windows stay short and focused.

### Write rule: never block the reasoning path

```python
# Correct — fire-and-forget background task:
asyncio.create_task(mem0_client.add(messages, agent_id=agent_id))

# Wrong — blocks on LLM API call, stalls the reasoning loop:
await mem0_client.add(messages, agent_id=agent_id)
```

```python
# Background worker (non-blocking on main loop):
asyncio.create_task(
    mem0_client.add(messages: list, agent_id: str, user_id: str | None, metadata: dict)
)

# Foreground retrieval (fast Chroma lookup, no LLM call):
mem0_client.search(query: str, agent_id: str, limit: int) -> list[Memory]
mem0_client.get_all(agent_id: str) -> list[Memory]
mem0_client.delete(memory_id: str)
# Memory: { id, memory, score, metadata, created_at, updated_at }
```

```bash
pip install mem0ai
```

---

## Storage Service 7: LMDB (Bare-Metal KV — Operational Tracing)

**Role:** High-velocity operational log and exact-key store for tool execution traces, run state, and processed ID deduplication.

**Backs:** `memory.kv.lmdb` — all hot-path writes that must persist immediately

LMDB uses `mmap` for direct zero-copy pointer access to disk. Writes are atomic and crash-proof via MVCC. If the agent crashes mid-run due to an LLM timeout or rate limit, LMDB state is never corrupted.

**Why agent-native:** Absorbs every tool call, state transition, and trace event on the critical path without blocking. Reads are non-blocking and concurrent. Sub-millisecond lookups for deduplication and cursor tracking.

### Constraints — must be respected at instantiation

1. **`map_size` is fixed at open time.** Cannot be resized while the database is open. Set 512 MB for most agents; 2 GB for computer-use agents with heavy tool output.
2. **One writer at a time per environment.** All LMDB writes for one agent route through its sequential write worker.
3. **Reads are always concurrent.** Multiple coroutines can read simultaneously with zero locking.

```
~/.local/share/agent-runtime/lmdb/{agent_id}.lmdb
```

### Key namespace convention

```
tool:{run_id}:{call_index}        → tool call params + result (JSON bytes)
state:{run_id}:{step}             → agent reasoning state snapshot
trace:{run_id}:{timestamp_us}     → microsecond trace event
processed:{table}:{external_id}   → deduplication flag (value: b"1")
cursor:{source}                   → last-processed position for a data source
```

```python
lmdb_store.get(agent_id, key: str) -> bytes | None        # concurrent, non-blocking
lmdb_store.put(agent_id, key: str, value: bytes)          # sequential worker only
lmdb_store.delete(agent_id, key: str)                     # sequential worker only
lmdb_store.exists(agent_id, key: str) -> bool             # concurrent, non-blocking
lmdb_store.scan_prefix(agent_id, prefix: str) -> list[tuple[str, bytes]]
lmdb_store.count_prefix(agent_id, prefix: str) -> int
```

```bash
pip install lmdb
```

---

## Service 8: Fernet Cryptography Vault (Secret Store)

**Role:** Zero-dependency, in-process encrypted credential store.

**Backs:** `tool_connection` (credential_ref resolution), `permission`

The vault is a single Fernet-encrypted JSON block file on disk. At boot, a master key is loaded from an environment variable or a `chmod 600` key file. All reads and writes go through the Python `cryptography` library. No OS-specific binaries, no GNOME Keyring, no `age`, no external tools.

The vault file and TinyDB `.json` files and Chroma directories all move together as a single portable bundle.

### Boot configuration (priority order)

```
1. Environment variable: AGENT_RUNTIME_MASTER_KEY  (base64 Fernet key) ← preferred
2. Key file: ~/.local/share/agent-runtime/secrets/master.key (chmod 600) ← fallback
3. Fail with setup instructions
```

```
~/.local/share/agent-runtime/secrets/
  vault.bin      ← Fernet-encrypted JSON block; useless without the master key
  master.key     ← chmod 600; used only if env var is unset
```

Vault JSON structure (decrypted in-memory only, never written to disk decrypted):

```json
{
  "cred_gmail_usr123":   "ya29.oauth-token...",
  "cred_hubspot_usr123": "pat-na1-...",
  "cred_slack_ws":       "xoxb-..."
}
```

```python
vault.get(ref: str) -> str           # decrypt in-memory, return value
vault.set(ref: str, value: str)      # decrypt, update, re-encrypt, write
vault.delete(ref: str)
vault.exists(ref: str) -> bool
vault.rotate_key(new_key: bytes)     # re-encrypt all entries with new master key
```

First-time key generation:

```bash
python3 -c "
from cryptography.fernet import Fernet
print(Fernet.generate_key().decode())
" > ~/.local/share/agent-runtime/secrets/master.key
chmod 600 ~/.local/share/agent-runtime/secrets/master.key
```

```bash
pip install cryptography
```

---

## Service 9: Asyncio Priority Queue (Scheduler)

**Role:** 100% in-process task scheduler for time-based triggers, delayed events, and deferred multi-agent routines.

**Backs:** `trigger` (schedule, event, and deferred types)

No systemd unit. No APScheduler. No aiohttp HTTP server. The scheduler is a single asyncio coroutine inside the main event loop. It maintains an `asyncio.PriorityQueue` sorted by execution epoch timestamps. Job definitions persist in TinyDB (or SurrealDB) so they survive process restarts.

```python
# Priority queue entry: (fire_timestamp: float, job_payload: dict)
scheduler_queue: asyncio.PriorityQueue = asyncio.PriorityQueue()

async def scheduler_worker():
    while True:
        fire_at, job = await scheduler_queue.get()
        delay = fire_at - time.time()
        if delay > 0:
            await asyncio.sleep(delay)
        asyncio.create_task(event_bus.publish("trigger.fired", job))
        if job.get("cron"):                          # re-queue recurring jobs
            next_fire = compute_next_cron(job["cron"], job["timezone"])
            await scheduler_queue.put((next_fire, job))

async def boot_scheduler():
    for job in load_persisted_jobs_from_tinydb():    # reload after restart
        next_fire = compute_next_cron(job["cron"], job["timezone"])
        await scheduler_queue.put((next_fire, job))
    asyncio.create_task(scheduler_worker())
```

### Webhook trigger handling (no HTTP server)

Webhooks from external systems arrive as tool results from the `tool_connection` primitive's HTTP API connection type. They are validated in-process and pushed directly into the event bus as in-memory notifications.

```python
async def handle_webhook_payload(trigger_id: str, payload: dict):
    job = load_trigger_definition(trigger_id)
    if verify_hmac(payload, job["secret"]):
        asyncio.create_task(event_bus.publish("trigger.fired", {**job, "payload": payload}))
```

### Email and Slack trigger types

The `trigger` primitive defines `type: email` and `type: slack` as valid trigger types. The scheduler does **not** poll email inboxes or Slack channels directly. These trigger types are satisfied by routing through `tool_connection`:

- An `email` trigger requires a `gmail` (or equivalent) connection in `tool_connection`. The agent's reasoning loop polls for new messages matching `filter_from` / `filter_subject_contains` and pushes a `trigger.fired` event onto the event bus when a match arrives.
- A `slack` trigger requires a `slack` connection. The agent polls the configured channel for mentions or keywords and fires the same way.

The scheduler owns timing and delivery; `tool_connection` owns the channel access. Do not attempt to configure `type: email` or `type: slack` without the corresponding connection defined in the agent's `tool_connection` primitive.

```python
scheduler.add_cron(job_id, cron: str, timezone: str, agent_id: str, task_template: str)
scheduler.add_delayed(job_id, delay_seconds: float, agent_id: str, task_template: str)
scheduler.cancel(job_id: str)
scheduler.list_jobs(agent_id: str) -> list[JobDef]
```

---

## Service 10: Event Bus

*Not mentioned in `embedded_ai_data_tech_selections.md`; chosen here as `asyncio.Queue` with per-event-type routing — consistent with the fully in-process design.*

**Backs:** `trigger` (run dispatch), `generated_ui` (approval flow), `permission` (human approval requests)

```python
event_bus: dict[str, asyncio.Queue] = defaultdict(asyncio.Queue)

async def publish(event_type: str, payload: dict):
    await event_bus[event_type].put(payload)

async def consume(event_type: str, timeout: float | None = None) -> dict:
    return await asyncio.wait_for(event_bus[event_type].get(), timeout=timeout)
```

### Approval flow

```python
# 1. Reasoning loop pauses, publishes request:
await event_bus.publish("approval.requested", {
    "approval_id": str(uuid4()),
    "agent_id": agent_id,
    "action": "gmail.send_email",
    "inputs": {...},
    "approvers": ["usr_manager_..."],
})

# 2. generated_ui renders and delivers approval card (Slack, email, etc.)

# 3. Approver responds → delivery mechanism publishes:
await event_bus.publish("approval.resolved", {
    "approval_id": ..., "decision": "approved", "reviewer_id": ...
})

# 4. Reasoning loop resumes:
result = await event_bus.consume("approval.resolved", timeout=3600)
```

---

## Service 11: LLM Gateway

*Not mentioned in `embedded_ai_data_tech_selections.md`; chosen here as an in-process Python module.*

**Backs:** `reasoning_loop`

**Responsibilities:**
- Route to correct provider from `reasoning_loop.model.provider`.
- Inject `cache_control: {"type": "ephemeral"}` on stable prompt buckets (A and B per `context_engineering_strategies.md`).
- Enforce `cost_controls.max_cost_per_run_usd`; set `stop_reason=budget_exceeded` if breached.
- Retry on 429 / 529 / 500 with exponential backoff (3 attempts, base 2 s).
- Write every call to `cost_ledger` in `system.db`.

```python
llm_gateway.complete(
    messages: list,
    model_id: str,
    max_tokens: int,
    temperature: float,
    tools: list | None,
    cost_budget_usd: float,
    run_id: str,
) -> CompletionResult
# CompletionResult: content, tool_calls, input_tokens, output_tokens,
#                   cost_usd, cache_hit_tokens, stop_reason
```

**Package rationale:**

| Package | Status | Reason |
|---|---|---|
| `anthropic` | Required | Default provider for all agent reasoning AND Mem0 synthesis (Claude Haiku). Both fail without it. |
| `openai` | Optional | Only needed if an agent definition sets `provider: openai`. Nothing in the default stack requires it. |
| `google-generativeai` | Optional | Only needed if an agent definition sets `provider: google`. |
| `boto3` | Optional | Only needed for Bedrock deployments inside AWS infrastructure. |

```bash
pip install anthropic                                    # required
pip install openai google-generativeai boto3            # optional; install as needed
```

---

## Service 12: File Store

*Not mentioned in `embedded_ai_data_tech_selections.md`; chosen here as local filesystem.*

**Backs:** `memory` (history, summaries, artifacts), `observability` (trace files), `generated_ui`

> **Primitive gap — `observability.export` destinations `s3`, `gcs`, `datadog`, `custom` not yet implemented.**
> The `observability` primitive allows `export.destination: enum[local, s3, gcs, datadog, custom]`. This infrastructure layer only backs `local` — traces are written to JSONL files in the File Store. The composition validator must warn (not fail) if an agent definition sets `export.destination` to anything other than `local`. Remote export requires adding an async export worker as a future infrastructure addition.

```
~/.local/share/agent-runtime/files/agents/{agent_id}/
  history/        ← raw run transcripts (.md, one per run)
  summaries/      ← LLM-generated compacted summaries, bucketed by time window
  artifacts/      ← reports, scripts, outputs produced by the agent
  uploads/        ← user-provided files
  traces/         ← observability JSONL trace files ({run_id}.jsonl)
  workspace/      ← scratch space; one subdirectory per run; cleared after run
```

```python
file_store.write(agent_id, path, content)
file_store.read(agent_id, path) -> str
file_store.list(agent_id, prefix) -> list[str]
file_store.delete(agent_id, path)
file_store.exists(agent_id, path) -> bool
```

---

## Service 13: Browser Pool

*Not mentioned in `embedded_ai_data_tech_selections.md`; chosen here as Playwright + Chromium.*

**Backs:** `compute` (browser-enabled agents)

Playwright manages per-agent browser contexts. Session persistence stored under `browser-profiles/{agent_id}/`. All screenshots and downloads are written to the File Store before being referenced in the prompt — never inlined (see `context_engineering_strategies.md` Strategy 5).

```bash
pip install playwright
playwright install chromium
playwright install-deps chromium
```

---

## Service 14: Shell Runner

*Not mentioned in `embedded_ai_data_tech_selections.md`; chosen here as Python subprocess + resource limits.*

**Backs:** `compute` (shell-enabled agents)

> **Primitive gap — `compute.vm` not yet implemented.**
> The `compute` primitive defines a `vm` section (`vm.enabled`, `vm.image`, `vm.persist_between_runs`, etc.) and exposes `vm_exec(command)` in its runtime contract. No VM service exists in this infrastructure layer. Agents must not set `compute.vm.enabled: true`; the composition validator must reject any `AgentDefinition` that does. VM support requires adding a Docker or QEMU-backed service as a future infrastructure addition.

```python
shell_runner.exec(
    command: str,
    working_dir: str,
    timeout_seconds: int,
    max_memory_mb: int,
    allowed_commands: list,
) -> ShellResult
# ShellResult: stdout, stderr, exit_code, timed_out, oom_killed
```

Each run gets an isolated working directory under `workspace/{run_id}/`. Cleaned after run unless `persist_between_runs: true`.

---

## Runtime Manifest

```yaml
# ~/.config/agent-runtime/runtime.yaml

storage:
  sql:
    driver: sqlite
    system_db: ~/.local/share/agent-runtime/db/system.db

  files:
    root: ~/.local/share/agent-runtime/files

  tinydb:
    db_dir: ~/.local/share/agent-runtime/tinydb
    write_queue_size: 1000

  chroma:
    persist_dir: ~/.local/share/agent-runtime/chroma
    embedding_model: sentence-transformers/all-MiniLM-L6-v2

  ladybug:
    db_dir: ~/.local/share/agent-runtime/ladybug

  surreal:
    mode: embedded
    path: ~/.local/share/agent-runtime/surreal
    namespace: agent_runtime

  mem0:
    llm_model: claude-haiku-4-5-20251001
    llm_api_key_env: ANTHROPIC_API_KEY
    embedder: sentence-transformers/all-MiniLM-L6-v2
    chroma_path: ~/.local/share/agent-runtime/mem0/chroma
    write_queue_size: 200

  lmdb:
    db_dir: ~/.local/share/agent-runtime/lmdb
    default_map_size_mb: 512      # increase to 2048 for computer-use agents

secrets:
  driver: fernet
  vault_path: ~/.local/share/agent-runtime/secrets/vault.bin
  master_key_env: AGENT_RUNTIME_MASTER_KEY
  master_key_file: ~/.local/share/agent-runtime/secrets/master.key

scheduler:
  driver: asyncio_priority_queue
  persistence_backend: tinydb
  job_table: scheduler_jobs

llm:
  providers:
    - id: anthropic
      api_key_env: ANTHROPIC_API_KEY
    - id: openai
      api_key_env: OPENAI_API_KEY
    - id: google
      api_key_env: GOOGLE_API_KEY
  default_provider: anthropic
  retry:
    max_attempts: 3
    backoff_base_seconds: 2

browser:
  driver: playwright
  browser_type: chromium
  profiles_dir: ~/.local/share/agent-runtime/browser-profiles
  headless: true
  max_concurrent_contexts: 3

shell:
  max_memory_mb: 512
  default_timeout_seconds: 30
  workspace_root: ~/.local/share/agent-runtime/workspace

observability:
  trace_dir: ~/.local/share/agent-runtime/files/agents
  max_trace_age_days: 30
```

---

## Bootstrap Procedure (Fresh Linux Desktop)

### Step 1: System packages

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-pip python3-venv sqlite3
```

### Step 2: Python environment

```bash
python3 -m venv ~/.local/share/agent-runtime/venv
source ~/.local/share/agent-runtime/venv/bin/activate

# Required packages:
pip install \
  anthropic \
  playwright \
  tinydb \
  chromadb sentence-transformers \
  ladybugdb \
  surrealdb \
  mem0ai \
  lmdb \
  cryptography \
  pyyaml

# Optional (install only for providers you use):
# pip install openai google-generativeai boto3
```

### Step 3: Browser runtime

```bash
playwright install chromium
playwright install-deps chromium
```

### Step 4: Directory structure

```bash
mkdir -p ~/.local/share/agent-runtime/{db,files/agents}
mkdir -p ~/.local/share/agent-runtime/{tinydb,chroma,ladybug,surreal,lmdb}
mkdir -p ~/.local/share/agent-runtime/{mem0/chroma,browser-profiles,workspace,secrets}
mkdir -p ~/.config/agent-runtime
```

### Step 5: Master encryption key

```bash
python3 -c "
from cryptography.fernet import Fernet
print(Fernet.generate_key().decode())
" > ~/.local/share/agent-runtime/secrets/master.key
chmod 600 ~/.local/share/agent-runtime/secrets/master.key

# Preferred: export as env var instead of relying on file:
export AGENT_RUNTIME_MASTER_KEY=$(cat ~/.local/share/agent-runtime/secrets/master.key)
echo 'export AGENT_RUNTIME_MASTER_KEY=$(cat ~/.local/share/agent-runtime/secrets/master.key)' >> ~/.profile
```

### Step 6: SurrealDB CLI

```bash
curl -sSf https://install.surrealdb.com | sh
echo 'export PATH="$HOME/.surrealdb:$PATH"' >> ~/.bashrc
source ~/.bashrc
surreal version
```

### Step 7: API key

```bash
echo 'export ANTHROPIC_API_KEY="sk-ant-..."' >> ~/.profile
source ~/.profile
```

### Step 8: Runtime manifest

```bash
# Write the runtime.yaml from the template above:
nano ~/.config/agent-runtime/runtime.yaml
```

### Step 9: Initialize all stores

```bash
python3 -m agent_runtime.bootstrap
# Expected output:
#   [OK] system.db initialized
#   [OK] Fernet vault reachable
#   [OK] TinyDB store reachable
#   [OK] Chroma embedded reachable
#   [OK] LadybugDB reachable
#   [OK] SurrealDB embedded reachable
#   [OK] LMDB reachable
#   [OK] Mem0 local mode reachable
#   [OK] LLM gateway: anthropic reachable
#   [OK] Playwright chromium reachable
#   [OK] Asyncio scheduler ready
#   Bootstrap complete — no external daemons running
```

---

## Per-Agent Instantiation Sequence

When `registry.create(definition)` is called:

```
0.  Pre-flight: reject the definition if compute.vm.enabled is true (no VM
    service exists — composition invariant 12). Fail early before any store is
    created.
1.  Assign agent_id (UUID)
2.  Write AgentDefinition to system.db — status=draft

3.  SQLite: ensure action_audit and run_index rows will reference agent_id

4.  TinyDB: create db_dir/{agent_id}/ with empty table files
    - states.json, step_tracking.json, tool_outputs.json, pipeline_traces.json

5.  Chroma: initialize collection for agent_id with configured embedding model

6.  LadybugDB: create ladybug/{agent_id}/ graph database
    - Execute CREATE NODE TABLE / CREATE REL TABLE from domain brief schema

7.  SurrealDB: open surreal/{agent_id}/ embedded db
    - USE NS agent_runtime DB {agent_id}
    - DEFINE SCHEMAFULL tables from memory.structured_state.schema
    - DEFINE SCHEMALESS tables for documents and memories
    - DEFINE INDEX HNSW on all embedding fields
    - DEFINE SEARCH INDEX on all text fields

8.  LMDB: open lmdb/{agent_id}.lmdb with configured map_size_mb
    - Cannot be resized after open — set correctly here

9.  Mem0: initialize collection for agent_id in mem0/chroma/

10. File Store: create directories
    - files/agents/{agent_id}/{history,summaries,artifacts,uploads,traces,workspace}/

11. If compute.browser.persist_session=true:
    - Create browser-profiles/{agent_id}/

12. If trigger is configured:
    - Compute first fire timestamps from cron expressions
    - Enqueue jobs into asyncio scheduler_queue
    - Persist job definitions to TinyDB scheduler_jobs table for restart survival

13. Update system.db agents table: status=active
14. Return agent_id
```

---

## Operational Commitments

These rules must hold for every agent. Violations cause data corruption, lock contention, or silent failures.

**1. TinyDB writes are sequential.**
TinyDB file locking allows only one writer. All writes go through the dedicated asyncio write worker queue. Never write from two concurrent coroutines.

**2. LMDB writes are sequential per agent.**
Each agent's LMDB environment allows one writer at a time. Route all LMDB writes for a given agent through its sequential write worker. Reads are always concurrent and non-blocking.

**3. Mem0 writes never block the reasoning loop.**
Mem0 calls an LLM API internally. Always dispatch with `asyncio.create_task`. Never `await` a Mem0 write from the critical reasoning path.

**4. LMDB `map_size` is declared at open and is fixed.**
512 MB default. 2 GB for computer-use agents. Cannot be resized while open. Set it correctly during instantiation.

**5. No external daemons, ever.**
No systemd unit. No Redis. No aiohttp server. If any deployment starts these as separate processes, the architecture contract is broken. Everything runs in the single Python asyncio event loop.

**6. Vault and master key are stored separately.**
`vault.bin` is the encrypted blob. `master.key` (or the env var) is the decryption key. Neither is useful without the other. Never commit either to version control.

---

## Package Summary

| Package | Required | Provides |
|---|---|---|
| `sqlite3` | Yes (stdlib) | System DB — agent registry, cost ledger, audit |
| `tinydb` | Yes | Document store |
| `chromadb` | Yes | Vector store + Mem0 backend |
| `sentence-transformers` | Yes | Embeddings for Chroma and Mem0 |
| `ladybugdb` | Yes | Graph store |
| `surrealdb` | Yes | Universal multi-model store |
| `mem0ai` | Yes | Cognitive KV — preference synthesis |
| `lmdb` | Yes | Bare-metal KV — operational tracing |
| `cryptography` | Yes | Fernet secret vault |
| `anthropic` | Yes | LLM gateway (default provider) + Mem0 synthesis model |
| `playwright` | Yes (if browser compute) | Browser pool |
| `openai` | Optional | LLM gateway — only if agent uses `provider: openai` |
| `google-generativeai` | Optional | LLM gateway — only if agent uses `provider: google` |
| `boto3` | Optional | LLM gateway — only for AWS Bedrock deployments |

Zero system binaries required beyond Python 3 and the Playwright browser install. No root access needed after the initial `apt-get install python3`. All runtime data lives under `~/.local/share/agent-runtime/`.
