"""OS detection + adapter selection.

This module is the bridge between the OS-agnostic core and the per-OS
adapters that live in ``adapters/<os>/``. Adapters are discovered through
the ``ascendo.adapters`` entry-point group declared in each adapter's
``pyproject.toml``; a direct-import fallback exists for editable installs
where entry-point metadata may not be registered yet (a common pain point
during local development).

Public surface:

- :func:`detect_os` — best-effort OS family detection.
- :class:`AdapterRegistry` — discovers, caches, and exposes adapter classes.
- :func:`select_adapter` — convenience: detect (or accept) an OS and return
  an instantiated :class:`IAdapter`.

Errors raised:

- :class:`NoAdapterAvailableError` — no adapter is registered for the
  requested OS and no fallback succeeded.
- :class:`AdapterDiscoveryError` — entry-point metadata is malformed or
  resolving an adapter target raised an unexpected exception.

The factory is **stateless from the orchestrator's point of view**: a
single :class:`AdapterRegistry` is typically created at process start
and reused. Tests instantiate their own registry and use
:meth:`AdapterRegistry.register` to inject fakes.
"""

from __future__ import annotations

import importlib
import logging
import platform
from importlib import metadata
from pathlib import Path
from typing import TYPE_CHECKING, Mapping

from ..interfaces.adapter import IAdapter
from ..models.host import OperatingSystem

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = [
    "AdapterDiscoveryError",
    "AdapterRegistry",
    "NoAdapterAvailableError",
    "detect_os",
    "select_adapter",
]

_LOG = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────

#: Entry-point group name. Must match every adapter's ``pyproject.toml``.
_ENTRY_POINT_GROUP = "ascendo.adapters"

#: Mapping from :class:`OperatingSystem` to the entry-point name an adapter
#: registers under. ``LINUX_DEBIAN`` re-uses the Ubuntu adapter because
#: Ubuntu/Debian share apt + most snap/flatpak tooling (Tier 1 covers both
#: per ADR-0006).
_OS_TO_ENTRY_POINT: Mapping[OperatingSystem, str] = {
    OperatingSystem.LINUX_UBUNTU: "ubuntu",
    OperatingSystem.LINUX_DEBIAN: "ubuntu",
    OperatingSystem.WINDOWS: "windows",
    OperatingSystem.MACOS: "macos",
}

#: Direct-import fallback table. Used when ``importlib.metadata.entry_points``
#: returns nothing (typical of editable installs that skipped entry-point
#: registration). Maps entry-point name → ``(module, class)``.
_DIRECT_IMPORT_FALLBACK: Mapping[str, tuple[str, str]] = {
    "ubuntu": ("ascendo_ubuntu", "UbuntuAdapter"),
    "windows": ("ascendo_windows", "WindowsAdapter"),
    "macos": ("ascendo_macos", "MacOSAdapter"),
}

#: ``ID_LIKE`` / ``ID`` token → :class:`OperatingSystem`. Looked up against
#: ``/etc/os-release`` on Linux. Order matches likelihood; longest-match wins
#: by virtue of explicit per-token matching.
_LINUX_ID_TO_OS: Mapping[str, OperatingSystem] = {
    "ubuntu": OperatingSystem.LINUX_UBUNTU,
    "debian": OperatingSystem.LINUX_DEBIAN,
    "fedora": OperatingSystem.LINUX_FEDORA,
    "rhel": OperatingSystem.LINUX_FEDORA,
    "centos": OperatingSystem.LINUX_FEDORA,
    "arch": OperatingSystem.LINUX_ARCH,
    "manjaro": OperatingSystem.LINUX_ARCH,
}


# ── Exceptions ───────────────────────────────────────────────────────────


class NoAdapterAvailableError(RuntimeError):
    """No adapter could be selected for the requested operating system.

    Raised by :func:`select_adapter` when neither the requested OS nor any
    documented fallback chain yielded a registered adapter.
    """


class AdapterDiscoveryError(RuntimeError):
    """Entry-point discovery or import resolution failed unexpectedly.

    Distinct from :class:`NoAdapterAvailableError`: this signals broken
    metadata or a corrupt install, not an unsupported OS.
    """


# ── OS detection ─────────────────────────────────────────────────────────


def _read_os_release(path: Path = Path("/etc/os-release")) -> dict[str, str]:
    """Parse ``/etc/os-release`` into a dict.

    Returns an empty dict if the file is missing or unreadable. The
    canonical format is ``KEY=VALUE`` with optional double quotes around
    the value; we strip both single and double quotes.
    """
    if not path.is_file():
        return {}
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:  # pragma: no cover — defensive
        _LOG.debug("Failed to read %s: %s", path, exc)
        return {}

    out: dict[str, str] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip().strip("\"'")
    return out


