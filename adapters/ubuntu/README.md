# adapters/ubuntu/

Tier 1 (official) Linux/Ubuntu adapter for Ascendo.

## Supported categories

- `apt` — APT package manager (system packages)
- `snap` — Snap (Canonical's universal packages)
- `flatpak` — Flatpak (Linux universal packages)
- `brew` — Homebrew on Linux (`/home/linuxbrew/.linuxbrew`)
- `npm` — npm globals (cross-OS, but adapter-mediated)
- `pip` / `pipx` — Python packages
- `inventory` — generates APPS.md / PROGRAMS.md
- `drivers` — fwupd, NVIDIA (via plugins)

## Structure

```
adapters/ubuntu/
├── ascendo_ubuntu/         # Python adapter package
├── scripts/                # Bash 5-phase scripts per category
│   ├── apt/{check,plan,apply,verify,cleanup}.sh
│   ├── snap/...
│   └── ...
├── lib/                    # Bash shared utilities
│   ├── common.sh
│   ├── detect.sh
│   ├── json_emit.sh        # bash wrapper for core/ascendo/utils/json_emit.py
│   └── ...
├── systemd/                # systemd unit templates (timer, dashboard service)
├── tests/                  # pytest + bats
└── pyproject.toml          # adapter package metadata
```

## System dependencies

- Bash 5.0+
- Python 3.11+
- `apt`, `snap`, `flatpak` CLI binaries (presence-checked at runtime)
- `systemd` (for scheduler integration)
- `timeshift` or `etckeeper` (optional, for snapshots)
- `fwupd` (optional, for firmware updates)

## Tested distributions

- Ubuntu 22.04 LTS (Jammy)
- Ubuntu 24.04 LTS (Noble)
- Pop!_OS 22.04 (compatible)

## Migrated from

Ubuntu_Aktualizacje (`D:\Dev_Env\Ubuntu_Aktualizacje`) — most logic
preserved, refactored to clean architecture.

## See also

- `docs/adapter-author-guide.md` — adapter contract
- `docs/architecture/0006-two-tier-adapter-system.md` — Tier 1 vs Tier 2
