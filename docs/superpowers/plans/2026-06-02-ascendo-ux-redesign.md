# Ascendo UX/UI Redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn Ascendo's SPA from a flat developer-utility surface into a calm, answer-first operations product — an honest live-run monitor, a safe-by-default run flow, an answer-first Dashboard, weight-differentiated Library, and grouped Settings — without changing the no-build vanilla-JS architecture.

**Architecture:** Additive only. New UI = `window.AC` primitives (`.asc-*`, tokens-only) + a tiny observable `runStore`; routing stays `shell.js`-owned (`ui.show`); telemetry stays the existing SSE stream (extended with derived/new events). Cross-platform via `platform.js`; macOS-first rollout.

**Tech Stack:** Vanilla ES (classic `<script defer>`), `window.AC` factory, `colors_and_type.css` tokens, FastAPI dashboard (`core/ascendo/dashboard/`), SSE `GET /runs/{id}/events`, Python orchestrator (`run_async.py` / `runner.py`), SQLite `inventory_db` / `update_history`. EN/PL i18n parity enforced.

---

## Rolling-plan note

This is a **4-phase, multi-session effort**. **Phase 1** and **Phase 2's data layer** are written as fully-actionable bite-sized tasks with code. **Phase 2-UI, Phase 3, and Phase 4** are decomposed into grounded task lists (files + component contract + acceptance + verify); each is expanded into full task-by-task detail **at the start of that phase** (re-grep first — see below). Each phase ships working, verifiable software on its own.

## Codebase grounding & corrections (read first)

- **Backend lives in `core/ascendo/dashboard/`** — `app.py` (asset allow-list `_spa_assets`, ~lines 435-503), `routes/runs.py` (run + SSE endpoints), `routes/spa_real.py`, `routes/scheduler_real.py`, `routes/ai.py`, `inventory_db.py`. **Ignore `app/backend/main.py`** — that's the legacy Ubuntu backend, not what the macOS/Windows dashboard runs.
- **`app.js` is 7,370 lines.** Read-only exploration could not fully map it. The following **already exist** — find them with `grep -n` before editing, then *extend*; do NOT recreate: `startRunWithSudo`, `attachStream` (SSE consumer), `openRunDrawer`, `loadInsights`, `loadCategories`, `loadAppsView`, `loadHistory`, the settings submit + "Saved ✓" flash, the `aitools.*` namespace, `sudoMgr`, `frontendCache`.
- **`GET /runs/{id}/action-required` + `POST /web/open` + the `#action-required-panel` + SPA Action-required card already exist** (Sesja 79). Phase 3 **reuses and elevates** this onto the Dashboard "Needs your attention" — it is not new.
- **`#view-overview` is now just `<div id="overview-root">`** (old markup deleted) — `ui.loadOverview()` is the render entry; confirm whether it exists (grep) and extend or create.
- **`RunStatus` already has `CANCELLED`** + `cancel_event` + cooperative cancel (`run_async.py`). Per-source progress + skipped-why are **derivable client-side from existing `sidecar` SSE events** — no backend blocker for the monitor UI.

## Conventions (every task)

- **No-build, additive.** New components are `window.AC.<Name>` functions in `components.js` returning DOM via the `el()`/`append()` helpers (XSS-safe, `textContent` only). New CSS is `.asc-*` in `components.css`, **tokens only** (no hardcoded hex — the hygiene script warns on hex in CSS).
- **Register new asset files** in `core/ascendo/dashboard/app.py` `_spa_assets` AND add a `<script defer>`/`<link>` in `index.html` (after `app.js`, before/after `shell.js` per existing order). New files are 404 until registered.
- **i18n parity is law.** Every new user-facing string gets a key in **both** `i18n.en.js` and `i18n.pl.js`. After any i18n touch: `python3 scripts/check-i18n-parity.py` (exit 0) AND `python3 scripts/check-frontend-hygiene.py` (exit 0).
- **Verification = live, not unit (frontend).** This repo has no JS unit tests; verify in the running dashboard:
  1. restart/reload (`preview_start` "ascendo" on :8765, then `preview_eval` `location.reload()` or cache-bust),
  2. `preview_eval` hard DOM assertions on the changed region,
  3. `preview_console_logs level=error` → **0 errors**,
  4. repeat in BOTH themes (`preview_eval` toggle `data-theme`),
  5. `preview_screenshot` for the visual record.
  Backend changes (SSE events, lifecycle, ETA) get a **pytest** in `tests/contract/` or `adapters/macos/tests/` PLUS a live SSE check.
