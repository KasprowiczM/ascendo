"""Guard (final review I2): no macOS adapter test may stub `sleep` on PATH.

The phase scripts wait through `_web_sleep` -> $ASCENDO_WEB_SLEEP_BIN, so a PATH
`sleep` stub never observes (or prevents) their waits -- it only makes "must not
sleep" assertions pass vacuously. Use ASCENDO_WEB_SLEEP_BIN=<stub> instead.
"""
from __future__ import annotations

import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
# `bin / "sleep"`, `bindir/'sleep'`, `fakebin / "sleep"` ... (exact name only)
_PAT = re.compile(r"""/\s*["']sleep["']""")


def test_no_test_creates_a_sleep_stub_on_path():
    offenders = []
    for f in sorted(HERE.glob("*.py")):
        if f.name == Path(__file__).name:
            continue
        for n, line in enumerate(f.read_text().splitlines(), 1):
            if _PAT.search(line):
                offenders.append(f"{f.name}:{n}: {line.strip()}")
    assert not offenders, "PATH sleep stubs found:\n" + "\n".join(offenders)
