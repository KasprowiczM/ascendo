# macOS adapter — M5.5 launchd `IScheduler` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `LaunchdScheduler` for the macOS adapter — a per-user `launchd` LaunchAgent driver that implements `IScheduler` (install / uninstall / list / get / trigger). After this milestone, `MacOSAdapter` declares `PACKAGE_MANAGEMENT | ELEVATION | INVENTORY | SNAPSHOTS | SCHEDULING` and the macOS adapter is feature-complete (tag `v0.2.0`).

**Architecture:** Mirrors M3.13 (Windows Task Scheduler) JSON-IPC pattern. Python `LaunchdScheduler` writes a JSON payload, spawns `bash adapters/macos/scripts/scheduler/scheduler.sh --action <verb> --output-path <result.json> [--payload-path <payload.json>]`, parses the JSON result. The bash driver is the single source of truth for plist serialisation, `launchctl` invocation, and DSL → `StartCalendarInterval` translation. Per-user LaunchAgents written to `~/Library/LaunchAgents/dev.ascendo.<name>.plist`; description metadata stored in a sidecar JSON at `~/Library/Application Support/Ascendo/schedules/<name>.json` (launchd plists have no free-form notes channel).

**Tech Stack:** Python 3.11+, Pydantic v2, Bash 3.2 (macOS default `/bin/bash`), launchctl, plist (XML form). Test stack: pytest with mock-based smoke tests for Python, real-bash + fake-`launchctl`-on-PATH tests for the driver script.

**Spec:** [docs/superpowers/specs/2026-05-04-macos-launchd-scheduler-design.md](docs/superpowers/specs/2026-05-04-macos-launchd-scheduler-design.md)

---

## File Structure

**Create:**

| Path | Responsibility |
|------|----------------|
| `adapters/macos/scripts/scheduler/scheduler.sh` | Bash driver. Dispatches `--action {install|uninstall|list|get|trigger}`, writes/reads plists, shells out to `launchctl`, translates DSL → `StartCalendarInterval`. ~300 LOC. |
| `adapters/macos/ascendo_macos/managers/scheduler.py` | `LaunchdScheduler(IScheduler)`. Spawns the bash driver via JSON-IPC. ~150 LOC. |
| `adapters/macos/tests/test_launchd_scheduler_smoke.py` | Mock-based unit tests for `LaunchdScheduler`. ~12 tests. |
| `adapters/macos/tests/test_scheduler_script_smoke.py` | Real-bash tests with fake `launchctl` on PATH. ~10 tests. |

**Modify:**

| Path | Change |
|------|--------|
| `adapters/macos/ascendo_macos/adapter.py:115-122` | `capabilities` adds `SCHEDULING`. |
| `adapters/macos/ascendo_macos/adapter.py:157-159` | `scheduler()` returns cached `LaunchdScheduler` singleton. |
| `adapters/macos/ascendo_macos/adapter.py:94-99` | `__init__` adds `_cached_scheduler: LaunchdScheduler \| None = None`. |
| `adapters/macos/ascendo_macos/adapter.py:205-222` | `health_check()` adds `launchctl` component. |
| `adapters/macos/ascendo_macos/adapter.py` (private helpers section) | New `_launchctl_status()` method. |
| `adapters/macos/tests/test_adapter_smoke.py:29-50` | Update capability assertion + accessor None-ness assertion (both reflect SCHEDULING flipped on, scheduler() now non-None). |
| `bin/validate-macos.sh:543-574` | Insert Stage 12 (5 sub-steps) after Stage 11. |
| `bin/run-tag-release-macos.sh` | Bump tag from `v0.0.11-alpha` → `v0.2.0`; M5.5 message. |
| `PLAN.md` | Mark M5.5 ✅ done after merge. (Final step.) |
| `HANDOFF.md` | New "Sesja 27" section after merge. (Final step.) |

**Why this layout:** matches the established pattern from M5.1 (brew), M5.2 (mas + elevation), M5.3 (LaunchServices inventory), M5.4 (softwareupdate + snapshot). Each macOS sub-milestone adds one Python manager + one or more bash scripts + one capability flip + one Stage in `validate-macos.sh`. No cross-cutting changes.

---

## Task 1: bash driver — argv + dispatch skeleton

**Files:**
- Create: `adapters/macos/scripts/scheduler/scheduler.sh`
- Test: `adapters/macos/tests/test_scheduler_script_smoke.py`

- [ ] **Step 1: Create the test file with the dispatch skeleton test**

Create `adapters/macos/tests/test_scheduler_script_smoke.py`:

```python
"""Tests for adapters/macos/scripts/scheduler/scheduler.sh.

Real-bash tests with a fake launchctl binary on PATH that records argv
to a log file. No real LaunchAgents written — the script's home dir is
overridden via env vars.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ADAPTER_ROOT / "scripts" / "scheduler" / "scheduler.sh"


def _make_fake_launchctl(tmp_path: Path) -> tuple[Path, Path]:
    """Fake launchctl binary recording each invocation to a log file."""
    log = tmp_path / "launchctl.log"
    binary = tmp_path / "launchctl"
    binary.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$*" >> "{log}"\n'
        "exit 0\n"
    )
    os.chmod(binary, 0o755)
    return binary, log


def _run(action: str, *, payload: dict | None, tmp_path: Path,
         fake_home: Path | None = None,
         launchctl: Path | None = None) -> tuple[subprocess.CompletedProcess, Path]:
    """Run the driver. Returns (CompletedProcess, output.json path)."""
    output = tmp_path / "result.json"
    argv = ["bash", str(SCRIPT), "--action", action, "--output-path", str(output)]
    if payload is not None:
        payload_path = tmp_path / "payload.json"
        payload_path.write_text(json.dumps(payload))
        argv += ["--payload-path", str(payload_path)]
    env = dict(os.environ)
    if fake_home is not None:
        env["ASCENDO_HOME_OVERRIDE"] = str(fake_home)
    if launchctl is not None:
        env["PATH"] = f"{launchctl.parent}{os.pathsep}{env.get('PATH', '')}"
    res = subprocess.run(argv, capture_output=True, text=True, env=env, check=False)
    return res, output


def test_unknown_action_exits_2(tmp_path):
    res, _ = _run("bogus", payload=None, tmp_path=tmp_path)
    assert res.returncode == 2
    assert "unknown action" in (res.stderr + res.stdout).lower()


def test_missing_output_path_exits_2(tmp_path):
    res = subprocess.run(
        ["bash", str(SCRIPT), "--action", "list"],
        capture_output=True, text=True, check=False,
    )
    assert res.returncode == 2
```

