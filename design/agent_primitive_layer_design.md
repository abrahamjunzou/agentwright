# Agent Primitive Layer — Design Document

## Overview

This document describes a two-layer architecture for building domain-specific agent instances from reusable, composable primitives.

**Layer 1 — Primitive Layer:** A registry of typed, templated building blocks. Each primitive defines a capability contract: what it needs to be configured, what it provides at runtime, and how it integrates with other primitives.

**Layer 2 — Domain Orchestration Layer:** Agents (or human operators) in a specific domain compose an agent instance by selecting and configuring a subset of primitives. The result is a fully-specified, runnable agent definition.

The design principle is that no single workflow is hardcoded. Instead, domain-layer agents reason over the primitive registry and compose instances dynamically. As model capability improves, the same primitive registry supports richer and more reliable agents without architectural changes.

---

## System Architecture

```
Domain Orchestration Layer
  - domain context (goals, tools available, compliance rules)
  - primitive selector agent
  - instance configuration agent
  - validation agent
        ↓ selects + configures
Primitive Layer
  - identity primitive
  - memory primitive
  - reasoning loop primitive
  - tool/connection primitive
  - compute primitive
  - trigger primitive
  - ui primitive
  - permission primitive
  - observability primitive
        ↓ instantiated as
Agent Instance
  - runs with selected primitive set
  - executes tasks
  - updates state
  - logs to audit
```

---

## Layer 1: Primitive Definitions

Each primitive is a template with three sections:

- **Config schema** — what must be provided at instantiation time
- **Runtime contract** — what the primitive exposes during a run
- **Dependencies** — which other primitives this one requires

---

### Primitive 1: Identity

The agent's persistent anchor. Holds instructions, personality, run history pointer, and owner information.

```yaml
primitive: identity
version: "1.0"

config:
  name: string                  # human-readable agent name
  description: string           # purpose / role
  instructions: string          # canonical system prompt / standing orders
  owner_id: string              # user or team that owns this agent
  tags: list[string]            # optional labels (e.g. "sales", "finance")
  created_at: timestamp
  updated_at: timestamp

runtime:
  provides:
    - agent_id: uuid            # stable identity token for this agent
    - resolved_instructions: string   # instructions merged with feedback
    - run_count: int
    - last_run_at: timestamp

dependencies: []                # no dependencies; identity is the root primitive
```

---

### Primitive 2: Memory

Manages what the agent knows across runs. Includes short-term context, long-term structured state, and file-based artifacts.

```yaml
primitive: memory
version: "2.0"

config:
  short_term:
    window_turns: int           # how many recent turns to include raw
    include_tool_outputs: bool

  long_term:
    # Enable the store types this agent needs. universal_store recommended always true.
    document_store: bool        # TinyDB  — flexible states, execution step tracking
    vector_store: bool          # Chroma  — semantic embeddings, similarity recall
    graph_store: bool           # LadybugDB — entity ontologies, GraphRAG traversal
    universal_store: bool       # SurrealDB — unified cross-model queries; always true recommended
    compaction_strategy: enum[summarize, discard_oldest, hierarchical]
    max_storage_mb: int

  kv:
    lmdb:
      map_size_mb: int          # fixed at open time; 512 for most agents, 2048 for computer-use
      trace_tool_calls: bool
      trace_run_state: bool
    mem0:
      enabled: bool
      llm_model: string         # LLM used for preference synthesis; cheap model recommended
      context_decay_days: int | null   # null = no decay

  structured_state:
    enabled: bool               # stored as SCHEMAFULL tables in SurrealDB
    schema:
      - name: string
        type: enum[string, int, bool, json, timestamp]
        description: string

  search:
    semantic_top_k: int         # results to return from Chroma vector search
    graph_hop_depth: int        # max traversal depth for LadybugDB graph queries

runtime:
  provides:
    - context_snapshot: object              # assembled context for the current run
    - recall(query: string): list           # semantic search via Chroma
    - graph_query(cypher: string): list     # graph traversal via LadybugDB
    - surql(query: string, params): any     # cross-model query via SurrealDB
    - write_state(key, value)               # persist to SurrealDB structured state
    - read_state(key): any                  # read from SurrealDB structured state
    - kv_put(key: string, value: bytes)     # write to LMDB (sequential worker)
    - kv_get(key: string): bytes            # read from LMDB (non-blocking)
    - mem_add(messages: list)               # add to Mem0 (background task, non-blocking)
    - mem_search(query: string): list       # search Mem0 preferences
    - append_artifact(path, content)        # write to file-based workspace
    - load_artifact(path): string

dependencies:
  - identity
```

