"""AgentRuntime — the Layer-0 facade (boot + per-agent instantiation).

Wires every runtime service together and implements the design's Per-Agent
Instantiation Sequence: given a validated ``AgentDefinition``, it provisions the
stores the definition's primitives require (driven by the definition, never
hardcoded) and registers the agent. The vm pre-flight check (composition
invariant 12) runs first, failing before any store is created.

All services are in-process; constructing an AgentRuntime starts no daemons.
Heavy stores use their in-memory implementations by default and can be replaced
with real backends by passing alternatives to the constructor.
"""

from __future__ import annotations

from pathlib import Path

from ..orchestration.agent_definition import AgentDefinition
from ..telemetry import get_tracer
from .browser_pool import StubBrowserPool
from .document_store import DocumentStore
from .event_bus import EventBus
from .file_store import FileStore
from .interfaces import (
    GraphStore,
    LLMGateway,
    PreferenceStore,
    UniversalStore,
    VectorStore,
)
from .kv_store import KVStore
from .llm_gateway import FakeLLMGateway
from .memory_backends import (
    InMemoryGraphStore,
    InMemoryPreferenceStore,
    InMemoryUniversalStore,
    InMemoryVectorStore,
)
from .paths import runtime_root
from .scheduler import Scheduler
from .shell_runner import ShellRunner
from .system_db import SystemDB
from .vault import Vault

_tracer = get_tracer()


class RuntimeError_(RuntimeError):
    """Raised when an agent cannot be instantiated (e.g. vm pre-flight reject)."""


class AgentRuntime:
    """In-process container of all Layer-0 services."""

    def __init__(
        self,
        root: str | Path | None = None,
        *,
        vault: Vault | None = None,
        vector_store: VectorStore | None = None,
        graph_store: GraphStore | None = None,
        universal_store: UniversalStore | None = None,
        preference_store: PreferenceStore | None = None,
        llm_gateway: LLMGateway | None = None,
    ) -> None:
        base = Path(root) if root is not None else runtime_root()

        # Real in-process Tier-A services.
        self.system_db = SystemDB(base / "db" / "system.db")
        self.file_store = FileStore(base / "files")
        self.document_store = DocumentStore(base / "tinydb")
        self.kv_store = KVStore(base / "lmdb")
        self.shell_runner = ShellRunner()
        self.event_bus = EventBus()
        self.scheduler = Scheduler(self.event_bus)
        self.browser_pool = StubBrowserPool(self.file_store)
        self.vault = vault  # may be None until a master key is configured

        # Tier-B stores: in-memory by default, swappable via constructor args.
        self.vector_store = vector_store or InMemoryVectorStore()
        self.graph_store = graph_store or InMemoryGraphStore()
        self.universal_store = universal_store or InMemoryUniversalStore()
        self.preference_store = preference_store or InMemoryPreferenceStore()
        self.llm_gateway = llm_gateway or FakeLLMGateway(self.system_db)

    def instantiate(self, definition: AgentDefinition) -> str:
        """Provision per-agent stores for a validated definition (design
        "Per-Agent Instantiation Sequence"). Returns the agent_id.

        Stores are created only for the primitives the definition actually
        carries — the definition drives the infrastructure, not the reverse.
        """
        with _tracer.start_as_current_span("runtime.instantiate") as span:
            agent_id = definition.agent_id
            span.set_attribute("agent_id", agent_id)
            prims = definition.primitives

            # Step 0 — vm pre-flight (composition invariant 12).
            if prims.compute is not None and prims.compute.vm.enabled:
                raise RuntimeError_(
                    "compute.vm.enabled is not supported — no VM service in the runtime"
                )

            # File store is always provisioned (history/traces/workspace).
            self.file_store.init_agent(agent_id)

            # LMDB is always active as the operational-tracing backbone.
            map_size = (
                prims.memory.kv.lmdb.map_size_mb if prims.memory is not None else 512
            )
            self.kv_store.open_agent(agent_id, map_size_mb=map_size)

            # Memory-backed stores only when the memory primitive enables them.
            if prims.memory is not None:
                self.document_store.init_agent(agent_id)
                lt = prims.memory.long_term
                # universal/vector/graph stores are lazy; structured state seeds
                # the universal store with the declared table.
                if prims.memory.structured_state.enabled and lt.universal_store:
                    for fld in prims.memory.structured_state.state_schema:
                        self.universal_store.write_state(agent_id, fld.name, None)

            # Triggers: enqueue schedule jobs onto the scheduler.
            if prims.trigger is not None:
                for trig in prims.trigger.triggers:
                    if trig.type == "schedule" and trig.schedule is not None:
                        self.scheduler.add_cron(
                            job_id=f"{agent_id}:{trig.id}",
                            cron=trig.schedule.cron,
                            timezone_name=trig.schedule.timezone,
                            agent_id=agent_id,
                            task_template=trig.task_injection.template,
                        )

            span.set_attribute("stores_provisioned", True)
            return agent_id

    def close(self) -> None:
        """Release file handles / DB connections."""
        self.system_db.close()
        self.document_store.close()
        self.kv_store.close()
