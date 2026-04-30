"""IElevation — privilege escalation abstraction (sudo / UAC / pkexec).

Most Ascendo phases need elevated privileges (apt installs, VSS snapshots,
NVIDIA driver install). The mechanism varies per OS:

- Linux: ``sudo`` (preferred), ``pkexec`` (Polkit), ``doas`` (BSD-style).
- Windows: UAC elevation prompt via ``Start-Process -Verb RunAs`` /
  ``ShellExecute(SW_RUNAS)``.
- macOS: ``sudo`` (system shell), occasionally ``osascript`` for GUI prompts.

Per the threat model T4 (Local privesc), this interface enforces:

- **No shell strings** — only ``argv``-style ``list[str]`` is accepted.
- **Allow-list for elevated commands** — adapters declare upfront which
  commands they may run elevated; everything else is rejected.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable, Sequence

from pydantic import BaseModel, ConfigDict, Field

from ..models.host import ElevationMethod, HostInfo


class ElevationResult(BaseModel):
    """Result of an elevated command execution. Frozen."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    exit_code: int = Field(description="Process exit code.")
    stdout: str = Field(default="", description="Captured stdout.")
    stderr: str = Field(default="", description="Captured stderr.")
    method: ElevationMethod = Field(description="Mechanism used.")
    duration_ms: int = Field(description="Wall-clock duration in milliseconds.")


class IElevation(ABC):
    """Privilege escalation interface.

    Implementations are responsible for:

    1. Detecting available elevation mechanisms on the host.
    2. Validating each request against the adapter's allow-list.
    3. Spawning the privileged process with the chosen mechanism.
    4. Capturing stdout/stderr for sidecar persistence.

    **Implementations MUST refuse shell-style command strings.**
    The argv list is accepted as-is and passed to ``execv`` /
    ``CreateProcess`` without re-quoting.
    """

    @property
    @abstractmethod
    def available_methods(self) -> tuple[ElevationMethod, ...]:
        """Methods this implementation can drive on the current host.

        Detected at adapter init time. May be empty (no elevation
        available — orchestrator falls back to user-context only).
        """

    @abstractmethod
    def is_currently_elevated(self, host: HostInfo) -> bool:
        """True if the current process already has elevated privileges.

        On POSIX: ``os.geteuid() == 0``. On Windows: token elevation
        check via ``GetTokenInformation``.
        """

    @abstractmethod
    def register_allowlist(self, allowed_commands: Iterable[str]) -> None:
        """Register the set of commands this elevator is permitted to run.

        Called once per run by the adapter, BEFORE any ``run()`` call.
        Each entry is a basename (e.g. ``"apt-get"``, ``"winget.exe"``,
        ``"dcu-cli.exe"``). Subsequent ``run()`` calls whose first argv
        element does not match the allow-list are rejected immediately
        with ``ElevationDenied``.

        The allow-list is process-scoped — separate runs require
        re-registration. This prevents accidental privilege creep across
        adapter loads.
        """

    @abstractmethod
    def run(
        self,
        host: HostInfo,
        argv: Sequence[str],
        *,
        timeout_sec: int | None = None,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        method: ElevationMethod | None = None,
    ) -> ElevationResult:
        """Execute a command with elevated privileges.

        Args:
            host: Host context.
            argv: Command + arguments, NEVER a shell string. The first
                element must be in the registered allow-list.
            timeout_sec: Kill the child after this many seconds. ``None``
                means inherit the orchestrator default (typically 1800).
            env: Environment overrides. The base environment is the
                current process's; this dict is merged on top.
            cwd: Working directory for the child.
            method: Force a specific elevation mechanism. ``None`` means
                pick the best available (per ``available_methods`` order).

        Returns:
            :class:`ElevationResult` with exit code + captured I/O.

        Raises:
            ElevationDenied: command not in allow-list, or no elevation
                mechanism available.
            ElevationTimeout: child exceeded ``timeout_sec``.
        """


class ElevationDenied(PermissionError):
    """Command rejected — not in the registered allow-list, or no mechanism."""


class ElevationTimeout(TimeoutError):
    """Elevated process exceeded its timeout and was killed."""