---

### Primitive 3: Reasoning Loop

The core execution engine. Defines how the agent calls the model, handles tool requests, and decides when a task is complete.

```yaml
primitive: reasoning_loop
version: "1.0"

config:
  model:
    provider: enum[anthropic, openai, google, bedrock, custom]
    model_id: string            # e.g. "claude-opus-4-8"
    temperature: float
    max_tokens: int

  loop:
    max_iterations: int         # circuit breaker
    tool_call_limit: int        # max tool calls per run
    parallel_tool_calls: bool

  cost_controls:
    max_cost_per_run_usd: float
    warn_at_pct: int            # warn at X% of budget

  compaction:
    strategy: enum[none, summarize, trim]
    trigger_at_context_pct: int  # compact when context is X% full

runtime:
  provides:
    - run(task: string, context: object): RunResult
    - RunResult:
        output: string
        tool_calls: list[ToolCall]
        tokens_used: int
        cost_usd: float
        stop_reason: enum[done, budget_exceeded, iteration_limit, tool_error, human_required]

dependencies:
  - identity
  - memory
```

---

### Primitive 4: Tool / Connection

Defines which external systems the agent can reach and how. Manages auth, schemas, and per-agent grants.

```yaml
primitive: tool_connection
version: "1.0"

config:
  connections:
    - id: string
      type: enum[native, mcp, http_api, pipedream, custom]
      display_name: string
      auth:
        method: enum[oauth2, api_key, bearer, basic, none]
        credential_ref: string    # pointer to secrets store
      tools:                      # list of tools exposed from this connection
        - name: string
          description: string
          input_schema: object    # JSON Schema
          requires_approval: bool # if true, human must approve before execution
      rate_limits:
        calls_per_minute: int
        calls_per_day: int

  http_api_connections:           # direct API access with doc-driven calls
    - id: string
      base_url: string
      docs_url: string            # used by model to learn API
      auth:
        method: enum[api_key, oauth2, bearer]
        credential_ref: string
      allowed_methods: list[enum[GET, POST, PUT, PATCH, DELETE]]
      max_response_bytes: int

runtime:
  provides:
    - list_tools(): list[ToolDef]
    - call_tool(tool_name: string, inputs: object): ToolResult
    - ToolResult:
        output: any
        success: bool
        error: string | null
        latency_ms: int
        audit_id: uuid

dependencies:
  - identity
  - permission
```

---

### Primitive 5: Compute

Provides the agent with an isolated execution environment: a shell, headless VM, and/or browser VM.

```yaml
primitive: compute
version: "1.0"

config:
  shell:
    enabled: bool
    allowed_commands: list[string]   # allowlist; empty = unrestricted
    timeout_seconds: int
    working_dir: string

  browser:
    enabled: bool
    persist_session: bool            # keep cookies/session across runs
    viewport: { width: int, height: int }
    timeout_seconds: int
    stealth_mode: bool

  vm:
    enabled: bool
    image: string                    # base OS image
    persist_between_runs: bool
    max_runtime_seconds: int
    memory_mb: int
    cpu_cores: float

  isolation:
    network: enum[full, allow_list, none]
    allowed_domains: list[string]    # if allow_list

runtime:
  provides:
    - exec_shell(command: string): ShellResult
    - browse(url: string): BrowseResult
    - screenshot(): image
    - click(selector: string)
    - type(selector: string, text: string)
    - download(url: string): FilePath
    - vm_exec(command: string): VMResult

dependencies:
  - identity
  - permission
```

