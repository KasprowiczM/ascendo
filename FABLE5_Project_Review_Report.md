# PROJECT_REVIEW_REPORT.md — Ascendo

> Whole-repo engagement review. Read-only — no files were modified. Reviewed at commit `9a49677` (main), 2026-06-09.
> **Verification limitation:** this review environment ships Python 3.10; the repo requires ≥3.11, so the pytest suites could not be executed here. Claims below are backed by static inspection, grep evidence, and the repo's own CI state (validate.yml reported 6/6 green on `main`, run `26724790115`). Items I could not execute are flagged as assumptions.

---

### 1. Project overview

**What it is.** Ascendo is a cross-platform "update everything" orchestrator for individual machines: one product surfaced three ways — a Typer CLI (`python -m ascendo`), a FastAPI web dashboard with a vanilla-JS SPA, and a Tauri 2.x desktop shell that embeds the dashboard as a Python sidecar. It drives per-OS package managers (winget/msstore/Windows Update; apt/snap/brew/npm/pip/flatpak; brew/mas/softwareupdate) through a strict 5-phase contract (check → plan → apply → verify → cleanup), each phase emitting a JSON-Schema-validated sidecar.

**Who it serves.** Power users and operators who want one trustworthy place to see and apply all pending updates on Windows, Ubuntu/Debian, and macOS — with consent gates around anything destructive.

**Detected stack.**

| Layer | Tech |
|---|---|
| Core | Python ≥3.11, Pydantic v2, FastAPI/Starlette, Typer, SQLite (WAL) |
| Adapters | Python wrappers + native PowerShell / bash phase scripts per OS |
| Frontend | Hand-rolled vanilla JS/CSS SPA (`app/frontend/`, ~20k lines, no bundler), SSE for live run streaming |
| Desktop | Tauri 2.x (Rust shell) + Python sidecar |
| AI | Multi-provider assistant (Anthropic/OpenAI/OpenRouter/Google/Ollama/LM Studio + CLI drivers), whitelisted action dispatch |
| Contracts | JSON Schema (`schemas/phase-result.schema.json`, run/v1) |
| CI/CD | GitHub Actions: `validate.yml` (6-job config + cross-OS test matrix), `frontend-smoke.yml` (Playwright/Chromium), `release.yml` (tag-driven) |
| Packaging | MSI/NSIS, PyInstaller, .deb, Homebrew tap, winget manifest, macOS .pkg |
| Tooling | ruff (strict ruleset), mypy `--strict` on core, pre-commit, pytest (+asyncio, +xdist) |

**Main user journeys.** First-run onboarding wizard → "check" run → review per-category status pills → consent-gated "apply" (Administrator/sudo/Touch-ID elevation) → live Run Center (SSE log + phase stepper) → post-run report + action-required cards (web-app in-app updaters, duplicate-source resolution) → history/insights → optional schedules (Task Scheduler / systemd / launchd) → AI assistant for diagnosis and suggested actions.

**Maturity.** Late-beta. Heading to `v1.0-beta` per `PLAN.md`; the three remaining platform punch lists live in `PROMPT_MACOS.md` / `PROMPT_WINDOWS.md` / `PROMPT_UBUNTU.md`. Two prior internal audits exist (`ASCENDO_ULTRA_REVIEW*.md`); most of their P0s are verifiably fixed on `main` (dedup fail-safe in `deduplicator.py:_confirm_uninstall`, consent surface in `routes/dedup.py` + `dedup.js`, honest status pills in `components.js:STATUS()` incl. `triggered_pending`).

---

### 2. Architecture & module map

Six-layer architecture (per ADR-0005), enforced reasonably well: interfaces → models → orchestrator → dashboard/CLI, with adapters resolved at runtime via `adapter_factory` so core never imports an adapter package (the `set_active_adapter()` indirection in `web_config.py` exists precisely for this).

- **`core/ascendo/interfaces/`** — ABCs: `IAdapter`, `IPackageManager`, `IScheduler`, `ISnapshot`, `IElevation`, `IInventory`, `IWebRegistry`. Clean seam; tests inject fakes here.
- **`core/ascendo/models/`** — Pydantic models for sidecars, runs, packages, dedup; `legacy.py` holds the frozen legacy-schema literal (post-incident, with a do-not-rename warning — good).
- **`core/ascendo/orchestrator/`** — `runner.py` (phase loop, fail-isolated per manager), `run_async.py` (thread-per-run + bounded `RunRegistry`, cooperative cancel, lifecycle sub-states), `sidecar_io.py` (atomic write + hardened read), `deduplicator.py`, `report.py`.
- **`core/ascendo/dashboard/`** — `app.py` factory (CORS allowlist, loopback-refusal, `LanGuardMiddleware` capability token, `EditionGateMiddleware`), 18 routers, `inventory_db.py` (SQLite source of truth for Apps/Categories).
- **`core/ascendo/ai/`** — provider drivers, context resolvers (doctor, sidecars, history), `actions.py` fence-parser + `ALLOWED_ACTIONS` whitelist (the LLM→action security boundary), `persistence.py` (ChatsDB, `chmod 0o600`).
- **`core/ascendo/cli/`** — single 1,562-line `__init__.py` Typer app.
- **`adapters/{windows,ubuntu,macos}/`** — Python manager classes + native phase scripts + per-adapter test suites (substantial: 450+/140+/415+ tests per the session logs).
- **`app/frontend/`** — the served SPA; **`app/backend/`** — the *legacy Linux-only* FastAPI dashboard (1,438-line `main.py`), still present alongside the new core dashboard.
- **`ui/desktop-tauri/`** — Tauri shell; **`plugins/`** — third-party phase scripts; **`schemas/`** — the contract.

