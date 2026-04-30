"""Host (machine) descriptor — `host.*` block of the JSON v1 sidecar.

A `HostInfo` is captured once per run by the orchestrator and embedded
in every sidecar emitted during that run. It pins the run to a specific
machine state for audit purposes (which OS version, which architecture,
who was logged in, were they elevated).
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


class OperatingSystem(str, Enum):
    """Supported operating systems.

    Tier 1 (`adapters/<os>/`): linux_ubuntu, windows, macos.
    Tier 2 (`contrib/adapters/<os>/`): everything else.

    The string value is what native scripts emit and what the adapter
    factory uses to select an implementation.
    """

    LINUX_UBUNTU = "linux_ubuntu"
    LINUX_DEBIAN = "linux_debian"
    LINUX_FEDORA = "linux_fedora"
    LINUX_ARCH = "linux_arch"
    LINUX_OTHER = "linux_other"
    WINDOWS = "windows"
    MACOS = "macos"
    UNKNOWN = "unknown"


class ElevationMethod(str, Enum):
    """How the run process obtained elevated privileges (if at all).

    `none` means no elevation was used; the run executed as the invoking
    user. `sudo`, `pkexec`, `doas`, and `uac` describe the actual mechanism
    when one was used.
    """

    NONE = "none"
    SUDO = "sudo"
    PKEXEC = "pkexec"
    DOAS = "doas"
    UAC = "uac"
    LAUNCHD_ROOT = "launchd_root"
    UNKNOWN = "unknown"


# A non-empty trimmed string. Used for hostname, user, etc.
NonEmptyStr = Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]


class HostInfo(BaseModel):
    """Snapshot of the machine state at run start.

    Frozen — once captured, never mutated.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        str_strip_whitespace=True,
    )

    hostname: NonEmptyStr = Field(
        description="Result of `hostname` (Linux/macOS) or `$env:COMPUTERNAME` (Windows).",
    )
    os: OperatingSystem = Field(
        description="Detected operating system family. Drives adapter selection.",
    )
    os_version: NonEmptyStr = Field(
        description=(
            "Free-form OS version string. Examples: '24.04 LTS' (Ubuntu), "
            "'11 Pro 26200' (Windows), '14.5 (23F79)' (macOS Sonoma)."
        ),
    )
    arch: NonEmptyStr = Field(
        description="Architecture string. 'x86_64' / 'arm64' / 'aarch64' / etc.",
    )
    user: NonEmptyStr = Field(
        description="Username under which the run is executing.",
    )
    is_elevated: bool = Field(
        description=(
            "True if the run process has effective elevated privileges "
            "(root on POSIX, Administrator on Windows)."
        ),
    )
    elevation_method: ElevationMethod = Field(
        default=ElevationMethod.NONE,
        description="Mechanism used to obtain elevation, if any.",
    )
    locale: str | None = Field(
        default=None,
        description=(
            "BCP-47 locale tag of the user shell, if known. Used for i18n "
            "lookups in the orchestrator. Example: 'pl-PL', 'en-US'."
        ),
    )
