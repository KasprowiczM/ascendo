# macOS adapter — M5.1 brew MVP design

> **Status:** approved 2026-05-03
> **Scope:** M5.1 only — brew × 5 phases, end-to-end on real Mac hardware
> **Target tag:** `v0.0.8-alpha`
> **Estimated effort:** ~5 days, single-dev
> **Reference:** legacy `/Users/mk/Dev_Env/Ascendo/`, Windows adapter `adapters/windows/`

---

## §1 — Goal + scope

Ship `python -m ascendo run --category brew --phase {check|plan|apply|verify|cleanup}` working end-to-end on this MacBook. One real outdated brew package gets upgraded. Tag `v0.0.8-alpha` once `bin/validate-macos.sh` prints `ALL CHECKS PASSED` and a real apply succeeds.

Parity target: Windows v0.0.7-alpha — same green-test bar, same tag pattern, same architectural shape (Layer 4 core unchanged, Layer 5 Python adapter wraps Layer 6 native scripts, JSON sidecar at every boundary).

**Out of scope for M5.1**: `mas`, `softwareupdate`, `LaunchServicesInventory`, Time Machine snapshots, launchd scheduler, `MacElevation` interface, frontend changes. The existing SPA Categories tab will pick up `brew` automatically once `MacOSAdapter.package_managers()` reports it; no UI work required for M5.1.

**Future milestones (roadmap only, separate specs):**
- M5.2 — `mas` manager + `MacElevation` (sudo askpass cache)
- M5.3 — `LaunchServicesInventory` + `INVENTORY` capability
- M5.4 — `softwareupdate` manager (the `-R` rule) + Time Machine read-only `ISnapshot`
- M5.5 — `launchd` `IScheduler`
- M5.6 — Tag `v0.2.0` (full M5 done)

---

## §2 — Directory layout

```
adapters/macos/
├── pyproject.toml                              (already exists — M1.4)
├── README.md                                   NEW — analog of adapters/windows/README.md
├── ascendo_macos/
│   ├── __init__.py                             exposes MacOSAdapter
│   ├── adapter.py                              MacOSAdapter(IAdapter)
│   └── managers/
│       ├── __init__.py
│       └── brew.py                             BrewManager(IPackageManager)
├── lib/
│   ├── _json_emit.py                           ported from Ascendo/lib/_json_emit.py
│   │                                           schema repointed: ascendo/v1 → ascendo/v1
│   ├── ascendo_json.sh                         bash wrapper (analog of AscendoJson.psm1)
│   └── ascendo_brew.sh                         brew helpers (analog of AscendoWinget.psm1):
│                                               resolve_brew_prefix, brew_outdated_json,
│                                               brew_info_json, kill_cask_apps, exit-code map
└── scripts/
    └── brew/
        ├── check.sh                            read-only: brew outdated --json=v2 → sidecar
        ├── plan.sh                             side-effect-free upgrade list (no `brew update`)
        ├── apply.sh                            the only mutating script; --dry-run + --filter supported
        ├── verify.sh                           post-apply re-check vs sibling apply__brew.json
        └── cleanup.sh                          `brew cleanup -s` + log retention prune (60 days)

adapters/macos/tests/
├── conftest.py                                 mirrors adapters/windows/tests/conftest.py
├── test_brew_manager_smoke.py                  ~12 mock-based tests (no real brew)
└── fixtures/
    ├── brew-outdated.json                      captured live output from `brew outdated --json=v2`
    └── brew-info-formula.json                  one example for parser tests

bin/
├── install-dev-macos.sh                        NEW — analog of bin/install-dev.ps1
├── validate-macos.sh                           NEW — analog of bin/validate-windows.ps1
└── run-tag-release-macos.sh                    NEW — analog of bin/run-tag-release.ps1
```

---

## §3 — `BrewManager` Python adapter (Layer 5)

Mirrors `WingetManager` exactly. `IPackageManager` impl. Key surface:

