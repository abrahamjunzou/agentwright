# Agent Task Decomposition and Orchestration Guidelines

## Purpose

This document gives Claude Code implementation guidance for building an agent system that performs task decomposition in a controlled, production-oriented way.

The main design principle is:

> The human/system architect defines the components, tools, task types, schemas, and safety policies.  
> The Planner Agent decides how to compose those predefined components for a specific user goal.  
> The Orchestrator validates and controls execution.  
> Specialist agents execute assigned tasks, but do not freely redesign the whole workflow.

This avoids building an unpredictable “LLM with a big prompt.” The target system should be a controlled agent runtime with planning, execution, evaluation, replanning, and human approval where needed.

---

# 1. Core Architecture

The agent system should use this architecture:

```text
User Goal
  ↓
Planner Agent
  ↓
Structured Plan
  ↓
Plan Validator
  ↓
Orchestrator
  ↓
Task Executor / Specialist Agents
  ↓
Tool Layer
  ↓
Task Evaluator
  ↓
Replanner when needed
  ↓
Final Output
```

The Planner proposes a plan.  
The Orchestrator governs the plan.  
The Executor runs tasks.  
The Evaluator checks results.  
The Replanner adjusts only when needed.

---

# 2. Responsibility Boundaries

## Human/System Architect Defines

The system architect should define:

```text
- Allowed task primitives
- Allowed task types
- Available tools
- Input/output schemas
- Success criteria templates
- Risk levels
- Retry policies
- Human approval rules
- Logging and trace requirements
```

The system should not allow the Planner Agent to invent arbitrary new tools or uncontrolled workflow steps.

## Planner Agent Decides

The Planner Agent should decide:

```text
- Which predefined task primitives are needed
- The order of tasks
- Dependencies between tasks
- Which tools each task should use
- Expected output from each task
- Success criteria for each task
- Whether clarification is needed
```

The Planner Agent should only compose the building blocks provided to it.

## Orchestrator Controls

The Orchestrator should:

```text
- Validate the plan
- Reject invalid task types
- Reject nonexistent tools
- Check dependencies
- Enforce risk policies
- Enforce human approval requirements
- Execute tasks in order
- Stop when done condition is met
- Trigger replanning when tasks fail
```

The Orchestrator is the runtime authority.

## Specialist Agents Execute

Specialist agents should:

```text
- Execute assigned tasks
- Use only allowed tools
- Return structured output
- Include evidence/citations when required
- Report failure explicitly
```

Specialist agents may decompose their own assigned task locally, but they should not redesign the entire global workflow.

---

# 3. Recommended Task Primitives

Use a controlled primitive set. Start with these:

```text
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

Each primitive should have:

```text
- Description
- Input schema
- Output schema
- Allowed tools
- Success criteria
- Risk level
- Retry policy
```

Example primitive definition:

```json
{
  "task_type": "extract_structured_data",
  "description": "Extract structured facts from source material.",
  "input": "document blocks, retrieved passages, or raw text",
  "output": "structured facts with source references",
  "allowed_tools": ["llm_extractor", "table_extractor"],
  "success_criteria": [
    "Each extracted fact has a source reference",
    "Each numeric value has a unit when available",
    "Low-confidence facts are marked"
  ],
  "risk_level": "medium",
  "retry_policy": {
    "max_retries": 2,
    "retry_on": ["schema_validation_error", "missing_required_fields"]
  }
}
```

---

# 4. Planner Agent Prompt

Use the Planner Agent to turn a user goal into a structured, executable plan.

## Planner Prompt Template

```text
You are the planning module for an AI agent system.

Your job is to decompose the user's goal into small, executable tasks using only the provided task primitives and tools.

Rules:
1. Each task must have a clear objective.
2. Each task must produce a concrete output.
3. Each task must be verifiable.
4. Do not create vague tasks like "analyze", "understand", or "research" unless they specify exactly what to collect, compare, verify, or produce.
5. Prefer 3–8 tasks for normal workflows.
6. Use only available task primitives.
7. Use only available tools.
8. Mark dependencies between tasks.
9. Define success criteria for each task.
10. Mark risk level for each task.
11. Mark whether human approval is required.
12. Do not execute the task. Only create the plan.
13. Return JSON only.

User goal:
{{USER_GOAL}}

Available task primitives:
{{TASK_PRIMITIVES}}

Available tools:
{{TOOLS}}

Constraints:
{{CONSTRAINTS}}

