# Tauri skin

Native desktop shell wrapping the FastAPI dashboard.

## Architecture

```
┌────────────────────────────────────────────┐
│ Tauri window                                │
│  WebView → http://127.0.0.1:8765/           │
└──────────┬─────────────────────────────────┘
           │ spawned at startup
           ▼
   app/.venv/bin/python -m app.backend
       (uvicorn :8765, FastAPI, SPA)
```

The skin is intentionally thin: ~80 lines of Rust whose only job is to
spawn `python -m app.backend`, wait for the port to listen, then open a
webview at the URL. On close it kills the child.

## Why not pure Rust?

The FastAPI backend already exposes a stable REST API plus the vanilla SPA.
Reimplementing the same logic in Rust would be duplicate effort for zero
user-visible benefit. If you need a fully native binary later, swap the
webview URL for an embedded static SPA and port the API to a Rust HTTP
framework (axum/actix); the JSON contract stays unchanged.

## Build (one-shot, auto-installs prereqs)

```bash
cd app/tauri
bash build.sh           # interactive: prompts before installing rust/libs
# or
bash build.sh -y        # non-interactive, accepts all prompts

# Find the produced .deb / .AppImage:
find src-tauri/target -name 'ascendo*.deb' -o -name '*.AppImage'
```

`build.sh` will:
1. Install system libs (`libwebkit2gtk-4.1-dev`, `libgtk-3-dev`,
   `libayatana-appindicator3-dev`, `librsvg2-dev`, `libsoup-3.0-dev`,
   `pkg-config`, `build-essential`) via `sudo apt`.
2. Install Rust via rustup (`https://sh.rustup.rs`) if `cargo` missing.
3. Install `cargo install tauri-cli` (~3 min compile).
4. Bootstrap `app/.venv/` so the .deb works at runtime.
5. Run `cargo tauri build`, producing both `.deb` and `.AppImage`.

Skip auto-install with `--skip-deps` if you have prereqs already.

## Install the produced .deb

```bash
DEB=$(find src-tauri/target -name 'ascendo*.deb' | head -1)
sudo apt install "./$DEB"
ascendo      # launches the window
```

> **Important:** `apt install ./*.deb` only works when the path expands to
> exactly one file. If the wildcard returns no matches you'll get
> `Unsupported file ./...` — use the `find | head -1` form above.

## Manual build (if you prefer)

```bash
sudo apt install -y libwebkit2gtk-4.1-dev libgtk-3-dev \
    libayatana-appindicator3-dev librsvg2-dev libsoup-3.0-dev \
    pkg-config build-essential
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
source $HOME/.cargo/env
cargo install tauri-cli --version "^2"
bash ../install.sh
cd src-tauri && cargo tauri build
```

The `.deb` declares dependencies on `python3`, `python3-venv`, and
`ssh-client` (for the Hosts view). The python venv at
`~/Dev_Env/Ascendo/app/.venv/` is created on first run by
`bash app/install.sh` (or by `bash systemd/user/install-dashboard.sh`).

## Icons

`src-tauri/icons/` needs `icon.png`, `32x32.png`, `128x128.png`. Generate
from a 512×512 source:

```bash
cd src-tauri/icons
convert source.png -resize 32x32 32x32.png
convert source.png -resize 128x128 128x128.png
cp source.png icon.png
```

If you don't have one yet, the build will warn but still succeed using the
default Tauri icon.

## Troubleshooting

- `connection refused at 127.0.0.1:8765` after launch → the spawned python
  process didn't start. Check `journalctl --user -u ascendo-dashboard.service`
  or run `app/.venv/bin/python -m app.backend` manually to see the error.
- `python3: command not found` → install python3 or set `PATH` for the
  service. The skin falls back to `python3` if `app/.venv/bin/python` is
  missing.
