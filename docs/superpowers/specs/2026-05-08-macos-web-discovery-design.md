# macOS Web Manager — Auto-Discovery + Tiered Probes (M5.7)

> **Status:** design, awaiting plan generation
> **Author:** session 37 (post-v0.3.0 follow-up)
> **Supersedes parts of:** `2026-05-06-macos-web-updater-design.md` (M5.6)
> **Targets:** v0.4.0
> **Scope:** macOS adapter, `WebManager` only

## 1. Problem statement

M5.6 shipped `WebManager` with 7 handlers covering 24 curated apps. After
real-Mac smoke on Mac.r12.home (60 apps in `/Applications`), the
operator-facing reality is:

| Outcome on `web --phase check` | Apps |
|--------------------------------|------|
| `up_to_date` / `planned` (real candidate detected) | 4 (brave, keepassxc, trezor-suite, chatgpt-atlas) |
| `skipped, candidate=None` (handler opaque by design) | 9 (chrome, gdrive, gemini, claude, chatgpt, warp, lm-studio, perplexity, comet) |
| Not detected (not in registry but installed) | ~10 (antigravity, notion, obsidian, proton-mail, proton-drive, protonvpn, codeedit, telegram, whatsapp, zoom, …) |
| Not installed | 11 (registry includes opera, firefox-dev, ledger-live, etc.) |

Two structural gaps:

- **Breadth gap.** Registry is a static curated list of 24. Newly-installed
  apps don't show up unless someone hand-edits TOML.
- **Depth gap.** `keystone_check` and `squirrel_check` deliberately echo
  empty because their underlying update agents are opaque from outside the
  app process. ~50% of the registry is silently invisible to the operator.

Goal of M5.7: close both gaps. Every web-orphan app installed on the
machine appears in `check` output, with the most precise candidate version
we can fetch given the vendor's update mechanism. Where no precise probe
exists, status is honest (`triggered` after apply, `skipped: opaque`
before).

## 2. Non-goals

- Self-installing the Sparkle framework probe at runtime to query apps
  that don't link Sparkle.
- Auto-detecting Sparkle appcasts where `SUFeedURL` is present but the
  vendor's feed is broken / uses a non-standard schema. Manual override
  path covers this.
- Cross-cutting refactor of the 5 duplicated `_*_get` heredoc helpers
  (still deferred to v0.4 cleanup, tracked in `HANDOFF.md` Sesja 35).
- Refactoring the Tier 1 / Tier 2 sources (brew/mas/softwareupdate/npm/
  pip/web) — only `web` is in scope.
- macOS adapter changes outside `WebManager`. No new `IPackageManager`,
  no schema bumps to `ascendo/v1` sidecar contract.

## 3. Architecture overview

Replace the static registry-as-source-of-truth with a three-layer pipeline:

```
                    /Applications/*.app
                          ↓
              ┌─────────────────────────┐
              │  1. Discovery layer     │  walks /Applications, reads
              │  web_discovery.sh       │  Info.plist fingerprints,
              │                         │  computes ownership exclusions
              └────────────┬────────────┘
                           ↓ list[{bundle_id, version, fingerprint}]
              ┌─────────────────────────┐
              │  2. Override registry   │  optional per-app overrides:
              │  web_apps.toml v2       │  ksadmin productID, GH repo,
              │                         │  release_feed URL, kill_safe…
              └────────────┬────────────┘
                           ↓ list[ResolvedApp]
              ┌─────────────────────────┐
              │  3. Handler dispatch    │  Tier-A: real probe (sparkle,
              │  lib/handlers/*.sh      │  github_dmg, release_feed,
              │                         │  msupdate, docker)
              │                         │  Tier-B: trigger only
              │                         │  (keystone, squirrel, builtin)
              └─────────────────────────┘
```

Every component has a single responsibility and a fakeable boundary
(`ASCENDO_WEB_APPS_ROOT` for discovery, fixture TOML for registry,
existing fake-fetch hooks per handler).

## 4. Discovery layer

### 4.1 Inputs

- `${ASCENDO_WEB_APPS_ROOT:-/Applications}` — root to walk. Test fixture
  point.
