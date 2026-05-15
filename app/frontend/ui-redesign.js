// ============================================================================
// ui-redesign.js — UI Redesign DOM layer (Sesja 75)
// ============================================================================
// Progressive-enhancement layer loaded LAST (after ui-components.js). It
// REORGANISES existing DOM nodes at runtime — it does NOT rewrite any
// view renderer, the SSE/runs/AI wiring, the router, or i18n. Every node
// it moves is still found by the original code via getElementById /
// querySelector (id + data-i18n based), so app.js/shell.js/ui-components.js
// keep working unchanged.
//
// Mirrors the shell.js contract: wrap ui.show() so each reorg re-asserts
// when its view becomes active, but every reorg is idempotent (guarded by
// a data-flag) so it runs its structural move exactly once.
//
// Reversible: delete this file + its _spa_assets entry + the <script> tag
// and the previous structure returns byte-for-byte.
// ============================================================================
(function () {
  "use strict";

  function trOr(key, fallback) {
    if (typeof window.tr === "function") {
      const v = window.tr(key);
      if (v && v !== key) return v;
    }
    return fallback;
  }

  // ── Tools (Library → Tools): make the AI chat the primary, above-the-
  //    fold workspace; demote Quick Suggestions to a collapsed secondary
  //    panel below it. CSS alone can't fix this (it's DOM stacking order).
  //
  //    Source order in index.html #view-suggest is:
  //      <h2> AI Tools + backend pill
  //      <h3> Quick suggestions ─┐
  //      Smart-Suggestions row    │  the suggestions cluster — everything
  //      .tab-help.card           │  between <h2> and the Chat <h3>
  //      .dim hint                │
  //      #suggest-list           ─┘
  //      <h3 data-i18n=aitools.chat_h> Chat
  //      .dim hint
  //      #aitools-shell  (the 3-pane chat workspace)
  //
  //    Target order: h2 → Chat(h3+hint) → #aitools-shell →
  //                   <details> wrapping the whole suggestions cluster.
  function reorgTools() {
    const v = document.getElementById("view-suggest");
    if (!v || v.dataset.rdReorg === "1") return;

    const h2 = v.querySelector("h2");
    const chatH3 = v.querySelector('[data-i18n="aitools.chat_h"]');
    const chatHint = v.querySelector('[data-i18n="aitools.chat_hint"]');
    const shell = document.getElementById("aitools-shell");

    // Degrade safely: if the markup ever changes shape, leave it as-is
    // rather than half-moving nodes.
    if (!h2 || !chatH3 || !shell) return;

    // Collect the suggestions cluster = every element between <h2> and
    // the Chat <h3>, by walking siblings (position-independent of count).
    const cluster = [];
    let n = h2.nextElementSibling;
    while (n && n !== chatH3) {
      const next = n.nextElementSibling;
      cluster.push(n);
      n = next;
    }

    // Wrap the cluster in a collapsed <details> (secondary, opt-in).
    const det = document.createElement("details");
    det.className = "rd-secondary";
    const sum = document.createElement("summary");
    sum.textContent = trOr("aitools.quick_suggestions_h", "Quick suggestions");
    det.appendChild(sum);
    cluster.forEach((el) => det.appendChild(el)); // .appendChild MOVES

    // Chat becomes primary: right after the h2. Element.after() moves
    // existing nodes (a node lives in one place), so this re-parents
    // chatH3/chatHint/shell out of their old slot automatically.
    if (chatHint) h2.after(chatH3, chatHint, shell);
    else h2.after(chatH3, shell);
    shell.after(det);

    v.dataset.rdReorg = "1";
  }

  // Registry of per-view reorgs. Each is idempotent. Runs/Settings IA
  // restructures slot in here next.
  const REORGS = [reorgTools];

  function runAll() {
    REORGS.forEach((fn) => {
      try { fn(); } catch (e) { /* never break the app on a reorg */ }
    });
  }

  function init() {
    runAll(); // views are static in index.html — nodes exist even if hidden

    // Re-assert after navigation in case a future renderer rebuilds a
    // container (idempotent guards make repeat calls free).
    if (window.ui && typeof ui.show === "function") {
      const orig = ui.show.bind(ui);
      ui.show = function (input) {
        orig(input);
        runAll();
      };
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