- **Commit after each task.** Branch off `main` first (`git switch -c feat/ux-redesign-phaseN`); do NOT commit straight to `main` until a phase is reviewed. Conventional Commits.
- **Keep lime rare:** the brand accent (`--accent`/`--lime-400`) is used for ≤1 primary action per screen. Status uses `--ok/--warn/--err/--info` via dot + left-stripe + full-contrast label.

---

## File structure (created / modified)

**New files**
- `app/frontend/run-store.js` — observable run state + SSE `reduce()` + ETA (Phase 2). Registered in `_spa_assets` + `index.html`.

**Modified**
- `app/frontend/components.js` / `components.css` — new `window.AC` primitives (all phases).
- `app/frontend/shell.js` — add Runs "Active" sub-tab; Settings sub-tab regroup (Phase 1 / 4).
- `app/frontend/index.html` — `#view-active` section; `#view-overview` content; Settings group markup; remove `ui-redesign.css` link.
- `app/frontend/app.js` — `ui.loadOverview`, monitor render, run-start flow, completion, `loadCategories` regroup, settings groups, Assistant rename (extend existing functions).
- `app/frontend/i18n.en.js` / `i18n.pl.js` — new keys (all phases).
- `core/ascendo/dashboard/app.py` — register new assets; retire `ui-redesign.css` entry.
- `core/ascendo/dashboard/routes/runs.py` — `phase`/`source`/`attention` SSE events; `preparing`/`finalizing` (Phase 2).
- `core/ascendo/orchestrator/run_async.py` / `runner.py` — lifecycle sub-states + clean/warnings split (Phase 2).

---

# Phase 1 — IA & layout cleanup (fully actionable)

**Outcome:** the structural foundation — new AC primitives + CSS exist, the dead CSS layer is retired, the "Active" Runs sub-tab is wired, and the Dashboard container is ready — with zero behaviour regression.

### Task 1.1 — ⚠ REVISED (2026-06-02): `ui-redesign.css` is load-bearing — DO NOT remove in Phase 1

**Finding during execution:** the grounding agent's "dead layer" claim was wrong. `ui-redesign.css` has no JS companion (`ui-redesign.js` was removed Sesja 76), BUT the **CSS itself still styles the current screens** — `index.html:46/49/1694` comments confirm it *"restores the previous look byte-for-byte"* and carries *"P0 + M4 rules"*; HANDOFF Sesja 77 marks its removal *"high-regression, deferred."* Removing it in Phase 1 (which adds no replacement screens) would regress the live UI with zero benefit.

**Decision:** **keep** `ui-redesign.css`. `components.css` already loads AFTER it (`index.html:50` > `:47`), so new `.asc-*` rules win by source order — Phase 1's primitives are unaffected. Removal is deferred to the phase where redesigned screens supersede its rules (revisit Phase 3/4 with full per-screen re-verify). Commit to `main` (repo convention), no feature branch.

- [x] Confirmed load-bearing; removal deferred. No edit. Proceed to 1.2.

### Task 1.2 — `ProgressBar` AC primitive

**Files:** Modify `app/frontend/components.js`, `app/frontend/components.css`

- [ ] **Step 1: Add the component** to `components.js` (before the `window.AC` export):

