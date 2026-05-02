# Ascendo Windows end-to-end — A + B + C design

> Status: approved 2026-05-02. Author: Claude Opus 4.7 (1M context). Owner: m.kasprowicz@gmail.com.
>
> Branch: `claude/windows-end-to-end-2026-05-02` (off `restructure/monorepo`).
> Successor of session 12. Closes the 30-min path to v0.0.7-alpha tag plus the
> two next-milestone items the user said they want testable today: **frontend
> apply UX on Windows** and **Tauri 2.x desktop shell on Windows**.

---

## 1. Why this exists

PLAN.md lists three things that must land before the user can self-validate
end-to-end on Windows in CLI / web / desktop:

1. **Finish v0.0.7-alpha (sub-project A)** — placeholder CLI commands
   (`snapshot`, `schedule`) wired to existing managers; reboot detection
   surfaced as a distinct exit code; `runs json` for piping; the 5 Dell
   plugin scripts brought up to the StrictMode-safe pattern.
2. **Frontend apply UX (sub-project B)** — the legacy SPA at
   `app/frontend/` is mounted by the dashboard (✅) but most of its data
   endpoints are stubs in `core/ascendo/dashboard/routes/spa_stubs.py`.
   For the user to "apply updates from the web UI" we need: live inventory
   per category, real `POST /runs/async` wiring, SSE log stream, health
   card, and a confirmation modal that mirrors `bin/run-apply.ps1`.
3. **Tauri 2.x desktop shell (sub-project C)** — `app/tauri/` exists on
   Tauri 1.x with a bash build script; we need a Tauri 2.x scaffolding
   that spawns `python -m ascendo dashboard --background` as a sidecar
   and points a webview at it. Builds an `.exe` for local testing only;
   signing is M4.

These are independent enough to parallelise, with one sequencing
constraint: B3-B5 (apply UX) depends on B1+B2 (live inventory + run
endpoints) being wired first.

## 2. Scope

| In scope | Out of scope |
|---|---|
| A1: wire `ascendo snapshot` to `WindowsSnapshotManager` | Real `winget` apply (user runs from Admin PS) |
| A2: exit code 75 + stderr line on `needs_reboot=true` | New PowerShell scripts |
| A3: `ascendo runs json <id>` consolidated report | Sidecar schema changes |
| A4: fix 5 Dell plugin PowerShell scripts | New `dcu-cli.exe` test fixtures |
| B1: replace `/categories`, `/inventory`, `/inventory/summary`, `/inventory/{cat}` stubs with real adapter calls | Move `app/frontend/` → `ui/frontend/` (defer to M4 — already mounted) |
| B2: replace `/health/check` stub with real adapter health snapshot | New i18n keys beyond what the SPA already has |
| B3: `POST /runs/async` is real already; replace `/runs/active`, `/runs/active/stream`, `/runs/active/stop` stubs with `RunRegistry`-backed real impls | New SSE event types beyond `status / sidecar / log / done` |
| B4: confirmation modal in SPA before apply, mirroring `bin/run-apply.ps1` | Auth/SSO |
| B5: per-category 5-phase buttons (check/plan/apply/verify/cleanup) wired to `POST /runs/async` | New profile editor |
| B6: self-host Inter Tight + JetBrains Mono woff2; remove Google Fonts CDN | New fonts |
| B7: 5th wizard step — theme picker | Onboarding rework |
| C1: Tauri 2.x scaffold under `ui/desktop-tauri/` | Code signing (M4) |
| C2: sidecar that spawns FastAPI on ephemeral port | Auto-update channel |
| C3: branded window 1280×800 with favicon | Native menus / tray |
| C5: `bin/launch-desktop.ps1` for one-click smoke | MSI installer (M4) |

## 3. Architecture

No new architectural decisions. Reuses existing layers:

