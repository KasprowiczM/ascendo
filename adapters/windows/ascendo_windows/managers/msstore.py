"""MSStoreManager — IPackageManager for the Microsoft Store package source.

Microsoft Store packages on modern Windows can be enumerated through two
complementary surfaces:

1. ``winget --source msstore`` for everything that has a Store listing
   (which is almost all UWP apps installed via the Store, plus a growing
   share of classic apps Microsoft has signed for distribution there).
2. ``Get-AppxPackage`` for sideloaded MSIX / pre-installed UWP packages
   that never round-trip through the Store backend.

This manager covers (1). The (2) surface is owned by the future Inventory
implementation (M3.11) and is intentionally NOT duplicated here.

The IPC contract, sidecar layout, ``-DryRun`` switch convention, and the
``-ItemFilter`` semantics are all identical to :class:`WingetManager` —
the two managers differ only in which PowerShell scripts they spawn and
in the ``SourceType`` they declare. By inheriting from WingetManager we
get the spawn / parse / error-format machinery for free.
"""
from __future__ import annotations

from typing import ClassVar

from ascendo.models.package import SourceType
from ascendo.models.run import Phase

from .winget import WingetManager


class MSStoreManager(WingetManager):
    """Microsoft Store (msstore) per-source manager.

    Re-uses every IPC / sidecar internal from :class:`WingetManager` and
    only overrides identity + which script directory to look in. The
    PowerShell scripts under ``adapters/windows/scripts/msstore/`` invoke
    ``winget --source msstore`` and emit the standard ``ascendo/v1``
    sidecar with ``category="msstore"``.
    """

    SCRIPT_BY_PHASE: ClassVar[dict[Phase, str]] = {
        Phase.CHECK:   "msstore/check.ps1",
        Phase.PLAN:    "msstore/plan.ps1",
        Phase.APPLY:   "msstore/apply.ps1",
        Phase.VERIFY:  "msstore/verify.ps1",
        Phase.CLEANUP: "msstore/cleanup.ps1",
    }

    @property
    def category(self) -> SourceType:
        return SourceType.MSSTORE

    @property
    def display_name(self) -> str:
        return "Microsoft Store (msstore)"

    # is_available inherited verbatim from WingetManager: Microsoft Store
    # operations route through ``winget --source msstore``, so the
    # availability probe is identical (Windows + winget on PATH).
