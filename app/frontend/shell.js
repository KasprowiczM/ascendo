// ============================================================================
// shell.js — AppShell: 5-destination IA, header, sub-tabs, Preferences,
//            run-detail drawer. Loaded AFTER app.js.
// ============================================================================
// Design intent: this is a presentation/navigation LAYER on top of the
// existing working router. It does NOT rewrite any view loader or the
// SSE/runs/AI wiring. The single integration point is wrapping
// `ui.show(view)` so the chrome (sidebar active state, page header,
// sub-tab rail, deep-link hash) reacts to whatever the rest of the app
// already does. Deep-link routes (`#runs/history`) resolve to the real
// underlying view id before the original router runs, so nothing
// downstream needs to know the IA exists.
// ============================================================================
(function () {
  "use strict";

  function trOr(key, fallback) {
    if (typeof window.tr === "function") {
      const v = window.tr(key);
      if (v && v !== key) return v;
    }
    return fallback != null ? fallback : key;
  }

  // ── Information architecture ──────────────────────────────────────────
  // Each destination owns one user question. `view` on a tab is the
  // existing #view-<id> the original router already knows how to render.
  // `feature` (optional) gates a tab by Platform capability. `edition`
  // (optional) hides a tab outside the matching edition.
  const IA = {
    dashboard: {
      icon: "overview",
      titleKey: "shell.dashboard.title", descKey: "shell.dashboard.desc",
      cta: { labelKey: "shell.dashboard.cta", action: "startRun" },
      defaultView: "overview",
      tabs: null, // single page — no sub-tabs
    },
    library: {
      icon: "apps",
      titleKey: "shell.library.title", descKey: "shell.library.desc",
      cta: { labelKey: "shell.library.cta", action: "library" },
      defaultView: "categories",
      tabs: [
        { id: "sources", labelKey: "shell.tabs.sources", view: "categories" },
        { id: "apps",    labelKey: "shell.tabs.apps",    view: "apps" },
        { id: "tools",   labelKey: "shell.tabs.tools",   view: "suggest" },
      ],
    },
    runs: {
      icon: "run",
      titleKey: "shell.runs.title", descKey: "shell.runs.desc",
      cta: { labelKey: "shell.runs.cta", action: "startRun" },
      defaultView: "run",
      tabs: [
        { id: "start",     labelKey: "shell.tabs.start",     view: "run" },
        { id: "scheduled", labelKey: "shell.tabs.scheduled", view: "schedule" },
        { id: "history",   labelKey: "shell.tabs.history",   view: "history" },
      ],
    },
    insights: {
      icon: "history",
      titleKey: "shell.insights.title", descKey: "shell.insights.desc",
      cta: { labelKey: "shell.insights.cta", action: "latestReport" },
      defaultView: "insights",
      tabs: [
        { id: "trends", labelKey: "shell.tabs.trends", view: "insights" },
        { id: "logs",   labelKey: "shell.tabs.logs",   view: "logs",
          edition: "dev" },
      ],
    },
    settings: {
      icon: "settings",
      titleKey: "shell.settings.title", descKey: "shell.settings.desc",
      cta: null,
      defaultView: "settings",
      tabs: [
        { id: "general",      labelKey: "shell.tabs.general",      view: "settings" },
        { id: "integrations", labelKey: "shell.tabs.integrations", view: "hosts",
          edition: "dev" },
        { id: "sync",         labelKey: "shell.tabs.sync",         view: "sync",
          edition: "dev" },
        { id: "support",      labelKey: "shell.tabs.support",      view: "help" },
        { id: "about",        labelKey: "shell.tabs.about",        view: "about" },
      ],
    },
  };

  // Reverse map: underlying view id → { dest, tabId }. Built once.
  const VIEW_TO_ROUTE = {};
  Object.keys(IA).forEach((dest) => {
    const d = IA[dest];
    if (!d.tabs) { VIEW_TO_ROUTE[d.defaultView] = { dest, tab: null }; return; }
    d.tabs.forEach((t) => { VIEW_TO_ROUTE[t.view] = { dest, tab: t.id }; });
  });
  // Views the original router still has but that fold into a destination
  // without their own tab (run-detail uses logs view; about/help live in
  // Settings already mapped above).
  if (!VIEW_TO_ROUTE.overview) VIEW_TO_ROUTE.overview = { dest: "dashboard", tab: null };

  function edition() { return window.ASCENDO_EDITION || "basic"; }

  function tabAllowed(t) {
    if (t.edition && t.edition !== edition()) return false;
    if (t.feature && window.Platform && !window.Platform.allow(t.feature)) return false;
    return true;
  }

  // Resolve any nav input into a concrete underlying view + chrome route.
  // Accepts: real view id ("history"), a destination ("runs"),
  // a deep route ("runs/history"), or unknown → dashboard.
  function resolveRoute(input) {
    const raw = String(input || "").replace(/^#/, "");
    const [head, sub] = raw.split("/");

    // Deep route: dest/tab
    if (IA[head]) {
      const d = IA[head];
      if (sub && d.tabs) {
        const t = d.tabs.find((x) => x.id === sub && tabAllowed(x));
        if (t) return { view: t.view, dest: head, tab: t.id };
      }
      // Destination only → its default (first allowed) tab.
      if (d.tabs) {
        const first = d.tabs.find(tabAllowed) || d.tabs[0];
        return { view: first.view, dest: head, tab: first.id };
      }
      return { view: d.defaultView, dest: head, tab: null };
    }

    // Real underlying view id (old bookmarks: #history, #apps, …)
    if (VIEW_TO_ROUTE[raw]) {
      const r = VIEW_TO_ROUTE[raw];
      return { view: raw, dest: r.dest, tab: r.tab };
    }

    // Unknown → Dashboard.
    return { view: "overview", dest: "dashboard", tab: null };
  }

  const shell = {
    _suppressHash: false,
    _current: { dest: "dashboard", tab: null, view: "overview" },

    // Called after the original ui.show() ran with a real view id.
    renderChrome(dest, tab, view) {
      this._current = { dest, tab, view };
      this.paintSidebar(dest);
      this.paintHeader(dest, tab);
      this.paintStatus();
      // Platform/feature gating on the now-visible view.
      if (window.Platform) {
        const v = document.getElementById("view-" + view);
        if (v) window.Platform.applyTo(v);
      }
      // Insights is shell-owned data; lazy-load on first visit.
      if (view === "insights" && window.ui && typeof ui.loadInsights === "function"
          && !ui._loaded?.insights) {
        ui._loaded = ui._loaded || {};
        ui._loaded.insights = true;
        ui.loadInsights();
      }
    },

    paintSidebar(dest) {
      document.querySelectorAll("#nav .nav-link").forEach((a) => {
        a.classList.toggle("active", a.dataset.dest === dest);
        if (a.dataset.dest === dest) a.setAttribute("aria-current", "page");
        else a.removeAttribute("aria-current");
      });
    },

    paintHeader(dest, tab) {
      const d = IA[dest] || IA.dashboard;
      const titleEl = document.getElementById("app-header-title");
      const descEl = document.getElementById("app-header-desc");
      if (titleEl) titleEl.textContent = trOr(d.titleKey, dest);
      if (descEl) descEl.textContent = trOr(d.descKey, "");

      // Primary CTA — one strong action per screen.
      const ctaEl = document.getElementById("app-header-cta");
      if (ctaEl) {
        if (d.cta) {
          ctaEl.hidden = false;
          ctaEl.textContent = trOr(d.cta.labelKey, "");
          ctaEl.onclick = () => shell.doAction(d.cta.action);
        } else {
          ctaEl.hidden = true;
          ctaEl.onclick = null;
        }
      }

      // Sub-tab rail.
      const railEl = document.getElementById("app-subtabs");
      if (!railEl) return;
      const tabs = (d.tabs || []).filter(tabAllowed);
      if (tabs.length < 2) {
        railEl.hidden = true;
        railEl.innerHTML = "";
        return;
      }
      railEl.hidden = false;
      railEl.innerHTML = "";
      tabs.forEach((t) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "app-subtab" + (t.id === tab ? " active" : "");
        btn.setAttribute("role", "tab");
        btn.setAttribute("aria-selected", t.id === tab ? "true" : "false");
        btn.textContent = trOr(t.labelKey, t.id);
        btn.addEventListener("click", () => {
          if (window.ui) ui.show(dest + "/" + t.id);
        });
        railEl.appendChild(btn);
      });
    },

    paintStatus() {
      const el = document.getElementById("platform-status");
      if (!el || !window.Platform) return;
      const P = window.Platform;
      if (!P.isKnown()) { el.textContent = ""; return; }
      el.innerHTML = "";
      const dot = document.createElement("span");
      dot.className = "platform-status-dot";
      const label = document.createElement("span");
      label.textContent = P.label + " · " + P.elevationTerm;
      el.appendChild(dot);
      el.appendChild(label);
      el.title = P.copy("ops_note", "");
    },

    doAction(action) {
      if (!window.ui) return;
      if (action === "startRun") { ui.show("runs/start"); return; }
      if (action === "library") { ui.show("library/sources"); return; }
      if (action === "latestReport") {
        fetch("/runs?limit=1").then((r) => r.json()).then((d) => {
          const run = (d.runs || [])[0];
          if (run && run.id) window.open("/runs/" + encodeURIComponent(run.id) + "/report", "_blank", "noopener");
          else ui.show("runs/history");
        }).catch(() => ui.show("runs/history"));
        return;
      }
    },

    // ── Preferences popover ─────────────────────────────────────────────
    bindPrefs() {
      const toggle = document.getElementById("prefs-toggle");
      const menu = document.getElementById("prefs-menu");
      if (!toggle || !menu) return;
      const icon = toggle.querySelector(".prefs-toggle-icon");
      if (icon && window.ICONS && window.ICONS.settings) {
        icon.innerHTML = window.ICONS.settings;
      }
      const close = () => {
        menu.hidden = true;
        toggle.setAttribute("aria-expanded", "false");
      };
      const open = () => {
        menu.hidden = false;
        toggle.setAttribute("aria-expanded", "true");
        const first = menu.querySelector("button");
        if (first) first.focus();
      };
      toggle.addEventListener("click", (e) => {
        e.stopPropagation();
        menu.hidden ? open() : close();
      });
      document.addEventListener("click", (e) => {
        if (menu.hidden) return;
        if (!menu.contains(e.target) && e.target !== toggle) close();
      });
      document.addEventListener("keydown", (e) => {
        if (e.key === "Escape" && !menu.hidden) { close(); toggle.focus(); }
      });
    },

    // ── Run-detail drawer ───────────────────────────────────────────────
    bindDrawer() {
      const drawer = document.getElementById("run-drawer");
      if (!drawer) return;
      drawer.querySelectorAll("[data-drawer-close]").forEach((el) => {
        el.addEventListener("click", () => shell.closeDrawer());
      });
      document.addEventListener("keydown", (e) => {
        if (e.key === "Escape" && !drawer.classList.contains("hidden")) {
          shell.closeDrawer();
        }
      });
    },

    openDrawer(html) {
      const drawer = document.getElementById("run-drawer");
      const body = document.getElementById("run-drawer-body");
      if (!drawer || !body) return;
      body.innerHTML = html;
      if (window.applyI18n) window.applyI18n(drawer);
      drawer.classList.remove("hidden");
      const panel = drawer.querySelector(".drawer-panel");
      if (panel) panel.focus();
    },

    closeDrawer() {
      const drawer = document.getElementById("run-drawer");
      if (drawer) drawer.classList.add("hidden");
    },

    // ── Router integration ──────────────────────────────────────────────
    patchRouter() {
      if (!window.ui || typeof ui.show !== "function") return;
      const orig = ui.show.bind(ui);

      ui.show = function (input) {
        const { view, dest, tab } = resolveRoute(input);
        orig(view);                       // original router does the real work
        // Rewrite hash to the shareable dest[/tab] form without recursing.
        const wanted = tab ? "#" + dest + "/" + tab : "#" + dest;
        if (location.hash !== wanted) {
          shell._suppressHash = true;
          location.hash = wanted;
          setTimeout(() => { shell._suppressHash = false; }, 0);
        }
        shell.renderChrome(dest, tab, view);
      };

      // Deep-link / back-forward support.
      window.addEventListener("hashchange", () => {
        if (shell._suppressHash) return;
        const r = resolveRoute(location.hash);
        ui.show(r.tab ? r.dest + "/" + r.tab : r.dest);
      });

      // Let bootstrap's `location.hash.replace('#','') || 'overview'`
      // still produce a valid view: our wrapped ui.show resolves any
      // dest/route input, so no app.js edit is needed.
    },

    init() {
      this.patchRouter();
      this.bindPrefs();
      this.bindDrawer();
      if (window.Platform) window.Platform.applyTo(document);
      // Paint chrome for whatever view is active right now (bootstrap may
      // have already called ui.show before we patched, or not yet —
      // resolve from the current hash either way).
      const r = resolveRoute(location.hash || "#dashboard");
      this.renderChrome(r.dest, r.tab, r.view);
      if (window.applyI18n) window.applyI18n(document.getElementById("app-header"));
    },
  };

  window.shell = shell;

  // Scripts sit at end of <body>, so the DOM is parsed. app.js has run
  // (its module body executed; bootstrap() is async and still awaiting
  // /settings + /version), so ui.show exists and is safe to wrap now.
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => shell.init());
  } else {
    shell.init();
  }
})();