```
┌─────────────────────────────────────────────────────────────────┐
│  C: Tauri 2.x shell (ui/desktop-tauri/) — Rust + WebView2       │
│      ↓ spawns python -m ascendo dashboard --background          │
│      ↓ webview points at http://127.0.0.1:<ephemeral>/          │
├─────────────────────────────────────────────────────────────────┤
│  B: SPA at app/frontend/ — vanilla JS                           │
│      ↑ fetches: real /categories, /inventory*, /health/check    │
│      ↑ POST /runs/async + SSE /runs/active/stream               │
├─────────────────────────────────────────────────────────────────┤
│  A: Core CLI (core/ascendo/cli/)                                │
│      ↑ ascendo snapshot create|list|restore                     │
│      ↑ ascendo schedule install|remove|list|trigger             │
│      ↑ ascendo runs json <id>                                   │
│      ↑ exit 75 on needs_reboot                                  │
├─────────────────────────────────────────────────────────────────┤
│  Existing: WindowsAdapter + 7 managers (winget/msstore/arp/wu/  │
│            snapshot/scheduler/elevation) — UNCHANGED            │
└─────────────────────────────────────────────────────────────────┘
```

## 4. Data flow per slice

### A1 — `ascendo snapshot create|list|restore`

```
CLI typer.command("snapshot")
  → adapter.snapshots() → WindowsSnapshotManager
    → create: VSS via PowerShell
    → list: read from registry
    → restore: VSS revert
  → typer.echo(table) + exit 0
```

### A2 — exit 75 on needs_reboot

```
run_phases() → returns RunReport with needs_reboot field aggregated
  from per-phase sidecars
CLI run() command checks report.needs_reboot
  → if True: typer.secho(reboot warning, RED, err=True); raise Exit(75)
```

### A3 — `ascendo runs json <id>`

```
read_run(runs_dir/<id>) → list[Sidecar]
  → consolidate into JSON blob: {run_id, status, started_at,
     finished_at, sidecars: [...], summary}
  → typer.echo(json.dumps(...))
```

### A4 — Dell plugin scripts

Replace 5 files in `plugins/dell-driver-update/windows/*.ps1` using
`adapters/windows/scripts/winget/check.ps1` as the canonical template.
Same StrictMode-safe pattern, swap winget calls for `dcu-cli.exe /scan`
(check), `/applyUpdates -reboot=disable` (apply), no-op (cleanup).

### B1+B2 — wire stubs to real adapter

Edit `core/ascendo/dashboard/routes/spa_stubs.py` (or move handlers to a
new `routes/spa_real.py` to keep stubs untouched and the inventory list
honest):

```python
@router.get("/inventory")
async def inventory_real(request):
    adapter = request.app.state.adapter
    inv = adapter.inventory()                  # IInventory
    host = adapter.detect_host()
    packages = inv.list_installed(host)         # 137 items on DP5520WMK
    return _bucket_packages(packages)
```

The stub already does this — but classifies status incorrectly and lacks
caching. Fix: introduce `InventoryCache` (60s TTL) keyed by host hash,
and switch `status` calculation to `_version_gt()` (port the fix from
Etap 12 of the legacy code).

### B3 — real SSE stream

Existing `RunRegistry` (M2.10) tracks runs in memory. Replace
`runs_active_stream_stub()` with a real generator that:

1. Reads `request.app.state.run_registry`.
2. Subscribes to the active run's event queue (per-run asyncio.Queue).
3. Emits `event: sidecar\ndata: {sidecar_json}\n\n` per phase finish.
4. Emits `event: done\ndata: {report_json}\n\n` on completion.

`run_async.py` already publishes events to the queue — just need to
expose the consumer.

### B4 — confirmation modal

In `app/frontend/app.js`, add:

```js
async function confirmApply(category, phase) {
  if (phase !== 'apply') return true;
  const typed = await openModal({
    title: `Confirm real apply on ${category}`,
    body: `This will mutate your system. Type 'apply' to proceed:`,
    inputType: 'text',
  });
  return typed === 'apply';
}
```

