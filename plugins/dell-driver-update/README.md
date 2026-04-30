# plugins/dell-driver-update/

**Tier 1, Windows-only plugin** — wraps Dell Command Update CLI (`dcu-cli.exe`)
for Dell-branded laptops/desktops.

## Why a plugin (not core)?

Driver-management is a CORE concept (interface `IDriverProvider`), but
**vendor-specific implementations are plugins**. Most Ascendo users don't
have Dell hardware, so we don't burden them with Dell-specific logic.

## Supported hardware

- All Dell systems supported by Dell Command Update (corporate + consumer lines)
- Tested on: Dell Precision 5520 (DP5520WMK reference machine)

## Phases (Windows only)

- **check** — `dcu-cli.exe /scan -silent -outputFormat=xml` → parse to JSON sidecar
- **plan** — format scan output into per-driver upgrade plan
- **apply** — `dcu-cli.exe /applyUpdates -reboot=disable -silent`
- **verify** — re-scan, expect empty list
- **cleanup** — log rotation, archive `DCU_*.log` to `~/.ascendo/logs/`

## System dependencies

- Dell Command Update installed (auto-checked in `check` phase, plugin
  emits skip if missing)
- Windows 10+ on Dell hardware

## Source

`D:\Dev_Env\Aktualizacje-W11-Dell5520\2_Update-Drivers.ps1` — preserved 1:1,
moved to plugin in M3 with refactor to 5-phase contract.

## See also

- `docs/architecture/0009-driver-management-as-plugins.md` (TBD in M3)
