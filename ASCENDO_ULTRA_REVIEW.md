# Ascendo Ultra Review — Architecture, Process, Inventory, and Production Plan

> **Scope & method.** Read-only review of the live repository at `/Users/mk/Dev_Env/Ascendo` (no labeled code chunks were pasted; the live tree + the project's own `CLAUDE.md`/`PLAN.md`/`HANDOFF.md`/ADRs were the inputs). Findings were produced by a 7-dimension multi-agent pass (architecture, inventory/state, update-engine, web-apps, cross-platform/prod, diff/detection, tests/CI), then **adversarially verified** against the actual code — several plausible findings were **refuted** and are marked as such so this report does not propagate false claims. Highest-stakes files (`models/legacy.py`, `dashboard/inventory_db.py`, `orchestrator/run_async.py`, `orchestrator/sidecar_io.py`, `adapter_factory`, `dashboard/app.py`, `.github/workflows/validate.yml`) were also read first-hand.
>
> **Constraint honored:** no code was modified or rewritten. Everything below is analysis + patch-style suggestions + a phased plan. Assumptions (vs. confirmed code) are flagged inline.

---

## 1. Executive Summary

Ascendo is a genuinely well-architected project for its stage: a clean 6-layer separation (SPA → Tauri shell → FastAPI → Python core → Python adapter → native scripts), an OS-agnostic JSON "sidecar" contract (`ascendo/v1`), and a thoughtful adapter-factory with entry-point discovery + direct-import fallback. The macOS adapter is feature-complete and battle-hardened across ~82 sessions; Windows is close; Ubuntu trails. The codebase shows real engineering maturity — frozen Pydantic models, atomic+locked sidecar I/O, phase-priority reconciliation, and a documented threat model.

**However, it is not yet production-ready for a 1.0 across three OSes.** The review surfaced a consistent theme — **silent degradation**: the system frequently chooses "appear to succeed / show green" over "surface the failure." This shows up in the inventory status vocabulary (`failed→outdated`, `triggered→up_to_date`), in web-app update handling (the central pain point — apps are *kicked* but never reconciled if the vendor daemon dies; a feed-regex break silently emits a raw version; a discovery crash reports "0 outdated = all current"), and in best-effort post-run flushes that swallow exceptions.

The most important, **verified** issues to fix before 1.0:

1. **Fedora/Arch are detected, routed to the Ubuntu adapter, and silently produce empty inventory** (no `dnf`/`pacman`). A user on those distros sees "nothing installed." *(A4 — verified)*
2. **The security threat model is partly unwired:** `ISource.verify_signature` is declared but **implemented by no adapter** (T2/T3 gap), and the dashboard ships **CORS `allow_origins=["*"]` with no auth token** while `--host` is unconstrained — a confused-deputy / network-exposure surface for a tool that triggers privileged updates. *(P1, P5 — verified)*
3. **CI is shallow.** `.github/workflows/validate.yml` runs only config-validation + readme-check on `ubuntu-24.04`. The **132 Python test files, 4 bats suites, and the `validate-*.sh/.ps1` harnesses do not run in CI**, and there is no Windows/macOS matrix. PowerShell scripts have **zero execution tests**. *(T8, T10, T2 — verified)*
4. **The "web app not updating" complaint is structural, not a single bug:** Tier-B (Keystone/Squirrel/builtin) apps emit `triggered`, the inventory paints them `up_to_date`, and **nothing ever detects if the vendor agent failed** — the app stays outdated forever, invisibly. *(W13, W4, `_INVENTORY_STATUS_MAP` — verified)*
5. **Cross-run inventory hygiene leaks:** upsert-only flush orphans rows for apps uninstalled between runs (only the narrow apply-skipped case is evicted), and `bulk_upsert` never updates `last_scan_at`, so `is_fresh()` can serve a stale *partial* DB as if it were a full scan. *(I2, D4, I9 — verified)*

The good news: the architecture can absorb all of these without restructuring. The fixes are mostly additive (hard-error vs. silent-fallback, a reconciliation pass, a real CI matrix, an honest status vocabulary, source verification). A focused Phase 0/1 effort gets Ascendo to a defensible 1.0-beta.

**Refuted-during-verification (do NOT action):** several initially-flagged items were checked against code and found false — `stop_on_failure` does abort correctly (E2); the release_feed *text* path *does* guard against emitting a 2 MiB body (W5); Windows UAC redirection paths *are* quoted (P2); the cross-run "oscillation" overlay *does* overwrite stale status to `up_to_date` (D1, D2); the `_normalize_item_id` "contains-name" claim was wrong — it only collapses *ends-with-separator+name* (I6, though a narrower real variant survives as D3). These are documented in §4–§8 so the team doesn't waste effort on non-bugs.

---

## 2. Reconstructed Mental Model of Ascendo

**Problem solved.** One unified control plane to keep *every* installed thing current across Windows, macOS, and Ubuntu — OS updates, package-manager apps (winget/msstore/apt/snap/brew/npm/pip/flatpak), and "web" apps installed outside any package manager (Sparkle/Keystone/Squirrel/Omaha/Electron-feed apps), plus driver/firmware plugins (Dell DCU). Surfaced via CLI (`ascendo …`), a local web dashboard, and a Tauri desktop shell.

**Core concepts.**
- **Adapter** (per-OS) implements `IAdapter` and exposes capability flags + sub-interfaces: `IPackageManager`, `IInventory`, `ISnapshot`, `IScheduler`, `ISource`, `IElevation`.
- **Package manager / source** = a category (e.g. `winget`, `brew`, `web`) that runs the **5-phase contract**: `check → plan → apply → verify → cleanup`.
- **Sidecar** = the immutable JSON record one phase×category emits (`ascendo/v1`), validated by frozen Pydantic models. The orchestrator aggregates sidecars into a `RunReport` and (for apply) a human-readable `REPORT.md`.
- **Inventory** = canonical SQLite cache (`~/.ascendo/inventory.db`) that *both* the Categories and Apps views read, fed by (a) full live-scans and (b) post-run sidecar flushes.
- **Item status taxonomy** = `up_to_date / planned / missing / skipped` (scan vocabulary) and `success / failed / triggered / partial` (apply/verify vocabulary), folded together for the inventory.

**Data flow.**
```
discover (OS enumerators + web fingerprinting)
   → check/plan (probe candidate versions, classify)
   → apply (mutate; Tier-A install, Tier-B trigger vendor agent)
   → verify (re-read installed version; sleep for async agents)
   → cleanup (prune)
   → sidecars on disk  ──► RunReport + REPORT.md
                        └─► post-run flush → inventory.db (phase-priority merge + uninstall eviction)
                        └─► update_history (per-app version transitions)
```

**Where platform-specific behavior lives.** In `adapters/<os>/` — Python managers that shell out to native scripts (`.ps1` on Windows, `.sh` on macOS/Ubuntu). The contract (schema + 5 phases + interfaces) is shared; the implementations are not. This is the right boundary.

---

## 3. Architecture and Module Overview

```
core/ascendo/
  interfaces/        IAdapter + 6 sub-interfaces (abc.ABC)         ← Layer 4 contract
  models/            Pydantic v2: sidecar, package, result, run, host, legacy
  adapter_factory/   detect_os() + AdapterRegistry (entry_points + direct-import fallback)
  orchestrator/      runner (5-phase), run_async (RunRegistry/SSE), sidecar_io (atomic+locked), report, run_logger
  dashboard/         FastAPI app, routes/, inventory_db.py, middleware/edition_gate
  ai/                AI-tools chat (drivers, persistence, actions whitelist)
  cli/               Typer CLI
adapters/{windows,ubuntu,macos}/   Python managers + native scripts + lib/
app/                 LEGACY Ubuntu-only backend+frontend (app/frontend SPA is current; app/backend superseded by core/ascendo/dashboard)
ui/desktop-tauri/    current Tauri 2.x shell ; app/tauri/ is legacy 1.x
plugins/             dell-driver-update (+ _template)
```

**Strengths (verified).**
- **Adapter factory** (`adapter_factory/__init__.py`) — entry-point discovery with direct-import fallback handles editable installs; explicit error types; test-injectable registry. *(A9)*
- **Interface layering** — core depends only on `interfaces/`; an import-linter contract is referenced. *(A10)*
- **Thin orchestrator** — `runner.run_phases` drives the contract, catches `ManagerError`, synthesizes failed sidecars, and contains no OS logic. *(A11)*
- **Frozen models + validators** — `Sidecar`/`ToolInfo` are `frozen=True`; summary/items consistency + reverse-time validators enforce logical integrity at parse. *(I11)*

**Coherence problems (verified).**
- **A5 / `core → adapters` coupling.** Despite ADR-0005's "core never imports adapters," `core/ascendo/dashboard/routes/web_config.py` imports `ascendo_macos.web_registry` and `routes/service.py` imports `ascendo_windows.managers.service` (lazy, try/except-guarded). The generic web-config editor is effectively hardwired to the macOS adapter. *(confirmed first-hand)*
- **A1 type-contract drift.** Ubuntu/macOS override `inventory() -> IInventory | None  # type: ignore[override]` while the interface declares non-optional. Verify pass confirms the impls *never actually return None* — so it's a **lying annotation**, not a runtime crash, but it forces defensive None-checks and masks intent.
- **A2/A3 lifecycle inconsistency.** Windows constructs a **new** `WindowsElevation()` / inventory / snapshot on *every* accessor call; macOS/Ubuntu cache singletons. Real consequence: a sudo password registered via one `elevation()` call is **invisible** to a manager that fetched a different instance on Windows. The interface doesn't document singleton-vs-fresh semantics.
- **A4 Linux distro routing gap (high).** Fedora/Arch are detected, lack entry-point mappings, fall back to the Ubuntu adapter (apt/snap/brew/npm/pip/flatpak only), and the inventory script enumerates none of dnf/pacman → **silent empty inventory**.
- **A6 plugins_loader is dead code.** `core/ascendo/plugins_loader/` exists but nothing imports it; the orchestrator has no plugin dispatch hook. Either wire it or remove it; document the intended plugin model.
- **A7 path-resolution divergence.** Ubuntu resolves top-level `scripts/`/`lib/`; Windows/macOS resolve adapter-local dirs. The legacy top-level migration debt (`app/`, `lib/`, `scripts/`) compounds this.

---

## 4. End-to-End Process Review (Discovery → Cleanup)

### 4.1 Discovery & Inventory Build
**How it works.** OS enumerators produce minimal `Package` objects (Windows ARP/winget/msstore; macOS `system_profiler`/brew/mas + `/Applications` Info.plist fingerprinting; Ubuntu dpkg/snap/apt/flatpak/brew/npm/pip). Web discovery fingerprints bundles by `SUFeedURL`→sparkle, `KSProductID`→keystone, `Squirrel.framework`→squirrel, else builtin, and excludes brew/mas/softwareupdate-owned + MAS-receipt bundles.

**Weaknesses.**
- **W10 (verified) — discovery failure reads as "all current."** `check.sh` sources `web_discovery.sh … 2>/dev/null`; if discovery yields no lines, check emits `0 outdated, 0 up-to-date` and exits 0. `discovery.sh` is engineered to always exit 0 (errors suppressed), so a real internal failure is indistinguishable from "no apps." **No assertion that discovery produced ≥1 line.**
- **W3 (design) — post-ship apps silently classified `builtin`.** A newly-installed app absent from the registry and lacking fingerprints becomes `builtin`/manual with no advisory.
- **A4 (verified) — Fedora/Arch enumerate nothing** (see §3).
- **P10 (verified gap) — no caching on `/health/check`; inventory cache is 60s** but health re-runs `shutil.which()` for 12+ tools per call; tab-switch storms hammer scans.

### 4.2 Diffing & Change Detection
**How it works.** Within a single run, `_flush_run_to_inventory_db` merges sidecars by **phase priority** (`verify > apply > check > plan > cleanup`); `spa_real._latest_check_overlay` overlays post-apply results onto the freshest check, **filtered to the same run** (Sesja-66 fix). This is **deterministic within a run** and correctly reflects post-apply truth — verified good (I12).

**REFUTED claims (checked false — do not action):**
- **D1/D2 "cross-run oscillation."** The overlay iterates payloads oldest→newest and overwrites status to `up_to_date` for success/triggered items, and same-run filtering holds — stale `triggered` does **not** persist. *(refuted)*
- **I6 "_normalize_item_id collapses any id containing the name."** False — it only collapses ids that *end with* `separator+name`. `firefox-bin`/`Package-x86` are preserved. *(refuted)*

**Real weaknesses (verified).**
- **D3 — `_normalize_item_id` over-collapses a narrower real case.** `id="Microsoft.VCRedist.2008.x64.Runtime"`, `name="Runtime"` ends with `.Runtime` → collapses to `item_id=""`, defeating the very multi-arch separation Sesja-67 added. The heuristic should require id == exactly `prefix+sep+name` *or* a known synthetic-prefix allowlist (`brew:`,`apt:`,`snap:`,…), not "ends-with."
- **D8 — `delete_row` orphans legacy `item_id=''` rows.** Eviction keys on the *computed* item_id; a pre-Sesja-67 row stored with `item_id=''` won't match a new real item_id. A wildcard `delete_row(cat, name, item_id='*')` is needed.
- **E8 — missing `phase` field → priority 0 → silent overwrite.** `_phase_of` returns `""` for a corrupt/legacy sidecar; `_PHASE_PRIORITY.get("",0)` makes its items losable with no warning.
- **D7 — only `''` collapses to `None`;** literal `"Unknown"`/`"N/A"` strings survive into the UI. Normalize a blank-version allowlist.

### 4.3 Update Planning
Apps are classified **Tier-A** (real candidate probe + install: sparkle/github_dmg/release_feed/omaha/msupdate/docker) vs **Tier-B** (trigger vendor agent: keystone/squirrel/builtin). The registry (`web_apps.toml`, Pydantic-validated) merges a shipped baseline with a user override by `bundle_id`/slug. This is a sound model.

- **W9 (verified) — Windows & macOS web registries have diverged** (`ascendo-web-apps/v2` vs `ascendo-web-apps-windows/v1`, separate schemas, separate `web_registry.py`). Maintenance/doc burden; no shared base schema.

### 4.4 Update Execution
**How it works.** `start_run_async` registers a `RunState`, spawns a worker via `asyncio.to_thread`, streams SSE. `conflicting_apply` refuses two applies on the same category; read-only phases may overlap.

**Weaknesses.**
- **`_INVENTORY_STATUS_MAP` honesty gap (verified first-hand, run_async.py:241).** `failed→outdated`, `triggered→up_to_date`, `partial→outdated`. A failed apply is painted "outdated" (indistinguishable from "never tried"); a merely-*kicked* Tier-B app is painted "up_to_date" though nothing was verified. This is the inventory face of the "web not updating" complaint.
- **`os.environ[ASCENDO_STREAM_LOG]` global mutation race (verified first-hand, run_async.py:543).** The worker mutates a process-global env var with save/restore, but `conflicting_apply` *allows concurrent read-only runs*. Two overlapping check runs race the save/restore → subprocesses tee to the wrong stream log or none.
- **E11 (verified) — cancelled runs are marked `COMPLETED`** and still run the inventory flush; SSE clients can't distinguish user-stop from natural completion; partial state is committed as if whole. Add a `CANCELLED` status and skip flush on cancel.
- **W1 (verified) — `SAFE_MODE` now forced for *all* profiles** (was safe/quick only). `full` no longer launches GUI updaters; Tier-B/builtin return exit 95 → action-required. Intentional, but it inverts the documented "full = GUI" contract; rename/document.
- **E5 (verified, reframed) — dead `except OSError`.** `runner.py` catches `OSError` around `write_sidecar`, but `write_sidecar` raises `SidecarWriteError`/`SidecarLockError` (RuntimeError subclasses) — so the clause **never fires** and those exceptions propagate uncaught (killing the run). Either the handling is wrong or the intent (fatal) should be made explicit by catching the right type.
- **P11 (verified) — Windows UAC silently ignores `env`.** `_run_uac(env=…)` accepts but never applies it; the elevated child inherits parent env. A caller passing proxy/locale overrides loses them silently. Prefer fail-fast (`raise NotImplementedError`) or honor it.

**REFUTED:** **E2** — `stop_on_failure` correctly `break`s the phase loop when all *selected* managers fail. **P2** — UAC redirection paths *are* quoted.

### 4.5 Post-Update Verification & Cleanup
**How it works.** Verify re-reads installed versions, sleeps 10s (keystone) / 30s (squirrel) for async agents, and backfills `update_history.to_version`.

**Weaknesses (the core of the "web not updating" pain).**
- **W4 + W13 (verified, high) — `triggered_pending` is terminal-but-invisible.** If the vendor daemon (ksadmin/Squirrel) crashes or never reconciles, verify reports `triggered_pending` **forever**; there is no timeout, retry, daemon health-check, or escalation. The operator believes "it'll update on relaunch"; it never does. This is *the* web-app failure mode.
- **W2 (verified, high) — release_feed JSON regex silently degrades.** When `version_regex` no-matches, `_rf_apply_regex` returns the raw value and the handler still exits 0 — a vendor format change yields a wrong/garbage version with no `probe_broken` signal. *(The **text** path is safe — W5 refuted.)*
- **E7 — partial-sidecar recovery discards parsed items** (always stub `failed`/`items=[]`), so a crash after 5/100 items reports "0 items," and the flush may then evict those 5 apps.
- **E14 — if all apply sidecars are corrupt, no `REPORT.md` is generated and nothing is logged** (the guard checks `Phase.APPLY in phases`, not "any apply sidecar loaded").

---

## 5. Inventory Model & State Management

The DB (`inventory_db.py`) is solid in shape — per-call connections, WAL, `executemany` batch upsert, schema-v2 PK `(category, name, item_id)`, frozen source models, and the descriptive fd-leak fix (Sesja-49) is real and good. Remaining issues:

| ID | Severity | Issue | Status |
|----|----------|-------|--------|
| **I2 / D4** | high | **Upsert-only flush orphans rows** for apps uninstalled between runs (only apply-phase `skipped`+no-version is evicted; a zero-item category run leaves stale rows forever). | verified |
| **I9** | high (reframed) | **`bulk_upsert` never calls `set_meta`** → after a partial post-run flush, `last_scan_at` stays old → `is_fresh()` serves an *incomplete* DB as "fresh full scan." Distinguish *adapter-scan* freshness from *any-write* freshness. | verified |
| **I5** | medium | **Non-atomic flush:** `bulk_upsert` commits, then per-row `delete_row` loop in separate transactions → readers see transient "(not installed)" rows. Batch deletes into one statement. | verified |
| **D8** | medium | `delete_row` exact-PK match orphans legacy `item_id=''` rows. | verified |
| **D11** | high | `delete_row` failures are logged-and-swallowed; eviction count is not returned/surfaced → uninstalled apps reappear with no operator awareness. | verified |
| **I8 / migration** | low | v1→v2 migration `DROP TABLE`s data with **no `PRAGMA user_version`** anchor — future migrations have no version marker; rename-to-archive before drop. | verified first-hand |
| **I3** | medium | No test pins legacy `warn→skipped` mapping (lossy, undocumented in tests). | verified |
| **I7** | low | `Item.name` has no min-length; empty names are silently fixed-up from `id` at flush, hiding malformation. | plausible |
| **I10** | low | Post-run flush overwrites `vendor` with `None` (sidecars rarely carry vendor) → vendor strings vanish until next live-scan. | plausible |

**Architectural recommendation: make "raw scan" and "canonical inventory" first-class and separate.** Today the DB conflates full live-scans with partial post-run flushes, and freshness is "last write" not "last full scan." Introduce: (1) a per-category `scan_complete` watermark updated only by full live-scans; (2) a periodic **reconciliation** job that diffs DB vs. a fresh scan and removes rows not seen (closing I2/D4/D11 generically instead of the reactive apply-skipped special case); (3) `PRAGMA user_version` for migrations.

---

## 6. Install / Uninstall / New App Detection

- **New apps:** detected via live-scan enumeration + web fingerprinting — works; weakness is W3 (post-ship apps silently `builtin`) and W10 (silent discovery failure).
- **Uninstalls:** detected only via the **apply-phase `skipped`+no-version** heuristic, then evicted via `delete_row` — narrow and reactive (I2/D4/D8/D11). A check-only run, or a category that scans zero items, never triggers eviction.
- **Cross-source identity:** the same app can appear under `web`, `winget`, and `registry_arp` (Windows) — there's name-cache dedup in `AscendoWebDiscovery`, but no single canonical identity across categories. This is UX confusion, not corruption.
- **Recommendation:** replace heuristic eviction with the reconciliation pass (§5). For detection robustness, make `discovery.sh` emit an explicit `discovery_failed` signal and have `check.sh` distinguish "0 apps" from "discovery crashed."

---

## 7. Web Apps and Non-Standard Updaters

This is the stated pain point, and the review confirms it is **structural** — the model is mostly right, but the *honesty* and *reconciliation* are missing.

**Why web apps "don't update":**
1. **Tier-B is fire-and-forget.** Keystone/Squirrel/builtin emit `triggered`; verify waits 10–30s once; if the vendor agent doesn't reconcile in that window it's `triggered_pending` **forever** (W4/W13). The inventory then maps `triggered→up_to_date` and the operator sees green.
2. **No "version" notion for many web apps.** Some apps update silently on launch and expose no machine-readable version; modeling them as "outdated/up_to_date" is category-incorrect.
3. **Silent probe degradation.** A vendor rotating their feed format breaks the regex (W2) or the whole discovery (W10) with no `probe_broken` surface.

**Recommended modeling (conceptual).** Give web items an explicit **control class** instead of forcing them into the version-diff vocabulary:
- `auto_silent` — vendor self-updates; Ascendo only *reports presence*, never claims outdated. (Brave/Chrome/Edge/most Squirrel apps.)
- `controllable` — Tier-A: real probe + install; full success/failure semantics.
- `manual_check` — Tier-B with **bounded reconciliation**: verify escalates `triggered_pending → action_required` after a timeout (e.g. 120s in-run, or "still pending after N days" cross-run), with a one-click "Open to check for updates" action.

The Sesja-79 **action-required guarantee** (every non-silent app surfaced in `## ⚠ Action required` + `GET /runs/{id}/action-required` + SPA card) is the right backbone — it just needs (a) the `triggered_pending` timeout→escalation to feed it, and (b) the inventory to stop painting kicked apps as `up_to_date`.

**Other verified web findings:** W1 (SAFE_MODE all-profiles semantics), W2 (JSON regex silent degrade), W9 (registry divergence), W11 (`sort -V` portability — implement version compare in Python), W8 (parallelism only env-tunable). **Refuted:** W5 (text path is guarded).

---

## 8. Inclusion/Exclusion Rules and Inventory Scope

- **Ownership filters** (`_owned_by`: brew/mas/softwareupdate, `_MASReceipt`, ineligible patterns, system-bundle classification) are reasonable; W6 notes MAS filtering relies on `_MASReceipt` fallback when `ASCENDO_WEB_MAS_BUNDLE_IDS` is empty (the default).
- **User exclusion** (`excluded.json` via `POST /apps/exclude`) is default-include and straightforward; **D10** notes it's re-read from disk on *every* request (add a small mtime-keyed TTL cache).
- **Determinism:** within a run, reconciliation is deterministic (fixed phase priority). Across runs it's largely fine (D1/D2 oscillation refuted), but the orphan/eviction gaps (§5/§6) mean the *scope* can drift. No "ignored vs hidden vs temporary" distinction exists — worth adding for UX clarity.
- **Recommendation:** express scope as an explicit, ordered rule set (own-by-manager → ineligible patterns → user-exclude → include) and make the reconciliation pass the single authority for "what's in scope right now."

---

## 9. Cross-Platform & Production Readiness

**Security (most urgent).**
- **P1 (verified, high) — `ISource.verify_signature` unimplemented in all three adapters.** T2 (compromised source) / T3 (MITM) are unmitigated at the core layer. *(Tier-A web installers do `spctl`/Authenticode/quarantine-strip in handlers, so it's not zero — but the abstraction the threat model relies on is empty.)* Implement at least apt-GPG for Ubuntu and wire `verify_signature` into apply; document Windows/macOS deferral.
- **P5 (verified, medium→high) — CORS `["*"]` + no auth + unconstrained `--host`.** Default to loopback origins, refuse/ warn on non-loopback bind, and add an Origin/CSRF guard or a localhost capability token. A browser visiting a malicious page can `POST /elevation/auth` / `POST /runs/async` against `127.0.0.1:8765` (DNS-rebind/confused-deputy) for a tool that performs privileged installs.
- **P8 (verified) — ChatsDB `chmod 0o600` is POSIX-only;** Windows leaves default ACLs (chat history may contain secrets). Set a Windows ACL or warn.
- **P3/P6 (medium) — password lifetime + symlink resolution.** Wrap `register_password` callsites in try/finally; resolve elevated argv[0] via `shutil.which` and compare resolved path, not just basename.
- **AI actions audit (gap)** — `ALLOWED_ACTIONS` rejections are silent (422) with no log of proposed/rejected actions or LLM reasoning; add an audit trail (adversarial-prompt detection).

**Resilience.**
- **P12 (verified) — stale sidecar lock deadlock:** a crashed lock-holder leaves a `.lock` that blocks future runs (~6.5s then `SidecarLockError`); add stale-lock detection (mtime/PID) + document `rm` recovery in `doctor`.
- **Tauri shell (gaps):** no backend health/respawn loop, `spawn_backend().ok()` swallows bootstrap errors, window-close `kill()` doesn't wait for graceful shutdown (sidecar/WAL mid-write). *(Assumption: the gaps cite `app/tauri/` — the legacy 1.x shell; the current `ui/desktop-tauri/src-tauri/src/main.rs` follows the same spawn→poll→kill shape, so they likely apply there too. Verify against the shipping shell.)*
- **Keepalive subshell pipe-hang class** (Ubuntu Sesja-68) is fixed but fragile — any future custom EXIT trap that doesn't kill the keepalive can re-hang; expose a `register_phase_exit_handler` helper.

**Performance.** P7/P10 — no metrics/histograms on scans, health, lock contention; no `/runs/stats`; health check uncached. Add caching + a stats endpoint + structured (JSON) logs.

**Cross-platform abstraction.** Adding a new OS is *mostly* clean (factory + interfaces) **except** A4 (no Fedora/Arch managers), A5 (core hardwired to specific adapter packages), and W9 (divergent web schemas). Fix A5 by promoting `web_registry`/`service` to optional `IAdapter` methods.

---

## 10. Testing, Validation, and CI/CD

**Current state.** 132 Python test files (fast, mock-based contract + adapter smoke), 4 bats suites, two live harnesses (`validate-ubuntu.sh`, `validate-windows.ps1`) + a macOS one. Strengths: comprehensive sidecar-schema validation (T13), well-isolated contract tests that run in seconds (T14).

**Critical gaps (verified).**
| ID | Severity | Gap |
|----|----------|-----|
| **T8 / T10** | high | **CI runs only `validate-configs` + `check-readme` on `ubuntu-24.04`.** Pytest, bats, and the `validate-*` harnesses are **not** in CI; no Windows/macOS matrix; no build/sign/release. A PowerShell typo or macOS-only break passes CI. |
| **T2** | high | **PowerShell scripts have zero execution tests** (shellcheck runs on `.sh` only; no Pester; the `.py` "tests" explicitly *don't* spawn pwsh). Registry mutations / winget / elevation are unvalidated pre-merge. |
| **T1** | high | **Zero real-LLM AI-chat tests** — only fake CLI fixtures + mocked `stream()`. Stream parsing, cancel, large streams, mid-stream HTTP errors untested. |
| **T3** | high | **Subprocess timeout + crash-salvage untested** — and a control-flow note: `TimeoutExpired` is re-raised as `ManagerError` *before* the salvage path, so timeouts never reach salvage. |
| **T6** | medium | No **privilege-escalation round-trip** test (askpass→`sudo -A`); only component isolation. |
| **T4** | medium | **`apply_report grouping` flake** committed without `xfail`/skip — fails every CI run, masking real regressions. Fix or mark. |
| **T9** | medium | Windows `service_endpoints` flake undocumented + unmarked. |
| **T7** | medium | `_normalize_item_id` heuristic has **no unit tests** (and is false-positive-prone — D3). |
| **T11** | medium | Cooperative-stop has no end-to-end cancel test. |
| **T12** | low | i18n parity is a CI lint step, not a pytest test. |
| **T5** | — | *Partially refuted:* sparkle/omaha **do** have Python isolation tests; release_feed has a bash test but it's **orphaned from pytest discovery**. Windows handlers untested. |

**Minimal suite before 1.0:** (1) wire existing pytest + bats + `validate-*` into CI on a **3-OS matrix**; (2) add Pester (or pwsh-subprocess) tests for Windows handlers; (3) integration tests for timeout→salvage and cancel→clean-stop; (4) parametrized `_normalize_item_id` edge cases; (5) a test pinning the legacy schema literal distinct from canonical (I1) and `warn→skipped` (I3); (6) `xfail`-mark or fix the two documented flakes.

---

## 11. Prioritized Improvement Plan (Roadmap)

Effort: **S** ≤0.5d · **M** ~1–3d · **L** >3d. Each item lists the finding IDs it closes.

### Phase 0 — Correctness, data integrity, security blockers (pre-1.0)
| Item | Rationale / Impact | Effort | Deps |
|------|--------------------|--------|------|
| **Hard-error (or build) Fedora/Arch** (A4) | Silent empty inventory on whole distro families. Add explicit "unsupported distro" error + `doctor` degraded status; or stand up dnf/pacman adapters. | M (error) / L (adapters) | — |
| **Honest inventory status** — stop `failed→outdated`, `triggered→up_to_date` (`_INVENTORY_STATUS_MAP`) | Failed applies & un-reconciled web apps look green; directly drives the "web not updating" complaint. Add `failed`/`triggered_pending` to the inventory vocabulary + SPA pills. | M | §7 |
| **`triggered_pending` timeout → action_required** (W4/W13) | Dead vendor daemon = app outdated forever, invisibly. Verify escalates after timeout; feed the existing action-required guarantee. | M | honest-status |
| **CI: run the tests that exist + 3-OS matrix** (T8/T10/T2) | The biggest safety gap — none of the suites gate merges. | M (Linux pytest+harness) / L (win+mac matrix + Pester) | — |
| **Wire `ISource.verify_signature` (apt GPG first) + apply-time check** (P1) | T2/T3 mitigation is currently absent at the core. | M (apt) | — |
| **CORS lockdown + bind-host guard (+ localhost token)** (P5) | Network-exposed privileged endpoints if `--host 0.0.0.0`. | S–M | — |
| **Fix stream-log env race for concurrent read-only runs** | Pass stream-log path through call args/contextvar instead of mutating `os.environ`. | S | — |
| **Add legacy-literal-distinctness + warn→skipped tests** (I1/I3) | Prevents recurrence of the Sesja-82 rebrand outage. | S | — |

### Phase 1 — Inventory model & diff correctness
| Item | IDs | Effort |
|------|-----|--------|
| Reconciliation pass (DB vs. fresh scan) replacing reactive eviction | I2, D4, D8, D11 | M |
| Split scan-freshness from write-freshness; `set_meta` semantics; `PRAGMA user_version` | I9, I8 | M |
| Batch flush deletes into one transaction | I5 | S |
| Refine `_normalize_item_id` (exact-prefix/allowlist) + tests | D3, T7 | S |
| Standardize adapter sub-interface caching + fix `inventory()` annotation | A1, A2, A3 | S–M |
| Blank-version allowlist; missing-phase warning; empty-name validation | D7, E8, I7 | S |

### Phase 2 — Update engine robustness & web apps
| Item | IDs | Effort |
|------|-----|--------|
| Web control-class model (`auto_silent`/`controllable`/`manual_check`) | §7, W1 | M |
| Fail-loud on probe degradation (regex no-match, discovery zero-results) | W2, W10 | S |
| `CANCELLED` run status; skip flush on cancel; persist partial sidecars | E11 | M |
| Fix dead `except OSError` / classify sidecar write errors | E5 | S |
| Recover items from truncated sidecars; warn when no apply sidecar loaded | E7, E14 | S |
| Honor/await graceful shutdown signals; report flush failures | E12, E1 | S |

### Phase 3 — Cross-platform polish, UX, security hardening
| Item | IDs | Effort |
|------|-----|--------|
| Expose `web_registry`/`service` via `IAdapter` (kill core→adapter imports) | A5 | M |
| Unify web registry schema (platform sub-tables) | A9/W9 | L |
| Windows ChatsDB ACL; UAC env fail-fast; elevation password/symlink hardening | P8, P11, P3, P6 | M |
| Stale sidecar-lock recovery + `doctor` hint | P12 | S |
| Decide plugins_loader: wire or remove | A6 | M |
| `sort -V` → Python version compare | W11 | S |

### Phase 4 — Observability, telemetry, scaling
| Item | IDs | Effort |
|------|-----|--------|
| Metrics + `/runs/stats` + structured JSON logs + lock-contention histograms | P7 | M |
| Cache health-check; document inventory cache busting | P10 | S |
| `run.log` rotation | P9 | S |
| AI-actions audit trail + rejection metrics | gap | S |
| Tauri backend health/respawn + graceful shutdown + surfaced bootstrap errors | gaps | M |
| Dev-sync secret cleanup on interruption | gap | S |

---

## 12. Suggested Multi-Agent / Workflow Decomposition

This review *was* produced this way (a map → adversarially-verify → completeness-critic workflow). The same decomposition drives implementation:

| Agent | Role | Input | Output | Hand-off |
|-------|------|-------|--------|----------|
| **A — Codebase Mapper** | Build/refresh the module→responsibility map; confirm layering | repo + ADRs | module map, dependency-rule violations | feeds B–F scope |
| **B — Inventory & State Analyst** | Own `inventory_db`, flush, reconciliation, schema migration | A's map + §5 | patches for I2/I5/I8/I9/D8/D11 + tests | → G |
| **C — Update-Engine Analyst** | Own runner/run_async/sidecar_io/report; phases, cancel, errors | §4 | patches for E5/E8/E11/E14 + honest status | → G |
| **D — Web & Edge-Case Analyst** | Own handlers/registry/discovery; control-class model | §7 | W1/W2/W4/W10/W13 fixes + escalation | → C (status) |
| **E — Cross-Platform & Prod Reviewer** | Security, elevation, CORS, resilience, perf, observability | §9 | P1/P5/P8/P11/P12 fixes | → G |
| **F — Test & CI Strategist** | CI matrix, Pester, integration tests, flake triage | §10 | `.github/workflows` + new tests | gates B–E |
| **G — Planner / Roadmap Assembler** | Sequence, dedup, verify each change preserves behavior | B–F outputs | merged, ordered PR series | ships |

Each implementation agent must **write/extend tests first** (per the repo's own TDD discipline), keep changes minimal/behavior-preserving, and verify before claiming done. F's CI matrix gates all of them.

---

## 13. IMPLEMENTATION MEGA-PROMPT FOR SMALLER MODELS

> Copy everything in this fenced block into a cheaper code-agent. It assumes the agent has the same repo checked out and access to this report (`ASCENDO_ULTRA_REVIEW.md`).

```
You are implementing the production-hardening plan for the "Ascendo" cross-platform
updates app. You have the repo checked out at the project root, and the ultra-review
report at ASCENDO_ULTRA_REVIEW.md. READ §11 (the phased roadmap) and the finding IDs
it references; the per-finding evidence (file:line, what the code does, the
recommendation) lives in §3–§10. Do NOT re-read HANDOFF.md in full — it is ~250KB and
will exhaust your context; grep it only for a specific session note if needed.

GROUND RULES (non-negotiable):
1. Respect the existing architecture. Do NOT restructure layers, rename the sidecar
   schema, or change the 5-phase contract unless a finding explicitly says to.
   The legacy schema literal "ubuntu-aktualizacje/v1" MUST stay distinct from the
   canonical "ascendo/v1" — collapsing them caused a production outage (see I1).
2. Preserve behavior while refactoring. Every change must keep existing tests green.
3. TEST-FIRST. Before each fix: write or extend a failing test that pins the bug,
   then make it pass. Add the test under tests/contract/ (mock-based, fast) or the
   relevant adapters/<os>/tests/. Run `python -m pytest tests/ adapters/*/tests -q`
   after each change.
4. Some review findings were REFUTED during verification (I6, E2, P2, W5, D1, D2).
   §4–§8 mark these — do NOT "fix" them.
5. Document non-trivial changes in code comments and, for behavior changes, in
   CHANGELOG.md + a one-line PLAN.md note. Update the matching ADR if you change a
   contract (e.g. ISource, status vocabulary).
6. Work in small, reviewable commits — one finding (or one tightly-related cluster)
   per commit, with the finding ID in the message.

EXECUTION ORDER: do Phase 0 first (correctness/security/CI blockers), then Phase 1,
2, 3, 4 in order. Within a phase, do items top-to-bottom. STOP and report after each
phase for review before starting the next.

WORK IN PASSES (treat each as a focused sub-agent / role; do them sequentially to keep
context small — open only the files a pass needs):

  PASS F0 — CI FIRST (so every later change is gated):
    Files: .github/workflows/validate.yml, bin/validate-ubuntu.sh.
    - Add a job that runs `python -m pytest tests/ adapters/*/tests -q` on ubuntu-24.04.
    - Add a job that runs bats tests/bash/*.bats.
    - Add windows-latest + macos-latest matrix jobs that run the native validate
      harness (validate-windows.ps1 / validate-macos.sh) with expensive real
      package-manager steps skipped via an env flag.
    - xfail-mark the two documented flakes (apply_report grouping T4, service_endpoints
      T9) so CI is green-on-known-state.
    Verify: the workflow yaml parses and the Linux job runs the suite.

  PASS B — INVENTORY/STATE (B agent role):
    Files: core/ascendo/dashboard/inventory_db.py, core/ascendo/orchestrator/run_async.py,
           core/ascendo/dashboard/routes/spa_real.py, tests/contract/test_inventory_db*.py.
    - I9: have the full-scan path call set_meta; do NOT advance last_scan_at on partial
      post-run flushes. Add a per-category scan-complete watermark; is_fresh() must key
      on full-scan freshness.
    - I2/D4/D8/D11: add a reconciliation routine (diff DB vs a fresh live-scan; remove
      rows not seen) and call it on full refresh; make delete failures count + surface
      (return evicted count; log ERROR if >threshold). Support delete-all-item_ids for
      a (category,name).
    - I5: batch the uninstalled deletions into one transaction.
    - I8: add `PRAGMA user_version`; rename old table to *_v1_archive before drop.
    - D3/T7: refine _normalize_item_id to collapse only exact `prefix+sep+name` OR a
      known synthetic-prefix allowlist; add parametrized tests including
      id="Microsoft.VCRedist.2008.x64.Runtime"/name="Runtime" (must NOT collapse).

  PASS C — UPDATE ENGINE (C agent role):
    Files: core/ascendo/orchestrator/{runner.py,run_async.py,sidecar_io.py,report.py,
           run_logger.py}.
    - Honest status: extend the inventory status vocabulary so failed apply ≠ outdated
      and triggered ≠ up_to_date; add a `triggered_pending` inventory state. Update the
      SPA status pills. (Coordinate the exact strings with PASS D.)
    - Stream-log race: stop mutating os.environ in the worker; pass the stream-log path
      via the run context / call args (or a contextvar) so concurrent read-only runs
      don't clobber each other.
    - E11: add RunStatus.CANCELLED; set it when should_cancel fired; skip the inventory
      flush on cancel; persist partial sidecars.
    - E5: catch SidecarWriteError/SidecarLockError (not OSError) around write_sidecar,
      or make fatal explicit.
    - E8: when phase is missing, log a warning and do not silently treat as priority 0.
    - E7/E14: recover parsed items from truncated sidecars; log + skip-report when no
      apply sidecar loaded.

  PASS D — WEB APPS (D agent role):
    Files: adapters/macos/lib/handlers/*.sh, adapters/macos/scripts/web/*.sh,
           adapters/macos/lib/{web_discovery.sh,ascendo_web.sh},
           adapters/macos/ascendo_macos/web_registry.py (+ Windows equivalents).
    - W4/W13: in verify, after the wait window, escalate triggered_pending →
      action_required when the daemon hasn't reconciled (in-run timeout). Cross-run,
      flag items pending for >N days. Feed the existing action-required report.
    - W2: when version_regex is configured but does not match, return a probe-broken
      exit code (do NOT silently fall back to raw) — mirror the text-path guard.
    - W10: make discovery emit an explicit failure signal; check.sh must distinguish
      "0 apps" from "discovery crashed" (assert ≥1 emitted line or a discovery_ok flag).
    - W1: document SAFE_MODE-all-profiles, or add a profile override; rename if helpful.
    - W11: replace `sort -V` version comparison with the python3 already invoked.
    Add bats isolation tests for sparkle/omaha/release_feed handlers (mock curl).

  PASS E — CROSS-PLATFORM/SECURITY (E agent role):
    Files: core/ascendo/interfaces/source.py, adapters/*/managers/elevation.py,
           core/ascendo/dashboard/app.py, core/ascendo/ai/persistence.py,
           core/ascendo/orchestrator/sidecar_io.py.
    - P1: implement ISource for Ubuntu (apt GPG key verification); wire verify_signature
      into apply-phase item processing; return None elsewhere but document the deferral
      in ADR-0005.
    - P5: default CORS to loopback origins; if bind host != 127.0.0.1 and CORS=="*",
      refuse to start (or require an explicit --allow-remote flag) and log a warning;
      consider a localhost capability token on mutating endpoints.
    - P8: set a Windows ACL on chats.db (ctypes) or warn if world-readable.
    - P11: in Windows _run_uac, raise NotImplementedError if env is non-None (fail-fast).
    - P3/P6: wrap register_password callsites in try/finally; resolve elevated argv[0]
      via shutil.which and compare resolved paths.
    - P12: detect stale sidecar locks (mtime/PID) and document `rm` recovery in doctor.

  PASS A — ARCHITECTURE (do AFTER B–E so it doesn't churn them):
    - A5: add optional IAdapter.web_registry()/service_manager() (or a capability +
      interface) and route dashboard web_config/service through it instead of importing
      ascendo_macos/ascendo_windows directly.
    - A1/A2/A3: standardize sub-interface caching (singletons, matching macOS/Ubuntu);
      fix the inventory() return annotation; remove the type: ignore.
    - A4: add an explicit unsupported-distro error path (and a doctor 'distro_supported'
      component) until dnf/pacman adapters exist.
    - A6: decide plugins_loader — wire into the orchestrator phase loop or remove it;
      record the decision in an ADR.

SKILLS REQUIRED PER PASS:
  - F0: CI/GitHub-Actions authoring, pytest/bats invocation.
  - B: SQLite + Pydantic + careful migration; writing parametrized contract tests.
  - C: Python concurrency/asyncio + exception-flow reasoning; behavior-preserving refactor.
  - D: bash + curl + feed parsing; bats test authoring; PowerShell parity.
  - E: security (signatures, ACLs, CORS), subprocess/elevation, ctypes on Windows.
  - A: interface design + dependency-rule reasoning; import-linter.

CONTEXT-BUDGET DISCIPLINE:
  - Never load HANDOFF.md whole. Open only the files a pass lists; grep for symbols.
  - One pass = one context window. Commit, run the suite, then start the next pass fresh.
  - If a change touches >3 files, split it.

DEFINITION OF DONE per pass:
  - New/updated tests fail before the fix and pass after.
  - `python -m pytest tests/ adapters/*/tests -q` is green (modulo xfail-marked flakes).
  - Behavior preserved (no existing test newly fails).
  - CHANGELOG.md updated; ADR updated if a contract changed.
  - Commit message names the finding IDs closed.
Report a short summary after each PASS and STOP at each phase boundary for review.
```

---

*End of report. Findings are tagged with IDs (A=architecture, I=inventory, E=engine, W=web, P=prod, D=diff, T=tests) for cross-reference; "verified" = confirmed against code by an adversarial pass, "refuted" = checked and found false, "first-hand" = read directly by the reviewer.*
