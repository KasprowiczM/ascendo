// Ubuntu_Aktualizacje dashboard - vanilla SPA
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const api = {
  async get(path) {
    const r = await fetch(path);
    if (!r.ok) throw new Error(`${path}: ${r.status}`);
    return r.json();
  },
  async post(path, body) {
    const r = await fetch(path, {
      method: "POST",
      headers: {"content-type": "application/json"},
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!r.ok) {
      const t = await r.text();
      const err = new Error(`${path}: ${r.status} ${t}`);
      err.status = r.status;
      err.body = t;
      throw err;
    }
    return r.json();
  },
};

// ---------------------------------------------------------------------
// frontendCache — session-scoped read-through cache for slow scans.
// The user complaint: re-scanning every time we switch back to Overview
// or expand Categories is annoying. Keep results in module memory until
// (a) the explicit Refresh button is clicked, or (b) a run completes
// and ui.invalidateCaches() runs. Reload of the page also clears it
// (cache is in JS heap; not persisted in localStorage). Keying by
// adapter+os means a different machine reachable via the same dashboard
// URL never picks up a cached payload from elsewhere.
// ---------------------------------------------------------------------
const frontendCache = {
  _store: new Map(),
  _key(path) {
    const adapter = (window.ADAPTER_NAME || "unknown");
    const os = (document.documentElement.dataset.platform || "");
    return adapter + ":" + os + ":" + path;
  },
  async get(path, opts) {
    opts = opts || {};
    const key = this._key(path);
    if (!opts.refresh && this._store.has(key)) {
      return this._store.get(key);
    }
    const value = await api.get(path);
    this._store.set(key, value);
    return value;
  },
  set(path, value) {
    this._store.set(this._key(path), value);
  },
  invalidate(path) {
    if (!path) { this._store.clear(); return; }
    for (const k of Array.from(this._store.keys())) {
      if (k.endsWith(":" + path)) this._store.delete(k);
    }
  },
  invalidatePrefix(prefix) {
    for (const k of Array.from(this._store.keys())) {
      const idx2 = k.indexOf(":", k.indexOf(":") + 1);
      const tail = idx2 >= 0 ? k.slice(idx2 + 1) : k;
      if (tail.startsWith(prefix)) this._store.delete(k);
    }
  },
  clear() { this._store.clear(); },
};
window.frontendCache = frontendCache;

// Mark a button as "refreshing" + run an async op, then unmark. Lets every
// Refresh-button binding share spinner UX without copy-pasting try/finally.
async function runWithRefreshSpinner(btn, fn) {
  if (!btn) return await fn();
  const wasDisabled = btn.disabled;
  btn.classList.add("is-refreshing");
  btn.disabled = true;
  try {
    return await fn();
  } finally {
    btn.classList.remove("is-refreshing");
    btn.disabled = wasDisabled;
  }
}

// -- sudo modal --------------------------------------------------------------
const sudoMgr = {
  pending: null,  // resolve() of in-flight prompt
  open(reason) {
    return new Promise(resolve => {
      this.pending = resolve;
      const m = $("#sudo-modal");
      $("#sudo-error").textContent = reason || "";
      $("#sudo-pass").value = "";
      m.classList.remove("hidden");
      setTimeout(() => $("#sudo-pass").focus(), 50);
    });
  },
  close(authenticated) {
    $("#sudo-modal").classList.add("hidden");
    if (this.pending) {
      const r = this.pending; this.pending = null;
      r(authenticated);
    }
    sudoMgr.refreshIndicator();
  },
  // Adapter detection — fall through to the html[data-adapter] attribute
  // so the very first paint (before /version resolves) still uses the
  // right wording on macOS + Linux. Windows is the i18n default so missing
  // detection is benign there.
  _adapter() {
    return (window.ADAPTER_NAME
            || document.documentElement.dataset.adapter
            || "unknown").toLowerCase();
  },
  _isUnix() {
    const a = this._adapter();
    return a === "macos" || a === "ubuntu" || a === "linux";
  },
  async refreshIndicator() {
    try {
      const s = await api.get("/sudo/status");
      const ind = $("#sudo-indicator");
      // DOM-construction (not innerHTML interpolation) so a malicious
      // i18n value can never inject markup. Strings flow through textContent.
      ind.textContent = "";
      const span = document.createElement("span");
      span.className = "badge " + (s.cached ? "ok" : "warn");
      // Adapter-aware wording. macOS + Linux say "sudo cached / not cached";
      // Windows says "Administrator authorized / not authorized". The
      // dedicated elevation.* namespace is the source of truth — sudo.*
      // is preserved for legacy callers but the indicator text is what
      // users see, so it gets the per-adapter copy.
      const a = this._adapter();
      const key = a === "macos" || a === "ubuntu" || a === "linux"
        ? (s.cached ? "elevation.sudo_active" : "elevation.sudo_not_active")
        : (s.cached ? "elevation.admin_authorized" : "elevation.admin_not_active");
      span.textContent = (window.tr && window.tr(key)) || key;
      ind.appendChild(span);
    } catch {}
  },
  async ensure() {
    const s = await api.get("/sudo/status");
    // CRITICAL: only skip the password modal when the backend has
    // confirmed BOTH (a) sudo credentials are cached AND (b) askpass is
    // wired (a real SPA password is registered, SUDO_ASKPASS will be
    // set in subprocess env). Pre-fix this also accepted "cached=true,
    // method=timestamp" — but a fresh OS-sudo-timestamp does not
    // guarantee the apply subprocess can drive sudo non-interactively.
    // When the dashboard is launched from Tauri/Ascendo.app (no TTY),
    // apply subprocesses then hung silently on sudo with no Touch ID
    // prompt visible. This was the operator-reported "Touch ID stopped
    // prompting, updates don't apply" regression.
    if (s.cached === true && s.askpass_ready === true) return true;
    // macOS shortcut: if PAM Touch ID is wired (auth sufficient pam_tid.so
    // in /etc/pam.d/sudo_local), skip the password modal entirely. The
    // first apply phase's `_ascendo_sudo_warm` will trigger the Touch ID
    // sheet via TTY-PAM, sudo timestamps are cached, and every later
    // phase short-circuits via `sudo -n -v`. Apply scripts pick `sudo`
    // vs `sudo -A` automatically (see _ascendo_sudo helper) so no
    // askpass is needed.
    //
    // BUT: Touch-ID-only skip only works when the dashboard subprocess
    // has access to a real TTY (e.g. you launched it from Terminal). A
    // GUI-launched dashboard (Tauri-spawned sidecar, Ascendo.app
    // double-click) has no /dev/tty, so TTY-PAM can't drive Touch ID
    // and we MUST register a password via the modal. Heuristic: the
    // backend exposes `tty_available` in the status endpoint when known;
    // if missing, fall back to "always modal when no askpass" for
    // safety.
    if (this._adapter() === "macos" && s.tty_available !== false) {
      try {
        const ti = await api.get("/elevation/touchid/status");
        if (ti && ti.enabled) return true;
      } catch {}
    }
    const fallback = this._isUnix()
      ? "sudo credentials needed — enter your password to authenticate."
      : "Administrator credentials needed — enter your password to authenticate.";
    const prompt = (window.tr && window.tr("sudo.empty_prompt")) || fallback;
    return this.open(prompt);
  },
};

// -- Windows service indicator ----------------------------------------------
//
// Mirrors sudoMgr's pattern: refreshIndicator() updates the footer pill via
// DOM construction (no innerHTML interpolation; the security hook blocks it
// and i18n strings could otherwise inject markup). Settings tab buttons
// drive install / uninstall / restart through the /service/* REST surface.
const serviceMgr = {
  _last: null,
  _t: (key, fallback) => (window.tr && window.tr("service." + key)) || fallback,

  async refreshIndicator() {
    try {
      const s = await api.get("/service/status");
      this._last = s;
      const ind = document.getElementById("service-indicator");
      if (!ind) return;
      ind.textContent = "";
      const span = document.createElement("span");
      const installed = !!s.installed;
      const running = !!s.running;
      span.className = "badge " + (running ? "ok" : (installed ? "warn" : "dim"));
      span.textContent = running
        ? this._t("pill_running", "service running")
        : installed
          ? this._t("pill_stopped", "service stopped")
          : this._t("pill_not_installed", "service off");
      ind.appendChild(span);
      this._renderCard(s);
    } catch (e) {
      // Backend may not have /service yet (mid-rollout) — pill silently absent.
    }
  },

  _renderCard(s) {
    const card = document.getElementById("service-status-card");
    if (!card) return;
    card.textContent = "";
    if (!s.installed) {
      const p = document.createElement("p");
      p.className = "dim";
      p.textContent = this._t("not_installed_msg",
        "Service not installed. Click Install to register AscendoDashboard.");
      card.appendChild(p);
      return;
    }
    const row = (label, value) => {
      const div = document.createElement("div");
      div.style.display = "flex";
      div.style.justifyContent = "space-between";
      const lab = document.createElement("span"); lab.textContent = label;
      const val = document.createElement("span"); val.textContent = value;
      val.style.fontFamily = "var(--mono, monospace)";
      div.appendChild(lab); div.appendChild(val);
      return div;
    };
    const yes = this._t("yes", "yes");
    const no = this._t("no", "no");
    card.appendChild(row(this._t("status_installed", "Installed"), s.installed ? yes : no));
    card.appendChild(row(this._t("status_running", "Running"),     s.running   ? yes : no));
    if (s.port_listening !== undefined) card.appendChild(row(this._t("status_port", "Port"), s.port_listening ? yes : no));
    if (s.health) card.appendChild(row(this._t("status_health", "Health"), String(s.health)));
    if (s.pid)    card.appendChild(row(this._t("status_pid", "PID"), String(s.pid)));
    if (s.last_started) card.appendChild(row(this._t("status_last_started", "Last started"), String(s.last_started)));
  },

  async _post(action, confirmKey) {
    const msg = (window.tr && window.tr("service." + confirmKey));
    if (msg && !window.confirm(msg)) return;
    try {
      const r = await api.post("/service/" + action, {});
      // refresh status pill + card whether or not the call succeeded
      await this.refreshIndicator();
      const tag = r && r.ok === false ? "action_failed" : "action_ok";
      const line = document.getElementById("status-line");
      if (line) line.textContent = this._t(tag, tag === "action_ok" ? "Service action completed" : "Service action failed");
    } catch (e) {
      const line = document.getElementById("status-line");
      if (line) line.textContent = this._t("action_failed", "Service action failed") +
        ": " + (e && e.message ? e.message : "");
      await this.refreshIndicator();
    }
  },

  install()   { return this._post("install", "confirm_install"); },
  uninstall() { return this._post("uninstall", "confirm_uninstall"); },
  restart()   { return this._post("restart"); },
};

// Wire button bindings once DOM is ready (bindings are idempotent — safe to
// re-run if the Settings tab gets re-rendered).
function _bindServiceButtons() {
  const map = {
    "service-install-btn":   () => serviceMgr.install(),
    "service-uninstall-btn": () => serviceMgr.uninstall(),
    "service-restart-btn":   () => serviceMgr.restart(),
    "service-refresh-btn":   () => serviceMgr.refreshIndicator(),
  };
  for (const [id, fn] of Object.entries(map)) {
    const el = document.getElementById(id);
    if (el && !el.__ascendoBound) {
      el.__ascendoBound = true;
      el.addEventListener("click", fn);
    }
  }
}

const ui = {
  show(view) {
    // Edition gate: in "basic", redirect dev-only views to History so deep
    // links (e.g. /#logs after a CLI run) still land somewhere useful.
    const BASIC_HIDDEN_VIEWS = new Set(["sync", "hosts", "logs"]);
    if (window.ASCENDO_EDITION === "basic" && BASIC_HIDDEN_VIEWS.has(view)) {
      view = "history";
    }
    $$(".view").forEach(v => v.classList.add("hidden"));
    $(`#view-${view}`).classList.remove("hidden");
    $$("a[data-view]").forEach(a => a.classList.toggle("active", a.dataset.view === view));
    location.hash = view;
    // Lazy-load on first visit only. Subsequent tab switches reuse the cached
    // data - user explicitly clicks "Refresh" (or finishes a run, which calls
    // ui.invalidateCaches()) to re-fetch. This avoids the per-visit spinner
    // for slow scans (inventory takes seconds).
    ui._loaded = ui._loaded || {};
    if (view === "overview"   && !ui._loaded.overview)   { ui._loaded.overview = true;   ui.loadOverview(); }
    if (view === "categories" && !ui._loaded.categories) { ui._loaded.categories = true; ui.loadCategories(); }
    if (view === "history"    && !ui._loaded.history)    { ui._loaded.history = true;    ui.loadHistory(); }
    if (view === "sync"       && !ui._loaded.sync)       { ui._loaded.sync = true;       ui.loadSync(); }
    if (view === "settings"   && !ui._loaded.settings)   { ui._loaded.settings = true;   ui.loadSettings(); }
    if (view === "hosts"      && !ui._loaded.hosts)      { ui._loaded.hosts = true;      ui.loadHosts(); }
    if (view === "schedule"   && !ui._loaded.schedule)   { ui._loaded.schedule = true;   ui.loadSchedule(); }
    if (view === "apps"       && !ui._loaded.apps)       { ui._loaded.apps = true;       ui.loadApps(); }
    if (view === "suggest"    && !ui._loaded.suggest)    { ui._loaded.suggest = true;    ui.loadSuggestions(); }
    if (view === "suggest") { try { window.aitools && window.aitools.init(); } catch {} }
    if (view === "logs"       && !ui._loaded.logs)       { ui._loaded.logs = true;       ui.loadLogsList(); }
    if (view === "about"      && !ui._loaded.about)      { ui._loaded.about = true;      ui.loadAbout(); }
    // Run Center is special: must always (re)bind active-stream subscription.
    if (view === "run") ui.loadRunCenter();
    // Refresh the sidebar contextual help block. Pulls from the
    // <view>.help_summary i18n key the per-view help cards already use,
    // so we don't have to duplicate translations.
    try { ui.updateSidebarHelp(view); } catch {}
  },

  updateSidebarHelp(view) {
    const el = document.getElementById("sidebar-help");
    if (!el) return;
    const keyMap = {
      overview:   "overview.help_summary",
      categories: "categories.help_summary",
      run:        "run.help_summary",
      history:    "history.help_summary",
      logs:       "logs.help_summary",
      sync:       "sync.help_summary",
      apps:       "apps.help_summary",
      suggest:    "suggest.help_summary",
      hosts:      "hosts.help_summary",
      settings:   "settings.help_summary",
      help:       "help.help_summary",
      about:      "about.help_summary",
    };
    const key = keyMap[view];
    if (!key) { el.textContent = ""; return; }
    const text = (window.tr && window.tr(key)) || "";
    // textContent (not innerHTML) so translated copy can't inject markup.
    el.textContent = text;
  },

  invalidateCaches() {
    // Called after a run completes or when the user hits "Refresh".
    ui._loaded = {};
    window.INV_SUMMARY = null;
    frontendCache.clear();
  },

  async maybeShowWizard() {
    // The wizard fires ON FIRST RUN ONLY. Source of truth: the backend's
    // ``onboarding.json`` (~/.ascendo/onboarding.json) — once
    // ``onboarded=true`` is persisted there, we NEVER re-show the
    // wizard. The user changes any setting via the Settings menu.
    //
    // Earlier this also fired on missing ``localStorage.ui-locale`` /
    // ``ui-language``, intending to catch "user wiped browser profile,
    // never picked a language". But that meant clearing browser cookies
    // re-showed the full 6-step wizard EVERY TIME — operator-reported
    // annoyance. New behaviour:
    //   1. Backend onboarded=false → show full wizard.
    //   2. Backend onboarded=true but localStorage lang missing →
    //      silently default to ``navigator.language`` (en if it starts
    //      with "en", else pl) and persist. No popup. User can change
    //      in Settings → UI Language at any time.
    let needsWizard = false;
    try {
      const s = await api.get("/onboarding/state");
      if (!s.onboarded) needsWizard = true;
    } catch {
      // Endpoint unreachable on a fresh install (e.g. very-first request
      // racing app startup). Default to showing the wizard — better
      // than swallowing the first-run flow.
      needsWizard = true;
    }
    if (needsWizard) {
      ui.wizard.start();
      return;
    }
    // Already onboarded — silently seed language preference if missing
    // so subsequent navigations render in the user's browser locale.
    try {
      const have = localStorage.getItem("ui-locale")
                || localStorage.getItem("ui-language")
                || localStorage.getItem("ascendo_lang");
      if (!have) {
        const browserLang = (navigator.language || "en").toLowerCase();
        const pick = browserLang.startsWith("pl") ? "pl" : "en";
        localStorage.setItem("ui-locale", pick);
        if (window.i18n && typeof window.i18n.setLocale === "function") {
          try { window.i18n.setLocale(pick); } catch {}
        }
      }
    } catch {}
  },

  // Step-router for the 6-step first-run wizard.
  // Self-contained: every step is a function that builds DOM into
  // #wizard-step-host. Persists choices to localStorage as the user
  // moves so a refresh mid-wizard doesn't lose work; final POST to
  // /onboarding/complete writes the durable state file.
  //
  // Adapter-conditional: every step that mentions specific package
  // sources, elevation method, or CLI examples reads per-adapter
  // strings from wizard.os.<adapter> in i18n.js (see osTr()). Adapter
  // is read from document.documentElement.dataset.adapter (set during
  // boot()). Falls back to 'windows' for unknown adapters so the
  // wizard never has empty strings.
  wizard: {
    state: null,
    // Basic edition shows the original 6-step path (welcome -> prefs ->
    // admin -> scan -> sources -> done). Dev edition appends three more
    // steps after `done` was renamed (see below) so the operator sees
    // GitHub repo config, dev-sync provider setup, and developer
    // resource links before finishing. The step list is rebuilt in
    // start() so a runtime edition flip (rare) doesn't strand the user.
    steps: ["welcome", "prefs", "admin", "scan", "sources", "done"],
    BASIC_STEPS: ["welcome", "prefs", "admin", "scan", "sources", "done"],
    DEV_EXTRA_STEPS: ["github_config", "dev_sync_setup", "dev_resources"],
    currentIdx: 0,
    // Resolve a per-adapter wizard string. Reads adapter from <html>
    // and falls back to 'windows' if no per-adapter copy exists for
    // the current adapter (or if the adapter is 'unknown'). 'ubuntu'
    // and 'linux' both map to the 'linux' variant.
    osTr(key) {
      const adapter = (document.documentElement.dataset.adapter
                       || window.ADAPTER_NAME
                       || "unknown").toLowerCase();
      const variant = (adapter === "ubuntu" || adapter === "linux")
                      ? "linux"
                      : (adapter === "macos" || adapter === "windows")
                        ? adapter
                        : "windows";
      const v = tr(`wizard.os.${variant}.${key}`);
      // tr() returns the path string when the lookup misses; fall back
      // to the windows variant in that case so empty strings never ship.
      if (v === `wizard.os.${variant}.${key}`) {
        return tr(`wizard.os.windows.${key}`);
      }
      return v;
    },
    osList(key) {
      const v = this.osTr(key);
      return Array.isArray(v) ? v : [];
    },
    start() {
      // Branch the step list on edition. Basic = 6 steps; dev = 9. Dev
      // edition picks up after `done` because the dry-run step is shared
      // and sensible for both audiences.
      const isDev = (window.ASCENDO_EDITION === "dev");
      this.steps = isDev
        ? [...this.BASIC_STEPS, ...this.DEV_EXTRA_STEPS]
        : [...this.BASIC_STEPS];
      this.state = {
        language: window.UI_LANG || "en",
        theme:    "dark",
        admin_authorised: false,
        inventory_seen: false,
        dry_run_seen: false,
        sources_summary: null,
        wu_check: null,         // {status: "idle"|"running"|"done"|"failed", count?, error?}
        dry_run: null,          // {status, items, error?}
        // Dev-only state. Empty/null on basic; the builders just don't
        // run in that case so it never gets read.
        github_repo: "KasprowiczM/ascendo",
        github_test: null,      // {status: "ok"|"failed", message?}
        dev_sync_config: null,  // last /sync/config-status response
        dev_sync_setup_run: null, // {status, exit_code?, stdout?}
      };
      // Pull any mid-wizard scratch from a previous reload so progress
      // survives. Keys are namespaced under 'wizard:'.
      try {
        const saved = localStorage.getItem("wizard:state");
        if (saved) Object.assign(this.state, JSON.parse(saved));
      } catch {}
      try {
        const lsTheme = localStorage.getItem("ui-theme") || localStorage.getItem("ascendo_theme");
        if (lsTheme) this.state.theme = lsTheme;
      } catch {}
      this.currentIdx = 0;
      $("#wizard-modal").classList.remove("hidden");
      this.render();
    },
    saveScratch() {
      try {
        // Don't persist the streaming/transient bits.
        const {language, theme, admin_authorised, inventory_seen,
               dry_run_seen} = this.state;
        localStorage.setItem("wizard:state", JSON.stringify({
          language, theme, admin_authorised, inventory_seen, dry_run_seen,
        }));
      } catch {}
    },
    clearScratch() {
      try { localStorage.removeItem("wizard:state"); } catch {}
    },
    advance() {
      if (this.currentIdx < this.steps.length - 1) {
        this.currentIdx++;
        this.render();
      }
    },
    back() {
      if (this.currentIdx > 0) {
        this.currentIdx--;
        this.render();
      }
    },
    renderBreadcrumb() {
      const ol = $("#wizard-breadcrumb");
      ol.textContent = "";
      this.steps.forEach((id, i) => {
        const li = document.createElement("li");
        if (i === this.currentIdx) li.classList.add("current");
        else if (i < this.currentIdx) li.classList.add("done");
        const dot = document.createElement("span");
        dot.className = "dot";
        const lbl = document.createElement("span");
        lbl.textContent = `${i + 1}. ${tr(`wizard.step.${id}`)}`;
        li.appendChild(dot);
        li.appendChild(lbl);
        // Admin step shows authenticated pill once user has approved.
        if (id === "admin" && this.state.admin_authorised) {
          const pill = document.createElement("span");
          pill.className = "pill-ok";
          pill.textContent = tr("wizard.admin.ok_pill");
          li.appendChild(pill);
        }
        ol.appendChild(li);
      });
    },
    render() {
      this.renderBreadcrumb();
      const host = $("#wizard-step-host");
      host.textContent = "";
      const id = this.steps[this.currentIdx];
      const builder = this[`build_${id}`];
      if (typeof builder === "function") builder.call(this, host);
      // Footer state.
      $("#wizard-back").style.visibility = (this.currentIdx === 0) ? "hidden" : "visible";
      const nextBtn = $("#wizard-next");
      const isLastStep = (this.currentIdx === this.steps.length - 1);
      // Welcome step uses its own internal CTA so hide footer Next.
      // The "done" step gates Next on dry_run_seen so the operator can't
      // skip past the dry-run on basic edition (the last step). On dev
      // edition `done` is mid-sequence so the Next-as-finish gating
      // shifts to the actual last step (`dev_resources`).
      if (id === "welcome") {
        nextBtn.style.display = "none";
      } else if (id === "done" && isLastStep) {
        nextBtn.style.display = "";
        nextBtn.textContent = tr("wizard.done.finish_btn");
        nextBtn.disabled = !this.state.dry_run_seen;
        nextBtn.dataset.role = "finish";
      } else if (isLastStep) {
        // Dev edition's last step (dev_resources). Finish always enabled
        // there — the dry-run gate already passed when leaving `done`.
        nextBtn.style.display = "";
        nextBtn.textContent = tr("wizard.done.finish_btn");
        nextBtn.disabled = false;
        nextBtn.dataset.role = "finish";
      } else if (id === "done") {
        // Dev edition: leaving `done` shouldn't be possible until the
        // operator has either run or skipped the dry-run.
        nextBtn.style.display = "";
        nextBtn.textContent = tr("wizard.next");
        nextBtn.disabled = !this.state.dry_run_seen;
        nextBtn.dataset.role = "next";
      } else {
        nextBtn.style.display = "";
        nextBtn.textContent = tr("wizard.next");
        nextBtn.disabled = false;
        nextBtn.dataset.role = "next";
      }
      // Translate static labels in the footer / skip btn.
      window.applyI18n(host);
      window.applyI18n($("#wizard-modal").querySelector("header"));
      window.applyI18n($("#wizard-modal").querySelector("footer"));
    },
    // ── Step 1: Welcome ─────────────────────────────────────────────
    build_welcome(host) {
      const hero = document.createElement("div");
      hero.className = "wizard-welcome-hero";
      const img = document.createElement("img");
      img.className = "hero-logo";
      img.src = "/assets/logo-mark.svg";
      img.alt = "";
      hero.appendChild(img);
      const tx = document.createElement("div");
      tx.className = "hero-text";
      const h = document.createElement("h3");
      h.id = "wizard-title";
      h.textContent = tr("wizard.welcome.title");
      const tag = document.createElement("p");
      tag.className = "tagline";
      // Tagline is adapter-specific ("Unified updates for macOS" vs
      // "...for Windows" vs "...for Linux").
      tag.textContent = this.osTr("tagline");
      tx.appendChild(h); tx.appendChild(tag);
      hero.appendChild(tx);
      host.appendChild(hero);
      const body = document.createElement("p");
      // Body names the four (or five on Linux) sources Ascendo manages
      // for this OS — adapter-specific.
      body.textContent = this.osTr("intro");
      host.appendChild(body);
      const preview = document.createElement("div");
      preview.className = "wizard-welcome-preview";
      const ph = document.createElement("h4");
      ph.textContent = tr("wizard.welcome.preview");
      preview.appendChild(ph);
      const ol = document.createElement("ol");
      // Bullets 2 + 5 are adapter-specific (admin term + dry-run
      // category name); the rest are adapter-neutral.
      const bullets = [
        tr("wizard.welcome.bullet1"),
        this.osTr("bullet_admin"),
        tr("wizard.welcome.bullet3"),
        tr("wizard.welcome.bullet4"),
        this.osTr("bullet_dryrun"),
      ];
      bullets.forEach(text => {
        const li = document.createElement("li");
        li.textContent = text;
        ol.appendChild(li);
      });
      preview.appendChild(ol);
      host.appendChild(preview);
      const cta = document.createElement("div");
      cta.className = "wizard-welcome-cta";
      const btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = tr("wizard.welcome.cta");
      btn.addEventListener("click", () => ui.wizard.advance());
      cta.appendChild(btn);
      host.appendChild(cta);
    },
    // ── Step 2: Language + Theme ─────────────────────────────────
    build_prefs(host) {
      const h = document.createElement("h3");
      h.textContent = tr("wizard.prefs.title");
      host.appendChild(h);
      const body = document.createElement("p");
      body.textContent = tr("wizard.prefs.body");
      host.appendChild(body);
      const grid = document.createElement("div");
      grid.className = "wizard-choice-grid";
      // Language card
      const langCard = document.createElement("div");
      langCard.className = "wizard-choice-card";
      const langH = document.createElement("h4");
      langH.textContent = tr("wizard.prefs.lang_h");
      langCard.appendChild(langH);
      [["en", tr("wizard.prefs.lang_en")], ["pl", tr("wizard.prefs.lang_pl")]].forEach(([val, lbl]) => {
        const opt = document.createElement("label");
        opt.className = "opt";
        const r = document.createElement("input");
        r.type = "radio"; r.name = "wiz-lang"; r.value = val;
        if (this.state.language === val) r.checked = true;
        r.addEventListener("change", () => {
          this.state.language = val;
          window.UI_LANG = val;
          try { window.applyI18n(); } catch {}
          // Persist immediately so reload + maybeShowWizard sees the
          // language as picked and doesn't re-prompt.
          try { localStorage.setItem("ui-locale", val); } catch {}
          this.saveScratch();
          // Re-render so all step copy switches language live.
          this.render();
        });
        const sp = document.createElement("span");
        sp.textContent = lbl;
        opt.appendChild(r); opt.appendChild(sp);
        langCard.appendChild(opt);
      });
      grid.appendChild(langCard);
      // Theme card
      const themeCard = document.createElement("div");
      themeCard.className = "wizard-choice-card";
      const themeH = document.createElement("h4");
      themeH.textContent = tr("wizard.prefs.theme_h");
      themeCard.appendChild(themeH);
      [["auto", tr("wizard.prefs.theme_auto")],
       ["dark", tr("wizard.prefs.theme_dark")],
       ["light", tr("wizard.prefs.theme_light")]].forEach(([val, lbl]) => {
        const opt = document.createElement("label");
        opt.className = "opt";
        const r = document.createElement("input");
        r.type = "radio"; r.name = "wiz-theme"; r.value = val;
        if (this.state.theme === val) r.checked = true;
        r.addEventListener("change", () => {
          this.state.theme = val;
          try {
            localStorage.setItem("ui-theme", val);
            document.documentElement.dataset.themePref = val;
            window.applyTheme(val);
          } catch {}
          this.saveScratch();
        });
        const sp = document.createElement("span");
        sp.textContent = lbl;
        opt.appendChild(r); opt.appendChild(sp);
        themeCard.appendChild(opt);
      });
      grid.appendChild(themeCard);
      host.appendChild(grid);
      const hint = document.createElement("p");
      hint.className = "wizard-live-hint";
      hint.textContent = tr("wizard.prefs.live_hint");
      host.appendChild(hint);
      // Persist immediately to /settings so refresh keeps the choice.
      this.persistPrefs();
    },
    async persistPrefs() {
      try {
        const cur = window.SETTINGS_CACHE || (await api.get("/settings").catch(()=>({}))) || {};
        const merged = {
          ...cur,
          ui: { ...(cur.ui || {}), language: this.state.language, theme: this.state.theme },
        };
        await fetch("/settings", {
          method: "PUT",
          headers: {"content-type": "application/json"},
          body: JSON.stringify(merged),
        }).catch(()=>{});
        window.SETTINGS_CACHE = merged;
      } catch {}
    },
    // ── Step 3: Elevation (Administrator/UAC on Windows, sudo on
    //                       macOS + Linux) ──────────────────────────
    build_admin(host) {
      const h = document.createElement("h3");
      // "Administrator (UAC) access" on Windows; "sudo access (askpass
      // cache)" on macOS + Linux.
      h.textContent = this.osTr("admin_title");
      host.appendChild(h);
      const body = document.createElement("p");
      // Names the actual elevation primitive (UAC vs sudo) and the
      // categories that need it.
      body.textContent = this.osTr("admin_body");
      host.appendChild(body);
      // Two callouts: "Why we ask" (uses adapter-specific reasons) and
      // "What you can do" (adapter-neutral guidance).
      ["why", "do"].forEach(k => {
        const co = document.createElement("div");
        co.className = "wizard-admin-callout";
        const t = document.createElement("h4");
        t.textContent = tr(`wizard.admin.${k}_h`);
        const b = document.createElement("p");
        b.textContent = this.osTr(`admin_${k}_b`);
        co.appendChild(t); co.appendChild(b);
        host.appendChild(co);
      });
      const acts = document.createElement("div");
      acts.className = "wizard-admin-actions";
      const authBtn = document.createElement("button");
      authBtn.type = "button";
      authBtn.textContent = tr("wizard.admin.auth_now");
      authBtn.addEventListener("click", async () => {
        const ok = await sudoMgr.open(tr("wizard.admin.title"));
        if (ok) {
          this.state.admin_authorised = true;
          this.saveScratch();
          this.render();
          // Auto-advance after a short pause so the green pill is visible.
          setTimeout(() => this.advance(), 700);
        }
      });
      const skipBtn = document.createElement("button");
      skipBtn.type = "button";
      skipBtn.className = "secondary";
      skipBtn.textContent = tr("wizard.admin.skip_now");
      skipBtn.addEventListener("click", () => this.advance());
      acts.appendChild(authBtn);
      acts.appendChild(skipBtn);
      host.appendChild(acts);
      const status = document.createElement("div");
      status.className = "wizard-admin-status";
      if (this.state.admin_authorised) {
        const pill = document.createElement("span");
        pill.className = "pill-ok";
        pill.textContent = tr("wizard.admin.ok_pill");
        status.appendChild(pill);
      } else {
        const pill = document.createElement("span");
        pill.className = "pill-warn";
        pill.textContent = tr("wizard.admin.skipped_pill");
        status.appendChild(pill);
      }
      host.appendChild(status);
    },
    // ── Step 4: Inventory scan ───────────────────────────────────
    build_scan(host) {
      const h = document.createElement("h3");
      h.textContent = tr("wizard.scan.title");
      host.appendChild(h);
      const body = document.createElement("p");
      // "We're scanning the four sources Ascendo manages on Windows"
      // vs "...on macOS" vs "...on Linux".
      body.textContent = this.osTr("scan_body");
      host.appendChild(body);
      const prog = document.createElement("div");
      prog.className = "wizard-scan-progress";
      const bar = document.createElement("div");
      bar.className = "wizard-scan-bar";
      const fill = document.createElement("div");
      fill.className = "fill";
      bar.appendChild(fill);
      prog.appendChild(bar);
      const src = document.createElement("div");
      src.className = "wizard-scan-source";
      src.id = "wizard-scan-src";
      const spin = document.createElement("span");
      spin.className = "spinner";
      const lbl = document.createElement("span");
      lbl.id = "wizard-scan-lbl";
      lbl.textContent = tr("wizard.scan.progress_label");
      src.appendChild(spin); src.appendChild(lbl);
      prog.appendChild(src);
      host.appendChild(prog);
      const summary = document.createElement("div");
      summary.id = "wizard-scan-summary";
      summary.className = "wizard-scan-summary";
      summary.style.display = "none";
      host.appendChild(summary);
      this.runInventoryScan(fill, lbl, summary);
    },
    async runInventoryScan(fillEl, lblEl, summaryEl) {
      // Build ticker labels from the adapter-specific sources_table.
      // Each label is composed by substituting the source id into the
      // generic "Scanning <id>…" template — avoids needing a translation
      // key per source per locale per adapter. The original Windows-only
      // strings (scanning_winget/_msstore/_arp/_wu) are preserved in
      // i18n.js for backward-compat with anything that still references
      // them, but the ticker no longer uses them directly.
      const winLabel = tr("wizard.scan.scanning_winget"); // "Scanning winget…"
      const sources = (this.osList("sources_table") || []).map(row => {
        return [row.id, winLabel.replace("winget", row.id)];
      });
      // Defensive fallback if the adapter sources_table is missing.
      if (sources.length === 0) {
        sources.push(
          ["winget",         tr("wizard.scan.scanning_winget")],
          ["msstore",        tr("wizard.scan.scanning_msstore")],
          ["registry_arp",   tr("wizard.scan.scanning_arp")],
          ["windows_update", tr("wizard.scan.scanning_wu")],
        );
      }
      const t0 = performance.now();
      const minDuration = 2000;
      // Drive the user-facing progress bar through the labels while the
      // real call (POST /inventory/refresh + GET /inventory/summary)
      // runs concurrently in the background. The ticker just yields
      // visible progress in case the refresh resolves instantly.
      let stopTicker = false;
      const totalSteps = sources.length;
      let idx = 0;
      const stepDuration = 350;
      const ticker = (async () => {
        for (; idx < totalSteps; idx++) {
          if (stopTicker) break;
          // sources[idx][1] is now a literal label string, not an i18n key.
          lblEl.textContent = sources[idx][1];
          fillEl.style.width = `${Math.round((idx + 0.5) / totalSteps * 90)}%`;
          await new Promise(r => setTimeout(r, stepDuration));
        }
      })();
      let summary = null;
      let err = null;
      try {
        // Trigger a real cache-bust then a fresh /inventory/summary.
        await api.post("/inventory/refresh").catch(()=>{});
        summary = await api.get("/inventory/summary");
      } catch (e) { err = e; }
      stopTicker = true;
      await ticker;
      // Hold for 2s minimum so the user sees the success state.
      const elapsed = performance.now() - t0;
      if (elapsed < minDuration) {
        await new Promise(r => setTimeout(r, minDuration - elapsed));
      }
      fillEl.style.width = "100%";
      if (err) {
        lblEl.textContent = String(err);
        return;
      }
      lblEl.textContent = tr("wizard.scan.done_title");
      this.state.sources_summary = summary;
      this.state.inventory_seen = true;
      this.saveScratch();
      // Build summary box: donut + totals.
      summaryEl.style.display = "";
      summaryEl.textContent = "";
      const donutHost = document.createElement("div");
      donutHost.className = "donut-host";
      donutHost.id = "wizard-scan-donut";
      summaryEl.appendChild(donutHost);
      const totals = summary.totals || {ok:0, outdated:0, missing:0, total:0};
      const totalsText = document.createElement("div");
      totalsText.className = "totals-text";
      const sub = document.createElement("h4");
      sub.style.margin = "0 0 0.25rem";
      sub.textContent = tr("wizard.scan.done_subtitle");
      totalsText.appendChild(sub);
      const summaryLine = document.createElement("p");
      summaryLine.style.margin = "0";
      const tplVars = {
        total: totals.total || 0,
        // Default to the adapter's source count if the backend hasn't
        // reported any categories yet.
        sources: Object.keys(summary.categories || {}).length
                 || this.osList("sources_table").length
                 || 4,
      };
      summaryLine.textContent = tr("wizard.scan.done_body")
        .replace("{total}", tplVars.total).replace("{sources}", tplVars.sources);
      totalsText.appendChild(summaryLine);
      summaryEl.appendChild(totalsText);
      ui.renderDonut("wizard-scan-donut", [
        { label: "ok",       value: totals.ok || 0,       color: "var(--ok)" },
        { label: "outdated", value: totals.outdated || 0, color: "var(--warn)" },
        { label: "missing",  value: totals.missing || 0,  color: "var(--err)" },
      ]);
      // Auto-advance after a short pause, but only if user hasn't moved.
      const myIdx = this.currentIdx;
      setTimeout(() => {
        if (this.currentIdx === myIdx) this.advance();
      }, 1700);
    },
    // ── Step 5: Sources preview ─────────────────────────────────
    build_sources(host) {
      const h = document.createElement("h3");
      h.textContent = tr("wizard.sources.title");
      host.appendChild(h);
      const body = document.createElement("p");
      // Adapter-specific intro ("These are the four places Windows
      // installs apps from..." vs "...macOS..." vs "...Linux...").
      body.textContent = this.osTr("sources_intro");
      host.appendChild(body);
      const summary = this.state.sources_summary || {categories: {}, totals: {}};
      const tbl = document.createElement("table");
      tbl.className = "wizard-sources-table";
      const thead = document.createElement("thead");
      const trh = document.createElement("tr");
      ["col_source","col_desc","col_total","col_outdated"].forEach(k => {
        const th = document.createElement("th");
        th.textContent = tr(`wizard.sources.${k}`);
        if (k === "col_total" || k === "col_outdated") th.classList.add("num");
        trh.appendChild(th);
      });
      // Action column for the deferred per-adapter check (e.g. "Run
      // check" on windows_update for Windows, softwareupdate on macOS).
      const thAct = document.createElement("th");
      thAct.textContent = "";
      trh.appendChild(thAct);
      thead.appendChild(trh);
      tbl.appendChild(thead);
      const tb = document.createElement("tbody");
      // Source rows + descriptions come from the per-adapter
      // sources_table (array of {id, desc} objects).
      const rows = (this.osList("sources_table") || []).map(r => [r.id, r.desc]);
      // The "deferred check" is the source whose count is only known
      // after running an explicit check (Windows Update on Windows
      // because PSWindowsUpdate scan is slow; softwareupdate on macOS
      // for the same reason). Linux has no deferred source today.
      const deferredId = this.osTr("deferred_check_id") || null;
      let outdatedTotal = 0;
      for (const [src, descText] of rows) {
        const cat = (summary.categories || {})[src] || {total: 0, outdated: 0};
        const trr = document.createElement("tr");
        const tdSrc = document.createElement("td");
        tdSrc.className = "src-name";
        tdSrc.textContent = src;
        trr.appendChild(tdSrc);
        const tdDesc = document.createElement("td");
        tdDesc.className = "src-desc";
        tdDesc.textContent = descText;
        trr.appendChild(tdDesc);
        const tdTotal = document.createElement("td");
        tdTotal.className = "num";
        tdTotal.textContent = cat.total || 0;
        trr.appendChild(tdTotal);
        const tdOut = document.createElement("td");
        tdOut.className = "num";
        if (src === deferredId && (!cat || cat.total === 0)) {
          if (this.state.wu_check && this.state.wu_check.status === "done") {
            tdOut.textContent = String(this.state.wu_check.count || 0);
            outdatedTotal += this.state.wu_check.count || 0;
          } else if (this.state.wu_check && this.state.wu_check.status === "running") {
            tdOut.textContent = "…";
          } else if (this.state.wu_check && this.state.wu_check.status === "failed") {
            tdOut.textContent = "—";
          } else {
            tdOut.textContent = tr("wizard.sources.wu_pending");
          }
        } else {
          tdOut.textContent = cat.outdated || 0;
          outdatedTotal += cat.outdated || 0;
        }
        trr.appendChild(tdOut);
        const tdAct = document.createElement("td");
        if (deferredId && src === deferredId) {
          const btn = document.createElement("button");
          btn.type = "button";
          btn.className = "secondary";
          btn.style.fontSize = "0.78rem";
          btn.textContent = tr("wizard.sources.run_check");
          if (this.state.wu_check && this.state.wu_check.status === "running") {
            btn.disabled = true;
            btn.textContent = "…";
          }
          btn.addEventListener("click", () => this.runDeferredCheck());
          tdAct.appendChild(btn);
        }
        trr.appendChild(tdAct);
        tb.appendChild(trr);
      }
      tbl.appendChild(tb);
      host.appendChild(tbl);
      // Banner — green if all clear, warn if anything outdated.
      const banner = document.createElement("div");
      if (outdatedTotal > 0) {
        banner.className = "wizard-sources-banner";
        banner.textContent = tr("wizard.sources.upgradeable_banner")
          .replace("{count}", String(outdatedTotal));
      } else {
        banner.className = "wizard-sources-banner ok";
        banner.textContent = tr("wizard.sources.all_clear_banner");
      }
      host.appendChild(banner);
      // Surface the live deferred-check status under the table.
      // Per-adapter wording (e.g. "Running Windows Update check…" vs
      // "Running softwareupdate check…").
      if (this.state.wu_check) {
        const note = document.createElement("p");
        note.className = "dim";
        note.style.fontSize = "0.82rem";
        if (this.state.wu_check.status === "running") {
          note.textContent = this.osTr("deferred_check_running");
        } else if (this.state.wu_check.status === "done") {
          note.textContent = this.osTr("deferred_check_done")
            .replace("{count}", String(this.state.wu_check.count || 0));
        } else if (this.state.wu_check.status === "failed") {
          note.textContent = this.osTr("deferred_check_failed");
        }
        if (note.textContent) host.appendChild(note);
      }
    },
    // Renamed from runWindowsUpdateCheck — runs a check against the
    // adapter's deferred-check source (windows_update / softwareupdate
    // / etc.). Linux has no deferred source so this is a no-op there.
    async runDeferredCheck() {
      const deferredId = this.osTr("deferred_check_id");
      if (!deferredId) return;
      this.state.wu_check = {status: "running"};
      this.render();
      try {
        const r = await api.post("/runs/async", {
          profile: "quick",
          phases: ["check"],
          categories: [deferredId],
          dry_run: true,
        });
        const runId = r.run_id;
        // Subscribe to the SSE for events; resolve once we see "done".
        let count = 0;
        await new Promise((resolve, reject) => {
          const es = new EventSource(`/runs/${runId}/events`);
          es.addEventListener("sidecar", e => {
            try {
              const sc = JSON.parse(e.data);
              const summ = sc.summary || {};
              count += (summ.total ?? (sc.items || []).length) || 0;
            } catch {}
          });
          es.addEventListener("done", () => {
            try { es.close(); } catch {}
            resolve();
          });
          es.onerror = () => {
            try { es.close(); } catch {}
            reject(new Error("stream closed"));
          };
          // Hard timeout 60s.
          setTimeout(() => { try { es.close(); } catch {}; resolve(); }, 60000);
        });
        this.state.wu_check = {status: "done", count};
      } catch (e) {
        this.state.wu_check = {status: "failed", error: String(e)};
      }
      this.render();
    },
    // ── Step 6: Done — try a dry-run + finish ─────────────────────
    build_done(host) {
      const h = document.createElement("h3");
      h.textContent = tr("wizard.done.title");
      host.appendChild(h);
      // A — dry-run (against the adapter's primary category — winget on
      // Windows, brew on macOS, apt on Linux).
      const dryS = document.createElement("div");
      dryS.className = "wizard-done-section";
      const dryH = document.createElement("h4");
      dryH.textContent = this.osTr("dry_h");
      dryS.appendChild(dryH);
      const dryP = document.createElement("p");
      dryP.textContent = this.osTr("dry_body");
      dryS.appendChild(dryP);
      const row = document.createElement("div");
      row.className = "wizard-dryrun-row";
      const runBtn = document.createElement("button");
      runBtn.type = "button";
      runBtn.id = "wizard-dryrun-btn";
      runBtn.textContent = this.osTr("dry_btn");
      const skipBtn = document.createElement("button");
      skipBtn.type = "button";
      skipBtn.className = "secondary";
      skipBtn.textContent = tr("wizard.done.skip_dry");
      skipBtn.addEventListener("click", () => {
        this.state.dry_run_seen = true;
        this.saveScratch();
        this.render();
      });
      const dryStatus = document.createElement("span");
      dryStatus.className = "dim";
      dryStatus.id = "wizard-dryrun-status";
      dryStatus.style.fontSize = "0.85rem";
      if (this.state.dry_run && this.state.dry_run.status === "done") {
        dryStatus.textContent = this.osTr("dry_done")
          .replace("{items}", String(this.state.dry_run.items || 0));
      } else if (this.state.dry_run && this.state.dry_run.status === "running") {
        dryStatus.textContent = this.osTr("dry_running");
      } else if (this.state.dry_run && this.state.dry_run.status === "failed") {
        dryStatus.textContent = tr("wizard.done.dry_failed")
          .replace("{error}", this.state.dry_run.error || "");
      }
      runBtn.addEventListener("click", () => this.runDryRun(dryStatus));
      row.appendChild(runBtn); row.appendChild(skipBtn); row.appendChild(dryStatus);
      dryS.appendChild(row);
      const dryOut = document.createElement("pre");
      dryOut.className = "wizard-dryrun-output";
      dryOut.id = "wizard-dryrun-out";
      dryS.appendChild(dryOut);
      host.appendChild(dryS);
      // B — apply for real. apply_1 + apply_2 are adapter-neutral; the
      // CLI example (apply_3) names the adapter's primary category, so
      // it's adapter-specific.
      const appS = document.createElement("div");
      appS.className = "wizard-done-section";
      const appH = document.createElement("h4");
      appH.textContent = tr("wizard.done.apply_h");
      appS.appendChild(appH);
      const ul = document.createElement("ul");
      const applyLines = [
        tr("wizard.done.apply_1"),
        tr("wizard.done.apply_2"),
        this.osTr("cli_apply"),
      ];
      applyLines.forEach(text => {
        const li = document.createElement("li");
        li.textContent = text;
        ul.appendChild(li);
      });
      appS.appendChild(ul);
      host.appendChild(appS);
      // C — where to find things
      const whS = document.createElement("div");
      whS.className = "wizard-done-section wizard-done-where";
      const whH = document.createElement("h4");
      whH.textContent = tr("wizard.done.where_h");
      whS.appendChild(whH);
      const whUl = document.createElement("ul");
      [["where_overview","overview"],["where_categories","categories"],
       ["where_run","run"],["where_history","history"],["where_logs","logs"],
       ["where_help","help"]].forEach(([k, view]) => {
        const li = document.createElement("li");
        const a = document.createElement("a");
        a.href = `#${view}`;
        a.textContent = tr(`wizard.done.${k}`);
        a.addEventListener("click", () => {
          // Defer view-switch until after Finish closes the modal.
          ui.wizard.targetView = view;
        });
        li.appendChild(a);
        whUl.appendChild(li);
      });
      whS.appendChild(whUl);
      host.appendChild(whS);
      // Footer hint
      if (!this.state.dry_run_seen) {
        const hint = document.createElement("p");
        hint.className = "wizard-finish-hint";
        hint.textContent = tr("wizard.done.finish_hint");
        host.appendChild(hint);
      }
    },
    async runDryRun(statusEl) {
      this.state.dry_run = {status: "running"};
      const out = $("#wizard-dryrun-out");
      if (out) out.textContent = "";
      const btn = $("#wizard-dryrun-btn");
      if (btn) btn.disabled = true;
      if (statusEl) statusEl.textContent = this.osTr("dry_running");
      // The dry-run targets the adapter's primary category — winget
      // on Windows, brew on macOS, apt on Linux. dry_category falls
      // back to 'winget' via osTr's windows-default if missing.
      const dryCat = this.osTr("dry_category") || "winget";
      try {
        const r = await api.post("/runs/async", {
          profile: "quick",
          phases: ["plan"],
          categories: [dryCat],
          dry_run: true,
        });
        const runId = r.run_id;
        let items = 0;
        await new Promise((resolve, reject) => {
          const es = new EventSource(`/runs/${runId}/events`);
          es.addEventListener("sidecar", e => {
            try {
              const sc = JSON.parse(e.data);
              const summ = sc.summary || {};
              const total = summ.total ?? (sc.items || []).length;
              items += total || 0;
              if (out) {
                const ln = `[${sc.phase}:${sc.category}] ${sc.status || "ok"} - ${total || 0} items\n`;
                out.textContent += ln;
              }
            } catch {}
          });
          es.addEventListener("log", e => {
            try {
              const m = JSON.parse(e.data);
              if (out && m.line) {
                out.textContent += m.line + "\n";
                out.scrollTop = out.scrollHeight;
              }
            } catch {}
          });
          es.addEventListener("done", () => {
            try { es.close(); } catch {}
            resolve();
          });
          es.onerror = () => {
            try { es.close(); } catch {}
            // Some adapters' events stream closes after first done; treat as success.
            resolve();
          };
          setTimeout(() => { try { es.close(); } catch {}; resolve(); }, 90000);
        });
        this.state.dry_run = {status: "done", items};
        this.state.dry_run_seen = true;
        this.saveScratch();
      } catch (e) {
        this.state.dry_run = {status: "failed", error: String(e)};
      }
      if (btn) btn.disabled = false;
      this.render();
    },
    // ── Step 7 (dev only): GitHub repo config ──────────────────────
    // Lets the operator name the source repo Ascendo polls for update
    // notifications. The Test button does a single GET against the
    // GitHub releases API with no auth header — works for any public
    // repo without burning a token.
    build_github_config(host) {
      const h = document.createElement("h3");
      h.textContent = tr("wizard.github.title");
      host.appendChild(h);
      const body = document.createElement("p");
      body.textContent = tr("wizard.github.body");
      host.appendChild(body);
      const row = document.createElement("div");
      row.className = "wizard-dryrun-row";
      const label = document.createElement("label");
      label.textContent = tr("wizard.github.label");
      label.style.marginRight = "0.5rem";
      const input = document.createElement("input");
      input.type = "text";
      input.id = "wizard-gh-repo";
      input.value = this.state.github_repo || "KasprowiczM/ascendo";
      input.placeholder = "owner/repo";
      input.style.minWidth = "260px";
      input.addEventListener("input", (e) => {
        this.state.github_repo = e.target.value.trim();
      });
      const testBtn = document.createElement("button");
      testBtn.type = "button";
      testBtn.textContent = tr("wizard.github.test_btn");
      const skipLink = document.createElement("button");
      skipLink.type = "button";
      skipLink.className = "secondary";
      skipLink.textContent = tr("wizard.github.skip");
      skipLink.addEventListener("click", () => this.advance());
      const status = document.createElement("span");
      status.className = "dim";
      status.id = "wizard-gh-status";
      status.style.fontSize = "0.85rem";
      status.style.marginLeft = "0.5rem";
      if (this.state.github_test) {
        status.textContent = (this.state.github_test.status === "ok")
          ? tr("wizard.github.test_ok").replace("{tag}", this.state.github_test.tag || "")
          : tr("wizard.github.test_failed").replace("{error}", this.state.github_test.message || "");
      }
      testBtn.addEventListener("click", () => this.testGithubRepo(status));
      label.appendChild(input);
      row.appendChild(label);
      row.appendChild(testBtn);
      row.appendChild(skipLink);
      row.appendChild(status);
      host.appendChild(row);
    },
    async testGithubRepo(statusEl) {
      const repo = (this.state.github_repo || "").trim();
      if (!/^[\w.-]+\/[\w.-]+$/.test(repo)) {
        this.state.github_test = {status: "failed", message: tr("wizard.github.bad_format")};
        if (statusEl) statusEl.textContent = tr("wizard.github.test_failed")
          .replace("{error}", tr("wizard.github.bad_format"));
        return;
      }
      if (statusEl) statusEl.textContent = tr("wizard.github.testing");
      try {
        const r = await fetch(`https://api.github.com/repos/${repo}/releases/latest`, {
          headers: {accept: "application/vnd.github+json"},
        });
        if (r.status === 404) {
          this.state.github_test = {status: "failed", message: "404 — no releases yet (this is OK for new repos)"};
          if (statusEl) statusEl.textContent = tr("wizard.github.test_404");
          return;
        }
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const data = await r.json();
        const tag = (data && data.tag_name) || "(unknown)";
        this.state.github_test = {status: "ok", tag};
        if (statusEl) statusEl.textContent = tr("wizard.github.test_ok").replace("{tag}", tag);
      } catch (e) {
        this.state.github_test = {status: "failed", message: String(e)};
        if (statusEl) statusEl.textContent = tr("wizard.github.test_failed")
          .replace("{error}", String(e));
      }
    },
    // ── Step 8 (dev only): Dev-sync provider setup ─────────────────
    // GET /sync/config-status reports whether .dev_sync_config.json
    // exists + parses + has a provider; POST /sync/setup shells out to
    // dev-sync/dev-sync-provider-setup.sh. Both are gated by the
    // EditionGateMiddleware so they never reach this step on basic.
    build_dev_sync_setup(host) {
      const h = document.createElement("h3");
      h.textContent = tr("wizard.devsync.title");
      host.appendChild(h);
      const body = document.createElement("p");
      body.textContent = tr("wizard.devsync.body");
      host.appendChild(body);
      const statusBox = document.createElement("div");
      statusBox.className = "wizard-admin-callout";
      statusBox.id = "wizard-devsync-status-box";
      const statusH = document.createElement("h4");
      statusH.textContent = tr("wizard.devsync.status_h");
      const statusLine = document.createElement("p");
      statusLine.id = "wizard-devsync-status";
      statusLine.textContent = tr("wizard.devsync.checking");
      statusBox.appendChild(statusH);
      statusBox.appendChild(statusLine);
      host.appendChild(statusBox);
      const row = document.createElement("div");
      row.className = "wizard-dryrun-row";
      const setupBtn = document.createElement("button");
      setupBtn.type = "button";
      setupBtn.id = "wizard-devsync-setup-btn";
      setupBtn.textContent = tr("wizard.devsync.setup_btn");
      setupBtn.addEventListener("click", () => this.runDevSyncSetup());
      const skipLink = document.createElement("button");
      skipLink.type = "button";
      skipLink.className = "secondary";
      skipLink.textContent = tr("wizard.devsync.skip");
      skipLink.addEventListener("click", () => this.advance());
      row.appendChild(setupBtn);
      row.appendChild(skipLink);
      host.appendChild(row);
      const out = document.createElement("pre");
      out.className = "wizard-dryrun-output";
      out.id = "wizard-devsync-out";
      host.appendChild(out);
      // Probe status on entry; cached result wins to avoid hammering disk
      // on every render() pass.
      if (!this.state.dev_sync_config) {
        this.refreshDevSyncStatus(statusLine);
      } else {
        this.renderDevSyncStatus(statusLine, this.state.dev_sync_config);
      }
    },
    renderDevSyncStatus(el, cfg) {
      if (!el) return;
      if (cfg.present && cfg.valid) {
        el.textContent = tr("wizard.devsync.status_present")
          .replace("{provider}", cfg.provider || "(no provider set)")
          .replace("{path}", cfg.path || "");
      } else if (cfg.present && !cfg.valid) {
        el.textContent = tr("wizard.devsync.status_invalid").replace("{path}", cfg.path || "");
      } else {
        el.textContent = tr("wizard.devsync.status_missing").replace("{path}", cfg.path || "");
      }
    },
    async refreshDevSyncStatus(el) {
      try {
        const cfg = await api.get("/sync/config-status");
        this.state.dev_sync_config = cfg;
        this.renderDevSyncStatus(el, cfg);
      } catch (e) {
        if (el) el.textContent = tr("wizard.devsync.status_error").replace("{error}", String(e));
      }
    },
    async runDevSyncSetup() {
      const out = $("#wizard-devsync-out");
      const btn = $("#wizard-devsync-setup-btn");
      const statusLine = $("#wizard-devsync-status");
      if (out) out.textContent = tr("wizard.devsync.running") + "\n";
      if (btn) btn.disabled = true;
      try {
        const r = await api.post("/sync/setup", {});
        this.state.dev_sync_setup_run = r;
        if (out) {
          const lines = [];
          if (r.stdout) lines.push("--- stdout ---", r.stdout);
          if (r.stderr) lines.push("--- stderr ---", r.stderr);
          lines.push(`--- exit code: ${r.exit_code} ---`);
          out.textContent = lines.join("\n");
        }
        // Re-probe — config may now exist.
        await this.refreshDevSyncStatus(statusLine);
      } catch (e) {
        this.state.dev_sync_setup_run = {ok: false, error: String(e)};
        if (out) out.textContent = tr("wizard.devsync.setup_failed").replace("{error}", String(e));
      }
      if (btn) btn.disabled = false;
    },
    // ── Step 9 (dev only): Developer resources ────────────────────
    // Static reference list. PLAN.md / HANDOFF.md ride in the dev-sync
    // overlay so we render them as plain text rather than clickable
    // links — those files are never served by the dashboard.
    build_dev_resources(host) {
      const h = document.createElement("h3");
      h.textContent = tr("wizard.devresources.title");
      host.appendChild(h);
      const body = document.createElement("p");
      body.textContent = tr("wizard.devresources.intro");
      host.appendChild(body);
      const ul = document.createElement("ul");
      // GitHub link is clickable; the rest are descriptive (overlay
      // files aren't served by the dashboard).
      const ghRepo = this.state.github_repo || "KasprowiczM/ascendo";
      const ghLi = document.createElement("li");
      const ghA = document.createElement("a");
      ghA.href = `https://github.com/${ghRepo}`;
      ghA.target = "_blank";
      ghA.rel = "noopener noreferrer";
      ghA.textContent = tr("wizard.devresources.github").replace("{repo}", ghRepo);
      ghLi.appendChild(ghA);
      ul.appendChild(ghLi);
      [
        ["plan", "PLAN.md"],
        ["handoff", "HANDOFF.md"],
        ["guide", "DEV_GUIDE.md"],
      ].forEach(([key, file]) => {
        const li = document.createElement("li");
        const code = document.createElement("code");
        code.textContent = file;
        li.appendChild(code);
        li.appendChild(document.createTextNode(" — " + tr(`wizard.devresources.${key}`)));
        ul.appendChild(li);
      });
      host.appendChild(ul);
      const note = document.createElement("p");
      note.className = "dim";
      note.style.fontSize = "0.85rem";
      note.textContent = tr("wizard.devresources.overlay_note");
      host.appendChild(note);
    },
    async finalize(skip) {
      const choices = {
        language: this.state.language || "en",
        theme: this.state.theme || "dark",
        admin_authorised: !!this.state.admin_authorised,
        inventory_seen: !!this.state.inventory_seen,
        dry_run_seen: !!this.state.dry_run_seen,
        skipped: !!skip,
        step: this.currentIdx + 1,
      };
      // Persist language locally too so a fresh load doesn't re-prompt
      // even before /onboarding/state has been written.
      try {
        localStorage.setItem("ui-locale", choices.language);
        localStorage.setItem("ui-theme",  choices.theme);
      } catch {}
      try {
        await api.post("/onboarding/complete", choices);
      } catch (e) { console.warn("wizard finalize:", e); }
      this.clearScratch();
      $("#wizard-modal").classList.add("hidden");
      // If user clicked a "where to find" link on step 6, hop to that view.
      if (this.targetView) {
        try { ui.show(this.targetView); } catch {}
        this.targetView = null;
      } else {
        try { ui.show("overview"); } catch {}
      }
    },
  },
  // Legacy alias: some external integrations still call ui.finishWizard().
  async finishWizard(skip) {
    await ui.wizard.finalize(skip);
  },

  async checkRebootBanner() {
    try {
      const p = await api.get("/preflight");
      const banner = $("#reboot-banner");
      if (p.needs_reboot) banner.classList.remove("hidden");
      else                banner.classList.add("hidden");
    } catch {}
  },

  async rebootNow() {
    if (!confirm(
      tr("overview.reboot_confirm")
      || "Restart the computer now? Any unsaved work will be lost."
    )) return;
    const ok = await sudoMgr.ensure();
    if (!ok) { ui.status(tr("overview.reboot_no_sudo") || "sudo required"); return; }
    try {
      await api.post("/system/reboot?delay=5", {});
      ui.status(tr("overview.reboot_scheduled") || "reboot scheduled in 5s - saving your work now is recommended");
      $("#reboot-banner").innerHTML =
        `<span class="reboot-banner-icon">⏻</span> rebooting in 5 seconds…`;
    } catch (e) { ui.status(String(e)); }
  },
  status(msg) { $("#status-line").textContent = msg; },
  badge(status) {
    const cls = (status || "").toLowerCase();
    return `<span class="badge ${cls}">${status || "?"}</span>`;
  },
  fmtTime(s) {
    if (!s) return "-";
    const d = new Date(s);
    if (isNaN(d.getTime())) return s;
    return d.toLocaleString();
  },
  // Compute a relative-time staleness label + a token-based color hint
  // for the Overview "Last run" card. Buckets:
  //   <  1h         -> fresh      (--ok)
  //   <  6h         -> fresh      (--ok)
  //   < 24h         -> ok         (--fg-muted)
  //   <  7d         -> stale      (--warn)
  //   >= 7d         -> very_stale (--err)
  //   no timestamp  -> never      (--fg-muted)
  staleness(isoStr) {
    if (!isoStr) return { key: "never", color: "var(--fg-muted)", label: tr("overview.staleness_never") };
    const d = new Date(isoStr);
    if (isNaN(d.getTime())) return { key: "never", color: "var(--fg-muted)", label: tr("overview.staleness_never") };
    const ms = Date.now() - d.getTime();
    if (ms < 0) return { key: "ok", color: "var(--fg-muted)", label: tr("overview.staleness_prefix") + " " + tr("overview.staleness_just_now") };
    const min = Math.floor(ms / 60000);
    const hr  = Math.floor(ms / 3600000);
    const day = Math.floor(ms / 86400000);
    let key, color, rel;
    if (min < 60) {
      key = "fresh"; color = "var(--ok)";
      rel = (min <= 1) ? tr("overview.staleness_just_now")
                       : tr("overview.staleness_minutes_ago").replace("{n}", min);
    } else if (hr < 6) {
      key = "fresh"; color = "var(--ok)";
      rel = tr("overview.staleness_hours_ago").replace("{n}", hr);
    } else if (hr < 24) {
      key = "ok"; color = "var(--fg-muted)";
      rel = tr("overview.staleness_hours_ago").replace("{n}", hr);
    } else if (day < 7) {
      key = "stale"; color = "var(--warn)";
      rel = tr("overview.staleness_days_ago").replace("{n}", day);
    } else {
      key = "very_stale"; color = "var(--err)";
      rel = tr("overview.staleness_days_ago").replace("{n}", day);
    }
    return { key, color, label: tr("overview.staleness_prefix") + " " + rel };
  },

  async loadHealth() {
    const card = $("#health-card");
    if (!card) return;
    try {
      const h = await api.get("/health/check");
      if (!h.available) {
        card.innerHTML = `<p class="dim">${tr("health.no_data")}</p>`;
        return;
      }
      const score = h.score ?? 0;
      const cls = score >= 85 ? "good" : score >= 60 ? "warn" : "bad";
      const lbl = score >= 85 ? tr("health.good")
                : score >= 60 ? tr("health.warn")
                : tr("health.bad");
      const issues = h.issues || [];
      card.innerHTML =
        `<div class="health-score ${cls}">${score}<span style="font-size:0.5em;color:var(--dim)">/100</span></div>
         <div style="text-align:center"><b>${lbl}</b></div>
         ${issues.length ? `<div class="health-issues"><ul>${issues.map(i =>
            `<li><span class="badge ${i.severity === "err" ? "fail" : "warn"}">${i.severity}</span> ${i.msg}</li>`
          ).join("")}</ul></div>` : ""}
         ${h.run_id ? `<div class="dim" style="font-size:0.7rem;margin-top:0.4rem">run: <code>${h.run_id}</code></div>` : ""}`;
    } catch (e) { card.innerHTML = `<p class="dim">${e}</p>`; }
  },

  async loadSuggestions() {
    const wrap = $("#suggest-list");
    if (wrap) wrap.innerHTML = `<span class="spinner"></span> ${tr("overview.scanning")}`;
    // Load both the preloaded library + (legacy) /suggestions in parallel.
    let items = [];
    try {
      const lib = await api.get("/suggestions/library");
      items = lib.items || [];
    } catch (_e) {
      try {
        const legacy = await api.get("/suggestions");
        items = legacy.items || legacy.suggestions || [];
      } catch (_e2) { items = []; }
    }
    if (wrap) {
      if (!items.length) {
        wrap.innerHTML = `<p class="dim">${tr("suggest.empty") || "No suggestions right now."}</p>`;
      } else {
        wrap.innerHTML = items.map(s => {
          const sev = s.severity || "info";
          const aiBadge = s.ai_generated
            ? `<span class="badge ok">${tr("suggest.ai_badge") || "AI"}</span>`
            : `<span class="badge">${tr("suggest.rule_badge") || "rule"}</span>`;
          const action = s.action || {};
          const apply = (action.type === "run_async" && action.payload)
            ? `<button data-sg-run-async='${JSON.stringify(action.payload).replace(/'/g, "&#39;")}'>${(action.label || tr("suggest.btn_apply") || "Apply")}</button>`
            : ((s.diff||[]).length
                ? `<button data-sg-apply='${JSON.stringify({id:s.id,diff:s.diff})}'>${tr("suggest.btn_apply") || "Apply"}</button>`
                : "");
          return `
            <div class="suggest-card severity-${sev}" data-sid="${s.id}">
              <div class="suggest-card-meta">${sev}${aiBadge ? " · " + aiBadge : ""}${s.category ? " · " + s.category : ""}</div>
              <div class="suggest-card-title">${s.title || ""}</div>
              <div class="suggest-card-body">${s.body || s.rationale || ""}</div>
              <div class="suggest-card-actions">
                ${apply}
                <button class="secondary" data-sg-dismiss="${s.id}">${tr("suggest.btn_dismiss") || "Dismiss"}</button>
              </div>
            </div>`;
        }).join("");
      }
    }
    // Initialize the 3-step AI wizard once.
    if (typeof ui.initAiWizard === "function") {
      try { await ui.initAiWizard(); } catch (_e) { /* ignore */ }
    }
  },

  async applySuggestion(payload) {
    try {
      const r = await api.post("/suggestions/apply", payload);
      ui.status(`applied: ${(r.changes||[]).map(c=>c.file).join(", ")}`);
      ui._loaded.suggest = false; ui.loadSuggestions();
    } catch (e) { ui.status(String(e)); }
  },
  async dismissSuggestion(sid) {
    try { await api.post("/suggestions/dismiss", {id: sid}); }
    catch (e) { ui.status(String(e)); return; }
    ui._loaded.suggest = false; ui.loadSuggestions();
  },

  async loadOverview(opts) {
    opts = opts || {};
    const refresh = !!opts.refresh;
    try {
      const h = await api.get("/health");
      $("#hostbadge").textContent = h.repo_root || "";
    } catch {}
    try {
      const runs = (await api.get("/runs?limit=1")).runs;
      const last = runs[0];
      // Staleness line is dev-only chrome; suppress in basic edition.
      const isBasic = window.ASCENDO_EDITION === "basic";
      if (last) {
        const stale = ui.staleness(last.started_at);
        $("#last-run").innerHTML = `${ui.badge(last.status)} <code>${last.id}</code><br>
           <span class="dim">${ui.fmtTime(last.started_at)} → ${ui.fmtTime(last.ended_at)}</span><br>
           profile: ${last.profile || "-"}, dry-run: ${last.dry_run ? "yes" : "no"}<br>
           ${last.needs_reboot ? `<b>${tr("overview.reboot_required")}</b><br>` : ""}
           ${isBasic ? "" : `<span class="meta" style="color:${stale.color};font-weight:600" data-staleness="${stale.key}">${stale.label}</span>`}`;
      } else {
        const stale = ui.staleness(null);
        $("#last-run").innerHTML = `<span class='dim'>${tr("overview.no_runs")}</span>${isBasic ? "" : `<br>
           <span class="meta" style="color:${stale.color}" data-staleness="${stale.key}">${stale.label}</span>`}`;
      }
    } catch (e) { $("#last-run").textContent = String(e); }
    try {
      const p = await api.get("/preflight");
      // Backend /preflight currently returns {ok, checks, warnings, errors}.
      // Some legacy paths instead returned {needs_reboot, items: [{tool, present}]}.
      // Accept BOTH shapes so the System Health card never throws on
      // ``undefined.map`` and instead renders whatever it has.
      const items = Array.isArray(p.items) ? p.items : [];
      const errors = Array.isArray(p.errors) ? p.errors : [];
      const warnings = Array.isArray(p.warnings) ? p.warnings : [];
      const parts = [];
      if (p.needs_reboot) parts.push(`<b>${tr("overview.reboot_pending")}</b>`);
      if (items.length) {
        parts.push(items.map(i =>
          `<span class="badge ${i.present ? "ok" : "warn"}">${i.tool || ""}</span>`
        ).join(" "));
      } else if (errors.length || warnings.length) {
        if (errors.length) parts.push(
          errors.map(e => `<span class="badge fail">${(e.msg || e).toString()}</span>`).join(" "));
        if (warnings.length) parts.push(
          warnings.map(w => `<span class="badge warn">${(w.msg || w).toString()}</span>`).join(" "));
      } else {
        // Genuinely all-clear — render an unobtrusive ok pill.
        parts.push(`<span class="badge ok">${p.ok === false ? "issues" : "ok"}</span>`);
      }
      $("#preflight").innerHTML = parts.join("<br>");
    } catch (e) { $("#preflight").textContent = String(e); }
    if (window.ASCENDO_EDITION !== "basic") {
      try {
        const g = await api.get("/git/status");
        $("#git-status").innerHTML = `branch <code>${g.branch || "(unknown)"}</code> ` +
          (g.dirty ? "<span class='badge warn'>dirty</span>" : "<span class='badge ok'>clean</span>") +
          ` <span class="dim">↑${g.ahead} ↓${g.behind}</span>`;
      } catch (e) { $("#git-status").textContent = String(e); }
    } else {
      // Hide the entire #git-status card in basic edition.
      const card = $("#git-status");
      if (card) {
        const cardWrap = card.closest(".card");
        if (cardWrap) cardWrap.style.display = "none";
      }
    }
    // Inventory charts (slow scan, runs after the rest paints).
    // Forward {refresh: true} so post-apply repaint actually busts the
    // backend cache + repaints the donut/bar charts with new totals.
    ui.loadInventoryDashboard({ refresh });
    ui.loadHealth();
  },

  // -- SVG donut + bar charts (pure DOM, no chart libs) -----------------
  renderDonut(elId, segments) {
    const total = segments.reduce((a, s) => a + (s.value||0), 0);
    const host = $("#"+elId);
    if (total === 0) {
      host.innerHTML = `<p class="dim" style="text-align:center;padding:2rem">-</p>`;
      return;
    }
    // Bigger, anti-aliased donut with rounded line caps + visible center
    // total + always-visible legend below the SVG (no negative margins).
    const r = 64, cx = 80, cy = 80, sw = 18;
    const C = 2 * Math.PI * r;
    let off = 0, arcs = "";
    for (const seg of segments) {
      if (!seg.value) continue;
      const len = (seg.value / total) * C;
      arcs += `<circle cx="${cx}" cy="${cy}" r="${r}" fill="none"
                       stroke="${seg.color}" stroke-width="${sw}"
                       stroke-linecap="round"
                       stroke-dasharray="${len.toFixed(2)} ${(C - len).toFixed(2)}"
                       stroke-dashoffset="${(-off).toFixed(2)}" />`;
      off += len;
    }
    const okPct = Math.round(((segments.find(s=>s.label==="ok")||{}).value||0) * 100 / total);
    host.innerHTML = `
      <div class="donut-wrap">
        <svg viewBox="0 0 160 160" width="180" height="180" role="img"
             aria-label="status donut">
          <g transform="rotate(-90 80 80)">
            <circle cx="${cx}" cy="${cy}" r="${r}" fill="none"
                    stroke="var(--border)" stroke-width="${sw}" />
            ${arcs}
          </g>
          <text x="80" y="76" text-anchor="middle" font-size="30" font-weight="700"
                fill="currentColor">${total}</text>
          <text x="80" y="98" text-anchor="middle" font-size="11" fill="var(--dim)">
                ${okPct}% ok</text>
        </svg>
      </div>
      <div class="donut-legend">
        ${segments.filter(s => s.value).map(s => {
          const pct = Math.round(s.value * 100 / total);
          return `<span><span class="swatch" style="background:${s.color}"></span>${s.label}: <b>${s.value}</b> <span class="dim">(${pct}%)</span></span>`;
        }).join("")}
      </div>`;
  },

  renderBars(elId, perCat) {
    const rows = Object.entries(perCat).map(([cat, c]) => {
      const total = c.total || 1;
      return `<div class="bar-row">
        <span class="bar-label mono">${cat}</span>
        <span class="bar-track">
          <span class="bar-fill-ok"       style="width:${(c.ok/total)*100}%"></span>
          <span class="bar-fill-outdated" style="width:${(c.outdated/total)*100}%"></span>
          <span class="bar-fill-missing"  style="width:${(c.missing/total)*100}%"></span>
        </span>
        <span class="bar-counts">${c.ok}/${c.outdated}/${c.missing} (${c.total})</span>
      </div>`;
    }).join("");
    $("#"+elId).innerHTML = rows + `
      <p class="dim" style="margin-top:0.5rem;font-size:0.75rem">
        <span style="color:var(--ok)">█ ok</span> /
        <span style="color:var(--warn)">█ outdated</span> /
        <span style="color:var(--err)">█ missing</span>
      </p>`;
  },

  // Apps view in-memory state (filters/groups). Lives across re-renders
  // until the page reloads.
  _appsState: {
    apps: [],
    search: "",
    categories: new Set(),  // empty = ALL
    statuses: new Set(),    // empty = ALL
    collapsed: new Set(),   // category names with collapsed body
  },

  async loadApps(opts) {
    opts = opts || {};
    const refresh = !!opts.refresh;
    const wrap = $("#apps-table-wrap");
    const summary = $("#apps-summary");
    if (!wrap) return;
    const cached = !refresh && frontendCache._store.has(frontendCache._key("/apps/detect"));
    if (!cached) {
      wrap.textContent = "";
      const spinner = document.createElement("span");
      spinner.className = "spinner";
      wrap.appendChild(spinner);
      wrap.appendChild(document.createTextNode(" " + (tr("overview.scanning") || "Scanning…")));
      if (summary) summary.textContent = "";
    }

    try {
      const data = await frontendCache.get("/apps/detect", { refresh });
      const apps = data.apps || [];
      const sum = data.summary || {total: 0, tracked: 0, excluded: 0, missing: 0};

      if (summary) {
        summary.textContent = "";
        const pill = (cls, label, n) => {
          const s = document.createElement("span");
          s.className = "st-pill " + cls;
          s.textContent = `${label} ${n}`;
          return s;
        };
        summary.appendChild(pill("st-info",  tr("apps.pill_total")    || "total",    sum.total));
        summary.appendChild(document.createTextNode(" "));
        summary.appendChild(pill("st-ok",    tr("apps.pill_tracked")  || "in config", sum.tracked));
        summary.appendChild(document.createTextNode(" "));
        summary.appendChild(pill("st-skip",  tr("apps.pill_excluded") || "excluded",  sum.excluded));
      }

      // Backfill status if backend left it blank.
      apps.forEach(a => {
        const inst = (a.installed || "").trim();
        const cand = (a.candidate || "").trim();
        if (!a.status || a.status === "unknown") {
          if (!inst && cand) a.status = "missing";
          else if (inst && cand && inst !== cand) a.status = "outdated";
          else a.status = "ok";
        }
      });

      ui._appsState.apps = apps;
      ui._renderAppsFilters();
      ui._renderAppsTable();
    } catch (e) {
      wrap.textContent = String(e);
    }
  },

  _renderAppsFilters() {
    const apps = ui._appsState.apps;
    const search = $("#apps-search");
    const statuses = $("#apps-status-chips");
    const cats = $("#apps-category-chips");
    if (search && !search.dataset.bound) {
      search.dataset.bound = "1";
      search.placeholder = tr("apps.search_placeholder") || "Search apps…";
      let t = null;
      search.addEventListener("input", () => {
        if (t) clearTimeout(t);
        t = setTimeout(() => {
          ui._appsState.search = (search.value || "").trim().toLowerCase();
          ui._renderAppsTable();
        }, 200);
      });
    }
    if (search && ui._appsState.search && !search.value) {
      search.value = ui._appsState.search;
    }

    if (statuses) {
      statuses.textContent = "";
      const counts = {ok: 0, outdated: 0, missing: 0};
      apps.forEach(a => { counts[a.status] = (counts[a.status] || 0) + 1; });
      ["ok", "outdated", "missing"].forEach(st => {
        if (!counts[st]) return;
        const chip = document.createElement("button");
        chip.type = "button";
        chip.className = "chip";
        if (ui._appsState.statuses.has(st)) chip.classList.add("active");
        chip.dataset.appsStatusChip = st;
        const label = (tr("apps.st_" + st) || st);
        chip.innerHTML = `${label}<span class="chip-count">${counts[st]}</span>`;
        statuses.appendChild(chip);
      });
    }

    if (cats) {
      cats.textContent = "";
      const counts = {};
      apps.forEach(a => {
        const c = a.category || "unknown";
        counts[c] = (counts[c] || 0) + 1;
      });
      Object.keys(counts).sort().forEach(c => {
        const chip = document.createElement("button");
        chip.type = "button";
        chip.className = "chip";
        if (ui._appsState.categories.has(c)) chip.classList.add("active");
        chip.dataset.appsCategoryChip = c;
        chip.innerHTML = `${c}<span class="chip-count">${counts[c]}</span>`;
        cats.appendChild(chip);
      });
    }
  },

  _renderAppsTable() {
    const wrap = $("#apps-table-wrap");
    if (!wrap) return;
    wrap.textContent = "";
    const apps = ui._appsState.apps;
    const search = ui._appsState.search;
    const catsF = ui._appsState.categories;
    const stF = ui._appsState.statuses;

    const visible = apps.filter(a => {
      if (search && !(a.name || "").toLowerCase().includes(search)) return false;
      if (catsF.size && !catsF.has(a.category || "unknown")) return false;
      if (stF.size && !stF.has(a.status || "ok")) return false;
      return true;
    });

    if (!visible.length) {
      const e = document.createElement("div");
      e.className = "apps-empty";
      e.textContent = apps.length
        ? (tr("apps.no_match") || "No apps match the current filters.")
        : (tr("apps.empty") || "No apps detected. Run a check from Categories first.");
      wrap.appendChild(e);
      return;
    }

    const groups = {};
    visible.forEach(a => {
      const c = a.category || "unknown";
      (groups[c] = groups[c] || []).push(a);
    });
    Object.keys(groups).sort().forEach(catName => {
      const items = groups[catName];
      items.sort((a, b) =>
        ((a.in_config ? 1 : 0) - (b.in_config ? 1 : 0)) ||
        (a.name || "").localeCompare(b.name || ""));
      const groupEl = document.createElement("div");
      groupEl.className = "apps-group";
      if (ui._appsState.collapsed.has(catName)) groupEl.classList.add("collapsed");
      const header = document.createElement("div");
      header.className = "apps-group-header";
      header.dataset.appsGroup = catName;
      header.innerHTML =
        `<span class="apps-group-arrow">▾</span>` +
        `<span class="apps-group-title">${catName}</span>` +
        `<span class="apps-group-count">${items.length} ${tr("apps.items_label") || "items"}</span>`;
      groupEl.appendChild(header);
      const body = document.createElement("div");
      body.className = "apps-group-body";
      body.appendChild(ui._buildAppsTable(items));
      groupEl.appendChild(body);
      wrap.appendChild(groupEl);
    });
  },

  _buildAppsTable(items) {
    const tbl = document.createElement("table");
    tbl.className = "tbl inv-table";
    const thead = document.createElement("thead");
    const trh = document.createElement("tr");
    [
      tr("categories.col_pkg")     || "Package",
      tr("categories.col_inst")    || "Installed",
      tr("categories.col_cand")    || "Candidate",
      tr("categories.col_status")  || "Status",
      tr("categories.col_src")     || "Source",
      tr("apps.col_in_config")     || "In config",
      tr("categories.col_act")     || "Action",
    ].forEach(label => {
      const th = document.createElement("th");
      th.textContent = label;
      trh.appendChild(th);
    });
    thead.appendChild(trh);
    tbl.appendChild(thead);

    const tbody = document.createElement("tbody");
    const stCls = {ok: "st-ok", outdated: "st-warn", missing: "st-err", unknown: "st-skip"};
    items.forEach(it => {
      const trRow = document.createElement("tr");
      if (!it.in_config) trRow.classList.add("excluded");
      trRow.classList.add("status-" + (it.status || "ok"));
      const addCell = (text, cls) => {
        const td = document.createElement("td");
        if (cls) td.className = cls;
        td.textContent = text;
        trRow.appendChild(td);
      };
      addCell(it.name || "—", "pkg-name");
      addCell(it.installed || "—", "mono");
      addCell(it.candidate || "—", "mono");
      const tdStatus = document.createElement("td");
      const pill = document.createElement("span");
      pill.className = "st-pill " + (stCls[it.status] || "st-skip");
      pill.textContent = it.status || "ok";
      tdStatus.appendChild(pill);
      trRow.appendChild(tdStatus);
      addCell(it.source || it.category || "—", "dim mono");

      const tdToggle = document.createElement("td");
      tdToggle.className = "in-config-toggle";
      const lbl = document.createElement("label");
      lbl.title = it.in_config
        ? (tr("apps.in_config_on_hint")  || "In config — Ascendo will update this app. Uncheck to skip.")
        : (tr("apps.in_config_off_hint") || "Excluded — Ascendo will NOT update this app. Check to re-include.");
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = !!it.in_config;
      cb.dataset.appsToggle = "1";
      cb.dataset.cat = it.category;
      cb.dataset.name = it.name;
      lbl.appendChild(cb);
      lbl.appendChild(document.createTextNode(" "));
      const txt = document.createElement("span");
      txt.className = "dim";
      txt.textContent = it.in_config
        ? (tr("apps.in_config_on")  || "in config")
        : (tr("apps.in_config_off") || "excluded");
      lbl.appendChild(txt);
      tdToggle.appendChild(lbl);
      trRow.appendChild(tdToggle);

      const tdAction = document.createElement("td");
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "secondary";
      btn.style.fontSize = "0.78rem";
      if (it.in_config) {
        btn.textContent = tr("apps.btn_remove") || "Remove from config";
        btn.dataset.appsExclude = "1";
      } else {
        btn.textContent = tr("apps.btn_add") || "+ Add to config";
        btn.dataset.appsInclude = "1";
      }
      btn.dataset.cat = it.category;
      btn.dataset.name = it.name;
      tdAction.appendChild(btn);

      // "View history" link — fetches /apps/{cat}/{name}/history and
      // renders an inline <tr> with the past version transitions.
      tdAction.appendChild(document.createTextNode(" "));
      const histLink = document.createElement("button");
      histLink.type = "button";
      histLink.className = "secondary";
      histLink.style.fontSize = "0.78rem";
      histLink.textContent = tr("apps.history.link") || "History";
      histLink.dataset.appsHistory = "1";
      histLink.dataset.cat = it.category;
      histLink.dataset.name = it.name;
      tdAction.appendChild(histLink);

      trRow.appendChild(tdAction);

      tbody.appendChild(trRow);
    });
    tbl.appendChild(tbody);
    return tbl;
  },

  async toggleExclusion(pkg, cat, on) {
    // ``on`` = checkbox is now CHECKED → user wants this app IN config
    // (i.e. NOT excluded). on=false → user wants the app excluded.
    try {
      const url = on ? "/apps/include" : "/apps/exclude";
      await api.post(url, {category: cat, name: pkg});
      ui.status(on ? `${cat}:${pkg} → in config` : `${cat}:${pkg} → excluded`);
    } catch (e) { ui.status(String(e)); }
  },

  async appsAdd(pkg, cat) {
    // Legacy hook (pre-default-include): "Add to config" now means
    // remove from the exclusion list (= /apps/include).
    try { await api.post("/apps/include", {category: cat, name: pkg}); }
    catch (e) { ui.status(String(e)); return; }
    ui._loaded.apps = false; ui.show("apps");
  },
  async appsRemove(pkg, cat) {
    // Legacy hook (pre-default-include): "Remove from config" now means
    // add to the exclusion list (= /apps/exclude). Does NOT uninstall.
    if (!confirm(`Exclude ${pkg} (${cat}) from updates? (does NOT uninstall — Ascendo will skip it on future runs.)`)) return;
    try { await api.post("/apps/exclude", {category: cat, name: pkg}); }
    catch (e) { ui.status(String(e)); return; }
    ui._loaded.apps = false; ui.show("apps");
  },

  async loadInventoryDashboard(opts) {
    opts = opts || {};
    const refresh = !!opts.refresh;
    const spin = `<span class="spinner"></span> ${tr("overview.scanning")}`;
    // Only paint the spinner if there is no cached value (so a tab
    // switch back to Overview re-renders instantly from cache).
    const haveSummary = window.INV_SUMMARY && !refresh;
    if (!haveSummary) {
      $("#inv-donut").innerHTML  = spin;
      $("#inv-bars").innerHTML   = spin;
      $("#inv-updates").innerHTML = spin;
    }
    try {
      const s = await frontendCache.get("/inventory/summary", { refresh });
      window.INV_SUMMARY = s;
      ui.renderDonut("inv-donut", [
        { label: "ok",       value: s.totals.ok,       color: "var(--ok)" },
        { label: "outdated", value: s.totals.outdated, color: "var(--warn)" },
        { label: "missing",  value: s.totals.missing,  color: "var(--err)" },
      ]);
      ui.renderBars("inv-bars", s.categories);
    } catch (e) { $("#inv-donut").textContent = String(e); $("#inv-bars").textContent = ""; }
    try {
      const all = (await frontendCache.get("/inventory", { refresh })).categories;
      const upd = [];
      for (const [cat, items] of Object.entries(all))
        for (const it of items) if (it.status === "outdated") upd.push({cat, ...it});
      if (!upd.length) {
        $("#inv-updates").innerHTML = `<p class="dim">${tr("overview.no_updates")}</p>`;
      } else {
        $("#inv-updates").innerHTML = `
          <table class="inv-table">
            <thead><tr>
              <th>${tr("categories.col_cat")}</th>
              <th>${tr("categories.col_pkg")}</th>
              <th>${tr("categories.col_inst")}</th>
              <th>${tr("categories.col_cand")}</th>
              <th>${tr("categories.col_source")}</th>
            </tr></thead>
            <tbody>${upd.map(u => `
              <tr class="status-outdated">
                <td>${u.cat}</td>
                <td class="pkg-name">${u.name}</td>
                <td class="dim mono">${u.installed||"-"}</td>
                <td class="mono"><b>${u.candidate||"-"}</b></td>
                <td class="dim">${u.source||""}</td>
              </tr>`).join("")}
            </tbody>
          </table>`;
      }
    } catch (e) { $("#inv-updates").textContent = String(e); }
  },

  async loadCategories(opts) {
    opts = opts || {};
    const refresh = !!opts.refresh;
    const cats = (await frontendCache.get("/categories", { refresh })).categories;
    let summary = window.INV_SUMMARY;
    if (refresh || !summary) {
      try {
        summary = await frontendCache.get("/inventory/summary", { refresh });
      } catch { summary = { categories: {} }; }
    }
    window.INV_SUMMARY = summary;
    const tb = $("#cats-table tbody");
    tb.innerHTML = "";
    for (const c of cats) {
      const counts = (summary.categories && summary.categories[c.id]) || {ok:0,outdated:0,missing:0,total:0};
      const tr = document.createElement("tr");
      tr.className = "cat-row";
      tr.dataset.cat = c.id;
      tr.innerHTML = `
        <td><span class="toggle">▶</span></td>
        <td><b>${c.id}</b><br><span class="dim">${c.display_name}</span></td>
        <td class="mono">${counts.total}</td>
        <td><span class="badge ok">${counts.ok}</span></td>
        <td>${counts.outdated ? `<span class="badge warn">${counts.outdated}</span>` : `<span class="dim">${counts.outdated}</span>`}</td>
        <td>${counts.missing  ? `<span class="badge fail">${counts.missing}</span>`  : `<span class="dim">${counts.missing}</span>`}</td>
        <td>
          <div class="cat-actions">
            <button class="phase-check"   data-cat-run data-only="${c.id}" data-phase="check">check</button>
            <button class="phase-plan"    data-cat-run data-only="${c.id}" data-phase="plan">plan</button>
            <button class="phase-apply"   data-cat-run data-only="${c.id}" data-phase="apply">apply</button>
            <button class="phase-verify"  data-cat-run data-only="${c.id}" data-phase="verify">verify</button>
            <button class="phase-cleanup" data-cat-run data-only="${c.id}" data-phase="cleanup">cleanup</button>
            <button class="phase-all"     data-cat-run data-only="${c.id}" data-phase="" title="Run all phases for this category">▶ run all</button>
          </div>
        </td>`;
      tb.appendChild(tr);
      const det = document.createElement("tr");
      det.className = "cat-detail hidden";
      det.innerHTML = `<td colspan="7"><div class="cat-detail-inner" id="cat-detail-${c.id}"></div></td>`;
      tb.appendChild(det);
    }
    // Toggle the cat-detail TR for a given .cat-row. Bidirectional:
    // first call expands (loads detail), second call collapses.
    const toggleCatRow = (row) => {
      const cat = row.dataset.cat;
      const det = row.nextElementSibling;
      if (!det || !det.classList.contains("cat-detail")) return;
      const isHidden = det.classList.contains("hidden");
      if (isHidden) {
        det.classList.remove("hidden");
        row.classList.add("open");
        ui.loadCategoryDetail(cat);
      } else {
        det.classList.add("hidden");
        row.classList.remove("open");
      }
    };
    $$("#cats-table .cat-row").forEach(row => {
      // Row click — toggle, except when click landed on (or inside) any
      // button. closest('button') catches icons/spans nested in buttons
      // so e.g. an SVG inside `▶ run all` doesn't trigger collapse.
      row.addEventListener("click", e => {
        if (e.target.closest && e.target.closest("button")) return;
        toggleCatRow(row);
      });
    });
    // Explicit chevron-cell click handler so clicks on the chevron itself
    // (or its parent TD) ALWAYS toggle, even if a future restyling adds a
    // ::before/::after pseudo-element that swallows the row-level event.
    // Belt-and-suspenders fix for the operator-reported "collapse-back
    // not working" — phase buttons live in the rightmost cell, and a near-
    // miss click during fast collapse-collapse cycles could hit the action
    // column. The chevron cell is the dedicated toggle target.
    $$("#cats-table .cat-row > td:first-child").forEach(td => {
      td.addEventListener("click", e => {
        e.stopPropagation();          // prevent row-handler double-fire
        toggleCatRow(td.parentElement);
      });
    });
    // Stop clicks INSIDE the expanded detail row from bubbling up and
    // accidentally triggering the parent cat-row's toggle if the operator
    // clicks on a non-button area (e.g. the table header of the detail).
    $$("#cats-table .cat-detail").forEach(det => {
      det.addEventListener("click", e => {
        e.stopPropagation();
      });
    });
    // Per-category phase buttons → start the run directly (with sudo for mutating).
    // B4: ``apply`` (and unscoped "run all") gates on the apply-confirm modal —
    // the user must type the literal word ``apply`` to proceed. ``check`` and
    // ``plan`` are non-mutating, so we mark them ``dry_run`` to avoid sudo.
    $$("#cats-table button[data-cat-run]").forEach(b => b.addEventListener("click", async e => {
      e.stopPropagation();
      // B4 debounce: disable the button until the run actually starts.
      // Without this, a double-click (or a slow modal) can fire two
      // /runs/async calls for the same category — bad behaviour, especially
      // for apply where the user only typed ``apply`` once.
      if (b.disabled) return;
      b.disabled = true;
      try {
        const phase = b.dataset.phase || null;
        const cat = b.dataset.only || null;
        const isApply = phase === "apply" || phase === "" || phase === null;
        const isReadOnly = phase === "check" || phase === "plan";
        if (isApply) {
          const ok = await confirmApply(cat || "all categories");
          if (!ok) { ui.status(tr("apply.cancelled") || "apply cancelled"); return; }
        }
        // Backend (RunRequest, Pydantic v2, ``extra='forbid'``) requires
        // ``categories: list[SourceType]`` and ``phases: list[Phase]``.
        // Pre-monorepo SPA used singular ``only``/``phase`` strings — those
        // now produce HTTP 422 ``extra_forbidden`` errors. Translate here.
        const body = {
          dry_run: isReadOnly,
        };
        if (cat) body.categories = [cat];
        if (phase) body.phases = [phase];
        try {
          const r = await startRunWithSudo(body);
          ui.show("run");
          ui.attachStream(r.run_id);
          $("#stop-btn").disabled = false;
          ui.status(`run ${r.run_id} started - ${cat}/${phase || "all phases"}`);
        } catch (err) { ui.status(String(err)); }
      } finally {
        b.disabled = false;
      }
    }));
  },

  async loadCategoryDetail(cat, opts) {
    opts = opts || {};
    const refresh = !!opts.refresh;
    const target = $("#cat-detail-" + cat);
    // Only paint the spinner when we will actually fetch — a cached hit
    // re-renders synchronously without the "scanning…" flash.
    const path = `/inventory/${encodeURIComponent(cat)}`;
    const cached = !refresh && frontendCache._store.has(frontendCache._key(path));
    if (!cached) {
      target.innerHTML = `<span class="spinner"></span> ${tr("overview.scanning")}`;
    }
    try {
      if (refresh) {
        // Explicit Refresh: bust the backend's 60s inventory cache so
        // the row reflects whatever check sidecar JUST landed.
        await api.post(`/inventory/refresh?category=${encodeURIComponent(cat)}`, {}).catch(()=>{});
      }
      const items = (await frontendCache.get(path, { refresh })).items;
      if (!items.length) {
        target.innerHTML = `<p class="dim">${tr("categories.no_items")}</p>`;
        return;
      }
      const order = {outdated:0, missing:1, ok:2, unknown:3};
      items.sort((a,b) => (order[a.status]||9) - (order[b.status]||9) || a.name.localeCompare(b.name));
      target.innerHTML = `
        <table class="inv-table">
          <thead><tr>
            <th>${tr("categories.col_pkg")}</th>
            <th>${tr("categories.col_inst")}</th>
            <th>${tr("categories.col_cand")}</th>
            <th>${tr("categories.col_status")}</th>
            <th>${tr("categories.col_source")}</th>
            <th>${tr("categories.col_in_cfg")}</th>
            <th>Action</th>
          </tr></thead>
          <tbody>
            ${items.map(it => `
              <tr class="status-${it.status}">
                <td class="pkg-name">${it.name}</td>
                <td class="mono">${it.installed||"-"}</td>
                <td class="mono">${it.candidate||"-"}</td>
                <td>${ui.badge(it.status)}</td>
                <td class="dim">${it.source||""}</td>
                <td>${it.in_config ? "✔" : "<span class='dim'>-</span>"}</td>
                <td>
                  ${it.in_config
                    ? `<button class="secondary" data-cat-rm data-pkg="${it.name}" data-cat="${cat}" title="Remove from config (does NOT uninstall)">remove</button>`
                    : `<button class="secondary" data-cat-add data-pkg="${it.name}" data-cat="${cat}" title="Add to config so future updates include it">+ add</button>`}
                  ${cat === "apt" && it.installed && it.installed !== "-"
                    ? ` <button class="secondary" data-apt-downgrade data-pkg="${it.name}" data-ver="${it.installed}" title="Roll back / pin to a specific older version (apt --allow-downgrades)" style="font-size:0.72rem">↓ rollback</button>`
                    : ""}
                </td>
              </tr>`).join("")}
          </tbody>
        </table>`;
    } catch (e) {
      target.innerHTML = `<p class="badge fail">${e}</p>`;
    }
  },

  async loadRunCenter() {
    if (!$("#profile-select").options.length) {
      const profs = (await api.get("/profiles")).profiles;
      const sel = $("#profile-select");
      for (const p of profs) {
        const opt = document.createElement("option");
        opt.value = p.id;
        opt.textContent = p.description ? `${p.id} - ${p.description}` : (p.label && p.label !== p.id ? `${p.id} - ${p.label}` : p.id);
        sel.appendChild(opt);
      }
      const cats = (await api.get("/categories")).categories;
      const onlySel = $("#only-select");
      for (const c of cats) {
        const opt = document.createElement("option");
        opt.value = c.id;
        opt.textContent = c.display_name;
        onlySel.appendChild(opt);
      }
    }
    // Existing active run?
    try {
      const a = (await api.get("/runs/active")).active;
      if (a && !a.finished) {
        ui.attachStream(a.run_id);
        $("#stop-btn").disabled = false;
      }
    } catch {}
  },

  async loadHistory() {
    const [rows, eta] = await Promise.all([
      api.get("/runs?limit=200").then(d => d.runs),
      api.get("/telemetry/eta").catch(() => ({profiles:{}})),
    ]);
    // Header line: shows expected duration for each profile based on history.
    const etaTxt = Object.entries(eta.profiles||{}).map(([prof, p]) =>
      `<span class="badge ${p.ok_pct>=90?"ok":p.ok_pct>=70?"warn":"fail"}">${prof}</span> avg ${Math.round(p.avg_seconds/60)}m, p90 ${Math.round(p.p90_seconds/60)}m, ${p.ok_pct}% ok (${p.samples})`
    ).join(" · ");
    $("#history-eta").innerHTML = etaTxt
      ? `Based on history: ${etaTxt}`
      : "<span class='dim'>No prior runs to compute ETA from yet.</span>";
    const tb = $("#history-table tbody");
    tb.innerHTML = "";
    for (const r of rows) {
      const tr = document.createElement("tr");
      const phaseSummary = r.summary && r.summary.phases
        ? `${r.summary.phases.length} phase(s)` : "-";
      let durStr = "-";
      if (r.started_at && r.ended_at) {
        try {
          const a = new Date(r.started_at), b = new Date(r.ended_at);
          const sec = Math.max(0, Math.round((b - a) / 1000));
          durStr = sec >= 60 ? `${Math.floor(sec/60)}m${sec%60}s` : `${sec}s`;
        } catch {}
      }
      const srcTag = r.source === "cli"
        ? ` <span class="st-pill st-info" title="Imported from CLI run">cli</span>`
        : "";
      tr.innerHTML = `
        <td>${ui.fmtTime(r.started_at)}</td>
        <td>${r.profile || "-"}${r.dry_run ? " <span class='dim'>(dry)</span>":""}${srcTag}</td>
        <td>${ui.badge(r.status)}</td>
        <td class="duration">${durStr}</td>
        <td>${phaseSummary}</td>
        <td>${r.needs_reboot ? "yes" : "-"}</td>
        <td><a href="#logs" data-run="${r.id}">${r.id}</a></td>`;
      // Sesja 66: link to /runs/{id}/report (human-readable REPORT.md).
      // Audit found endpoint exists at runs.py:458 but UI never linked it.
      // Use DOM methods (not innerHTML) so the run id can't introduce XSS.
      const lastCell = tr.lastElementChild;
      if (lastCell) {
        lastCell.appendChild(document.createTextNode(" "));
        const reportA = document.createElement("a");
        reportA.href = "/runs/" + encodeURIComponent(r.id) + "/report";
        reportA.target = "_blank";
        reportA.rel = "noopener";
        reportA.className = "dim history-report-link";
        reportA.title = i18n.t("history.view_report", "View report");
        reportA.textContent = "\u{1F4C4}";  // 📄 page icon
        lastCell.appendChild(reportA);
      }
      tb.appendChild(tr);
    }
    $$("a[data-run]").forEach(a => a.addEventListener("click", e => {
      e.preventDefault();
      const runId = a.dataset.run;
      if (window.ASCENDO_EDITION === "basic") {
        // Inline-expand the row's logs instead of switching to the Logs view
        // (which is hidden in basic edition).
        const parentTr = a.closest("tr");
        if (parentTr) ui.toggleHistoryLogsRow(parentTr, runId);
      } else {
        ui.show("logs");
        ui.loadRunDetail(runId);
      }
    }));
  },

  async loadRunDetail(runId, targetEl) {
    // Resolve render target. Default to the Logs view's #run-detail panel;
    // basic-edition inline-expand callers pass their own container.
    const target = targetEl || $("#run-detail");
    try {
      // Backend ``GET /runs/{id}`` returns a list[Sidecar] (the raw
      // sidecar dump). The pre-monorepo backend wrapped that in
      // ``{run: {id, status, started_at, ended_at, phases: [...]}}`` so
      // accept both shapes — synthesise the run-level fields from the
      // list when needed, otherwise fall through to the legacy wrapper.
      const raw = await api.get(`/runs/${runId}`);
      let r;
      if (Array.isArray(raw)) {
        const list = raw.filter(sc => sc && !sc._recovery_stub);
        const ords = {failed:4, partial:3, skipped:2, success:1, up_to_date:0};
        const worst = list.reduce(
          (a, s) => ((ords[s.status] ?? -1) > (ords[a] ?? -1) ? s.status : a),
          list[0] && list[0].status,
        );
        const startedSorted = list.map(s => s.started_at).filter(Boolean).sort();
        const endedSorted   = list.map(s => s.finished_at).filter(Boolean).sort();
        r = {
          id: runId,
          status: worst || "unknown",
          started_at: startedSorted[0] || null,
          ended_at: endedSorted[endedSorted.length - 1] || null,
          phases: list.map(sc => ({
            category: sc.category,
            phase: sc.phase,
            exit_code: (sc.summary && sc.summary.exit_code) ?? null,
            summary: {
              ok:   ((sc.summary && sc.summary.success)    || 0)
                  + ((sc.summary && sc.summary.up_to_date) || 0),
              warn: ((sc.summary && sc.summary.partial)    || 0)
                  + ((sc.summary && sc.summary.skipped)    || 0),
              err:  (sc.summary && sc.summary.failed) || 0,
            },
          })),
        };
      } else {
        r = (raw && raw.run) || raw;
      }
      const phases = r.phases || (r.run && r.run.phases) || [];
      let html = `<h3><code>${r.id || runId}</code> - ${ui.badge(r.status)}</h3>
        <p class="dim">${ui.fmtTime(r.started_at)} → ${ui.fmtTime(r.ended_at)}</p>
        <table><thead><tr>
          <th>Category</th><th>Phase</th><th>Exit</th><th>OK</th><th>Warn</th><th>Err</th><th>Sidecar</th>
        </tr></thead><tbody>`;
      for (const p of phases) {
        const s = p.summary || {};
        const cat = p.category, ph = p.phase || p.kind;
        html += `<tr>
          <td>${cat}</td>
          <td>${ph}</td>
          <td>${p.exit_code ?? "-"}</td>
          <td>${s.ok ?? "-"}</td>
          <td>${s.warn ?? "-"}</td>
          <td>${s.err ?? "-"}</td>
          <td>
            <a href="/runs/${runId}/phase/${cat}/${ph}" target="_blank">json</a> ·
            <a href="/runs/${runId}/phase/${cat}/${ph}/log" target="_blank">log</a>
          </td>
        </tr>`;
      }
      html += "</tbody></table>";
      if (target) target.innerHTML = html;
    } catch (e) {
      if (target) target.innerHTML = `<p class="badge fail">${e}</p>`;
    }
  },

  // Inline log expansion for the History tab in basic edition. Toggles a
  // sibling <tr class="history-logs-row"> below the clicked row that hosts
  // the same per-phase table loadRunDetail() renders into the Logs view.
  async toggleHistoryLogsRow(parentTr, runId) {
    const next = parentTr.nextElementSibling;
    if (next && next.classList.contains("history-logs-row") && next.dataset.runId === runId) {
      next.remove();
      return;
    }
    // Keep only one row open at a time — collapse any existing expansion.
    document.querySelectorAll("tr.history-logs-row").forEach(r => r.remove());
    const colCount = parentTr.children.length;
    const newRow = document.createElement("tr");
    newRow.className = "history-logs-row";
    newRow.dataset.runId = runId;
    const td = document.createElement("td");
    td.colSpan = colCount;
    const content = document.createElement("div");
    content.className = "history-logs-content";
    content.textContent = "Loading logs…";
    td.appendChild(content);
    newRow.appendChild(td);
    parentTr.parentElement.insertBefore(newRow, parentTr.nextSibling);
    try {
      await ui.loadRunDetail(runId, content);
    } catch (e) {
      content.textContent = "Failed to load logs: " + String(e);
    }
  },

  async loadHosts() {
    const tb = $("#hosts-table tbody");
    tb.innerHTML = '<tr><td colspan="7" class="dim">loading…</td></tr>';
    try {
      const hosts = (await api.get("/hosts")).hosts;
      if (!hosts.length) {
        tb.innerHTML = '<tr><td colspan="7" class="dim">No hosts configured. Copy <code>config/hosts.toml.example</code> → <code>config/hosts.toml</code> and add entries.</td></tr>';
        return;
      }
      tb.innerHTML = "";
      for (const h of hosts) {
        const tr = document.createElement("tr");
        tr.innerHTML = `
          <td><b>${h.id || h.hostname || "(unknown)"}</b><br><span class="dim">${h.display_name || h.hostname || ""}</span></td>
          <td colspan="5" class="dim">checking…</td>
          <td>${ui.badge("running")}</td>`;
        tb.appendChild(tr);
        api.get(`/hosts/${encodeURIComponent(h.id)}/preflight`).then(p => {
          const lastRun = p.last_run ? `${p.last_run.status || "?"} (${p.last_run.run_id || ""})` : "-";
          tr.innerHTML = `
            <td><b>${h.id || h.hostname || "(unknown)"}</b><br><span class="dim">${h.display_name || h.hostname || ""}</span></td>
            <td>${p.hostname || "-"}</td>
            <td>${p.os || "-"}</td>
            <td>${p.kernel || "-"}</td>
            <td>${p.repo_present ? `<span class='badge ok'>${p.git_head||""}</span>` : "<span class='badge warn'>missing</span>"}</td>
            <td>${lastRun}</td>
            <td>${p.ok ? ui.badge("ok") : ui.badge("fail")}<br><span class="dim">${(p.error||"").slice(0,80)}</span></td>`;
        }).catch(e => {
          tr.innerHTML = `<td><b>${h.id}</b></td><td colspan="5" class="dim">error: ${String(e).slice(0,200)}</td><td>${ui.badge("fail")}</td>`;
        });
      }
    } catch (e) {
      tb.innerHTML = `<tr><td colspan="7" class="badge fail">${e}</td></tr>`;
    }
  },

  // Schedule (Sesja 67) — drives the adapter's IScheduler via
  // /scheduler/list /scheduler/install /scheduler/remove /scheduler/trigger.
  async loadSchedule() {
    const empty = document.getElementById("schedule-list-empty");
    const table = document.getElementById("schedule-table");
    const tbody = table ? table.querySelector("tbody") : null;
    const setStatus = (txt, cls) => {
      const el = document.getElementById("schedule-form-status");
      if (!el) return;
      el.textContent = txt || "";
      el.className = cls || "dim";
    };
    if (!table || !tbody || !empty) return;

    setStatus(i18n.t("schedule.loading", "Loading…"), "dim");
    let resp;
    try {
      resp = await api.get("/scheduler/list");
    } catch (e) {
      setStatus(i18n.t("schedule.unavailable",
        "Scheduling not available on this adapter."), "warn");
      table.style.display = "none";
      empty.style.display = "";
      return;
    }
    setStatus("", "dim");

    const items = (resp && resp.ok && Array.isArray(resp.items)) ? resp.items : [];
    tbody.innerHTML = "";
    if (!items.length) {
      table.style.display = "none";
      empty.style.display = "";
    } else {
      table.style.display = "";
      empty.style.display = "none";
      for (const it of items) {
        const tr = document.createElement("tr");
        // Use DOM-safe construction; schedule names are user-typed and
        // could otherwise carry HTML when echoed by a misbehaving backend.
        const cells = [
          it.name || "",
          it.expression || "",
          it.profile || "",
          it.enabled ? (i18n.t("schedule.yes", "yes")) : (i18n.t("schedule.no", "no")),
        ];
        for (const c of cells) {
          const td = document.createElement("td");
          td.textContent = c;
          tr.appendChild(td);
        }
        const actTd = document.createElement("td");
        const trigBtn = document.createElement("button");
        trigBtn.type = "button";
        trigBtn.className = "secondary";
        trigBtn.style.fontSize = "0.78rem";
        trigBtn.textContent = i18n.t("schedule.trigger_now", "Run now");
        trigBtn.addEventListener("click", () => ui.scheduleTrigger(it.name));
        const delBtn = document.createElement("button");
        delBtn.type = "button";
        delBtn.className = "secondary";
        delBtn.style.fontSize = "0.78rem";
        delBtn.style.marginLeft = "0.3rem";
        delBtn.textContent = i18n.t("schedule.delete", "Delete");
        delBtn.addEventListener("click", () => ui.scheduleRemove(it.name));
        const editBtn = document.createElement("button");
        editBtn.type = "button";
        editBtn.className = "secondary";
        editBtn.style.fontSize = "0.78rem";
        editBtn.style.marginLeft = "0.3rem";
        editBtn.textContent = i18n.t("schedule.edit", "Edit");
        editBtn.addEventListener("click", () => ui.scheduleEdit(it));
        actTd.appendChild(trigBtn);
        actTd.appendChild(editBtn);
        actTd.appendChild(delBtn);
        tr.appendChild(actTd);
        tbody.appendChild(tr);
      }
    }

    // Wire form + refresh button once.
    if (!ui._scheduleWired) {
      ui._scheduleWired = true;
      const form = document.getElementById("schedule-form");
      if (form) form.addEventListener("submit", ui.scheduleSubmit);
      const refresh = document.getElementById("schedule-refresh-btn");
      if (refresh) refresh.addEventListener("click", () => {
        ui._loaded.schedule = false;
        ui.loadSchedule();
      });
    }
  },

  scheduleEdit(it) {
    const $n = document.getElementById("schedule-f-name");
    const $e = document.getElementById("schedule-f-expr");
    const $p = document.getElementById("schedule-f-profile");
    const $en = document.getElementById("schedule-f-enabled");
    const $d = document.getElementById("schedule-f-desc");
    if ($n) $n.value = it.name || "";
    if ($e) $e.value = it.expression || "";
    if ($p) $p.value = it.profile || "safe";
    if ($en) $en.checked = !!it.enabled;
    if ($d) $d.value = it.description || "";
    if ($n) $n.scrollIntoView({behavior: "smooth", block: "center"});
  },

  async scheduleSubmit(ev) {
    ev.preventDefault();
    const setStatus = (txt, cls) => {
      const el = document.getElementById("schedule-form-status");
      if (!el) return;
      el.textContent = txt;
      el.className = cls || "dim";
    };
    const body = {
      name:        (document.getElementById("schedule-f-name") || {}).value || "",
      expression:  (document.getElementById("schedule-f-expr") || {}).value || "",
      profile:     (document.getElementById("schedule-f-profile") || {}).value || "safe",
      enabled:    !!(document.getElementById("schedule-f-enabled") || {}).checked,
      description: (document.getElementById("schedule-f-desc") || {}).value || null,
    };
    if (!body.name.trim() || !body.expression.trim()) {
      setStatus(i18n.t("schedule.err_required", "Name and expression are required."), "fail");
      return;
    }
    setStatus(i18n.t("schedule.saving", "Saving…"), "dim");
    try {
      const r = await api.post("/scheduler/install", body);
      if (!r || r.ok === false) {
        setStatus((r && r.error) || i18n.t("schedule.err_save",
          "Save failed."), "fail");
        return;
      }
      setStatus(i18n.t("schedule.saved", "Saved."), "ok");
      ui._loaded.schedule = false;
      ui.loadSchedule();
    } catch (e) {
      setStatus(String(e).slice(0, 200), "fail");
    }
  },

  async scheduleRemove(name) {
    if (!name) return;
    const msg = i18n.t("schedule.confirm_delete",
      "Delete schedule {name}?").replace("{name}", name);
    if (!confirm(msg)) return;
    try {
      const r = await api.post("/scheduler/remove", {name});
      if (!r || r.ok === false) {
        alert((r && r.error) || i18n.t("schedule.err_delete",
          "Delete failed."));
        return;
      }
      ui._loaded.schedule = false;
      ui.loadSchedule();
    } catch (e) {
      alert(String(e).slice(0, 200));
    }
  },

  async scheduleTrigger(name) {
    if (!name) return;
    try {
      const r = await api.post("/scheduler/trigger", {name});
      if (!r || r.ok === false) {
        alert((r && r.error) || i18n.t("schedule.err_trigger",
          "Trigger failed."));
        return;
      }
      alert(i18n.t("schedule.triggered",
        "Triggered '{name}' once.").replace("{name}", name));
    } catch (e) {
      alert(String(e).slice(0, 200));
    }
  },

  async loadProfilesPanel() {
    const wrap = $("#profiles-list");
    if (!wrap) return;
    try {
      const r = await api.get("/profiles/templates");
      const templates = r.items || r.templates || [];
      if (!templates.length) {
        wrap.innerHTML = `<p class="dim">No templates in config/profiles/.</p>`;
        return;
      }
      wrap.innerHTML = templates.map(t => `
        <div style="border:1px solid var(--border);border-radius:6px;padding:0.5rem 0.7rem;margin:0.4rem 0">
          <div><b>${t.name}</b> <span class="dim">- ${t.lines} pkg(s)</span></div>
          <div class="dim" style="font-size:0.78rem;margin:0.2rem 0">${t.summary || ""}</div>
          <div style="display:flex;gap:0.3rem">
            <button class="secondary" data-profile-import="${t.name}" data-dry="1" style="font-size:0.78rem">Preview</button>
            <button class="secondary" data-profile-import="${t.name}" data-dry="0" style="font-size:0.78rem">Apply</button>
          </div>
        </div>`).join("");
    } catch (e) { wrap.textContent = String(e); }
  },

  async loadSettings() {
    const s = await api.get("/settings");
    window.SETTINGS_CACHE = s;
    // Re-bind service buttons (idempotent) and refresh status now that the
    // Settings tab is visible — the user opened the panel because they want
    // to see service state.
    _bindServiceButtons();
    serviceMgr.refreshIndicator();
    // Load profile templates panel + updates repo field.
    ui.loadProfilesPanel();
    const f = $("#settings-form");
    f.elements.default_profile.value = s.default_profile || "safe";
    f.elements.snapshot_before_apply.checked = !!s.snapshot_before_apply;
    f.elements.notifications_desktop.checked = !!(s.notifications && s.notifications.desktop);
    f.elements.ui_theme.value    = (s.ui && s.ui.theme)    || "auto";
    f.elements.ui_language.value = (s.ui && s.ui.language) || "auto";
    f.elements.scheduler_enabled.checked = !!(s.scheduler && s.scheduler.enabled);
    f.elements.scheduler_calendar.value = (s.scheduler && s.scheduler.calendar) || "Sun *-*-* 03:00:00";
    f.elements.scheduler_profile.value = (s.scheduler && s.scheduler.profile) || "safe";
    f.elements.scheduler_no_drivers.checked = !!(s.scheduler && s.scheduler.no_drivers);
    if (f.elements.updates_check_repo) {
      f.elements.updates_check_repo.value = (s.updates && s.updates.check_repo) || "";
    }
  },

  collectSettings() {
    const f = $("#settings-form");
    return {
      default_profile: f.elements.default_profile.value,
      snapshot_before_apply: f.elements.snapshot_before_apply.checked,
      notifications: { desktop: f.elements.notifications_desktop.checked },
      ui: {
        theme:    f.elements.ui_theme.value,
        language: f.elements.ui_language.value,
      },
      scheduler: {
        enabled: f.elements.scheduler_enabled.checked,
        calendar: f.elements.scheduler_calendar.value,
        profile:  f.elements.scheduler_profile.value,
        no_drivers: f.elements.scheduler_no_drivers.checked,
      },
      updates: {
        check_repo: f.elements.updates_check_repo
          ? f.elements.updates_check_repo.value.trim() : "",
      },
    };
  },

  async loadSync() {
    try {
      const g = await api.get("/git/status");
      $("#sync-git").innerHTML =
        `branch <code>${g.branch || "(unknown)"}</code> ` +
        (g.dirty ? "<span class='badge warn'>dirty</span>" : "<span class='badge ok'>clean</span>") +
        ` <span class="dim">↑${g.ahead} ↓${g.behind}</span>`;
    } catch (e) { $("#sync-git").textContent = String(e); }
    try {
      const s = await api.get("/sync/status");
      if (s.available) {
        $("#sync-cloud").innerHTML =
          `${ui.badge(s.overall === "PASS" ? "ok" : "warn")} ` +
          `last verify: <code>${s.log_path}</code><br>` +
          `<span class="dim">overall: ${s.overall}</span>`;
      } else {
        $("#sync-cloud").innerHTML = `<span class="dim">${s.reason || "cloud sync not configured (open Cloud Provider panel below to set it up)"}</span>`;
      }
    } catch (e) { $("#sync-cloud").textContent = String(e); }
  },

  async syncCall(label, fn) {
    const out = $("#sync-output");
    out.textContent = `[${label}] starting…\n`;
    try {
      const r = await fn();
      out.textContent += `[${label}] ok=${r.ok}\n`;
      if (r.stdout) out.textContent += "--- stdout ---\n" + r.stdout + "\n";
      if (r.stderr) out.textContent += "--- stderr ---\n" + r.stderr + "\n";
      ui.loadSync();
    } catch (e) {
      out.textContent += `[${label}] FAILED: ${e}\n`;
    }
  },

  attachStream(runId) {
    // Sesja 50 fix — 4× duplicate output bug. Each call to attachStream
    // used to create a fresh EventSource without closing any prior one.
    // If the user clicked "Full update" twice, or if the wizard had
    // started its own ES that never reached `done` (e.g. user navigated
    // away mid-flow), N stale ESes were all still listening to the new
    // run and appending the same line to #live-log + #run-stream-log,
    // producing the wall of N× duplicates the operator saw on Sesja 49.
    if (window._ascendoActiveStreams) {
      for (const es of window._ascendoActiveStreams) {
        try { es.close(); } catch {}
      }
    }
    window._ascendoActiveStreams = [];
    const log = $("#live-log");
    log.textContent = "";
    const prog = $("#run-progress");
    const fill = prog.querySelector(".run-progress-fill");
    const lbl  = prog.querySelector(".run-progress-label");
    const rec  = prog.querySelector(".run-progress-recent");
    rec.innerHTML = ""; lbl.innerHTML = ""; fill.style.width = "0%";
    prog.classList.add("hidden");
    // Reset the live-detail panel for the new run.
    if (window.runDetail && typeof window.runDetail.reset === "function") {
      window.runDetail.reset(runId);
    }
    // Reset the terminal-style stream box (the "every line as it
    // happens" panel above the structured detail panel).
    const streamBox    = $("#run-stream");
    const streamLog    = $("#run-stream-log");
    const streamPct    = $("#run-stream-pct");
    const streamFill   = $("#run-stream-bar-fill");
    const streamCur    = $("#run-stream-current");
    if (streamBox && streamLog && streamFill) {
      streamBox.classList.add("hidden");
      streamLog.textContent = "";
      streamPct.textContent = "0%";
      streamFill.style.width = "0%";
      if (streamCur) streamCur.classList.remove("is-active");
    }
    // Sticky-bottom autoscroll: if the user scrolls up to read older
    // output, stop following the tail until they scroll back down.
    const streamScroll = { stick: true };
    if (streamLog && !streamLog._scrollWired) {
      streamLog._scrollWired = true;
      streamLog.addEventListener("scroll", () => {
        const nearBottom = streamLog.scrollTop + streamLog.clientHeight
          >= streamLog.scrollHeight - 6;
        streamScroll.stick = nearBottom;
      });
    }
    const stripAnsi = s => s.replace(/\x1b\[[0-9;]*m/g, "");
    function classifyLine(s) {
      const lower = s.toLowerCase();
      if (s.startsWith(">>> ")) return "marker";
      if (/\b(error|fatal|failed|panic)\b/.test(lower)) return "err";
      if (/\b(warn|warning|deprecated)\b/.test(lower)) return "warn";
      if (/^==>/.test(s) || /\b(success|installed|upgraded|done)\b/.test(lower)) return "info";
      return "";
    }
    function appendStreamLine(rawLine) {
      if (!streamLog) return;
      streamBox && streamBox.classList.remove("hidden");
      const line = stripAnsi(rawLine);
      const cls = classifyLine(line);
      const span = document.createElement("span");
      if (cls) span.className = cls;
      span.textContent = line + "\n";
      streamLog.appendChild(span);
      // Cap to ~2000 lines to keep DOM light on long runs.
      while (streamLog.childNodes.length > 2000) {
        streamLog.removeChild(streamLog.firstChild);
      }
      if (streamScroll.stick) {
        streamLog.scrollTop = streamLog.scrollHeight;
      }
    }
    function setStreamProgress(pct, label) {
      if (!streamBox || !streamFill || !streamPct) return;
      streamBox.classList.remove("hidden");
      if (Number.isFinite(pct)) {
        const clamped = Math.max(0, Math.min(100, pct));
        streamFill.style.width = clamped + "%";
        streamPct.textContent = clamped + "%";
      }
      if (streamCur && typeof label === "string" && label.length > 0) {
        streamCur.textContent = label;
        streamCur.classList.add("is-active");
      }
    }

    // Parse PROGRESS|... markers emitted by lib/progress.sh + apt awk parser.
    function handleMarker(line) {
      const stripped = stripAnsi(line);
      if (!stripped.startsWith("PROGRESS|")) return false;
      const parts = stripped.split("|");
      const kind = parts[1];
      if (kind === "start") {
        const total = +parts[3]; const label = parts[4] || parts[2];
        prog.classList.remove("hidden");
        lbl.innerHTML = `<span><b>${label}</b> - 0/${total}</span><span class="dim">running…</span>`;
        fill.style.width = "0%";
        rec.innerHTML = "";
        prog._total = total;
      } else if (kind === "step") {
        const n = +parts[3], total = +parts[4], status = parts[5], msg = parts.slice(6).join("|");
        const pct = total > 0 ? Math.round((n/total) * 100) : 0;
        fill.style.width = pct + "%";
        lbl.innerHTML = `<span><b>${parts[2]}</b> - ${n}/${total}</span><span class="dim">${pct}%</span>`;
        const div = document.createElement("div");
        div.className = status;
        div.textContent = `[${n}/${total}] ${msg}`;
        rec.prepend(div);
        // Cap to last 12 entries to keep DOM light.
        while (rec.children.length > 12) rec.removeChild(rec.lastChild);
      } else if (kind === "done") {
        const ok = +parts[3], warn = +parts[4], err = +parts[5];
        lbl.innerHTML = `<span><b>${parts[2]}</b> - done</span>` +
          `<span><span class="badge ok">${ok}</span> ` +
          `<span class="badge ${warn?"warn":"ok"}">${warn} warn</span> ` +
          `<span class="badge ${err?"fail":"ok"}">${err} err</span></span>`;
        fill.style.width = "100%";
        // Auto-hide after a short delay; user still sees the recent list.
        setTimeout(() => { if (prog._total === +parts[3]) prog.classList.add("hidden"); }, 4000);
      }
      return true;
    }

    // M2.10: subscribe to per-run /runs/{id}/events. Falls back to legacy
    // /runs/active/stream if the per-run URL 404s (older backend).
    const perRunUrl = `/runs/${encodeURIComponent(runId)}/events`;
    let es = new EventSource(perRunUrl);
    window._ascendoActiveStreams.push(es);
    let usingLegacy = false;
    const phaseRows = new Map();
    function ensureProgVisible() { prog.classList.remove("hidden"); }
    function renderSidecar(sc) {
      ensureProgVisible();
      const key = `${sc.phase}__${sc.category}`;
      const summary = sc.summary || {};
      const total   = summary.total ?? (sc.items || []).length;
      const failed  = summary.failed ?? 0;
      const status  = sc.status || "running";
      const cls = status === "success" ? "ok"
                : status === "failed"  ? "fail"
                : status === "partial" ? "warn"
                : status === "skipped" ? "dim" : "running";
      const text = `[${sc.phase}:${sc.category}] ${status} - ${total} items, ${failed} failed`;
      let row = phaseRows.get(key);
      if (!row) { row = document.createElement("div"); rec.prepend(row); phaseRows.set(key, row);
                  while (rec.children.length > 14) rec.removeChild(rec.lastChild); }
      row.className = cls; row.textContent = text;
      lbl.innerHTML = `<span><b>${sc.phase}</b> · ${sc.category}</span><span class="dim">${phaseRows.size} sidecars</span>`;
    }
    es.addEventListener("status", e => { try { const m=JSON.parse(e.data); ui.status(`run ${runId}: ${m.status}`); if(m.status==="running") ensureProgVisible(); if (window.runDetail) window.runDetail.onStatus(runId, m); } catch {} });
    es.addEventListener("sidecar", e => { try { const sc=JSON.parse(e.data); renderSidecar(sc); log.textContent += `[${sc.phase}:${sc.category}] ${sc.status}\n`; log.scrollTop=log.scrollHeight; if (window.runDetail) window.runDetail.onSidecar(runId, sc); } catch {} });
    es.addEventListener("sidecar_error", e => { try { const m=JSON.parse(e.data); log.textContent += `[sidecar parse error] ${m.path}: ${m.error}\n`; if (window.runDetail) window.runDetail.onSidecarError(runId, m); } catch {} });
    es.addEventListener("done", e => {
      let p = {}; try { p = JSON.parse(e.data); } catch {}
      const status = p.status || "completed";
      const ms = p.duration_ms;
      log.textContent += `\n[done - ${status}${ms ? ` in ${(ms/1000).toFixed(1)}s` : ""}]\n`;
      ui.status(`run ${runId} ${status}${ms ? ` (${(ms/1000).toFixed(1)}s)` : ""}`);
      const stopBtn = $("#stop-btn"); if (stopBtn) stopBtn.disabled = true;
      fill.style.width = "100%"; es.close();
      // Lock the live stream bar at 100% and surface the terminal
      // verdict in the "currently processing" line.
      setStreamProgress(100, `${status}${ms ? ` (${(ms/1000).toFixed(1)}s)` : ""}`);
      appendStreamLine(`>>> done - ${status}${ms ? ` in ${(ms/1000).toFixed(1)}s` : ""}`);
      if (window.runDetail) window.runDetail.onDone(runId, p);
      ui.invalidateCaches(); ui.checkRebootBanner(); ui.loadHealth();
      // After a run completes, force-repaint whichever live view the
      // user is on so they see post-apply state without manually
      // hitting Refresh. Apps + Categories + Overview are the most
      // common views to be staring at while a run is finishing.
      //
      // B3 fix: previously this was fire-and-forget which raced
      // /inventory/db/refresh (10-30s full scan) against the immediate
      // view repaint, so the SPA showed stale pre-apply versions until
      // the next user click. We now await the refresh before
      // re-fetching the view. The refresh writes back the latest
      // sidecars (including post-apply verify sidecars per Sesja 53)
      // into InventoryDB, then loadAppsView reads from the freshly
      // populated DB.
      //
      // We don't block the SSE 'done' handler — refresh + repaint are
      // wrapped in an async IIFE so the rest of the cleanup
      // (invalidateCaches/checkRebootBanner/loadHealth above) is
      // synchronous, but the user sees up-to-date versions when the
      // network round-trip completes.
      (async () => {
        try {
          await fetch("/inventory/db/refresh", { method: "POST" });
        } catch { /* endpoint missing on legacy backends — fall through */ }
        try {
          const active = document.querySelector(".nav-link.active")?.dataset?.view
                       || (window.ui && ui.activeView)
                       || null;
          if (active === "apps") {
            await ui.loadAppsView({ refresh: true });
            ui._loaded.apps = true;
          } else if (active === "categories") {
            await ui.loadCategoriesView({ refresh: true });
            ui._loaded.categories = true;
          } else if (active === "overview") {
            await ui.loadOverview({ refresh: true });
            ui._loaded.overview = true;
          }
        } catch {}
      })();
    });
    es.addEventListener("log", e => { try { const m=JSON.parse(e.data); const ln=m.line||""; if (!handleMarker(ln)) { log.textContent += ln + "\n"; log.scrollTop=log.scrollHeight; appendStreamLine(ln); } } catch {} });
    es.addEventListener("log_line", e => {
      try {
        const m = JSON.parse(e.data);
        const ln = m.line || "";
        // Also tee into the legacy raw event log so the old <pre>
        // collapsible keeps working for power users.
        log.textContent += ln + "\n"; log.scrollTop = log.scrollHeight;
        appendStreamLine(ln);
      } catch {}
    });
    es.addEventListener("progress", e => {
      try {
        const m = JSON.parse(e.data);
        const pct = (typeof m.pct === "number") ? m.pct : NaN;
        setStreamProgress(pct, m.label || "");
      } catch {}
    });
    es.onerror = () => {
      if (!usingLegacy) {
        usingLegacy = true;
        // Drop the failed EventSource from the active-streams list before
        // pushing the fallback — otherwise repeated network blips during a
        // long apply grow _ascendoActiveStreams indefinitely (each closure
        // retains references to log/streamLog/handleMarker etc.). Only the
        // most recent ES is the one that matters for new events.
        const closedEs = es;
        try { closedEs.close(); } catch {}
        const arr = window._ascendoActiveStreams;
        const idx = arr.indexOf(closedEs);
        if (idx >= 0) arr.splice(idx, 1);
        es = new EventSource(`/runs/active/stream`);
        arr.push(es);
        es.addEventListener("log", e => { try { const m=JSON.parse(e.data); const ln=m.line||""; if (!handleMarker(ln)) { log.textContent += ln + "\n"; log.scrollTop=log.scrollHeight; } } catch {} });
        es.addEventListener("done", e => { let m={}; try{m=JSON.parse(e.data);}catch{} log.textContent += `\n[done - exit ${m.exit_code}]\n`; es.close(); ui.invalidateCaches(); ui.checkRebootBanner(); ui.loadHealth(); });
        es.onerror = () => {
          try { es.close(); } catch {}
          const i = arr.indexOf(es);
          if (i >= 0) arr.splice(i, 1);
        };
      }
    };
  },
};

// -- Logs tab: dropdown of runs + per-phase plain log viewer -----------------
ui.loadLogsList = async function() {
  const sel = $("#logs-run-select");
  const runs = (await api.get("/runs?limit=100")).runs || [];
  sel.innerHTML = `<option value="">- pick a run -</option>` +
    runs.map(r => `<option value="${r.id}">${ui.fmtTime(r.started_at)} · ${r.profile||"?"} · ${r.status||"?"} · ${r.id}</option>`).join("");
};
ui.openPhaseLog = async function(runId, cat, phase) {
  const v = $("#phase-log-viewer");
  v.textContent = "loading…";
  try {
    const r = await fetch(`/runs/${runId}/phase/${cat}/${phase}/log`);
    if (!r.ok) { v.textContent = `404: log not found for ${cat}/${phase}`; return; }
    v.textContent = await r.text();
    v.scrollTop = 0;
  } catch (e) { v.textContent = String(e); }
};
document.addEventListener("change", e => {
  if (e.target && e.target.id === "logs-run-select") {
    const id = e.target.value;
    if (!id) return;
    ui.loadRunDetail(id);
  }
});
document.addEventListener("click", e => {
  if (e.target.id === "logs-refresh-btn") { ui._loaded.logs = false; ui.loadLogsList(); }
  // Phase row → load plain log into the bottom viewer.
  const a = e.target.closest("[data-phase-log]");
  if (a) {
    e.preventDefault();
    const [runId, cat, phase] = a.dataset.phaseLog.split("|");
    ui.openPhaseLog(runId, cat, phase);
  }
});

// Patch loadRunDetail to add data-phase-log buttons (so user can view inline)
const _origLoadRunDetail = ui.loadRunDetail;
ui.loadRunDetail = async function(runId, targetEl) {
  // Reuse existing renderer, then post-process the HTML to add inline buttons.
  await _origLoadRunDetail.call(this, runId, targetEl);
  const det = targetEl || $("#run-detail");
  if (!det) return;
  // For every phase row, inject an inline "view log" button.
  det.querySelectorAll("tr").forEach(tr => {
    const cells = tr.querySelectorAll("td");
    if (cells.length < 7) return;
    const cat = cells[0]?.textContent?.trim();
    const ph  = cells[1]?.textContent?.trim();
    if (!cat || !ph) return;
    const btn = document.createElement("button");
    btn.className = "secondary"; btn.style.fontSize = "0.75rem"; btn.style.padding = "0.15rem 0.45rem";
    btn.dataset.phaseLog = `${runId}|${cat}|${ph}`;
    btn.textContent = "view log";
    cells[6].appendChild(document.createTextNode(" "));
    cells[6].appendChild(btn);
  });
};

// -- About tab: version + system + release notes -----------------------------
ui.loadAbout = async function() {
  try {
    const a = await api.get("/about");
    // Stash the platform on <html> so loadHelp() (and any future
    // platform-aware views) can branch on it without re-fetching /about.
    const platform = (a.platform || "windows").toLowerCase();
    document.documentElement.dataset.platform = platform;

    // -- App card (DOM construction; static template, app values via textContent)
    const app = $("#about-app"); app.textContent = "";
    const line1 = document.createElement("div");
    const nameB = document.createElement("b"); nameB.textContent = a.name || "Ascendo";
    line1.appendChild(nameB);
    const tagSpan = document.createElement("span");
    tagSpan.className = "dim";
    tagSpan.textContent = " — " + (a.tagline || "");
    line1.appendChild(tagSpan);
    app.appendChild(line1);
    const line2 = document.createElement("div");
    line2.appendChild(document.createTextNode("Version: "));
    const verCode = document.createElement("code"); verCode.textContent = a.version || "?";
    line2.appendChild(verCode);
    line2.appendChild(document.createTextNode(" "));
    const platSpan = document.createElement("span"); platSpan.className = "dim";
    platSpan.textContent = "platform: " + platform;
    line2.appendChild(platSpan);
    app.appendChild(line2);
    const line3 = document.createElement("div"); line3.className = "dim";
    line3.textContent = "Python: " + (a.python || "?");
    app.appendChild(line3);

    // -- Host card
    const sys = $("#about-system"); sys.textContent = "";
    const sysRow = (label, value) => {
      const div = document.createElement("div");
      div.appendChild(document.createTextNode(label + ": "));
      const c = document.createElement("code");
      c.textContent = String(value ?? "?");
      div.appendChild(c);
      sys.appendChild(div);
    };
    sysRow("Host", a.host);
    sysRow("OS", a.distro);
    sysRow("Kernel", a.kernel);
    sysRow("Arch", a.arch);

    // -- Release notes from /about/release-notes (platform-tagged from CHANGELOG)
    const rel = $("#about-release");
    rel.textContent = "";
    const spinner = document.createElement("span"); spinner.className = "spinner";
    rel.appendChild(spinner);
    rel.appendChild(document.createTextNode(" loading release notes…"));
    try {
      const rn = await api.get("/about/release-notes?platform=" + encodeURIComponent(platform) + "&limit=20");
      rel.textContent = "";
      const head = document.createElement("p");
      head.className = "dim";
      head.textContent = `Showing ${rn.entries.length} of ${rn.total_in_changelog || 0} CHANGELOG entries tagged for ${rn.platform}.`;
      rel.appendChild(head);
      if (!rn.entries || rn.entries.length === 0) {
        const empty = document.createElement("p");
        empty.className = "dim";
        empty.textContent = "No release notes for this platform yet.";
        rel.appendChild(empty);
      } else {
        rn.entries.forEach(e => {
          const sec = document.createElement("section");
          sec.className = "release-entry";
          const h = document.createElement("h3");
          h.textContent = `[${e.version}]` + (e.rest ? " — " + e.rest : "");
          sec.appendChild(h);
          // Render the body as a <pre> for fidelity with CHANGELOG markup.
          // Doing minimal markdown→HTML here would be unreliable across the
          // long-form Added/Changed/Fixed bullet lists.
          const body = document.createElement("pre");
          body.className = "release-body";
          body.style.whiteSpace = "pre-wrap";
          body.style.fontFamily = "var(--mono, monospace)";
          body.style.fontSize = "0.78rem";
          body.textContent = e.body || "";
          sec.appendChild(body);
          rel.appendChild(sec);
        });
      }
    } catch (e) {
      rel.textContent = "";
      const err = document.createElement("p");
      err.className = "dim";
      err.textContent = "Failed to load release notes: " + e;
      rel.appendChild(err);
    }

    // -- Re-render Help (it's platform-aware now via loadHelp).
    if (typeof ui.loadHelp === "function") ui.loadHelp(platform);
  } catch (e) {
    $("#about-app").textContent = String(e);
  }
};

// Platform-aware Help section. The hardcoded HTML in index.html is
// Windows-flavoured; on Linux/macOS we hide it and show a "coming soon"
// banner. When the Ubuntu / macOS adapters land, swap in their content.
ui.loadHelp = function(platform) {
  const root = document.getElementById("view-help");
  if (!root) return;
  const p = (platform || document.documentElement.dataset.platform || "windows").toLowerCase();
  // Tag the root so CSS / future per-platform sections can branch.
  root.dataset.platform = p;
  // Show / hide the existing Windows article based on platform tag.
  // Sections in index.html opt-in via data-platforms="windows"
  // (space-separated); untagged sections show on every platform.
  root.querySelectorAll("[data-platforms]").forEach(el => {
    const platforms = (el.dataset.platforms || "").split(/\s+/).filter(Boolean);
    el.hidden = platforms.length > 0 && !platforms.includes(p);
  });
  // Insert a small platform banner at the top of Help (idempotent).
  let banner = root.querySelector(".help-platform-banner");
  if (!banner) {
    banner = document.createElement("div");
    banner.className = "help-platform-banner dim";
    banner.style.padding = "0.4rem 0.6rem";
    banner.style.borderRadius = "6px";
    banner.style.background = "var(--accent-soft, var(--bg-elev))";
    banner.style.marginBottom = "0.6rem";
    banner.style.fontSize = "0.82rem";
    const h2 = root.querySelector("h2");
    (h2 ? h2.parentNode : root).insertBefore(banner, h2 ? h2.nextSibling : root.firstChild);
  }
  const labelMap = {windows: "Windows", linux: "Linux / Ubuntu", macos: "macOS"};
  banner.textContent = `Operator manual for ${labelMap[p] || p}. Switch platforms by running this dashboard from a different OS.`;
};

// -- Hosts tab: add/edit/delete + form binding -------------------------------
const _origLoadHosts = ui.loadHosts;
ui.loadHosts = async function() {
  await _origLoadHosts.call(this);
  // Append edit/delete buttons in last column.
  const tb = $("#hosts-table tbody");
  tb.querySelectorAll("tr").forEach(tr => {
    const idEl = tr.querySelector("td b");
    if (!idEl) return;
    const id = idEl.textContent.trim();
    const lastTd = tr.querySelector("td:last-child");
    if (!lastTd || lastTd.querySelector("[data-host-edit]")) return;
    const wrap = document.createElement("div");
    wrap.style.marginTop = "0.3rem";
    wrap.innerHTML =
      `<button class="secondary" data-host-edit="${id}" style="font-size:0.72rem;padding:0.15rem 0.4rem">edit</button>
       <button class="secondary" data-host-del="${id}" style="font-size:0.72rem;padding:0.15rem 0.4rem">delete</button>`;
    lastTd.appendChild(wrap);
  });
};

function _showHostForm(host) {
  const f = $("#hosts-form");
  f.classList.remove("hidden");
  f.elements.id.value           = host?.id || "";
  f.elements.display_name.value = host?.display_name || "";
  f.elements.ssh_alias.value    = host?.ssh_alias || "";
  f.elements.repo_path.value    = host?.repo_path || "~/Dev_Env/Ascendo";
  f.elements.description.value  = host?.description || "";
  f.elements.orig_id.value      = host?.id || "";
  f.elements.id.focus();
}
document.addEventListener("click", async e => {
  if (e.target.id === "hosts-add-btn") _showHostForm(null);
  if (e.target.id === "hosts-cancel-btn") $("#hosts-form").classList.add("hidden");
  const ed = e.target.closest("[data-host-edit]");
  if (ed) {
    const list = await api.get("/hosts/list");
    const h = (list.items||[]).find(x => x.id === ed.dataset.hostEdit);
    _showHostForm(h);
  }
  const dl = e.target.closest("[data-host-del]");
  if (dl) {
    if (!confirm(`Delete host '${dl.dataset.hostDel}' from config/hosts.toml?`)) return;
    try {
      await api.post("/hosts/delete", {id: dl.dataset.hostDel});
      ui._loaded.hosts = false; ui.loadHosts();
    } catch (err) { ui.status(String(err)); }
  }
});
document.addEventListener("submit", async e => {
  if (e.target && e.target.id === "hosts-form") {
    e.preventDefault();
    const f = e.target;
    const body = {
      id:           f.elements.id.value.trim(),
      display_name: f.elements.display_name.value.trim(),
      ssh_alias:    f.elements.ssh_alias.value.trim(),
      repo_path:    f.elements.repo_path.value.trim(),
      description:  f.elements.description.value.trim(),
      orig_id:      f.elements.orig_id.value.trim() || null,
    };
    try {
      await api.post("/hosts/upsert", body);
      f.classList.add("hidden");
      ui._loaded.hosts = false; ui.loadHosts();
      ui.status(`saved host ${body.id}`);
    } catch (err) { ui.status(String(err)); }
  }
});

// -- Sync provider form ------------------------------------------------------
async function _loadSyncProvider() {
  const f = $("#sync-provider-form");
  if (!f) return;
  // Populate remote name dropdown from `rclone listremotes`.
  await _loadRemotes();
  try {
    const s = await api.get("/sync/provider");
    f.elements.provider.value = s.provider || "";
    if (s.remote_name) {
      // If the saved name isn't in the list (e.g. rclone not yet configured),
      // append it as a placeholder.
      const sel = f.elements.remote_name;
      if (![...sel.options].some(o => o.value === s.remote_name)) {
        const o = document.createElement("option"); o.value = s.remote_name;
        o.textContent = s.remote_name + " (not in rclone)"; sel.appendChild(o);
      }
      sel.value = s.remote_name;
    }
    f.elements.remote_path.value = s.remote_path || "";
    f.elements.copy_only.checked = s.copy_only !== false;
  } catch {}
}
async function _loadRemotes() {
  const sel = $("#sync-remote-name");
  if (!sel) return;
  try {
    const r = await api.get("/sync/remotes");
    sel.innerHTML = '<option value="">(pick a configured remote)</option>';
    (r.remotes || []).forEach(name => {
      const o = document.createElement("option"); o.value = name; o.textContent = name;
      sel.appendChild(o);
    });
    if (r.error) sel.title = r.error;
  } catch (e) { sel.innerHTML = `<option value="">${e}</option>`; }
}

// Browse modal: list folders, support up/in navigation, pick current path.
async function _browseAt(path) {
  $("#sync-browse-pwd").textContent = path;
  const list = $("#sync-browse-list");
  list.innerHTML = '<span class="spinner"></span> listing…';
  try {
    const r = await api.get(`/sync/browse?path=${encodeURIComponent(path)}`);
    if (!r.ok) {
      list.innerHTML = `<p class="badge fail">${r.error||"failed"}</p>`;
      return;
    }
    list.innerHTML = (r.dirs.length
      ? r.dirs.map(d => `<div data-browse-into="${d}" style="padding:0.3rem 0.4rem;cursor:pointer;border-radius:4px"><span class="dim">📁</span> ${d}</div>`).join("")
      : '<p class="dim">(empty folder)</p>');
  } catch (e) { list.innerHTML = `<p class="badge fail">${e}</p>`; }
}
document.addEventListener("click", async e => {
  if (e.target.id === "sync-remote-refresh") _loadRemotes();
  if (e.target.id === "sync-remote-browse") {
    const sel = $("#sync-remote-name");
    const remote = sel?.value;
    if (!remote) { ui.status("pick a remote first"); return; }
    $("#sync-browse-modal").classList.remove("hidden");
    _browseAt(`${remote}:/`);
  }
  if (e.target.id === "sync-browse-close") $("#sync-browse-modal").classList.add("hidden");
  if (e.target.id === "sync-browse-up") {
    const cur = $("#sync-browse-pwd").textContent || "";
    const m = cur.match(/^([^:]+):\/(.*)$/);
    if (!m) return;
    const remote = m[1]; let p = m[2].replace(/\/$/, "");
    p = p.includes("/") ? p.replace(/\/[^/]+$/, "") : "";
    _browseAt(`${remote}:/${p}`);
  }
  if (e.target.id === "sync-browse-pick") {
    $("#sync-remote-path").value = $("#sync-browse-pwd").textContent;
    $("#sync-browse-modal").classList.add("hidden");
  }
  const into = e.target.closest("[data-browse-into]");
  if (into && into.closest("#sync-browse-list")) {
    const cur = $("#sync-browse-pwd").textContent || "";
    const next = cur.replace(/\/$/, "") + "/" + into.dataset.browseInto;
    _browseAt(next);
  }
});
document.addEventListener("submit", async e => {
  if (e.target && e.target.id === "sync-provider-form") {
    e.preventDefault();
    const f = e.target;
    const out = $("#sync-provider-output");
    try {
      const r = await api.post("/sync/provider", {
        provider:    f.elements.provider.value,
        remote_name: (f.elements.remote_name.value || "").trim(),
        remote_path: f.elements.remote_path.value.trim(),
        copy_only:   f.elements.copy_only.checked,
      });
      out.textContent = "saved: " + JSON.stringify(r, null, 2);
    } catch (err) { out.textContent = String(err); }
  }
});
document.addEventListener("click", async e => {
  if (e.target.id === "sync-provider-test") {
    const out = $("#sync-provider-output");
    out.textContent = "testing rclone connection (12s timeout)…";
    try {
      const r = await api.post("/sync/provider/test", {});
      out.textContent = (r.ok ? "OK\n" : "FAIL\n") + (r.stderr || r.stdout || "");
    } catch (err) { out.textContent = String(err); }
  }
});

// =====================================================================
// AI provider 3-step wizard.
//
// Step 1: pick provider (POST cards from /ai/providers).
// Step 2: enter API key / base URL → POST /ai/test-connection.
// Step 3: pick model (from the list returned by step 2) → POST /ai/config.
// =====================================================================

ui._aiWizard = {
  initialized: false,
  providers: [],
  selected: null,           // chosen provider id
  needs_url: false,
  base_url: "",
  api_key: "",
  models: [],               // populated by /ai/test-connection
  defaults: {},             // default base URLs by provider id
  saved: null,              // /ai/config response
};

ui.initAiWizard = async function () {
  if (ui._aiWizard.initialized) {
    // Light refresh — re-render cards (saved state may have changed).
    ui._aiRenderProviders();
    return;
  }
  ui._aiWizard.initialized = true;
  // Load provider catalog + saved config in parallel.
  try {
    const [providers, saved] = await Promise.all([
      api.get("/ai/providers"),
      api.get("/ai/config").catch(() => ({})),
    ]);
    ui._aiWizard.providers = providers.providers || [];
    ui._aiWizard.defaults  = providers.default_base_urls || {};
    ui._aiWizard.saved     = saved || {};
    if (saved && saved.provider) {
      const match = ui._aiWizard.providers.find(p => p.id === saved.provider);
      if (match && match.implemented) {
        ui._aiWizard.selected = saved.provider;
        ui._aiWizard.needs_url = !!match.needs_url;
        ui._aiWizard.base_url = saved.base_url || ui._aiWizard.defaults[saved.provider] || "";
      }
    }
    ui._aiRenderProviders();
    ui._aiUpdateNextEnabled();
  } catch (e) {
    const grid = $("#ai-provider-grid");
    if (grid) grid.textContent = "Failed to load providers: " + e;
  }
};

ui._aiRenderProviders = function () {
  const grid = $("#ai-provider-grid");
  if (!grid) return;
  grid.textContent = "";
  ui._aiWizard.providers.forEach(p => {
    const card = document.createElement("button");
    card.type = "button";
    card.className = "ai-provider-card";
    if (!p.implemented) card.classList.add("unimplemented");
    if (p.id === ui._aiWizard.selected) card.classList.add("selected");
    card.dataset.aiProviderId = p.id;
    card.disabled = !p.implemented;
    const meta = p.implemented
      ? (p.needs_url ? "local · base url" : "cloud · api key")
      : "coming soon";
    card.innerHTML =
      `<span class="ai-provider-name">${p.label}</span>` +
      `<span class="ai-provider-meta">${meta}</span>`;
    grid.appendChild(card);
  });
};

ui._aiUpdateNextEnabled = function () {
  const next = $("#ai-step1-next");
  if (next) next.disabled = !ui._aiWizard.selected;
};

ui._aiShowStep = function (n) {
  [1, 2, 3].forEach(i => {
    const panel = document.getElementById(`ai-step-${i}`);
    const tab   = document.getElementById(`ai-step-tab-${i}`);
    if (panel) panel.classList.toggle("hidden", i !== n);
    if (tab)   tab.classList.toggle("active", i === n);
  });
};

ui._aiPrepareStep2 = function () {
  const sel = ui._aiWizard.selected;
  const provider = ui._aiWizard.providers.find(p => p.id === sel);
  ui._aiWizard.needs_url = !!(provider && provider.needs_url);
  const apiRow = $("#ai-apikey-row");
  const urlRow = $("#ai-baseurl-row");
  // Cloud providers (anthropic, openai) need an API key.
  // Local providers (ollama, lm_studio, litellm) need a base URL — and
  // openrouter is the special case: cloud + needs URL.
  const needsKey = !ui._aiWizard.needs_url || sel === "openrouter";
  if (apiRow) apiRow.classList.toggle("hidden", !needsKey);
  if (urlRow) urlRow.classList.toggle("hidden", !ui._aiWizard.needs_url);
  // Pre-fill the base URL with the provider's default.
  const urlInput = document.querySelector('#ai-baseurl-row input[name="ai_base_url"]');
  if (urlInput) {
    urlInput.value = ui._aiWizard.base_url
      || ui._aiWizard.defaults[sel]
      || "";
  }
  // Don't pre-fill the api key (security): use placeholder if a key exists.
  const apiInput = document.querySelector('#ai-apikey-row input[name="ai_api_key"]');
  if (apiInput) {
    apiInput.value = "";
    apiInput.placeholder = (ui._aiWizard.saved && ui._aiWizard.saved.has_api_key)
      ? "(api key saved — leave blank to keep)"
      : "";
  }
  const out = $("#ai-test-output");
  if (out) { out.textContent = ""; out.classList.remove("ok", "err"); }
};

ui._aiTestConnection = async function () {
  const out = $("#ai-test-output");
  if (out) {
    out.textContent = (tr("suggest.wiz.testing") || "Testing connection…");
    out.classList.remove("ok", "err");
  }
  const apiInput = document.querySelector('#ai-apikey-row input[name="ai_api_key"]');
  const urlInput = document.querySelector('#ai-baseurl-row input[name="ai_base_url"]');
  const payload = { provider: ui._aiWizard.selected };
  if (apiInput && apiInput.value) {
    payload.api_key = apiInput.value;
  } else if (ui._aiWizard.saved && ui._aiWizard.saved.has_api_key) {
    // The user is re-testing — we can't send the saved key (we redacted it
    // server-side). Tell them to retype.
    if (out) {
      out.textContent = (tr("suggest.wiz.retype_key")
        || "Re-type the API key to test again (saved keys are redacted).");
      out.classList.add("err");
    }
    return;
  }
  if (urlInput && urlInput.value) payload.base_url = urlInput.value;
  let r;
  try {
    r = await api.post("/ai/test-connection", payload);
  } catch (e) {
    if (out) {
      out.textContent = String(e);
      out.classList.add("err");
    }
    return;
  }
  if (!r || r.ok === false) {
    if (out) {
      out.textContent = (r && r.error) || "Connection failed.";
      out.classList.add("err");
    }
    return;
  }
  ui._aiWizard.api_key = (apiInput && apiInput.value) || "";
  ui._aiWizard.base_url = (urlInput && urlInput.value) || "";
  ui._aiWizard.models = r.models || [];
  if (out) {
    out.textContent = `OK — ${ui._aiWizard.models.length} model(s) found.`;
    out.classList.add("ok");
  }
  ui._aiPopulateModels();
  ui._aiShowStep(3);
};

ui._aiPopulateModels = function () {
  const sel = $("#ai-model-select");
  if (!sel) return;
  sel.innerHTML = "";
  const saved = (ui._aiWizard.saved && ui._aiWizard.saved.model) || "";
  ui._aiWizard.models.forEach(m => {
    const opt = document.createElement("option");
    opt.value = m.id;
    opt.textContent = m.label || m.id;
    if (m.id === saved) opt.selected = true;
    sel.appendChild(opt);
  });
};

ui._aiSaveSettings = async function () {
  const sel = $("#ai-model-select");
  const out = $("#ai-save-output");
  if (out) { out.textContent = ""; out.classList.remove("ok", "err"); }
  const payload = {
    provider: ui._aiWizard.selected || "",
    api_key:  ui._aiWizard.api_key || "",
    base_url: ui._aiWizard.base_url || "",
    model:    sel ? sel.value : "",
  };
  try {
    const r = await api.post("/ai/config", payload);
    ui._aiWizard.saved = r;
    if (out) {
      out.textContent = (tr("suggest.wiz.saved") || "Saved.");
      out.classList.add("ok");
    }
  } catch (e) {
    if (out) {
      out.textContent = String(e);
      out.classList.add("err");
    }
  }
};

document.addEventListener("click", async e => {
  // Step 1: provider card.
  const card = e.target.closest("[data-ai-provider-id]");
  if (card && !card.disabled) {
    ui._aiWizard.selected = card.dataset.aiProviderId;
    ui._aiRenderProviders();
    ui._aiUpdateNextEnabled();
    return;
  }
  if (e.target.id === "ai-step1-next") {
    if (!ui._aiWizard.selected) return;
    ui._aiPrepareStep2();
    ui._aiShowStep(2);
    return;
  }
  if (e.target.id === "ai-step2-back") {
    ui._aiShowStep(1);
    return;
  }
  if (e.target.id === "ai-step2-test") {
    await ui._aiTestConnection();
    return;
  }
  if (e.target.id === "ai-step3-back") {
    ui._aiShowStep(2);
    return;
  }
  if (e.target.id === "ai-step3-save") {
    await ui._aiSaveSettings();
    return;
  }
});

// Hook into Sync tab loader
const _origLoadSync = ui.loadSync;
ui.loadSync = async function() {
  await _origLoadSync.call(this);
  _loadSyncProvider();
};

// Hook nav (works for both old top-nav and new sidebar nav-link)
document.addEventListener("click", e => {
  const a = e.target.closest("a[data-view]");
  if (!a) return;
  e.preventDefault();
  ui.show(a.dataset.view);
});

// B4 — Apply confirmation modal (mirrors bin/run-apply.ps1 gating).
// Resolves true only when the user types the literal string "apply" and
// clicks Confirm. Native <dialog> handles Esc/backdrop/focus-trap. Falls
// back to window.confirm() if the browser predates <dialog>.
async function confirmApply(targetLabel) {
  const dlg = document.getElementById("apply-confirm-modal");
  if (!dlg || typeof dlg.showModal !== "function") {
    return window.confirm(
      (tr("apply.modal.warn") || "This will mutate your system.") +
      "\n\n" + (targetLabel || "all categories") +
      "\n\n" + (tr("apply.modal.instruction") || "Type 'apply' to proceed.")
    );
  }
  const label = document.getElementById("apply-modal-target");
  const input = document.getElementById("apply-modal-input");
  const ok    = document.getElementById("apply-modal-confirm");
  if (label) label.textContent = targetLabel || "all categories";
  if (input) { input.value = ""; }
  if (ok)    { ok.disabled = true; }
  if (input) {
    input.oninput = () => { if (ok) ok.disabled = (input.value !== "apply"); };
  }
  return await new Promise(resolve => {
    const onClose = () => {
      dlg.removeEventListener("close", onClose);
      const confirmed = dlg.returnValue === "confirm" && input && input.value === "apply";
      resolve(!!confirmed);
    };
    dlg.addEventListener("close", onClose, { once: true });
    try { dlg.showModal(); }
    catch { resolve(false); }
  });
}

// B5 — SSE stream consumer. Returns a teardown function that closes the
// connection. ``onEvent(kind, data)`` fires on ``status`` / ``sidecar`` /
// ``log``. ``onDone(data)`` fires on the terminal ``done`` event. ``onError``
// fires once on connection drop, after which we close the EventSource so the
// caller can decide whether to retry or fall back to polling /runs/active.
function streamActiveRun(onEvent, onDone, onError) {
  let es;
  try { es = new EventSource("/runs/active/stream"); }
  catch (e) { onError && onError(e); return () => {}; }
  es.addEventListener("status",  e => { try { onEvent && onEvent("status",  JSON.parse(e.data)); } catch {} });
  es.addEventListener("sidecar", e => { try { onEvent && onEvent("sidecar", JSON.parse(e.data)); } catch {} });
  es.addEventListener("log",     e => { try { onEvent && onEvent("log",     JSON.parse(e.data)); } catch {} });
  es.addEventListener("done",    e => {
    let data = {}; try { data = JSON.parse(e.data); } catch {}
    onDone && onDone(data);
    try { es.close(); } catch {}
  });
  es.onerror = (e) => {
    try { es.close(); } catch {}
    onError && onError(e);
  };
  return () => { try { es.close(); } catch {} };
}

// Helper that posts to /runs/async (M2.10) with legacy /runs fallback.
async function startRunWithSudo(body) {
  // Backend takes ``phases: list[Phase]``; legacy SPA also passed
  // ``phase: string``. Accept both shapes here so the mutating-check
  // works whichever payload key is present. Empty/absent phases means
  // "all default phases", which DOES include apply+cleanup → mutating.
  const phaseList = (Array.isArray(body.phases) && body.phases.length)
    ? body.phases
    : (body.phase ? [body.phase] : []);
  // Profile=quick maps to CHECK only on the backend (see runs.py
  // _PROFILE_PHASES). Treat it as read-only here so the user doesn't get
  // a sudo prompt for a read-only sweep.
  const isReadOnlyProfile = body.profile === "quick";
  const mutating = !body.dry_run && !isReadOnlyProfile && (
    phaseList.length === 0
    || phaseList.some(p => p === "apply" || p === "cleanup")
  );
  if (mutating) {
    const ok = await sudoMgr.ensure();
    if (!ok) throw new Error((window.tr && window.tr("sudo.cancelled")) || "Administrator authentication cancelled");
  }
  const tryEndpoint = async (url) => {
    try { return await api.post(url, body); }
    catch (e) {
      if (e.status === 401 && String(e.body || "").includes("SUDO-REQUIRED")) {
        const expiredPrompt = (window.tr && window.tr("sudo.expired_prompt"))
          || "Administrator session expired — re-authenticate to continue.";
        const ok = await sudoMgr.open(expiredPrompt);
        if (!ok) throw new Error((window.tr && window.tr("sudo.cancelled")) || "Administrator authentication cancelled");
        return await api.post(url, body);
      }
      throw e;
    }
  };
  try { return await tryEndpoint("/runs/async"); }
  catch (e) {
    if (e.status === 404 || e.status === 405) return await tryEndpoint("/runs");
    throw e;
  }
}

// Normalise a legacy ``data-quick`` body into the wire shape the new
// monorepo backend accepts (RunRequest with ``extra='forbid'``):
//   only:  string  → categories: list[string]
//   phase: string  → phases:     list[string]
// Anything not in the allowed set is dropped silently — the legacy
// ``extra_args`` channel is not supported on the new backend, so we
// neither send it nor 422 the user.
function normaliseRunBody(raw) {
  if (!raw || typeof raw !== "object") return {};
  const ALLOWED = new Set([
    "profile", "categories", "phases", "dry_run",
    "item_filter", "stop_on_failure",
  ]);
  const out = {};
  for (const [k, v] of Object.entries(raw)) {
    if (ALLOWED.has(k) && v !== null && v !== undefined) out[k] = v;
  }
  if (raw.only && !out.categories)  out.categories = [raw.only];
  if (raw.phase && !out.phases)     out.phases     = [raw.phase];
  return out;
}

// Quick-action buttons. Use delegation so dynamically-added buttons (e.g.
// inside a re-rendered card) inherit the handler.
document.addEventListener("click", async e => {
  const b = e.target.closest("[data-quick]");
  if (!b) return;
  let raw;
  try { raw = JSON.parse(b.dataset.quick); } catch { return; }
  // Confirm destructive NVIDIA path. We check the RAW body so the legacy
  // ``extra_args=["--nvidia"]`` flag still gates the confirmation even
  // though we drop it before posting (the new backend has no flag for it
  // — the user must apt-upgrade NVIDIA explicitly via the drivers
  // category instead).
  if ((raw.extra_args || []).includes("--nvidia")) {
    if (!confirm("Apply NVIDIA driver upgrade?\n\nNVIDIA drivers are held by default because DKMS rebuilds can fail. The upgrade will run apt with --only-upgrade nvidia-driver-*, then verify nvidia-smi.")) return;
  }
  const body = normaliseRunBody(raw);
  try {
    const r = await startRunWithSudo(body);
    ui.show("run");
    ui.attachStream(r.run_id);
    $("#stop-btn").disabled = false;
    ui.status(`run ${r.run_id} started`);
  } catch (err) { ui.status(String(err)); }
});

// Run form
$("#run-form").addEventListener("submit", async e => {
  e.preventDefault();
  const fd = new FormData(e.target);
  // See note on the per-category buttons above: the backend rejects
  // ``only``/``phase`` (singular) with HTTP 422 ``extra_forbidden``. Send
  // the list-shaped fields the new RunRequest accepts, and only include
  // them when the user actually selected a value (omit empty so the
  // backend uses its defaults — all categories, all phases).
  const profile = fd.get("profile");
  const only    = fd.get("only");
  const phase   = fd.get("phase");
  const body    = { dry_run: fd.get("dry_run") === "on" };
  if (profile) body.profile    = profile;
  if (only)    body.categories = [only];
  if (phase)   body.phases     = [phase];
  try {
    const r = await startRunWithSudo(body);
    ui.attachStream(r.run_id);
    $("#stop-btn").disabled = false;
    ui.status(`run ${r.run_id} started`);
  } catch (err) { ui.status(String(err)); }
});

// Sudo modal handlers
$("#sudo-form").addEventListener("submit", async e => {
  e.preventDefault();
  const pw = $("#sudo-pass").value;
  $("#sudo-error").textContent = "";
  if (!pw) {
    $("#sudo-error").textContent = "password required";
    return;
  }
  try {
    const r = await fetch("/sudo/auth", {
      method: "POST",
      headers: {"content-type": "application/json"},
      body: JSON.stringify({password: pw}),
    });
    if (r.ok) {
      sudoMgr.close(true);
      ui.status("sudo authenticated");
    } else {
      const t = await r.text();
      $("#sudo-error").textContent = `auth failed: ${t.slice(0, 200)}`;
    }
  } catch (err) {
    $("#sudo-error").textContent = String(err);
  } finally {
    $("#sudo-pass").value = "";  // never linger
  }
});
$("#sudo-cancel").addEventListener("click", () => sudoMgr.close(false));

// Belt-and-suspenders: explicit Enter-key handler on the password input.
// Native form submit on Enter should already work (<button type="submit"> +
// <form>), but in some browser/locale combinations the keystroke gets
// swallowed when the modal is reopened (focus race after .hidden toggle).
// This guarantees Enter ALWAYS submits the form. Sesja 56.
$("#sudo-pass").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.altKey && !e.ctrlKey && !e.metaKey) {
    e.preventDefault();
    // Trigger the submit handler via the form's requestSubmit() — that
    // path runs the registered submit listener, where Enter's native
    // form-submission would have led us anyway.
    const form = $("#sudo-form");
    if (form && typeof form.requestSubmit === "function") {
      form.requestSubmit();
    } else if (form) {
      // Old browsers without requestSubmit: dispatch a synthetic event.
      form.dispatchEvent(new Event("submit", { cancelable: true, bubbles: true }));
    }
  }
});
// Refresh sudo indicator on load + every 30s
sudoMgr.refreshIndicator();
setInterval(() => sudoMgr.refreshIndicator(), 30000);

