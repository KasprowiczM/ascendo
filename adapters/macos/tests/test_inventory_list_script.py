"""Tests for adapters/macos/scripts/inventory/list.sh.

Six integration tests using a fake system_profiler binary that returns
the canned fixture JSON. Bash-only execution path (no Python under test).
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
SCRIPT = ADAPTER_ROOT / "scripts" / "inventory" / "list.sh"
FIX = ADAPTER_ROOT / "tests" / "fixtures"


@pytest.fixture(autouse=True)
def _require_jq() -> None:
    if shutil.which("jq") is None:
        pytest.skip("jq not on PATH")


def _make_fake_sp(tmp_path: Path) -> Path:
    """Fake system_profiler binary returning the canned fixture JSON."""
    fixture = (FIX / "system_profiler_apps.json").read_text()
    p = tmp_path / "fake_system_profiler"
    body = (
        "#!/usr/bin/env bash\n"
        "if [ \"$1\" = '--version' ]; then\n"
        "    echo 'system_profiler test-fake 1.0'\n"
        "    exit 0\n"
        "fi\n"
        f"cat <<'EOF_SP'\n{fixture}\nEOF_SP\n"
    )
    p.write_text(body)
    os.chmod(p, 0o755)
    return p


def _make_fake_mas(tmp_path: Path, *, app_names: list[str] | None = None) -> Path:
    """Fake mas binary returning a `mas list` style table for the given names."""
    p = tmp_path / "fake_mas"
    if app_names is None:
        app_names = ["Amphetamine", "iMovie", "KeePassium"]
    rows = "\n".join(
        f" {1000000000 + i:>10}  {name:<28}({i}.0)"
        for i, name in enumerate(app_names, start=1)
    )
    body = (
        "#!/usr/bin/env bash\n"
        "case \"$1\" in\n"
        f"  list)    cat <<'EOF_LIST'\n{rows}\nEOF_LIST\n           ;;\n"
        "  version) echo '6.0.1' ;;\n"
        "  *) exit 1 ;;\n"
        "esac\n"
    )
    p.write_text(body)
    os.chmod(p, 0o755)
    return p


def _make_fake_brew(tmp_path: Path, *, casks: list[str] | None = None) -> Path:
    """Fake brew binary returning `brew list --cask` for the given tokens."""
    p = tmp_path / "fake_brew"
    if casks is None:
        casks = ["inkscape", "macwhisper", "blackhole-2ch"]
    body = (
        "#!/usr/bin/env bash\n"
        "if [ \"$1\" = 'list' ] && [ \"$2\" = '--cask' ]; then\n"
        f"  printf '%s\\n' {' '.join(repr(c) for c in casks)}\n"
        "  exit 0\n"
        "fi\n"
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


def _run(script: Path, sp: Path, mas: Path | None, brew: Path | None,
         output_dir: Path, run_id: str, *extra: str):
    env = dict(os.environ)
    env["SP_BIN"] = str(sp)
    if mas is not None:
        env["MAS_BIN"] = str(mas)
    else:
        env["MAS_BIN"] = ""   # explicitly disabled -- prevents PATH fallback
    if brew is not None:
        env["BREW_BIN"] = str(brew)
    else:
        env["BREW_BIN"] = ""  # explicitly disabled -- prevents PATH fallback
    return subprocess.run(
        ["bash", str(script),
         "--run-id", run_id, "--trigger", "cli",
         "--profile", "default", "--output-dir", str(output_dir),
         *extra],
        capture_output=True, text=True, env=env, check=False,
    )


def test_emits_one_item_per_app(tmp_path):
    sp = _make_fake_sp(tmp_path)
    mas = _make_fake_mas(tmp_path)
    brew = _make_fake_brew(tmp_path)
    out = tmp_path / "out"
    rid = str(uuid.uuid4())
    res = _run(SCRIPT, sp, mas, brew, out, rid)
    assert res.returncode == 0, res.stderr
    sc = _parse(out / rid / "check__inventory.json")
    # 13 valid apps; the empty-path entry is skipped.
    assert len(sc.items) == 13
    assert sc.status.value == "success"


# Note: rule 2 (mas-name match) and rule 3 (obtained_from=mac_app_store)
# are co-active for all MAS fixture entries, so the test asserts the
# combined effect rather than each rule in isolation. test_no_mas_falls_through
# confirms rule 3 alone catches them when mas is absent. A future fixture
# addition (a MAS app with obtained_from=identified_developer) would
# exercise rule 2 in isolation.
def test_classification_distribution(tmp_path):
    sp = _make_fake_sp(tmp_path)
    mas = _make_fake_mas(tmp_path)
    brew = _make_fake_brew(tmp_path)
    out = tmp_path / "out"
    rid = str(uuid.uuid4())
    res = _run(SCRIPT, sp, mas, brew, out, rid)
    assert res.returncode == 0, res.stderr
    sc = _parse(out / rid / "check__inventory.json")
    by_source: dict[str, int] = {}
    for item in sc.items:
        st = item.source.type.value
        by_source[st] = by_source.get(st, 0) + 1
    # SYSTEM: Safari, Mail, Calculator, Notes
    assert by_source.get("system", 0) == 4
    # MAS: Amphetamine, iMovie, KeePassium (rule 2 via _name match)
    assert by_source.get("mas", 0) == 3
    # BREW: Inkscape, MacWhisper, BlackHole 2ch (cask token match)
    assert by_source.get("brew", 0) == 3
    # WEB: Firefox, VLC, Custom Internal Tool
    assert by_source.get("web", 0) == 3


def test_no_mas_falls_through_to_obtained_from(tmp_path):
    """When mas is not on PATH, rule 2 doesn't fire; rule 3 still catches MAS apps via obtained_from."""
    sp = _make_fake_sp(tmp_path)
    brew = _make_fake_brew(tmp_path)
    out = tmp_path / "out"
    rid = str(uuid.uuid4())
    # Pass MAS_BIN as an empty/missing path so the script's check fails
    res = _run(SCRIPT, sp, None, brew, out, rid)
    assert res.returncode == 0, res.stderr
    sc = _parse(out / rid / "check__inventory.json")
    by_source: dict[str, int] = {}
    for item in sc.items:
        by_source[item.source.type.value] = by_source.get(item.source.type.value, 0) + 1
    # Same MAS count via rule 3 fallback (obtained_from=mac_app_store)
    assert by_source.get("mas", 0) == 3


