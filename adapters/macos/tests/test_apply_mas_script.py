"""Tests for adapters/macos/scripts/mas/apply.sh.

The mutating phase. We test:
  1. --dry-run produces planned items, NEVER invokes sudo
  2. real apply path invokes `sudo -A mas upgrade` (validated via fake sudo)
  3. signed-out fail-fast (no sudo invocation)
  4. --filter restricts upgrades to listed ids
  5. successful apply emits items with status=success
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ADAPTER_ROOT / "scripts" / "mas" / "apply.sh"
FIX = ADAPTER_ROOT / "tests" / "fixtures"


@pytest.fixture(autouse=True)
def _require_jq() -> None:
    if shutil.which("jq") is None:
        pytest.skip("jq not on PATH")


def _make_fake_sudo(tmp_path: Path, *, log_path: Path) -> Path:
    """Fake sudo that logs argv and forwards to argv[2:] if argv[1] == '-A',
    else argv[1:]. Always exits 0."""
    p = tmp_path / "fake_sudo"
    body = (
        "#!/usr/bin/env bash\n"
        f"echo \"$@\" >> {log_path}\n"
        "if [ \"$1\" = '-A' ]; then shift; fi\n"
        "\"$@\"\n"
    )
    p.write_text(body)
    os.chmod(p, 0o755)
    return p


def _make_fake_mas(tmp_path: Path, *, signed_in: bool,
                   outdated_text: str = "", upgrade_log: Path | None = None) -> Path:
    list_text = (FIX / "mas-list.txt").read_text()
    p = tmp_path / "fake_mas"
    if not signed_in:
        body = "#!/usr/bin/env bash\nexit 1\n"
    else:
        body = (
            "#!/usr/bin/env bash\n"
            f"[ -n \"{upgrade_log or ''}\" ] && echo \"$@\" >> {upgrade_log or '/dev/null'}\n"
            "case \"$1\" in\n"
            f"  list)     cat <<'EOF_LIST'\n{list_text}EOF_LIST\n            ;;\n"
            f"  outdated) cat <<'EOF_OUT'\n{outdated_text}EOF_OUT\n            ;;\n"
            "  upgrade)  shift; for id in \"$@\"; do echo \"==> upgrading $id\"; done; exit 0 ;;\n"
            "  version)  echo '4.3.0' ;;\n"
            "  *) exit 1 ;;\n"
            "esac\n"
        )
    p.write_text(body)
    os.chmod(p, 0o755)
    return p


def _parse(p: Path):
    sys.path.insert(0, str(ADAPTER_ROOT.parent.parent / "core"))
    try:
        from ascendo.models.sidecar import parse_sidecar
        return parse_sidecar(p.read_text())
    finally:
        sys.path.pop(0)


def _run_apply(fake_mas: Path, fake_sudo: Path, output_dir: Path,
               run_id: str, *extra: str):
    env = dict(os.environ)
    env["MAS_BIN"] = str(fake_mas)
    # Prepend a dir containing fake sudo to PATH
    bindir = fake_sudo.parent
    env["PATH"] = f"{bindir}:{env['PATH']}"
    # Rename our fake to literally "sudo" so PATH lookup finds it first
    sudo_link = bindir / "sudo"
    if not sudo_link.exists():
        sudo_link.symlink_to(fake_sudo.name)
    return subprocess.run(
        ["bash", str(SCRIPT),
         "--run-id", run_id, "--trigger", "cli",
         "--profile", "default", "--output-dir", str(output_dir),
         *extra],
        capture_output=True, text=True, env=env, check=False,
    )


def test_dry_run_emits_planned_items_no_sudo(tmp_path):
    sudo_log = tmp_path / "sudo.log"
    fake_sudo = _make_fake_sudo(tmp_path, log_path=sudo_log)
    fake_mas = _make_fake_mas(tmp_path, signed_in=True,
                              outdated_text=(FIX / "mas-outdated.txt").read_text())
    out = tmp_path / "out"
    rid = str(uuid.uuid4())
    res = _run_apply(fake_mas, fake_sudo, out, rid, "--dry-run")
    assert res.returncode == 0, res.stderr

    sc = _parse(out / rid / "apply__mas.json")
    statuses = {i.status.value for i in sc.items}
    assert statuses <= {"planned"}
    assert sudo_log.exists() is False or sudo_log.read_text() == ""


def test_real_apply_invokes_sudo_a_mas_upgrade(tmp_path):
    sudo_log = tmp_path / "sudo.log"
    upgrade_log = tmp_path / "upgrade.log"
    fake_sudo = _make_fake_sudo(tmp_path, log_path=sudo_log)
    fake_mas = _make_fake_mas(tmp_path, signed_in=True,
                              outdated_text=(FIX / "mas-outdated.txt").read_text(),
                              upgrade_log=upgrade_log)
    out = tmp_path / "out"
    rid = str(uuid.uuid4())
    res = _run_apply(fake_mas, fake_sudo, out, rid)
    assert res.returncode == 0, res.stderr

    # First sudo call must START with `-A` (CVE-2025-43411: -A must be first arg)
    log_lines = sudo_log.read_text().strip().splitlines()
    assert any(line.startswith("-A ") and "upgrade" in line for line in log_lines), log_lines


def test_signed_out_fail_fast_no_sudo(tmp_path):
    sudo_log = tmp_path / "sudo.log"
    fake_sudo = _make_fake_sudo(tmp_path, log_path=sudo_log)
    fake_mas = _make_fake_mas(tmp_path, signed_in=False)
    out = tmp_path / "out"
    rid = str(uuid.uuid4())
    res = _run_apply(fake_mas, fake_sudo, out, rid)
    assert res.returncode == 0  # phase exits 0; failure in sidecar
    sc = _parse(out / rid / "apply__mas.json")
    assert sc.status.value == "failed"
    assert any(i.id == "mas:not-signed-in" for i in sc.items)
    assert (not sudo_log.exists()) or sudo_log.read_text() == ""


def test_filter_restricts_to_listed_ids(tmp_path):
    sudo_log = tmp_path / "sudo.log"
    upgrade_log = tmp_path / "upgrade.log"
    fake_sudo = _make_fake_sudo(tmp_path, log_path=sudo_log)
    fake_mas = _make_fake_mas(tmp_path, signed_in=True,
                              outdated_text=(FIX / "mas-outdated.txt").read_text(),
                              upgrade_log=upgrade_log)
    out = tmp_path / "out"
    rid = str(uuid.uuid4())
    res = _run_apply(fake_mas, fake_sudo, out, rid,
                     "--filter", "1153157709")
    assert res.returncode == 0, res.stderr

    # Only Keka was upgraded; 1Password was not
    upg = upgrade_log.read_text() if upgrade_log.exists() else ""
    assert "1153157709" in upg
    assert "1333542190" not in upg


def test_apply_emits_success_items_for_upgraded(tmp_path):
    sudo_log = tmp_path / "sudo.log"
    fake_sudo = _make_fake_sudo(tmp_path, log_path=sudo_log)
    fake_mas = _make_fake_mas(tmp_path, signed_in=True,
                              outdated_text=(FIX / "mas-outdated.txt").read_text())
    out = tmp_path / "out"
    rid = str(uuid.uuid4())
    res = _run_apply(fake_mas, fake_sudo, out, rid)
    assert res.returncode == 0, res.stderr
    sc = _parse(out / rid / "apply__mas.json")
    statuses = [i.status.value for i in sc.items]
    assert any(s == "success" for s in statuses)


def test_apply_no_outdated_emits_no_items_no_sudo(tmp_path):
    """When mas outdated returns empty, no sudo invocation, sidecar has zero items."""
    sudo_log = tmp_path / "sudo.log"
    fake_sudo = _make_fake_sudo(tmp_path, log_path=sudo_log)
    fake_mas = _make_fake_mas(tmp_path, signed_in=True, outdated_text="")
    out = tmp_path / "out"
    rid = str(uuid.uuid4())
    res = _run_apply(fake_mas, fake_sudo, out, rid)
    assert res.returncode == 0, res.stderr
    sc = _parse(out / rid / "apply__mas.json")
    assert sc.items == []
    assert (not sudo_log.exists()) or sudo_log.read_text() == ""
