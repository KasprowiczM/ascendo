"""Cross-platform subprocess helpers for Windows console window suppression."""

import subprocess
import sys


def no_window_kwargs() -> dict:
    """Return subprocess kwargs to hide the console window on Windows.

    On Windows: returns {'creationflags': subprocess.CREATE_NO_WINDOW} so child
    processes spawn without a visible black PowerShell window. This is essential
    for GUI contexts (dashboard, phase runners) where a flashing window is
    visually jarring and breaks the user experience.

    On non-Windows (Linux, macOS): returns {} as there is no equivalent concept.

    Usage in subprocess.run():
        subprocess.run([...], **no_window_kwargs(), capture_output=True, text=True)

    Usage in subprocess.Popen():
        if sys.platform == "win32":
            creationflags = subprocess.CREATE_NO_WINDOW
        else:
            creationflags = 0
        subprocess.Popen([...], creationflags=creationflags)

    Returns:
        dict: Empty dict on non-Windows; {'creationflags': 0x08000000} on Windows.
    """
    if sys.platform == "win32":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}
    return {}
