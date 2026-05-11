"""Smoke tests for LinuxElevation (Ubuntu adapter IElevation impl).

Mirrors the macOS test suite's coverage:
  - argv-only contract (shell strings rejected)
  - allow-list normalisation (lowercase basenames; with + without dir)
  - reject not-in-allowlist
  - reject empty argv
  - is_available True on Linux, False off-Linux
  - build_subprocess_env contains SUDO_ASKPASS only when password cached
  - askpass helper script exists, is executable, and echoes the env var
"""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ascendo.interfaces.elevation import ElevationDenied
from ascendo.models.host import ElevationMethod, HostInfo, OperatingSystem
from ascendo_ubuntu.managers.elevation import LinuxElevation


# ── Fixtures ────────────────────────────────────────────────────────────────


_HELPER_PATH = (
    Path(__file__).resolve().parents[1] / "lib" / "askpass_helper.sh"
)


@pytest.fixture
def linux_host() -> HostInfo:
    return HostInfo(
        hostname="mk-uP5520",
        os=OperatingSystem.LINUX_UBUNTU,
        os_version="24.04",
        arch="x86_64",
        user="mk",
        is_elevated=False,
        elevation_method=ElevationMethod.NONE,
    )


@pytest.fixture
def darwin_host() -> HostInfo:
    return HostInfo(
        hostname="mac.local",
        os=OperatingSystem.MACOS,
        os_version="14.5",
        arch="arm64",
        user="mk",
        is_elevated=False,
        elevation_method=ElevationMethod.NONE,
    )


@pytest.fixture
def elevator() -> LinuxElevation:
    return LinuxElevation(askpass_helper=_HELPER_PATH)


# ── Lifecycle ───────────────────────────────────────────────────────────────


def test_register_and_clear_password_roundtrip(monkeypatch, elevator):
    """register_password caches; clear_password wipes."""
    monkeypatch.setattr("subprocess.run",
                        lambda *a, **kw: MagicMock(returncode=0, stdout="", stderr=""))
    ok, msg = elevator.register_password("hunter2")
    assert ok is True
    assert "stored" in msg.lower()
    assert elevator.has_password_registered() is True
    assert elevator.is_authenticated() is True

    elevator.clear_password()
    assert elevator.has_password_registered() is False
    assert elevator.is_authenticated() is False


def test_register_password_returns_false_on_bad_password(monkeypatch, elevator):
    """Bad password: returns (False, reason); does NOT cache."""
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **kw: MagicMock(
            returncode=1, stdout="", stderr="Sorry, try again."
        ),
    )
    ok, detail = elevator.register_password("wrong")
    assert ok is False
    assert "try again" in detail.lower() or "1" in detail
    assert elevator.has_password_registered() is False


def test_register_password_empty_string_rejected(elevator):
    """Empty string is rejected without ever invoking sudo."""
    ok, msg = elevator.register_password("")
    assert ok is False
    assert msg == "empty password"


def test_register_password_calls_sudo_with_dash_k(monkeypatch, elevator):
    """The verify path uses sudo -S -k -p '' -v so it bypasses cached timestamps."""
    captured: dict = {}

    def fake_run(argv, *, input=None, **kw):
        captured["argv"] = argv
        captured["input"] = input
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)
    elevator.register_password("hunter2")
    assert captured["argv"] == ["sudo", "-S", "-k", "-p", "", "-v"]
    assert captured["input"] == "hunter2\n"


def test_invalidate_wipes_state_and_calls_sudo_k(monkeypatch, elevator):
    """invalidate clears the in-memory password AND runs sudo -k."""
    calls: list = []

    def fake_run(argv, **kw):
        calls.append(list(argv))
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)
    elevator.register_password("hunter2")
    assert elevator.has_password_registered() is True

    elevator.invalidate()
    assert elevator.has_password_registered() is False
    # Last call should be sudo -k
    assert ["sudo", "-k"] in calls

    # Idempotent
    elevator.invalidate()
    assert elevator.has_password_registered() is False


