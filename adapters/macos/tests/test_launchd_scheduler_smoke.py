"""Mock-based smoke tests for LaunchdScheduler.

No real launchctl / bash invocations — every external call is patched.
Covers identity, OS gate, JSON-IPC argv shape, error paths.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ascendo.interfaces.scheduler import (
    IScheduler,
    ScheduleSpec,
    SchedulerError,
)
from ascendo.models.host import ElevationMethod, HostInfo, OperatingSystem


ADAPTER_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def mac_host() -> HostInfo:
    return HostInfo(
        hostname="testmac.local", os=OperatingSystem.MACOS,
        os_version="14.5", arch="arm64", user="mk",
        is_elevated=False, elevation_method=ElevationMethod.NONE,
    )


@pytest.fixture
def linux_host() -> HostInfo:
    return HostInfo(
        hostname="testlin", os=OperatingSystem.LINUX_OTHER,
        os_version="24.04", arch="x86_64", user="x",
        is_elevated=False, elevation_method=ElevationMethod.NONE,
    )


def _make_scheduler():
    from ascendo_macos.managers.scheduler import LaunchdScheduler
    return LaunchdScheduler(
        scripts_dir=ADAPTER_ROOT / "scripts",
        lib_dir=ADAPTER_ROOT / "lib",
    )


def test_backend_slug_is_launchd():
    s = _make_scheduler()
    assert s.backend == "launchd"


def test_implements_ischeduler():
    s = _make_scheduler()
    assert isinstance(s, IScheduler)


def test_is_available_macos_with_launchctl(mac_host):
    s = _make_scheduler()
    with patch("ascendo_macos.managers.scheduler.shutil.which", return_value="/bin/launchctl"):
        assert s.is_available(mac_host) is True


def test_is_available_macos_without_launchctl(mac_host):
    s = _make_scheduler()
    with patch("ascendo_macos.managers.scheduler.shutil.which", return_value=None):
        assert s.is_available(mac_host) is False


def test_is_available_linux_returns_false(linux_host):
    s = _make_scheduler()
    with patch("ascendo_macos.managers.scheduler.shutil.which", return_value="/bin/launchctl"):
        assert s.is_available(linux_host) is False


# ─────────────────────────────────────────────────────────────────────────
# Install / uninstall / list / get / trigger methods
# ─────────────────────────────────────────────────────────────────────────


def test_install_invokes_with_correct_payload(mac_host):
    """install() should invoke bash script with install action + ScheduleSpec payload."""
    s = _make_scheduler()
    spec = ScheduleSpec(
        name="ascendo-weekly",
        expression="WEEKLY 03:00",
        profile="safe",
        enabled=True,
        description="Weekly safe update",
    )
    with patch.object(s, "_invoke") as mock_invoke:
        s.install(mac_host, spec)
        mock_invoke.assert_called_once()
        action, payload = mock_invoke.call_args[0][0], mock_invoke.call_args[1]["payload"]
        assert action == "install"
        assert payload["name"] == "ascendo-weekly"
        assert payload["expression"] == "WEEKLY 03:00"
        assert payload["profile"] == "safe"
        assert payload["enabled"] is True
        assert payload["description"] == "Weekly safe update"


def test_install_with_no_description(mac_host):
    """install() should pass empty string for None description."""
    s = _make_scheduler()
    spec = ScheduleSpec(
        name="ascendo-quick",
        expression="DAILY 09:00",
        profile="quick",
        enabled=False,
        description=None,
    )
    with patch.object(s, "_invoke") as mock_invoke:
        s.install(mac_host, spec)
        _, payload = mock_invoke.call_args[0][0], mock_invoke.call_args[1]["payload"]
        assert payload["description"] == ""


def test_uninstall_invokes_with_name(mac_host):
    """uninstall() should invoke with uninstall action + name payload."""
    s = _make_scheduler()
    with patch.object(s, "_invoke") as mock_invoke:
        s.uninstall(mac_host, "ascendo-weekly")
        mock_invoke.assert_called_once_with("uninstall", payload={"name": "ascendo-weekly"})


def test_list_returns_empty_when_invoke_returns_non_list(mac_host):
    """list() should return empty list if result is not a list."""
    s = _make_scheduler()
    with patch.object(s, "_invoke", return_value="not a list"):
        result = s.list(mac_host)
        assert result == []


def test_list_returns_empty_list_when_invoke_returns_none(mac_host):
    """list() should return empty list if invoke returns None."""
    s = _make_scheduler()
    with patch.object(s, "_invoke", return_value=None):
        result = s.list(mac_host)
        assert result == []


def test_list_parses_valid_specs(mac_host):
    """list() should parse all valid items and skip malformed ones."""
    s = _make_scheduler()
    mock_result = [
        {
            "name": "ascendo-weekly",
            "expression": "WEEKLY 03:00",
            "profile": "safe",
            "enabled": True,
            "description": "Weekly safe",
        },
        {
            "name": "ascendo-daily",
            "expression": "DAILY 09:00",
            "profile": "quick",
            # Missing enabled and description
        },
        {"invalid": "no name or expression"},  # Malformed, should be skipped
        {
            "name": "ascendo-monthly",
            "expression": "MONTHLY 00:00",
            "profile": "full",
            "enabled": False,
            "description": None,
        },
    ]
    with patch.object(s, "_invoke", return_value=mock_result):
        result = s.list(mac_host)
        assert len(result) == 3
        assert result[0].name == "ascendo-weekly"
        assert result[0].enabled is True
        assert result[0].description == "Weekly safe"
        assert result[1].name == "ascendo-daily"
        assert result[1].enabled is True  # Default when missing
        assert result[1].profile == "quick"
        assert result[2].name == "ascendo-monthly"
        assert result[2].enabled is False
        assert result[2].description is None


def test_get_returns_none_when_not_found(mac_host):
    """get() should return None if name not in list."""
    s = _make_scheduler()
    mock_specs = [
        ScheduleSpec(
            name="ascendo-weekly",
            expression="WEEKLY 03:00",
            profile="safe",
            enabled=True,
        ),
    ]
    with patch.object(s, "list", return_value=mock_specs):
        result = s.get(mac_host, "nonexistent")
        assert result is None


def test_get_returns_matching_spec(mac_host):
    """get() should return ScheduleSpec when name matches."""
    s = _make_scheduler()
    spec1 = ScheduleSpec(
        name="ascendo-weekly",
        expression="WEEKLY 03:00",
        profile="safe",
        enabled=True,
    )
    spec2 = ScheduleSpec(
        name="ascendo-daily",
        expression="DAILY 09:00",
        profile="quick",
        enabled=False,
    )
    with patch.object(s, "list", return_value=[spec1, spec2]):
        result = s.get(mac_host, "ascendo-daily")
        assert result == spec2
        assert result.name == "ascendo-daily"


def test_trigger_invokes_with_name(mac_host):
    """trigger() should invoke with trigger action + name payload."""
    s = _make_scheduler()
    with patch.object(s, "_invoke") as mock_invoke:
        s.trigger(mac_host, "ascendo-weekly")
        mock_invoke.assert_called_once_with("trigger", payload={"name": "ascendo-weekly"})


# ─────────────────────────────────────────────────────────────────────────
# _invoke() helper method
# ─────────────────────────────────────────────────────────────────────────


def test_invoke_without_payload(mac_host):
    """_invoke() should spawn bash script without payload file when payload is None."""
    s = _make_scheduler()
    with patch("ascendo_macos.managers.scheduler.subprocess.run") as mock_run, \
         patch.object(s, "_resolve_bash", return_value="/bin/bash"):
        mock_run.return_value = MagicMock(returncode=0)
        result = s._invoke("list")
        assert result is None
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args[0] == "/bin/bash"
        assert "scheduler.sh" in args[1]
        assert "--action" in args
        assert "list" in args
        assert "--payload" not in args


def test_invoke_with_payload(mac_host):
    """_invoke() should write payload JSON and pass path to script."""
    s = _make_scheduler()
    payload = {"name": "ascendo-weekly", "enabled": True}
    with patch("ascendo_macos.managers.scheduler.subprocess.run") as mock_run, \
         patch.object(s, "_resolve_bash", return_value="/bin/bash"), \
         patch("ascendo_macos.managers.scheduler.tempfile.TemporaryDirectory") as mock_tmpdir:
        mock_tmpdir.return_value.__enter__.return_value = "/tmp/test"
        mock_run.return_value = MagicMock(returncode=0)
        with patch("pathlib.Path.write_text") as mock_write:
            with patch("pathlib.Path.exists", return_value=False):
                result = s._invoke("install", payload=payload)
                assert result is None


def test_invoke_returns_json_when_output_exists(mac_host):
    """_invoke() should parse and return JSON from output file."""
    s = _make_scheduler()
    expected_output = {"status": "ok", "items": ["item1", "item2"]}
    with patch("ascendo_macos.managers.scheduler.subprocess.run") as mock_run, \
         patch.object(s, "_resolve_bash", return_value="/bin/bash"), \
         patch("pathlib.Path.exists", return_value=True), \
         patch("pathlib.Path.read_text", return_value=json.dumps(expected_output)):
        mock_run.return_value = MagicMock(returncode=0)
        result = s._invoke("list")
        assert result == expected_output


def test_invoke_timeout_raises_scheduler_error(mac_host):
    """_invoke() should raise SchedulerError on subprocess timeout."""
    s = _make_scheduler()
    with patch("ascendo_macos.managers.scheduler.subprocess.run") as mock_run, \
         patch.object(s, "_resolve_bash", return_value="/bin/bash"):
        mock_run.side_effect = subprocess.TimeoutExpired("cmd", 30)
        with pytest.raises(SchedulerError, match="scheduler .* timed out"):
            s._invoke("install", payload={"name": "test"})


def test_invoke_oserror_raises_scheduler_error(mac_host):
    """_invoke() should raise SchedulerError on OSError (spawn failure)."""
    s = _make_scheduler()
    with patch("ascendo_macos.managers.scheduler.subprocess.run") as mock_run, \
         patch.object(s, "_resolve_bash", return_value="/bin/bash"):
        mock_run.side_effect = OSError("No such file")
        with pytest.raises(SchedulerError, match="failed to spawn bash"):
            s._invoke("list")


def test_invoke_nonzero_exit_without_output_raises_error(mac_host):
    """_invoke() should raise SchedulerError if exit != 0 and no output file."""
    s = _make_scheduler()
    with patch("ascendo_macos.managers.scheduler.subprocess.run") as mock_run, \
         patch.object(s, "_resolve_bash", return_value="/bin/bash"), \
         patch("pathlib.Path.exists", return_value=False):
        mock_run.return_value = MagicMock(
            returncode=2,
            stderr="script error: unknown action"
        )
        with pytest.raises(SchedulerError, match="scheduler .* failed: exit=2"):
            s._invoke("install", payload={"name": "test"})


def test_invoke_nonzero_exit_with_output_returns_json(mac_host):
    """_invoke() should still return JSON if output exists even with nonzero exit."""
    s = _make_scheduler()
    expected_output = {"status": "error", "message": "action not found"}
    with patch("ascendo_macos.managers.scheduler.subprocess.run") as mock_run, \
         patch.object(s, "_resolve_bash", return_value="/bin/bash"), \
         patch("pathlib.Path.exists", return_value=True), \
         patch("pathlib.Path.read_text", return_value=json.dumps(expected_output)):
        mock_run.return_value = MagicMock(returncode=1, stderr="")
        result = s._invoke("unknown_action")
        assert result == expected_output


def test_invoke_invalid_json_output_raises_error(mac_host):
    """_invoke() should raise SchedulerError if output JSON is malformed."""
    s = _make_scheduler()
    with patch("ascendo_macos.managers.scheduler.subprocess.run") as mock_run, \
         patch.object(s, "_resolve_bash", return_value="/bin/bash"), \
         patch("pathlib.Path.exists", return_value=True), \
         patch("pathlib.Path.read_text", return_value="{ invalid json"):
        mock_run.return_value = MagicMock(returncode=0)
        with pytest.raises(SchedulerError, match="scheduler .* emitted invalid JSON"):
            s._invoke("list")


# ─────────────────────────────────────────────────────────────────────────
# _parse_spec() helper method
# ─────────────────────────────────────────────────────────────────────────


def test_parse_spec_with_all_fields():
    """_parse_spec() should parse dict with all ScheduleSpec fields."""
    s = _make_scheduler()
    item = {
        "name": "ascendo-weekly",
        "expression": "WEEKLY 03:00",
        "profile": "safe",
        "enabled": True,
        "description": "Weekly safe update",
    }
    result = s._parse_spec(item)
    assert result.name == "ascendo-weekly"
    assert result.expression == "WEEKLY 03:00"
    assert result.profile == "safe"
    assert result.enabled is True
    assert result.description == "Weekly safe update"


def test_parse_spec_with_missing_optional_fields():
    """_parse_spec() should use defaults for missing optional fields."""
    s = _make_scheduler()
    item = {
        "name": "ascendo-daily",
        "expression": "DAILY 09:00",
    }
    result = s._parse_spec(item)
    assert result.name == "ascendo-daily"
    assert result.expression == "DAILY 09:00"
    assert result.profile == "full"  # Default
    assert result.enabled is True  # Default
    assert result.description is None  # Default


def test_parse_spec_converts_name_to_string():
    """_parse_spec() should convert name to string."""
    s = _make_scheduler()
    item = {
        "name": 123,  # Non-string
        "expression": "DAILY 09:00",
    }
    result = s._parse_spec(item)
    assert result.name == "123"
    assert isinstance(result.name, str)


def test_parse_spec_with_false_enabled():
    """_parse_spec() should correctly handle enabled=False."""
    s = _make_scheduler()
    item = {
        "name": "ascendo-disabled",
        "expression": "MONTHLY 00:00",
        "enabled": False,
    }
    result = s._parse_spec(item)
    assert result.enabled is False


def test_parse_spec_with_null_description():
    """_parse_spec() should treat falsy description as None."""
    s = _make_scheduler()
    item = {
        "name": "ascendo-test",
        "expression": "HOURLY 00:00",
        "description": None,
    }
    result = s._parse_spec(item)
    assert result.description is None


# ─────────────────────────────────────────────────────────────────────────
# _resolve_bash() helper method
# ─────────────────────────────────────────────────────────────────────────


def test_resolve_bash_returns_cached_value():
    """_resolve_bash() should return cached bash path on second call."""
    s = _make_scheduler()
    s._bash_resolved = "/usr/local/bin/bash"
    result = s._resolve_bash()
    assert result == "/usr/local/bin/bash"


def test_resolve_bash_uses_override_if_set():
    """_resolve_bash() should use override path when provided."""
    from ascendo_macos.managers.scheduler import LaunchdScheduler
    s = LaunchdScheduler(
        scripts_dir=Path("/scripts"),
        lib_dir=Path("/lib"),
        bash_path="/custom/bash",
    )
    result = s._resolve_bash()
    assert result == "/custom/bash"


def test_resolve_bash_discovers_bash_in_candidates(mac_host):
    """_resolve_bash() should find bash in candidate locations."""
    s = _make_scheduler()
    with patch("ascendo_macos.managers.scheduler.shutil.which") as mock_which:
        # bash not found, /bin/bash found
        mock_which.side_effect = [None, "/bin/bash", None]
        result = s._resolve_bash()
        assert result == "/bin/bash"
        assert s._bash_resolved == "/bin/bash"  # Cached


def test_resolve_bash_tries_candidates_in_order(mac_host):
    """_resolve_bash() should try candidates in order: bash, /bin/bash, /usr/local/bin/bash."""
    s = _make_scheduler()
    with patch("ascendo_macos.managers.scheduler.shutil.which") as mock_which:
        mock_which.side_effect = [None, None, "/usr/local/bin/bash"]
        result = s._resolve_bash()
        assert result == "/usr/local/bin/bash"
        # Verify all three candidates were checked
        assert mock_which.call_count == 3


def test_resolve_bash_raises_error_when_not_found(mac_host):
    """_resolve_bash() should raise SchedulerError if no bash found."""
    s = _make_scheduler()
    with patch("ascendo_macos.managers.scheduler.shutil.which", return_value=None):
        with pytest.raises(SchedulerError, match="no bash binary on PATH"):
            s._resolve_bash()


def test_resolve_bash_caches_discovered_path(mac_host):
    """_resolve_bash() should cache the discovered bash path."""
    s = _make_scheduler()
    with patch("ascendo_macos.managers.scheduler.shutil.which") as mock_which:
        mock_which.return_value = "/bin/bash"
        result1 = s._resolve_bash()
        result2 = s._resolve_bash()
        assert result1 == result2 == "/bin/bash"
        # shutil.which should only be called once
        assert mock_which.call_count == 1