```js
/* ---- ProgressBar ------------------------------------- */
function ProgressBar(o) {
  o = o || {};
  var pct = o.total > 0 ? Math.max(0, Math.min(100, Math.round((o.value / o.total) * 100)))
                        : (o.pct != null ? o.pct : 0);
  var variant = o.variant || "accent"; // accent | ok | warn | err
  var wrap = el("div", "asc-progress");
  wrap.setAttribute("role", "progressbar");
  wrap.setAttribute("aria-valuenow", String(pct));
  wrap.setAttribute("aria-valuemin", "0");
  wrap.setAttribute("aria-valuemax", "100");
  var fill = el("div", "asc-progress__fill asc-progress__fill--" + variant);
  fill.style.width = pct + "%";
  wrap.appendChild(fill);
  return wrap;
}
```

- [ ] **Step 2: Export it** — add `ProgressBar: ProgressBar,` to the `window.AC = {…}` object.
- [ ] **Step 3: Add CSS** to `components.css` (tokens only):

```css
.asc-progress{height:8px;border-radius:999px;background:var(--bg-sunk);overflow:hidden}
.asc-progress__fill{height:100%;border-radius:999px;transition:width var(--motion-base,.3s) ease}
.asc-progress__fill--accent{background:var(--accent)}
.asc-progress__fill--ok{background:var(--ok)}
.asc-progress__fill--warn{background:var(--warn)}
.asc-progress__fill--err{background:var(--err)}
```

- [ ] **Step 4: Verify.** `preview_eval`: `(function(){var n=AC.ProgressBar({value:14,total:23});document.body.appendChild(n);return n.getAttribute('aria-valuenow');})()` → expect `"61"`; `preview_console_logs level=error` → 0.
- [ ] **Step 5: Commit.** `git commit -am "feat(ui): AC.ProgressBar primitive"`

### Task 1.3 — `VerdictHeader` + `AttentionList` AC primitives

**Files:** Modify `app/frontend/components.js`, `app/frontend/components.css`

- [ ] **Step 1: Add `VerdictHeader`** to `components.js`:

```js
/* ---- VerdictHeader ----------------------------------- */
function VerdictHeader(o) {
  o = o || {};                 // {status, title, sub, cta:{label,onClick,variant}}
  var h = el("div", "asc-verdict");
  var main = el("div", "asc-verdict__main");
  var line = el("div", "asc-verdict__line");
  line.appendChild(el("span", "asc-verdict__dot asc-verdict__dot--" + STATUS(o.status)));
  line.appendChild(el("span", "asc-verdict__title", o.title || ""));
  main.appendChild(line);
  if (o.sub != null) main.appendChild(el("p", "asc-verdict__sub", o.sub));
  h.appendChild(main);
  if (o.cta) h.appendChild(Button(o.cta));
  return h;
}
```

- [ ] **Step 2: Add `AttentionList`** to `components.js` (renders nothing when empty):

```js
/* ---- AttentionList ----------------------------------- */
function AttentionCard(it) {
  it = it || {};               // {tone, title, detail, actions:[Button opts]}
  var c = el("div", "asc-attn asc-attn--" + STATUS(it.tone || "warn"));
  var body = el("div", "asc-attn__body");
  body.appendChild(el("div", "asc-attn__title", it.title || ""));
  if (it.detail != null) body.appendChild(el("div", "asc-attn__detail", it.detail));
  c.appendChild(body);
  if (it.actions && it.actions.length) {
    var acts = el("div", "asc-attn__actions");
    it.actions.forEach(function (a) { acts.appendChild(Button(a)); });
    c.appendChild(acts);
  }
  return c;
}
function AttentionList(items) {
  items = items || [];
  if (!items.length) return null;           // caller skips the section
  var list = el("div", "asc-attn-list");
  items.forEach(function (it) { list.appendChild(AttentionCard(it)); });
  return list;
}
```

- [ ] **Step 3: Export** `VerdictHeader`, `AttentionList`, `AttentionCard` in `window.AC`.
- [ ] **Step 4: Add CSS** to `components.css` (tokens only — verdict title full-contrast `--fg`, dot carries the semantic color; attention uses left-stripe per the visual system):

