"""MacOSAdapter smoke tests -- mock-based, runs on any OS."""
from __future__ import annotations

import platform
from pathlib import Path

import pytest

from ascendo.interfaces import AdapterCapability
from ascendo.models.host import HostInfo, OperatingSystem
from ascendo.models.package import SourceType

from ascendo_macos import MacOSAdapter


# ── Identity ─────────────────────────────────────────────────────────────


def test_adapter_identity() -> None:
    a = MacOSAdapter()
    assert a.name == "macos"
    assert a.display_name == "macOS"
    assert a.tier == 1


# ── Capabilities ─────────────────────────────────────────────────────────


def test_capabilities_is_package_management_only() -> None:
    """M5.1 declares only PACKAGE_MANAGEMENT — not the full TIER_1_FULL set."""
    a = MacOSAdapter()
    assert a.capabilities == AdapterCapability.PACKAGE_MANAGEMENT
    # Explicitly confirm the other capabilities are NOT set
    assert not (a.capabilities & AdapterCapability.INVENTORY)
    assert not (a.capabilities & AdapterCapability.SNAPSHOTS)
    assert not (a.capabilities & AdapterCapability.SCHEDULING)
    assert not (a.capabilities & AdapterCapability.ELEVATION)


# ── Accessor None-ness (M5.2-M5.5 reserved) ──────────────────────────────


def test_unsupported_accessors_return_none_in_m51() -> None:
    a = MacOSAdapter()
    assert a.inventory() is None
    assert a.snapshot() is None
    assert a.scheduler() is None
    assert a.source() is None
    assert a.elevation() is None


# ── package_managers ─────────────────────────────────────────────────────


def test_package_managers_returns_brew() -> None:
    a = MacOSAdapter()
    host = HostInfo(
        hostname="macbook.local",
        os=OperatingSystem.MACOS,
        os_version="14.5",
        arch="arm64",
        user="mk",
        is_elevated=False,
    )
    mgrs = a.package_managers(host)
    assert len(mgrs) == 1
    assert mgrs[0].category is SourceType.BREW


# ── detect_host ──────────────────────────────────────────────────────────


def test_detect_host_returns_macos_on_darwin() -> None:
    """detect_host on this Mac returns OS=MACOS with valid arch + hostname."""
    if platform.system() != "Darwin":
        pytest.skip("requires macOS (Darwin)")
    h = MacOSAdapter().detect_host()
    assert h.os is OperatingSystem.MACOS
    assert h.arch in {"arm64", "x86_64"}
    assert h.hostname
    assert len(h.hostname) > 0


def test_detect_host_is_cached() -> None:
    """Second call to detect_host returns the same object (caching)."""
    if platform.system() != "Darwin":
        pytest.skip("requires macOS (Darwin)")
    a = MacOSAdapter()
    h1 = a.detect_host()
    h2 = a.detect_host()
    assert h1 is h2


# ── health_check ─────────────────────────────────────────────────────────


def test_health_check_reports_required_keys() -> None:
    """health_check() must return all 5 expected component keys."""
    a = MacOSAdapter()
    h = a.health_check()
    assert "brew" in h
    assert "jq" in h
    assert "bash" in h
    assert "ascendo_lib" in h
    assert "ascendo_scripts" in h


def test_health_check_values_are_strings() -> None:
    """Each health_check value starts with ok/degraded/unavailable/error."""
    a = MacOSAdapter()
    for key, value in a.health_check().items():
        assert isinstance(value, str), f"health_check[{key!r}] is not a string"
        assert any(
            value.startswith(prefix)
            for prefix in ("ok", "degraded", "unavailable", "error")
        ), f"health_check[{key!r}] = {value!r} does not start with a known status prefix"


# ── adapter_factory integration ───────────────────────────────────────────


def test_adapter_factory_resolves_macos() -> None:
    """AdapterRegistry.discover() must find MacOSAdapter for OperatingSystem.MACOS."""
    from ascendo.adapter_factory import AdapterRegistry

    reg = AdapterRegistry()
    reg.discover()
    cls = reg.get(OperatingSystem.MACOS)
    assert cls is not None, (
        "AdapterRegistry.discover() did not register a macos adapter. "
        "Ensure ascendo-macos is installed (pip install -e adapters/macos --no-deps) "
        "OR the direct-import fallback path in adapter_factory works."
    )
    assert cls.__name__ == "MacOSAdapter"


def test_adapter_via_select_adapter() -> None:
    """select_adapter(MACOS) returns a working MacOSAdapter instance."""
    from ascendo.adapter_factory import select_adapter

    a = select_adapter(OperatingSystem.MACOS)
    assert isinstance(a, MacOSAdapter)
    assert a.tier == 1
    assert a.name == "macos"