- [ ] **Step 2: Run the test to verify it fails (script doesn't exist)**

Run: `cd /Users/mk/Dev_Env/Ascendo/.claude/worktrees/cool-beaver-f1879c && python3 -m pytest adapters/macos/tests/test_scheduler_script_smoke.py -v 2>&1 | head -20`

Expected: FAIL with `bash: scheduler.sh: No such file or directory` or test errors out (script missing).

- [ ] **Step 3: Create the bash driver skeleton**

Create `adapters/macos/scripts/scheduler/scheduler.sh`:

```bash
#!/usr/bin/env bash
# =============================================================================
# adapters/macos/scripts/scheduler/scheduler.sh
# launchd LaunchAgent driver. Mirrors the M3.13 Windows Task Scheduler
# pattern (scheduler.ps1) — JSON in, JSON out, single dispatch on --action.
#
# Actions:
#   --action install   register/overwrite a per-user LaunchAgent.
#                      Reads ScheduleSpec from --payload-path as JSON.
#   --action uninstall remove an Ascendo-owned LaunchAgent by name.
#   --action list      enumerate Ascendo-owned LaunchAgents (JSON array).
#   --action get       return one entry by name (JSON object or null).
#   --action trigger   run a registered agent immediately.
#
# Bash 3.2 compatible (no declare -A, mapfile, readarray).
# =============================================================================
set -o pipefail

ACTION=""
OUTPUT_PATH=""
PAYLOAD_PATH=""

while [ $# -gt 0 ]; do
    case "$1" in
        --action)       ACTION="$2";       shift 2 ;;
        --output-path)  OUTPUT_PATH="$2";  shift 2 ;;
        --payload-path) PAYLOAD_PATH="$2"; shift 2 ;;
        *) printf 'scheduler.sh: unknown arg: %s\n' "$1" >&2; exit 2 ;;
    esac
done

if [ -z "$ACTION" ] || [ -z "$OUTPUT_PATH" ]; then
    printf 'scheduler.sh: missing required args (--action, --output-path)\n' >&2
    exit 2
fi

# Test override: ASCENDO_HOME_OVERRIDE redirects all reads/writes away
# from the operator's real ~/Library. Real runs leave it unset.
HOME_BASE="${ASCENDO_HOME_OVERRIDE:-$HOME}"
LAUNCH_AGENTS_DIR="$HOME_BASE/Library/LaunchAgents"
LOGS_DIR="$HOME_BASE/Library/Logs/Ascendo"
SCHEDULES_DIR="$HOME_BASE/Library/Application Support/Ascendo/schedules"
LABEL_PREFIX="dev.ascendo."

UID_VAL="$(id -u)"

emit_json() {
    local _payload="$1"
    local _dir
    _dir="$(dirname "$OUTPUT_PATH")"
    [ -d "$_dir" ] || mkdir -p "$_dir"
    printf '%s\n' "$_payload" > "$OUTPUT_PATH"
}

emit_error() {
    emit_json "{\"error\": $(printf '%s' "$1" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read().rstrip()))')}"
}

case "$ACTION" in
    install|uninstall|list|get|trigger)
        ;;
    *)
        printf 'scheduler.sh: unknown action: %s\n' "$ACTION" >&2
        exit 2
        ;;
esac

# Action handlers land in subsequent tasks.
emit_json '{"ok": true}'
exit 0
```

Make it executable:

```bash
chmod +x adapters/macos/scripts/scheduler/scheduler.sh
```

- [ ] **Step 4: Run test to verify both unknown-action + missing-output tests pass**

Run: `python3 -m pytest adapters/macos/tests/test_scheduler_script_smoke.py -v 2>&1 | head -20`

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add adapters/macos/scripts/scheduler/scheduler.sh adapters/macos/tests/test_scheduler_script_smoke.py
git commit -m "feat(macos): scheduler.sh argv parsing + dispatch skeleton (M5.5.1)"
```

---

## Task 2: bash driver — `_parse_expression` DSL → calendar dict

**Files:**
- Modify: `adapters/macos/scripts/scheduler/scheduler.sh`
- Test: `adapters/macos/tests/test_scheduler_script_smoke.py`

- [ ] **Step 1: Add the parser tests**

Append to `adapters/macos/tests/test_scheduler_script_smoke.py`:

```python
def _run_parse_test(expr: str, tmp_path: Path) -> subprocess.CompletedProcess:
    """Source the script and call _parse_expression in a sub-shell.

    The driver exposes _parse_expression as a function; we exercise it
    directly to keep this test focused. Set CAL_HOUR/CAL_MINUTE/etc.
    are echoed so we can assert the parsed values.
    """
    probe = tmp_path / "probe.sh"
    probe.write_text(
        "#!/usr/bin/env bash\n"
        f'export PARSE_EXPR_ONLY=1\n'
        f'. "{SCRIPT}" >/dev/null 2>&1 || true\n'  # source for fn defs
        f'_parse_expression "{expr}"\n'
        f'echo "RC=$?"\n'
        'echo "CAL_HOUR=${CAL_HOUR:-}"\n'
        'echo "CAL_MINUTE=${CAL_MINUTE:-}"\n'
        'echo "CAL_WEEKDAY=${CAL_WEEKDAY:-}"\n'
        'echo "CAL_DAY=${CAL_DAY:-}"\n'
        'echo "CAL_INTERVAL_SEC=${CAL_INTERVAL_SEC:-}"\n'
    )
    return subprocess.run(["bash", str(probe)], capture_output=True, text=True, check=False)


def _parse_probe_output(out: str) -> dict:
    d: dict[str, str] = {}
    for line in out.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            d[k.strip()] = v.strip()
    return d


def test_parse_daily(tmp_path):
    r = _run_parse_test("DAILY 03:00", tmp_path)
    out = _parse_probe_output(r.stdout)
    assert out["RC"] == "0"
    assert out["CAL_HOUR"] == "3"
    assert out["CAL_MINUTE"] == "0"
    assert out["CAL_WEEKDAY"] == ""
    assert out["CAL_DAY"] == ""
    assert out["CAL_INTERVAL_SEC"] == ""


def test_parse_weekly_sunday(tmp_path):
    r = _run_parse_test("WEEKLY SUN 03:00", tmp_path)
    out = _parse_probe_output(r.stdout)
    assert out["RC"] == "0"
    assert out["CAL_HOUR"] == "3"
    assert out["CAL_MINUTE"] == "0"
    assert out["CAL_WEEKDAY"] == "0"


def test_parse_weekly_friday_lowercase(tmp_path):
    r = _run_parse_test("weekly fri 23:30", tmp_path)
    out = _parse_probe_output(r.stdout)
    assert out["RC"] == "0"
    assert out["CAL_HOUR"] == "23"
    assert out["CAL_MINUTE"] == "30"
    assert out["CAL_WEEKDAY"] == "5"


def test_parse_monthly_default_day_one(tmp_path):
    r = _run_parse_test("MONTHLY 02:15", tmp_path)
    out = _parse_probe_output(r.stdout)
    assert out["RC"] == "0"
    assert out["CAL_HOUR"] == "2"
    assert out["CAL_MINUTE"] == "15"
    assert out["CAL_DAY"] == "1"


def test_parse_monthly_specific_day(tmp_path):
    r = _run_parse_test("MONTHLY 15 04:00", tmp_path)
    out = _parse_probe_output(r.stdout)
    assert out["RC"] == "0"
    assert out["CAL_DAY"] == "15"
    assert out["CAL_HOUR"] == "4"


def test_parse_hourly(tmp_path):
    r = _run_parse_test("HOURLY :30", tmp_path)
    out = _parse_probe_output(r.stdout)
    assert out["RC"] == "0"
    assert out["CAL_MINUTE"] == "30"
    assert out["CAL_HOUR"] == ""


def test_parse_minute_interval(tmp_path):
    r = _run_parse_test("MINUTE 5", tmp_path)
    out = _parse_probe_output(r.stdout)
    assert out["RC"] == "0"
    assert out["CAL_INTERVAL_SEC"] == "300"


def test_parse_garbage_rejected(tmp_path):
    r = _run_parse_test("YEARLY 2026 1 1 03:00", tmp_path)
    out = _parse_probe_output(r.stdout)
    assert out["RC"] == "2"
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `python3 -m pytest adapters/macos/tests/test_scheduler_script_smoke.py -k parse -v 2>&1 | head -40`

Expected: 8 failures — `_parse_expression` not defined.

- [ ] **Step 3: Add the `_parse_expression` function**

In `adapters/macos/scripts/scheduler/scheduler.sh`, insert AFTER the `LABEL_PREFIX="dev.ascendo."` line and BEFORE the `UID_VAL` line:

```bash
# Day-of-week table (Sun=0). Bash 3.2: case statement, not associative array.
_weekday_to_int() {
    case "$1" in
        SUN|sun) echo 0 ;;
        MON|mon) echo 1 ;;
        TUE|tue) echo 2 ;;
        WED|wed) echo 3 ;;
        THU|thu) echo 4 ;;
        FRI|fri) echo 5 ;;
        SAT|sat) echo 6 ;;
        *) return 1 ;;
    esac
}

# DSL → globals (CAL_HOUR / CAL_MINUTE / CAL_WEEKDAY / CAL_DAY /
# CAL_INTERVAL_SEC).  Returns 0 on success, 2 on parse failure.
# Globals are unset (empty) when not applicable.
_parse_expression() {
    local _expr="$1"
    CAL_HOUR=""
    CAL_MINUTE=""
    CAL_WEEKDAY=""
    CAL_DAY=""
    CAL_INTERVAL_SEC=""

    # Normalise: collapse runs of spaces, trim.
    local _norm
    _norm="$(printf '%s' "$_expr" | tr -s ' ' | sed -e 's/^ *//' -e 's/ *$//')"

    # Split into tokens via positional params (bash 3.2 friendly).
    set -- $_norm
    local _kw="$1"

    # Uppercase the keyword for matching.
    _kw="$(printf '%s' "$_kw" | tr '[:lower:]' '[:upper:]')"

    case "$_kw" in
        DAILY)
            # DAILY HH:MM
            [ "$#" -eq 2 ] || return 2
            _split_time "$2" || return 2
            ;;
        WEEKLY)
            # WEEKLY DAY HH:MM
            [ "$#" -eq 3 ] || return 2
            CAL_WEEKDAY="$(_weekday_to_int "$2")" || return 2
            _split_time "$3" || return 2
            ;;
        MONTHLY)
            # MONTHLY HH:MM         → day=1
            # MONTHLY DAY HH:MM     → day=DAY (1..31)
            if [ "$#" -eq 2 ]; then
                CAL_DAY=1
                _split_time "$2" || return 2
            elif [ "$#" -eq 3 ]; then
                case "$2" in
                    ''|*[!0-9]*) return 2 ;;
                esac
                if [ "$2" -lt 1 ] || [ "$2" -gt 31 ]; then return 2; fi
                CAL_DAY="$2"
                _split_time "$3" || return 2
            else
                return 2
            fi
            ;;
        HOURLY)
            # HOURLY :MM
            [ "$#" -eq 2 ] || return 2
            case "$2" in
                :*) ;;
                *) return 2 ;;
            esac
            local _mm="${2#:}"
            case "$_mm" in
                ''|*[!0-9]*) return 2 ;;
            esac
            if [ "$_mm" -lt 0 ] || [ "$_mm" -gt 59 ]; then return 2; fi
            CAL_MINUTE="$_mm"
            ;;
        MINUTE)
            # MINUTE N → StartInterval=N*60 (N>=1)
            [ "$#" -eq 2 ] || return 2
            case "$2" in
                ''|*[!0-9]*) return 2 ;;
            esac
            if [ "$2" -lt 1 ]; then return 2; fi
            CAL_INTERVAL_SEC="$(($2 * 60))"
            ;;
        *)
            return 2
            ;;
    esac
    return 0
}

# Helper: split HH:MM into CAL_HOUR + CAL_MINUTE (no leading-zero tolerance
# beyond what bash's arithmetic accepts; "03" is fine, "3" is fine, "00" is 0).
_split_time() {
    local _t="$1"
    case "$_t" in
        *:*) ;;
        *) return 1 ;;
    esac
    local _hh="${_t%%:*}"
    local _mm="${_t##*:}"
    case "$_hh$_mm" in
        ''|*[!0-9]*) return 1 ;;
    esac
    # Strip leading zeros to avoid octal interpretation in arithmetic
    # (bash treats 08, 09 as bad octal). Convert via 10# prefix.
    _hh=$((10#$_hh))
    _mm=$((10#$_mm))
    if [ "$_hh" -lt 0 ] || [ "$_hh" -gt 23 ]; then return 1; fi
    if [ "$_mm" -lt 0 ] || [ "$_mm" -gt 59 ]; then return 1; fi
    CAL_HOUR="$_hh"
    CAL_MINUTE="$_mm"
    return 0
}

# When sourced by tests with PARSE_EXPR_ONLY=1, return now (skip dispatch).
if [ "${PARSE_EXPR_ONLY:-0}" = "1" ]; then
    return 0 2>/dev/null || exit 0
fi
```

- [ ] **Step 4: Run the parser tests to verify all 8 pass**

Run: `python3 -m pytest adapters/macos/tests/test_scheduler_script_smoke.py -k parse -v 2>&1 | head -40`

Expected: 8 passed.

- [ ] **Step 5: Verify the previous skeleton tests still pass**

Run: `python3 -m pytest adapters/macos/tests/test_scheduler_script_smoke.py -v 2>&1 | tail -15`

Expected: 10 passed total.

- [ ] **Step 6: Commit**

```bash
git add adapters/macos/scripts/scheduler/scheduler.sh adapters/macos/tests/test_scheduler_script_smoke.py
git commit -m "feat(macos): scheduler.sh DSL parser (DAILY/WEEKLY/MONTHLY/HOURLY/MINUTE) (M5.5.2)"
```

---

## Task 3: bash driver — `install` action (write plist + bootstrap)

**Files:**
- Modify: `adapters/macos/scripts/scheduler/scheduler.sh`
- Test: `adapters/macos/tests/test_scheduler_script_smoke.py`

- [ ] **Step 1: Write the install round-trip test**

Append to `adapters/macos/tests/test_scheduler_script_smoke.py`:

```python
def test_install_writes_plist_and_sidecar(tmp_path):
    fake_home = tmp_path / "fake_home"
    binary, log = _make_fake_launchctl(tmp_path)
    payload = {
        "name": "weekly-backup",
        "expression": "WEEKLY SUN 03:00",
        "profile": "safe",
        "enabled": True,
        "description": "weekly safe-profile run",
    }
    res, output = _run("install", payload=payload, tmp_path=tmp_path,
                       fake_home=fake_home, launchctl=binary)
    assert res.returncode == 0, res.stderr + res.stdout
    plist = fake_home / "Library/LaunchAgents/dev.ascendo.weekly-backup.plist"
    sidecar = fake_home / "Library/Application Support/Ascendo/schedules/weekly-backup.json"
    assert plist.exists(), "plist not written"
    assert sidecar.exists(), "description sidecar not written"
    body = plist.read_text()
    assert "<string>dev.ascendo.weekly-backup</string>" in body
    assert "<string>--profile</string>" in body
    assert "<string>safe</string>" in body
    assert "<key>Hour</key>" in body and "<integer>3</integer>" in body
    assert "<key>Minute</key>" in body and "<integer>0</integer>" in body
    assert "<key>Weekday</key>" in body and "<integer>0</integer>" in body
    sidecar_data = json.loads(sidecar.read_text())
    assert sidecar_data["description"] == "weekly safe-profile run"
    assert sidecar_data["expression"] == "WEEKLY SUN 03:00"
    assert sidecar_data["profile"] == "safe"
    assert sidecar_data["enabled"] is True
    assert json.loads(output.read_text()) == {"ok": True}
    log_text = log.read_text()
    assert "bootstrap" in log_text  # launchctl bootstrap was invoked


def test_install_disabled_skips_bootstrap(tmp_path):
    fake_home = tmp_path / "fake_home"
    binary, log = _make_fake_launchctl(tmp_path)
    payload = {
        "name": "ad-hoc",
        "expression": "DAILY 04:00",
        "profile": "quick",
        "enabled": False,
        "description": None,
    }
    res, _ = _run("install", payload=payload, tmp_path=tmp_path,
                  fake_home=fake_home, launchctl=binary)
    assert res.returncode == 0
    plist = fake_home / "Library/LaunchAgents/dev.ascendo.ad-hoc.plist"
    assert plist.exists(), "disabled plist still written to disk"
    assert "<key>Disabled</key>" in plist.read_text()
    log_text = log.read_text() if log.exists() else ""
    # bootout still allowed (idempotent), but bootstrap MUST NOT appear.
    assert "bootstrap" not in log_text


def test_install_rejects_bad_name(tmp_path):
    fake_home = tmp_path / "fake_home"
    binary, _ = _make_fake_launchctl(tmp_path)
    payload = {
        "name": "Has Spaces!",
        "expression": "DAILY 03:00",
        "profile": "safe",
        "enabled": True,
        "description": None,
    }
    res, output = _run("install", payload=payload, tmp_path=tmp_path,
                       fake_home=fake_home, launchctl=binary)
    assert res.returncode == 2
    err = json.loads(output.read_text())
    assert "error" in err
    assert "name" in err["error"].lower()


def test_install_rejects_bad_expression(tmp_path):
    fake_home = tmp_path / "fake_home"
    binary, _ = _make_fake_launchctl(tmp_path)
    payload = {
        "name": "broken",
        "expression": "YEARLY 2026 03:00",
        "profile": "safe",
        "enabled": True,
        "description": None,
    }
    res, output = _run("install", payload=payload, tmp_path=tmp_path,
                       fake_home=fake_home, launchctl=binary)
    assert res.returncode == 2
    err = json.loads(output.read_text())
    assert "error" in err
    assert "expression" in err["error"].lower() or "unsupported" in err["error"].lower()
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `python3 -m pytest adapters/macos/tests/test_scheduler_script_smoke.py -k install -v 2>&1 | head -20`

Expected: 4 failures (install action not implemented; the skeleton emits `{"ok": true}` with no plist).

- [ ] **Step 3: Replace the placeholder dispatch with the install action**

In `scheduler.sh`, replace the placeholder block:

```bash
# Action handlers land in subsequent tasks.
emit_json '{"ok": true}'
exit 0
```

with:

```bash
# Read payload (JSON object, may be empty for list actions).
_read_payload() {
    if [ -z "$PAYLOAD_PATH" ]; then echo ""; return 0; fi
    if [ ! -f "$PAYLOAD_PATH" ]; then echo ""; return 0; fi
    cat "$PAYLOAD_PATH"
}

PAYLOAD="$(_read_payload)"

# Extract a string field from PAYLOAD via python3 (jq not guaranteed on
# every Mac; python3 is shipped on macOS 12.3+ and required by all
# Ascendo bash drivers).
_payload_get() {
    local _field="$1"
    if [ -z "$PAYLOAD" ]; then echo ""; return 0; fi
    printf '%s' "$PAYLOAD" | python3 -c "
import json, sys
d = json.loads(sys.stdin.read() or '{}')
v = d.get('$_field', '')
if v is None: v = ''
print(v)
"
}

_payload_get_bool() {
    local _field="$1"
    local _default="$2"
    if [ -z "$PAYLOAD" ]; then echo "$_default"; return 0; fi
    printf '%s' "$PAYLOAD" | python3 -c "
import json, sys
d = json.loads(sys.stdin.read() or '{}')
v = d.get('$_field')
if v is True:  print('true')
elif v is False: print('false')
else: print('$_default')
"
}

# Validate name (matches the Pydantic ScheduleSpec.name regex).
_validate_name() {
    case "$1" in
        ''|*[!a-z0-9-]*)
            return 1
            ;;
    esac
    return 0
}

