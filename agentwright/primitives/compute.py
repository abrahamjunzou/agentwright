"""Primitive 5: Compute — isolated execution environment.

Shell, browser, and (future) VM. Depends on identity and permission.

Infrastructure constraint: the runtime infrastructure layer has NO VM service
yet ("Primitive gap — compute.vm not yet implemented"). The validator rejects
any definition that sets ``vm.enabled: true``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .base import PrimitiveTemplate


class ShellConfig(BaseModel):
    """Shell runner config. Empty allowed_commands = unrestricted."""

    enabled: bool = False
    allowed_commands: list[str] = Field(default_factory=list)
    timeout_seconds: int = 30
    working_dir: str = "workspace"


class Viewport(BaseModel):
    """Browser viewport size."""

    width: int = 1280
    height: int = 800


class BrowserConfig(BaseModel):
    """Playwright browser config."""

    enabled: bool = False
    persist_session: bool = False  # keep cookies/session across runs
    viewport: Viewport = Field(default_factory=Viewport)
    timeout_seconds: int = 30
    stealth_mode: bool = False


class VmConfig(BaseModel):
    """VM config. Not backed by the runtime layer yet — must stay disabled."""

    enabled: bool = False
    image: str = ""
    persist_between_runs: bool = False
    max_runtime_seconds: int = 300
    memory_mb: int = 1024
    cpu_cores: float = 1.0


class IsolationConfig(BaseModel):
    """Network isolation for the compute environment."""

    network: Literal["full", "allow_list", "none"] = "allow_list"
    allowed_domains: list[str] = Field(default_factory=list)


class ComputeConfig(BaseModel):
    """Full config schema for the compute primitive (design Primitive 5)."""

    shell: ShellConfig = Field(default_factory=ShellConfig)
    browser: BrowserConfig = Field(default_factory=BrowserConfig)
    vm: VmConfig = Field(default_factory=VmConfig)
    isolation: IsolationConfig = Field(default_factory=IsolationConfig)


TEMPLATE = PrimitiveTemplate(
    name="compute",
    version="1.0",
    config_model=ComputeConfig,
    dependencies=("identity", "permission"),
    runtime_contract=(
        "exec_shell(command): ShellResult",
        "browse(url): BrowseResult",
        "screenshot(): image",
        "click(selector)",
        "type(selector, text)",
        "download(url): FilePath",
        "vm_exec(command): VMResult  # not yet backed by infrastructure",
    ),
)