```css
.asc-verdict{display:flex;align-items:flex-start;justify-content:space-between;gap:var(--space-4);flex-wrap:wrap}
.asc-verdict__line{display:flex;align-items:center;gap:var(--space-2)}
.asc-verdict__title{font-size:var(--fs-h2,1.5rem);font-weight:700;color:var(--fg);line-height:1.2}
.asc-verdict__dot{width:11px;height:11px;border-radius:50%}
.asc-verdict__dot--ok{background:var(--ok)} .asc-verdict__dot--warn{background:var(--warn)}
.asc-verdict__dot--err{background:var(--err)} .asc-verdict__dot--neutral{background:var(--fg-faint)}
.asc-verdict__sub{margin:var(--space-1) 0 0;color:var(--fg-muted);font-size:var(--fs-body,.95rem)}
.asc-attn-list{display:flex;flex-direction:column;gap:var(--space-2)}
.asc-attn{display:flex;align-items:center;justify-content:space-between;gap:var(--space-3);
  background:var(--bg-elev);border:1px solid var(--border);border-radius:var(--radius-md,10px);padding:12px 15px}
.asc-attn--warn{border-left:3px solid var(--warn)} .asc-attn--err{border-left:3px solid var(--err)}
.asc-attn__title{font-weight:600;color:var(--fg)} .asc-attn__detail{color:var(--fg-muted);font-size:.82rem;margin-top:2px}
.asc-attn__actions{display:flex;gap:8px;flex:none}
```

- [ ] **Step 5: Verify.** `preview_eval` mount both into `document.body`, assert `AC.AttentionList([])===null` and a populated list has `.asc-attn` children; `preview_console_logs level=error` → 0; screenshot both themes.
- [ ] **Step 6: Commit.** `git commit -am "feat(ui): AC.VerdictHeader + AttentionList primitives"`

### Task 1.4 — Promote the "Active" Runs sub-tab + `#view-active` container

**Files:** Modify `app/frontend/shell.js:64`, `app/frontend/index.html` (near `#view-run`), `i18n.en.js`/`i18n.pl.js`

- [ ] **Step 1: Add the sub-tab** in `shell.js` `runs.tabs` (after `start`, line 64):

```js
{ id: "active",    labelKey: "shell.tabs.active",    view: "active" },
```

- [ ] **Step 2: Add the view container** in `index.html` after `#view-run`:

```html
<section id="view-active" class="view hidden">
  <div id="active-root"></div>
</section>
```

- [ ] **Step 3: Add i18n** `shell.tabs.active` → EN `"Active"`, PL `"Aktywne"` in both locale files (alongside the other `shell.tabs.*`).
- [ ] **Step 4: i18n gate.** `python3 scripts/check-i18n-parity.py` (exit 0) + `python3 scripts/check-frontend-hygiene.py` (exit 0).
- [ ] **Step 5: Verify live.** `preview_eval ui.show('runs/active')` → assert `location.hash==='#runs/active'`, `#view-active` not `.hidden`, the sub-tab rail shows 4 tabs with "Active" active; `preview_console_logs level=error` → 0.
- [ ] **Step 6: Commit.** `git commit -am "feat(runs): first-class Active sub-tab + #view-active"`

### Task 1.5 — Phase-1 review checkpoint

- [ ] Run the full live sweep (5 destinations × 2 themes, 0 console errors); parity + hygiene green; confirm no regression vs `main`.
- [ ] Open PR / merge `feat/ux-redesign-phase1` per `superpowers:finishing-a-development-branch`.

---

# Phase 2 — Run monitoring UX (centerpiece)

**Outcome:** the execution-state data layer, the live-run monitor, the intent-based run-start flow with `DangerConfirm`, and the completion summary.

## 2A · Data layer (fully actionable)

### Task 2.1 — `run-store.js`: observable run state + SSE reduce

**Files:** Create `app/frontend/run-store.js`; Modify `core/ascendo/dashboard/app.py` (`_spa_assets`), `index.html` (`<script defer>` after `app.js`)

- [ ] **Step 1: Create `app/frontend/run-store.js`:**

