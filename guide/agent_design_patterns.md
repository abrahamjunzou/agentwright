# Agent Design Pattern Library

## How to Use This Document

You are an agent reading this document to choose your own design strategy.

1. Read the **Pattern Selection Guide** to pick the right pattern for your task.
2. Read your chosen pattern fully. Each pattern is self-contained.
3. Follow the rules, roles, and execution loop defined in that pattern.
4. Use **Shared Techniques** (at the end) for tool design, permissions, evaluation, and logging — these apply to every pattern.

Do not mix pattern architectures. Pick one pattern and execute it consistently.

---

## Pattern Selection Guide

| If your task is... | Use Pattern |
|---|---|
| Complex, multi-step, unpredictable structure, auditability required | **Pattern 1: Controlled Planner-Orchestrator** |
| Predictable, linear, each stage cleanly feeds the next | **Pattern 2: Sequential Pipeline** |
| Multiple parallel or semi-parallel subtasks, single decision authority | **Pattern 3: Supervisor-Worker** |
| Long-running, research-heavy, or asynchronous multi-agent collaboration | **Pattern 4: Blackboard Shared State** |
| High-stakes output, quality-critical reasoning, final synthesis or review | **Pattern 5: Critique-Revision** |

---

---

# Pattern 1: Controlled Planner-Orchestrator (CPO)

## When to Use

Choose this pattern when:
- The full set of steps is not known at the start
- Auditability and control over every step are required
- Tasks can fail and require replanning
- Some tasks require human approval before execution
- The workflow involves 3–8 distinct phases

## Architecture

```
User Goal
  ↓
Planner Agent        → produces a structured JSON plan
  ↓
Plan Validator       → rejects invalid or vague plans
  ↓
Orchestrator         → governs execution order and dependencies
  ↓
Specialist Agents    → execute individual tasks
  ↓
Evaluator            → checks task output against success criteria
  ↓
Replanner            → adjusts the plan if a task fails
  ↓
Final Output
```

## Roles

### Planner Agent
- Receives the user goal plus the available task primitives and tools
- Produces a structured JSON plan using only predefined primitives
- Does not execute anything
- Does not invent new tools or primitives
- Returns: goal, assumptions, clarifying questions if needed, list of tasks, done condition

### Plan Validator
- Checks: valid JSON, required fields, unique task IDs, valid task types, valid tools, valid dependencies, no circular dependencies, no vague objectives, done condition present
- Rejects vague objectives such as "understand the documents" or "analyze"
- A valid task must have: objective, output, success criteria, risk level

### Orchestrator
- Topologically sorts tasks by dependency
- Checks all dependencies are complete before starting a task
- Pauses for human approval on high-risk or side-effecting tasks
- Calls the appropriate specialist agent for each task
- Stores task results in state
- On failure: triggers retry if allowed, then triggers replanning

### Specialist Agent
- Executes its single assigned task
- Uses only the tools allowed for that task
- Returns structured output
- Includes source citations when required
- Reports failures explicitly — never silently proceeds

### Evaluator
- Checks each task result against its success criteria
- Validates output schema
- Classifies failure type if the task did not pass
- Recommends: continue, retry, replan, or ask human

### Replanner
- Activated only on task failure, blockage, or new observation
- Does not modify completed tasks
- Adds or revises only the remaining tasks
- Preserves the original user goal
- Returns an updated JSON plan

## Execution Loop

```
Load next task (in topological order)
  ↓
Check all dependencies are complete
  ↓
If task requires human approval → pause and request it
  ↓
Prepare task input from state
  ↓
Call specialist agent or tool
  ↓
Validate output schema
  ↓
Evaluate against success criteria
  ↓
Store result in state
  ↓
If passed → continue to next task
If failed and can retry → retry with instruction
If failed and cannot retry → trigger replanner
If done condition is met → stop
```

## Plan Format

```json
{
  "goal": "string",
  "assumptions": ["string"],
  "needs_clarification": false,
  "clarifying_questions": [],
  "tasks": [
    {
      "id": "T1",
      "task_type": "parse_document",
      "name": "string",
      "objective": "string — specific and concrete",
      "input": ["string or task id"],
      "output": "string",
      "tools": ["string"],
      "depends_on": ["T_id"],
      "success_criteria": ["string"],
      "risk": "low|medium|high",
      "requires_human_approval": false
    }
  ],
  "done_condition": "string"
}
```

