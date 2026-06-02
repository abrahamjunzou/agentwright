"""Primitive 9: Observability — structured traces of every run.

Trace level, capture toggles, retention, export destination, and alerts.
Depends on identity.

Infrastructure constraint: only ``export.destination: local`` is backed by the
runtime layer. The validator WARNS (does not fail) for s3/gcs/datadog/custom.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .base import PrimitiveTemplate


class CaptureConfig(BaseModel):
    """Which event classes to capture in traces."""

    llm_calls: bool = True
    tool_calls: bool = True
    context_snapshots: bool = False
    browser_actions: bool = False
    memory_reads_writes: bool = False
    permission_checks: bool = True
    trigger_events: bool = True
    cost_per_run: bool = True


class RetentionConfig(BaseModel):
    """How long traces are kept."""

    runs_to_keep: int = 100
    days_to_keep: int = 90
    archive_on_expiry: bool = False


class ExportConfig(BaseModel):
    """Trace export. Only 'local' is backed by the infrastructure layer today."""

    enabled: bool = False
    destination: Literal["local", "s3", "gcs", "datadog", "custom"] = "local"
    endpoint: str | None = None
    credential_ref: str | None = None


class AlertConfig(BaseModel):
    """A condition-triggered alert routed to a channel."""

    id: str
    condition: str  # e.g. "cost_usd > 5.0"
    channel: Literal["email", "slack", "webhook"]
    destination: str


class ObservabilityConfig(BaseModel):
    """Full config schema for the observability primitive (design Primitive 9)."""

    trace_level: Literal["minimal", "standard", "verbose"] = "standard"
    capture: CaptureConfig = Field(default_factory=CaptureConfig)
    retention: RetentionConfig = Field(default_factory=RetentionConfig)
    export: ExportConfig = Field(default_factory=ExportConfig)
    alerts: list[AlertConfig] = Field(default_factory=list)


TEMPLATE = PrimitiveTemplate(
    name="observability",
    version="1.0",
    config_model=ObservabilityConfig,
    dependencies=("identity",),
    runtime_contract=(
        "start_trace(run_id): TraceContext",
        "log_event(trace, event)",
        "end_trace(trace, result)",
        "get_run(run_id): RunTrace",
        "list_runs(agent_id, filters): list[RunSummary]",
        "replay(run_id): RunTrace",
    ),
)