**Architectural smells.**

- **Two dashboards coexist.** `app/backend/` duplicates inventory, suggestions, auth, sudo, telemetry, backup logic that core has re-implemented (`spa_real.py:59` even says "ported from app/backend/inventory.py"). Drift between the two is a standing correctness hazard, and `app/` weighs 35 MB (includes a checked-in-place `.venv`).
- **Legacy Linux top-level** (`lib/`, `scripts/`, `update-all.sh`, `setup.sh`) still pending migration into `adapters/ubuntu/` — three places where "how Ubuntu updates work" can disagree.
- **God modules:** `cli/__init__.py` (1,562 lines), `app/frontend/app.js` (7,701 lines), `spa_real.py` (1,262), `app/backend/main.py` (1,438).
- **Frontend layering by accretion:** four CSS layers (`style.css` + `ui-redesign.css` + `components.css` + `colors_and_type.css`) and three JS UI layers (`shell.js`, `ui-components.js`, `components.js`) that win "by source order" — explicitly documented, but fragile.
- **Module-global mutable state:** `web_config._ACTIVE_ADAPTER` (set/cleared in lifespan). Correct today; will break the day two apps share a process concurrently.
- **Side-effectful factory:** `create_app()` mutates process `PATH` (`_augment_path_for_macos_gui`) and `os.environ` (stream-log var in workers). Documented, but makes test ordering and multi-app hosting delicate.
- **Repo hygiene:** binary artifacts committed/present at root (`ruvector.db` 1.6 MB, `agentdb.rvf`, `.DS_Store`, `.playwright-mcp/` with 188 entries, `core/ascendo/orchestrator/__test_write.txt`), `HANDOFF.md` at 556 KB.

---

### 3. Feature inventory

| Feature | User value | Key locations | State |
|---|---|---|---|
| 5-phase orchestrated runs | Safe, auditable updates | `orchestrator/runner.py`, `run_async.py`, `schemas/` | ✅ Mature, contract-tested |
| Per-OS managers (winget, msstore, WU, apt, snap, brew, mas, softwareupdate, npm, pip, flatpak, Dell/NVIDIA drivers) | One tool for everything | `adapters/*/ascendo_*/managers/`, `adapters/*/scripts/` | ✅ Tier-1 on all 3 OSes |
| Web dashboard SPA (Overview, Apps, Categories, Run Center, History, Insights, Schedule, Library, Assistant, Settings, Help) | Non-CLI operation | `app/frontend/`, `dashboard/routes/` | ✅ Recently redesigned (Phases 1–4) |
| SSE live run streaming | Real-time apply visibility | `stream_log.py`, `routes/runs.py:stream_run_events`, `run-store.js` | ✅ |
| Elevation (UAC / sudo askpass / Touch-ID-first) | Frictionless consent | `routes/elevation.py`, adapters' elevation managers, `ascendo_json.sh:_ascendo_sudo_warm` | ✅ Hard-won (Sesja 81) |
| Cross-source dedup w/ consent | No duplicate installs, no silent uninstalls | `orchestrator/deduplicator.py`, `routes/dedup.py`, `dedup.js` | ✅ Fail-safe + consent card |
| Scheduler (Task Scheduler/systemd/launchd) | Unattended hygiene | `routes/scheduler_real.py`, adapters' schedulers | ✅ |
| Snapshots / rollback | Safety net | `snapshot/`, adapters | ✅ Windows/macOS; verify Ubuntu parity |
| AI assistant + whitelisted actions | Diagnose failures, propose runs | `ai/`, `routes/ai.py`, `routes/chat.py` | ✅ Differentiator |
| Inventory DB (Apps/Categories parity) | Consistent app tracking | `dashboard/inventory_db.py` | ✅ |
| Web-app registry (in-app updaters) | Covers non-PM apps | `web_registry.py` per adapter, `routes/web_config.py` | ✅ macOS-centric; thinner elsewhere |
| Onboarding wizard, editions (basic/dev), i18n | First-run UX, gated power tools | `routes/onboarding.py`, `middleware/edition_gate.py`, `i18n/` | ✅ |
| Plugins | Extensibility (e.g. dell-driver-update) | `plugins/`, `plugins_loader/` | 🟡 Works; no marketplace/signing |

**Dead / transitional / experimental:**
- 43 endpoints in `spa_stubs.py` still answer `{ok:true, stub:true}` (e.g. `POST /apps/add`, `POST /backup/import`, `GET /updates/check`) — the SPA can render success for actions that did nothing.
- `app/backend/` — superseded legacy dashboard; effectively dead but still importable/runnable (`python -m app.backend`).
- `core/ascendo/elevation/__init__.py` — flagged orphaned in Sesja 81, still present.
- Layout-editor assets — deliberately removed from the served whitelist but files remain.
- Core ships 7 CLI locales (`de,en,es,fr,it,pl,pt`); the SPA ships only `en,pl` — partially-finished i18n surface.

---

### 4. Code quality, bugs & correctness

#### Critical / likely bugs

