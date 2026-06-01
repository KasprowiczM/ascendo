// Ascendo — cross-source deduplication consent card (Sesja 86).
//
// Self-contained, additive module (no coupling to app.js's run lifecycle).
// Polls GET /dedup/pending and, when duplicates exist, renders an
// "Action required → resolve duplicate" card into #dedup-panel. The
// "Resolve duplicate" button POSTs /dedup/apply — consent is always an
// explicit click; the backend recomputes + validates the uninstall set,
// so nothing destructive is ever written implicitly.
(function () {
  "use strict";

  function t(key, fallback) {
    try {
      if (typeof window.tr === "function") {
        const out = window.tr(key);
        if (out && out !== key) return out;
      }
    } catch (_e) { /* i18n optional */ }
    return fallback;
  }

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  async function apply(appId, btn) {
    const label = btn.textContent;
    btn.disabled = true;
    btn.textContent = t("dedup.applying", "Resolving…");
    try {
      const r = await fetch("/dedup/apply", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(appId ? { app_ids: [appId] } : {}),
      });
      if (!r.ok) {
        const detail = await r.text();
        btn.textContent = t("dedup.failed", "Failed");
        btn.disabled = false;
        // eslint-disable-next-line no-console
        console.warn("dedup apply failed", r.status, detail);
        return;
      }
      btn.textContent = t("dedup.queued", "Queued ✓");
      // Re-poll after a beat so the card reflects the resolved state.
      setTimeout(refresh, 1500);
    } catch (e) {
      btn.textContent = label;
      btn.disabled = false;
      // eslint-disable-next-line no-console
      console.warn("dedup apply error", e);
    }
  }

  function render(fixes) {
    const panel = document.getElementById("dedup-panel");
    if (!panel) return;
    panel.innerHTML = "";
    if (!fixes || fixes.length === 0) {
      panel.classList.add("hidden");
      return;
    }
    panel.classList.remove("hidden");
    panel.classList.add("card");

    const head = el("h3", null,
      t("dedup.title", "Action required — resolve duplicate installs"));
    panel.appendChild(head);
    panel.appendChild(el("p", "dedup-intro",
      t("dedup.intro",
        "These apps are installed via more than one package manager. " +
        "Keep the recommended source; the others can be uninstalled.")));

    fixes.forEach(function (fix) {
      const row = el("div", "action-row");
      const meta = el("div", "action-meta");
      meta.appendChild(el("strong", null, fix.app_name || fix.app_id));
      const others = (fix.installed || [])
        .filter(function (s) { return s.recommended_uninstall; })
        .map(function (s) { return s.source; });
      const reason = el("span", "action-reason",
        t("dedup.reason_keep", "Keep") + " " + (fix.best_installed || "?") +
        (others.length
          ? " · " + t("dedup.reason_remove", "remove") + " " + others.join(", ")
          : ""));
      meta.appendChild(reason);
      row.appendChild(meta);

      const buttons = el("div", "action-buttons");
      const btn = el("button", "btn-danger",
        t("dedup.resolve", "Resolve duplicate"));
      btn.addEventListener("click", function () {
        const msg = t("dedup.confirm",
          "Uninstall the non-preferred source(s) of " +
          (fix.app_name || fix.app_id) + "? This removes the duplicate package.");
        if (window.confirm(msg)) apply(fix.app_id, btn);
      });
      buttons.appendChild(btn);
      row.appendChild(buttons);
      panel.appendChild(row);
    });
  }

  async function refresh() {
    try {
      const r = await fetch("/dedup/pending");
      if (!r.ok) return;
      const body = await r.json();
      render(body && body.fixes);
    } catch (_e) {
      // Endpoint unavailable (older backend) — leave the panel hidden.
    }
  }

  window.ascendoDedup = { refresh: refresh };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", refresh);
  } else {
    refresh();
  }
  // Re-poll when the operator returns to the dashboard view.
  window.addEventListener("hashchange", function () {
    const h = (location.hash || "").toLowerCase();
    if (h === "" || h.indexOf("dashboard") !== -1 || h.indexOf("overview") !== -1) {
      refresh();
    }
  });
})();
