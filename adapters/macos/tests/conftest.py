"""Shared fixtures for adapters/macos/tests/."""
from __future__ import annotations

import sys
from pathlib import Path

# Make ascendo (core) importable in tests without an editable install.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_CORE_SRC = _REPO_ROOT / "core"
if str(_CORE_SRC) not in sys.path:
    sys.path.insert(0, str(_CORE_SRC))

# Make ascendo_macos importable.
_ADAPTER_SRC = Path(__file__).resolve().parents[1]
if str(_ADAPTER_SRC) not in sys.path:
    sys.path.insert(0, str(_ADAPTER_SRC))


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_ipad_scan(tmp_path_factory, monkeypatch):
    """The mas phase scripts scan /Applications for iPad wrapper apps and, when
    any exist, hit the network (iTunes Lookup). Point every test at an empty app
    dir so the suite never depends on -- or contacts anything for -- the host's
    real apps. Tests that exercise the iPad path set ASCENDO_APP_DIRS themselves.
    """
    empty = tmp_path_factory.mktemp("no_ipad_apps")
    monkeypatch.setenv("ASCENDO_APP_DIRS", str(empty))


@pytest.fixture(autouse=True)
def _isolate_web_state(tmp_path_factory, monkeypatch):
    """The web phases hand state to each other through the canonical run dir
    ($ASCENDO_RUNS_DIR, else ~/.ascendo/runs) and the version-seen file. Point
    every test at throw-away locations so no test ever writes into the real
    ~/.ascendo (tests that care set these themselves)."""
    base = tmp_path_factory.mktemp("web_state")
    monkeypatch.setenv("ASCENDO_RUNS_DIR", str(base / "runs"))
    monkeypatch.setenv("ASCENDO_WEB_SEEN_FILE", str(base / "seen.tsv"))


@pytest.fixture(autouse=True)
def _isolate_mau_state(tmp_path_factory, monkeypatch):
    """Microsoft AutoUpdate: never read/write the real com.microsoft.autoupdate2
    domain, never run pgrep, never reach the Teams-cask oracle over the network.
    Tests that exercise those paths set the knobs themselves."""
    base = tmp_path_factory.mktemp("mau_state")
    prefs = base / "prefs.plist"
    prefs.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0"><dict/></plist>\n'
    )
    monkeypatch.setenv("ASCENDO_MAU_PREFS_FILE", str(prefs))
    monkeypatch.setenv("ASCENDO_MSUPDATE_TEAMS_CASK_URL", "")
    monkeypatch.setenv("ASCENDO_MAU_MOCK_ACTIVE_PROCESSES", "")
    for name in ("ASCENDO_MAU_SCOPED_INSTALL", "ASCENDO_MAU_MUTATE_PREFS",
                 "ASCENDO_MSUPDATE_LIST_FILE", "ASCENDO_MSUPDATE_CHECK_TIMEOUT",
                 "ASCENDO_MSUPDATE_INSTALL_WAIT"):
        monkeypatch.delenv(name, raising=False)


TRIPWIRE_TOOLS = ("brew", "mas", "msupdate", "softwareupdate", "osascript", "open",
                  "mdfind", "docker", "GoogleUpdater", "CometUpdater")


@pytest.fixture(autouse=True)
def tripwire(tmp_path_factory, monkeypatch):
    """Suite-wide tripwire (Ruling 44): no test may reach a REAL updater/installer.

    A bin dir of fake brew / mas / msupdate / softwareupdate / osascript / open /
    mdfind / docker / GoogleUpdater / CometUpdater goes FIRST on PATH; each logs its
    argv to ``$TRIPWIRE_LOG`` and exits 97. The env seams for absolute-path tools
    (updater binaries, msupdate, docker, softwareupdate, mdfind, system_profiler)
    default to tripwire paths. Tests that install their own fakes keep working:
    their PATH / env is applied after (and wins over) these defaults.

    Yields the log path; ``tripwire_hits(log)`` lists the tools that were invoked.
    """
    import os
    import stat

    base = tmp_path_factory.mktemp("tripwire")
    bindir = base / "bin"
    bindir.mkdir()
    log = base / "tripwire.log"
    log.write_text("")
    for tool in TRIPWIRE_TOOLS:
        if tool == "brew" and os.environ.get("ASCENDO_ALLOW_REAL_BREW") == "1":
            continue   # explicit opt-in to the read-only real-brew tests
        p = bindir / tool
        p.write_text(
            "#!/bin/sh\n"
            f"printf '%s %s\\n' '{tool}' \"$*\" >> '{log}'\n"
            "echo 'TRIPWIRE: refusing to run a real tool in a test' >&2\n"
            "exit 97\n")
        p.chmod(p.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("TRIPWIRE_LOG", str(log))
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("SU_BIN", str(bindir / "softwareupdate"))
    monkeypatch.setenv("ASCENDO_MSUPDATE_BIN", str(bindir / "msupdate"))
    monkeypatch.setenv("ASCENDO_WEB_UPDATER_BIN", str(bindir / "GoogleUpdater"))
    # Updater binaries under the pytest tmp tree are the tests' own fakes.
    monkeypatch.setenv("ASCENDO_WEB_UPDATER_ALLOW_PREFIX",
                       str(tmp_path_factory.getbasetemp()) + "/")
    monkeypatch.setenv("ASCENDO_WEB_MDFIND_BIN", str(bindir / "mdfind"))
    sp_empty = base / "sp_empty.json"
    sp_empty.write_text('{"SPApplicationsDataType": []}')
    monkeypatch.setenv("ASCENDO_WEB_SP_CACHE", str(sp_empty))
    monkeypatch.setenv("ASCENDO_WEB_SP_BIN", str(bindir / "mdfind"))
    return log


def tripwire_hits(log) -> list:
    """Tools the tripwire saw, in order (``[]`` = nothing real was reached)."""
    return [ln.split(" ", 1)[0] for ln in log.read_text().splitlines() if ln.strip()]
