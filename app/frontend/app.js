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
  async refreshIndicator() {
    try {
      const s = await api.get("/sudo/status");
      const ind = $("#sudo-indicator");
      // DOM-construction (not innerHTML interpolation) so a malicious
      // i18n value can never inject markup. Strings flow through textContent.
      ind.textContent = "";
      const span = document.createElement("span");
      span.className = "badge " + (s.cached ? "ok" : "warn");
      span.textContent = s.cached
        ? ((window.tr && window.tr("sudo.cached")) || "Administrator authorized")
        : ((window.tr && window.tr("sudo.not_cached")) || "Administrator not authorized");
      ind.appendChild(span);
    } catch {}
  },
  async ensure() {
    const s = await api.get("/sudo/status");
    if (s.cached) return true;
    const prompt = (window.tr && window.tr("sudo.empty_prompt"))
      || "Administrator credentials needed — enter your password to authenticate.";
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
    if (view === "apps"       && !ui._loaded.apps)       { ui._loaded.apps = true;       ui.loadApps(); }
    if (view === "suggest"    && !ui._loaded.suggest)    { ui._loaded.suggest = true;    ui.loadSuggestions(); }
    if (view === "logs"       && !ui._loaded.logs)       { ui._loaded.logs = true;       ui.loadLogsList(); }
    if (view === "about"      && !ui._loaded.about)      { ui._loaded.about = true;      ui.loadAbout(); }
    // Run Center is special: must always (re)bind active-stream subscription.
    if (view === "run") ui.loadRunCenter();
  },
  invalidateCaches() {
    // Called after a run completes or when the user hits "Refresh".
    ui._loaded = {};
    window.INV_SUMMARY = null;
  },

  async maybeShowWizard() {
    try {
      const s = await api.get("/onboarding/state");
      if (s.onboarded) return;
      // Initialise the step-router state machine before showing the modal.
      ui.wizard.start();
    } catch {}
  },

  // Step-router for the 6-step Windows first-run wizard.
  // Self-contained: every step is a function that builds DOM into
  // #wizard-step-host. Persists choices to localStorage as the user
  // moves so a refresh mid-wizard doesn't lose work; final POST to
  // /onboarding/complete writes the durable state file.
  wizard: {
    state: null,
    steps: ["welcome", "prefs", "admin", "scan", "sources", "done"],
    currentIdx: 0,
    start() {
      this.state = {
        language: window.UI_LANG || "en",
        theme:    "dark",
        admin_authorised: false,
        inventory_seen: false,
        dry_run_seen: false,
        sources_summary: null,
        wu_check: null,         // {status: "idle"|"running"|"done"|"failed", count?, error?}
        dry_run: null,          // {status, items, error?}
      };
      // Pull any mid-wizard scratch from a previous reload so progress
      // survives. Keys are namespaced under 'wizard:'.
      try {
        const saved = localStorage.getItem("wizard:state");
        if (saved) Object.assign(this.state, JSON.parse(saved));
      } catch {}
      try {
        const lsTheme = localStorage.getItem("ascendo_theme");
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
      // Welcome step uses its own internal CTA so hide footer Next.
      // Done step swaps Next for Finish, gated on dry_run_seen.
      if (id === "welcome") {
        nextBtn.style.display = "none";
      } else if (id === "done") {
        nextBtn.style.display = "";
        nextBtn.textContent = tr("wizard.done.finish_btn");
        nextBtn.disabled = !this.state.dry_run_seen;
        nextBtn.dataset.role = "finish";
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
      tag.textContent = tr("wizard.welcome.tagline");
      tx.appendChild(h); tx.appendChild(tag);
      hero.appendChild(tx);
      host.appendChild(hero);
      const body = document.createElement("p");
      body.textContent = tr("wizard.welcome.body");
      host.appendChild(body);
      const preview = document.createElement("div");
      preview.className = "wizard-welcome-preview";
      const ph = document.createElement("h4");
      ph.textContent = tr("wizard.welcome.preview");
      preview.appendChild(ph);
      const ol = document.createElement("ol");
      ["bullet1","bullet2","bullet3","bullet4","bullet5"].forEach(k => {
        const li = document.createElement("li");
        li.textContent = tr(`wizard.welcome.${k}`);
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
            localStorage.setItem("ascendo_theme", val);
            window.applyThemePref(val);
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
    // ── Step 3: Administrator (UAC) ─────────────────────────────
    build_admin(host) {
      const h = document.createElement("h3");
      h.textContent = tr("wizard.admin.title");
      host.appendChild(h);
      const body = document.createElement("p");
      body.textContent = tr("wizard.admin.body");
      host.appendChild(body);
      ["why", "do"].forEach(k => {
        const co = document.createElement("div");
        co.className = "wizard-admin-callout";
        const t = document.createElement("h4");
        t.textContent = tr(`wizard.admin.${k}_h`);
        const b = document.createElement("p");
        b.textContent = tr(`wizard.admin.${k}_b`);
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
      body.textContent = tr("wizard.scan.body");
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
      const sources = [
        ["winget",         "wizard.scan.scanning_winget"],
        ["msstore",        "wizard.scan.scanning_msstore"],
        ["registry_arp",   "wizard.scan.scanning_arp"],
        ["windows_update", "wizard.scan.scanning_wu"],
      ];
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
          lblEl.textContent = tr(sources[idx][1]);
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
        sources: Object.keys(summary.categories || {}).length || 4,
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
      body.textContent = tr("wizard.sources.body");
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
      // Action column for Windows Update "Run check".
      const thAct = document.createElement("th");
      thAct.textContent = "";
      trh.appendChild(thAct);
      thead.appendChild(trh);
      tbl.appendChild(thead);
      const tb = document.createElement("tbody");
      const rows = [
        ["winget",         "wizard.sources.winget_desc"],
        ["msstore",        "wizard.sources.msstore_desc"],
        ["registry_arp",   "wizard.sources.arp_desc"],
        ["windows_update", "wizard.sources.wu_desc"],
      ];
      let outdatedTotal = 0;
      for (const [src, descKey] of rows) {
        const cat = (summary.categories || {})[src] || {total: 0, outdated: 0};
        const trr = document.createElement("tr");
        const tdSrc = document.createElement("td");
        tdSrc.className = "src-name";
        tdSrc.textContent = src;
        trr.appendChild(tdSrc);
        const tdDesc = document.createElement("td");
        tdDesc.className = "src-desc";
        tdDesc.textContent = tr(descKey);
        trr.appendChild(tdDesc);
        const tdTotal = document.createElement("td");
        tdTotal.className = "num";
        tdTotal.textContent = cat.total || 0;
        trr.appendChild(tdTotal);
        const tdOut = document.createElement("td");
        tdOut.className = "num";
        if (src === "windows_update" && (!cat || cat.total === 0)) {
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
        if (src === "windows_update") {
          const btn = document.createElement("button");
          btn.type = "button";
          btn.className = "secondary";
          btn.style.fontSize = "0.78rem";
          btn.textContent = tr("wizard.sources.run_check");
          if (this.state.wu_check && this.state.wu_check.status === "running") {
            btn.disabled = true;
            btn.textContent = "…";
          }
          btn.addEventListener("click", () => this.runWindowsUpdateCheck());
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
      // Surface the live wu_check status under the table.
      if (this.state.wu_check) {
        const note = document.createElement("p");
        note.className = "dim";
        note.style.fontSize = "0.82rem";
        if (this.state.wu_check.status === "running") {
          note.textContent = tr("wizard.sources.wu_running");
        } else if (this.state.wu_check.status === "done") {
          note.textContent = tr("wizard.sources.wu_done")
            .replace("{count}", String(this.state.wu_check.count || 0));
        } else if (this.state.wu_check.status === "failed") {
          note.textContent = tr("wizard.sources.wu_failed");
        }
        if (note.textContent) host.appendChild(note);
      }
    },
    async runWindowsUpdateCheck() {
      this.state.wu_check = {status: "running"};
      this.render();
      try {
        const r = await api.post("/runs/async", {
          profile: "quick",
          phases: ["check"],
          categories: ["windows_update"],
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
      // A — dry-run
      const dryS = document.createElement("div");
      dryS.className = "wizard-done-section";
      const dryH = document.createElement("h4");
      dryH.textContent = tr("wizard.done.dry_h");
      dryS.appendChild(dryH);
      const dryP = document.createElement("p");
      dryP.textContent = tr("wizard.done.dry_body");
      dryS.appendChild(dryP);
      const row = document.createElement("div");
      row.className = "wizard-dryrun-row";
      const runBtn = document.createElement("button");
      runBtn.type = "button";
      runBtn.id = "wizard-dryrun-btn";
      runBtn.textContent = tr("wizard.done.dry_btn");
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
        dryStatus.textContent = tr("wizard.done.dry_done")
          .replace("{items}", String(this.state.dry_run.items || 0));
      } else if (this.state.dry_run && this.state.dry_run.status === "running") {
        dryStatus.textContent = tr("wizard.done.dry_running");
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
      // B — apply for real
      const appS = document.createElement("div");
      appS.className = "wizard-done-section";
      const appH = document.createElement("h4");
      appH.textContent = tr("wizard.done.apply_h");
      appS.appendChild(appH);
      const ul = document.createElement("ul");
      ["apply_1","apply_2","apply_3"].forEach(k => {
        const li = document.createElement("li");
        li.textContent = tr(`wizard.done.${k}`);
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
      if (statusEl) statusEl.textContent = tr("wizard.done.dry_running");
      try {
        const r = await api.post("/runs/async", {
          profile: "quick",
          phases: ["plan"],
          categories: ["winget"],
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
    wrap.innerHTML = `<span class="spinner"></span> ${tr("overview.scanning")}`;
    try {
      const items = (await api.get("/suggestions")).items || [];
      if (!items.length) {
        wrap.innerHTML = `<p class="dim">${tr("suggest.empty")}</p>`;
      } else {
        wrap.innerHTML = items.map(s => {
          const conf = s.confidence === "high" ? "confidence-high" :
                       s.confidence === "med"  ? "confidence-med" : "confidence-low";
          const confLbl = s.confidence === "high" ? tr("suggest.conf_high")
                        : s.confidence === "med"  ? tr("suggest.conf_med")
                        :                            tr("suggest.conf_low");
          const diffStr = (s.diff||[]).map(d => {
            const adds = (d.add||[]).map(L => `<span class="add">+ ${L}</span>`).join("\n");
            const dels = (d.remove||[]).map(L => `<span class="del">- ${L}</span>`).join("\n");
            return `${d.file}\n${[adds, dels].filter(Boolean).join("\n")}`;
          }).join("\n\n");
          return `
            <div class="suggestion ${conf}" data-sid="${s.id}">
              <h4>${s.title}</h4>
              <div class="meta">${confLbl} · ${s.category} · source: ${s.source}</div>
              <p>${s.rationale}</p>
              ${diffStr ? `<pre class="diff">${diffStr}</pre>` : ""}
              <div class="actions">
                ${(s.diff||[]).length ? `<button data-sg-apply='${JSON.stringify({id:s.id,diff:s.diff})}'>${tr("suggest.btn_apply")}</button>` : ""}
                <button class="secondary" data-sg-dismiss="${s.id}">${tr("suggest.btn_dismiss")}</button>
              </div>
            </div>`;
        }).join("");
      }
      // Load AI form values
      const s = await api.get("/settings");
      const ai = s.ai || {};
      const f = $("#ai-form");
      if (f) {
        f.elements.ai_provider.value = ai.provider || "";
        f.elements.ai_api_key.value  = ai.api_key  || "";
        f.elements.ai_model.value    = ai.model    || "";
      }
    } catch (e) {
      wrap.innerHTML = `<p class="badge fail">${e}</p>`;
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

  async loadOverview() {
    try {
      const h = await api.get("/health");
      $("#hostbadge").textContent = h.repo_root || "";
    } catch {}
    try {
      const runs = (await api.get("/runs?limit=1")).runs;
      const last = runs[0];
      $("#last-run").innerHTML = last
        ? `${ui.badge(last.status)} <code>${last.id}</code><br>
           <span class="dim">${ui.fmtTime(last.started_at)} → ${ui.fmtTime(last.ended_at)}</span><br>
           profile: ${last.profile || "-"}, dry-run: ${last.dry_run ? "yes" : "no"}<br>
           ${last.needs_reboot ? `<b>${tr("overview.reboot_required")}</b>` : ""}`
        : `<span class='dim'>${tr("overview.no_runs")}</span>`;
    } catch (e) { $("#last-run").textContent = String(e); }
    try {
      const p = await api.get("/preflight");
      $("#preflight").innerHTML = `${p.needs_reboot ? `<b>${tr("overview.reboot_pending")}</b><br>` : ""}` +
        p.items.map(i => `<span class="badge ${i.present ? "ok" : "warn"}">${i.tool}</span>`).join(" ");
    } catch (e) { $("#preflight").textContent = String(e); }
    try {
      const g = await api.get("/git/status");
      $("#git-status").innerHTML = `branch <code>${g.branch}</code> ` +
        (g.dirty ? "<span class='badge warn'>dirty</span>" : "<span class='badge ok'>clean</span>") +
        ` <span class="dim">↑${g.ahead} ↓${g.behind}</span>`;
    } catch (e) { $("#git-status").textContent = String(e); }
    // Inventory charts (slow scan, runs after the rest paints)
    ui.loadInventoryDashboard();
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

  async loadApps() {
    // Default-include model (per resident operator's spec, 2026-05-03):
    // every detected app is in_config by default; user opts OUT per app
    // by toggling the "In config" checkbox (POST /apps/exclude). The
    // backend persists exclusions to %APPDATA%\Ascendo\excluded.json.
    const wrap = $("#apps-table-wrap");
    const summary = $("#apps-summary");
    if (!wrap) return;
    wrap.textContent = "";
    const spinner = document.createElement("span");
    spinner.className = "spinner";
    wrap.appendChild(spinner);
    wrap.appendChild(document.createTextNode(" " + (tr("overview.scanning") || "Scanning…")));
    if (summary) summary.textContent = "";

    try {
      const data = await api.get("/apps/detect");
      const apps = data.apps || [];
      const sum = data.summary || {total: 0, tracked: 0, excluded: 0, missing: 0};

      // Summary pills: total / tracked / excluded.
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

      // Sort: excluded first (so the user can see what they've opted
      // out of at the top), then by category, then alphabetical.
      apps.sort((a, b) =>
        ((a.in_config ? 1 : 0) - (b.in_config ? 1 : 0)) ||
        (a.category || "").localeCompare(b.category || "") ||
        (a.name || "").localeCompare(b.name || ""));

      const tbl = document.createElement("table");
      tbl.className = "tbl inv-table";
      const thead = document.createElement("thead");
      const trh = document.createElement("tr");
      [
        tr("categories.col_cat")     || "Category",
        tr("categories.col_pkg")     || "Package",
        tr("categories.col_inst")    || "Installed",
        tr("categories.col_cand")    || "Candidate",
        tr("categories.col_status")  || "Status",
        tr("apps.col_in_config")     || "In config",
      ].forEach(label => {
        const th = document.createElement("th");
        th.textContent = label;
        trh.appendChild(th);
      });
      thead.appendChild(trh);
      tbl.appendChild(thead);

      const tbody = document.createElement("tbody");
      const stCls = {ok: "st-ok", outdated: "st-warn", missing: "st-err", unknown: "st-skip"};

      if (!apps.length) {
        const trEmpty = document.createElement("tr");
        const td = document.createElement("td");
        td.colSpan = 6;
        td.className = "dim";
        td.textContent = tr("apps.empty") || "No apps detected. Run a check from Categories first.";
        trEmpty.appendChild(td);
        tbody.appendChild(trEmpty);
      } else {
        apps.forEach(it => {
          const trRow = document.createElement("tr");
          if (!it.in_config) trRow.classList.add("excluded");
          trRow.classList.add("status-" + (it.status || "ok"));
          const addCell = (text, cls) => {
            const td = document.createElement("td");
            if (cls) td.className = cls;
            td.textContent = text;
            trRow.appendChild(td);
          };
          addCell(it.category || "—", "dim");
          addCell(it.name || "—", "pkg-name");
          addCell(it.installed || "—", "mono");
          addCell(it.candidate || "—", "mono");
          // Status pill cell
          const tdStatus = document.createElement("td");
          const pill = document.createElement("span");
          pill.className = "st-pill " + (stCls[it.status] || "st-skip");
          pill.textContent = it.status || "ok";
          tdStatus.appendChild(pill);
          trRow.appendChild(tdStatus);
          // In-config toggle
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

          tbody.appendChild(trRow);
        });
      }
      tbl.appendChild(tbody);
      wrap.textContent = "";
      wrap.appendChild(tbl);
    } catch (e) {
      wrap.textContent = String(e);
    }
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

  async loadInventoryDashboard() {
    const spin = `<span class="spinner"></span> ${tr("overview.scanning")}`;
    $("#inv-donut").innerHTML  = spin;
    $("#inv-bars").innerHTML   = spin;
    $("#inv-updates").innerHTML = spin;
    try {
      const s = await api.get("/inventory/summary");
      window.INV_SUMMARY = s;
      ui.renderDonut("inv-donut", [
        { label: "ok",       value: s.totals.ok,       color: "var(--ok)" },
        { label: "outdated", value: s.totals.outdated, color: "var(--warn)" },
        { label: "missing",  value: s.totals.missing,  color: "var(--err)" },
      ]);
      ui.renderBars("inv-bars", s.categories);
    } catch (e) { $("#inv-donut").textContent = String(e); $("#inv-bars").textContent = ""; }
    try {
      const all = (await api.get("/inventory")).categories;
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

  async loadCategories() {
    const cats = (await api.get("/categories")).categories;
    const summary = window.INV_SUMMARY || (await api.get("/inventory/summary").catch(()=>({categories:{}})));
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
    $$("#cats-table .cat-row").forEach(row => {
      row.addEventListener("click", e => {
        if (e.target.tagName === "BUTTON") return;
        const cat = row.dataset.cat;
        const det = row.nextElementSibling;
        if (det.classList.contains("hidden")) {
          det.classList.remove("hidden"); row.classList.add("open");
          ui.loadCategoryDetail(cat);
        } else {
          det.classList.add("hidden"); row.classList.remove("open");
        }
      });
    });
    // Per-category phase buttons → start the run directly (with sudo for mutating).
    // B4: ``apply`` (and unscoped "run all") gates on the apply-confirm modal —
    // the user must type the literal word ``apply`` to proceed. ``check`` and
    // ``plan`` are non-mutating, so we mark them ``dry_run`` to avoid sudo.
    $$("#cats-table button[data-cat-run]").forEach(b => b.addEventListener("click", async e => {
      e.stopPropagation();
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
    }));
  },

  async loadCategoryDetail(cat) {
    const target = $("#cat-detail-" + cat);
    target.innerHTML = `<span class="spinner"></span> ${tr("overview.scanning")}`;
    try {
      const items = (await api.get(`/inventory/${encodeURIComponent(cat)}`)).items;
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
        opt.textContent = `${p.id} - ${p.description}`;
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
      tb.appendChild(tr);
    }
    $$("a[data-run]").forEach(a => a.addEventListener("click", e => {
      e.preventDefault();
      ui.show("logs");
      ui.loadRunDetail(a.dataset.run);
    }));
  },

  async loadRunDetail(runId) {
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
      $("#run-detail").innerHTML = html;
    } catch (e) {
      $("#run-detail").innerHTML = `<p class="badge fail">${e}</p>`;
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
          <td><b>${h.id}</b><br><span class="dim">${h.display_name}</span></td>
          <td colspan="5" class="dim">checking…</td>
          <td>${ui.badge("running")}</td>`;
        tb.appendChild(tr);
        api.get(`/hosts/${encodeURIComponent(h.id)}/preflight`).then(p => {
          const lastRun = p.last_run ? `${p.last_run.status || "?"} (${p.last_run.run_id || ""})` : "-";
          tr.innerHTML = `
            <td><b>${h.id}</b><br><span class="dim">${h.display_name}</span></td>
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

  async loadProfilesPanel() {
    const wrap = $("#profiles-list");
    if (!wrap) return;
    try {
      const r = await api.get("/profiles/templates");
      if (!r.items.length) {
        wrap.innerHTML = `<p class="dim">No templates in config/profiles/.</p>`;
        return;
      }
      wrap.innerHTML = r.items.map(t => `
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
    f.elements.default_profile.value = s.default_profile;
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
        `branch <code>${g.branch}</code> ` +
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
        $("#sync-cloud").innerHTML = `<span class="dim">${s.reason}</span>`;
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
    const log = $("#live-log");
    log.textContent = "";
    const prog = $("#run-progress");
    const fill = prog.querySelector(".run-progress-fill");
    const lbl  = prog.querySelector(".run-progress-label");
    const rec  = prog.querySelector(".run-progress-recent");
    rec.innerHTML = ""; lbl.innerHTML = ""; fill.style.width = "0%";
    prog.classList.add("hidden");
    const stripAnsi = s => s.replace(/\x1b\[[0-9;]*m/g, "");

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
    es.addEventListener("status", e => { try { const m=JSON.parse(e.data); ui.status(`run ${runId}: ${m.status}`); if(m.status==="running") ensureProgVisible(); } catch {} });
    es.addEventListener("sidecar", e => { try { const sc=JSON.parse(e.data); renderSidecar(sc); log.textContent += `[${sc.phase}:${sc.category}] ${sc.status}\n`; log.scrollTop=log.scrollHeight; } catch {} });
    es.addEventListener("sidecar_error", e => { try { const m=JSON.parse(e.data); log.textContent += `[sidecar parse error] ${m.path}: ${m.error}\n`; } catch {} });
    es.addEventListener("done", e => {
      let p = {}; try { p = JSON.parse(e.data); } catch {}
      const status = p.status || "completed";
      const ms = p.duration_ms;
      log.textContent += `\n[done - ${status}${ms ? ` in ${(ms/1000).toFixed(1)}s` : ""}]\n`;
      ui.status(`run ${runId} ${status}${ms ? ` (${(ms/1000).toFixed(1)}s)` : ""}`);
      const stopBtn = $("#stop-btn"); if (stopBtn) stopBtn.disabled = true;
      fill.style.width = "100%"; es.close();
      ui.invalidateCaches(); ui.checkRebootBanner(); ui.loadHealth();
    });
    es.addEventListener("log", e => { try { const m=JSON.parse(e.data); const ln=m.line||""; if (!handleMarker(ln)) { log.textContent += ln + "\n"; log.scrollTop=log.scrollHeight; } } catch {} });
    es.onerror = () => {
      if (!usingLegacy) {
        usingLegacy = true; try { es.close(); } catch {}
        es = new EventSource(`/runs/active/stream`);
        es.addEventListener("log", e => { try { const m=JSON.parse(e.data); const ln=m.line||""; if (!handleMarker(ln)) { log.textContent += ln + "\n"; log.scrollTop=log.scrollHeight; } } catch {} });
        es.addEventListener("done", e => { let m={}; try{m=JSON.parse(e.data);}catch{} log.textContent += `\n[done - exit ${m.exit_code}]\n`; es.close(); ui.invalidateCaches(); ui.checkRebootBanner(); ui.loadHealth(); });
        es.onerror = () => { try { es.close(); } catch {} };
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
ui.loadRunDetail = async function(runId) {
  // Reuse existing renderer, then post-process the HTML to add inline buttons.
  await _origLoadRunDetail.call(this, runId);
  const det = $("#run-detail");
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
  f.elements.repo_path.value    = host?.repo_path || "~/Dev_Env/Ubuntu_Aktualizacje";
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
  // AI provider Test connection
  if (e.target.id === "ai-test-btn") {
    const out = $("#ai-output");
    out.textContent = "testing AI provider…";
    try {
      const r = await api.post("/suggestions/test", {});
      out.textContent = JSON.stringify(r, null, 2);
    } catch (err) { out.textContent = String(err); }
  }
});

// AI form: persist base_url too
const _origLoadSuggestions = ui.loadSuggestions;
ui.loadSuggestions = async function() {
  await _origLoadSuggestions.call(this);
  try {
    const s = await api.get("/settings");
    const f = $("#ai-form");
    if (f && f.elements.ai_base_url) f.elements.ai_base_url.value = (s.ai && s.ai.base_url) || "";
  } catch {}
};

// Patch AI form save handler to include base_url
document.addEventListener("submit", async e => {
  if (e.target && e.target.id === "ai-form") {
    e.preventDefault();
    e.stopImmediatePropagation();
    const f = e.target;
    const out = $("#ai-output");
    try {
      const cur = await api.get("/settings");
      const merged = {...cur, ai: {
        provider: f.elements.ai_provider.value,
        api_key:  f.elements.ai_api_key.value,
        model:    f.elements.ai_model.value,
        base_url: f.elements.ai_base_url ? f.elements.ai_base_url.value : "",
      }};
      const r = await fetch("/settings", {method:"PUT",
        headers:{"content-type":"application/json"}, body: JSON.stringify(merged)});
      out.textContent = r.ok ? "saved" : `error ${r.status}`;
      ui._loaded.suggest = false; ui.loadSuggestions();
    } catch (err) { out.textContent = String(err); }
  }
}, true);  // capture phase so this runs before the older one

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
  const mutating = !body.dry_run && (
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

// Reboot banner
document.addEventListener("click", e => {
  if (e.target.id === "reboot-now-btn")     ui.rebootNow();
  if (e.target.id === "reboot-dismiss-btn") $("#reboot-banner").classList.add("hidden");
  if (e.target.id === "overview-refresh-btn") {
    ui.invalidateCaches();
    ui._loaded.overview = true;
    ui.loadOverview();
    ui.checkRebootBanner();
  }
  if (e.target.id === "apps-refresh-btn") {
    ui._loaded.apps = false; ui.loadApps();
  }
  const addBtn = e.target.closest("[data-apps-add]");
  if (addBtn) ui.appsAdd(addBtn.dataset.pkg, addBtn.dataset.cat);
  const rmBtn = e.target.closest("[data-apps-rm]");
  if (rmBtn) ui.appsRemove(rmBtn.dataset.pkg, rmBtn.dataset.cat);
  if (e.target.id === "inv-refresh-btn") {
    // Inventory-only refresh: clears the backend cache too.
    api.post("/inventory/refresh", {}).catch(()=>{});
    window.INV_SUMMARY = null;
    ui.loadInventoryDashboard();
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
      const r = await api.post("/apps/add", {package: ad.dataset.pkg, category: ad.dataset.cat});
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
      const r = await api.post("/apps/remove", {package: rm.dataset.pkg, category: rm.dataset.cat});
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
      const r = await api.post("/apps/add", {package: pkg, category: cat});
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
  try { lsTheme = localStorage.getItem("ascendo_theme"); } catch {}
  if (lsTheme) {
    try { window.applyThemePref(lsTheme); } catch {}
  }
  try {
    const s = await api.get("/settings");
    window.SETTINGS_CACHE = s;
    // localStorage wins over /settings — the wizard wrote it last.
    const themePref = lsTheme || (s.ui && s.ui.theme) || "dark";
    const langPref  = (s.ui && s.ui.language) || "auto";
    window.applyThemePref(themePref);
    window.UI_LANG = (langPref === "en" || langPref === "pl")
      ? langPref
      : window.detectLanguage();
    window.applyI18n();
  } catch {
    window.applyThemePref(lsTheme || "dark");
    window.UI_LANG = window.detectLanguage();
    window.applyI18n();
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
