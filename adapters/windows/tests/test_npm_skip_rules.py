"""Static-analysis regression tests for the npm skip-rule.

Operator observation on DP5520WMK (run a925b9f5, 2026-05-13 07:38 UTC):
the npm apply phase fired ``npm install -g npx@latest`` against the
legacy standalone ``npx@10.x`` registry package, which failed with
``EEXIST: file already exists`` because the ``npx.cmd`` shim is already
present (npm itself ships it since npm 7 in 2020).

Two-layer fix:
  1. Remove ``npx`` from the shipped manifest (analog of the existing
     "we don't track Node itself" policy already documented in the
     manifest header).
  2. Add ``npx`` to ``Test-AscendoNpmShouldSkip`` so a re-addition
     can't reintroduce the EEXIST (parity with the
     ``Test-AscendoPipShouldSkip`` rule for pip/setuptools/wheel).

These are pure text checks: they pin the source files so a future
refactor that drops either layer fails CI loudly.
"""
from __future__ import annotations

from pathlib import Path

import pytest


ADAPTER_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ADAPTER_ROOT / "config" / "npm_global_clis.txt"
PSM1_PATH = ADAPTER_ROOT / "lib" / "AscendoNpm.psm1"
APPLY_PS1 = ADAPTER_ROOT / "scripts" / "npm" / "apply.ps1"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _manifest_packages() -> list[str]:
    """Return the package names declared in the shipped manifest.

    Mirrors Read-AscendoNpmManifest: strips ``#`` comments and blank lines.
    """
    packages: list[str] = []
    for raw in _read(MANIFEST_PATH).splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            packages.append(line)
    return packages


def test_npx_not_in_shipped_manifest() -> None:
    """``npx`` must NOT appear as a tracked package.

    Root-cause fix for the EEXIST failure observed on 2026-05-13: npm
    ships the ``npx.cmd`` shim itself, so ``npm install -g npx`` can't
    overwrite it.
    """
    pkgs = _manifest_packages()
    assert "npx" not in pkgs, (
        "npx must not be in npm_global_clis.txt -- it is a CLI bundled "
        "with npm itself (npm install -g npx fails with EEXIST)."
    )


def test_manifest_documents_npx_exclusion() -> None:
    """Manifest header must explain WHY npx is excluded.

    Future maintainers (human or otherwise) need to see the reasoning
    before re-adding npx based on familiarity with older npm versions.
    """
    text = _read(MANIFEST_PATH)
    assert "npx" in text.lower(), (
        "Manifest should explicitly document why npx is excluded so "
        "future readers don't re-add it."
    )
    assert "bundled" in text.lower() or "EEXIST" in text, (
        "Header should mention the bundled-with-npm policy or the "
        "EEXIST failure mode."
    )


def test_should_skip_rejects_npx() -> None:
    """``Test-AscendoNpmShouldSkip`` source must reference 'npx'.

    Defense-in-depth: if a user re-adds npx to a custom manifest, the
    apply phase should skip rather than fail with EEXIST.
    """
    text = _read(PSM1_PATH)
    # Locate the function block.
    needle = "function Test-AscendoNpmShouldSkip"
    assert needle in text, f"{needle} must exist in AscendoNpm.psm1"
    block = text.split(needle, 1)[1].split("function ", 1)[0]
    assert "'npx'" in block, (
        "Test-AscendoNpmShouldSkip must compare against 'npx' (current "
        "skip-list rule for the bundled-shim case)."
    )


def test_should_skip_is_case_insensitive() -> None:
    """Skip rule must lowercase before comparing -- npm package names are
    case-insensitive on the registry, and the manifest accepts any
    casing. Mirrors the pip skip-rule shape.
    """
    text = _read(PSM1_PATH)
    block = (
        text.split("function Test-AscendoNpmShouldSkip", 1)[1]
            .split("function ", 1)[0]
    )
    assert "ToLowerInvariant" in block, (
        "Skip-rule should canonicalise input via ToLowerInvariant before "
        "comparing -- same as Test-AscendoPipShouldSkip."
    )


def test_apply_ps1_still_wires_should_skip() -> None:
    """Pin: apply.ps1 must continue to call Test-AscendoNpmShouldSkip.

    If a future refactor moves the skip-call elsewhere or removes it,
    the skip rule becomes inert; this test catches that drift.
    """
    text = _read(APPLY_PS1)
    assert "Test-AscendoNpmShouldSkip" in text, (
        "scripts/npm/apply.ps1 must still call Test-AscendoNpmShouldSkip"
        " -- otherwise the npx skip rule has no effect."
    )


@pytest.mark.parametrize(
    "expected_pkg",
    ["npm", "pnpm", "yarn", "@anthropic-ai/claude-code", "typescript"],
)
def test_other_core_packages_remain(expected_pkg: str) -> None:
    """The 'remove npx' edit must not have collateral damage on its
    neighbouring lines (npm/pnpm/yarn block + AI CLIs).
    """
    pkgs = _manifest_packages()
    assert expected_pkg in pkgs, (
        f"{expected_pkg} should still be in the manifest -- removing npx "
        "must not have removed its neighbours."
    )