def _detect_linux_flavour(release: Mapping[str, str]) -> OperatingSystem:
    """Map ``/etc/os-release`` data to a :class:`OperatingSystem` value."""
    # ``ID`` is the canonical token; ``ID_LIKE`` is a space-separated list of
    # ancestor distros (e.g. Mint reports ``ID=linuxmint ID_LIKE="ubuntu debian"``).
    candidates: list[str] = []
    if (primary := release.get("ID", "").strip().lower()):
        candidates.append(primary)
    id_like = release.get("ID_LIKE", "").strip().lower()
    if id_like:
        candidates.extend(token for token in id_like.split() if token)

    for token in candidates:
        if token in _LINUX_ID_TO_OS:
            return _LINUX_ID_TO_OS[token]

    return OperatingSystem.LINUX_OTHER


def detect_os(host_overrides: dict[str, str] | None = None) -> OperatingSystem:
    """Detect the operating system family of the current host.

    Args:
        host_overrides: Test-only escape hatch. Recognised keys:

            - ``"system"`` — overrides :func:`platform.system`.
            - ``"os_release_path"`` — alternate path to read instead of
              ``/etc/os-release``.
            - ``"id"`` / ``"id_like"`` — pre-parsed os-release values
              (skip file I/O entirely).

    Returns:
        The detected :class:`OperatingSystem`. Returns
        :attr:`OperatingSystem.UNKNOWN` for systems we can't classify.
    """
    overrides = host_overrides or {}
    system = (overrides.get("system") or platform.system() or "").lower()

    if system == "windows":
        return OperatingSystem.WINDOWS
    if system == "darwin":
        return OperatingSystem.MACOS
    if system != "linux":
        _LOG.debug("Unrecognised platform.system() value: %r", system)
        return OperatingSystem.UNKNOWN

    # Linux: prefer pre-parsed overrides, then read /etc/os-release.
    if "id" in overrides or "id_like" in overrides:
        release = {
            "ID": overrides.get("id", ""),
            "ID_LIKE": overrides.get("id_like", ""),
        }
    else:
        release_path = Path(overrides.get("os_release_path", "/etc/os-release"))
        release = _read_os_release(release_path)

    if not release:
        _LOG.debug("No os-release data; falling back to LINUX_OTHER")
        return OperatingSystem.LINUX_OTHER

    return _detect_linux_flavour(release)


# ── Registry ─────────────────────────────────────────────────────────────


