// ============================================================================
// shell.js — AppShell: OWNS the route. 5-destination IA, header, sub-tabs,
//            Preferences popover, mobile bottom nav, run-detail drawer.
//            Loaded `<script defer src="/shell.js">` AFTER app.js, BEFORE
//            ui-components.js.
// ============================================================================
// Ownership model (changed from the old monkey-patch wrapper):
//
//   * app.js still defines the *core view renderer* (hide `.view`, unhide
//     `#view-<id>`, run the lazy-load flags, set sidebar-help). That logic
//     is the single source of truth for "make a view visible" and we do
//     NOT duplicate it.
//   * The shell captures that core renderer ONCE as a private delegate
//     (`coreShow`) and then defines `window.ui.show` EXACTLY ONCE as the
//     shell router. There is no chain of wrappers: the rest of app.js
//     keeps calling `ui.show(...)`, and that single definition resolves
//     the IA route, asks the core renderer to paint the real view, then
//     paints the chrome. One definition, one hashchange listener.
//   * No shadow renderer. There is no second fetch, no MutationObserver,
//     and no dependency on ui-redesign.js. Insights lazy-load goes through
//     the existing `ui.loadInsights` path only.
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
  // existing #view-<id> the core renderer already knows how to render.
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

  // Reverse map: underlying view id → { dest, tab }. Built once.
  const VIEW_TO_ROUTE = {};
  Object.keys(IA).forEach((dest) => {
    const d = IA[dest];
    if (!d.tabs) { VIEW_TO_ROUTE[d.defaultView] = { dest, tab: null }; return; }
    d.tabs.forEach((t) => { VIEW_TO_ROUTE[t.view] = { dest, tab: t.id }; });
  });
  // Views the core renderer still has but that fold into a destination
  // without their own tab (overview is the dashboard default).
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
    // The core view renderer captured from app.js's `ui.show`. Set ONCE in
    // ownRoute(); never reassigned, never chained.
    _coreShow: null,

    // ── Chrome painting ─────────────────────────────────────────────────
    renderChrome(dest, tab, view) {
      this._current = { dest, tab, view };
      this.paintSidebar(dest);
      this.paintHeader(dest, tab);
      this.paintBottomNav(dest);
      this.paintStatus();
      // Platform/feature gating on the now-visible view.
      if (window.Platform) {
        const v = document.getElementById("view-" + view);
        if (v) window.Platform.applyTo(v);
      }
      // Insights is shell-owned data; lazy-load on first visit through the
      // EXISTING single data path. No second fetch, no observer.
      if (view === "insights" && window.ui && typeof ui.loadInsights === "function"
          && !(ui._loaded && ui._loaded.insights)) {
        ui._loaded = ui._loaded || {};
        ui._loaded.insights = true;
        ui.loadInsights();
      }
    },

    paintSidebar(dest) {
      document.querySelectorAll("#nav .nav-link").forEach((a) => {
        const on = a.dataset.dest === dest;
        a.classList.toggle("active", on);
        if (on) a.setAttribute("aria-current", "page");
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

    // ── Mobile bottom nav ───────────────────────────────────────────────
    // Absorbed from ui-components.js. The legacy bug was that the nav was
    // appended somewhere that became a containing block for `position:
    // fixed` (a transformed / `contain`/`filter` ancestor), so it floated
    // mid-page. Fix: attach it as a DIRECT CHILD of <body> (which is never
    // a fixed-positioning containing block) and add an inline fallback so
    // even if CSS regresses it still pins to the viewport bottom. Class
    // names preserved so existing `.bottom-nav` rules in style.css apply.
    mountBottomNav() {
      if (document.getElementById("bottom-nav")) return;
      const src = document.querySelectorAll("#nav .nav-link");
      if (!src.length) return;

      const nav = document.createElement("nav");
      nav.id = "bottom-nav";
      nav.className = "bottom-nav";
      nav.setAttribute("aria-label", trOr("uikit.sections", "Sections"));
      // Defensive inline pinning: if an ancestor ever breaks `fixed`, the
      // nav is body-attached so this still resolves against the viewport.
      nav.style.cssText =
        "position:fixed;left:0;right:0;bottom:0;z-index:80";

      src.forEach((a) => {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "bottom-nav-btn";
        b.dataset.dest = a.dataset.dest || "";
        b.dataset.view = a.dataset.view || "";

        const icon = document.createElement("span");
        icon.className = "bottom-nav-icon";
        icon.setAttribute("aria-hidden", "true");
        const ic = a.querySelector(".nav-icon");
        if (ic) icon.innerHTML = ic.innerHTML;

        const lab = a.querySelector(".nav-label");
        const label = document.createElement("span");
        label.className = "bottom-nav-label";
        label.textContent = lab ? lab.textContent.trim() : (a.dataset.dest || "");

        b.appendChild(icon);
        b.appendChild(label);
        b.addEventListener("click", () => {
          if (window.ui && typeof ui.show === "function") {
            ui.show(b.dataset.dest || b.dataset.view);
          }
        });
        nav.appendChild(b);
      });

      // DIRECT child of <body> — never inside a transformed/contain
      // ancestor, so `position:fixed` resolves against the viewport.
      document.body.appendChild(nav);
    },

    // Mirror the active destination onto the bottom-nav buttons. Driven
    // directly from renderChrome (no MutationObserver, no second source).
    paintBottomNav(dest) {
      const cur = dest || (this._current && this._current.dest) || "";
      document.querySelectorAll(".bottom-nav-btn").forEach((b) => {
        const on = b.dataset.dest === cur;
        b.classList.toggle("is-active", on);
        if (on) b.setAttribute("aria-current", "page");
        else b.removeAttribute("aria-current");
      });
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

    // ── Route ownership ─────────────────────────────────────────────────
    // Define `window.ui.show` EXACTLY ONCE. We capture app.js's core
    // renderer as `_coreShow` (the function that hides views, unhides the
    // target, runs the lazy-load flags, sets the help block, sets
    // location.hash to the bare view) and delegate the "render the view"
    // step to it. The shell adds: IA route resolution, the shareable
    // dest[/tab] hash, and chrome painting. No prior shell wrapper is ever
    // chained — there is one and only one shell `ui.show`.
    ownRoute() {
      if (!window.ui) window.ui = {};
      // Capture the core renderer once. If a previous shell.js instance
      // already ran (hot reload / double include), `_owned` guards against
      // capturing the shell wrapper as the core delegate.
      if (!shell._coreShow) {
        if (typeof window.ui.show === "function" && !window.ui.show._shellOwned) {
          shell._coreShow = window.ui.show.bind(window.ui);
        } else if (typeof window.ui.show === "function" && window.ui.show._coreRef) {
          shell._coreShow = window.ui.show._coreRef;
        }
      }
      // Fallback core renderer if app.js somehow exposed no show() yet.
      const coreShow = (view) => {
        if (shell._coreShow) { shell._coreShow(view); return; }
        document.querySelectorAll(".view").forEach((v) => v.classList.add("hidden"));
        const el = document.getElementById("view-" + view);
        if (el) el.classList.remove("hidden");
      };

      function shellShow(input) {
        const { view, dest, tab } = resolveRoute(input);
        // 1) Core renderer paints the real view (single source of truth
        //    for visibility + lazy-load flags). It also sets
        //    location.hash = view; we rewrite to the shareable form next.
        coreShow(view);
        // 2) Rewrite hash to the shareable dest[/tab] form without
        //    re-entering via hashchange.
        const wanted = tab ? "#" + dest + "/" + tab : "#" + dest;
        if (location.hash !== wanted) {
          shell._suppressHash = true;
          location.hash = wanted;
          setTimeout(() => { shell._suppressHash = false; }, 0);
        }
        // 3) Paint chrome.
        shell.renderChrome(dest, tab, view);
      }
      shellShow._shellOwned = true;
      shellShow._coreRef = shell._coreShow;
      // THE single definition of ui.show after this point.
      window.ui.show = shellShow;

      // THE single hashchange listener. Bound once; guarded so a double
      // include doesn't add a second one.
      if (!shell._hashBound) {
        shell._hashBound = true;
        window.addEventListener("hashchange", () => {
          if (shell._suppressHash) return;
          const r = resolveRoute(location.hash);
          window.ui.show(r.tab ? r.dest + "/" + r.tab : r.dest);
        });
      }
    },

    init() {
      this.ownRoute();
      this.bindPrefs();
      this.bindDrawer();
      this.mountBottomNav();
      if (window.Platform) window.Platform.applyTo(document);
      // Paint chrome (and re-run the route through the now-owned ui.show)
      // for whatever the current hash says. app.js bootstrap may already
      // have called the core renderer with a bare view before we owned
      // the route; re-running here normalises the hash to dest[/tab] and
      // paints chrome consistently.
      const r = resolveRoute(location.hash || "#dashboard");
      if (window.ui && typeof window.ui.show === "function") {
        window.ui.show(r.tab ? r.dest + "/" + r.tab : r.dest);
      } else {
        this.renderChrome(r.dest, r.tab, r.view);
      }
      if (window.applyI18n) window.applyI18n(document.getElementById("app-header"));
    },
  };

  window.shell = shell;

  // Loaded with `defer`, so app.js (also deferred, earlier in document
  // order) has fully executed and `window.ui` exists by the time this
  // runs. `defer` scripts execute after the document is parsed but the
  // DOMContentLoaded event has not necessarily fired yet for the very
  // last deferred script — guard both cases.
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => shell.init());
  } else {
    shell.init();
  }
})();