- Existing manager-ownership signals (cached per-run):
  - `brew list --cask` → casks owned by Homebrew.
  - `mas list` → bundle IDs owned by Mac App Store.
  - `system_profiler SPApplicationsDataType` → `Software Signing` issued
    by Apple → softwareupdate-managed.

Microsoft Office bundles (`com.microsoft.*`) are **not** filtered out
here — they're owned by the `web` manager via the `msupdate` handler,
which wraps Microsoft AutoUpdate. They flow through discovery normally
and the classifier (§4.3) routes them to `msupdate`.

### 4.2 Output

A JSON array streamed to stdout (one app per line) by
`web_discovery.sh --emit-json`:

```json
{
  "bundle_id": "com.electron.warp",
  "app_path": "/Applications/Warp.app",
  "version": "0.2026.04.29.08.57.01",
  "display_name": "Warp",
  "fingerprint": {
    "sparkle_feed_url": null,
    "keystone_product_id": null,
    "squirrel_framework": false,
    "shipit_helper": true,
    "executable_signed_by": "Warp Terminal Inc."
  },
  "owned_by": null
}
```

`owned_by` is one of `brew`, `mas`, `softwareupdate`, or `null`. Web
manager only processes `null` rows; the others are surfaced by the
respective managers and including them would double-count. (Microsoft
Office apps stay `null` because the `msupdate` handler is part of `web`.)

### 4.3 Classification (fingerprint → handler)

Priority order (first match wins):

1. **Override hit.** `web_apps.toml` has a row for this `bundle_id`.
   Use that handler regardless of fingerprint.
2. **Apple-bundled / system app.** Skip (owned by softwareupdate).
3. **Microsoft Office app.** Route to `msupdate` handler.
4. **Sparkle appcast detected.** `Info.plist:SUFeedURL` present →
   `sparkle`.
5. **Keystone product.** `Info.plist:KSProductID` present → `keystone`.
6. **Squirrel.framework present.** → `squirrel`.
7. **`Frameworks/.../Resources/ShipIt`** present (Squirrel-without-
   framework variant) → `squirrel`.
8. **Fallback.** `builtin` handler with `display_name`-only config —
   apply just runs `open -a` and emits an instructional sidecar message.

Discovery never fails the run. Unrecognised apps land in `builtin` and
the operator sees them in inventory with an honest "no auto-update
mechanism known."

### 4.4 Caching

Discovery is recomputed every check. Cost is bounded by app count (≤80
in practice) × ~3 PlistBuddy calls = sub-second on real hardware. No
on-disk cache; the SQLite `inventory.db` already absorbs run output for
the SPA.

## 5. Registry v2 schema

### 5.1 Schema bump

`schema = "ascendo-web-apps/v1"` → `"ascendo-web-apps/v2"`. v1 entries
load as v2 with `bundle_id` made required and slug demoted to display-
only. Validator emits a single deprecation message if v1 schema is
seen, then auto-coerces.

### 5.2 v2 record shape

```toml
schema = "ascendo-web-apps/v2"

[[apps]]
# Identity (required)
bundle_id   = "com.google.Chrome"
display_name = "Google Chrome"

# Optional override fields (additive over discovery defaults)
slug        = "chrome"          # display only; defaults to slugified display_name
handler     = "keystone"        # override classification
enabled     = true              # default true
notes       = "Long-form note for operator override."

# Tier-A handler config (mutually exclusive by handler)
appcast_url        = "https://…"             # sparkle
apply_cli_argv     = ["--update", "--silent"] # sparkle (optional)
github_repo        = "user/repo"             # github_dmg
asset_pattern      = "Foo-{version}-arm64\\.dmg"  # github_dmg
arch               = "arm64"                 # github_dmg
prerelease         = false                   # github_dmg
ksadmin_product_id = "com.google.Chrome"     # keystone
update_url         = "https://…"             # builtin

# release_feed (new in v2) — generic JSON-feed probe, Tier-A
[apps.release_feed]
url            = "https://desktop.warp.dev/version.json"
version_path   = "latest.darwin.arm64.version"
download_path  = "latest.darwin.arm64.url"   # optional; promotes apply to Tier-A install
arch_path      = "latest.darwin.arm64.arch"  # optional; sanity-check arch match

# Behaviour overrides (apply to any handler)
defer_if_running = true     # default per-handler; override here
kill_safe        = false    # default false; if true, apply may TERM running app
```

