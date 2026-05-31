"""Cross-source deduplication logic for the orchestrator."""

from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path
from uuid import UUID

from ..models.deduplication import AppSourcesRegistry
from ..models.sidecar import Sidecar
from ..models.result import ItemStatus, Message, MessageLevel
from .sidecar_io import write_sidecar

_log = logging.getLogger(__name__)

def _confirm_uninstall(app_name: str, src_name: str, preferred: str) -> bool:
    """Decide whether to QUEUE an automatic uninstall of a non-preferred
    duplicate source.

    FAIL-SAFE (production-hardening, 2026-05-31 audit). Auto-uninstall is a
    DESTRUCTIVE action: on Windows the winget/npm/pip ``apply.ps1`` scripts
    read ``DEDUPLICATION_TASKS.json`` and run ``winget/npm/pip uninstall``.
    We therefore only queue it when the operator can actually consent:

      * an interactive TTY prompt (default **No**), or
      * an explicit ``ASCENDO_DEDUP_AUTO_UNINSTALL=1`` opt-in for unattended
        automation.

    In every other non-interactive context — the dashboard worker thread,
    the systemd/launchd service, the Tauri sidecar — we return ``False`` so
    the duplicate is only *reported* in ``DEDUPLICATION_REPORT.md``, never
    silently uninstalled. The previous behaviour returned ``True`` for every
    non-TTY caller, so a "Safe update" clicked in the dashboard (non-TTY)
    could uninstall a real package with zero user consent.
    """
    import os
    import sys

    if os.environ.get("ASCENDO_DEDUP_AUTO_UNINSTALL") == "1":
        return True
    if not sys.stdout.isatty():
        return False
    try:
        from rich.prompt import Confirm
        from rich.console import Console
        Console().print(f"[yellow]Duplicate detected: {app_name} is installed via '{src_name}', but '{preferred}' is preferred.[/yellow]")
        return Confirm.ask(f"Uninstall the non-preferred '{src_name}' version of {app_name} now?", default=False)
    except ImportError:
        res = input(f"Duplicate detected: {app_name} is installed via '{src_name}'. Uninstall? [y/N]: ")
        return res.lower() in ("y", "yes")


def apply_deduplication(sidecars: list[Sidecar], run_id: UUID, base_dir: Path, config_path: Path | None = None) -> None:
    """Analyzes check phase sidecars, identifies duplicates, and ignores non-preferred sources.
    
    Generates a DEDUPLICATION_REPORT.md if actionable duplicates are found.
    Mutates the `sidecars` list items in-place and rewrites them to disk.
    """
    import os
    import shutil
    
    import sys
    is_linux = sys.platform.startswith("linux")
    is_macos = sys.platform == "darwin"
    
    # Look for config in user profile
    user_config_dir = Path(os.environ.get("ASCENDO_HOME") or Path.home() / ".ascendo")
    if config_path is None:
        if is_linux:
            config_path = user_config_dir / "ubuntu_app_sources.toml"
        elif is_macos:
            config_path = user_config_dir / "macos_app_sources.toml"
        else:
            config_path = user_config_dir / "windows_app_sources.toml"
        
    if not config_path.exists():
        if is_linux:
            default_path = Path(__file__).parent.parent.parent.parent / "adapters" / "ubuntu" / "config" / "ubuntu_app_sources.toml"
        elif is_macos:
            default_path = Path(__file__).parent.parent.parent.parent / "adapters" / "macos" / "config" / "macos_app_sources.toml"
        else:
            default_path = Path(__file__).parent.parent.parent.parent / "adapters" / "windows" / "config" / "app_sources.toml"
            
        if default_path.exists():
            user_config_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(default_path, config_path)
            _log.info("Initialized default app sources at %s", config_path)
        else:
            return

    registry = AppSourcesRegistry.load(config_path)
    if not registry.apps:
        return

    # Map package ID -> Sidecar Item for quick lookup
    # We only care about installed items (current_version is not None)
    installed_items = []
    for sidecar in sidecars:
        for item in sidecar.items:
            if item.current_version:
                installed_items.append((sidecar, item))

    actionable_fixes = []
    uninstall_tasks = defaultdict(list)

    for app in registry.apps:
        # Check which sources are installed for this logical app
        installed_sources = {}
        for sidecar, item in installed_items:
            # Does this item match one of the mapped sources for this app?
            for source_name, source_pkg_id in app.sources.items():
                if sidecar.category.value == source_name and item.id == source_pkg_id:
                    installed_sources[source_name] = (sidecar, item)

        _log.debug("installed_sources for %s = %s", app.id, list(installed_sources.keys()))
        if not installed_sources:
            continue

        # Find the best installed source according to preferred_order
        best_installed_idx = 999
        best_source = None
        for source_name in installed_sources:
            if source_name in app.preferred_order:
                idx = app.preferred_order.index(source_name)
                if idx < best_installed_idx:
                    best_installed_idx = idx
                    best_source = source_name

        if not best_source:
            # None of the installed sources are in the preferred list, fallback
            best_source = list(installed_sources.keys())[0]

        # Check if the BEST INSTALLED source is actually the absolute preferred (index 0)
        absolute_preferred = app.preferred_order[0] if app.preferred_order else best_source

        # If they don't match, or if multiple are installed, we have a deduplication action
        if len(installed_sources) > 1 or best_source != absolute_preferred:
            actionable_fixes.append({
                "app": app,
                "installed_sources": installed_sources,
                "preferred": absolute_preferred
            })

            # Ignore updates for non-best installed sources
            for src_name, (sidecar, item) in installed_sources.items():
                if src_name != best_source:
                    if _confirm_uninstall(app.name, src_name, absolute_preferred):
                        _log.debug("Auto-uninstalling %s item %s", src_name, item.id)
                        uninstall_tasks[src_name].append(item.id)

                        # Change status from successful check to planned uninstall.
                        # This is an explicitly opted-in DESTRUCTIVE action, so
                        # mutating + rewriting the sidecar is intentional here.
                        item.status = ItemStatus.PLANNED
                        item.action = "uninstall"
                        item.target_version = "ABSENT"
                        item.messages.append(Message(
                            level=MessageLevel.WARN,
                            text=f"Auto-uninstalling non-preferred source '{src_name}'. Preferred is '{absolute_preferred}'."
                        ))
                        # Rewrite the sidecar to disk since we mutated it.
                        write_sidecar(sidecar, base_dir=base_dir)
                    else:
                        # REPORT-ONLY (fail-safe default, 2026-05-31 audit):
                        # do NOT mutate or rewrite the read-only CHECK sidecar.
                        # The honest check result is left intact; the duplicate
                        # is surfaced in DEDUPLICATION_REPORT.md only. Silently
                        # flipping a check item to SKIPPED/up-to-date here would
                        # re-introduce the dishonest-status problem the audit
                        # fixed at the data layer.
                        _log.debug(
                            "Report-only: leaving %s item %s untouched (no sidecar mutation)",
                            src_name, item.id,
                        )

    if uninstall_tasks:
        import json
        tasks_path = base_dir / str(run_id) / "DEDUPLICATION_TASKS.json"
        tasks_path.write_text(json.dumps(uninstall_tasks), encoding="utf-8")
        _log.info("Written DEDUPLICATION_TASKS.json")

    if actionable_fixes:
        _generate_report(actionable_fixes, run_id, base_dir)

