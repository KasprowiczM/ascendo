"""Task 12: status vocabulary in REPORT.md (ASCENDO-STATUS), Manufacturer warnings,
narrowed triggered promotion, human texts, F3 soft system deferral.

Delta v1.5.4 row 15: a stale vendor feed (older than the installed version) is a
*warning*: never "Still pending", never action-required, no duplicate generic
"unconfirmed" item, and it must not hide a real problem.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from ascendo.models.package import SourceType
from ascendo.models.result import ItemStatus, Message, MessageLevel
from ascendo.models.run import Phase, PhaseStatus
from ascendo.orchestrator.report import (
    STATUS_RE,
    _DEFAULT_REASON_TEXT,
    _REASON_TEXT,
    collect_action_required,
    generate_apply_report,
)

from ._report_fixtures import _build_item, _build_sidecar, _write, run_dir  # noqa: F401

STARTED = datetime(2026, 10, 17, 10, 0, tzinfo=timezone.utc)
WEB = SourceType.WEB


def _stale_msg(slug="warp", vendor="0.8", installed="0.9"):
    return Message(level=MessageLevel.WARN, code="feed_stale",
                   text=f"{slug}: vendor feed {vendor} is older than installed {installed} "
                        "| ASCENDO-STATUS code=feed_stale")


def _warn(text, code=None):
    return Message(level=MessageLevel.WARN, text=text, code=code)


def _info(text, code=None):
    return Message(level=MessageLevel.INFO, text=text, code=code)


def _sc(run_dir, phase, category, items, *, messages=None, status=None):
    sc = _build_sidecar(phase=phase, category=category, items=items, started=STARTED, messages=messages)
    if status is not None:
        sc = sc.model_copy(update={"status": status})
    _write(run_dir, sc)
    return sc


def _web(slug, status, *, cur=None, target=None, messages=None):
    return _build_item(item_id=f"web:{slug}", category=WEB, status=status,
                       current=cur, target=target, messages=messages)


def _tok(reason, cur="?", cand="?"):
    return f"ASCENDO-ACTION-REQUIRED reason={reason} cur={cur} cand={cand}"


def _section(md: str, title: str) -> str:
    """Text of the '## <title>...' section (up to the next '## ')."""
    start = md.index(f"## {title}")
    rest = md[start + 3:]
    nxt = rest.find("\n## ")
    return rest if nxt < 0 else rest[:nxt]


# ---------------------------------------------------------------------------
# STATUS_RE
# ---------------------------------------------------------------------------

def test_status_re_parses_the_code_and_rejects_malformed_tokens():
    assert STATUS_RE.search("x | ASCENDO-STATUS code=feed_stale").group(1) == "feed_stale"
    assert STATUS_RE.search("x | ASCENDO-STATUS code=") is None
    assert STATUS_RE.search("x | ASCENDO-STATUS") is None


# ---------------------------------------------------------------------------
# Stale feed = Manufacturer warning (binding tests, delta v1.5.4)
# ---------------------------------------------------------------------------

def test_stale_feed_is_warning_without_an_update_target_or_fake_launch(run_dir):
    _sc(run_dir, Phase.APPLY, WEB, [_web("warp", ItemStatus.SKIPPED, cur="0.9")], messages=[_stale_msg()])
    assert collect_action_required(run_dir) == []
    md = generate_apply_report(run_dir)
    assert "## Manufacturer warnings (1)" in md
    block = _section(md, "Manufacturer warnings")
    assert "Warp" in block and "`0.8`" in block and "`0.9`" in block
    assert "Action required" not in md
    assert "Still pending" not in md
    assert "Trigger-only" not in md                       # no fake "updater launched" item
    assert "## Deferred" not in md                        # not double-listed
    assert "1 manufacturer warning" in md                 # counted as a warning
    assert "All updates applied successfully" not in md
    assert "Nothing to update — everything was already current" not in md   # unverified is not "current"
    assert "→" not in block                               # never shown as an update (no target)


def test_stale_feed_preserves_actual_unconfirmed_apps_and_hard_steps(run_dir):
    items = [
        _web("warp", ItemStatus.SKIPPED, cur="0.9"),
        _web("slack", ItemStatus.TRIGGERED, cur="4.0"),                 # uncoded: genuinely unconfirmed
        _web("zoom", ItemStatus.FAILED, cur="5.0", messages=[
            Message(level=MessageLevel.ERROR, text="zoom: handler exit 3: boom")]),
    ]
    _sc(run_dir, Phase.APPLY, WEB, items, messages=[_stale_msg()])
    _sc(run_dir, Phase.VERIFY, SourceType.BREW, [], status=PhaseStatus.FAILED,
        messages=[Message(level=MessageLevel.ERROR, text="brew verify exited 1")])
    md = generate_apply_report(run_dir)
    assert "## Manufacturer warnings (1)" in md
    mfr = _section(md, "Manufacturer warnings")
    assert "Warp" in mfr and "Slack" not in mfr and "Zoom" not in mfr
    assert "Slack" in _section(md, "Trigger-only")
    action = _section(md, "⚠ Action required")
    assert "Slack" in action and "Warp" not in action                    # still surfaced by name
    assert "Zoom" in _section(md, "Failed") and "boom" in md             # hard failure untouched
    assert "Problems and unverified steps" in md and "brew verify exited 1" in md
    assert "Finished with problems" in md or "Mostly succeeded" in md or "Some updates failed" in md


def test_stale_feed_does_not_hide_unexplained_or_malformed_status(run_dir):
    items = [
        _web("warp", ItemStatus.SKIPPED, cur="0.9"),                      # a real stale-feed warning
        _web("alpha", ItemStatus.SKIPPED, cur="1.0"),                     # unrecognised code
        _web("beta", ItemStatus.SKIPPED, cur="2.0"),                      # malformed token
        _web("gamma", ItemStatus.TRIGGERED, cur="3.0"),                   # triggered + unrecognised code
        _web("delta", ItemStatus.FAILED, cur="4.0"),                      # feed_stale code on a FAILED item
    ]
    msgs = [
        _stale_msg(),
        _info("alpha: odd outcome | ASCENDO-STATUS code=totally_made_up"),
        _info("beta: odd outcome | ASCENDO-STATUS code="),
        _info("gamma: odd outcome | ASCENDO-STATUS code=totally_made_up"),
        Message(level=MessageLevel.ERROR, text="delta: vendor feed 0.1 is older than installed 4.0 "
                                               "| ASCENDO-STATUS code=feed_stale"),
    ]
    _sc(run_dir, Phase.APPLY, WEB, items, messages=msgs)
    names = {a.slug for a in collect_action_required(run_dir)}
    assert {"alpha", "beta", "gamma"} <= names and "warp" not in names
    md = generate_apply_report(run_dir)
    mfr = _section(md, "Manufacturer warnings")
    assert "Warp" in mfr
    for hidden in ("Alpha", "Beta", "Gamma", "Delta"):
        assert hidden not in mfr
    assert "Delta" in _section(md, "Failed")


def test_warning_without_parseable_versions_preserves_diagnostic(run_dir):
    # The token claims feed_stale but there is no installed version and no vendor version:
    # nothing to show as a warning, so the generic diagnostic must stay.
    item = _web("warp", ItemStatus.SKIPPED)                                  # no current_version
    _sc(run_dir, Phase.APPLY, WEB, [item],
        messages=[_warn("warp: something odd | ASCENDO-STATUS code=feed_stale", code="feed_stale")])
    md = generate_apply_report(run_dir)
    assert "Manufacturer warnings" not in md
    assert {a.slug for a in collect_action_required(run_dir)} == {"warp"}
    assert "Action required" in md


# ---------------------------------------------------------------------------
# Triggered promotion is narrowed
# ---------------------------------------------------------------------------

def test_triggered_launched_is_not_action_required_but_behind_needs_restart_are(run_dir):
    items = [
        _web("launchedapp", ItemStatus.TRIGGERED, cur="1.0", target="2.0"),
        _web("updatedapp", ItemStatus.TRIGGERED, cur="1.0"),
        _web("unverifiedapp", ItemStatus.TRIGGERED, cur="1.0"),
        _web("behindapp", ItemStatus.SKIPPED, cur="1.0", target="2.0"),
        _web("restartapp", ItemStatus.SKIPPED, cur="1.0", target="2.0"),
        _web("legacyapp", ItemStatus.TRIGGERED, cur="1.0"),               # no code at all: as before
    ]
    msgs = [
        _info("launchedapp: launched | ASCENDO-STATUS code=launched", "launched"),
        _info("updatedapp: updated | ASCENDO-STATUS code=updated", "updated"),
        _info("unverifiedapp: no proof | ASCENDO-STATUS code=unverified", "unverified"),
        _info("behindapp: behind | ASCENDO-STATUS code=behind | " + _tok("behind", "1.0", "2.0"), "behind"),
        _info("restartapp: running | ASCENDO-STATUS code=needs_restart | " + _tok("needs_restart", "1.0", "2.0"),
              "needs_restart"),
    ]
    _sc(run_dir, Phase.APPLY, WEB, items, messages=msgs)
    got = {a.slug: a.reason for a in collect_action_required(run_dir)}
    assert "launchedapp" not in got and "updatedapp" not in got
    assert "unverifiedapp" not in got                  # fresh unverified: Trigger-only, not action-required
    assert got["behindapp"] == "behind" and got["restartapp"] == "needs_restart"
    assert "legacyapp" in got                                              # "nothing silently missing" stays
    md = generate_apply_report(run_dir)
    trig = _section(md, "Trigger-only")
    assert "Launchedapp" in trig and "Updatedapp" in trig and "Unverifiedapp" in trig


def test_fresh_unverified_verify_item_is_not_promoted_but_a_stale_one_is(run_dir):
    _sc(run_dir, Phase.APPLY, WEB, [_web("slack", ItemStatus.TRIGGERED, cur="4.0")],
        messages=[_info("slack: launched | ASCENDO-STATUS code=launched", "launched")])
    _sc(run_dir, Phase.VERIFY, WEB, [_web("slack", ItemStatus.TRIGGERED, cur="4.0")],
        messages=[_info("slack: unverified (unchanged) | ASCENDO-STATUS code=unverified", "unverified")])
    assert collect_action_required(run_dir) == []
    # the stale case is what verify.sh stamps with an ACTION-REQUIRED token (item failed)
    _sc(run_dir, Phase.VERIFY, WEB, [_web("slack", ItemStatus.FAILED, cur="4.0")], messages=[
        Message(level=MessageLevel.ERROR, text="slack: action_required (version 4.0 unchanged for 60 days) | "
                + _tok("unverified", "4.0", "?"))])
    items = collect_action_required(run_dir)
    assert [(a.category, a.slug, a.reason) for a in items] == [("web", "slack", "unverified")]
    assert items[0].reason_text != _DEFAULT_REASON_TEXT


def test_verify_behind_token_is_read_by_the_report(run_dir):
    _sc(run_dir, Phase.APPLY, WEB, [_web("slack", ItemStatus.TRIGGERED, cur="4.0")])
    _sc(run_dir, Phase.VERIFY, WEB, [_web("slack", ItemStatus.SKIPPED, cur="4.0", target="5.0")], messages=[
        _info("slack: behind -- installed 4.0, vendor 5.0 | " + _tok("behind", "4.0", "5.0"), "behind")])
    got = {a.slug: a.reason for a in collect_action_required(run_dir)}
    assert got["slack"] in ("behind", "self_update")


def test_rollout_hold_is_informational_not_action_required(run_dir):
    _sc(run_dir, Phase.APPLY, WEB, [_web("chrome", ItemStatus.SKIPPED, cur="1.0", target="2.0")],
        messages=[_info("chrome: rollout_hold: vendor 2.0 is a staged release | ASCENDO-STATUS code=rollout_hold",
                        "rollout_hold")])
    assert collect_action_required(run_dir) == []
    md = generate_apply_report(run_dir)
    assert "Action required" not in md
    deferred = _section(md, "Deferred")
    assert "Chrome" in deferred and "in stages" in deferred and "rollout_hold" not in deferred           # human text, not a raw code


# ---------------------------------------------------------------------------
# Human texts for the reason vocabulary
# ---------------------------------------------------------------------------

def test_every_vocabulary_reason_has_its_own_text():
    for code in ("needs_restart", "rollout_hold", "error_soft", "feed_stale", "behind", "download_error",
                 "launch_failed", "launched", "identity_unverifiable", "app_facts_unavailable",
                 "major_upgrade", "ipad_pending", "unverified", "unknown_version"):
        assert code in _REASON_TEXT, code
        assert _REASON_TEXT[code] != _DEFAULT_REASON_TEXT and len(_REASON_TEXT[code]) > 20, code


def test_token_reasons_render_their_text_in_the_report(run_dir):
    items = [_web("a1", ItemStatus.SKIPPED, cur="1"), _web("a2", ItemStatus.SKIPPED, cur="1"),
             _web("a3", ItemStatus.SKIPPED, cur="1")]
    msgs = [_warn("a1: x | " + _tok("download_error", "1", "2")),
            _warn("a2: x | " + _tok("launch_failed", "1", "2")),
            _warn("a3: x | " + _tok("identity_unverifiable", "1", "2"))]
    _sc(run_dir, Phase.APPLY, WEB, items, messages=msgs)
    md = generate_apply_report(run_dir)
    for code in ("download_error", "launch_failed", "identity_unverifiable"):
        assert _REASON_TEXT[code] in md
    assert "ASCENDO-" not in md                                             # no machine tokens leak


# ---------------------------------------------------------------------------
# F3: a soft system deferral is a run warning; a cancel is not
# ---------------------------------------------------------------------------

def _su_item(status=ItemStatus.SKIPPED):
    return _build_item(item_id="macOS 99.1", category=SourceType.SOFTWAREUPDATE, status=status,
                       current="99.0", target="99.1")


def test_soft_system_deferral_counts_as_a_run_warning_not_a_failure(run_dir):
    _sc(run_dir, Phase.APPLY, SourceType.SOFTWAREUPDATE, [_su_item()], messages=[
        _warn("macOS 99.1: skipped -- authenticate interactively and re-run (SUDO_UNAVAILABLE)",
              code="SUDO_UNAVAILABLE")])
    md = generate_apply_report(run_dir)
    assert "Nothing to update — everything was already current" not in md
    assert "All updates applied successfully" not in md
    assert "Finished with problems" not in md and "Some updates failed" not in md
    assert "1 system step deferred" in md
    assert "authenticate interactively" in md.lower()                               # not silent: the reason is listed
    assert "## Failed" not in md or "## Failed (0 apps)" in md


def test_a_cancelled_run_does_not_get_the_deferral_warning(run_dir):
    _sc(run_dir, Phase.APPLY, SourceType.SOFTWAREUPDATE, [_su_item()], messages=[
        _warn("macOS 99.1: skipped (SUDO_UNAVAILABLE)", code="SUDO_UNAVAILABLE")])
    (run_dir / "run_meta.json").write_text(json.dumps({"cancelled": True, "aborted_after_phase": "apply"}))
    md = generate_apply_report(run_dir)
    assert "Run cancelled" in md
    assert "system step deferred" not in md and "system steps deferred" not in md
    assert "was deferred" not in md and "were deferred" not in md


def test_run_status_helpers_treat_sudo_unavailable_as_warning_and_cancel_as_not():
    from ascendo.orchestrator.run_async import _report_has_warnings
    from ascendo.orchestrator.runner import RunReport

    from ._report_fixtures import _host, _make_sidecar, _run

    run, host = _run(), _host()
    quiet = _make_sidecar(run, host, phase=Phase.APPLY, messages=[
        Message(level=MessageLevel.INFO, text="authenticate interactively", code="SUDO_UNAVAILABLE")])
    assert _report_has_warnings(RunReport(run=run, host=host, sidecars=[quiet])) is True
    clean = _make_sidecar(run, host, phase=Phase.APPLY)
    assert _report_has_warnings(RunReport(run=run, host=host, sidecars=[clean])) is False


def test_policy_skips_and_ipad_pending_are_not_soft_deferrals(run_dir):
    su = _build_item(item_id="macOS 99.1", category=SourceType.SOFTWAREUPDATE, status=ItemStatus.SKIPPED,
                     current="99.0", target="99.1")
    _sc(run_dir, Phase.APPLY, SourceType.SOFTWAREUPDATE, [su], messages=[
        _info("macOS 99.1: skipped - not marked Recommended")])
    mas = _build_item(item_id="UniFi", category=SourceType.MAS, status=ItemStatus.SKIPPED, current="1", target="2")
    _sc(run_dir, Phase.APPLY, SourceType.MAS, [mas], messages=[
        _info("UniFi: iPad app - " + _tok("ipad_pending", "1", "2"))])
    md = generate_apply_report(run_dir)
    assert "system step" not in md


def test_restart_deferral_of_a_system_step_is_a_soft_deferral(run_dir):
    su = _build_item(item_id="macOS 99.1", category=SourceType.SOFTWAREUPDATE, status=ItemStatus.SKIPPED,
                     current="99.0", target="99.1")
    _sc(run_dir, Phase.APPLY, SourceType.SOFTWAREUPDATE, [su], messages=[
        _info("macOS 99.1: needs a restart - " + _tok("needs_restart", "99.0", "99.1"))])
    assert "1 system step deferred" in generate_apply_report(run_dir)
