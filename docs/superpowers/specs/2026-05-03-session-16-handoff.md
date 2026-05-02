# Session 16 handoff — 7 user-reported bugs + Run Center detail panel

> Written 2026-05-03 by Claude Opus 4.7 (1M context).
> Supersedes [`2026-05-02-session-15-handoff.md`](2026-05-02-session-15-handoff.md).
> Read this first when resuming.

---

## TL;DR

Session 15 shipped v0.0.7-rc1 with the installer, wizard, service, and
marketing polish. The user installed it on their real Win11 dev box,
walked through the dashboard, and surfaced 7 distinct bugs / UX gaps
in their next message. Session 16 fixed all 7 + shipped a brand-new
Run Center live-detail panel via subagent. Tagged v0.0.7-rc2 and
pushed everything to origin/main. **287 + 40 subtests green**;
`bin/validate-windows.ps1` ALL CHECKS PASSED on the same DP5520WMK.

---

## What the user reported (verbatim, with root cause)

| # | User report | Root cause | Fix |
|---|---|---|---|
| 1 | "Apps menu still shows nothing even after refresh, can't add any apps into my config" | `/apps/detect` was a stub returning empty arrays. The frontend rendering relied on items existing | New `core/ascendo/dashboard/routes/apps.py` with default-include model: every detected app is `in_config=true` unless on the exclusion list. Apps tab UI rewritten to use the new shape with per-app checkbox |
| 2 | "By default all apps should be added [to config], then if I don't want to update one, I either skip it in apps or remove it from config in categories" | Spec change: from "explicit allowlist" to "implicit allowlist with opt-out exclusion list" | `/apps/exclude` and `/apps/include` POST endpoints + persistent `%APPDATA%\\Ascendo\\excluded.json`. Categories tab "remove" button now writes to the exclusion list (legacy `/apps/add` and `/apps/remove` preserved as no-op shims for any cached SPA tabs) |
| 3 | "Even if I run check, nothing is populated into categories — no version installed, no candidate, no in config" | `/inventory/{cat}` returned a static snapshot from `adapter.inventory()` which only knows names — installed/candidate are filled by check phase but never merged back | New `_latest_check_overlay(runs_dir, category)` in `spa_real.py` finds the most-recent `<run-id>/<category>/check__<category>.json` sidecar (bounded scan: latest 50 runs) and merges its items[] into inventory rows. `/inventory`, `/inventory/{cat}`, `/inventory/summary` all enriched |
| 4 | "Overview System Health card shows TypeError: Cannot read properties of undefined (reading 'map')" | `/health/check` returned `{score, status, components, failed, checked_at}` but the SPA's `loadHealth` checked `h.available` (undefined → falsy → returned "no data") and called `(h.issues || []).map()`. Without an `available: true` flag the card showed the no-data state; the .map error came from a downstream rendering path also expecting `issues` to exist | `/health/check` now returns `available: true` + `issues: [{severity, msg}, ...]` shaped exactly as the SPA renders. Defensive `\|\| []` retained on the SPA side |
| 5 | "In Available Updates it says everything is up to date, when I know it isn't" | `/inventory/summary` counted from un-enriched buckets where every `status` was 'ok' (because installed/candidate were always None) | Fixed by #3 above — same enrichment applied to summary. Run check once on a category and the outdated count flips |
| 6 | "In About menu, add Windows-related release notes from GitHub commits. Help menu still shows Ubuntu-related help — update it for Windows, depending on platform" | `/about` was a stub; Help section was hardcoded HTML | New `core/ascendo/dashboard/routes/about.py`: `/about` returns `platform=windows/linux/macos`, `/about/release-notes` parses CHANGELOG.md into platform-tagged entries (matching by header keywords + platform mentions in body). Help section: `<article data-platforms="windows">` wraps the existing Windows operator manual; `loadHelp(platform)` hides off-platform sections. Linux/macOS slot in cleanly when adapters migrate |
| 7 | "There is no system icon and option for theme, you lost it somehow" | Session-14 UX-baseline pass binary-fied the theme cycle to dark↔light, dropping the auto/system option | Restored 3-state cycle: dark → light → auto. matchMedia listener re-applies on OS theme change so 'auto' is genuinely live. Icon set: moon / sun / monitor (all already in icons.js) |

Plus the user's 8th ask: **"In Run Center I need to have full information what is going on in each step with progress bars, detailed info about the packages found, candidates, installation, everything in full below sidecar."** → delivered by subagent (see "Subagent delivery" below).

---

## Backend changes (this session)

### `core/ascendo/dashboard/routes/apps.py` (NEW — 224 LOC)

Default-include apps tracker. Endpoints: `/apps/detect` (returns every
detected app + `in_config` flag), `/apps/exclude` + `/apps/include`
(POST `{category, name}`, idempotent), `/apps/excluded` (raw store).
Persistent JSON at `%APPDATA%\\Ascendo\\excluded.json`
(override via `$ASCENDO_EXCLUDED_FILE` for tests). Atomic writes via
temp + rename. The store is ≤ a few KB even on installs with hundreds
of exclusions.