Wired to the per-category 5-phase buttons added in B5.

### B5 — per-category 5-phase buttons

In Categories view (already exists), add a row of 5 buttons per category:
`check / plan / apply / verify / cleanup`. Each calls
`POST /runs/async` with `{phases: [phase], categories: [cat]}` and
opens the live log panel via SSE. Apply button gates on `confirmApply`.

### B6 — self-host webfonts

Currently `app/frontend/index.html` imports Google Fonts CDN. Drop the
`@import` rule. Add `app/frontend/fonts/` with woff2 files. Add
`@font-face` declarations in `colors_and_type.css`.

### B7 — wizard 5th step

Existing wizard has 4 steps. Add 5th: theme picker (dark / light / auto).
Persists to `localStorage.ascendo_theme`. Apply on next paint.

### C1+C2+C3 — Tauri 2.x

```
ui/desktop-tauri/
  package.json                    # @tauri-apps/cli@^2
  src-tauri/
    Cargo.toml                    # tauri@^2 deps
    tauri.conf.json               # window 1280x800, identifier dev.ascendo.app
    src/main.rs                   # spawn sidecar, build window
    icons/                        # .ico .png from branding/
    capabilities/
      default.json                # webview, http, dialog
  src/                            # webview content (a thin shell that just
                                  # redirects to the sidecar URL)
    index.html
    main.js
```

`main.rs` boots:
1. Pick ephemeral port (try 8765 first, fall back to 0)
2. Spawn `python -m ascendo dashboard --port <port> --host 127.0.0.1`
3. Wait for `/health` to respond (max 10s, 200ms poll)
4. Open WebView pointed at `http://127.0.0.1:<port>/`
5. On window close: send SIGTERM to sidecar

### C5 — `bin/launch-desktop.ps1`

Wraps the dev-mode launch:

```powershell
cd ui/desktop-tauri
npm install   # if first run
npm run tauri dev
```

For packaged build: `npm run tauri build` — produces `target/release/bundle/`.

## 5. Testing strategy

| Slice | Test |
|---|---|
| A1 | `pytest adapters/windows/tests/test_snapshot_cli.py` (new) — Typer CliRunner against `ascendo snapshot create --dry-run` |
| A2 | `pytest tests/contract/test_cli_exit_codes.py` — fake report with `needs_reboot=True`, assert exit 75 |
| A3 | `pytest tests/contract/test_runs_json.py` — synthetic sidecars on disk, assert JSON shape |
| A4 | `Invoke-Pester plugins/dell-driver-update/windows/*.Tests.ps1` — sidecar contract test against fixture |
| B1+B2 | `pytest tests/contract/test_dashboard_inventory.py` — TestClient hitting `/inventory` with fake adapter, assert 137 mock items pass through |
| B3 | `pytest tests/contract/test_dashboard_async.py` (extend existing) — assert SSE emits `event: sidecar` per phase |
| B4+B5 | Manual smoke + Playwright if available |
| B6 | Visual: open SPA in browser, no Google Fonts requests in DevTools network tab |
| B7 | Manual smoke: open wizard, step 5 visible, theme persists across reload |
| C1-C5 | `bin/launch-desktop.ps1` opens window, dashboard loads, `/health` returns 200 |

End-to-end: `bin/validate-windows.ps1` (existing) must continue passing.

## 6. Risks

1. **Tauri 2.x toolchain on Windows** — needs Rust + WebView2. If
   missing, agent C falls back to producing the scaffold + a
   `bin/install-tauri-prereqs.ps1` script for the user, marking C as
   "scaffold ready, build follow-up".
2. **Real apply step stays user-driven** — every dispatched agent
   knows: never run `winget apply` from the session, never call
   `Save-VMSnapshot` against the live VSS store, never write to
   HKLM without explicit user confirmation. All "apply" tests use
   `--dry-run`.