---

### Primitive 6: Trigger

Defines how and when the agent is activated autonomously, without a direct user message.

```yaml
primitive: trigger
version: "1.0"

config:
  triggers:
    - id: string
      type: enum[schedule, webhook, email, slack, event, manual]

      schedule:                   # used when type=schedule
        cron: string              # standard cron expression
        timezone: string
        enabled: bool

      webhook:                    # used when type=webhook
        endpoint_path: string
        secret_ref: string        # HMAC verification secret
        method: enum[POST, GET]
        payload_schema: object

      email:                      # used when type=email
        monitored_address: string
        filter_from: list[string]
        filter_subject_contains: list[string]

      slack:                      # used when type=slack
        channel: string
        mention_only: bool
        filter_keywords: list[string]

      task_injection:             # what task text to inject when this trigger fires
        template: string          # supports {{trigger_payload}} substitution
        include_payload: bool

runtime:
  provides:
    - list_active_triggers(): list[TriggerDef]
    - fire_trigger(id: string, payload: object): RunHandle
    - pause_trigger(id: string)
    - resume_trigger(id: string)

dependencies:
  - identity
  - reasoning_loop
```

---

### Primitive 7: Generated UI

Allows the agent to produce interactive artifacts: dashboards, forms, review screens, and data visualizations.

```yaml
primitive: generated_ui
version: "1.0"

config:
  output_formats: list[enum[markdown, html, react, json_schema_form, table, chart]]
  approval_flows:
    enabled: bool
    default_timeout_seconds: int
    timeout_action: enum[auto_proceed, auto_cancel, escalate]

  embedding:
    mode: enum[inline, hosted_url, iframe]
    allowed_interactions: list[enum[view, edit, submit, approve, reject]]

runtime:
  provides:
    - render(spec: UISpec): UIArtifact
    - request_approval(artifact: UIArtifact, prompt: string): ApprovalResult
    - ApprovalResult:
        decision: enum[approved, rejected, timed_out]
        reviewer_id: string | null
        notes: string | null
        decided_at: timestamp

dependencies:
  - identity
```

---

### Primitive 8: Permission

Defines what the agent is allowed to do, who can authorize high-risk actions, and how access is scoped.

```yaml
primitive: permission
version: "1.0"

config:
  owner:
    user_id: string
    org_id: string | null
    team_ids: list[string]

  action_policy:
    default: enum[allow, deny, require_approval]
    overrides:
      - action_pattern: string      # glob or exact match e.g. "gmail.send_email"
        policy: enum[allow, deny, require_approval]
        approvers: list[string]     # user_ids allowed to approve

  data_policy:
    read_scopes: list[string]       # what data categories can be read
    write_scopes: list[string]
    pii_handling: enum[allow, redact, deny]
    data_retention_days: int

  cost_policy:
    max_daily_spend_usd: float
    max_per_run_spend_usd: float
    alert_user_at_usd: float

runtime:
  provides:
    - check(action: string, context: object): PolicyDecision
    - PolicyDecision:
        allowed: bool
        requires_approval: bool
        approvers: list[string] | null
        reason: string
    - request_approval(action: string, context: object): ApprovalResult
    - log_action(action: string, outcome: string, metadata: object)

dependencies:
  - identity
```

---

### Primitive 9: Observability

Captures structured traces of every meaningful event in an agent run for debugging, replay, and cost analysis.

