# macOS Web App Updater — Design

> Date: 2026-05-06
> Milestone: M5.6 (post-`v0.2.0`)
> Target tag: `v0.3.0` after Stage 13 e2e green on Mac.r12.home
> Spec lives forward; HANDOFF.md will close out the milestone after implementation.

## 1. Goals and non-goals

### Goals

- Add a sixth `IPackageManager` to the macOS adapter — `WebManager` — that
  unifies update tracking + automation for ~24 apps installed outside
  brew / mas / softwareupdate (the 309 apps inventory currently labels
  `SourceType.WEB`).
- Maximise silent, automatic updates: any app whose vendor exposes a
  programmable update path is updated without user input. Apps with no
  programmable path are surfaced in the unified Apps view with an honest
  "manual" badge and an `open -a` shortcut to the user's update flow.
- Ship a curated, version-controlled `_apps.toml` registry that defines
  the MVP coverage, with a user override file at
  `~/.config/ascendo/web_apps.toml` for extension and per-host
  customisation.
- Cover six update mechanisms: Sparkle (appcast XML), GitHub Releases
  (DMG asset), Keystone (Google's update agent), Squirrel.Mac
  (auto-on-relaunch), Microsoft AutoUpdate (`msupdate`), and Docker
  Desktop's `docker desktop update` CLI. Plus a fallback "builtin"
  mode for apps with no automation path.
- Match the existing 5-phase contract (`check → plan → apply → verify
  → cleanup`) and the JSON-v1 sidecar schema unchanged. No core
  protocol changes.

### Non-goals

- "Manual-only" apps (DaVinci Resolve, Blackmagic, IPMIView, AppCleaner,
  VirtualBox) — explicitly skipped from the MVP. The legacy
  `update_internet_apps.sh` printed warnings for them; we don't need
  that surface.
- Auto-detection of handler type from disk fingerprints (probing
  `Info.plist` `SUFeedURL`, scanning for Keystone agent, etc.). The
  legacy script tried this and ended up hardcoding apps anyway. Pure
  registry-driven for v1; auto-probe is a v0.4 follow-up.
- AppleScript-driven menu navigation for built-in updaters (e.g.
  `tell System Events to click menu item "Check for Updates"`). The
  legacy script had this; it bit-rots every time a vendor renames their
  Help menu. Built-in updater apps are routed via "open + emit
  instruction" or, when a Squirrel-style auto-relaunch path exists, via
  the squirrel handler.
- Pre-apply Time Machine snapshot integration — APFS auto-management
  blocks programmatic snapshots; status quo from Sesja 28 unchanged.
- Parallel apply within the web category. Apps run sequentially,
  matching the existing per-category pattern. Parallel apply is a
  separate cross-cutting milestone per HANDOFF.md.

## 2. Architecture

```
adapters/macos/
├── ascendo_macos/
│   ├── managers/web.py             # WebManager(IPackageManager)         ~140 LOC
│   ├── web_registry.py             # Pydantic schema + load+merge        ~120 LOC
│   └── adapter.py                  # +1 manager, +1 health component     mod
├── config/
│   └── web_apps.toml               # MVP registry, ~24 entries           ~250 LOC
├── lib/
│   ├── ascendo_web.sh              # shared helpers                       ~250 LOC
│   ├── web_registry.py             # CLI shim over web_registry.py model ~50 LOC
│   └── handlers/
│       ├── sparkle.sh              # appcast XML + DMG install            ~120 LOC
│       ├── github_dmg.sh           # GH API + DMG install                ~140 LOC
│       ├── keystone.sh             # ksadmin probe + trigger              ~70 LOC
│       ├── squirrel.sh             # pkill + open -a                      ~50 LOC
│       ├── builtin.sh              # open -a + info message               ~40 LOC
│       ├── msupdate.sh             # msupdate --list/--install            ~80 LOC
│       └── docker.sh               # docker desktop update                ~60 LOC
└── scripts/web/
    ├── check.sh                    # iterate registry, dispatch probe     ~150 LOC
    ├── plan.sh                     # check minus up_to_date, +deferred    ~100 LOC
    ├── apply.sh                    # defer-if-running, dispatch handler   ~180 LOC
    ├── verify.sh                   # re-read CFBundleShortVersionString   ~120 LOC
    └── cleanup.sh                  # prune ~/Library/Caches/Ascendo/web   ~50 LOC
```

User override registry at `~/.config/ascendo/web_apps.toml`. Both files
load through `WebRegistry.load(shipped, user)`; user entries with
matching `slug` replace shipped entries completely; new slugs append.

## 3. Data model — `_apps.toml` schema

Schema name: `ascendo-web-apps/v1`. Validated by `web_registry.py`
Pydantic model on every load (adapter init + `health_check` + every
phase script invocation). Failure to validate → `health_check` reports
`web: error: ...`, phases exit 2 with a sidecar message naming the
offending entry.

### Common fields (every entry)

| Field          | Required | Type                          | Notes                                                                |
|----------------|----------|-------------------------------|----------------------------------------------------------------------|
| `slug`         | yes      | regex `^[a-z0-9-]+$`          | filter token (`--filter chrome`); primary key for override merge      |
| `bundle_id`    | yes      | dot-segmented string          | for `defaults read /Applications/<X>.app/Contents/Info CFBundleShortVersionString` |
| `display_name` | yes      | string                        | sidecar item name + UI display                                       |
| `handler`      | yes      | enum (7 values)                | `sparkle \| github_dmg \| keystone \| squirrel \| builtin \| msupdate \| docker` |
| `app_path`     | no       | path                          | defaults to `/Applications/<display_name>.app`; override for non-standard locations |
| `enabled`      | no       | bool, default `true`          | user can disable shipped entries via override                        |
| `notes`        | no       | string                        | free-form; surfaces in sidecar `info` messages for transparency      |

### Handler-specific fields

| Handler      | Required                                      | Optional                                                                 |
|--------------|-----------------------------------------------|--------------------------------------------------------------------------|
| `sparkle`    | `appcast_url` (https)                          | `apply_cli_argv` (array — when set, apply spawns the CLI instead of downloading the appcast enclosure) |
| `github_dmg` | `github_repo` (`owner/name`), `asset_pattern` (regex matching arm64 asset filename) | `arch` (default `arm64`), `prerelease` (default `false`)                 |
| `keystone`   | `ksadmin_product_id`                           | —                                                                        |
| `squirrel`   | —                                              | —                                                                        |
| `builtin`    | —                                              | `update_url` (string; surfaced verbatim in sidecar info message)         |
| `msupdate`   | —                                              | —                                                                        |
| `docker`     | —                                              | —                                                                        |

Pydantic `model_validator` rejects mismatches (e.g. `handler = "sparkle"`
without `appcast_url`, or `handler = "squirrel"` with `appcast_url`).

### Worked example

```toml
schema = "ascendo-web-apps/v1"

[[app]]
slug = "chrome"
bundle_id = "com.google.Chrome"
display_name = "Google Chrome"
handler = "keystone"
ksadmin_product_id = "com.google.Chrome"

[[app]]
slug = "brave"
bundle_id = "com.brave.Browser"
display_name = "Brave Browser"
handler = "sparkle"
appcast_url = "https://updates.bravesoftware.com/sparkle/Brave-Browser/stable/appcast.xml"
apply_cli_argv = ["/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
                  "--check-for-update"]

[[app]]
slug = "firefox-dev"
bundle_id = "org.mozilla.firefoxdeveloperedition"
display_name = "Firefox Developer Edition"
handler = "github_dmg"
github_repo = "mozilla-firefox/firefox"
asset_pattern = "^Firefox \\d[\\d.]+\\.dmg$"

[[app]]
slug = "slack"
bundle_id = "com.tinyspeck.slackmacgap"
display_name = "Slack"
handler = "squirrel"

[[app]]
slug = "ms365"
bundle_id = "com.microsoft.autoupdate2"
display_name = "Microsoft 365 Suite"
handler = "msupdate"

[[app]]
slug = "docker"
bundle_id = "com.docker.docker"
display_name = "Docker Desktop"
handler = "docker"
```

### Bash access

Bash 3.2 has no native TOML parser. The Pydantic model lives in Python;
phase scripts call a CLI shim:

- `web_registry.py --list-slugs` → newline-delimited active slugs
- `web_registry.py --get-app <slug>` → single-line JSON with all fields
- `web_registry.py --validate` → exit 0 on success; exit 2 with line/col + message on failure (used by `health_check` and the doctor command)

Phase scripts read individual app config via process substitution + `jq`
(jq is already a documented dependency in the macOS adapter).

## 4. Phase contract

### `check` (read-only, ~5–10s for 24 apps)

For each enabled registry entry:

```
installed = defaults read "$app_path/Contents/Info" CFBundleShortVersionString
if installed empty: continue                # not installed; web manager only updates installed apps

dispatch to <handler>_check from lib/handlers/<handler>.sh:
  sparkle      → curl appcast.xml → extract sparkle:shortVersionString of newest item
  github_dmg   → curl api.github.com/repos/<repo>/releases/latest → parse tag_name; verify asset_pattern matches an arm64 asset
  keystone     → ksadmin --print | parse for product_id; return latest known version (may be empty)
  msupdate     → msupdate --list 2>&1 | parse pending updates table
  docker       → docker desktop version --updates (fallback to release feed if --updates unavailable)
  squirrel     → ""    (latest unknown by design)
  builtin      → ""    (no automation path)

classify:
  latest empty AND handler in {squirrel, builtin}              → status=skipped, reason=auto_on_relaunch | manual_required
  latest empty AND handler in {sparkle, gh, keystone, msupdate, docker} → status=failed (probe broke; emit error message)
  installed == latest                                          → status=up_to_date
  installed < latest                                           → status=planned, target_version=latest
  installed > latest (user is on a beta/pre-release)           → status=up_to_date, info message
```

Always emit one item per active registry entry that's installed.
Visibility is the unified-Apps-view value proposition.

### `plan` (read-only)

Identical probe logic to `check`. Output reduces to items the apply
phase would touch.

**Defer-if-running policy (per-handler).** Q3 picked "defer if running"
for safety, but the rule only applies to handlers that destructively
replace the running app bundle. Handlers that update via a separate
agent or queue for next launch don't need it:

| Handler      | Defer if running? | Why                                                        |
|--------------|-------------------|------------------------------------------------------------|
| `sparkle`    | yes               | Replaces app bundle in-place; running process holds open file handles |
| `github_dmg` | yes               | Same as sparkle                                            |
| `squirrel`   | yes               | Apply IS the relaunch; deferred means "user must close app first" |
| `keystone`   | no                | Daemon updates separately; Chrome restarts on its own when ready |
| `msupdate`   | no                | Queues update for next Office app launch                   |
| `docker`     | no                | Docker Desktop's updater handles running containers gracefully |
| `builtin`    | n/a               | No state mutation; always emits info regardless of running |

Plan output rules:

- `up_to_date` items dropped
- `failed` (probe broken) items kept (apply will retry)
- defer-eligible (sparkle/github_dmg/squirrel) + running → `skipped`,
  reason `deferred_app_in_use`
- squirrel + not running → `planned` (apply will relaunch)
- builtin (always) → `skipped`, reason `manual_required` (apply does
  `open -a` but emits info, not a state mutation)
- non-defer-eligible (keystone/msupdate/docker) → `planned` regardless
  of running state

### `apply` (mutating)

For every `planned` item from the latest plan sidecar (or, when invoked
without a prior plan, re-derived in-process):

```
pre-flight: for defer-eligible handlers (sparkle/github_dmg/squirrel),
re-check is_running (state may have shifted since plan).
  if running and defer-eligible → skipped, deferred_app_in_use, continue
  otherwise → proceed (keystone/msupdate/docker apply regardless)

dispatch to <handler>_apply:
  sparkle:
    if apply_cli_argv set:
      spawn argv with 60s timeout; exit code → status; stderr (last 12 lines) → message
    else:
      curl enclosure_url > "$cache/$slug.dmg"
      hdiutil attach -nobrowse "$cache/$slug.dmg" → mount point
      spctl --assess --type execute --verbose "$mount/<DisplayName>.app" || abort
      cp -R "$mount/<DisplayName>.app" "/Applications/" (no sudo first; sudo -A on EACCES)
      xattr -dr com.apple.quarantine "/Applications/<DisplayName>.app"
      hdiutil detach "$mount"
  github_dmg:
    GH API → arm64 asset URL via asset_pattern
    same dl + spctl + install pipeline as sparkle (no apply_cli path)
  keystone:
    ksadmin --update -productid "$ksadmin_product_id"
    daemon does the install async; we don't block
  squirrel:
    open -a "$app_path"  (app self-updates on launch in background)
  builtin:
    open -a "$app_path"
    emit info: "Open the app's Help menu and run Check for Updates" + update_url if set
    item status=skipped (no mutation occurred from Ascendo's POV)
  msupdate:
    sudo -A msupdate --install   (handles every enrolled MS app in one call)
  docker:
    docker desktop update --quiet
```

Per-app stderr capture (last 12 lines via `tail -n 12 | head -c 1500`)
into sidecar messages — same pattern as the npm/pip handlers from
Sesja 34. `_stream_log` integration so the dashboard's terminal box
shows live progress per app.

`/Applications` writes try without sudo first; on `EACCES` retry with
`sudo -A cp -R` via the existing askpass cache. The dashboard's modal
prompt + Touch ID warming (Sesja 34) covers both flows.

### `verify` (read-only, varies by handler)

```
re-read installed CFBundleShortVersionString; compare to apply sidecar's target_version (or just installed_pre).

  sparkle, github_dmg, msupdate, docker:
    installed == target → success
    installed < target  → failed (apply did not take)
  keystone:
    sleep 10s
    if installed bumped at all → success
    else → success, info message "Keystone update pending; will land on next launch"
  squirrel:
    sleep 30s; re-read version
    if installed > pre-apply → success
    else → success, info message "relaunch completed; version unchanged (likely already current)"
  builtin:
    no-op; status=skipped, reason=manual_verify_required
```

The 10s/30s sleeps are deliberate — Keystone and Squirrel both apply
async after their trigger. Verify is the reconciliation point.

### `cleanup`

- Prune `~/Library/Caches/Ascendo/web/*.dmg` and `*.zip` older than 7 days
- No-op for keystone/squirrel/builtin/msupdate/docker (no debris)
- Idempotent

## 5. Per-handler bash scripts

Each `lib/handlers/<name>.sh` exports two functions and is sourced by
phase scripts. Functions follow the naming convention `<handler>_check`
and `<handler>_apply`, plus optional `<handler>_verify` for handlers
needing custom verification logic (only squirrel + keystone use this;
others fall through to the default version-compare verify in
`verify.sh`).

```bash
# lib/handlers/sparkle.sh
sparkle_check() {           # args: slug, app_config_json → echoes latest version or empty
  local slug="$1" cfg="$2"
  local appcast_url
  appcast_url=$(printf '%s' "$cfg" | jq -r '.appcast_url')
  curl -fsSL --max-time 10 "$appcast_url" \
    | _web_extract_sparkle_latest_version       # in ascendo_web.sh
}

sparkle_apply() {           # args: slug, app_config_json → exit 0/non-0
  local slug="$1" cfg="$2"
  local cli_argv
  cli_argv=$(printf '%s' "$cfg" | jq -c '.apply_cli_argv // empty')
  if [ -n "$cli_argv" ]; then
    _web_run_apply_cli "$slug" "$cli_argv"
  else
    local enclosure_url
    enclosure_url=$(_web_sparkle_enclosure_url "$cfg")
    _web_install_dmg "$slug" "$enclosure_url" "$(printf '%s' "$cfg" | jq -r '.app_path // ""')"
  fi
}
```

Shared helpers in `ascendo_web.sh`:

- `_web_load_registry` — sources slugs into a bash array
- `_web_installed_version "$app_path"` — wrap `defaults read` with empty-string-on-missing
- `_version_gt "$a" "$b"` — already-existing helper from `ascendo_pip.sh`; relocate to `ascendo_web.sh` and re-source from pip's helper to avoid duplication
- `_web_is_running "$bundle_id"` — `pgrep -fx <pattern>` → exit 0/1
- `_web_install_dmg "$slug" "$url" "$app_path"` — full mount/spctl/cp/xattr/detach pipeline
- `_web_verify_signature "$path"` — `spctl --assess --type execute --verbose`; reject on non-zero
- `_web_download "$url" "$dest"` — curl with progress streamed via `_stream_progress` (Sesja 30 pattern)
- `_web_run_apply_cli "$slug" "$argv_json"` — eval JSON argv with timeout

## 6. Python class — `WebManager`

Mirrors `NpmManager` (~140 LOC). Slots into `MacOSAdapter.package_managers()`
between `pip` and `softwareupdate`. Reads sidecar via the existing M2.4
`sidecar_io` module. `is_available(host)` returns `False` off macOS,
`False` if registry validation fails, `True` otherwise.

```python
class WebManager(IPackageManager):
    category = SourceType.WEB
    display_name = "Web apps (Sparkle / GitHub / Keystone / Squirrel / msupdate / Docker)"
    SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts" / "web"
    LIB_DIR = Path(__file__).resolve().parent.parent.parent / "lib"
    SHIPPED_REGISTRY = Path(__file__).resolve().parent.parent.parent / "config" / "web_apps.toml"
    USER_REGISTRY = Path("~/.config/ascendo/web_apps.toml").expanduser()
    SCRIPT_BY_PHASE = {
        Phase.CHECK: "check.sh", Phase.PLAN: "plan.sh",
        Phase.APPLY: "apply.sh", Phase.VERIFY: "verify.sh",
        Phase.CLEANUP: "cleanup.sh",
    }
```

`_build_argv` matches `NpmManager` exactly:

```python
def _build_argv(self, phase, run, item_filter, output_dir):
    argv = ["bash", str(self.SCRIPTS_DIR / self.SCRIPT_BY_PHASE[phase]),
            "--run-id", run.run_id,
            "--trigger", run.trigger.value,
            "--profile", run.profile.value,
            "--output-dir", str(output_dir)]
    if run.dry_run:
        argv.append("--dry-run")
    if item_filter:
        argv.extend(["--filter", ",".join(item_filter)])
    return argv
```

## 7. Adapter wiring + health check

`MacOSAdapter.package_managers()`:

```python
return [
    BrewManager(...),
    MasManager(...),
    NpmManager(...),
    PipManager(...),
    WebManager(...),                # ← new, between pip and softwareupdate
    SoftwareUpdateManager(...),     # stays last (reboot semantics)
]
```

`MacOSAdapter.health_check()` adds a `web` component (count goes
**11 → 12**):

```python
def _web_status(self) -> str:
    try:
        reg = WebRegistry.load(self.SHIPPED_WEB_REGISTRY, self.USER_WEB_REGISTRY)
        return f"ok: {len(reg.active_apps())} apps registered"
    except FileNotFoundError:
        return "error: web_apps.toml not found"
    except ValidationError as e:
        first = e.errors()[0]
        return f"error: registry validation failed at {'.'.join(map(str, first['loc']))}: {first['msg']}"
```

## 8. Operational details

### Sudo policy

`/Applications` writes attempt without sudo first. On `EACCES`
(replacing root-owned bundles) retry via `sudo -A cp -R`. Existing
`MacElevation` askpass cache (Sesja 21) plus Touch-ID-first warming
(Sesja 34) handle the prompt. `msupdate` always sudo. Other handlers
never sudo.

### Cache directory

`~/Library/Caches/Ascendo/web/` for downloaded DMGs/zips. Created on
first download with `mkdir -p`. Cleanup phase prunes files older than
7 days. Override via `ASCENDO_WEB_CACHE_DIR` env var (used by tests).

### Quarantine + Gatekeeper

Every downloaded app goes through `spctl --assess --type execute
--verbose <app_path>` before install. Reject installs that fail
notarization (Gatekeeper check). After install, strip
`com.apple.quarantine` xattr recursively from the installed bundle to
prevent the "Are you sure you want to open this app?" first-launch
prompt for Ascendo-installed apps.

### Concurrency

Apps run sequentially within the web category. The orchestrator
already enforces sequential per-category apply across the macOS
adapter; "parallel apply" remains a separate cross-cutting milestone
(M5.x deferred follow-up).

### GitHub API rate limit

Anonymous GH API: 60 requests/hour per IP. With ~9 GH-handled apps,
one full check uses ~9 calls. Acceptable. Handler reads optional
`GITHUB_TOKEN` env var; when set, included as `Authorization: token`
header (lifts limit to 5000/hr). Documented in MACOS_QUICKSTART.

### Sidecar item shape

Standard `ascendo/v1` schema. New `category` value: `web` (already in
`SourceType.WEB` enum). Item fields populated:

- `id` → `web:<slug>`
- `name` → `display_name` from registry
- `current_version` → installed CFBundleShortVersionString
- `target_version` → handler-probed latest (where known)
- `status` → see classification rules above
- `evidence.bundle_id` → bundle_id
- `evidence.handler` → handler name
- `evidence.app_path` → resolved app_path
- `messages[]` → handler-specific info/error notes (last 12 lines of
  stderr on apply failure; quarantine-stripped notice; spctl rejection
  reason; etc.)

## 9. MVP curated registry (~24 apps)

| # | Slug              | Display name              | Handler      | Notes                                            |
|---|-------------------|---------------------------|--------------|--------------------------------------------------|
| 1 | `chrome`          | Google Chrome             | keystone     | `ksadmin_product_id = com.google.Chrome`         |
| 2 | `gdrive`          | Google Drive              | keystone     | `ksadmin_product_id = com.google.drivefs`        |
| 3 | `brave`           | Brave Browser             | sparkle      | `apply_cli_argv` available                       |
| 4 | `opera`           | Opera                     | sparkle      |                                                  |
| 5 | `chatgpt-atlas`   | ChatGPT Atlas             | sparkle      | Sparkle appcast at persistent.oaistatic.com      |
| 6 | `firefox-dev`     | Firefox Developer Edition | github_dmg   | mozilla-firefox/firefox repo                     |
| 7 | `keepassxc`       | KeePassXC                 | github_dmg   | keepassxreboot/keepassxc                         |
| 8 | `trezor-suite`    | Trezor Suite              | github_dmg   | trezor/trezor-suite                              |
| 9 | `ledger-live`     | Ledger Live               | github_dmg   | LedgerHQ/ledger-live-desktop                     |
| 10| `vscode`          | Visual Studio Code        | github_dmg   | microsoft/vscode (note: many users have brew cask) |
| 11| `codeedit`        | CodeEdit                  | github_dmg   | CodeEditApp/CodeEdit                             |
| 12| `macwhisper`      | MacWhisper                | github_dmg   | (TBC during implementation; vendor's GH repo)    |
| 13| `rdm`             | Remote Desktop Manager    | github_dmg   | Devolutions/RemoteDesktopManagerMac              |
| 14| `slack`           | Slack                     | squirrel     |                                                  |
| 15| `claude`          | Claude                    | squirrel     |                                                  |
| 16| `chatgpt`         | ChatGPT                   | squirrel     |                                                  |
| 17| `warp`            | Warp                      | squirrel     | (verify Sparkle vs Squirrel during implementation) |
| 18| `gemini`          | Gemini                    | squirrel     |                                                  |
| 19| `lm-studio`       | LM Studio                 | squirrel     |                                                  |
| 20| `perplexity`      | Perplexity                | squirrel     |                                                  |
| 21| `codex`           | Codex Desktop             | squirrel     |                                                  |
| 22| `opencode`        | OpenCode Desktop          | squirrel     |                                                  |
| 23| `ms365`           | Microsoft 365 Suite       | msupdate     | one entry covers Word/Excel/PPT/Outlook/OneNote/Teams |
| 24| `docker`          | Docker Desktop            | docker       | `docker desktop update --quiet`                  |

Bundle IDs to be confirmed during implementation by running `defaults
read /Applications/<App>.app/Contents/Info CFBundleIdentifier` on
Mac.r12.home — operator presence required.

## 10. Tests (~53 new)

| File                                    | Count | Coverage                                                           |
|-----------------------------------------|-------|--------------------------------------------------------------------|
| `tests/test_web_registry.py`            | 15    | Pydantic schema per handler; required-field enforcement; slug regex; override merge by slug; disabled entries skipped; malformed TOML raises with line/col; schema-version mismatch rejected |
| `tests/test_web_manager_smoke.py`       | 12    | Mocked subprocess; identity; `is_available` matrix (linux/windows/macos × valid/invalid registry); 5 phases dispatched correctly; filter propagation; `--dry-run` argv shape |
| `tests/test_web_check_script.py`        | 10    | Drive `check.sh` with fake `defaults`/`curl`/`ksadmin`/`msupdate` on PATH; per-handler classification (Sparkle outdated → planned; GH up-to-date → up_to_date; squirrel app installed → skipped/auto_on_relaunch; etc.) |
| `tests/test_web_handler_sparkle.py`     | 4     | `sparkle_check` extracts version from sample appcast XML; `apply_cli_argv` path vs DMG path; timeout handling |
| `tests/test_web_handler_github_dmg.py`  | 4     | `github_dmg_check` parses GH API JSON; `asset_pattern` selects right asset; arm64 vs x86_64 filter; rate-limit error |
| `tests/test_web_handler_squirrel.py`    | 3     | `squirrel_apply` dispatches `pkill` then `open -a`; defer-if-running short-circuits; verify sleeps then re-reads |
| `tests/test_adapter_smoke.py` (extend)  | 5     | 6 managers in `package_managers()`; web slot between pip and softwareupdate; 12 health components; `_web_status` ok+error paths |

Aggregate test count (53 new): 495 → 548.

## 11. Validation script + release flow

### `bin/validate-macos.sh` Stage 13 (~7 sub-steps)

1. Doctor: `web` component shows ok with N apps registered
2. `lib/web_registry.py --validate` exits 0 against shipped TOML
3. `web --phase check` runs, sidecar valid, ≥1 item present
4. `web --phase plan` runs, sidecar shape correct
5. `web --phase apply --dry-run` runs without mutation
6. `web --phase verify` runs (post-apply or empty-no-op)
7. `web --phase cleanup` runs, no errors

Updated total expected: 34/34 (Sesja 28 baseline) + 7 = **41/41 PASS**.

### `bin/run-tag-release-macos.sh`

- Tag bump: `v0.2.0` → `v0.3.0` with M5.6 release message
- New `--web` flag analogous to `--mas`: enables Stage 5c (web apply
  smoke against one chosen `--filter <slug>` to keep it fast)

## 12. Open questions / deferred follow-ups

- **Auto-detection of Sparkle apps** (option C from the brainstorm).
  Cheap to probe (`defaults read .../Info SUFeedURL`); could surface
  unregistered apps. Defer to v0.4 once registry semantics are stable.
- **AppleScript menu navigation** for selected `builtin` apps. If a
  small number of high-value apps prove this is durable, we add a
  `applescript_path` field to the registry. Out of scope for v0.3.0.
- **Per-app `kill_safe` flag** (option C from Q3). Defer until we have
  evidence that defer-if-running causes user friction in practice.
- **Bulk plan-preview UI** aggregating per-category plan sidecars into
  one diff view. Existing M5.x deferred item; web reuses it when it
  lands.
- **Pre-apply Time Machine snapshot integration**. Still APFS-blocked.
- **Parallel apply within web category**. Lock coordination is
  cross-cutting; defer to its own milestone.

## 13. Tag

After Stage 13 e2e green on Mac.r12.home + final review across all
M5.6.* commits, tag `v0.3.0`. HANDOFF.md gets a Sesja 35 closeout entry
referencing this spec, the implementation plan, and the test inventory.

---

End of design.