```python
# adapters/macos/ascendo_macos/managers/brew.py

class BrewManager(IPackageManager):
    SOURCE_TYPE = SourceType.BREW
    SCRIPTS_DIR = Path(__file__).parents[2] / "scripts" / "brew"
    LIB_DIR     = Path(__file__).parents[2] / "lib"
    SCRIPT_BY_PHASE = {
        Phase.CHECK:   SCRIPTS_DIR / "check.sh",
        Phase.PLAN:    SCRIPTS_DIR / "plan.sh",
        Phase.APPLY:   SCRIPTS_DIR / "apply.sh",
        Phase.VERIFY:  SCRIPTS_DIR / "verify.sh",
        Phase.CLEANUP: SCRIPTS_DIR / "cleanup.sh",
    }

    def is_available(self, host: HostInfo) -> bool:
        if host.os is not OperatingSystem.MACOS:
            return False
        return shutil.which("brew") is not None and shutil.which("jq") is not None

    def run_phase(self, phase, run, host, *, item_filter=None) -> Sidecar:
        argv = self._build_argv(phase, run, host, item_filter)
        # spawns: bash <script.sh> --run-id ... --trigger ... --profile ...
        #                          --output-dir ... [--dry-run] [--filter csv]
        # reads:  <output_dir>/<run-id>/<phase>__brew.json via sidecar_io.read_sidecar (M2.4)
        ...
```

`_build_argv`: bash invocation, argv-only (T4 mitigation per ADR-0005 — no shell strings).
`--dry-run` is **presence-based** (the Sesja 9 lesson — Python conditionally appends the
flag when `run.dry_run=True`, never as a value).
`--filter` is a comma-joined token list of brew formula/cask IDs.

Sidecar produced by the bash script gets parsed back through `parse_sidecar()` so legacy
+ canonical schemas both work. `ManagerError` raised on non-zero exit AND missing/corrupted
sidecar. Same contract as Windows `WingetManager`.

`MacOSAdapter`:
- `capabilities = AdapterCapability.PACKAGE_MANAGEMENT` (E1 — minimum viable)
- `package_managers()` returns `[BrewManager()]`
- `health_check()` reports `brew`, `jq`, `bash`, `python3`, `ascendo_macos_lib`
- `inventory()`, `snapshot()`, `scheduler()`, `elevation()`, `source()` all return `None`

---

## §4 — Layer 6 native scripts (Bash)

Each phase script ~150 LOC, driven by `lib/ascendo_json.sh` + `lib/ascendo_brew.sh` so the
bulk lives in libs (analog of `AscendoJson.psm1` + `AscendoWinget.psm1` on Windows).

Skeleton:

```bash
#!/usr/bin/env bash
# adapters/macos/scripts/brew/check.sh
set -o pipefail                                 # NOT set -e (per Ascendo rule #6)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"     # no hardcoded paths (rule #5)
ADAPTER_LIB="$SCRIPT_DIR/../../lib"
. "$ADAPTER_LIB/ascendo_json.sh"                # exports json_init/json_add_item/json_save
. "$ADAPTER_LIB/ascendo_brew.sh"                # exports brew_outdated_json/...

parse_args "$@"   # --run-id --trigger --profile --output-dir [--dry-run] [--filter csv]

json_init "ascendo/v1" "check" "brew" \
          "$RUN_ID" "$TRIGGER" "$PROFILE_NAME" \
          "brew" "$(brew --version | head -n1)"
trap 'json_save_on_exit "$OUTPUT_DIR" "$RUN_ID" "$PHASE" "brew"' EXIT

# Real work
outdated_json="$(brew_outdated_json)" || { json_add_message err "brew outdated failed"; exit 30; }
echo "$outdated_json" | jq -c '.formulae[],.casks[]' | while IFS= read -r line; do
    id=$(jq -r '.name // .token' <<<"$line")
    cur=$(jq -r '.installed_versions[0] // .installed_versions' <<<"$line")
    tgt=$(jq -r '.current_version // .current_versions[0]' <<<"$line")
    json_add_item "$id" "$cur" "$tgt" "planned" "brew"
done
# Then walk `brew list` for non-outdated installed packages → status=up_to_date
```

### Critical rules from `Ascendo/CLAUDE.md` enforced throughout

1. ✅ `set -o pipefail` (NOT `set -e` — orchestrator runs every step even on partial failure)
2. ✅ Bash 3.2 only — no `declare -A`, no `mapfile`, no `readarray`
3. ✅ No hardcoded paths — `SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"`
4. ✅ Temp files: `mktemp -d "${TMPDIR:-/tmp}/ascendo_brew_XXXXXX"` (never bare `/tmp/`)
5. ⏭️ `softwareupdate -R` rule — N/A here (M5.1 is brew, not OS updates) — reserved for M5.4
6. ⏭️ `mas upgrade` sudo (CVE-2025-43411) — N/A here — reserved for M5.2

### `apply.sh` — `kill_cask_apps` (the macOS hidden gem)

Ports the `osascript -e 'tell application "X" to quit'` graceful-quit pattern from
`Ascendo/update_brew.sh`. 5-second wait → fallback `pkill -f "/Applications/X.app/"`.