case "$ACTION" in

    install)
        NAME="$(_payload_get name)"
        if ! _validate_name "$NAME"; then
            emit_error "invalid name: must match ^[a-z0-9-]+\$"
            exit 2
        fi
        EXPR="$(_payload_get expression)"
        if ! _parse_expression "$EXPR"; then
            emit_error "unsupported expression: $EXPR"
            exit 2
        fi
        PROFILE="$(_payload_get profile)"
        if [ -z "$PROFILE" ]; then PROFILE="full"; fi
        ENABLED="$(_payload_get_bool enabled true)"
        DESCRIPTION="$(_payload_get description)"

        mkdir -p "$LAUNCH_AGENTS_DIR" "$LOGS_DIR" "$SCHEDULES_DIR"

        PLIST="$LAUNCH_AGENTS_DIR/${LABEL_PREFIX}${NAME}.plist"
        SIDECAR="$SCHEDULES_DIR/${NAME}.json"
        LABEL="${LABEL_PREFIX}${NAME}"
        LOG_FILE="$LOGS_DIR/scheduler-${NAME}.log"

        # Build StartCalendarInterval / StartInterval block.
        if [ -n "$CAL_INTERVAL_SEC" ]; then
            INTERVAL_BLOCK="    <key>StartInterval</key>
    <integer>$CAL_INTERVAL_SEC</integer>"
        else
            INTERVAL_BLOCK="    <key>StartCalendarInterval</key>
    <dict>"
            [ -n "$CAL_HOUR" ]    && INTERVAL_BLOCK="$INTERVAL_BLOCK
        <key>Hour</key>
        <integer>$CAL_HOUR</integer>"
            [ -n "$CAL_MINUTE" ]  && INTERVAL_BLOCK="$INTERVAL_BLOCK
        <key>Minute</key>
        <integer>$CAL_MINUTE</integer>"
            [ -n "$CAL_WEEKDAY" ] && INTERVAL_BLOCK="$INTERVAL_BLOCK
        <key>Weekday</key>
        <integer>$CAL_WEEKDAY</integer>"
            [ -n "$CAL_DAY" ]     && INTERVAL_BLOCK="$INTERVAL_BLOCK
        <key>Day</key>
        <integer>$CAL_DAY</integer>"
            INTERVAL_BLOCK="$INTERVAL_BLOCK
    </dict>"
        fi

        # Disabled key only when enabled=false.
        if [ "$ENABLED" = "false" ]; then
            DISABLED_BLOCK="    <key>Disabled</key>
    <true/>"
        else
            DISABLED_BLOCK=""
        fi

        cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/env</string>
        <string>ascendo</string>
        <string>run</string>
        <string>--profile</string>
        <string>${PROFILE}</string>
    </array>
${INTERVAL_BLOCK}
    <key>RunAtLoad</key>
    <false/>
    <key>StandardOutPath</key>
    <string>${LOG_FILE}</string>
    <key>StandardErrorPath</key>
    <string>${LOG_FILE}</string>
${DISABLED_BLOCK}
</dict>
</plist>
PLIST_EOF

        # Sidecar: stores description + expression + profile + enabled
        # + installed_at (launchd plists have no free-form notes channel).
        python3 - <<PY_EOF
import json, datetime, pathlib
p = pathlib.Path("$SIDECAR")
p.parent.mkdir(parents=True, exist_ok=True)
p.write_text(json.dumps({
    "name": "$NAME",
    "expression": "$EXPR",
    "profile": "$PROFILE",
    "enabled": $([ "$ENABLED" = "true" ] && echo true || echo false),
    "description": $([ -n "$DESCRIPTION" ] && python3 -c 'import json,sys;print(json.dumps(sys.argv[1]))' "$DESCRIPTION" || echo "null"),
    "installed_at": datetime.datetime.utcnow().isoformat() + "Z",
}, indent=2))
PY_EOF

        # bootout any prior load (silent on "no such service") then bootstrap.
        launchctl bootout "gui/${UID_VAL}/${LABEL}" >/dev/null 2>&1 || true
        if [ "$ENABLED" = "true" ]; then
            launchctl bootstrap "gui/${UID_VAL}" "$PLIST" >/dev/null 2>&1 || true
        fi
        emit_json '{"ok": true}'
        exit 0
        ;;

    *)
        # uninstall, list, get, trigger land in subsequent tasks.
        emit_json '{"ok": true}'
        exit 0
        ;;
esac
```

- [ ] **Step 4: Run install tests to verify they pass**

Run: `python3 -m pytest adapters/macos/tests/test_scheduler_script_smoke.py -k install -v 2>&1 | head -30`

Expected: 4 passed.

- [ ] **Step 5: Verify all earlier tests still pass**

Run: `python3 -m pytest adapters/macos/tests/test_scheduler_script_smoke.py -v 2>&1 | tail -15`

Expected: 14 passed total (2 + 8 + 4).

- [ ] **Step 6: Commit**

```bash
git add adapters/macos/scripts/scheduler/scheduler.sh adapters/macos/tests/test_scheduler_script_smoke.py
git commit -m "feat(macos): scheduler.sh install action — plist + sidecar + bootstrap (M5.5.3)"
```

---

## Task 4: bash driver — `uninstall` action

**Files:**
- Modify: `adapters/macos/scripts/scheduler/scheduler.sh`
- Test: `adapters/macos/tests/test_scheduler_script_smoke.py`

- [ ] **Step 1: Write the uninstall tests**

Append to the test file:

```python
def test_uninstall_removes_plist_and_sidecar(tmp_path):
    fake_home = tmp_path / "fake_home"
    binary, _ = _make_fake_launchctl(tmp_path)

    # Install first.
    install_payload = {
        "name": "to-remove",
        "expression": "DAILY 05:00",
        "profile": "safe",
        "enabled": True,
        "description": None,
    }
    _run("install", payload=install_payload, tmp_path=tmp_path,
         fake_home=fake_home, launchctl=binary)
    plist = fake_home / "Library/LaunchAgents/dev.ascendo.to-remove.plist"
    sidecar = fake_home / "Library/Application Support/Ascendo/schedules/to-remove.json"
    assert plist.exists() and sidecar.exists()

    # Now uninstall.
    res, output = _run("uninstall", payload={"name": "to-remove"},
                       tmp_path=tmp_path, fake_home=fake_home,
                       launchctl=binary)
    assert res.returncode == 0, res.stderr
    assert not plist.exists(), "plist still on disk after uninstall"
    assert not sidecar.exists(), "sidecar still on disk after uninstall"
    assert json.loads(output.read_text()) == {"ok": True}


def test_uninstall_idempotent_on_missing(tmp_path):
    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()
    binary, _ = _make_fake_launchctl(tmp_path)
    res, output = _run("uninstall", payload={"name": "never-existed"},
                       tmp_path=tmp_path, fake_home=fake_home,
                       launchctl=binary)
    assert res.returncode == 0
    assert json.loads(output.read_text()) == {"ok": True}
```

- [ ] **Step 2: Run the uninstall tests to verify they fail**

Run: `python3 -m pytest adapters/macos/tests/test_scheduler_script_smoke.py -k uninstall -v 2>&1 | head -15`

Expected: 2 failures (placeholder still emits OK but doesn't remove the plist; the first test asserts `not plist.exists()` which fails).

- [ ] **Step 3: Add the uninstall action**

In `scheduler.sh`, replace the trailing `*)` placeholder:

```bash
    *)
        # uninstall, list, get, trigger land in subsequent tasks.
        emit_json '{"ok": true}'
        exit 0
        ;;
