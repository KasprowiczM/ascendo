"""REPORT.md honesty + category-agnostic action-required.

Real run 50f1cab5: ``verify__mas`` and ``cleanup__mas`` sidecars were
``failed`` yet REPORT.md said "All updates applied successfully" and
"Failed (0 apps)". Unknown is not zero: a failed/partial sidecar of ANY
phase, or an unverifiable post-apply state, must be surfaced by name.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ascendo.models.package import SourceType
from ascendo.models.result import ItemStatus, Message, MessageLevel
from ascendo.models.run import Phase, PhaseStatus
from ascendo.orchestrator.report import (
    ACTION_RE,
    collect_action_required,
    generate_apply_report,
)

from .test_apply_report import _build_item, _build_sidecar, _write, run_dir  # noqa: F401

STARTED = datetime(2026, 9, 29, 10, 0, tzinfo=timezone.utc)
OK_CLAIM = "All updates applied successfully"
NOTHING_CLAIM = "Nothing to update"


def _err(text: str) -> Message:
    return Message(level=MessageLevel.ERROR, text=text)


def _info(text: str) -> Message:
    return Message(level=MessageLevel.INFO, text=text)


def _sc(run_dir, phase, category, items, *, status=None, messages=None):
    sc = _build_sidecar(
        phase=phase, category=category, items=items, started=STARTED,
        messages=messages,
    )
    if status is not None:
        sc = sc.model_copy(update={"status": status})
    _write(run_dir, sc)
    return sc


def _brew_upgrade(name="glib"):
    return _build_item(
        item_id=name, category=SourceType.BREW, status=ItemStatus.SUCCESS,
        current="1", target="2", resolved="2",
    )


def _good_apply(run_dir, category=SourceType.BREW):
    _sc(run_dir, Phase.APPLY, category, [_brew_upgrade()])


# ── (a) manager-level verify/cleanup failure ────────────────────────────────


def test_failed_verify_sidecar_is_surfaced_and_no_success_claim(run_dir):
    _good_apply(run_dir, SourceType.MAS)
    _sc(run_dir, Phase.VERIFY, SourceType.MAS, [], status=PhaseStatus.FAILED,
        messages=[_err("mas verify script produced no sidecar.\n  script: /x")])
    md = generate_apply_report(run_dir)
    assert md is not None
    assert "Problems and unverified steps" in md
    assert "Mac App Store" in md and "verify" in md
    assert "script crashed before reporting" in md
    assert OK_CLAIM not in md
    assert NOTHING_CLAIM not in md
    assert "Finished with problems" in md
    assert "## Failed (0 apps)" not in md          # never claim "Failed (0)"


def test_unknown_arg_error_is_translated(run_dir):
    _good_apply(run_dir, SourceType.MAS)
    _sc(run_dir, Phase.VERIFY, SourceType.MAS, [], status=PhaseStatus.FAILED,
        messages=[_err("verify.sh: unknown arg: --filter")])
    md = generate_apply_report(run_dir)
    assert "internal argument error (please report)" in md
    assert "--filter" not in md


def test_failed_cleanup_is_a_warning_not_a_headline_failure(run_dir):
    _good_apply(run_dir)
    _sc(run_dir, Phase.CLEANUP, SourceType.BREW, [], status=PhaseStatus.FAILED,
        messages=[_err("brew cleanup exited 1")])
    md = generate_apply_report(run_dir)
    assert "## Warnings (1)" in md
    assert "brew cleanup exited 1" in md
    assert "Problems and unverified steps" not in md
    assert "Finished with problems" not in md
    assert OK_CLAIM not in md                       # but never "all good"
    assert "cleanup step reported problems" in md
    assert "## Failed (0 apps)" not in md


# ── (b) failed check sidecar is not "already up-to-date" ────────────────────


def test_failed_check_is_not_counted_up_to_date(run_dir):
    _good_apply(run_dir)
    ud = [_build_item(item_id="jq", category=SourceType.BREW,
                      status=ItemStatus.UP_TO_DATE, current="1", target="1")]
    _sc(run_dir, Phase.CHECK, SourceType.BREW, ud, status=PhaseStatus.FAILED,
        messages=[_err("brew outdated failed: network down")])
    md = generate_apply_report(run_dir)
    assert "Already up-to-date" not in md
    assert "Problems and unverified steps" in md
    assert "brew outdated failed: network down" in md
    assert "Homebrew" in md and "check" in md


def test_successful_check_still_counted(run_dir):        # regression
    _good_apply(run_dir)
    ud = [_build_item(item_id=f"p{i}", category=SourceType.BREW,
                      status=ItemStatus.UP_TO_DATE, current="1", target="1")
          for i in range(3)]
    _sc(run_dir, Phase.CHECK, SourceType.BREW, ud)
    md = generate_apply_report(run_dir)
    assert "3 packages" in md


# ── (c) verify item still pending ───────────────────────────────────────────


def test_verify_item_failed_is_listed_by_name_as_still_pending(run_dir):
    items = [
        _build_item(item_id="1153157709", name="Keka", category=SourceType.MAS,
                    status=ItemStatus.SUCCESS, current="1.4.2", target="1.4.3",
                    resolved="1.4.3"),
    ]
    _sc(run_dir, Phase.APPLY, SourceType.MAS, items)
    v = [_build_item(item_id="1153157709", name="Keka", category=SourceType.MAS,
                     status=ItemStatus.FAILED, current="1.4.2", target="1.4.3")]
    _sc(run_dir, Phase.VERIFY, SourceType.MAS, v)
    md = generate_apply_report(run_dir)
    assert "## Still pending after this run (1)" in md
    pending = md[md.index("## Still pending"):]
    assert "Keka" in pending and "1.4.2" in pending and "1.4.3" in pending
    assert OK_CLAIM not in md
    assert "Finished with problems" in md


# ── unknown state: verify missing while verify ran elsewhere ────────────────


def test_missing_verify_for_a_category_is_unknown_not_ok(run_dir):
    _good_apply(run_dir, SourceType.BREW)
    _good_apply(run_dir, SourceType.NPM)
    # verify ran for brew only (npm's verify never produced a sidecar)
    _sc(run_dir, Phase.VERIFY, SourceType.BREW,
        [_build_item(item_id="glib", category=SourceType.BREW,
                     status=ItemStatus.SUCCESS, resolved="2")])
    md = generate_apply_report(run_dir)
    assert "Problems and unverified steps" in md
    assert "Node.js (npm)" in md
    assert "could not be verified" in md
    assert OK_CLAIM not in md


def test_no_verify_phase_requested_is_not_a_problem(run_dir):   # regression
    _good_apply(run_dir)
    md = generate_apply_report(run_dir)
    assert OK_CLAIM in md
    assert "Problems and unverified" not in md
    assert "## Failed (0 apps)" in md


def test_aborted_run_marks_changed_categories_unverified(run_dir):
    _good_apply(run_dir)
    (run_dir / "run_meta.json").write_text(json.dumps({
        "aborted_after_phase": "apply", "cancelled": False,
        "skipped_managers": [["mas", "is_available() returned False"],
                             ["pip", "not in categories filter"]],
        "status": "partial",
    }))
    md = generate_apply_report(run_dir)
    assert "Run stopped after phase apply" in md
    assert "could not be verified" in md
    assert "## Run notes" in md
    assert "Skipped: Mac App Store (is_available() returned False)" in md
    assert "not in categories filter" not in md      # user's own selection
    assert OK_CLAIM not in md


def test_cancelled_run_is_a_problem(run_dir):
    _good_apply(run_dir)
    (run_dir / "run_meta.json").write_text(json.dumps({
        "aborted_after_phase": "verify", "cancelled": True,
        "skipped_managers": [], "status": "success",
    }))
    md = generate_apply_report(run_dir)
    assert "Run cancelled" in md
    assert OK_CLAIM not in md


# ── (d) all good: unchanged ─────────────────────────────────────────────────


def test_all_good_run_is_unchanged(run_dir):
    _good_apply(run_dir)
    _sc(run_dir, Phase.VERIFY, SourceType.BREW,
        [_build_item(item_id="glib", category=SourceType.BREW,
                     status=ItemStatus.SUCCESS, resolved="2")])
    _sc(run_dir, Phase.CLEANUP, SourceType.BREW, [])
    md = generate_apply_report(run_dir)
    assert OK_CLAIM in md
    assert "Problems" not in md and "Still pending" not in md and "Warnings" not in md
    assert "## Failed (0 apps)" in md and "(none)" in md


def test_failed_apply_items_are_not_duplicated_into_problems(run_dir):
    items = [_build_item(item_id="web:obsidian", category=SourceType.WEB,
                         status=ItemStatus.FAILED,
                         messages=[_err("handler exit 26")])]
    _sc(run_dir, Phase.APPLY, SourceType.WEB, items)
    md = generate_apply_report(run_dir)
    assert "## Failed (1 app)" in md
    assert "Problems and unverified" not in md
    assert "Some updates failed." in md


# ── action-required beyond web ──────────────────────────────────────────────


def _token(reason, cur="?", cand="?"):
    return f"ASCENDO-ACTION-REQUIRED reason={reason} cur={cur} cand={cand}"


def test_token_message_is_collected_for_any_category(run_dir):
    su = _build_item(item_id="macOS Sequoia 16.0-25A123", category=SourceType.SOFTWAREUPDATE,
                     status=ItemStatus.SKIPPED, current="15.6", target="16.0")
    _sc(run_dir, Phase.APPLY, SourceType.SOFTWAREUPDATE, [su], messages=[
        _info("macOS Sequoia 16.0-25A123: major upgrade skipped | "
              + _token("major_upgrade", "15.6", "16.0")),
    ])
    ipad = _build_item(item_id="UniFi", category=SourceType.MAS,
                       status=ItemStatus.PLANNED, current="9.1", target="9.2")
    _sc(run_dir, Phase.APPLY, SourceType.MAS, [ipad], messages=[
        _info("UniFi: iPad app - " + _token("ipad_pending", "9.1", "9.2")),
    ])
    lic = _build_item(item_id="brew:xcode-license", category=SourceType.BREW,
                      status=ItemStatus.SKIPPED)
    _sc(run_dir, Phase.APPLY, SourceType.BREW, [lic], messages=[
        _info("brew:xcode-license: Xcode licence not accepted | " + _token("xcode_license")),
        _info("old-cask: gone | " + _token("cask_orphan")),
        _info("bigcask: needs admin | " + _token("cask_needs_admin")),
        _info("firefox: app in use | " + _token("needs_restart", "1", "2")),
    ])
    items = collect_action_required(run_dir)
    got = {(a.category, a.slug): a for a in items}
    assert got[("softwareupdate", "macOS Sequoia 16.0-25A123")].reason == "major_upgrade"
    assert got[("softwareupdate", "macOS Sequoia 16.0-25A123")].candidate == "16.0"
    assert got[("mas", "UniFi")].reason == "ipad_pending"
    assert got[("brew", "brew:xcode-license")].reason == "xcode_license"
    assert got[("brew", "old-cask")].reason == "cask_orphan"
    assert got[("brew", "bigcask")].reason == "cask_needs_admin"
    assert got[("brew", "firefox")].reason == "needs_restart"
    for a in items:
        assert a.reason_text and a.reason_text != "Open the app and check for updates manually."
    # non-web names are NOT title-cased/mangled
    assert got[("softwareupdate", "macOS Sequoia 16.0-25A123")].name == "macOS Sequoia 16.0-25A123"

    md = generate_apply_report(run_dir)
    assert "## ⚠ Action required — 6 items need your attention" in md
    assert "Never installs it automatically".lower() in md.lower() or "never installs it" in md.lower()
    # web-only hint is not printed for non-web items
    assert "Make Ascendo cover an app" not in md


def test_item_level_token_message_is_collected(run_dir):
    it = _build_item(item_id="UniFi", category=SourceType.MAS,
                     status=ItemStatus.PLANNED,
                     messages=[_info("UniFi: x | " + _token("ipad_pending", "1", "2"))])
    _sc(run_dir, Phase.APPLY, SourceType.MAS, [it])
    assert [a.reason for a in collect_action_required(run_dir)] == ["ipad_pending"]


def test_web_behaviour_is_unchanged_and_not_double_listed(run_dir):
    web = _build_item(item_id="web:claude", category=SourceType.WEB,
                      status=ItemStatus.SKIPPED, current="1.0", target="2.0")
    _sc(run_dir, Phase.APPLY, SourceType.WEB, [web], messages=[
        _info("claude: skipped in safe mode | " + _token("self_update", "1.0", "2.0")),
    ])
    items = collect_action_required(run_dir)
    assert [(a.category, a.slug, a.reason, a.name) for a in items] == [
        ("web", "claude", "self_update", "Claude")]
    md = generate_apply_report(run_dir)
    assert "you to open them" in md
    assert "Make Ascendo cover an app" in md
    assert "## Deferred" not in md


def test_non_web_action_items_are_not_double_listed_in_deferred(run_dir):
    it = _build_item(item_id="UniFi", category=SourceType.MAS,
                     status=ItemStatus.SKIPPED, current="1", target="2")
    _sc(run_dir, Phase.APPLY, SourceType.MAS, [it], messages=[
        _info("UniFi: iPad | " + _token("ipad_pending", "1", "2"))])
    md = generate_apply_report(run_dir)
    assert "Action required" in md
    assert "## Deferred" not in md


def test_non_token_non_web_skips_stay_deferred(run_dir):
    it = _build_item(item_id="foo", category=SourceType.BREW,
                     status=ItemStatus.SKIPPED,
                     messages=[_info("brew also owns 'foo'")])
    _sc(run_dir, Phase.APPLY, SourceType.BREW, [it])
    assert collect_action_required(run_dir) == []
    assert "## Deferred" in generate_apply_report(run_dir)


def test_token_regex_contract_is_stable():
    m = ACTION_RE.search("x | ASCENDO-ACTION-REQUIRED reason=app_in_use cur=1.2 cand=?")
    assert m.groups() == ("app_in_use", "1.2", "?")


def test_web_registry_resolver_only_sees_web_items(run_dir, tmp_path):
    from ascendo.ai.resolvers.web_registry_schema import _collect_uncovered

    ipad = _build_item(item_id="UniFi", category=SourceType.MAS, status=ItemStatus.PLANNED)
    _sc(run_dir, Phase.APPLY, SourceType.MAS, [ipad],
        messages=[_info("UniFi: x | " + _token("ipad_pending"))])
    web = _build_item(item_id="web:claude", category=SourceType.WEB, status=ItemStatus.SKIPPED)
    _sc(run_dir, Phase.APPLY, SourceType.WEB, [web],
        messages=[_info("claude: x | " + _token("self_update"))])
    got = _collect_uncovered(run_dir.parent)
    assert [(a.category, a.slug) for a in got] == [("web", "claude")]


def test_machine_token_never_leaks_into_human_reasons(run_dir):
    it = _build_item(item_id="foo", category=SourceType.BREW, status=ItemStatus.FAILED,
                     messages=[_err("foo: upgrade failed | " + _token("cask_needs_admin"))])
    _sc(run_dir, Phase.APPLY, SourceType.BREW, [it])
    md = generate_apply_report(run_dir)
    failed = md[md.index("## Failed"):]
    assert "upgrade failed" in failed
    assert "ASCENDO-ACTION-REQUIRED" not in failed


def test_npm_cli_duplicate_message_token_is_collected_without_an_item(run_dir):
    """Task 8b: a duplicate of a native CLI is a phase-level warn message (no
    synthetic item) carrying reason=cli_duplicate; the report lists it."""
    ok = _build_item(item_id="claude-code", category=SourceType.NPM,
                     status=ItemStatus.UP_TO_DATE, current="2.0.0", target="2.0.0")
    _sc(run_dir, Phase.APPLY, SourceType.NPM, [ok], messages=[
        Message(level=MessageLevel.WARN, text=(
            "claude-code@/p/npm-global: native claude 2.0.0 is installed but a duplicate "
            "@anthropic-ai/claude-code 1.0.0 remains in /p/npm-global (report-only); remove "
            "it with: npm uninstall -g --prefix /p/npm-global @anthropic-ai/claude-code | "
            + _token("cli_duplicate", "2.0.0", "1.0.0"))),
    ])
    items = collect_action_required(run_dir)
    assert [(a.category, a.slug, a.reason, a.current, a.candidate) for a in items] == [
        ("npm", "claude-code@/p/npm-global", "cli_duplicate", "2.0.0", "1.0.0")]
    a = items[0]
    assert "second copy" in a.reason_text and "brew uninstall" in a.reason_text
    assert a.reason_text != "Open the app and check for updates manually."
    assert a.open_hint
    md = generate_apply_report(run_dir)
    assert "## ⚠ Action required — 1 item needs your attention" in md
    assert "claude-code@/p/npm-global" in md


def test_feed_stale_and_behind_reason_codes_have_their_own_text(run_dir):
    """Task 9: the new vendor-truth reason codes are known to the report."""
    from ascendo.orchestrator.report import _DEFAULT_REASON_TEXT, _REASON_TEXT

    for code in ("feed_stale", "behind", "needs_restart"):
        assert code in _REASON_TEXT, code
        assert _REASON_TEXT[code] != _DEFAULT_REASON_TEXT
    app = _build_item(item_id="web:cursor", category=SourceType.WEB,
                      status=ItemStatus.SKIPPED, current="3.22.7", target=None)
    _sc(run_dir, Phase.APPLY, SourceType.WEB, [app], messages=[
        _info("cursor: behind | " + _token("behind", "3.22.7", "3.22.12")),
        _info("warp: feed_stale | " + _token("feed_stale", "0.9", "0.8")),
    ])
    got = {a.slug: a for a in collect_action_required(run_dir)}
    assert got["cursor"].reason == "behind" and got["cursor"].reason_text == _REASON_TEXT["behind"]
    assert got["warp"].reason == "feed_stale"
