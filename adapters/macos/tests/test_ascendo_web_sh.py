"""Tests for adapters/macos/lib/ascendo_web.sh."""
from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
LIB = REPO_ROOT / "adapters" / "macos" / "lib" / "ascendo_web.sh"


def _run_bash(snippet: str, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    full = textwrap.dedent(f"""\
        set -eo pipefail
        source {LIB}
        {snippet}
    """)
    env = {**os.environ}
    if env_extra:
        env.update(env_extra)
    return subprocess.run(["bash", "-c", full], capture_output=True, text=True, env=env)


def test_installed_version_returns_value(tmp_path: Path) -> None:
    # Build a fake .app bundle with Info.plist
    app = tmp_path / "Fake.app"
    (app / "Contents").mkdir(parents=True)
    (app / "Contents" / "Info.plist").write_text(
        '<?xml version="1.0"?>'
        '<plist version="1.0"><dict>'
        '<key>CFBundleShortVersionString</key><string>1.2.3</string>'
        '</dict></plist>',
        encoding="utf-8",
    )
    r = _run_bash(f'_web_installed_version "{app}"')
    assert r.returncode == 0
    assert r.stdout.strip() == "1.2.3"


def test_installed_version_empty_for_missing_app() -> None:
    r = _run_bash('_web_installed_version "/Applications/DoesNotExist.app"')
    assert r.returncode == 0
    assert r.stdout.strip() == ""


def test_version_gt_basic_semver() -> None:
    cases = [
        ("2.0.0", "1.9.9", 0),
        ("1.0.0", "1.0.0", 1),
        ("1.0.0", "1.0.1", 1),
        ("10.0.0", "9.0.0", 0),
        ("1.2.3", "1.2.3.4", 1),
    ]
    for a, b, expected in cases:
        r = _run_bash(f'_version_gt "{a}" "{b}" && echo y || echo n')
        if expected == 0:
            assert "y" in r.stdout, f"{a} > {b} expected 0; got {r.stdout!r}"
        else:
            assert "n" in r.stdout, f"{a} > {b} expected 1; got {r.stdout!r}"


def test_is_running_returns_1_for_random_bundle_id() -> None:
    # zzz-prefix to avoid colliding with anything actually running
    r = _run_bash('_web_is_running "zzz.nonexistent.app.bundle.id" && echo y || echo n')
    assert "n" in r.stdout


def test_cache_dir_default() -> None:
    r = _run_bash('echo "$ASCENDO_WEB_CACHE_DIR"')
    # Default unset means helper will set ~/Library/Caches/Ascendo/web
    assert r.stdout.strip() == "" or "Ascendo/web" in r.stdout


def test_web_extract_sparkle_latest_version() -> None:
    snippet = textwrap.dedent("""\
        cat <<'XML' | _web_extract_sparkle_latest_version
        <?xml version="1.0"?>
        <rss><channel>
          <item>
            <enclosure url="https://e/foo.dmg" sparkle:shortVersionString="2.5.0"/>
          </item>
          <item>
            <enclosure url="https://e/foo-old.dmg" sparkle:shortVersionString="2.4.9"/>
          </item>
        </channel></rss>
        XML
    """)
    r = _run_bash(snippet)
    assert r.returncode == 0
    assert r.stdout.strip() == "2.5.0"
