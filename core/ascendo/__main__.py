"""Module entry point — enables ``python -m ascendo``.

Useful when the ``ascendo`` console-script isn't on ``PATH`` (e.g. on
Windows installs of Python where ``%LOCALAPPDATA%\\...\\Scripts`` isn't
added to PATH automatically). This file makes
``python -m ascendo run --category winget`` equivalent to the script.
"""
from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    main()
