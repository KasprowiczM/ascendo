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
    // Keep ui-components' NATIVE progressive reveal (step N appears only
    // when you advance — operator wants that restored). CSS wraps the
    // form as one big card with the active step as an inner card. We
    // only relabel Continue→"Next →" and inject a "← Back" that
    // re-reveals the previous step by clicking its head (prior steps
    // are is-done, not is-locked, so ui-components' own head handler
    // reveals them — no closure access needed).
    steps.forEach((step, i) => {
      const next = step.querySelector(".u-step-next");
      if (next) next.textContent = trOr("uikit.next", "Next →");
      if (i > 0 && !step.querySelector(".rd-step-back")) {
        const back = document.createElement("button");
        back.type = "button";
        back.className = "rd-step-back";
        back.textContent = trOr("uikit.back", "← Back");
        back.addEventListener("click", () => {
          const prevHead = steps[i - 1].querySelector(".u-step-head");
          if (prevHead) prevHead.click();
        });
        if (next && next.parentNode) next.parentNode.insertBefore(back, next);
        else step.appendChild(back);
      }
    });
    form.dataset.rdRun = "1";
  }

  // ── Runs → Scheduled: turn the flat "Add or replace schedule" form
  //    into a 3-step wizard (What · When · Options) inside one big
  //    card, progressive with Back/Next. All fields stay inside
  //    <form id="schedule-form"> so FormData + app.js's submit handler
  //    keep working byte-for-byte (we only re-parent existing nodes).
  function reorgScheduleForm() {
    const form = document.getElementById("schedule-form");
    if (!form || form.dataset.rdSched === "1") return;
    const kids = [...form.children];
    const byHas = (sel) =>
      kids.find((k) => k.querySelector && k.querySelector(sel));
    const lblName = byHas("#schedule-f-name");
    const lblExpr = byHas("#schedule-f-expr");
    const lblProf = byHas("#schedule-f-profile");
    const lblEn = byHas("#schedule-f-enabled");
    const lblDesc = byHas("#schedule-f-desc");
    const btns = form.querySelector(".schedule-form-buttons");
    if (!lblName || !lblExpr || !btns) return; // shape unexpected → leave

    function go(n) {
      [s1, s2, s3].forEach((s, i) => s.classList.toggle("rd-on", i + 1 === n));
    }
    function mkStep(n, title, nodes, withNext) {
      const s = document.createElement("div");
      s.className = "rd-sched-step" + (n === 1 ? " rd-on" : "");
      const head = document.createElement("div");
      head.className = "rd-sched-head";
      const num = document.createElement("span");
      num.className = "rd-sched-num";
      num.textContent = String(n);
      const ttl = document.createElement("span");
      ttl.textContent = title;
      head.appendChild(num);
      head.appendChild(ttl);
      s.appendChild(head);
      nodes.filter(Boolean).forEach((x) => s.appendChild(x)); // moves nodes
      const nav = document.createElement("div");
      nav.className = "rd-sched-nav";
      if (n > 1) {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "rd-step-back";
        b.textContent = trOr("uikit.back", "← Back");
        b.addEventListener("click", () => go(n - 1));
        nav.appendChild(b);
      }
      if (withNext) {
        const nx = document.createElement("button");
        nx.type = "button";
        nx.className = "u-step-next";
        nx.textContent = trOr("uikit.next", "Next →");
        nx.addEventListener("click", () => go(n + 1));
        nav.appendChild(nx);
      }
      s.appendChild(nav);
      return s;
    }
    const s1 = mkStep(1, trOr("schedule.step_what", "What"),
      [lblName, lblProf], true);
    const s2 = mkStep(2, trOr("schedule.step_when", "When"),
      [lblExpr], true);
    const s3 = mkStep(3, trOr("schedule.step_options", "Options"),
      [lblEn, lblDesc, btns], false);
    form.appendChild(s1);
    form.appendChild(s2);
    form.appendChild(s3);
    form.dataset.rdSched = "1";
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

  // ── In-app report + log viewer ────────────────────────────────────────
  // Operator: report/logs must show INSIDE the app, not as an external
  // browser tab. We intercept the existing report <a target="_blank">
  // and the drawer "View logs" button (capture phase, so app.js's own
  // window.open/navigate handler never fires) and render the content in
  // a modal. Endpoints: GET /runs/{id}/report (text/markdown),
  // GET /runs/{id} (sidecar list), GET /runs/{id}/phase/{cat}/{ph}/log.
  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }
  // Minimal, injection-safe markdown → HTML. Everything is escaped
  // first; only a known tag set is reintroduced.
  function mdToHtml(src) {
    const lines = String(src || "").replace(/\r\n/g, "\n").split("\n");
    let html = "", i = 0, inUl = false, inFence = false, fence = [];
    const closeUl = () => { if (inUl) { html += "</ul>"; inUl = false; } };
    const inline = (t) => esc(t)
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    for (; i < lines.length; i++) {
      const ln = lines[i];
      if (/^```/.test(ln)) {
        if (inFence) { html += "<pre class='rd-doc-pre'>" + esc(fence.join("\n")) + "</pre>"; fence = []; inFence = false; }
        else { closeUl(); inFence = true; }
        continue;
      }
      if (inFence) { fence.push(ln); continue; }
      // table block: a line of | cells | followed by a |---| separator
      if (/^\s*\|.*\|\s*$/.test(ln) && /^\s*\|[\s:|-]+\|\s*$/.test(lines[i + 1] || "")) {
        closeUl();
        const cells = (r) => r.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
        let t = "<table class='rd-doc-table'><thead><tr>";
        cells(ln).forEach((c) => { t += "<th>" + inline(c) + "</th>"; });
        t += "</tr></thead><tbody>";
        i += 2;
        for (; i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i]); i++) {
          t += "<tr>";
          cells(lines[i]).forEach((c) => { t += "<td>" + inline(c) + "</td>"; });
          t += "</tr>";
        }
        i--;
        html += t + "</tbody></table>";
        continue;
      }
      const h = ln.match(/^(#{1,6})\s+(.*)$/);
      if (h) { closeUl(); html += "<h" + h[1].length + " class='rd-doc-h'>" + inline(h[2]) + "</h" + h[1].length + ">"; continue; }
      if (/^\s*[-*]\s+/.test(ln)) {
        if (!inUl) { html += "<ul class='rd-doc-ul'>"; inUl = true; }
        html += "<li>" + inline(ln.replace(/^\s*[-*]\s+/, "")) + "</li>";
        continue;
      }
      if (/^\s*(-{3,}|_{3,})\s*$/.test(ln)) { closeUl(); html += "<hr>"; continue; }
      if (!ln.trim()) { closeUl(); continue; }
      closeUl();
      html += "<p>" + inline(ln) + "</p>";
    }
    if (inFence) html += "<pre class='rd-doc-pre'>" + esc(fence.join("\n")) + "</pre>";
    closeUl();
    return html;
  }

  let _modalEsc = null;
  function closeModal() {
    const m = document.getElementById("rd-modal");
    if (m) m.remove();
    if (_modalEsc) { document.removeEventListener("keydown", _modalEsc); _modalEsc = null; }
  }
  function openModal(title) {
    closeModal();
    const m = document.createElement("div");
    m.id = "rd-modal";
    m.className = "rd-modal";
    m.setAttribute("role", "dialog");
    m.setAttribute("aria-modal", "true");
    m.innerHTML =
      '<div class="rd-modal-backdrop" data-rd-close></div>' +
      '<div class="rd-modal-panel" tabindex="-1">' +
      '<header class="rd-modal-head"><span class="rd-modal-title"></span>' +
      '<button type="button" class="rd-modal-x" data-rd-close aria-label="Close">×</button>' +
      '</header><div class="rd-modal-body"></div></div>';
    m.querySelector(".rd-modal-title").textContent = title;
    document.body.appendChild(m);
    m.addEventListener("click", (e) => {
      if (e.target.closest("[data-rd-close]")) closeModal();
    });
    _modalEsc = (e) => { if (e.key === "Escape") closeModal(); };
    document.addEventListener("keydown", _modalEsc);
    const panel = m.querySelector(".rd-modal-panel");
    if (panel) panel.focus();
    return m.querySelector(".rd-modal-body");
  }

  function showReport(runId) {
    const body = openModal(trOr("shell.detail.open_report", "Full report"));
    body.innerHTML = "<p class='dim'>" + esc(trOr("uikit.loading", "Loading…")) + "</p>";
    fetch("/runs/" + encodeURIComponent(runId) + "/report")
      .then((r) => r.ok ? r.text() : Promise.reject(r.status))
      .then((md) => { body.className = "rd-modal-body rd-doc"; body.innerHTML = mdToHtml(md); })
      .catch((s) => {
        body.innerHTML = "<p class='dim'>" +
          esc(s === 404
            ? trOr("shell.detail.no_report", "No report — this run had no apply phase.")
            : trOr("uikit.error", "Could not load the report.")) + "</p>";
      });
  }

  function showLogs(runId) {
    const body = openModal(trOr("shell.detail.open_logs", "Logs"));
    body.innerHTML = "<p class='dim'>" + esc(trOr("uikit.loading", "Loading…")) + "</p>";
    fetch("/runs/" + encodeURIComponent(runId))
      .then((r) => r.ok ? r.json() : Promise.reject(r.status))
      .then((sidecars) => {
        const list = Array.isArray(sidecars) ? sidecars : [];
        body.innerHTML = "";
        const rail = document.createElement("div");
        rail.className = "rd-log-rail";
        const pre = document.createElement("pre");
        pre.className = "rd-log-pre";
        pre.textContent = trOr("logs.pick_run_hint", "Select a phase to view its log.");
        if (!list.length) {
          body.innerHTML = "<p class='dim'>" +
            esc(trOr("logs.empty_state", "No logs for this run.")) + "</p>";
          return;
        }
        list.forEach((sc) => {
          const cat = sc.category || (sc.run && sc.run.category) || "?";
          const ph = sc.phase || "?";
          const st = sc.status || (sc.summary && sc.summary.status) || "";
          const b = document.createElement("button");
          b.type = "button";
          b.className = "rd-log-item";
          b.textContent = ph + " · " + cat + (st ? "  (" + st + ")" : "");
          b.addEventListener("click", () => {
            rail.querySelectorAll(".rd-log-item").forEach((x) => x.classList.remove("active"));
            b.classList.add("active");
            pre.textContent = trOr("uikit.loading", "Loading…");
            fetch("/runs/" + encodeURIComponent(runId) + "/phase/" +
              encodeURIComponent(cat) + "/" + encodeURIComponent(ph) + "/log")
              .then((r) => r.ok ? r.text() : Promise.reject(r.status))
              .then((txt) => { pre.textContent = txt || trOr("logs.empty_state", "(empty log)"); })
              .catch(() => { pre.textContent = trOr("uikit.error", "Could not load this log."); });
          });
          rail.appendChild(b);
        });
        const split = document.createElement("div");
        split.className = "rd-log-split";
        split.appendChild(rail);
        split.appendChild(pre);
        body.appendChild(split);
      })
      .catch(() => {
        body.innerHTML = "<p class='dim'>" +
          esc(trOr("uikit.error", "Could not load logs.")) + "</p>";
      });
  }

  function runIdFromHref(href) {
    const m = String(href || "").match(/\/runs\/([^/]+)\/report/);
    return m ? decodeURIComponent(m[1]) : null;
  }
  function setupDocViewer() {
    document.addEventListener("click", (e) => {
      // In-app report (History drawer "Open full report" + Insights links)
      const a = e.target.closest('a[href*="/runs/"][href*="/report"]');
      if (a) {
        const id = runIdFromHref(a.getAttribute("href"));
        if (id) {
          e.preventDefault();
          e.stopImmediatePropagation();
          showReport(id);
          return;
        }
      }
      // In-app logs (drawer "View logs" — app.js binds window.open/navigate
      // on bubble; capture + stopImmediatePropagation pre-empts it).
      const lb = e.target.closest("#drawer-logs-btn");
      if (lb) {
        const sib = (lb.closest(".drawer-actions") || document)
          .querySelector('a[href*="/runs/"][href*="/report"]');
        const id = sib ? runIdFromHref(sib.getAttribute("href")) : null;
        if (id) {
          e.preventDefault();
          e.stopImmediatePropagation();
          showLogs(id);
        }
      }
    }, true); // capture
  }

  // ── Insights: replace the flat status-bar list with two real
  //    time-series charts (success-rate % + run count, bucketed per
  //    day) and add a Y-axis scale to the duration trend. SVG only,
  //    theme-aware via CSS vars. loadInsights() also writes these
  //    elements (timing race), so a MutationObserver re-asserts our
  //    charts whenever they get overwritten — idempotent (our render
  //    is skipped once our marker is present, so no loop).
  let _insRows = null, _insObs = false;
  function _fmtDur(s) {
    s = Math.round(s);
    return s >= 60 ? Math.floor(s / 60) + "m" + (s % 60) + "s" : s + "s";
  }
  function _dayKey(iso) {
    try { return new Date(iso).toISOString().slice(0, 10); } catch (_) { return null; }
  }
  function _buckets(rows) {
    const m = new Map();
    rows.forEach((r) => {
      const k = _dayKey(r.started_at);
      if (!k) return;
      const b = m.get(k) || { total: 0, ok: 0 };
      b.total++;
      if (r.status === "success" || r.status === "ok") b.ok++;
      m.set(k, b);
    });
    return [...m.entries()].sort((a, b) => (a[0] < b[0] ? -1 : 1)).slice(-14);
  }
  // Tiny SVG line+area chart with a 0/mid/max Y scale and end X labels.
  function _lineChart(vals, max, unit, xa, xb, color) {
    const W = 320, H = 96, pl = 34, pr = 8, pt = 8, pb = 18;
    const iw = W - pl - pr, ih = H - pt - pb;
    const n = vals.length;
    const x = (i) => pl + (n <= 1 ? iw / 2 : (i / (n - 1)) * iw);
    const y = (val) => pt + ih - (max ? (val / max) * ih : 0);
    const pts = vals.map((v, i) => x(i) + "," + y(v)).join(" ");
    const area = "M" + x(0) + "," + (pt + ih) + " L" + pts.replace(/ /g, " L") +
      " L" + x(n - 1) + "," + (pt + ih) + " Z";
    const grid = (gv) => '<line x1="' + pl + '" y1="' + y(gv) + '" x2="' + (W - pr) +
      '" y2="' + y(gv) + '" class="rd-chart-grid"/>' +
      '<text x="' + (pl - 5) + '" y="' + (y(gv) + 3) + '" class="rd-chart-yl">' + gv + unit + "</text>";
    return '<svg class="rd-chart" viewBox="0 0 ' + W + " " + H +
      '" preserveAspectRatio="none" role="img">' +
      grid(0) + grid(Math.round(max / 2)) + grid(max) +
      '<path d="' + area + '" class="rd-chart-area" style="fill:' + color + '"/>' +
      '<polyline points="' + pts + '" class="rd-chart-line" style="stroke:' + color + '"/>' +
      '<text x="' + pl + '" y="' + (H - 4) + '" class="rd-chart-xl">' + esc(xa) + "</text>" +
      '<text x="' + (W - pr) + '" y="' + (H - 4) + '" class="rd-chart-xl" text-anchor="end">' + esc(xb) + "</text>" +
      "</svg>";
  }
  function _barChart(items, max, fmt) {
    // items: [{v, color, title}]; Y scale max/mid/0 + bars.
    const W = 320, H = 96, pl = 44, pr = 8, pt = 8, pb = 18;
    const iw = W - pl - pr, ih = H - pt - pb, n = items.length || 1;
    const bw = (iw / n) * 0.7, gap = (iw / n) * 0.3;
    const y = (v) => pt + ih - (max ? (v / max) * ih : 0);
    let s = '<svg class="rd-chart rd-dur" viewBox="0 0 ' + W + " " + H +
      '" preserveAspectRatio="none" role="img">';
    [0, max / 2, max].forEach((gv) => {
      s += '<line x1="' + pl + '" y1="' + y(gv) + '" x2="' + (W - pr) +
        '" y2="' + y(gv) + '" class="rd-chart-grid"/>' +
        '<text x="' + (pl - 6) + '" y="' + (y(gv) + 3) + '" class="rd-chart-yl">' +
        esc(fmt(gv)) + "</text>";
    });
    items.forEach((it, i) => {
      const bx = pl + i * (bw + gap) + gap / 2;
      const by = y(it.v), bh = pt + ih - by;
      s += '<rect x="' + bx.toFixed(1) + '" y="' + by.toFixed(1) + '" width="' +
        bw.toFixed(1) + '" height="' + Math.max(1, bh).toFixed(1) +
        '" rx="2" style="fill:' + it.color + '"><title>' + esc(it.title) +
        "</title></rect>";
    });
    return s + "</svg>";
  }
  function _shortDate(k) { return k ? k.slice(5) : ""; }
  function renderInsCharts() {
    const t = document.getElementById("insights-trends");
    const dEl = document.getElementById("insights-duration");
    if (!t || !dEl || !_insRows || !_insRows.length) return;
    if (!t.querySelector(".rd-chart")) {
      const bk = _buckets(_insRows);
      const xa = _shortDate(bk[0] && bk[0][0]);
      const xb = _shortDate(bk[bk.length - 1] && bk[bk.length - 1][0]);
      const pct = bk.map(([, b]) => (b.total ? Math.round((b.ok / b.total) * 100) : 0));
      const cnt = bk.map(([, b]) => b.total);
      const cmax = Math.max(1, ...cnt);
      t.innerHTML =
        '<div class="rd-chart-block"><div class="rd-chart-cap">' +
        esc(trOr("shell.ins.success_rate", "Success rate")) + "</div>" +
        _lineChart(pct, 100, "%", xa, xb, "var(--ok)") + "</div>" +
        '<div class="rd-chart-block"><div class="rd-chart-cap">' +
        esc(trOr("shell.ins.total_runs", "Runs")) + "</div>" +
        _lineChart(cnt, cmax, "", xa, xb, "var(--accent)") + "</div>";
    }
    if (!dEl.querySelector(".rd-dur")) {
      const last = _insRows.slice(0, 12).reverse();
      const secs = last.map((r) => {
        try { return Math.max(0, (new Date(r.ended_at) - new Date(r.started_at)) / 1000); }
        catch (_) { return 0; }
      });
      const dmax = Math.max(1, ...secs);
      const items = last.map((r, i) => ({
        v: secs[i],
        color: r.status === "failed" ? "var(--err)"
          : r.status === "partial" ? "var(--warn)" : "var(--accent)",
        title: (r.started_at || "") + " · " + _fmtDur(secs[i]),
      }));
      dEl.innerHTML = _barChart(items, dmax, (v) => _fmtDur(v));
    }
  }
  function reorgInsights() {
    const v = document.getElementById("view-insights");
    if (!v || v.classList.contains("hidden")) return;
    if (_insRows) { renderInsCharts(); }
    else {
      fetch("/runs?limit=200")
        .then((r) => (r.ok ? r.json() : { runs: [] }))
        .then((d) => { _insRows = d.runs || []; renderInsCharts(); })
        .catch(() => {});
    }
    if (!_insObs) {
      const t = document.getElementById("insights-trends");
      const dEl = document.getElementById("insights-duration");
      if (t && dEl) {
        const mo = new MutationObserver(() => renderInsCharts());
        mo.observe(t, { childList: true });
        mo.observe(dEl, { childList: true });
        _insObs = true;
      }
    }
  }

  // Registry of per-view reorgs. Each is idempotent.
  const REORGS = [
    reorgTools, reorgSettings, reorgRunsFilter, reorgOverview, reorgLibrary,
    reorgRunCenter, reorgProfilesPanel, reorgScheduleForm, reorgInsights,
  ];

  function runAll() {
    REORGS.forEach((fn) => {
      try { fn(); } catch (e) { /* never break the app on a reorg */ }
    });
  }

  function init() {
    setupDocViewer(); // in-app report/log modal (event-driven, view-agnostic)
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
