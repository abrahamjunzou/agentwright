"""Primitive 4: Tool / Connection — external systems the agent can reach.

Manages connection definitions, auth (by reference into the secret vault),
tool schemas, and per-tool approval flags. Depends on identity and permission:
every external action is gated by a permission policy.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .base import PrimitiveTemplate


class AuthConfig(BaseModel):
    """How a connection authenticates. credential_ref points into the vault."""

    method: Literal["oauth2", "api_key", "bearer", "basic", "none"] = "none"
    credential_ref: str | None = None


class ToolDef(BaseModel):
    """One tool exposed by a connection."""

    name: str
    description: str = ""
    input_schema: dict = Field(default_factory=dict)  # JSON Schema
    requires_approval: bool = False  # human must approve before execution


class RateLimits(BaseModel):
    """Per-connection call rate limits (None = unlimited)."""

    calls_per_minute: int | None = None
    calls_per_day: int | None = None


class Connection(BaseModel):
    """A native/MCP/pipedream-style connection with explicit tools."""

    id: str
    type: Literal["native", "mcp", "http_api", "pipedream", "custom"] = "native"
    display_name: str = ""
    auth: AuthConfig = Field(default_factory=AuthConfig)
    tools: list[ToolDef] = Field(default_factory=list)
    rate_limits: RateLimits = Field(default_factory=RateLimits)


class HttpApiAuth(BaseModel):
    """Auth for a direct, doc-driven HTTP API connection."""

    method: Literal["api_key", "oauth2", "bearer"] = "api_key"
    credential_ref: str | None = None


class HttpApiConnection(BaseModel):
    """Direct API access where the model learns the API from its docs."""

    id: str
    base_url: str
    docs_url: str = ""
    auth: HttpApiAuth = Field(default_factory=HttpApiAuth)
    allowed_methods: list[Literal["GET", "POST", "PUT", "PATCH", "DELETE"]] = Field(
        default_factory=lambda: ["GET"]
    )
    max_response_bytes: int = 1_000_000


class ToolConnectionConfig(BaseModel):
    """Full config schema for the tool_connection primitive (design Primitive 4)."""

    connections: list[Connection] = Field(default_factory=list)
    http_api_connections: list[HttpApiConnection] = Field(default_factory=list)


TEMPLATE = PrimitiveTemplate(
    name="tool_connection",
    version="1.0",
    config_model=ToolConnectionConfig,
    dependencies=("identity", "permission"),
    runtime_contract=(
        "list_tools(): list[ToolDef]",
        "call_tool(tool_name, inputs): ToolResult",
        "ToolResult.output: any",
        "ToolResult.success: bool",
        "ToolResult.error: string | null",
        "ToolResult.latency_ms: int",
        "ToolResult.audit_id: uuid",
    ),
)
