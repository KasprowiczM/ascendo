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
    var p = el("span", "asc-pill asc-pill--" + status);
    p.appendChild(el("span", "asc-pill__dot"));
    p.appendChild(el("span", "asc-pill__txt", o.label != null ? o.label : status));
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
    b.appendChild(el("span", "asc-banner__dot"));
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
      line.appendChild(el("span", "asc-timeline__label", r.label != null ? r.label : ""));
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
    line.appendChild(el("span", "asc-verdict__dot asc-verdict__dot--" + STATUS(o.status)));
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
    AttentionList: AttentionList
  };
})();
