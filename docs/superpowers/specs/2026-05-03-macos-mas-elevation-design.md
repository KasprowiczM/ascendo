# macOS adapter — M5.2 mas + MacElevation design

> **Status:** approved 2026-05-03
> **Scope:** M5.2 only — `mas` × 5 phases + `MacElevation` (askpass cache)
> **Target tag:** `v0.0.9-alpha`
> **Estimated effort:** ~6 days, single-dev
> **References:**
> - M5.1 spec: `docs/superpowers/specs/2026-05-03-macos-brew-mvp-design.md`
> - Legacy mas script: `/Users/mk/Dev_Env/Aktualizacje_MAC/update_appstore.sh`
> - Legacy askpass: `app/backend/sudo.py` (Linux dashboard)
> - Windows elevation parallel: `adapters/windows/ascendo_windows/managers/elevation.py`

---

## §1 — Goal + scope

Ship `python -m ascendo run --category mas --phase {check|plan|apply|verify|cleanup}` end-to-end on this Mac, with one real `sudo mas upgrade` performed via the new `MacElevation` interface, exercised both from the CLI and from the dashboard's `POST /elevation/auth` askpass round-trip. Tag `v0.0.9-alpha` once `bin/validate-macos.sh` passes including the elevation smoke.

Capability flip: `MacOSAdapter.capabilities` becomes `PACKAGE_MANAGEMENT | ELEVATION`. `package_managers()` returns `[BrewManager, MasManager]` (brew first because mas itself is brew-installed). `elevation()` returns the new `MacElevation` instance.

**Out of scope for M5.2** (all reserved for separate milestones):

- Track 2 — AppleScript GUI automation for iPad apps that `mas` can't reach (UniFi, WiFiman, myCANAL, etc.). Operator continues to click "Update All" in App Store manually.
- osascript GUI password dialog (`display dialog with hidden answer`) — only matters when neither TTY nor pre-registered password is available.
- Frontend SPA modal extension to prompt for sudo password when targeting mas — backend endpoints land in M5.2 so the round-trip is `curl`-validated; SPA work is non-blocking.
- `LaunchServicesInventory` (M5.3), `softwareupdate` (M5.4), Time Machine `ISnapshot` (M5.4), `launchd` `IScheduler` (M5.5).

---

## §2 — Directory layout

```
adapters/macos/
├── ascendo_macos/
│   ├── adapter.py                              MODIFIED — capabilities flip; wire MasManager + MacElevation
│   └── managers/
│       ├── mas.py                              NEW — MasManager(IPackageManager)
│       └── elevation.py                        NEW — MacElevation(IElevation)
├── lib/
│   └── ascendo_mas.sh                          NEW — mas_signed_in / mas_list_json / mas_outdated_json
│                                                     / mas_version_at_least / mas_classify_exit
└── scripts/
    └── mas/
        ├── check.sh                            NEW — sign-in probe + outdated/list → sidecar
        ├── plan.sh                             NEW — read-only outdated list
        ├── apply.sh                            NEW — sudo -A mas upgrade [<id>...]; --dry-run + --filter
        ├── verify.sh                           NEW — re-check vs sibling apply__mas.json
        └── cleanup.sh                          NEW — no-op (no caches to prune)

adapters/macos/tests/
├── test_mas_manager_smoke.py                   NEW — ~14 mock-based tests
├── test_elevation_smoke.py                     NEW — ~10 askpass-helper-shape tests
├── test_ascendo_mas_helpers.py                 NEW — bash-side parser tests (fixtures)
└── fixtures/
    ├── mas-list.txt                            captured `mas list` output
    └── mas-outdated.txt                        captured `mas outdated` output

core/ascendo/
├── dashboard/
│   └── routes/
│       └── elevation.py                        NEW — POST /elevation/auth, /invalidate; GET /status
└── models/package.py                           MODIFIED — add SourceType.MAS

docs/architecture/schemas/
└── sidecar.v1.schema.json                      REGENERATED via scripts/export-sidecar-schema.py

bin/
├── validate-macos.sh                           MODIFIED — add Stage 8 (mas + dashboard askpass)
└── run-tag-release-macos.sh                    MODIFIED — --mas flag + tag bump to v0.0.9-alpha
```

