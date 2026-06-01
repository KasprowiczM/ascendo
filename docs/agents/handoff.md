# Handoff

## 2026-06-01 — v1.0-beta production push: Windows leg (Sesja 88)

Windows P1/P2 items from `ASCENDO_ULTRA_REVIEW_2.md` sec.2/4/7. Five commits on
`main` (`3451f03`, `cf747e3`, `1e9dcd1`, `765f4e4`, + this docs commit):

- **P1.1 — Dedup uninstall executor gated.** winget/npm/pip `apply.ps1` no
  longer auto-uninstall from a stray `DEDUPLICATION_TASKS.json`. New shared
  `Get-AscendoDedupUninstalls` (`AscendoJson.psm1`) authorizes only on
  `$env:ASCENDO_DEDUP_AUTO_UNINSTALL=1` **or** a per-run `DEDUPLICATION_APPROVED`
  marker (written by `POST /dedup/apply` and the core opt-in path). pwsh
  execution test proves "stray file + no opt-in ⇒ no uninstall".
- **P2 A2/A3 — adapter caches sub-interface singletons** (elevation token now
  visible across accessors).
- **P2 W2 — `release_feed` regex no-match ⇒ probe_broken (`$null` ⇒ skipped)**,
  was a silent raw-value fallback. **W10** assessed as not-applicable on Windows
  (discovery is supplemental; `check.ps1` already fails loud / exit 1 on a
  registry-validate failure).
- **Security P8/P11/P3/P6 — verified already landed** in `cf1d5c4` (ChatsDB ACL,
  UAC `env` fail-fast, full-path argv[0] resolve, password try/finally).
- **T2 — first PowerShell execution tests** (`adapters/windows/tests/ps/`) +
  pytest wrappers (windows-latest CI leg) + a stage 3.5 in `validate-windows.ps1`.

Verification: `python -m pytest adapters/windows/tests/ -q` → 459 passed / 1
skipped; `pwsh bin/validate-windows.ps1 -SkipExpensive` → ALL CHECKS PASSED.
PLAN/HANDOFF session logs also updated in the private overlay (gitignored).

## 2026-05-31 — CI green: Validate Config workflow 6/6 on all 3 OSes (Sesja 85)

The `.github/workflows/validate.yml` workflow was failing on every push to
`main` (jobs: `validate-configs`, `check-readme`, `python-tests`, and the
`validate-cross-platform` matrix × ubuntu/macos/windows). Root-caused + fixed a
masked cascade of latent/pre-existing failures and drove it to a fully green
run, verified live via `gh run watch` (run `26724790115` = success). Three
commits on `main` (`4766beb`, `6be48f7`, `0a6e91a`):

- **Schema/emitter contract** — `schemas/phase-result.schema.json` now accepts
  both `"ascendo/v1"` and `"ubuntu-aktualizacje/v1"` (enum). The bash emitter
  (`lib/_json_emit.py`) deliberately stamps `ubuntu-aktualizacje/v1` so the core
  reader translates legacy-shape sidecars (the Sesja-82 trap — changing the
  emitter would reintroduce the `KeyError`). Fixes the phase-JSON-contract step
  + `test_json_emit` + all 14 `test_phase_contract`.
- **`plugins/_template/manifest.toml`** — `[scripts]` collapsed from invalid
  multi-line inline tables to single-line (TOML 1.0.0 / `tomllib`). Fixes the
  plugin-scanner step.
- **Test corrections** — `test_require_sudo_trap.bats` asserts the finalize-only
  `"exit_code"` field (per-phase sidecars have no `status`); `test_cli_web`
  introspects command params instead of grepping rich-truncated `--help`;
  `test_installers` pwsh AST harness pre-declares `$tokens`/`$errors`.
- **Workflow deps** — dashboard-smoke installs `pytest`; the matrix adapter-test
  step installs `pytest-asyncio` (root `asyncio_mode="auto"` + strict
  `filterwarnings` → fatal `INTERNALERROR` without it); the python-tests job
  installs the macOS adapter so the web-registry contract tests (which import
  `ascendo_macos.web_registry` by design) run instead of 503'ing.
- **Windows registry data** — `opencode` in `adapters/windows/config/web_apps.toml`:
  `silent_args ["/S"]→["--silent"]` (Squirrel, not NSIS — also a latent runtime
  apply fix) and `windows_uninstall_key` GUID → `"OpenCode"` (DisplayName
  fallback).

**Caveat:** CI runs the harnesses in reduced mode (`--quick`/`-SkipExpensive`/
`--skip-*`). Full real-hardware validation + the v1.0-beta MUST-DO items
(`PROMPT_*.md`) remain the next step. The A5 core→adapter coupling was
pragmatically sidestepped in CI (install macOS adapter in python-tests), not
resolved.

**Validation:** bats `test_json_emit` 7/7, `test_phase_contract` 14/14,
`test_require_sudo_trap` 6/6, plugin scanner, overlay guard (fresh clone = 1
file); contract 558 passed; macOS harness 18/18 + `adapters/macos` 417 passed;
`adapters/ubuntu` 149 passed; `test_cli_web` 16/16 at `COLUMNS=40`. Windows/pwsh
paths verified on the CI windows runner (run 26724790115 green).

---

## 2026-05-29 — macOS Deduplicator Integration & Production Hardening Audit

### Co poszło na produkcję

- **macOS Cross-Source Deduplicator Integration**:
  - Rozwiązano krytyczny błąd wykrywania platformy macOS (`darwin`) w `core/ascendo/orchestrator/deduplicator.py` (wcześniej system wpadał w domyślną gałąź Windows, próbując ładować nieistniejący plik `windows_app_sources.toml`).
  - Dodano dedykowaną, domyślną konfigurację deduplikacji dla macOS w nowym pliku `adapters/macos/config/macos_app_sources.toml`. Obecnie plik ten zawiera regułę dla `Claude` (preferowany menedżer npm nad brew).
  - Rozbudowano orkiestrator (`deduplicator.py` i powiązane szablony raportu) o pełną obsługę odinstalowywania i generowania komend dla menedżera `mas` (Mac App Store) na macOS.
  - Oczyszczono kod deduplikatora z tymczasowych instrukcji typu `print(f"DEBUG: ...")`, zastępując je prawidłowym logowaniem przy użyciu loggera `_log.debug()`.
  - Wdrożono kompleksowy test jednostkowy `test_deduplicator_macos_brew_npm` w pliku `tests/test_deduplicator.py`, sprawdzający poprawność działania deduplikacji, priorytetyzację oraz poprawne generowanie komend odinstalowania dla brew/npm na macOS.

- **Weryfikacja i Audyt Production Hardening**:
  - Przeprowadzono pełną weryfikację i audyt wdrożenia planu produkcyjnego bezpieczeństwa i stabilizacji (zgodnie ze specyfikacją `HANDOFF_PLAN.md` i `HANDOFF_TASK.md`).
  - Potwierdzono w kodzie prawidłowe działanie wszystkich kluczowych poprawek, w tym:
    - **P5 CORS lockdown** — zablokowanie domyślnego dostępu CORS do localhost w `app.py`.
    - **P12 Stale lock detection** — funkcja `detect_stale_locks` w `sidecar_io.py` szukająca starych plików `.lock`.
    - **E11 Lifecycle state** — pełne wsparcie dla statusu `RunStatus.CANCELLED` oraz pomijanie zapisu baz danych przy przerwaniu sesji.
    - **E5 Exception handling** — poprawne wyłapywanie specyficznych wyjątków zapisu i blokad sidecarów.
    - **I9 DB freshness** — zaimplementowana tabela `scan_meta` z datami ukończenia skanowania na kategorię w `inventory_db.py`.