Cask-name → app-bundle-name map lives in `ascendo_brew.sh` as a **case statement** (Bash 3.2 —
no associative arrays). Initial entries: Slack, VSCode (`Visual Studio Code.app`), Chrome
(`Google Chrome.app`), Firefox, Spotify, Notion. Extensible.

### `verify.sh`

Reads `<run-id>/apply__brew.json` (sibling sidecar), re-runs `brew outdated --json=v2`.
Items that resolved to expected version → `success`, mismatch/still-outdated → `failed`.
Same model as Windows `winget verify`. Soft no-op if apply sidecar missing (verify can run
after check-only — no crash).

### `cleanup.sh`

`brew cleanup -s` (formulae + casks + downloads). 60-day log retention prune (port from
`Ascendo` `clean_logs.sh`). DryRun mode emits `planned` items instead of deleting
— per-file deletion items for audit trail.

---

## §5 — JSON sidecar emitter

Port `Ascendo/lib/_json_emit.py` and `Ascendo/lib/json.sh` into
`adapters/macos/lib/`, with three changes:

1. **Schema flip.** Every emitted sidecar uses `"schema": "ascendo/v1"`
   (was `"ascendo/v1"` in legacy). The Pydantic `parse_sidecar()` in
   `core/ascendo/models/legacy.py` accepts both, so this is forward-compatible.
2. **Field rename to match `ascendo/v1`.**

   | Legacy field | New field |
   |---|---|
   | `kind` | `phase` |
   | bare `host` string | `HostInfo` object (hostname, os, os_version, arch, user, is_elevated, elevation_method) |
   | `summary.{ok,warn,err}` | `summary.{success,skipped,failed}` |
   | `items[].{from,to,result}` | `items[].{current_version,target_version,status}` |

   Most of this is already aligned in `_json_emit.py`; the port mostly renames constructor args.
3. **Atomic write retained.** Already done in legacy (`tempfile + os.replace`). Cross-OS
   locking from `core/ascendo/orchestrator/sidecar_io.py` (M2.4) handles the read side —
   emitter just does atomic write.

### Bash function name mapping

| Windows (`AscendoJson.psm1`) | macOS (`ascendo_json.sh`) | Purpose |
|---|---|---|
| `New-Sidecar` | `json_init` | Initialize sidecar (run_id, trigger, profile, phase, category, tool, host) |
| `Add-SidecarItem` | `json_add_item` | Append item: id/current/target/status/source |
| `Add-SidecarMessage` | `json_add_message` | Append message line (level + text) |
| `Save-Sidecar` | `json_save` | Atomic write to `<output_dir>/<run-id>/<phase>__<category>.json` |
| `Get-AscendoHostInfo` | `host_info_json` | Snapshot host (`sw_vers` + `uname` + `whoami`) |

The bash functions are thin shims that maintain a `$JSON_PAYLOAD` variable as a JSON string
and delegate complex transforms to `python3 -m _json_emit ...` calls — same pattern as
`Ascendo/lib/json.sh`. State carrying between bash and python is the JSON file
itself: bash invokes `python3 _json_emit.py append-item --file "$JSON_TMP" --id ... --status ...`,
python reads/mutates/writes the file, bash continues. Avoids needing a Python long-running
daemon. Bash 3.2-safe throughout.

---

## §6 — `bin/` cross-platform launchers

Three new shell scripts mirroring the Windows trio:

| Script | macOS analog | Behavior |
|---|---|---|
| `bin/install-dev.ps1` | `bin/install-dev-macos.sh` | Installs `core/` + `adapters/macos/` editable, plus runtime deps (`fastapi uvicorn[standard] httpx`), system deps (`brew install jq` if missing). Auto-runs `validate-macos.sh` at end unless `--skip-validate`. Idempotent — safe after `git pull`. |
| `bin/validate-windows.ps1` | `bin/validate-macos.sh` | End-to-end smoke: `python -m ascendo --help` / `version` / `doctor` → all 5 brew phases (`apply` with `--dry-run`) → dashboard sync + async + SSE. Exits 0 only on `ALL CHECKS PASSED`. ~90s. |
| `bin/run-tag-release.ps1` | `bin/run-tag-release-macos.sh` | Tag-release one-liner: preflight → `brew plan` → confirmation gate (literal `apply`) → real `brew apply` → verify → cleanup → doctor → `git tag -a v0.0.8-alpha`. Flags: `--what-if`, `--no-tag`, `--no-snapshot` (no-op M5.1). Doesn't push the tag. |