`MacElevation` lives under `adapters/macos/` (not `core/`) because it's macOS-specific. The shared `IElevation` ABC stays in `core/ascendo/interfaces/elevation.py` unchanged.

---

## §3 — `MasManager` Python adapter (Layer 5)

Mirrors `BrewManager` exactly. `IPackageManager` impl. Key surface:

```python
# adapters/macos/ascendo_macos/managers/mas.py

class MasManager(IPackageManager):
    SOURCE_TYPE = SourceType.MAS
    SCRIPT_BY_PHASE = {
        Phase.CHECK:   "mas/check.sh",
        Phase.PLAN:    "mas/plan.sh",
        Phase.APPLY:   "mas/apply.sh",
        Phase.VERIFY:  "mas/verify.sh",
        Phase.CLEANUP: "mas/cleanup.sh",
    }
    MIN_MAS_MAJOR = 4
    DEFAULT_TIMEOUT_SEC = 1800

    def __init__(self, *, scripts_dir, lib_dir, elevation, bash_path=None,
                 timeout_sec=DEFAULT_TIMEOUT_SEC):
        self._scripts_dir = Path(scripts_dir)
        self._lib_dir = Path(lib_dir)
        self._elevation = elevation     # IElevation; concrete-only methods accessed for askpass
        self._bash_override = bash_path
        self._timeout_sec = timeout_sec

    @property
    def category(self) -> SourceType:
        return SourceType.MAS

    def is_available(self, host) -> bool:
        if host.os is not OperatingSystem.MACOS: return False
        if shutil.which("mas") is None: return False
        if shutil.which("jq") is None:  return False
        return self._mas_major_at_least(self.MIN_MAS_MAJOR)

    def run_phase(self, phase, run, host, *, item_filter=None) -> Sidecar:
        # Same shape as BrewManager.run_phase:
        #   - argv = bash <script> --run-id ... --trigger ... --profile ...
        #            --output-dir ... [--dry-run] [--filter csv]
        #   - For phase=APPLY ONLY: extend env with SUDO_ASKPASS=<helper-path>
        #     when self._elevation.has_password_registered() is True.
        #     Otherwise no env override — child sudo prompts the controlling TTY.
        #   - reads sidecar at <output_dir>/<run-id>/<phase>__mas.json
        #     via core/ascendo/orchestrator/sidecar_io.read_sidecar (M2.4)
```

Contract differences from `BrewManager`:

1. **`__init__` takes an `IElevation`** dependency. The MasManager doesn't `import MacElevation` — it accepts the interface. Tests inject a fake. `MacOSAdapter` constructs both and passes the elevation instance into `MasManager`.
2. **For `Phase.APPLY` only**, `_build_argv` extends the env with `SUDO_ASKPASS=<askpass-helper-path>` when `elevation.has_password_registered()` is True. When no password is registered, no env override — the child `sudo -A` falls back to TTY prompt (CLI flow).
3. **Version-floor check** in `is_available` — `mas --version` → parse major → reject if < 4. Reason surfaces in `health_check` output: `degraded: mas 3.x found, need >=4 (brew upgrade mas)`.

---

## §4 — `MacElevation` + dashboard wiring

### 4.1 — `MacElevation` (~200 LOC)

