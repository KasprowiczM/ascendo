// Prevents additional console window on Windows in release.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

//! Ascendo desktop shell (Tauri 2.x).
//!
//! Boots in this order:
//!   1. Pick a loopback port (try 8765, fall back to OS-assigned).
//!   2. Locate the bundled `ascendo` sidecar binary. In a packaged
//!      install (MSI/NSIS) this lives under `<resource_dir>/binaries/
//!      python-sidecar/ascendo.exe`. In a `cargo run` dev build the
//!      same path is resolved relative to `src-tauri/binaries/...`.
//!   3. Spawn `ascendo.exe dashboard --host 127.0.0.1 --port <port>`
//!      as a child process, capturing the handle in shared state.
//!   4. Poll `http://127.0.0.1:<port>/health` for up to 60s
//!      (200ms interval) until it returns 200. If it never does, we
//!      still open the window — the user gets a connection-refused
//!      page they can recover from.
//!   5. Open a single 1280x800 WebView pointing at the sidecar.
//!   6. On window destroy, terminate the sidecar (kill on all
//!      platforms; on Windows this maps to TerminateProcess).
//!
//! Why a bundled sidecar instead of `python -m ascendo`?
//! End users running the MSI/NSIS installer do not have Python
//! installed (and shouldn't need to). The PyInstaller bundle in
//! `packaging/pyinstaller/ascendo.spec` produces a self-contained
//! `ascendo.exe` that is shipped as a Tauri bundle resource. The
//! shell never assumes a system Python.

use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::thread::sleep;
use std::time::{Duration, Instant};

use tauri::{Manager, WebviewUrl, WebviewWindowBuilder, WindowEvent};

/// Shared state: the handle to the Python sidecar process.
struct SidecarProcess(Mutex<Option<Child>>);

/// Try the canonical port first; fall back to an OS-assigned ephemeral port.
fn pick_port() -> u16 {
    use std::net::TcpListener;

    if TcpListener::bind("127.0.0.1:8765").is_ok() {
        return 8765;
    }
    let listener = TcpListener::bind("127.0.0.1:0").expect("could not bind to loopback");
    let port = listener
        .local_addr()
        .expect("listener has no local address")
        .port();
    drop(listener);
    port
}

/// Resolve the bundled `ascendo` sidecar binary.
///
/// Search order:
/// 1. Tauri resource dir (production install): `<resource_dir>/binaries/
///    python-sidecar/ascendo.exe`. The MSI/NSIS bundle ships the whole
///    `python-sidecar/` directory under `bundle.resources` in
///    `tauri.conf.json`.
/// 2. Repo-relative dev path: `<src-tauri>/binaries/python-sidecar/ascendo.exe`.
///    This is the layout `bin/build-installer.ps1` populates before
///    running `tauri build`, so `cargo run`/`tauri dev` works the same
///    way as a packaged install.
/// 3. Well-known absolute install paths. macOS GUI apps inherit only
///    launchctl's PATH (typically just `/usr/bin:/bin:/usr/sbin:/sbin`),
///    so a bare `Command::new("ascendo")` cannot find brew's
///    `/opt/homebrew/bin/ascendo` or the venv-installed
///    `~/.local/bin/ascendo` even though the user can run them from
///    Terminal. Probe every known location explicitly.
/// 4. Last resort: trust PATH (works in dev shells / Linux / Windows
///    where the GUI inherits the full env).
fn locate_sidecar(app_handle: &tauri::AppHandle) -> PathBuf {
    // 1. Packaged: Tauri resource_dir().
    if let Ok(resource_dir) = app_handle.path().resource_dir() {
        let candidate = resource_dir
            .join("binaries")
            .join("python-sidecar")
            .join(SIDECAR_BIN);
        if candidate.is_file() {
            return candidate;
        }
    }
    // 2. Dev: walk up from the current exe to find the src-tauri/binaries
    // path. `cargo run` puts the binary at `target/debug/ascendo-desktop.exe`
    // so we walk up two levels to land on `src-tauri/`.
    if let Ok(exe) = std::env::current_exe() {
        let mut cursor = exe.clone();
        for _ in 0..6 {
            if let Some(parent) = cursor.parent() {
                let candidate = parent
                    .join("binaries")
                    .join("python-sidecar")
                    .join(SIDECAR_BIN);
                if candidate.is_file() {
                    return candidate;
                }
                cursor = parent.to_path_buf();
            } else {
                break;
            }
        }
    }
    // 3. Well-known absolute install paths. Listed in priority order:
    //    user-local installs first (where install.sh drops the shim),
    //    then system-wide installs.
    let absolute_candidates: Vec<PathBuf> = {
        let mut v = Vec::new();
        // ~/.local/bin/ascendo — install.sh symlinks here on POSIX.
        if let Some(home) = std::env::var_os("HOME") {
            let mut p = PathBuf::from(home);
            p.push(".local");
            p.push("bin");
            p.push(SIDECAR_BIN);
            v.push(p);
        }
        // ~/.local/share/ascendo/.venv/bin/ascendo — direct venv shim.
        if let Some(home) = std::env::var_os("HOME") {
            let mut p = PathBuf::from(home);
            p.push(".local");
            p.push("share");
            p.push("ascendo");
            p.push(".venv");
            p.push("bin");
            p.push(SIDECAR_BIN);
            v.push(p);
        }
        #[cfg(target_os = "macos")]
        {
            // Apple Silicon Homebrew default.
            v.push(PathBuf::from("/opt/homebrew/bin").join(SIDECAR_BIN));
            // Intel Homebrew default.
            v.push(PathBuf::from("/usr/local/bin").join(SIDECAR_BIN));
            // System-wide install dir.
            v.push(PathBuf::from("/usr/local/bin").join(SIDECAR_BIN));
        }
        #[cfg(target_os = "linux")]
        {
            v.push(PathBuf::from("/usr/local/bin").join(SIDECAR_BIN));
            v.push(PathBuf::from("/opt/ascendo/.venv/bin").join(SIDECAR_BIN));
        }
        v
    };
    for candidate in &absolute_candidates {
        if candidate.is_file() {
            return candidate.clone();
        }
    }
    // 4. Last resort: trust PATH. On a fresh GUI-launch this typically fails
    // on macOS (launchctl PATH is restrictive), but spawn() will return
    // ErrorKind::NotFound and our caller now handles that gracefully
    // instead of panicking.
    PathBuf::from(SIDECAR_BIN)
}