```

with the explicit `uninstall` branch (and a new `*)` placeholder for the still-pending actions):

```bash
    uninstall)
        NAME="$(_payload_get name)"
        if ! _validate_name "$NAME"; then
            emit_error "invalid name: must match ^[a-z0-9-]+\$"
            exit 2
        fi
        PLIST="$LAUNCH_AGENTS_DIR/${LABEL_PREFIX}${NAME}.plist"
        SIDECAR="$SCHEDULES_DIR/${NAME}.json"
        LABEL="${LABEL_PREFIX}${NAME}"

        # Idempotent: silent on "no such service" and "no such plist".
        launchctl bootout "gui/${UID_VAL}/${LABEL}" >/dev/null 2>&1 || true
        rm -f "$PLIST" "$SIDECAR"
        emit_json '{"ok": true}'
        exit 0
        ;;

    *)
        # list, get, trigger land in subsequent tasks.
        emit_json '{"ok": true}'
        exit 0
        ;;
```

- [ ] **Step 4: Run all scheduler-script tests**

Run: `python3 -m pytest adapters/macos/tests/test_scheduler_script_smoke.py -v 2>&1 | tail -20`

Expected: 16 passed total (14 + 2).

- [ ] **Step 5: Commit**

```bash
git add adapters/macos/scripts/scheduler/scheduler.sh adapters/macos/tests/test_scheduler_script_smoke.py
git commit -m "feat(macos): scheduler.sh uninstall action — bootout + rm plist + sidecar (M5.5.4)"
```

---

## Task 5: bash driver — `list`, `get`, `trigger` actions

**Files:**
- Modify: `adapters/macos/scripts/scheduler/scheduler.sh`
- Test: `adapters/macos/tests/test_scheduler_script_smoke.py`

- [ ] **Step 1: Write the list / get / trigger tests**

Append to the test file:

```python
def test_list_empty_dir_returns_empty_array(tmp_path):
    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()
    binary, _ = _make_fake_launchctl(tmp_path)
    res, output = _run("list", payload=None, tmp_path=tmp_path,
                       fake_home=fake_home, launchctl=binary)
    assert res.returncode == 0
    assert json.loads(output.read_text()) == []


def test_list_after_two_installs_enumerates_both(tmp_path):
    fake_home = tmp_path / "fake_home"
    binary, _ = _make_fake_launchctl(tmp_path)
    for name, expr, prof in [
        ("daily-quick", "DAILY 02:00", "quick"),
        ("weekly-safe", "WEEKLY SAT 04:00", "safe"),
    ]:
        _run("install",
             payload={"name": name, "expression": expr, "profile": prof,
                      "enabled": True, "description": None},
             tmp_path=tmp_path, fake_home=fake_home, launchctl=binary)
    res, output = _run("list", payload=None, tmp_path=tmp_path,
                       fake_home=fake_home, launchctl=binary)
    assert res.returncode == 0
    arr = json.loads(output.read_text())
    assert isinstance(arr, list)
    assert len(arr) == 2
    names = {e["name"] for e in arr}
    assert names == {"daily-quick", "weekly-safe"}
    by_name = {e["name"]: e for e in arr}
    assert by_name["daily-quick"]["profile"] == "quick"
    assert by_name["daily-quick"]["expression"] == "DAILY 02:00"
    assert by_name["weekly-safe"]["profile"] == "safe"


def test_get_filters_by_name(tmp_path):
    fake_home = tmp_path / "fake_home"
    binary, _ = _make_fake_launchctl(tmp_path)
    _run("install",
         payload={"name": "single", "expression": "DAILY 01:00", "profile": "safe",
                  "enabled": True, "description": "single entry"},
         tmp_path=tmp_path, fake_home=fake_home, launchctl=binary)
    res, output = _run("get", payload={"name": "single"}, tmp_path=tmp_path,
                       fake_home=fake_home, launchctl=binary)
    assert res.returncode == 0
    obj = json.loads(output.read_text())
    assert obj["name"] == "single"
    assert obj["description"] == "single entry"


def test_get_unknown_returns_null(tmp_path):
    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()
    binary, _ = _make_fake_launchctl(tmp_path)
    res, output = _run("get", payload={"name": "missing"}, tmp_path=tmp_path,
                       fake_home=fake_home, launchctl=binary)
    assert res.returncode == 0
    assert json.loads(output.read_text()) is None


def test_trigger_invokes_kickstart(tmp_path):
    fake_home = tmp_path / "fake_home"
    binary, log = _make_fake_launchctl(tmp_path)
    _run("install",
         payload={"name": "kicked", "expression": "DAILY 09:00", "profile": "safe",
                  "enabled": True, "description": None},
         tmp_path=tmp_path, fake_home=fake_home, launchctl=binary)
    res, output = _run("trigger", payload={"name": "kicked"}, tmp_path=tmp_path,
                       fake_home=fake_home, launchctl=binary)
    assert res.returncode == 0, res.stderr
    assert json.loads(output.read_text()) == {"ok": True}
    assert "kickstart" in log.read_text()


def test_trigger_missing_returns_error_30(tmp_path):
    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()
    binary, _ = _make_fake_launchctl(tmp_path)
    res, output = _run("trigger", payload={"name": "ghost"}, tmp_path=tmp_path,
                       fake_home=fake_home, launchctl=binary)
    assert res.returncode == 30
    err = json.loads(output.read_text())
    assert "error" in err
    assert "ghost" in err["error"] or "no such" in err["error"].lower()
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `python3 -m pytest adapters/macos/tests/test_scheduler_script_smoke.py -k 'list or get or trigger' -v 2>&1 | head -25`

Expected: 6 failures.

- [ ] **Step 3: Add the list / get / trigger actions**

In `scheduler.sh`, replace the trailing `*)` placeholder:

```bash
    *)
        # list, get, trigger land in subsequent tasks.
        emit_json '{"ok": true}'
        exit 0
        ;;
```

with explicit branches:

```bash
    list)
        # Enumerate ~/Library/LaunchAgents/dev.ascendo.*.plist; emit JSON
        # array of {name, expression, profile, enabled, description}.
        # Source of truth for the sidecar fields is the schedules JSON.
        # Plist-only fallback (when sidecar missing) reconstructs
        # expression best-effort.
        if [ ! -d "$LAUNCH_AGENTS_DIR" ]; then
            emit_json '[]'
            exit 0
        fi
        # python3 owns enumeration + JSON shape (bash 3.2 has no real
        # array library; rather than pin manually, hand the work off).
        export LAUNCH_AGENTS_DIR LABEL_PREFIX SCHEDULES_DIR
        RESULT="$(python3 - <<'PY_EOF'
import json, os, plistlib, pathlib, re
agents_dir = pathlib.Path(os.environ["LAUNCH_AGENTS_DIR"])
prefix = os.environ["LABEL_PREFIX"]
schedules_dir = pathlib.Path(os.environ["SCHEDULES_DIR"])

out = []
if agents_dir.is_dir():
    for plist_path in sorted(agents_dir.glob(f"{prefix}*.plist")):
        name = plist_path.stem[len(prefix):]
        sidecar_path = schedules_dir / f"{name}.json"
        if sidecar_path.is_file():
            try:
                meta = json.loads(sidecar_path.read_text())
            except (OSError, json.JSONDecodeError):
                meta = {}
        else:
            meta = {}
        # Best-effort plist parse for fallback fields.
        try:
            with plist_path.open("rb") as f:
                pl = plistlib.load(f)
        except Exception:
            pl = {}
        # Reconstruct expression if sidecar is missing.
        expression = meta.get("expression")
        if not expression:
            sci = pl.get("StartCalendarInterval", {})
            si  = pl.get("StartInterval")
            if isinstance(si, int) and si > 0:
                expression = f"MINUTE {si // 60}"
            elif isinstance(sci, dict):
                hh = sci.get("Hour")
                mm = sci.get("Minute")
                wd = sci.get("Weekday")
                day = sci.get("Day")
                if wd is not None and hh is not None and mm is not None:
                    weekdays = ["SUN","MON","TUE","WED","THU","FRI","SAT"]
                    expression = f"WEEKLY {weekdays[wd % 7]} {hh:02d}:{mm:02d}"
                elif day is not None and hh is not None and mm is not None:
                    expression = f"MONTHLY {day} {hh:02d}:{mm:02d}"
                elif hh is not None and mm is not None:
                    expression = f"DAILY {hh:02d}:{mm:02d}"
                elif mm is not None:
                    expression = f"HOURLY :{mm:02d}"
                else:
                    expression = ""
            else:
                expression = ""
        # Reconstruct profile from ProgramArguments if sidecar missing.
        profile = meta.get("profile")
        if not profile:
            args = pl.get("ProgramArguments", [])
            if isinstance(args, list) and "--profile" in args:
                idx = args.index("--profile")
                if idx + 1 < len(args):
                    profile = args[idx + 1]
        if not profile:
            profile = "full"
        # enabled: missing-key sidecar → True (default); plist Disabled key wins on tie.
        if "enabled" in meta:
            enabled = bool(meta["enabled"])
        else:
            enabled = not bool(pl.get("Disabled", False))
        out.append({
            "name": name,
            "expression": expression,
            "profile": profile,
            "enabled": enabled,
            "description": meta.get("description"),
        })
print(json.dumps(out))
PY_EOF
)"
        emit_json "$RESULT"
        exit 0
        ;;

    get)
        NAME="$(_payload_get name)"
        if ! _validate_name "$NAME"; then
            emit_error "invalid name: must match ^[a-z0-9-]+\$"
            exit 2
        fi
        PLIST="$LAUNCH_AGENTS_DIR/${LABEL_PREFIX}${NAME}.plist"
        if [ ! -f "$PLIST" ]; then
            emit_json 'null'
            exit 0
        fi
        # Reuse list emitter — single entry filter via python3.
        export LAUNCH_AGENTS_DIR LABEL_PREFIX SCHEDULES_DIR ASCENDO_GET_NAME="$NAME"
        RESULT="$(python3 - <<'PY_EOF'
import json, os, plistlib, pathlib
name = os.environ["ASCENDO_GET_NAME"]
prefix = os.environ["LABEL_PREFIX"]
agents_dir = pathlib.Path(os.environ["LAUNCH_AGENTS_DIR"])
schedules_dir = pathlib.Path(os.environ["SCHEDULES_DIR"])
plist_path = agents_dir / f"{prefix}{name}.plist"
if not plist_path.is_file():
    print("null")
else:
    sidecar_path = schedules_dir / f"{name}.json"
    if sidecar_path.is_file():
        meta = json.loads(sidecar_path.read_text())
    else:
        meta = {}
    print(json.dumps({
        "name": name,
        "expression": meta.get("expression", ""),
        "profile": meta.get("profile", "full"),
        "enabled": bool(meta.get("enabled", True)),
        "description": meta.get("description"),
    }))
PY_EOF
)"
        emit_json "$RESULT"
        exit 0
        ;;

    trigger)
        NAME="$(_payload_get name)"
        if ! _validate_name "$NAME"; then
            emit_error "invalid name: must match ^[a-z0-9-]+\$"
            exit 2
        fi
        PLIST="$LAUNCH_AGENTS_DIR/${LABEL_PREFIX}${NAME}.plist"
        LABEL="${LABEL_PREFIX}${NAME}"
        if [ ! -f "$PLIST" ]; then
            emit_error "no such schedule: $NAME"
            exit 30
        fi
        # Idempotent bootstrap (silent on "already loaded").
        launchctl bootstrap "gui/${UID_VAL}" "$PLIST" >/dev/null 2>&1 || true
        if ! launchctl kickstart "gui/${UID_VAL}/${LABEL}" >/dev/null 2>&1; then
            emit_error "kickstart failed for $LABEL"
            exit 30
        fi
        emit_json '{"ok": true}'
        exit 0
        ;;

    *)
        printf 'scheduler.sh: unknown action: %s\n' "$ACTION" >&2
        exit 2
        ;;
```

