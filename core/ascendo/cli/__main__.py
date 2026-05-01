"""Module entry point — enables ``python -m ascendo.cli``.

Equivalent to ``python -m ascendo`` (top-level). Both forms are supported
to be friendly to users following different conventions.
"""
from __future__ import annotations

from . import main

if __name__ == "__main__":
    main()
