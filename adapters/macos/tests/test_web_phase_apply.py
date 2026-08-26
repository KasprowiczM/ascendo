"""apply.sh tests."""
from __future__ import annotations

import json
import os
import subprocess
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = REPO_ROOT / "adapters" / "macos" / "scripts" / "web"


def _make_fake_app(tmp_path: Path, name: str, version: str = "1.0.0") -> Path:
    app = tmp_path / "Applications" / f"{name}.app"
    (app / "Contents").mkdir(parents=True, exist_ok=True)
    (app / "Contents" / "Info.plist").write_text(
        '<?xml version="1.0"?><plist version="1.0"><dict>'
        '<key>CFBundleShortVersionString</key>'
        f'<string>{version}</string></dict></plist>'
    )
    return app


def _run_apply(registry: Path, output_dir: Path, *,
              env_extra: dict | None = None, filter_arg: str | None = None,
              dry_run: bool = False):
    run_id = str(uuid.uuid4())
    env = {**os.environ,
           "ASCENDO_WEB_REGISTRY_PATH": str(registry),
           "ASCENDO_WEB_USER_REGISTRY_PATH": "",
           "ASCENDO_SUDO_WARM_DISABLE": "1",
           "PYTHONPATH": f"{REPO_ROOT}/core:{REPO_ROOT}/adapters/macos"}
    if env_extra:
        env.update(env_extra)
    cmd = ["bash", str(SCRIPTS / "apply.sh"),
           "--run-id", run_id, "--trigger", "cli", "--profile", "full",
           "--output-dir", str(output_dir)]
    if dry_run:
        cmd.append("--dry-run")
    if filter_arg:
        cmd += ["--filter", filter_arg]
    return subprocess.run(cmd, capture_output=True, text=True, env=env), run_id


def _read_sidecar(output_dir: Path, run_id: str) -> dict:
    return json.loads((output_dir / run_id / "apply__web.json").read_text())


def test_apply_squirrel_invokes_open(tmp_path: Path) -> None:
    fake_app = _make_fake_app(tmp_path, "Slack")
    log = tmp_path / "open.log"
    fake_open = tmp_path / "open"
    fake_open.write_text(f"#!/bin/sh\necho \"$@\" >> {log}\nexit 0\n")
    fake_open.chmod(0o755)
    registry = tmp_path / "r.toml"
    registry.write_text(
        'schema = "ascendo-web-apps/v1"\n'
        '[[app]]\nslug = "slack"\nbundle_id = "com.zzz-not-running.test"\n'
        f'display_name = "Slack"\nhandler = "squirrel"\napp_path = "{fake_app}"\n',
    )
    out = tmp_path / "out"
    r, run_id = _run_apply(
        registry, out,
        env_extra={
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
        },
    )
    assert r.returncode == 0, r.stderr
    sc = _read_sidecar(out, run_id)
    assert len(sc["items"]) == 1
    item = sc["items"][0]
    # M5.7 T9: Tier-B handlers (squirrel/keystone/builtin) emit 'triggered'
    # instead of 'success' because the actual update is async (Squirrel
    # self-updates on next quit/relaunch).
    assert item["status"] == "triggered"
    assert log.exists()
    assert str(fake_app) in log.read_text()


def test_apply_dry_run_emits_planned_no_mutation(tmp_path: Path) -> None:
    fake_app = _make_fake_app(tmp_path, "Slack")
    log = tmp_path / "open.log"
    fake_open = tmp_path / "open"
    fake_open.write_text(f"#!/bin/sh\necho \"$@\" >> {log}\nexit 0\n")
    fake_open.chmod(0o755)
    registry = tmp_path / "r.toml"
    registry.write_text(
        'schema = "ascendo-web-apps/v1"\n'
        '[[app]]\nslug = "slack"\nbundle_id = "com.zzz.notrunning"\n'
        f'display_name = "Slack"\nhandler = "squirrel"\napp_path = "{fake_app}"\n',
    )
    out = tmp_path / "out"
    r, run_id = _run_apply(
        registry, out,
        env_extra={"PATH": f"{tmp_path}:{os.environ['PATH']}"},
        dry_run=True,
    )
    assert r.returncode == 0, r.stderr
    sc = _read_sidecar(out, run_id)
    assert sc["items"][0]["status"] == "planned"
    # Dry run should NOT have invoked our fake `open`
    assert not log.exists() or log.read_text().strip() == ""


