// Ascendo Tauri skin.
//
// Strategy: thin native shell. We do NOT reimplement the dashboard in Rust.
// Instead we spawn the existing FastAPI backend (uvicorn from app/.venv) as a
// child process at startup, wait for it to listen on 127.0.0.1:8765, then
// open a webview pointing there. On window close we terminate the child.
//
// This gives us:
//   - native .deb / .appimage packaging
//   - desktop integration (icon, taskbar, single window)
//   - zero duplication: same REST + same SPA as `python3 -m app.backend`

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::net::TcpStream;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};
use tauri::Manager;

struct AppState {
    child: Mutex<Option<Child>>,
    is_systemd: Mutex<bool>,
}

const HOST: &str = "127.0.0.1";
const PORT: u16 = 8765;
const STARTUP_TIMEOUT_SECS: u64 = 30;

fn repo_root() -> std::path::PathBuf {
    // src-tauri/ is at <repo>/app/tauri/src-tauri/. Walk up 3 levels.
    let mut p = std::env::current_exe().unwrap_or_else(|_| std::path::PathBuf::from("."));
    for _ in 0..6 {
        if p.join("update-all.sh").exists() {
            return p;
        }
        if !p.pop() {
            break;
        }
    }
    // Fallback: $HOME/Dev_Env/Ascendo
    if let Some(home) = std::env::var_os("HOME") {
        let candidate = std::path::PathBuf::from(home).join("Dev_Env/Ascendo");
        if candidate.exists() {
            return candidate;
        }
    }
    std::path::PathBuf::from(".")
}

fn spawn_backend() -> std::io::Result<Child> {
    let repo = repo_root();
    let venv_python = repo.join("app/.venv/bin/python");
    let python = if venv_python.exists() {
        venv_python.into_os_string()
    } else {
        std::ffi::OsString::from("python3")
    };
    let mut cmd = Command::new(python);
    cmd.current_dir(&repo)
        .args(["-m", "app.backend"])
        .env("UA_DASHBOARD_HOST", HOST)
        .env("UA_DASHBOARD_PORT", PORT.to_string())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    cmd.spawn()
}

fn wait_for_backend() -> bool {
    let start = Instant::now();
    while start.elapsed() < Duration::from_secs(STARTUP_TIMEOUT_SECS) {
        if TcpStream::connect((HOST, PORT)).is_ok() {
            return true;
        }
        thread::sleep(Duration::from_millis(150));
    }
    false
}

fn main() {
    // 1. Try to start the service via systemd-user
    let mut is_systemd = false;
    let mut child = None;

    let mut cmd = Command::new("systemctl");
    cmd.args(["--user", "start", "ascendo-dashboard.service"]);
    if let Ok(status) = cmd.status() {
        if status.success() {
            is_systemd = true;
            println!("Started dashboard service via systemd-user");
        }
    }

    // 2. Fallback to raw Python process spawn if systemd fails
    if !is_systemd {
        println!("systemd service launch failed/unavailable, falling back to direct spawn");
        child = spawn_backend().ok();
    }

    if !wait_for_backend() {
        eprintln!("warning: backend did not start within {STARTUP_TIMEOUT_SECS}s");
    }

    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .manage(AppState {
            child: Mutex::new(child),
            is_systemd: Mutex::new(is_systemd),
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { .. } = event {
                if let Some(state) = window.app_handle().try_state::<AppState>() {
                    // Check if we started via systemd
                    if let Ok(is_sys) = state.is_systemd.lock() {
                        if *is_sys {
                            let mut cmd = Command::new("systemctl");
                            cmd.args(["--user", "stop", "ascendo-dashboard.service"]);
                            let _ = cmd.status();
                            println!("Stopped dashboard service via systemd-user");
                            return;
                        }
                    }
                    // Direct spawn fallback cleanup
                    if let Ok(mut guard) = state.child.lock() {
                        if let Some(mut c) = guard.take() {
                            let _ = c.kill();
                            let _ = c.wait();
                            println!("Terminated raw backend process");
                        }
                    }
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
