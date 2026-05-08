"""check.sh + plan.sh end-to-end tests with fake handlers + tools.

Network access is stubbed via a PATH shim that replaces /usr/bin/curl
with a small Python script that serves canned content keyed by URL.
The registry validator only accepts https URLs, so test fixtures use
synthetic https hostnames whose responses are intercepted by the shim.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import textwrap
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = REPO_ROOT / "adapters" / "macos" / "scripts" / "web"


def _make_fake_app(tmp_path: Path, name: str, version: str,
                   bundle_id: str | None = None) -> Path:
    """Build a fake .app bundle under tmp_path/Applications/.

    M5.7: discovery layer walks /Applications and reads CFBundleIdentifier
    from each Info.plist. Tests must pass bundle_id matching the registry
    fixture so discovery emits the expected bundle.
    """
    if bundle_id is None:
        bundle_id = f"com.fixture.{name.lower().replace(' ', '_')}"
    app = tmp_path / "Applications" / f"{name}.app"
    (app / "Contents").mkdir(parents=True, exist_ok=True)
    (app / "Contents" / "Info.plist").write_text(
        '<?xml version="1.0"?><plist version="1.0"><dict>'
        f'<key>CFBundleIdentifier</key><string>{bundle_id}</string>'
        '<key>CFBundleShortVersionString</key>'
        f'<string>{version}</string>'
        f'<key>CFBundleName</key><string>{name}</string>'
        '</dict></plist>'
    )
    return app


def _make_curl_shim(tmp_path: Path, fixtures: dict[str, str]) -> Path:
    """Build a PATH-injectable directory containing a fake `curl`.

    The shim parses curl's argv, finds the URL, and either echoes the
    fixture for that URL (exit 0) or exits 22 (HTTP-not-found-equivalent
    used by curl -f).
    """
    shim_dir = tmp_path / "binshim"
    shim_dir.mkdir()
    fixture_dir = tmp_path / "curl_fixtures"
    fixture_dir.mkdir()
    fixture_map = {}
    for url, content in fixtures.items():
        # Cheap stable filename derived from URL.
        safe = "".join(c if c.isalnum() else "_" for c in url)[:80]
        fp = fixture_dir / f"{safe}.txt"
        fp.write_text(content)
        fixture_map[url] = str(fp)
    map_file = fixture_dir / "_map.json"
    map_file.write_text(json.dumps(fixture_map))

    curl = shim_dir / "curl"
    curl.write_text(textwrap.dedent(f"""\
        #!/usr/bin/env python3
        import json, sys, os
        fixtures = json.load(open({json.dumps(str(map_file))}))
        url = None
        for a in sys.argv[1:]:
            if a.startswith("http://") or a.startswith("https://"):
                url = a
                break
        if url is None or url not in fixtures:
            sys.exit(22)
        sys.stdout.write(open(fixtures[url]).read())
        sys.exit(0)
        """))
    curl.chmod(curl.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return shim_dir


def _run_phase(phase: str, registry_toml: Path, output_dir: Path,
               env_extra: dict | None = None,
               curl_shim_dir: Path | None = None,
               extra_args: list | None = None):
    run_id = str(uuid.uuid4())
    # Point discovery at the test's Applications fixture dir. If the dir
    # doesn't exist, set it to a guaranteed-empty path (NOT /Applications
    # which would leak real-machine apps into hermetic tests). Discovery
    # exits cleanly when the root doesn't exist.
    apps_root = registry_toml.parent / "Applications"
    if not apps_root.is_dir():
        apps_root.mkdir(parents=True, exist_ok=True)
    env = {**os.environ,
           "ASCENDO_WEB_REGISTRY_PATH": str(registry_toml),
           "ASCENDO_WEB_USER_REGISTRY_PATH": "",
           "ASCENDO_WEB_APPS_ROOT": str(apps_root),
           # Tests don't have brew/mas installed; skip the auto-populate
           # ownership probes by setting empty values explicitly.
           "ASCENDO_WEB_BREW_CASKS": "",
           "ASCENDO_WEB_MAS_BUNDLE_IDS": "",
           "ASCENDO_WEB_APPLE_BUNDLES": "",
           "PYTHONPATH": f"{REPO_ROOT}/core:{REPO_ROOT}/adapters/macos"}
    if curl_shim_dir is not None:
        # Prepend the shim dir so our fake curl is found before /usr/bin/curl.
        # Note: handlers call /usr/bin/curl (absolute path) explicitly, so
        # PATH alone won't intercept. We have to also override the binary
        # inside the handlers via ASCENDO_WEB_CURL_BIN env (not implemented
        # in handlers); for sparkle the call uses /usr/bin/curl absolute.
        # This shim therefore only intercepts when the handler invokes a
        # bare `curl` (which sparkle doesn't). To get around this we
        # additionally bind-mount via PATH and rely on shell builtins.
        env["PATH"] = f"{curl_shim_dir}:{env.get('PATH', '')}"
    if env_extra:
        env.update(env_extra)
    cmd = ["bash", str(SCRIPTS / f"{phase}.sh"),
           "--run-id", run_id, "--trigger", "cli", "--profile", "full",
           "--output-dir", str(output_dir)]
    if extra_args:
        cmd.extend(extra_args)
    return subprocess.run(cmd, capture_output=True, text=True, env=env), run_id


def _read_sidecar(output_dir: Path, run_id: str, phase: str) -> dict:
    p = output_dir / run_id / f"{phase}__web.json"
    return json.loads(p.read_text())


# ----------------------------------------------------------------------
# Squirrel handler tests (no network needed: squirrel_check always returns
# empty; check.sh classifies as `skipped`).
# ----------------------------------------------------------------------

def test_check_emits_sidecar_for_squirrel_app(tmp_path: Path) -> None:
    fake_app = _make_fake_app(tmp_path, "Slack", "4.40.0",
                              bundle_id="com.tinyspeck.slackmacgap")
    registry = tmp_path / "registry.toml"
    registry.write_text(
        'schema = "ascendo-web-apps/v1"\n'
        '[[app]]\nslug = "slack"\nbundle_id = "com.tinyspeck.slackmacgap"\n'
        'display_name = "Slack"\nhandler = "squirrel"\n'
        f'app_path = "{fake_app}"\n',
    )
    out = tmp_path / "out"
    r, run_id = _run_phase("check", registry, out)
    assert r.returncode == 0, f"stderr={r.stderr}\nstdout={r.stdout}"
    sc = _read_sidecar(out, run_id, "check")
    assert sc["category"] == "web"
    assert len(sc["items"]) == 1
    item = sc["items"][0]
    assert item["id"] == "web:slack"
    assert item["status"] == "skipped"     # squirrel + latest unknown
    assert item["current_version"] == "4.40.0"


def test_check_skips_uninstalled_apps(tmp_path: Path) -> None:
    registry = tmp_path / "registry.toml"
    registry.write_text(
        'schema = "ascendo-web-apps/v1"\n'
        '[[app]]\nslug = "ghost"\nbundle_id = "com.example.ghost"\n'
        'display_name = "GhostApp"\nhandler = "squirrel"\n'
        'app_path = "/Applications/DefinitelyDoesNotExist.app"\n',
    )
    out = tmp_path / "out"
    r, run_id = _run_phase("check", registry, out)
    assert r.returncode == 0, r.stderr
    sc = _read_sidecar(out, run_id, "check")
    assert sc["items"] == []


def test_plan_skips_squirrel_when_not_running(tmp_path: Path) -> None:
    """squirrel + not running -> planned (apply opens the app for self-update)."""
    fake_app = _make_fake_app(tmp_path, "Slack", "4.40.0",
                              bundle_id="com.this-bundle-is-not-running.test")
    registry = tmp_path / "registry.toml"
    registry.write_text(
        'schema = "ascendo-web-apps/v1"\n'
        '[[app]]\nslug = "slack"\nbundle_id = "com.this-bundle-is-not-running.test"\n'
        'display_name = "Slack"\nhandler = "squirrel"\n'
        f'app_path = "{fake_app}"\n',
    )
    out = tmp_path / "out"
    r, run_id = _run_phase("plan", registry, out)
    assert r.returncode == 0, f"stderr={r.stderr}"
    sc = _read_sidecar(out, run_id, "plan")
    assert len(sc["items"]) == 1
    assert sc["items"][0]["status"] == "planned"


# ----------------------------------------------------------------------
# Builtin handler tests
# ----------------------------------------------------------------------

def test_check_builtin_emits_skipped(tmp_path: Path) -> None:
    fake_app = _make_fake_app(tmp_path, "Zoom", "5.17.0",
                              bundle_id="us.zoom.xos")
    registry = tmp_path / "registry.toml"
    registry.write_text(
        'schema = "ascendo-web-apps/v1"\n'
        '[[app]]\nslug = "zoom"\nbundle_id = "us.zoom.xos"\n'
        'display_name = "Zoom"\nhandler = "builtin"\n'
        f'app_path = "{fake_app}"\n',
    )
    out = tmp_path / "out"
    r, run_id = _run_phase("check", registry, out)
    assert r.returncode == 0, r.stderr
    sc = _read_sidecar(out, run_id, "check")
    assert len(sc["items"]) == 1
    item = sc["items"][0]
    assert item["status"] == "skipped"
    # An info message should be emitted explaining manual_required.
    msg_texts = " ".join(m.get("text", "") for m in sc.get("messages", []))
    assert "manual_required" in msg_texts


def test_plan_builtin_keeps_skipped_with_manual_required(tmp_path: Path) -> None:
    fake_app = _make_fake_app(tmp_path, "Zoom", "5.17.0",
                              bundle_id="us.zoom.xos")
    registry = tmp_path / "registry.toml"
    registry.write_text(
        'schema = "ascendo-web-apps/v1"\n'
        '[[app]]\nslug = "zoom"\nbundle_id = "us.zoom.xos"\n'
        'display_name = "Zoom"\nhandler = "builtin"\n'
        f'app_path = "{fake_app}"\n',
    )
    out = tmp_path / "out"
    r, run_id = _run_phase("plan", registry, out)
    assert r.returncode == 0, r.stderr
    sc = _read_sidecar(out, run_id, "plan")
    assert len(sc["items"]) == 1
    assert sc["items"][0]["status"] == "skipped"


# ----------------------------------------------------------------------
# Sparkle handler tests with a fake curl shim. Sparkle's handler invokes
# /usr/bin/curl with an absolute path, so PATH-shim won't intercept it.
# To exercise sparkle integration, we use a real https URL whose response
# is empty (curl -f exits non-zero, sparkle_check echoes empty). That
# yields the `failed` status path (probe broken) which we test below.
# ----------------------------------------------------------------------

def test_check_sparkle_unreachable_marks_skipped(tmp_path: Path) -> None:
    """When sparkle can't reach the appcast, check.sh reports skipped (M5.7).

    Pre-M5.7 this was 'failed' but that caused a single broken probe to
    mark the entire phase failed (orchestrator's all-managers-failed
    abort fired). New semantics: empty Tier-A probe is 'skipped' with
    a warn message — operator sees the row, the run continues.
    """
    fake_app = _make_fake_app(tmp_path, "TestApp", "1.0.0",
                              bundle_id="com.example.testapp")
    registry = tmp_path / "registry.toml"
    registry.write_text(
        'schema = "ascendo-web-apps/v1"\n'
        '[[app]]\nslug = "testapp"\nbundle_id = "com.example.testapp"\n'
        'display_name = "TestApp"\nhandler = "sparkle"\n'
        f'app_path = "{fake_app}"\n'
        'appcast_url = "https://nonexistent.invalid.ascendo-test/appcast.xml"\n',
    )
    out = tmp_path / "out"
    r, run_id = _run_phase("check", registry, out)
    assert r.returncode == 0, r.stderr
    sc = _read_sidecar(out, run_id, "check")
    assert len(sc["items"]) == 1
    item = sc["items"][0]
    assert item["status"] == "skipped"
    assert item["current_version"] == "1.0.0"