```yaml
primitive: observability
version: "1.0"

config:
  trace_level: enum[minimal, standard, verbose]

  capture:
    llm_calls: bool
    tool_calls: bool
    context_snapshots: bool
    browser_actions: bool
    memory_reads_writes: bool
    permission_checks: bool
    trigger_events: bool
    cost_per_run: bool

  retention:
    runs_to_keep: int
    days_to_keep: int
    archive_on_expiry: bool

  export:
    enabled: bool
    destination: enum[local, s3, gcs, datadog, custom]
    endpoint: string | null
    credential_ref: string | null

  alerts:
    - id: string
      condition: string         # e.g. "cost_usd > 5.0", "stop_reason == tool_error"
      channel: enum[email, slack, webhook]
      destination: string

runtime:
  provides:
    - start_trace(run_id: uuid): TraceContext
    - log_event(trace: TraceContext, event: TraceEvent)
    - end_trace(trace: TraceContext, result: RunResult)
    - get_run(run_id: uuid): RunTrace
    - list_runs(agent_id: uuid, filters: object): list[RunSummary]
    - replay(run_id: uuid): RunTrace   # reconstructs what the agent saw

dependencies:
  - identity
```

---

## Layer 2: Domain Orchestration Layer

The domain layer is responsible for deciding which primitives a new agent instance needs, configuring each one for the domain's context, and validating the resulting composition.

### 2.1 Roles

| Role | Responsibility |
|---|---|
| **Primitive Selector** | Given a domain brief, determine the minimal required primitive set |
| **Instance Configurator** | Populate each selected primitive's config schema with domain-specific values |
| **Composition Validator** | Check dependency satisfaction, policy conflicts, and cost budget coherence |
| **Instance Registry** | Persist finalized agent definitions; issue agent IDs |

### 2.2 Domain Brief Schema

A domain brief is the input handed to the orchestration layer. It describes intent, not implementation.

```yaml
domain_brief:
  name: string                    # e.g. "Weekly Sales Pipeline Reviewer"
  goal: string                    # plain-language description of what this agent does
  domain: string                  # e.g. "sales", "finance", "engineering", "ops"

  constraints:
    cost_per_run_usd_max: float
    requires_human_approval_for: list[string]  # action patterns
    pii_handling: enum[allow, redact, deny]
    data_retention_days: int

  available_connections: list[string]    # connection IDs pre-authorized for this domain
  available_compute: list[enum[shell, browser, vm]]
  triggers_needed: list[enum[schedule, webhook, email, slack, event, manual]]
  ui_output_needed: bool
  long_lived: bool                # needs persistent identity/memory across runs
  sub_agent: bool                 # will be spawned by a parent agent, not a user
```

### 2.3 Orchestration Flow

```
Receive domain_brief
    ↓
Primitive Selector
  - evaluate goal, constraints, and available resources
  - output: list of required primitive types
    ↓
Instance Configurator
  - for each selected primitive:
      - load primitive template
      - fill config fields from domain_brief + defaults
      - flag any config requiring human input
    ↓
Composition Validator
  - check all dependency chains are satisfied
  - check no permission policy contradicts a tool's required_approval setting
  - check cost_per_run budget is consistent across reasoning_loop, tool, and permission primitives
  - output: validated AgentDefinition or list of validation errors
    ↓
(if valid)
Instance Registry
  - assign agent_id
  - persist AgentDefinition
  - return agent_id + activation endpoint
```

### 2.4 Primitive Selection Logic

The selector agent uses a decision matrix to determine which primitives are required vs. optional.

| Condition in domain brief | Required primitives |
|---|---|
| Always | `identity`, `reasoning_loop`, `observability`, `permission` |
| `long_lived: true` | `memory` |
| `available_connections` non-empty | `tool_connection` |
| `available_compute` non-empty | `compute` |
| `triggers_needed` non-empty | `trigger` |
| `ui_output_needed: true` | `generated_ui` |

The selector may also reason about implicit needs. For example, a goal that mentions "send a weekly report" implies `trigger` (schedule) and `generated_ui` even if the domain brief does not explicitly set them.

### 2.4.1 Memory Store Configuration Guidance

The selection matrix decides *whether* `memory` is included. Once it is, the Instance Configurator must decide *which stores* to enable. The v2.0 memory primitive is multi-backend, so this is no longer a single choice. The following decision table gives the configurator deterministic rules so that two configurators running on the same brief produce the same memory configuration.