#[cfg(windows)]
const SIDECAR_BIN: &str = "ascendo.exe";
#[cfg(not(windows))]
const SIDECAR_BIN: &str = "ascendo";

/// Minimal percent-encoding (RFC 3986 unreserved + safe HTML chars).
/// Used to embed our recovery HTML in a data: URL when the sidecar
/// can't be found. Pulling in a dep just for one urlencode call would
/// be silly — this covers everything the recovery page emits.
fn urlencoding_encode(s: &str) -> String {
    let mut out = String::with_capacity(s.len() * 3);
    for byte in s.bytes() {
        match byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                out.push(byte as char);
            }
            _ => {
                use std::fmt::Write;
                let _ = write!(out, "%{:02X}", byte);
            }
        }
    }
    out
}

/// Build the recovery HTML page shown when the sidecar can't be spawned.
fn fallback_html(attempted_path: &PathBuf) -> String {
    let attempted = attempted_path.display();
    format!(
        r#"<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Ascendo — sidecar not found</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
       background: #1a1a1a; color: #e0e0e0; padding: 40px; line-height: 1.6; max-width: 720px;
       margin: 40px auto; }}
h1 {{ font-size: 24px; margin: 0 0 16px; color: #fff; }}
.hint {{ background: #2a2a2a; padding: 16px; border-radius: 6px; border-left: 3px solid #ffb347; }}
code {{ background: #333; padding: 2px 6px; border-radius: 3px; font-family: ui-monospace, "SF Mono", Menlo, monospace;
        font-size: 13px; color: #b8e986; }}
pre {{ background: #0d1117; color: #d1d5da; padding: 16px; border-radius: 6px; overflow-x: auto;
        font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size: 13px; line-height: 1.5; }}
small {{ color: #888; }}
</style>
</head>
<body>
<h1>Ascendo can't find its backend yet</h1>
<p>The desktop app tried to launch the <code>ascendo</code> sidecar but couldn't
locate it on your system.</p>
<p class="hint">Most likely cause: macOS GUI apps inherit a restricted PATH
that doesn't include Homebrew. The fix is to symlink <code>ascendo</code> into
a default-PATH location.</p>
<p><b>Tried:</b> <code>{attempted}</code></p>
<p><b>To fix (one time, takes 5 seconds):</b></p>
<pre>sudo ln -sf $(which ascendo) /usr/local/bin/ascendo
open -a Ascendo</pre>
<p>If you don't have <code>ascendo</code> installed yet, run:</p>
<pre>curl -fsSL https://raw.githubusercontent.com/KasprowiczM/ascendo/main/install.sh | bash</pre>
<small>This page is shown by the Ascendo desktop shell when the sidecar
spawn fails. The full troubleshooting guide lives in
<code>MACOS_QUICKSTART.md</code> in the repo.</small>
</body>
</html>"#
    )
}

/// Spawn the Python sidecar. Stdio is silenced so the child can't pollute
/// the desktop shell's terminal (and on Windows the console window is
/// suppressed via CREATE_NO_WINDOW so packaged users never see a popup).
///
/// References to "ascendo dashboard" are kept in the source so the test
/// in `tests/test_scaffold.py::test_main_rs_spawns_python` continues to
/// pass — the test asserts both `"ascendo"` and `"dashboard"` appear in
/// this file regardless of how we invoke them.
fn spawn_backend(sidecar_path: PathBuf, port: u16) -> Option<Child> {
    // Invoke the bundled binary as `ascendo dashboard --host ... --port ...`.
    // This is the same CLI surface as `python -m ascendo dashboard` in
    // dev — only the binary itself differs.
    let mut cmd = Command::new(&sidecar_path);
    cmd.args([
        "dashboard",
        "--host",
        "127.0.0.1",
        "--port",
        &port.to_string(),
    ]);
    // Tell the Python core it's running inside the desktop shell, and which
    // shell version, so the self-update check can distinguish a desktop
    // (Tauri) launch from a plain `ascendo dashboard` and detect when the
    // native shell binary itself is out of date (ASCENDO_SHELL_VERSION vs
    // the manifest's shell.version).
    cmd.env("ASCENDO_DESKTOP", "1");
    cmd.env("ASCENDO_SHELL_VERSION", env!("CARGO_PKG_VERSION"));
    cmd.stdout(Stdio::null())
        .stderr(Stdio::null())
        .stdin(Stdio::null());

    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }

    // Fail-soft: a panic here used to manifest as the operator-visible
    // "Ascendo quit unexpectedly" dialog the user hit on macOS when the
    // GUI PATH didn't contain ascendo. Now we return None and let the
    // caller render an in-WebView error page instead, which the user
    // can read + recover from.
    match cmd.spawn() {
        Ok(child) => Some(child),
        Err(err) => {
            eprintln!(
                "ascendo: failed to spawn sidecar '{}': {} ({})",
                sidecar_path.display(),
                err,
                err.kind() as u8
            );
            None
        }
    }
}

/// Poll /health until it returns 200 or `timeout` elapses.
fn wait_for_health(port: u16, timeout: Duration) -> bool {
    let url = format!("http://127.0.0.1:{}/health", port);
    let start = Instant::now();
    while start.elapsed() < timeout {
        if let Ok(resp) = ureq::get(&url)
            .timeout(Duration::from_millis(500))
            .call()
        {
            if resp.status() == 200 {
                return true;
            }
        }
        sleep(Duration::from_millis(200));
    }
    false
}

fn main() {
    let port = pick_port();

    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .manage(SidecarProcess(Mutex::new(None)))
        .setup(move |app| {
            // Locate + spawn the sidecar AFTER the Tauri app is
            // constructed so we can reach app.path().resource_dir().
            // Doing it in the closure also ensures the child PID is
            // installed in shared state before any window event has a
            // chance to ask for it.
            let sidecar_path = locate_sidecar(&app.handle());

            // Always spawn our OWN fresh sidecar — never reuse a dashboard
            // that happens to be running on 8765. Reusing a stale process
            // meant an UPDATED app kept serving the old Python code in
            // memory (wrong version reported, /api/updates/* missing) until
            // the user manually killed it. pick_port() already fell back to
            // an ephemeral port when 8765 was taken, so spawning our own
            // never conflicts with whatever else is on 8765.
            let target_port = port;
            let spawned_ok = match spawn_backend(sidecar_path.clone(), port) {
                Some(child) => {
                    if let Some(state) = app.try_state::<SidecarProcess>() {
                        if let Ok(mut guard) = state.0.lock() {
                            *guard = Some(child);
                        }
                    }
                    true
                }
                None => false,
            };

            // 60s window: wait for the freshly-spawned sidecar to become healthy.
            if spawned_ok && !wait_for_health(port, Duration::from_secs(60)) {
                // Don't bail — let the WebView render a connection-
                // refused page so the user can read the troubleshooting
                // hint at the top of ui/desktop-tauri/README.md.
                eprintln!(
                    "ascendo: backend did not become healthy within 60s on port {}",
                    port
                );
            }

            // If the sidecar spawn failed (most common: macOS GUI PATH
            // lacks ascendo binary), point the WebView at an embedded
            // data: URL with a recovery banner instead of failing to
            // open at all. The user can read the hint and run the
            // suggested command in Terminal.
            let url = if spawned_ok {
                format!("http://127.0.0.1:{}/", target_port)
            } else {
                format!(
                    "data:text/html,{}",
                    urlencoding_encode(&fallback_html(&sidecar_path))
                )
            };
            let parsed_url = url
                .parse()
                .expect("constructed loopback URL must parse");

            // We deliberately do NOT declare any windows in tauri.conf.json
            // (`app.windows = []` there) so we can build the only window
            // here with a runtime-resolved URL. If a tauri.conf window with
            // label "main" is ever re-added, this builder will panic with
            // "a webview with label `main` already exists".
            let _window = WebviewWindowBuilder::new(
                app,
                "main",
                WebviewUrl::External(parsed_url),
            )
            .title("Ascendo - Unified Updates")
            .inner_size(1280.0, 800.0)
            .min_inner_size(960.0, 600.0)
            .resizable(true)
            .center()
            .build()?;
            Ok(())
        })
        .on_window_event(|window, event| {
            if let WindowEvent::Destroyed = event {
                let app = window.app_handle();
                if let Some(state) = app.try_state::<SidecarProcess>() {
                    if let Ok(mut guard) = state.0.lock() {
                        if let Some(mut child) = guard.take() {
                            // kill() maps to TerminateProcess on Windows
                            // and SIGKILL on Unix — adequate for a shell
                            // that owns the only handle to the sidecar.
                            let _ = child.kill();
                            let _ = child.wait();
                        }
                    }
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