```js
// run-store.js — observable run state reduced from the SSE stream. No build.
(function () {
  "use strict";
  var PHASES = ["scanning", "planning", "applying", "verifying", "cleaning_up"];
  var PHASE_OF = { check:"scanning", plan:"planning", apply:"applying", verify:"verifying", cleanup:"cleaning_up" };
  function blank(runId) {
    return { runId: runId || null, lifecycle: "queued", phase: null,
      phases: PHASES.map(function (p){ return { id:p, status:"pending" }; }),
      sources: {}, attention: [], logs: [],
      counts:{ updated:0, deferred:0, warned:0, failed:0 },
      startedAt: null, elapsedMs: 0, etaMs: null, needsReboot: false };
  }
  var state = blank(null), subs = [];
  function emit(){ subs.forEach(function (fn){ try { fn(state); } catch(e){} }); }
  function subscribe(fn){ subs.push(fn); fn(state); return function(){ subs = subs.filter(function(s){return s!==fn;}); }; }
  function reset(runId){ state = blank(runId); emit(); }
  function srcOf(name){ return (state.sources[name] = state.sources[name] || { status:"queued", done:0, total:0, current:"", elapsedMs:0 }); }

  function reduce(ev) {
    if (!ev || !ev.type) return;
    switch (ev.type) {
      case "run": case "status":
        if (ev.state) state.lifecycle = ev.state;
        else if (ev.status) state.lifecycle = mapStatus(ev.status);
        if (ev.phase) setPhase(ev.phase);
        if (ev.started_at && !state.startedAt) state.startedAt = ev.started_at;
        break;
      case "phase": setPhase(ev.phase, ev.status); break;
      case "source": {
        var s = srcOf(ev.source);
        if (ev.status) s.status = ev.status;
        if (ev.done != null) s.done = ev.done;
        if (ev.total != null) s.total = ev.total;
        if (ev.current_item != null) s.current = ev.current_item;
        if (ev.elapsed_ms != null) s.elapsedMs = ev.elapsed_ms;
        break;
      }
      case "sidecar": reduceSidecar(ev); break;     // derive source+attention client-side (Phase 2A)
      case "attention": state.attention.push({ source:ev.source, code:ev.code, message:ev.message, action:ev.action }); break;
      case "log": case "log_line":
        state.logs.push({ ts:ev.ts||"", source:ev.source||"", level:ev.level||"info", text:ev.text||ev.line||"" });
        if (state.logs.length > 2000) state.logs.shift();
        break;
      case "done":
        state.lifecycle = ev.state || mapStatus(ev.status);
        if (ev.counts) state.counts = ev.counts;
        if (ev.needs_reboot != null) state.needsReboot = ev.needs_reboot;
        break;
    }
    emit();
  }
  function mapStatus(s){ return ({pending:"queued",running:"running",completed:"completed",failed:"failed",cancelled:"cancelled"})[s] || s; }
  function setPhase(backendOrUi, status){
    var ui = PHASE_OF[backendOrUi] || backendOrUi; state.phase = ui;
    var hit = false;
    state.phases.forEach(function (p){
      if (p.id === ui){ p.status = status || "running"; hit = true; }
      else if (!hit) p.status = "done";
    });
  }
  function reduceSidecar(sc){           // existing sidecar event → source row + attention (no backend change)
    var src = sc.category, items = sc.items || [], sum = sc.summary || {};
    var s = srcOf(src);
    s.total = sum.total != null ? sum.total : items.length;
    s.done = (sum.success||0) + (sum.up_to_date||0);
    s.status = sc.status === "failed" ? "failed" : (sc.phase === "apply" ? "running" : s.status);
    if (sc.needs_reboot) state.needsReboot = true;
    items.forEach(function (it){
      if (it.status === "skipped" || it.status === "triggered") {
        var why = (it.messages && it.messages[0] && it.messages[0].text) || "";
        state.attention.push({ source:src, code:it.status, message:(it.name||it.id)+(why?" — "+why:""), action:null });
      }
    });
  }
  function tickElapsed(){ if (state.startedAt) state.elapsedMs = Date.now() - new Date(state.startedAt).getTime(); }
  window.runStore = { subscribe:subscribe, reduce:reduce, reset:reset, get:function(){return state;}, tickElapsed:tickElapsed, PHASES:PHASES };
})();
```

