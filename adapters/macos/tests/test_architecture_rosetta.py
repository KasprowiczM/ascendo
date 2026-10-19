"""Architecture / Rosetta awareness (Task 17, Spec F).

Fake lipo / defaults / pgrep on PATH; ASCENDO_ROSETTA_DIR points at a temp dir.
softwareupdate --install-rosetta is NEVER real: the fake softwareupdate from
su_fakes records the call.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import uuid
from pathlib import Path

import pytest

from . import test_web_phase_check_plan as W
from .su_fakes import su_env, _write_exec

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
ARCH_LIB = ADAPTER_ROOT / "lib" / "ascendo_arch.sh"
SU_APPLY = ADAPTER_ROOT / "scripts" / "softwareupdate" / "apply.sh"


def _fakebin(tmp_path: Path, archs: str, oahd: bool = False) -> Path:
    b = tmp_path / "archbin"
    b.mkdir(exist_ok=True)
    _write_exec(b / "lipo", f"#!/bin/sh\necho '{archs}'\n")
    _write_exec(b / "defaults", "#!/bin/sh\necho Exe\n")
    _write_exec(b / "pgrep", f"#!/bin/sh\nexit {0 if oahd else 1}\n")
    return b


def _bundle(tmp_path: Path) -> Path:
    app = tmp_path / "Intel.app"
    (app / "Contents" / "MacOS").mkdir(parents=True)
    (app / "Contents" / "Info.plist").write_text("<plist/>")
    (app / "Contents" / "MacOS" / "Exe").write_text("x")
    return app


def _sh(tmp_path, body, archs="x86_64", oahd=False, **env):
    e = dict(os.environ)
    e["PATH"] = f"{_fakebin(tmp_path, archs, oahd)}:{e['PATH']}"
    e["ASCENDO_ROSETTA_DIR"] = str(tmp_path / "no-rosetta")
    e.pop("ASCENDO_ROSETTA_PRESENT", None)
    e.update(env)
    return subprocess.run(["bash", "-c", f'. "{ARCH_LIB}"; {body}'], capture_output=True,
                          text=True, env=e)


@pytest.mark.parametrize("archs,want", [
    ("x86_64", "x86_64-only"), ("i386", "x86_64-only"),
    ("arm64", "arm64"), ("x86_64 arm64", "universal"), ("arm64 x86_64", "universal"),
    ("", "unknown"),
])
def test_app_architecture(tmp_path, archs, want):
    app = _bundle(tmp_path)
    r = _sh(tmp_path, f'ascendo_app_architecture "{app}"', archs=archs)
    assert r.stdout.strip() == want, r.stderr


def test_architecture_unknown_for_missing_bundle(tmp_path):
    r = _sh(tmp_path, f'ascendo_app_architecture "{tmp_path}/nope.app"')
    assert r.stdout.strip() == "unknown"


def test_rosetta_installed_by_dir_or_oahd_or_seam(tmp_path):
    assert _sh(tmp_path, "ascendo_rosetta_installed").returncode == 1
    (tmp_path / "no-rosetta").mkdir()
    assert _sh(tmp_path, "ascendo_rosetta_installed").returncode == 0
    shutil.rmtree(tmp_path / "no-rosetta")
    assert _sh(tmp_path, "ascendo_rosetta_installed", oahd=True).returncode == 0
    assert _sh(tmp_path, "ascendo_rosetta_installed", ASCENDO_ROSETTA_PRESENT="1").returncode == 0
    assert _sh(tmp_path, "ascendo_rosetta_installed", oahd=True,
               ASCENDO_ROSETTA_PRESENT="0").returncode == 1


def test_rosetta_needed_only_on_apple_silicon_intel_app_without_rosetta(tmp_path):
    app = _bundle(tmp_path)
    cmd = f'ascendo_rosetta_needed "{app}"'
    assert _sh(tmp_path, cmd, ASCENDO_HOST_ARCH="arm64").returncode == 0
    assert _sh(tmp_path, cmd, ASCENDO_HOST_ARCH="x86_64").returncode == 1
    assert _sh(tmp_path, cmd, ASCENDO_HOST_ARCH="arm64", ASCENDO_ROSETTA_PRESENT="1").returncode == 1
    assert _sh(tmp_path, cmd, archs="x86_64 arm64", ASCENDO_HOST_ARCH="arm64").returncode == 1


# -- web check -----------------------------------------------------------------


def _web_check(tmp_path, rosetta_present: str):
    app = W._make_fake_app(tmp_path, "Slack", "4.40.0", bundle_id="com.tinyspeck.slackmacgap")
    exe = app / "Contents" / "MacOS"
    exe.mkdir(parents=True, exist_ok=True)
    (exe / "Exe").write_text("x")
    registry = tmp_path / "registry.toml"
    registry.write_text(
        'schema = "ascendo-web-apps/v1"\n'
        '[[app]]\nslug = "slack"\nbundle_id = "com.tinyspeck.slackmacgap"\n'
        'display_name = "Slack"\nhandler = "squirrel"\n'
        f'app_path = "{app}"\n')
    out = tmp_path / "out"
    r, rid = W._run_phase("check", registry, out, env_extra={
        "PATH": f"{_fakebin(tmp_path, 'x86_64')}:{os.environ['PATH']}",
        "ASCENDO_HOST_ARCH": "arm64", "ASCENDO_ROSETTA_PRESENT": rosetta_present})
    assert r.returncode == 0, r.stderr
    return W._read_sidecar(out, rid, "check")


def test_web_check_flags_intel_only_without_rosetta(tmp_path):
    sc = _web_check(tmp_path, "0")
    texts = [m["text"] for m in sc["messages"]]
    assert any("Intel-only (x86_64); needs Rosetta" in t for t in texts)
    ar = [t for t in texts if "reason=rosetta_missing" in t]
    assert ar and ar[0].startswith("slack: ")
    assert sc["items"][0]["status"] == "skipped"        # version flow unchanged


def test_web_check_intel_only_with_rosetta_is_info_only(tmp_path):
    sc = _web_check(tmp_path, "1")
    texts = [m["text"] for m in sc["messages"]]
    assert any("Intel-only (x86_64)" in t for t in texts)
    assert not any("rosetta_missing" in t for t in texts)


# -- softwareupdate --install-rosetta ------------------------------------------


def _su(tmp_path, *, profile="full", flag="1", extra=(), make_rosetta=False, **env):
    ctx = su_env(tmp_path, "no-updates.txt")
    if make_rosetta:
        su = tmp_path / "fake_softwareupdate"
        su.write_text(su.read_text().replace(
            'if args[:1] in (["--help"], ["-h"]):',
            f'if args[:1] == ["--install-rosetta"]:\n    os.makedirs({str(tmp_path / "no-rosetta")!r}, exist_ok=True); sys.exit(0)\n'
            'if args[:1] in (["--help"], ["-h"]):', 1))
    ctx["env"]["ASCENDO_HOST_ARCH"] = "arm64"
    ctx["env"]["ASCENDO_ROSETTA_DIR"] = str(tmp_path / "no-rosetta")
    ctx["env"]["ASCENDO_ROSETTA_PRESENT"] = "" if make_rosetta else "0"
    ctx["env"]["PATH"] = f"{_fakebin(tmp_path, 'x86_64')}:{ctx['env']['PATH']}"
    if flag is not None:
        ctx["env"]["ASCENDO_INSTALL_ROSETTA"] = flag
    ctx["env"].update(env)
    out = tmp_path / "out"
    rid = str(uuid.uuid4())
    r = subprocess.run(["bash", str(SU_APPLY), "--run-id", rid, "--trigger", "cli",
                        "--profile", profile, "--output-dir", str(out), *extra],
                       capture_output=True, text=True, env=ctx["env"])
    side = out / rid / "apply__softwareupdate.json"
    return ctx, r, (json.loads(side.read_text()) if side.exists() else None)


def test_rosetta_not_installed_without_opt_in(tmp_path):
    ctx, r, sc = _su(tmp_path, flag=None)
    assert r.returncode == 0, r.stderr
    assert ctx["installs"]() == []
    assert not any(i["id"] == "rosetta:install" for i in sc["items"])


def test_rosetta_not_installed_under_safe_profile(tmp_path):
    ctx, r, sc = _su(tmp_path, profile="safe")
    assert ctx["installs"]() == []
    assert not any(i["id"] == "rosetta:install" for i in sc["items"])


def test_rosetta_dry_run_is_planned_only(tmp_path):
    ctx, r, sc = _su(tmp_path, extra=("--dry-run",))
    assert [i["status"] for i in sc["items"] if i["id"] == "rosetta:install"] == ["planned"]
    assert ctx["sudo_log"].exists() is False or ctx["sudo_log"].read_text() == ""


def test_rosetta_install_success_needs_post_measurement(tmp_path):
    # The fake softwareupdate does not create the Rosetta dir -> not measured -> failed.
    ctx, r, sc = _su(tmp_path)
    log = ctx["sudo_log"].read_text()
    assert "--install-rosetta --agree-to-license" in log
    assert [i["status"] for i in sc["items"] if i["id"] == "rosetta:install"] == ["failed"]


def test_rosetta_install_success_when_rosetta_appears(tmp_path):
    # Fake --install-rosetta creates the Rosetta dir -> post-measurement passes.
    ctx, r, sc = _su(tmp_path, make_rosetta=True)
    assert "--install-rosetta --agree-to-license" in ctx["sudo_log"].read_text()
    assert [i["status"] for i in sc["items"] if i["id"] == "rosetta:install"] == ["success"]


def test_rosetta_install_honours_no_sudo(tmp_path):
    ctx, r, sc = _su(tmp_path, ASCENDO_NO_SUDO="1")
    assert ctx["sudo_log"].exists() is False or "install-rosetta" not in ctx["sudo_log"].read_text()
    it = [i for i in sc["items"] if i["id"] == "rosetta:install"]
    assert it and it[0]["status"] == "skipped"
    assert any("SUDO_UNAVAILABLE" in (m.get("code") or "") or "SUDO_UNAVAILABLE" in m["text"]
               for m in sc["messages"])


def test_rosetta_not_installed_for_non_full_profile_name(tmp_path):
    ctx, r, sc = _su(tmp_path, profile="default")
    assert ctx["installs"]() == []
    assert not any(i["id"] == "rosetta:install" for i in sc["items"])


def test_verify_skips_rosetta_pseudo_label(tmp_path):
    verify = ADAPTER_ROOT / "scripts" / "softwareupdate" / "verify.sh"
    ctx = su_env(tmp_path, "no-updates.txt")
    out = tmp_path / "out"
    rid = str(uuid.uuid4())
    (out / rid).mkdir(parents=True)
    (out / rid / "apply__softwareupdate.json").write_text(json.dumps({"items": [
        {"id": "rosetta:install", "status": "success"}]}))
    r = subprocess.run(["bash", str(verify), "--run-id", rid, "--trigger", "cli", "--profile", "full",
                        "--output-dir", str(out)], capture_output=True, text=True, env=ctx["env"])
    assert r.returncode == 0, r.stderr
    sc = json.loads((out / rid / "verify__softwareupdate.json").read_text())
    assert not any("rosetta" in i["id"] for i in sc["items"])


def test_arch_single_detector_lipo_wins_sp_is_fallback(tmp_path):
    app = _bundle(tmp_path)
    # lipo says universal: system_profiler's arch_i64 must NOT make it Intel-only.
    r = _sh(tmp_path, f'ascendo_is_intel_only "{app}" arch_i64; echo $?', archs="x86_64 arm64")
    assert r.stdout.strip() == "1"
    # lipo cannot decide (no executable): fall back to system_profiler's arch_kind.
    bare = tmp_path / "Bare.app"
    bare.mkdir()
    r = _sh(tmp_path, f'ascendo_app_architecture "{bare}" arch_i64')
    assert r.stdout.strip() == "x86_64-only"
    r = _sh(tmp_path, f'ascendo_app_architecture "{bare}"')
    assert r.stdout.strip() == "unknown"