### Pliki

**Nowe:**
- `adapters/macos/config/macos_app_sources.toml`

**Zmodyfikowane:**
- `core/ascendo/orchestrator/deduplicator.py`
- `tests/test_deduplicator.py`

### Walidacja
- Zestaw 2/2 testów deduplikatora, 556 testów kontraktowych oraz wszystkie 417 testów adaptera macOS przechodzą pomyślnie na lokalnej maszynie deweloperskiej bez żadnych błędów czy regresji.

---

## 2026-05-29 — Button Fixes: Missing API Endpoints (app/backend/main.py)

### Problem
Multiple buttons in the Ascendo desktop/web app were failing with
`405 Method Not Allowed` because the frontend (`app.js`) calls endpoints
that exist only in `core/ascendo/dashboard/routes/spa_real.py` (the legacy
monorepo router) but were never added to `app/backend/main.py` (the actual
running backend).

**Root cause confirmed:** `app/backend/main.py` serves the live dashboard
at 127.0.0.1:8765. The frontend's `app.js` was calling:
- `POST /inventory/db/refresh` → "Build inventory" / "Rebuild inventory" buttons
- `POST /inventory/clear` → "Clear inventory" button
- `GET /service/status`, `POST /service/{action}` → Settings → Service card
- `GET /ai/providers`, `GET+POST /ai/config`, `POST /ai/test-connection` → AI wizard
- `GET /scheduler/list`, `POST /scheduler/trigger` → Schedule tab
- `POST /runs/async` → Run Center wizard / deferred check
- `GET /version` → adapter locale detection
- `GET /suggestions/library` → AI suggestions view
- `GET /sync/config-status` → Dev-sync status
- `GET /about/release-notes` → About tab release notes
- `POST /apps/exclude` / `POST /apps/include` → per-package exclude toggle
- `GET /elevation/touchid/status` → macOS-only, now returns `{enabled:false}` on Linux

### Fix
Added all missing endpoints to `app/backend/main.py` (lines ~1088–1410).
Key mappings:
- `/inventory/db/refresh` → calls `inv_mod.invalidate(None)` + `inv_mod.summary()`
- `/inventory/clear` → calls `inv_mod.invalidate(None)` (no rescan)
- `/service/status` → queries systemd `ascendo-dashboard.service` + `ss -tlnp`
- `/ai/providers` → static catalog of 7 providers (same as `suggestions.py` implements)
- `/ai/config` GET/POST → reads/writes `settings.json["ai"]` (api_key masked on GET)
- `/runs/async` → translates `{phases:[], categories:[]}` to `StartRunRequest` shape
- `/apps/exclude`+`/apps/include` → delegates to `excl_mod.add`/`excl_mod.remove`
- All others → proper implementations using existing modules

### Validation
- All 597 tests pass (4 skipped, 1 xfail, unchanged from baseline).
- Manual curl verified each new endpoint returns expected JSON.
- Service restarted clean; `GET /health` → `{"ok": true}`.

### Files Modified
- `app/backend/main.py` — added ~350 lines of new route handlers

### State
- ✅ "Build inventory" button works (returns full scan result in <30s)
- ✅ "Rebuild inventory" button works
- ✅ "Clear inventory" button works
- ✅ Service status card in Settings works
- ✅ AI provider wizard loads
- ✅ Schedule tab loads without errors
- ✅ About → Release notes loads
- ⚠️ `deb` package NOT yet rebuilt/reinstalled — run `bash packaging/build-deb.sh && sudo dpkg -i dist/ascendo-basic_*.deb` to sync the installed version

---



### Co poszło na produkcję
- **Ubuntu App Deduplication**: Rozszerzono `core/ascendo/orchestrator/deduplicator.py` o detekcję platformy oraz wsparcie i mapowanie komend dla Linuksa (`apt`, `snap`, `flatpak`, `brew`).
- **Konfiguracja deduplikacji na Linuksie**: Wprowadzono `adapters/ubuntu/config/ubuntu_app_sources.toml` by kontrolować kolejność i przypisywanie aplikacji typu Docker (preferowany `apt`), Claude (preferowany `npm`), czy VS Code (preferowany `snap`).
- **Naprawa środowiska testowego (Ubuntu)**:
  - Wdrożono `--import-mode=importlib` w `pyproject.toml` by całkowicie wyeliminować `ModuleNotFoundError` oraz zjawisko "shadowing" pomiędzy rożnymi katalogami o nazwie `tests/`.
  - Poprawiono logikę testowania deduplikatora (in-memory assertions) chroniąc testy przed różnicami miedzy trybem `TTY` a `non-TTY`.
  - Aktualizacja długu technologicznego: zaktualizowano `test_ubuntu_adapter_smoke.py` gdzie `UbuntuAdapter.source()` jest już wprowadzony z użyciem singletona.

### Pliki

**Nowe:**
- `adapters/ubuntu/config/ubuntu_app_sources.toml`

**Zmodyfikowane:**
- `core/ascendo/orchestrator/deduplicator.py`
- `pyproject.toml`
- `tests/test_deduplicator.py`
- `adapters/ubuntu/tests/test_ubuntu_adapter_smoke.py`

### Walidacja
- Zestaw setek testów (ponad 630) przechodzi pomyślnie dla wszystkich adapterów i core.
- `update-all.sh` dry-run pomyślnie buduje raport sidecar włączając wsparcie odinstalowywania duplikatów.

### Następne kroki
1. **Rozpoczęcie macOS Parity**. Baza pod Windows i Ubuntu została domknięta i działają na nich uniwersalne paczki testów kontraktowych. Przejęcie środowiska przez sesję macOS by wdrożyć integrację z Mac.

## 2026-05-29 — Windows Production Hardening & Auto-Uninstaller (PASS E Closeout)

### Co poszło na produkcję

- **P8 Windows ACLs na `chats.db`**: Dodano surowe restrykcje używając `ctypes` (`advapi32.SetNamedSecurityInfoW`). Zabezpiecza bazę rozmów przed byciem world-readable. Testy jednostkowe potwierdzają działanie ACL-ek na Windowsie (fallback loguje ostrzeżenie, jeśli plik systemowy).
- **P11 Fail-fast w UAC `env` overrides**: Zmodyfikowano `_run_uac` w `adapters/windows/managers/elevation.py`. Teraz zrzuca `NotImplementedError`, jeżeli środowisko nie jest puste (Windows `ShellExecuteExW` z `runas` nie wspiera dziedziczenia własnego środowiska bez hackowania tokenów).
- **P3/P6 Proton Mail kill + Github Release handlers**: Zaktualizowano `web_apps.toml` o dodanie `Update` (Squirrel) process kills, aby upewnić się, że nie blokuje updatów. Drobne poprawki do `github_release.ps1` w celu spójnej walidacji UAC i silent apply.
- **Cross-Source Deduplication & Auto-Uninstall**:
  - Konfiguracja `app_sources.toml` przeniesiona z codebase do profilu użytkownika (`~/.config/ascendo/windows_app_sources.toml`), z automatycznym kopiowaniem domyślnego szablonu.
  - Zmodyfikowano model `Item` o nową flagę `action = "uninstall"`.
  - Orkiestrator teraz zatrzymuje się na interaktywnym promtcie (`rich.prompt.Confirm`) pytając, czy użytkownik chce automatycznie odinstalować niepreferowane źródła instalacyjne przed wymuszeniem wybranego profilu (jeżeli to terminal non-interactive, domyślnie leci silent uninstall).
  - Skrypty PowerShell (Winget, NPM, PIP apply.ps1) pod maską parsują globalną instrukcję `DEDUPLICATION_TASKS.json` i czyszczą śmieci (uruchamiają odpowiednie komendy odinstalowania).

