# Context Engineering Strategies

This document defines five executable strategies for context engineering. Each strategy is internally consistent — you can follow it top to bottom without conflicting instructions. Strategies may share individual techniques; the difference is which techniques they combine and how they prioritize them.

**How to use this document:**
1. Read the "Use when" section of each strategy.
2. Pick the one that fits your agent's purpose.
3. Follow the architecture and execution steps exactly.
4. Do not mix strategies mid-execution. If your needs change, migrate to a new strategy cleanly.

---

## Strategy 1: Minimal Working Set

**Use when:**
- The task is bounded and short (single session, one conversation, one job).
- There is little or no history that needs to persist.
- You need low cost and low latency.
- You are answering a question, doing a focused research task, or executing a one-shot operation.

**Core idea:** Give the model only what it needs right now. Let it search for anything else.

### Context Layout

```
[Stable system prompt]         ← never changes per run; cache this
[Permissions block]            ← compact; what is allowed, what is not
[Runtime context message]      ← current time, timezone, session metadata
[Task description]             ← the current job, clearly stated
[Hints block]                  ← pointers to files/data the model may need
[Current turn]                 ← the user message or trigger input
```

### Rules

1. **Nothing goes into the prompt unless it is needed for this turn.** If it might be needed, put a pointer to it in the hints block instead.
2. **Dynamic data (timestamps, session IDs, live values) goes in the runtime context message, never in the system prompt.** The system prompt must remain stable so it can be cached.
3. **Give the model search tools.** It must be able to look up what it needs: file reads, API calls, web search, history search. Pointers are only useful if the model can act on them.
4. **Permissions must be stated explicitly and compactly.** One block, clear rules, no ambiguity. Do not scatter permission constraints across the prompt.
5. **Do not summarize what is not there.** If there is no history, do not include a history section. Empty or padded sections cost tokens and distract.

### Execution Steps

```
1. Build stable system prompt (identity, behavior rules, capability description).
2. Build permissions block (tools available, constraints, approval requirements).
3. Inject runtime context message (timestamp, session data, trigger metadata).
4. Write task description from current input.
5. Scan available files and data sources; write a compact hints block listing what is relevant.
6. Append the current user message or trigger input.
7. Execute. Do not carry state forward after the session ends.
```

### What not to do

- Do not stuff previous runs or history into context if there are no previous runs.
- Do not add a "memory" section if the agent has no persistent memory.
- Do not include dynamic data in the system prompt; it breaks caching.

---

## Strategy 2: Durable Long-Running Agent

**Use when:**
- The agent has been running for days, weeks, or months.
- History accumulates and must be recoverable.
- The agent must remember user preferences, prior decisions, exceptions, and processed items.
- Exact facts (IDs, preferences, rules) matter, not just general impressions.

**Core idea:** Keep the prompt small. Keep the full history in files and structured state. Give the model tools to zoom in when it needs detail.

### Context Layout

```
[Stable system prompt]         ← identity, behavior rules; never changes
[Permissions block]            ← compact capabilities and constraints
[Long-term summary]            ← LLM-generated summary of all history older than recent window; rarely changes
[Recent high-fidelity context] ← last N turns at full detail
[State references]             ← pointers to structured state (DB) the model may query
[File hints]                   ← compact list of relevant files/artifacts
[Runtime context message]      ← current time, trigger metadata
[Current turn]                 ← this run's task or user message
```

### Memory Architecture

```
File system:
  /history/          ← raw full conversation and run history, one file per run
  /summaries/        ← LLM-generated summaries, bucketed by time period
  /artifacts/        ← reports, scripts, outputs produced by the agent
  /files/            ← user-uploaded or generated files

Structured state (database):
  processed_items    ← IDs of items already handled; never re-processed
  preferences        ← user preferences and rules
  exceptions         ← items with special treatment
  prior_actions      ← audit log of actions taken
  external_ids       ← mappings to external systems
```

### Fidelity Rules