```python
# adapters/macos/ascendo_macos/managers/elevation.py

class MacElevation(IElevation):
    """sudo via askpass cache for dashboard, TTY fallback for CLI.

    State is process-local. Threading-safe via _lock.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._password: str | None = None        # held in memory only
        self._askpass_path: Path | None = None
        self._allowlist: frozenset[str] = frozenset()
        atexit.register(self._cleanup_at_exit)

    # --- IElevation surface ---

    @property
    def available_methods(self) -> tuple[ElevationMethod, ...]:
        return (ElevationMethod.SUDO,) if shutil.which("sudo") else ()

    def is_currently_elevated(self, host) -> bool:
        return os.geteuid() == 0

    def register_allowlist(self, allowed_commands: Iterable[str]) -> None:
        # Lowercase basenames. T4 mitigation per ADR-0005.
        self._allowlist = frozenset(Path(c).name.lower() for c in allowed_commands)

    def run(self, host, argv, *, timeout_sec=None, env=None, cwd=None,
            method=None) -> ElevationResult:
        # 1. argv MUST be Sequence[str], not a shell string. Reject otherwise.
        # 2. argv[0] basename MUST be in allowlist. Else ElevationDenied.
        # 3. Build full argv: ["sudo", "-A", *argv] when password registered,
        #    else ["sudo", *argv] (TTY prompt).
        # 4. env: merge parent env, set SUDO_ASKPASS if registered.
        # 5. subprocess.run with timeout. Capture stdout/stderr.
        # 6. Return ElevationResult(exit_code=, stdout=, stderr=,
        #    method=ElevationMethod.SUDO, duration_ms=).

    # --- Extra surface (concrete-only; not in IElevation) ---

    def register_password(self, password: str, *, verify: bool = True,
                          timeout: int = 15) -> tuple[bool, str]:
        # Verify via `sudo -S -p '' -v` (POSIX-portable).
        # On success: store in memory + create askpass helper.
        # Returns (ok, detail).

    def invalidate(self) -> None:
        # Wipe in-memory password, unlink askpass helper, run `sudo -k`. Idempotent.

    def has_password_registered(self) -> bool: ...
    def askpass_path(self) -> Path | None: ...

    # --- Internals ---

    def _create_askpass_helper(self, password: str) -> Path:
        # 0700 file at $TMPDIR/ascendo/askpass-<random>.sh containing:
        #   #!/usr/bin/env bash
        #   printf '%s\n' '<single-quoted password>'
        # Single-quote escape rule: ' -> '\''
```

Lifetime + cleanup:

- Helper created on `register_password()`; unlinked on `invalidate()` and on interpreter exit (`atexit`).
- On hard crash, leftover files are 0700 + in `$TMPDIR/ascendo/`; documented in `docs/agents/security.md`.

### 4.2 — Dashboard endpoints (Layer 3)

`core/ascendo/dashboard/routes/elevation.py`:

| Endpoint | Body | Returns | Behaviour |
|---|---|---|---|
| `POST /elevation/auth` | `{"password": "..."}` | `200 {"ok": true}` or `401 {"detail": "..."}` | Calls `adapter.elevation().register_password()`. 401 on wrong password (no info leak). 503 if adapter has no `IElevation`. |
| `POST /elevation/invalidate` | — | `200 {"ok": true}` | Wipes password + helper. Idempotent. |
| `GET /elevation/status` | — | `200 {"registered": bool, "method": "sudo" \| null}` | For SPA to render a "🔓 sudo cached" pill. |

Mounted only when `app.state.adapter.elevation() is not None` — Windows/Linux dashboards aren't affected. CORS allow-list inherits from `/runs`.

The SPA's apply-confirmation modal (Sesja 13 work) gets a future small extension: when targeting `mas`, also prompt for sudo password if `GET /elevation/status` reports `registered=false`. Submit → `POST /elevation/auth` → if 200, proceed with `POST /runs/async`. **Frontend changes deferred** to a follow-up — backend endpoints land in M5.2 so the round-trip is `curl`-validated.

---

## §5 — Layer 6 native scripts (Bash)

Each phase script ~50–120 LOC, driven by `lib/ascendo_json.sh` (shipped in M5.1) + the new `lib/ascendo_mas.sh`. All scripts: `set -o pipefail` (no `set -e`), Bash 3.2-safe, no hardcoded paths, `mktemp -d "${TMPDIR:-/tmp}/ascendo_mas_XXXXXX"`.

### 5.1 — `lib/ascendo_mas.sh` helpers

```bash
mas_signed_in()        # 0 if `mas list` succeeds with output, 1 otherwise
mas_list_json()        # JSON array of installed apps (id, name, version)
mas_outdated_json()    # JSON array of outdated apps (id, name, current, target)
mas_version_at_least() # 0 if mas major >= $1
mas_classify_exit()    # mas exit code → ascendo item status + suggested rollback
```