`extra="forbid"` retained — typos still rejected. Handler-irrelevance
checks tightened: each tier-A handler has a known set of required +
optional fields; cross-handler fields rejected.

### 5.3 Override resolution

Discovery emits a default record per installed app. The merge
algorithm:

```
final = deepcopy(discovery_default)
if override exists for bundle_id:
    for field in override:
        final[field] = override[field]   # user wins, field-by-field
```

`bundle_id` is the merge key. Slug is purely cosmetic; collisions
across bundle IDs are rejected at validation.

### 5.4 Migration of M5.6 24 entries

Each existing entry ports to v2 with `bundle_id` populated from a
mapping table built once during this milestone. The 24 mappings already
exist as `bundle_id_check` matchers in `ascendo_web.sh` — we extract
them into the TOML directly (no runtime fetch).

Three entries marked `enabled = false` because their bundle IDs were
unverified on Mac.r12.home (per HANDOFF Sesja 35: opera, ledger-live,
ms365). These re-enable themselves automatically when discovery
detects them on a machine where they're installed.

## 6. Handler tier rework

### 6.1 Tier definitions

| Tier | Handlers | Check semantics | Apply semantics |
|------|----------|-----------------|------------------|
| **A** (real probe) | `sparkle`, `github_dmg`, `release_feed`, `msupdate`, `docker` | Fetch candidate version → compare vs installed | Download + install (or invoke vendor CLI) |
| **B** (trigger only) | `keystone`, `squirrel`, `builtin` | No candidate; status `skipped` with reason `vendor_opaque` | Trigger vendor's update agent; status `triggered` |

Promotion path: a Tier-B app gains a `release_feed` override → handler
flips to `release_feed` → app graduates to Tier-A. No code change in
the handler dispatch.

### 6.2 New status: `triggered`

Add `ItemStatus.TRIGGERED = "triggered"` to `core/ascendo/models/result.py`
+ regenerate `sidecar.v1.schema.json`. Frontend renders a neutral
("info") pill. `triggered` is **not** a failure — it's "we did our part,
the vendor's daemon will finish asynchronously, and `verify` will
re-read the version after a delay."

`triggered` only emitted by Tier-B apply. Check-phase skips for Tier-B
remain `skipped` with `reason: "vendor_opaque"` (machine-readable, can
be filtered out of UI counts).

### 6.3 Verify behaviour per tier

| Tier | Verify | Sleep |
|------|--------|-------|
| A — sparkle / github_dmg / release_feed | Compare new installed version to expected candidate; mismatch → `failed` | 0 |
| A — msupdate / docker | Re-query CLI; mismatch → `failed` | 0 |
| B — keystone | Re-read `CFBundleShortVersionString` after sleep. Version change → `triggered_confirmed`. No change → `triggered_pending` (still success — Keystone often defers to next user idle). | 10 s |
| B — squirrel | Re-read `CFBundleShortVersionString`. Version change → `triggered_confirmed`, no change → `triggered_pending` (Squirrel updates apply only on next relaunch — pending is the common case). | 30 s |
| B — builtin | No-op | 0 |

## 7. The `release_feed` handler

### 7.1 Purpose

A generic JSON-over-HTTPS probe for vendors that publish a release feed
but don't ship Sparkle. Closes the gap for Warp, Claude, ChatGPT,
Comet, Perplexity, Cursor, Antigravity, etc.

### 7.2 Configuration

```toml
[apps.release_feed]
url            = "https://desktop.warp.dev/version.json"
version_path   = "latest.darwin.arm64.version"
download_path  = "latest.darwin.arm64.url"
arch_path      = "latest.darwin.arm64.arch"
expected_arch  = "arm64"        # if arch_path provided, must match
http_timeout_s = 8              # default 8
http_method    = "GET"          # only GET supported in v2
```

### 7.3 Implementation

`adapters/macos/lib/handlers/release_feed.sh` exports two functions
mirroring the existing handler contract:

```bash
release_feed_check  <slug> <config_json>   # echoes candidate version, exit 0
release_feed_apply  <slug> <config_json>   # downloads + installs DMG if download_path set
                                          # else falls back to `open -a` (trigger-only)
```