def _generate_report(actionable_fixes: list[dict], run_id: UUID, base_dir: Path) -> None:
    report_path = base_dir / str(run_id) / "DEDUPLICATION_REPORT.md"
    lines = [
        "# Ascendo Cross-Source Deduplication Report",
        "",
        "The following applications were detected using non-preferred package managers.",
        "Ascendo has ignored updates for the non-preferred sources.",
        "To ensure you receive updates correctly, please apply the recommended fixes below.",
        ""
    ]

    for fix in actionable_fixes:
        app = fix["app"]
        preferred = fix["preferred"]
        installed = fix["installed_sources"]
        
        lines.extend([
            f"## {app.name}",
            f"- **Recommended Source**: `{preferred}`",
            "- **Currently Installed Via**:"
        ])
        
        for src, (_, item) in installed.items():
            lines.append(f"  - `{src}` (Package ID: `{item.id}`, Version: `{item.current_version}`)")
            
        lines.extend([
            "",
            "### Proposed Fix",
            "Run the following commands to transition to the recommended source:",
            "```powershell"
        ])
        
        # Generate uninstall commands for non-preferred
        for src, (_, item) in installed.items():
            if src != preferred:
                if src == "winget":
                    lines.append(f"winget uninstall --id {item.id} --exact")
                elif src == "npm":
                    lines.append(f"npm uninstall -g {item.id}")
                elif src == "pip":
                    lines.append(f"pip uninstall -y {item.id}")
                elif src == "apt":
                    lines.append(f"sudo apt remove -y {item.id}")
                elif src == "snap":
                    lines.append(f"sudo snap remove {item.id}")
                elif src == "flatpak":
                    lines.append(f"flatpak uninstall -y {item.id}")
                elif src == "brew":
                    lines.append(f"brew uninstall {item.id}")
                elif src == "mas":
                    lines.append(f"mas uninstall {item.id}")
                elif src == "softwareupdate":
                    lines.append(f"# softwareupdate items cannot be selectively uninstalled")
                else:
                    lines.append(f"# Please uninstall '{item.name}' manually from {src}")

        # Generate install command for preferred
        pref_id = app.sources.get(preferred)
        if preferred == "winget":
            lines.append(f"winget install --id {pref_id} --exact")
        elif preferred == "npm":
            lines.append(f"npm install -g {pref_id}")
        elif preferred == "pip":
            lines.append(f"pip install {pref_id}")
        elif preferred == "apt":
            lines.append(f"sudo apt install -y {pref_id}")
        elif preferred == "snap":
            lines.append(f"sudo snap install {pref_id}")
        elif preferred == "flatpak":
            lines.append(f"flatpak install -y flathub {pref_id}")
        elif preferred == "brew":
            lines.append(f"brew install {pref_id}")
        elif preferred == "mas":
            lines.append(f"mas install {pref_id}")
        else:
            lines.append(f"# Please install '{app.name}' via {preferred}")
            
        lines.extend([
            "```",
            ""
        ])

    report_path.write_text("\n".join(lines), encoding="utf-8")
    _log.info("Generated DEDUPLICATION_REPORT.md at %s", report_path)
