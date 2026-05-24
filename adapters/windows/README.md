# adapters/windows/

Tier 1 (official) Windows adapter for Ascendo. Source of native PowerShell
scripts is the legacy `D:\Dev_Env\Ascendo` repository,
refactored to the 5-phase contract.

## Supported categories

- `windows-update` — Windows OS updates via PSWindowsUpdate
- `winget` — winget package manager (Microsoft + community sources)
- `msstore` — Microsoft Store apps via winget msstore + native SYSTEM scan fallback
- `npm` — npm globals (cross-OS abstract, Windows-specific binary paths)
- `inventory` — generates PROGRAMS.md / PROGRAMS.json
- `drivers` — Dell, NVIDIA, etc. (via plugins, NOT in core)

## Hidden gems preserved

PowerShell hidden gems from the legacy repo, isolated into reusable modules
in `lib/`:

- **Column-position parser** (`Winget-Parser.ps1`) — `Get-ColValue` reads
  fixed-width columns from header positions (immune to spaces in package
  names + UTF-8 ellipsis bug at U+2026)
- **Unknown-version suppression** (`Unknown-Version.ps1`) — local evidence
  (registry/AppX/MSIX manifest version) blocks repeat upgrade offers when
  winget reports "Available: Unknown" but local version matches
- **Native installer detection** (`Native-Installer.ps1`) — whitelist for
  packages with first-class native installers (e.g. Claude Code at
  `~\.local\bin\claude.exe`)
- **Exit-code mapping** — handles `0`/`-1978335190`/`-1978335212`/`3010`
  with clear semantics (success / already-current / not-found / reboot-required)
- **Test-IsActualPowerShell** — distinguishes real PowerShell upgrade
  (emit exit 2 for self-restart) from Windows Terminal upgrade (no restart)
- **Separator-before-header** — winget list header detection that's immune to
  locale changes and regex encoding bugs

## Structure

```
adapters/windows/
├── ascendo_windows/        # Python adapter package
├── scripts/                # PowerShell 5-phase scripts per category
│   ├── windows-update/{check,plan,apply,verify,cleanup}.ps1
│   ├── winget/...
│   ├── msstore/...
│   └── inventory/...
├── lib/                    # Shared PowerShell modules (.ps1 dot-source style)
│   ├── Common.ps1
│   ├── Json-Emit.ps1
│   ├── Winget-Parser.ps1
│   ├── Unknown-Version.ps1
│   ├── Native-Installer.ps1
│   ├── Process-Kill.ps1
│   └── Elevation.ps1
└── tests/                  # Pester + pytest
```

## System dependencies

- Windows 10 (build 19041+) or Windows 11
- PowerShell 5.1+ or 7.x (both supported, tested on 7.6.0)
- winget (built-in on modern Windows, version 1.28+)
- PSWindowsUpdate module (auto-installed if missing)
- .NET runtime (built-in)
- Python 3.11+ (bundled via PyInstaller in MSI installer)

## Tested

- Windows 11 Pro Build 26200 (DP5520WMK reference machine)

## Migration source

- `D:\Dev_Env\Ascendo\` — ALL logic preserved
- `0_Run-Maintenance.ps1` → archived (orchestration moved to Python core)
- `1_Update-Windows.ps1` → `scripts/windows-update/`
- `2_Update-Drivers.ps1` → `plugins/dell-driver-update/windows/`
- `3_Update-Programs.ps1` → split into `scripts/winget/` + `scripts/msstore/` + `plugins/agent-clis/windows/`
- `4_Generate-ProgramsList.ps1` → `scripts/inventory/`
- `5_Add-ToPath.ps1` → handled by MSI installer (`packaging/msi/`)
- `6_Migration-Setup.ps1` → `core/ascendo/cli/commands/setup.py`

## See also

- `docs/adapter-author-guide.md`
- `HANDOFF.md` "Migration plan"
