# macOS adapter — M5.5 launchd `IScheduler` design

> **Status**: design approved 2026-05-04. Implementation plan to follow in
> `docs/superpowers/plans/2026-05-04-macos-launchd-scheduler.md`.
> Target tag: `v0.2.0` (full M5 — macOS adapter feature-complete).

## 1. Goal

Ship the last Layer-5 component for the macOS adapter: a `LaunchdScheduler`
that implements `IScheduler` via macOS `launchd` LaunchAgents. After this
milestone, `MacOSAdapter` declares the same `TIER_1_FULL` capability set as
`WindowsAdapter` (minus `SOURCE`, which is M6 cross-cutting):

```
PACKAGE_MANAGEMENT | ELEVATION | INVENTORY | SNAPSHOTS | SCHEDULING
```

Functional outcome:

- `python -m ascendo schedule install --weekly` registers a per-user
  LaunchAgent that runs `ascendo run --profile <p>` on a recurring schedule.
- `python -m ascendo schedule list` enumerates Ascendo-owned LaunchAgents
  (and only those — no system-wide enumeration).
- `python -m ascendo schedule trigger <name>` runs the agent immediately
  (`launchctl kickstart`).
- `MacOSAdapter.scheduler()` returns a non-None instance.

Tag exit bar: `v0.2.0` after Stage 12 (scheduler round-trip) green in
`bin/validate-macos.sh`.

## 2. Architecture

Mirrors M3.13 (Windows Task Scheduler) shape exactly, with macOS-specific
backend. Layers:

- **Layer 4 (core)**: no changes. `IScheduler` + `ScheduleSpec` already exist.
- **Layer 5 (Python adapter)**:
  - `adapters/macos/ascendo_macos/managers/scheduler.py` (~150 LOC) —
    `LaunchdScheduler` mirrors the `WindowsScheduler` JSON-IPC pattern: each
    method writes a JSON payload, invokes the bash driver with
    `--action <verb>`, parses the JSON result, returns typed objects.
- **Layer 6 (bash driver)**:
  - `adapters/macos/scripts/scheduler/scheduler.sh` (~300 LOC) — single
    driver script, dispatches on `--action {install|uninstall|list|get|trigger}`.
    Writes/reads plist files under `~/Library/LaunchAgents/` and shells out
    to `launchctl bootstrap|bootout|kickstart|print`.
- **Adapter wire-up**:
  - `MacOSAdapter.capabilities` adds `SCHEDULING`.
  - `MacOSAdapter.scheduler()` returns a lazy-init cached `LaunchdScheduler`.
  - `MacOSAdapter.health_check()` adds `launchctl` component (10 components
    total, was 9).

**No new dashboard work.** The existing `ascendo schedule {install|remove|
list|trigger}` CLI subcommands (already wired to `IScheduler` via
`_resolve_adapter_for_capability(SCHEDULING)`) start working unchanged.

**No orchestrator change.**

## 3. Capability flag

`MacOSAdapter.capabilities` flips from
`PACKAGE_MANAGEMENT | ELEVATION | INVENTORY | SNAPSHOTS` (M5.4 state) to
`PACKAGE_MANAGEMENT | ELEVATION | INVENTORY | SNAPSHOTS | SCHEDULING`.

## 4. Schedule expression DSL (mirror Windows)

`ScheduleSpec.expression` is parsed by the bash driver and translated into
a `StartCalendarInterval` plist dict. Supported syntax (subset that maps
cleanly onto launchd's single-shot calendar entries):

| DSL form              | launchd plist (`StartCalendarInterval`) |
|-----------------------|------------------------------------------|
| `DAILY HH:MM`         | `{Hour=H, Minute=M}` |
| `WEEKLY DAY HH:MM`    | `{Hour=H, Minute=M, Weekday=D}` (D = 0–7, 0/7 = Sun) |
| `MONTHLY HH:MM`       | `{Hour=H, Minute=M, Day=1}` |
| `MONTHLY DAY HH:MM`   | `{Hour=H, Minute=M, Day=D}` (D = 1–31) |
| `HOURLY :MM`          | `{Minute=M}` (fires every hour at minute M) |
| `MINUTE N`            | `{StartInterval=N*60}` (interval form, N ≥ 1 minute) |

`DAY` token accepted on `WEEKLY` (case-insensitive): `SUN`, `MON`, `TUE`,
`WED`, `THU`, `FRI`, `SAT`. Anything else → `SchedulerError("unsupported
schedule expression: <expr>")` raised by the bash driver and surfaced as
non-zero exit + JSON `{"error": "..."}` to Python.

The DSL is a strict subset of what Windows accepts. Windows-accepted forms
that don't map (`MONTHLY <DAY>` with day > 28 isn't a worry — launchd
silently skips months without that day, which is acceptable; no special
handling). The Windows-only "advanced schtasks passthrough" feature is NOT
mirrored — macOS gets only the documented DSL forms. Operators wanting
custom plist tweaks edit the file by hand and are explicitly out of scope.