// Refresh service indicator on load + every 30s; bind buttons in case the
// Settings tab is the very first view the user opens.
serviceMgr.refreshIndicator();
setInterval(() => serviceMgr.refreshIndicator(), 30000);
_bindServiceButtons();

// First-run wizard — step-router controls
document.addEventListener("click", e => {
  if (!ui.wizard) return;
  // Skip button (top-right of wizard header).
  if (e.target.id === "wizard-skip-top") { ui.wizard.finalize(true); return; }
  // Next / Finish (footer): role flips on the last step.
  if (e.target.id === "wizard-next") {
    if (e.target.dataset.role === "finish") ui.wizard.finalize(false);
    else ui.wizard.advance();
    return;
  }
  if (e.target.id === "wizard-back") { ui.wizard.back(); return; }
});

// Reboot banner + every Refresh button on the SPA. The Refresh buttons
// share a tiny pattern: bust the right cache slice, paint the spinner
// on the button, and re-run the loader with refresh=true.
document.addEventListener("click", async e => {
  if (e.target.id === "reboot-now-btn")     ui.rebootNow();
  if (e.target.id === "reboot-dismiss-btn") $("#reboot-banner").classList.add("hidden");
  const overviewBtn = e.target.closest("#overview-refresh-btn");
  if (overviewBtn) {
    await runWithRefreshSpinner(overviewBtn, async () => {
      ui.invalidateCaches();
      ui._loaded.overview = true;
      await ui.loadOverview();
      ui.checkRebootBanner();
    });
    return;
  }
  const appsBtn = e.target.closest("#apps-refresh-btn");
  if (appsBtn) {
    await runWithRefreshSpinner(appsBtn, async () => {
      frontendCache.invalidatePrefix("/apps");
      ui._loaded.apps = false;
      await ui.loadApps({ refresh: true });
    });
    return;
  }
  // Apps view: status chip toggle.
  const stChip = e.target.closest("[data-apps-status-chip]");
  if (stChip) {
    const v = stChip.dataset.appsStatusChip;
    const set = ui._appsState.statuses;
    if (set.has(v)) set.delete(v); else set.add(v);
    ui._renderAppsFilters();
    ui._renderAppsTable();
    return;
  }
  // Apps view: category chip toggle.
  const catChip = e.target.closest("[data-apps-category-chip]");
  if (catChip) {
    const v = catChip.dataset.appsCategoryChip;
    const set = ui._appsState.categories;
    if (set.has(v)) set.delete(v); else set.add(v);
    ui._renderAppsFilters();
    ui._renderAppsTable();
    return;
  }
  // Apps view: clear all filters.
  if (e.target.id === "apps-clear-filters") {
    ui._appsState.search = "";
    ui._appsState.categories.clear();
    ui._appsState.statuses.clear();
    const search = $("#apps-search");
    if (search) search.value = "";
    ui._renderAppsFilters();
    ui._renderAppsTable();
    return;
  }
  // Apps view: collapse/expand a category group.
  const groupHeader = e.target.closest("[data-apps-group]");
  if (groupHeader) {
    const c = groupHeader.dataset.appsGroup;
    const set = ui._appsState.collapsed;
    if (set.has(c)) set.delete(c); else set.add(c);
    ui._renderAppsTable();
    return;
  }
  const addBtn = e.target.closest("[data-apps-add]");
  if (addBtn) ui.appsAdd(addBtn.dataset.pkg, addBtn.dataset.cat);
  const rmBtn = e.target.closest("[data-apps-rm]");
  if (rmBtn) ui.appsRemove(rmBtn.dataset.pkg, rmBtn.dataset.cat);
  const invBtn = e.target.closest("#inv-refresh-btn");
  if (invBtn) {
    await runWithRefreshSpinner(invBtn, async () => {
      // Inventory-only refresh: clears the backend cache too, then forces
      // a network re-read in the frontend cache.
      api.post("/inventory/refresh", {}).catch(()=>{});
      frontendCache.invalidatePrefix("/inventory");
      window.INV_SUMMARY = null;
      await ui.loadInventoryDashboard({ refresh: true });
    });
    return;
  }
  const catsBtn = e.target.closest("#categories-refresh-btn");
  if (catsBtn) {
    await runWithRefreshSpinner(catsBtn, async () => {
      api.post("/inventory/refresh", {}).catch(()=>{});
      frontendCache.invalidatePrefix("/inventory");
      frontendCache.invalidatePrefix("/categories");
      window.INV_SUMMARY = null;
      ui._loaded.categories = false;
      await ui.loadCategories({ refresh: true });
    });
    return;
  }
  // Action 1: Build inventory — explicit "scan now" button on Overview.
  // Same effect as inv-refresh-btn but more discoverable + numbered.
  const action1 = e.target.closest("#action-1-inventory");
  if (action1) {
    await runWithRefreshSpinner(action1, async () => {
      api.post("/inventory/refresh", {}).catch(()=>{});
      frontendCache.invalidatePrefix("/inventory");
      frontendCache.invalidatePrefix("/apps");
      window.INV_SUMMARY = null;
      ui._loaded.categories = false;
      await ui.loadInventoryDashboard({ refresh: true });
      ui.status(tr("overview.action_1_inventory_hint")
                || "inventory rebuilt");
    });
    return;
  }
});