Return JSON in this format:

{
  "goal": "...",
  "assumptions": [],
  "needs_clarification": false,
  "clarifying_questions": [],
  "tasks": [
    {
      "id": "T1",
      "task_type": "...",
      "name": "...",
      "objective": "...",
      "input": [],
      "output": "...",
      "tools": [],
      "depends_on": [],
      "success_criteria": [],
      "risk": "low|medium|high",
      "requires_human_approval": false
    }
  ],
  "done_condition": "..."
}
```

---

# 5. Plan Schema

The plan should be represented as structured JSON.

```json
{
  "goal": "string",
  "assumptions": ["string"],
  "needs_clarification": false,
  "clarifying_questions": ["string"],
  "tasks": [
    {
      "id": "T1",
      "task_type": "parse_document",
      "name": "Parse input documents",
      "objective": "Extract page-level text, tables, and metadata from input documents.",
      "input": ["uploaded_files"],
      "output": "document_blocks",
      "tools": ["pdf_parser", "ocr_tool"],
      "depends_on": [],
      "success_criteria": [
        "All provided files are parsed",
        "Each extracted block includes source document and page number",
        "Tables are preserved when possible"
      ],
      "risk": "medium",
      "requires_human_approval": false
    }
  ],
  "done_condition": "A final output satisfying the user goal has been generated and verified."
}
```

---

# 6. Plan Validation

Do not execute a plan until it passes validation.

The Plan Validator should check:

```text
- Plan is valid JSON
- Required fields exist
- Every task has a unique ID
- Every task_type is in the allowed primitive list
- Every tool exists in the available tool list
- Dependencies reference valid task IDs
- No circular dependencies exist
- Every task has objective, output, and success criteria
- No vague task objectives exist
- Risk levels are valid
- Human approval is required for side-effecting actions
- Done condition exists
```

Reject vague tasks.

Bad task:

```json
{
  "id": "T2",
  "task_type": "extract_structured_data",
  "name": "Analyze documents",
  "objective": "Understand the documents",
  "output": "analysis"
}
```

Good task:

```json
{
  "id": "T2",
  "task_type": "extract_structured_data",
  "name": "Extract technical facts",
  "objective": "Extract parameters, assumptions, standards, checklist items, and references from parsed document blocks.",
  "output": "technical_facts_with_citations",
  "success_criteria": [
    "Each fact has a source document and page number",
    "Each numeric fact has value and unit when available",
    "Each fact has a confidence score"
  ]
}
```

---

# 7. Task Granularity

Use medium-grained tasks.

Avoid tasks that are too large:

```text
Bad:
Build the whole agent.
```

Avoid tasks that are too small:

```text
Bad:
Open file.
Read page 1.
Read page 2.
Read page 3.
```

Prefer medium-grained tasks:

```text
Good:
Parse all PDFs into page-level text and table blocks with source metadata.

Good:
Extract all numeric engineering parameters into a structured schema.

Good:
Compare extracted facts across documents and flag conflicts.
```

Each task should be independently executable and testable.

---

# 8. Hierarchical Decomposition

Use two levels of decomposition.

## Level 1: Global Plan

Created by the Planner Agent.

Example:

```text
1. Parse documents
2. Extract facts
3. Normalize facts
4. Compare facts
5. Verify discrepancies
6. Generate final report
```

## Level 2: Local Sub-Plan

Created by a specialist executor only inside its assigned task.

Example:

```text
Task: Extract facts

Extractor local sub-plan:
1. Identify document sections
2. Extract tables
3. Extract numeric parameters
4. Extract standards and references
5. Attach source citations
6. Return facts in schema
```

The specialist can use local decomposition to execute its task better, but it should not change the global workflow.

---

# 9. Replanning

Task decomposition should not happen only once. The system should support replanning when execution produces new information or failures.

## Replanning Trigger Examples

```text
- Tool failed
- OCR quality is poor
- Required input is missing
- Extracted output fails schema validation
- Evidence is insufficient
- Verification fails
- Human clarification is required
```

## Replanner Prompt Template

```text
You are the replanning module for an AI agent system.

Original user goal:
{{USER_GOAL}}

Current plan:
{{PLAN}}

Completed tasks:
{{COMPLETED_TASKS}}

Failed or blocked task:
{{FAILED_TASK}}

Observation:
{{OBSERVATION}}

Revise the remaining plan.