## 5. LaunchAgent plist layout

Each Ascendo schedule writes to:

```
~/Library/LaunchAgents/dev.ascendo.<name>.plist
```

Where `<name>` is `ScheduleSpec.name` (already constrained to `^[a-z0-9-]+$`
by Pydantic, so it's filename-safe). Reverse-DNS prefix `dev.ascendo.` is
fixed and used as the **enumeration filter**: `list()` globs
`dev.ascendo.*.plist` and ignores anything else under `LaunchAgents/`.

Plist contents (XML, written by the bash driver):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>dev.ascendo.&lt;name&gt;</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/env</string>
        <string>ascendo</string>
        <string>run</string>
        <string>--profile</string>
        <string>&lt;profile&gt;</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>3</integer>
        <key>Minute</key>
        <integer>0</integer>
        <key>Weekday</key>
        <integer>0</integer>
    </dict>
    <key>RunAtLoad</key>
    <false/>
    <key>StandardOutPath</key>
    <string>/Users/&lt;user&gt;/Library/Logs/Ascendo/scheduler-&lt;name&gt;.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/&lt;user&gt;/Library/Logs/Ascendo/scheduler-&lt;name&gt;.log</string>
</dict>
</plist>
```

Notes:

- **`/usr/bin/env ascendo`** rather than a hardcoded absolute path. Per-user
  Python installs (Homebrew, pyenv) put `ascendo` in different locations.
  `env` walks `PATH` at agent fire time. The PATH that launchd inherits is
  documented to include `/usr/local/bin` and the user's shell PATH after
  `path_helper`, which covers Homebrew installs. If `ascendo` isn't found,
  launchd surfaces it as a load failure and the agent's stderr log captures
  the message — operators see it via `python -m ascendo schedule list`.
- **`Description` field deliberately not used** — `man launchd.plist` does
  not document a `Description` key, and unrecognised keys are silently
  ignored. We persist `description` in a sidecar JSON in
  `~/Library/Application Support/Ascendo/schedules/<name>.json` instead
  (mirrors the Windows pattern of using a separate registry for fields
  Task Scheduler doesn't store natively).
- **`enabled=False`** translates to `Disabled` key set to `<true/>`. On
  install, the bash driver bootouts then bootstraps the plist; if disabled,
  it bootouts and skips the bootstrap (the file stays on disk for
  auditability and future re-enable).
- **`RunAtLoad=False`** — agents only fire on schedule, never on boot/login.
  Operators wanting a "run-on-login" semantic use `trigger` after install.
- **Logs directory** `~/Library/Logs/Ascendo/` is created (mkdir -p) by the
  bash driver before plist write so the first agent fire doesn't fail with
  `posix_spawn: ENOENT`.

## 6. `LaunchdScheduler` (Python) shape

```python
class LaunchdScheduler(IScheduler):
    BACKEND: ClassVar[str] = "launchd"
    DEFAULT_TIMEOUT_SEC: ClassVar[int] = 30

    def __init__(self, *, scripts_dir: Path, lib_dir: Path,
                 timeout_sec: int = DEFAULT_TIMEOUT_SEC) -> None: ...

    @property
    def backend(self) -> str: return self.BACKEND

    def is_available(self, host: HostInfo) -> bool:
        if host.os is not OperatingSystem.MACOS: return False
        return shutil.which("launchctl") is not None

    def install(self, host, spec): self._invoke("install", payload={...})
    def uninstall(self, host, name): self._invoke("uninstall", payload={"name": name})
    def list(self, host) -> list[ScheduleSpec]: ...  # parses JSON array result
    def get(self, host, name): for s in self.list(host): if s.name == name: return s
    def trigger(self, host, name): self._invoke("trigger", payload={"name": name})
```

`_invoke` mirrors `WindowsScheduler._invoke`:

1. Write `payload` JSON to a temp file.
2. Spawn `bash adapters/macos/scripts/scheduler/scheduler.sh
   --action <verb> --output-path <result.json> [--payload-path <payload.json>]`.
3. Capture stdout/stderr; check exit code.
4. If `result.json` exists, parse JSON (list for `list`, dict for `get`,
   None for install/uninstall/trigger).
5. Translate non-zero exit + missing/invalid result.json into
   `SchedulerError("scheduler <verb> failed: ...")`.

The bash driver is the single source of truth for plist serialisation,
launchctl invocation, and DSL → `StartCalendarInterval` translation.
Python stays dumb (matches the established adapter pattern).

## 7. Bash driver (`scripts/scheduler/scheduler.sh`) shape

Single script, dispatches on `--action`. Reads `--payload-path` (when
present) and `--output-path` (always set by the Python caller). Bash 3.2
compatible (no `declare -A`, `mapfile`, `readarray`).

### 7.1 install action

```
Inputs:
  payload.json = {name, expression, profile, enabled, description}

Steps:
  1. Validate name (regex ^[a-z0-9-]+$).
  2. Parse expression → calendar dict via _parse_expression(). Errors fail
     with exit 2 + {"error": "..."} written to output.json.
  3. mkdir -p ~/Library/LaunchAgents and ~/Library/Logs/Ascendo
     and ~/Library/Application\ Support/Ascendo/schedules.
  4. Write the plist file via cat<<EOF > ~/Library/LaunchAgents/dev.ascendo.<name>.plist
     with the parsed calendar dict + ProgramArguments.
  5. Write the description sidecar to
     ~/Library/Application\ Support/Ascendo/schedules/<name>.json
     ({name, description, profile, expression, enabled, installed_at}).
  6. If a previous agent of the same Label was loaded, bootout it first
     (idempotent: silent on "no such service").
  7. If enabled: launchctl bootstrap gui/<uid> ~/Library/LaunchAgents/...plist
     If !enabled: skip bootstrap; plist sits on disk for re-enable later.
  8. Emit {"ok": true} to output.json. Exit 0.
```

### 7.2 uninstall action

```
Inputs:
  payload.json = {name}

Steps:
  1. plist=~/Library/LaunchAgents/dev.ascendo.<name>.plist
  2. If plist exists: launchctl bootout gui/<uid> "$plist" || true
                      (silent on "no such service" — agent might not be loaded)
                      rm -f "$plist"
                      rm -f the description sidecar
                      Emit {"ok": true} to output.json. Exit 0.
  3. If plist doesn't exist: emit {"ok": true} (no-op per IScheduler contract).
     Exit 0.
```

### 7.3 list action

```
Steps:
  1. shopt -s nullglob (or equivalent)
  2. For each ~/Library/LaunchAgents/dev.ascendo.*.plist:
     - Extract name via filename suffix.
     - Read description sidecar (if present) for {profile, expression,
       enabled, description}.
     - If sidecar missing: parse plist for ProgramArguments[--profile] +
       StartCalendarInterval and reconstruct expression best-effort
       (covers manually-edited plists; expression may degrade to
       "DAILY HH:MM" or "" on irrecoverable cases).
  3. Emit JSON array to output.json. Exit 0.
```

### 7.4 get action

Same as list but filtered to one name. (Python falls back to list-and-
filter, but the bash action exists for symmetry with the Windows driver
and possible direct CLI invocation.)

### 7.5 trigger action

```
Inputs:
  payload.json = {name}

Steps:
  1. plist=~/Library/LaunchAgents/dev.ascendo.<name>.plist
  2. If !plist: emit {"error": "no such schedule: <name>"} to output.json,
     exit 30. (SchedulerError on the Python side.)
  3. Ensure agent is loaded: launchctl bootstrap gui/<uid> "$plist" || true
     (silent on "service already loaded" — idempotent).
  4. launchctl kickstart gui/<uid>/dev.ascendo.<name>
     Capture exit code; if non-zero, emit {"error": "kickstart failed:
     ..."} and exit 30.
  5. Emit {"ok": true}. Exit 0.
```

### 7.6 `_parse_expression` helper

Pure-bash regex match against the 6 DSL forms. Sets globals `CAL_HOUR`,
`CAL_MINUTE`, `CAL_WEEKDAY`, `CAL_DAY`, `CAL_INTERVAL_SEC` (or unset
where not applicable). Returns 0 on success, 2 on parse error with stderr
`unsupported schedule expression: <expr>`.

Day-name table: `SUN=0 MON=1 TUE=2 WED=3 THU=4 FRI=5 SAT=6`. Bash 3.2:
big `case` statement, not associative array.

## 8. Capability discovery + UID resolution

`launchctl bootstrap`/`bootout`/`kickstart` need a domain target:
`gui/<uid>` for per-user agents. The bash driver computes
`uid="$(id -u)"` once at top — no need to take it from Python (Python's
`getpass.getuser()` runs in a different process).

`is_available()` (Python side) checks `host.os is MACOS` AND
`shutil.which("launchctl")` returns truthy. `launchctl` is a built-in
macOS binary at `/bin/launchctl`; it should always be present, so the
PATH check is mostly defensive against a stripped-down environment
(CI containers, etc.).

## 9. Health check addition

`MacOSAdapter._launchctl_status()`:
- `path = shutil.which("launchctl") or "/bin/launchctl"`
- If `not Path(path).is_file()`: return
  `"unavailable: launchctl not found (macOS-only built-in)"`
- `subprocess.run([path, "version"], timeout=5, check=False)` — confirm
  it executes. (`launchctl version` is a documented subcommand on
  macOS 10.10+; fallback to `launchctl help` if `version` fails on
  older releases.)
- Return `"ok: <stdout-first-line>"` on success, `"error: ..."` on
  subprocess failure.

Component count goes 9 → 10. Existing 9: `brew, jq, mas, system_profiler,
softwareupdate, tmutil, bash, ascendo_lib, ascendo_scripts`. New: `launchctl`.

Slot the `launchctl` check between `tmutil` and `bash` so the macOS
built-ins (`system_profiler / softwareupdate / tmutil / launchctl`) cluster
together in `ascendo doctor` output.

## 10. Tests target

| Test file | Count |
|---|---|
| `adapters/macos/tests/test_launchd_scheduler_smoke.py` | ~12 (mock-based: backend slug, OS gate, launchctl missing → unavailable, install/uninstall/list/get/trigger argv shape, `_invoke` writes payload + parses result, SchedulerError on non-zero exit, SchedulerError on invalid JSON, parametrized over the 5 actions) |
| `adapters/macos/tests/test_scheduler_script_smoke.py` | ~10 (real bash, fake `launchctl` on PATH that records argv: install round-trips DSL→plist for all 6 DSL forms, uninstall idempotent on missing plist, list emits empty array on empty dir, list parses sidecar metadata, list reconstructs expression when sidecar missing, trigger fails clean with exit 30 on missing plist, _parse_expression rejects garbage, enabled=False skips bootstrap) |
| `adapters/macos/tests/test_adapter_smoke.py` | +4 wiring tests (capabilities includes SCHEDULING, scheduler() returns LaunchdScheduler singleton, scheduler() result not None on macOS host, health_check includes launchctl) |
| `tests/contract/test_scheduler_contract.py` | +2 (LaunchdScheduler conforms to IScheduler abstract methods; ScheduleSpec name regex still rejects garbage) |

Total: **~28 new tests** + Stage 12 (5 sub-steps) e2e via `validate-macos.sh`.

## 11. `validate-macos.sh` Stage 12

Stage 12 — scheduler round-trip on real Mac. `validate-macos.sh` script
exit count goes 29 → 34.

- 12.1 doctor reports `launchctl` component as `ok`.
- 12.2 `python -m ascendo schedule install` for a throwaway entry
  (`ascendo-validate-test`, `MINUTE 1`, `quick`). Verify exit 0,
  ~/Library/LaunchAgents/dev.ascendo.ascendo-validate-test.plist exists,
  description sidecar exists, agent shows up in
  `launchctl print gui/<uid>/dev.ascendo.ascendo-validate-test`.
- 12.3 `python -m ascendo schedule list` includes the new entry with
  matching profile + expression.
- 12.4 `python -m ascendo schedule trigger ascendo-validate-test`
  exits 0. (We deliberately don't assert the run actually completed —
  that depends on `ascendo run --profile quick` having time to finish,
  which can be 10s of seconds. The kickstart succeeding is sufficient
  validation that the wiring is correct.)
- 12.5 `python -m ascendo schedule remove ascendo-validate-test`
  exits 0. Verify plist file gone, sidecar gone,
  `launchctl print gui/<uid>/dev.ascendo.ascendo-validate-test`
  exits non-zero (service unloaded).

Cleanup: any leftover plists from a failed Stage 12 run get cleaned by
a `trap` at script start (`rm -f ~/Library/LaunchAgents/dev.ascendo.ascendo-validate-test.plist`).
This avoids polluting the operator's LaunchAgents dir on test runs that
crash mid-stage.

## 12. Threat model deltas

`launchctl` is a built-in macOS binary, no signing risk. Plist files are
written to the per-user `~/Library/LaunchAgents/` (no root, no system-wide
exposure). `ProgramArguments` is `/usr/bin/env ascendo run --profile <p>`
— argv-only, no shell-string concatenation. `<p>` is `ScheduleSpec.profile`
which is already validated by the Pydantic `ProfileName` constraint
(matches `^[A-Za-z0-9_-]+$`).

`<name>` is constrained to `^[a-z0-9-]+$` by Pydantic before the bash
driver sees it, eliminating injection via the plist filename or the
`launchctl` domain target.

No new T1–T7 surface. Per-user LaunchAgents cannot escalate privileges
— they run as the installing user and have no special capability beyond
that user's interactive session.

## 13. What's deferred

- **System-wide LaunchDaemons** — would need root and would let scheduled
  runs fire while no user is logged in. Out of scope for v0.2.0; revisit
  if operators ask for "fleet-wide unattended schedule" semantics.
- **`StartInterval` for sub-minute frequencies** — `MINUTE N` enforces
  N≥1 because launchd plist `StartInterval` is in seconds and the SPA
  prompt "every N minutes" maps cleanly to N\*60s. Sub-minute intervals
  are noise.
- **Random delay** (`StartCalendarInterval` is exact; launchd has no
  `RandomDelaySec` for calendar entries — only for `StartInterval`).
  Not needed for an update orchestrator.
- **Cron-string parser** — option A from brainstorming. The DSL already
  covers every real schedule shape; cron strings would just translate
  to the same launchd subset with worse readability. If operators
  request, add as a follow-up parser layer that feeds the existing DSL.
- **Dashboard schedule editor UI** — CLI-only for v0.2.0. The dashboard
  already has a Schedule view that reads `IScheduler.list()`; mutations
  via the dashboard are deferred (no change in scope versus M3.13's
  Windows release).
- **Major-version macOS upgrade scheduling** — operator-driven, not a
  recurring schedule.

## 14. Tag exit bar

`v0.2.0` after:

- All ~28 new tests green.
- `bin/validate-macos.sh` Stage 12 green on Mac.r12.home (5 sub-steps,
  total exit count 34/34 PASS).
- `MacOSAdapter.capabilities` enumerates `SCHEDULING`.
- `MacOSAdapter.scheduler()` returns a non-None instance whose
  `is_available(host)` is True on the test Mac.
- `MacOSAdapter.health_check()` reports `launchctl` component as `ok`.
- Real-Mac smoke `python -m ascendo schedule install ... && schedule list
  && schedule remove ...` round-trip, the throwaway `ascendo-validate-test`
  plist gone afterwards.
- Tag created locally on the merge commit; user runs `git push --tags`
  manually.

This closes M5. After v0.2.0, the macOS adapter declares the same Tier-1
capability set as the Windows adapter (modulo `SOURCE`, which is M6
cross-cutting across all OSes).

## 15. Spec + plan references

- This spec: `docs/superpowers/specs/2026-05-04-macos-launchd-scheduler-design.md`
- Plan (next): `docs/superpowers/plans/2026-05-04-macos-launchd-scheduler.md`
- M5.4 spec for the most recent macOS pattern:
  `docs/superpowers/specs/2026-05-04-macos-softwareupdate-snapshot-design.md`
- Windows scheduler reference (M3.13):
  `adapters/windows/ascendo_windows/managers/scheduler.py` +
  `adapters/windows/scripts/scheduler/scheduler.ps1`
- IScheduler contract: `core/ascendo/interfaces/scheduler.py`
- Legacy macOS scheduling in `Ascendo/` is shell-cron-style
  (ad-hoc `crontab` edits, not launchd) — not a useful template; this
  spec is built fresh on launchd best practices from `man launchd.plist`
  and `man launchctl`.
