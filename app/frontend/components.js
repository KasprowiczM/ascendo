/* ============================================================
   ASCENDO — Component foundation (window.AC)
   components.js — plain ES, classic <script>, no build step.

   XSS-safe: every text node via textContent. Never innerHTML
   with interpolated data. All classes prefixed `asc-`.
   Styles live in components.css (.asc-* only, tokens only).
   ============================================================ */
(function () {
  "use strict";

  /* ---- tiny DOM helpers --------------------------------- */
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = String(text);
    return n;
  }
  function append(parent, child) {
    if (child == null) return parent;
    if (Array.isArray(child)) {
      for (var i = 0; i < child.length; i++) append(parent, child[i]);
      return parent;
    }
    if (typeof child === "string") {
      parent.appendChild(document.createTextNode(child));
      return parent;
    }
    parent.appendChild(child);
    return parent;
  }
  function STATUS(s) {
    // Raw visual variants pass through unchanged.
    var variants = { ok: 1, warn: 1, err: 1, info: 1, neutral: 1 };
    if (variants[s]) return s;
    // Honest domain status -> visual variant (Sesja 86 / audit P2). Before
    // this, anything not a raw variant collapsed to "neutral" grey — so a
    // failed apply, a triggered-but-unconfirmed update, and an up-to-date
    // package all looked identical. A failed apply must read RED, never grey,
    // never green.
    var map = {
      up_to_date: "ok",
      success: "ok",
      outdated: "warn",
      planned: "warn",
      triggered: "warn",
      triggered_pending: "warn",  // pending vendor reconciliation
      failed: "err",
      partial: "err",
      missing: "err",
      skipped: "neutral",
      unknown: "neutral"
    };
    return map[s] || "neutral";
  }

  /* ---- mount ------------------------------------------- */
  function mount(host, node) {
    if (!host) return;
    while (host.firstChild) host.removeChild(host.firstChild);
    append(host, node);
    return host;
  }

  /* ---- StatusPill -------------------------------------- */
  function StatusPill(o) {
    o = o || {};
    var status = STATUS(o.status);
    var label = o.label != null ? o.label : status;
    var p = el("span", "asc-pill asc-pill--" + status);
    p.setAttribute("aria-label", label || status);
    var dot = el("span", "asc-pill__dot");
    dot.setAttribute("aria-hidden", "true");
    p.appendChild(dot);
    p.appendChild(el("span", "asc-pill__txt", label));
    return p;
  }

  /* ---- Button ------------------------------------------ */
  function Button(o) {
    o = o || {};
    var variant = o.variant || "secondary";
    var tag = o.href ? "a" : "button";
    var b = el(tag, "asc-btn asc-btn--" + variant);
    if (o.href) {
      b.setAttribute("href", o.href);
    } else {
      b.setAttribute("type", "button");
    }
    if (o.icon) {
      var ic = el("span", "asc-btn__icon");
      if (o.icon instanceof Node) ic.appendChild(o.icon);
      else ic.textContent = String(o.icon);
      b.appendChild(ic);
    }
    b.appendChild(el("span", "asc-btn__label", o.label || ""));
    if (typeof o.onClick === "function") b.addEventListener("click", o.onClick);
    return b;
  }

  /* ---- Card -------------------------------------------- */
  function Card(o) {
    o = o || {};
    var tone = o.tone || "default";
    var c = el("section", "asc-card asc-card--" + tone);
    if (o.title || o.action) {
      var head = el("header", "asc-card__head");
      head.appendChild(el("h2", "asc-card__title", o.title || ""));
      if (o.action) {
        var a = el("div", "asc-card__action");
        append(a, o.action);
        head.appendChild(a);
      }
      c.appendChild(head);
    }
    var body = el("div", "asc-card__body");
    append(body, o.children);
    c.appendChild(body);
    return c;
  }

  /* ---- StatPair ---------------------------------------- */
  function StatPair(o) {
    o = o || {};
    var clickable = typeof o.onClick === "function";
    var s = el(clickable ? "button" : "div", "asc-stat" + (clickable ? " asc-stat--btn" : ""));
    if (clickable) {
      s.setAttribute("type", "button");
      s.addEventListener("click", o.onClick);
    }
    var top = el("div", "asc-stat__top");
    top.appendChild(el("span", "asc-stat__value", o.value != null ? o.value : "—"));
    if (o.status) {
      var dot = el("span", "asc-stat__dot asc-stat__dot--" + STATUS(o.status));
      top.appendChild(dot);
    }
    s.appendChild(top);
    s.appendChild(el("span", "asc-stat__label", o.label || ""));
    if (o.sub != null) s.appendChild(el("span", "asc-stat__sub", o.sub));
    return s;
  }

  /* ---- KpiStrip ---------------------------------------- */
  function KpiStrip(items) {
    var strip = el("div", "asc-kpi");
    (items || []).forEach(function (it) {
      strip.appendChild(StatPair(it));
    });
    return strip;
  }

  /* ---- EmptyState -------------------------------------- */
  function EmptyState(o) {
    o = o || {};
    var e = el("div", "asc-empty");
    e.appendChild(el("div", "asc-empty__glyph", o.glyph != null ? o.glyph : "✓"));
    e.appendChild(el("h3", "asc-empty__title", o.title || ""));
    if (o.line != null) e.appendChild(el("p", "asc-empty__line", o.line));
    if (o.action) {
      var wrap = el("div", "asc-empty__action");
      append(wrap, o.action);
      e.appendChild(wrap);
    }
    return e;
  }

  /* ---- Banner ------------------------------------------ */
  function Banner(o) {
    o = o || {};
    var tone = STATUS(o.tone);
    var b = el("div", "asc-banner asc-banner--" + tone);
    b.setAttribute("role", tone === "err" ? "alert" : "status");
    var dot = el("span", "asc-banner__dot");
    dot.setAttribute("aria-hidden", "true");
    b.appendChild(dot);
    b.appendChild(el("p", "asc-banner__text", o.text || ""));
    if (o.actions) {
      var acts = el("div", "asc-banner__actions");
      append(acts, o.actions);
      b.appendChild(acts);
    }
    return b;
  }

  /* ---- Skeleton ---------------------------------------- */
  function Skeleton(o) {
    o = o || {};
    var rows = o.rows && o.rows > 0 ? o.rows : 3;
    var variant = o.variant || "rows";
    var s = el("div", "asc-skel asc-skel--" + variant);
    s.setAttribute("aria-hidden", "true");
    for (var i = 0; i < rows; i++) {
      var line = el("div", "asc-skel__row");
      if (i === 0 && variant === "rows") line.classList.add("asc-skel__row--lead");
      s.appendChild(line);
    }
    return s;
  }

  /* ---- Timeline ---------------------------------------- */
  function Timeline(rows) {
    var t = el("ul", "asc-timeline");
    (rows || []).forEach(function (r) {
      r = r || {};
      var open = typeof r.onOpen === "function";
      var li = el("li", "asc-timeline__row");
      var line = el(open ? "button" : "div", "asc-timeline__line" + (open ? " asc-timeline__line--btn" : ""));
      if (open) {
        line.setAttribute("type", "button");
        line.addEventListener("click", r.onOpen);
      }
      line.appendChild(el("time", "asc-timeline__time", r.time != null ? r.time : ""));
      var mid = el("div", "asc-timeline__mid");
      mid.appendChild(el("span", "asc-timeline__label", r.label != null ? r.label : ""));
      if (r.detail != null && r.detail !== "") mid.appendChild(el("span", "asc-timeline__detail", r.detail));
      line.appendChild(mid);
      if (r.tags && r.tags.length) {
        var tags = el("div", "asc-timeline__tags");
        r.tags.forEach(function (tg) {
          if (tg) tags.appendChild(StatusPill({ status: tg.tone, label: tg.label != null ? tg.label : "" }));
        });
        line.appendChild(tags);
      }
      line.appendChild(
        StatusPill({ status: r.status, label: r.statusLabel != null ? r.statusLabel : "" })
      );
      li.appendChild(line);
      t.appendChild(li);
    });
    return t;
  }

  /* ---- Drawer ------------------------------------------ */
  var _drawerRoot = null;
  var _lastFocus = null;

  function _ensureDrawer() {
    if (_drawerRoot) return _drawerRoot;
    var root = el("div", "asc-drawer-root");
    root.setAttribute("hidden", "");
    var scrim = el("div", "asc-drawer__scrim");
    var panel = el("aside", "asc-drawer");
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-modal", "true");
    panel.setAttribute("tabindex", "-1");
    var head = el("header", "asc-drawer__head");
    var title = el("h2", "asc-drawer__title");
    var close = Button({
      variant: "ghost",
      label: "Close",
      onClick: function () {
        Drawer.close();
      }
    });
    close.classList.add("asc-drawer__close");
    head.appendChild(title);
    head.appendChild(close);
    var body = el("div", "asc-drawer__body");
    var footer = el("footer", "asc-drawer__footer");
    panel.appendChild(head);
    panel.appendChild(body);
    panel.appendChild(footer);
    root.appendChild(scrim);
    root.appendChild(panel);
    document.body.appendChild(root);
    scrim.addEventListener("click", function () {
      Drawer.close();
    });
    document.addEventListener("keydown", function (ev) {
      if (root.hasAttribute("hidden")) return;
      if (ev.key === "Escape") {
        ev.preventDefault();
        Drawer.close();
        return;
      }
      if (ev.key === "Tab") _trap(ev, panel);
    });
    _drawerRoot = {
      root: root,
      panel: panel,
      title: title,
      body: body,
      footer: footer
    };
    return _drawerRoot;
  }

  function _focusables(scope) {
    return Array.prototype.slice
      .call(
        scope.querySelectorAll(
          'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])'
        )
      )
      .filter(function (n) {
        return n.offsetParent !== null || n === document.activeElement;
      });
  }
  function _trap(ev, scope) {
    var f = _focusables(scope);
    if (!f.length) {
      ev.preventDefault();
      scope.focus();
      return;
    }
    var first = f[0];
    var last = f[f.length - 1];
    if (ev.shiftKey && document.activeElement === first) {
      ev.preventDefault();
      last.focus();
    } else if (!ev.shiftKey && document.activeElement === last) {
      ev.preventDefault();
      first.focus();
    }
  }

  var Drawer = {
    open: function (o) {
      o = o || {};
      var d = _ensureDrawer();
      _lastFocus = document.activeElement;
      d.title.textContent = o.title || "";
      while (d.body.firstChild) d.body.removeChild(d.body.firstChild);
      append(d.body, o.body);
      while (d.footer.firstChild) d.footer.removeChild(d.footer.firstChild);
      if (o.footer) {
        append(d.footer, o.footer);
        d.footer.removeAttribute("hidden");
      } else {
        d.footer.setAttribute("hidden", "");
      }
      d.root.removeAttribute("hidden");
      // force reflow so the open transition runs
      void d.panel.offsetWidth;
      d.root.classList.add("asc-drawer-root--open");
      var f = _focusables(d.panel);
      (f[0] || d.panel).focus();
      return d;
    },
    close: function () {
      if (!_drawerRoot) return;
      var d = _drawerRoot;
      d.root.classList.remove("asc-drawer-root--open");
      d.root.setAttribute("hidden", "");
      if (_lastFocus && typeof _lastFocus.focus === "function") {
        _lastFocus.focus();
      }
      _lastFocus = null;
    }
  };

  /* ---- ProgressBar ------------------------------------- */
  function ProgressBar(o) {
    o = o || {};
    var raw = (o.total > 0)
      ? Math.round((o.value / o.total) * 100)
      : (o.pct != null ? o.pct : 0);
    var pct = Math.max(0, Math.min(100, raw)); // clamp both paths (review M2)
    var variant = o.variant || "accent"; // accent | ok | warn | err | info
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

  /* ---- VerdictHeader ----------------------------------- */
  function VerdictHeader(o) {
    o = o || {}; // {status, title, sub, cta:{label,onClick,variant}}
    var h = el("div", "asc-verdict");
    var main = el("div", "asc-verdict__main");
    var line = el("div", "asc-verdict__line");
    var dot = el("span", "asc-verdict__dot asc-verdict__dot--" + STATUS(o.status));
    dot.setAttribute("aria-hidden", "true");
    line.appendChild(dot);
    line.appendChild(el("span", "asc-verdict__title", o.title != null ? o.title : ""));
    main.appendChild(line);
    if (o.sub != null) main.appendChild(el("p", "asc-verdict__sub", o.sub));
    h.appendChild(main);
    if (o.cta) h.appendChild(Button(o.cta));
    return h;
  }

  /* ---- AttentionList ----------------------------------- */
  function AttentionCard(it) {
    it = it || {}; // {tone, title, detail, actions:[Button opts]}
    var c = el("div", "asc-attn asc-attn--" + STATUS(it.tone || "warn"));
    var body = el("div", "asc-attn__body");
    body.appendChild(el("div", "asc-attn__title", it.title != null ? it.title : ""));
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
    if (!items.length) return null; // caller skips the section entirely
    var list = el("div", "asc-attn-list");
    items.forEach(function (it) { list.appendChild(AttentionCard(it)); });
    return list;
  }

  /* ---- run-monitor helpers (internal) ------------------ */
  function fmtDur(ms) {
    if (ms == null || ms < 0 || isNaN(ms)) return "0:00";
    var s = Math.floor(ms / 1000);
    var h = Math.floor(s / 3600);
    var m = Math.floor((s % 3600) / 60);
    var sec = s % 60;
    var mm = (h > 0 && m < 10) ? "0" + m : String(m);
    var ss = sec < 10 ? "0" + sec : String(sec);
    return (h > 0 ? h + ":" : "") + mm + ":" + ss;
  }
  function runVariant(state) {
    switch (state) {
      case "completed": return "ok";
      case "completed_with_warnings": return "warn";
      case "failed": return "err";
      case "cancelled": return "neutral";
      default: return "info"; // queued|preparing|running|finalizing|unknown active
    }
  }
  function srcVariant(status) {
    switch (status) {
      case "completed": case "success": case "up_to_date": return "ok";
      case "running": return "info";
      case "failed": case "partial": return "err";
      case "warn": case "triggered": return "warn";
      default: return "neutral"; // queued|pending|skipped
    }
  }
  function humanizePhase(id) {
    return String(id || "").replace(/_/g, " ").replace(/^\w/, function (c) {
      return c.toUpperCase();
    });
  }

  /* ---- RunHeader --------------------------------------- */
  // {intent, state, elapsedMs, etaMs, value, total, pct, onStop,
  //  stateLabel, stopLabel, etaSuffix}
  function RunHeader(o) {
    o = o || {};
    var variant = runVariant(o.state);
    var active = variant === "info";
    var h = el("div", "asc-runhead");
    var top = el("div", "asc-runhead__top");

    var main = el("div", "asc-runhead__main");
    var line = el("div", "asc-runhead__line");
    var dot = el("span", "asc-runhead__dot asc-runhead__dot--" + variant + (active ? " asc-runhead__dot--live" : ""));
    dot.setAttribute("aria-hidden", "true");
    line.appendChild(dot);
    line.appendChild(el("span", "asc-runhead__intent", o.intent != null ? o.intent : ""));
    main.appendChild(line);

    var meta = el("div", "asc-runhead__meta");
    meta.appendChild(el("span", "asc-runhead__state", o.stateLabel != null ? o.stateLabel : (o.state != null ? o.state : "")));
    if (o.elapsedMs != null) {
      meta.appendChild(el("span", "asc-runhead__sep", "·"));
      meta.appendChild(el("span", "asc-runhead__time", fmtDur(o.elapsedMs)));
    }
    if (active && o.etaMs != null && o.etaMs > 0) {
      meta.appendChild(el("span", "asc-runhead__sep", "·"));
      meta.appendChild(el("span", "asc-runhead__eta", "~" + fmtDur(o.etaMs) + " " + (o.etaSuffix != null ? o.etaSuffix : "left (est.)")));
    }
    main.appendChild(meta);
    top.appendChild(main);

    if (typeof o.onStop === "function") {
      top.appendChild(Button({ variant: "danger", label: o.stopLabel != null ? o.stopLabel : "Stop", onClick: o.onStop }));
    }
    h.appendChild(top);
    h.appendChild(ProgressBar({ value: o.value, total: o.total, pct: o.pct, variant: variant }));
    return h;
  }

  /* ---- PhaseStepper ------------------------------------ */
  // phases: [{id, status, label?}]  status: pending|running|done|failed
  function PhaseStepper(phases) {
    phases = phases || [];
    var ol = el("ol", "asc-stepper");
    ol.setAttribute("aria-live", "polite");
    ol.setAttribute("aria-relevant", "additions text");
    phases.forEach(function (p, i) {
      p = p || {};
      var st = p.status || "pending";
      var li = el("li", "asc-stepper__step asc-stepper__step--" + st);
      li.setAttribute("aria-current", st === "running" ? "step" : "false");
      var marker = el("span", "asc-stepper__marker");
      marker.setAttribute("aria-hidden", "true");
      if (st === "done") marker.textContent = "✓";
      else if (st === "failed") marker.textContent = "!";
      else marker.textContent = String(i + 1);
      li.appendChild(marker);
      var lbl = p.label != null ? p.label : humanizePhase(p.id);
      var labelSpan = el("span", "asc-stepper__label", lbl);
      li.appendChild(labelSpan);
      var srStatus = el("span", "asc-sr-only", " (" + st + ")");
      li.appendChild(srStatus);
      ol.appendChild(li);
    });
    return ol;
  }

  /* ---- SourceProgressRow ------------------------------- */
  // {name, status, done, total, current, statusLabel}
  function SourceProgressRow(o) {
    o = o || {};
    var v = srcVariant(o.status);
    var row = el("div", "asc-srcrow asc-srcrow--" + v);
    var head = el("div", "asc-srcrow__head");
    head.appendChild(el("span", "asc-srcrow__dot asc-srcrow__dot--" + v));
    head.appendChild(el("span", "asc-srcrow__name", o.name != null ? o.name : ""));
    if (o.total > 0) {
      head.appendChild(el("span", "asc-srcrow__counts", (o.done || 0) + " / " + o.total));
    }
    head.appendChild(StatusPill({ status: v, label: o.statusLabel != null ? o.statusLabel : (o.status != null ? o.status : "") }));
    row.appendChild(head);
    row.appendChild(ProgressBar({ value: o.done, total: o.total, variant: v }));
    if (o.current) row.appendChild(el("div", "asc-srcrow__current", o.current));
    return row;
  }

  /* ---- LogViewer --------------------------------------- */
  // {lines:[{ts,source,level,text}], collapsed:true, showLabel, hideLabel,
  //  filterPlaceholder, linesSuffix}
  function LogViewer(o) {
    o = o || {};
    var lines = o.lines || [];
    var collapsed = (o.collapsed !== false); // default collapsed
    var wrap = el("div", "asc-log" + (collapsed ? "" : " asc-log--open"));

    var head = el("div", "asc-log__head");
    var toggle = el("button", "asc-log__toggle");
    toggle.setAttribute("type", "button");
    toggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
    toggle.appendChild(el("span", "asc-log__caret"));
    var toggleTxt = el("span", "asc-log__toggle-txt", collapsed ? (o.showLabel || "Show log") : (o.hideLabel || "Hide log"));
    toggle.appendChild(toggleTxt);
    head.appendChild(toggle);
    head.appendChild(el("span", "asc-log__count", String(lines.length) + (o.linesSuffix != null ? o.linesSuffix : " lines")));
    var filter = el("input", "asc-log__filter");
    filter.setAttribute("type", "text");
    var fph = o.filterPlaceholder != null ? o.filterPlaceholder : "Filter…";
    filter.setAttribute("placeholder", fph);
    filter.setAttribute("aria-label", fph);
    head.appendChild(filter);
    wrap.appendChild(head);

    var body = el("div", "asc-log__body");
    if (collapsed) body.setAttribute("hidden", "");
    var view = el("div", "asc-log__view");
    // Cap rendered rows for DOM perf — runStore already ring-buffers at 2000.
    var slice = lines.length > 600 ? lines.slice(-600) : lines;
    slice.forEach(function (ln) {
      ln = ln || {};
      var r = el("div", "asc-log__row asc-log__row--" + (ln.level || "info"));
      if (ln.source) r.appendChild(el("span", "asc-log__src", ln.source));
      r.appendChild(el("span", "asc-log__text", ln.text != null ? ln.text : ""));
      view.appendChild(r);
    });
    body.appendChild(view);
    wrap.appendChild(body);

    function autoscroll() { view.scrollTop = view.scrollHeight; }

    toggle.addEventListener("click", function () {
      var open = wrap.classList.toggle("asc-log--open");
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
      toggleTxt.textContent = open ? (o.hideLabel || "Hide log") : (o.showLabel || "Show log");
      if (open) { body.removeAttribute("hidden"); autoscroll(); }
      else { body.setAttribute("hidden", ""); }
    });
    filter.addEventListener("input", function () {
      var q = filter.value.toLowerCase();
      var rows = view.childNodes;
      for (var i = 0; i < rows.length; i++) {
        var node = rows[i];
        if (node.nodeType !== 1) continue;
        if (!q || node.textContent.toLowerCase().indexOf(q) !== -1) node.removeAttribute("hidden");
        else node.setAttribute("hidden", "");
      }
    });
    if (!collapsed) autoscroll();
    return wrap;
  }

  /* ---- IntentRunCard (Phase 2.6) ----------------------- */
  // {weight:'hero'|'secondary'|'caution', title, desc, tag, ctaLabel, onClick}
  // The whole card is the button. weight drives emphasis: hero = the one
  // lime action per screen; secondary = quiet; caution = amber-flagged.
  function IntentRunCard(o) {
    o = o || {};
    var weight = o.weight || "secondary";
    var card = el("button", "asc-intent asc-intent--" + weight);
    card.setAttribute("type", "button");
    var head = el("div", "asc-intent__head");
    head.appendChild(el("span", "asc-intent__title", o.title != null ? o.title : ""));
    if (o.tag) head.appendChild(el("span", "asc-intent__tag", o.tag));
    card.appendChild(head);
    if (o.desc != null) card.appendChild(el("p", "asc-intent__desc", o.desc));
    if (o.ctaLabel) card.appendChild(el("span", "asc-intent__cta", o.ctaLabel));
    if (typeof o.onClick === "function") {
      card.addEventListener("click", function () {
        if (card.classList.contains("is-busy")) return;
        o.onClick(card);
      });
    }
    return card;
  }

  /* ---- DangerConfirm (Phase 2.6) ----------------------- */
  // Type-to-confirm destructive gate. Opens the shared Drawer (scrim + Esc +
  // focus-trap = safe Cancel). The destructive button is DISABLED until the
  // operator types the confirm word; Cancel is the default and sits far from
  // the red action. {title, body, confirmWord, typeHint, confirmLabel,
  // cancelLabel, onConfirm, onCancel}
  function DangerConfirm(o) {
    o = o || {};
    var word = (o.confirmWord || "update");
    var hint = (o.typeHint || "Type “{w}” to confirm").replace("{w}", word);
    var body = el("div", "asc-danger");
    if (o.body != null) body.appendChild(el("p", "asc-danger__body", o.body));
    var label = el("label", "asc-danger__field");
    label.appendChild(el("span", "asc-danger__hint", hint));
    var input = el("input", "asc-danger__input");
    input.setAttribute("type", "text");
    input.setAttribute("autocomplete", "off");
    input.setAttribute("autocapitalize", "off");
    input.setAttribute("spellcheck", "false");
    input.setAttribute("aria-label", hint);
    label.appendChild(input);
    body.appendChild(label);

    var confirmBtn = Button({
      variant: "danger",
      label: o.confirmLabel || "Confirm",
      onClick: function () {
        if (input.value.trim().toLowerCase() !== word.toLowerCase()) return;
        Drawer.close();
        if (typeof o.onConfirm === "function") o.onConfirm();
      }
    });
    confirmBtn.disabled = true;
    confirmBtn.setAttribute("aria-disabled", "true");
    input.addEventListener("input", function () {
      var ok = input.value.trim().toLowerCase() === word.toLowerCase();
      confirmBtn.disabled = !ok;
      confirmBtn.setAttribute("aria-disabled", ok ? "false" : "true");
    });
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !confirmBtn.disabled) { e.preventDefault(); confirmBtn.click(); }
    });

    var cancelBtn = Button({
      variant: "secondary",
      label: o.cancelLabel || "Cancel",
      onClick: function () { Drawer.close(); if (typeof o.onCancel === "function") o.onCancel(); }
    });

    var actions = el("div", "asc-danger__actions");
    actions.appendChild(cancelBtn);                      // safe default, left
    actions.appendChild(el("span", "asc-danger__spacer")); // distance the red action
    actions.appendChild(confirmBtn);                     // destructive, far right
    Drawer.open({ title: o.title || "Confirm", body: body, footer: actions });
    setTimeout(function () { try { input.focus(); } catch (e) {} }, 60);
    return null;
  }

  /* ---- CompletionSummary (Phase 2.7) ------------------- */
  // Verdict-first summary of a finished run. Reused by the live monitor (on
  // done) and the History run-detail drawer. {verdict, verdictLabel, sub,
  // needsReboot, rebootText, counts, countLabels, attention, attentionTitle,
  // changed, changedTitle, actions}
  function CompletionSummary(o) {
    o = o || {};
    var v = el("div", "asc-completion");
    // Map the terminal lifecycle (completed / completed_with_warnings /
    // failed / cancelled) → a status variant; STATUS() doesn't know these.
    v.appendChild(VerdictHeader({
      status: runVariant(o.verdict),
      title: o.verdictLabel != null ? o.verdictLabel : "",
      sub: o.sub
    }));
    if (o.needsReboot) {
      v.appendChild(Banner({ tone: "warn", text: o.rebootText || "A restart is required to finish." }));
    }
    if (o.counts) {
      var labels = o.countLabels || {};
      var kpis = [];
      ["updated", "deferred", "warned", "failed"].forEach(function (k) {
        var val = o.counts[k];
        if (val == null) return;
        var st = (k === "failed" && val > 0) ? "err"
          : (k === "warned" && val > 0) ? "warn"
          : (k === "updated" && val > 0) ? "ok" : "neutral";
        kpis.push({ value: val, label: labels[k] || k, status: st });
      });
      if (kpis.length) v.appendChild(KpiStrip(kpis));
    }
    var attn = AttentionList(o.attention || []);
    if (attn) {
      v.appendChild(Card({ title: o.attentionTitle || "Needs your attention", children: attn }));
    }
    if (o.changed && o.changed.length) {
      var list = el("ul", "asc-changed");
      o.changed.forEach(function (c) {
        var li = el("li", "asc-changed__row");
        li.appendChild(StatusPill({ status: c.status, label: "" }));
        li.appendChild(el("span", "asc-changed__name", c.name || ""));
        var ver = (c.from && c.to) ? (c.from + " → " + c.to) : (c.to || c.from || "");
        if (ver) li.appendChild(el("span", "asc-changed__ver", ver));
        list.appendChild(li);
      });
      v.appendChild(Card({ title: o.changedTitle || "What changed", children: list }));
    }
    if (o.actions && o.actions.length) {
      var acts = el("div", "asc-completion__actions");
      o.actions.forEach(function (a) { acts.appendChild(Button(a)); });
      v.appendChild(acts);
    }
    return v;
  }

  /* ---- SourceListItem (Phase 4.2) ----------------------- */
  // One Library source row, weight-differentiated. {outdated}>0 renders
  // the heavy "update" variant (amber left-stripe + tinted count badge +
  // secondary Update action); otherwise the compact "current" one-liner.
  // Advanced (raw 5-phase) actions hide behind a `⋯` overflow button.
  function SourceListItem(o) {
    o = o || {};
    // {id, displayName, total, outdated, totalLabel, outdatedLabel,
    //  updateLabel, advancedLabel, onUpdate, onAdvanced}
    var needsUpdate = (o.outdated || 0) > 0;
    var row = el("div", "asc-source asc-source--" + (needsUpdate ? "update" : "current"));

    var name = el("div", "asc-source__name");
    name.appendChild(el("b", "asc-source__id", o.id != null ? o.id : ""));
    if (o.displayName) name.appendChild(el("span", "asc-source__sub", o.displayName));
    row.appendChild(name);

    var meta = el("div", "asc-source__meta");
    if (needsUpdate) {
      meta.appendChild(el("span", "asc-source__badge", String(o.outdated || 0)));
      if (o.outdatedLabel) meta.appendChild(el("span", "asc-source__metalbl", o.outdatedLabel));
    } else {
      meta.appendChild(StatusPill({
        status: "ok",
        label: (o.total || 0) + (o.totalLabel ? " " + o.totalLabel : ""),
      }));
    }
    row.appendChild(meta);

    var acts = el("div", "asc-source__acts");
    if (needsUpdate && typeof o.onUpdate === "function") {
      acts.appendChild(Button({
        variant: "secondary",
        label: (o.updateLabel || "Update") + (o.outdated ? " " + o.outdated : ""),
        onClick: o.onUpdate,
      }));
    }
    if (typeof o.onAdvanced === "function") {
      var more = Button({ variant: "ghost", label: "⋯", onClick: o.onAdvanced });
      more.classList.add("asc-source__more");
      if (o.advancedLabel) more.setAttribute("aria-label", o.advancedLabel);
      acts.appendChild(more);
    }
    row.appendChild(acts);
    return row;
  }

  /* ---- export ------------------------------------------ */
  window.AC = {
    mount: mount,
    Card: Card,
    StatPair: StatPair,
    StatusPill: StatusPill,
    Button: Button,
    KpiStrip: KpiStrip,
    EmptyState: EmptyState,
    Banner: Banner,
    Skeleton: Skeleton,
    Timeline: Timeline,
    Drawer: Drawer,
    ProgressBar: ProgressBar,
    VerdictHeader: VerdictHeader,
    AttentionCard: AttentionCard,
    AttentionList: AttentionList,
    RunHeader: RunHeader,
    PhaseStepper: PhaseStepper,
    SourceProgressRow: SourceProgressRow,
    LogViewer: LogViewer,
    IntentRunCard: IntentRunCard,
    DangerConfirm: DangerConfirm,
    CompletionSummary: CompletionSummary,
    SourceListItem: SourceListItem
  };
})();
