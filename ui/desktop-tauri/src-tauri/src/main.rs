// Prevents additional console window on Windows in release.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

//! Ascendo desktop shell (Tauri 2.x).
//!
//! Boots in this order:
//!   1. Pick a loopback port (try 8765, fall back to OS-assigned).
//!   2. Spawn `python -m ascendo dashboard --host 127.0.0.1 --port <port>`
//!      as a child process, capturing the handle in shared state.
//!   3. Poll `http://127.0.0.1:<port>/health` for up to 10s (200ms interval)
//!      until it returns 200. If it never does, we still open the window —
//!      the user gets a connection-refused page that can guide them to the
//!      troubleshooting docs.
//!   4. Open a single 1280x800 WebView pointing at the sidecar.
//!   5. On window destroy, terminate the sidecar (kill on all platforms;
//!      on Windows this maps to TerminateProcess, on Unix to SIGKILL).

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

/// Spawn the Python sidecar. Stdio is silenced so the child can't pollute
/// the desktop shell's terminal (and on Windows the console window is
/// suppressed via CREATE_NO_WINDOW so packaged users never see a popup).
fn spawn_backend(port: u16) -> Child {
    let mut cmd = Command::new("python");
    cmd.args([
        "-m",
        "ascendo",
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
    let child = spawn_backend(port);

    if !wait_for_health(port, Duration::from_secs(10)) {
        // Don't bail — let the WebView render a connection-refused page so
        // the user can read the troubleshooting hint at the very top of
        // ui/desktop-tauri/README.md.
        eprintln!(
            "ascendo: backend did not become healthy within 10s on port {}",
            port
        );
    }

    let url = format!("http://127.0.0.1:{}/", port);
    let parsed_url = url
        .parse()
        .expect("constructed loopback URL must parse");

    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .manage(SidecarProcess(Mutex::new(Some(child))))
        .setup(move |app| {
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
