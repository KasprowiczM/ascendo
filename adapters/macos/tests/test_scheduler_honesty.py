"""Scheduler honesty (Task 17, Spec E): a launchd load failure is an error,
the quarantine flag is stripped from the plist and the job is enabled.

Fake launchctl / xattr on PATH only; the LaunchAgents dir lives under tmp.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ADAPTER_ROOT / "scripts" / "scheduler" / "scheduler.sh"
PAYLOAD = {"name": "nightly", "expression": "DAILY 03:00", "profile": "safe",
           "enabled": True, "description": None}


def _fakes(tmp_path: Path, bootstrap_rc: int, print_rc: int, load_rc: int = 0):
    bindir = tmp_path / "fakebin"
    bindir.mkdir()
    log = tmp_path / "calls.log"
    lc = bindir / "launchctl"
    lc.write_text(
        "#!/bin/sh\n"
        f'echo "launchctl $*" >> "{log}"\n'
        'case "$1" in\n'
        f"  bootstrap) exit {bootstrap_rc} ;;\n"
        f"  print) exit {print_rc} ;;\n"
        f"  load) exit {load_rc} ;;\n"
        "esac\nexit 0\n")
    xa = bindir / "xattr"
    xa.write_text(f'#!/bin/sh\necho "xattr $*" >> "{log}"\nexit 0\n')
    for f in (lc, xa):
        f.chmod(0o755)
    return bindir, log


def _install(tmp_path, bindir):
    out = tmp_path / "out.json"
    pl = tmp_path / "payload.json"
    pl.write_text(json.dumps(PAYLOAD))
    env = dict(os.environ)
    env["ASCENDO_HOME_OVERRIDE"] = str(tmp_path / "home")
    env["PATH"] = f"{bindir}{os.pathsep}{env['PATH']}"
    r = subprocess.run(["bash", str(SCRIPT), "--action", "install", "--output-path", str(out),
                        "--payload-path", str(pl)], capture_output=True, text=True, env=env)
    return r, out


def test_bootstrap_failure_is_an_error(tmp_path):
    bindir, log = _fakes(tmp_path, bootstrap_rc=5, print_rc=113, load_rc=1)
    r, out = _install(tmp_path, bindir)
    assert r.returncode == 30, r.stderr
    assert "could not load dev.ascendo.nightly" in json.loads(out.read_text())["error"]
    calls = log.read_text()
    assert "launchctl load -w" in calls          # legacy fallback was attempted
    assert "launchctl enable" not in calls


def test_failed_bootstrap_rc_ignored_when_job_is_loaded(tmp_path):
    # bootstrap rc != 0 but launchd lists the job: honest success.
    bindir, log = _fakes(tmp_path, bootstrap_rc=5, print_rc=0)
    r, out = _install(tmp_path, bindir)
    assert r.returncode == 0, r.stderr
    assert json.loads(out.read_text()) == {"ok": True}


def test_success_strips_quarantine_and_enables(tmp_path):
    bindir, log = _fakes(tmp_path, bootstrap_rc=0, print_rc=0)
    r, _ = _install(tmp_path, bindir)
    assert r.returncode == 0, r.stderr
    calls = log.read_text().splitlines()
    plist = str(tmp_path / "home/Library/LaunchAgents/dev.ascendo.nightly.plist")
    xi = [i for i, c in enumerate(calls) if c == f"xattr -d com.apple.quarantine {plist}"]
    bi = [i for i, c in enumerate(calls) if c.startswith("launchctl bootstrap")]
    assert xi and bi and xi[0] < bi[0], calls   # quarantine stripped BEFORE bootstrap
    assert any(c == "launchctl enable gui/%d/dev.ascendo.nightly" % os.getuid() for c in calls)
