"""WebManager smoke tests - mock-based, runs on any OS.

Mirrors test_npm_manager_smoke.py shape. The Python manager just orchestrates
pwsh.exe + parses the sidecar; the real work is in the PowerShell scripts
which can't be unit-tested without pwsh. These tests verify identity,
availability, phase dispatch, argv shape, and sidecar parsing.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from ascendo.interfaces.package_manager import ManagerError
from ascendo.models.package import SourceType
from ascendo.models.run import Phase
from ascendo_windows.managers.web import WebManager


# ── Helpers ───────────────────────────────────────────────────────────


def _make(scripts_dir: Path, lib_dir: Path, config_dir: Path | None = None) -> WebManager:
    return WebManager(
        scripts_dir=scripts_dir,
        lib_dir=lib_dir,
        config_dir=config_dir,
        pwsh_path="/usr/bin/true",
        timeout_sec=30,
    )


def _run_completed(
    *,
    output_dir_arg: str,
    run_id: str,
    payload: dict[str, Any] | None,
    returncode: int,
    phase_value: str = "check",
) -> subprocess.CompletedProcess[str]:
    if payload is not None:
        target = Path(output_dir_arg) / run_id / f"{phase_value}__web.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload), encoding="utf-8")
    return subprocess.CompletedProcess(
        args=["pwsh", "-File", "fake.ps1"],
        returncode=returncode,
        stdout="ok\n",
        stderr="",
    )


def _extract_output_dir(argv: list[str]) -> str:
    return argv[argv.index("-OutputDir") + 1]


def _web_sidecar_payload(run, host, phase_value: str = "check") -> dict[str, Any]:
    return {
        "schema": "ascendo/v1",
        "run": json.loads(run.model_dump_json()),
        "host": json.loads(host.model_dump_json()),
        "tool": {
            "name": "ascendo-web",
            "version": "0.1.0",
            "binary_path": None,
        },
        "phase": phase_value,
        "category": "web",
        "started_at": "2026-05-12T18:00:00Z",
        "finished_at": "2026-05-12T18:00:05Z",
        "status": "success",
        "items": [
            {
                "id": "web:obsidian",
                "name": "Obsidian",
                "category": "web",
                "source": {"type": "web", "feed": None, "url": None},
                "current_version": "1.12.7",
                "target_version": "1.12.7",
                "resolved_version": None,
                "status": "up_to_date",
                "exit_code": None,
                "duration_ms": None,
                "evidence": None,
                "rollback": None,
                "messages": [],
            },
        ],
        "summary": {
            "total": 1,
            "success": 0,
            "up_to_date": 1,
            "failed": 0,
            "skipped": 0,
            "planned": 0,
            "partial": 0,
            "duration_ms": None,
            "exit_code": 0,
        },
        "messages": [],
    }


@pytest.fixture
def web_check_payload(run_info, windows_host):
    return _web_sidecar_payload(run_info, windows_host, phase_value="check")


@pytest.fixture
def fake_web_scripts(fake_scripts_dir: Path) -> Path:
    """Ensure scripts/web/<phase>.ps1 placeholder files exist."""
    d = fake_scripts_dir / "web"
    d.mkdir(parents=True, exist_ok=True)
    for ph in ("check", "plan", "apply", "verify", "cleanup"):
        (d / f"{ph}.ps1").write_text(
            f"# placeholder web/{ph}.ps1\n", encoding="utf-8",
        )
    return fake_scripts_dir


@pytest.fixture
def fake_config_dir(tmp_path: Path) -> Path:
    """A tmp config/ holding a minimal valid web_apps.toml."""
    d = tmp_path / "config"
    d.mkdir(parents=True, exist_ok=True)
    (d / "web_apps.toml").write_text(
        'schema = "ascendo-web-apps-windows/v1"\n\n'
        '[[app]]\n'
        'slug = "obsidian"\n'
        'display_name = "Obsidian"\n'
        'handler = "builtin"\n\n'
        '[app.builtin]\n'
        'url = "https://obsidian.md/download"\n',
        encoding="utf-8",
    )
    return d


# ── Identity ──────────────────────────────────────────────────────────


def test_category_is_web(fake_web_scripts, fake_lib_dir, fake_config_dir):
    assert _make(fake_web_scripts, fake_lib_dir, fake_config_dir).category is SourceType.WEB


def test_display_name_mentions_web(fake_web_scripts, fake_lib_dir, fake_config_dir):
    name = _make(fake_web_scripts, fake_lib_dir, fake_config_dir).display_name.lower()
    assert "web" in name or "installer" in name


# ── is_available ──────────────────────────────────────────────────────


def test_is_available_returns_false_on_non_windows(
    fake_web_scripts, fake_lib_dir, fake_config_dir, linux_host,
):
    manager = _make(fake_web_scripts, fake_lib_dir, fake_config_dir)
    assert manager.is_available(linux_host) is False


def test_is_available_true_when_registry_parses(
    fake_web_scripts, fake_lib_dir, fake_config_dir, windows_host,
):
    manager = _make(fake_web_scripts, fake_lib_dir, fake_config_dir)
    assert manager.is_available(windows_host) is True


def test_is_available_false_when_registry_missing(
    fake_web_scripts, fake_lib_dir, tmp_path, windows_host,
):
    """No web_apps.toml -> unavailable."""
    empty_config = tmp_path / "empty_config"
    empty_config.mkdir()
    manager = _make(fake_web_scripts, fake_lib_dir, empty_config)
    assert manager.is_available(windows_host) is False


def test_is_available_false_when_registry_malformed(
    fake_web_scripts, fake_lib_dir, tmp_path, windows_host,
):
    """Malformed TOML -> unavailable (Pydantic validation catches it)."""
    bad_config = tmp_path / "bad_config"
    bad_config.mkdir()
    (bad_config / "web_apps.toml").write_text(
        'schema = "ascendo-web-apps-windows/v1"\n\n'
        '[[app]]\n'
        'slug = "broken"\n'
        'display_name = "Broken"\n'
        'handler = "github_release"\n'
        '# missing [app.github_release] sub-table\n',
        encoding="utf-8",
    )
    manager = _make(fake_web_scripts, fake_lib_dir, bad_config)
    assert manager.is_available(windows_host) is False


# ── Phase dispatch ────────────────────────────────────────────────────


def test_run_phase_unknown_phase_raises(
    fake_web_scripts, fake_lib_dir, fake_config_dir, run_info, windows_host,
):
    manager = _make(fake_web_scripts, fake_lib_dir, fake_config_dir)
    bad = MagicMock(spec=Phase)
    bad.value = "wat"
    with pytest.raises(ManagerError, match="does not support phase"):
        manager.run_phase(bad, run_info, windows_host)


@pytest.mark.parametrize(
    ("phase", "script_name"),
    [
        (Phase.CHECK, "check.ps1"),
        (Phase.PLAN, "plan.ps1"),
        (Phase.APPLY, "apply.ps1"),
        (Phase.VERIFY, "verify.ps1"),
        (Phase.CLEANUP, "cleanup.ps1"),
    ],
)
def test_run_phase_dispatches_correct_script_per_phase(
    phase, script_name, fake_web_scripts, fake_lib_dir, fake_config_dir,
    run_info, windows_host, web_check_payload,
):
    manager = _make(fake_web_scripts, fake_lib_dir, fake_config_dir)
    payload = dict(web_check_payload)
    payload["phase"] = phase.value

    captured: dict[str, Any] = {}

    def fake_run(argv: list[str], **_: Any):
        captured["argv"] = argv
        return _run_completed(
            output_dir_arg=_extract_output_dir(argv),
            run_id=str(run_info.id),
            payload=payload,
            returncode=0,
            phase_value=phase.value,
        )

    with patch.object(
        manager,
        "_run_streaming",
        side_effect=lambda argv, **kw: fake_run(argv),
    ):
        sidecar = manager.run_phase(phase, run_info, windows_host)

    assert str(fake_web_scripts / "web" / script_name) in captured["argv"]
    assert sidecar.phase is phase
    assert sidecar.category.value == "web"


def test_run_phase_raises_on_missing_sidecar(
    fake_web_scripts, fake_lib_dir, fake_config_dir, run_info, windows_host,
):
    manager = _make(fake_web_scripts, fake_lib_dir, fake_config_dir)

    def fake_run(argv: list[str], **_: Any):
        # Returns 0 but never writes the sidecar — manager must raise.
        return _run_completed(
            output_dir_arg=_extract_output_dir(argv),
            run_id=str(run_info.id),
            payload=None,
            returncode=0,
        )

    with patch.object(
        manager,
        "_run_streaming",
        side_effect=lambda argv, **kw: fake_run(argv),
    ):
        with pytest.raises(ManagerError, match="produced no sidecar"):
            manager.run_phase(Phase.CHECK, run_info, windows_host)


# ── _build_argv shape ─────────────────────────────────────────────────


def test_build_argv_includes_config_dir(
    fake_web_scripts, fake_lib_dir, fake_config_dir, run_info,
):
    """-ConfigDir must be passed so the script finds web_apps.toml when
    Ascendo is installed outside its source tree."""
    manager = _make(fake_web_scripts, fake_lib_dir, fake_config_dir)
    argv = manager._build_argv(
        pwsh="/usr/bin/true",
        script_path=fake_web_scripts / "web" / "check.ps1",
        run=run_info,
        output_dir=fake_web_scripts / "out",
        item_filter=None,
    )
    assert "-ConfigDir" in argv
    assert str(fake_config_dir) in argv


def test_build_argv_includes_dry_run_switch_when_requested(
    fake_web_scripts, fake_lib_dir, fake_config_dir, run_info,
):
    dry_run = run_info.model_copy(update={"dry_run": True})
    manager = _make(fake_web_scripts, fake_lib_dir, fake_config_dir)
    argv = manager._build_argv(
        pwsh="/usr/bin/true",
        script_path=fake_web_scripts / "web" / "check.ps1",
        run=dry_run,
        output_dir=fake_web_scripts / "out",
        item_filter=None,
    )
    assert "-DryRun" in argv


def test_build_argv_omits_dry_run_when_false(
    fake_web_scripts, fake_lib_dir, fake_config_dir, run_info,
):
    manager = _make(fake_web_scripts, fake_lib_dir, fake_config_dir)
    argv = manager._build_argv(
        pwsh="/usr/bin/true",
        script_path=fake_web_scripts / "web" / "check.ps1",
        run=run_info,  # dry_run=False default
        output_dir=fake_web_scripts / "out",
        item_filter=None,
    )
    assert "-DryRun" not in argv


def test_build_argv_includes_item_filter_when_set(
    fake_web_scripts, fake_lib_dir, fake_config_dir, run_info,
):
    manager = _make(fake_web_scripts, fake_lib_dir, fake_config_dir)
    argv = manager._build_argv(
        pwsh="/usr/bin/true",
        script_path=fake_web_scripts / "web" / "check.ps1",
        run=run_info,
        output_dir=fake_web_scripts / "out",
        item_filter=["obsidian", "brave"],
    )
    assert "-ItemFilter" in argv
    idx = argv.index("-ItemFilter")
    assert argv[idx + 1] == "obsidian,brave"


def test_build_argv_omits_item_filter_when_empty(
    fake_web_scripts, fake_lib_dir, fake_config_dir, run_info,
):
    """All-whitespace / empty filter items must be dropped, not joined to
    a leading-comma string."""
    manager = _make(fake_web_scripts, fake_lib_dir, fake_config_dir)
    argv = manager._build_argv(
        pwsh="/usr/bin/true",
        script_path=fake_web_scripts / "web" / "check.ps1",
        run=run_info,
        output_dir=fake_web_scripts / "out",
        item_filter=["", "  ", None, "obsidian"],  # type: ignore[list-item]
    )
    assert "-ItemFilter" in argv
    idx = argv.index("-ItemFilter")
    assert argv[idx + 1] == "obsidian"


# ── Adapter wiring ────────────────────────────────────────────────────


def test_windows_adapter_includes_web_manager_in_package_managers(
    windows_host,
):
    """WindowsAdapter.package_managers() must include WebManager."""
    from ascendo_windows.adapter import WindowsAdapter
    adapter = WindowsAdapter()
    managers = adapter.package_managers(windows_host)
    categories = [m.category for m in managers]
    assert SourceType.WEB in categories
    web_mgr = next(m for m in managers if m.category is SourceType.WEB)
    assert isinstance(web_mgr, WebManager)


def test_windows_adapter_web_registry_status_reports_apps_count():
    """_web_registry_status() must now report 'ok: N apps registered'."""
    from ascendo_windows.adapter import WindowsAdapter
    adapter = WindowsAdapter()
    status = adapter._web_registry_status()
    # Shipped registry has >= 10 apps; status must be 'ok'.
    assert status.startswith("ok:"), f"unexpected status: {status}"
    assert "apps registered" in status