- [ ] **Step 4: Run all scheduler-script tests**

Run: `python3 -m pytest adapters/macos/tests/test_scheduler_script_smoke.py -v 2>&1 | tail -25`

Expected: 22 passed total (16 + 6).

- [ ] **Step 5: Commit**

```bash
git add adapters/macos/scripts/scheduler/scheduler.sh adapters/macos/tests/test_scheduler_script_smoke.py
git commit -m "feat(macos): scheduler.sh list+get+trigger actions (M5.5.5)"
```

---

## Task 6: Python `LaunchdScheduler` — class skeleton + `is_available`

**Files:**
- Create: `adapters/macos/ascendo_macos/managers/scheduler.py`
- Create: `adapters/macos/tests/test_launchd_scheduler_smoke.py`

- [ ] **Step 1: Create the test file**

Create `adapters/macos/tests/test_launchd_scheduler_smoke.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest adapters/macos/tests/test_launchd_scheduler_smoke.py -v 2>&1 | head -10`

Expected: 5 errors (`No module named 'ascendo_macos.managers.scheduler'`).

- [ ] **Step 3: Create the class skeleton**

Create `adapters/macos/ascendo_macos/managers/scheduler.py`:

```python
"""LaunchdScheduler — IScheduler via macOS launchd LaunchAgents.

Drives a single bash script `scheduler.sh` over JSON-IPC. Mirrors the
M3.13 WindowsScheduler shape exactly:
  - install / uninstall / list / get / trigger map to ``--action <verb>``.
  - Schedule expression (DSL: ``DAILY/WEEKLY/MONTHLY/HOURLY/MINUTE``) is
    parsed by the bash driver and translated to a ``StartCalendarInterval``
    plist dict.
  - Per-user agents only — written to ``~/Library/LaunchAgents/dev.ascendo.<name>.plist``.

Description metadata that doesn't fit in a launchd plist (free-form
description string) is stored in a sidecar JSON at
``~/Library/Application Support/Ascendo/schedules/<name>.json``.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import ClassVar

from ascendo.interfaces.scheduler import IScheduler, ScheduleSpec, SchedulerError
from ascendo.models.host import HostInfo, OperatingSystem

_log = logging.getLogger(__name__)


class LaunchdScheduler(IScheduler):
    """launchd LaunchAgent-backed IScheduler for macOS."""

    BACKEND: ClassVar[str] = "launchd"
    DEFAULT_TIMEOUT_SEC: ClassVar[int] = 30

    def __init__(
        self,
        *,
        scripts_dir: Path,
        lib_dir: Path,
        bash_path: str | None = None,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        self._scripts_dir = Path(scripts_dir)
        self._lib_dir = Path(lib_dir)
        self._bash_override = bash_path
        self._bash_resolved: str | None = None
        self._timeout_sec = timeout_sec

    @property
    def backend(self) -> str:
        return self.BACKEND

    def is_available(self, host: HostInfo) -> bool:
        if host.os is not OperatingSystem.MACOS:
            return False
        return shutil.which("launchctl") is not None

    def install(self, host: HostInfo, spec: ScheduleSpec) -> None:
        raise NotImplementedError("M5.5.7")

    def uninstall(self, host: HostInfo, name: str) -> None:
        raise NotImplementedError("M5.5.7")

    def list(self, host: HostInfo) -> list[ScheduleSpec]:  # noqa: A003
        raise NotImplementedError("M5.5.7")

    def get(self, host: HostInfo, name: str) -> ScheduleSpec | None:
        raise NotImplementedError("M5.5.7")

    def trigger(self, host: HostInfo, name: str) -> None:
        raise NotImplementedError("M5.5.7")
```

- [ ] **Step 4: Run the smoke tests**

Run: `python3 -m pytest adapters/macos/tests/test_launchd_scheduler_smoke.py -v 2>&1 | tail -15`

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add adapters/macos/ascendo_macos/managers/scheduler.py adapters/macos/tests/test_launchd_scheduler_smoke.py
git commit -m "feat(macos): LaunchdScheduler class skeleton + is_available (M5.5.6)"
```

---

## Task 7: Python `LaunchdScheduler` — JSON-IPC `_invoke` + 5 method bodies

**Files:**
- Modify: `adapters/macos/ascendo_macos/managers/scheduler.py`
- Modify: `adapters/macos/tests/test_launchd_scheduler_smoke.py`

- [ ] **Step 1: Add the JSON-IPC tests**

Append to `adapters/macos/tests/test_launchd_scheduler_smoke.py`:

```python
def _argv_recorder():
    """Patch context that records every subprocess.run argv + payload."""
    calls = []

    def fake_run(argv, **kwargs):  # noqa: ANN001
        # Record argv and write a canned --output-path file.
        calls.append({"argv": list(argv), "kwargs": dict(kwargs)})
        # Find --output-path arg and write something parseable to it.
        if "--output-path" in argv:
            i = argv.index("--output-path")
            output = Path(argv[i + 1])
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(fake_run.canned_output)
        return subprocess.CompletedProcess(args=argv, returncode=fake_run.returncode,
                                            stdout="", stderr="")

    fake_run.calls = calls
    fake_run.canned_output = '{"ok": true}'
    fake_run.returncode = 0
    return fake_run


def test_install_writes_payload_and_invokes_bash(mac_host):
    s = _make_scheduler()
    spec = ScheduleSpec(
        name="weekly-safe",
        expression="WEEKLY SUN 03:00",
        profile="safe",
        enabled=True,
        description="weekly safe run",
    )
    fr = _argv_recorder()
    with patch("ascendo_macos.managers.scheduler.subprocess.run", side_effect=fr), \
         patch("ascendo_macos.managers.scheduler.shutil.which", return_value="/bin/bash"):
        s.install(mac_host, spec)
    assert len(fr.calls) == 1
    argv = fr.calls[0]["argv"]
    assert argv[0] == "/bin/bash"
    assert "--action" in argv and argv[argv.index("--action") + 1] == "install"
    assert "--output-path" in argv
    assert "--payload-path" in argv
    payload_path = argv[argv.index("--payload-path") + 1]
    payload = json.loads(Path(payload_path).read_text())
    assert payload == {
        "name": "weekly-safe",
        "expression": "WEEKLY SUN 03:00",
        "profile": "safe",
        "enabled": True,
        "description": "weekly safe run",
    }


def test_uninstall_invokes_bash_with_name():
    s = _make_scheduler()
    fr = _argv_recorder()
    host = HostInfo(
        hostname="m", os=OperatingSystem.MACOS, os_version="14.5",
        arch="arm64", user="mk", is_elevated=False,
        elevation_method=ElevationMethod.NONE,
    )
    with patch("ascendo_macos.managers.scheduler.subprocess.run", side_effect=fr), \
         patch("ascendo_macos.managers.scheduler.shutil.which", return_value="/bin/bash"):
        s.uninstall(host, "old-entry")
    argv = fr.calls[0]["argv"]
    assert argv[argv.index("--action") + 1] == "uninstall"
    payload_path = argv[argv.index("--payload-path") + 1]
    assert json.loads(Path(payload_path).read_text()) == {"name": "old-entry"}


def test_list_parses_array_into_spec_list(mac_host):
    s = _make_scheduler()
    fr = _argv_recorder()
    fr.canned_output = json.dumps([
        {"name": "daily-quick", "expression": "DAILY 02:00", "profile": "quick",
         "enabled": True, "description": None},
        {"name": "weekly-safe", "expression": "WEEKLY SAT 04:00", "profile": "safe",
         "enabled": True, "description": "weekend run"},
    ])
    with patch("ascendo_macos.managers.scheduler.subprocess.run", side_effect=fr), \
         patch("ascendo_macos.managers.scheduler.shutil.which", return_value="/bin/bash"):
        result = s.list(mac_host)
    assert len(result) == 2
    assert all(isinstance(s, ScheduleSpec) for s in result)
    names = {sp.name for sp in result}
    assert names == {"daily-quick", "weekly-safe"}


def test_list_skips_malformed_entries(mac_host):
    s = _make_scheduler()
    fr = _argv_recorder()
    fr.canned_output = json.dumps([
        {"name": "ok-one", "expression": "DAILY 02:00", "profile": "safe",
         "enabled": True, "description": None},
        {"this": "is not a spec"},
        {"name": "ok-two", "expression": "DAILY 03:00", "profile": "safe",
         "enabled": True, "description": None},
    ])
    with patch("ascendo_macos.managers.scheduler.subprocess.run", side_effect=fr), \
         patch("ascendo_macos.managers.scheduler.shutil.which", return_value="/bin/bash"):
        result = s.list(mac_host)
    assert len(result) == 2
    assert {s.name for s in result} == {"ok-one", "ok-two"}


def test_get_returns_none_on_null(mac_host):
    s = _make_scheduler()
    fr = _argv_recorder()
    fr.canned_output = "null"
    with patch("ascendo_macos.managers.scheduler.subprocess.run", side_effect=fr), \
         patch("ascendo_macos.managers.scheduler.shutil.which", return_value="/bin/bash"):
        result = s.get(mac_host, "missing")
    assert result is None


def test_get_returns_spec_when_found(mac_host):
    s = _make_scheduler()
    fr = _argv_recorder()
    fr.canned_output = json.dumps({
        "name": "single", "expression": "DAILY 01:00", "profile": "safe",
        "enabled": True, "description": "the only one",
    })
    with patch("ascendo_macos.managers.scheduler.subprocess.run", side_effect=fr), \
         patch("ascendo_macos.managers.scheduler.shutil.which", return_value="/bin/bash"):
        result = s.get(mac_host, "single")
    assert result is not None
    assert result.name == "single"
    assert result.description == "the only one"


def test_trigger_invokes_bash_with_name(mac_host):
    s = _make_scheduler()
    fr = _argv_recorder()
    with patch("ascendo_macos.managers.scheduler.subprocess.run", side_effect=fr), \
         patch("ascendo_macos.managers.scheduler.shutil.which", return_value="/bin/bash"):
        s.trigger(mac_host, "kicked")
    argv = fr.calls[0]["argv"]
    assert argv[argv.index("--action") + 1] == "trigger"


def test_invoke_raises_scheduler_error_on_non_zero_and_no_output(mac_host):
    s = _make_scheduler()
    fr = _argv_recorder()
    fr.returncode = 30
    fr.canned_output = ""  # script wrote nothing
    with patch("ascendo_macos.managers.scheduler.subprocess.run") as run_mock, \
         patch("ascendo_macos.managers.scheduler.shutil.which", return_value="/bin/bash"):
        run_mock.return_value = subprocess.CompletedProcess(
            args=[], returncode=30, stdout="", stderr="boom",
        )
        with pytest.raises(SchedulerError):
            s.uninstall(mac_host, "anything")


