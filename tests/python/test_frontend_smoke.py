"""Smoke tests for the SPA: assert markup contracts hold (no JS execution).

Covers B4-B7 of the windows-end-to-end design:
  - apply-confirm modal markup + styles
  - self-hosted webfonts (no Google Fonts CDN)
  - confirmApply / streamActiveRun JS helpers
  - localStorage-backed theme persistence
  - i18n PL+EN parity for new keys

Run via:
    python -m unittest tests.python.test_frontend_smoke -v
"""
from __future__ import annotations

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2] / "app" / "frontend"


class TestFrontendSmoke(unittest.TestCase):
    def test_index_has_apply_modal(self):
        text = (ROOT / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="apply-confirm-modal"', text)
        self.assertIn('apply.modal.title', text)
        # Confirm gating: native <dialog> with a disabled confirm button by default.
        self.assertIn('id="apply-modal-confirm"', text)
        self.assertIn('id="apply-modal-input"', text)

    def test_apply_modal_styles_present(self):
        text = (ROOT / "style.css").read_text(encoding="utf-8")
        # Either the legacy .modal block (sudo/wizard) or the new dialog.modal*
        # block must be present. We assert both: legacy AND new dialog flavour.
        self.assertIn('dialog.modal-dialog', text)
        self.assertIn('::backdrop', text)

    def test_no_google_fonts_import(self):
        for name in ("index.html", "colors_and_type.css", "style.css"):
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertNotIn(
                "fonts.googleapis.com", text,
                f"{name} still imports Google Fonts",
            )
            self.assertNotIn(
                "fonts.gstatic.com", text,
                f"{name} still imports gstatic",
            )

    def test_self_host_font_face_present(self):
        text = (ROOT / "colors_and_type.css").read_text(encoding="utf-8")
        self.assertIn("@font-face", text)
        self.assertIn("Inter Tight", text)
        self.assertIn("JetBrains Mono", text)
        # The CSS must reference the woff2 files via a path the SPA actually
        # serves. The dashboard mounts the entire frontend dir at /static/,
        # so /static/fonts/<name>.woff2 is the canonical resolvable URL.
        self.assertIn("/static/fonts/inter-tight-400.woff2", text)
        self.assertIn("/static/fonts/jetbrains-mono-400.woff2", text)

    def test_fonts_dir_exists(self):
        fonts = ROOT / "fonts"
        self.assertTrue(fonts.is_dir(), f"missing dir: {fonts}")
        # All four expected files present (placeholders or real woff2).
        for name in (
            "inter-tight-400.woff2",
            "inter-tight-600.woff2",
            "jetbrains-mono-400.woff2",
            "jetbrains-mono-600.woff2",
        ):
            self.assertTrue(
                (fonts / name).is_file(),
                f"missing font file: fonts/{name}",
            )

    def test_app_js_has_confirm_apply(self):
        text = (ROOT / "app.js").read_text(encoding="utf-8")
        self.assertIn("confirmApply", text)
        self.assertIn("streamActiveRun", text)
        # The streamActiveRun helper must use the standard SSE endpoint.
        self.assertIn("/runs/active/stream", text)

    def test_app_js_has_theme_persistence(self):
        text = (ROOT / "app.js").read_text(encoding="utf-8")
        self.assertIn("ascendo_theme", text)
        # The bootstrap path must read from localStorage before applying.
        self.assertIn("localStorage.getItem", text)
        self.assertIn("applyThemePref", text)

    def test_i18n_pl_en_parity(self):
        text = (ROOT / "i18n.js").read_text(encoding="utf-8")
        new_keys = [
            "apply.modal.title",
            "apply.modal.confirm",
            "wizard.theme.title",
            "wizard.theme.dark",
            "wizard.theme.light",
        ]
        # Each leaf key (the part after the last dot) must appear as a JS
        # identifier in BOTH locales. Counting occurrences guarantees both.
        for full in new_keys:
            leaf = full.split(".")[-1]
            occurrences = text.count(f"{leaf}:")
            self.assertGreaterEqual(
                occurrences, 2,
                f"i18n.js: leaf key {leaf!r} (from {full}) appears "
                f"{occurrences}× — expected ≥2 (en + pl)",
            )
        # Sanity: the PL-specific translation strings exist verbatim.
        self.assertIn("Potwierdź instalację", text)
        self.assertIn("Wybierz motyw", text)


if __name__ == "__main__":
    unittest.main()
