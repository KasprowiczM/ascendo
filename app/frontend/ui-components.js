// ============================================================================
// ui-components.js — touch-first UI kit (Sesja 74)
// ============================================================================
// Progressive-enhancement layer loaded AFTER shell.js. It rewrites desktop-
// leaning patterns into mobile/touch-first ones WITHOUT touching the working
// router, the run/SSE wiring, or any form-handling logic in app.js:
//
//   • Every native <select> is replaced by an accessible, no-dropdown
//     control (SegmentedControl / ChoiceCardGroup / searchable list). The
//     original <select> stays in the DOM, visually hidden and value-synced,
//     so `new FormData(form)`, `.value`, `.options.length` and existing
//     `change` listeners keep working byte-for-byte.
//   • MobileBottomNav — a 5-destination tab bar (≤768px) wired to ui.show.
//   • The Run Center form becomes a 3-step progressive reveal
//     (Profile → Options → Confirm) with a sticky mobile action bar.
//   • The History table degrades into stacked, tappable run cards on mobile.
//
// Defensive throughout: every enhancer feature-detects its target and bails
// silently if the DOM is not the shape it expects — a working app is never
// allowed to break because the kit could not enhance something.
// ============================================================================
(function () {
  "use strict";

  var D = document;

  function trOr(key, fb) {
    if (typeof window.tr === "function") {
      var v = window.tr(key);
      if (v && v !== key) return v;
    }
    return fb != null ? fb : key;
  }

  function el(tag, cls, attrs) {
    var n = D.createElement(tag);
    if (cls) n.className = cls;
    if (attrs) Object.keys(attrs).forEach(function (k) {
      if (k === "text") n.textContent = attrs[k];
      else n.setAttribute(k, attrs[k]);
    });
    return n;
  }

  // ── Select → no-dropdown control ──────────────────────────────────────
  var UPG = "data-uikit-upgraded";

  function readOptions(sel) {
    return Array.prototype.map.call(sel.options, function (o) {
      return {
        value: o.value,
        label: (o.textContent || o.value || "").trim(),
        desc: o.getAttribute("data-desc") || "",
        selected: o.selected,
        disabled: o.disabled,
      };
    });
  }

  function variantFor(sel, opts) {
    var forced = sel.getAttribute("data-uikit");
    if (forced === "segmented" || forced === "cards" || forced === "list") {
      return forced;
    }
    if (opts.length > 8) return "list";
    var longest = opts.reduce(function (m, o) {
      return Math.max(m, o.label.length);
    }, 0);
    var hasDesc = opts.some(function (o) { return !!o.desc; });
    if (!hasDesc && opts.length <= 4 && longest <= 16) return "segmented";
    return "cards";
  }

  // Accessible label text for the group (associated <label>, aria-label,
  // or a humanised name attribute).
  function groupLabel(sel) {
    var lbl = sel.closest("label");
    if (lbl) {
      var span = lbl.querySelector("span");
      if (span && span.textContent.trim()) return span.textContent.trim();
    }
    if (sel.getAttribute("aria-label")) return sel.getAttribute("aria-label");
    var id = sel.id || sel.name || "";
    return id.replace(/[-_]/g, " ").replace(/\bselect\b/i, "").trim() || "Choose";
  }

  function commit(sel, value) {
    if (sel.value === value) return;
    sel.value = value;
    sel.dispatchEvent(new Event("input", { bubbles: true }));
    sel.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function buildControl(sel) {
    var opts = readOptions(sel);
    if (!opts.length) return null;
    var variant = variantFor(sel, opts);
    var current = sel.value;
    var wrap = el("div", "u-choice u-choice--" + variant, {
      role: "radiogroup",
      "aria-label": groupLabel(sel),
    });

    function makeOption(o, parentList) {
      var btn = el("button", "u-opt", { type: "button", role: "radio" });
      btn.dataset.value = o.value;
      var checked = o.value === current;
      btn.setAttribute("aria-checked", checked ? "true" : "false");
      btn.tabIndex = checked ? 0 : -1;
      if (o.disabled) { btn.disabled = true; btn.tabIndex = -1; }
      var lab = el("span", "u-opt-label", { text: o.label || "—" });
      btn.appendChild(lab);
      if (o.desc) btn.appendChild(el("span", "u-opt-desc", { text: o.desc }));
      btn.addEventListener("click", function () {
        if (o.disabled) return;
        commit(sel, o.value);
        select(o.value);
      });
      parentList.appendChild(btn);
      return btn;
    }

    var optionEls = [];

    function select(value) {
      current = value;
      optionEls.forEach(function (b) {
        var on = b.dataset.value === value;
        b.classList.toggle("is-selected", on);
        b.setAttribute("aria-checked", on ? "true" : "false");
        b.tabIndex = on ? 0 : (b.disabled ? -1 : -1);
      });
      var sel0 = optionEls.filter(function (b) {
        return b.dataset.value === value && !b.disabled;
      })[0];
      if (sel0) sel0.tabIndex = 0;
      else if (optionEls[0]) optionEls[0].tabIndex = 0;
    }

    // Roving-tabindex keyboard model for the radio group.
    function onKey(e) {
      var idx = optionEls.indexOf(e.target);
      if (idx < 0) return;
      var dir = 0;
      if (e.key === "ArrowRight" || e.key === "ArrowDown") dir = 1;
      else if (e.key === "ArrowLeft" || e.key === "ArrowUp") dir = -1;
      else if (e.key === "Home") { focusIdx(0); e.preventDefault(); return; }
      else if (e.key === "End") { focusIdx(optionEls.length - 1); e.preventDefault(); return; }
      else if (e.key === " " || e.key === "Enter") {
        e.preventDefault(); e.target.click(); return;
      } else return;
      e.preventDefault();
      var n = optionEls.length, i = idx;
      for (var step = 0; step < n; step++) {
        i = (i + dir + n) % n;
        if (!optionEls[i].disabled) break;
      }
      focusIdx(i);
    }
    function focusIdx(i) {
      var b = optionEls[i];
      if (!b) return;
      commit(sel, b.dataset.value);
      select(b.dataset.value);
      b.focus();
    }

    if (variant === "list") {
      // Searchable, progressively-disclosed list. Avoids a nested scroll
      // region by capping visible rows + "show more" instead of an inner
      // scrollbar (one scroll region per page, per the UX contract).
      var picked = el("div", "u-list-current");
      var pickedLabel = el("span", "u-list-current-label");
      var changeBtn = el("button", "u-list-change", {
        type: "button", text: trOr("uikit.change", "Change"),
      });
      picked.appendChild(pickedLabel);
      picked.appendChild(changeBtn);

      var panel = el("div", "u-list-panel");
      var search = el("input", "u-list-search", {
        type: "search", autocomplete: "off",
        placeholder: trOr("uikit.search", "Search…"),
        "aria-label": groupLabel(sel),
      });
      var listWrap = el("div", "u-list-items", { role: "listbox" });
      var moreBtn = el("button", "u-list-more secondary", {
        type: "button", text: trOr("uikit.show_more", "Show more"),
      });
      panel.appendChild(search);
      panel.appendChild(listWrap);
      panel.appendChild(moreBtn);
      wrap.appendChild(picked);
      wrap.appendChild(panel);

      var CAP = 10, expanded = false;
      function curLabel() {
        var hit = opts.filter(function (o) { return o.value === current; })[0];
        return hit ? hit.label : (opts[0] ? opts[0].label : "—");
      }
      function paint() {
        var q = (search.value || "").toLowerCase();
        var filtered = opts.filter(function (o) {
          return !q || o.label.toLowerCase().indexOf(q) !== -1;
        });
        var shown = expanded ? filtered : filtered.slice(0, CAP);
        listWrap.innerHTML = "";
        optionEls = [];
        shown.forEach(function (o) {
          var b = makeOption(o, listWrap);
          b.classList.add("u-opt--row");
          b.setAttribute("role", "option");
          b.addEventListener("click", function () { collapse(); });
          optionEls.push(b);
        });
        select(current);
        moreBtn.hidden = expanded || filtered.length <= CAP;
        moreBtn.textContent = trOr("uikit.show_more", "Show more") +
          " (" + Math.max(0, filtered.length - CAP) + ")";
      }
      function expand() {
        wrap.classList.add("is-open");
        picked.hidden = true; panel.hidden = false;
        paint(); search.focus();
      }
      function collapse() {
        wrap.classList.remove("is-open");
        pickedLabel.textContent = curLabel();
        picked.hidden = false; panel.hidden = true;
      }
      changeBtn.addEventListener("click", expand);
      search.addEventListener("input", function () { expanded = false; paint(); });
      moreBtn.addEventListener("click", function () { expanded = true; paint(); });
      listWrap.addEventListener("keydown", onKey);
      collapse();
      return wrap;
    }

    // segmented / cards: every option rendered as a tap target.
    opts.forEach(function (o) { optionEls.push(makeOption(o, wrap)); });
    select(current);
    wrap.addEventListener("keydown", onKey);
    return wrap;
  }

  function upgradeSelect(sel) {
    if (!sel || sel.multiple) return;
    if (sel.closest("[data-uikit-skip]")) return;
    var control = buildControl(sel);
    if (!control) return;

    // Tear down a previous upgrade (dynamic <option> repopulation).
    if (sel.getAttribute(UPG) === "1" && sel._uikitControl) {
      sel._uikitControl.replaceWith(control);
    } else {
      sel.classList.add("u-native");
      sel.setAttribute("tabindex", "-1");
      sel.setAttribute("aria-hidden", "true");
      sel.insertAdjacentElement("afterend", control);
    }
    sel._uikitControl = control;
    sel.setAttribute(UPG, "1");

    // Re-render when the app repopulates options (profile/only/logs are
    // filled lazily by app.js after a fetch).
    if (!sel._uikitMO) {
      sel._uikitMO = new MutationObserver(function () {
        clearTimeout(sel._uikitT);
        sel._uikitT = setTimeout(function () { upgradeSelect(sel); }, 30);
      });
      sel._uikitMO.observe(sel, { childList: true });
      // Keep the control in sync if code sets `.value` directly.
      sel.addEventListener("change", function () {
        if (!sel._uikitControl) return;
        sel._uikitControl.querySelectorAll(".u-opt").forEach(function (b) {
          var on = b.dataset.value === sel.value;
          b.classList.toggle("is-selected", on);
          b.setAttribute("aria-checked", on ? "true" : "false");
        });
      });
    }
  }

  function upgradeAllSelects(root) {
    (root || D).querySelectorAll("select").forEach(upgradeSelect);
  }

  // ── MobileBottomNav ───────────────────────────────────────────────────
  function mountBottomNav() {
    if (D.getElementById("bottom-nav")) return;
    var src = D.querySelectorAll("#nav .nav-link");
    if (!src.length) return;
    var nav = el("nav", "bottom-nav", {
      id: "bottom-nav", "aria-label": trOr("uikit.sections", "Sections"),
    });
    src.forEach(function (a) {
      var b = el("button", "bottom-nav-btn", { type: "button" });
      b.dataset.dest = a.dataset.dest || "";
      b.dataset.view = a.dataset.view || "";
      var ic = a.querySelector(".nav-icon");
      var icon = el("span", "bottom-nav-icon");
      icon.setAttribute("aria-hidden", "true");
      if (ic) icon.innerHTML = ic.innerHTML;
      var lab = a.querySelector(".nav-label");
      var label = el("span", "bottom-nav-label", {
        text: lab ? lab.textContent.trim() : (a.dataset.dest || ""),
      });
      b.appendChild(icon);
      b.appendChild(label);
      b.addEventListener("click", function () {
        if (window.ui && typeof window.ui.show === "function") {
          window.ui.show(b.dataset.dest || b.dataset.view);
        }
      });
      nav.appendChild(b);
    });
    D.body.appendChild(nav);
    syncBottomNav();
    // Mirror the sidebar active state (shell.js toggles .active on #nav).
    var navHost = D.getElementById("nav");
    if (navHost) {
      new MutationObserver(syncBottomNav).observe(navHost, {
        subtree: true, attributes: true, attributeFilter: ["class"],
      });
    }
    window.addEventListener("hashchange", syncBottomNav);
  }

  function syncBottomNav() {
    var active = D.querySelector("#nav .nav-link.active");
    var dest = active ? active.dataset.dest : "";
    D.querySelectorAll(".bottom-nav-btn").forEach(function (b) {
      var on = b.dataset.dest === dest;
      b.classList.toggle("is-active", on);
      if (on) b.setAttribute("aria-current", "page");
      else b.removeAttribute("aria-current");
    });
  }

  // ── Run Center → 3-step progressive reveal ────────────────────────────
  function enhanceRunForm() {
    var form = D.getElementById("run-form");
    if (!form || form.dataset.uikitStepper === "1") return;
    var labels = Array.prototype.slice.call(form.children).filter(function (c) {
      return c.tagName === "LABEL";
    });
    var submit = form.querySelector('button[type="submit"]');
    var stop = D.getElementById("stop-btn");
    var profileLbl = labelContaining(form, 'select[name="profile"]');
    if (!profileLbl || !submit) return; // shape unexpected → leave as-is

    var others = labels.filter(function (l) { return l !== profileLbl; });

    var s1 = el("div", "u-step is-active", { "data-step": "1" });
    var s2 = el("div", "u-step is-locked", { "data-step": "2" });
    var s3 = el("div", "u-step is-locked", { "data-step": "3" });
    s1.appendChild(stepHead(1, trOr("uikit.run.s1", "Profile")));
    s2.appendChild(stepHead(2, trOr("uikit.run.s2", "Options")));
    s3.appendChild(stepHead(3, trOr("uikit.run.s3", "Confirm & run")));

    s1.appendChild(profileLbl);
    var cont1 = el("button", "u-step-next", {
      type: "button", text: trOr("uikit.continue", "Continue"),
    });
    s1.appendChild(cont1);

    others.forEach(function (l) { s2.appendChild(l); });
    var cont2 = el("button", "u-step-next", {
      type: "button", text: trOr("uikit.continue", "Continue"),
    });
    s2.appendChild(cont2);

    var summary = el("p", "u-run-summary dim");
    s3.appendChild(summary);
    var bar = el("div", "u-action-bar");
    bar.appendChild(submit);
    if (stop) bar.appendChild(stop);
    s3.appendChild(bar);

    form.appendChild(s1);
    form.appendChild(s2);
    form.appendChild(s3);
    form.dataset.uikitStepper = "1";

    function reveal(step) {
      [s1, s2, s3].forEach(function (s, i) {
        var n = i + 1;
        s.classList.toggle("is-active", n === step);
        s.classList.toggle("is-locked", n > step);
        s.classList.toggle("is-done", n < step);
      });
      if (step === 3) summary.textContent = summarise();
      var target = step === 1 ? s1 : step === 2 ? s2 : s3;
      try { target.scrollIntoView({ block: "nearest", behavior: "smooth" }); }
      catch (_) {}
    }
    function valOf(name) {
      var c = form.querySelector('[name="' + name + '"]');
      if (!c) return "";
      if (c.type === "checkbox") return c.checked;
      return c.value;
    }
    function summarise() {
      var p = valOf("profile") || trOr("uikit.run.default", "default");
      var only = valOf("only");
      var phase = valOf("phase");
      var dry = valOf("dry_run");
      var parts = [trOr("uikit.run.s1", "Profile") + ": " + p];
      parts.push(trOr("uikit.run.scope", "Scope") + ": " +
        (only || trOr("uikit.run.all", "all categories")));
      parts.push(trOr("uikit.run.phase", "Phase") + ": " +
        (phase || trOr("uikit.run.profile_default", "profile default")));
      if (dry) parts.push(trOr("uikit.run.dry", "dry-run"));
      return parts.join("  ·  ");
    }
    cont1.addEventListener("click", function () { reveal(2); });
    cont2.addEventListener("click", function () { reveal(3); });
    form.addEventListener("change", function () {
      if (s3.classList.contains("is-active")) summary.textContent = summarise();
    });
    // Header chips let the user jump back to an earlier step.
    [s1, s2, s3].forEach(function (s, i) {
      s.querySelector(".u-step-head").addEventListener("click", function () {
        if (!s.classList.contains("is-locked")) reveal(i + 1);
      });
    });
    reveal(1);

    // A run can become active without the user stepping through the form
    // (Overview quick actions post directly; loadRunCenter re-attaches a
    // running stream on view entry). app.js flips #stop-btn from disabled
    // → enabled whenever a run is live. The Stop control lives in the
    // confirm step, which the progressive-disclosure CSS collapses while
    // step 1/2 is active — so without this it would be unreachable
    // mid-run. Force-reveal the confirm step the instant a run is active
    // so Stop is always accessible. (Progressive disclosure is preserved
    // for the manual setup path.)
    if (stop) {
      var ensureStopVisible = function () {
        if (!stop.disabled && !s3.classList.contains("is-active")) reveal(3);
      };
      ensureStopVisible(); // run may already be live on first enhance
      new MutationObserver(ensureStopVisible).observe(stop, {
        attributes: true, attributeFilter: ["disabled"],
      });
    }
  }

  function labelContaining(form, selector) {
    var c = form.querySelector(selector);
    return c ? c.closest("label") : null;
  }
  function stepHead(n, text) {
    var h = el("div", "u-step-head", { role: "button", tabindex: "0" });
    h.appendChild(el("span", "u-step-num", { text: String(n) }));
    h.appendChild(el("span", "u-step-title", { text: text }));
    h.addEventListener("keydown", function (e) {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); h.click(); }
    });
    return h;
  }

  // ── History table → tappable mobile cards ─────────────────────────────
  function enhanceHistory() {
    var tbl = D.getElementById("history-table");
    if (!tbl) return;
    var ths = Array.prototype.map.call(tbl.querySelectorAll("thead th"),
      function (th) { return th.textContent.trim(); });
    var body = tbl.querySelector("tbody");
    if (!body) return;

    function decorate() {
      tbl.classList.add("u-cardtable");
      body.querySelectorAll("tr").forEach(function (tr) {
        Array.prototype.forEach.call(tr.children, function (td, i) {
          if (ths[i]) td.setAttribute("data-label", ths[i]);
        });
        if (tr.dataset.uikitRow === "1") return;
        tr.dataset.uikitRow = "1";
        tr.tabIndex = 0;
        tr.setAttribute("role", "button");
        tr.setAttribute("aria-label",
          trOr("uikit.open_run", "Open run details"));
        var open = function (e) {
          if (e.target.closest("button,a")) return; // keep inner buttons
          var btn = tr.querySelector(".history-details-btn");
          if (btn) btn.click();
        };
        tr.addEventListener("click", open);
        tr.addEventListener("keydown", function (e) {
          if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(e); }
        });
      });
    }
    decorate();
    if (!body._uikitMO) {
      body._uikitMO = new MutationObserver(function () {
        clearTimeout(body._uikitT);
        body._uikitT = setTimeout(decorate, 20);
      });
      body._uikitMO.observe(body, { childList: true });
    }
  }

  // ── Orchestration ─────────────────────────────────────────────────────
  function enhanceAll() {
    upgradeAllSelects(D);
    enhanceRunForm();
    enhanceHistory();
  }

  function init() {
    mountBottomNav();
    enhanceAll();

    // Re-enhance after every route change (views are lazy-rendered, so
    // selects/history rows can appear after init).
    if (window.ui && typeof window.ui.show === "function") {
      var origShow = window.ui.show.bind(window.ui);
      window.ui.show = function (v) {
        var r = origShow(v);
        setTimeout(enhanceAll, 0);
        return r;
      };
    }
    // Language switch re-renders i18n text; rebuild controls so the
    // visible labels follow the new locale.
    if (typeof window.applyI18n === "function") {
      var origI18n = window.applyI18n;
      window.applyI18n = function (root) {
        var r = origI18n(root);
        D.querySelectorAll("select[" + UPG + '="1"]').forEach(upgradeSelect);
        return r;
      };
    }
    // Catch any <select> injected later by a fetch-driven render.
    var main = D.querySelector("main");
    if (main) {
      new MutationObserver(function (muts) {
        var hit = false;
        muts.forEach(function (m) {
          m.addedNodes && m.addedNodes.forEach(function (n) {
            if (n.nodeType === 1 &&
                (n.tagName === "SELECT" || n.querySelector &&
                 n.querySelector("select"))) hit = true;
          });
        });
        if (hit) { clearTimeout(window._uikitGT);
          window._uikitGT = setTimeout(function () { upgradeAllSelects(main); }, 40); }
      }).observe(main, { childList: true, subtree: true });
    }
  }

  window.Uikit = {
    upgradeSelect: upgradeSelect,
    upgradeAllSelects: upgradeAllSelects,
    enhanceAll: enhanceAll,
  };

  if (D.readyState === "loading") {
    D.addEventListener("DOMContentLoaded", function () { setTimeout(init, 0); });
  } else {
    setTimeout(init, 0);
  }
})();