def test_apply_falls_back_to_bundle_resolver_when_registry_path_wrong(
    tmp_path: Path,
) -> None:
    """Registry uses canonical names (Docker Desktop.app) but user has
    short names (Docker.app). Apply should resolve via bundle_id and
    still update the app — not silently mark it skipped."""
    # Real app on disk at "Docker.app", but registry says "Docker Desktop.app"
    real_app = _make_fake_app(tmp_path, "Docker", version="4.73.0")
    # Add a CFBundleIdentifier so mdfind/system_profiler can match it.
    plist = real_app / "Contents" / "Info.plist"
    plist.write_text(
        '<?xml version="1.0"?><plist version="1.0"><dict>'
        '<key>CFBundleShortVersionString</key><string>4.73.0</string>'
        '<key>CFBundleIdentifier</key><string>com.test.fakedocker</string>'
        '</dict></plist>'
    )
    fake_open = tmp_path / "open"
    fake_open.write_text("#!/bin/sh\nexit 0\n")
    fake_open.chmod(0o755)
    # Pre-build the system_profiler cache with our fake app so the
    # resolver finds it without depending on the host's real apps.
    sp_cache = tmp_path / "sp.json"
    sp_cache.write_text(json.dumps({
        "SPApplicationsDataType": [{"_name": "Docker", "path": str(real_app)}],
    }))
    registry = tmp_path / "r.toml"
    # Note: app_path points at the WRONG path on purpose to simulate
    # the operator-reported bug.
    registry.write_text(
        'schema = "ascendo-web-apps/v1"\n'
        '[[app]]\nslug = "docker"\nbundle_id = "com.test.fakedocker"\n'
        f'display_name = "Docker Desktop"\nhandler = "squirrel"\n'
        f'app_path = "{tmp_path}/Applications/Docker Desktop.app"\n'
    )
    out = tmp_path / "out"
    r, run_id = _run_apply(
        registry, out,
        env_extra={
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "ASCENDO_WEB_SP_CACHE": str(sp_cache),
        },
        dry_run=True,
    )
    assert r.returncode == 0, r.stderr
    sc = _read_sidecar(out, run_id)
    item = next(i for i in sc["items"] if i["id"] == "web:docker")
    # Pre-fix would have been "skipped" with not_installed message.
    # Post-fix: resolver finds Docker.app via bundle_id, reads version
    # 4.73.0, emits "planned" in dry-run.
    assert item["status"] == "planned", item
    assert item["current_version"] == "4.73.0"


def test_apply_filter_limits_to_one_app(tmp_path: Path) -> None:
    fake_a = _make_fake_app(tmp_path, "AppA")
    fake_b = _make_fake_app(tmp_path, "AppB")
    log = tmp_path / "open.log"
    fake_open = tmp_path / "open"
    fake_open.write_text(f"#!/bin/sh\necho \"$@\" >> {log}\nexit 0\n")
    fake_open.chmod(0o755)
    registry = tmp_path / "r.toml"
    registry.write_text(
        'schema = "ascendo-web-apps/v1"\n'
        '[[app]]\nslug = "appa"\nbundle_id = "com.zzz.appa"\n'
        f'display_name = "AppA"\nhandler = "squirrel"\napp_path = "{fake_a}"\n'
        '[[app]]\nslug = "appb"\nbundle_id = "com.zzz.appb"\n'
        f'display_name = "AppB"\nhandler = "squirrel"\napp_path = "{fake_b}"\n',
    )
    out = tmp_path / "out"
    r, run_id = _run_apply(
        registry, out,
        env_extra={"PATH": f"{tmp_path}:{os.environ['PATH']}"},
        filter_arg="appa",
    )
    assert r.returncode == 0, r.stderr
    sc = _read_sidecar(out, run_id)
    item_ids = {i["id"] for i in sc["items"]}
    assert "web:appa" in item_ids
    assert "web:appb" not in item_ids
