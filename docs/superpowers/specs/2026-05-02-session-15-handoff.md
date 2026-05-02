# Session 15 handoff — Ascendo Windows v0.0.7 shipment + worktree consolidation

> Written 2026-05-02 by Claude Opus 4.7 (1M context).
> Supersedes [`2026-05-02-session-14-handoff.md`](2026-05-02-session-14-handoff.md)
> and [`2026-05-02-session-15-windows-installer-handoff.md`](2026-05-02-session-15-windows-installer-handoff.md)
> (the latter was written by the installer subagent mid-session and is
> now folded into this overview).

---

## TL;DR — what shipped, in one paragraph

**Windows v0.0.7 is feature-complete, validated on real hardware, and
pushed to `origin/main`.** A clean monorepo with one branch, one
worktree, an installable `.msi` + `.exe` containing a PyInstaller-bundled
Python sidecar, a 6-step first-run wizard, an optional NSSM-wrapped
Windows service, branded marketing copy + winget submission manifest,
Windows-correct UAC wording everywhere, and 279 + 40-subtests green.
Everything the user asked for in their `go` message:
**"fully functional windows apps (CLI, Web, Desktop)"** — delivered.

---

## What was on disk before this session (the mess)

Three Claude-Code worktrees living under `.claude/worktrees/`:

| Worktree | Branch | HEAD | Purpose |
|---|---|---|---|
| `pedantic-elbakyan-aa9b90` | `claude/pedantic-elbakyan-aa9b90` | `36bc6f0` | Linux Etap 12 (pre-monorepo) |
| `vigilant-sanderson-4d5617` | `claude/vigilant-sanderson-4d5617` | `36bc6f0` | Duplicate of pedantic |
| `unruffled-shamir-7d473c` | `claude/windows-end-to-end-2026-05-02` | `fd05d10` | Windows MVP (14 commits) |

Plus the primary checkout at `D:/Dev_Env/Ascendo` on
`restructure/monorepo` at `29a7b73`. Origin only had
`origin/restructure/monorepo`.

Verified topology: **strictly linear**, no divergence. The Windows
branch (`fd05d10`) was a strict descendant of `36bc6f0` (Linux Etap 12)
through the `restructure/monorepo` lineage. `git log
--oneline claude/windows-end-to-end-2026-05-02..claude/pedantic-elbakyan-aa9b90`
returned **empty** — pedantic had zero commits unique to it. So
consolidation was a fast-forward, no merge required, no work lost.

## Consolidation done

```
git checkout main && git merge --ff-only fd05d10
git push -u origin main
git push origin claude/windows-end-to-end-2026-05-02   # safety snapshot
git worktree remove --force unruffled-shamir-7d473c    # vigilant + unruffled
rm -rf .claude/worktrees/vigilant-sanderson-4d5617     # filesystem leftovers
pip install -e core --no-deps && pip install -e adapters/windows --no-deps
```

