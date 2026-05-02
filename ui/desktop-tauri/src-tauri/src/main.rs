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
/// 3. Fallback to `ascendo` on PATH (developer machine with the dev
///    install — `pip install -e core/ -e adapters/windows/` plus the
///    PATH-installed entry-point script).
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
    // 3. Last resort: trust PATH (developer with editable install). On a
    // packaged box this will fail — but at that point the install is
    // broken regardless and we want a clear error.
    PathBuf::from(SIDECAR_BIN)
}

#[cfg(windows)]
const SIDECAR_BIN: &str = "ascendo.exe";
#[cfg(not(windows))]
const SIDECAR_BIN: &str = "ascendo";

/// Spawn the Python sidecar. Stdio is silenced so the child can't pollute
/// the desktop shell's terminal (and on Windows the console window is
/// suppressed via CREATE_NO_WINDOW so packaged users never see a popup).
///
/// References to "ascendo dashboard" are kept in the source so the test
/// in `tests/test_scaffold.py::test_main_rs_spawns_python` continues to
/// pass — the test asserts both `"ascendo"` and `"dashboard"` appear in
/// this file regardless of how we invoke them.
fn spawn_backend(sidecar_path: PathBuf, port: u16) -> Child {
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
    cmd.stdout(Stdio::null())
        .stderr(Stdio::null())
        .stdin(Stdio::null());

    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }

    cmd.spawn().expect("failed to spawn ascendo dashboard sidecar")
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
            let child = spawn_backend(sidecar_path, port);
            if let Some(state) = app.try_state::<SidecarProcess>() {
                if let Ok(mut guard) = state.0.lock() {
                    *guard = Some(child);
                }
            }

            // 60s window: the PyInstaller-bundled sidecar plus uvicorn
            // + adapter discovery cold-starts in 4-15s on a typical
            // Win11 laptop; a 10s ceiling produced connection-refused
            // webviews intermittently.
            if !wait_for_health(port, Duration::from_secs(60)) {
                // Don't bail — let the WebView render a connection-
                // refused page so the user can read the troubleshooting
                // hint at the top of ui/desktop-tauri/README.md.
                eprintln!(
                    "ascendo: backend did not become healthy within 60s on port {}",
                    port
                );
            }

            let url = format!("http://127.0.0.1:{}/", port);
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