1. **AI provider API keys persisted in plaintext with default permissions.**
   `routes/ai.py:_write_config()` writes `~/.config/ascendo/ai.json` (containing `api_key`) via `Path.write_text` + `replace` with **no `chmod 0o600`** — verified by grep (no `chmod`/`keyring` in the file), while `ai/persistence.py` *does* chmod its ChatsDB. On multi-user machines the key is world-readable subject to umask. *Verify:* `POST /ai/config` with a key, then `stat -c %a ~/.config/ascendo/ai.json`. *Fix:* chmod 0600 on the tmp file before `replace()`; better, integrate OS keychain (see §6).

2. **Path traversal in dedup endpoints via unvalidated `run_id`.**
   `routes/dedup.py:_resolve_source_run()` does `runs_dir / run_id` with a raw string (`run_id: str | None`), unlike `routes/runs.py` where `run_id: UUID` types the path. `?run_id=../../<dir>` resolves outside `runs_dir`; impact is limited (it only reads `check__*.json` and seeds an apply from that dir) and the dashboard is loopback-only, but `POST /dedup/apply` is a *mutating* endpoint and should not accept attacker-shaped paths. *Verify:* `GET /dedup/pending?run_id=../..` and observe directory probing. *Fix:* parse to `UUID` (reject 422) exactly like `runs.py`.

3. **Stub endpoints report success for no-op mutations.**
   `spa_stubs.py` (43 routes) returns `{ok:true, stub:true}` for `POST /apps/add`, `/apps/remove`, `/backup/import`, etc. If any SPA path still calls these, the user sees success while nothing persisted. This is the same failure *class* as the Sesja-84 "honest statuses" finding, one layer up. *Verify:* grep `app.js` for each stubbed path; click-test each in the SPA. *Fix:* return HTTP 501 + a typed `{implemented:false}` shape and render an explicit "not available yet" toast; delete stubs aggressively.

4. **Frontend resolution breaks under wheel/installed deployment.**
   `app.py:_resolve_frontend_dir()` walks `parents[4]` from `core/ascendo/dashboard/app.py` to find `<repo>/app/frontend` — valid only for editable/git installs. `core/ascendo/frontend_static/` exists but holds only `.gitkeep`; `core/pyproject.toml` packages `ascendo` without the SPA. A `pip install ascendo` (or PyInstaller layout drift) silently serves API-only. *Verify:* `pip install core/` into a clean venv outside the repo, run dashboard, GET `/` → no SPA. *Fix:* build step copying `app/frontend/` into `frontend_static/` at package time; fall back to `importlib.resources`.

5. **Process-global env mutation for stream logs is still a shared-resource race.**
   `run_async.py` documents a per-run `stream_log_path` save/restore, but the mechanism remains "worker sets `os.environ` for subprocess inheritance." Two concurrent runs in distinct categories (allowed — conflicts are checked per `(category, apply)`) can interleave environ writes; a child of run A can inherit run B's log path. Low frequency, confusing symptom (cross-wired live logs). *Verify:* start two concurrent async runs with distinct categories, assert each `STREAM.log` contains only its own categories' lines. *Fix:* pass the env explicitly in the `subprocess.run(..., env=...)` call chain instead of mutating `os.environ`.

6. **Four documented flaky tests are normalized.**
   PLAN.md treats "4 pre-existing flakes" (Sesja-43 apply_report grouping, Sesja-79 cooperative stop, 2× environmental `test_cli_web`) as acceptable baseline. Documented-but-red tests rot the suite's signal and mask regressions in exactly the areas (cancellation, report grouping) most likely to regress. *Fix:* quarantine with `@pytest.mark.flaky`/skip + tracking issue, or fix; CI should be 100%-green-or-skipped, never "green except known reds."

7. **Assumption (could not execute):** the reduced-mode CI (`--quick` / `-SkipExpensive` / `--skip-dashboard --skip-scheduler --skip-web`) means **no CI coverage of real apply, schedulers, or elevation flows** — the repo's own PROMPT files acknowledge this. Until full-harness runs are at least periodically automated (self-hosted or nightly real-hardware), "CI green" overstates assurance for the most dangerous code paths.

#### Design & maintainability issues

- **`app/frontend/app.js` (7,701 lines, single IIFE-era file, no modules/bundler/types).** The single largest risk to velocity. 13 `innerHTML` sites coexist with a stated `textContent`-only i18n discipline — each new `innerHTML` is a latent XSS/regression site (see §6).
- **`cli/__init__.py` (1,562 lines)** mixes argument parsing, rendering, and orchestration; split into `cli/run.py`, `cli/runs.py`, `cli/schedule.py`, etc.
- **Duplicated logic across eras:** version comparator (core ↔ `app/backend/inventory.py`), suggestions (`routes/suggestions.py` ↔ `app/backend/suggestions.py`), sudo/auth (core elevation ↔ `app/backend/sudo.py`/`auth.py`).
- **95 broad `except Exception` sites in core** — many are justified ("teardown must never raise", "corrupt sidecar must not 500") and individually annotated, which is good practice; but the count warrants a sweep to ensure each logs with context and none swallows the *first* error of a cascade (the Sesja-82 incident was exactly a silent `except Exception` in the async worker).
- **Comment-driven ordering contracts:** router mount order in `app.py` ("MUST be BEFORE spa_stubs") and CSS "wins by source order" are enforced only by comments. Add a startup assertion (no duplicate route paths resolving to stubs when a real router registered the same path) and a route-collision test.
- **`BaseHTTPMiddleware` for LanGuard/EditionGate**: Starlette's `BaseHTTPMiddleware` has known interactions with streaming responses (SSE) and background tasks. The SSE endpoints appear to work today (loopback skips LanGuard logic but the middleware still wraps the stream). Prefer pure-ASGI middleware for both.