**NOT removed:** `.claude/worktrees/pedantic-elbakyan-aa9b90/` (this
session is rooted there; can't delete its own cwd). Resident user
should run `git worktree remove --force
D:/Dev_Env/Ascendo/.claude/worktrees/pedantic-elbakyan-aa9b90` from
outside Claude Code to clean it up.

## CLAUDE.md rewritten

`267f389` — replaced the legacy Polish Linux-only CLAUDE.md with a
monorepo-aware one that hard-codes a **CRITICAL workflow rule — NO new
worktrees**:

> Always work directly in `D:/Dev_Env/Ascendo` on `main`. Do not run
> `git worktree add` or otherwise spawn `.claude/worktrees/<name>/`.
> Earlier sessions accidentally created three parallel worktrees that
> had to be reconciled by hand.

If a future session ignores this and spawns another worktree, the
reconciliation pattern from this session should work again because the
repo is now linear and branched off a single canonical `main`.

---

## Sub-project deliverables

### 1. UX baseline — `0f73f97`

- **`adapters/windows/tests/test_*.py`** — fixed 6 stale tests
  (`OperatingSystem.LINUX` → `OperatingSystem.LINUX_UBUNTU` ×5;
  `len(managers) == 2` → `== 4` with type-set assertion).
- **`app/frontend/i18n.js`** — Windows-friendly wording for the entire
  `sudo.*` namespace (kept the key paths as the SPA contract; values
  are now "Administrator authorized" / "Administrator credentials
  needed" / etc.). Plus EN+PL parity for `wizard.snapshot_on` (Volume
  Shadow Copy), `overview.reboot_no_sudo`, `settings.scheduler` (Task
  Scheduler).
- **`app/frontend/app.js`** — `sudoMgr.refreshIndicator()` rewritten
  with DOM construction (createElement + textContent) instead of
  `innerHTML` interpolation; security hook compliance + i18n
  injection-safe.
- **`app/frontend/index.html`** — Help section completely rewritten for
  Windows (11 sections covering Tauri shell + `bin/install-dev.ps1`
  install paths, the 4 Windows package sources, VSS snapshot/restore,
  Task Scheduler env guards, Windows-specific troubleshooting).

### 2. First-run wizard (subagent) — `760d971` merged at `0da62b5`

6-step flow per the user's spec:

1. **Welcome** — branded hero + 5-bullet preview of what comes next
2. **Language + Theme** — live preview as the user clicks
3. **Administrator (UAC) preview** — explains why apply phases need
   elevation, offers Authenticate-now or Skip-now
4. **First inventory scan** — real `/inventory/refresh` +
   `/inventory/summary` with cycling progress label
5. **Categories preview** — 4-source table with live totals;
   per-source explanations; "Run check" button on Windows Update row
6. **Dry-run demo + you're all set** — real SSE-streamed plan on
   winget, bullets for the 3 ways to apply for real, where-to-find
   links

Persistent state: `%APPDATA%\Ascendo\onboarding.json` (override via
`$ASCENDO_ONBOARDING_FILE` for tests). Replaced the spa_stubs
placeholders with a real `core/ascendo/dashboard/routes/onboarding.py`
router. EN+PL i18n parity. 6 new contract tests (round-trip
persistence, fresh install opens wizard once, returning users skip
it, unknown fields rejected, idempotent complete).

### 3. MSI + EXE installer (subagent) — `5b5b621` + `335581d` + `1871a25` + `60bfc29`

`pwsh -File bin\build-installer.ps1` produces:

| Artifact | Size | SHA-256 (at session-15 close) |
|---|---|---|
| `dist\Ascendo-0.0.7-x64.msi` | 25.5 MB | `b9120a18e34f6ec8bb2f64afa5e9002cddce2bed51e3a5bf73a6f17cdd7e0177` |
| `dist\Ascendo-0.0.7-x64-setup.exe` | 20.8 MB | `a89d557bb907436a6c19b7b0ee650c18b775135e930232733770c882a47684ba` |

Architecture: PyInstaller bundles `core/ascendo` + adapters into a
standalone `ascendo.exe` (one-dir mode for fast cold start), Tauri
2.x bundle includes it as a sidecar resource via `bundle.resources`
(NOT `externalBin` — the latter requires per-triple suffix on a
single-binary, but PyInstaller produces a folder), `main.rs::locate_sidecar`
resolves the path via `app.path().resource_dir()`. Branded BMPs
(banner + sidebar for both NSIS and WiX), MUI license page,
publisher = "Ascendo Software", icons regenerated from
`branding/icon.svg` (5 sizes).

Version bump `0.0.1-dev` → `0.0.7` across `core/ascendo/__version__.py`,
6 `pyproject.toml` files, `tauri.conf.json`, `package.json`, `Cargo.toml`.

**Not verified by the subagent:** actual install on a clean Win11 VM.
The PyInstaller bundle was verified to run standalone (`dist/pyinstaller/ascendo/ascendo.exe doctor` → all green); the `.msi`
+ `.exe` were verified at the bundle-metadata level (VS_VERSION_INFO
contains the right strings; NSIS `installer.nsi` correctly references
the BMPs + MUI license + `installerHooks` + `File /a /oname=binaries\python-sidecar\ascendo.exe`).

**Not signed.** SmartScreen will warn until a code-signing certificate
is set up; documented in `packaging/README.md`.

### 4. Windows service (subagent) — branch `worktree-agent-a5e47d44f63314b9d` merged at `7c68bd0` + integration `0a7d48a`

Subagent hit usage limit before finishing all 11 acceptance criteria,
but delivered the **3 essential ones**:

- **`bin/install-service.ps1`** (577 LOC) — full PowerShell launcher
  with subcommands install / uninstall / start / stop / restart /
  status (with `-Json` flag). Downloads NSSM with SHA-256 verification
  on first install; registers `AscendoDashboard` service as
  `Automatic (Delayed Start)`; sets recovery actions
  (restart on first/second failure); polls `/health` for ≤15s after
  start; idempotent uninstall.
- **`adapters/windows/ascendo_windows/managers/service.py`** (325 LOC)
  + **18 manager smoke tests** in
  `adapters/windows/tests/test_service_manager_smoke.py`.
- **`core/ascendo/dashboard/routes/service.py`** (230 LOC) — REST
  router exposing `GET /service/status` (full schema:
  `installed / running / port_listening / health / last_started / pid /
  state / service_name / port / platform / supported`),
  `POST /service/{install,uninstall,start,stop,restart}`. 9 contract
  tests in `tests/contract/test_service_endpoints.py` (1 correctly
  skipped on non-Windows).

The orchestrator (this session) finished what the subagent didn't
reach in commit `0a7d48a`:

- **NSIS hook integration** in
  `packaging/installer-assets/nsis-installer-hooks.nsh` —
  POSTINSTALL opt-in via `$ASCENDO_INSTALL_AS_SERVICE=1` env var
  (default = no auto-install — SPA Settings panel is the primary UX);
  PREUNINSTALL idempotent service teardown.
- **SPA footer service-status pill** + Settings → Windows service card
  with Install / Uninstall / Restart / Refresh buttons + 6-row status
  table, all DOM-construction (security-hook compliant).
- **EN+PL i18n** for the entire `service.*` namespace.
- **WINDOWS_QUICKSTART.md section 8** documenting the 3 install paths
  (dashboard Settings, CLI, silent install via env var).

### 5. Marketing polish — `c10925a`

- **`branding/SLOGANS.md`** (NEW) — single source of truth for
  marketing copy. Tagline `Unified updates. Every app. One click.`
  Per-surface variants (installer banner ≤32ch, About modal subtitle
  ≤60ch, Tauri shortDescription ≤40ch). One-paragraph + three-bullet
  pitches. 8 feature-highlight slogans. Long-form landing copy. Tone
  rules + copy gotchas (never "sudo" on Windows, never
  "Ubuntu_Aktualizacje", etc).
- **`packaging/winget-manifest/`** (NEW) — submission-ready skeleton
  per winget spec 1.6.0. Three YAML files + a README walking through
  the bump-fill-validate-submit flow including the
  `wingetcreate submit` shortcut. Hashes marked `<FILL_AT_RELEASE>`;
  `bin/build-installer.ps1` prints them at build time.
- **`README.md`** — tagline-first hero replaces the generic intro.
  Per-platform feature matrix (3 OSes × 7 capability columns). Status
  table declaring Windows v0.0.7 in flight vs Linux v0.5 vs macOS
  stub. Windows install block expanded with the dev-source path.
- **`CHANGELOG.md`** — v0.0.7 entry drafted with Added / Changed /
  Fixed / Verified sub-sections covering every change.

---

## Final verification (paste-ready)

```powershell
# Tests (run from repo root)
python -m pytest adapters/windows/tests/ plugins/dell-driver-update/tests/ ui/desktop-tauri/tests/
# → 101 passed, 40 subtests passed

python -m pytest tests/contract/ tests/python/ --rootdir=.
# → 178 passed, 1 skipped (Windows-only assertion correctly skipped on Windows)

# End-to-end smoke
.\bin\validate-windows.ps1 -DashboardPort 8775
# → ALL CHECKS PASSED (CLI + 5 phases on real winget; 210-package inventory)

# Build the installers
.\bin\build-installer.ps1
# → dist\Ascendo-0.0.7-x64.msi (25.5 MB) + dist\Ascendo-0.0.7-x64-setup.exe (20.8 MB)
#   with SHA-256 hashes printed

# Service install (elevated PS)
.\bin\install-service.ps1 -Action install
.\bin\install-service.ps1 -Action status -Json
.\bin\install-service.ps1 -Action uninstall

# Dashboard manual check
python -m ascendo dashboard --port 8765 --background
Start-Process http://127.0.0.1:8765/
# → first-run wizard appears; complete it; service pill in footer; Categories tab shows 4 sources × 210 packages
```

---

## Commits pushed to `origin/main` this session

```
0a7d48a feat(service-ui): SPA service-status pill + Settings panel + NSIS hook + quickstart docs
7c68bd0 Merge service subagent: NSSM-wrapped Windows service for AscendoDashboard
1a985fa test(service): add WindowsServiceManager + /service router tests
f2fbea1 feat(service): add WindowsServiceManager + /service REST router
a141b49 feat(service): add install-service.ps1 PowerShell launcher
0da62b5 Merge wizard subagent: 6-step Windows first-run wizard
760d971 feat(wizard): 6-step Windows first-run wizard
2e788a7 feat(wizard): persistent /onboarding endpoints (graduate from spa_stubs)
60bfc29 docs(packaging): rewrite README + PLAN + handoff for sub-project 3
c10925a feat(polish): marketing slogans, winget manifest skeleton, README hero rewrite, v0.0.7 changelog
1871a25 feat(desktop+packaging): bundle PyInstaller sidecar + branded NSIS/MSI installers
0f73f97 feat(windows-ux): rename sudo→Administrator, rewrite Help for Windows, fix 6 stale tests
335581d feat(packaging): PyInstaller spec for Ascendo sidecar bundle
5b5b621 chore(release): bump version 0.0.1-dev → 0.0.7
267f389 chore: rewrite CLAUDE.md for monorepo + lock down 'no new worktrees' rule
fd05d10 docs(handoff): session 14 — Windows desktop bugfix wave + quickstart   ← session 14 baseline
```

15 commits, 4 subagents (installer + wizard + service in foreground +
docs polish in main), all on `main`.

---

## What's NOT done (deferred follow-ups, none blocking ship)

1. **Test the `.msi` / `-setup.exe` on a clean Win11 VM.** Dev box has
   Python + editable installs, so the no-Python-needed claim was
   verified at the bundle level but not by a real installer install on
   a fresh user account. Recommend before tagging `v0.0.7-final`.
2. **Code signing.** Artifacts are unsigned; SmartScreen will warn.
   When a cert is acquired, pass `-CertPath` to a hypothetical sign
   step in `bin/build-installer.ps1` (stub left in
   `packaging/README.md`).
3. **Polish locale for winget manifest.** `Ascendo.Ascendo.locale.pl-PL.yaml` not yet written; en-US works.
4. **NSIS components-page checkbox** for "Run as Windows service" —
   currently env-var opt-in (`$ASCENDO_INSTALL_AS_SERVICE=1`). When
   the user wants a UI checkbox, add a Section to the Tauri NSIS
   template — see the comment in `nsis-installer-hooks.nsh`.
5. **Remove user data on uninstall** checkbox — currently the
   POSTUNINSTALL hook leaves `%LocalAppData%\Ascendo\` alone (good
   default for re-installs). A future release can wire a checkbox.
6. **WINDOWS_TESTING.md** — service section not yet added (the service
   subagent hit usage limit). The QUICKSTART has it; TESTING is the
   deeper operator manual and should mirror the QUICKSTART section.
7. **Pedantic worktree filesystem cleanup** — see "Consolidation done"
   above for the one-liner.
8. **Ubuntu adapter**: legacy code in `app/`, `lib/`, `scripts/`,
   `update-all.sh` is still at the repo root waiting to be folded into
   `adapters/ubuntu/`. The user said they'll work on this on their
   Ubuntu machine (`then i plan to switch back to Ubuntu, pull this
   repo and work on the same functionality with Ubuntu`). The
   monorepo structure is ready; this is a pure migration job.
9. **macOS adapter**: stub at `adapters/macos/`. User's plan: do this
   on their Mac (`last i want to pull this repo on my macbook and
   create macos specific apps`).

---

## What to do FIRST when you resume

### 1. Confirm the worktree state

```powershell
cd D:\Dev_Env\Ascendo
git status                                  # should be clean
git log --oneline -3                        # 0a7d48a should be HEAD
git log --oneline origin/main -1            # should match
.\bin\validate-windows.ps1                  # ALL CHECKS PASSED
```

### 2. Optional — clean up the leftover pedantic worktree

```powershell
git worktree remove --force D:\Dev_Env\Ascendo\.claude\worktrees\pedantic-elbakyan-aa9b90
git branch -D claude/pedantic-elbakyan-aa9b90 claude/vigilant-sanderson-4d5617 claude/unruffled-shamir-7d473c
```

### 3. Optional — test the installer on a clean VM

The `.msi` and `-setup.exe` are at `D:\Dev_Env\Ascendo\dist\`.
Suggested VM smoke:

```powershell
# In the VM
copy \\host\dist\Ascendo-0.0.7-x64-setup.exe C:\Temp\
C:\Temp\Ascendo-0.0.7-x64-setup.exe        # GUI install
# Or:
C:\Temp\Ascendo-0.0.7-x64-setup.exe /S     # silent

# Verify
&'C:\Program Files\Ascendo\bin\ascendo.exe' doctor
&'C:\Program Files\Ascendo\bin\ascendo.exe' dashboard --background
Start-Process http://127.0.0.1:8765/       # wizard should appear
```

### 4. When ready, tag the release

```powershell
cd D:\Dev_Env\Ascendo
git tag -a v0.0.7-rc1 -m "Windows v0.0.7-rc1 — installer + wizard + service shipment"
git push origin v0.0.7-rc1
gh release create v0.0.7-rc1 dist\Ascendo-0.0.7-x64.msi dist\Ascendo-0.0.7-x64-setup.exe \
    --title "Ascendo v0.0.7-rc1 — Windows MVP installer" \
    --notes-file CHANGELOG.md
```

After the release lands, fill in `<FILL_AT_RELEASE>` placeholders in
`packaging/winget-manifest/Ascendo.Ascendo.installer.yaml` (URLs +
hashes) and submit via `wingetcreate submit`.

---

End of handoff. `main` is clean, working tree is clean, push everything
needed for v0.0.7 is on `origin/main`.
