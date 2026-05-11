"""TimeshiftSnapshot tests — fully mock-based, run on any OS."""
from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from ascendo.interfaces.snapshot import SnapshotError, SnapshotInfo
from ascendo.models.host import ElevationMethod, HostInfo, OperatingSystem
from ascendo_ubuntu.snapshot import TimeshiftSnapshot


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def linux_host() -> HostInfo:
    return HostInfo(
        hostname="mk-uP5520",
        os=OperatingSystem.LINUX_UBUNTU,
        os_version="24.04",
        arch="x86_64",
        user="mk",
        is_elevated=False,
        elevation_method=ElevationMethod.NONE,
    )


@pytest.fixture
def macos_host() -> HostInfo:
    return HostInfo(
        hostname="mac.local",
        os=OperatingSystem.MACOS,
        os_version="14.5",
        arch="arm64",
        user="mk",
        is_elevated=False,
        elevation_method=ElevationMethod.NONE,
    )


SAMPLE_LIST_OUTPUT = """\

Num     Name                 Tags  Description
------------------------------------------------
0    >  2026-05-09_15-30-22  O     Ascendo pre-apply
1    >  2026-05-08_22-11-14  D     Daily snapshot
2    >  2026-05-07_03-00-00  W
"""

EMPTY_LIST_OUTPUT = """\

Num     Name                 Tags  Description
------------------------------------------------
"""


# ── is_available ─────────────────────────────────────────────────────────


def test_is_available_true_when_timeshift_present(linux_host: HostInfo) -> None:
    snap = TimeshiftSnapshot()
    with patch("ascendo_ubuntu.snapshot.shutil.which", return_value="/usr/bin/timeshift"):
        assert snap.is_available(linux_host) is True


def test_is_available_false_when_no_timeshift(linux_host: HostInfo) -> None:
    snap = TimeshiftSnapshot()
    with patch("ascendo_ubuntu.snapshot.shutil.which", return_value=None):
        assert snap.is_available(linux_host) is False


def test_is_available_false_on_macos(macos_host: HostInfo) -> None:
    snap = TimeshiftSnapshot()
    # Even with timeshift "present", non-Linux returns False.
    with patch("ascendo_ubuntu.snapshot.shutil.which", return_value="/usr/bin/timeshift"):
        assert snap.is_available(macos_host) is False


def test_backend_slug() -> None:
    assert TimeshiftSnapshot().backend == "timeshift"


# ── list ─────────────────────────────────────────────────────────────────


def test_list_parses_table_output(linux_host: HostInfo) -> None:
    snap = TimeshiftSnapshot(timeshift_path="/usr/bin/timeshift")
    fake = subprocess.CompletedProcess(
        args=[], returncode=0, stdout=SAMPLE_LIST_OUTPUT, stderr=""
    )
    with patch("ascendo_ubuntu.snapshot.subprocess.run", return_value=fake):
        results = snap.list(linux_host)
    assert len(results) == 3
    # Newest first
    assert results[0].id == "2026-05-09_15-30-22"
    assert results[0].created_at == datetime(
        2026, 5, 9, 15, 30, 22, tzinfo=timezone.utc
    )
    assert results[0].label == "Ascendo pre-apply"
    assert results[0].backend == "timeshift"
    assert results[1].id == "2026-05-08_22-11-14"
    assert results[1].label == "Daily snapshot"
    # Third row had no description
    assert results[2].id == "2026-05-07_03-00-00"
    assert results[2].label is None


def test_list_returns_empty_when_no_snapshots(linux_host: HostInfo) -> None:
    snap = TimeshiftSnapshot(timeshift_path="/usr/bin/timeshift")
    fake = subprocess.CompletedProcess(
        args=[], returncode=0, stdout=EMPTY_LIST_OUTPUT, stderr=""
    )
    with patch("ascendo_ubuntu.snapshot.subprocess.run", return_value=fake):
        assert snap.list(linux_host) == []


def test_list_returns_empty_on_non_linux(macos_host: HostInfo) -> None:
    snap = TimeshiftSnapshot(timeshift_path="/usr/bin/timeshift")
    # No subprocess call expected.
    with patch("ascendo_ubuntu.snapshot.subprocess.run") as mock_run:
        assert snap.list(macos_host) == []
        mock_run.assert_not_called()


def test_list_raises_when_timeshift_missing(linux_host: HostInfo) -> None:
    snap = TimeshiftSnapshot()
    with patch("ascendo_ubuntu.snapshot.shutil.which", return_value=None):
        with pytest.raises(SnapshotError, match="timeshift not installed"):
            snap.list(linux_host)


def test_list_raises_on_nonzero_exit(linux_host: HostInfo) -> None:
    snap = TimeshiftSnapshot(timeshift_path="/usr/bin/timeshift")
    fake = subprocess.CompletedProcess(
        args=[], returncode=1, stdout="", stderr="sudo: a password is required"
    )
    with patch("ascendo_ubuntu.snapshot.subprocess.run", return_value=fake):
        with pytest.raises(SnapshotError, match="timeshift --list failed"):
            snap.list(linux_host)


