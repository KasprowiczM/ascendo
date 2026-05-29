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
