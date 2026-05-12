"""Regression test for the Sesja 57 bidirectional ``from=``/``to=`` bug class.

On macOS/Ubuntu, every "present" inventory item must emit BOTH
``current_version`` AND ``target_version`` (mapped to the SPA's ``installed``
and ``candidate`` columns). On Windows the same rule applies: any PowerShell
``Add-SidecarItem`` call that flags ``Status = 'up_to_date'`` MUST set both
``CurrentVersion`` and ``TargetVersion`` on its ``$itemArgs`` hashtable.

If only ``CurrentVersion`` is set, the SPA overlay reads ``current_version``
into ``installed`` but leaves ``candidate`` as ``None`` — and the row paints
as "candidate unknown" / "outdated/unknown" instead of "up to date".

Mirrors the canonical macOS pattern in
``adapters/macos/scripts/brew/check.sh::_emit_up_to_date``:

    json_add_item "$_id" "$_ver" "$_ver" "up_to_date" "brew" "$_bucket"
    #                      ^^^^^^  ^^^^^^
    #                      from=   to=    — same value for up_to_date items

This test is **static analysis** on the .ps1 source files: it scans for
``$itemArgs = @{ ... }`` blocks that contain ``Status = 'up_to_date'`` and
asserts both ``CurrentVersion`` and ``TargetVersion`` keys appear in the
span between the hashtable opening and its consuming
``Add-SidecarItem @itemArgs`` call. Some scripts conditionally set the
version fields after the initial hashtable (e.g.
``$itemArgs['CurrentVersion'] = $current``), so the scan covers post-
hashtable assignments too.

Platform-agnostic — no pwsh required. Runs in CI on any OS.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


# Path setup — tests live at adapters/windows/tests/, scripts at
# adapters/windows/scripts/.
_TESTS_DIR = Path(__file__).resolve().parent
_WINDOWS_ADAPTER = _TESTS_DIR.parent
_SCRIPTS = _WINDOWS_ADAPTER / "scripts"

# The 5 check scripts in scope of the bidirectional from=/to= invariant.
# Web check.ps1 was added in the WebManager v1 (handler=github_release |
# release_feed | builtin) and must satisfy the same rule: every emission
# whose Status is 'up_to_date' must set both CurrentVersion AND
# TargetVersion (otherwise the SPA overlay reads candidate=None and
# paints the row as "candidate unknown").
CHECK_SCRIPTS = [
    _SCRIPTS / "winget" / "check.ps1",
    _SCRIPTS / "msstore" / "check.ps1",
    _SCRIPTS / "arp" / "check.ps1",
    _SCRIPTS / "windows_update" / "check.ps1",
    _SCRIPTS / "web" / "check.ps1",
]

# Inventory uses Status='success' to flag present-installed items — same
# bug class (no candidate version emitted = SPA overlay flips to unknown).
INVENTORY_SCRIPT = _SCRIPTS / "inventory" / "list.ps1"


def _extract_itemargs_spans(
    text: str,
    *,
    status_value: str,
) -> list[tuple[int, str]]:
    """Return ``(start_line, span_text)`` for every ``$itemArgs = @{ ... }``
    block whose hashtable body OR subsequent assignments include
    ``Status = '<status_value>'``, up to the closing
    ``Add-SidecarItem @itemArgs`` call.

    We scan post-hashtable lines too because some scripts (e.g. winget/
    apply.ps1's skip path) bind ``CurrentVersion`` / ``TargetVersion`` via
    ``$itemArgs['CurrentVersion'] = $current`` AFTER the initial @{...}
    block — those still count for the bidirectional contract.
    """
    spans: list[tuple[int, str]] = []
    lines = text.splitlines()

    # Find every line that starts an ``$itemArgs = @{`` block.
    starts: list[int] = []
    for idx, line in enumerate(lines):
        if re.search(r"\$itemArgs\s*=\s*@\{", line):
            starts.append(idx)

    for start_idx in starts:
        # Walk forward, tracking brace depth, to find the matching ``}``
        # that closes the hashtable. Then keep walking until we see
        # ``Add-SidecarItem @itemArgs`` (the consumer) OR another
        # ``$itemArgs = @{`` (next block — defensive).
        depth = 0
        hashtable_end_idx = start_idx  # fallback
        for idx in range(start_idx, len(lines)):
            depth += lines[idx].count("{") - lines[idx].count("}")
            if depth == 0 and idx > start_idx:
                hashtable_end_idx = idx
                break

        # Walk to the Add-SidecarItem @itemArgs call (allows trailing
        # conditional assignments like ``$itemArgs['Key'] = $value``).
        consumer_idx = hashtable_end_idx
        for idx in range(hashtable_end_idx, len(lines)):
            if re.search(r"Add-SidecarItem\s+@itemArgs", lines[idx]):
                consumer_idx = idx
                break
            # Defensive: stop at the next $itemArgs = @{ block (means we
            # missed the consumer somehow — emit what we have).
            if idx > hashtable_end_idx and re.search(
                r"\$itemArgs\s*=\s*@\{", lines[idx]
            ):
                consumer_idx = idx - 1
                break

        span_lines = lines[start_idx : consumer_idx + 1]
        span_text = "\n".join(span_lines)
        # Only retain spans whose span_text mentions the target status.
        status_pat = rf"Status\s*=\s*['\"]{re.escape(status_value)}['\"]"
        if re.search(status_pat, span_text):
            spans.append((start_idx + 1, span_text))  # 1-indexed line
    return spans


def _span_has_both_versions(span_text: str) -> tuple[bool, bool]:
    """Return ``(has_current, has_target)`` — True if the span sets each
    key either as a hashtable entry or via post-construction assignment.
    """
    # Hashtable-entry style: ``CurrentVersion = ...`` (with surrounding
    # whitespace inside the hashtable). Post-construction style:
    # ``$itemArgs['CurrentVersion'] = ...`` or ``$itemArgs.CurrentVersion = ...``
    current_pat = (
        r"(?:CurrentVersion\s*=)"
        r"|(?:\$itemArgs\[['\"]CurrentVersion['\"]\]\s*=)"
        r"|(?:\$itemArgs\.CurrentVersion\s*=)"
    )
    target_pat = (
        r"(?:TargetVersion\s*=)"
        r"|(?:\$itemArgs\[['\"]TargetVersion['\"]\]\s*=)"
        r"|(?:\$itemArgs\.TargetVersion\s*=)"
    )
    return (
        bool(re.search(current_pat, span_text)),
        bool(re.search(target_pat, span_text)),
    )


@pytest.mark.parametrize(
    "script_path",
    CHECK_SCRIPTS,
    ids=lambda p: f"{p.parent.name}/{p.name}",
)
def test_up_to_date_items_emit_both_current_and_target_version(
    script_path: Path,
) -> None:
    """Every ``Status = 'up_to_date'`` emission in the 4 Windows check.ps1
    scripts must set BOTH CurrentVersion AND TargetVersion (bidirectional
    from=/to= for the SPA overlay).

    This is the mirror of the Sesja 57 macOS/Ubuntu fix: without
    TargetVersion, ``candidate`` is ``None`` and the row paints as
    "candidate unknown" instead of "up to date".
    """
    assert script_path.exists(), f"missing fixture: {script_path}"
    text = script_path.read_text(encoding="utf-8")

    spans = _extract_itemargs_spans(text, status_value="up_to_date")
    assert spans, (
        f"{script_path}: no $itemArgs blocks with Status='up_to_date' "
        f"found — has the script been refactored? Re-validate manually."
    )

    failures: list[str] = []
    for start_line, span in spans:
        has_current, has_target = _span_has_both_versions(span)
        if not (has_current and has_target):
            failures.append(
                f"{script_path}:{start_line} — up_to_date emission missing "
                f"version key(s): "
                f"CurrentVersion={'set' if has_current else 'MISSING'}, "
                f"TargetVersion={'set' if has_target else 'MISSING'}. "
                f"\n--- span ---\n{span}\n--- end span ---"
            )

    if failures:
        joined = "\n\n".join(failures)
        pytest.fail(
            f"Sesja 57 bidirectional from=/to= regression in {script_path.name}:\n\n"
            f"{joined}\n\n"
            f"Every up_to_date item must echo CurrentVersion into TargetVersion "
            f"(same value) so the SPA overlay reads candidate == installed "
            f"instead of leaving candidate=None and painting the row as "
            f"'candidate unknown'."
        )


def test_inventory_success_items_emit_both_current_and_target_version() -> None:
    """``inventory/list.ps1`` emits ``Status = 'success'`` for every
    present-installed package — same bug class as up_to_date in check.ps1.
    Must set both CurrentVersion AND TargetVersion.
    """
    assert INVENTORY_SCRIPT.exists(), f"missing fixture: {INVENTORY_SCRIPT}"
    text = INVENTORY_SCRIPT.read_text(encoding="utf-8")
    spans = _extract_itemargs_spans(text, status_value="success")
    assert spans, (
        "inventory/list.ps1: no $itemArgs blocks with Status='success' "
        "found — has the script been refactored? Re-validate manually."
    )
    failures: list[str] = []
    for start_line, span in spans:
        has_current, has_target = _span_has_both_versions(span)
        if not (has_current and has_target):
            failures.append(
                f"{INVENTORY_SCRIPT}:{start_line} — success emission missing "
                f"version key(s): "
                f"CurrentVersion={'set' if has_current else 'MISSING'}, "
                f"TargetVersion={'set' if has_target else 'MISSING'}. "
                f"\n--- span ---\n{span}\n--- end span ---"
            )
    if failures:
        joined = "\n\n".join(failures)
        pytest.fail(
            "Sesja 57 bidirectional from=/to= regression in inventory/list.ps1:\n\n"
            f"{joined}\n\n"
            "Every present-installed inventory item must echo CurrentVersion "
            "into TargetVersion (same value) so the SPA candidate column "
            "matches installed instead of painting blank."
        )
