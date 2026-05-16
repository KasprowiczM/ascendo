// Ascendo - i18n loader + helpers (vanilla, no framework).
//
// Locale DATA lives in i18n.en.js / i18n.pl.js, loaded via
// <script defer> BEFORE this file (defer preserves document order, so
// window.I18N.{en,pl} exist before app.js calls tr()). This file owns
// only the runtime helpers: tr / applyI18n / detectLanguage / applyTheme.
window.I18N = window.I18N || {};

// Helpers
window.tr = function tr(path) {
  const lang = window.UI_LANG || "en";
  const dict = window.I18N[lang] || window.I18N.en;
  let cur = dict;
  for (const part of path.split(".")) {
    if (cur && typeof cur === "object" && part in cur) cur = cur[part];
    else { cur = undefined; break; }
  }
  if (cur === undefined && lang !== "en") {
    // fallback to English
    let en = window.I18N.en;
    for (const part of path.split(".")) {
      if (en && typeof en === "object" && part in en) en = en[part];
      else { en = undefined; break; }
    }
    return en !== undefined ? en : path;
  }
  return cur !== undefined ? cur : path;
};

// Apply translation to all elements with [data-i18n="path.to.key"]
window.applyI18n = function applyI18n(root) {
  root = root || document;
  root.querySelectorAll("[data-i18n]").forEach(el => {
    const key = el.getAttribute("data-i18n");
    el.textContent = window.tr(key);
  });
  root.querySelectorAll("[data-i18n-placeholder]").forEach(el => {
    el.setAttribute("placeholder", window.tr(el.getAttribute("data-i18n-placeholder")));
  });
  // Tooltips. Used by the wizard's top-right Skip button and others.
  root.querySelectorAll("[data-i18n-title]").forEach(el => {
    el.setAttribute("title", window.tr(el.getAttribute("data-i18n-title")));
  });
  document.documentElement.lang = window.UI_LANG === "pl" ? "pl" : "en";
};

window.detectLanguage = function detectLanguage() {
  const stored = (window.SETTINGS_CACHE && window.SETTINGS_CACHE.ui && window.SETTINGS_CACHE.ui.language) || "auto";
  if (stored === "en" || stored === "pl") return stored;
  const browser = (navigator.language || "en").toLowerCase();
  return browser.startsWith("pl") ? "pl" : "en";
};

window.applyTheme = function applyTheme(themePref) {
  // Three-state preference: "dark" / "light" / "auto" (follows the OS via
  // prefers-color-scheme). Anything unrecognised resolves to dark — the
  // design system's primary surface — so a stale localStorage value can
  // never leak the CSS-default light theme through.
  const root = document.documentElement;
  let mode;
  if (themePref === "auto") {
    const sys = window.matchMedia &&
      window.matchMedia("(prefers-color-scheme: dark)").matches;
    mode = sys ? "dark" : "light";
  } else {
    mode = themePref === "light" ? "light" : "dark";
  }
  root.setAttribute("data-theme", mode);
  // Keep the user-facing preference in dataset.themePref (might be "auto")
  // separately from the resolved-mode in data-theme (always "dark" or
  // "light"). This lets the cycle resume from the user's intent, not
  // the rendered mode.
  root.dataset.themePref = themePref || "dark";
};
