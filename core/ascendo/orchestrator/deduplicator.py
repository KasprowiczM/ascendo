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

def apply_deduplication(sidecars: list[Sidecar], run_id: UUID, base_dir: Path, config_path: Path | None = None) -> None:
    """Analyzes check phase sidecars, identifies duplicates, and ignores non-preferred sources.
    
    Generates a DEDUPLICATION_REPORT.md if actionable duplicates are found.
    Mutates the `sidecars` list items in-place and rewrites them to disk.
    """
    # Look for config in adapters/windows/config
    if config_path is None:
        config_path = Path(__file__).parent.parent.parent.parent / "adapters" / "windows" / "config" / "app_sources.toml"
    if not config_path.exists():
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

    for app in registry.apps:
        # Check which sources are installed for this logical app
        installed_sources = {}
        for sidecar, item in installed_items:
            # Does this item match one of the mapped sources for this app?
            for source_name, source_pkg_id in app.sources.items():
                if sidecar.category.value == source_name and item.id == source_pkg_id:
                    installed_sources[source_name] = (sidecar, item)

        print(f"DEBUG: installed_sources for {app.id} = {list(installed_sources.keys())}")
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
                    print(f"DEBUG: Skipping {src_name} item {item.id}")
                    item.status = ItemStatus.SKIPPED
                    item.target_version = item.current_version  # Suppress update
                    item.messages.append(Message(
                        level=MessageLevel.WARN,
                        text=f"Ignored update from non-preferred source '{src_name}'. Preferred is '{absolute_preferred}'."
                    ))
                    # Rewrite the sidecar to disk since we mutated it
                    write_sidecar(sidecar, base_dir=base_dir)

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
        else:
            lines.append(f"# Please install '{app.name}' via {preferred}")
            
        lines.extend([
            "```",
            ""
        ])

    report_path.write_text("\n".join(lines), encoding="utf-8")
    _log.info("Generated DEDUPLICATION_REPORT.md at %s", report_path)