**Snapshot step omitted for M5.1.** `MacOSAdapter.snapshot()` returns `None`; the tag-release
script warns `[no snapshot — Time Machine integration in M5.4]` and proceeds. Mirrors the
Windows behavior before M3.12 landed.

**Sudo for `apply`.** If a cask write to `/Applications` needs root, the script prompts via
terminal (standard `sudo`). M5.1 doesn't yet support dashboard-driven sudo (Linux uses askpass
cache; we'll port that into `MacElevation` in M5.2). Documented limitation.

---

## §7 — Test plan

### Unit tests (`adapters/macos/tests/test_brew_manager_smoke.py`)

~12–15 mock-based tests, runs on any OS in CI:

| # | Test | Asserts |
|---|---|---|
| 1 | `is_available()` False on Linux/Windows | OS gate enforced |
| 2 | `is_available()` False when `brew` missing | `shutil.which` mock |
| 3 | `is_available()` False when `jq` missing | `shutil.which` mock |
| 4 | `is_available()` True on macOS with brew + jq | both present |
| 5–9 | `run_phase` dispatches correct script per phase (parametrized × 5 phases) | argv contains `check.sh`/`plan.sh`/etc. |
| 10 | `run_phase` passes `--dry-run` when `run.dry_run=True` (presence-based) | argv assertion — Sesja 9 lesson |
| 11 | `run_phase` does NOT pass `--dry-run` when `run.dry_run=False` | argv assertion |
| 12 | `run_phase` passes `--filter id1,id2,id3` for item_filter | argv assertion |
| 13 | `run_phase` raises `ManagerError` when bash exits non-zero AND no sidecar produced | error path |
| 14 | `run_phase` parses fixture sidecar through `parse_sidecar()` round-trip | schema compliance |
| 15 | `MacOSAdapter` declares `PACKAGE_MANAGEMENT` only, all other accessors return `None` | wiring assertion |

Plus 2 contract tests in `tests/contract/test_brew_sidecar_v1.py` round-tripping the captured
`brew-outdated.json` fixture through `parse_sidecar()` to catch schema drift.

### Real-hardware tests (`bin/validate-macos.sh`, runs only on this Mac)

1. CLI: `python -m ascendo --help / version / doctor` — exit 0
2. `python -m ascendo run --category brew --phase check` — sidecar lands at correct path with `schema=ascendo/v1`, `phase=check`, `category=brew`, `summary.total>0`
3. Same for `plan`
4. `apply --dry-run=True` — items have `status=planned`, no real upgrades
5. `verify` — soft no-op without real apply, doesn't crash
6. `cleanup --dry-run=True` — emits planned deletions, no real deletes
7. Dashboard: start in background, hit `/version` + `/health` + `POST /runs/async` + poll `/runs/{id}/status` to completed
8. Frontend: `colors_and_type.css` mounted, brand SVGs round-trip (already covered by `tests/contract/test_dashboard_spa.py`)

Final line: `ALL CHECKS PASSED.` ← exit 0.

`bin/run-tag-release-macos.sh` is the *separate* harness for the **real apply** that
produces the tag — that one mutates the system and requires interactive confirmation.

---

## §8 — Sequenced milestone breakdown (M5.1.x)

Mirrors Windows M3.1–M3.7 sequence. Each step independently committable; each leaves the tree green.

