# Portability — what happens on a different Mac?

> **Short answer:** Ascendo discovers what's installed on YOUR machine
> dynamically every time it runs. It never installs apps you don't have.
> Your installed-apps state isn't shipped in the repo, so a new user
> never sees your apps unless they install them themselves.

This page answers two operator questions verbatim:

1. *"Somebody else clones Ascendo to a different macOS, has totally
   different apps installed — after inventory, are all his apps going
   to be added to inventory, to config, and updated?"*
2. *"If somebody won't have apps that I have on my macbook, after
   installing and running Ascendo, is he going to get my app list
   installed automatically? (I don't want this to happen.)"*

## TL;DR

| Question | Answer |
|---|---|
| Does Ascendo auto-install apps? | **No.** Never. There's no `--install-from-manifest` workflow today. |
| Are my installed apps shared with other users? | **No.** Per-machine state lives in `~/.ascendo/` and `~/.config/ascendo/` — not in git. |
| Will user B see THEIR apps in inventory? | **Yes**, automatically. Discovery runs against `${ASCENDO_WEB_APPS_ROOT:-/Applications}` on every check phase. |
| Will user B see MY apps that they don't have? | **No.** Registry entries that don't match a real bundle on disk are silently skipped. |
| Can user B add custom probes for apps Ascendo doesn't know about? | **Yes** — drop entries in `~/.config/ascendo/web_apps.toml`. |

## How the inventory works (per-OS)

Every category Ascendo manages is **driven by what's actually installed
on the host**, never by a static manifest of "apps Ascendo wants to
install":

| Category | Source of truth | What "discovery" returns |
|---|---|---|
| `brew` | `brew list --formula` + `brew list --cask` | Only formulae/casks the user has installed |
| `mas` | `mas list` | Only Mac App Store apps the user has installed |
| `softwareupdate` | `softwareupdate -l` | Only OS patches Apple has staged |
| `npm` | `npm ls -g --json` filtered by `adapters/macos/config/npm_global_clis.txt` | Only globals matching the tracked-CLI list AND actually installed |
| `pip` | `pip list --format=json` filtered by `adapters/macos/config/pip_global_clis.txt` | Same: tracked AND installed |
| `web` | `lib/web_discovery.sh` walks `${ASCENDO_WEB_APPS_ROOT:-/Applications}` | One row per `.app` bundle on disk that isn't owned by brew/mas/softwareupdate |

The `npm`/`pip` manifests are **declarations of which CLIs Ascendo will
TRACK if installed** — they're not a "go install these" list. If the
user has `pipx` installed, Ascendo manages updates for it. If not, it
silently doesn't appear in the inventory.

## How the web registry works (the interesting case)

`web` apps are the only category where update mechanisms vary
per-vendor (Sparkle, Squirrel, Keystone, GitHub releases, MS AutoUpdate,
Docker, Omaha, ...). The shipped registry at
`adapters/macos/config/web_apps.toml` carries **per-app overrides**
keyed by `bundle_id`. An entry is a no-op until the user actually has
that bundle installed.

### Discovery flow (every `web --phase check` run)

```
1. Walk ${ASCENDO_WEB_APPS_ROOT:-/Applications}/*.app/Contents/Info.plist
2. For each bundle:
     a. Skip if owned by brew/mas/softwareupdate (`_owned_by` rule).
     b. Skip if matches an "ineligible" pattern (Google Workspace
        shortcuts, Defender shims, etc.).
     c. Look up bundle_id in web_apps.toml (shipped + user override).
     d. If found → use the registry's handler (Tier-A real probe).
     e. If not found → auto-classify by Info.plist fingerprints:
          SUFeedURL → sparkle (Tier-A best-effort)
          KSProductID → keystone (Tier-B trigger-only)
          Squirrel.framework → squirrel (Tier-B)
          else → builtin (manual update path)
3. Emit one item per bundle into the check sidecar.
```

**Key invariant:** registry entries that don't match a real bundle are
**silently skipped, never installed**. There's no apply path that
takes a registry entry and downloads/installs the app from scratch.

## Worked example — two users, same Ascendo, different apps

**User A's `/Applications/`:** Firefox Developer Edition, Notion,
ProtonVPN, Docker, Cursor (5 apps).

**User B's `/Applications/`:** VSCode, Slack, Telegram (3 apps; no
overlap with A).

After both run `ascendo run --category web --phase check`:

| User | What appears in their inventory | What does NOT appear |
|---|---|---|
| A | Firefox-Dev, Notion, ProtonVPN, Docker, Cursor | VSCode, Slack, Telegram (B's apps — A doesn't have them) |
| B | VSCode (Tier-A via release_feed), Slack (Tier-B keystone trigger), Telegram (filtered out — it's a MAS app) | Firefox-Dev, Notion, ProtonVPN, Docker, Cursor (A's apps — B doesn't have them) |

Neither user's machine ever installs an app they didn't already have.
The shared `web_apps.toml` ships probe URLs and version-extraction
recipes; it doesn't ship installers or app payloads.

If user B later installs ProtonVPN themselves, the next `check` phase
auto-detects it (the Info.plist appears under `/Applications/`), looks
up `ch.protonvpn.mac` in the registry, finds the existing entry, and
B inherits A's hard work — full Tier-A coverage from the first run.

## Adding a custom probe (no fork required)

`~/.config/ascendo/web_apps.toml` is merged on load — user entries win
over shipped entries on `bundle_id` match. Schema is identical to
`adapters/macos/config/web_apps.toml`. Example:

```toml
schema_version = 2

[[apps]]
slug = "my-app"
bundle_id = "com.example.MyApp"
display_name = "My App"
handler = "release_feed"

[apps.release_feed]
url = "https://example.com/latest-mac.json"
version_path = "version"
download_path = "url"
```

Workflow recommended for upstreaming a new probe:

1. Add to `~/.config/ascendo/web_apps.toml` and verify locally with
   `ascendo run --category web --phase check`.
2. Once the probe is reliable, send a PR upstreaming the entry to
   `adapters/macos/config/web_apps.toml`.
3. After merge, `git pull` ships the entry to every other Ascendo user
   automatically. Their next `check` phase picks it up.

## What state is per-machine vs in-repo

**Per-machine (NOT in git, NOT shared):**

| Path | What it holds |
|---|---|
| `~/.ascendo/runs/<run-id>/` | Sidecars + REPORT.md + per-phase logs |
| `~/.ascendo/inventory.db` | SQLite cache of what's installed |
| `~/.config/ascendo/web_apps.toml` | User's custom web probes |
| `~/.config/ascendo/ai.json` | AI provider credentials (api_key redacted from API responses) |
| `~/.config/ascendo/locale.txt` | UI language preference |
| `$TMPDIR/ascendo/askpass-*` | Per-process sudo askpass helper (never on disk after process exits) |

**In-repo (shared via git, identical for every user):**

| Path | What it holds |
|---|---|
| `core/`, `adapters/`, `app/`, `ui/` | Code |
| `adapters/macos/config/web_apps.toml` | Shipped baseline of vendor probes (overrides, not a manifest) |
| `adapters/macos/config/{npm_global_clis,pip_global_clis}.txt` | Tracked-CLI lists for npm/pip (also overrides — only fires if installed) |
| Handlers, schemas, helpers | Pure code |

A user's installed-apps state is **never** in git. Cloning the repo and
running `bin/install-dev-macos.sh` gives you the orchestrator, the
dashboard, and the shipped registry — nothing about the previous
operator's installed apps comes along.

## Customisation knobs (if defaults don't match your setup)

| Env var | Default | Use case |
|---|---|---|
| `ASCENDO_WEB_APPS_ROOT` | `/Applications` | Scan a different folder (e.g. `~/Applications` for per-user installs) |
| `ASCENDO_WEB_INELIGIBLE_PATTERNS` | (empty) | Add bundle-id glob patterns to skip — e.g. internal corporate shims |
| `ASCENDO_INVENTORY_DB` | `~/.ascendo/inventory.db` | Move the SQLite cache to another location |

## What WOULD ship a list of apps to install? (not implemented)

A "from-manifest install" workflow — the operator declares "I want
these 30 apps; install whatever I'm missing" — does not exist today.
If we ship it, it'll be opt-in (`--install-missing` flag) with a
loud confirm gate, never the default behaviour, never triggered by a
plain check phase. M6 backlog.

## See also

- `adapters/macos/config/web_apps.toml` — the shipped registry
- `adapters/macos/lib/web_discovery.sh` — the Info.plist walker
- `HANDOFF.md` Sesja 37 (M5.7) — discovery + tiered probes design
- `HANDOFF.md` Sesja 40 (M5.7.3) — coverage push + ineligible patterns
- `PLAN.md` — forward roadmap