Rules:
1. Do not change completed tasks.
2. Fix the failure directly.
3. Add only necessary new tasks.
4. Use only available task primitives.
5. Use only available tools.
6. Preserve the original goal.
7. Return updated JSON plan only.
```

Example:

```text
Original plan:
T1 Parse PDFs
T2 Extract facts
T3 Compare facts
T4 Generate report

Observation:
One PDF is scanned and text extraction quality is poor.

Updated plan:
T1a Run OCR on scanned PDF
T1b Check OCR quality
T2 Extract facts from OCR output and mark low-confidence text
T3 Compare facts
T4 Generate report
```

---

# 10. Tool Design

Tools should be typed and constrained.

A tool should have:

```text
- Name
- Description
- Input schema
- Output schema
- Permission level
- Timeout
- Retry policy
- Side-effect classification
- Audit log requirement
```

Example read-only tool:

```json
{
  "name": "search_documents",
  "description": "Search internal documents for relevant passages.",
  "input_schema": {
    "query": "string",
    "top_k": "integer"
  },
  "output_schema": {
    "results": [
      {
        "title": "string",
        "snippet": "string",
        "source_id": "string"
      }
    ]
  },
  "side_effect": false,
  "requires_approval": false
}
```

Example side-effecting tool:

```json
{
  "name": "send_email",
  "description": "Send an email to external recipients.",
  "side_effect": true,
  "requires_approval": true
}
```

Avoid generic tools such as:

```text
do_anything(command: string)
```

Prefer specific tools:

```text
search_jobs(query, location, seniority)
score_job_fit(job_description, resume_profile)
create_outreach_draft(contact, role, angle)
```

---

# 11. Permission Levels

Group tools by risk.

```text
Level 0: Read-only tools
- search
- retrieve
- summarize
- inspect files

Level 1: Local write tools
- create draft
- generate document
- create proposed task

Level 2: External side-effect tools
- send email
- submit application
- update CRM
- update production system

Level 3: High-risk tools
- payment
- deletion
- legal/HR action
- production deployment
```

Rules:

```text
- Level 0 can usually run automatically.
- Level 1 can run automatically if local-only.
- Level 2 requires explicit human approval.
- Level 3 should require stronger approval and may be disabled by default.
```

---

# 12. Task Execution Loop

Each task should follow this loop:

```text
Load task
  ↓
Check dependencies
  ↓
Prepare task input
  ↓
Call assigned tool or specialist agent
  ↓
Validate output schema
  ↓
Evaluate success criteria
  ↓
Store result
  ↓
Continue, retry, ask human, or replan
```

Pseudo-code:

```python
def execute_plan(plan, state):
    validated_plan = validate_plan(plan)

    for task in topological_sort(validated_plan.tasks):
        if not dependencies_complete(task, state):
            raise DependencyError(task.id)

        if task.requires_human_approval:
            request_human_approval(task)

        result = execute_task(task, state)
        validation = validate_task_output(task, result)
        evaluation = evaluate_task_success(task, result)

        state.store(task.id, result, validation, evaluation)

        if not evaluation.passed:
            if can_retry(task):
                retry_task(task, state)
            else:
                plan = replan(plan, task, evaluation, state)

    return state
```

---

# 13. Task Evaluation

Every task should be checked against success criteria.

Evaluation should answer:

```text
- Did the task produce the expected output?
- Is the output in the expected schema?
- Is the output complete enough?
- Are required citations/evidence present?
- Are there signs of hallucination?
- Should the task be retried?
- Should the plan be revised?
```

Example task evaluation result:

```json
{
  "task_id": "T2",
  "passed": false,
  "score": 0.62,
  "issues": [
    "Several numeric facts are missing units",
    "Three facts do not include page references"
  ],
  "recommended_action": "retry",
  "retry_instruction": "Re-extract facts and require value, unit, source document, and page number."
}
```

---

# 14. Error Classification

When a task or full workflow fails, classify the failure.

```text
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
11. Safety / permission failure
```

Use error classification to improve the system systematically.

Do not randomly tweak prompts. Track failure type, frequency, severity, and fix.

Example:

```text
Failure:
Agent produced a discrepancy report but missed a major mismatch.

Root cause:
Extraction failure. The table extractor skipped scanned table text.