### Pliki

**Modyfikacje:**
- `adapters/windows/ascendo_windows/managers/elevation.py` (P11)
- `adapters/windows/lib/AscendoWinget.psm1`
- `adapters/windows/lib/handlers/github_release.ps1` (P3/P6)
- `adapters/windows/scripts/npm/apply.ps1`
- `adapters/windows/scripts/pip/apply.ps1`
- `adapters/windows/scripts/winget/apply.ps1` (Deduplikacja)
- `adapters/windows/scripts/winget/verify.ps1`
- `core/ascendo/ai/persistence.py` (P8)
- `core/ascendo/models/result.py`
- `core/ascendo/orchestrator/deduplicator.py` (Deduplikacja & Prompt)

**Nowe:**
- `adapters/windows/tests/test_elevation.py`
- `core/ascendo/models/deduplication.py`
- `tests/test_deduplicator.py`

### Walidacja

- Pomyślnie uruchomiono w trybie dry-run dla weryfikacji generowania sidecarów ze statusem `action="uninstall"`.
- Zmodyfikowane testy Pydantic models przechodzą weryfikację.
- Python AST + Pydantic validation clean dla wszystkich modeli.
- Działanie potwierdzone w środowisku operacyjnym przez agenta.

### Następne kroki

1. Testy na produkcji Ubuntu.
2. Testy na produkcji macOS.

## 2026-05-24 (Sesja 13) — M5.7.6 macOS coverage closeout + operator-grade ports from Ascendo

### Co poszło na produkcję

**Phase A — registry coverage parity:**
- 5 new entries in `adapters/macos/config/web_apps.toml`:
  `antigravity-ide` (release_feed, root endpoint text-regex mirror),
  `appcleaner` (sparkle, SUFeedURL extracted from installed Info.plist:
  `freemacsoft.net/appcleaner/updates.xml`),
  `protonvpn` (sparkle, `protonvpn.com/download/macos/updates/v5/sparkle.xml`
  — the legacy macos-update.xml feed is the abandoned 1.x channel),
  `protondrive` (release_feed, `proton.me/download/drive/macos/version.json`,
  Tier-A apply with download_path=`Releases[0].File.Url`),
  `ipmiview` (builtin, no auto-updater).
- 4 dead entries dropped (operator confirmed single-Mac scope):
  `opera`, `macwhisper`, `notion` (desktop), `notion-calendar`.
- `codeedit` re-enabled with the correct universal asset pattern
  `^CodeEdit\.dmg$` — live probe of `releases/latest` confirmed
  v0.3.6 ships a single universal DMG (no `-arm64` variant; the legacy
  pattern was vendor-speculation).
- `com.microsoft.autoupdate2` retagged `category="infrastructure"` so
  the SPA hides MAU from the Categories grid (engine, not product).
- New `category: Literal["app","infrastructure"]` field on WebApp
  schema, default `"app"`. `extra="forbid"` preserved.
- 13 new contract tests in `adapters/macos/tests/test_phase_a_coverage.py`
  pin every Phase A change (new entries / dropped entries / codeedit
  pattern / infrastructure tag / category default).
- iWork "Creator Studio" rename investigated and dismissed as
  not-a-bug: `mas list` uses CLI output indifferent to the
  `/Applications/<Name>.app` rename; all three iWork bundles
  (`com.apple.{Keynote,Numbers,Pages}`) are still covered by the
  existing mas manager.

**Phase B — operator-grade ports from `Ascendo`:**
- **B1 TOR-2 MAS-GUI handler** for iPad-on-Apple-Silicon apps that
  `mas` cannot touch (UniFi / WiFiman / Picsart): new
  `adapters/macos/scripts/mas/gui_fallback.sh` ports the reference's
  AppleScript-based 3-pass "Update All" automation verbatim
  (`Ascendo/update_appstore.sh:215`). Auto-detects
  Accessibility permission and opens the System Settings Privacy pane
  on denial. Top-level entry point at `bin/ascendo-mas-gui-update.sh`.
  Smoke test in `adapters/macos/tests/test_mas_gui_fallback.sh`
  (4/4 passing on Mac.r12.home).
- **B2 vendor-direct DMG for Ledger Live** — confirmed the existing
  `_web_install_dmg` pipeline in `adapters/macos/lib/ascendo_web.sh`
  already does the full chain (download → `hdiutil`/`ditto` → `spctl
  --assess --type execute --verbose` Gatekeeper verify → remove-then-
  copy atomic swap → xattr quarantine strip), so the only change
  needed was promoting `ledger-live` from Tier-B trigger-only to
  Tier-A apply by setting `download_path = "files[1].url"` against
  `download.live.ledger.com/latest-mac.yml`. Live probe verified:
  `files[1]` is the DMG (`files[0]` is the .zip). Mirrors the
  reference's 2026-05-22 Ledger fix.
- **B3 rotated per-run logs** — new
  `core/ascendo/orchestrator/run_logger.py` attaches a
  `logging.FileHandler` at `<base_dir>/<run.id>/run.log` for the
  lifetime of `run_phases()`, then prunes the runs directory to the
  newest N=30 entries (override via `ASCENDO_RUN_LOG_KEEP`).
  Pruner only touches UUID4 / legacy `YYYYMMDDTHHMMSSZ-<hex>` named
  directories — operator-owned paths are preserved. Wired into
  `run_phases` via a thin wrapper (`_run_phases_inner`). Mirrors the
  `Ascendo/logs/update_all_<ts>.log` rotation but inside
  Ascendo's per-run sidecar dir. 230 stale runs detected on
  Mac.r12.home; will be pruned on next run.
- **B4 inventory override** — deferred as redundant. The existing
  `~/.config/ascendo/web_apps.toml` user override (merge-by-bundle_id)
  already covers the operator's add-custom-entries use case.

**Phase C — bug fixes:**
- **C1 `bin/validate-macos.sh --quick` + auto-detect** — added
  `--quick` / `--full` flags. Default is now `--quick` when stdin is
  not a TTY (CI / dashboard sidecar / piped invocation), eliminating
  the 120s+ hang Sesja 12 observed. Quick mode skips dashboard
  smoke, softwareupdate `-l` network call, and the launchd scheduler
  round-trip (stages 10-12). Validated live:
  `validate-macos.sh </dev/null` returns
  "ALL QUICK CHECKS PASSED" in ~3s with 18/0 passed/failed.
- **C2 dead elevation stub deleted** —
  `core/ascendo/elevation/__init__.py` (17-line docstring-only orphan
  flagged for optional deletion in Sesja 81 carry-forward) was
  confirmed to have zero importers and removed. Empty directory
  also pruned.
- **C3 flaky-test triage** — three documented flakes
  (`test_runs_active_stop_running_run`,
  `test_apply_squirrel_invokes_open`,
  `test_generate_apply_report_groups_categories`) catalogued in
  new `docs/known-flaky-tests.md` with root-cause and "don't fix
  here" rationale per Sesja 81 carry-forward.
- **C4 editable re-install in dev workflow** — new
  `bin/update-dev.sh` post-`git pull` editable refresh. Verifies
  `python3 -c "import ascendo"` resolves to `$REPO_ROOT/core` and
  re-runs `pip install --upgrade -e core/ -e adapters/<os>/`.
  Idempotent. `--check-only` exits 0/1 based on drift. Handles
  PEP 668 on Homebrew Python via `--break-system-packages`.

