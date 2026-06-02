// ============================================================================
// run-store.js — observable run state reduced from the SSE stream. No build.
// (UX redesign Phase 2.) Defines window.runStore: subscribe / reduce / reset /
// get / tickElapsed. The live-run monitor (Phase 2B) subscribes to slices;
// app.js attachStream feeds every parsed SSE event through runStore.reduce.
//
// Per-source progress + attention are DERIVED client-side from the existing
// `sidecar` SSE events (no backend change required to start); first-class
// `phase` / `source` / `attention` events are also handled when the backend
// promotes them.
// ============================================================================
(function () {
  "use strict";

  var PHASES = ["scanning", "planning", "applying", "verifying", "cleaning_up"];
  var PHASE_OF = {
    check: "scanning", plan: "planning", apply: "applying",
    verify: "verifying", cleanup: "cleaning_up"
  };

  function blank(runId) {
    return {
      runId: runId || null,
      lifecycle: "queued",            // queued|preparing|running|finalizing|completed|completed_with_warnings|failed|cancelled
      phase: null,                    // current UI phase (scanning…cleaning_up)
      phases: PHASES.map(function (p) { return { id: p, status: "pending" }; }),
      sources: {},                    // name -> {status,done,total,current,elapsedMs}
      attention: [],                  // {source,code,message,action}
      logs: [],                       // {ts,source,level,text} (ring buffer)
      counts: { updated: 0, deferred: 0, warned: 0, failed: 0 },
      startedAt: null,
      elapsedMs: 0,
      etaMs: null,
      needsReboot: false
    };
  }

  var state = blank(null);
  var subs = [];

  function emit() { for (var i = 0; i < subs.length; i++) { try { subs[i](state); } catch (e) {} } }
  function subscribe(fn) { subs.push(fn); fn(state); return function () { subs = subs.filter(function (s) { return s !== fn; }); }; }
  function reset(runId) { state = blank(runId); emit(); return state; }
  function get() { return state; }

  function srcOf(name) {
    if (!state.sources[name]) {
      state.sources[name] = { status: "queued", done: 0, total: 0, current: "", elapsedMs: 0 };
    }
    return state.sources[name];
  }

  function mapStatus(s) {
    return ({ pending: "queued", running: "running", completed: "completed",
      failed: "failed", cancelled: "cancelled" })[s] || s;
  }

  function setPhase(backendOrUi, status) {
    var ui = PHASE_OF[backendOrUi] || backendOrUi;
    state.phase = ui;
    var hit = false;
    state.phases.forEach(function (p) {
      if (p.id === ui) { p.status = status || "running"; hit = true; }
      else if (!hit) { p.status = "done"; }
    });
  }

  // Derive per-source row + attention from an existing sidecar event so the
  // monitor works before the backend emits first-class source/attention events.
  function reduceSidecar(sc) {
    var src = sc.category;
    var items = sc.items || [];
    var sum = sc.summary || {};
    var s = srcOf(src);
    s.total = (sum.total != null) ? sum.total : items.length;
    s.done = (sum.success || 0) + (sum.up_to_date || 0);
    s.status = (sc.status === "failed") ? "failed"
      : (sc.phase === "apply" ? "running" : s.status);
    if (sc.needs_reboot) state.needsReboot = true;
    items.forEach(function (it) {
      if (it.status === "skipped" || it.status === "triggered") {
        var why = (it.messages && it.messages[0] && it.messages[0].text) || "";
        state.attention.push({
          source: src, code: it.status,
          message: (it.name || it.id) + (why ? " — " + why : ""), action: null
        });
      }
    });
  }

  function reduce(ev) {
    if (!ev || !ev.type) return state;
    switch (ev.type) {
      case "run":
      case "status":
        if (ev.state) state.lifecycle = ev.state;
        else if (ev.status) state.lifecycle = mapStatus(ev.status);
        if (ev.phase) setPhase(ev.phase);
        if (ev.started_at && !state.startedAt) state.startedAt = ev.started_at;
        break;
      case "phase":
        setPhase(ev.phase, ev.status);
        break;
      case "source": {
        var s = srcOf(ev.source);
        if (ev.status != null) s.status = ev.status;
        if (ev.done != null) s.done = ev.done;
        if (ev.total != null) s.total = ev.total;
        if (ev.current_item != null) s.current = ev.current_item;
        if (ev.elapsed_ms != null) s.elapsedMs = ev.elapsed_ms;
        break;
      }
      case "sidecar":
        reduceSidecar(ev);
        break;
      case "attention":
        state.attention.push({ source: ev.source, code: ev.code, message: ev.message, action: ev.action || null });
        break;
      case "log":
      case "log_line":
        state.logs.push({ ts: ev.ts || "", source: ev.source || "", level: ev.level || "info", text: ev.text || ev.line || "" });
        if (state.logs.length > 2000) state.logs.shift();
        break;
      case "done":
        state.lifecycle = ev.state || mapStatus(ev.status);
        if (ev.counts) state.counts = ev.counts;
        if (ev.needs_reboot != null) state.needsReboot = ev.needs_reboot;
        // Settle the stepper: a finished run must not leave a phase stuck
        // "running". Mark the in-flight phase done (or failed on a failed run);
        // pending phases that never ran stay pending.
        var term = (state.lifecycle === "failed") ? "failed" : "done";
        state.phases.forEach(function (p) { if (p.status === "running") p.status = term; });
        break;
    }
    emit();
    return state;
  }

  function tickElapsed() {
    if (state.startedAt) {
      state.elapsedMs = Date.now() - new Date(state.startedAt).getTime();
    }
    return state.elapsedMs;
  }

  window.runStore = {
    subscribe: subscribe, reduce: reduce, reset: reset, get: get,
    tickElapsed: tickElapsed, PHASES: PHASES
  };
})();