```
Current turn              → full fidelity (complete messages, all tool calls, all outputs)
Last 3–5 turns            → mostly full fidelity; drop verbose tool output bodies
Turns beyond recent window → stripped tool outputs, shortened messages, collapsed tool calls
Everything older           → LLM-generated summary only; pointer to full file in /history/
```

### Compaction Rule

Run compaction when the recent context bucket exceeds your token budget:
```
recent bucket too large
  → compact oldest turns into medium summary
medium summary too large
  → compact into long-term summary
long-term summary
  → always preserved; original files in /history/ never deleted
```

Compaction is incremental. Never recompute from scratch on every run.

### Recovery Tools

Give the model these tools so it can zoom in:
```
read_file(path)
search_history(query)
load_run(run_id)
query_state(sql)
update_state(sql)
```

The model should see a hint, decide it needs more detail, and call the tool. The prompt carries the map; the tools carry the territory.

### Execution Steps

```
1. Load stable system prompt (cached).
2. Load permissions block (cached).
3. Load or refresh long-term summary (changes rarely; usually cached).
4. Append recent high-fidelity turns (last N turns from /history/).
5. Build compact state references block (key DB tables, recent writes).
6. Build file hints block (relevant /artifacts/ or /files/).
7. Inject runtime context message.
8. Append current turn.
9. Execute.
10. After execution: append raw run to /history/; update DB state; run compaction if needed.
```

### What not to do

- Do not put all history into the prompt. It will grow without limit.
- Do not use mutable JSON in the system prompt for state. It breaks caching and grows until it corrupts.
- Do not delete raw history files after compaction. They are the recovery path.
- Do not store preferences or processed IDs only in summaries. Use structured state for anything that must be exact.

---

## Strategy 3: Triggered / Scheduled Agent

**Use when:**
- The agent runs on a schedule (hourly, daily, weekly) or on external events.
- The agent may run hundreds or thousands of times.
- Cost and latency must stay flat per run as history grows.
- Cache efficiency is a production requirement, not a nice-to-have.

**Core idea:** Cache everything that does not change between runs. Compact aggressively. Generate run instructions fresh from canonical state at execution time, not from stale persisted instructions.

### Context Layout

```
[Bucket A: stable system prompt]      ← identity, behavior rules; NEVER changes; always cached
[Bucket B: long-term summary]         ← compacted history; changes rarely; usually cached
[Bucket C: medium-term summary]       ← recent patterns; changes occasionally; often cached
[Bucket D: recent run state]          ← last 3–5 runs; changes every few runs
[Bucket E: runtime context message]   ← current time, trigger metadata, this run's task; always fresh
```

Buckets A and B must be stable across consecutive runs to get cache hits. Never inject dynamic data into Bucket A or B.

### Cache Rules

```
Bucket A → never modify between runs (no timestamps, no session IDs, no live values)
Bucket B → update only when compaction fires (not on every run)
Bucket C → update when medium window fills
Bucket D → update every run (accept cache miss here)
Bucket E → always fresh (accept cache miss here)
```

If you need to include something dynamic, put it in Bucket E, not Bucket A.

### Incremental Compaction

```
After each run:
  1. Append raw run output to /history/run-{id}.md
  2. Add run to Bucket D (recent)
  3. If Bucket D > threshold:
       compact Bucket D → update Bucket C
  4. If Bucket C > threshold:
       compact Bucket C → update Bucket B
  5. Write updated buckets to storage
  6. Never recompute Bucket A or B from scratch unless intentionally triggered
```

Compaction happens at bucket boundaries, not on every run. This keeps per-run cost flat.

### Just-in-Time Run Instructions

Do not persist sub-task instructions. Generate them fresh at run time from canonical state:

```
At run time, generate this run's instruction from:
  - Current trigger config (what this agent is scheduled to do)
  - Latest user preferences from structured state
  - Recent user corrections from memory
  - This run's input data or event

Do NOT use a previously generated and saved instruction from a prior run.
```

