# plugins/nvidia-driver-update/

**Tier 1, Linux-only plugin** — wraps NVIDIA proprietary drivers via apt
+ `nvidia-driver-535` (or current LTS) + DKMS.

## Why a plugin (not core)?

Same rationale as `dell-driver-update`: NVIDIA driver management is
hardware-specific. Linux users without NVIDIA GPUs don't need this.

## Supported hardware

- NVIDIA GPUs requiring proprietary drivers (legacy + current generations)
- Tested on: Dell Precision 5520 with NVIDIA Quadro M1200M (legacy 470 driver)

## Phases (Linux only)

- **check** — `nvidia-smi --query-gpu=driver_version` + apt-cache policy
- **plan** — compare current vs available, factor in `--nvidia` opt-in flag
- **apply** — `apt install nvidia-driver-<version>` + DKMS rebuild
- **verify** — `nvidia-smi` runs, X11/Wayland session intact
- **cleanup** — apt autoremove old driver packages

## System dependencies

- Ubuntu 22.04+ or compatible Debian-based distro
- NVIDIA hardware
- `apt`, `dkms`, `apt-cache` available

## Important behavior

NVIDIA upgrades are **risky on a running session** — wrong driver version
can break X11/Wayland and require recovery. The plugin:

- Sets `risk = "high"` in manifest → `manual_confirm` enforced
- Recommends running from TTY (Ctrl+Alt+F3) instead of GUI session
- Emits prominent warning in `check` phase if user has X11 session active
- Generates `~/.ascendo/rollback/<run-id>-nvidia-instructions.md` for recovery

## Source

Existing config in `D:\Dev_Env\Ubuntu_Aktualizacje\config\nvidia-*.list`
plus inline NVIDIA logic in `update-all.sh` (`--nvidia` flag). Refactored
into plugin in M2 (alongside core scheduler/snapshot abstraction work).

## See also

- `docs/operator-runbook.md` — NVIDIA recovery procedures