Fix:
Add OCR fallback and table extraction confidence scoring.
```

---

# 15. Recommended Agent Roles

Start with a single orchestrated agent system. Do not begin with many independent agents.

Initial roles:

```text
Planner
Executor
Evaluator
Replanner
```

Later, split into specialist agents only when needed:

```text
Researcher Agent
Extractor Agent
Verifier Agent
Writer Agent
Critic Agent
```

Recommended production pattern:

```text
Supervisor / Orchestrator
├── Planner
├── Researcher or Retriever
├── Extractor
├── Verifier
├── Writer
└── Human Reviewer
```

Each role should have:

```text
- Clear responsibility
- Allowed tools
- Input schema
- Output schema
- Success criteria
- Permission boundaries
```

---

# 16. Multi-Agent Communication Patterns

Use multi-agent workflows only when the separation improves quality, speed, or safety.

## Recommended Default: Supervisor Pattern

```text
Supervisor → assigns tasks → agents return results → supervisor decides next step
```

Benefits:

```text
- Easier to control
- Easier to debug
- Easier to audit
- Easier to evaluate
```

## Pipeline Pattern

```text
Researcher → Extractor → Verifier → Writer
```

Best when the workflow is predictable.

## Blackboard Pattern

```text
Shared state
Agents read/write intermediate artifacts
Orchestrator monitors progress
```

Useful for research, coding, and long-running projects.

## Critique Pattern

```text
Proposer → Critic → Judge → Revised result
```

Useful for high-stakes reasoning and quality control.

Avoid uncontrolled peer-to-peer agent conversations unless there is a strong reason.

---

# 17. Reflection Pattern

Reflection should be used to improve outputs, but it should be tied to objective criteria.

Basic loop:

```text
Draft
  ↓
Critique
  ↓
Revise
  ↓
Verify
```

Good critic prompt:

```text
Review the draft against these criteria:
1. Does it answer the exact user goal?
2. Are all factual claims supported?
3. Are there missing edge cases?
4. Is there any hallucination risk?
5. Are required output fields present?
6. What specific changes are required?

Return only actionable defects.
```

Revision prompt:

```text
Revise the draft using only the listed defects.
Do not rewrite sections that already pass.
Preserve citations and evidence.
```

Reflection should not become endless self-talk. Use stop rules.

```text
- Maximum reflection rounds: 1–2
- Stop if evaluator passes
- Stop if no new actionable defect is found
- Stop if human approval is required
```

---

# 18. Context and State

Use explicit state rather than relying only on conversation history.

Recommended state object:

```json
{
  "user_goal": "",
  "constraints": [],
  "available_tools": [],
  "plan": [],
  "completed_tasks": [],
  "failed_tasks": [],
  "tool_results": [],
  "evidence": [],
  "draft_output": "",
  "critique": "",
  "final_output": "",
  "eval_results": [],
  "audit_log": []
}
```

Store:

```text
- User goal
- Plan versions
- Task inputs
- Task outputs
- Tool calls
- Tool results
- Evaluations
- Errors
- Replanning decisions
- Final output
```

This makes the system debuggable and auditable.

---

# 19. Observability and Audit Trail

Every agent step should log:

```text
- Timestamp
- Task ID
- Task type
- Model used
- Prompt version
- Tool called
- Tool input
- Tool output summary
- Latency
- Cost
- Validation result
- Evaluation result
- Error category
- Human approval status
```

This is essential for production debugging.

The system should be able to answer:

```text
- Why did the agent choose this step?
- Which tool did it call?
- What evidence did it use?
- Which task failed?
- What changed during replanning?
- How much did the workflow cost?
- How long did it take?
```

---

# 20. Cost and Latency Controls

Agent systems can become slow and expensive.

Add these controls:

```text
- Maximum number of tasks
- Maximum number of tool calls
- Maximum number of replans
- Maximum reflection rounds
- Timeout per tool
- Token budget per workflow
- Cost budget per workflow
- Early stopping when success criteria pass
```

Use model routing:

```text
Small model:
- intent classification
- routing
- simple extraction
- schema cleanup

Medium model:
- summarization
- standard task execution
- drafting