**Phase D — parallel vendor doc refresh:**
- 41 Tier-A registry entries probed in parallel (12-thread executor,
  1.9s wall-clock). Findings categorised in
  `docs/phase-d-vendor-probe-2026-05-24.md`:
  - **3 real outdated** apps on this Mac: Google Chrome
    (148.0.7778.179 → 149.0.7827.29), Brave (148.1.90.124 →
    148.1.90.125), Proton Mail (1.13.0 → 1.13.1). Operator action:
    `ascendo run --category web --phase apply`.
  - **15 ✓ up_to_date** (claude/warp/trezor/codeedit/rdm/docker/
    vscode/keepassxc/obsidian/opencode/inkscape/spotify/protonvpn/
    protondrive/lm-studio).
  - **7 false-drift probe artifacts** (megasync 4-digit vs 3-digit
    format, zoom space-paren vs dot, firefox-dev beta-channel
    picker, cursor stale ToDesktop app-id, antigravity rollout
    cohort drift, appcleaner Sparkle item-order). Production
    handlers do the right thing; probe was a one-shot quick check.
  - **3 sparkle parse errors** (chatgpt / chatgpt-atlas / codex):
    probe regex assumed attribute form, vendors use
    `<sparkle:shortVersionString>X</sparkle:shortVersionString>`
    element form. Production handler grabs both.
  - **13 skipped** (omaha / msupdate / builtin handlers not in the
    probe — they do their own fetch).
- One real registry-fix recommendation: `cursor` slug points at a
  stale ToDesktop app-id (`230313mzl4w4u92` returns 0.45.14 while
  installed is 3.5.17). Operator should capture a fresh URL via
  `mitmproxy` on next Cursor launch.

### Pliki

**New:**
- `core/ascendo/orchestrator/run_logger.py` (M5.7.6 B3)
- `bin/update-dev.sh` (M5.7.6 C4)
- `bin/ascendo-mas-gui-update.sh` (M5.7.6 B1)
- `adapters/macos/scripts/mas/gui_fallback.sh` (M5.7.6 B1)
- `adapters/macos/tests/test_phase_a_coverage.py` (13 tests)
- `adapters/macos/tests/test_mas_gui_fallback.sh` (4 smokes)
- `docs/known-flaky-tests.md` (C3 triage)
- `docs/phase-d-vendor-probe-2026-05-24.md` (Phase D report)

**Modified:**
- `adapters/macos/config/web_apps.toml` (5 new entries, 4 dropped,
  codeedit fixed, ms365 retagged, ledger-live → Tier-A apply)
- `adapters/macos/ascendo_macos/web_registry.py` (+category field)
- `core/ascendo/orchestrator/runner.py` (attach_run_log wrapper +
  `_run_phases_inner` split)
- `bin/validate-macos.sh` (--quick / --full + auto-detect)

**Deleted:**
- `core/ascendo/elevation/__init__.py` + empty parent dir

### Walidacja

- 413/413 tests pass on `core/ascendo/` + `adapters/macos/tests/`
  excluding the documented Sesja-73 pre-existing failure
  (`test_apply_squirrel_invokes_open`); that single failure
  reproduces identically on the pristine baseline and is
  catalogued in `docs/known-flaky-tests.md`.
- All shell scripts: `bash -n` clean.
- All Python: AST parse + Pydantic validate clean.
- `bin/validate-macos.sh </dev/null` → ALL QUICK CHECKS PASSED
  (18/0) in ~3s.
- `bin/update-dev.sh --check-only` → "dev tree pinned correctly
  at /Users/mk/Dev_Env/Ascendo".
- `bash adapters/macos/tests/test_mas_gui_fallback.sh` → 4/4 PASS.
- `run_logger` smoke: log content reaches the file (321 B), prune
  trims to keep=10, operator-owned dirs preserved.

### Otwarte ryzyka / follow-ups

1. **Cursor registry URL is stale** — fix per the Phase D report
   recommendation (capture live update endpoint via mitmproxy on
   next Cursor launch).
2. **Firefox-dev beta channel** — if the next Phase D probe also
   returns a `b2`-suffixed tag, switch `version_path` from
   `FIREFOX_DEVEDITION` to a stable-only channel pointer.
3. **iPad MAS-GUI not wired into mas/apply.sh** — `gui_fallback.sh`
   is currently invocable only via `bin/ascendo-mas-gui-update.sh`.
   Future session: wire it as an opt-in fallback after `mas upgrade`
   when `mas list` shows iPad apps still pending.
4. **Phase D probe regex robustness** — three sparkle false-positives
   (chatgpt/chatgpt-atlas/codex) suggest the one-shot probe should
   also accept the `<element>X</element>` form. Probe is a doc
   artifact, not production code, so this is documentation polish.
5. **3 documented flaky tests** — see `docs/known-flaky-tests.md`.
6. **Self-update Ascendo.app via GH Releases** — depends on M4
   signing/notarization; M5.7.6 left `dev.ascendo.desktop` filtered
   out of the web inventory.

### Sesja count

| Window | Count |
|---|---|
| Sesja 12 (M3.12-M3.15 Windows MVP) | shipped 2026-05-01 |
| Sesja 13 (M5.7.6 macOS coverage closeout) | **shipped 2026-05-24** |

**macOS adapter status:** every non-silent app on this Mac (40
installed `.app` bundles + brew casks + mas + native CLI) is now
either covered by a Tier-A real-candidate probe, covered by the
existing mas / msupdate / brew adapters, or surfaces as a clean
Action-required item (IPMIView). The 3 real outdated apps detected
in the Phase D probe (Chrome / Brave / Proton Mail) confirm the
end-to-end coverage path works.

---

## 2026-05-01 (Sesja 12) — Windows MVP feature-complete (M3.12 + M3.13 + M3.14 + M3.15)

### Co poszło na produkcję

- **M3.12 VSS snapshot** — `ISnapshot` impl pod Windows. Manager Python (`managers/snapshot.py`) + driver PS (`scripts/snapshot/snapshot.ps1`). `create` używa `Checkpoint-Computer` (System Restore wraps VSS shadow copies); `list` enumeruje `Win32_ShadowCopy` przez `Get-CimInstance`. Label/notes round-trip przez `%ProgramData%\Ascendo\snapshots\registry.json`. Restore intencjonalnie poza interface'em — destruktywna operacja z reboot, gated na explicit user gesture.
- **M3.13 Task Scheduler** — `IScheduler` impl. Manager (`managers/scheduler.py`) + driver PS (`scripts/scheduler/scheduler.ps1`). Tasks pod `\Ascendo\<name>`. Parser wyrażeń: `DAILY HH:MM`, `WEEKLY <DAY> HH:MM`, `MONTHLY HH:MM`, `HOURLY HH:MM`, `MINUTE <N>` + passthrough do schtasks. Akcja zawsze: `ascendo run --profile <profile>` z fallbackem `python -m ascendo`.
- **M3.14 UAC elevation** — `IElevation` impl. Pure stdlib (`ctypes` + `subprocess` + `tempfile`), bez pywin32. Dwie ścieżki: bezpośredni spawn gdy już elevated, `ShellExecuteEx(runas)` + cmd.exe redirection do tempfile gdy nie. Argv-only kontrakt enforced przez basename allow-list (T4 mitigation per ADR-0005). `ERROR_CANCELLED (1223)` → `ElevationDenied` gdy user kliknie "Nie".
- **M3.15 Dell Driver Update plugin** — pierwszy oficjalny plugin. `plugins/dell-driver-update/manifest.toml` (manifest v1 per ADR-0007) + `windows/{check,plan,apply,verify,cleanup}.ps1` opakowuje `dcu-cli.exe`. DCU exit codes mapowane: 0=success, 1=reboot pending (needs_reboot=true), 500=no updates.
- **WindowsAdapter** teraz deklaruje `PACKAGE_MANAGEMENT | INVENTORY | SNAPSHOTS | SCHEDULING | ELEVATION`. `snapshot()/scheduler()/elevation()` zwracają instancje (były `None`).