# ── get ──────────────────────────────────────────────────────────────────


def test_get_returns_matching_snapshot(linux_host: HostInfo) -> None:
    snap = TimeshiftSnapshot(timeshift_path="/usr/bin/timeshift")
    fake = subprocess.CompletedProcess(
        args=[], returncode=0, stdout=SAMPLE_LIST_OUTPUT, stderr=""
    )
    with patch("ascendo_ubuntu.snapshot.subprocess.run", return_value=fake):
        info = snap.get(linux_host, "2026-05-08_22-11-14")
    assert info is not None
    assert isinstance(info, SnapshotInfo)
    assert info.id == "2026-05-08_22-11-14"
    assert info.label == "Daily snapshot"


def test_get_returns_none_when_id_not_found(linux_host: HostInfo) -> None:
    snap = TimeshiftSnapshot(timeshift_path="/usr/bin/timeshift")
    fake = subprocess.CompletedProcess(
        args=[], returncode=0, stdout=SAMPLE_LIST_OUTPUT, stderr=""
    )
    with patch("ascendo_ubuntu.snapshot.subprocess.run", return_value=fake):
        assert snap.get(linux_host, "1999-01-01_00-00-00") is None


# ── create ───────────────────────────────────────────────────────────────


def test_create_raises_snapshot_error_when_sudo_fails(linux_host: HostInfo) -> None:
    snap = TimeshiftSnapshot(timeshift_path="/usr/bin/timeshift")
    fake = subprocess.CompletedProcess(
        args=[], returncode=1, stdout="", stderr="sudo: a password is required"
    )
    with patch("ascendo_ubuntu.snapshot.subprocess.run", return_value=fake):
        with pytest.raises(SnapshotError, match="timeshift --create failed"):
            snap.create(linux_host, label="Ascendo pre-apply")


def test_create_raises_when_timeshift_missing(linux_host: HostInfo) -> None:
    snap = TimeshiftSnapshot()
    with patch("ascendo_ubuntu.snapshot.shutil.which", return_value=None):
        with pytest.raises(SnapshotError, match="timeshift not installed"):
            snap.create(linux_host, label="x")


def test_create_returns_info_from_newest_listed(linux_host: HostInfo) -> None:
    """After a successful --create, we re-list and return the newest entry."""
    snap = TimeshiftSnapshot(timeshift_path="/usr/bin/timeshift")

    # First call (create) succeeds; second call (list) returns SAMPLE.
    call_responses = [
        subprocess.CompletedProcess(args=[], returncode=0, stdout="created", stderr=""),
        subprocess.CompletedProcess(
            args=[], returncode=0, stdout=SAMPLE_LIST_OUTPUT, stderr=""
        ),
    ]
    with patch(
        "ascendo_ubuntu.snapshot.subprocess.run", side_effect=call_responses
    ):
        info = snap.create(linux_host, label="my-label", notes="some notes")
    assert info.id == "2026-05-09_15-30-22"
    assert info.label == "my-label"
    assert info.notes == "some notes"
    assert info.backend == "timeshift"


def test_create_raises_when_list_empty_after_create(linux_host: HostInfo) -> None:
    snap = TimeshiftSnapshot(timeshift_path="/usr/bin/timeshift")
    call_responses = [
        subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
        subprocess.CompletedProcess(
            args=[], returncode=0, stdout=EMPTY_LIST_OUTPUT, stderr=""
        ),
    ]
    with patch(
        "ascendo_ubuntu.snapshot.subprocess.run", side_effect=call_responses
    ):
        with pytest.raises(SnapshotError, match="not visible"):
            snap.create(linux_host, label="x")


def test_create_argv_includes_sudo_a_and_scripted(linux_host: HostInfo) -> None:
    """Verify CVE-conscious argv: sudo -A + --scripted + --comments."""
    snap = TimeshiftSnapshot(timeshift_path="/usr/bin/timeshift")

    captured: dict = {}

    def fake_run(argv, **kw):
        captured.setdefault("argvs", []).append(list(argv))
        # First call is create, second is list — both succeed.
        if "--create" in argv:
            return subprocess.CompletedProcess(
                args=argv, returncode=0, stdout="", stderr=""
            )
        return subprocess.CompletedProcess(
            args=argv, returncode=0, stdout=SAMPLE_LIST_OUTPUT, stderr=""
        )

    with patch("ascendo_ubuntu.snapshot.subprocess.run", side_effect=fake_run):
        snap.create(linux_host, label="before-apt-apply")

    create_argv = captured["argvs"][0]
    assert create_argv[0] == "sudo"
    assert "-A" in create_argv
    assert "--create" in create_argv
    assert "--scripted" in create_argv
    # comment piped through
    i = create_argv.index("--comments")
    assert create_argv[i + 1] == "before-apt-apply"
