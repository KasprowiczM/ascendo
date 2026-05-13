"""Static-analysis regression tests for the ``return ,$arr.ToArray()``
PowerShell anti-pattern.

Background: when a PowerShell function returns ``,$arr.ToArray()`` and
the caller wraps with ``@()`` (as in ``@(Invoke-AscendoWebDiscovery)``
or ``@(Install-WindowsUpdateBatch ...)``), PowerShell ends up with a
**1-element outer array containing the inner N-element array**. The
loop ``foreach ($d in $discovered)`` then iterates ONCE with ``$d``
being the inner array. Member access on ``$d`` (e.g. ``$d.Name``)
fans out across every element and PowerShell joins the result into a
single space-separated string.

Operator observation on DP5520WMK (run a925b9f5, 2026-05-13):
  - ``check__web.json`` collapsed ~90 registry-discovered apps into ONE
    sidecar item with ``name = "AutoHotkey CCleaner Dell Display ..."``,
    ``current_version = "2.0.26 6.35 2.3.1.16 ..."``.
  - Earlier (run f3f9d20f, 07:05 UTC) the same idiom in
    ``Install-WindowsUpdateBatch`` collapsed 2 PSWindowsUpdate result
    rows into ``"KB5087051 KB5089549"`` / ``Result="Installed Installed"``,
    which then mis-classified as ``failed`` via the anchored regex.

The fix is one character per file: drop the comma so PowerShell
enumerates the array on the output stream and ``@()`` collects N
items correctly.

These tests pin the source so a future refactor doesn't reintroduce
the comma. They are pure text checks (no PowerShell execution) so they
run on any OS.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


ADAPTER_ROOT = Path(__file__).resolve().parents[1]


# Files where ``return ,$something.ToArray()`` was the source of bugs
# observed in production. Each one must NOT contain the bad idiom.
_PSM1_FILES_THAT_MUST_NOT_HAVE_COMMA_RETURN = [
    ADAPTER_ROOT / "lib" / "AscendoWebDiscovery.psm1",
    ADAPTER_ROOT / "lib" / "AscendoPSWindowsUpdate.psm1",
]

_BAD_RETURN_PATTERN = re.compile(r"^\s*return\s*,\s*\$", re.MULTILINE)


@pytest.mark.parametrize("path", _PSM1_FILES_THAT_MUST_NOT_HAVE_COMMA_RETURN)
def test_no_comma_return_in_lib_module(path: Path) -> None:
    """``return ,$arr.ToArray()`` must not appear in lib modules used
    by the phase scripts.

    The lib modules are imported via ``Import-Module ... -Force``
    inside ``scripts/*/<phase>.ps1``; those callers typically wrap
    function results in ``@()`` to force-array-shape, which triggers
    the collapse bug.

    Found by reverse-engineering the ``check__web.json`` collapsed-row
    sidecar after the operator-visible "web lists all apps as one
    entry" complaint on 2026-05-13.
    """
    text = path.read_text(encoding="utf-8")
    hits = list(_BAD_RETURN_PATTERN.finditer(text))
    if hits:
        line_numbers = []
        for h in hits:
            line_no = text[: h.start()].count("\n") + 1
            line_numbers.append(line_no)
        raise AssertionError(
            f"{path.name} reintroduces the comma-return anti-pattern at "
            f"line(s) {line_numbers}. Use ``return $arr.ToArray()`` (no "
            f"comma) so @() callers enumerate correctly. See the file's "
            f"own comment block for the full rationale."
        )


def test_webdiscovery_returns_array_to_array() -> None:
    """Positive assertion: the fix is the plain ``return ... .ToArray()``
    form. If a refactor renames ``$results`` or restructures the body,
    this test still passes -- it just requires a ``.ToArray()`` return.
    """
    text = (ADAPTER_ROOT / "lib" / "AscendoWebDiscovery.psm1").read_text(
        encoding="utf-8",
    )
    # Must contain the plain enumeration return inside Invoke-AscendoWebDiscovery.
    needle = "Invoke-AscendoWebDiscovery"
    assert needle in text
    # Find the function body's last return statement; it must be the
    # plain ``return $X.ToArray()`` form.
    body = text.split(needle, 1)[1]
    plain_returns = re.findall(r"return\s+\$[A-Za-z_][\w]*\.ToArray\(\)", body)
    assert plain_returns, (
        "Invoke-AscendoWebDiscovery must return $arr.ToArray() (without "
        "leading comma operator) for the @() caller idiom to work."
    )


def test_pswindowsupdate_fns_return_array_to_array() -> None:
    """``Get-PendingWindowsUpdates``, ``Install-WindowsUpdateBatch`` and
    ``Expand-WUAggregatedRow`` all return PSCustomObject lists; their
    apply.ps1 callers all wrap with ``@()``. Must be plain returns.
    """
    text = (ADAPTER_ROOT / "lib" / "AscendoPSWindowsUpdate.psm1").read_text(
        encoding="utf-8",
    )
    plain_returns = re.findall(r"return\s+\$[A-Za-z_][\w]*\.ToArray\(\)", text)
    # Three sites: Expand-WUAggregatedRow, Get-PendingWindowsUpdates,
    # Install-WindowsUpdateBatch. Each must be plain.
    assert len(plain_returns) >= 3, (
        f"Expected >=3 plain-return ``return $arr.ToArray()`` sites in "
        f"AscendoPSWindowsUpdate.psm1; found {len(plain_returns)}. The "
        f"comma-collapse fix may have been partially reverted."
    )