3. **Subagent context drift** — each Wave 1 agent gets a self-contained
   prompt that names exact files, exact functions, exact tests. No
   "based on the design, do the right thing".

## 7. Wave plan (orchestration)

```
Wave 1 (parallel, 4 agents):
  W1-A1: snapshot/schedule CLI wiring          → core/ascendo/cli/__init__.py
  W1-A2: exit 75 + stderr on needs_reboot      → core/ascendo/cli/__init__.py
  W1-A3: ascendo runs json <id> command        → core/ascendo/cli/__init__.py
  W1-A4: Dell plugin StrictMode-safe rewrite   → plugins/dell-driver-update/windows/*

Wave 2 (parallel, 3 agents) — runs after Wave 1:
  W2-B1: real /inventory + InventoryCache      → core/ascendo/dashboard/routes/spa_real.py (NEW)
  W2-B6: self-host fonts                        → app/frontend/{fonts/,colors_and_type.css,index.html}
  W2-B7: wizard 5th step                        → app/frontend/{app.js, i18n.js}

Wave 3 (parallel, 3 agents) — runs after Wave 2:
  W3-B2: real /health/check                     → core/ascendo/dashboard/routes/spa_real.py
  W3-B3: real SSE stream                        → core/ascendo/dashboard/routes/spa_real.py
  W3-B4+B5: per-category buttons + apply modal  → app/frontend/{app.js, style.css}

Wave 4 (parallel with Wave 2/3, 1 agent):
  W4-C: Tauri 2.x scaffold + sidecar + window  → ui/desktop-tauri/* (NEW)

Wave 5 (final, single agent + me):
  W5: integration tests, bin/run-tag-release.ps1, validate-windows.ps1 still passes,
      MIGRATION.md / WINDOWS_TESTING.md updates, hand off
```

Wave 1 ends after the 4 agents commit and the test suite for `core/`
passes. Wave 2 is dispatched only after Wave 1 finishes.

## 8. Acceptance criteria

The user can, on their Windows host:

1. Run `python -m ascendo snapshot create` and see a VSS snapshot id.
2. Run `python -m ascendo schedule install --weekly` and see a Task
   Scheduler entry.
3. Run `python -m ascendo runs json <id> | jq .summary` and get a
   one-line summary.
4. Run `python -m ascendo run --phase apply --dry-run` and see exit
   code 75 if any sidecar reports `needs_reboot=true` (synthesised
   case for testing — real reboot detection requires real apply).
5. Open the dashboard, see 137 packages bucketed by source (winget /
   msstore / arp / wu) with status pills.
6. Click "apply" on a category, get the confirmation modal, type
   `apply`, watch the SSE log stream until done.
7. See the post-run health card update.
8. Open the desktop app via `bin/launch-desktop.ps1`, see the same
   dashboard rendered inside a Tauri window.
9. `bin/validate-windows.ps1` still prints `ALL CHECKS PASSED.`.
10. The 5 Dell plugin scripts produce schema-valid sidecars (verified
    against `tests/fixtures/sidecars/` examples).

## 9. Rollback

Every commit is on the `claude/windows-end-to-end-2026-05-02` branch.
If a slice causes regression, `git revert` that commit. The branch
will be merged via PR after the user smoke-tests; until then,
`restructure/monorepo` remains stable.

## 10. References

- PLAN.md §"Immediate next steps", §"M4 — Distribution"
- HANDOFF.md FAST RESUME (2026-05-01)
- ADR-0003 (sidecar v1), ADR-0005 (six-layer), ADR-0007 (plugin manifest)
- `core/ascendo/dashboard/routes/spa_stubs.py` — endpoint inventory
- `bin/run-apply.ps1` — confirmation pattern reference
- `adapters/windows/scripts/winget/check.ps1` — PowerShell template

---

*End of design doc. Implementation plan is dispatched as Waves 1-5 below.*
