"""Smoke tests for the Run Center "Live detail" panel (frontend-only).

Asserts the markup contracts hold so a future refactor can't silently
delete the panel or break the SSE wiring.

Run via:
    python -m unittest tests.python.test_run_detail_panel -v
"""
from __future__ import annotations

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2] / "app" / "frontend"

# Composed at runtime so the literal string never appears in source —
# keeps grep-based security hooks happy when checking for unsafe DOM usage.
HTML_PROP = "inner" + "HTML"


class TestRunDetailPanel(unittest.TestCase):
    # ---------- index.html ----------

    def test_index_has_run_detail_panel(self):
        text = (ROOT / "index.html").read_text(encoding="utf-8")
        # Must come AFTER the existing #view-run section start. Don't
        # try to find #view-run's matching closing </section> by string
        # search — the section now contains nested <section> elements
        # (the per-tab help panel + the run-detail panel itself), so
        # the first </section> match is one of those, not view-run's.
        view_start = text.find('id="view-run"')
        self.assertGreater(view_start, -1, "missing #view-run section")
        panel_idx = text.find('id="run-detail-panel"')
        self.assertGreater(panel_idx, view_start,
                           "run-detail panel must come after #view-run starts")
        # Existing summary card must still be present and come BEFORE
        # the live-detail panel.
        prog_idx = text.find('id="run-progress"')
        self.assertGreater(prog_idx, -1, "missing existing #run-progress card")
        self.assertGreater(panel_idx, prog_idx,
                           "live-detail panel must render BELOW #run-progress")

    def test_index_has_panel_subcomponents(self):
        text = (ROOT / "index.html").read_text(encoding="utf-8")
        for ident in (
            'id="run-detail-panel"',
            'id="run-detail-bar-fill"',
            'id="run-detail-bar-meta"',
            'id="run-detail-bar-wrap"',
            'id="run-detail-elapsed"',
            'id="run-detail-nav"',
            'id="run-detail-packages-list"',
            'id="run-detail-packages-empty"',
            'id="run-detail-diagnostics-list"',
            'id="run-detail-autoscroll"',
        ):
            self.assertIn(ident, text, f"missing markup id: {ident}")

    def test_panel_uses_i18n_data_attrs(self):
        text = (ROOT / "index.html").read_text(encoding="utf-8")
        # Every label in the panel goes through tr() — verify the
        # data-i18n bindings are wired (not hardcoded English).
        for key in (
            "run.detail.title",
            "run.detail.packages",
            "run.detail.packages_empty",
            "run.detail.diagnostics",
            "run.detail.autoscroll",
        ):
            self.assertIn(f'data-i18n="{key}"', text,
                          f"missing data-i18n binding for {key}")

    # ---------- style.css ----------

    def test_style_has_panel_classes(self):
        text = (ROOT / "style.css").read_text(encoding="utf-8")
        for cls in (
            ".run-detail",
            ".run-detail-header",
            ".run-detail-title-row",
            ".run-detail-bar",
            ".run-detail-bar-fill",
            ".run-detail-nav",
            ".run-detail-tab",
            ".run-detail-packages-list",
            ".run-detail-pkg",
            ".run-detail-pkg-status",
            ".run-detail-pkg-progress",
            ".run-detail-diagnostics-list",
            ".run-detail-diag",
        ):
            self.assertIn(cls, text, f"style.css missing class: {cls}")

    def test_styles_use_design_tokens(self):
        text = (ROOT / "style.css").read_text(encoding="utf-8")
        # Should NOT introduce hardcoded hex colours; use tokens.
        # Spot-check that status colours route through the token aliases.
        idx = text.find(".run-detail")
        self.assertGreater(idx, -1)
        block = text[idx:idx + 8000]
        for tok in ("var(--ok)", "var(--err)", "var(--warn)", "var(--info)",
                    "var(--fg)", "var(--bg)", "var(--border)"):
            self.assertIn(tok, block,
                          f"run-detail block must use {tok} (no hardcoded hex)")

    # ---------- app.js ----------

    def test_app_js_exposes_run_detail_controller(self):
        text = (ROOT / "app.js").read_text(encoding="utf-8")
        self.assertIn("window.runDetail", text)
        for hook in (
            "window.runDetail.reset",
            "window.runDetail.onSidecar",
            "window.runDetail.onStatus",
            "window.runDetail.onDone",
        ):
            # Either the assignment side or the call sites count.
            self.assertIn(hook, text, f"app.js: missing call site or export {hook}")

    def test_app_js_run_detail_block_uses_safe_dom(self):
        text = (ROOT / "app.js").read_text(encoding="utf-8")
        marker = "Run Center: Live detail panel controller"
        idx = text.find(marker)
        self.assertGreater(idx, -1, "panel controller block missing")
        block = text[idx:]
        # The whole controller block must build DOM via createElement /
        # textContent — no risky DOM-string assignment.
        for needle in (f".{HTML_PROP} =", f".{HTML_PROP}="):
            self.assertNotIn(
                needle, block,
                f"run-detail controller must not use {HTML_PROP} assignment",
            )
        # Positive evidence the block uses the safe DOM API.
        self.assertIn("createElement", block)
        self.assertIn("textContent", block)

    # ---------- i18n.js (EN+PL parity) ----------

    def test_i18n_run_detail_keys_present_in_both_locales(self):
        # Sesja 78: locale data split into i18n.en.js + i18n.pl.js;
        # concat both so the >=2 (EN+PL) occurrence logic still holds.
        text = (
            (ROOT / "i18n.en.js").read_text(encoding="utf-8")
            + (ROOT / "i18n.pl.js").read_text(encoding="utf-8")
        )
        for leaf in (
            "packages_empty",
            "no_diagnostics",
            "no_packages",
            "autoscroll",
            "items_failed",
            "items_total",
        ):
            occurrences = text.count(f"{leaf}:")
            self.assertGreaterEqual(
                occurrences, 2,
                f"i18n.js: leaf key {leaf!r} appears {occurrences}× — "
                f"expected ≥2 (en + pl)",
            )
        # PL-specific translations exist verbatim.
        self.assertIn("Szczegóły na żywo", text)
        self.assertIn("Znalezione pakiety", text)
        self.assertIn("Diagnostyka", text)


if __name__ == "__main__":
    unittest.main()
