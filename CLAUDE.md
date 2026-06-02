# CLAUDE.md

Behavioral guidelines to reduce common LLM coding mistakes, followed by
project-specific instructions for agentwright.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.

---

# Project: AI Agent Primitives 

Read design docs under ./design

## Tech stack
- Python 3.11+
- FastAPI — backend / API
- Streamlit — quick UI dashboard when needed
- uv — manages the Python environment and all dependencies (do not use pip / poetry
  / raw venv directly)

## Engineering rules (non-negotiable)
1. **Test everything. Untested code is broken code.** Every function and behavior
   has a test. Run the full suite before treating work as done. Test the UI too —
   actually verify Streamlit renders and the flows work; do not assume.
2. **Readability over cleverness.** Simple, straightforward Python; avoid clever or
   complicated syntax. Every function and every class has a clear comment/docstring
   explaining what it does and why. Add enough comments that a reader new to the
   code can follow it.
3. **Observability always — log, metric, and trace.** Instrument all code with
   OpenTelemetry. Cover the key boundaries: trigger firing, case creation, step
   execution, state transitions, effect enactment, task completion. Use OTel
   auto-instrumentation for FastAPI to get breadth cheaply.
4. **Per-module README.** Every module has a README describing its functionality and
   structure. Update it whenever features are added or behavior changes.
5. **Minimal dependencies, clean MVP architecture.** Prefer the standard library and
   the chosen frameworks; justify any new dependency.
6. Create PRs for chagnes to .md files (exlduing README files and TECH_REPORTS files)

