"""Layer 0: Runtime Infrastructure.

In-process, zero-daemon services that back the primitive layer. Tier-A services
(SQLite system DB, File Store, Fernet vault, Event Bus, Scheduler, TinyDB, LMDB,
Shell Runner) are real implementations. The heavy Tier-B stores (vector, graph,
universal, preference), the LLM gateway, and the browser pool are defined as
protocols in ``interfaces`` with in-memory implementations here; real backends
(Chroma/SurrealDB/LadybugDB/Mem0/Anthropic/Playwright) drop in behind the same
protocols.

``AgentRuntime`` is the facade that wires them together.
"""

from .browser_pool import StubBrowserPool
from .document_store import DocumentStore, WriteWorker
from .event_bus import EventBus
from .file_store import FileStore
from .interfaces import (
    BrowserPool,
    CompletionResult,
    GraphStore,
    LLMGateway,
    Memory,
    PreferenceStore,
    SearchResult,
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
from .policy import PolicyDecision, evaluate
from .reasoning_engine import ReasoningEngine, RunResult
from .runtime import AgentRuntime
from .scheduler import Scheduler, next_cron
from .shell_runner import ShellResult, ShellRunner
from .system_db import SystemDB
from .vault import Vault, VaultError, generate_master_key

__all__ = [
    "AgentRuntime",
    "ReasoningEngine",
    "RunResult",
    "evaluate",
    "PolicyDecision",
    "SystemDB",
    "FileStore",
    "Vault",
    "VaultError",
    "generate_master_key",
    "EventBus",
    "Scheduler",
    "next_cron",
    "DocumentStore",
    "WriteWorker",
    "KVStore",
    "ShellRunner",
    "ShellResult",
    "StubBrowserPool",
    "FakeLLMGateway",
    "InMemoryVectorStore",
    "InMemoryGraphStore",
    "InMemoryUniversalStore",
    "InMemoryPreferenceStore",
    # protocols + dataclasses
    "VectorStore",
    "GraphStore",
    "UniversalStore",
    "PreferenceStore",
    "LLMGateway",
    "BrowserPool",
    "SearchResult",
    "Memory",
    "CompletionResult",
]
