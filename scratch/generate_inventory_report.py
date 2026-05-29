import json
import os
from pathlib import Path
from collections import defaultdict

def generate_report():
    runs_dir = Path(os.path.expanduser("~/.ascendo/runs"))
    if not runs_dir.exists():
        print("No runs dir found.")
        return

    # Find the latest run
    run_dirs = sorted([d for d in runs_dir.iterdir() if d.is_dir()], key=os.path.getmtime, reverse=True)
    if not run_dirs:
        print("No runs found.")
        return
        
    latest_run = run_dirs[0]
    print(f"Using run: {latest_run.name}")

    inventory = defaultdict(list)
    
    for f in latest_run.glob("check__*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            category = data.get("category", "unknown")
            for item in data.get("items", []):
                if item.get("current_version"):
                    inventory[category].append(item)
        except Exception as e:
            pass
            
    report_lines = ["# Windows Inventory Report", ""]
    
    for category, items in inventory.items():
        report_lines.append(f"## {category.upper()} Apps")
        for item in items:
            name = item.get("name")
            current = item.get("current_version")
            target = item.get("target_version")
            status = item.get("status")
            update_action = "Needs Update" if target and target != current else "Up-to-date"
            report_lines.append(f"- **{name}** (v{current}) -> Target: {target or 'None'} [{update_action}]")
        report_lines.append("")
        
    report_path = Path("C:/Users/MK/.gemini/antigravity-ide/brain/187e3512-fee1-4911-9ed4-f58384ee02bf/INVENTORY_REPORT.md")
    report_path.write_text("\n".join(report_lines), encoding="utf-8")
    print("Report generated.")

if __name__ == "__main__":
    generate_report()