This prevents stale instructions from persisting after the user changes their mind.

### Structured State

Store exact facts in a database, not in context:
```
processed_ids     ← items already handled this cycle; check before acting
schedule_rules    ← user-defined timing or routing rules
last_run_status   ← success/failure/partial for idempotency
user_corrections  ← corrections the user made during prior runs
external_ids      ← IDs of objects in connected systems
```

Query state at the start of each run. Update state after each run. Never derive these values from summaries.

### Execution Steps

```
1. Load Bucket A (system prompt) — expect cache hit.
2. Load Bucket B (long-term summary) — expect cache hit if no compaction this run.
3. Load Bucket C (medium summary) — likely cache hit.
4. Load Bucket D (recent runs) — probably cache miss; accepted.
5. Query structured state for this run's relevant facts.
6. Generate this run's instruction just-in-time from trigger config + state + corrections.
7. Build Bucket E (runtime context: current time, this run's instruction, input data).
8. Execute.
9. Post-run: write raw output to /history/; update structured state; run compaction if needed.
```

### What not to do

- Do not put the current timestamp inside Bucket A or B. Every minute invalidates the cache.
- Do not persist generated sub-task instructions between runs. They go stale when users give corrections.
- Do not recompute all summaries from scratch on every run. Compaction must be incremental.
- Do not query structured state inside the prompt. Query it before building the prompt and inject only the relevant result.

---

## Strategy 4: Multi-Agent Orchestration

**Use when:**
- A main agent coordinates sub-agents that each do a scoped piece of work.
- Different sub-agents need different tool access and instruction sets.
- The main agent has large accumulated history that sub-agents should not receive.
- Instruction conflicts between main and sub-agents are a real risk.

**Core idea:** The main agent owns long-term identity and memory. Each sub-agent receives a minimal, purpose-built context slice. Instructions are generated fresh for each sub-agent invocation.

### Main Agent Context Layout

```
[Stable system prompt]         ← main agent identity, orchestration behavior
[Permissions block]            ← what the main agent is allowed to do and delegate
[Long-term summary]            ← accumulated history of all prior orchestration runs
[Recent orchestration state]   ← recent sub-agent completions, failures, partial results
[Runtime context message]      ← current time, this orchestration task
[Current task]                 ← the top-level goal for this run
```

The main agent's full context is NOT passed to sub-agents.

### Sub-Agent Context Layout (generated fresh per invocation)

```
[Sub-agent system prompt]      ← this sub-agent's identity and behavioral rules only
[Scoped permissions block]     ← only the tools and connections this sub-agent needs
[Task instruction]             ← what this sub-agent must accomplish (generated JIT)
[Relevant context slice]       ← only the history/state relevant to this sub-task
[Runtime context message]      ← current time, this sub-task's input
```

### Context Slicing Rules

When generating a sub-agent's context from the main agent's state:

```
Include:
  - The sub-agent's specific task instruction
  - Relevant user preferences from structured state
  - Recent outputs from prior sub-agents this sub-task depends on
  - Files or artifacts the sub-task needs
  - Permissions scoped to this sub-task only

Exclude:
  - Main agent's full conversation history
  - Other sub-agents' full outputs (include only what this sub-task depends on)
  - Global permissions that don't apply to this sub-task
  - Long-term summaries unless directly relevant
```

When in doubt, exclude. The sub-agent should request more context via tools if it needs it.

### Instruction Generation

Generate each sub-agent's task instruction at invocation time:

```
Input to instruction generator:
  - Main agent's current goal
  - This sub-task's role in the overall plan
  - Latest user corrections from structured state
  - Relevant context from prior sub-agent results
  - This sub-task's allowed tools and connections

Output:
  - Compact, unambiguous task instruction for this sub-agent
  - No references to other sub-agents' internal state
  - No contradictions with user's latest preferences
```

Never reuse a task instruction from a previous orchestration run.

### Conflict Prevention

Before deploying a new or modified instruction to any agent:

