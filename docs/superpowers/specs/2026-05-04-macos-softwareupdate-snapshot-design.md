# macOS adapter — M5.4 softwareupdate + Time Machine read-only snapshot design

> **Status**: design approved 2026-05-04. Implementation plan to follow in
> `docs/superpowers/plans/2026-05-04-macos-softwareupdate-snapshot.md`.
> Target tag: `v0.0.11-alpha`.

## 1. Goal

Ship two new Layer-5 components for the macOS adapter:

1. **`SoftwareUpdateManager`** — wraps Apple's `softwareupdate` CLI. Implements
   the 5-phase contract (check/plan/apply/verify/cleanup). Lists pending macOS
   OS updates via `softwareupdate -l`, applies via
   `sudo -A softwareupdate -ir -R --verbose` (recommended-only by default;
   `--all` flag opts into `-ia` for non-recommended updates). The `-R` flag
   is **mandatory** — it sets the boot metadata that triggers the update on
   restart. Without `-R`, updates download but never apply.

2. **`TimeMachineSnapshot`** — implements `ISnapshot` for macOS, **read-only**.
   Lists existing APFS local snapshots via `tmutil listlocalsnapshots /`.
   Does NOT create snapshots — local snapshots are auto-managed by APFS;
   user-initiated backups go through System Settings. Operator-facing surface:
   pre-apply safety check ("there are N existing snapshots, you can roll back
   to ~Xh ago").

After this milestone:
- `python -m ascendo run --category softwareupdate --phase check` enumerates
  pending macOS OS updates on real hardware.
- `MacOSAdapter.snapshot()` returns a non-None instance whose `list()` reports
  the local APFS snapshots.
- `MacOSAdapter.capabilities` flips to
  `PACKAGE_MANAGEMENT | ELEVATION | INVENTORY | SNAPSHOTS`.

Tag exit bar: `v0.0.11-alpha` after Stage 10 (softwareupdate read-only) +
Stage 11 (Time Machine list) green in `bin/validate-macos.sh`.

## 2. Architecture

Mirrors M5.2 + M5.3 patterns exactly. Layers:

- **Layer 4 (core)**: one enum line — `SourceType.SOFTWAREUPDATE = "softwareupdate"`.
  Sidecar JSON Schema regenerated.
- **Layer 5 (Python adapters)**:
  - `adapters/macos/ascendo_macos/managers/softwareupdate.py` (~120 LOC)
    inherits from `BrewManager`-style template, takes `MacElevation` for
    SUDO_ASKPASS injection on Phase.APPLY (mirrors `MasManager`). 5-phase
    SCRIPT_BY_PHASE dispatch.
  - `adapters/macos/ascendo_macos/snapshot.py` (~100 LOC) implements
    `ISnapshot`. `is_available()` checks `tmutil` on PATH. `list()` shells
    out to `scripts/snapshot/list.sh`. `create()` raises `SnapshotError`
    with a clear message about APFS auto-management. `get(snapshot_id)`
    parses metadata from the snapshot ID (timestamp embedded in name).
- **Layer 6 (bash scripts)**:
  - `adapters/macos/scripts/softwareupdate/{check,plan,apply,verify,cleanup}.sh`
    (~250 LOC across 5 files).
  - `adapters/macos/scripts/snapshot/list.sh` (~80 LOC) — `tmutil listlocalsnapshots /`,
    parse timestamps, emit one Item per snapshot.
- **Adapter wire-up**:
  - `MacOSAdapter.capabilities` adds `SNAPSHOTS`.
  - `MacOSAdapter.package_managers()` extends to
    `[BrewManager, MasManager, SoftwareUpdateManager]` (softwareupdate last
    because it's the most disruptive — apply may reboot the Mac).
  - `MacOSAdapter.snapshot()` returns lazy-init cached `TimeMachineSnapshot`.
  - `MacOSAdapter.health_check()` adds `softwareupdate` + `tmutil` components.

**No new dashboard work.** The `/runs` flow handles the new manager. A
follow-up `/snapshots` endpoint is deferred to a later milestone unless the
dashboard team requests it during M5.4 review.

**No orchestrator change.** Snapshot integration with the orchestrator's
"snapshot-before-apply" pre-flight is deferred — that's a cross-cutting
change touching `run_phases()` and is bigger than M5.4. For now, snapshot is
read-only-only: dashboard reads, dashboard displays, operator decides.

## 3. Capability flag

`MacOSAdapter.capabilities` flips from
`PACKAGE_MANAGEMENT | ELEVATION | INVENTORY` (M5.3 state) to
`PACKAGE_MANAGEMENT | ELEVATION | INVENTORY | SNAPSHOTS`.

## 4. SoftwareUpdateManager — flag semantics

The legacy `update_system.sh` from `/Users/mk/Dev_Env/Aktualizacje_MAC/`
contains a battle-tested comment block (in Polish, capitalized as **WAŻNE**):
the `-R` flag is mandatory because it sets the boot metadata via macOS's
proper update mechanism. A raw `sudo reboot` instead of `-R` would skip the
update step on restart. **The bash script MUST always pass `-R` on apply.**

Default invocation: `sudo -A softwareupdate -ir -R --verbose`
- `-i` = install
- `-r` = recommended-only (skips Suggested-but-not-Recommended like major
  upgrade prompts that aren't required)
- `-R` = restart after install (mandatory boot-metadata flag)
- `--verbose` = log progress

Operator opt-in for non-recommended: `--all` flag on apply.sh translates to
`sudo -A softwareupdate -ia -R --verbose` (`-a` = all, including
non-recommended). For major version upgrades (Sonoma → Sequoia), use
`--filter "macOS <NAME>"` style — same precedent as `mas/apply.sh`'s
per-id filter.

**Reboot semantics**: apply.sh emits its `success` sidecar BEFORE invoking
`sudo softwareupdate` so the sidecar survives the reboot. After invocation,
if `softwareupdate` reports any item with `Action: restart`, the script
exits 75 (NEEDS_REBOOT per `docs/agents/contract.md`). On the next boot,
operator runs `python -m ascendo run --category softwareupdate --phase verify`
which re-runs `softwareupdate -l` and confirms the previously-pending items
no longer appear. Apply may reboot mid-run — that's expected and documented.

## 5. `softwareupdate -l` parser

Output format (real example from macOS 14+):

```
Software Update Tool

Finding available software
Software Update found the following new or updated software:
* Label: Safari17.4-17.4
	Title: Safari, Version: 17.4, Size: 87651K, Recommended: YES,
* Label: macOS Sonoma 14.7-23H311
	Title: macOS Sonoma 14.7, Version: 14.7, Size: 5.2G, Recommended: YES, Action: restart,
```

Or when nothing is pending:

```
Software Update Tool

Finding available software
No new software available.
```

Parser rules (sed/awk, Bash 3.2 safe):

1. `* Label: <text>` line marks a new item; `<text>` is the canonical update
   label (item.id).
2. The next indented `Title:` line carries comma-separated key-value pairs.
   Parse out:
   - `Title` → display name
   - `Version` → item.current_version (the available version, no installed
     version available from this CLI; current_version doubles as "available
     version" for OS updates because there is no per-package "installed
     version" surface)
   - `Size` → metadata only (not in sidecar — saves space)
   - `Recommended: YES|NO`
   - `Action: restart` (optional) → triggers `needs_reboot` flag
3. "No new software available." → emit zero items, status=success.
4. Any other shape → emit one error message + status=failed (defensive
   against format drift across macOS versions).

**Test fixtures** (3 files in `adapters/macos/tests/fixtures/softwareupdate/`):
- `no-updates.txt` — clean "no new software" case
- `incremental-updates.txt` — Safari + XProtect updates, no restart needed
- `restart-required.txt` — macOS point release with `Action: restart`

These fixtures are sourced from real `softwareupdate -l` output captured on
macOS 14.x and 15.x. README in the fixtures dir documents format-drift risk
and the expected response (capture a new fixture + add a parser case).

## 6. TimeMachineSnapshot — scope

`is_available(host)`:
- True if `tmutil` is on PATH (built-in macOS — should always be true).
- False on Linux/Windows hosts (host gate).

`list(host)` → `list[SnapshotInfo]`:
- Shells out to `scripts/snapshot/list.sh`, which runs
  `tmutil listlocalsnapshots /`.
- Each line is of the form
  `com.apple.TimeMachine.YYYY-MM-DD-HHMMSS.local`.
- Parse the timestamp into ISO-8601, set `id = full snapshot name`,
  `created_at = parsed timestamp`, `backend = "time_machine"`,
  `size_bytes = None` (not exposed by tmutil), `label = None`.
- Returns the list newest-first.

`create(host, *, label, notes=None)` → raises `SnapshotError`:
```python
raise SnapshotError(
    "macOS local snapshots are auto-managed by APFS. "
    "To configure backups: System Settings > General > Time Machine. "
    "Ascendo cannot create local snapshots on demand."
)
```

`get(host, snapshot_id)` → `SnapshotInfo | None`:
- Calls `list()` and filters by id (no separate `tmutil` query — list is
  cheap, ~10ms on a typical Mac).

**Permissions**: `tmutil listlocalsnapshots /` runs unprivileged with no TCC
prompts. **`tmutil latestbackup` is intentionally NOT used** — real-Mac
probing showed it errors with "Failed to mount destination" even on
properly-configured Macs unless TCC permissions for the calling binary are
granted. Local snapshots are sufficient for the M5.4 use case (operator-facing
"how recent are your auto-snapshots").

## 7. Health check additions

`MacOSAdapter._softwareupdate_status()`:
- `shutil.which("softwareupdate")` — should always resolve at
  `/usr/sbin/softwareupdate` on macOS.
- `subprocess.run([path, "--help"], timeout=5)` — confirm it executes.
- Return `"ok"` on success, `"unavailable: softwareupdate not found"`
  if missing, `"error: <msg>"` on subprocess failure.

`MacOSAdapter._tmutil_status()`:
- Same shape. Confirms `tmutil` is callable. Doesn't probe local snapshots
  (that's TimeMachineSnapshot's job, not health-check overhead).

Both are added to `health_check()` after the existing `system_profiler`
component. The health-check key list grows from 7 to 9 components.

## 8. Per-app metadata captured in softwareupdate sidecars

Each `Item` from check/plan carries:

| Field | Source |
|---|---|
| `id` | `Label:` value (e.g. `Safari17.4-17.4`, `macOS Sonoma 14.7-23H311`) |
| `name` | `Title:` value (human-readable) |
| `current_version` | Available version per `Version:` (the OS reports only "available"; there is no installed-version surface for system updates) |
| `target_version` | Same as current_version (singular value from `softwareupdate`) |
| `status` | `planned` for check/plan; `success`/`failed` for apply/verify |
| `source.type` | `SourceType.SOFTWAREUPDATE` |
| `evidence.binary_version` | The `Version:` parsed value |

Apply phase additionally surfaces `needs_reboot=True` on the sidecar's
top-level when any item carried `Action: restart`. The bash script returns
exit 75 in this case (per `docs/agents/contract.md`).

## 9. Tests target

| Test file | Count |
|---|---|
| `adapters/macos/tests/test_softwareupdate_manager_smoke.py` | ~14 (mock-based: identity, OS gate, sw-update missing, parametrized 5-phase argv dispatch, SUDO_ASKPASS injection on APPLY only, ManagerError on missing sidecar) |
| `adapters/macos/tests/test_softwareupdate_phase_scripts.py` | ~6 (fake-softwareupdate binary fed canned fixtures: no-updates / incremental / restart-required / sign-in-not-applicable / verbose-flag / dry-run-emits-planned) |
| `adapters/macos/tests/test_macos_snapshot_smoke.py` | ~6 (mock-based: backend slug, is_available, list returns SnapshotInfo with parsed timestamp, get filters by id, create raises SnapshotError, non-macOS host returns []) |
| `adapters/macos/tests/test_snapshot_list_script.py` | ~4 (fake-tmutil: list parses timestamps correctly, empty list returns zero items, malformed snapshot name skipped, exit-30 on tmutil failure) |
| `adapters/macos/tests/test_adapter_smoke.py` | +4 wiring tests (capabilities includes SNAPSHOTS, package_managers includes SoftwareUpdateManager, snapshot() returns TimeMachineSnapshot singleton, health_check includes softwareupdate + tmutil) |
| `tests/contract/test_sidecar_v1.py` | +1 (`SourceType.SOFTWAREUPDATE` exists) |

Total: **~35 new tests** + Stage 10 (4 sub-steps) + Stage 11 (2 sub-steps) e2e.

## 10. validate-macos.sh Stage 10 + Stage 11

**Stage 10 — softwareupdate read-only on real Mac**:
- 10.1 doctor reports `softwareupdate` component
- 10.2 `softwareupdate check` phase end-to-end (sidecar emitted, status=success
  whether updates are pending or not)
- 10.3 `softwareupdate plan` phase end-to-end (same)
- 10.4 `softwareupdate verify` phase soft-no-op when no apply sidecar
  exists
- 10.5 `softwareupdate cleanup` phase no-op
- 10.6 `softwareupdate apply --dry-run` emits planned items, NEVER invokes
  `sudo softwareupdate` (test asserts via captured fake-sudo log NOT being
  populated)

NOTE: real apply during a tag run is **forbidden** — softwareupdate apply
reboots the Mac, breaking the validate flow. Operator runs real apply
manually after tag, separately.

**Stage 11 — Time Machine read-only**:
- 11.1 doctor reports `tmutil` component
- 11.2 `MacOSAdapter.snapshot().list(host)` returns ≥0 SnapshotInfo entries
  (≥0 because a fresh-install Mac may have no local snapshots yet; we don't
  enforce a lower bound). Asserts no exception, return type, parsed timestamp
  shape.

## 11. Threat model deltas

`softwareupdate` is a built-in macOS binary, no signing risk. We invoke it
with explicit argv (no shell-string concatenation). Apply phase uses
`sudo -A` (askpass) to elevate via `MacElevation` (the same path mas uses for
CVE-2025-43411 mitigation, even though softwareupdate has no documented
equivalent CVE — uniform argv-only sudo is the project standard).

`tmutil` runs unprivileged for `listlocalsnapshots`. No new T1-T7 surface.

## 12. What's deferred

- **Real apply during validate** — too disruptive (reboots the Mac). Apply
  is operator-driven post-tag.
- **Snapshot creation** — APFS local snapshots are auto-managed; offering
  `create()` would mislead operators. Backups via System Settings.
- **`tmutil latestbackup`** — TCC permissions require Full Disk Access on
  the calling binary; opaque failure mode. Skip in M5.4; revisit if
  operators request.
- **Snapshot integration with orchestrator pre-apply** — touches
  `run_phases()` cross-cutting; bigger than M5.4. For now, snapshot is
  read-only display.
- **Major-version upgrade automation** — `softwareupdate --filter "macOS Sequoia"`
  works but reboots and may take 30+ minutes. M5.4 documents the path;
  operators run it manually when they want a major upgrade.
- **`softwareupdate-management.sh`-style preferences gating** — Apple's MDM
  surface for software update controls. Out of scope.

## 13. Tag exit bar

`v0.0.11-alpha` after:
- All ~35 new tests green
- `bin/validate-macos.sh` Stage 10 + Stage 11 green on Mac.r12.home
- `MacOSAdapter.capabilities` enumerates SNAPSHOTS
- `MacOSAdapter.snapshot().list(host)` returns the ≥9 local snapshots
  currently on Mac.r12.home
- Real-Mac smoke `python -m ascendo run --category softwareupdate --phase check`
  produces a clean sidecar (whether updates are pending or not)

## 14. Spec + plan references

- This spec: `docs/superpowers/specs/2026-05-04-macos-softwareupdate-snapshot-design.md`
- Plan (next): `docs/superpowers/plans/2026-05-04-macos-softwareupdate-snapshot.md`
- M5.3 spec for comparison: `docs/superpowers/specs/2026-05-04-macos-inventory-launchservices-design.md`
- M5.2 spec for the elevation pattern: `docs/superpowers/specs/2026-05-03-macos-mas-elevation-design.md`
- Legacy `update_system.sh` for the `-R` flag wisdom:
  `/Users/mk/Dev_Env/Aktualizacje_MAC/update_system.sh`
