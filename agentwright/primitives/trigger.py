"""Primitive 6: Trigger — how/when the agent activates autonomously.

Schedule, webhook, email, slack, event, and manual trigger types. Depends on
identity and reasoning_loop.

Infrastructure constraint: email/slack trigger types are satisfied by polling
through a tool_connection (the scheduler does not poll inboxes/channels). The
validator warns if an email/slack trigger has no corresponding connection.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .base import PrimitiveTemplate


class ScheduleSpec(BaseModel):
    """Cron schedule (used when type=schedule)."""

    cron: str
    timezone: str = "UTC"
    enabled: bool = True


class WebhookSpec(BaseModel):
    """Inbound webhook (used when type=webhook)."""

    endpoint_path: str
    secret_ref: str | None = None  # HMAC verification secret
    method: Literal["POST", "GET"] = "POST"
    payload_schema: dict = Field(default_factory=dict)


class EmailSpec(BaseModel):
    """Email monitoring (used when type=email). Needs a gmail-like connection."""

    monitored_address: str
    filter_from: list[str] = Field(default_factory=list)
    filter_subject_contains: list[str] = Field(default_factory=list)


class SlackSpec(BaseModel):
    """Slack monitoring (used when type=slack). Needs a slack connection."""

    channel: str
    mention_only: bool = True
    filter_keywords: list[str] = Field(default_factory=list)


class TaskInjection(BaseModel):
    """The task text injected when the trigger fires."""

    template: str = ""  # supports {{trigger_payload}} substitution
    include_payload: bool = False


class TriggerDef(BaseModel):
    """One trigger definition. Only the sub-spec matching ``type`` is used."""

    id: str
    type: Literal["schedule", "webhook", "email", "slack", "event", "manual"]
    schedule: ScheduleSpec | None = None
    webhook: WebhookSpec | None = None
    email: EmailSpec | None = None
    slack: SlackSpec | None = None
    task_injection: TaskInjection = Field(default_factory=TaskInjection)


class TriggerConfig(BaseModel):
    """Full config schema for the trigger primitive (design Primitive 6)."""

    triggers: list[TriggerDef] = Field(default_factory=list)


TEMPLATE = PrimitiveTemplate(
    name="trigger",
    version="1.0",
    config_model=TriggerConfig,
    dependencies=("identity", "reasoning_loop"),
    runtime_contract=(
        "list_active_triggers(): list[TriggerDef]",
        "fire_trigger(id, payload): RunHandle",
        "pause_trigger(id)",
        "resume_trigger(id)",
    ),
)
