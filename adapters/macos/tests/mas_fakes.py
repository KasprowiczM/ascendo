"""Shared fakes for the Mac App Store (mas) phase-script tests.

NEVER touches the real system: ``fake mas`` serves canned ``list`` / ``outdated``
output and records every call; ``fake sudo`` logs its argv, drops
MAS_NO_AUTO_INDEX like real sudo's env_reset (so a test only passes when the
variable is delivered explicitly via ``sudo env ...``) and marks the child as
running "under sudo"; ``fake curl`` serves a canned iTunes Lookup body.

Like the real tool, fake mas stops listing an id in ``outdated`` once it was
upgraded -- unless the id is in FAKE_MAS_KEEP_IDS (the sudo'd upgrade "did not
take": the 2026-09 WhatsApp failure) and, for those, a *user-session* upgrade
clears it only when FAKE_MAS_USER_CLEARS=1.

Env knobs read by fake mas (all optional):
    FAKE_MAS_STATE                 state dir (set by ``mas_env``)
    FAKE_MAS_OUTDATED_FAIL=1       ``outdated`` exits 1 (stderr text)
    FAKE_MAS_OUTDATED_FAIL_AFTER_UPGRADE=1   ``outdated`` exits 1 once an upgrade ran
    FAKE_MAS_OUTDATED_SLEEP=N      ``outdated`` sleeps N seconds first
    FAKE_MAS_LIST_FAIL=1           ``list`` exits 1
    FAKE_MAS_UPGRADE_RC=N          ``upgrade`` exits N (does not clear ids when != 0)
    FAKE_MAS_UPGRADE_SLEEP=N       ``upgrade`` sleeps N seconds first
    FAKE_MAS_KEEP_IDS              newline list (see above)
    FAKE_MAS_USER_CLEARS=1         see above
    FAKE_MAS_STORE=PL              ``config --json`` prints {"store": "PL"}
Env knobs read by fake curl:
    FAKE_CURL_BODY_FILE            file whose content is printed
    FAKE_CURL_RC=N                 exit N (no output), e.g. 22 for an HTTP error
"""
from __future__ import annotations

import os
import plistlib
import stat
from pathlib import Path

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
FIX = ADAPTER_ROOT / "tests" / "fixtures"

_FAKE_MAS = r'''#!/usr/bin/env python3
import os, sys, time
state = os.environ["FAKE_MAS_STATE"]
args = sys.argv[1:]

def lines(name):
    p = os.path.join(state, name)
    return open(p).read().split("\n") if os.path.exists(p) else []

def append(name, text):
    with open(os.path.join(state, name), "a") as fh:
        fh.write(text + "\n")

def envlist(var):
    return [x for x in os.environ.get(var, "").split("\n") if x]

sudo = "1" if os.environ.get("FAKE_SUDO_ACTIVE") == "1" else "0"
append("calls.log", "sudo=%s MAS_NO_AUTO_INDEX=%s ARGS=%s" % (
    sudo, os.environ.get("MAS_NO_AUTO_INDEX", "<unset>"), " ".join(args)))

cmd = args[:1]
if cmd == ["version"]:
    print("4.3.0"); sys.exit(0)
if cmd == ["config"]:
    if os.environ.get("FAKE_MAS_STORE"):
        print('{"store":"%s","region":"%s"}' % ((os.environ["FAKE_MAS_STORE"],) * 2))
        sys.exit(0)
    sys.exit(1)
if cmd == ["list"]:
    if os.environ.get("FAKE_MAS_LIST_FAIL") == "1":
        sys.stderr.write("list broke\n"); sys.exit(1)
    sys.stdout.write(open(os.path.join(state, "list.txt")).read()); sys.exit(0)
if cmd == ["outdated"]:
    if os.environ.get("FAKE_MAS_OUTDATED_SLEEP"):
        time.sleep(float(os.environ["FAKE_MAS_OUTDATED_SLEEP"]))
    if os.environ.get("FAKE_MAS_OUTDATED_FAIL") == "1":
        sys.stderr.write("outdated broke\n"); sys.exit(1)
    upgrades = [l for l in lines("upgrades.log") if l]
    if upgrades and os.environ.get("FAKE_MAS_OUTDATED_FAIL_AFTER_UPGRADE") == "1":
        sys.stderr.write("outdated broke after upgrade\n"); sys.exit(1)
    done = set(l for l in lines("cleared.txt") if l)
    for line in open(os.path.join(state, "outdated.txt")).read().splitlines():
        first = line.split()[0] if line.split() else ""
        if first in done:
            continue
        print(line)
    sys.exit(0)
if cmd == ["upgrade"]:
    ids = args[1:]
    append("upgrades.log", " ".join(ids))
    if os.environ.get("FAKE_MAS_UPGRADE_SLEEP"):
        time.sleep(float(os.environ["FAKE_MAS_UPGRADE_SLEEP"]))
    rc = int(os.environ.get("FAKE_MAS_UPGRADE_RC", "0"))
    keep = envlist("FAKE_MAS_KEEP_IDS")
    for i in ids:
        print("==> upgrading %s" % i)
        if rc != 0:
            continue
        stuck = i in keep and not (sudo == "0" and os.environ.get("FAKE_MAS_USER_CLEARS") == "1")
        if not stuck:
            append("cleared.txt", i)
    sys.exit(rc)
sys.exit(1)
'''

