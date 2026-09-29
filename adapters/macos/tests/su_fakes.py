"""Shared fakes for the softwareupdate phase-script tests.

NEVER touches the real system: ``fake softwareupdate`` is a Python script that
serves a canned ``-l`` listing, records every install call, and (like the real
tool) stops listing labels once they were installed. ``fake sudo`` logs its
argv then runs the command; ``fake sw_vers`` pins the installed macOS version.

Env knobs read by the fake softwareupdate (all optional):
    FAKE_SU_STATE               state dir (set by ``su_env``)
    FAKE_SU_FAIL_LABELS         newline list of labels whose ``-i`` exits 1
    FAKE_SU_KEEP_LABELS         newline list of labels that stay listed after a
                                successful install (install "did not take")
    FAKE_SU_LIST_FAIL_AFTER_INSTALL=1   ``-l`` exits 1 once any install ran
    FAKE_SU_LIST_FAIL=1         ``-l`` always exits 1
    state/garbage_after_install (file)  ``-l`` prints unparseable text once any
                                install ran
    FAKE_SU_SNAPSHOT_DIR        dir to search for */apply__softwareupdate.json;
                                the first ``-i`` copies it to state/snapshot.json
                                (proves what was on disk BEFORE the first install)
"""
from __future__ import annotations

import os
import stat
from pathlib import Path

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
FIX = ADAPTER_ROOT / "tests" / "fixtures" / "softwareupdate"

_FAKE_SU = r'''#!/usr/bin/env python3
import os, sys
state = os.environ["FAKE_SU_STATE"]
args = sys.argv[1:]

def lines(name):
    p = os.path.join(state, name)
    return open(p).read().split("\n") if os.path.exists(p) else []

def append(name, text):
    with open(os.path.join(state, name), "a") as fh:
        fh.write(text + "\n")

def envlist(var):
    return [x for x in os.environ.get(var, "").split("\n") if x]

if args[:1] in (["--help"], ["-h"]):
    print("fake-su test"); sys.exit(0)
if args[:1] in (["-l"], ["--list"]):
    append("list_calls.log", "LANG=%s LC_ALL=%s ARGS=%s" % (
        os.environ.get("LANG"), os.environ.get("LC_ALL"), " ".join(args)))
    if os.environ.get("FAKE_SU_LIST_FAIL") == "1":
        sys.stderr.write("broken\n"); sys.exit(1)
    installs = [l for l in lines("installs.log") if l]
    if installs and os.environ.get("FAKE_SU_LIST_FAIL_AFTER_INSTALL") == "1":
        sys.stderr.write("broken after install\n"); sys.exit(1)
    if installs and os.path.exists(os.path.join(state, "garbage_after_install")):
        print("Software Update Tool\n\nunexpected output"); sys.exit(0)
    installed = set(l for l in lines("installed.txt") if l)
    text = open(os.path.join(state, "listing.txt")).read()
    out, keep = [], True
    for line in text.split("\n"):
        if line.startswith("* "):
            label = line[2:]
            if label.startswith("Label: "):
                label = label[len("Label: "):]
            keep = label not in installed
        if keep:
            out.append(line)
    had_blocks = any(l.startswith("* ") for l in text.split("\n"))
    if had_blocks and not any(l.startswith("* ") for l in out):
        # like the real tool once nothing is left
        sys.stdout.write("Software Update Tool\n\nFinding available software\nNo new software available.\n")
        sys.exit(0)
    sys.stdout.write("\n".join(out))
    sys.exit(0)
if args[:1] == ["-i"]:
    labels = []
    for a in args[1:]:
        if a.startswith("-"):
            break
        labels.append(a)
    snap_dir = os.environ.get("FAKE_SU_SNAPSHOT_DIR")
    if snap_dir and not os.path.exists(os.path.join(state, "snapshot.json")):
        import glob, shutil
        found = glob.glob(os.path.join(snap_dir, "*", "apply__softwareupdate.json"))
        if found:
            shutil.copy(found[0], os.path.join(state, "snapshot.json"))
        else:
            open(os.path.join(state, "snapshot.json"), "w").write("MISSING")
    append("installs.log", "|".join(args))
    if any(l in envlist("FAKE_SU_FAIL_LABELS") for l in labels):
        sys.stderr.write("install failed\n"); sys.exit(1)
    keep = envlist("FAKE_SU_KEEP_LABELS")
    for l in labels:
        if l not in keep:
            append("installed.txt", l)
    print("==> simulated install: " + " ".join(args))
    sys.exit(0)
sys.exit(1)
'''


def _write_exec(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def su_env(tmp_path: Path, fixture: str = "incremental-updates.txt", *,
           cur_os: str = "26.6.2", listing_text: str | None = None,
           **extra_env: str) -> dict:
    """Env for running a softwareupdate phase script against the fakes.

    Returns a dict with: ``env`` (subprocess env), ``state`` (state dir),
    ``sudo_log`` (Path, one line per sudo call), and helpers ``installs()``
    (list of ``-i`` argv strings joined by ``|``).
    """
    state = tmp_path / "su_state"
    state.mkdir(exist_ok=True)
    (state / "listing.txt").write_text(
        listing_text if listing_text is not None else (FIX / fixture).read_text())
    su = _write_exec(tmp_path / "fake_softwareupdate", _FAKE_SU)
    sudo_log = tmp_path / "sudo.log"
    bindir = tmp_path / "bin"
    _write_exec(
        bindir / "sudo",
        "#!/usr/bin/env bash\n"
        f"echo \"$@\" >> {sudo_log}\n"
        "if [ \"$1\" = '-A' ]; then shift; fi\n"
        "\"$@\"\n",
    )
    _write_exec(
        bindir / "sw_vers",
        "#!/usr/bin/env bash\n"
        f"if [ \"$1\" = '-productVersion' ]; then echo '{cur_os}'; exit 0; fi\n"
        "exit 0\n",
    )
    env = dict(os.environ)
    env.pop("ASCENDO_SAFE_MODE", None)
    env.pop("ASCENDO_ALLOW_MAJOR_UPGRADE", None)
    env.update({
        "SU_BIN": str(su),
        "FAKE_SU_STATE": str(state),
        "PATH": f"{bindir}:{env['PATH']}",
    })
    env.update(extra_env)

    def installs() -> list:
        p = state / "installs.log"
        return [ln for ln in p.read_text().splitlines() if ln] if p.exists() else []

    return {"env": env, "state": state, "sudo_log": sudo_log, "installs": installs}
