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


def test_capabilities_is_package_management_and_elevation_and_inventory() -> None:
    """M5.3 declares PACKAGE_MANAGEMENT | ELEVATION | INVENTORY."""
    a = MacOSAdapter()
    assert a.capabilities & AdapterCapability.PACKAGE_MANAGEMENT
    assert a.capabilities & AdapterCapability.ELEVATION
    assert a.capabilities & AdapterCapability.INVENTORY  # M5.3 wired
    # M5.4-M5.5 capabilities not yet set
    assert not (a.capabilities & AdapterCapability.SNAPSHOTS)
    assert not (a.capabilities & AdapterCapability.SCHEDULING)


# ── Accessor None-ness (M5.2-M5.5 reserved) ──────────────────────────────


def test_unsupported_accessors_return_none_m54_m55() -> None:
    """M5.4-M5.5 accessors still return None; elevation() and inventory() are wired."""
    a = MacOSAdapter()
    assert a.inventory() is not None  # M5.3 wired
    assert a.snapshot() is None       # M5.4 (Time Machine read-only)
    assert a.scheduler() is None      # M5.5 (launchd)
    assert a.source() is None
    assert a.elevation() is not None  # M5.2 wired


# ── package_managers ─────────────────────────────────────────────────────


def test_package_managers_returns_brew_and_mas() -> None:
    """M5.2 adds MasManager after BrewManager."""
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
    assert len(mgrs) == 2
    assert mgrs[0].category is SourceType.BREW
    assert mgrs[1].category is SourceType.MAS


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
    """health_check() must return all 7 expected component keys (M5.3 adds system_profiler)."""
    a = MacOSAdapter()
    h = a.health_check()
    assert "brew" in h
    assert "jq" in h
    assert "mas" in h
    assert "system_profiler" in h  # M5.3
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


# ── M5.2 wiring assertions ────────────────────────────────────────────────


@pytest.fixture
def mac_host() -> HostInfo:
    from ascendo.models.host import ElevationMethod

    return HostInfo(
        hostname="testmac.local",
        os=OperatingSystem.MACOS,
        os_version="14.5",
        arch="arm64",
        user="mk",
        is_elevated=False,
        elevation_method=ElevationMethod.NONE,
    )


def test_capabilities_includes_elevation() -> None:
    """M5.2 adds ELEVATION to the declared capability set."""
    a = MacOSAdapter()
    assert AdapterCapability.PACKAGE_MANAGEMENT in a.capabilities
    assert AdapterCapability.ELEVATION in a.capabilities


def test_package_managers_includes_brew_and_mas(mac_host: HostInfo) -> None:
    """package_managers() returns [BrewManager, MasManager] in that order."""
    a = MacOSAdapter()
    pkgs = a.package_managers(mac_host)
    names = [type(p).__name__ for p in pkgs]
    assert names == ["BrewManager", "MasManager"]


def test_elevation_returns_macelevation() -> None:
    """elevation() returns a MacElevation singleton (same object on repeated calls)."""
    from ascendo_macos.managers.elevation import MacElevation

    a = MacOSAdapter()
    e1 = a.elevation()
    e2 = a.elevation()
    assert isinstance(e1, MacElevation)
    assert e1 is e2


def test_health_check_includes_mas_component() -> None:
    """health_check() must include a 'mas' component key."""
    a = MacOSAdapter()
    h = a.health_check()
    assert "mas" in h


# ── M5.3 wiring assertions ────────────────────────────────────────────────


def test_capabilities_includes_inventory() -> None:
    """M5.3 adds INVENTORY to the declared capability set."""
    a = MacOSAdapter()
    assert AdapterCapability.PACKAGE_MANAGEMENT in a.capabilities
    assert AdapterCapability.ELEVATION in a.capabilities
    assert AdapterCapability.INVENTORY in a.capabilities


def test_inventory_returns_macosinventory_singleton() -> None:
    """inventory() returns a MacOSInventory instance and caches it (same object)."""
    from ascendo_macos.inventory import MacOSInventory

    a = MacOSAdapter()
    e1 = a.inventory()
    e2 = a.inventory()
    assert isinstance(e1, MacOSInventory)
    assert e1 is e2


def test_health_check_includes_system_profiler_component() -> None:
    """health_check() must include a 'system_profiler' component key (M5.3)."""
    a = MacOSAdapter()
    h = a.health_check()
    assert "system_profiler" in h