Internally:

1. `curl -fsSL --max-time "$http_timeout_s" "$url"` → response body.
2. Parse JSON via Python heredoc-via-env (existing pattern in
   `_sparkle_get`/`_gh_get`).
3. Walk `version_path` (dotted with `[N]` indices, no jq dep).
4. Echo the version string.

Apply path takes the resolved `download_path` URL, runs the existing
`_web_install_dmg` helper from `lib/ascendo_web.sh` (signature check
+ quarantine xattr strip + `/Applications` write w/ `sudo -A` on
EACCES — already battle-tested in M5.6).

### 7.4 Failure modes

| Failure | Status | Sidecar message |
|---------|--------|-----------------|
| HTTP timeout / 5xx / DNS | `skipped` | `release_feed: HTTP <code> from <url>` |
| Body not JSON | `skipped` | `release_feed: malformed JSON from <url>` |
| Path missing in JSON | `skipped` | `release_feed: missing path <p> in feed` |
| Arch mismatch | `skipped` | `release_feed: arch mismatch (got <a>, expected <e>)` |
| Apply download fails | `failed` | (existing `_web_install_dmg` error path) |

`skipped` here means "we tried; vendor feed isn't reachable or shaped
right; not the operator's fault." Distinct from Tier-B `skipped:
vendor_opaque`.

## 8. Status semantics (full table)

```
                 Tier-A handlers         Tier-B handlers
check, ok        up_to_date | planned   skipped (reason: vendor_opaque)
check, fail      skipped (reason: …)    skipped (reason: vendor_opaque)
plan, ok         planned                skipped
plan, fail       skipped                skipped
apply, ok        success                triggered
apply, fail      failed                 failed
verify, ok       up_to_date             triggered_confirmed | triggered_pending
verify, fail     failed                 failed
cleanup          (no-op)                (no-op)
```

`triggered_confirmed` and `triggered_pending` are sub-statuses of
`triggered`. They're optional refinements emitted by verify; if the
adapter doesn't support the distinction, it stays at plain `triggered`.

## 9. Phasing

### 9.1 M5.7 — discovery + tier rework + release_feed skeleton

Single milestone, ~14 tasks. Lands as v0.4.0.

- T1: Add `ItemStatus.TRIGGERED` enum + regenerate sidecar schema.
- T2: `WebRegistry` schema bump v1 → v2 with auto-coerce of v1.
- T3: Discovery script `lib/web_discovery.sh` + `--emit-json`.
- T4: Discovery integration in `scripts/web/check.sh` (replaces
  `--list-slugs` walk).
- T5: Override resolution in `web_registry.py` keyed by `bundle_id`.
- T6: Migrate the 24 M5.6 entries to v2 with `bundle_id` populated.
- T7: Handler tier dispatch in check.sh / plan.sh / apply.sh / verify.sh.
- T8: `release_feed.sh` handler skeleton (no per-vendor configs yet).
- T9: Tier-B verify with sleep + sub-status emission.
- T10: SPA Apps tab status pill renders `triggered` correctly.
- T11: New tests:
  - `test_web_discovery.sh` — fixture `/Applications` root with 4
    bundles (sparkle, keystone, squirrel, no-fingerprint).
  - `test_release_feed_handler.sh` — fixture HTTP server with 200/404/
    malformed-JSON/missing-path responses.
  - Existing 358 macOS adapter tests stay green (registry-shim level
    compat).
- T12: `bin/validate-macos.sh` Stage 13 extended with sub-steps 13.8
  (discovery enumerates ≥20 web-orphan apps) and 13.9 (release_feed
  resolves a candidate against fixture feed).
- T13: PLAN.md / HANDOFF.md / MACOS_QUICKSTART.md updated.
- T14: `bin/run-tag-release-macos.sh` bumps to v0.4.0; final review.

**Coverage outcome of M5.7:**
- Every installed web-orphan app appears in `check` output.
- Tier-A apps (sparkle / github_dmg / msupdate / docker) keep working.
- Tier-B apps emit honest `skipped: vendor_opaque` on check, `triggered`
  on apply.
- `release_feed` handler exists and is tested but no per-app configs
  shipped yet.

### 9.2 M5.7.1 — first batch of vendor probes (registry-only)

No code change. Pure `web_apps.toml` v2 additions. Targets:

| App | Probe URL (subject to verification) | Handler flip |
|-----|-------------------------------------|--------------|
| Warp | `https://desktop.warp.dev/version.json` (or scraped from app's update endpoint) | squirrel → release_feed |
| Claude | Anthropic's release endpoint (TBD — observe network traffic on app launch) | squirrel → release_feed |
| ChatGPT | OpenAI's release endpoint (TBD) | squirrel → release_feed |
| Antigravity | Vendor's release endpoint (TBD) | squirrel → release_feed |
| Comet | (probably needs a feed; if vendor exposes none, stays squirrel) | squirrel → release_feed (if feasible) |
| Perplexity | Probably GitHub releases | squirrel → github_dmg |
| Cursor | `https://download.cursor.sh/api/update/darwin-arm64/cursor/latest` (well-known) | discovered → release_feed |

