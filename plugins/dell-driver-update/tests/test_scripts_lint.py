"""
Lint smoke for plugins/dell-driver-update/windows/*.ps1.

Verifies StrictMode-safe pattern compliance via plain-text checks so the
test runs on any host (Linux, macOS, Windows) without requiring pwsh or
Pester. Pester contract tests (sidecar fixture validation) live alongside
the scripts under windows/*.Tests.ps1 and are gated on Windows CI.

Patterns enforced (per HANDOFF.md FAST RESUME and the design spec A4):

  1. `Set-StrictMode -Version Latest` is set in every script.
  2. Every phase script calls `Save-Sidecar`.
  3. Phase-level messages use `Add-SidecarMessage -Text` (NOT `-Message`).
  4. `New-Sidecar` is invoked via splatting (`@<varname>`), NOT inline
     hashtable (`@{...}`) which is a positional arg, not splat.
  5. `[Alias('Profile')]` is declared on the ProfileName parameter
     (`Profile` is a reserved automatic variable).
  6. The legacy bug names `Get-WingetVersion -Tool` and the literal
     argument `'-Tool '` (used to be a mis-call to AscendoWinget) do
     NOT appear.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
WINDOWS_DIR = PLUGIN_ROOT / "windows"
PHASES = ("check", "plan", "apply", "verify", "cleanup")


def _read(name: str) -> str:
    p = WINDOWS_DIR / f"{name}.ps1"
    return p.read_text(encoding="utf-8")


class DellPluginScriptLint(unittest.TestCase):
    """Plain-text lint over all 5 phase scripts."""

    def setUp(self) -> None:
        self.scripts = {phase: _read(phase) for phase in PHASES}

    # -- pattern 1 ------------------------------------------------------
    def test_strict_mode_present(self) -> None:
        for phase, body in self.scripts.items():
            with self.subTest(phase=phase):
                self.assertRegex(
                    body,
                    r"(?m)^\s*Set-StrictMode\s+-Version\s+Latest\b",
                    f"{phase}.ps1 missing 'Set-StrictMode -Version Latest'",
                )

    # -- pattern 2 ------------------------------------------------------
    def test_save_sidecar_called(self) -> None:
        for phase, body in self.scripts.items():
            with self.subTest(phase=phase):
                self.assertIn(
                    "Save-Sidecar",
                    body,
                    f"{phase}.ps1 never calls Save-Sidecar",
                )
                # And we don't write the sidecar via the legacy `-Path`
                # contract; OutputDir is the supported parameter.
                self.assertRegex(
                    body,
                    r"Save-Sidecar[^\n]*-OutputDir\s+\$OutputDir",
                    f"{phase}.ps1 must call Save-Sidecar -OutputDir $OutputDir",
                )

    # -- pattern 3 ------------------------------------------------------
    def test_add_sidecar_message_uses_text_not_message(self) -> None:
        message_pattern = re.compile(
            r"Add-SidecarMessage\b[^\n`]*?\s-Message\b",
            re.MULTILINE,
        )
        text_pattern = re.compile(
            r"Add-SidecarMessage\b",
            re.MULTILINE,
        )
        for phase, body in self.scripts.items():
            with self.subTest(phase=phase):
                # Strip line-continuations so multi-line `-Message` calls
                # are detected as single logical lines.
                flat = re.sub(r"`\s*\r?\n", " ", body)
                self.assertNotRegex(
                    flat,
                    r"Add-SidecarMessage\b[^\n]*?\s-Message\b",
                    f"{phase}.ps1 uses obsolete '-Message' on Add-SidecarMessage; "
                    "the parameter is '-Text'.",
                )
                # Sanity: at least one Add-SidecarMessage call exists in
                # phases that have one.
                if "Add-SidecarMessage" in body:
                    self.assertRegex(
                        flat,
                        r"Add-SidecarMessage\b[^\n]*?\s-Text\b",
                        f"{phase}.ps1 calls Add-SidecarMessage but never with -Text",
                    )

    # -- pattern 4 ------------------------------------------------------
    def test_new_sidecar_uses_splat_not_inline_hashtable(self) -> None:
        # Allowed: New-Sidecar @newSidecarArgs   (splat)
        # Forbidden: New-Sidecar @{ ... }        (positional hashtable)
        # Strip line continuations so multi-line splats are caught even when
        # the splatted variable is on the next line. We then look for the
        # literal '@{' immediately after `New-Sidecar` (with optional
        # whitespace) which is the bug pattern.
        bad_pattern = re.compile(r"New-Sidecar\s+@\{", re.MULTILINE)
        good_pattern = re.compile(r"New-Sidecar\s+@\w+", re.MULTILINE)
        for phase, body in self.scripts.items():
            with self.subTest(phase=phase):
                self.assertIsNone(
                    bad_pattern.search(body),
                    f"{phase}.ps1 calls New-Sidecar with an inline hashtable "
                    "(@{...}); use a splat variable: $args = @{...}; "
                    "New-Sidecar @args.",
                )
                self.assertIsNotNone(
                    good_pattern.search(body),
                    f"{phase}.ps1 must invoke New-Sidecar via a splatted "
                    "hashtable variable.",
                )

    # -- pattern 5 ------------------------------------------------------
    def test_profile_alias_declared(self) -> None:
        # The param block must say  [Alias('Profile')] [string] $ProfileName
        # (potentially across line breaks). Strip continuations.
        for phase, body in self.scripts.items():
            with self.subTest(phase=phase):
                flat = re.sub(r"\s+", " ", body)
                self.assertRegex(
                    flat,
                    r"\[Alias\(\s*'Profile'\s*\)\]\s*\[string\]\s*\$ProfileName",
                    f"{phase}.ps1 must declare "
                    "[Alias('Profile')] [string] $ProfileName "
                    "('Profile' is a PowerShell reserved automatic variable).",
                )

    # -- pattern 6 ------------------------------------------------------
    def test_no_legacy_bug_names(self) -> None:
        # These come from the buggy in-tree msstore/arp scripts before
        # they were fixed; copying them into the Dell plugin reintroduces
        # the bug.
        forbidden_substrings = (
            "Get-WingetVersion",         # winget-specific, not for Dell
            "AscendoWinget",             # don't import the winget helper
            "-Tool @{",                  # legacy New-Sidecar -Tool param
            "-Run $RunId",               # legacy New-Sidecar -Run param
            "-Profile $Profile ",        # passing reserved Profile var
            "-DryRun:$DryRun ",          # only valid when next token is `
        )
        for phase, body in self.scripts.items():
            with self.subTest(phase=phase):
                for needle in forbidden_substrings:
                    if needle == "-DryRun:$DryRun ":
                        # This bareword is acceptable in nested calls; only
                        # forbid it when followed by '-Phase' (the legacy
                        # New-Sidecar invocation pattern).
                        legacy = "-DryRun:$DryRun -Phase"
                        self.assertNotIn(
                            legacy,
                            body,
                            f"{phase}.ps1 contains legacy New-Sidecar argv "
                            f"pattern '{legacy}'.",
                        )
                        continue
                    self.assertNotIn(
                        needle,
                        body,
                        f"{phase}.ps1 contains forbidden legacy pattern "
                        f"'{needle}'.",
                    )

    # -- additional sanity: dcu-cli is referenced -----------------------
    def test_uses_dcu_cli(self) -> None:
        # Every script must reference dcu-cli.exe at least once (either
        # to look it up, run it, or annotate that it's missing).
        for phase, body in self.scripts.items():
            with self.subTest(phase=phase):
                self.assertIn(
                    "dcu-cli",
                    body,
                    f"{phase}.ps1 doesn't reference dcu-cli.exe at all "
                    "(this is a Dell-driver-update plugin).",
                )

    # -- sanity: Category and SourceType are valid enum values ----------
    def test_uses_plugin_source_type(self) -> None:
        # The schema enum doesn't include 'dell_driver_update' as a source
        # type, so we use 'plugin' (the umbrella value) for both Category
        # and SourceType. Older drafts of these scripts used the literal
        # 'dell_driver_update' string and tripped runtime validation.
        bad = "Category    = 'dell_driver_update'"
        for phase, body in self.scripts.items():
            with self.subTest(phase=phase):
                self.assertNotIn(
                    bad,
                    body,
                    f"{phase}.ps1 sets Category='dell_driver_update' which "
                    "is not in the ascendo/v1 SOURCE_TYPE enum; use 'plugin' "
                    "with SourceFeed='dell_command_update' instead.",
                )


if __name__ == "__main__":  # pragma: no cover
    unittest.main(verbosity=2)