Bash 3.2 — `awk` + `read` only; no `declare -A`, no `mapfile`, no `readarray`.

### 5.2 — `check.sh` (~80 LOC)

1. `parse_args` (`--run-id`, `--trigger`, `--profile`, `--output-dir`, `[--dry-run]`, `[--filter]`)
2. `json_init "ascendo/v1" "check" "mas" "$RUN_ID" ... "mas" "$(mas version)"`
3. `trap 'json_save_on_exit "$OUTPUT_DIR" "$RUN_ID" "$PHASE" "mas"' EXIT`
4. Sign-in probe — single `mas_signed_in` call. If not signed in:
   - `json_add_item "mas:not-signed-in" "" "" "failed" "mas"`
   - `json_add_message err "Not signed into Mac App Store. Open App Store.app and sign in."`
   - `exit 0`  (phase status='failed' from item, not exit code)
5. Walk `mas_outdated_json` → `status=planned` items
6. Walk `mas_list_json` minus outdated → `status=up_to_date` items

### 5.3 — `plan.sh` (~50 LOC)

Side-effect-free. Same as check but emits `planned` items only (omits up-to-date). No `mas list` walk — only `mas outdated`. If sign-in probe fails, same fail-fast pattern.

### 5.4 — `apply.sh` (~120 LOC) — the only mutating script

```
1. parse_args
2. json_init ... "apply" "mas" ...
3. If --dry-run: enumerate mas_outdated_json, emit one planned item per app, exit 0.
4. Real apply path:
   - If --filter csv given: per-id loop calling `sudo -A mas upgrade <id>`
     (avoids upgrading apps the user didn't ask for).
   - Else: single `sudo -A mas upgrade` (mas's bulk upgrade).
   - Capture stdout+stderr per invocation.
   - For each line `App Foo (123) → 1.2.3 → 1.2.4`, emit success item.
   - Map sudo's own exit codes: 1 = "no askpass and no TTY" → failed item with
     message "elevation unavailable; run from terminal or POST /elevation/auth".
5. mas's exit codes mapped via mas_classify_exit.
6. Optional `softwareupdate -l` reboot probe omitted (M5.4 territory).
```

`sudo -A` is critical — falls back to TTY prompt when `$SUDO_ASKPASS` unset, uses askpass helper when set. The Python adapter (`MasManager.run_phase`) decides which env to ship; the bash script never inspects `$SUDO_ASKPASS` directly.

### 5.5 — `verify.sh` (~70 LOC)

Reads sibling `<run-id>/apply__mas.json`. Re-runs `mas_outdated_json`. For each apply item with `status=success`: if its id is no longer outdated → verify `success`; if still outdated → verify `failed`. Soft no-op when apply sidecar missing (verify can run after check-only).

### 5.6 — `cleanup.sh` (~30 LOC)

`mas` has no caches to prune. Script emits a `success` sidecar with one info-level message (`mas has no cleanup; no-op completed`) and zero items. Mirrors the contract shape so the orchestrator's per-(phase, category) accounting works uniformly.

### 5.7 — Critical rules carried forward from M5.1

- ✅ `set -o pipefail` (NOT `set -e`)
- ✅ Bash 3.2 only — no `declare -A`, `mapfile`, `readarray`
- ✅ `SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"`
- ✅ Tempfiles via `mktemp -d "${TMPDIR:-/tmp}/ascendo_mas_XXXXXX"`
- ✅ `sudo mas upgrade` rule (CVE-2025-43411) — apply never calls bare `mas upgrade`

---

## §6 — Test plan

### 6.1 — Mock-based unit tests (CI-portable)

**`adapters/macos/tests/test_mas_manager_smoke.py` (~14 tests)**