Each row is one TOML stanza. Discovery confirmation step per app: run
`curl -fsSL <url>` on Mac.r12.home, validate response shape, write the
probe.

**Coverage outcome of M5.7.1:** ~6 more Tier-B apps move to Tier-A.

### 9.3 M5.7.2+ — ongoing vendor probe additions

Treated as registry-only PRs; not milestone-scoped. New apps that
don't fit the existing handlers may need handler-specific helpers; if
so, that's an M5.7.x milestone with a new spec.

## 10. Test strategy

### 10.1 Discovery

`adapters/macos/tests/fixtures/discovery/Applications/` — 4 fake `.app`
bundles:

- `FakeSparkle.app/Contents/Info.plist` with `SUFeedURL` set.
- `FakeKeystone.app/Contents/Info.plist` with `KSProductID` set.
- `FakeSquirrel.app/Contents/Frameworks/Squirrel.framework/`.
- `FakeOrphan.app/Contents/Info.plist` — no fingerprints.

Tests assert: each fixture classifies to expected handler; orphan
falls to `builtin`; bundle IDs deduplicate; ownership exclusions can
be mocked via env vars (`ASCENDO_WEB_BREW_CASKS=foo,bar`,
`ASCENDO_WEB_MAS_BUNDLE_IDS=…`).

### 10.2 release_feed handler

Use a fixture HTTP server (Python `http.server` in subprocess, like
existing test patterns). Cases:

- Happy path: 200 + JSON with version_path → echoes version.
- 404 → exit 25 (configured in handler) + sidecar message.
- 500 → exit 26.
- Malformed JSON → exit 27.
- Missing path → exit 28.
- Arch mismatch → exit 29.
- Timeout → exit 30 (use `--max-time 1` against a 5s-delay endpoint).

### 10.3 Registry v1→v2 coercion

A v1 fixture TOML loads cleanly under v2 schema. A v2 fixture with
required `bundle_id` missing fails validation. A v1 with handler-
specific cross-fields (existing in M5.6 codebase) preserves behaviour.

### 10.4 End-to-end (Stage 13)

`bin/validate-macos.sh` adds:

- 13.8 `web --phase check` produces ≥ 20 items (real Mac.r12.home count).
- 13.9 release_feed handler resolves a candidate against a fixture
  feed launched on `127.0.0.1:8780`.
- 13.10 keystone+squirrel apply emits `triggered` status.
- 13.11 cleanup removes stale `~/Library/Caches/Ascendo/web/` files
  >7 days (already in M5.6, retained).

## 11. Backward compatibility

- Sidecar contract `ascendo/v1` extended (new enum value), not broken.
  Existing readers tolerate unknown enum values via Pydantic
  `use_enum_values=True` fallback.
- Slug remains in `Item.id` (e.g. `web:warp`), so SPA caches +
  `inventory.db` upserts are unaffected.
- M5.6 user override TOMLs at `~/.config/ascendo/web_apps.toml` load
  under v2 schema (auto-coerce). Operator sees a one-line deprecation
  notice in run output: `web_registry: schema v1 detected, treating as
  v2 (please bump 'schema' field on next edit)`.
- `WebManager.health_check()` component count stays at 12 (web's slot
  still validates registry parses + counts active apps).

## 12. Threat-model deltas