#### Testing & reliability

- **What exists (strong):** 151 test files. `tests/contract/` (~55 files: dashboard, SSE, run lifecycle, sidecar IO, schema literals, LAN safety, status pills, i18n parity, stream-log race), `tests/cross-cut/`, `tests/integration/`, `tests/bash/` (bats for the shell lib + phase contract), `tests/e2e/` (Playwright SPA smoke in CI), and per-adapter suites (`adapters/*/tests`, now gated in CI). Regression tests are written for past incidents (e.g. `os.setsid` guards, legacy-literal tests) — excellent practice.
- **Gaps:**
  - **Apply-path realism:** CI never executes a real package apply, scheduler install, or elevation prompt on any OS (reduced mode). The riskiest 20% of the product has only manual validation harnesses (`bin/validate-*.{ps1,sh}`).
  - **Frontend unit tests:** zero. 20k lines of JS guarded only by one Playwright smoke. `STATUS()` mapping, `run-store.js` SSE reduction, and i18n key resolution are pure functions begging for cheap node-based tests.
  - **Native script coverage:** PowerShell phase scripts have Python-side smoke tests but no Pester suite; bash has bats for the lib but per-source `check.sh/apply.sh` coverage is thin.
  - **Coverage measurement:** pytest-cov is a dev dep but no coverage threshold is enforced in CI.
  - **Property/fuzz testing** of `sidecar_io.read_sidecar` (the component whose failure mode once killed every dashboard run) would be high-leverage: feed it mutated/truncated/key-dropped JSON and assert `SidecarReadError`, never anything else.
- **Concrete additions:** (a) nightly full-harness workflow on self-hosted runners per OS (`bin/validate-*.{ps1,sh}` without skip flags), apply against a sacrificial pinned package; (b) `tests/contract/test_route_collisions.py` asserting no stub shadows a real route; (c) `vitest` (or plain node:test) for `components.js`/`run-store.js`/`i18n.js`; (d) hypothesis-based sidecar fuzz; (e) coverage floor (e.g. 80% core) in `validate.yml`.

---

### 5. Performance & scalability

Posture is appropriate for a single-host local tool; nothing here blocks v1.0-beta. Specific items:

1. **Sequential manager execution within a phase** (`runner.py` iterates managers serially). A full check across 8+ sources is dominated by network-bound subprocess waits; parallelizing the *check* phase (read-only by contract) with a small thread pool could cut "Quick check" wall time substantially (the 203 s full-profile run in Sesja 82 is the baseline to beat). Apply should remain serial (lock semantics, exit code 11). *Verify:* time `run --phase check` full profile before/after.
2. **SSE via directory polling + log tailing.** Fine at this scale; document the poll interval and ensure backoff when no run is active so an idle dashboard tab doesn't spin. *Verify:* CPU sampling of the uvicorn process with an idle SPA open for 10 min.
3. **SPA payload:** ~20k lines across 14 unminified JS/CSS files, every load revalidating via `no-cache`. Locally this is fine; for the Tauri bundle, pre-compress or at least concatenate to cut startup I/O. Larger win: the redesign already removed a runtime DOM-reorg layer (`ui-redesign.js`) — continue consolidating layers (§7).
4. **`InventoryDB` per-call SQLite connections** with WAL: correct and simple; connection churn is irrelevant at this QPS. No N+1 risk visible. Keep.
5. **`RunRegistry` bounded LRU + never-evict-running:** correct. One caveat — `Path.iterdir()+stat` sorting in `_latest_run_with_check` and history listings is O(total runs) per request; with years of runs (thousands of dirs) the History tab will crawl. Add run-dir retention/pruning (config: keep N runs / M days) and an index (the inventory DB could track runs too). *Verify:* synth 5,000 run dirs, time `GET /runs`.
6. **`HANDOFF.md` (556 KB) and `PLAN.md` (92 KB)** are also a *context* performance problem for the AI-assisted workflow this repo runs on — they exceed what sessions can ingest; per its own rule ("trim at ~60%"), they're overdue for archival splitting (`docs/superpowers/specs/handoff-archive/`).

---

### 6. Security & data protection

**Model overview (good baseline):** loopback-only bind by default with a hard `RuntimeError` refusal on non-loopback bind unless `--allow-remote`/env opt-in; when remote, a per-process capability token (`X-Ascendo-Token`) gates mutating methods via `LanGuardMiddleware`; CORS pinned to a loopback/tauri allowlist, never `*`, `allow_credentials=False`; edition gate 404s dev-only surfaces; elevation passwords held in memory only; destructive dedup requires explicit consent recomputed server-side ("never trusts client-supplied uninstall ids"); AI actions pass through an `ALLOWED_ACTIONS` whitelist with `extra="forbid"` Pydantic bodies; no first-party `shell=True` (grep-verified — only vendored deps). SPA asset serving defends against traversal twice.

**Notable risks (ordered):**