```
Check:
  Does this instruction contradict any existing instruction in the same context?
  Does it change tool-use behavior in a way that affects downstream sub-agents?
  Does it affect permissions granted or denied elsewhere?
  Does it reference state that may not be present in the sub-agent's slice?
  Has it been tested against the actual sub-task, not just reviewed in isolation?
```

One person or automated check must own this review. Instruction conflicts degrade probabilistically — they do not always fail on the first run, which makes them dangerous.

### Structured State for Coordination

```
sub_agent_results     ← outputs from completed sub-agents this run
sub_agent_status      ← pending / running / complete / failed per sub-task
shared_artifacts      ← files written by one sub-agent that others may read
coordination_rules    ← ordering, dependency, retry policy
```

Sub-agents write their outputs to structured state or the file system. The main agent reads from there. Sub-agents do not call each other directly.

### Execution Steps

```
Main agent:
1. Load main agent context (system prompt + permissions + long-term summary + recent state).
2. Decompose the current task into sub-tasks.
3. For each sub-task:
   a. Generate sub-agent context slice (identity + scoped permissions + JIT instruction + relevant slice).
   b. Invoke sub-agent with that context.
   c. Sub-agent executes and writes result to structured state / file system.
   d. Main agent reads result, updates orchestration state.
4. When all sub-tasks complete: synthesize final output.
5. Compact main agent state; update long-term summary if needed.
```

### What not to do

- Do not pass the main agent's full context to sub-agents. It is too large and contains irrelevant history.
- Do not let sub-agents share context with each other directly. Route through structured state.
- Do not reuse persisted sub-agent instructions across runs. Generate them fresh.
- Do not give sub-agents permissions they do not need for their specific task.
- Do not skip instruction conflict review when modifying any shared prompt.

---

## Strategy 5: Rich Interaction / Computer Use

**Use when:**
- The agent interacts with UIs, browsers, or operating system interfaces.
- The agent generates or consumes large artifacts: screenshots, DOM snapshots, file contents, shell output.
- Context can grow rapidly from visual data, downloaded content, and interaction traces.
- The agent must remember what it tried, what failed, and what succeeded.

**Core idea:** Keep large artifacts in files, not in the prompt. Expose only what the model needs for its current decision. Keep recent failures at full fidelity. Compact old interaction traces aggressively.

### Context Layout

```
[Stable system prompt]         ← agent identity, interaction behavior rules
[Permissions block]            ← what the agent can click, type, download, execute
[Task description]             ← what the agent is trying to accomplish
[Current interaction state]    ← recent actions and results at full fidelity
[Failure context]              ← last N failures with full detail (errors, screenshots, state)
[Compact prior trace]          ← summary of completed interaction steps
[Runtime context message]      ← current time, session metadata, active URL or app state
[Current decision point]       ← what the agent must decide or do next
```

### Artifact Rules

```
Screenshot           → save to /files/screenshots/{timestamp}.png; include in prompt only when needed for current decision
DOM snapshot         → save to /files/dom/{timestamp}.html; include compact summary or relevant excerpt in prompt
Shell output         → save to /files/shell/{run_id}.txt; include last N lines in prompt; pointer to full file
Downloaded file      → save to /files/downloads/{name}; include summary or relevant excerpt in prompt
Generated script     → save to /artifacts/scripts/{name}; include in prompt only when executing
Error output         → keep at full fidelity in prompt until resolved; then compact to summary
```

Never inline a full screenshot or full DOM blob into the prompt. Always store it first, then include either a compact summary or a targeted excerpt.

### Fidelity Rules for Interaction Traces

```
Current step                → full fidelity (action taken, result received, any error)
Last 3–5 steps              → full fidelity (needed for local reasoning about what to do next)
Steps 6–20                  → compact summary (action type + outcome, no raw output)
Steps beyond 20             → summary entry only (e.g., "Navigated to checkout, submitted form")
Session start state         → preserved exactly (initial URL, login state, session config)
```