_FAKE_SUDO = r'''#!/usr/bin/env bash
echo "$@" >> "$FAKE_MAS_STATE/sudo.log"
if [ "$1" = '-A' ]; then shift; fi
# real sudo's env_reset drops MAS_NO_AUTO_INDEX; only `sudo env X=1 cmd` delivers it
exec env -u MAS_NO_AUTO_INDEX FAKE_SUDO_ACTIVE=1 "$@"
'''

_FAKE_CURL = r'''#!/usr/bin/env bash
echo "$@" >> "$FAKE_MAS_STATE/curl.log"
if [ -n "${FAKE_CURL_RC:-}" ] && [ "$FAKE_CURL_RC" != "0" ]; then exit "$FAKE_CURL_RC"; fi
[ -n "${FAKE_CURL_BODY_FILE:-}" ] && cat "$FAKE_CURL_BODY_FILE"
exit 0
'''


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def mas_env(tmp_path: Path, *, outdated_text: str | None = None,
            list_text: str | None = None, app_dirs: list[Path] | None = None) -> dict:
    """Build an env dict (MAS_BIN, PATH with fake sudo+curl, state dir).

    Returns ``{"env": {...}, "state": Path, "bin": Path}``; ``env`` holds only the
    overrides -- merge it over ``os.environ`` when spawning a script.
    """
    state = tmp_path / "mas_state"
    state.mkdir(exist_ok=True)
    (state / "outdated.txt").write_text(
        outdated_text if outdated_text is not None else (FIX / "mas-outdated.txt").read_text())
    (state / "list.txt").write_text(
        list_text if list_text is not None else (FIX / "mas-list.txt").read_text())
    binroot = tmp_path / "mas_bin"
    mas = _write(binroot / "mas", _FAKE_MAS)
    _write(binroot / "sudo", _FAKE_SUDO)
    _write(binroot / "curl", _FAKE_CURL)
    empty_apps = tmp_path / "no_apps"
    empty_apps.mkdir(exist_ok=True)
    dirs = [str(d) for d in app_dirs] if app_dirs is not None else [str(empty_apps)]
    env = {
        "MAS_BIN": str(mas),
        "PATH": f"{binroot}:{os.environ['PATH']}",
        "FAKE_MAS_STATE": str(state),
        "ASCENDO_APP_DIRS": ":".join(dirs),
    }
    return {"env": env, "state": state, "bin": binroot}


def make_ipad_app(root: Path, name: str, item_id: int, version: str,
                  item_name: str | None = None) -> Path:
    """Create ``<root>/<name>.app/Wrapper/iTunesMetadata.plist`` (an iPad wrapper)."""
    app = root / f"{name}.app"
    (app / "Wrapper").mkdir(parents=True, exist_ok=True)
    (app / "Wrapper" / "iTunesMetadata.plist").write_bytes(plistlib.dumps({
        "itemId": item_id,
        "bundleShortVersionString": version,
        "itemName": item_name or name,
    }))
    return app