### Pliki

- New: `adapters/windows/ascendo_windows/managers/{snapshot,scheduler,elevation}.py`, `adapters/windows/scripts/{snapshot,scheduler}/*.ps1`, `adapters/windows/tests/test_m3_12_to_14_smoke.py`, `plugins/dell-driver-update/manifest.toml`, `plugins/dell-driver-update/windows/*.ps1`
- Modified: `adapters/windows/ascendo_windows/adapter.py`, `HANDOFF.md`, `docs/agents/handoff.md`

### Walidacja

- Python AST OK na wszystkich zmienionych plikach (snapshot/scheduler/elevation/adapter/tests).
- 13 nowych testów smoke w `test_m3_12_to_14_smoke.py`: identity, availability, allow-list normalisation (basename + case), denial paths (non-Windows / empty argv / not-allowlisted), adapter wiring assertion.
- E2E (real `vssadmin` / `schtasks` / UAC dialog / `dcu-cli`) deferred do M3.16.

### M3 status

| Item | Status |
|---|---|
| M3.1-M3.11 | ✅ |
| **M3.12 VSS snapshot** | ✅ Sesja 12 |
| **M3.13 Task Scheduler** | ✅ Sesja 12 |
| **M3.14 UAC elevation** | ✅ Sesja 12 |
| **M3.15 Dell DCU plugin** | ✅ Sesja 12 |
| M3.16 real-hardware validation | ⏳ user-side |

**Windows MVP feature-complete.** Pozostaje tylko M3.16.

### Następne kroki

1. **M3.16** — user na DP5520WMK: `bin/validate-windows.ps1` + manualny test snapshotów + scheduler + UAC dialog. ~30 min.
2. **M4** — MSI installer (WiX), winget manifest, GitHub Releases pipeline, Tauri 2.x shell, code signing. ~2-3 tygodnie.
3. **M5** — macOS adapter (`adapters/macos/`), brew + mas + softwareupdate + LaunchServices. ~3 tygodnie.
4. **v0.1.0-alpha tag** po M3.16 + M4.

### Krok 4w (commit block w `HANDOFF.md`)

---

## 2026-05-01 (Sesja 11) — CLI parity + SPA async + M3.8/M3.9 + visual polish

### Co poszło na produkcję

- **CLI parity**. `core/ascendo/cli/__init__.py` rozszerzone:
  - `ascendo dashboard --background` / `-b` — detached uvicorn (cross-platform: `CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS` na Windows, `start_new_session` na Unix).
  - `ascendo runs list [--limit N] [--status STATE]` — czyta sidecary z `~/.ascendo/runs/`, sortuje newest-first.
  - `ascendo runs show <run-id>` — overall + per-phase per-category, z exit code mapowanym na overall status.
- **SPA async + SSE wiring** (M2.10). `startRunWithSudo` woła `POST /runs/async` (HTTP 202 + run_id), fallback do legacy `/runs` przy 404/405. `attachStream` subskrybuje `/runs/{id}/events` i renderuje per-(phase, category) wiersze z eventów `status` / `sidecar` / `sidecar_error` / `done`. Klasyczny `prefers-color-scheme` listener usunięty.
- **M3.8 Microsoft Store manager**. `managers/msstore.py` dziedziczy po WingetManager, 5 skryptów PS pod `scripts/msstore/` (check/plan/apply/verify/cleanup) opartych na `winget --source msstore`.
- **M3.9 MSI/Registry ARP manager**. `managers/arp.py` (też dziedziczy po WingetManager) skanuje trzy gałęzie `Uninstall\*` w rejestrze (HKLM, WOW6432Node, HKCU), filtruje system-components + child entries. `is_available()` zmienione — ARP nie potrzebuje winget. Apply używa `QuietUninstallString` lub `UninstallString` przez `cmd.exe /c`, exit `0` i `3010` traktowane jako success.
- **WindowsAdapter** teraz zwraca `[Winget, MSStore, Arp, WindowsUpdate]` w `package_managers()`.
- **Polish wizualny pod mockup webapp/index.html**:
  - sidebar brand 17px / -0.02em (było 20px)
  - tagline 9px mono / 0.14em / `--fg-faint` (było jaśniejsze)
  - card radius 10px + padding 18px, plus `.eye` / `.big` / `.meta` sub-elements
  - status pills `3px 10px` padding, `6px` gap, dot `6×6` — mockup ma więcej powietrza
  - desktop topbar utilities w pływającej kapsule (top-right, `--bg-elev` + `--border` + `--shadow-sm`) — wcześniej były na transparentnym pasku i niewidoczne

### Pilne fixy w środku sesji

- `dashboard/app.py` miał osierocony duplikat trasy `/assets/{filename}` na poziomie modułu (IndentationError z poprzedniej recovery sesji 10) — usunięte.
- `app/frontend/index.html` był obcięty na końcu, brakowało zamykających tagów + 3 `<script>` tagów. Po przywróceniu nawigacja, theme/lang/font switchery działają.
- Wprowadzono `--accent-fg` (theme-aware alias) — bright lime na dark, lime-600 na light — żeby tekst akcentu zachował kontrast AA na obu motywach.

### Pliki

- New: `core/ascendo/cli/__init__.py` (extended), `adapters/windows/ascendo_windows/managers/{msstore,arp}.py`, `adapters/windows/scripts/{msstore,arp}/*.ps1`, `adapters/windows/tests/test_msstore_arp_smoke.py`
- Modified: `core/ascendo/dashboard/app.py`, `adapters/windows/ascendo_windows/adapter.py`, `app/frontend/{index.html, style.css, app.js, i18n.js}`, `tests/contract/test_dashboard_spa.py`, `HANDOFF.md`, `docs/agents/handoff.md`

### Walidacja

- `python3 ast` parse: `dashboard/app.py`, `cli/__init__.py`, `managers/{msstore,arp}.py`, `adapter.py`, `tests/test_msstore_arp_smoke.py`, `tests/contract/test_dashboard_spa.py` — wszystkie OK.
- `node --check`: `app.js`, `i18n.js`, `icons.js` — OK.
- `style.css`: 571 linii, brace balance 0, UTF-8 OK.
- `index.html`: 12 view sections, 4 script tags, zamyka się poprawnie.
- Pytest run deferred do user (sandbox = Python 3.10, projekt = 3.11+). Spodziewane: 158 → 169 contract tests passing.

### Otwarte ryzyka / follow-ups

1. M3.12 VSS snapshot — wpięcie do `ascendo snapshot` CLI placeholder.
2. M3.13 Task Scheduler — wpięcie do `ascendo schedule` CLI placeholder.
3. M3.14 UAC elevation — `runas` / ShellExecute verb=runas.
4. M3.15 Dell DCU plugin — pierwszy oficjalny plugin w `plugins/dell-driver-update/`.
5. Migracja `app/frontend/` → `ui/frontend/` (M4).
6. Self-host woff2 dla Inter Tight + JetBrains Mono (offline Tauri).

---

## 2026-05-01 (Sesja 10) — Ascendo design system integrated, dark theme primary

