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


def test_is_prerelease_detects_common_markers() -> None:
    """_is_prerelease detects beta/rc/alpha/dev/nightly/pre suffixes."""
    prerelease_examples = [
        "151.0b8",       # Mozilla Firefox beta
        "151.0a1",       # Firefox alpha (Nightly)
        "1.0.0-rc1",
        "1.0.0-rc.2",
        "2.0-beta",
        "2.0-Beta",
        "3.0-alpha.1",
        "1.0.0-dev",
        "1.0-pre",
        "1.0-nightly",
    ]
    for v in prerelease_examples:
        r = _run_bash(f'_is_prerelease "{v}" && echo y || echo n')
        assert "y" in r.stdout, f"{v} should be detected as pre-release; got {r.stdout!r}"

    stable_examples = [
        "151.0",
        "1.2.3",
        "1.2.89.539",
        "26.4.2",
        "0",
        "10.0.0",
    ]
    for v in stable_examples:
        r = _run_bash(f'_is_prerelease "{v}" && echo y || echo n')
        assert "n" in r.stdout, f"{v} should NOT be detected as pre-release; got {r.stdout!r}"


def test_should_skip_upgrade_blocks_downgrade_and_prerelease_regression() -> None:
    """Sesja 46 regression: apply.sh must skip when installed >= candidate
    OR candidate is a pre-release the user didn't ask for.

    Without this guard:
      * Spotify 1.2.89.539 → 1.2.88.483 (brew livecheck lags vendor's
        own auto-updater) was attempted as a "success" downgrade.
      * Firefox Developer Edition 151.0 stable → 151.0b8 beta (Mozilla
        product-details API still publishing the beta channel) was
        attempted as a "success" replacement of stable with beta.
    """
    # cases: (installed, candidate, expected) — expected="skip" or "apply"
    cases = [
        ("1.2.89.539", "1.2.88.483", "skip"),    # Spotify downgrade
        ("151.0", "151.0b8", "skip"),            # Firefox stable → beta
        ("151.0b8", "151.0b9", "apply"),         # beta channel upgrade
        ("151.0", "152.0", "apply"),             # real major upgrade
        ("151.0", "151.0", "skip"),              # equal
        ("", "151.0", "apply"),                  # missing installed
        ("151.0", "", "apply"),                  # missing candidate
        ("1.0", "1.0-rc1", "skip"),              # stable → rc regression
        ("1.0-rc1", "1.0-rc2", "apply"),         # rc upgrade
        ("26.4.1", "26.4.2", "apply"),           # patch upgrade
        ("26.4.2", "26.4.1", "skip"),            # patch downgrade
    ]
    for installed, cand, expected in cases:
        r = _run_bash(f'_should_skip_upgrade "{installed}" "{cand}" && echo skip || echo apply')
        out = r.stdout.strip()
        assert out == expected, (
            f"_should_skip_upgrade installed={installed!r} cand={cand!r}: "
            f"got {out!r}, expected {expected!r}"
        )


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