1. **Plaintext AI keys, no file-mode hardening** — `routes/ai.py` (§4.1). Remediate: chmod 0600 now; then an `ISecretStore` interface with DPAPI (Windows), Keychain (macOS), SecretService/keyring (Linux) backends and JSON fallback. The redaction on `GET /ai/config` (`sk-***`) is already right.
2. **Dedup `run_id` traversal** — `routes/dedup.py` (§4.2). UUID-validate.
3. **Local-malware threat model is unaddressed (accepted risk — document it).** Any process on the machine can POST to 127.0.0.1:8765: trigger applies, attempt `/elevation/auth` brute force (no rate limit / lockout), read inventory. For a local tool this is a defensible boundary (local code execution is already game-over), but: (a) add basic rate limiting + constant-time compare on `/elevation/auth`; (b) consider an origin-bound session token issued to the served SPA so arbitrary local processes can't drive mutating endpoints — the LanGuard machinery already exists, extend it to loopback for mutating routes behind a config flag; (c) state the threat model in `docs/agents/security.md`.
4. **`POST /web/probe-entry` executes bash handlers with client-supplied config.** It writes the request's `cfg.json` into a temp dir and runs `_web_probe_parallel` (bash). The handler code is repo-shipped, but config *values* flow into shell-adjacent code (curl args, etc.). Pydantic validation of `WebApp` helps; audit `ascendo_web.sh` handlers for unquoted expansion of cfg fields, and add a bats test injecting `"; touch /tmp/pwned"`-style values.
5. **DOM XSS budget in the SPA:** 13 `innerHTML` sites in `app.js`. Most render trusted templates, but sidecar `messages[]`, package names, and AI chat output are *external* data (a malicious package name in a repo is attacker-controlled). Sweep every `innerHTML` reaching run/inventory/AI data; convert to `textContent`/element building (the codebase already prefers this — finish the job). AI chat rendering deserves special attention: model output → action chips must never be `innerHTML`'d.
6. **Prompt injection → action proposals:** sidecar/context resolvers feed real system output to the LLM, which can emit `ascendo-action` fences. The whitelist + explicit click is the right design; ensure the *rendered chip label* can't misrepresent the validated payload (display the canonical action from the dispatcher's echo, not the LLM's text).
7. **Secrets in logs:** elevation route docs promise the password is never logged — add a regression test asserting no handler logs request bodies on the auth path, and a logging filter that redacts `password`/`api_key` keys globally.
8. **Compliance:** all data is local (runs, inventory, chats). Chat history may contain pasted secrets — ChatsDB is 0600 (good); mention in docs + add a "clear all AI data" button. No telemetry is collected (legacy `app/backend/telemetry.py` notwithstanding) — keep it that way or make it opt-in with a visible toggle.

---

### 7. UX/UI, accessibility, SEO & i18n

**UX/UI.** The Sesja 73–86 redesign (AppShell 5-destination nav, answer-first Overview with VerdictHeader/AttentionList, KPI strips, run stepper, consent cards) is a coherent, modern direction with a real design system (`Ascendo_Design_System/`, self-hosted Inter Tight + JetBrains Mono). Concrete issues:

- **Silent no-op surfaces** (stubbed POSTs, §4.3) are the top UX-trust issue.
- **Action-required discoverability:** dedup + web-updater cards depend on polling (`dedup.js` polls `/dedup/pending`); ensure a single consolidated "Needs attention" inbox rather than per-feature cards accreting.
- **Error UX in the Run Center:** the Sesja-82 class of failure (worker dies, zero sidecars) should render an explicit "run crashed before producing results" state, not an empty run. There is now a `terminal_state="failed"`; verify the SPA renders it distinctly (PROMPT_MACOS §2 suggests this only recently landed).
- **Layer consolidation:** merge `ui-redesign.css` into `components.css`/`style.css` now that the redesign is stable; every "wins by source order" layer is a future regression.

**Accessibility.** No evidence of an a11y pass: hand-rolled nav, custom dropdown-free controls (`ui-components.js`), status conveyed by colored dots/pills. Minimum bar: (a) `aria-live="polite"` on the Run Center log/status; (b) status pills get text or `aria-label`, never color-only (also add a colorblind-safe palette check — red/green/amber dots are the primary signal); (c) keyboard reachability of the 5-destination nav + bottom mobile nav + consent cards; (d) focus management when the run stepper advances; (e) `prefers-reduced-motion`. Add a Playwright + axe-core step to `frontend-smoke.yml`.

**SEO.** Not applicable (local app). The `website/` dir is nearly empty — when a marketing site ships, standard meta/OG/sitemap apply there, not here.

**i18n/GEO.** Core CLI: 7 locales with a dependency-free loader and parity tests (`test_i18n_parity.py`) — strong. SPA: only `en`/`pl` (`i18n.en.js`/`i18n.pl.js`, ~1,170/1,664 entries). Recommendations: (1) generate SPA catalogs from the same source as core's JSON to stop the two surfaces drifting; (2) decide v1 scope honestly — ship en+pl and hide other languages in the picker rather than half-translating; (3) dates/numbers via `Intl.*` keyed to locale, not hand-formatting; (4) the i18n-through-`textContent` discipline is excellent — keep the lint script that enforces it in CI.

---

### 8. DevEx, tooling & workflows

**What's good:** monorepo layout matches the architecture docs; ADRs exist and are referenced from code comments; ruff (broad ruleset incl. flake8-bandit-adjacent picks) + mypy `--strict` on core with the pydantic plugin; pre-commit configured; per-OS install/validate scripts (`bin/install-dev.ps1`, `bin/validate-*.{sh,ps1}`); three CI workflows incl. a real-browser SPA smoke; an unusually rigorous session-handoff discipline.

**Friction & fixes:**