## Rules

1. The Planner proposes. The Orchestrator governs.
2. Use only predefined task primitives.
3. Every task must have a concrete, verifiable output.
4. No vague task objectives.
5. Replanning is allowed only after failure, blockage, or new observation.
6. Human approval is required before any external side-effecting action.
7. All task results, evaluations, and replanning decisions must be logged.

## Task Granularity

Use medium-grained tasks.

Too large — do not do this:
```
Build the whole agent.
```

Too small — do not do this:
```
Open file.
Read page 1.
Read page 2.
```

Correct granularity:
```
Parse all PDFs into page-level text and table blocks with source metadata.
Extract all numeric engineering parameters into a structured schema.
Compare extracted facts across documents and flag conflicts.
```

## Replanning Triggers

- Tool failed
- OCR quality is poor
- Required input is missing
- Extracted output fails schema validation
- Evidence is insufficient
- Verification fails
- Human clarification is required

---

---

# Pattern 2: Sequential Pipeline (SP)

## When to Use

Choose this pattern when:
- The workflow is predictable and linear
- Each stage has a clearly defined input (the previous stage's output)
- Stages do not need to communicate laterally — only forward
- Failure at any stage should halt the pipeline
- You can define typed handoff schemas between stages

## Architecture

```
User Input
  ↓
Stage 1 Agent
  ↓  [typed output]
Stage 2 Agent
  ↓  [typed output]
Stage 3 Agent
  ↓  [typed output]
...
  ↓
Final Output
```

## Roles

### Pipeline Designer (you, at design time)
- Define the stages in order
- Define the typed output schema for each stage
- Define the success criteria for each stage handoff
- Define what halts the pipeline (hard failure) versus what continues with a flag (soft failure)

### Stage Agent
- Receives one typed input from the previous stage
- Executes its single responsibility
- Produces one typed output
- Validates its own output before passing it forward
- If output fails validation: halts and reports — does not pass bad data forward

### Pipeline Runner
- Calls stages in order
- Passes the output of stage N as the input of stage N+1
- On hard failure: halts and reports the failed stage and reason
- On soft failure: flags the issue in the output and continues

## Execution Loop

```
Prepare typed input for Stage 1
  ↓
Call Stage 1 Agent
  ↓
Validate Stage 1 output schema
  ↓
If validation fails → halt pipeline, report Stage 1 failure
  ↓
Pass Stage 1 output to Stage 2
  ↓
(repeat for each stage)
  ↓
Final stage output = pipeline result
```

## Stage Definition Format

```json
{
  "stage_id": "S1",
  "name": "string",
  "responsibility": "string — one clear job",
  "input_schema": {},
  "output_schema": {},
  "tools": ["string"],
  "success_criteria": ["string"],
  "on_failure": "halt|flag_and_continue"
}
```

## Example Stage Sequence

```
S1: Researcher Agent      → source_candidates
S2: Extractor Agent       → claim_evidence_table
S3: Verifier Agent        → verified_claims
S4: Writer Agent          → final_report
```

## Rules

1. Each stage has exactly one responsibility.
2. A stage never reaches back to a previous stage.
3. A stage never skips forward more than one step.
4. Output schema must be validated before the next stage starts.
5. Bad data is never passed forward. Halt rather than forward invalid output.
6. If a stage needs to flag uncertainty, it marks it in the output schema — it does not abort unless validation actually fails.

## When Not to Use This Pattern

Do not use this pattern if:
- Stages need to loop back based on results
- Some stages are optional based on conditions
- Tasks must run in parallel and merge results

Use Pattern 1 (CPO) or Pattern 3 (Supervisor-Worker) for those cases.

---

---

# Pattern 3: Supervisor-Worker (SW)

## When to Use

Choose this pattern when:
- Multiple subtasks can run in parallel or semi-parallel
- A single authority must decide what to do next after each batch of results returns
- Workers are interchangeable or specialized but do not need to coordinate with each other
- The supervisor needs full context to make routing decisions

## Architecture

```
User Goal
  ↓
Supervisor Agent
  ↓  [assigns task batch]
Worker Agent 1 | Worker Agent 2 | Worker Agent 3
  ↓  [results]
Supervisor Agent   ← decides next step
  ↓  [assigns next batch or terminates]
...
  ↓
Final Output (assembled by Supervisor)
```

## Roles

### Supervisor Agent
- Holds the full user goal and current state
- Decides which tasks to assign and to which workers
- Collects and evaluates all worker results
- Decides what to do next: assign more tasks, request clarification, or stop
- Assembles the final output
- Never does domain execution work itself — delegates to workers

### Worker Agent
- Receives a single assigned task with a typed input
- Executes the task using allowed tools
- Returns structured output to the supervisor
- Does not coordinate with other workers
- Does not decide what comes next

## Execution Loop

```
Supervisor receives user goal
  ↓
Supervisor produces first task batch
  ↓
Assign tasks to workers (may run in parallel)
  ↓
Workers execute and return results
  ↓
Supervisor evaluates all results
  ↓
Supervisor decides: assign next batch, retry, ask human, or finalize
  ↓
(repeat until done condition is met)
  ↓
Supervisor assembles final output
```

## Task Assignment Format

```json
{
  "worker_id": "string",
  "task": {
    "id": "string",
    "objective": "string",
    "input": {},
    "output_schema": {},
    "tools": ["string"],
    "success_criteria": ["string"]
  }
}
```

## Rules

1. Workers never communicate with each other — only with the supervisor.
2. The supervisor never executes domain tasks — only coordinates.
3. Every worker result is evaluated by the supervisor before the next step.
4. The supervisor can reassign a failed task to the same or a different worker.
5. The done condition is held by the supervisor, not by workers.
6. Workers must report failure explicitly — they do not silently produce partial output.

## When Not to Use This Pattern

Do not use this pattern if:
- Tasks are strictly sequential and each depends on the previous
- The workflow is linear and predictable with no branching

Use Pattern 2 (Sequential Pipeline) for that case.

---

---

# Pattern 4: Blackboard Shared State (BSS)

## When to Use

Choose this pattern when:
- The task is open-ended or research-heavy
- Multiple agents contribute incrementally to a shared knowledge base
- Agents work asynchronously or on different aspects of the same problem
- No single agent has the full picture — each reads and writes partial artifacts
- The orchestrator monitors progress rather than controlling step-by-step

## Architecture

```
User Goal
  ↓
Orchestrator          → initializes shared state, monitors progress
  ↓
Shared State (Blackboard)
  ↑  ↓
Agent A: reads blackboard, adds findings
Agent B: reads blackboard, adds findings
Agent C: reads blackboard, synthesizes
  ↓
Orchestrator          → detects done condition or triggers next phase
  ↓
Final Output
```

## Roles

### Orchestrator
- Initializes the blackboard with the user goal and initial artifacts
- Monitors the blackboard for progress
- Decides when to activate each agent based on what is available
- Detects the done condition
- Does not directly execute domain tasks

### Blackboard (Shared State)
- Holds all intermediate artifacts
- Each artifact has: type, content, source agent, timestamp, confidence level
- Agents read from and write to the blackboard
- All writes are append-only and logged (agents do not overwrite prior artifacts)

### Specialist Agent (Reader-Writer)
- Reads relevant artifacts from the blackboard
- Executes its specialized task
- Writes its output back to the blackboard with source metadata
- Does not delete or overwrite existing blackboard entries
- Reports what it produced and what it read

## Blackboard Artifact Format

```json
{
  "artifact_id": "string",
  "type": "string",
  "content": {},
  "produced_by": "agent_id",
  "timestamp": "ISO 8601",
  "confidence": "high|medium|low",
  "source_artifacts": ["artifact_id"]
}
```

## Shared State Object

```json
{
  "user_goal": "string",
  "constraints": [],
  "phase": "string",
  "artifacts": [],
  "agent_log": [],
  "done_condition": "string",
  "done": false
}
```

## Execution Loop

```
Orchestrator initializes blackboard
  ↓
Orchestrator activates Agent A (based on what is available)
  ↓
Agent A reads blackboard, produces artifact, writes to blackboard
  ↓
Orchestrator detects new artifact, activates next eligible agent
  ↓
(repeat — agents may run in parallel when their inputs are available)
  ↓
Orchestrator checks done condition against blackboard state
  ↓
If done → extract final output from blackboard
```

## Rules

1. Agents never overwrite existing blackboard entries — append only.
2. Every artifact includes source metadata (which agent, which inputs, confidence).
3. Agents only read artifacts marked as complete — not drafts in progress.
4. The orchestrator decides which agent to activate next — agents do not self-dispatch.
5. The done condition must be objective and checkable against blackboard content.
6. Conflicting artifacts are preserved — not silently resolved. The synthesis agent handles conflicts.

## When Not to Use This Pattern

Do not use this pattern if:
- Tasks are strictly sequential and simple
- You need tight step-by-step control with human approval at each gate

Use Pattern 1 (CPO) or Pattern 2 (Pipeline) for those cases.

---

---

# Pattern 5: Critique-Revision (CR)

## When to Use

Choose this pattern when:
- The output is high-stakes and quality-critical
- A single draft pass is not sufficient
- You need a systematic, criteria-driven review before final output
- Hallucination risk is significant
- The output must be verifiable against source evidence

## Architecture

```
Input (goal + context)
  ↓
Proposer Agent       → produces draft output
  ↓
Critic Agent         → reviews draft against objective criteria, returns defect list
  ↓
Reviser Agent        → applies defect fixes, does not rewrite passing sections
  ↓
Verifier Agent       → checks final output against facts and citations
  ↓
Final Output
```

One reflection cycle = Propose → Critique → Revise → Verify.

## Roles

### Proposer Agent
- Receives the full goal and all available context
- Produces a complete draft output
- Does not self-critique — hands off the draft immediately

### Critic Agent
- Reviews the draft against explicit, predefined success criteria
- Returns only actionable defects — not style preferences
- Does not rewrite anything — only flags issues
- Each defect must reference the specific failing criterion
- Halts the cycle if no new defects are found (do not manufacture issues)

### Reviser Agent
- Applies only the listed defects from the Critic
- Does not rewrite sections that already pass
- Preserves citations, evidence, and source references
- Returns the revised draft — not a full rewrite

### Verifier Agent
- Checks the revised draft against source facts and citations
- Confirms all factual claims have support
- Flags unsupported claims as low-confidence or removes them
- Confirms required output fields are present
- Returns: pass with final output, or fail with specific issue

## Critic Criteria Template

Critic agent must evaluate against all of these:
```
1. Does the output directly answer the user goal?
2. Are all factual claims supported by evidence or citations?
3. Are required output fields present and complete?
4. Are there missing edge cases or gaps?
5. Are there signs of hallucination or unsupported inference?
6. Is uncertainty clearly marked where applicable?
```

Return only actionable defects. Do not return generic suggestions.

## Reflection Stop Rules

Stop the critique-revision cycle when any of these are true:
- The verifier passes
- No new actionable defect is found in a critique round
- Maximum reflection rounds reached (default: 2)
- Human approval is required for the next step

Never run more than the maximum rounds. Endless self-critique degrades quality.

## Revision Instruction Format

```json
{
  "defects": [
    {
      "criterion": "string — which criterion failed",
      "location": "string — where in the draft",
      "issue": "string — specific problem",
      "fix": "string — specific required change"
    }
  ]
}
```

## Rules

1. The Proposer drafts. The Critic evaluates. The Reviser fixes. The Verifier confirms.
2. No role does another role's job.
3. The Critic returns only defects — not rewrites, not suggestions.
4. The Reviser changes only what the Critic flagged — nothing else.
5. Reflection stops at the maximum round limit regardless of output state.
6. A cycle that finds no new defects is a passed cycle — do not force critique.
7. Citations and source evidence are never removed during revision.

## When Not to Use This Pattern

Do not use this pattern as the primary architecture for a multi-step workflow. Use it as the final quality gate within another pattern (e.g., the last step of a Pipeline or CPO workflow).

Do not use it if:
- The output does not need quality verification
- Speed is critical and one good pass is sufficient

---

---

# Shared Techniques

These techniques apply to any pattern you choose. Use them consistently regardless of which pattern you selected.

---

## Task Primitive Set

Use this controlled set of task types. Do not invent new types outside this list.

```
clarify_goal
search
retrieve
read_file
parse_document
extract_structured_data
normalize_data
compare
verify
critique
generate_report
ask_human
execute_action
```

---

## Tool Design Rules

Every tool must define:

```
Name
Description
Input schema (typed)
Output schema (typed)
Permission level (0–3)
Timeout
Retry policy
Side-effect classification (true/false)
Audit log required (true/false)
```

Design specific tools. Do not create generic tools.

Do not do this:
```
do_anything(command: string)
```

Do this instead:
```
search_documents(query: string, top_k: integer)
extract_table_data(document_id: string, page: integer)
compare_facts(fact_a: object, fact_b: object)
```

---

## Permission Levels

```
Level 0 — Read-only
  search, retrieve, summarize, read file
  → Runs automatically

Level 1 — Local write
  create draft, generate document, save to local state
  → Runs automatically if local-only

Level 2 — External side effect
  send email, submit form, update CRM, call external API
  → Requires explicit human approval

Level 3 — High-risk
  payment, deletion, production deployment, legal/HR action
  → Requires strong approval and may be disabled by default
```

---

## Task Evaluation

Every task result must be evaluated before the next step proceeds.

Evaluation must answer:
```
1. Did the task produce the expected output type?
2. Is the output in the expected schema?
3. Is the output complete (no required fields missing)?
4. Are citations or evidence present when required?
5. Are there signs of hallucination or unsupported inference?
6. Should the task be retried, replanned, or passed to human?
```

Evaluation result format:
```json
{
  "task_id": "string",
  "passed": true,
  "score": 0.0,
  "issues": ["string"],
  "recommended_action": "continue|retry|replan|ask_human",
  "retry_instruction": "string"
}
```

---

## Failure Classification

When a task fails, classify the failure type before deciding how to respond.

```
1. Intent understanding failure
2. Planning failure
3. Tool selection failure
4. Tool execution failure
5. Retrieval failure
6. Extraction failure
7. Normalization failure
8. Comparison failure
9. Verification failure
10. Formatting failure
11. Safety or permission failure
```

Use the failure type to determine the fix. Do not randomly change prompts. Track failure type, frequency, and resolution.

---

## State Object (All Patterns)

Maintain explicit state throughout execution. Do not rely only on conversation history.

```json
{
  "user_goal": "string",
  "constraints": [],
  "available_tools": [],
  "plan": [],
  "completed_tasks": [],
  "failed_tasks": [],
  "tool_results": [],
  "evidence": [],
  "draft_output": "string",
  "critique": "string",
  "final_output": "string",
  "eval_results": [],
  "audit_log": []
}
```

---

## Observability — Log Every Step

Every agent action must log:

```
Timestamp
Task ID
Task type
Agent or model used
Tool called
Tool input
Tool output summary
Latency
Validation result
Evaluation result
Error category (if failed)
Human approval status
```

The system must be able to answer:
```
Why did the agent take this step?
Which tool did it call and with what input?
What evidence did it use?
Which task failed and why?
What changed during replanning?
```

---

## Cost and Latency Controls

Apply these controls to every workflow:

```
Maximum tasks per plan
Maximum tool calls per task
Maximum replanning rounds
Maximum reflection rounds (for Pattern 5)
Timeout per tool call
Token budget per workflow
```

Model routing by task type:

```
Small model:
  intent classification, routing, simple extraction, schema cleanup

Medium model:
  summarization, standard task execution, drafting

Strong model:
  planning, hard reasoning, conflicting evidence resolution, final synthesis
```

---

## Hierarchical Decomposition (Two Levels Only)

Level 1 — Global plan created by the Planner or Supervisor.
Level 2 — Local sub-plan created by a specialist for its own task only.

A specialist may decompose its assigned task locally to execute it better.
A specialist must not change the global workflow.

---

## Vague Task Detection

Reject any task with an objective like:
```
"Understand the documents"
"Analyze the content"
"Research the topic"
"Look into the data"
```

Require objectives that name what to collect, compare, verify, or produce:
```
Good: "Extract numeric parameters with value, unit, source document, and page number."
Good: "Compare extracted facts across documents and flag any conflicting values."
Good: "Verify each discrepancy candidate against source text and remove unsupported findings."
```

---
