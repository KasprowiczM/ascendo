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

  // ── Settings (Settings → General): the long single-grid card stack
  //    gets a sticky local-nav (segmented "jump to" rail) so it's
  //    navigable instead of a blind scroll. We do NOT hide/move the
  //    cards (form handlers are id/name based and must stay reachable)
  //    — we only assign each card an id and inject a nav that
  //    smooth-scrolls to it. Platform/edition-gated cards (hidden via
  //    .adapter-hide-macos / data-edition-only) are excluded from the
  //    rail so e.g. macOS never lists "Windows service".
  function reorgSettings() {
    const v = document.getElementById("view-settings");
    if (!v || v.classList.contains("hidden")) return; // build only when active
    const form = v.querySelector("#settings-form") || v;
    const grid = form.querySelector(".grid");
    if (!grid) return;

    const items = [];
    [...grid.children].forEach((card, i) => {
      if (!card.classList || !card.classList.contains("card")) return;
      const h = card.querySelector("h3");
      if (!h) return;
      if (!card.id) {
        const slug = h.textContent.trim().toLowerCase()
          .replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
        card.id = "rd-set-" + (slug || i);
      }
      // Excluded if platform/edition gating collapsed it.
      const cs = getComputedStyle(card);
      if (card.hidden || cs.display === "none" || card.offsetParent === null) return;
      items.push({ id: card.id, label: h.textContent.trim() });
    });

    // Rebuild each visit (gating can change after Platform.applyTo).
    const old = v.querySelector(".rd-settings-nav");
    if (old) old.remove();
    if (items.length < 2) return;

    const nav = document.createElement("nav");
    nav.className = "rd-settings-nav";
    nav.setAttribute("aria-label", trOr("shell.settings.title", "Settings"));
    items.forEach((it) => {
      const b = document.createElement("button");
      b.type = "button";
      b.textContent = it.label;
      b.addEventListener("click", () => {
        const t = document.getElementById(it.id);
        if (!t) return;
        t.scrollIntoView({ behavior: "smooth", block: "start" });
        t.classList.remove("rd-flash");
        void t.offsetWidth; // restart the flash animation
        t.classList.add("rd-flash");
      });
      nav.appendChild(b);
    });
    form.parentNode.insertBefore(nav, form);
  }

  // ── Runs (Runs → History): client-side status filter chips above the
  //    table. Pure post-processing of app.js's rendered rows (toggles
  //    <tr> hidden by the row's .st-pill class in the Status column);
  //    a MutationObserver re-applies the active filter whenever
  //    loadHistory() rebuilds the tbody. Zero changes to app.js.
  function reorgRunsFilter() {
    const v = document.getElementById("view-history");
    const tbl = document.getElementById("history-table");
    if (!v || !tbl) return;

    function statusIdx() {
      const ths = [...tbl.querySelectorAll("thead th")];
      const i = ths.findIndex(
        (t) => (t.getAttribute("data-i18n") || "") === "history.status"
      );
      return i < 0 ? 2 : i;
    }
    // The History table renders status as <span class="badge <status>">
    // (NOT the st-pill scheme the inventory tables use). Read the status
    // keyword from the badge in the Status column.
    function rowStatus(tr, idx) {
      const cell = tr.children[idx];
      if (!cell) return "";
      const b = cell.querySelector(".badge");
      if (b) {
        const k = [...b.classList].find((c) => c !== "badge");
        return (k || b.textContent || "").trim().toLowerCase();
      }
      return cell.textContent.trim().toLowerCase();
    }
    function applyFilter() {
      const want = v.dataset.rdFilter || "";
      const idx = statusIdx();
      tbl.querySelectorAll("tbody tr").forEach((tr) => {
        tr.hidden = want ? rowStatus(tr, idx) !== want : false;
      });
    }

    if (!v.querySelector(".rd-filterbar")) {
      // key = status keyword the badge class carries ("" = All).
      const defs = [
        ["", trOr("history.f_all", "All")],
        ["success", trOr("history.f_success", "Success")],
        ["partial", trOr("history.f_partial", "Partial")],
        ["failed", trOr("history.f_failed", "Failed")],
        ["running", trOr("history.f_running", "Running")],
      ];
      const bar = document.createElement("div");
      bar.className = "rd-filterbar";
      defs.forEach(([key, label], i) => {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "rd-chip" + (i === 0 ? " active" : "");
        b.textContent = label;
        b.addEventListener("click", () => {
          bar.querySelectorAll(".rd-chip").forEach((x) =>
            x.classList.remove("active"));
          b.classList.add("active");
          v.dataset.rdFilter = key;
          applyFilter();
        });
        bar.appendChild(b);
      });
      tbl.parentNode.insertBefore(bar, tbl);
    }

    const tb = tbl.querySelector("tbody");
    if (tb && !tb.dataset.rdObserved) {
      new MutationObserver(() => applyFilter())
        .observe(tb, { childList: true });
      tb.dataset.rdObserved = "1";
    }
    applyFilter();
  }

  // ── Overview: swap the Quick-actions and Last-run cards (operator
  //    wants actions before the run summary). One-time DOM node swap.
  function reorgOverview() {
    const v = document.getElementById("view-overview");
    if (!v || v.dataset.rdSwap === "1") return;
    const grid = v.querySelector(".grid");
    if (!grid) return;
    const lastRun = (grid.querySelector("#last-run") || {}).closest
      ? grid.querySelector("#last-run").closest(".card") : null;
    const qaEl = grid.querySelector("[data-quick]") ||
      grid.querySelector("#action-1-inventory");
    const qa = qaEl ? qaEl.closest(".card") : null;
    if (!lastRun || !qa || lastRun === qa) return;
    const ph = document.createComment("rd-swap");
    qa.replaceWith(ph);
    lastRun.replaceWith(qa);
    ph.replaceWith(lastRun);
    v.dataset.rdSwap = "1";
  }

  // ── Library → Sources: rename the category select's blank option to
  //    "All categories" (selecting it searches every category) and let
  //    ui-components rebuild the control with the new label. CSS lays
  //    the upgraded control out as a horizontal wrapping chip row.
  function reorgLibrary() {
    const sel = document.getElementById("cats-add-cat");
    if (!sel) return;
    const o = sel.querySelector('option[value=""]');
    if (!o) return;
    const want = trOr("categories.f_all", "All categories");
    if (o.textContent.trim() === want) return;
    o.textContent = want;
    sel.setAttribute("aria-label", want);
    if (window.Uikit && typeof Uikit.upgradeSelect === "function") {
      Uikit.upgradeSelect(sel);
    }
  }

  // ── Run Center: ui-components.js (Sesja 74) turns #run-form into a
  //    single-column progressive stepper. Operator wants the 3 steps as
  //    horizontal cards (wrap on mobile) with Back/Next. CSS lays them
  //    out + force-shows all 3; here we relabel Continue→Next, inject a
  //    Back button into steps 2/3, and drive a visual "current step"
  //    highlight + scroll (decoupled from ui-components' reveal closure).
  function reorgRunCenter() {
    const form = document.getElementById("run-form");
    if (!form) return;
    const steps = [...form.querySelectorAll(".u-step")];
    if (steps.length < 2) { // stepper not built yet — retry once
      if (!form.dataset.rdRunRetry) {
        form.dataset.rdRunRetry = "1";
        setTimeout(reorgRunCenter, 120);
      }
      return;
    }
    if (form.dataset.rdRun === "1") return;

    function activate(i) {
      steps.forEach((s, n) => s.classList.toggle("rd-active", n === i));
      try { steps[i].scrollIntoView({ block: "nearest", inline: "center", behavior: "smooth" }); }
      catch (_) {}
      const focusable = steps[i].querySelector(
        "input,select,button,textarea,.u-opt");
      if (focusable) try { focusable.focus({ preventScroll: true }); } catch (_) {}
    }

    steps.forEach((step, i) => {
      const next = step.querySelector(".u-step-next");
      if (next) {
        next.textContent = trOr("uikit.next", "Next →");
        next.addEventListener("click", () => activate(Math.min(i + 1, steps.length - 1)));
      }
      if (i > 0 && !step.querySelector(".rd-step-back")) {
        const back = document.createElement("button");
        back.type = "button";
        back.className = "rd-step-back";
        back.textContent = trOr("uikit.back", "← Back");
        back.addEventListener("click", () => activate(i - 1));
        // Sit it next to the Next button (or at the end of the step).
        if (next && next.parentNode) next.parentNode.insertBefore(back, next);
        else step.appendChild(back);
      }
    });
    steps[0].classList.add("rd-active");
    form.dataset.rdRun = "1";
  }

  // ── Settings: the "Profile templates" card is empty on most machines
  //    ("No templates in config/profiles/."). Replace that dead space
  //    with a useful "Common tasks" one-click run card. Reuses the
  //    existing delegated [data-quick] handler in app.js so the buttons
  //    actually fire runs. Re-injects if loadSettings() repaints it.
  const COMMON_TASKS = [
    ["quick_check", '{"profile":"quick"}'],
    ["safe_update", '{"profile":"safe"}'],
    ["full_dry", '{"profile":"full","dry_run":true}'],
    ["cleanup", '{"profile":"full","phase":"cleanup"}'],
  ];
  function paintCommonTasks(wrap) {
    if (wrap.querySelector(".rd-common-tasks")) return;
    wrap.innerHTML = "";
    const intro = document.createElement("p");
    intro.className = "dim";
    intro.style.cssText = "margin:0 0 8px;font-size:0.8rem";
    intro.textContent = trOr("profiles.common_hint",
      "No saved templates — common one-click runs instead:");
    wrap.appendChild(intro);
    const row = document.createElement("div");
    row.className = "rd-common-tasks";
    COMMON_TASKS.forEach(([key, quick]) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "secondary";
      b.setAttribute("data-quick", quick);
      b.textContent = trOr("profiles.task_" + key,
        key.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase()));
      row.appendChild(b);
    });
    wrap.appendChild(row);
  }
  function maybePaint(wrap) {
    // Paint only when there are no real templates and we haven't
    // already injected. Real templates ([data-profile-import]) win.
    if (wrap.querySelector("[data-profile-import]")) return;
    if (wrap.querySelector(".rd-common-tasks")) return;
    if (!/no templates/i.test(wrap.textContent || "") &&
        wrap.children.length > 0) return;
    paintCommonTasks(wrap);
  }
  function reorgProfilesPanel() {
    const wrap = document.getElementById("profiles-list");
    if (!wrap) return;
    maybePaint(wrap);
    if (!wrap.dataset.rdObserved) {
      new MutationObserver(() => maybePaint(wrap))
        .observe(wrap, { childList: true });
      wrap.dataset.rdObserved = "1";
    }
  }

  // Registry of per-view reorgs. Each is idempotent.
  const REORGS = [
    reorgTools, reorgSettings, reorgRunsFilter, reorgOverview, reorgLibrary,
    reorgRunCenter, reorgProfilesPanel,
  ];

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