### What shipped

- `Ascendo_Design_System/colors_and_type.css` adopted as the SPA's design-token source. Loaded before `style.css` in `app/frontend/index.html`. Defines all colors, type (Inter Tight / JetBrains Mono / Instrument Serif), spacing (4px ramp), radii, shadows, motion — with both light and dark variants gated on `:root[data-theme="dark"]`.
- Dark theme is now the primary surface: `<html data-theme="dark">` literal + inline pre-paint `<script>` reads `localStorage.ui-theme` and pins dark before paint. Theme switcher cycles binary dark ↔ light, default dark. The legacy `auto` track and `prefers-color-scheme` listener were removed.
- `style.css` reskinned around tokens. Legacy variable names (`--panel`, `--text`, `--dim`, `--mono`) kept as aliases so existing component selectors continue working without a markup rewrite. New `--accent-fg` alias gives foreground-accent text the bright lime on dark and the readable `--accent-strong` (lime-600) on light, dodging the lime-on-paper AA-contrast trap.
- Brand assets replaced: green→blue gradient SVG dropped. New `app/frontend/assets/{logo-mark, logo-mark-light, logo-mark-mono, logo-wordmark, logo-wordmark-dark}.svg` ship the design-system marks. Favicon = `/assets/logo-mark.svg`. HTML uses an `<img class="brand-img--dark|--light">` pair, swapped via CSS on `[data-theme="light"]`.
- `core/ascendo/dashboard/app.py` adds `/colors_and_type.css` to `_spa_assets` and a new `/assets/{filename}` route streaming SVGs/PNGs from `app/frontend/assets/` with explicit `..` traversal blocking.
- `tests/contract/test_dashboard_spa.py` extended with: `/colors_and_type.css` mount assertion, brand-asset round-trip on every SVG, traversal-block test, dark-pin-by-default assertion.

### Pliki

- New: `app/frontend/colors_and_type.css`, `app/frontend/assets/*.svg`
- Modified: `app/frontend/{index.html, style.css, app.js, i18n.js}`, `core/ascendo/dashboard/app.py`, `tests/contract/test_dashboard_spa.py`, `HANDOFF.md`, `docs/agents/handoff.md`

### Walidacja

- `python3 ast` parse OK na zmienionych `.py`.
- `node --check` OK na `app.js` + `i18n.js`.
- `style.css`: 562 linie, brace balance 0, UTF-8 OK, 226 `var()` refs, 46 unique tokens, 0 unmapped.
- `index.html`: tokens-before-style.css ✓, `<html data-theme="dark">` ✓, pre-paint script ✓.
- Pytest run deferred to Linux box (sandbox tu ma Python 3.10, projekt wymaga 3.11+). Expected: +7 contract tests, 158 → 165 passing.

### Otwarte ryzyka / follow-ups

1. Tauri desktop shell + landing page — kit ma `ui_kits/desktop/` i `ui_kits/landing/`, do zaadoptowania w M4.
2. Lekka kontrola kontrastu primary button text na lime (manual review).
3. Self-host woff2 fonts w `app/frontend/fonts/` dla offline Tauri — obecnie z Google Fonts CDN.

---

## 2026-04-30 (Etap 12) — Inventory false-positive outdated fix + unified title

### Bug