def test_verify_password_does_not_mutate_cache(monkeypatch, elevator):
    """verify_password returns True/False without ever touching the cache."""
    monkeypatch.setattr("subprocess.run",
                        lambda *a, **kw: MagicMock(returncode=0, stdout="", stderr=""))
    assert elevator.verify_password("hunter2") is True
    assert elevator.has_password_registered() is False  # NOT cached

    monkeypatch.setattr("subprocess.run",
                        lambda *a, **kw: MagicMock(returncode=1, stdout="", stderr="bad"))
    assert elevator.verify_password("nope") is False
    assert elevator.has_password_registered() is False


# ── Allow-list ──────────────────────────────────────────────────────────────


def test_register_allowlist_lowercases_basenames(elevator):
    """Allow-list entries are stored as lowercase basenames."""
    elevator.register_allowlist(["/usr/bin/APT", "Snap", "/opt/x/Brew"])
    # White-box check on the internal frozenset.
    assert elevator._allowlist == frozenset({"apt", "snap", "brew"})


def test_register_allowlist_handles_full_path_and_basename(elevator):
    """Both /usr/bin/apt and 'apt' should normalise to 'apt'."""
    elevator.register_allowlist(["/usr/bin/apt"])
    assert "apt" in elevator._allowlist
    elevator.register_allowlist(["apt"])
    assert "apt" in elevator._allowlist


def test_register_allowlist_drops_empty_entries(elevator):
    """Empty / falsy entries are ignored."""
    elevator.register_allowlist(["apt", "", None, "snap"])  # type: ignore[list-item]
    assert elevator._allowlist == frozenset({"apt", "snap"})


# ── argv contract ───────────────────────────────────────────────────────────


def test_run_with_shell_string_argv_raises_typeerror(elevator, linux_host):
    """Shell strings (str argv) must be rejected with TypeError."""
    elevator.register_allowlist(["apt"])
    with pytest.raises(TypeError):
        elevator.run(linux_host, "apt-get install foo")  # type: ignore[arg-type]


def test_run_with_empty_argv_raises_elevation_denied(elevator, linux_host):
    """Empty argv is rejected with ElevationDenied (not TypeError)."""
    elevator.register_allowlist(["apt"])
    with pytest.raises(ElevationDenied):
        elevator.run(linux_host, [])


def test_run_rejects_command_not_in_allowlist(elevator, linux_host):
    """Commands not in the allow-list are rejected with ElevationDenied."""
    elevator.register_allowlist(["apt"])
    with pytest.raises(ElevationDenied) as excinfo:
        elevator.run(linux_host, ["rm", "-rf", "/"])
    assert "rm" in str(excinfo.value)


def test_run_with_empty_allowlist_denies_everything(elevator, linux_host):
    """Deny-by-default: never-registered allow-list rejects all commands."""
    with pytest.raises(ElevationDenied):
        elevator.run(linux_host, ["apt", "update"])


# ── is_available ────────────────────────────────────────────────────────────


def test_is_available_true_on_linux_with_sudo(monkeypatch, elevator, linux_host):
    monkeypatch.setattr("shutil.which",
                        lambda name: "/usr/bin/sudo" if name == "sudo" else None)
    assert elevator.is_available(linux_host) is True


def test_is_available_false_on_darwin(monkeypatch, elevator, darwin_host):
    """is_available returns False on non-Linux hosts even when sudo exists."""
    monkeypatch.setattr("shutil.which",
                        lambda name: "/usr/bin/sudo" if name == "sudo" else None)
    assert elevator.is_available(darwin_host) is False


def test_is_available_false_on_linux_without_sudo(monkeypatch, elevator, linux_host):
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert elevator.is_available(linux_host) is False


def test_available_methods_empty_when_sudo_missing(monkeypatch, elevator):
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert elevator.available_methods == ()


def test_available_methods_contains_sudo_when_present(monkeypatch, elevator):
    monkeypatch.setattr("shutil.which",
                        lambda name: "/usr/bin/sudo" if name == "sudo" else None)
    assert elevator.available_methods == (ElevationMethod.SUDO,)


# ── build_subprocess_env ────────────────────────────────────────────────────


def test_build_subprocess_env_omits_askpass_when_no_password(elevator):
    """No password cached → env mirrors os.environ; no SUDO_ASKPASS."""
    env = elevator.build_subprocess_env()
    assert "SUDO_ASKPASS" not in env
    assert "_ASCENDO_SUDO_PW" not in env
    # Sanity: looks like a normal env
    assert "PATH" in env or len(env) > 0


