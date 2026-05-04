# macOS adapter — M5.3 LaunchServices inventory design

> **Status**: design approved 2026-05-04. Implementation plan to follow in
> `docs/superpowers/plans/2026-05-04-macos-inventory-launchservices.md`.
> Target tag: `v0.0.10-alpha`.

## 1. Goal

Ship `MacOSAdapter.inventory()` returning a `MacOSInventory` instance that
enumerates every installed macOS application via
`system_profiler SPApplicationsDataType -json`, classifies each by source
(SYSTEM / MAS / BREW / WEB), and emits an `ascendo/v1` sidecar consumable
by the existing dashboard `/inventory*` endpoints + 60s `InventoryCache`.

After this milestone, opening the dashboard Categories tab on macOS shows
the real installed-apps list (today: empty, because `inventory()` returns
`None` and the SPA falls back to stub data).

## 2. Architecture

Mirrors `WindowsInventory` exactly. Layers:

- **Layer 4 (core)**: one enum line — `SourceType.SYSTEM = "system"`.
  Sidecar JSON Schema regenerated.
- **Layer 5 (Python adapter)**: new `MacOSInventory(IInventory)` at
  `adapters/macos/ascendo_macos/inventory.py`. Spawns the bash list
  script, parses the resulting sidecar, returns `list[Package]`. Same
  uuid4-per-call + private tempdir pattern as `WindowsInventory` (so the
  sidecar filename never collides with phase-pipeline emissions).
- **Layer 6 (bash script)**: new
  `adapters/macos/scripts/inventory/list.sh`. Calls `system_profiler`
  once, classifies each entry post-hoc, emits one `Item` per app via
  the same `lib/ascendo_json.sh` helpers used by mas/brew.

**No new dashboard work.** `core/ascendo/dashboard/routes/spa_real.py`
already serves `/inventory`, `/inventory/summary`, `/inventory/{cat}`
and ferries the result through `InventoryCache` (60s TTL). The Mac SPA
will start populating the moment `MacOSAdapter.inventory()` returns a
non-None instance.

**No orchestrator change.** Inventory is invoked OUTSIDE the 5-phase
pipeline (matches the established Windows precedent) — the dashboard
calls `adapter.inventory().list_installed(host)` directly.

## 3. Capability flag

`MacOSAdapter.capabilities` flips from
`PACKAGE_MANAGEMENT | ELEVATION` (M5.2 state) to
`PACKAGE_MANAGEMENT | ELEVATION | INVENTORY`.

## 4. Enumeration: `system_profiler`

```bash
system_profiler -json -detailLevel mini SPApplicationsDataType
```

`-detailLevel mini` returns the fields we want without the heavy
"signed_by certificate chain" detail (saves ~2× wall time on machines
with many apps).

Returns JSON shape (one element per app):

```json
{
  "_name": "Amphetamine",
  "info": "5.3.5",
  "lastModified": "2024-08-12T14:23:11Z",
  "obtained_from": "mac_app_store",
  "path": "/Applications/Amphetamine.app",
  "signed_by": ["Apple Mac OS Application Signing", "Apple Worldwide ..."],
  "version": "5.3.5",
  "arch_kind": "arch_arm_i64"
}
```

Wall time on Mac.r12.home (~13 mas + ~30 brew casks + ~50 web/system
apps): ~3-5s. The 60s dashboard cache makes the user-perceived latency
near-zero after the first request.

`obtained_from` field values observed on macOS 14+:
`apple`, `mac_app_store`, `identified_developer`, `unknown`. We use
this as a *hint* but override with our own classification (next §).

## 5. Source classification (post-hoc)

Decision tree applied to each app's `path`, `bundle_id` (extracted from
the app's `Info.plist` via `defaults read`), and the result of two
warm-up commands run once at start of the script:

```bash
MAS_IDS_BY_BUNDLE_ID="$(mas list 2>/dev/null | awk '{print $1}' | xargs -I{} mas info {} 2>/dev/null | …)"
BREW_CASK_TOKENS="$(brew list --cask 2>/dev/null)"
```

(Implementation may use simpler probes — see plan. The classification
output is what matters.)

Rules evaluated top-to-bottom; first match wins:

```
1. path startswith /System/Applications/             → SYSTEM
2. bundle_id in MAS_IDS                              → MAS
3. system_profiler.obtained_from == mac_app_store    → MAS    (catches iPad apps + sandboxed apps mas CLI doesn't track)
4. cask token matches app name (lowercased)          → BREW
5. otherwise                                         → WEB
```

Rule 2 beats rule 4 deliberately: when an app exists both in `mas list`
and as a brew cask (rare but happens — e.g. some users install via both
to get auto-update from one and version-pinning from the other), MAS is
the more authoritative source for upgrade decisions.

Rule 3 is the iPad/sandboxed-app safety net — `mas` CLI doesn't enumerate
every Mac App Store app on Apple Silicon. Falling back to
`system_profiler`'s own `obtained_from` field catches them.

`SYSTEM` covers Apple-bundled apps (Mail, Safari, Calculator, etc.).
The dashboard Categories tab will group them as a separate non-managed
section so users can see them without expecting upgrade actions.

