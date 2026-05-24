# Ascendo — User Guide (Basic Edition)

End-user walkthrough for the Basic edition. If you installed with the
default `ASCENDO_EDITION=basic` (or no override at all), this is the
guide for you. Contributors and maintainers running the **dev edition**
should read [DEV_GUIDE.md](DEV_GUIDE.md) instead.

> **Operating systems supported**
> macOS (Apple Silicon + Intel) — feature-complete
> Windows 11 / 10 — feature-complete
> Ubuntu 22.04+ / Debian 12+ — stable, parity in progress
>
> See [docs/PLATFORM_STATUS.md](docs/PLATFORM_STATUS.md) for the
> per-feature matrix.

---

## What Ascendo does for you

Ascendo is a **unified-update orchestrator**. You install it once, and
it talks to every package source on your machine through one set of
commands:

- **macOS:** Homebrew · Mac App Store · macOS softwareupdate · npm · pip · web apps (DMG / Sparkle / Keystone / Squirrel / Microsoft AutoUpdate / Docker Desktop)
- **Windows:** winget · Microsoft Store · Add/Remove Programs · Windows Update · npm · pip
- **Linux:** apt · snap · brew · flatpak · npm · pip

Every operation goes through the same five phases:

| Phase | What happens | Mutating? |
|-------|--------------|-----------|
| `check`   | Read-only inventory + "what's outdated?" | no |
| `plan`    | Dry-run "what would change?" | no |
| `apply`   | The only mutating step | **yes** |
| `verify`  | Post-apply re-check | no |
| `cleanup` | Caches / autoremove | sometimes |

Every phase writes a JSON receipt ("sidecar") to
`~/.ascendo/runs/<run-id>/<phase>__<source>.json` so you can audit,
replay, or diagnose any change after the fact.

---

## 1. Install

The recommended path is the one-liner from the README — it auto-detects
your OS, installs missing dependencies (Python 3.11+, git), clones the
repo to a per-user dir, sets up a venv, and finishes with a
`ascendo doctor` self-test.

| OS | Install one-liner |
|----|-------------------|
| macOS / Linux | `curl -fsSL https://raw.githubusercontent.com/KasprowiczM/ascendo/main/install.sh \| bash` |
| Windows (PowerShell) | `iwr -useb https://raw.githubusercontent.com/KasprowiczM/ascendo/main/install.ps1 \| iex` |

The Basic edition ships four install profiles. Pick one with
`ASCENDO_PROFILE=…`:

| Profile | What you get | Disk |
|---------|--------------|------|
| `cli` | Just the `ascendo` CLI | ~30 MB |
| `web` *(default)* | CLI + FastAPI dashboard at `http://127.0.0.1:8765/` | ~50 MB |
| `desktop` | CLI + native Tauri 2.x desktop app | ~80 MB |
| `full` | Everything (CLI + Web + Desktop) | ~100 MB |

Example — the most common pick:

```bash
curl -fsSL https://raw.githubusercontent.com/KasprowiczM/ascendo/main/install.sh \
  | ASCENDO_PROFILE=full bash
```

After install completes you'll have:

- `ascendo` on PATH (the underlying CLI)
- A handful of friendly `ascendo_*` helper shims also on PATH
- A per-user data dir at `~/.ascendo/` (runs, sidecars, logs)
- A repo checkout at `~/.local/share/ascendo/` (POSIX) or
  `%LOCALAPPDATA%\Ascendo\src` (Windows)

For platform-specific notes (sudo/UAC handling, browser launch, Tauri
build prerequisites) see the platform quickstarts:

- [MACOS_QUICKSTART.md](MACOS_QUICKSTART.md)
- [WINDOWS_QUICKSTART.md](WINDOWS_QUICKSTART.md)
- [LINUX_QUICKSTART.md](LINUX_QUICKSTART.md)

---

## 2. Open the dashboard

Three equivalent ways to launch the dashboard. Pick whichever you
prefer:

```bash
# A. Web profile — open in your browser
ascendo_start_web                   # starts FastAPI on 127.0.0.1:8765
# then open http://127.0.0.1:8765/

# B. Desktop profile — native window (macOS / Windows)
ascendo_start_desktop

# C. CLI fallback if you skipped Web/Desktop
ascendo doctor                      # one-shot health snapshot
```

To stop the background dashboard later:

```bash
ascendo_stop_web                    # or ascendo_restart_web to bounce it
ascendo_stop_desktop                # if you started the Tauri shell
```

---

## 3. Tour of the dashboard

The dashboard is organised as **five workflow destinations** in the
left sidebar — each answers one question. Sub-tabs handle facets
*inside* a destination, so you're never hunting across the nav.

| Destination | The question it answers | Sub-tabs |
|-------------|-------------------------|----------|
| **Dashboard** | What's happening and what should I do next? | — (single page) |
| **Library** | What sources, apps, and tools does Ascendo manage? | Sources · Apps · Tools |
| **Runs** | What can I run now, what's scheduled, what ran before? | Start · Scheduled · History |
| **Insights** | What happened, what failed, what trends matter? | Trends · *(Logs — dev edition)* |
| **Settings** | How does Ascendo behave and how do I configure it? | General · *(Hosts/Sync — dev)* · Help · About |

Every page opens with a **header strip**: the page title, a one-line
description, and a single primary action (e.g. **Start Run**).

- **Display & preferences** (theme, language, font size) live in the
  ⚙ Preferences popover at the top-right of the header — one compact
  cluster instead of three buttons competing with content.
- The sidebar footer shows an **OS status pill** (e.g. `macOS · admin
  permission`) so you always know which platform and elevation model
  is in effect.

### Action Required

If Ascendo detects an app that needs manual intervention (e.g., an unverified URL or an app that requires a manual download), it will appear in an **Action Required** panel on the Dashboard.
- Click **Open** to launch the download URL.
- Click **Fix with AI** to let the LLM automatically research and propose an automated update path for that app.
- Click **Open all** to open all pending download URLs at once.

**Old → new map** (bookmarks still work — old hashes auto-resolve):

| Old tab | Now lives at |
|---------|--------------|
| Overview | **Dashboard** |
| Categories | **Library → Sources** |
| Apps | **Library → Apps** |
| AI Tools / Suggestions | **Library → Tools** |
| Run Center | **Runs → Start** |
| Schedule | **Runs → Scheduled** |
| History | **Runs → History** |
| *(new)* trends / failures / duration | **Insights → Trends** |
| Settings | **Settings → General** |
| Help / About | **Settings → Help / About** |
| Hosts / Sync / Logs | dev-edition only (Settings / Insights) |

### Touch, mobile & themes

The dashboard is touch-first and fully responsive (375px phone →
desktop):

- **No dropdown menus anywhere.** Profile / theme / language / scope
  pickers are visible **segmented buttons or choice cards**; long
  lists (e.g. Logs) become a searchable list. Pick one option and the
  next relevant choices reveal in place.
- **Phones** get a bottom tab bar for the 5 destinations; the Run
  Center offers a unified Start view (Safe / Quick / Advanced options);
  the History table becomes tappable cards (tap a card for the
  run drawer). All controls are ≥44px tap targets.
- **Light & dark themes** (⚙ Preferences → Theme; `dark` is the
  default). Light mode has strong contrast and clear card/section
  separation for daylight use; dark is the primary surface.
- Keyboard: arrow keys move within segmented controls, `Tab`/`Enter`
  everywhere, visible focus rings, reduced-motion respected.

---

## 4. Scan for what's outdated

The everyday flow:

1. Open **Library → Sources**.
2. Click any source row to expand it.
3. Click **check** on that row. A read-only scan runs (~5–30 s); live
   progress streams in **Runs → Start**.
4. When the row's "outdated" count updates, you know what's pending.

You can repeat this per source, or use the **Dashboard → Start Run**
header action / the Run Center for a multi-source sweep:

```
[1] Safe update   [2] Quick update   [3] Advanced ▸
```

Equivalent CLI one-liners (handy for terminals or scripts):