- [ ] **Step 2: Register** `("run-store.js","application/javascript")` in `app.py` `_spa_assets`; add `<script defer src="/run-store.js"></script>` in `index.html` after `app.js`.
- [ ] **Step 3: Verify.** `preview_eval`: feed a fake sequence — `runStore.reset('t'); runStore.reduce({type:'phase',phase:'apply'}); runStore.reduce({type:'source',source:'brew',done:5,total:5,status:'success'}); var s=runStore.get(); [s.phase, s.phases[2].status, s.sources.brew.done]` → expect `["applying","running",5]`. `preview_console_logs level=error` → 0.
- [ ] **Step 4: Commit.** `git commit -am "feat(runs): runStore observable + SSE reduce (client-side source/attention derivation)"`

### Task 2.2 — Backend: lifecycle sub-states + clean/warnings split

**Files:** Modify `core/ascendo/orchestrator/run_async.py` (RunStatus/RunState/worker), `tests/contract/test_run_async_lifecycle.py` (new)

- [ ] **Step 1: Write the failing test** in `tests/contract/test_run_async_lifecycle.py` asserting a completed-with-warnings run reports a distinguishable terminal state and that `preparing`/`finalizing` markers are emitted (drive `start_run_async` with a fake adapter; assert `RunState` exposes `phase`/sub-state + a `warnings` count, and `done` payload carries `state ∈ {completed, completed_with_warnings, failed, cancelled}`).
- [ ] **Step 2: Run it** → FAIL. `PYTHONPATH=core:adapters/macos python3 -m pytest tests/contract/test_run_async_lifecycle.py -v`
- [ ] **Step 3: Implement** — add `preparing`/`finalizing` window around the phase loop; compute `completed_with_warnings` when the run succeeded but any sidecar has `summary.skipped/partial>0` or a warn message or `needs_reboot`. Keep `RunStatus` enum for the registry; expose the refined terminal state on the `done` payload + `GET /runs/{id}/status`.
- [ ] **Step 4: Run** → PASS. **Step 5: Commit.** `git commit -am "feat(runs): preparing/finalizing sub-states + completed-with-warnings split"`

### Task 2.3 — Backend: `phase` + `source` + `attention` SSE events

**Files:** Modify `core/ascendo/dashboard/routes/runs.py` (SSE generator), `core/ascendo/orchestrator/runner.py` (phase-boundary hooks), `tests/contract/test_sse_events.py` (new)

- [ ] **Step 1: Write the failing test** — connect to `GET /runs/{id}/events` for a fake run; assert the stream contains a `phase` event with `{phase,index,total}` at each phase boundary and that `done` carries `counts`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** — emit `event: phase` when the runner enters each phase (index/total over the 5 phases); keep `source`/`attention` derivation client-side for now (runStore already does it from `sidecar`), but add a server `attention` emission for deferred/needs-you items so the Dashboard can read it without re-deriving (reuse the Sesja-79 action-required collector). Preserve all existing events.
- [ ] **Step 4: Run** → PASS + live SSE check (`curl -N` shows `event: phase`). **Step 5: Commit.** `git commit -am "feat(sse): phase + attention events on /runs/{id}/events"`

## 2B · Monitor + run-start + completion (decomposed — expand at kickoff)

> Re-grep `app.js` for the current `startRunWithSudo`/`attachStream`/run render before editing; **extend**, don't replace. Wire `attachStream` to call `runStore.reduce(parsedEvent)` and subscribe the monitor render to `runStore`.