def test_no_brew_falls_through_to_web(tmp_path):
    """When brew is not on PATH, would-be-BREW apps classify as WEB."""
    sp = _make_fake_sp(tmp_path)
    mas = _make_fake_mas(tmp_path)
    out = tmp_path / "out"
    rid = str(uuid.uuid4())
    res = _run(SCRIPT, sp, mas, None, out, rid)
    assert res.returncode == 0, res.stderr
    sc = _parse(out / rid / "check__inventory.json")
    by_source: dict[str, int] = {}
    for item in sc.items:
        by_source[item.source.type.value] = by_source.get(item.source.type.value, 0) + 1
    assert by_source.get("brew", 0) == 0
    # Inkscape + MacWhisper + BlackHole 2ch now classify as WEB,
    # plus the original 3 WEB apps (Firefox, VLC, Custom Internal Tool) -> 6 total
    assert by_source.get("web", 0) == 6


def test_per_item_metadata(tmp_path):
    sp = _make_fake_sp(tmp_path)
    mas = _make_fake_mas(tmp_path)
    brew = _make_fake_brew(tmp_path)
    out = tmp_path / "out"
    rid = str(uuid.uuid4())
    res = _run(SCRIPT, sp, mas, brew, out, rid)
    assert res.returncode == 0, res.stderr
    sc = _parse(out / rid / "check__inventory.json")
    safari = next(i for i in sc.items if i.id == "Safari")
    assert safari.source.type.value == "system"
    assert safari.current_version == "18.2"
    assert safari.target_version is None  # empty string -> None in Pydantic optional field
    assert safari.status.value == "up_to_date"
    # source.feed carries the bundle path
    assert safari.source.feed == "/System/Applications/Safari.app"


def test_system_profiler_failure_exits_30(tmp_path):
    """When system_profiler exits non-zero, script aborts with exit 30 + sidecar."""
    sp = tmp_path / "broken_sp"
    sp.write_text("#!/usr/bin/env bash\necho 'system_profiler crashed' >&2\nexit 1\n")
    os.chmod(sp, 0o755)
    out = tmp_path / "out"
    rid = str(uuid.uuid4())
    res = _run(SCRIPT, sp, None, None, out, rid)
    # Expect exit 30 (apply-fail-unknown per docs/agents/contract.md)
    assert res.returncode == 30
    # Sidecar still emitted via EXIT trap
    sc = _parse(out / rid / "check__inventory.json")
    assert sc.status.value == "failed"
    assert any(m.level.value == "error" for m in sc.messages)
