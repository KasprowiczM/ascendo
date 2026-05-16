"""Regression: web check and plan must agree that an outdated
release_feed/sparkle/github_dmg app is `planned` (not `skipped`),
regardless of whether the app is currently running.

The megasync bug: a running MEGAsync showed `planned` in check but
`skipped deferred_app_in_use` in plan. Deferral-because-running is an
apply-phase outcome (now surfaced by the Phase-A Action-required
panel), not a plan-phase status downgrade.
"""

from __future__ import annotations

import re
from pathlib import Path

_WEB = Path(__file__).resolve().parents[2] / "adapters" / "macos" / "scripts" / "web"


def _branch(src: str, header: str) -> str:
    """Return the body of the `sparkle|github_dmg|release_feed)` case
    branch up to the next `;;`."""
    i = src.index(header)
    j = src.index(";;", i)
    return src[i:j]


def test_plan_does_not_downgrade_running_app_to_skipped() -> None:
    plan = (_WEB / "plan.sh").read_text(encoding="utf-8")
    branch = _branch(plan, "sparkle|github_dmg|release_feed)")
    # An outdated item must be PLANNED.
    assert '"planned" "web" "$HANDLER"' in branch
    # It must NOT be downgraded to skipped/deferred when running.
    assert "deferred_app_in_use" not in branch, (
        "plan.sh still downgrades a running outdated app to "
        "skipped — re-introduces the megasync inconsistency"
    )
    assert not re.search(r"IS_RUNNING\s+-eq\s+1[^;]*skipped", branch)


def test_check_classifies_outdated_release_feed_as_planned() -> None:
    check = (_WEB / "check.sh").read_text(encoding="utf-8")
    # check.sh's classifier emits `planned` for an outdated probe.
    assert '"planned" "web" "$HANDLER"' in check