### Task 2.4 — `RunHeader` + `PhaseStepper` + `SourceProgressRow` + `LogViewer` AC primitives
- **Files:** `components.js` / `components.css`.
- **Contract:** `RunHeader({intent,state,elapsedMs,etaMs,onStop,value,total})`; `PhaseStepper(phasesArray)`; `SourceProgressRow({name,status,done,total,current})` (dot + name + counts + `AC.ProgressBar` mini + current-item + status); `LogViewer({lines,collapsed:true})` (dark console, level colors, ring buffer, autoscroll, filter).
- **Acceptance:** each renders from a `runStore` slice; Stop always present; log collapsed by default. Verify via `preview_eval` mounting with fake state, 0 console errors, both themes.
- **Commit** per component.

### Task 2.5 — Live monitor assembly (`ui.renderActiveRun`) into `#view-active`
- **Files:** `app.js` (new `ui.renderActiveRun` subscribing to `runStore`), wire `attachStream` → `runStore.reduce`.
- **Acceptance:** start a real **Quick check** (read-only, safe) on :8765; the Active tab shows phase stepper advancing, per-source rows, elapsed ticking, ETA est., attention strip, log on expand; 0 console errors. Stop halts (cooperative).
- **Commit.**

### Task 2.6 — Intent-based run-start (`IntentRunCard`) + `DangerConfirm`
- **Files:** `components.js`/`components.css` (`IntentRunCard` weight variants hero/secondary/caution; `DangerConfirm` type-to-confirm, disabled-until-typed, Cancel-default, red action distanced); `app.js` run-start render into `#view-run`; map Quick check→`quick`, Safe update→`safe`, Full update→`full` profiles; keep the existing scope+phase builder under **▸ Advanced**.
- **Acceptance:** Safe update is the only lime button; Full update opens `DangerConfirm` (type `update`); Advanced still drives the raw builder; existing `startRunWithSudo` path intact. Verify live + 0 errors + parity/hygiene for new strings.
- **Commit.**

### Task 2.7 — `CompletionSummary` + run-detail drawer reuse
- **Files:** `components.js`/`components.css` (`CompletionSummary({verdict,needsReboot,counts,attention,changed,actions})`); `app.js` render it on `done` and inside the existing `openRunDrawer` (History rows reuse it).
- **Acceptance:** finished run shows verdict + stat line + Needs-you + What-changed + actions; drawer from History shows the same; "View full report" opens `/runs/{id}/report`. Verify live + 0 errors.
- **Commit + Phase-2 review checkpoint.**

---

# Phase 3 — Dashboard & Insights (decomposed — expand at kickoff)

> Reuse the existing `GET /runs/{id}/action-required` + `#action-required-panel` (Sesja 79). Re-grep `loadInsights`/`openRunDrawer`/`loadOverview` first.

### Task 3.1 — Answer-first `ui.loadOverview` into `#overview-root`
- **Files:** `app.js` (`ui.loadOverview`); consumes `GET /inventory/summary` (verdict counts), `GET /runs?limit=N` (recent changes + failure detection), `GET /health/check` (health-glance), `GET /runs/{id}/action-required` (attention).
- **Build:** `AC.VerdictHeader` (status from outdated count: 0→ok "up to date", >0→warn "N ready · M need you") + lime `Safe update` CTA → `ui.show('runs/start')` preset safe; `AC.AttentionList` (only when non-empty; includes failed recent runs → View/Retry, deferred → Resolve); recent-changes `AC.Timeline` (failed flagged, row → `openRunDrawer`); demoted "At a glance" (health 12/12 + managed/outdated) using `AC.StatPair`/`KpiStrip`.
- **Acceptance:** both states render (busy + calm); a recent failed run is surfaced in attention (fixes the live-app gap); health/inventory demoted; one lime CTA; 0 console errors both themes; parity/hygiene green.
- **Commit.**

### Task 3.2 — Slim Insights (`loadInsights`)
- **Files:** `app.js` (`loadInsights`), `index.html` `#view-insights` (drop notes/filler containers).
- **Build:** `AC.KpiStrip` (total runs · success rate · avg duration · packages updated) + single-hue duration trend + recent-failures (rows → `openRunDrawer`) + outcome trend. Logs sub-tab stays dev. Owns the time axis only.
- **Acceptance:** renders from `/runs?limit=N`; no operational-notes filler; failures open the shared drawer; 0 errors.
- **Commit + Phase-3 review.**

