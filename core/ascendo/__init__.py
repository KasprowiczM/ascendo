"""Ascendo — cross-platform update orchestrator.

OS-agnostic core. Adapters in `adapters/<os>/` provide per-OS implementations
of interfaces defined in `ascendo.interfaces`.

See `docs/architecture/0001-monorepo-with-adapters.md` for the architecture
overview and `HANDOFF.md` (repo root) for the current implementation state.
"""

from .__version__ import __version__

__all__ = ["__version__"]
