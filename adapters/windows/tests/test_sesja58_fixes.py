"""Regression tests for bugs found in the first real safe-update run after Sesja 58.

The bugs were:

1. ``AscendoNpm.psm1`` line ~178: ``$lines = $trimmed -split ... | Where-Object``
   pipeline collapsed to a scalar string under PowerShell's single-element
   auto-unwrap, then ``$lines.Count`` raised
   ``PropertyNotFoundException`` under ``Set-StrictMode -Version Latest``.
   Fix: wrap in ``@(...)`` to force array.

2. ``scripts/web/check.ps1`` (and other new .ps1 files): em-dash characters
   (U+2014) and other multi-byte UTF-8 punctuation inside string literals
   broke the PS5.1 parser because PowerShell 5.1 reads .ps1 files via the
   system code page (CP1252 on Windows), and the em-dash bytes decode to
   a closing quote, ending the string prematurely. Fix: replace all
   non-ASCII punctuation in Sesja 58 .ps1 / .psm1 files with ASCII
   equivalents (em-dash -> ``--``, ellipsis -> ``...``, etc.).

3. ``lib/handlers/*.ps1``: had ``Export-ModuleMember`` calls. When the
   handler is loaded via ``Import-Module path/to/foo.ps1``, PowerShell
   wraps the .ps1 in a transient module, but PS 5.1 still rejects the
   explicit ``Export-ModuleMember`` call with "can only be called from
   inside a module". Fix: remove ``Export-ModuleMember`` from all handler
   .ps1 files (functions are auto-exported from transient modules).

4. ``AscendoWeb.psm1``: ``Invoke-WebRegistry`` preferred ``python``
   over ``py`` for Python discovery. On Windows boxes with multiple
   Python installs, ``python`` often resolves to an older 3.x without
   pydantic / ascendo_windows in site-packages, while ``py`` (Python
   launcher) picks the highest-installed Python. Fix: reverse discovery
   order to ``('py', 'python3', 'python')``. Plus the validate branch
   was swallowing stderr/stdout via ``*> $null`` so callers never saw
   ImportError. Fix: capture output, re-throw with context on non-zero
   exit.

5. ``lib/web_registry.py``: imported ``from ascendo_windows.web_registry
   import WebRegistryV2`` without bootstrapping ``sys.path``. When called
   by a Python that doesn't have the editable install (or a different
   Python than the one the user typed ``pip install`` against), the
   import failed with ModuleNotFoundError. Fix: prepend
   ``adapters/windows/`` and ``core/`` to ``sys.path`` from the shim
   itself, derived from ``__file__``.

These tests are static-analysis only — they read the relevant files as
text and assert the fixes are in place. No subprocess spawning, so they
run identically on any platform.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
ADAPTER_ROOT = REPO_ROOT / "adapters" / "windows"

# Files introduced or substantially edited in Sesja 58 that must stay ASCII-clean.
SESJA58_PS_FILES = [
    ADAPTER_ROOT / "lib" / "AscendoNpm.psm1",
    ADAPTER_ROOT / "lib" / "AscendoPip.psm1",
    ADAPTER_ROOT / "lib" / "AscendoWeb.psm1",
    ADAPTER_ROOT / "lib" / "handlers" / "builtin.ps1",
    ADAPTER_ROOT / "lib" / "handlers" / "github_release.ps1",
    ADAPTER_ROOT / "lib" / "handlers" / "release_feed.ps1",
    ADAPTER_ROOT / "scripts" / "npm" / "check.ps1",
    ADAPTER_ROOT / "scripts" / "npm" / "plan.ps1",
    ADAPTER_ROOT / "scripts" / "npm" / "apply.ps1",
    ADAPTER_ROOT / "scripts" / "npm" / "verify.ps1",
    ADAPTER_ROOT / "scripts" / "npm" / "cleanup.ps1",
    ADAPTER_ROOT / "scripts" / "pip" / "check.ps1",
    ADAPTER_ROOT / "scripts" / "pip" / "plan.ps1",
    ADAPTER_ROOT / "scripts" / "pip" / "apply.ps1",
    ADAPTER_ROOT / "scripts" / "pip" / "verify.ps1",
    ADAPTER_ROOT / "scripts" / "pip" / "cleanup.ps1",
    ADAPTER_ROOT / "scripts" / "web" / "check.ps1",
    ADAPTER_ROOT / "scripts" / "web" / "plan.ps1",
    ADAPTER_ROOT / "scripts" / "web" / "apply.ps1",
    ADAPTER_ROOT / "scripts" / "web" / "verify.ps1",
    ADAPTER_ROOT / "scripts" / "web" / "cleanup.ps1",
    REPO_ROOT / "bin" / "ascendo-web-start.ps1",
    REPO_ROOT / "bin" / "ascendo-web-stop.ps1",
    REPO_ROOT / "bin" / "ascendo-web-restart.ps1",
    REPO_ROOT / "bin" / "ascendo-web-status.ps1",
    REPO_ROOT / "bin" / "build-inventory.ps1",
]

# Punctuation that decodes ambiguously between UTF-8 and Windows-1252 and
# breaks PS5.1 parsing when inside a string literal. The em-dash (U+2014)
# is the historical offender; the rest are belt-and-suspenders.
DANGEROUS_CHARS = {
    "—": "em-dash -> '--'",
    "–": "en-dash -> '-'",
    "…": "ellipsis -> '...'",
    "‘": "left single quote -> ASCII apostrophe",
    "’": "right single quote -> ASCII apostrophe",
    "“": "left double quote -> ASCII double-quote",
    "”": "right double quote -> ASCII double-quote",
    " ": "non-breaking space -> regular space",
    "→": "right arrow -> '->'",
    "←": "left arrow -> '<-'",
}


@pytest.mark.parametrize(
    "path",
    SESJA58_PS_FILES,
    ids=lambda p: str(p.relative_to(REPO_ROOT)).replace("\\", "/"),
)
def test_no_dangerous_unicode_in_sesja58_ps_files(path: Path) -> None:
    """PS5.1 reads BOM-less .ps1 via system code page (CP1252), and several
    UTF-8 multibyte chars decode to closing quotes. The Sesja 58 first run
    hit this exact bug on ``scripts/web/check.ps1:252`` — the em-dash bytes
    ended a string early, leaving 'vendor' as an unexpected token.
    """
    assert path.exists(), f"file moved/renamed: {path}"
    text = path.read_text(encoding="utf-8")
    bad: list[tuple[str, str, int]] = []
    for ch, hint in DANGEROUS_CHARS.items():
        idx = text.find(ch)
        if idx >= 0:
            line_no = text[:idx].count("\n") + 1
            bad.append((ch, hint, line_no))
    assert not bad, (
        f"{path.relative_to(REPO_ROOT)} contains non-ASCII punctuation that "
        f"breaks PS5.1 parsing under CP1252:\n"
        + "\n".join(
            f"  U+{ord(ch):04X} on line {line}: {hint}"
            for ch, hint, line in bad
        )
    )


def test_ascendonpm_get_latest_wraps_split_in_array() -> None:
    """Bug 1: ``$lines = $trimmed -split '...' | Where-Object`` collapses to
    scalar on single-element pipeline; ``$lines.Count`` fails StrictMode.
    Fix is to wrap with ``@(...)`` to force array.
    """
    path = ADAPTER_ROOT / "lib" / "AscendoNpm.psm1"
    text = path.read_text(encoding="utf-8")
    # The hardened line should be ``@(... -split ... | Where-Object ...)``.
    # We look for the wrapped form before checking the unwrapped form is
    # NOT present in Get-AscendoNpmLatestVersion.
    pattern = re.compile(
        r"\$lines\s*=\s*@\(\s*\$trimmed\s+-split\s+\"`r\?`n\"\s+\|"
        r"\s*Where-Object\s+\{[^}]*\}\s*\)",
        re.MULTILINE,
    )
    assert pattern.search(text), (
        "AscendoNpm.psm1 must wrap the -split | Where-Object pipeline "
        "in @(...) so single-element output stays an array under StrictMode."
    )
    # Defensive: the unwrapped variant must NOT appear anywhere.
    unwrapped = re.compile(
        r"\$lines\s*=\s*\$trimmed\s+-split\b[^@]",
        re.MULTILINE,
    )
    assert not unwrapped.search(text), (
        "Found unwrapped `$lines = $trimmed -split ...` — would regress "
        "the StrictMode .Count bug."
    )


@pytest.mark.parametrize(
    "handler",
    [
        ADAPTER_ROOT / "lib" / "handlers" / "builtin.ps1",
        ADAPTER_ROOT / "lib" / "handlers" / "github_release.ps1",
        ADAPTER_ROOT / "lib" / "handlers" / "release_feed.ps1",
    ],
    ids=lambda p: p.name,
)
def test_handler_files_have_no_export_modulemember(handler: Path) -> None:
    """Bug 3: handler .ps1 files were loaded via ``Import-Module foo.ps1``
    which creates a transient module but doesn't permit
    ``Export-ModuleMember`` calls inside (PS 5.1 raises
    InvalidOperationException). Functions defined at file scope are
    auto-exported from a transient .ps1 module, so the explicit call is
    redundant and harmful. Fix: drop all Export-ModuleMember from
    handler .ps1 files.
    """
    assert handler.exists()
    text = handler.read_text(encoding="utf-8")
    # Strip comments (everything after `#` on each line) before searching,
    # so the docstring comment that mentions Export-ModuleMember doesn't
    # trigger the regression detector.
    stripped = re.sub(r"#[^\n]*", "", text)
    assert "Export-ModuleMember" not in stripped, (
        f"{handler.name}: must not contain Export-ModuleMember "
        f"(handler .ps1 files are loaded as transient modules; explicit "
        f"export call raises in PS5.1)"
    )


def test_ascendoweb_python_discovery_prefers_py_launcher() -> None:
    """Bug 4: discovery order ``('python', 'python3', 'py')`` picks the
    bare ``python`` first, which on multi-install Windows boxes is often
    an older 3.x install that lacks ``pydantic`` / ``ascendo_windows``.
    The ``py`` launcher always picks the highest installed Python. Fix:
    reorder to ``('py', 'python3', 'python')``.
    """
    path = ADAPTER_ROOT / "lib" / "AscendoWeb.psm1"
    text = path.read_text(encoding="utf-8")
    # Find the discovery foreach line and assert order is py / python3 / python.
    match = re.search(
        r"foreach\s*\(\s*\$cand\s+in\s+@\(([^\)]+)\)\s*\)",
        text,
    )
    assert match, "Could not find Python discovery foreach in AscendoWeb.psm1"
    candidates_raw = match.group(1)
    # Pull the literal candidates from the list (e.g. 'py', 'python3', 'python').
    candidates = re.findall(r"'([^']+)'", candidates_raw)
    assert candidates and candidates[0] == "py", (
        f"AscendoWeb.psm1 Python discovery must start with 'py' (got {candidates})"
    )


def test_apply_scripts_pass_hashtable_not_ordered_dict_to_messages_param() -> None:
    """Bug 6 (post-Sesja-58 follow-up): npm/apply.ps1 + pip/apply.ps1 built
    the per-item ``-Messages`` argument array as ``[ordered]@{level=...;text=...}``
    entries. AscendoJson.psm1 Add-SidecarItem -Messages requires plain
    ``[hashtable]`` instances (its validator at line ~538 throws
    "Each entry in -Messages must be a hashtable with 'level' and 'text'."
    when handed an OrderedDictionary).

    The bug only fires when npm/pip apply hits a per-package install failure
    (rc != 0), so it was invisible until the first real safe-update run
    against multiple packages where at least one couldn't install.

    Regression check: scan every Windows ``apply.ps1`` for
    ``$finalArgs['Messages']`` assignments and assert the value array
    only contains ``@{...}`` entries, NOT ``[ordered]@{...}``.

    Why this scan and not a full PS unit test: the bug is purely about
    the OrderedDictionary-vs-Hashtable type distinction at the param-
    binding boundary, and static text scanning catches it deterministically
    without needing a running PowerShell.
    """
    apply_scripts = list(
        (REPO_ROOT / "adapters" / "windows" / "scripts").glob("*/apply.ps1")
    )
    assert apply_scripts, "no apply.ps1 scripts found"

    # Find every `$finalArgs['Messages'] = @(...)` or `$itemArgs['Messages'] = @(...)`
    # block. Detect [ordered]@{ inside that array literal — it'd be the regression.
    pattern = re.compile(
        r"\$\w+\[['\"]Messages['\"]\]\s*=\s*@\((.*?)\)",
        re.MULTILINE | re.DOTALL,
    )
    offenders: list[tuple[str, int]] = []
    for script in apply_scripts:
        text = script.read_text(encoding="utf-8")
        for match in pattern.finditer(text):
            block = match.group(1)
            if "[ordered]" in block:
                line_no = text[: match.start()].count("\n") + 1
                offenders.append((str(script.relative_to(REPO_ROOT)), line_no))

    assert not offenders, (
        "Found [ordered]@{} inside -Messages assignment(s) — these will be "
        "rejected by Add-SidecarItem's hashtable validator at apply-time:\n"
        + "\n".join(f"  {path}:{line}" for path, line in offenders)
    )


def test_web_registry_shim_self_bootstraps_sys_path() -> None:
    """Bug 5: ``lib/web_registry.py`` imports ``from ascendo_windows....``
    which fails when the calling Python doesn't have the editable install.
    Fix: bootstrap sys.path from __file__ before importing project deps.
    """
    path = ADAPTER_ROOT / "lib" / "web_registry.py"
    text = path.read_text(encoding="utf-8")
    # Look for the sys.path insertion pattern (any reasonable form).
    has_bootstrap = (
        "sys.path.insert" in text
        and "_ADAPTER_DIR" in text or "adapters" in text.split("from ascendo_windows")[0]
    )
    assert has_bootstrap, (
        "lib/web_registry.py must bootstrap sys.path before importing "
        "ascendo_windows.web_registry so the shim works from any Python."
    )
    # Verify the bootstrap happens BEFORE the `from ascendo_windows...` import,
    # not after.
    sys_path_idx = text.find("sys.path.insert")
    import_idx = text.find("from ascendo_windows")
    assert sys_path_idx > -1 and import_idx > -1
    assert sys_path_idx < import_idx, (
        "sys.path bootstrap must precede the ascendo_windows import "
        "(otherwise the import fails before the bootstrap runs)."
    )
