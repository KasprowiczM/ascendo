"""Smoke tests for MacElevation.

Tests cover: argv-only contract, allow-list normalisation, askpass
helper shape (mode + content + escape), state lifecycle. No real
sudo invocations; subprocess.run is mocked.
"""
from __future__ import annotations

import stat
from unittest.mock import MagicMock

import pytest

from ascendo.interfaces.elevation import ElevationDenied
from ascendo.models.host import ElevationMethod, HostInfo, OperatingSystem
from ascendo_macos.managers.elevation import MacElevation


@pytest.fixture
def host() -> HostInfo:
    return HostInfo(
        hostname="testmac.local",
        os=OperatingSystem.MACOS,
        os_version="14.5",
        arch="arm64",
        user="mk",
        is_elevated=False,
        elevation_method=ElevationMethod.NONE,
    )


def test_register_allowlist_lowercases_basenames():
    e = MacElevation()
    e.register_allowlist(["/usr/bin/MAS", "Foo.SH", "/Users/x/Bar"])
    assert e._allowlist == frozenset({"mas", "foo.sh", "bar"})


def test_run_with_empty_allowlist_denies_everything(host):
    """Deny-by-default: when register_allowlist was never called, every
    command is rejected. Mirrors Windows elevation contract (T4 mitigation
    per ADR-0005)."""
    e = MacElevation()
    # Note: register_allowlist NOT called.
    with pytest.raises(ElevationDenied):
        e.run(host, ["mas", "upgrade"])


def test_run_with_empty_argv_raises_elevation_denied(host):
    e = MacElevation()
    e.register_allowlist(["mas"])
    with pytest.raises(ElevationDenied):
        e.run(host, [])


def test_run_with_shell_string_argv_raises_typeerror(host):
    e = MacElevation()
    e.register_allowlist(["mas"])
    with pytest.raises(TypeError):
        e.run(host, "mas upgrade Foo")  # type: ignore[arg-type]


def test_run_rejects_command_not_in_allowlist(host):
    e = MacElevation()
    e.register_allowlist(["mas"])
    with pytest.raises(ElevationDenied):
        e.run(host, ["rm", "-rf", "/"])


def test_register_password_verifies_via_sudo_v(monkeypatch, tmp_path):
    """register_password calls `sudo -S -p '' -v` and stores on success."""
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    e = MacElevation()
    captured = {}

    def fake_run(argv, *, input=None, capture_output=None, text=None, timeout=None):
        captured["argv"] = argv
        captured["input"] = input
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)
    ok, detail = e.register_password("hunter2")
    assert ok is True
    assert captured["argv"] == ["sudo", "-S", "-p", "", "-v"]
    assert captured["input"] == "hunter2\n"
    assert e.has_password_registered() is True


def test_register_password_returns_false_on_bad_password(monkeypatch):
    e = MacElevation()

    def fake_run(*a, **kw):
        return MagicMock(returncode=1, stdout="", stderr="Sorry, try again.")

    monkeypatch.setattr("subprocess.run", fake_run)
    ok, detail = e.register_password("wrong")
    assert ok is False
    assert "try again" in detail.lower() or "1" in detail
    assert e.has_password_registered() is False


def test_register_password_creates_0700_helper(monkeypatch, tmp_path):
    e = MacElevation()
    monkeypatch.setattr("subprocess.run",
                        lambda *a, **kw: MagicMock(returncode=0, stdout="", stderr=""))
    monkeypatch.setenv("TMPDIR", str(tmp_path))

    e.register_password("hunter2")
    p = e.askpass_path()
    assert p is not None and p.is_file()

    # Mode is 0700
    mode = stat.S_IMODE(p.stat().st_mode)
    assert mode == 0o700, f"expected 0700, got 0o{mode:o}"

    # Content prints the password
    body = p.read_text()
    assert body.startswith("#!/usr/bin/env bash\n")
    assert "printf '%s\\n' 'hunter2'\n" in body


def test_helper_escapes_single_quotes(monkeypatch, tmp_path):
    e = MacElevation()
    monkeypatch.setattr("subprocess.run",
                        lambda *a, **kw: MagicMock(returncode=0, stdout="", stderr=""))
    monkeypatch.setenv("TMPDIR", str(tmp_path))

    e.register_password("O'Brien42")
    body = e.askpass_path().read_text()
    # Single-quote escape rule: ' -> '\''
    # So O'Brien42 -> 'O'\''Brien42'
    assert "'O'\\''Brien42'" in body


def test_invalidate_wipes_state_and_is_idempotent(monkeypatch, tmp_path):
    e = MacElevation()
    monkeypatch.setattr("subprocess.run",
                        lambda *a, **kw: MagicMock(returncode=0, stdout="", stderr=""))
    monkeypatch.setenv("TMPDIR", str(tmp_path))

    e.register_password("hunter2")
    helper = e.askpass_path()
    assert helper.is_file()

    e.invalidate()
    assert e.has_password_registered() is False
    assert e.askpass_path() is None
    assert not helper.exists()

    # Second invalidate is a no-op
    e.invalidate()
    assert e.has_password_registered() is False


def test_register_password_does_not_partially_set_state_on_helper_failure(monkeypatch):
    """If _create_askpass_helper raises, neither _password nor _askpass_path
    should be set — has_password_registered() must return False."""
    e = MacElevation()
    # Mock subprocess.run for verify -> success
    monkeypatch.setattr("subprocess.run",
                        lambda *a, **kw: MagicMock(returncode=0, stdout="", stderr=""))
    # Force _create_askpass_helper to raise
    def boom(*a, **kw):
        raise OSError("disk full")
    monkeypatch.setattr(MacElevation, "_create_askpass_helper", boom)

    with pytest.raises(OSError):
        e.register_password("hunter2")
    assert e.has_password_registered() is False
    assert e.askpass_path() is None


def test_available_methods_empty_when_sudo_missing(monkeypatch):
    monkeypatch.setattr("shutil.which",
                        lambda name: None if name == "sudo" else "/x")
    e = MacElevation()
    assert e.available_methods == ()
