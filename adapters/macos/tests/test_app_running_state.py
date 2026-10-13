"""Tri-state running-app probe (Task 10 / F2, Ruling 25).

`ascendo_app_running_state <bundle_id>` prints true|false|unknown. `unknown`
(lsappinfo missing, non-zero exit, empty listing, invalid id) is never "idle":
every copy/swap/upgrade gate proceeds only on an explicit `false`.

Hermetic: the probe is pointed at a fake lsappinfo via ASCENDO_LSAPPINFO_BIN.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

LIB = Path(__file__).resolve().parents[1] / "lib"

BENIGN = 'bundleID="com.apple.finder"\nbundleID="com.example.other"\n'


def fake_lsappinfo(tmp: Path, listing: str = BENIGN, rc: int = 0) -> Path:
    path = tmp / "lsappinfo"
    path.write_text(f"#!/bin/sh\n[ \"$1\" = list ] && cat <<'EOF_LS'\n{listing}EOF_LS\nexit {rc}\n")
    path.chmod(0o755)
    return path


def run(tmp: Path, snippet: str, lsappinfo: Path | None, *, path_has_lsappinfo: bool = False):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("ASCENDO_", "MAC_UPDATE_"))}
    env["PATH"] = f"{tmp / 'emptybin'}:/usr/bin:/bin:/usr/sbin:/sbin"
    (tmp / "emptybin").mkdir(exist_ok=True)
    if lsappinfo is not None:
        env["ASCENDO_LSAPPINFO_BIN"] = str(lsappinfo)
    # Hermetic: if lsappinfo is NOT given, hide the real one (it lives in /usr/bin).
    if lsappinfo is None and not path_has_lsappinfo:
        env["ASCENDO_LSAPPINFO_BIN"] = str(tmp / "does-not-exist")
    return subprocess.run(["bash", "-c", f'. "{LIB}/ascendo_proc.sh"; . "{LIB}/ascendo_web.sh"; {snippet}'],
                          capture_output=True, text=True, env=env)


def state(tmp: Path, bid: str, ls: Path | None) -> str:
    r = run(tmp, f'ascendo_app_running_state "{bid}"; echo "rc=$?"', ls)
    assert r.returncode == 0, r.stderr
    out = r.stdout.split()
    assert out[-1] == "rc=0", r.stdout
    return out[0]


def test_state_true_false(tmp_path):
    ls = fake_lsappinfo(tmp_path, BENIGN + 'bundleID="com.example.app"\n')
    assert state(tmp_path, "com.example.app", ls) == "true"
    assert state(tmp_path, "com.example.zzz", ls) == "false"


def test_state_is_exact_not_substring(tmp_path):
    ls = fake_lsappinfo(tmp_path, BENIGN + 'bundleID="com.example.app.helper"\n')
    assert state(tmp_path, "com.example.app", ls) == "false"


def test_state_unknown_when_lsappinfo_missing(tmp_path):
    assert state(tmp_path, "com.example.app", tmp_path / "nope") == "unknown"


def test_state_unknown_on_nonzero_exit(tmp_path):
    ls = fake_lsappinfo(tmp_path, BENIGN + 'bundleID="com.example.app"\n', rc=3)
    # even though the listing mentions it, a failed probe is not an answer
    assert state(tmp_path, "com.example.app", ls) == "unknown"


def test_state_unknown_on_empty_listing(tmp_path):
    ls = fake_lsappinfo(tmp_path, "")
    assert state(tmp_path, "com.example.app", ls) == "unknown"


def test_state_unknown_for_invalid_bundle_id(tmp_path):
    ls = fake_lsappinfo(tmp_path)
    for bad in ("", "a b", 'a"b', "a;b", "$(x)"):
        r = subprocess.run(["bash", "-c", f'. "{LIB}/ascendo_proc.sh"; ascendo_app_running_state "$1"', "x", bad],
                           capture_output=True, text=True,
                           env={"PATH": "/usr/bin:/bin", "ASCENDO_LSAPPINFO_BIN": str(ls)})
        assert r.stdout.strip() == "unknown", (bad, r.stdout)


def test_state_unknown_when_probe_times_out(tmp_path):
    """The runner's 124 (deadline hit) is an unanswered probe. The 10 s budget is
    replaced by a stub that reports the timeout so the test stays instant."""
    ls = fake_lsappinfo(tmp_path, BENIGN)
    env = {"PATH": "/usr/bin:/bin", "ASCENDO_LSAPPINFO_BIN": str(ls)}
    r = subprocess.run(["bash", "-c",
                        f'. "{LIB}/ascendo_proc.sh"; '
                        'ascendo_run_with_timeout() { return 124; }; '
                        'ascendo_app_running_state com.example.app'],
                       capture_output=True, text=True, env=env)
    assert r.stdout.strip() == "unknown"


def test_state_large_listing_with_pipefail(tmp_path):
    big = "".join(f'bundleID="com.filler.app{i}"\n' for i in range(40000)) + 'bundleID="com.example.app"\n'
    ls = fake_lsappinfo(tmp_path, big)
    r = run(tmp_path, 'set -o pipefail; ascendo_app_running_state com.example.app', ls)
    assert r.stdout.strip() == "true"


def test_web_is_running_is_thin_wrapper(tmp_path):
    ls = fake_lsappinfo(tmp_path, BENIGN + 'bundleID="com.example.app"\n')
    for bid, expect in (("com.example.app", "y"), ("com.example.zzz", "n")):
        r = run(tmp_path, f'_web_is_running {bid} && echo y || echo n', ls)
        assert r.stdout.strip() == expect
    # unknown is NOT "running" for the message wrapper; gates use the state.
    r = run(tmp_path, '_web_is_running com.example.app && echo y || echo n', tmp_path / "nope")
    assert r.stdout.strip() == "n"
    r = run(tmp_path, '[ "$(ascendo_app_running_state com.example.app)" != false ] && echo defer || echo go',
            tmp_path / "nope")
    assert r.stdout.strip() == "defer"


def test_brew_bundle_running_is_thin_wrapper(tmp_path):
    ls = fake_lsappinfo(tmp_path, BENIGN + 'bundleID="com.example.app"\n')
    snippet = '. "%s/ascendo_brew.sh"; ascendo_brew_bundle_running %s && echo y || echo n'
    for bid, expect in (("com.example.app", "y"), ("com.example.zzz", "n")):
        r = run(tmp_path, snippet % (LIB, bid), ls)
        assert r.stdout.strip() == expect, r.stderr