class AdapterRegistry:
    """Caches discovered adapter classes by entry-point name.

    The registry is intentionally small — it does not instantiate adapters
    until :meth:`get` is called by :func:`select_adapter`. Repeated calls
    to :meth:`discover` are idempotent; once an adapter is known, it stays
    known.

    Tests typically construct a fresh registry and call :meth:`register`
    to inject fakes, bypassing entry-point discovery entirely.
    """

    def __init__(self) -> None:
        self._adapters: dict[str, type[IAdapter]] = {}
        self._discovered: bool = False

    # ── Discovery ────────────────────────────────────────────────────

    def discover(self) -> None:
        """Populate the registry from entry points + direct-import fallback.

        Safe to call multiple times. The first successful resolution for a
        given entry-point name wins; later attempts are skipped.

        Raises:
            AdapterDiscoveryError: if entry-point metadata exists but the
                target module/attribute cannot be resolved.
        """
        self._discover_via_entry_points()
        self._discover_via_direct_import()
        self._discovered = True

    def _discover_via_entry_points(self) -> None:
        """Walk ``importlib.metadata.entry_points`` for our group."""
        try:
            # Python 3.10+ API: select(group=...) returns an EntryPoints view.
            eps = metadata.entry_points(group=_ENTRY_POINT_GROUP)
        except TypeError:  # pragma: no cover — Python <3.10 fallback
            eps = metadata.entry_points().get(_ENTRY_POINT_GROUP, [])  # type: ignore[attr-defined]

        ep_iter: Iterable[metadata.EntryPoint] = list(eps)
        if not ep_iter:
            _LOG.debug(
                "No entry points found for group %r; will try direct imports.",
                _ENTRY_POINT_GROUP,
            )
            return

        for ep in ep_iter:
            if ep.name in self._adapters:
                continue
            try:
                target = ep.load()
            except Exception as exc:  # noqa: BLE001 — wrap and re-raise
                msg = (
                    f"Entry point {ep.name!r} ({ep.value!r}) failed to load: {exc}"
                )
                raise AdapterDiscoveryError(msg) from exc

            if not (isinstance(target, type) and issubclass(target, IAdapter)):
                msg = (
                    f"Entry point {ep.name!r} resolved to {target!r}, which is "
                    f"not a subclass of IAdapter."
                )
                raise AdapterDiscoveryError(msg)

            self._adapters[ep.name] = target
            _LOG.debug("Registered adapter %r via entry-point: %s", ep.name, target)

    def _discover_via_direct_import(self) -> None:
        """Try direct ``importlib.import_module`` for known adapter modules.

        This covers the editable-install case where ``pip install -e`` did
        not register the entry-point in the working environment's metadata
        (a real and recurring issue with hatchling + editable mode).
        """
        for name, (module_name, attr_name) in _DIRECT_IMPORT_FALLBACK.items():
            if name in self._adapters:
                continue
            try:
                module = importlib.import_module(module_name)
            except ImportError as exc:
                _LOG.debug(
                    "Direct import %s failed (likely not installed): %s",
                    module_name,
                    exc,
                )
                continue

            target = getattr(module, attr_name, None)
            if target is None:
                _LOG.warning(
                    "Module %s imported but has no attribute %s; skipping.",
                    module_name,
                    attr_name,
                )
                continue
            if not (isinstance(target, type) and issubclass(target, IAdapter)):
                _LOG.warning(
                    "Direct-import target %s.%s is not an IAdapter subclass; skipping.",
                    module_name,
                    attr_name,
                )
                continue

            self._adapters[name] = target
            _LOG.debug("Registered adapter %r via direct import: %s", name, target)

    # ── Mutation (test-friendly) ─────────────────────────────────────

    def register(self, name: str, adapter_cls: type[IAdapter]) -> None:
        """Manually register an adapter class.

        Useful in tests to inject fakes without touching the entry-point
        machinery. Overwrites any previous registration with the same name.
        """
        if not (isinstance(adapter_cls, type) and issubclass(adapter_cls, IAdapter)):
            msg = f"{adapter_cls!r} is not a subclass of IAdapter."
            raise TypeError(msg)
        self._adapters[name] = adapter_cls
        self._discovered = True
        _LOG.debug("Adapter %r registered manually: %s", name, adapter_cls)

    # ── Query ────────────────────────────────────────────────────────

    def get(self, os: OperatingSystem) -> type[IAdapter] | None:
        """Return the adapter class registered for ``os``, or ``None``.

        Triggers :meth:`discover` on first use.
        """
        if not self._discovered:
            self.discover()
        ep_name = _OS_TO_ENTRY_POINT.get(os)
        if ep_name is None:
            return None
        return self._adapters.get(ep_name)

    def available_oses(self) -> list[OperatingSystem]:
        """Return the list of :class:`OperatingSystem` values that resolve.

        Order is deterministic (insertion order of :data:`_OS_TO_ENTRY_POINT`).
        """
        if not self._discovered:
            self.discover()
        return [
            os
            for os, ep_name in _OS_TO_ENTRY_POINT.items()
            if ep_name in self._adapters
        ]


# ── Selection ────────────────────────────────────────────────────────────


def select_adapter(
    os: OperatingSystem | None = None,
    *,
    registry: AdapterRegistry | None = None,
) -> IAdapter:
    """Pick and instantiate the adapter for the given (or detected) OS.

    Args:
        os: The OS to select an adapter for. ``None`` calls
            :func:`detect_os` first.
        registry: An :class:`AdapterRegistry` to use. If ``None``, a fresh
            one is created and discovery runs immediately.

    Returns:
        A newly instantiated :class:`IAdapter` for the chosen OS.

    Raises:
        NoAdapterAvailableError: No adapter is registered for ``os`` and
            no Tier 1 fallback was applicable.
    """
    reg = registry if registry is not None else AdapterRegistry()
    target_os = os if os is not None else detect_os()

    cls = reg.get(target_os)
    if cls is None and target_os.value.startswith("linux_"):
        # Tier 1 fallback: every linux_* family attempts the Ubuntu adapter,
        # which covers Debian-family distros (apt + snap + flatpak). This
        # mirrors ADR-0006's "Ubuntu adapter is the Linux Tier 1 default".
        _LOG.info(
            "No adapter for %s; falling back to LINUX_UBUNTU.", target_os.value,
        )
        cls = reg.get(OperatingSystem.LINUX_UBUNTU)

    if cls is None:
        available = ", ".join(o.value for o in reg.available_oses()) or "<none>"
        msg = (
            f"No adapter available for {target_os.value!r}. "
            f"Available adapters: {available}. "
            f"Install the matching adapter package (e.g. `pip install "
            f"ascendo-ubuntu` for Linux)."
        )
        raise NoAdapterAvailableError(msg)

    _LOG.debug("Instantiating adapter %s for OS %s", cls.__name__, target_os.value)
    return cls()