| # | Step | Files | Est. | Windows analog |
|---|---|---|---|---|
| **M5.1.1** | JSON emitter — `_json_emit.py` + `ascendo_json.sh` ported, schema flipped to `ascendo/v1`, round-trips through `parse_sidecar()` | `adapters/macos/lib/{_json_emit.py,ascendo_json.sh}` | ½ d | M3.1 |
| **M5.1.2** | Brew helpers — `ascendo_brew.sh` with `brew_outdated_json` / `brew_info_json` / `kill_cask_apps` / cask-app-name map / exit-code mapping | `adapters/macos/lib/ascendo_brew.sh` | ½ d | M3.2 |
| **M5.1.3** | `check.sh` — read-only inventory phase, sidecar at `<run-id>/check__brew.json` | `adapters/macos/scripts/brew/check.sh` | ½ d | M3.3 |
| **M5.1.4** | Python adapter — `MacOSAdapter` + `BrewManager` + 12-15 mock smoke tests | `adapters/macos/ascendo_macos/{__init__,adapter}.py`, `managers/brew.py`, `tests/test_brew_manager_smoke.py` + fixtures | 1 d | M3.4 |
| **M5.1.5** | Cross-module integration — `adapter_factory.select_adapter(MACOS)` returns `MacOSAdapter`, `is_available()` matrix correct, all 12+ tests green, dashboard `/version` reports `adapter=macos tier=1` | wiring + verify `core/ascendo/adapter_factory/__init__.py` | ½ d | M3.5 |
| **M5.1.6** | `apply.sh` — first mutation. `kill_cask_apps` integrated, `--dry-run` emits `planned`, real path runs `brew upgrade --formula` and `brew upgrade --cask`, exit-code map | `adapters/macos/scripts/brew/apply.sh` | 1 d | M3.6 |
| **M5.1.7** | `plan.sh` + `verify.sh` + `cleanup.sh` — read-only triplet completing the 5-phase contract | three `.sh` files | ½ d | M3.7 |
| **M5.1.8** | `bin/install-dev-macos.sh` + `bin/validate-macos.sh` + `bin/run-tag-release-macos.sh` | three new shell scripts | ½ d | install/validate/tag-release trio |
| **M5.1.9** | Real-hardware validation on this Mac — `validate-macos.sh` exits 0 with `ALL CHECKS PASSED.`; `run-tag-release-macos.sh` does one real `brew upgrade`; `git tag -a v0.0.8-alpha` | (no code) | ½ d | M3.16 |
| **M5.1.10** | Merge `claude/quizzical-sanderson-6a5664` → `main`, push tag, update `HANDOFF.md` + `PLAN.md` | docs + git | ¼ d | always-last |

**Total: ~5 days**, with steps 1–4 sequentially blocking and 6–9 sequentially blocking.
Steps 1+2 can parallelize via two subagents (independent files, no dep).

---

## §9 — Decisions log (questions answered during brainstorm)

| Q | Decision | Why |
|---|---|---|
| MVP scope | Mirror Windows M3.1–M3.7 — brew first, all 5 phases | Lowest-risk source on Mac (no sudo for user installs), proven pattern from Windows |
| MVP done bar | Full 5 phases + one real `brew upgrade` on this Mac | Mirrors v0.0.7-alpha tag pattern; `brew outdated` always non-empty on a dev Mac |
| Sidecar emitter | Hybrid Bash + Python helper (B3) | Matches Linux adapter pattern; cross-platform consistency comes from shared *contract* (schema + 5-phase + IPackageManager), not shared code |
| Brew CLI parsing | `brew outdated --json=v2` + `jq` (C1) | Stable machine interface since Homebrew 2.5; `jq` is one `brew install jq` away |
| Category split | Single `brew` covering formulae + casks (D1) | One `brew outdated --json=v2` envelope returns both; splitting locks artificial boundary |
| Capability declared | `PACKAGE_MANAGEMENT` only (E1) | Smallest viable wiring; INVENTORY/ELEVATION land in M5.2/M5.3 once brew round-trip is proven |
| Test strategy | Mock-based unit tests + `bin/validate-macos.sh` for real (F3) | Mirrors Windows; CI-portable; same harness pattern user already knows |

---

## §10 — Appendix: cross-platform contract restatement

This spec assumes (and depends on) the following pre-existing shared contracts. **None are
modified** by M5.1:

- **`ascendo/v1` JSON sidecar schema** — `core/ascendo/models/sidecar.py`. Unchanged.
- **5-phase `Phase` enum** — `core/ascendo/models/run.py`. Unchanged.
- **`IPackageManager` interface** — `core/ascendo/interfaces/package_manager.py`. Unchanged.
- **`IAdapter` aggregate interface** — `core/ascendo/interfaces/adapter.py`. Unchanged.
- **`AdapterCapability.PACKAGE_MANAGEMENT` flag** — `core/ascendo/interfaces/adapter.py`. Unchanged.
- **`SourceType.BREW` enum value** — `core/ascendo/models/package.py`. Verify exists; add if missing (one-line change in M5.1.4).
- **`OperatingSystem.MACOS` enum value** — `core/ascendo/models/host.py`. Already exists.
- **`adapter_factory.detect_os()` returns `MACOS` on Darwin** — `core/ascendo/adapter_factory/__init__.py`. Already exists. Verify in M5.1.5.
- **`adapter_factory.AdapterRegistry.discover()` finds `ascendo_macos` via direct-import fallback** — already supported (same pattern Windows uses). Verify in M5.1.5.

The Layer 4 core is OS-agnostic by design; M5.1 adds Layer 5 (`MacOSAdapter` + `BrewManager`)
and Layer 6 (5 bash scripts + 2 lib files) without touching shared code.
