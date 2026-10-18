"""Run-lock surfaces: sync POST /runs and POST /runs/async answer 409, the CLI
exits 11 (contract 'lock contention'); read-only runs are never blocked."""
from __future__ import annotations

import subprocess
import sys
import time

import pytest
from fastapi.testclient import TestClient

from ascendo.dashboard import create_app

from .test_dashboard_async import _FakeAdapter

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only lock")


@pytest.fixture
def held_lock(tmp_path, monkeypatch):
    holder = subprocess.Popen(["sleep", "30"])
    lock = tmp_path / "held.lock"
    lock.write_text(f"pid={holder.pid}\nstarted=x\nstarted_unix={time.time()}\nrun_id=other\n")
    monkeypatch.setenv("ASCENDO_RUN_LOCK_FILE", str(lock))
    yield holder
    holder.kill()
    holder.wait()


def test_sync_run_apply_is_409_when_lock_held(tmp_path, held_lock):
    client = TestClient(create_app(adapter=_FakeAdapter(), runs_dir=tmp_path / "runs"))
    r = client.post("/runs", json={"phases": ["apply"]})
    assert r.status_code == 409
    assert "in progress" in r.json()["detail"]


def test_async_run_apply_is_409_when_lock_held(tmp_path, held_lock):
    client = TestClient(create_app(adapter=_FakeAdapter(), runs_dir=tmp_path / "runs"))
    r = client.post("/runs/async", json={"phases": ["apply"]})
    assert r.status_code == 409
    assert str(held_lock.pid) in r.json()["detail"]


def test_read_only_runs_are_not_blocked(tmp_path, held_lock):
    client = TestClient(create_app(adapter=_FakeAdapter(), runs_dir=tmp_path / "runs"))
    assert client.post("/runs", json={"phases": ["check"]}).status_code == 200
    assert client.post("/runs/async", json={"phases": ["check"]}).status_code == 202


def test_cli_exits_11_on_lock_contention(tmp_path, held_lock, monkeypatch):
    from typer.testing import CliRunner

    from ascendo import cli

    class _Reg:
        def discover(self): ...

    monkeypatch.setattr(cli, "AdapterRegistry", lambda: _Reg(), raising=False)
    monkeypatch.setattr(cli, "select_adapter", lambda registry=None: _FakeAdapter(), raising=False)
    res = CliRunner().invoke(cli.app, [
        "run", "-c", "winget", "-p", "apply", "--runs-dir", str(tmp_path / "runs")])
    assert res.exit_code == 11, res.output
    assert "in progress" in res.output + (res.stderr if hasattr(res, "stderr") else "")


def _cli_invoke(monkeypatch, tmp_path, *args):
    from typer.testing import CliRunner

    from ascendo import cli

    class _Reg:
        def discover(self): ...

    seen = {}
    real = cli.run_phases

    def spy(adapter, run, host, **kw):
        seen["trigger"] = run.trigger.value
        return real(adapter, run, host, **kw)

    monkeypatch.setattr(cli, "AdapterRegistry", lambda: _Reg(), raising=False)
    monkeypatch.setattr(cli, "select_adapter", lambda registry=None: _FakeAdapter(), raising=False)
    monkeypatch.setattr(cli, "run_phases", spy, raising=False)
    res = CliRunner().invoke(cli.app, ["run", "-c", "winget", "-p", "check",
                                       "--runs-dir", str(tmp_path / "runs"), *args])
    return res, seen


def test_cli_trigger_option_default_and_scheduler(tmp_path, monkeypatch):
    res, seen = _cli_invoke(monkeypatch, tmp_path)
    assert res.exit_code == 0 and seen["trigger"] == "cli"
    res, seen = _cli_invoke(monkeypatch, tmp_path, "--trigger", "scheduler")
    assert res.exit_code == 0 and seen["trigger"] == "scheduler"


def test_cli_rejects_unknown_trigger(tmp_path, monkeypatch):
    res, _ = _cli_invoke(monkeypatch, tmp_path, "--trigger", "bogus")
    assert res.exit_code == 2