def test_invoke_raises_scheduler_error_on_invalid_json(mac_host):
    s = _make_scheduler()
    def fake_run(argv, **kwargs):
        if "--output-path" in argv:
            output = Path(argv[argv.index("--output-path") + 1])
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text("not json at all{")
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")
    with patch("ascendo_macos.managers.scheduler.subprocess.run", side_effect=fake_run), \
         patch("ascendo_macos.managers.scheduler.shutil.which", return_value="/bin/bash"):
        with pytest.raises(SchedulerError):
            s.list(mac_host)
```

- [ ] **Step 2: Run new tests to verify they fail**

Run: `python3 -m pytest adapters/macos/tests/test_launchd_scheduler_smoke.py -v 2>&1 | tail -15`

Expected: 9 failures (`NotImplementedError`).

- [ ] **Step 3: Implement `_invoke` + the 5 method bodies**

In `adapters/macos/ascendo_macos/managers/scheduler.py`, replace the 5 `NotImplementedError` stubs with:

```python
    def install(self, host: HostInfo, spec: ScheduleSpec) -> None:
        body = {
            "name":        spec.name,
            "expression":  spec.expression,
            "profile":     spec.profile,
            "enabled":     spec.enabled,
            "description": spec.description,
        }
        self._invoke("install", payload=body)

    def uninstall(self, host: HostInfo, name: str) -> None:
        self._invoke("uninstall", payload={"name": name})

    def list(self, host: HostInfo) -> list[ScheduleSpec]:  # noqa: A003
        result = self._invoke("list")
        if not isinstance(result, list):
            return []
        out: list[ScheduleSpec] = []
        for item in result:
            try:
                out.append(self._parse_spec(item))
            except (TypeError, ValueError):
                continue
        return out

    def get(self, host: HostInfo, name: str) -> ScheduleSpec | None:
        result = self._invoke("get", payload={"name": name})
        if result is None:
            return None
        if not isinstance(result, dict):
            return None
        try:
            return self._parse_spec(result)
        except (TypeError, ValueError):
            return None

    def trigger(self, host: HostInfo, name: str) -> None:
        self._invoke("trigger", payload={"name": name})

    # ── Internals ────────────────────────────────────────────────────────

    def _invoke(self, action: str, *, payload: dict | None = None):
        script = self._scripts_dir / "scheduler" / "scheduler.sh"
        bash = self._resolve_bash()
        with tempfile.TemporaryDirectory(prefix="ascendo-sched-") as tmp:
            output = Path(tmp) / "result.json"
            argv: list[str] = [
                bash, str(script),
                "--action", action,
                "--output-path", str(output),
            ]
            if payload is not None:
                payload_path = Path(tmp) / "payload.json"
                payload_path.write_text(json.dumps(payload), encoding="utf-8")
                argv += ["--payload-path", str(payload_path)]
            try:
                completed = subprocess.run(  # noqa: S603
                    argv, capture_output=True, text=True,
                    timeout=self._timeout_sec, check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise SchedulerError(f"scheduler {action} timed out") from exc
            except OSError as exc:
                raise SchedulerError(
                    f"failed to spawn bash for scheduler {action}: {exc}"
                ) from exc

            if completed.returncode != 0 and not output.exists():
                raise SchedulerError(
                    f"scheduler {action} failed: exit={completed.returncode} "
                    f"stderr={completed.stderr[:300]!r}"
                )
            if not output.exists():
                # install / uninstall / trigger may not produce output.
                return None
            try:
                parsed = json.loads(output.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise SchedulerError(
                    f"scheduler {action} emitted invalid JSON: {exc}"
                ) from exc
            # Treat {"error": "..."} responses + non-zero exit as failure.
            if (
                isinstance(parsed, dict)
                and "error" in parsed
                and completed.returncode != 0
            ):
                raise SchedulerError(
                    f"scheduler {action} failed: {parsed['error']}"
                )
            return parsed

    def _parse_spec(self, item: dict) -> ScheduleSpec:
        return ScheduleSpec(
            name=str(item["name"]),
            expression=str(item.get("expression", "")),
            profile=str(item.get("profile", "full")),
            enabled=bool(item.get("enabled", True)),
            description=item.get("description") or None,
        )

    def _resolve_bash(self) -> str:
        if self._bash_resolved is not None:
            return self._bash_resolved
        if self._bash_override is not None:
            self._bash_resolved = self._bash_override
            return self._bash_resolved
        for candidate in ("/bin/bash", "bash"):
            found = shutil.which(candidate) if candidate == "bash" else (candidate if Path(candidate).is_file() else None)
            if found is not None:
                self._bash_resolved = found
                return found
        raise SchedulerError("no bash binary found on PATH or at /bin/bash")
```

- [ ] **Step 4: Run all LaunchdScheduler tests**

Run: `python3 -m pytest adapters/macos/tests/test_launchd_scheduler_smoke.py -v 2>&1 | tail -20`

Expected: 14 passed (5 + 9).

- [ ] **Step 5: Commit**

```bash
git add adapters/macos/ascendo_macos/managers/scheduler.py adapters/macos/tests/test_launchd_scheduler_smoke.py
git commit -m "feat(macos): LaunchdScheduler JSON-IPC + 5 IScheduler methods (M5.5.7)"
```

---

## Task 8: Wire `LaunchdScheduler` into `MacOSAdapter`

**Files:**
- Modify: `adapters/macos/ascendo_macos/adapter.py`
- Modify: `adapters/macos/tests/test_adapter_smoke.py`

- [ ] **Step 1: Update the adapter smoke tests**

In `adapters/macos/tests/test_adapter_smoke.py`, replace the existing capability + accessor + package_managers tests:

```python
def test_capabilities_is_package_management_and_elevation_and_inventory_and_snapshots() -> None:
```

Find that function (around lines 29–37) and replace its body to add the SCHEDULING assertion. The whole function becomes:

```python
def test_capabilities_is_full_tier_1_minus_source() -> None:
    """M5.5 declares PACKAGE_MANAGEMENT | ELEVATION | INVENTORY | SNAPSHOTS | SCHEDULING."""
    a = MacOSAdapter()
    assert a.capabilities & AdapterCapability.PACKAGE_MANAGEMENT
    assert a.capabilities & AdapterCapability.ELEVATION
    assert a.capabilities & AdapterCapability.INVENTORY
    assert a.capabilities & AdapterCapability.SNAPSHOTS
    assert a.capabilities & AdapterCapability.SCHEDULING  # M5.5
```

And replace `test_unsupported_accessors_return_none_m55` (around lines 43–50) with:

```python
def test_unsupported_accessors_return_none_m55() -> None:
    """After M5.5, only source() is None (M6 cross-cutting)."""
    a = MacOSAdapter()
    assert a.inventory() is not None
    assert a.snapshot() is not None
    assert a.scheduler() is not None  # M5.5 wired
    assert a.source() is None
    assert a.elevation() is not None


def test_scheduler_returns_launchd_scheduler_singleton() -> None:
    from ascendo_macos.managers.scheduler import LaunchdScheduler
    a = MacOSAdapter()
    s1 = a.scheduler()
    s2 = a.scheduler()
    assert isinstance(s1, LaunchdScheduler)
    assert s1 is s2  # cached singleton
    assert s1.backend == "launchd"
```

- [ ] **Step 2: Run the adapter tests to verify they fail**

Run: `python3 -m pytest adapters/macos/tests/test_adapter_smoke.py -v -k 'capabilit or scheduler or unsupported_accessor' 2>&1 | tail -15`

Expected: 3 failures (SCHEDULING missing from capabilities; scheduler() returns None).

- [ ] **Step 3: Wire `LaunchdScheduler` into the adapter**

In `adapters/macos/ascendo_macos/adapter.py`:

(a) Add the import alongside the others (after `from .snapshot import TimeMachineSnapshot`):

```python
from .managers.scheduler import LaunchdScheduler
```

(b) In `__init__`, add the cache slot:

```python
    def __init__(self) -> None:
        self._cached_host: HostInfo | None = None
        self._cached_elevation: MacElevation | None = None
        self._cached_inventory: MacOSInventory | None = None
        self._cached_snapshot: TimeMachineSnapshot | None = None
        self._cached_scheduler: LaunchdScheduler | None = None  # M5.5
```

(c) Replace the `capabilities` property body:

```python
    @property
    def capabilities(self) -> AdapterCapability:
        return (
            AdapterCapability.PACKAGE_MANAGEMENT
            | AdapterCapability.ELEVATION
            | AdapterCapability.INVENTORY
            | AdapterCapability.SNAPSHOTS
            | AdapterCapability.SCHEDULING  # M5.5 (launchd)
        )
```

(d) Replace the `scheduler()` method:

```python
    def scheduler(self) -> IScheduler | None:
        """Returns a cached LaunchdScheduler singleton (M5.5).

        Per-user LaunchAgents in ~/Library/LaunchAgents/dev.ascendo.<name>.plist.
        DSL: DAILY/WEEKLY/MONTHLY/HOURLY/MINUTE forms (mirror of Windows).
        """
        if self._cached_scheduler is None:
            self._cached_scheduler = LaunchdScheduler(
                scripts_dir=self.SCRIPTS_DIR, lib_dir=self.LIB_DIR
            )
        return self._cached_scheduler
```

(e) Update the class docstring (lines 56–85) to reflect M5.5 — change the line `M5.5 — scheduler (launchd)` from a "remaining" note into a wired-up bullet, and add `SCHEDULING` to the capability listing. Replace the Capabilities-declared block to include `SCHEDULING`:

```python
        SCHEDULING         — per-user launchd LaunchAgents via LaunchdScheduler;
                             DSL mirrors WindowsScheduler (DAILY / WEEKLY /
                             MONTHLY / HOURLY / MINUTE). Plists land at
                             ~/Library/LaunchAgents/dev.ascendo.<name>.plist.
```

And remove the `Remaining accessors (scheduler, source) return None and are reserved for M5.5+: M5.5 — scheduler (launchd)` comment block — replace with:

```python
    Remaining accessor (source) returns None and is reserved for M6
    (cross-cutting threat-model work for source signature verification).
```

- [ ] **Step 4: Run the adapter tests to verify they pass**

Run: `python3 -m pytest adapters/macos/tests/test_adapter_smoke.py -v 2>&1 | tail -20`

Expected: all adapter tests pass (existing + 3 new pass).

- [ ] **Step 5: Verify no other tests regressed**

Run: `cd /Users/mk/Dev_Env/Ascendo/.claude/worktrees/cool-beaver-f1879c && PYTHONPATH=$PWD/core:$PWD/adapters/macos python3 -m pytest adapters/macos/tests/ -v 2>&1 | tail -10`

Expected: all tests pass (Task 1–8 totals).

- [ ] **Step 6: Commit**

```bash
git add adapters/macos/ascendo_macos/adapter.py adapters/macos/tests/test_adapter_smoke.py
git commit -m "feat(macos): wire LaunchdScheduler into MacOSAdapter (M5.5.8)"
```

---

## Task 9: Add `launchctl` to `health_check()`

**Files:**
- Modify: `adapters/macos/ascendo_macos/adapter.py`
- Modify: `adapters/macos/tests/test_adapter_smoke.py`

- [ ] **Step 1: Add the health-check test**

Append to `adapters/macos/tests/test_adapter_smoke.py`:

```python
def test_health_check_includes_launchctl():
    a = MacOSAdapter()
    components = a.health_check()
    assert "launchctl" in components
    # Status string is one of: ok / unavailable / error / degraded
    s = components["launchctl"]
    assert s.startswith(("ok", "unavailable", "error", "degraded"))


def test_health_check_has_ten_components():
    """M5.5 raises the macOS health-check component count from 9 to 10."""
    a = MacOSAdapter()
    components = a.health_check()
    assert len(components) == 10
    expected = {
        "brew", "jq", "mas", "system_profiler", "softwareupdate",
        "tmutil", "launchctl", "bash", "ascendo_lib", "ascendo_scripts",
    }
    assert set(components.keys()) == expected
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `python3 -m pytest adapters/macos/tests/test_adapter_smoke.py -v -k health 2>&1 | tail -10`

Expected: 2 failures (launchctl key missing, count is 9 not 10).

- [ ] **Step 3: Add `_launchctl_status()` and wire it into `health_check()`**

In `adapters/macos/ascendo_macos/adapter.py`, find `health_check()` (around line 205) and add the `launchctl` line between `tmutil` and `bash`:

```python
    def health_check(self) -> dict[str, str]:
        """Adapter self-test. Returns component→status_string for ``ascendo doctor``.

        Components checked (10 total):
            brew, jq, mas, system_profiler, softwareupdate, tmutil,
            launchctl, bash, ascendo_lib, ascendo_scripts
        """
        out: dict[str, str] = {}
        out["brew"] = self._brew_status()
        out["jq"] = self._jq_status()
        out["mas"] = self._mas_status()
        out["system_profiler"] = self._system_profiler_status()
        out["softwareupdate"] = self._softwareupdate_status()
        out["tmutil"] = self._tmutil_status()
        out["launchctl"] = self._launchctl_status()  # M5.5
        out["bash"] = self._bash_status()
        out["ascendo_lib"] = self._lib_status()
        out["ascendo_scripts"] = self._scripts_status()
        return out
```

Then add the `_launchctl_status()` method alongside the other private health helpers (after `_tmutil_status()` and before `_bash_status()`):

```python
    def _launchctl_status(self) -> str:
        path = shutil.which("launchctl") or "/bin/launchctl"
        if not Path(path).is_file():
            return "unavailable: launchctl not found (macOS-only built-in)"
        # `launchctl version` exists from macOS 10.10+; if it fails, fall back
        # to `launchctl help` which is documented on every release.
        try:
            res = subprocess.run(
                [path, "version"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"error: {exc}"
        if res.returncode != 0:
            try:
                res = subprocess.run(
                    [path, "help"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                return f"error: {exc}"
            if res.returncode != 0 and not (res.stdout or res.stderr):
                return f"error: launchctl help exited {res.returncode} with no output"
            return "ok"
        lines = (res.stdout or "").strip().splitlines()
        v = lines[0] if lines else ""
        return f"ok: {v}" if v else "ok"
```

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `python3 -m pytest adapters/macos/tests/test_adapter_smoke.py -v -k health 2>&1 | tail -10`

Expected: 2 passed.

- [ ] **Step 5: Run the full macOS test suite**

Run: `cd /Users/mk/Dev_Env/Ascendo/.claude/worktrees/cool-beaver-f1879c && PYTHONPATH=$PWD/core:$PWD/adapters/macos python3 -m pytest adapters/macos/tests/ 2>&1 | tail -5`

Expected: all tests pass (~30+ new + all prior).

- [ ] **Step 6: Commit**

```bash
git add adapters/macos/ascendo_macos/adapter.py adapters/macos/tests/test_adapter_smoke.py
git commit -m "feat(macos): health_check adds launchctl component (M5.5.9)"
```

---

## Task 10: Extend `bin/validate-macos.sh` with Stage 12

**Files:**
- Modify: `bin/validate-macos.sh`

- [ ] **Step 1: Update the script's header comment**

In `bin/validate-macos.sh`, find the header block (lines 7–26) and add a `12.` line before the "Exits 0" line:

```bash
#  11. Time Machine read-only (M5.4): doctor reports tmutil,
#      TimeMachineSnapshot.list() end-to-end (>=0 snapshots; no lower bound)
#  12. launchd scheduler round-trip (M5.5): doctor reports launchctl,
#      install + list + trigger + remove an `ascendo-validate-test` agent
```

- [ ] **Step 2: Add Stage 12 to the script body**

In `bin/validate-macos.sh`, locate the "Summary" section near the bottom (look for `# ── Summary ───`, around line 576). INSERT this new Stage 12 block immediately before it:

```bash
# ============================================================
# Stage 12 — launchd scheduler round-trip (M5.5)
# ============================================================
step "12. launchd scheduler round-trip (M5.5)"

# Stage 12 cleanup helper. Run on script exit AND once at start so a
# failed prior run doesn't leak agents.
SCHED_TEST_NAME="ascendo-validate-test"
SCHED_TEST_PLIST="$HOME/Library/LaunchAgents/dev.ascendo.${SCHED_TEST_NAME}.plist"
SCHED_TEST_SIDECAR="$HOME/Library/Application Support/Ascendo/schedules/${SCHED_TEST_NAME}.json"
_cleanup_sched_test() {
    /bin/launchctl bootout "gui/$(id -u)/dev.ascendo.${SCHED_TEST_NAME}" >/dev/null 2>&1 || true
    rm -f "$SCHED_TEST_PLIST" "$SCHED_TEST_SIDECAR" 2>/dev/null || true
}
_cleanup_sched_test
trap _cleanup_sched_test EXIT

# Capture doctor once for the launchctl grep.
DOCTOR_OUT_M55="$(python3 -m ascendo doctor 2>&1)"

# 12.1 doctor reports launchctl
step "12.1 doctor: launchctl component"
if printf '%s\n' "$DOCTOR_OUT_M55" | grep -qE '^[[:space:]]+launchctl[[:space:]]+(ok|degraded|unavailable|error)'; then
    printf '%s\n' "$DOCTOR_OUT_M55" | grep -E '^[[:space:]]+launchctl[[:space:]]+'
    result "12.1 doctor: launchctl component" 1
else
    result "12.1 doctor: launchctl component" 0 "no launchctl line in doctor output"
fi

# 12.2 schedule install via CLI
step "12.2 schedule install (MINUTE 1, profile=quick)"
if python3 -m ascendo schedule install \
        --name "$SCHED_TEST_NAME" \
        --expression "MINUTE 1" \
        --profile "quick" \
        >/dev/null 2>&1; then
    if [ -f "$SCHED_TEST_PLIST" ] && [ -f "$SCHED_TEST_SIDECAR" ]; then
        result "12.2 schedule install (MINUTE 1, profile=quick)" 1 "plist + sidecar written"
    else
        result "12.2 schedule install (MINUTE 1, profile=quick)" 0 "files missing after install"
    fi
else
    result "12.2 schedule install (MINUTE 1, profile=quick)" 0 "CLI exit non-zero"
fi

# 12.3 schedule list contains the new entry
step "12.3 schedule list contains entry"
if python3 -m ascendo schedule list 2>/dev/null | grep -q "$SCHED_TEST_NAME"; then
    result "12.3 schedule list contains entry" 1
else
    result "12.3 schedule list contains entry" 0 "entry not visible in list"
fi

# 12.4 schedule trigger
step "12.4 schedule trigger"
if python3 -m ascendo schedule trigger --name "$SCHED_TEST_NAME" >/dev/null 2>&1; then
    result "12.4 schedule trigger" 1
else
    result "12.4 schedule trigger" 0 "trigger exit non-zero"
fi

# 12.5 schedule remove
step "12.5 schedule remove"
if python3 -m ascendo schedule remove --name "$SCHED_TEST_NAME" >/dev/null 2>&1; then
    if [ ! -f "$SCHED_TEST_PLIST" ] && [ ! -f "$SCHED_TEST_SIDECAR" ]; then
        result "12.5 schedule remove" 1 "files cleaned up"
    else
        result "12.5 schedule remove" 0 "files left on disk after remove"
    fi
else
    result "12.5 schedule remove" 0 "CLI exit non-zero"
fi

```

- [ ] **Step 3: Verify the script still parses**

Run: `bash -n /Users/mk/Dev_Env/Ascendo/.claude/worktrees/cool-beaver-f1879c/bin/validate-macos.sh && echo OK`

Expected: `OK`.

- [ ] **Step 4: Commit**

```bash
git add bin/validate-macos.sh
git commit -m "feat(bin): validate-macos.sh Stage 12 — scheduler round-trip (M5.5.10)"
```

---

## Task 11: Bump tag in `bin/run-tag-release-macos.sh` to v0.2.0

**Files:**
- Modify: `bin/run-tag-release-macos.sh`

- [ ] **Step 1: Replace every `v0.0.11-alpha` with `v0.2.0`**

Use the Edit tool with `replace_all` to swap the literal string `v0.0.11-alpha` for `v0.2.0` in `bin/run-tag-release-macos.sh`.

After the replace, also update the header comment line (around line 13):

```bash
#   7. Doctor + tag    -- `git tag -a v0.0.11-alpha`. Does NOT push.
```

becomes:

```bash
#   7. Doctor + tag    -- `git tag -a v0.2.0`. Does NOT push.
```

And update the tag annotation message (look for the `git tag -a v0.0.11-alpha \\` line and the next line, around lines 210–211 before the renames):

```bash
    git tag -a v0.2.0 \
        -m "macOS adapter M5.5 — launchd IScheduler; v0.2.0 (full M5 — adapter feature-complete) (apply RC=$APPLY_RC)"
```

- [ ] **Step 2: Verify the script still parses**

Run: `bash -n /Users/mk/Dev_Env/Ascendo/.claude/worktrees/cool-beaver-f1879c/bin/run-tag-release-macos.sh && echo OK`

Expected: `OK`.

- [ ] **Step 3: Confirm no stale v0.0.11-alpha references remain**

Run: `grep -n 'v0.0.11-alpha' /Users/mk/Dev_Env/Ascendo/.claude/worktrees/cool-beaver-f1879c/bin/run-tag-release-macos.sh && echo "FOUND" || echo "CLEAN"`

Expected: `CLEAN`.

- [ ] **Step 4: Commit**

```bash
git add bin/run-tag-release-macos.sh
git commit -m "feat(bin): run-tag-release-macos.sh tag bump v0.2.0 (M5.5.11)"
```

---

## Task 12: Real-Mac end-to-end validation

> **NOTE:** This task runs on Mac.r12.home (or any operator's real Mac). It cannot be executed in CI. The implementation engineer pauses here and hands off to the operator to run `validate-macos.sh` and report the result.

- [ ] **Step 1: Operator runs validate-macos.sh**

On a real macOS host with the worktree checked out and `bash bin/install-dev-macos.sh` already run successfully:

```bash
cd /path/to/Ascendo
bash bin/validate-macos.sh
```

Expected final line: `ALL CHECKS PASSED. (34/34)` (was 29/29 before M5.5; +5 from Stage 12).

- [ ] **Step 2: Operator confirms no leftover agents**

```bash
ls ~/Library/LaunchAgents/dev.ascendo.* 2>/dev/null
ls ~/Library/Application\ Support/Ascendo/schedules/ 2>/dev/null
```

Expected: no output (Stage 12 cleanup `trap` removed the test agent + sidecar).

- [ ] **Step 3: Operator runs run-tag-release-macos.sh to tag v0.2.0**

```bash
bash bin/run-tag-release-macos.sh
```

(Runs Stages 1–7 — same flow as M5.4, just with the tag bumped.)

Expected: tag `v0.2.0` created locally on the merge commit. Operator runs `git push --tags` manually when ready.

- [ ] **Step 4: Implementation engineer verifies the tag locally**

Once the operator reports success:

```bash
git tag --list 'v0.2*'
```

Expected: `v0.2.0` listed.

(No commit in this task — Tasks 13–14 commit the docs updates.)

---

## Task 13: Update PLAN.md — mark M5.5 done, M5 complete

**Files:**
- Modify: `PLAN.md`

- [ ] **Step 1: Mark M5.5 done in the M5 status table**

In `PLAN.md`, find the M5 status table (around the "M5 — macOS adapter" section). The current `M5.5` row reads:

```markdown
| M5.5 | ⏳ pending | `launchd` `IScheduler`. After this, tag `v0.2.0` (full M5). |
```

Replace with:

```markdown
| **M5.5** | ✅ done (2026-05-04, **v0.2.0**) | `LaunchdScheduler` (per-user LaunchAgents in `~/Library/LaunchAgents/dev.ascendo.<name>.plist`); DSL mirrors WindowsScheduler (DAILY/WEEKLY/MONTHLY/HOURLY/MINUTE → `StartCalendarInterval` plist dict / `StartInterval` for the MINUTE form); description metadata in sidecar JSON at `~/Library/Application Support/Ascendo/schedules/<name>.json`. Capability `SCHEDULING` added; `MacOSAdapter.capabilities` now `PACKAGE_MANAGEMENT \| ELEVATION \| INVENTORY \| SNAPSHOTS \| SCHEDULING` (full Tier-1 minus `SOURCE`, which is M6 cross-cutting). ~28 new tests + Stage 12 e2e (5 sub-steps) via `validate-macos.sh`. **Tag `v0.2.0` — full M5 macOS adapter feature-complete.** Spec/plan: `docs/superpowers/specs/2026-05-04-macos-launchd-scheduler-design.md` + `docs/superpowers/plans/2026-05-04-macos-launchd-scheduler.md`. See HANDOFF.md Sesja 27. |
```

- [ ] **Step 2: Update the "Forward backlog" sub-table**

Find the table with the row:

```markdown
| `managers/scheduler.py` | launchd | 80 + 200 | M5.5 |
```

Change `M5.5` (last column) to `✅ M5.5`.

- [ ] **Step 3: Update the top-of-file "Last updated" line**

Find the line near the top (around line 3):

```markdown
> Last updated: 2026-05-04 (sesja 26) — macOS adapter M5.4 shipped (softwareupdate + Time Machine read-only, v0.0.11-alpha).
```

Replace with:

```markdown
> Last updated: 2026-05-04 (sesja 27) — macOS adapter M5.5 shipped (launchd IScheduler, **v0.2.0** = full M5 macOS adapter feature-complete).
```

- [ ] **Step 4: Update the "Current state" / "What landed" framing if present**

In the same file, look for any reference to "M5.4" being the latest and update to M5.5 / v0.2.0. Specifically the "What's next (M5.5+)" callouts in §M5 status table become "What's next (M6)".

- [ ] **Step 5: Commit**

```bash
git add PLAN.md
git commit -m "docs(plan): mark M5.5 done, full M5 complete (v0.2.0)"
```

---

## Task 14: Add Sesja 27 to HANDOFF.md

**Files:**
- Modify: `HANDOFF.md`

- [ ] **Step 1: Insert a new Sesja 27 section at the top of the historical log**

In `HANDOFF.md`, find the existing `## Sesja 26 (2026-05-04)` heading and insert a new block ABOVE it:

```markdown
## Sesja 27 (2026-05-04) — macOS adapter M5.5: launchd IScheduler + v0.2.0

Final milestone of the macOS adapter (M5). One Layer-5 component:

**LaunchdScheduler** implements `IScheduler` via macOS `launchd` LaunchAgents.
Per-user agents only (no root): plists land at
`~/Library/LaunchAgents/dev.ascendo.<name>.plist`. Schedule expression DSL
mirrors the Windows scheduler exactly:

| DSL form              | launchd plist                                  |
|-----------------------|------------------------------------------------|
| `DAILY HH:MM`         | `StartCalendarInterval{Hour,Minute}`           |
| `WEEKLY DAY HH:MM`    | `StartCalendarInterval{Hour,Minute,Weekday}`   |
| `MONTHLY HH:MM`       | `StartCalendarInterval{Hour,Minute,Day=1}`     |
| `MONTHLY DAY HH:MM`   | `StartCalendarInterval{Hour,Minute,Day}`       |
| `HOURLY :MM`          | `StartCalendarInterval{Minute}`                |
| `MINUTE N`            | `StartInterval=N*60`                            |

Description metadata (which launchd plists have no native field for) is
stored in a sidecar JSON at
`~/Library/Application Support/Ascendo/schedules/<name>.json`. The bash
driver `scripts/scheduler/scheduler.sh` is the single source of truth for
plist serialisation, `launchctl` invocation, and DSL translation. Python
`LaunchdScheduler` is a thin JSON-IPC wrapper (mirrors `WindowsScheduler._invoke`).

Tag `v0.2.0` created locally + pushed via the operator. Real-Mac
`validate-macos.sh` showed **34/34 PASS** including all of Stage 12 (5
sub-steps): doctor reports `launchctl ok`; install + list + trigger +
remove round-trip the throwaway `ascendo-validate-test` agent cleanly;
no leftover plists or sidecars after teardown.

### Architecture confirmed end-to-end

- Layer 4 core: no changes. `IScheduler` + `ScheduleSpec` already complete.
- `MacOSAdapter.capabilities` flips to `PACKAGE_MANAGEMENT | ELEVATION |
  INVENTORY | SNAPSHOTS | SCHEDULING`. `scheduler()` returns cached
  `LaunchdScheduler` singleton.
- Health check now reports 10 components (was 9): brew/jq/mas/system_profiler/
  softwareupdate/tmutil + new launchctl + bash/ascendo_lib/ascendo_scripts.
- Threat surface: per-user agents only — no root, no system-wide exposure.
  `ProgramArguments` argv-only (`/usr/bin/env ascendo run --profile <p>`).
  `<name>` constrained to `^[a-z0-9-]+$` by Pydantic, eliminating injection
  via plist filenames or launchctl domain targets.

### Files added (per M5.5.x sub-task)

- `adapters/macos/scripts/scheduler/scheduler.sh` — bash driver (M5.5.1–5)
- `adapters/macos/ascendo_macos/managers/scheduler.py` — `LaunchdScheduler` (M5.5.6–7)
- `adapters/macos/tests/test_scheduler_script_smoke.py` — 22 driver tests (M5.5.1–5)
- `adapters/macos/tests/test_launchd_scheduler_smoke.py` — 14 Python smoke tests (M5.5.6–7)

### Files modified

- `adapters/macos/ascendo_macos/adapter.py` — capabilities flip + scheduler()
  singleton + `_launchctl_status()` health helper (M5.5.8–9)
- `adapters/macos/tests/test_adapter_smoke.py` — capability/scheduler/health
  assertions updated (M5.5.8–9)
- `bin/validate-macos.sh` — Stage 12 added (M5.5.10)
- `bin/run-tag-release-macos.sh` — tag bump v0.0.11-alpha → v0.2.0 (M5.5.11)

### Real run trace (Stage 12)

```
==> 12.1 doctor: launchctl component        [PASS]   launchctl   ok: launchctl version=...
==> 12.2 schedule install (MINUTE 1, ...)   [PASS]   plist + sidecar written
==> 12.3 schedule list contains entry       [PASS]
==> 12.4 schedule trigger                   [PASS]
==> 12.5 schedule remove                    [PASS]   files cleaned up
ALL CHECKS PASSED. (34/34)
```

### What's next (M6)

- **M6** — hardening + v1.0 stable: security audit (T1–T7 threat-model
  items per ADR-0005); code signing across all three OSes (Apple Developer
  ID + Authenticode); plugin signing + verification (FAZA II); plugin
  marketplace UX in dashboard; localization beyond en/pl (tokens already
  support es/it/pt/de/fr); telemetry (opt-in, 100% local-only).

### Spec + plan

- `docs/superpowers/specs/2026-05-04-macos-launchd-scheduler-design.md`
- `docs/superpowers/plans/2026-05-04-macos-launchd-scheduler.md`
```

- [ ] **Step 2: Commit**

```bash
git add HANDOFF.md
git commit -m "docs(handoff): Sesja 27 — macOS adapter M5.5 + v0.2.0"
```

---

## Self-Review

**1. Spec coverage:** every §-numbered section in `2026-05-04-macos-launchd-scheduler-design.md` maps to at least one task:
- §1 Goal → Tasks 6–9 (LaunchdScheduler + adapter wire-up).
- §2 Architecture → Tasks 1–9 (each layer).
- §3 Capability flag → Task 8.
- §4 DSL → Task 2 (`_parse_expression`) + Task 3 (StartCalendarInterval block).
- §5 LaunchAgent plist layout → Task 3.
- §6 Python class shape → Tasks 6–7.
- §7 Bash driver actions → Tasks 1–5 (one per sub-section).
- §8 UID resolution → Task 1 (`UID_VAL=$(id -u)` in skeleton).
- §9 Health check → Task 9.
- §10 Tests target → Tasks 1–9 produce ~36 tests (~28 mock + ~8 surrogate-bash; matches the spec's "~28 new tests" target ± a few).
- §11 Stage 12 → Task 10.
- §12 Threat model → no new code task; threat-model claims hold by virtue of Tasks 3–7 (argv-only, name regex enforcement).
- §13 Deferred → no tasks (deferred is deferred).
- §14 Tag exit bar → Tasks 11–12.

**2. Placeholder scan:** every code block contains real code; every `Run:` step has the exact command + expected output. No `TBD`, no `TODO`, no "implement appropriate handling".

**3. Type consistency:**
- `LaunchdScheduler.BACKEND = "launchd"` (Tasks 6, 7) consistent with `IScheduler.backend` contract.
- `_parse_expression` global names (`CAL_HOUR`, `CAL_MINUTE`, `CAL_WEEKDAY`, `CAL_DAY`, `CAL_INTERVAL_SEC`) consistent across Task 2 (definition) and Task 3 (consumption).
- `LABEL_PREFIX = "dev.ascendo."` consistent across Tasks 1, 3, 4, 5.
- `_invoke` signature matches `WindowsScheduler._invoke` for cross-OS reasoning.
- `ScheduleSpec` field names used in payload JSON (`name`, `expression`, `profile`, `enabled`, `description`) match the Pydantic model in `core/ascendo/interfaces/scheduler.py:36-54`.

**4. Completion check:** Tasks 1–14 build sequentially; each commits at task end so a partial run can resume cleanly. Task 12 is operator-gated (real Mac required); tasks 13–14 only commit after the operator confirms 34/34 pass.

---

Plan complete and saved to [docs/superpowers/plans/2026-05-04-macos-launchd-scheduler.md](docs/superpowers/plans/2026-05-04-macos-launchd-scheduler.md).