Strong model:
- planning
- hard reasoning
- conflicting evidence resolution
- final synthesis
```

---

# 21. Example: Engineering Document Review Agent

For a document discrepancy detection prototype, the Planner should produce a plan like this:

```json
{
  "goal": "Find cross-document engineering mismatches with citations",
  "assumptions": [
    "At least two engineering PDFs are available"
  ],
  "needs_clarification": false,
  "clarifying_questions": [],
  "tasks": [
    {
      "id": "T1",
      "task_type": "parse_document",
      "name": "Parse engineering PDFs",
      "objective": "Extract page-level text, tables, and metadata from all input PDFs.",
      "input": ["uploaded_pdfs"],
      "output": "document_blocks",
      "tools": ["pdf_parser", "ocr_tool"],
      "depends_on": [],
      "success_criteria": [
        "All PDFs are parsed",
        "Each block includes document name and page number",
        "Tables are extracted or marked as low-confidence"
      ],
      "risk": "medium",
      "requires_human_approval": false
    },
    {
      "id": "T2",
      "task_type": "extract_structured_data",
      "name": "Extract technical facts",
      "objective": "Extract parameters, assumptions, standards, checklist items, and references from parsed document blocks.",
      "input": ["T1"],
      "output": "technical_facts",
      "tools": ["llm_extractor"],
      "depends_on": ["T1"],
      "success_criteria": [
        "Each fact has type, name, value, unit if available, source document, and page number",
        "Each fact has confidence score",
        "No fact is accepted without source metadata"
      ],
      "risk": "high",
      "requires_human_approval": false
    },
    {
      "id": "T3",
      "task_type": "normalize_data",
      "name": "Normalize extracted facts",
      "objective": "Normalize units, names, aliases, and categories so facts can be compared across documents.",
      "input": ["T2"],
      "output": "normalized_facts",
      "tools": ["unit_normalizer", "llm_normalizer"],
      "depends_on": ["T2"],
      "success_criteria": [
        "Equivalent units are normalized",
        "Likely aliases are linked",
        "Original values are preserved"
      ],
      "risk": "medium",
      "requires_human_approval": false
    },
    {
      "id": "T4",
      "task_type": "compare",
      "name": "Detect discrepancy candidates",
      "objective": "Compare normalized facts across documents and flag conflicting values, assumptions, references, or missing checklist coverage.",
      "input": ["T3"],
      "output": "candidate_discrepancies",
      "tools": ["fact_comparator"],
      "depends_on": ["T3"],
      "success_criteria": [
        "Each candidate discrepancy references at least two source facts where applicable",
        "Each discrepancy has category and severity",
        "Missing checklist items identify the expected and missing source"
      ],
      "risk": "high",
      "requires_human_approval": false
    },
    {
      "id": "T5",
      "task_type": "verify",
      "name": "Verify discrepancy candidates",
      "objective": "Verify each candidate discrepancy against source text and remove unsupported findings.",
      "input": ["T4"],
      "output": "verified_discrepancies",
      "tools": ["source_verifier"],
      "depends_on": ["T4"],
      "success_criteria": [
        "Every verified discrepancy has source citation",
        "Unsupported candidates are rejected or marked low-confidence",
        "Final list contains no uncited claim"
      ],
      "risk": "high",
      "requires_human_approval": false
    },
    {
      "id": "T6",
      "task_type": "generate_report",
      "name": "Generate discrepancy report",
      "objective": "Create a concise report listing verified discrepancies with severity, explanation, and source citations.",
      "input": ["T5"],
      "output": "final_report",
      "tools": ["report_writer"],
      "depends_on": ["T5"],
      "success_criteria": [
        "Report contains all verified discrepancies",
        "Every finding has citations",
        "Report separates high-confidence and low-confidence findings"
      ],
      "risk": "low",
      "requires_human_approval": false
    }
  ],
  "done_condition": "A discrepancy report is produced with source citations for every finding."
}
```

---

# 22. Example: Research Agent

For a research agent, use this pattern:

```text
1. Clarify research question
2. Search for sources
3. Filter credible sources
4. Extract claims and evidence
5. Compare viewpoints
6. Identify gaps or contradictions
7. Write report
8. Verify citations
```

Plan example:

```json
{
  "goal": "Produce a grounded research report",
  "tasks": [
    {
      "id": "T1",
      "task_type": "search",
      "name": "Find relevant sources",
      "objective": "Find credible sources that directly address the research question.",
      "tools": ["web_search"],
      "output": "source_candidates",
      "depends_on": [],
      "success_criteria": [
        "At least 5 relevant sources",
        "Sources include primary sources where possible"
      ],
      "risk": "low",
      "requires_human_approval": false
    },
    {
      "id": "T2",
      "task_type": "extract_structured_data",
      "name": "Extract claims and evidence",
      "objective": "Extract key claims, supporting evidence, and source references from selected sources.",
      "tools": ["content_extractor"],
      "input": ["T1"],
      "output": "claim_evidence_table",
      "depends_on": ["T1"],
      "success_criteria": [
        "Each claim has a source",
        "Claims are not copied as long verbatim text",
        "Conflicting claims are preserved"
      ],
      "risk": "medium",
      "requires_human_approval": false
    },
    {
      "id": "T3",
      "task_type": "compare",
      "name": "Compare source viewpoints",
      "objective": "Compare claims across sources and identify consensus, disagreement, and uncertainty.",
      "input": ["T2"],
      "output": "synthesis_notes",
      "tools": ["llm_reasoner"],
      "depends_on": ["T2"],
      "success_criteria": [
        "Major viewpoints are represented",
        "Uncertainty is clearly marked",
        "No unsupported conclusions are added"
      ],
      "risk": "medium",
      "requires_human_approval": false
    },
    {
      "id": "T4",
      "task_type": "generate_report",
      "name": "Write final research report",
      "objective": "Generate a structured research report grounded in the extracted evidence.",
      "input": ["T3"],
      "output": "final_report",
      "tools": ["report_writer"],
      "depends_on": ["T3"],
      "success_criteria": [
        "Report answers the original question",
        "Important claims have citations",
        "Open questions are identified"
      ],
      "risk": "low",
      "requires_human_approval": false
    }
  ],
  "done_condition": "The final report answers the research question with citations and uncertainty clearly marked."
}
```

---

# 23. Implementation Checklist for Claude Code

Implement the system in this order:

## Phase 1: Core Data Structures

```text
- Define TaskPrimitive
- Define ToolSpec
- Define Task
- Define Plan
- Define TaskResult
- Define EvaluationResult
- Define AgentState
```

## Phase 2: Planner

```text
- Build planner prompt
- Call LLM
- Parse JSON plan
- Validate schema
- Reject invalid plans
```

## Phase 3: Orchestrator

```text
- Topologically sort tasks
- Check dependencies
- Execute tasks
- Store results
- Stop on failure or trigger retry/replan
```

## Phase 4: Tool Layer

```text
- Implement typed tool registry
- Validate tool inputs
- Validate tool outputs
- Add timeout and retry
- Add tool audit logs
```

## Phase 5: Evaluator

```text
- Evaluate task result against success criteria
- Validate output schema
- Classify failure type
- Recommend continue/retry/replan/ask_human
```

## Phase 6: Replanner

```text
- Build replanner prompt
- Preserve completed tasks
- Add or revise remaining tasks
- Validate new plan
```

## Phase 7: Human Approval

```text
- Detect side-effecting tasks
- Pause execution
- Show proposed action
- Require approval before execution
```

## Phase 8: Observability

```text
- Log every model call
- Log every tool call
- Log every task result
- Log every eval result
- Track latency and cost
```

---

# 24. Design Rules

Use these rules throughout the implementation:

```text
1. The Planner proposes; the Orchestrator governs.
2. The Planner must use predefined primitives only.
3. Every task must have concrete output.
4. Every task must be verifiable.
5. Every tool must be typed and permissioned.
6. Specialist agents execute tasks, not redesign the global workflow.
7. Replanning is allowed only after failure, blockage, or new observations.
8. Reflection must be tied to success criteria.
9. Human approval is required for external side effects.
10. All plans, tool calls, results, and evaluations must be logged.
```

---

# 25. Minimal Target Version

The first working version should support:

```text
- Planner prompt
- JSON plan output
- Plan validation
- Task execution loop
- Tool registry
- Task evaluation
- Basic replanning
- Trace logs
```

Do not start with:

```text
- Many independent agents
- Complex memory
- Autonomous side effects
- Unbounded recursive self-improvement
- Unrestricted tool execution
```

Start controlled. Add autonomy only after the system is measurable and debuggable.

---

# 26. Final Summary

The correct design is not:

```text
User goal → Planner invents arbitrary workflow → agents execute freely
```

The correct design is:

```text
User goal
  → Planner composes predefined primitives
  → Orchestrator validates
  → Specialist agents execute
  → Evaluator checks
  → Replanner adapts if needed
  → Human approves risky actions
```

The human/system architect defines the world.  
The Planner Agent composes within that world.  
The Orchestrator enforces control.  
The Evaluator provides quality discipline.  
The trace layer makes the whole system debuggable.

This is the foundation for a real production-grade agent system.