def test_build_subprocess_env_includes_askpass_when_password_cached(
    monkeypatch, elevator
):
    """Password cached → env includes SUDO_ASKPASS + _ASCENDO_SUDO_PW."""
    monkeypatch.setattr("subprocess.run",
                        lambda *a, **kw: MagicMock(returncode=0, stdout="", stderr=""))
    elevator.register_password("hunter2")
    env = elevator.build_subprocess_env()
    assert env.get("SUDO_ASKPASS") == str(_HELPER_PATH)
    assert env.get("_ASCENDO_SUDO_PW") == "hunter2"


def test_build_subprocess_env_returns_fresh_dict_each_call(
    monkeypatch, elevator
):
    """Returned env is a fresh dict; mutating it doesn't leak into os.environ."""
    monkeypatch.setattr("subprocess.run",
                        lambda *a, **kw: MagicMock(returncode=0, stdout="", stderr=""))
    elevator.register_password("hunter2")
    env1 = elevator.build_subprocess_env()
    env1["_ASCENDO_SUDO_PW"] = "TAMPERED"
    env2 = elevator.build_subprocess_env()
    assert env2["_ASCENDO_SUDO_PW"] == "hunter2"
    assert os.environ.get("_ASCENDO_SUDO_PW") != "hunter2"


def test_askpass_path_returns_none_when_no_password(elevator):
    assert elevator.askpass_path() is None


def test_askpass_path_returns_helper_when_password_cached(monkeypatch, elevator):
    monkeypatch.setattr("subprocess.run",
                        lambda *a, **kw: MagicMock(returncode=0, stdout="", stderr=""))
    elevator.register_password("hunter2")
    assert elevator.askpass_path() == _HELPER_PATH


def test_askpass_path_returns_none_if_helper_missing(monkeypatch, tmp_path):
    """If the configured helper file is missing on disk, askpass_path is None."""
    missing = tmp_path / "does-not-exist.sh"
    e = LinuxElevation(askpass_helper=missing)
    monkeypatch.setattr("subprocess.run",
                        lambda *a, **kw: MagicMock(returncode=0, stdout="", stderr=""))
    e.register_password("hunter2")
    # Password cached, but helper missing → askpass_path() returns None
    # so the caller falls back to TTY prompts.
    assert e.askpass_path() is None


# ── askpass helper script ──────────────────────────────────────────────────


def test_askpass_helper_exists():
    """The static askpass helper ships with the adapter."""
    assert _HELPER_PATH.is_file(), f"missing askpass helper at {_HELPER_PATH}"


def test_askpass_helper_is_executable():
    """The askpass helper has the executable bit set (0o755 typical)."""
    mode = stat.S_IMODE(_HELPER_PATH.stat().st_mode)
    assert mode & 0o111, f"helper at {_HELPER_PATH} is not executable (mode 0o{mode:o})"


def test_askpass_helper_echoes_env_var():
    """Running the helper with _ASCENDO_SUDO_PW set prints the password."""
    res = subprocess.run(
        [str(_HELPER_PATH)],
        capture_output=True,
        text=True,
        env={**os.environ, "_ASCENDO_SUDO_PW": "hunter2"},
        timeout=5,
    )
    assert res.returncode == 0
    assert res.stdout.strip() == "hunter2"


def test_askpass_helper_exits_nonzero_when_env_var_missing():
    """Helper exits 1 (not 0) so sudo treats missing env as auth failure."""
    env = {k: v for k, v in os.environ.items() if k != "_ASCENDO_SUDO_PW"}
    res = subprocess.run(
        [str(_HELPER_PATH)],
        capture_output=True,
        text=True,
        env=env,
        timeout=5,
    )
    assert res.returncode != 0
    assert res.stdout == ""


def test_askpass_helper_handles_special_chars():
    """Special characters in the password round-trip cleanly."""
    pw = "O'Brien42!#$ \"quoted\" `cmd`"
    res = subprocess.run(
        [str(_HELPER_PATH)],
        capture_output=True,
        text=True,
        env={**os.environ, "_ASCENDO_SUDO_PW": pw},
        timeout=5,
    )
    assert res.returncode == 0
    assert res.stdout.rstrip("\n") == pw