| # | Test | Asserts |
|---|---|---|
| 1 | `is_available()` False on Linux/Windows | OS gate |
| 2 | `is_available()` False when `mas` missing | `shutil.which` mock |
| 3 | `is_available()` False when `jq` missing | `shutil.which` mock |
| 4 | `is_available()` False when `mas` major < 4 | `mas --version` mock returns `3.x` |
| 5 | `is_available()` True on macOS with mas>=4 + jq | all present |
| 6–10 | `run_phase` dispatches correct script per phase (parametrized × 5) | argv[1] endswith `mas/<phase>.sh` |
| 11 | `run_phase APPLY` exports `SUDO_ASKPASS` when `elevation.has_password_registered()` | env-dict assertion |
| 12 | `run_phase APPLY` does NOT export `SUDO_ASKPASS` when no password registered | env-dict assertion |
| 13 | `run_phase` raises `ManagerError` when bash exits non-zero AND no sidecar produced | error path |
| 14 | `MacOSAdapter` declares `PACKAGE_MANAGEMENT \| ELEVATION`, `package_managers()` includes both Brew + Mas, `elevation()` returns non-None | wiring assertion |

**`adapters/macos/tests/test_elevation_smoke.py` (~10 tests)**

| # | Test | Asserts |
|---|---|---|
| 1 | `register_allowlist` lowercases + basenames | normalisation |
| 2 | `run()` with empty argv raises `ElevationDenied` | guard |
| 3 | `run()` with shell-string argv raises `TypeError` | T4 — argv must be `Sequence[str]` |
| 4 | `run()` with command not in allow-list raises `ElevationDenied` | allow-list enforced |
| 5 | `register_password()` calls `sudo -S -p '' -v` and stores on success | subprocess mock |
| 6 | `register_password()` returns `(False, msg)` on bad password | subprocess returns 1 |
| 7 | `register_password()` creates 0700 helper at `$TMPDIR/ascendo/askpass-*.sh` | stat + content match |
| 8 | helper escapes single quotes correctly (`O'Brien` password round-trips) | content match |
| 9 | `invalidate()` wipes password + unlinks helper + idempotent | state assertion |
| 10 | `available_methods` returns `()` when sudo missing on PATH | `shutil.which` mock |

**`adapters/macos/tests/test_ascendo_mas_helpers.py` (~6 tests)**

Bash-side parser tests using captured fixture output. Same shape as M5.1's `test_ascendo_brew_helpers.py`. Skipped on non-macOS where `mas` isn't installable; CI provides bash + jq + the captured fixtures only.

**`tests/contract/test_dashboard_elevation.py` (~6 tests)**

Auth happy path (200), auth wrong password (401), auth no adapter elevation (503), invalidate idempotent (200), status before/after auth, status with no elevation capability (503).

### 6.2 — Real-hardware tests (`bin/validate-macos.sh`)

New Stage 8 added to existing harness:

```
==> [Stage 8] mas + elevation
  Step 8.1   doctor reports: mas ok, jq ok, brew ok
  Step 8.2   python -m ascendo run --category mas --phase check
             → sidecar at correct path, signed-in items > 0
  Step 8.3   python -m ascendo run --category mas --phase plan        (read-only)
  Step 8.4   python -m ascendo run --category mas --phase apply --dry-run
             → items have status=planned, no real upgrades
  Step 8.5   python -m ascendo run --category mas --phase verify       (soft no-op OK)
  Step 8.6   python -m ascendo run --category mas --phase cleanup
             → success sidecar, zero items, info message present
  Step 8.7   Dashboard askpass round-trip (skipped if $SUDO_PW unset):
             - start dashboard --background
             - GET  /elevation/status  → {"registered": false, "method": "sudo"}
             - POST /elevation/auth    {"password": "$SUDO_PW"}  → 200
             - GET  /elevation/status  → {"registered": true,  "method": "sudo"}
             - POST /runs/async        {"categories":["mas"], "phases":["apply"], "dry_run":true}
             - poll /runs/<id>/status until completed; assert status=success
             - POST /elevation/invalidate → 200
             - GET  /elevation/status  → {"registered": false}
             - stop dashboard
```

The validate script reads `$SUDO_PW` from env (operator sets it once before running). If unset, Step 8.7 is skipped with `[skip]` rather than failing — CI-friendly. **Step 8.7 must complete (not skipped) for the v0.0.9-alpha tag**; the operator running `bin/run-tag-release-macos.sh --mas` must export `$SUDO_PW` first.

