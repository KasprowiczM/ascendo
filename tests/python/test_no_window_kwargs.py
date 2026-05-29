"""Unit tests for the no_window_kwargs subprocess helper."""

import subprocess
import sys
from unittest.mock import patch

import pytest

from ascendo.utils.proc import no_window_kwargs


class TestNoWindowKwargs:
    """Test the subprocess window suppression helper."""

    def test_on_windows(self) -> None:
        """On Windows, returns creationflags for CREATE_NO_WINDOW."""
        with patch.object(sys, "platform", "win32"):
            result = no_window_kwargs()
            assert isinstance(result, dict)
            assert "creationflags" in result
            assert result["creationflags"] == getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

    def test_on_linux(self) -> None:
        """On Linux, returns empty dict."""
        with patch.object(sys, "platform", "linux"):
            result = no_window_kwargs()
            assert isinstance(result, dict)
            assert len(result) == 0

    def test_on_darwin(self) -> None:
        """On macOS (darwin), returns empty dict."""
        with patch.object(sys, "platform", "darwin"):
            result = no_window_kwargs()
            assert isinstance(result, dict)
            assert len(result) == 0

    def test_create_no_window_value(self) -> None:
        """CREATE_NO_WINDOW should equal 0x08000000."""
        with patch.object(sys, "platform", "win32"):
            result = no_window_kwargs()
            assert result["creationflags"] == 0x08000000

    def test_kwargs_can_be_unpacked(self) -> None:
        """Result dict is safe to unpack with **kwargs."""
        with patch.object(sys, "platform", "win32"):
            result = no_window_kwargs()
            # Simulating: subprocess.run([...], **no_window_kwargs())
            kwargs = {"capture_output": True, **result}
            assert "capture_output" in kwargs
            assert kwargs["capture_output"] is True
            assert "creationflags" in kwargs

    def test_empty_dict_unpacks_safely(self) -> None:
        """Empty dict from non-Windows unpacks without changing other kwargs."""
        with patch.object(sys, "platform", "linux"):
            result = no_window_kwargs()
            # Simulating: subprocess.run([...], timeout=10, **no_window_kwargs())
            kwargs = {"timeout": 10, **result}
            assert kwargs == {"timeout": 10}
