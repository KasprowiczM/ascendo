# Multi-Project Coexistence on Ubuntu

This repository is designed to fully coexist and run concurrently with the alternative Polish-localized repository `ascendo-ubuntu` situated at `Dev_Env/Ubuntu_Aktualizacje`. Both projects manage system updates on the same Ubuntu instance using distinct structures to guarantee zero mutual interference.

---

## 1. Coexistence Architecture

| Feature | Cross-Platform `ascendo` (This Repo) | Polish-Localized `ascendo-ubuntu` |
| :--- | :--- | :--- |
| **Port** | `8765` | `8766` |
| **Systemd Service** | `ascendo-dashboard.service` | `ascendo-ubuntu-dashboard.service` |
| **Installer Path** | `/opt/ascendo` | `/opt/ascendo-ubuntu` |
| **CLI Shim** | `/usr/local/bin/ascendo` | `/usr/bin/ascendo-ubuntu` |
| **Launch Shim** | `~/.local/bin/ascendo-launch` | `~/.local/bin/ascendo-ubuntu-launch` |
| **Desktop Shortcut** | `ascendo-desktop.desktop` | `ascendo-ubuntu-desktop.desktop` |
| **User Data Folder** | `~/.local/share/ascendo` | `~/.local/share/ubuntu-aktualizacje` |
| **User Config Folder**| `~/.config/ascendo` | `~/.config/ascendo-ubuntu` & `~/.config/ubuntu-aktualizacje` |

---

## 2. Standalone Tauri Desktop App Integration

To fulfill a unified lifecycle experience, the standalone desktop Tauri wrappers (`ascendo-desktop` and `ascendo-ubuntu-desktop`) manage their respective user-level systemd services under the hood:

1. **Service Start:** Upon launch, the desktop application executes a user-level `systemctl --user start <service>` command. If it succeeds, the app connects to the running dashboard service and sets a shutdown flag.
2. **Raw Process Fallback:** If systemd is unavailable (e.g. running outside a standard user session), the Tauri binary automatically falls back to spawning its own local Python backend process.
3. **Clean Shutdown:** Once the app window is closed, it executes a clean shutdown (`systemctl --user stop <service>` or kills the raw process), ensuring no dangling backend processes remain.

---

## 3. UI Navigation & External URL Redirection

Both projects use a secure global click interceptor in their frontend SPA layer to bypass strict WebKitGTK/Tauri sandboxing:
- Any call to `window.open(url, "_blank")` or link with `target="_blank"` is intercepted in `app.js`.
- The frontend posts the target URL to a local `/open-url` backend endpoint.
- The local FastAPI backend securely triggers the default system browser via Python's standard `webbrowser` module, popping open report/external links on the user's desktop seamlessly.

---

## 4. Verification & Testing

Both projects can run concurrently. To verify:
1. Verify the services run together:
   ```bash
   ss -lntp | grep -E '8765|8766'
   ```
2. Verify systemd user unit statuses:
   ```bash
   systemctl --user status ascendo-dashboard.service
   systemctl --user status ascendo-ubuntu-dashboard.service
   ```
3. Run all tests for the active repository to verify stability:
   ```bash
   python3 -m pytest tests/
   ```