```bash
ascendo run --profile=quick                       # check on every source (~15 s)
ascendo run --category brew --phase check         # one source
ascendo_maintenance quick                         # same, via helper shim
```

---

## 5. Apply updates

Every apply gates on a **confirmation modal** — you must type the
literal word `apply` (case-sensitive) before anything mutates. This
prevents accidental click-throughs from changing system state.

### Per source

1. Library → Sources → row → **plan** (preview the changes)
2. Library → Sources → row → **apply** (modal opens; type `apply` or use Touch ID on macOS)
3. Watch **Runs → Start** stream the run live
4. When done, Library counts + the Apps sub-tab auto-refresh

### "Update everything"

Use **Dashboard → Start Run** (or the Full-update quick chip in
**Runs → Start**). Same modal, all sources at once, sequential apply
(brew/winget first, OS updates last because of reboot semantics).

### Dry-run first if you're nervous

The **Full dry-run** quick chip runs every source through `plan` only,
shows you exactly what *would* change, never mutates.

### Reboot detection

If any source's apply sets the `needs_reboot` flag (Windows Update,
macOS softwareupdate, kernel updates on Linux), a banner appears at
the top of the dashboard. Reboot on your own schedule.

---

## 6. Schedule recurring runs

The schedule DSL is the same on every OS — Ascendo translates it to
launchd / Task Scheduler / systemd timers under the hood.

Open **Runs → Scheduled** to:

- See every active schedule in a table (Name / When / Profile / Enabled / Actions)
- Add or replace a schedule via the form below the table
- **Run now** any schedule once on demand (handy for verifying the
  expression works without waiting for the next slot)
- **Edit** by clicking Edit — the row's fields prepopulate the form;
  submit with the same name to replace in place
- **Delete** a schedule with a confirm prompt

Fill in the **Add or replace a schedule** form with:

- **Name** — short slug, e.g. `nightly` (letters / digits / `_` / `-` only)
- **Expression** — see table below
- **Profile** — `quick` / `safe` / `full`
- **Enabled** — toggle (uncheck to keep the entry but pause it)
- **Description** — optional free-text note

| Expression form        | Runs at                       |
|------------------------|-------------------------------|
| `DAILY 03:30`          | every day at 03:30            |
| `WEEKLY MONDAY 06:00`  | every Monday at 06:00         |
| `MONTHLY 15 03:00`     | the 15th of the month at 03:00|
| `HOURLY :15`           | every hour at :15 past        |
| `MINUTE 30`            | every 30 minutes              |

CLI equivalent:

```bash
ascendo schedule install --name nightly --calendar "DAILY 03:30" --profile safe
ascendo schedule list
ascendo schedule trigger --name nightly        # run now, synchronously
ascendo schedule remove --name nightly
```

Scheduled runs write the same sidecars to `~/.ascendo/runs/`. Check the
History tab afterwards to see what happened.

---

## 7. The Apps tab — per-app history + exclusions

The Apps tab is your "what's installed across every source" view:

- **Search box** with debounce — find an app by name fast
- **Status chips** — filter by `outdated` / `up_to_date` / `triggered` / etc.
- **Source chips** — filter by brew / winget / mas / web / etc.
- **Group by source** with collapsible sticky headers
- **Candidate column** — installed version vs available version
- **History link** per row — toggles an inline table of past upgrades for that app:

  ```
  When           From         To           Status   Run ID
  3 hours ago    1.14.40      1.14.41      ✓        abc-…
  2 days ago     1.14.39      1.14.40      ✓        def-…
  ```

- **Exclude** — toggle off any app you don't want Ascendo to upgrade
  automatically. Excluded apps stay visible but are skipped during
  apply runs. Stored in `~/.ascendo/excluded_apps.json`.

---

## 8. Reading the History tab

Every run gets one row in History. Click to expand — you'll see:

- Per-(phase × source) status pills
- The full **`REPORT.md`** if it's an apply run — a human-readable
  Markdown summary of what changed (e.g. "3 upgraded, 211 already
  up-to-date, 6 deferred")
- Inline `.log` files for each phase
- Links to the underlying JSON sidecars

The History tab also exposes a status filter (`success` / `partial` /
`failed`) and pagination. Use it to investigate "why did last night's
run fail?" without leaving the dashboard.

CLI equivalent:

```bash
ascendo runs list -n 10                         # last 10 runs
ascendo runs show <run-id>                      # human-readable summary
ascendo runs report <run-id>                    # the post-apply REPORT.md
ascendo runs json <run-id> --pretty | jq .      # machine-readable
```

---

## 9. Suggestions

The **Suggestions** tab gives you an opinionated recommendation engine.

**Sesja 67 made the AI integration first-class.** The `/suggestions/library`
endpoint now PREPENDS 1-3 AI-generated cards on top of the rule-based
library whenever an LLM provider is configured — no second click
required.

How it works:

- **Rule-based library** (always on) — deterministic "if X then Y"
  suggestions (e.g. "VSCode is outdated 1.119.1 → 1.120.0; click to
  schedule a web check"). Sorted by severity. Click any card's
  primary button to fire the linked action via `/runs/async`.
- **AI mode** (opt-in) — wire your own provider key in **Settings →
  AI providers**, pick a model, click **Save**. The next time you
  open Suggestions, Ascendo sends a compact inventory snapshot to
  the LLM with a strict JSON schema and merges 1-3 AI cards on top.
  Supported providers: **Anthropic** · **OpenAI** · **OpenRouter** ·
  **Ollama** (local) · **Google Gemini** · **LM Studio** (local) · **LiteLLM**.
- **Graceful fallback** — if the configured provider is offline,
  rate-limited, or returns malformed JSON, Ascendo silently falls
  back to rule-based cards. You see the rule-based library and a
  small `ai: error` hint in the panel header, never a 500.
- **Security** — action payloads from the LLM are sanitised: only
  `run_async` actions with known payload keys (`profile`, `phases`,
  `categories`) survive. The LLM cannot inject arbitrary shell
  commands or arbitrary endpoint calls. Credentials are stored
  locally in `~/.config/ascendo/ai.json` with the API key redacted
  in any UI echo.

---

## 10. Common day-to-day tasks

### Update Ascendo itself

```bash
ascendo_update                       # one-liner; idempotent
```

Equivalent direct one-liner:

```bash
curl -fsSL https://raw.githubusercontent.com/KasprowiczM/ascendo/main/update.sh | bash
```

### Health check

```bash
ascendo_doctor                       # full report (10 components on macOS, similar elsewhere)
ascendo doctor --verbose             # same, with capability flags
```

### Rebuild the inventory cache

If **Library → Sources** looks stale or wrong:

```bash
ascendo_maintenance rebuild-inventory
```

Equivalent: delete `~/.ascendo/inventory.db` and run any `check` —
the dashboard repopulates it on next request.

### Investigate a failed run

```bash
last_failed=$(ascendo runs list -n 1 --status failed | awk 'NR==2 {print $1}')
ascendo runs show "$last_failed"
ascendo runs json "$last_failed" --pretty | jq '.sidecars[] | select(.phase == "apply") | .messages'
cat ~/.ascendo/runs/"$last_failed"/apply__*.log
```

### Check for recent errors across all runs

```bash
ascendo_maintenance check-errors
```

### Wipe runtime state (start fresh)

```bash
rm -rf ~/.ascendo/                   # all runs, sidecars, logs, inventory cache
# next ascendo run recreates it
```

---

## 11. Where things live

```
~/.local/share/ascendo/   (POSIX)            # repo checkout
%LOCALAPPDATA%\Ascendo\src   (Windows)

~/.ascendo/                                  # runtime state
├── runs/<uuid>/                             # one folder per run
│   ├── check__brew.json
│   ├── plan__brew.json
│   ├── apply__brew.json                     # the sidecar = the receipt
│   ├── apply__brew.log                      # plain log
│   ├── verify__brew.json
│   ├── cleanup__brew.json
│   ├── REPORT.md                            # human-readable apply summary
│   └── run.json                             # consolidated summary
├── inventory.db                             # SQLite cache for the Apps tab
└── excluded_apps.json                       # per-app exclusions

~/.config/ascendo/                           # user-overridable config
├── ai.json                                  # AI provider creds (api_key redacted)
└── web_apps.toml                            # per-app override registry
```

---

## 12. Troubleshooting

The most common ten things people hit on a fresh install:

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `ascendo: command not found` | Helper shims dir not on PATH | Restart your shell, or run the install one-liner again — it re-adds PATH |
| `Library → Sources is empty` | First-run inventory not yet built | Click the **Build inventory** quick chip (Dashboard / Runs → Start) or `ascendo_maintenance rebuild-inventory` |
| `Dashboard 422 errors after a git pull` | Stale browser tab | Hard reload (`Ctrl-Shift-R` / `Cmd-Shift-R`) |
| `apply hangs at sudo prompt` (macOS) | No askpass / Touch ID configured | See [MACOS_QUICKSTART.md §6](MACOS_QUICKSTART.md) |
| `apply fails with "exited 1"` | Insufficient privilege | Re-run from an Administrator (Windows) / sudo-cached (macOS/Linux) shell |
| `Last run says "deferred"` | App was running during apply (defer-if-running) | Quit the app and re-run apply |
| `History entries missing inline logs` | Pre-Sesja-46 dashboard | Update Ascendo: `ascendo_update` |
| `"Reboot required" banner won't clear` | The flag stays until you actually reboot | Reboot, or click **Settings → Acknowledge reboot** if you've already restarted |
| `winget says X but Ascendo says Y` | Inventory cache 24h stale | Click the refresh button on Apps / Categories or `rebuild-inventory` |
| `Tauri desktop window won't open` | Missing build prerequisites (Rust / Node / WebView2) | See platform quickstart for your OS |

For platform-specific issues:

- macOS — [MACOS_QUICKSTART.md](MACOS_QUICKSTART.md), [MACOS_TESTING.md](MACOS_TESTING.md)
- Windows — [WINDOWS_QUICKSTART.md](WINDOWS_QUICKSTART.md), [WINDOWS_TESTING.md](WINDOWS_TESTING.md)
- Linux — [LINUX_QUICKSTART.md](LINUX_QUICKSTART.md)

If anything in this guide doesn't match what you see, file an issue
with the exact command, the output, and `ascendo doctor --verbose` —
that gives us enough to diagnose.

---

## 13. Uninstall

```bash
# macOS / Linux
rm -rf ~/.local/share/ascendo/                   # repo checkout
rm -rf ~/.ascendo/                               # runtime state (optional — keep if you might reinstall)
rm -rf ~/.config/ascendo/                        # config (optional)
rm -f  ~/.local/bin/ascendo*                     # helper shims

# Windows (PowerShell)
Remove-Item -Recurse -Force "$env:LOCALAPPDATA\Ascendo"
Remove-Item -Recurse -Force "$env:USERPROFILE\.ascendo"
Remove-Item -Force "$env:LOCALAPPDATA\Microsoft\WindowsApps\ascendo*.cmd"
```

If you used the Windows NSIS installer, **Add/Remove Programs →
Ascendo → Uninstall** does all of this in one click and removes the
AscendoDashboard service if installed.

Schedules survive uninstall by design (so a partial reinstall doesn't
break a running cron). To remove them explicitly:

```bash
ascendo schedule list                            # see what's there
ascendo schedule remove --name <each one>        # remove each
```

---

## 14. Where to next

- **Switch to dev edition** — [DEV_GUIDE.md](DEV_GUIDE.md) covers the
  Sync / Hosts / raw-events surfaces, dev-sync overlay, and how to
  hack on Ascendo itself.
- **Forward roadmap** — [PLAN.md](PLAN.md)
- **Architecture** — [docs/architecture/](docs/architecture/)
- **Cross-platform contract** — [docs/CROSS_PLATFORM.md](docs/CROSS_PLATFORM.md)
- **5-phase JSON contract** — [docs/agents/contract.md](docs/agents/contract.md)

License: [MIT](LICENSE) — do whatever you want, just keep the
copyright notice.