Failures require special treatment: keep the last three failures at full fidelity regardless of their position in the trace. A failure that happened 15 steps ago may still be causing the current problem.

### Session State

Store session-level facts outside the prompt:
```
active_url             ← current browser URL; inject into runtime context message
session_cookies        ← stored externally; do not inline in prompt
login_status           ← boolean + timestamp
downloaded_files       ← list of paths with descriptions
completed_steps        ← structured log of finished interaction steps
pending_steps          ← what is left to do
```

### Recovery

Give the model tools to inspect prior interaction:
```
read_screenshot(path)
read_dom_excerpt(path, css_selector)
read_shell_output(path, tail=50)
read_file(path)
search_interaction_log(query)
```

When the model is stuck, it should search the interaction log or re-read a prior artifact rather than re-executing the entire interaction from scratch.

### Execution Steps

```
1. Load stable system prompt and permissions block (cached).
2. Build task description from user goal.
3. Load compact prior trace (summary of completed steps).
4. Load recent high-fidelity steps (last 3–5).
5. Load failure context (last 3 failures at full fidelity, if any).
6. Inject runtime context message (current time, active URL, session state).
7. State the current decision point explicitly.
8. Execute the next action.
9. Post-action:
   a. Save any generated artifacts (screenshots, DOM, shell output) to /files/.
   b. Append action + result to interaction log in structured state.
   c. Run trace compaction if recent window exceeds budget.
   d. Update session state (active URL, downloaded files, etc.).
```

### What not to do

- Do not inline full screenshots or DOM blobs into the prompt. Store them and include summaries or excerpts.
- Do not discard failure context. It must remain at full fidelity until the failure is resolved.
- Do not compact the current step or the last three steps. The model needs recent actions at full fidelity.
- Do not re-execute an entire interaction to recover state. Use the interaction log and stored artifacts.
- Do not store session cookies or credentials in the prompt.

---

## Choosing a Strategy

| Your situation | Strategy |
|---|---|
| Short, bounded task, no persistent history | 1 — Minimal Working Set |
| Long-lived agent, months of history, needs exact memory | 2 — Durable Long-Running Agent |
| Scheduled/triggered agent, runs hundreds of times | 3 — Triggered / Scheduled Agent |
| Main agent coordinating specialized sub-agents | 4 — Multi-Agent Orchestration |
| Browser, UI, or computer-use agent | 5 — Rich Interaction / Computer Use |

**If you are unsure:** Start with Strategy 1. Add the persistent memory mechanisms from Strategy 2 only when history starts to matter. Add caching discipline from Strategy 3 only when you are running on a schedule.

**If your agent spans multiple categories** (e.g., a triggered agent that also orchestrates sub-agents): apply Strategy 3 for the outer loop and Strategy 4 for the inner orchestration. Do not mix context layout rules from different strategies within the same context.

---

## Universal Rules (apply to every strategy)

These rules hold across all five strategies. If your chosen strategy says something that conflicts with a rule here, the universal rule takes precedence.

1. **Dynamic data never goes in the stable system prompt.** Timestamps, session IDs, live values, and anything that changes between runs always go in the runtime context message.

2. **Raw history is never deleted.** Compaction produces summaries. It does not replace originals. Full history must remain recoverable via file reads or tool calls.

3. **Exact facts belong in structured state, not in summaries.** Processed IDs, user preferences, permission grants, and audit facts must be queryable with precision. A summary is not a substitute.

4. **Permissions are stated once, compactly, explicitly.** Never scatter permission rules across multiple prompt sections. One block, clear rules.

5. **Instructions are reviewed before deployment.** Any new or modified instruction must be checked against all other instructions in the same context for conflicts. Instruction conflicts are runtime bugs that fail probabilistically.

6. **The model must have search tools.** Pointers and hints are only useful if the model can act on them. Every strategy depends on the model being able to retrieve what it needs on demand.

7. **Context layout is an economic system.** Every token costs money and time. Every unstable prefix costs a cache miss. Measure both.
