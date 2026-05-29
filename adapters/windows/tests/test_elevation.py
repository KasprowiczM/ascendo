"""Tests for WindowsElevation."""
from __future__ import annotations
import pytest

from ascendo_windows.managers.elevation import WindowsElevation
from ascendo.interfaces.elevation import ElevationDenied

def test_run_uac_rejects_env():
    elev = WindowsElevation()
    elev.register_allowlist(["cmd.exe"])
    with pytest.raises(NotImplementedError, match="UAC elevation does not support environment variable overrides"):
        # We bypass run() directly to test _run_uac, but it's cleaner to test run()
        # if it hits _run_uac. However, we'd need to mock _is_admin_token to False.
        # Instead, we just call _run_uac directly for unit testing this specific fail-fast.
        elev._run_uac(["cmd.exe"], timeout_sec=10, env={"FOO": "bar"}, cwd=None)

def test_run_uac_prevents_spoofing(monkeypatch, tmp_path):
    elev = WindowsElevation()
    
    import shutil
    import os
    
    # Mock shutil.which to simulate spoofing
    def fake_which(cmd, *args, **kwargs):
        if cmd == "winget.exe":
            return r"C:\Windows\System32\winget.exe"
        if cmd == r"C:\temp\winget.exe":
            return r"C:\temp\winget.exe"
        if cmd == "dcu-cli.exe":
            return None
        if cmd == r"C:\temp\dcu-cli.exe":
            return r"C:\temp\dcu-cli.exe"
        return shutil.which(cmd, *args, **kwargs)
        
    monkeypatch.setattr(shutil, "which", fake_which)
    
    # Should block spoofed winget (because winget is in PATH)
    with pytest.raises(ElevationDenied, match="Spoofing detected"):
        elev._run_uac([r"C:\temp\winget.exe"], timeout_sec=10, env=None, cwd=None)
        
    # Should allow dcu-cli (because it's not in PATH, so absolute path is trusted if it resolves to itself)
    # Note: it will fail on subprocess spawn because cmd.exe /c isn't actually mocked, 
    # but we just want to ensure it doesn't raise ElevationDenied.
    # To prevent it from actually running, we can just monkeypatch subprocess.run.
    import subprocess
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args, 0))
    elev._run_uac([r"C:\temp\dcu-cli.exe"], timeout_sec=10, env=None, cwd=None)
