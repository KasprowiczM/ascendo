"""Contract tests for the bare-`ascendo` first-run banner.

Asserts:

* :func:`ascendo.cli.render_banner` returns a string and includes the
  canonical quick-start hints (``doctor``, ``run --phase``).
* The banner mentions key subcommands (``snapshot``, ``schedule``,
  ``dashboard``).
* Locale switching works: ``locale="pl"`` renders the Polish title;
  ``locale="en"`` renders the English title.
* Bare `ascendo` (no subcommand) on the CLI runner prints the banner;
  `ascendo --help` still shows Typer's auto-generated help.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Worktree path bootstrap (same pattern as test_cli_polish.py).
_WORKTREE_ROOT = Path(__file__).resolve().parents[2]
_WORKTREE_CORE = _WORKTREE_ROOT / "core"
if str(_WORKTREE_CORE) not in sys.path:
    sys.path.insert(0, str(_WORKTREE_CORE))

for _name in [k for k in list(sys.modules) if k == "ascendo" or k.startswith("ascendo.")]:
    if "ascendo.cli" in _name:
        del sys.modules[_name]

from typer.testing import CliRunner  # noqa: E402

from ascendo.cli import app, render_banner  # noqa: E402


runner = CliRunner()


def test_render_banner_returns_string_with_quickstart() -> None:
    """The banner must include the canonical quick-start hints."""
    text = render_banner(locale="en")
    assert isinstance(text, str)
    assert "doctor" in text
    assert "run --phase" in text


def test_render_banner_lists_core_subcommands() -> None:
    """The subcommand table must reference the user-facing command groups."""
    text = render_banner(locale="en")
    for token in ("version", "doctor", "run", "runs list", "runs show",
                  "runs json", "dashboard", "snapshot", "schedule"):
        assert token in text, f"banner missing subcommand mention: {token!r}"


def test_render_banner_polish_locale_uses_polish_title() -> None:
    """The Polish locale must render the Polish title string."""
    text_pl = render_banner(locale="pl")
    text_en = render_banner(locale="en")
    assert "Zunifikowane aktualizacje" in text_pl
    assert "Unified updates" in text_en
    assert text_pl != text_en


def test_render_banner_includes_examples_and_docs_link() -> None:
    """Examples block + GitHub docs link must be present (English locale)."""
    text = render_banner(locale="en")
    assert "Examples" in text
    assert "github.com/KasprowiczM/ascendo" in text


def test_bare_ascendo_invocation_prints_banner() -> None:
    """`ascendo` with no subcommand prints the banner and exits 0."""
    result = runner.invoke(app, [])
    assert result.exit_code == 0, f"expected exit 0, got {result.exit_code}: {result.output}"
    assert "doctor" in result.output
    assert "run --phase" in result.output


def test_help_flag_does_not_print_banner_title() -> None:
    """`ascendo --help` shows Typer's help, not our custom banner header."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    # Typer help lists the `Commands` block; our banner uses a different
    # title line. The help flag should NOT render the banner tagline.
    assert "Cross-platform update orchestrator" in result.output
    assert "Unified updates. Every app." not in result.output
