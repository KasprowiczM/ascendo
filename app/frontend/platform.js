// ============================================================================
// platform.js — OS/platform abstraction layer
// ============================================================================
// Single source of truth for OS-specific behaviour, copy, and conditional-UI
// rules. Loaded BEFORE app.js. Reads the adapter slug lazily from
// <html data-adapter> (stamped by app.js bootstrap from GET /version), so
// every getter reflects the live value even though this module parses first.
//
// HARD RULE: NVIDIA / discrete-GPU driver UI is Ubuntu/Linux + Windows ONLY.
// `Platform.supportsNvidia` is false on macOS (and unknown), and every
// NVIDIA/driver code path must gate on `Platform.allow('nvidia')`. This is a
// JS-level guard layered on top of the existing CSS `adapter-hide-macos`
// defense so macOS can never render driver UI even if a class is missed.
// ============================================================================
(function () {
  "use strict";

  // Normalise the raw adapter slug from /version into a stable OS family.
  // Backend emits: "windows" | "linux" | "linux_ubuntu" | "ubuntu" | "macos"
  // | null. We collapse the Linux variants to "linux" for rule lookups but
  // keep `raw` available for adapter-specific nuances.
  function normalize(raw) {
    const s = String(raw || "").toLowerCase();
    if (s === "macos" || s === "darwin") return "macos";
    if (s === "windows" || s === "win32") return "windows";
    if (s.startsWith("linux") || s === "ubuntu" || s === "debian") return "linux";
    return "unknown";
  }

  // Per-OS feature matrix. A feature absent from an OS's set is denied.
  // NVIDIA deliberately excluded from macos + unknown.
  const FEATURES = {
    macos:   new Set(["snapshots", "scheduling", "elevation", "appstore"]),
    linux:   new Set(["nvidia", "drivers", "snapshots", "scheduling",
                       "elevation", "kernel_modules", "reboot"]),
    windows: new Set(["nvidia", "drivers", "snapshots", "scheduling",
                       "elevation", "reboot"]),
    unknown: new Set([]),
  };

  // Elevation terminology per OS — used everywhere we'd otherwise hardcode
  // "sudo" (wrong on Windows/macOS). Resolves via i18n when window.tr is
  // available, falls back to the literal.
  const ELEVATION_TERM = {
    macos:   "platform.macos.elevation_term",
    linux:   "platform.linux.elevation_term",
    windows: "platform.windows.elevation_term",
    unknown: "platform.common.elevation_term",
  };
  const ELEVATION_FALLBACK = {
    macos:   "admin permission",
    linux:   "sudo",
    windows: "Administrator",
    unknown: "elevated permission",
  };

  function trOr(key, fallback) {
    if (typeof window.tr === "function") {
      const v = window.tr(key);
      if (v && v !== key) return v;
    }
    return fallback;
  }

  const Platform = {
    // Live OS family — reads the DOM attribute each access so it tracks
    // the value app.js stamps after /version resolves.
    get os() {
      return normalize(
        (document.documentElement.dataset.adapter) ||
        window.ADAPTER_NAME ||
        "unknown"
      );
    },

    // Raw adapter slug (linux_ubuntu vs linux) for the rare nuance case.
    get raw() {
      return String(
        document.documentElement.dataset.adapter ||
        window.ADAPTER_NAME || "unknown"
      ).toLowerCase();
    },

    is(os) { return this.os === normalize(os); },
    isMac() { return this.os === "macos"; },
    isLinux() { return this.os === "linux"; },
    isWindows() { return this.os === "windows"; },
    isKnown() { return this.os !== "unknown"; },

    // Capability gate. Every OS-specific UI path should call this rather
    // than inspecting `os` directly — keeps the matrix in one place.
    allow(feature) {
      return (FEATURES[this.os] || FEATURES.unknown).has(feature);
    },

    // The single NVIDIA guard. macOS + unknown → false, always.
    get supportsNvidia() {
      return this.allow("nvidia");
    },

    get elevationTerm() {
      return trOr(ELEVATION_TERM[this.os], ELEVATION_FALLBACK[this.os]);
    },

    // OS-resolved copy. Tries platform.<os>.<key>, then
    // platform.common.<key>, then the supplied fallback. Lets a string be
    // overridden per-OS without scattering conditionals through the views.
    copy(key, fallback) {
      const perOs = trOr("platform." + this.os + "." + key, "");
      if (perOs) return perOs;
      const common = trOr("platform.common." + key, "");
      if (common) return common;
      return fallback != null ? fallback : key;
    },

    // Friendly OS display label for badges / notices.
    get label() {
      return ({
        macos: "macOS", linux: "Linux", windows: "Windows",
        unknown: "—",
      })[this.os];
    },

    // Apply platform-conditional visibility to a DOM subtree. Honours:
    //   data-platforms="macos windows"   → show only on listed OS
    //   data-feature="nvidia"            → show only if Platform.allow(feature)
    // Defense-in-depth on top of the CSS adapter-only-* rules.
    applyTo(root) {
      root = root || document;
      const os = this.os;
      root.querySelectorAll("[data-platforms]").forEach(function (el) {
        const list = (el.dataset.platforms || "").split(/\s+/).filter(Boolean);
        el.hidden = list.length > 0 && list.indexOf(os) === -1;
      });
      root.querySelectorAll("[data-feature]").forEach((el) => {
        const feats = (el.dataset.feature || "").split(/\s+/).filter(Boolean);
        const ok = feats.every((f) => this.allow(f));
        el.hidden = !ok;
        if (!ok) el.setAttribute("aria-hidden", "true");
      });
    },
  };

  window.Platform = Platform;
})();
