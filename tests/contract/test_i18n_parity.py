"""Regression test: every EN i18n key must have a PL counterpart and vice versa.

Drives ``scripts/check-i18n-parity.py`` so the parity logic stays in one
place. Skipped on hosts without Node ≥ 18 (the script uses node to
evaluate the JS object literal directly).
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent.parent / "scripts" / "check-i18n-parity.py"


def test_i18n_en_pl_parity() -> None:
    if not shutil.which("node"):
        pytest.skip("node binary required for i18n parity check")
    r = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, (
        f"i18n parity check failed:\n--- stdout ---\n{r.stdout}\n"
        f"--- stderr ---\n{r.stderr}"
    )
    assert "OK" in r.stdout


def test_aitools_namespace_in_both_locales() -> None:
    """Quick targeted check: the new aitools.* namespace must exist in both."""
    src = (
        Path(__file__).resolve().parent.parent.parent
        / "app" / "frontend" / "i18n.js"
    ).read_text(encoding="utf-8")
    # Each locale block declares aitools as a top-level namespace, so the
    # pattern appears twice in the file (once per locale).
    assert src.count("\n    aitools: {") == 2, (
        f"expected aitools: namespace in both locales, "
        f"found {src.count(chr(10) + '    aitools: {')}"
    )
