from unittest.mock import patch, mock_open

import pytest

from ascendo.interfaces.source import SourceVerificationError, TrustTier
from ascendo.models.host import ElevationMethod, HostInfo, OperatingSystem
from ascendo.models.package import SourceType

from ascendo_ubuntu.managers.source import UbuntuSource

@pytest.fixture
def dummy_host():
    return HostInfo(
        hostname="test",
        os=OperatingSystem.LINUX_UBUNTU,
        os_version="24.04",
        arch="x86_64",
        user="mk",
        is_elevated=False,
        elevation_method=ElevationMethod.SUDO,
        locale="en-US"
    )

def test_list_known_sources(dummy_host):
    source = UbuntuSource()
    
    mock_sources_list = "deb http://archive.ubuntu.com/ubuntu noble main restricted\n"
    mock_deb822 = "URIs: http://security.ubuntu.com/ubuntu\nSuites: noble-security\n"
    
    def mock_read_text(self, *args, **kwargs):
        if self.name == "sources.list":
            return mock_sources_list
        return mock_deb822
        
    def mock_is_file(self):
        return True

    with patch("pathlib.Path.read_text", mock_read_text), \
         patch("pathlib.Path.is_file", mock_is_file), \
         patch("pathlib.Path.is_dir", return_value=True), \
         patch("pathlib.Path.glob", return_value=[patch("pathlib.Path").start() for _ in range(1)]):
        sources = source.list_known_sources(dummy_host)
        assert len(sources) > 0

def test_verify_signature_missing_hash(dummy_host):
    source = UbuntuSource()
    metadata = source.get(dummy_host, SourceType.APT, "test") or type("MockMeta", (), {"type": SourceType.APT})()
    
    with pytest.raises(SourceVerificationError, match="cannot be verified without an expected SHA256"):
        source.verify_signature(dummy_host, metadata, "/tmp/foo.deb", None)

def test_verify_signature_wrong_type(dummy_host):
    source = UbuntuSource()
    metadata = type("MockMeta", (), {"type": SourceType.WINGET})()
    assert source.verify_signature(dummy_host, metadata, "/tmp/foo.deb", "hash") is False

def test_verify_signature_success(dummy_host, tmp_path):
    source = UbuntuSource()
    metadata = type("MockMeta", (), {"type": SourceType.APT})()
    
    # Create a dummy .deb
    deb_path = tmp_path / "test.deb"
    deb_path.write_bytes(b"testcontent")
    
    import hashlib
    expected_hash = hashlib.sha256(b"testcontent").hexdigest()
    
    assert source.verify_signature(dummy_host, metadata, str(deb_path), expected_hash) is True

def test_verify_signature_failure(dummy_host, tmp_path):
    source = UbuntuSource()
    metadata = type("MockMeta", (), {"type": SourceType.APT})()

    deb_path = tmp_path / "test.deb"
    deb_path.write_bytes(b"testcontent")

    assert source.verify_signature(dummy_host, metadata, str(deb_path), "wronghash") is False


def test_verify_signature_apt_gpg(dummy_host, tmp_path):
    """Contract (P1, Layer 5): APT hash verification anchored by the GPG-signed
    manifest — valid hash ⇒ True, mismatch ⇒ False, missing hash ⇒ fail-closed.
    """
    import hashlib

    source = UbuntuSource()
    metadata = type("MockMeta", (), {"type": SourceType.APT})()
    deb_path = tmp_path / "pkg.deb"
    deb_path.write_bytes(b"deb-bytes")
    good = hashlib.sha256(b"deb-bytes").hexdigest()

    # valid hash from the signed manifest ⇒ True
    assert source.verify_signature(dummy_host, metadata, str(deb_path), good) is True
    # tampered artifact / wrong hash ⇒ False (refuse to install)
    assert source.verify_signature(dummy_host, metadata, str(deb_path), "0" * 64) is False
    # no hash available ⇒ fail-closed (cannot verify ⇒ raise, never silently pass)
    with pytest.raises(SourceVerificationError):
        source.verify_signature(dummy_host, metadata, str(deb_path), None)


class _FakeProc:
    """Minimal subprocess.CompletedProcess stand-in."""

    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _make_apt_manager():
    """Build an AptManager without invoking __init__ (constructor wiring is
    not under test here); only ``_elevation`` is read by the verify path."""
    from ascendo_ubuntu.managers.apt import AptManager

    mgr = AptManager.__new__(AptManager)
    mgr._elevation = None
    return mgr


def test_verify_apt_signatures_passes_real_hash_from_print_uris(dummy_host, monkeypatch):
    """Wiring: the apply path feeds the SHA-256 parsed from `apt-get
    --print-uris` (a real hash, NOT None) into verify_signature."""
    import ascendo_ubuntu.managers.apt as apt_mod

    mgr = _make_apt_manager()
    real_hash = "a" * 64
    print_uris = (
        "'http://archive.ubuntu.com/ubuntu/pool/main/f/foo/foo_1.2_amd64.deb' "
        f"foo_1.2_amd64.deb 12345 SHA256:{real_hash}\n"
    )

    def fake_run(cmd, *a, **k):
        if "--print-uris" in cmd:
            return _FakeProc(0, print_uris)
        return _FakeProc(0, "")  # download-only

    monkeypatch.setattr(apt_mod.subprocess, "run", fake_run)
    monkeypatch.setattr("os.path.exists", lambda p: True)

    captured = {}

    def fake_verify(self, host, source, artifact_path, expected_sha256=None):
        captured["hash"] = expected_sha256
        return True

    monkeypatch.setattr(
        "ascendo_ubuntu.managers.source.UbuntuSource.verify_signature", fake_verify
    )

    # Must not raise; must pass the real manifest hash (never None).
    mgr._verify_apt_signatures(dummy_host)
    assert captured["hash"] == real_hash


def test_verify_apt_signatures_fail_closed_on_mismatch(dummy_host, monkeypatch):
    """Wiring: a hash mismatch from verify_signature aborts the apply."""
    import ascendo_ubuntu.managers.apt as apt_mod
    from ascendo.interfaces.package_manager import ManagerError

    mgr = _make_apt_manager()
    print_uris = "'http://x/foo_1.2_amd64.deb' foo_1.2_amd64.deb 12345 SHA256:" + "b" * 64 + "\n"

    monkeypatch.setattr(
        apt_mod.subprocess,
        "run",
        lambda cmd, *a, **k: _FakeProc(0, print_uris) if "--print-uris" in cmd else _FakeProc(0, ""),
    )
    monkeypatch.setattr("os.path.exists", lambda p: True)
    monkeypatch.setattr(
        "ascendo_ubuntu.managers.source.UbuntuSource.verify_signature",
        lambda self, host, source, artifact_path, expected_sha256=None: False,
    )

    with pytest.raises(ManagerError):
        mgr._verify_apt_signatures(dummy_host)
