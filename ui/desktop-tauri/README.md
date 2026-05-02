# Ascendo desktop shell (Tauri 2.x)

Native Windows/macOS/Linux desktop window for Ascendo. Spawns
`python -m ascendo dashboard` as a sidecar process on a loopback port and
opens it in a single 1280x800 WebView.

## Quick start (dev)

```powershell
# from repo root
bin/launch-desktop.ps1                # runs `npm run tauri dev`
bin/launch-desktop.ps1 -Build         # produces .msi + .exe in
                                      # ui/desktop-tauri/src-tauri/target/release/bundle/
```

Or directly:

```bash
cd ui/desktop-tauri
npm install
npm run tauri dev
```

## What it does at boot

1. Picks a port (tries `8765`, falls back to OS-assigned ephemeral).
2. Spawns `python -m ascendo dashboard --host 127.0.0.1 --port <port>`
   with stdio detached and (on Windows) `CREATE_NO_WINDOW` set.
3. Polls `http://127.0.0.1:<port>/health` every 200 ms for up to 10 s.
4. Opens a single window titled "Ascendo - Unified Updates" pointing at
   `WebviewUrl::External(http://127.0.0.1:<port>/)`.
5. On window close: `child.kill()` (TerminateProcess on Windows, SIGKILL
   on Unix) plus `child.wait()`.

## Build prerequisites

This repo cannot run `cargo check` or `npm run tauri dev` until the
following are installed on the host. The scaffold is otherwise complete
and the smoke test (see below) verifies file structure without any
toolchain.

| Tool                  | Why                                            | Install                                                              |
|-----------------------|------------------------------------------------|----------------------------------------------------------------------|
| Rust 1.78+            | Compiles `src-tauri/`                          | <https://rustup.rs> -> `rustup default stable`                       |
| Node 18+              | Runs the Tauri CLI                             | <https://nodejs.org> (or `winget install OpenJS.NodeJS.LTS`)         |
| MSVC build tools      | Linker for the Rust toolchain on Windows       | `winget install Microsoft.VisualStudio.2022.BuildTools` (with C++)   |
| WebView2 runtime      | Hosts the embedded webview                     | preinstalled on Windows 11; on 10 install from Microsoft             |

After all four are present:

```powershell
cd ui/desktop-tauri
npm install                 # ~30 s
npm run tauri dev           # first compile ~5-10 min, subsequent <30 s
```

## Layout

```
ui/desktop-tauri/
├── package.json                      # Tauri CLI 2.x
├── README.md                         # this file
├── .gitignore                        # node_modules, target, dist
├── src/                              # WebView fallback content
│   ├── index.html                    # splash (only seen if redirect fails)
│   └── main.js                       # one-shot reload safety net
└── src-tauri/
    ├── Cargo.toml                    # tauri = "2", tauri-plugin-shell = "2", ureq = "2"
    ├── build.rs                      # tauri_build::build()
    ├── tauri.conf.json               # window 1280x800, identifier dev.ascendo.app
    ├── src/
    │   └── main.rs                   # sidecar spawn + /health poll + window
    ├── icons/                        # 32, 128, 256, 512 PNG + .ico
    └── capabilities/
        └── default.json              # core:default + window/webview defaults
```

## Icons (TODO: replace placeholders)

The 5 icon files in `src-tauri/icons/` are currently solid-colour
placeholders generated from `branding/icon.svg`'s mid-gradient hue. They
satisfy the bundler's "all listed paths must exist" requirement but are
not the real Ascendo "A" mark.

To regenerate from the SVG (requires ImageMagick on PATH):

```powershell
$svg = "branding/icon.svg"
$out = "ui/desktop-tauri/src-tauri/icons"
magick $svg -define icon:auto-resize=256,128,64,48,32,16 "$out/icon.ico"
magick $svg -resize 32x32   "$out/32x32.png"
magick $svg -resize 128x128 "$out/128x128.png"
magick $svg -resize 256x256 "$out/128x128@2x.png"
magick $svg -resize 512x512 "$out/icon.png"
```

Or via Tauri's own helper once the toolchain is installed:

```bash
npm run tauri -- icon ../../branding/icon.svg
```

## Smoke test (no toolchain required)

```bash
python -m pytest ui/desktop-tauri/tests/test_scaffold.py -v
```

Verifies that `package.json`, `Cargo.toml`, `tauri.conf.json`, and
`main.rs` exist and reference the right things (Tauri 2 dev dep, window
size, sidecar invocation).

## Troubleshooting

| Symptom                                                           | Likely cause / fix                                                                 |
|-------------------------------------------------------------------|------------------------------------------------------------------------------------|
| Window opens, page shows "ERR_CONNECTION_REFUSED"                  | Sidecar didn't start. Check `python -m ascendo dashboard --port 8765` runs by hand. |
| `npm run tauri dev` errors with `linker 'link.exe' not found`      | Install MSVC build tools (see prerequisites table).                                 |
| `cargo check` fails on `tauri-build = { version = "2" }`           | `rustup update stable` — Tauri 2 needs Rust 1.78+.                                  |
| Window opens but stays on the splash "Starting Ascendo..."          | Sidecar didn't reach `/health` within 10s. Look at `bin/launch-desktop.ps1` stderr. |

## Relationship to legacy `app/tauri/`

`app/tauri/` is the Tauri 1.x app that this scaffold replaces. It is
intentionally untouched in this commit. Removal happens in a separate
cleanup commit once the Tauri 2 path is validated end-to-end.
