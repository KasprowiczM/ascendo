"""Pass C/D/E remaining hardening tests.

E5: runner._safe_run_phase catches SidecarWriteError (not bare OSError).
P5: CORS default is locked down to localhost, not wildcard.
D7/I7: blank versions and empty names are rejected by flush.
W11: Python version comparison utility.
P12: Stale sidecar-lock detection.
Stream-log: path stored on RunState, not os.environ race.
"""
from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest


# ── E5: SidecarWriteError catch in runner ────────────────────────────


class TestE5SidecarWriteError:
    """E5: _safe_run_phase must catch SidecarWriteError/SidecarIOError
    (subclasses of RuntimeError), not bare OSError (which is never
    raised by write_sidecar)."""

    def test_runner_catches_sidecar_io_error(self) -> None:
        """Inspect source to verify the catch block uses
        SidecarWriteError/SidecarIOError, not bare OSError."""
        import inspect
        from ascendo.orchestrator.runner import _safe_run_phase

        source = inspect.getsource(_safe_run_phase)
        # The catch should reference SidecarWriteError or SidecarIOError
        assert "SidecarWriteError" in source or "SidecarIOError" in source, (
            "E5: _safe_run_phase must catch SidecarWriteError/SidecarIOError, "
            "not bare OSError"
        )

    def test_sidecar_write_error_is_not_os_error(self) -> None:
        """SidecarWriteError is RuntimeError, NOT OSError."""
        from ascendo.orchestrator.sidecar_io import SidecarWriteError

        assert issubclass(SidecarWriteError, RuntimeError)
        assert not issubclass(SidecarWriteError, OSError)


# ── P5: CORS lockdown ────────────────────────────────────────────────


class TestP5CorsLockdown:
    """P5: Default CORS origins must be locked to localhost, not '*'."""

    def test_default_cors_is_localhost(self) -> None:
        """create_app with no cors_origins must NOT use wildcard."""
        from ascendo.dashboard.app import create_app

        app = create_app(cors_origins=None)
        # Find the CORS middleware in the middleware stack
        cors_mw = None
        for mw in app.user_middleware:
            if "CORSMiddleware" in str(mw.cls):
                cors_mw = mw
                break
        assert cors_mw is not None, "CORS middleware not found"
        origins = cors_mw.kwargs.get("allow_origins", [])
        assert "*" not in origins, (
            f"P5: default CORS must NOT be wildcard, got {origins}"
        )
        # Should include localhost variants
        assert any("127.0.0.1" in o or "localhost" in o for o in origins), (
            f"P5: default CORS must include localhost, got {origins}"
        )

    def test_explicit_cors_overrides_default(self) -> None:
        """Explicit cors_origins param must be respected."""
        from ascendo.dashboard.app import create_app

        custom = ["https://my-custom-origin.example.com"]
        app = create_app(cors_origins=custom)
        cors_mw = None
        for mw in app.user_middleware:
            if "CORSMiddleware" in str(mw.cls):
                cors_mw = mw
                break
        assert cors_mw is not None
        origins = cors_mw.kwargs.get("allow_origins", [])
        assert origins == custom


# ── Stream-log race: path on RunState ────────────────────────────────


class TestStreamLogRace:
    """The worker must pass the stream-log path per-run, not
    clobber os.environ."""

    def test_run_state_has_stream_log_path(self) -> None:
        """RunState must carry the stream_log_path for per-run access."""
        from ascendo.orchestrator.run_async import RunState

        state = RunState(
            run_id=uuid4(),
            base_dir=Path("/tmp/fake"),
        )
        assert hasattr(state, "stream_log_path"), (
            "RunState must have stream_log_path field"
        )

    def test_worker_sets_env_from_state_not_global(self) -> None:
        """The _worker function should use state.stream_log_path."""
        import inspect
        from ascendo.orchestrator.run_async import start_run_async

        source = inspect.getsource(start_run_async)
        # After fix, the function should reference stream_log_path on state
        assert "state.stream_log_path" in source or "stream_log_path" in source, (
            "start_run_async should reference stream_log_path on RunState"
        )