- **Discovery surface.** `web_discovery.sh` reads `Info.plist` files.
  PlistBuddy is read-only; no exec, no network during discovery.
- **release_feed network.** Adds a new outbound HTTPS surface per
  override row. Mitigations:
  - https-only constraint (existing T3 mitigation, retained).
  - No request bodies / cookies — pure GET.
  - Response capped at 256 KiB to avoid OOM on hostile feed.
  - JSON parser is stdlib `json.loads` (no eval).
- **DMG download path.** Reuses `_web_install_dmg` from M5.6. spctl
  signature check + quarantine xattr strip already mitigate T3.
- **Path traversal.** `bundle_id` constrained to
  `^[A-Za-z0-9._-]+$` (Pydantic regex) so it can't escape the registry
  override map.

## 13. Open questions / risks

### 13.1 Discovery cost on huge `/Applications`

PlistBuddy invocation per app + brew/mas list fetch. On a typical Mac
(60-80 apps) this is sub-second. On corporate fleets with 200+ apps,
could approach 3-5 s per check. Mitigation: parallelise the
PlistBuddy walk with `xargs -P 8`. Defer to follow-up if issue
observed.

### 13.2 Brew cask name mismatches

`brew list --cask` gives token names (e.g. `keepassxc`), not bundle IDs.
Need a lookup `brew info --cask --json=v2 <token>` to extract bundle ID
from `artifacts[]`. This is the same gap flagged in HANDOFF Sesja 25
(brew classification rule). Mitigation: acceptable false positives in
M5.7 (a brew-managed cask appearing in web inventory is non-fatal —
worst case both managers attempt update, brew wins). Fix in M5.7.x.

### 13.3 Vendor feed brittleness

`release_feed` URLs from vendors aren't contractually stable. Warp,
Claude, etc. can rotate endpoints any time. Mitigation:
- Each override row has a `notes` field documenting how the URL was
  derived + last-verified date.
- Failed fetches degrade to `skipped`, not `failed`. Operator sees
  per-app message.
- Periodic verification in CI (M5.7.x): one job per vendor probe
  per week, alerts when probe shape changes.

### 13.4 Triggered-but-no-version-change ambiguity

A user with auto-updates already enabled may relaunch Claude, Squirrel
finishes, version bumps. Apply's `triggered` is honest — it says "I
did my part." But verify can't tell whether the version bump was
ours or Claude's own background updater. Mitigation: verify reports
`triggered_confirmed` if version changed during the verify window;
`triggered_pending` if not. Either is success, just informational.

### 13.5 SPA Apps tab semantics

Today the Apps tab counts `up_to_date` and `planned` only. Adding
`triggered` to a "successful apply" bucket means the count of "apps
needing update" depends on what the Tier-A vs Tier-B mix looks like.
Mitigation: Apps tab gets a small refinement — counts split into
"need update" (planned), "up to date" (up_to_date), and "self-updates"
(triggered + skipped:vendor_opaque). All three sum to total visible.

## 14. Decision log

- **Bundle_id, not slug, as canonical key.** Slug collides across
  vendors (`brave`/`brave-browser`); bundle_id is system-unique.
- **Discovery on every check, not cached.** `inventory.db` already
  caches results per-run. Recomputation is sub-second.
- **No new `IPackageManager` capability flag.** `WebManager` retains
  its current capability surface; this is internal restructuring.
- **Trigger-only is first-class, not a degraded mode.** Apps where
  the vendor self-updates show `triggered` after apply, not "skipped:
  not implemented." This is an honest representation of the world.
- **`release_feed` is a single generic handler, not N per-vendor
  handlers.** Per-vendor logic lives in TOML overrides. Code stays
  small; configuration grows.

## 15. References

- M5.6 design: `docs/superpowers/specs/2026-05-06-macos-web-updater-design.md`
- ADR-0003: JSON v1 sidecar contract
- ADR-0005: Six-layer architecture
- ADR-0007: Plugin manifest v1 (similar tier structure)
- HANDOFF Sesja 35 (M5.6 ship): `HANDOFF.md` lines describing M5.6.
- HANDOFF Sesja 25 (brew classification gap): same file.
- Real-Mac smoke evidence (60 apps in `/Applications`): captured in
  this session's working notes.