### `core/ascendo/dashboard/routes/about.py` (NEW — 178 LOC)

Replaces the `spa_stubs.about_stub`. `/about` returns the full host
info plus `platform: "windows" | "linux" | "macos" | "unknown"`.
`/about/release-notes?platform=<p>&limit=N` parses CHANGELOG.md (single
source of truth — no separate per-OS files, no GitHub API call) into
`{version, body, platforms[]}` entries via header regex
`^## \[(?P<version>[^\]]+)\]`. Platform tag inferred from body
mentions (`windows`, `linux`, `macos`, `mac os`, `ubuntu`, `debian` —
the latter two normalised to `linux`); entries with no mention are
treated as cross-platform (returned for every `?platform=`).

### `core/ascendo/dashboard/routes/spa_real.py` (modified)

- `_build_health_snapshot` adds `available: True` + `issues: [{severity, msg}]`
  per component that's not "ok".
- New `_latest_check_overlay(runs_dir, category)` reads the most-recent
  `<run-id>/<category>/check__<category>.json` sidecar (bounded scan:
  latest 50 runs by mtime) and returns
  `{name: {installed, candidate, status}}`.
- New `_enrich_items(items, category, runs_dir, excluded)` overlays
  the check result + applies `in_config = key not in excluded`.
- `/inventory`, `/inventory/{cat}`, `/inventory/summary` all enriched.
- Re-classifies status (`outdated` vs `ok`) when the overlay fills in
  versions.

### `core/ascendo/dashboard/routes/spa_stubs.py` (modified)

- Removed the `/about` and `/apps/detect` stubs (graduated).
- Inventory table updated: `/about`, `/about/release-notes`,
  `/apps/detect`, `/apps/excluded` flagged "served"; `/apps/exclude`
  and `/apps/include` flagged "served".

### `core/ascendo/dashboard/app.py` (modified)

Registered `apps_router` and `about_router` BEFORE `spa_stubs_router`
so the real handlers win on path collisions (mirrors the pattern used
for `onboarding_router`).

---

## Frontend changes (this session, my edits + subagent merge)

### `app/frontend/app.js` (heavy)

- `loadApps()` rewritten end-to-end for the default-include model.
  Pure DOM construction (createElement + textContent) so the security
  hook is satisfied. Columns: Category / Package / Installed /
  Candidate / Status / In config (checkbox).
- `toggleExclusion(pkg, cat, on)` POSTs to `/apps/include` (on=true)
  or `/apps/exclude` (on=false).
- `appsAdd` / `appsRemove` redirected to `/apps/include` /
  `/apps/exclude` so legacy "remove" / "+ add" buttons in Categories
  expanded rows still work.
- Theme cycle 3-state: dark → light → auto (`NEXT_THEME` map +
  `ICON_FOR` map). matchMedia listener re-applies on OS change.
  `applyTheme("auto")` reads `prefers-color-scheme` and resolves to
  dark or light, but keeps `data-theme-pref="auto"` so the cycle
  resumes from the user's intent.
- `loadAbout()` rewritten with DOM construction + a real fetch of
  `/about/release-notes?platform=<detected>` rendered as one
  `<section>` per CHANGELOG entry. Stashes platform on
  `document.documentElement.dataset.platform` so loadHelp can branch.
- `loadHelp(platform)` (NEW) hides `[data-platforms]`-tagged sections
  that don't include the current platform, and inserts a small banner
  "Operator manual for Windows" so the user knows what they're looking
  at.
- Subagent's `runDetail` controller (566 LOC IIFE) ingests SSE
  `sidecar` events and renders the full live detail panel.

### `app/frontend/i18n.js`

- New `apps.*` keys (en + pl): `col_in_config`, `in_config_on/off`,
  `in_config_on_hint`, `in_config_off_hint`, `pill_total`,
  `pill_tracked`, `pill_excluded`, `empty`.
- Existing `apps.hint` rewritten for the default-include model.
- Subagent added a full `run.detail.*` namespace (39 leaf keys + 8
  `status.*` sub-keys) for both en and pl.

### `app/frontend/index.html`

- Help `<article>` tagged `data-platforms="windows"` so `loadHelp`
  hides it on other platforms.
- Subagent added `<section id="run-detail-panel">` inside `#view-run`
  after the existing `#run-progress` summary card.

### `app/frontend/style.css`

- Subagent added the entire `.run-detail*` CSS family using design
  tokens (--ok / --warn / --err / --info / --fg / --bg / --border).
  ~237 lines.

---

## Subagent delivery — Run Center live detail panel

`worktree-agent-ac5705e8e77381971` → merged at commit on main. The
panel sits BELOW the existing per-phase sidecar summary cards
(unchanged) and renders, per (phase, source) pair the user is
inspecting:

1. **Top progress bar** — phase name, source, % complete (`processed/total`),
   elapsed time, ETA placeholder (degrades gracefully when
   `/telemetry/eta` is still a stub).
2. **Packages list** — every package the current phase touched, with
   installed / candidate versions, status badge, per-package mini
   progress bar (strobe fallback when winget doesn't stream item
   progress), click-to-expand for item-level diagnostics.
3. **Diagnostics tail** — chronological `messages[]` from the sidecar,
   auto-scroll toggle that respects manual scroll.
4. **Phase-source navigator** — chip row (max 4 sources × 5 phases =
   20 chips, wraps); default-selects the most recent phase to land or
   actively running.

8 new tests in `tests/python/test_run_detail_panel.py` (smoke:
markup IDs, CSS classes, data-i18n bindings, design-token usage, safe
DOM-only construction, EN+PL parity).

---

## Verification (everything green at session-16 close)

```powershell
python -m pytest adapters/windows/tests/ plugins/dell-driver-update/tests/ ui/desktop-tauri/tests/
# → 101 passed + 40 subtests passed in 1.26s

python -m pytest tests/contract/ tests/python/ --rootdir=.
# → 186 passed, 1 skipped (Windows-only assertion correctly skipped on Windows)

# Total: 287 + 40 subtests passing. Up from 279 + 40 last session
# (+8 run-detail-panel tests).

bin/validate-windows.ps1 -DashboardPort 8782
# → ALL CHECKS PASSED on real DP5520WMK:
#   - 210-package inventory across 4 sources
#   - All 5 phases (check/plan/apply/verify/cleanup) on real winget
#   - Async run completes in 17.2s
#   - Every dashboard endpoint healthy (/categories, /inventory,
#     /inventory/summary, /health/check, /runs/active, /, fonts)
```

---

## Commits pushed to `origin/main` (this session)

```
9bb8751 Merge subagent: Run Center live detail panel below sidecar
85337aa feat(run-detail): live progress / packages / diagnostics panel below sidecar (subagent)
7038b42 feat(spa+backend): fix 7 user-reported bugs + default-include apps + platform-aware about/help
```

3 commits, 1 subagent, 1 tag (`v0.0.7-rc2`).

---

## Outstanding for tomorrow (next session pickup)

1. **Test the live detail panel on real hardware** — start the
   dashboard, open Run Center, kick off a winget check, watch the
   panel populate. The static endpoint smoke is green; live rendering
   was eyeballed in TestClient but a real human-eye check is the
   useful confirmation.
2. **Test the theme switcher 3-state on real hardware** — click the
   icon thrice, watch dark → light → auto cycle. With auto selected,
   change OS dark mode in Windows Settings; the SPA should follow
   within a fraction of a second via the matchMedia listener.
3. **Test the Apps menu default-include** — open the Apps tab, see
   the 210-row table, uncheck a row's "in config" checkbox, click
   Categories → that source → confirm the row's "In config" column
   reads "—" (or the X marker, depending on style). Reload the page;
   exclusion persists.
4. **Test Help platform-aware** — should already render Windows
   manual + a small banner "Operator manual for Windows".
5. **Test About release notes** — should show 3 entries from
   CHANGELOG (v0.0.7, v0.0.1-alpha, etc.) tagged for windows.
6. **Verify the apps-menu 'in config' column in Categories tab** —
   open any category, expand a row's package list, the "In config"
   column should show ✔ (checked from the in_config flag now flowing
   through `/inventory/{cat}` enrichment).
7. **Build a fresh installer + test on a clean VM** if you want to
   tag `v0.0.7-final` instead of `-rc2`. The MSI/EXE artifacts in
   `dist/` are still from rc1; rebuild with
   `pwsh -File bin\build-installer.ps1` to pick up the new dashboard
   code.
8. **Optional: file PR / merge polish** — the tag `v0.0.7-rc2` is on
   origin; consider opening the GitHub release with the
   `dist/Ascendo-0.0.7-x64-setup.exe` + `.msi` attached and the
   v0.0.7 CHANGELOG entry as the body.
9. **Worktree cleanup** — `worktree-agent-ac5705e8e77381971` is still
   on disk. Run `git worktree remove --force
   D:\Dev_Env\Ascendo\.claude\worktrees\agent-ac5705e8e77381971`
   from outside Claude Code.

---

## What did NOT change this session

- Tauri shell (`ui/desktop-tauri/`).
- Installer pipeline (`bin/build-installer.ps1`, `packaging/`).
- Service install (`bin/install-service.ps1`,
  `core/ascendo/dashboard/routes/service.py`).
- First-run wizard (`tests/contract/test_onboarding.py`,
  `core/ascendo/dashboard/routes/onboarding.py`).
- All session-15 work — this session is purely the bugfix wave the
  user reported after testing rc1.

---

End of handoff. Tomorrow you pick up by smoke-testing the new
detail panel + new Apps UI on the resident user's Win11 dev box.
Tag is `v0.0.7-rc2`; HEAD is `9bb8751`; everything pushed.
