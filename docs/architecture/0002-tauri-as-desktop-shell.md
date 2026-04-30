# ADR 0002: Tauri as desktop shell

- **Status:** Accepted
- **Date:** 2026-04-30
- **Deciders:** @KasprowiczM
- **Supersedes:** —

## Context

Ascendo needs a desktop application UI that:

1. Runs as a single-icon-double-click experience for non-CLI users on
   three OSes (Linux, Windows, macOS).
2. Hosts the existing FastAPI backend + vanilla-JS SPA without rewriting
   them in another stack.
3. Feels native enough that Windows users don't see "Electron app" stigma
   (chunky bundle, ~150 MB RAM idle) and macOS users don't get a poorly
   integrated cross-platform feel.
4. Has a small enough Rust/native footprint that we don't take on
   significant Rust maintenance just to ship a webview.
5. Keeps a clean exit path — if Tauri itself becomes a problem, swapping
   it for an embedded HTTP server + system browser must be straightforward.

A Tauri proof-of-concept already exists in the source repo at
`app/tauri/` (developed during Etap 12 of `Ubuntu_Aktualizacje`). Its
`README.md` explicitly captured the swap-out path: *"If you need a fully
native binary later, swap the webview URL for an embedded static SPA and
port the API to a Rust HTTP framework — the JSON contract stays unchanged."*

## Decision

**Use Tauri 2.x as the desktop shell** for all three target OSes. The
existing `app/tauri/` prototype moves to `ui/desktop-tauri/` in the
restructured layout (per ADR-0001) and is extended from Linux-only to a
cross-OS build matrix.

The Tauri shell does three things only:
1. Spawn the bundled FastAPI backend (PyInstaller binary on Win/macOS,
   system Python on Linux).
2. Wait for `127.0.0.1:8765/health` to return 200.
3. Open a webview pointing at that URL.

No business logic in Rust. The `tauri.conf.json` allowlist is minimal.

## Consequences

### Positive

- **Bundle size: ~15-30 MB** (vs. Electron ~120 MB minimum). Materially
  matters for `winget` distribution and for the user's perception of
  "how big is this update tool I'm installing."
- **Native webview per OS:** WebView2 on Windows, WKWebView on macOS,
  WebKitGTK on Linux. The frontend SPA gets the OS-appropriate font
  rendering, smooth scrolling behavior, and security sandbox for free.
- **Existing prototype:** `app/tauri/` already works on Linux. Extending
  to Windows + macOS is incremental, not greenfield.
- **Rust shell at minimal surface (~80 LOC):** so few lines of Rust that
  almost any maintainer can read and understand the entire native side
  in 15 minutes.
- **Clear exit ramp:** the JSON contract between SPA and backend is HTTP +
  JSON; nothing Tauri-specific. Swapping Tauri for `pywebview` or even
  "open in default browser" is a 1-day refactor, not a rewrite.
- **Code signing / notarization integrate cleanly** with existing toolchains
  (Apple Developer ID, Authenticode), no Tauri-specific signing needed.

### Negative

- **Rust toolchain in CI:** Windows + macOS GitHub Actions runners need
  `rustup` and `cargo`. Adds ~2 minutes to CI cold starts. Acceptable.
- **Tauri 2.x is relatively young** (stable since late 2024). API churn is
  possible. Mitigated by pinning exact `tauri` and `tauri-cli` versions
  and reading every `CHANGELOG.md` before bumping.
- **WebView2 must be installed** on Windows < 10 build 19041. Tauri's
  installer offers to install it; we accept that prerequisite.
- **WebKitGTK behavioral differences** vs. Chrome: some CSS features
  ship later. We constrain the frontend to widely-supported CSS and
  avoid bleeding-edge features.

### Neutral

- The desktop shell does not get a CLI of its own — `ascendo` CLI is
  separate (Typer-based, ships from `core/`). They share a backend but
  not a launcher. Some users will use only one or the other; that's fine.

## Alternatives Considered

### Alternative 1: Electron

Description: Wrap the FastAPI backend + SPA in Electron, ship a
self-contained `~150 MB` binary per OS.

Why rejected:
- Bundle size 4-10× larger. Bad for distribution and user perception.
- Idle RAM ~150-300 MB just to host the webview. Bad for a tool that
  should run in the background.
- Chromium security update cadence means we'd be rebuilding releases
  monthly to ship patches.
- macOS notarization for Electron apps is fiddly compared to Tauri.

### Alternative 2: WinUI3 / SwiftUI / GTK (truly native per OS)

Description: Three separate native UIs — WinUI3 on Windows, SwiftUI on
macOS, GTK4 on Linux. Each consumes the FastAPI backend over HTTP.

Why rejected:
- Tripled UI work — three frontends to maintain, three style sheets,
  three accessibility audits, three bug surfaces.
- The dashboard is data-dense (status tables, run history, plugin lists).
  HTML/CSS does this well; rebuilding it three times is wasteful.
- Frontend contributors can be web developers. Native-three would require
  contributors fluent in three frameworks.

### Alternative 3: System default browser + tray icon

Description: Backend runs as a daemon. Tray icon (via `pystray`) opens
the dashboard URL in the user's default browser. No webview at all.

Why rejected:
- Loses "single window app" experience. Users on macOS/Windows expect a
  dock/taskbar icon they can pin and click — not "Ascendo runs and you
  open a Firefox tab to see it."
- Authentication + CSRF protection becomes harder when the dashboard
  shares a browser context with arbitrary other tabs.
- Kept as a fallback (`ascendo dashboard --open-in-browser`) for
  power-users and headless servers.

### Alternative 4: pywebview

Description: Python-only webview wrapper. Same model as Tauri but no
Rust shell.

Why rejected:
- Less polished cross-OS (notarization, autoupdate, code signing all
  harder).
- No daemon model for spawning the FastAPI backend; we'd need to
  reinvent the lifecycle management Tauri provides.
- Smaller community, slower bug-fix cadence.
- Worth revisiting if Tauri 2.x stability becomes a problem.

## References

- Related ADRs: [0001](0001-monorepo-with-adapters.md) (where Tauri lives:
  `ui/desktop-tauri/`), [0005](0005-six-layer-architecture.md) (Tauri is
  Layer 2), [0003](0003-json-v1-sidecar-contract.md) (the contract Tauri
  preserves)
- Existing prototype: `app/tauri/README.md` in pre-restructure repo
- Tauri docs: https://tauri.app/v2/
- HANDOFF.md — section "Dlaczego Tauri (a nie Electron, .NET MAUI, WinUI3)?"
