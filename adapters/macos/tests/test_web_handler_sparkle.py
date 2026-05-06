"""sparkle.sh handler tests."""
from __future__ import annotations

import json
import os
import subprocess
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
LIB = REPO_ROOT / "adapters" / "macos" / "lib"


def _run(snippet: str, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    full = textwrap.dedent(f"""\
        set -eo pipefail
        source {LIB}/ascendo_web.sh
        source {LIB}/handlers/sparkle.sh
        {snippet}
    """)
    env = {**os.environ, **(env_extra or {})}
    return subprocess.run(["bash", "-c", full], capture_output=True, text=True, env=env)


SAMPLE_APPCAST = """<?xml version="1.0"?>
<rss xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle">
<channel>
  <item>
    <title>Brave 1.95.0</title>
    <enclosure url="https://updates.bravesoftware.com/Brave-1.95.0.dmg"
               sparkle:shortVersionString="1.95.0"
               sparkle:version="195000"
               type="application/octet-stream"/>
  </item>
  <item>
    <enclosure url="https://updates.bravesoftware.com/Brave-1.94.0.dmg"
               sparkle:shortVersionString="1.94.0"/>
  </item>
</channel></rss>"""


def test_sparkle_check_extracts_latest_version(tmp_path: Path) -> None:
    appcast_file = tmp_path / "appcast.xml"
    appcast_file.write_text(SAMPLE_APPCAST)
    cfg = json.dumps({
        "slug": "brave",
        "appcast_url": f"file://{appcast_file}",
    })
    snippet = f"sparkle_check 'brave' {json.dumps(cfg)!r}"
    r = _run(snippet)
    assert r.returncode == 0
    assert r.stdout.strip() == "1.95.0"


def test_sparkle_check_returns_empty_on_unreachable() -> None:
    cfg = json.dumps({
        "slug": "brave",
        "appcast_url": "https://nonexistent.invalid/appcast.xml",
    })
    snippet = f"sparkle_check 'brave' {json.dumps(cfg)!r} || true"
    r = _run(snippet)
    # Empty output is the "unknown" signal
    assert r.stdout.strip() == ""


def test_sparkle_apply_uses_apply_cli_argv_when_set() -> None:
    cfg = json.dumps({
        "slug": "test",
        "apply_cli_argv": ["/bin/echo", "ok-from-cli"],
    })
    snippet = f"sparkle_apply 'test' {json.dumps(cfg)!r}"
    r = _run(snippet)
    assert r.returncode == 0
    assert "ok-from-cli" in r.stdout


def test_sparkle_apply_falls_back_to_dmg_when_no_cli(tmp_path: Path) -> None:
    cfg = json.dumps({
        "slug": "test",
        "appcast_url": "https://nonexistent.invalid/appcast.xml",
    })
    snippet = (
        f"sparkle_apply 'test' {json.dumps(cfg)!r} || rc=$?\n"
        "echo \"rc=$rc\""
    )
    r = _run(snippet)
    # Non-zero rc expected (download or enclosure-extract fail)
    assert "rc=" in r.stdout
    assert "rc=0" not in r.stdout