---

# Phase 4 — Settings, Library, Assistant (decomposed — expand at kickoff)

### Task 4.1 — Settings 8→4 groups
- **Files:** `shell.js` Settings `tabs` (General · Automation · Backup & Safety · AI & Integrations · About — keep `edition` gates on dev items); `index.html` `#view-settings` regroup into `SettingsGroup`/`SettingRow`; `app.js` keep the existing submit + "Saved ✓" flash; `i18n.en/pl` new group keys.
- **Acceptance:** four groups + About; every existing control still saves; sticky dirty-gated Save; 0 errors; parity/hygiene green; `validate-macos.sh` Stage 14 passes.
- **Commit.**

### Task 4.2 — Library Sources weight-differentiation (`loadCategories`)
- **Files:** `app.js` `loadCategories` → render an "Updates available" group (amber stripe + count badge + `Update N`) above a quiet "Up to date" group (compact rows); per-source advanced actions behind a `⋯` overflow; new `SourceListItem` AC primitive.
- **Acceptance:** sources with outdated>0 rise with an action; all-current sources are compact one-liners; advanced actions hidden by default; 0 errors both themes.
- **Commit.**

### Task 4.3 — Rename "Tools" → "Assistant"
- **Files:** `shell.js:55` `labelKey: "shell.tabs.tools"` → `"shell.tabs.assistant"`; add `shell.tabs.assistant` EN "Assistant"/PL "Asystent"; keep URL `#library/tools`→view `suggest` resolving (add alias if needed). No behavior change to `aitools.*`.
- **Acceptance:** tab reads "Assistant"; old `#suggest` bookmark still resolves; 0 errors; parity/hygiene green.
- **Commit + Phase-4 review + final i18n/hygiene pass.**

---

## Self-review (against the spec)

- **§1-2 diagnosis/audit** → addressed across phases (Dashboard 3.1, Runs 2.4-2.7, Settings 4.1, Library 4.2, Insights 3.2). ✓
- **§3 IA** → Active sub-tab (1.4); Settings groups (4.1); Assistant rename (4.3). ✓
- **§4 screens** → 4.1 Dashboard→3.1; 4.2 Runs→2.6; 4.3 monitor→2.4/2.5; 4.4 completion→2.7; 4.5 Library→4.2; 4.6 Insights→3.2; 4.7 Settings→4.1. ✓
- **§5 state model + telemetry + runStore + ETA** → 2.1 (runStore + ETA scaffold), 2.2 (lifecycle), 2.3 (events). ✓
- **§6 visual system** → enforced via tokens-only `.asc-*` CSS in every component task + lime-rare convention. ✓
- **§7 component inventory** → ProgressBar 1.2, VerdictHeader/AttentionList 1.3, RunHeader/PhaseStepper/SourceProgressRow/LogViewer 2.4, IntentRunCard/DangerConfirm 2.6, CompletionSummary 2.7, SourceListItem 4.2, SettingsGroup/SettingRow 4.1, runStore 2.1. ✓ (all named components covered)
- **§8 Polish copy** → every i18n task pairs EN+PL + parity gate. ✓ (full string set lands inline as each screen is built)
- **§9 phases** → Phase 1-4 match the spec roadmap order. ✓
- **§10 risks/anti-patterns** → CSS-debt (1.1 retire ui-redesign.css), app.js bloat (run-store.js module + extend-don't-recreate), ETA honesty (est. label in RunHeader), reconnect (rebuild runStore from `/status`+sidecars — add to 2.5), i18n gate every task. ✓

**Gaps noted for phase-kickoff expansion:** Phase 2B/3/4 tasks carry component contracts + acceptance but not line-by-line code (rolling-plan by design); each is expanded to bite-sized steps with full code at the start of its phase, after re-grepping the current `app.js`.
