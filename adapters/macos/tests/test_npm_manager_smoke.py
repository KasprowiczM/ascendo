"""NpmManager smoke tests — mock-based, runs on any OS."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ascendo.models.host import HostInfo, OperatingSystem
from ascendo.models.package import SourceType
from ascendo.models.run import Phase, RunInfo, Trigger

from ascendo_macos.managers.npm import NpmManager


@pytest.fixture
def mac_host() -> HostInfo:
    return HostInfo(
        hostname="mac.local",
        os=OperatingSystem.MACOS,
        os_version="15.0",
        arch="arm64",
        user="mk",
        is_elevated=False,
    )


@pytest.fixture
def linux_host() -> HostInfo:
    return HostInfo(
        hostname="linux",
        os=OperatingSystem.LINUX_OTHER,
        os_version="6.5",
        arch="x86_64",
        user="mk",
        is_elevated=False,
    )


def _make() -> NpmManager:
    return NpmManager(
        scripts_dir=Path("/tmp/scripts"),
        lib_dir=Path("/tmp/lib"),
        bash_path="/bin/bash",
    )


# ── Identity ─────────────────────────────────────────────────────────────


def test_category_is_npm():
    assert _make().category is SourceType.NPM


def test_display_name_mentions_node_npm_bun():
    name = _make().display_name.lower()
    assert "node" in name
    assert "npm" in name
    assert "bun" in name


# ── is_available ─────────────────────────────────────────────────────────


def test_is_available_returns_false_on_non_macos(linux_host):
    assert _make().is_available(linux_host) is False


def test_is_available_true_on_macos_when_bash_present(mac_host):
    # /bin/bash exists on every macOS host.
    assert _make().is_available(mac_host) is True


# ── Phase dispatch ───────────────────────────────────────────────────────


def test_run_phase_unknown_phase_raises(mac_host):
    from ascendo.interfaces.package_manager import ManagerError

    m = _make()
    bad = MagicMock(spec=Phase)
    bad.value = "wat"
    with pytest.raises(ManagerError, match="does not support phase"):
        m.run_phase(bad, _run(), mac_host)


@pytest.mark.parametrize("phase,expected_script", [
    (Phase.CHECK,   "npm/check.sh"),
    (Phase.PLAN,    "npm/plan.sh"),
    (Phase.APPLY,   "npm/apply.sh"),
    (Phase.VERIFY,  "npm/verify.sh"),
    (Phase.CLEANUP, "npm/cleanup.sh"),
])
def test_run_phase_dispatches_correct_script_per_phase(phase, expected_script, mac_host):
    m = _make()
    assert m.SCRIPT_BY_PHASE[phase] == expected_script


def _run(dry: bool = False) -> RunInfo:
    from datetime import datetime, timezone
    from uuid import uuid4
    return RunInfo(
        id=uuid4(),
        trigger=Trigger.CLI,
        profile="full",
        dry_run=dry,
        started_at=datetime.now(timezone.utc),
    )


# ── _build_argv shape ────────────────────────────────────────────────────


def test_build_argv_includes_dry_run_flag_when_requested(mac_host):
    m = _make()
    run = _run(dry=True)
    argv = m._build_argv(
        bash="/bin/bash",
        script_path=Path("/tmp/scripts/npm/check.sh"),
        run=run,
        output_dir=Path("/tmp/out"),
        item_filter=None,
    )
    assert "--dry-run" in argv


def test_build_argv_omits_dry_run_when_false(mac_host):
    m = _make()
    run = _run(dry=False)
    argv = m._build_argv(
        bash="/bin/bash",
        script_path=Path("/tmp/scripts/npm/check.sh"),
        run=run,
        output_dir=Path("/tmp/out"),
        item_filter=None,
    )
    assert "--dry-run" not in argv


def test_build_argv_passes_filter_csv(mac_host):
    m = _make()
    run = _run()
    argv = m._build_argv(
        bash="/bin/bash",
        script_path=Path("/tmp/scripts/npm/apply.sh"),
        run=run,
        output_dir=Path("/tmp/out"),
        item_filter=["claude-code", "codex-cli"],
    )
    i = argv.index("--filter")
    assert argv[i + 1] == "claude-code,codex-cli"
