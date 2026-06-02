"""Service 14: Shell Runner (Layer 0).

Runs shell commands in an isolated working directory with a timeout, an optional
command allowlist, and a best-effort memory cap (via POSIX ``resource`` limits
applied in the child before exec). Returns a structured result.

No VM service exists (design "Primitive gap — compute.vm"). VM execution is
rejected upstream by composition invariant 12; this runner is shell-only.
"""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

try:  # POSIX-only; absent on non-Unix, in which case the memory cap is skipped.
    import resource
except ImportError:  # pragma: no cover - platform dependent
    resource = None


@dataclass
class ShellResult:
    """Outcome of one shell execution."""

    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool
    oom_killed: bool = False


def _preexec(max_memory_mb: int):
    """Build a preexec_fn that caps address space in the child (POSIX only)."""
    if resource is None:
        return None

    def limit():
        nbytes = max_memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (nbytes, nbytes))

    return limit


class ShellRunner:
    """Subprocess-based command runner with timeout, allowlist, and mem cap."""

    def exec(
        self,
        command: str,
        working_dir: str | Path,
        timeout_seconds: int = 30,
        max_memory_mb: int = 512,
        allowed_commands: list[str] | None = None,
    ) -> ShellResult:
        """Execute ``command`` in ``working_dir``.

        ``allowed_commands`` (if non-empty) restricts the first token of the
        command to the allowlist. The working directory is created if missing.
        """
        work = Path(working_dir)
        work.mkdir(parents=True, exist_ok=True)

        if allowed_commands:
            program = shlex.split(command)[0] if command.strip() else ""
            if program not in allowed_commands:
                return ShellResult(
                    stdout="",
                    stderr=f"command not allowed: {program!r}",
                    exit_code=126,
                    timed_out=False,
                )

        try:
            proc = subprocess.run(
                command,
                shell=True,
                cwd=str(work),
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                preexec_fn=_preexec(max_memory_mb),
            )
        except subprocess.TimeoutExpired as exc:
            return ShellResult(
                stdout=exc.stdout or "",
                stderr=exc.stderr or "",
                exit_code=124,
                timed_out=True,
            )
        # A process killed for exceeding the memory cap typically dies on a
        # signal (negative return code) or with MemoryError on its stderr.
        oom = proc.returncode < 0 or "MemoryError" in (proc.stderr or "")
        return ShellResult(
            stdout=proc.stdout,
            stderr=proc.stderr,
            exit_code=proc.returncode,
            timed_out=False,
            oom_killed=oom,
        )