| Signal in domain brief | Enable |
|---|---|
| `long_lived` or accumulating history | `universal_store`, `vector_store` |
| Learning user preferences / feedback loops | `kv.mem0.enabled` |
| Entities, relationships, knowledge graph | `graph_store` |
| Flexible-schema outputs (reports, logs) | `document_store` |
| `structured_state.enabled` (exact dedup, IDs) | `universal_store` (required), LMDB always active |
| Any agent with a reasoning loop | LMDB always active |

Rules:

- `universal_store` (SurrealDB) defaults to `true` for all long-lived agents — it consolidates the other stores for cross-model query.
- LMDB is **always active** as the operational-tracing backbone, regardless of other store settings; it has no enable flag.
- The first signal (`long_lived`) is derivable from the brief directly. The remaining signals (preferences, entities, flexible outputs) require inferring intent from the goal text and are the province of an LLM-backed configurator; a purely rule-based configurator enables only the `long_lived` row plus any store implied by `structured_state`.

### 2.5 Agent Definition Schema

The output of the orchestration layer is an `AgentDefinition`.

```yaml
agent_definition:
  agent_id: uuid
  name: string
  domain: string
  created_at: timestamp
  created_by: string              # user_id or orchestrator agent_id

  primitives:
    identity: IdentityConfig
    memory: MemoryConfig | null
    reasoning_loop: ReasoningLoopConfig
    tool_connection: ToolConnectionConfig | null
    compute: ComputeConfig | null
    trigger: TriggerConfig | null
    generated_ui: GeneratedUIConfig | null
    permission: PermissionConfig
    observability: ObservabilityConfig

  status: enum[draft, validated, active, paused, archived]
  validation_errors: list[string]
```

---

## Primitive Dependency Graph

```
identity
  └── memory
  └── reasoning_loop
        └── (calls all tools/primitives at runtime)
  └── tool_connection
        └── permission
  └── compute
        └── permission
  └── trigger
        └── reasoning_loop
  └── generated_ui
  └── permission
  └── observability
```

All primitives depend on `identity`. `permission` is a required dependency for any primitive that performs external actions (`tool_connection`, `compute`).

---

## Composition Invariants

These rules must hold for any valid `AgentDefinition`. The validator enforces them.

1. `identity` is always present.
2. `reasoning_loop` is always present.
3. `permission` is always present.
4. `observability` is always present.
5. If `tool_connection` is present, `permission` must define a policy for each tool's `requires_approval` flag.
6. If `trigger` is present, `reasoning_loop` must be present (already required by invariant 2).
7. If `memory.structured_state.enabled` is true, `memory.universal_store` must be `true` (structured state is backed by SurrealDB SCHEMAFULL tables in the runtime infrastructure layer).
8. `reasoning_loop.cost_controls.max_cost_per_run_usd` must be ≤ `permission.cost_policy.max_per_run_spend_usd`.
9. If `compute.browser.enabled` is true, `observability.capture.browser_actions` should be true (warn if false).
10. A `sub_agent` definition must not define its own `trigger` (it is activated by a parent, not a schedule).
11. If any `trigger` has type `email` or `slack`, `tool_connection` must be present with a connection that provides access to that channel (a `gmail`/`email` connection for `email`, a `slack` connection for `slack`). The scheduler does not poll channels directly — channel access is owned by `tool_connection`. Violation is an error, not a warning: an unbacked channel trigger passes every other invariant and then fails silently at runtime.
12. `compute.vm.enabled` must be `false`. VM execution is defined in the `compute` primitive contract but is not backed by the current runtime infrastructure layer (no VM service). The validator must reject any `AgentDefinition` that sets `compute.vm.enabled: true`.

---

## Versioning

Each primitive template carries a `version` field. Agent definitions record the primitive version at instantiation time. This allows:

- rolling out primitive upgrades without breaking existing agent instances;
- auditing which version of a primitive was active during a specific run;
- migrating agent definitions to new primitive versions explicitly.