Dashboard Categories/Overview flagged npm packages as outdated whenever
`npm outdated -g --json` returned a row, regardless of direction. Visible
case: `@google/gemini-cli 0.40.0 → 0.1.9` and `npm 11.13.0 → 10.9.8`
(both downgrades — `latest` dist-tag pointed at an older release line, npm
itself installed via brew is newer than the registry's `latest`).

Root cause: `app/backend/inventory.py::_classify` returned `outdated`
whenever `candidate != installed`, without a direction check.

### Fix

- `_ver_key(v)` + `_version_gt(a, b)` — token-based version comparator,
  splits on `.-_+`, separates numeric vs alpha runs.
- `_classify` now requires strict `_version_gt(candidate, installed)`.
- npm/pip/brew scanners null out `candidate` when not strictly newer — the
  table no longer shows a phantom downgrade arrow.

### Audit (other categories)

- **apt** — `apt list --upgradable` is direction-aware, no fix needed.
- **snap** — `snap refresh --list` store-side, OK.
- **flatpak** — `flatpak remote-ls --updates` store-side, OK.
- **drivers** — already used `dpkg --compare-versions … gt …`, OK.
- **inventory** — pseudo-category, no version compare.

### App title rename

`Ascendo` → `Ascendo - Unified Updates` everywhere:

- `app/frontend/index.html` `<title>`
- `app/backend/main.py` FastAPI `title=`
- Repo desktop entries + `packaging/deb/usr/share/applications/`
- `~/.local/share/applications/{ascendo,ascendo-desktop}.desktop`
- `systemd/user/install-dashboard.sh` (banner + comments)
- `scripts/fresh-machine.sh` welcome string
- `app/README.md` heading

### Validation

```bash
python3 -c "from app.backend.inventory import scan_npm; \
  print([(i['name'],i['installed'],i['candidate'],i['status']) \
  for i in scan_npm() if i['status']=='outdated'])"
# []  (was 2 false-positives before fix)

curl -s http://127.0.0.1:8765/inventory/summary | jq .totals
# { ok: 340, outdated: 0, missing: 0 }

curl -s http://127.0.0.1:8765/ | grep -o '<title>[^<]*</title>'
# <title>Ascendo - Unified Updates</title>
```

---

## 2026-04-30 (late) — CRITICAL FIX: apt:apply EXIT trap override, JSON always dropped

**BUG:** `scripts/apt/apply.sh:118` unconditionally overwrote the JSON exit trap registered by `json_register_exit_trap()`, causing `apply.json` to never be written. Symptom: user runs `./update-all.sh full`, sees "all green" in CLI, but `apt list --upgradable` still shows packages outdated—apply silently skipped and never logged.

**FIX:** Composed EXIT trap to call both `_restore_*_holds()` AND `_json_finalize_on_exit()`. Added defensive sidecar synthesis in `lib/orchestrator.sh:orch_run_phase()` that detects missing JSON and forces `status=failed` (exit 30) so silent skips can never happen. Reworked `_temporarily_hold_excluded_apt` to NOT exit 0 when whole apt category is excluded—sets flag, lets main flow clean-exit with proper sidecar.

**Files:** `scripts/apt/apply.sh`, `lib/orchestrator.sh`, `MIGRATION.md` (new concise fresh-machine guide), `CLAUDE.md`.

**Validation:** `bash -n`, `./update-all.sh --profile quick --no-notify` → 6/6 ok, all sidecary present, apt items populated.

---

## 2026-05-04 (late) — Ascendo desktop icon + CLI runs in dashboard history (Etap 11)

### Stan na koniec sesji

| Obszar | Status |
|---|---|
| **Ikona Ubuntu desktop = Ascendo logo** | ✅ `share/icons/hicolor/scalable/apps/ascendo.svg` + `share/applications/ascendo.desktop` (`Name=Ascendo`, `Icon=ascendo`, `StartupWMClass=Ascendo`); poprzednio używało systemowego `software-update-available` |
| **User-level instalator ikony** | ✅ `systemd/user/install-dashboard.sh` instaluje ikonę i `.desktop` do `~/.local/share/{icons,applications}`, woła `update-desktop-database` + `gtk-update-icon-cache`, kasuje stare `ascendo.desktop` |
| **System-wide ikona w `.deb`** | ✅ `packaging/deb/usr/share/icons/hicolor/scalable/apps/ascendo.svg` + `packaging/deb/usr/share/applications/ascendo.desktop`, postinst odświeża bazy |
| **CLI runs widoczne w historii dashboard/web** | ✅ `db.import_disk_runs()` reconciliuje `logs/runs/<id>/run.json` z SQLite; wpięte w startup oraz w `/runs` i `/runs/{id}` |
| **Migracja `004 run_source`** | ✅ kolumna `runs.source` (`'cli'` vs `'dashboard'`); `insert_run` przyjmuje source; UI dorzuca pill **cli** w History |
| **Inferencja profilu z faz** | ✅ tylko `check` → `quick`; brak `drivers` → `safe`; reszta → `full`. `only_cat`/`only_phase` ustawiane gdy single-cat / single-kind |

### Pliki dotknięte

share/icons/hicolor/scalable/apps/ascendo.svg, share/applications/ascendo.desktop, systemd/user/install-dashboard.sh, packaging/deb/usr/share/icons/hicolor/scalable/apps/ascendo.svg, packaging/deb/usr/share/applications/ascendo.desktop, packaging/deb/DEBIAN/postinst, app/backend/migrations.py (+_m004_run_source), app/backend/db.py (import_disk_runs), app/backend/main.py (startup/lazy reconcile), app/frontend/app.js (cli pill).

### Walidacja

bash -n update-all.sh + scripts/*/*.sh + lib/*.sh + systemd/user/*.sh + DEBIAN/postinst OK; python3 ast parse app/backend/{main,runner,db,migrations,config}.py OK; import_disk_runs 28 runs OK; TestClient /runs?limit=10 → 200, mixed source OK.

### Mechanika importu

- `_RUN_ID_RE` parsuje `YYYYMMDDTHHMMSSZ-xxxxxx` → `started_at` ISO 8601.
- `ended_at`, `status`, `needs_reboot`, `phases` z `run.json`.
- `phase_results` upsertowane per faza. Idempotentne.

### Ryzyka

1. Race przy aktywnym CLI runie — `run.json` nie istnieje do finalize. Filesystem-runs pojawią się po końcu.
2. Brak hot-reload — każde przeładowanie History pokazuje nowe CLI runy, ale stronę trzymaną otwartą trzeba odświeżyć.
3. Profil heurystyką — `only_cat` + `profile=null` gdy single-category run (akceptowalne, profil w History informacyjny).

### Komendy do weryfikacji

```bash
systemctl --user restart ascendo-dashboard.service
./update-all.sh --profile quick --no-notify
curl -s 'http://127.0.0.1:8765/runs?limit=5' | jq '.runs[] | {id, source, profile, status}'
```

---

## 2026-05-04 — Final UX polish + profile templates + apt rollback + GH releases (Etap 10 — release v0.5)

### Stan na koniec sesji (oddajemy do użytkowników)

| Obszar | Status |
|---|---|
| **Slogan vertical pod logo** | ✅ `Ascendo` + tagline, font 0.7rem |
| **Sudo cache** w footer po prawej | ✅ `float:right` w status bar |
| **Theme switcher** auto = monitor icon | ✅ cycle monitor → sun → moon, persist localStorage |
| **Pie chart** czytelny | ✅ total + % ok wewnątrz, legend pod |
| **Sync hints PL/EN** | ✅ każdy guzik z tooltipem |
| **Sync remote dropdown + Browse** | ✅ `/sync/remotes` + `/sync/browse` (rclone lsf --dirs-only) |
| **Categories: drivers + inventory** | ✅ NVIDIA scan + APPS.md metadata |
| **Snap UX** | ✅ `SNAP-AUTO-REFRESHED` diag, blocked snap parser |
| **Help section** | ✅ 11 sekcji, 1rem font, TOC, troubleshooting |
| **About section** | ✅ version + system + Markdown release notes |
| **Hosts edit UI** | ✅ Add/Edit/Delete buttons, `.bak_<ts>` before save |
| **AI providers** | ✅ Anthropic/OpenAI/Gemini/Ollama/LM Studio + test |
| **Per-package apt rollback** | ✅ `/apt/downgrade` + ↓ button per row |
| **Profile templates** | ✅ `config/profiles/{dev-workstation,media-server,minimal-laptop}.list`, CLI `ascendo profile {list,import}` |
| **GH Releases notifier** | ✅ Settings → check_repo, 4s timeout |

### Nowe pliki

config/profiles/{dev-workstation,media-server,minimal-laptop}.list, scripts/apps/profile-import.sh.

### Zmodyfikowane (tej sesji)

app/backend/main.py (+/apt/downgrade, /profiles/*, /updates/check, /sync/remotes, /sync/browse), app/backend/inventory.py (scan_drivers, scan_inventory_meta), app/backend/settings.py (ai.base_url, sync.*, updates.*), app/backend/hosts_edit.py (NEW), app/frontend/{index.html, app.js, style.css}, app/frontend/i18n.js (PL/EN parity), app/frontend/icons.js (monitor, folder), packaging/deb/usr/bin/ascendo (settings/health/exclusions/profile subcommands), scripts/snap/apply.sh (SNAP-AUTO-REFRESHED), scripts/drivers/check.sh (dpkg --compare-versions), update-all.sh (--budget, --no-health, CHECK-ONLY banner).

### Walidacja

bash -n all .sh OK; python3 ast parse all .py OK; JS parse OK; TestClient 31 GET endpoints 31/31 → 200; POST /profiles/import (dry-run) 200 ok; `/apt/downgrade` schema OK; slogan vertical + sudo float:right confirmed; python3 tests/validate_phase_json.py PASS; test_dev_sync_safety.py 9/9 OK; `./update-all.sh --profile quick --no-notify` 6/6 ok, post-run health 100/100; `ascendo profile import dev-workstation --dry-run` added=22, skipped=10.

---

## 2026-05-03 (late) — Sidebar redesign + verbose progress + NVIDIA fix (Etap 8)

Sidebar layout redesign: `<aside id="sidebar">` left, brand+tagline+nav+hostbadge; topbar with utilities (theme/lang/font); hamburger + drawer mobile <768px. Inline SVG icons per nav (22 Lucide-style keys). Responsive grid: 1024 (narrow), 768 (drawer), mobile one-column. Categories add/remove widget. NVIDIA detection fixed: uses `apt_pkg_candidate` + `dpkg --compare-versions` instead of `madison NR==1`; shows "newer: X [dpkg verdict: X > Y]" when candidate > installed. Snap firefox with `--ignore-running` fallback, hint added. CHECK-ONLY yellow banner in CLI.

**Files:** app/frontend/icons.js (NEW), app/frontend/{index.html, style.css, app.js} (layout-shell, sidebar, topbar, responsive), scripts/drivers/check.sh (dpkg --compare-versions), scripts/snap/apply.sh (running-apps hint).

**Validation:** bash -n all .sh OK; python3 ast parse all .py OK; TestClient 22 GET endpoints 22/22 → 200; SPA layout+sidebar+topbar+icons confirmed; `./update-all.sh --only drivers --phase check` shows newer + dpkg verdict; python3 tests/validate_phase_json.py PASS; test_dev_sync_safety.py 9/9 OK.

---

## 2026-05-03 — UX wave 1+2 + AI suggestions + pain-points (Etap 7)

Slogan "unified updates" in UI + i18n PL/EN. Per-category 5-phase buttons (check/plan/apply/verify/cleanup + run all). Snapshot stuck-fix: `timeout` + SUDO_ASKPASS. `config/exclusions.list` + `lib/exclusions.sh` with per-package skip checkbox. Settings backup/restore (`/backup/{export,import}` + CLI). Smart Suggestions panel: heuristics + optional LLM, AI provider settings (Anthropic/OpenAI opt-in read-only). Post-run health check (score 0-100 + issues), ETA from history (avg/p90/ok%), `--budget Ns/m/h` w update-all.sh. Maintenance windows + battery guard dla schedulera. CLI `ascendo` extended: settings/health/exclusions. Stuck dashboard runs cleaned.

**New endpoints:** /suggestions, /suggestions/apply, /suggestions/dismiss, /health/{check,run}, /backup/{export,import}, /telemetry/eta, /exclusions*, /settings.

**New files:** config/exclusions.list, lib/exclusions.sh, scripts/health-check.sh, scripts/scheduler/should-run.sh, app/backend/{suggestions,health,backup,telemetry,exclusions}.py.

**Validation:** bash -n all .sh OK; python3 ast parse all .py OK; python3 tests/validate_phase_json.py PASS (266+); test_dev_sync_safety.py 9/9 OK; TestClient 18 GET endpoints 18/18 → 200; POST endpoints (exclusions, backup, suggestions) 200; `./update-all.sh --profile quick --no-notify` 6/6 ok, post-run health 100/100.

---

## 2026-05-02 — Ascendo brand + i18n + apps (Etap 6)

Branding Ascendo: logo.svg + icon.svg + banner.txt + favicon. CLI i18n (EN/PL): `lib/i18n.sh` + `i18n/{en,pl}.txt`, persisted to `~/.config/ascendo/lang`. CLI tables: `lib/tables.sh` with @ok/@warn/@err/@skip/@info pills, unicode box-drawing. App registration: `scripts/apps/{detect,add,remove,list,install-missing}.sh`. Backend `/apps/*` + `/i18n/*` endpoints. fresh-machine.sh: language pick step 0, apps detect read-only before setup. Wizard step 0 = language radio. Dev-sync TTY pretty output (box + table + ✔). User Journey docs (EN+PL). `bin/ascendo` shim auto-resolve ROOT. `.deb` rebrand: Package=ascendo.

**Files:** branding/{logo.svg,icon.svg,banner.txt}, app/frontend/favicon.svg, lib/{i18n.sh,tables.sh}, i18n/{en.txt,pl.txt}, scripts/apps/{detect,add,remove,list,install-missing}.sh, docs/{en,pl}/user-journey.md, bin/ascendo (NEW).

**Validation:** bash -n all .sh OK; python3 ast parse all .py OK; TestClient 16 GET endpoints 16/16 → 200; python3 tests/validate_phase_json.py 266/266 PASS; test_dev_sync_safety.py 9/9 OK; `bin/ascendo apps detect` tracked=38, detected=308, missing=0; i18n tn apps.summary (PL) "38 śledzonych · 308 wykrytych"; fresh-machine --lang en --check-only OK.

---

## 2026-05-01 — Roadmap implementation (Etap 5)

`.deb` package (packaging/build-deb.sh), first-run wizard modal + /onboarding endpoints, run diff view (/runs/diff?a=X&b=Y), notification routing (ntfy/Slack/email/Telegram), snapshot rollback wired (/snapshots/restore), Markdown report export (/runs/{id}/report.md), per-package live progress apt:apply (awk parser, per-item JSON), token auth middleware (+bearer token, /auth/*, SUDO_ASKPASS), libsecret migration (lib/secrets.sh), audit log (/audit, JSONL writer), Prometheus /metrics (text format, 36 lines, ubuntu_aktualizacje_* metrics), log retention daemon (prune-logs.sh, --keep/--days policy), shellcheck in CI (severity=warning, SC1090/91/2086 ignored).

**Files:** app/backend/{audit,auth,metrics,report,diff}.py, scripts/snapshot/restore.sh, scripts/maintenance/prune-logs.sh, packaging/build-deb.sh + DEBIAN/ subdirs.

**Validation:** bash -n all .sh OK; python3 ast parse all .py OK; TestClient 13 GET endpoints 13/13 → 200; metrics.render() 36 lines OK; report.render_run_id() 4171 chars OK; python3 tests/validate_phase_json.py 266/266 PASS; test_dev_sync_safety.py 9/9 OK.

---

## 2026-04-30 — UX/perf overhaul + portability (Etap 4)

Sudo: one password per CLI run via ephemeral askpass helper ($XDG_RUNTIME_DIR/ascendo/askpass-*.sh, chmod 0700). lib/common.sh::sudo() wraps all calls as `sudo -A`. Live progress: orchestrator tee's phase output to console + log; apt:apply prints upgradable preview. Inventory speed 85s → 11s via `apt_inventory_cache_init` (batched apt-cache policy). Brew cleanup proactive chown Cellar before prune. Dashboard Overview cache via ui._loaded[view]. Reboot UX: banner + POST /system/reboot?delay=5. dev-sync overlay 3527 → 8 files (Cargo target/, Tauri bundle, *.db, .gradle/ excluded). CI guard: overlay ≤ 50 files check. scripts/fresh-machine.sh: one-liner bring-up.

**Validation:** bash -n all .sh OK; python3 ast parse all .py OK; ./update-all.sh --profile quick --no-notify 6/6 ok, 14.5s; python3 tests/validate_phase_json.py 232/232 PASS; test_dev_sync_safety.py 9/9 OK.

---

## 2026-04-29 — Etapy 1+2+3 UKOŃCZONE: Fazyfikacja + Dashboard + Snapshot/Scheduler/Pluginy

**Etap 1 — Phase contract:** `schemas/phase-result.schema.json` (JSON Schema), `lib/json.sh` + `lib/_json_emit.py` emitter, `lib/orchestrator.sh` runner/aggregator, `config/{categories,profiles}.toml` taksonomia. 5 faz × 8 kategorii native scripts/\<cat\>/{check,plan,apply,verify,cleanup}.sh. `update-all.sh` rewritten as thin orchestrator, backward-compat 100% (--only, --dry-run, --no-drivers, --nvidia, --no-notify).

**Etap 2 — Dashboard (Plan B: FastAPI + vanilla SPA):** app/backend/{main,runner,db,config}.py REST + SSE, app/frontend/{index.html,style.css,app.js} vanilla (no build), 5 views (Overview/Categories/Run Center/History/Logs), SQLite history, live log SSE. All endpoints tested: GET /health, /categories, /profiles, /preflight, /git/status, /runs*, /runs/active/stream.

**Etap 3 — Snapshot/Scheduler/Pluginy/Packaging:** scripts/snapshot/{create,list}.sh (timeshift→etckeeper fallback), scripts/scheduler/install.sh (systemd timer generator), lib/plugins.sh manifest scanner, systemd/user dashboard service, share/applications .desktop, app/pyproject.toml package metadata.

**CI:** validate.yml extended with ~70 required files, phase contract tests, bats emitter tests, plugin scanner, backend smoke.

**Validation:** bash -n all .sh OK; python3 ast parse all .py OK; python3 tests/validate_phase_json.py 6/6 PASS; ./update-all.sh --profile quick 6/6 categories ok; plugin scanner OK; backend 7 GET endpoints + E2E POST /runs OK.

---

## Co zostawić po większej pracy

- Krótka lista: decyzje, zmienione pliki, uruchomione walidacje, otwarte ryzyka.
- Status: co jest gotowe, co wymaga kolejnego kroku.

## Kompresja kontekstu

- Przy ~60% kontekstu wykonuj podsumowanie robocze.
- Zachowuj tylko decyzje i aktualny stan; usuwaj zbędne logi i historyczne rozważania.
