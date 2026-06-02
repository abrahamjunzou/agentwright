# Primitives Module (Layer 1)

The registry of typed, templated building blocks from
`design/agent_primitive_layer_design.md`. Each primitive defines a **config
schema** (what must be provided at instantiation), a **runtime contract** (what
it exposes during a run), and its **dependencies**.

This layer is pure data modelling + validation. It does **not** execute
primitives — the runtime infrastructure layer
(`design/agent_runtime_infrastructure.md`) is what backs each config with a real
store/service. The runtime contracts here are therefore documentation strings,
not callable interfaces.

## Structure

| File | Primitive | Version | Depends on |
|---|---|---|---|
| `identity.py` | identity | 1.0 | — (root) |
| `memory.py` | memory | 2.0 | identity |
| `reasoning_loop.py` | reasoning_loop | 1.0 | identity¹ |
| `tool_connection.py` | tool_connection | 1.0 | identity, permission |
| `compute.py` | compute | 1.0 | identity, permission |
| `trigger.py` | trigger | 1.0 | identity, reasoning_loop |
| `generated_ui.py` | generated_ui | 1.0 | identity |
| `permission.py` | permission | 1.0 | identity |
| `observability.py` | observability | 1.0 | identity |
| `base.py` | `PrimitiveTemplate` metadata wrapper | | |
| `registry.py` | `PRIMITIVE_REGISTRY` + dependency helpers | | |

¹ The design's Primitive-3 YAML also lists `memory`, but its dependency graph
and selection matrix treat memory as optional (only when `long_lived`). We
follow the graph + matrix; see `reasoning_loop.py`'s docstring.

## Key APIs

```python
from agentwright.primitives import (
    PRIMITIVE_REGISTRY,      # name -> PrimitiveTemplate
    get_template,            # name -> PrimitiveTemplate (raises KeyError)
    resolve_dependencies,    # set[name] -> set[name] (transitive closure)
    missing_dependencies,    # set[name] -> {name: [absent deps]}
    register,                # add a custom primitive template
    describe_primitive,      # name -> JSON-serializable description (+ config JSON Schema)
    catalogue,               # list of all primitive descriptions (discovery API)
)
```

Each primitive module exposes its pydantic config model (e.g. `IdentityConfig`)
and a `TEMPLATE` object.

### Discovery API (Layer-2 / agent consumers)

`catalogue()` and `describe_primitive(name)` return JSON-serializable
descriptions — name, version, dependencies, runtime contract, and the config
**JSON Schema** — so a domain-orchestration agent (including an LLM reasoning
over the registry) can enumerate the primitive layer and learn how to configure
each primitive without importing internals.

## Custom primitives

`register(template)` adds a custom primitive (design "Extension: Custom
Primitives"). Its declared dependencies must already be registered, mirroring
the rule that custom dependencies must reference existing primitives.

## Notes / design interpretations

- `memory.structured_state` exposes the design's YAML key `schema` via an alias;
  the Python attribute is `state_schema` (avoids shadowing pydantic's reserved
  `schema`). Use `model_dump(by_alias=True)` to emit `schema`.
- `compute.vm` and non-`local` `observability.export` are modelled here but
  flagged by the validator because the infrastructure layer does not back them.