Primitive versions follow semantic versioning. A minor bump is backward-compatible config addition. A major bump requires a migration step.

---

## Extension: Custom Primitives

A domain can register a custom primitive by supplying:

1. A config schema (JSON Schema format)
2. A runtime contract description (typed interface)
3. A declared dependency list (must reference existing primitives)
4. An executor (code or container reference)

Custom primitives participate in the same dependency graph, validation, and versioning system as built-in primitives. The orchestration layer treats them identically.

---

## Example: Instantiating a Domain Agent

**Domain brief:**

```yaml
name: "Daily Lead Enrichment Agent"
goal: "Each morning, pull new inbound leads, enrich them via API, update CRM, and post a Slack summary."
domain: "sales"
constraints:
  cost_per_run_usd_max: 2.00
  requires_human_approval_for: ["crm.delete_contact"]
  pii_handling: redact
  data_retention_days: 90
available_connections: ["gmail", "hubspot", "slack", "clearbit_api"]
available_compute: []
triggers_needed: ["schedule"]
ui_output_needed: false
long_lived: true
sub_agent: false
```

**Primitive selector output:**

```
required: identity, memory, reasoning_loop, tool_connection, trigger, permission, observability
optional (not needed): compute, generated_ui
```

**Resulting agent definition (abbreviated):**

```yaml
agent_id: "agt_01j..."
name: "Daily Lead Enrichment Agent"
domain: "sales"
primitives:
  identity:
    name: "Daily Lead Enrichment Agent"
    instructions: "Each morning, check for new inbound leads in Gmail and HubSpot. Enrich each lead using the Clearbit API. Update HubSpot with enrichment data. Post a summary to the #sales-pipeline Slack channel. Do not delete contacts without human approval."
    owner_id: "usr_..."
  memory:
    short_term:
      window_turns: 20
      include_tool_outputs: true
    long_term:
      backend: sql
      compaction_strategy: summarize
    structured_state:
      enabled: true
      schema:
        - name: processed_lead_ids
          type: json
          description: "IDs of leads processed in previous runs to avoid duplicates"
  reasoning_loop:
    model:
      provider: anthropic
      model_id: "claude-opus-4-8"
      temperature: 0.2
      max_tokens: 8192
    loop:
      max_iterations: 40
      tool_call_limit: 80
    cost_controls:
      max_cost_per_run_usd: 2.00
  tool_connection:
    connections:
      - id: "gmail"
        type: native
        auth: { method: oauth2, credential_ref: "cred_gmail_..." }
      - id: "hubspot"
        type: native
        auth: { method: api_key, credential_ref: "cred_hubspot_..." }
      - id: "slack"
        type: native
        auth: { method: oauth2, credential_ref: "cred_slack_..." }
      - id: "clearbit_api"
        type: http_api
        base_url: "https://person.clearbit.com/v2"
        docs_url: "https://dashboard.clearbit.com/docs"
        auth: { method: bearer, credential_ref: "cred_clearbit_..." }
  trigger:
    triggers:
      - id: "morning_run"
        type: schedule
        schedule:
          cron: "0 7 * * 1-5"
          timezone: "America/New_York"
          enabled: true
        task_injection:
          template: "Run the daily lead enrichment workflow."
  permission:
    action_policy:
      default: allow
      overrides:
        - action_pattern: "crm.delete_contact"
          policy: require_approval
          approvers: ["usr_manager_..."]
    data_policy:
      pii_handling: redact
      data_retention_days: 90
    cost_policy:
      max_per_run_spend_usd: 2.00
  observability:
    trace_level: standard
    capture:
      llm_calls: true
      tool_calls: true
      cost_per_run: true
    retention:
      days_to_keep: 90
status: validated
```

This definition is persisted. When the schedule trigger fires each morning, the runtime loads the definition, assembles context from the memory primitive, and executes the reasoning loop with the configured tool set. The structured state primitive ensures previously processed lead IDs are available, preventing duplicate enrichment across runs.