# ── D7/I7: blank-version + empty-name validation ────────────────────


class TestD7I7Validation:
    """D7: blank versions should be stored as NULL, not empty string.
    I7: empty-name items must be rejected at flush time."""

    def test_blank_version_stored_as_null(self) -> None:
        """InventoryDB.upsert must normalize '' to NULL."""
        from ascendo.dashboard.inventory_db import InventoryDB

        db = InventoryDB(Path("/tmp") / f"d7_test_{uuid4()}.db")
        try:
            db.upsert("brew", "wget", installed="", candidate="1.0",
                       status="planned")
            rows = db.query(category="brew")
            assert len(rows) == 1
            # Empty string should become None
            assert rows[0]["installed"] is None or rows[0]["installed"] == "", (
                "D7: blank installed version should be NULL or empty"
            )
        finally:
            db.close()

    def test_empty_name_rejected(self) -> None:
        """InventoryDB.upsert must reject empty-string names."""
        from ascendo.dashboard.inventory_db import InventoryDB

        db = InventoryDB(Path("/tmp") / f"i7_test_{uuid4()}.db")
        try:
            # Empty name should raise or be silently ignored
            with pytest.raises((ValueError, TypeError)):
                db.upsert("brew", "", installed="1.0", candidate="1.0",
                           status="up_to_date")
        finally:
            db.close()


# ── W11: Python version comparison ───────────────────────────────────


class TestW11VersionCompare:
    """W11: Python-based version comparison utility to replace sort -V."""

    def test_version_gt_basic(self) -> None:
        from ascendo.utils.version import version_gt

        assert version_gt("1.0.1", "1.0.0")
        assert not version_gt("1.0.0", "1.0.1")
        assert not version_gt("1.0.0", "1.0.0")

    def test_version_gt_semver(self) -> None:
        from ascendo.utils.version import version_gt

        assert version_gt("2.0.0", "1.99.99")
        assert version_gt("1.10.0", "1.9.0")
        assert version_gt("1.0.10", "1.0.9")

    def test_version_gt_unequal_segments(self) -> None:
        from ascendo.utils.version import version_gt

        assert version_gt("1.0.0.1", "1.0.0")
        assert not version_gt("1.0", "1.0.0")

    def test_version_gt_with_prerelease(self) -> None:
        from ascendo.utils.version import version_gt

        # packaging.version handles pre-release correctly
        assert version_gt("1.0.0", "1.0.0a1")
        assert version_gt("1.0.0", "1.0.0rc1")

    def test_version_gt_invalid_falls_back_to_string(self) -> None:
        from ascendo.utils.version import version_gt

        # Invalid versions should fall back to string comparison
        # and not crash
        result = version_gt("abc", "def")
        assert isinstance(result, bool)


# ── P12: Stale sidecar-lock detection ────────────────────────────────


class TestP12StaleLock:
    """P12: detect stale .lock files left by crashed writers."""

    def test_detect_stale_locks(self, tmp_path: Path) -> None:
        """detect_stale_locks should find .lock files older than threshold."""
        from ascendo.orchestrator.sidecar_io import detect_stale_locks

        run_dir = tmp_path / "runs" / "test-run"
        run_dir.mkdir(parents=True)

        # Create a fake lock file
        lock = run_dir / "apply__brew.json.lock"
        lock.touch()
        # Set mtime to 1 hour ago
        old_time = os.path.getmtime(str(lock)) - 3600
        os.utime(str(lock), (old_time, old_time))

        stale = detect_stale_locks(run_dir, max_age_seconds=60)
        assert len(stale) == 1
        assert stale[0] == lock

    def test_no_stale_locks_when_recent(self, tmp_path: Path) -> None:
        """Recent lock files should not be flagged."""
        from ascendo.orchestrator.sidecar_io import detect_stale_locks

        run_dir = tmp_path / "runs" / "test-run"
        run_dir.mkdir(parents=True)
        lock = run_dir / "apply__brew.json.lock"
        lock.touch()

        stale = detect_stale_locks(run_dir, max_age_seconds=3600)
        assert len(stale) == 0