### 6.3 — Real apply tag harness (`bin/run-tag-release-macos.sh`)

Mirrors M5.1 harness. New `--mas` flag runs the real `sudo mas upgrade` step between brew and the tag step. If no App Store updates pending, falls back to `sudo mas install <id>` against an already-installed app id. Tag bumps from `v0.0.8-alpha` → `v0.0.9-alpha`.

---

## §7 — Sequenced milestone breakdown (M5.2.x)

| # | Step | Files | Est. | Notes |
|---|---|---|---|---|
| **M5.2.1** | `SourceType.MAS` enum + schema regenerate | `core/ascendo/models/package.py`, `docs/architecture/schemas/sidecar.v1.schema.json` | ¼ d | Trivial; mirrors M5.1.1's `SourceType.BREW` add. Unblocks all subsequent steps. |
| **M5.2.2** | `MacElevation` impl + 10 unit tests | `adapters/macos/ascendo_macos/managers/elevation.py`, `tests/test_elevation_smoke.py` | 1 d | No mas dependency — landable in isolation. Includes `register_password / invalidate / askpass_path / has_password_registered` extra surface beyond `IElevation` ABC. |
| **M5.2.3** | `lib/ascendo_mas.sh` helpers + bash-side tests | `adapters/macos/lib/ascendo_mas.sh`, `tests/test_ascendo_mas_helpers.py`, `tests/fixtures/{mas-list,mas-outdated}.txt` | ½ d | Pure parsers — no `sudo`, no real mas calls; tested against captured fixtures. |
| **M5.2.4** | `MasManager` Python adapter + 14 unit tests | `adapters/macos/ascendo_macos/managers/mas.py`, `tests/test_mas_manager_smoke.py` | 1 d | Constructor accepts `IElevation`; APPLY-only env injection of `SUDO_ASKPASS`. |
| **M5.2.5** | `MacOSAdapter` wire-up: `capabilities |= ELEVATION`, add `MasManager` to `package_managers()`, return `MacElevation` from `elevation()` | `adapters/macos/ascendo_macos/adapter.py`, existing `test_adapter_smoke.py` | ¼ d | Tests in `test_adapter_smoke.py` extended; one wiring assertion. |
| **M5.2.6** | `scripts/mas/{check,plan,verify,cleanup}.sh` — read-only quartet | 4 bash files | 1 d | Apply held back so this whole step is mutation-free; can land + ship safely. |
| **M5.2.7** | `scripts/mas/apply.sh` — first mutating script + dry-run path | 1 bash file | ¾ d | The `sudo -A mas upgrade` path. Per-id loop when `--filter` set, bulk otherwise. |
| **M5.2.8** | Dashboard `/elevation/{auth,invalidate,status}` endpoints + 6 contract tests | `core/ascendo/dashboard/routes/elevation.py`, registration in `app.py`, `tests/contract/test_dashboard_elevation.py` | ½ d | Mounted only when `adapter.elevation() is not None`. |
| **M5.2.9** | `bin/validate-macos.sh` — add Stage 8 (mas phases + dashboard askpass round-trip) | `bin/validate-macos.sh` | ¼ d | Skips Step 8.7 with `[skip]` when `$SUDO_PW` unset. |
| **M5.2.10** | `bin/run-tag-release-macos.sh` — add `--mas` flag for real `sudo mas upgrade` step + tag bump to `v0.0.9-alpha` | `bin/run-tag-release-macos.sh` | ¼ d | Falls back to `sudo mas install <id>` when nothing is outdated. |
| **M5.2.11** | Real-hardware validation on this Mac — `validate-macos.sh` exits 0 (Stage 8 incl. askpass round-trip green); `run-tag-release-macos.sh --mas` performs one real `sudo mas upgrade` (or `install`); `git tag -a v0.0.9-alpha` | (no code) | ½ d | Mirrors M5.1.9. |
| **M5.2.12** | Update `HANDOFF.md` (Sesja 21 entry) + `PLAN.md` (M5.2 → done) | docs | ¼ d | Always-last. |