- **Root clutter / discoverability:** ~25 markdown files at root, four overlapping review/plan documents, plus dev-sync scripts (×9 pairs), legacy `update-all.sh`/`setup.sh`/`install.sh`. Move reviews to `docs/reviews/`, dev-sync to `bin/dev-sync/`, archive `HANDOFF.md` by quarter. New contributors currently cannot tell live from legacy.
- **Binary/garbage files tracked or lingering:** `ruvector.db` (root, adapter, frontend copies), `agentdb.rvf(.lock)`, `.playwright-mcp/`, `app/.venv` (35 MB on disk), `core/ascendo/orchestrator/__test_write.txt`, `.DS_Store`. Audit `.gitignore` vs `git ls-files` and purge.
- **CI gaps:** no ruff/mypy job visible in `validate.yml` excerpts (pre-commit exists but CI should run `ruff check` + `mypy core/` explicitly); no coverage gate; no nightly full-harness (the single biggest assurance gap, §4.7); `release.yml` exists — add artifact signing + SBOM (pip-audit/`cargo audit` for the Tauri shell) to it.
- **Python floor mismatch:** repo requires ≥3.11; document this loudly in CONTRIBUTING (this very review's environment tripped on it).
- **CLAUDE.md path drift:** it hardcodes `D:/Dev_Env/Ascendo` (Windows) while sessions also run from `/Users/mk/Dev_Env/Ascendo` — parameterize as "the repo root on this machine."
- **Docs:** USER_GUIDE/QUICKSTARTs are thorough. Missing: a single ARCHITECTURE.md entry-point diagram (the six layers + request flow), and an honest "supported matrix" page (v1 = Ubuntu/Debian only on Linux — per the operator decision, make sure README says so).

---

### 9. Roadmap & implementation plan

#### Phase 1 — Ship v1.0-beta: close the punch list (1–2 weeks)
*Objectives: execute the repo's own MUST-DOs, eliminate dishonest UI, lock the contract.*

| # | Task | Size | Files | Verify |
|---|---|---|---|---|
| 1.1 | Run full real-hardware harnesses on all 3 OSes (no skip flags), per `PROMPT_{MACOS,WINDOWS,UBUNTU}.md` | M | `bin/validate-*.{sh,ps1}` | Harness exit 0; real apply observed; dashboards + schedulers live |
| 1.2 | Gate Windows dedup executor behind opt-in/per-run marker (PROMPT_WINDOWS §1) | S | `adapters/windows/scripts/{winget,pip,npm}/apply.ps1` | New smoke test: stray `DEDUPLICATION_TASKS.json` without marker → no uninstall |
| 1.3 | Convert all mutating spa_stubs to 501 + SPA "not available" toast; delete dead stubs | S | `routes/spa_stubs.py`, `app/frontend/app.js` | Route-collision test; click-test each surface |
| 1.4 | UUID-validate `run_id` in dedup routes | S | `routes/dedup.py` | New contract test: `?run_id=../..` → 422 |
| 1.5 | chmod 0600 AI config | S | `routes/ai.py:_write_config` | New test asserts mode |
| 1.6 | Fix-or-quarantine the 4 documented flakes | M | `tests/contract/test_apply_report.py`, cooperative-stop, `test_cli_web` | CI fully green with zero "known reds" |
| 1.7 | Verify failed/crashed runs render distinctly in the SPA | S | `app/frontend/{components.js,app.js}` | Playwright: simulate worker crash → "failed" pill visible |
| 1.8 | README/docs state Linux = Ubuntu/Debian only | S | `README.md`, `LINUX_QUICKSTART.md` | Doc review |

#### Phase 2 — Legacy excision & packaging integrity (2–3 weeks)
*Objectives: one implementation per behavior; installable artifact that actually contains the UI.*

| # | Task | Size | Files | Verify |
|---|---|---|---|---|
| 2.1 | Delete `app/backend/` (port any last unique logic: backup, telemetry decisions documented) | L | `app/backend/**`, `app/install.sh` | grep: no imports remain; harnesses green |
| 2.2 | Migrate `lib/`, `scripts/`, `update-all.sh`, `setup.sh` into `adapters/ubuntu/` | L | top-level legacy → `adapters/ubuntu/` | bats + Ubuntu adapter tests green; `bin/validate-ubuntu.sh` full |
| 2.3 | Package SPA into the wheel (`frontend_static/` build step) + `importlib.resources` fallback | M | `core/pyproject.toml`, `dashboard/app.py:_resolve_frontend_dir`, `release.yml` | Clean-venv install outside repo serves SPA |
| 2.4 | Repo hygiene purge (binary DBs, `.venv`, scratch artifacts) + `.gitignore` audit; archive HANDOFF by quarter | S | root, `.gitignore`, `docs/` | `git ls-files \| grep -E '\.(db|rvf)$'` empty |
| 2.5 | Split `cli/__init__.py` into subcommand modules | M | `core/ascendo/cli/` | CLI contract tests unchanged-green |
| 2.6 | Run-dir retention policy (keep N/M days) + `runs prune` command | M | `orchestrator/`, `cli/`, settings route | Synthetic 5k-dir benchmark; History tab timing |

#### Phase 3 — Hardening & reliability (2–3 weeks)
*Objectives: close §6 items; make CI assurance honest.*

| # | Task | Size | Files | Verify |
|---|---|---|---|---|
| 3.1 | `ISecretStore` (DPAPI/Keychain/keyring) for AI keys | M | `core/ascendo/ai/`, `routes/ai.py`, adapters | Keys absent from disk JSON; round-trip tests per OS |
| 3.2 | Rate-limit + constant-time compare on `/elevation/auth`; global log redaction filter | S | `routes/elevation.py`, `logging/` | Test: 10 bad attempts → 429; log capture shows no secrets |
| 3.3 | `innerHTML` sweep on external-data paths (run messages, package names, AI output) | M | `app/frontend/app.js`, `components.js` | Playwright test with `<img onerror>` package name fixture |
| 3.4 | Shell-injection audit of `ascendo_web.sh` handlers vs `probe-entry` cfg values | S | `adapters/macos/lib/`, `routes/web_config.py` | bats injection test |
| 3.5 | Nightly full-harness workflow (self-hosted/macOS+Windows+Ubuntu runners) | L | `.github/workflows/nightly.yml`, `bin/` | Nightly badge; first real-apply caught-regression counts as success |
| 3.6 | Sidecar fuzz tests (hypothesis) + env-passing fix for stream logs (§4.5) | M | `orchestrator/sidecar_io.py`, `run_async.py`, tests | Fuzz suite green; concurrent-run log isolation test |
| 3.7 | Pure-ASGI rewrite of LanGuard/EditionGate; coverage floor + ruff/mypy jobs in CI | M | `dashboard/middleware/`, `validate.yml` | SSE soak test; CI enforces ≥80% core coverage |

#### Phase 4 — Frontend modernization & a11y (3–4 weeks)
*Objectives: pay down the 20k-line UI so features stay cheap.*

Tasks: introduce a build step (Vite, output still vanilla-compatible) and split `app.js` by tab into ES modules (L); merge the 4 CSS layers into tokens + components + app (M); vitest unit tests for `STATUS()`, `run-store`, i18n loader (S); axe-core in `frontend-smoke.yml` + aria-live/keyboard/focus fixes (M); generate SPA i18n from core catalogs (S); consolidated "Needs attention" inbox (M). Verify: Playwright smoke + axe green; bundle served from `frontend_static/`; no behavior diffs in click-through matrix.

#### Phase 5 — Million-dollar product expansion (ongoing)
*Objectives: convert a polished local tool into a monetizable platform.*

1. **Security intelligence (highest willingness-to-pay):** match installed inventory against OSV/NVD feeds → "3 of your pending updates fix known CVEs" verdicts, exportable compliance report (PDF). Builds directly on `inventory_db` + `report.py`. (L)
2. **Fleet/multi-host (the dev-edition `hosts` stubs already point here):** an opt-in agent mode posting signed run summaries to a self-hosted hub; MSP/IT-team dashboard. This is the natural Pro/Team SKU. (XL)
3. **Unattended "maintenance windows":** schedule + snapshot + apply + auto-rollback on verify-failure, with morning summary notification (native notifications via Tauri). The 5-phase contract and snapshots make this uniquely credible. (L)
4. **Plugin marketplace with manifest signing** (`schemas/` already has a plugin manifest): community phase scripts, vetted registry. (M)
5. **Signed auto-update of Ascendo itself** through `release.yml` artifacts + Tauri updater — a product about updates must update itself flawlessly. (M)
6. **AI assistant deepening:** post-run "explain what changed and why it matters" digests; failure-diagnosis with one-click whitelisted remediation — already 80% of the plumbing exists in `ai/`. (M)

---

### 10. Prompts for less-capable models to implement this plan

> Usage: paste one template per session, fill the `{{…}}` slots. Each is self-contained.

#### 10.1 Bug-fixing prompt template

```text
You are a careful engineer working in the Ascendo monorepo (Python core in core/ascendo,
per-OS adapters in adapters/{windows,ubuntu,macos}, vanilla-JS SPA in app/frontend,
FastAPI dashboard in core/ascendo/dashboard). Work directly on `main`. NEVER create a
git worktree. Python ≥3.11 is required.

TASK: Fix exactly one bug, nothing else.
BUG: {{e.g. "routes/dedup.py:_resolve_source_run joins an unvalidated run_id string
into runs_dir, allowing ../ traversal. routes/runs.py shows the correct pattern
(run_id: UUID)."}}
FILES YOU MAY TOUCH: {{e.g. core/ascendo/dashboard/routes/dedup.py,
tests/contract/test_dedup_endpoints.py}} — touching ANY other file is a failure.

PROCESS (mandatory order):
1. Open and read every file listed above end-to-end before editing.
2. Write a FAILING test first in the listed test file that reproduces the bug
   (e.g. GET /dedup/pending?run_id=../.. must return 422). Run it, confirm it fails:
   python3 -m pytest tests/contract/test_dedup_endpoints.py -q
3. Make the minimal code change that fixes the bug. Preserve all existing behavior:
   valid inputs must produce byte-identical responses to before.
4. Run the focused tests, then the broader gate:
   python3 -m pytest tests/contract/ -q   (must be as green as before your change)
5. Output a unified diff of your changes and a 3-line summary: root cause, fix, proof.

RULES: no new dependencies; no refactoring "while you're here"; no API shape changes;
match the file's existing style (ruff-formatted, type-annotated, docstrings with the
repo's tone). If the fix requires touching an unlisted file, STOP and report why
instead of editing it.
```

#### 10.2 Refactoring prompt template

```text
You are refactoring inside the Ascendo monorepo. Refactor = identical behavior,
better structure. Work on `main`, no worktrees. Python ≥3.11.

TASK: {{e.g. "Split core/ascendo/cli/__init__.py (1,562 lines, one Typer app) into
cli/_app.py (the Typer roots), cli/run_cmd.py, cli/runs_cmd.py, cli/snapshot_cmd.py,
cli/schedule_cmd.py, cli/dashboard_cmd.py, keeping cli/__init__.py as a thin
re-export so `python -m ascendo` and every existing import path keep working."}}

SAFETY HARNESS (do this BEFORE refactoring):
1. Identify the tests that pin current behavior: {{e.g. tests/contract/test_cli_*.py}}.
   Run them and record the exact pass/fail baseline:
   python3 -m pytest {{test paths}} -q | tail -5
2. If behavior you're moving is untested, ADD characterization tests first that
   capture today's behavior (including odd behavior — do not "fix" anything).

REFACTORING RULES:
- Pure moves and extractions only. No renamed public symbols, no signature changes,
  no logic edits, no dependency changes, no comment deletions (move them with code).
- All existing import paths must keep working (re-export from the old location).
- After EVERY individual move, re-run the harness; never batch more than one
  extraction between test runs.
- Finish with: python3 -m pytest tests/contract/ tests/cross-cut/ -q  AND
  ruff check {{touched paths}} — both must match or beat the baseline.

DELIVERABLE: unified diff + a file-by-file table (old location → new location) +
the before/after test output proving identical results.
```

#### 10.3 Feature implementation prompt template

```text
You are implementing one roadmap feature in Ascendo. Architecture you must respect:
six layers (interfaces → models → orchestrator → dashboard/CLI); core NEVER imports
an adapter package (adapters are resolved via core/ascendo/adapter_factory and
accessor methods like adapter.web_registry() — see ADR-0005 in docs/); every
phase result is a JSON sidecar validated against schemas/phase-result.schema.json;
the dashboard is loopback-only FastAPI (core/ascendo/dashboard) serving the vanilla-JS
SPA in app/frontend; destructive actions ALWAYS require explicit user consent
(see routes/dedup.py for the canonical consent-surface pattern).

FEATURE: {{e.g. "Run-directory retention: a `runs prune` CLI command and a
settings-backed automatic policy (keep_last_n, keep_days) that deletes old run dirs
under ~/.ascendo/runs, never touching the active run."}}
CONTEXT FILES TO READ FIRST: {{e.g. core/ascendo/orchestrator/run_async.py (RunRegistry),
core/ascendo/cli/__init__.py (runs_app subcommands), core/ascendo/dashboard/routes/runs.py,
tests/contract/test_run_async_lifecycle.py}}
OUT OF SCOPE: {{e.g. "no UI changes; no changes to sidecar formats; no Windows
service integration"}}

EXPECTATIONS:
1. Design first: write a 10-line plan (data flow, files touched, failure modes,
   what happens mid-run) and check it against the architecture rules above.
2. Test-first: contract tests in tests/contract/ covering happy path + the dangerous
   edges ({{e.g. "active run is never pruned", "missing dir is a no-op",
   "permission error logs a warning and continues"}}).
3. Implementation: full type annotations (mypy --strict passes on core/), Pydantic
   models with extra="forbid" for any new request bodies, i18n keys (not literals)
   for any user-facing strings, structured logging via the module _log.
4. Docs: one paragraph in the relevant README/USER_GUIDE section.
5. Prove it: python3 -m pytest tests/contract/ -q (green) + a manual verification
   transcript ({{e.g. "created 5 fake run dirs, ran `ascendo runs prune --keep 2`,
   show ls before/after"}}).

RULES: no new third-party dependencies without stating why; no touching
adapters/ unless the feature spec says so; destructive operations default to
dry-run with an explicit --yes / consent flag.
```

#### 10.4 Testing & hardening prompt template

```text
You are hardening one module of Ascendo: adding tests, tightening types/validation,
and improving error handling — WITHOUT changing observable behavior for valid inputs.
Work on `main`, no worktrees. Python ≥3.11.

TARGET MODULE: {{e.g. core/ascendo/orchestrator/sidecar_io.py — the sidecar
reader/writer. History: a missing-key KeyError once escaped read_sidecar and silently
killed every dashboard run (see PLAN.md, Sesja 82). The invariant to enforce: ANY
malformed sidecar input must raise SidecarReadError and nothing else.}}
EXISTING TESTS: {{e.g. tests/contract/test_sidecar_io.py, test_sidecar_v1.py}} — run
them first and record the baseline: python3 -m pytest {{paths}} -q

DO, in order:
1. COVERAGE MAP: list every public function in the module and which existing test
   exercises it. Identify untested branches (especially except clauses).
2. ADD TESTS for each gap. Prioritize hostile inputs: truncated JSON, wrong types,
   missing required keys, huge payloads, non-UTF8 bytes, concurrent writes to the
   same path. Each test asserts the SPECIFIC exception type and that no other
   exception class can escape.
3. TIGHTEN ERROR HANDLING: any bare `except Exception` in the module must either
   (a) re-raise as the module's typed error, or (b) log with full context
   (_log.exception) and have a comment justifying why swallowing is safe. Never
   silently `pass`.
4. TIGHTEN TYPES/VALIDATION: add missing annotations until
   `mypy --strict core/ascendo/{{module path}}` is clean; for request/IO boundaries
   prefer Pydantic models with extra="forbid" over dict access.
5. PROVE NO REGRESSION: full gate python3 -m pytest tests/contract/ tests/cross-cut/ -q
   must match the baseline; ruff check must be clean on touched files.

RULES: do not change function signatures, return shapes, or exception types that
existing callers depend on (grep for callers first: grep -rn "{{function}}" core
adapters tests). Do not refactor for style. Deliver: diff + coverage map table
(function → tests before → tests after) + baseline-vs-final test output.
```
