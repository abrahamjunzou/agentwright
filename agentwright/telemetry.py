"""OpenTelemetry tracing helper for the primitive + orchestration layers.

Engineering rule 3 (CLAUDE.md) requires observability on the key boundaries.
For this layer the meaningful boundaries are the orchestration steps:
selection, configuration, validation, and registry persistence.

By default OpenTelemetry returns a no-op tracer when no ``TracerProvider`` has
been configured, so importing this module has zero side effects and needs no
running collector. Call :func:`enable_console_tracing` in a script or test to
see spans printed to stdout while debugging.
"""

from __future__ import annotations

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)

# Stable instrumentation scope name so all spans from this codebase group together.
_SCOPE = "agentwright"


def get_tracer(name: str = _SCOPE) -> trace.Tracer:
    """Return a tracer for the given scope.

    When no provider is configured this is a no-op tracer, so callers can wrap
    code in spans unconditionally without paying for an exporter.
    """
    return trace.get_tracer(name)


def enable_console_tracing() -> None:
    """Install a console-exporting tracer provider (for debugging only).

    Idempotent: OpenTelemetry only honours the first provider set per process,
    so calling this more than once is harmless.
    """
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
    trace.set_tracer_provider(provider)