**Total: ~6 days** single-dev. Steps 2 + 3 can parallelize (independent files, no shared state). Step 8 (dashboard) doesn't strictly block 11 but does block 9's Stage 8.7 — sequenced after 7 to keep the green line consistent.

---

## §8 — Decisions log

| Q | Decision | Why |
|---|---|---|
| Apply-path scope | **A** — Track 1 (`sudo mas upgrade`) only | Smallest viable slice; mirrors M5.1's lean shape; Track 2 (AppleScript GUI for iPad apps) deferred — most users don't have iPad apps and operator can manually click "Update All" until Track 2 lands |
| Elevation surface | **B** — in-memory password + ephemeral `SUDO_ASKPASS` helper, TTY fallback when no password registered | Mirrors proven `app/backend/sudo.py` pattern; dashboard-driven `mas apply` is the whole point; CLI users still get terminal prompt via `sudo -A` fallback |
| `mas` bootstrap | **A** — hard requirement (`is_available()` returns False when missing or version < 4) | Matches `BrewManager`'s `jq` handling; phase scripts stay side-effect-pure; doctor output tells the user `brew install mas` / `brew upgrade mas` |
| Sign-in probe location | **A** — `check.sh` only | `check.sh` is the contract entry point for "is this category usable right now?"; apply fail-fasts on mas's own error; no GUI side effects from phase scripts |
| Done bar / tag | **C** — real `sudo mas upgrade` + dashboard `POST /elevation/auth` round-trip in `validate-macos.sh`; tag `v0.0.9-alpha` | Validates both the elevation interface and the dashboard wire-up end-to-end; fallback to `sudo mas install <id>` when nothing pending |
| osascript GUI password dialog | Deferred (M5.2.x or later) | Small follow-up (~40 LOC); only matters for graphical-only operators with no terminal AND no password registered — rare enough for now |
| Frontend modal extension (sudo prompt for mas) | Deferred (M5.2.x or M5.3) | Backend endpoints land in M5.2 so the round-trip is `curl`-validated; SPA changes are non-blocking |
| Capability flag | `PACKAGE_MANAGEMENT \| ELEVATION` (was `PACKAGE_MANAGEMENT` only) | Smallest delta; INVENTORY (M5.3), SNAPSHOTS (M5.4), SCHEDULING (M5.5) land later |

---

## §9 — Cross-platform contract restatement

This spec assumes (and depends on) the following pre-existing shared contracts. **None are modified** by M5.2:

- **`ascendo/v1` JSON sidecar schema** — `core/ascendo/models/sidecar.py`. Unchanged.
- **5-phase `Phase` enum** — `core/ascendo/models/run.py`. Unchanged.
- **`IPackageManager` interface** — `core/ascendo/interfaces/package_manager.py`. Unchanged.
- **`IElevation` interface** — `core/ascendo/interfaces/elevation.py`. Unchanged. `MacElevation` provides extra concrete-only surface (`register_password / invalidate / askpass_path / has_password_registered`) beyond the ABC.
- **`IAdapter` aggregate interface** — `core/ascendo/interfaces/adapter.py`. Unchanged.
- **`AdapterCapability.{PACKAGE_MANAGEMENT,ELEVATION}` flags** — `core/ascendo/interfaces/adapter.py`. Unchanged.
- **`SourceType.MAS` enum value** — `core/ascendo/models/package.py`. **Add in M5.2.1** (one-line change; existing legacy `update_appstore` source type was distinct from this clean value).
- **`OperatingSystem.MACOS` enum value** — `core/ascendo/models/host.py`. Already exists.
- **`ElevationMethod.SUDO` enum value** — `core/ascendo/models/host.py`. Already exists.
- **Sidecar I/O (`read_sidecar`, atomic write, cross-OS lock)** — `core/ascendo/orchestrator/sidecar_io.py` (M2.4). Unchanged.

The Layer 4 core is OS-agnostic by design; M5.2 adds Layer 5 (`MasManager` + `MacElevation`), Layer 6 (5 bash scripts + 1 lib file), and Layer 3 (3 dashboard endpoints) without touching shared contracts other than the trivial enum addition.