`mas list` and `brew list --cask` are skipped gracefully if the tool
isn't installed — rules 2 + 4 simply never fire, and apps fall through
to rule 3 (App Store) or rule 5 (WEB).

## 6. Per-app metadata captured

Each `Item` carries:

| Field | Source |
|---|---|
| `id` | `bundle_id` (e.g. `com.apple.Safari`) — fallback to bundle path basename if Info.plist unreadable |
| `current_version` | `CFBundleShortVersionString`, fall back to `CFBundleVersion`, fall back to `system_profiler.version` |
| `target_version` | empty (inventory is pure read; upgrade-target lookup is the manager's job) |
| `status` | `up_to_date` (always — inventory has no notion of pending) |
| `source.type` | classification result (SYSTEM/MAS/BREW/WEB) |
| `source.feed` | bundle path |
| `evidence.binary_path` | bundle path |
| `evidence.binary_version` | same as `current_version` |

Skip `file_size`, `signed_by[]`, `arch_kind` — dashboard doesn't render
them (parity with Windows ARP). Adding them later is one bash + Python
change with zero schema impact (they all fit into existing free-form
fields).

## 7. Caching

The Python wrapper does NOT cache. The dashboard's existing
`InventoryCache` (60s TTL, per-adapter) covers the only realistic
high-frequency read pattern. Direct CLI/programmatic callers always get
fresh data — important for ad-hoc scripts and the upcoming `ascendo
inventory list` CLI command (out of scope for M5.3).

## 8. CLI surface

No new `python -m ascendo` subcommand in this milestone. The dashboard
flow is the user-facing artifact. Operators can hit the
`MacOSInventory` directly from a Python REPL if needed; we'll wrap it
in `ascendo inventory list` later if there's demand.

## 9. Tests

Following M5.2 precedent (sidecar-emitting bash + Python wrapper +
real-hardware Stage):

- **Bash script tests** (`adapters/macos/tests/test_inventory_list_script.py`,
  ~6 tests): fake `system_profiler` binary fed a fixture JSON;
  verifies items emitted, classification correct, sidecar parses
  through `parse_sidecar`, signed-out / no-mas / no-brew graceful
  fallback.
- **Python wrapper tests**
  (`adapters/macos/tests/test_macos_inventory_smoke.py`, ~8 tests):
  mock-based — patches `subprocess.run` to return a canned sidecar
  path, asserts `list_installed` returns the expected `Package` list,
  `emit_sidecar` produces a valid `Sidecar`, missing sidecar raises
  `ManagerError`, etc.
- **Adapter wire-up tests** (extend
  `adapters/macos/tests/test_adapter_smoke.py`, +3 tests):
  capabilities flag includes INVENTORY, `inventory()` returns a
  `MacOSInventory` (singleton), `health_check()` reports
  `system_profiler` component.
- **Stage 9 in `bin/validate-macos.sh`**: probes
  `python3 -m ascendo doctor | grep system_profiler`, then runs
  `inventory.list_installed()` end-to-end on the real Mac (no fakes),
  asserts non-empty list. Skip if running on Linux (mac-only
  validate harness — already true).

Total target: ~17 new tests + Stage 9 e2e.

## 10. Threat model deltas

`system_profiler` is a built-in macOS binary, no signing risk. We
invoke it with no shell-string interpolation (argv-only). The bash
script reads /System paths but never writes; the script runs as the
unprivileged user, no sudo. No network. No new T1-T7 surface from
M5.2.

## 11. What's deferred

- **`ascendo inventory list` CLI command** — wait for user demand.
- **Per-app upgrade-availability** — that's `mas outdated` /
  `brew outdated` territory, already covered by the 5-phase
  managers; inventory remains a pure snapshot.
- **iPad/iOS apps installed via Apple Silicon** — they appear in
  `system_profiler` with `obtained_from=mac_app_store` but their
  `bundle_id` typically does NOT show up in `mas list`. Rule 3 of the
  classification tree (§5) catches them: they classify as MAS via the
  `obtained_from` fallback. Dashboard shows them as MAS. `mas` itself
  can't upgrade them — M5.2 deferred Track 2 for iPad-app upgrade
  automation; that's where the upgrade story gets resolved. Inventory
  visibility is the M5.3 win.
- **Symlinked `~/Applications`** — some users symlink their personal
  apps directory. `system_profiler` follows the link, so we get them
  for free. No special handling.

## 12. Tag exit bar

`v0.0.10-alpha` after:
- All ~17 new tests green
- `bin/validate-macos.sh` Stage 9 green on Mac.r12.home (≥ 50 apps
  enumerated, classification distribution sensible — at least 5 MAS,
  at least 5 BREW, at least 5 SYSTEM)
- Dashboard `/inventory` returns the real list (manual smoke from a
  browser opened to `http://127.0.0.1:8765/`)

## 13. Spec + plan references

- This spec: `docs/superpowers/specs/2026-05-04-macos-inventory-launchservices-design.md`
- Plan (next): `docs/superpowers/plans/2026-05-04-macos-inventory-launchservices.md`
- M5.2 spec for comparison: `docs/superpowers/specs/2026-05-03-macos-mas-elevation-design.md`
- M5.2 handoff (Sesja 21) for the workflow precedent: `HANDOFF.md`
