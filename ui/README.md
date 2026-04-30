# ui/

User interface layers: desktop shell (Tauri) and web frontend (vanilla JS SPA).

## Structure

```
ui/
├── desktop-tauri/      # Tauri 2.x native shell (Rust) — wraps backend in webview
└── frontend/           # vanilla JS SPA — same on all 3 OS, served by FastAPI backend
```

## Why two?

- **`desktop-tauri/`** is the native shell on each OS. Spawns the FastAPI
  backend as a child process, opens a webview pointing to
  `http://127.0.0.1:8765/`. ~80 LOC of Rust. Targets: `.deb`/`.AppImage`
  (Linux), `.msi`/`.exe`/NSIS (Windows), `.app`/`.dmg` (macOS).
- **`frontend/`** is the actual UI — vanilla JS, no build step, served by
  the FastAPI backend at `127.0.0.1:8765/`. Identical on all 3 OS.

## See also

- `docs/architecture/0002-tauri-as-desktop-shell.md` — why Tauri
- `ui/desktop-tauri/README.md` — build instructions per OS