// Hosts refresh
document.addEventListener("click", e => {
  if (e.target.id === "hosts-refresh-btn") ui.loadHosts();
});

// Suggestions panel
document.addEventListener("click", async e => {
  if (e.target.id === "suggest-refresh-btn") { ui._loaded.suggest = false; ui.loadSuggestions(); }
  const ap = e.target.closest("[data-sg-apply]");
  if (ap) {
    try { ui.applySuggestion(JSON.parse(ap.dataset.sgApply)); }
    catch (err) { ui.status(String(err)); }
  }
  // New: suggestion card with a run-async action — kicks off /runs/async.
  const ra = e.target.closest("[data-sg-run-async]");
  if (ra) {
    try {
      const payload = JSON.parse(ra.dataset.sgRunAsync.replace(/&#39;/g, "'"));
      const r = await api.post("/runs/async", payload);
      ui.status(`run started: ${r.run_id || ""}`);
    } catch (err) { ui.status(String(err)); }
  }
  const dm = e.target.closest("[data-sg-dismiss]");
  if (dm) ui.dismissSuggestion(dm.dataset.sgDismiss);
  if (e.target.id === "health-recheck-btn") {
    try { await api.post("/health/run"); } catch {}
    ui.loadHealth();
  }
  if (e.target.id === "backup-export-btn") {
    location.href = "/backup/export";
  }
});

// AI form (in Suggestions tab)
document.addEventListener("submit", async e => {
  if (e.target && e.target.id === "ai-form") {
    e.preventDefault();
    const f = e.target;
    const out = $("#ai-output");
    try {
      const cur = await api.get("/settings");
      const merged = {...cur, ai: {
        provider: f.elements.ai_provider.value,
        api_key:  f.elements.ai_api_key.value,
        model:    f.elements.ai_model.value,
      }};
      const r = await fetch("/settings", {method:"PUT",
        headers:{"content-type":"application/json"}, body: JSON.stringify(merged)});
      out.textContent = r.ok ? "saved" : `error ${r.status}`;
      ui._loaded.suggest = false; ui.loadSuggestions();
    } catch (err) { out.textContent = String(err); }
  }
});

// Apps tab in-config checkboxes (default-include model). The new
// data attribute is ``data-apps-toggle`` written by ``ui.loadApps()``.
// ``data-excl-toggle`` (legacy) is preserved for any old SPA tabs that
// might still have it cached after a long-running session.
document.addEventListener("change", e => {
  const t = e.target.closest("[data-apps-toggle]");
  if (t) ui.toggleExclusion(t.dataset.name, t.dataset.cat, t.checked);
});
document.addEventListener("change", e => {
  const t = e.target.closest("[data-excl-toggle]");
  if (t) ui.toggleExclusion(t.dataset.pkg, t.dataset.cat, t.checked);
});

// Apps tab explicit Action-column buttons (Add to config / Remove from config).
// These are the obvious-button counterpart to the in-config checkbox.
document.addEventListener("click", async e => {
  const ex = e.target.closest("[data-apps-exclude]");
  if (ex) {
    try {
      await api.post("/apps/exclude", {category: ex.dataset.cat, name: ex.dataset.name});
      ui.status(`${ex.dataset.cat}:${ex.dataset.name} → excluded`);
      ui._loaded.apps = false; ui._loaded.categories = false;
      ui.loadApps();
    } catch (err) { ui.status(String(err)); }
  }
  const inc = e.target.closest("[data-apps-include]");
  if (inc) {
    try {
      await api.post("/apps/include", {category: inc.dataset.cat, name: inc.dataset.name});
      ui.status(`${inc.dataset.cat}:${inc.dataset.name} → in config`);
      ui._loaded.apps = false; ui._loaded.categories = false;
      ui.loadApps();
    } catch (err) { ui.status(String(err)); }
  }
  // Per-app update history toggle. Inserts/removes a sibling <tr> with
  // the last N version transitions (newest first) for the clicked app.
  const hist = e.target.closest("[data-apps-history]");
  if (hist) {
    const row = hist.closest("tr");
    if (!row) return;
    const next = row.nextElementSibling;
    if (next && next.classList.contains("apps-history-row")) {
      next.remove();
      return;
    }
    const cat = hist.dataset.cat;
    const name = hist.dataset.name;
    try {
      const cols = row.children.length || 7;
      const histRow = document.createElement("tr");
      histRow.className = "apps-history-row";
      const td = document.createElement("td");
      td.colSpan = cols;
      td.style.background = "var(--bg-sunk, transparent)";
      td.style.padding = "10px 14px";
      const data = await api.get(
        `/apps/${encodeURIComponent(cat)}/${encodeURIComponent(name)}/history?limit=20`,
      );
      const entries = (data && data.history) || [];
      if (!entries.length) {
        td.textContent = tr("apps.history.empty")
          || "No update history yet — try an apply phase.";
      } else {
        const title = document.createElement("div");
        title.style.fontWeight = "600";
        title.style.marginBottom = "6px";
        title.textContent = (tr("apps.history.title") || "Update history")
          + ` (${entries.length})`;
        td.appendChild(title);
        const tbl = document.createElement("table");
        tbl.className = "tbl";
        tbl.style.width = "100%";
        const thead = document.createElement("thead");
        const trh = document.createElement("tr");
        [
          tr("apps.history.column.when")   || "When",
          tr("apps.history.column.from")   || "From",
          tr("apps.history.column.to")     || "To",
          tr("apps.history.column.status") || "Status",
        ].forEach(label => {
          const th = document.createElement("th");
          th.textContent = label;
          trh.appendChild(th);
        });
        thead.appendChild(trh);
        tbl.appendChild(thead);
        const tb = document.createElement("tbody");
        entries.forEach(h => {
          const r = document.createElement("tr");
          // When (best-effort: cut microseconds; show local-ish form).
          const tdWhen = document.createElement("td");
          tdWhen.className = "mono";
          let when = String(h.applied_at || "—");
          // "2026-05-08T22:47:00+00:00" -> "2026-05-08 22:47"
          when = when.replace("T", " ").slice(0, 16);
          tdWhen.textContent = when;
          r.appendChild(tdWhen);
          const tdFrom = document.createElement("td");
          tdFrom.className = "mono";
          tdFrom.textContent = h.from || "—";
          r.appendChild(tdFrom);
          const tdTo = document.createElement("td");
          tdTo.className = "mono";
          tdTo.textContent = h.to || "—";
          r.appendChild(tdTo);
          const tdSt = document.createElement("td");
          const stMap = {
            success:  {sym: "✓", cls: "st-ok"},
            failed:   {sym: "⚠", cls: "st-err"},
            triggered:{sym: "⏳", cls: "st-info"},
            missing:  {sym: "+", cls: "st-warn"},
          };
          const sym = stMap[h.status] || {sym: h.status || "?", cls: "st-skip"};
          const span = document.createElement("span");
          span.className = "st-pill " + sym.cls;
          span.textContent = `${sym.sym} ${h.status || ""}`.trim();
          tdSt.appendChild(span);
          r.appendChild(tdSt);
          tb.appendChild(r);
        });
        tbl.appendChild(tb);
        td.appendChild(tbl);
      }
      histRow.appendChild(td);
      row.parentNode.insertBefore(histRow, row.nextSibling);
    } catch (err) {
      ui.status(String(err));
    }
  }
});

// Backup import (file upload)
document.addEventListener("change", async e => {
  if (e.target && e.target.id === "backup-import-file") {
    const f = e.target.files[0];
    if (!f) return;
    const out = $("#backup-output");
    out.textContent = `Uploading ${f.name} (${Math.round(f.size/1024)}KB)…`;
    try {
      const r = await fetch("/backup/import", {
        method: "POST",
        headers: {"content-type": "application/gzip"},
        body: f,
      });
      const j = await r.json();
      out.textContent = r.ok
        ? `restored ${(j.restored||[]).length} files. Reload the page.`
        : `failed: ${JSON.stringify(j)}`;
    } catch (err) { out.textContent = String(err); }
  }
});

$("#stop-btn").addEventListener("click", async () => {
  try {
    await api.post("/runs/active/stop");
    ui.status("stop sent");
  } catch (e) { ui.status(String(e)); }
});

// Sync screen buttons
document.addEventListener("click", e => {
  const id = e.target.id;
  if (id === "git-fetch-btn") ui.syncCall("git fetch", () => api.post("/git/fetch"));
  if (id === "git-pull-btn")  ui.syncCall("git pull",  () => api.post("/git/pull"));
  if (id === "git-push-btn")  ui.syncCall("git push",  () => api.post("/git/push"));
  if (id === "sync-export-dry-btn") ui.syncCall("sync export (dry)", () => api.post("/sync/export?dry_run=true"));
  if (id === "sync-export-btn")     ui.syncCall("sync export",       () => api.post("/sync/export?dry_run=false"));
});

// Settings form
const settingsForm = $("#settings-form");
if (settingsForm) {
  settingsForm.addEventListener("submit", async e => {
    e.preventDefault();
    const out = $("#settings-output");
    try {
      const r = await fetch("/settings", {
        method: "PUT",
        headers: {"content-type": "application/json"},
        body: JSON.stringify(ui.collectSettings()),
      });
      const j = await r.json();
      out.textContent = "saved:\n" + JSON.stringify(j, null, 2);
    } catch (err) { out.textContent = String(err); }
  });
}
document.addEventListener("click", async e => {
  const pi = e.target.closest("[data-profile-import]");
  if (pi) {
    const name = pi.dataset.profileImport;
    const dry = pi.dataset.dry === "1";
    if (!dry && !confirm(`Apply profile '${name}' - will append packages to your config/*.list files?`)) return;
    try {
      const r = await api.post("/profiles/import", {name, dry_run: dry});
      ui.status(dry ? `preview: ${name}` : `imported: ${name}`);
      $("#settings-output").textContent = (r.stdout || "") + (r.stderr ? "\n" + r.stderr : "");
    } catch (err) { ui.status(String(err)); }
  }
  if (e.target.id === "updates-check-btn") {
    const out = $("#updates-output");
    out.textContent = "checking…";
    try {
      const r = await api.get("/updates/check");
      if (!r.enabled) { out.textContent = "disabled - set GitHub repo first."; return; }
      if (r.error) { out.textContent = `error: ${r.error}`; return; }
      out.textContent = r.newer_available
        ? `📦 newer release available: ${r.latest} (current ${r.current}) - ${r.url}`
        : `up to date - current ${r.current}, latest ${r.latest||"?"}`;
    } catch (err) { out.textContent = String(err); }
  }
});
document.addEventListener("click", async e => {
  const id = e.target.id;
  const out = $("#settings-output");
  if (id === "scheduler-install-btn") {
    try {
      const r = await api.post("/scheduler/install", ui.collectSettings().scheduler);
      out.textContent = "scheduler/install:\n" + JSON.stringify(r, null, 2);
      ui.loadSettings();
    } catch (err) { out.textContent = String(err); }
  }
  if (id === "scheduler-remove-btn") {
    try {
      const r = await api.post("/scheduler/remove");
      out.textContent = "scheduler/remove:\n" + JSON.stringify(r, null, 2);
      ui.loadSettings();
    } catch (err) { out.textContent = String(err); }
  }
});

// -- Inject inline icons into nav + topbar buttons ----------------------
function injectIcons() {
  if (!window.ICONS) return;
  const map = { "help": "help", "about": "about" };
  document.querySelectorAll("[data-icon]").forEach(el => {
    const slot = el.querySelector(".nav-icon");
    const target = slot || el;
    const key = map[el.dataset.icon] || el.dataset.icon;
    const ic = window.ICONS[key];
    if (ic) target.innerHTML = ic;
  });
  // data-icon-prefix=<key>: prepend the icon to the existing button text.
  document.querySelectorAll("[data-icon-prefix]").forEach(el => {
    if (el.querySelector(".icon-prefix")) return;
    const ic = window.ICONS[el.dataset.iconPrefix];
    if (!ic) return;
    const wrap = document.createElement("span");
    wrap.className = "icon-prefix";
    wrap.style.cssText = "display:inline-flex;align-items:center;margin-right:6px;vertical-align:-3px;";
    wrap.innerHTML = ic;
    el.insertAdjacentElement("afterbegin", wrap);
  });
  // Topbar buttons get icons that reflect current state.
  const setBtn = (id, key) => {
    const b = document.getElementById(id);
    if (b && window.ICONS[key]) b.innerHTML = window.ICONS[key];
  };
  setBtn("sidebar-toggle", "menu");
  setBtn("lang-switcher",  "globe");
  setBtn("theme-switcher", (document.documentElement.dataset.theme === "dark") ? "moon" : "sun");
  setBtn("font-switcher",  "type");
}

// -- Sidebar drawer (mobile) --------------------------------------------
function bindSidebar() {
  const shell = document.body;
  const open  = () => { shell.classList.add("sidebar-open"); $("#sidebar-backdrop")?.classList.remove("hidden"); };
  const close = () => { shell.classList.remove("sidebar-open"); $("#sidebar-backdrop")?.classList.add("hidden"); };
  $("#sidebar-toggle")?.addEventListener("click", () => {
    shell.classList.contains("sidebar-open") ? close() : open();
  });
  $("#sidebar-backdrop")?.addEventListener("click", close);
  // Close drawer after picking a nav item on mobile.
  document.addEventListener("click", e => {
    if (window.matchMedia("(max-width: 768px)").matches && e.target.closest(".sidebar-nav .nav-link")) close();
  });
}

// -- Topbar switchers: theme / language / font-size ---------------------
function bindSwitchers() {
  const root = document.documentElement;
  // Theme cycle: dark → light → auto (system). Dark is the primary theme
  // of the Ascendo design system; light is the alternate; auto follows
  // the OS via prefers-color-scheme. The user explicitly asked for the
  // "system" option to come back (it was removed in the UX baseline pass).
  const VALID_THEMES = ["dark", "light", "auto"];
  const normalizePref = (p) => (VALID_THEMES.includes(p) ? p : "dark");
  const NEXT_THEME = { dark: "light", light: "auto", auto: "dark" };
  const ICON_FOR = { dark: "moon", light: "sun", auto: "monitor" };
  const repaintThemeIcon = (pref) => {
    if (!window.ICONS) return;
    const b = $("#theme-switcher");
    if (!b) return;
    // window.ICONS values are static SVG strings authored in icons.js —
    // not user input — so the innerHTML assignment is safe. Keep the
    // existing pattern for symmetry with lang/font switchers.
    b.innerHTML = window.ICONS[ICON_FOR[pref]] || window.ICONS.moon;
    b.title = `Theme: ${pref} (click to cycle dark → light → auto)`;
  };
  $("#theme-switcher")?.addEventListener("click", () => {
    const cur  = normalizePref(root.dataset.themePref
                  || (window.SETTINGS_CACHE?.ui?.theme));
    const next = NEXT_THEME[cur] || "dark";
    root.dataset.themePref = next;
    try { localStorage.setItem("ui-theme", next); } catch {}
    window.applyTheme(next);
    window.SETTINGS_CACHE = window.SETTINGS_CACHE || {};
    window.SETTINGS_CACHE.ui = {...(window.SETTINGS_CACHE.ui || {}), theme: next};
    fetch("/settings", {method:"PUT", headers:{"content-type":"application/json"},
      body: JSON.stringify(window.SETTINGS_CACHE)}).catch(()=>{});
    repaintThemeIcon(next);
    ui.status(`theme: ${next}`);
  });
  const initial = normalizePref(root.dataset.themePref
    || (() => { try { return localStorage.getItem("ui-theme"); } catch { return null; } })());
  root.dataset.themePref = initial;
  // Re-apply the chosen theme so the inline pre-paint hint
  // ("dark") gets overridden when the persisted value is "light" or "auto".
  window.applyTheme && window.applyTheme(initial);
  repaintThemeIcon(initial);
  // Live-track OS theme changes when the user is on "auto".
  if (window.matchMedia) {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onSystemChange = () => {
      if ((root.dataset.themePref || "dark") === "auto") {
        window.applyTheme && window.applyTheme("auto");
      }
    };
    if (mq.addEventListener) mq.addEventListener("change", onSystemChange);
    else if (mq.addListener) mq.addListener(onSystemChange);  // Safari < 14
  }
  // Language cycle: en ↔ pl
  $("#lang-switcher")?.addEventListener("click", () => {
    const cur = window.UI_LANG || "en";
    const next = cur === "en" ? "pl" : "en";
    window.UI_LANG = next; window.applyI18n();
    try { localStorage.setItem("ui-locale", next); } catch {}
    fetch("/settings", {method:"PUT", headers:{"content-type":"application/json"},
      body: JSON.stringify({...(window.SETTINGS_CACHE||{}), ui:{...((window.SETTINGS_CACHE||{}).ui||{}), language: next}})}).catch(()=>{});
    ui.status(`language: ${next}`);
  });
  // Font cycle: sm → md → lg → sm
  $("#font-switcher")?.addEventListener("click", () => {
    const order = ["sm", "md", "lg"];
    const cur = root.dataset.font || "md";
    const next = order[(order.indexOf(cur) + 1) % order.length];
    root.dataset.font = next;
    try { localStorage.setItem("ui-font", next); } catch {}
    ui.status(`font size: ${next}`);
  });
  // Restore persisted font choice.
  try {
    const f = localStorage.getItem("ui-font");
    if (f && ["sm","md","lg"].includes(f)) root.dataset.font = f;
    else root.dataset.font = "md";
  } catch { root.dataset.font = "md"; }
}

// -- Categories add-widget: append package to a config list -------------
async function bindCatsAddWidget() {
  // Populate <select> with categories.
  try {
    const cats = (await api.get("/categories")).categories || [];
    const sel = $("#cats-add-cat");
    if (sel && !sel.options.length || (sel && sel.options.length <= 1)) {
      for (const c of cats) {
        if (!c.id) continue;
        // Skip categories without a config/*.list file (drivers, inventory).
        if (["drivers", "inventory"].includes(c.id)) continue;
        const o = document.createElement("option");
        o.value = c.id; o.textContent = c.id;
        sel.appendChild(o);
      }
    }
  } catch {}
}
// Inline +add / remove buttons inside Categories detail.
// IMPORTANT: inventory.py keeps a 60s cache; without an explicit refresh the
// detail row won't show the new in_config=true state until the cache expires.
async function _refreshAfterCfgEdit(cat) {
  try {
    await api.post(`/inventory/refresh?category=${encodeURIComponent(cat)}`, {});
  } catch {}
  ui._loaded.apps = false; ui._loaded.categories = false;
}
document.addEventListener("click", async e => {
  const ad = e.target.closest("[data-cat-add]");
  if (ad) {
    try {
      const r = await api.post("/apps/include", {category: ad.dataset.cat, name: ad.dataset.pkg});
      ui.status(r.ok ? `added ${ad.dataset.cat}:${ad.dataset.pkg}` : `error: ${(r.stderr||"").slice(0,120)}`);
      await _refreshAfterCfgEdit(ad.dataset.cat);
      ui.loadCategoryDetail(ad.dataset.cat);
    } catch (err) { ui.status(String(err)); }
  }
  const dg = e.target.closest("[data-apt-downgrade]");
  if (dg) {
    const ver = prompt(`Downgrade ${dg.dataset.pkg} to which version?\n\nCurrently installed: ${dg.dataset.ver}\n\nFind a candidate with:  apt-cache madison ${dg.dataset.pkg}\nThis runs:  sudo apt-get install --allow-downgrades ${dg.dataset.pkg}=<version>`);
    if (!ver) return;
    if (!confirm(`Confirm: downgrade ${dg.dataset.pkg} → ${ver}?`)) return;
    const ok = await sudoMgr.ensure();
    if (!ok) { ui.status("sudo required"); return; }
    ui.status(`downgrading ${dg.dataset.pkg}=${ver}…`);
    try {
      const r = await api.post("/apt/downgrade", {package: dg.dataset.pkg, version: ver});
      ui.status(r.ok ? `downgraded ${dg.dataset.pkg} → ${ver}` : `failed: ${(r.stderr||"").slice(0,150)}`);
      ui._loaded.categories = false;
      ui.loadCategoryDetail("apt");
    } catch (err) { ui.status(String(err)); }
  }
  const rm = e.target.closest("[data-cat-rm]");
  if (rm) {
    if (!confirm(`Remove ${rm.dataset.pkg} from ${rm.dataset.cat} config?\n(does NOT uninstall the package itself)`)) return;
    try {
      const r = await api.post("/apps/exclude", {category: rm.dataset.cat, name: rm.dataset.pkg});
      ui.status(r.ok ? `removed ${rm.dataset.cat}:${rm.dataset.pkg}` : `error: ${(r.stderr||"").slice(0,120)}`);
      await _refreshAfterCfgEdit(rm.dataset.cat);
      ui.loadCategoryDetail(rm.dataset.cat);
    } catch (err) { ui.status(String(err)); }
  }
});

document.addEventListener("click", async e => {
  if (e.target.id === "cats-add-btn") {
    const cat = $("#cats-add-cat").value;
    const pkg = $("#cats-add-pkg").value.trim();
    const out = $("#cats-add-out");
    if (!cat || !pkg) { out.textContent = "pick a category and type a package name"; return; }
    out.textContent = "adding…";
    try {
      const r = await api.post("/apps/include", {category: cat, name: pkg});
      out.textContent = r.ok ? `added ${cat}:${pkg}` : `error: ${(r.stderr||"").slice(0,200)}`;
      $("#cats-add-pkg").value = "";
      // Bust the 60s inventory cache so the change is visible immediately.
      try { await api.post(`/inventory/refresh?category=${encodeURIComponent(cat)}`, {}); } catch {}
      ui._loaded.categories = false; ui._loaded.apps = false;
      ui.show("categories");
    } catch (err) { out.textContent = String(err); }
  }
});

// B7 — Theme preference helper. Resolves "auto" against the OS via
// matchMedia and forwards the concrete dark/light to the existing
// applyTheme(). Persists to localStorage so the next paint can read it
// synchronously before /settings comes back from the network.
window.applyThemePref = function applyThemePref(value) {
  const v = (value === "dark" || value === "light" || value === "auto") ? value : "auto";
  const real = v === "auto"
    ? (window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark")
    : v;
  window.applyTheme(real);
  document.documentElement.setAttribute("data-theme", real);
};

// Init: load settings first so theme/language are applied before paint flicker
async function bootstrap() {
  // B7 — read localStorage.ascendo_theme synchronously on first paint so the
  // wizard's theme choice survives reload even before /settings resolves.
  let lsTheme = null;
  let lsLocale = null;
  try { lsTheme = localStorage.getItem("ui-theme") || localStorage.getItem("ascendo_theme"); } catch {}
  try { lsLocale = localStorage.getItem("ui-locale") || localStorage.getItem("ui-language"); } catch {}
  if (lsTheme) {
    try { window.applyTheme(lsTheme); } catch {}
  }
  try {
    const s = await api.get("/settings");
    window.SETTINGS_CACHE = s;
    // localStorage wins over /settings — the wizard wrote it last.
    const themePref = lsTheme || (s.ui && s.ui.theme) || "dark";
    const langPref  = lsLocale || (s.ui && s.ui.language) || "auto";
    window.applyTheme(themePref);
    window.UI_LANG = (langPref === "en" || langPref === "pl")
      ? langPref
      : window.detectLanguage();
    window.applyI18n();
  } catch {
    window.applyTheme(lsTheme || "dark");
    window.UI_LANG = (lsLocale === "en" || lsLocale === "pl")
      ? lsLocale
      : window.detectLanguage();
    window.applyI18n();
  }
  // Adapter identity → drives platform-conditional UI (e.g. NVIDIA buttons
  // on Linux/Windows only, "Administrator authorized" wording on Windows).
  try {
    const v = await api.get("/version");
    const adapter = (v && (v.adapter || v.adapter_name)) || "unknown";
    document.documentElement.setAttribute("data-adapter", adapter);
    window.ADAPTER_NAME = adapter;
    // Edition gate (basic | dev). Default to "basic" so the conservative
    // state wins if the backend doesn't yet emit `edition` on /version.
    document.documentElement.setAttribute("data-edition", (v && v.edition) || "basic");
    window.ASCENDO_EDITION = (v && v.edition) || "basic";
  } catch {
    document.documentElement.setAttribute("data-adapter", "unknown");
    document.documentElement.setAttribute("data-edition", "basic");
    window.ASCENDO_EDITION = "basic";
  }

  injectIcons();
  bindSidebar();
  bindSwitchers();
  bindCatsAddWidget();
  const start = location.hash.replace("#", "") || "overview";
  ui.show(start);
  ui.checkRebootBanner();
  ui.maybeShowWizard();
  // OS-theme listener removed: dark/light is now an explicit binary
  // preference (no "auto" track). prefers-color-scheme no longer drives
  // the SPA - the design system sets dark as the deliberate default.
}

// Apply settings live when the user changes theme/language in the form
document.addEventListener("change", e => {
  if (e.target && e.target.id === "ui-theme-select") {
    window.applyTheme(e.target.value);
  }
  if (e.target && e.target.id === "ui-language-select") {
    const v = e.target.value;
    window.UI_LANG = (v === "en" || v === "pl") ? v : window.detectLanguage();
    window.applyI18n();
  }
});

bootstrap();
window.ui = ui;

// =====================================================================
// Run Center: Live detail panel controller (renders BELOW the summary
// cards). Fed by the SSE handlers in ui.attachStream — pure DOM, no
// innerHTML interpolation. The panel renders a per-(phase, source)
// progress bar + a streaming Packages-found list + a Diagnostics tail
// + a chip-row navigator. All labels go through tr() for EN+PL parity.
// =====================================================================
(function () {
  function $$(id) { return document.getElementById(id); }
  function trKey(key, fallback) {
    if (typeof window.tr !== "function") return fallback;
    const v = window.tr(key);
    return (v === key) ? fallback : v;
  }
  function clearChildren(node) {
    while (node && node.firstChild) node.removeChild(node.firstChild);
  }
  function fmtElapsed(ms) {
    if (!Number.isFinite(ms) || ms < 0) return "";
    const totalSec = Math.floor(ms / 1000);
    const m = Math.floor(totalSec / 60);
    const s = totalSec % 60;
    return m > 0 ? (m + "m " + String(s).padStart(2, "0") + "s")
                 : (s + "s");
  }
  function statusToCls(status) {
    if (status === "success" || status === "ok" || status === "up_to_date") return "success";
    if (status === "failed" || status === "error") return "failed";
    if (status === "partial" || status === "warn" || status === "warning") return "partial";
    if (status === "skipped") return "skipped";
    if (status === "running" || status === "planned") return "running";
    if (status === "triggered") return "triggered";
    return "skipped";
  }
  function pillClassFor(status) {
    const c = statusToCls(status);
    if (c === "success")   return "st-pill st-ok";
    if (c === "failed")    return "st-pill st-err";
    if (c === "partial")   return "st-pill st-warn";
    if (c === "running")   return "st-pill st-info";
    if (c === "triggered") return "st-pill st-triggered";
    return "st-pill st-skip";
  }
  function statusLabel(status) {
    const key = "run.detail.status." + (status || "unknown");
    const fb = (status || "unknown");
    return trKey(key, fb);
  }

  // Per-package row keyed by item.name + index fallback.
  function packageKey(item, idx) {
    const id = item && (item.name || item.id);
    return id ? String(id) : ("__row_" + idx);
  }

  // ---- State -----------------------------------------------------------
  // runDetail.state shape:
  //   {
  //     runId: string|null,
  //     activeKey: "<phase>__<source>" | null,
  //     phaseSource: Map<key, {
  //         phase, source, sidecar, status,
  //         items: Map<itemKey, item>,
  //         itemOrder: [itemKey, ...],
  //         messages: [{level, text, time}],
  //         lastEventAt: number,
  //         done: boolean,
  //     }>,
  //     started: number, finished: number|null,
  //     userScrolledDiagnostics: boolean,
  //     elapsedTimer: handle,
  //   }
  function newState() {
    return {
      runId: null,
      activeKey: null,
      phaseSource: new Map(),
      started: Date.now(),
      finished: null,
      userScrolledDiagnostics: false,
      elapsedTimer: null,
      etaSeconds: null,
    };
  }
  let state = newState();

  function setActive(key) {
    if (!state.phaseSource.has(key)) return;
    state.activeKey = key;
    render();
  }

  function ensureBucket(phase, source) {
    const key = (phase || "?") + "__" + (source || "?");
    let b = state.phaseSource.get(key);
    if (!b) {
      b = {
        phase: phase || "?",
        source: source || "?",
        sidecar: null,
        status: "running",
        items: new Map(),
        itemOrder: [],
        messages: [],
        lastEventAt: Date.now(),
        done: false,
      };
      state.phaseSource.set(key, b);
    }
    return { key, bucket: b };
  }

  function ingestSidecar(sc) {
    if (!sc || typeof sc !== "object") return;
    const phase = sc.phase || (sc.kind || "?");
    const source = sc.category || sc.source_type || "?";
    const { key, bucket } = ensureBucket(phase, source);
    bucket.sidecar = sc;
    bucket.status = sc.status || "running";
    bucket.lastEventAt = Date.now();
    bucket.done = (sc.status === "success" || sc.status === "failed"
                   || sc.status === "partial" || sc.status === "skipped");
    // Items: replace with the sidecar's items[] (sidecars are full snapshots).
    const items = Array.isArray(sc.items) ? sc.items : [];
    bucket.items = new Map();
    bucket.itemOrder = [];
    items.forEach((it, idx) => {
      const k = packageKey(it, idx);
      bucket.items.set(k, it);
      bucket.itemOrder.push(k);
    });
    // Diagnostics: rebuild from sidecar messages[].
    const msgs = Array.isArray(sc.messages) ? sc.messages : [];
    bucket.messages = msgs.map(m => ({
      level: (m.level || "info").toLowerCase(),
      text:  m.text || m.message || "",
      time:  m.time || m.timestamp || sc.finished_at || sc.started_at || null,
    }));
    // Auto-focus newly arrived bucket if no active or the active is done.
    const cur = state.activeKey ? state.phaseSource.get(state.activeKey) : null;
    if (!cur || cur.done || !cur.lastEventAt) state.activeKey = key;
  }

  function ingestSidecarError(payload) {
    // Surface parse errors as diagnostics on the active bucket so the user
    // sees them; if no bucket exists yet, create a synthetic one.
    const phase = "?", source = "?";
    const { key, bucket } = ensureBucket(phase, source);
    bucket.messages.push({
      level: "error",
      text: "sidecar parse error: " + (payload.path || "?") + ": " + (payload.error || "?"),
      time: new Date().toISOString(),
    });
    if (!state.activeKey) state.activeKey = key;
  }

  function onStatus(_runId, m) {
    if (!m) return;
    if (m.status === "running" && !state.elapsedTimer) {
      state.elapsedTimer = setInterval(render, 1000);
    }
    if (m.status === "completed" || m.status === "failed") {
      state.finished = Date.now();
    }
    render();
  }

  function onSidecar(_runId, sc) {
    ingestSidecar(sc);
    render();
  }

  function onSidecarError(_runId, m) {
    ingestSidecarError(m || {});
    render();
  }

  function onDone(_runId, p) {
    state.finished = Date.now();
    if (state.elapsedTimer) {
      clearInterval(state.elapsedTimer);
      state.elapsedTimer = null;
    }
    if (p && Number.isFinite(p.duration_ms)) {
      state.started = state.finished - p.duration_ms;
    }
    render();
  }

  function reset(runId) {
    if (state.elapsedTimer) {
      clearInterval(state.elapsedTimer);
      state.elapsedTimer = null;
    }
    state = newState();
    state.runId = runId || null;
    state.started = Date.now();
    state.elapsedTimer = setInterval(render, 1000);
    // Optional ETA, degrades gracefully if endpoint is unwired.
    fetch("/telemetry/eta", { credentials: "omit" })
      .then(r => r.ok ? r.json() : null)
      .then(j => {
        if (j && Number.isFinite(j.eta_seconds)) {
          state.etaSeconds = j.eta_seconds;
          render();
        }
      })
      .catch(() => { /* silent — endpoint optional */ });
    render();
  }

  // ---- Render ----------------------------------------------------------
  function render() {
    const panel = $("#run-detail-panel");
    if (!panel) return;
    const buckets = state.phaseSource;
    if (buckets.size === 0 && !state.runId) {
      panel.classList.add("hidden");
      return;
    }
    panel.classList.remove("hidden");

    renderElapsed();
    renderNav();
    renderActiveBucket();
  }

  function renderElapsed() {
    const elapsedEl = $("#run-detail-elapsed");
    if (!elapsedEl) return;
    const end = state.finished || Date.now();
    const ms = Math.max(0, end - state.started);
    const label = trKey("run.detail.elapsed", "elapsed");
    elapsedEl.textContent = label + " " + fmtElapsed(ms);
  }

  function renderNav() {
    const nav = $("#run-detail-nav");
    if (!nav) return;
    clearChildren(nav);
    // Render in canonical phase order, then by source.
    const phaseOrder = ["check", "plan", "apply", "verify", "cleanup"];
    const keys = Array.from(state.phaseSource.keys()).sort((a, b) => {
      const ba = state.phaseSource.get(a), bb = state.phaseSource.get(b);
      const pa = phaseOrder.indexOf(ba.phase), pb = phaseOrder.indexOf(bb.phase);
      const ia = pa < 0 ? phaseOrder.length : pa;
      const ib = pb < 0 ? phaseOrder.length : pb;
      if (ia !== ib) return ia - ib;
      return (ba.source || "").localeCompare(bb.source || "");
    });
    keys.forEach(key => {
      const b = state.phaseSource.get(key);
      const tab = document.createElement("button");
      tab.type = "button";
      tab.className = "run-detail-tab";
      tab.dataset.phase = b.phase;
      tab.dataset.source = b.source;
      tab.dataset.status = statusToCls(b.status);
      tab.setAttribute("aria-selected", state.activeKey === key ? "true" : "false");
      const dot = document.createElement("span");
      dot.className = "run-detail-tab-dot";
      tab.appendChild(dot);
      const lbl = document.createElement("span");
      lbl.className = "run-detail-tab-label";
      lbl.textContent = b.phase + " · " + b.source;
      tab.appendChild(lbl);
      tab.addEventListener("click", () => setActive(key));
      nav.appendChild(tab);
    });
  }

  function renderActiveBucket() {
    const b = state.activeKey ? state.phaseSource.get(state.activeKey) : null;
    renderBar(b);
    renderPackages(b);
    renderDiagnostics(b);
  }

  function renderBar(bucket) {
    const fill = $("#run-detail-bar-fill");
    const meta = $("#run-detail-bar-meta");
    const wrap = $("#run-detail-bar-wrap");
    if (!fill || !meta || !wrap) return;
    fill.classList.remove("is-warn", "is-err");
    if (!bucket) {
      fill.style.width = "0%";
      wrap.setAttribute("aria-valuenow", "0");
      clearChildren(meta);
      return;
    }
    const items = bucket.itemOrder.map(k => bucket.items.get(k)).filter(Boolean);
    const total = items.length;
    let processed = 0;
    let failed = 0;
    items.forEach(it => {
      const s = (it && it.status) || "";
      if (s && s !== "planned" && s !== "running") processed++;
      if (s === "failed" || s === "error") failed++;
    });
    const pct = total > 0 ? Math.round((processed / total) * 100)
                          : (bucket.done ? 100 : 0);
    fill.style.width = pct + "%";
    if (statusToCls(bucket.status) === "failed") fill.classList.add("is-err");
    else if (statusToCls(bucket.status) === "partial") fill.classList.add("is-warn");
    wrap.setAttribute("aria-valuenow", String(pct));

    clearChildren(meta);
    const left = document.createElement("span");
    left.className = "run-detail-meta-left";
    const phaseB = document.createElement("b");
    phaseB.textContent = bucket.phase;
    left.appendChild(phaseB);
    left.appendChild(document.createTextNode(" · " + bucket.source));
    meta.appendChild(left);

    const middle = document.createElement("span");
    middle.className = "run-detail-meta-middle";
    middle.textContent = pct + "%  ·  " + processed + " "
      + trKey("run.detail.of", "of") + " " + total + " "
      + trKey("run.detail.items_total", "items")
      + (failed > 0 ? "  ·  " + failed + " " + trKey("run.detail.items_failed", "failed") : "");
    meta.appendChild(middle);

    const right = document.createElement("span");
    right.className = "run-detail-meta-eta";
    if (Number.isFinite(state.etaSeconds) && state.etaSeconds > 0 && !bucket.done) {
      right.textContent = trKey("run.detail.eta", "ETA") + " " + fmtElapsed(state.etaSeconds * 1000);
    }
    meta.appendChild(right);
  }

  function renderPackages(bucket) {
    const list = $("#run-detail-packages-list");
    const empty = $("#run-detail-packages-empty");
    if (!list || !empty) return;
    clearChildren(list);
    const items = bucket ? bucket.itemOrder.map(k => bucket.items.get(k)).filter(Boolean) : [];
    if (!bucket) {
      empty.classList.remove("hidden");
      empty.textContent = trKey("run.detail.packages_empty",
                                "Waiting for the first sidecar to land.");
      return;
    }
    if (items.length === 0) {
      empty.classList.remove("hidden");
      empty.textContent = trKey("run.detail.no_packages",
                                "This phase reported no packages.");
      return;
    }
    empty.classList.add("hidden");
    items.forEach((it, idx) => list.appendChild(buildPackageRow(it, idx, bucket)));
  }

  function buildPackageRow(item, idx, bucket) {
    const row = document.createElement("li");
    const det = document.createElement("details");
    det.className = "run-detail-pkg";
    det.dataset.itemKey = packageKey(item, idx);

    const sum = document.createElement("summary");
    sum.className = "run-detail-pkg-summary";

    const name = document.createElement("span");
    name.className = "run-detail-pkg-name";
    name.title = item.name || item.id || "";
    name.textContent = item.name || item.id || "—";
    sum.appendChild(name);

    const installed = document.createElement("span");
    installed.className = "run-detail-pkg-version";
    const installedLabel = document.createElement("b");
    installedLabel.textContent = item.installed || item.current_version
                                 || trKey("run.detail.unknown_version", "—");
    installed.appendChild(installedLabel);
    sum.appendChild(installed);

    const candidate = document.createElement("span");
    candidate.className = "run-detail-pkg-version";
    const arrow = document.createElement("span");
    arrow.className = "run-detail-arrow";
    arrow.textContent = "→ ";
    candidate.appendChild(arrow);
    const candB = document.createElement("b");
    candB.textContent = item.candidate || item.target_version || item.resolved_version
                        || trKey("run.detail.unknown_version", "—");
    candidate.appendChild(candB);
    sum.appendChild(candidate);

    const pill = document.createElement("span");
    const status = item.status || "unknown";
    pill.className = pillClassFor(status) + " run-detail-pkg-status";
    pill.textContent = statusLabel(status);
    sum.appendChild(pill);

    // Per-package mini progress bar (apply phase or anything claiming
    // running). For sources that don't stream per-package progress,
    // we render an indeterminate strobe.
    if (bucket && bucket.phase === "apply") {
      const progress = document.createElement("span");
      progress.className = "run-detail-pkg-progress";
      const pf = document.createElement("span");
      pf.className = "run-detail-pkg-progress-fill";
      progress.appendChild(pf);
      const pct = (item && Number.isFinite(item.progress_pct)) ? item.progress_pct : null;
      if (pct !== null) {
        pf.style.width = Math.max(0, Math.min(100, pct)) + "%";
        progress.classList.add("is-running");
      } else if (status === "running" || status === "planned") {
        progress.classList.add("is-indeterminate");
      } else if (status === "success" || status === "up_to_date") {
        pf.style.width = "100%";
      } else if (status === "failed") {
        pf.style.width = "100%";
        pf.style.background = "var(--err)";
      }
      sum.appendChild(progress);
    }

    det.appendChild(sum);
    det.appendChild(buildPackageDetail(item));
    row.appendChild(det);
    return row;
  }

  function buildPackageDetail(item) {
    const wrap = document.createElement("div");
    wrap.className = "run-detail-pkg-detail";

    const dl = document.createElement("dl");
    dl.className = "run-detail-pkg-meta-row";
    function dtdd(label, value) {
      if (value === undefined || value === null || value === "") return;
      const dt = document.createElement("dt"); dt.textContent = label;
      const dd = document.createElement("dd"); dd.textContent = String(value);
      dl.appendChild(dt); dl.appendChild(dd);
    }
    dtdd(trKey("run.detail.installed", "Installed"),
         item.installed || item.current_version);
    dtdd(trKey("run.detail.candidate", "Candidate"),
         item.candidate || item.target_version);
    dtdd(trKey("run.detail.resolved", "Resolved"), item.resolved_version);
    dtdd(trKey("run.detail.source",   "Source"),
         (item.source && (item.source.type || item.source.feed)) || item.source_type);
    if (item.evidence && typeof item.evidence === "object") {
      const evParts = [];
      Object.keys(item.evidence).forEach(k => {
        if (item.evidence[k]) evParts.push(k + "=" + item.evidence[k]);
      });
      if (evParts.length) dtdd(trKey("run.detail.evidence", "Evidence"), evParts.join("  "));
    }
    if (Number.isFinite(item.exit_code)) dtdd("exit", item.exit_code);
    wrap.appendChild(dl);

    const messages = Array.isArray(item.messages) ? item.messages : [];
    const ul = document.createElement("ul");
    ul.className = "run-detail-pkg-msg";
    if (messages.length === 0) {
      const li = document.createElement("li");
      li.className = "lvl-info";
      li.textContent = trKey("run.detail.no_messages",
                             "No item-level messages.");
      ul.appendChild(li);
    } else {
      messages.forEach(m => {
        const li = document.createElement("li");
        const lvl = (m.level || "info").toLowerCase();
        li.className = "lvl-" + (lvl === "warning" ? "warn" : lvl);
        li.textContent = "[" + lvl.toUpperCase() + "] " + (m.text || m.message || "");
        ul.appendChild(li);
      });
    }
    wrap.appendChild(ul);
    return wrap;
  }

  function renderDiagnostics(bucket) {
    const list = $("#run-detail-diagnostics-list");
    if (!list) return;
    // Track userScrolledDiagnostics so auto-scroll only kicks when the user
    // has the toggle on AND hasn't scrolled away.
    if (!list._scrollHookInstalled) {
      list._scrollHookInstalled = true;
      list.addEventListener("scroll", () => {
        const nearBottom = (list.scrollTop + list.clientHeight) >= (list.scrollHeight - 4);
        state.userScrolledDiagnostics = !nearBottom;
      });
    }
    clearChildren(list);
    const messages = bucket ? bucket.messages : [];
    if (messages.length === 0) {
      const li = document.createElement("li");
      li.className = "run-detail-diag lvl-info";
      const time = document.createElement("span");
      time.className = "run-detail-diag-time"; time.textContent = "·";
      const lvl = document.createElement("span");
      lvl.className = "run-detail-diag-level"; lvl.textContent = "info";
      const text = document.createElement("span");
      text.className = "run-detail-diag-text";
      text.textContent = trKey("run.detail.no_diagnostics", "No diagnostics yet.");
      li.appendChild(time); li.appendChild(lvl); li.appendChild(text);
      list.appendChild(li);
      return;
    }
    // Chronological order (assume sidecar order is chronological).
    messages.forEach(m => {
      const li = document.createElement("li");
      const lvl = (m.level || "info").toLowerCase();
      const lvlNorm = (lvl === "warning") ? "warn" : (lvl === "err" ? "error" : lvl);
      li.className = "run-detail-diag lvl-" + lvlNorm;
      const time = document.createElement("span");
      time.className = "run-detail-diag-time";
      time.textContent = formatDiagTime(m.time);
      const lvlEl = document.createElement("span");
      lvlEl.className = "run-detail-diag-level";
      lvlEl.textContent = lvlNorm;
      const text = document.createElement("span");
      text.className = "run-detail-diag-text";
      text.textContent = m.text || "";
      li.appendChild(time); li.appendChild(lvlEl); li.appendChild(text);
      list.appendChild(li);
    });
    const auto = $("#run-detail-autoscroll");
    if (auto && auto.checked && !state.userScrolledDiagnostics) {
      list.scrollTop = list.scrollHeight;
    }
  }
  function formatDiagTime(t) {
    if (!t) return "·";
    try {
      const d = new Date(t);
      if (Number.isNaN(d.getTime())) return "·";
      const hh = String(d.getHours()).padStart(2, "0");
      const mm = String(d.getMinutes()).padStart(2, "0");
      const ss = String(d.getSeconds()).padStart(2, "0");
      return hh + ":" + mm + ":" + ss;
    } catch { return "·"; }
  }

  // ---- Public API exposed via window.runDetail ------------------------
  window.runDetail = {
    reset: reset,
    onStatus: onStatus,
    onSidecar: onSidecar,
    onSidecarError: onSidecarError,
    onDone: onDone,
    // Test/debug accessor (do not rely on the shape externally).
    _state: function () { return state; },
  };

  // Initial render once DOM is ready (panel just stays hidden until a run).
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", render, { once: true });
  } else {
    render();
  }
})();

/* ============================================================================
 * AI Tools tab (Sesja 70 Phase B). Drives the chat thread + library +
 * conversations rail inside #view-suggest. Coexists with the legacy Sesja 67
 * rule-based suggestions cards rendered below.
 * ========================================================================== */
(function () {
  const $ait = (id) => document.getElementById(id);
  const t = (k, fallback) => (window.tr && window.tr(k)) || (fallback || k);
  const locale = () => (window.UI_LANG === "pl" ? "pl" : "en");

  function escapeHtml(s) {
    return String(s).replace(/[&<>]/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;",
    }[c]));
  }

  // Cheap markdown: escape, then ` **bold** ` and ` *italic* ` and code spans.
  // Real implementation can swap in a tiny parser later; this is enough for
  // first-token render.
  function renderMarkdown(text) {
    const esc = escapeHtml(text);
    return esc
      .replace(/\*\*([^*]+?)\*\*/g, "<strong>$1</strong>")
      .replace(/`([^`]+?)`/g, "<code>$1</code>")
      .replace(/\n/g, "<br>");
  }

  const aitools = {
    state: {
      conversationId: null,
      pendingTurnId: null,
      pendingMsgEl: null,
      sse: null,
      initialized: false,
      backends: [],
    },

    async init() {
      if (this.state.initialized) return;
      this.state.initialized = true;
      this._wireDom();
      await Promise.all([
        this.loadConversations(),
        this.loadLibrary(),
        this.loadBackends(),
      ]);
    },

    _wireDom() {
      const send = $ait("aitools-send");
      const input = $ait("aitools-input");
      const newBtn = $ait("aitools-new-chat");
      if (newBtn) newBtn.addEventListener("click", () => this.newConversation());
      if (send) send.addEventListener("click", () => this._sendFromInput());
      if (input) {
        input.addEventListener("keydown", (e) => {
          if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
            e.preventDefault();
            this._sendFromInput();
          }
        });
      }
    },

    async loadConversations() {
      try {
        const r = await fetch("/ai/chat/conversations");
        const j = await r.json();
        this.renderConversations(j.conversations || []);
      } catch (e) {
        console.warn("aitools.loadConversations failed", e);
      }
    },

    renderConversations(list) {
      const root = $ait("aitools-conv-list");
      if (!root) return;
      while (root.firstChild) root.removeChild(root.firstChild);
      if (!list.length) {
        const p = document.createElement("p");
        p.className = "dim";
        p.textContent = t("aitools.empty_conversations");
        root.appendChild(p);
        return;
      }
      list.forEach((c) => {
        const item = document.createElement("div");
        item.className = "aitools-conv-item";
        if (c.id === this.state.conversationId) item.classList.add("active");
        item.textContent = c.title || "Untitled";
        item.title = c.title || "Untitled";
        item.addEventListener("click", () => this.openConversation(c.id));
        root.appendChild(item);
      });
    },

    async loadLibrary() {
      try {
        const r = await fetch("/ai/chat/library");
        const j = await r.json();
        this.renderLibrary(j.entries || []);
      } catch (e) {
        console.warn("aitools.loadLibrary failed", e);
      }
    },

    renderLibrary(entries) {
      const root = $ait("aitools-library-list");
      if (!root) return;
      while (root.firstChild) root.removeChild(root.firstChild);

      const groups = {};
      entries.forEach((e) => {
        const g = e.group || "misc";
        (groups[g] = groups[g] || []).push(e);
      });
      Object.entries(groups).forEach(([g, list]) => {
        const h = document.createElement("h4");
        h.textContent = t(`aitools.group.${g}`, g);
        root.appendChild(h);
        list.forEach((entry) => {
          const btn = document.createElement("button");
          btn.className = "aitools-prompt";
          const lang = locale();
          btn.textContent = entry.title?.[lang] || entry.title?.en || entry.id;
          btn.addEventListener("click", () => {
            const starter =
              entry.starter_prompt?.[lang] || entry.starter_prompt?.en || "";
            if (starter) this.send(starter, entry.id, entry.context_tags || []);
          });
          root.appendChild(btn);
        });
      });
    },

    async loadBackends() {
      const pill = $ait("aitools-backend");
      try {
        const r = await fetch("/ai/chat/backends");
        const j = await r.json();
        const list = j.backends || [];
        this.state.backends = list;
        const available = list.filter((b) => b.available === "true" || b.available === true);
        if (pill) {
          if (available.length) {
            pill.textContent = `${t("aitools.backend_label", "Backend")}: ${available[0].name}`;
          } else {
            pill.textContent = t("aitools.no_backend");
          }
        }
      } catch (e) {
        if (pill) pill.textContent = t("aitools.no_backend");
      }
    },

    async newConversation() {
      const r = await fetch("/ai/chat/conversations", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
      const j = await r.json();
      this.state.conversationId = j.id;
      this._clearThread();
      await this.loadConversations();
      const input = $ait("aitools-input");
      if (input) input.focus();
    },

    async openConversation(id) {
      this.state.conversationId = id;
      try {
        const r = await fetch(`/ai/chat/conversations/${encodeURIComponent(id)}`);
        if (!r.ok) return;
        const j = await r.json();
        this._clearThread();
        (j.messages || []).forEach((m) => {
          let actions = null;
          if (m.actions) {
            try { actions = JSON.parse(m.actions); } catch {}
          }
          this.appendMessage(m.role, m.content, actions);
        });
      } catch (e) {
        console.warn("openConversation failed", e);
      }
      await this.loadConversations();
    },

    _clearThread() {
      const thread = $ait("aitools-thread");
      if (!thread) return;
      while (thread.firstChild) thread.removeChild(thread.firstChild);
    },

    appendMessage(role, content, actions) {
      const thread = $ait("aitools-thread");
      if (!thread) return null;
      // Strip the empty-state placeholder on first real message.
      const empty = thread.querySelector(".aitools-empty");
      if (empty) empty.remove();

      const div = document.createElement("div");
      div.className = `aitools-msg aitools-msg-${role}`;

      const roleLine = document.createElement("div");
      roleLine.className = "aitools-msg-role";
      roleLine.textContent = t(`aitools.role.${role}`, role);
      div.appendChild(roleLine);

      const body = document.createElement("div");
      body.className = "aitools-msg-body";
      body.innerHTML = renderMarkdown(content || "");
      div.appendChild(body);

      if (actions && actions.length) {
        const chips = document.createElement("div");
        chips.className = "aitools-chips";
        actions.forEach((a) => chips.appendChild(this._makeChip(a)));
        div.appendChild(chips);
      }

      thread.appendChild(div);
      thread.scrollTop = thread.scrollHeight;
      return div;
    },

    _makeChip(action) {
      const btn = document.createElement("button");
      const risk = action.risk || "low";
      btn.className = `aitools-chip aitools-chip-${risk}`;
      const lang = locale();
      const label =
        action[`label_${lang}`] || action.label_en || action.id || "action";
      btn.textContent = label;
      btn.addEventListener("click", () => this.executeAction(action));
      return btn;
    },

    async executeAction(action) {
      const lang = locale();
      const label =
        action[`label_${lang}`] || action.label_en || action.id || "action";
      const risk = action.risk || "low";
      if (risk === "medium" || risk === "high") {
        const tpl = t("aitools.confirm_action", "Run action: {label}?");
        const msg = tpl.replace("{label}", label);
        if (!window.confirm(msg)) return;
      }
      try {
        const r = await fetch("/ai/chat/action", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            action_id: action.id,
            body: action.body || {},
          }),
        });
        const j = await r.json();
        if (r.ok && j.ok) {
          this.appendMessage(
            "system",
            `${label} → ${j.verb} ${j.path}`,
          );
        } else {
          this.appendMessage(
            "system",
            `${label} → error: ${j.detail || j.error || r.status}`,
          );
        }
      } catch (e) {
        this.appendMessage("system", `${label} → error: ${e.message || e}`);
      }
    },

    _sendFromInput() {
      const input = $ait("aitools-input");
      if (!input) return;
      const text = (input.value || "").trim();
      if (!text) return;
      input.value = "";
      this.send(text);
    },

    async send(text, templateId, contextTags) {
      if (!this.state.conversationId) {
        await this.newConversation();
      }
      this.appendMessage("user", text);
      try {
        const r = await fetch("/ai/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            conversation_id: this.state.conversationId,
            message: text,
            template_id: templateId || null,
            context_tags: contextTags || null,
            locale: locale(),
          }),
        });
        if (!r.ok) {
          this.appendMessage("system", t("aitools.error_send"));
          return;
        }
        const j = await r.json();
        this.state.pendingTurnId = j.turn_id;
        this._streamTurn(j.stream_url);
      } catch (e) {
        this.appendMessage("system", t("aitools.error_send"));
      }
    },

    _streamTurn(url) {
      if (this.state.sse) {
        try { this.state.sse.close(); } catch {}
      }
      const pending = this.appendMessage("assistant", "");
      if (pending) pending.classList.add("aitools-msg-pending");
      this.state.pendingMsgEl = pending;
      let buf = "";

      const sse = new EventSource(url);
      this.state.sse = sse;

      sse.addEventListener("token", (e) => {
        try {
          const data = JSON.parse(e.data);
          if (data.content) {
            buf += data.content;
            if (pending) {
              const body = pending.querySelector(".aitools-msg-body");
              if (body) body.innerHTML = renderMarkdown(buf);
            }
            const thread = $ait("aitools-thread");
            if (thread) thread.scrollTop = thread.scrollHeight;
          }
        } catch {}
      });

      sse.addEventListener("done", () => {
        if (pending) pending.classList.remove("aitools-msg-pending");
        sse.close();
        this.state.sse = null;
        this.state.pendingTurnId = null;
        // Refresh conversation list to pick up auto-title.
        this.loadConversations();
        // Re-open the active conversation so action chips render
        // (they live on the persisted assistant message).
        if (this.state.conversationId) {
          this.openConversation(this.state.conversationId);
        }
      });

      sse.addEventListener("error", () => {
        sse.close();
        this.state.sse = null;
        if (pending && pending.parentNode) {
          pending.classList.remove("aitools-msg-pending");
        }
      });
    },
  };

  window.aitools = aitools;
})();
