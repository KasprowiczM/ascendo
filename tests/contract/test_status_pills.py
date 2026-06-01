"""The honest backend statuses must reach the UI as the right pill variant.

`components.js` `STATUS()` previously collapsed any status not in
{ok,warn,err,info,neutral} to "neutral" — so the honest `failed` /
`triggered_pending` / `up_to_date` statuses rendered as neutral grey (audit
ASCENDO_ULTRA_REVIEW_2 §5). This drives the real `AC.StatusPill` render path
under a stubbed DOM via node and asserts the domain→variant mapping.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

_NODE = shutil.which("node")
_COMPONENTS = Path(__file__).resolve().parents[2] / "app" / "frontend" / "components.js"

# Honest domain status -> visual pill variant.
_CASES = {
    "failed": "err",
    "partial": "err",
    "missing": "err",
    "triggered_pending": "warn",
    "triggered": "warn",
    "outdated": "warn",
    "planned": "warn",
    "up_to_date": "ok",
    "success": "ok",
    "skipped": "neutral",
    # raw variants pass through unchanged
    "ok": "ok",
    "warn": "warn",
    "err": "err",
    "info": "info",
    "neutral": "neutral",
}


@pytest.mark.skipif(_NODE is None, reason="node not available")
def test_status_pill_maps_honest_statuses() -> None:
    harness = textwrap.dedent(
        """
        global.window = global;
        global.document = { createElement: function () {
            return { className: "", _t: "", appendChild: function () {},
                     setAttribute: function () {},
                     set textContent(v) { this._t = v; },
                     get textContent() { return this._t; } };
        } };
        require(%s);
        var cases = %s;
        for (var st in cases) {
            var cls = window.AC.StatusPill({ status: st }).className;
            if (cls.indexOf("asc-pill--" + cases[st]) === -1) {
                console.error("FAIL " + st + " expected " + cases[st] + " got " + cls);
                process.exit(1);
            }
        }
        // A failed apply must be clearly red — never neutral grey, never green.
        var f = window.AC.StatusPill({ status: "failed" }).className;
        if (f.indexOf("asc-pill--neutral") !== -1 || f.indexOf("asc-pill--ok") !== -1) {
            console.error("failed rendered non-error: " + f);
            process.exit(1);
        }
        console.log("OK");
        """
    ) % (json.dumps(str(_COMPONENTS)), json.dumps(_CASES))

    result = subprocess.run([_NODE, "-e", harness], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr + result.stdout
    assert "OK" in result.stdout
