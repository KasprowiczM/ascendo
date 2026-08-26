"""Tests for adapters/macos/scripts/softwareupdate/apply.sh.

The mutating phase. Mirrors test_apply_mas_script.py harness pattern:
fake sudo binary logs argv to a tempfile so tests can assert on the
sudo invocation shape; fake softwareupdate binary feeds canned text
fixtures for the discovery (`-l`) call.

Six tests:
  1. --dry-run emits planned items, NEVER invokes sudo
  2. real apply invokes `sudo -A softwareupdate -ir -R --verbose` (default)
  3. --all switches to `-ia` instead of `-ir`
  4. --filter LABEL passes -i <LABEL> -R --verbose
  5. no-updates fixture -> exit 0, no sudo invocation
  6. softwareupdate -l failure -> exit 30, no sudo invocation
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
SCRIPT = ADAPTER_ROOT / "scripts" / "softwareupdate" / "apply.sh"
FIX = ADAPTER_ROOT / "tests" / "fixtures" / "softwareupdate"


@pytest.fixture(autouse=True)
def _require_jq() -> None:
    if shutil.which("jq") is None:
        pytest.skip("jq not on PATH")


def _make_fake_sudo(tmp_path: Path, *, log_path: Path) -> Path:
    """Fake sudo: logs argv to log_path, then forwards to argv[2:] (skipping -A)."""
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


def _make_fake_su(tmp_path: Path, *, fixture_name: str = "incremental-updates.txt",
                  fail_on_l: bool = False) -> Path:
    """Fake softwareupdate binary.

    `-l` returns the named fixture (or exits 1 if fail_on_l=True).
    `-i ...` (the install path) succeeds silently — we don't care about
    the install side effect; the test asserts via the captured sudo log.
    `--help` returns a stub.
    """
    p = tmp_path / "fake_softwareupdate"
    if fail_on_l:
        body = (
            "#!/usr/bin/env bash\n"
            "if [ \"$1\" = '--help' ] || [ \"$1\" = '-h' ]; then\n"
            "    echo 'fake-su test'; exit 0\n"
            "fi\n"
            "if [ \"$1\" = '-l' ] || [ \"$1\" = '--list' ]; then\n"
            "    echo 'broken' >&2; exit 1\n"
            "fi\n"
            "exit 0\n"
        )
    else:
        fixture = (FIX / fixture_name).read_text()
        body = (
            "#!/usr/bin/env bash\n"
            "if [ \"$1\" = '--help' ] || [ \"$1\" = '-h' ]; then\n"
            "    echo 'fake-su test'; exit 0\n"
            "fi\n"
            "if [ \"$1\" = '-l' ] || [ \"$1\" = '--list' ]; then\n"
            f"    cat <<'EOF_SU'\n{fixture}\nEOF_SU\n"
            "    exit 0\n"
            "fi\n"
            # Install path: simulate success, just print
            "echo \"==> simulated install: $@\"\n"
            "exit 0\n"
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


def _run_apply(fake_su: Path, fake_sudo: Path, output_dir: Path,
               run_id: str, *extra: str):
    """Run apply.sh with fake binaries on PATH so `sudo` resolves to the fake."""
    env = dict(os.environ)
    env["SU_BIN"] = str(fake_su)
    bindir = fake_sudo.parent
    env["PATH"] = f"{bindir}:{env['PATH']}"
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
    fake_su = _make_fake_su(tmp_path, fixture_name="incremental-updates.txt")
    out = tmp_path / "out"
    rid = str(uuid.uuid4())
    res = _run_apply(fake_su, fake_sudo, out, rid, "--dry-run")
    assert res.returncode == 0, res.stderr

    sc = _parse(out / rid / "apply__softwareupdate.json")
    statuses = {i.status.value for i in sc.items}
    assert statuses <= {"planned"}, f"expected only planned items, got {statuses}"
    # Sudo MUST NOT have been invoked
    assert not sudo_log.exists() or sudo_log.read_text() == ""


def test_real_apply_invokes_sudo_a_softwareupdate_ir(tmp_path):
    """Default invocation: `sudo -A softwareupdate -i -r -R --verbose`."""
    sudo_log = tmp_path / "sudo.log"
    fake_sudo = _make_fake_sudo(tmp_path, log_path=sudo_log)
    fake_su = _make_fake_su(tmp_path, fixture_name="incremental-updates.txt")
    out = tmp_path / "out"
    rid = str(uuid.uuid4())
    res = _run_apply(fake_su, fake_sudo, out, rid)
    # Exit 0 (no Action: restart in incremental fixture)
    assert res.returncode == 0, res.stderr

    log_lines = sudo_log.read_text().strip().splitlines()
    # _ascendo_sudo picks `-A` (askpass) or plain sudo (TTY-PAM /
    # Touch-ID-only) by env. Both flows MUST keep -i -r -R --verbose
    # — the -R flag is mandatory per the legacy macOS update_system.sh
    # rule.
    assert any(
        "-i" in line and "-r" in line
        and "-R" in line and "--verbose" in line
        and "--include-config-data" in line
        for line in log_lines
    ), f"sudo log lacks -i -r -R --verbose --include-config-data: {log_lines}"


def test_all_flag_invokes_dash_a_not_dash_r(tmp_path):
    """--all switches default `-ir` to `-ia` (all available, not just recommended)."""
    sudo_log = tmp_path / "sudo.log"
    fake_sudo = _make_fake_sudo(tmp_path, log_path=sudo_log)
    fake_su = _make_fake_su(tmp_path, fixture_name="incremental-updates.txt")
    out = tmp_path / "out"
    rid = str(uuid.uuid4())
    res = _run_apply(fake_su, fake_sudo, out, rid, "--all")
    assert res.returncode == 0, res.stderr

    log_lines = sudo_log.read_text().strip().splitlines()
    # Must have -a flag, must NOT have standalone -r flag. -A askpass
    # prefix only present on dashboard flow; this test runs without
    # SUDO_ASKPASS so plain sudo is picked.
    assert any(
        " -a " in (" " + line + " ")
        and " -r " not in (" " + line + " ")
        for line in log_lines
    ), f"sudo log: expected -a but not -r, got: {log_lines}"


def test_filter_passes_label_to_softwareupdate(tmp_path):
    """--filter LABEL → `sudo -A softwareupdate -i <LABEL> -R --verbose`."""
    sudo_log = tmp_path / "sudo.log"
    fake_sudo = _make_fake_sudo(tmp_path, log_path=sudo_log)
    fake_su = _make_fake_su(tmp_path, fixture_name="incremental-updates.txt")
    out = tmp_path / "out"
    rid = str(uuid.uuid4())
    label = "Safari17.4-17.4"
    res = _run_apply(fake_su, fake_sudo, out, rid, "--filter", label)
    assert res.returncode == 0, res.stderr

    log_lines = sudo_log.read_text().strip().splitlines()
    # Sudo log should contain the label exactly once after -i
    assert any(label in line for line in log_lines), (
        f"sudo log missing label {label!r}: {log_lines}"
    )


def test_no_updates_exit_0_no_sudo(tmp_path):
    """When `softwareupdate -l` returns 'No new software available', exit 0,
    no sudo invocation."""
    sudo_log = tmp_path / "sudo.log"
    fake_sudo = _make_fake_sudo(tmp_path, log_path=sudo_log)
    fake_su = _make_fake_su(tmp_path, fixture_name="no-updates.txt")
    out = tmp_path / "out"
    rid = str(uuid.uuid4())
    res = _run_apply(fake_su, fake_sudo, out, rid)
    assert res.returncode == 0, res.stderr
    sc = _parse(out / rid / "apply__softwareupdate.json")
    assert sc.items == []
    assert not sudo_log.exists() or sudo_log.read_text() == ""


def test_softwareupdate_l_failure_exit_30_no_sudo(tmp_path):
    """When `softwareupdate -l` exits non-zero, abort BEFORE sudo with exit 30."""
    sudo_log = tmp_path / "sudo.log"
    fake_sudo = _make_fake_sudo(tmp_path, log_path=sudo_log)
    fake_su = _make_fake_su(tmp_path, fail_on_l=True)
    out = tmp_path / "out"
    rid = str(uuid.uuid4())
    res = _run_apply(fake_su, fake_sudo, out, rid)
    # Exit 30 (apply-fail-unknown — softwareupdate -l discovery failed)
    assert res.returncode == 30
    sc = _parse(out / rid / "apply__softwareupdate.json")
    assert sc.status.value == "failed"
    assert any(m.level.value == "error" for m in sc.messages)
    # Sudo MUST NOT have been invoked (we abort before the sudo call)
    assert not sudo_log.exists() or sudo_log.read_text() == ""


def test_restart_required_exits_75(tmp_path):
    """When apply succeeds and any item has Action: restart, exit 75 (NEEDS_REBOOT)."""
    sudo_log = tmp_path / "sudo.log"
    fake_sudo = _make_fake_sudo(tmp_path, log_path=sudo_log)
    fake_su = _make_fake_su(tmp_path, fixture_name="restart-required.txt")
    out = tmp_path / "out"
    rid = str(uuid.uuid4())
    res = _run_apply(fake_su, fake_sudo, out, rid)
    # Exit 75 = needs reboot (success, but operator must reboot)
    assert res.returncode == 75, res.stderr
    sc = _parse(out / rid / "apply__softwareupdate.json")
    assert sc.needs_reboot is True
