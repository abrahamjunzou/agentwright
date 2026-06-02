# Orchestration Module (Layer 2)

The Domain Orchestration Layer from `design/agent_primitive_layer_design.md`
§2. It turns an intent-level **domain brief** into a validated, persistable
**agent definition** by selecting, configuring, and validating primitives.

## Pipeline

```
DomainBrief
   │  selector.select_primitives        (decision matrix, §2.4)
   ▼
selected primitive names
   │  configurator.configure            (fill config from brief + defaults, §2.3)
   ▼
AgentDefinition (status=draft)
   │  validator.validate + apply        (10 invariants + infra gaps)
   ▼
AgentDefinition (validated | draft+errors)
   │  instance_registry.create          (persist, §2.1)
   ▼
agent_id
```

`orchestrator.py` wires these together and wraps each step in an OpenTelemetry
span. `compose()` runs select→configure→validate; `register()` persists a valid
definition and marks it active; `compose_and_register()` does both, registering
only if valid.

## Files

| File | Responsibility |
|---|---|
| `domain_brief.py` | `DomainBrief` input schema (§2.2) |
| `agent_definition.py` | `AgentDefinition` / `PrimitiveSet` output schema (§2.5) |
| `selector.py` | Primitive Selector — decision matrix (§2.4) |
| `configurator.py` | Instance Configurator — fills configs, flags human input |
| `validator.py` | Composition Validator — invariants 1–10 + infra constraints |
| `instance_registry.py` | Instance Registry — SQLite-backed persistence |
| `orchestrator.py` | End-to-end pipeline + tracing |
| `ids.py` | `agt_…` id generation |

## Invariants enforced (validator)

Design invariants 1–12, plus a runtime-infrastructure warning:

- **error** — invariants 1–8, 10, and dependency completeness.
- **error** — invariant 11 (email/slack trigger with no channel connection).
- **error** — invariant 12 (`compute.vm.enabled` — no VM service in the infra layer).
- **warning** — invariant 9 (browser without `browser_actions` capture).
- **warning** — `observability.export.destination` other than `local`.

`validate()` returns a `ValidationResult(errors, warnings)`; `.ok` is true when
there are no errors. `apply()` writes the outcome onto the definition and sets
status to `validated` (clean) or leaves it `draft` (errors).

## Custom primitives (first-class)

Beyond the nine built-ins, a brief can pull in any **registered custom
primitive** (design "Extension: Custom Primitives") with no per-type code in the
configurator — the registry is the single source of truth:

```python
from agentwright import PrimitiveTemplate, register_primitive, DomainBrief, compose

register_primitive(PrimitiveTemplate(
    name="sentiment", version="1.0.0", config_model=SentimentConfig,
    dependencies=("identity",), runtime_contract=("scores text sentiment",),
))

brief = DomainBrief(name="reviews", goal="triage reviews", domain="support",
                    extra_primitives=["sentiment"],
                    custom_config={"sentiment": {"threshold": 0.8}})
defn = compose(brief, owner_id="u", created_by="u")   # defn.primitives.custom["sentiment"]
```

The selector adds `brief.extra_primitives` to the set; the configurator builds
each from the template's own `config_model` (applying `brief.custom_config`
overrides); `PrimitiveSet.custom` carries them (serialized as their concrete
subclass via `SerializeAsAny`); the version is pinned and the validator checks
the custom primitive's declared `dependencies` exactly as for a built-in. An
unsatisfied dependency is a normal validation error; an unregistered name raises.

## Human input flagging

The configurator cannot derive every required value from a brief (credential
refs, approver ids, cron expressions, monitored addresses). It inserts safe
placeholders and records the field paths in `AgentDefinition.human_input_required`
so a human or later step can complete them.

## Scope

The Instance Registry uses SQLite (stdlib, zero-daemon), matching the infra
layer's `system.db`. The heavier runtime services (TinyDB, Chroma, SurrealDB,
LMDB, Mem0, LLM gateway, browser pool, scheduler) are **not** built here — this
layer composes and validates definitions; it does not run agents.
