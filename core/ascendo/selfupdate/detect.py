"""Detect *how* Ascendo was installed so the updater picks the right path.

The same Python core powers the CLI, the web dashboard, and the Tauri
desktop shell (the shell just spawns ``ascendo dashboard``). What differs
is the *delivery*:

- ``git``      — installed via install.sh / install.ps1 into a git checkout
                 at ASCENDO_HOME. Upgradable in-app via git pull + pip
                 (this is the common case on all three OSes).
- ``packaged`` — a standalone bundle with no git checkout (e.g. a future
                 signed .app/.msi). The core can't self-pull; the user
                 installs a new artifact. We surface a download link.

We also report whether we're running inside the desktop shell
(``desktop``) and the shell binary version, which the Tauri ``main.rs``
passes down via env vars (ASCENDO_DESKTOP=1, ASCENDO_SHELL_VERSION=...).
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

__all__ = ["InstallInfo", "detect_install"]


def detect_os() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("linux"):
        return "linux"
    return "unknown"


def default_install_dir(os_name: str) -> Path:
    """Mirror the install scripts' default ASCENDO_HOME per OS."""
    env = os.environ.get("ASCENDO_HOME")
    if env:
        return Path(env).expanduser()
    if os_name == "windows":
        base = os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
        return Path(base) / "Ascendo" / "src"
    return Path.home() / ".local" / "share" / "ascendo"


def _arch() -> str:
    import platform as _p

    m = (_p.machine() or "").lower()
    if m in {"arm64", "aarch64"}:
        return "arm64"
    if m in {"x86_64", "amd64"}:
        return "x64"
    return m or "unknown"


@dataclass
class InstallInfo:
    os: str
    arch: str
    install_dir: str
    is_git: bool
    method: str          # "git" | "packaged"
    desktop: bool        # running inside the Tauri shell?
    shell_version: str | None
    updater: str | None  # absolute path to update.sh / update.ps1, if present

    def to_dict(self) -> dict:
        return asdict(self)


def _repo_root_from_package() -> Path | None:
    """If we're running from an editable checkout, return its repo root.

    ``__file__`` is ``…/core/ascendo/selfupdate/detect.py`` → repo root is
    4 parents up (``…/core/ascendo/selfupdate`` → ascendo → core → root).
    """
    here = Path(__file__).resolve()
    candidate = here.parents[3]
    if (candidate / ".git").exists() or (candidate / "update.sh").is_file():
        return candidate
    return None


def detect_install() -> InstallInfo:
    os_name = detect_os()
    # Prefer the live checkout we're imported from; fall back to ASCENDO_HOME.
    repo = _repo_root_from_package()
    install_dir = repo or default_install_dir(os_name)
    is_git = (install_dir / ".git").exists()

    updater = None
    if os_name == "windows":
        cand = install_dir / "update.ps1"
    else:
        cand = install_dir / "update.sh"
    if cand.is_file():
        updater = str(cand)

    desktop = os.environ.get("ASCENDO_DESKTOP", "") in {"1", "true", "yes"}
    shell_version = os.environ.get("ASCENDO_SHELL_VERSION") or None

    return InstallInfo(
        os=os_name,
        arch=_arch(),
        install_dir=str(install_dir),
        is_git=is_git,
        method="git" if is_git else "packaged",
        desktop=desktop,
        shell_version=shell_version,
        updater=updater,
    )
