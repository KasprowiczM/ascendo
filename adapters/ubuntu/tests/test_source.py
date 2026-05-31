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


def test_verify_debs_passes_real_hash_from_print_uris(dummy_host, monkeypatch):
    """Wiring: the apply path feeds the SHA-256 parsed from `apt-get
    --print-uris` (a real hash, NOT None) into verify_signature."""
    from ascendo_ubuntu.managers.apt import AptManager, _AptCmdResult
    from ascendo.models.package import PackagePlan

    mgr = AptManager()
    real_hash = "a" * 64
    print_uris = (
        "'http://archive.ubuntu.com/ubuntu/pool/main/f/foo/foo_1.2_amd64.deb' "
        f"foo_1.2_amd64.deb 12345 SHA256:{real_hash}\n"
    )

    def fake_run_apt(host, args, elevated=False, timeout=300):
        if "--print-uris" in args:
            return _AptCmdResult(0, print_uris, "")
        return _AptCmdResult(0, "", "")  # download-only

    monkeypatch.setattr(mgr, "_run_apt", fake_run_apt)
    monkeypatch.setattr("pathlib.Path.is_file", lambda self: True)

    captured = {}

    def fake_verify(self, host, source, artifact_path, expected_sha256=None):
        captured["hash"] = expected_sha256
        return True

    monkeypatch.setattr(
        "ascendo_ubuntu.managers.source.UbuntuSource.verify_signature", fake_verify
    )

    plan = [PackagePlan(package_id="foo", name="foo", target_version="1.2")]
    ok, errors = mgr._verify_debs(dummy_host, plan)
    assert ok is True
    assert errors == []
    assert captured["hash"] == real_hash  # real GPG-manifest hash, never None


def test_verify_debs_fail_closed_on_mismatch(dummy_host, monkeypatch):
    """Wiring: a hash mismatch from verify_signature aborts the apply."""
    from ascendo_ubuntu.managers.apt import AptManager, _AptCmdResult
    from ascendo.models.package import PackagePlan

    mgr = AptManager()
    print_uris = "'http://x/foo_1.2_amd64.deb' foo_1.2_amd64.deb 12345 SHA256:" + "b" * 64 + "\n"

    monkeypatch.setattr(
        mgr,
        "_run_apt",
        lambda host, args, elevated=False, timeout=300: (
            _AptCmdResult(0, print_uris, "") if "--print-uris" in args else _AptCmdResult(0, "", "")
        ),
    )
    monkeypatch.setattr("pathlib.Path.is_file", lambda self: True)
    monkeypatch.setattr(
        "ascendo_ubuntu.managers.source.UbuntuSource.verify_signature",
        lambda self, host, source, artifact_path, expected_sha256=None: False,
    )

    plan = [PackagePlan(package_id="foo", name="foo", target_version="1.2")]
    ok, errors = mgr._verify_debs(dummy_host, plan)
    assert ok is False
    assert errors
