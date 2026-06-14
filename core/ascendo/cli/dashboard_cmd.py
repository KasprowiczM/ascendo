"""``ascendo dashboard`` command and ``ascendo web`` lifecycle subcommands."""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC
from pathlib import Path
from typing import Any

import typer

from ._app import (
    _default_runs_dir,
    _setup_logging,
    app,
    web_app,
)


@app.command()
def dashboard(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind address. Default: 127.0.0.1 (loopback only)."),
    port: int = typer.Option(8765, "--port", help="TCP port."),
    runs_dir: Path | None = typer.Option(None, "--runs-dir"),
    background: bool = typer.Option(
        False,
        "--background",
        "-b",
        help="Spawn uvicorn in a detached child process and return immediately.",
    ),
    allow_remote: bool = typer.Option(
        False,
        "--allow-remote",
        help="Allow binding to non-loopback addresses (e.g. 0.0.0.0).",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Launch the FastAPI dashboard backend on 127.0.0.1 (loopback only by default).

    With ``--background`` the dashboard is spawned as a detached child
    process and the CLI returns immediately. Stdout/stderr are silenced
    so the parent terminal does not stay tethered to the server. Use
    this when scripting (e.g. ``ascendo dashboard --background &&
    open http://127.0.0.1:8765/``).
    """
    _setup_logging(verbose)

    if host not in ("127.0.0.1", "localhost", "::1") and not allow_remote:
        typer.secho(
            f"error: Refusing to bind to remote-accessible host '{host}' without --allow-remote.",
            fg=typer.colors.RED,
            err=True,
        )
        typer.secho(
            "The dashboard exposes privileged operations. Binding to 0.0.0.0 without "
            "authentication can allow anyone on your network to execute updates.",
            fg=typer.colors.YELLOW,
            err=True,
        )
        raise typer.Exit(1)

    if background:
        # Probe whether a dashboard is already listening before spawning a
        # second one. The OS would error our second uvicorn out with
        # "Address already in use", but stderr is DEVNULL'd below so the
        # user only sees "started in background (pid=...)" and silently
        # gets a dead process. Worse, if NSSM or Tauri spawned the running
        # dashboard, the user double-spawns without realising it.
        try:
            import socket
            with socket.create_connection((host, port), timeout=0.5):
                typer.secho(
                    f"ascendo dashboard already listening on http://{host}:{port}/ — not re-spawning.",
                    fg=typer.colors.YELLOW,
                )
                typer.secho(
                    "tip: kill the existing process first, or use a different --port.",
                    fg=typer.colors.YELLOW,
                )
                return
        except (TimeoutError, OSError):  # nothing listening, safe to spawn
            pass
        # Re-invoke ourselves in the foreground mode of this same command,
        # detached from the parent process group / session so the terminal
        # is free to exit. Cross-platform via stdlib only.
        argv = [
            sys.executable, "-m", "ascendo", "dashboard",
            "--host", host, "--port", str(port),
        ]
        if runs_dir is not None:
            argv += ["--runs-dir", str(runs_dir)]
        if allow_remote:
            argv += ["--allow-remote"]
        if verbose:
            argv += ["--verbose"]
        popen_kwargs: dict[str, object] = {
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "stdin":  subprocess.DEVNULL,
        }
        if sys.platform == "win32":
            # CREATE_NEW_PROCESS_GROUP keeps the child alive past the
            # parent's exit; DETACHED_PROCESS detaches from the console.
            popen_kwargs["creationflags"] = (
                subprocess.CREATE_NEW_PROCESS_GROUP
                | getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
            )
        else:
            popen_kwargs["start_new_session"] = True
        proc = subprocess.Popen(argv, **popen_kwargs)
        typer.secho(
            f"ascendo dashboard started in background (pid={proc.pid}) on http://{host}:{port}/",
            fg=typer.colors.GREEN,
        )
        return

    try:
        import uvicorn
    except ImportError:
        typer.secho(
            "error: uvicorn not installed. Run: pip install 'ascendo[dashboard]' or pip install uvicorn",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(70) from None  # EX_SOFTWARE

    from ..dashboard import create_app

    base_dir = runs_dir or _default_runs_dir()
    # Thread host + allow_remote so create_app applies the LAN-safety guard
    # (refusal + capability-token gate) — defence in depth alongside the
    # CLI-level refusal above.
    app_instance = create_app(runs_dir=base_dir, host=host, allow_remote=allow_remote)
    typer.secho(
        f"ascendo dashboard listening on http://{host}:{port}/  (runs_dir={base_dir})",
        fg=typer.colors.GREEN,
    )
    uvicorn.run(app_instance, host=host, port=port, log_level="info" if verbose else "warning")


# ── `ascendo web` lifecycle subcommands ───────────────────────────────────
#
# Thin wrappers over the dashboard command for user-friendly start / stop /
# restart / status / open. Uses a pidfile at ``$ASCENDO_HOME/dashboard.pid``
# so stop/status work even after the parent terminal exits. Cross-platform:
# POSIX uses SIGTERM, Windows uses CTRL_BREAK_EVENT via taskkill.
#
# Why a pidfile and not pgrep? Because pgrep is unreliable cross-platform
# AND would match the Tauri-spawned dashboard sidecar (we want to NOT kill
# that). Pidfile is opt-in: only `ascendo web start` writes one; Tauri
# doesn't. So `ascendo web stop` exclusively kills the CLI-started one.

def _dashboard_pidfile() -> Path:
    override = os.environ.get("ASCENDO_HOME")
    base = Path(override) if override else Path.home() / ".ascendo"
    return base / "dashboard.pid"


def _read_pidfile() -> tuple[int | None, dict[str, Any]]:
    """Return (pid, metadata) from the pidfile, or (None, {}) if missing/corrupt.

    Metadata format is one line per ``key=value``: ``pid``, ``port``,
    ``host``, ``started_at``.
    """
    f = _dashboard_pidfile()
    if not f.is_file():
        return None, {}
    meta: dict[str, Any] = {}
    try:
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or "=" not in line:
                continue
            k, _, v = line.partition("=")
            meta[k.strip()] = v.strip()
    except OSError:
        return None, {}
    try:
        pid = int(meta.get("pid", "0"))
    except ValueError:
        pid = 0
    return (pid if pid > 0 else None), meta


def _write_pidfile(pid: int, host: str, port: int) -> None:
    f = _dashboard_pidfile()
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        from datetime import datetime
        f.write_text(
            "\n".join([
                f"pid={pid}",
                f"host={host}",
                f"port={port}",
                f"started_at={datetime.now(UTC).isoformat()}",
            ]) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        typer.secho(f"warning: could not write pidfile {f}: {exc}", fg=typer.colors.YELLOW, err=True)


def _clear_pidfile() -> None:
    f = _dashboard_pidfile()
    try:
        f.unlink(missing_ok=True)
    except OSError:
        pass


def _pid_alive(pid: int) -> bool:
    """True if the OS still has a process with this pid."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        # Windows: tasklist with /FI exact match. If the pid is listed we're alive.
        try:
            r = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True, text=True, timeout=5, check=False,
            )
            return str(pid) in r.stdout
        except (OSError, subprocess.TimeoutExpired):
            return False
    # POSIX: signal 0 is the "are you alive?" probe.
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False
    except OSError:
        return False


def _port_listening(host: str, port: int, timeout: float = 0.4) -> bool:
    """True if (host, port) accepts a TCP connection right now."""
    import socket
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (TimeoutError, OSError):
        return False


@web_app.command("start")
def web_start(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind address."),
    port: int = typer.Option(8765, "--port", help="TCP port."),
    open_browser: bool = typer.Option(
        True, "--open/--no-open",
        help="Open the dashboard in your default browser after start. On by default; pass --no-open for headless / SSH sessions.",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Start the web dashboard in the background and open it in your browser.

    Idempotent: if already running on the same port, prints the URL,
    opens the browser, and exits 0. Use ``ascendo web restart`` to
    force a fresh start. Pass ``--no-open`` for headless / SSH sessions.
    """
    _setup_logging(verbose)

    # Already running? (port-listening + pidfile concur)
    existing_pid, meta = _read_pidfile()
    if _port_listening(host, port):
        typer.secho(
            f"ascendo web already running on http://{host}:{port}/"
            + (f" (pid={existing_pid})" if existing_pid else ""),
            fg=typer.colors.GREEN,
        )
        if open_browser:
            _open_browser(host, port)
        return

    # Stale pidfile (process gone)?
    if existing_pid is not None and not _pid_alive(existing_pid):
        _clear_pidfile()

    # Spawn detached (same args as `dashboard --background`).
    argv = [
        sys.executable, "-m", "ascendo", "dashboard",
        "--host", host, "--port", str(port),
    ]
    if verbose:
        argv += ["--verbose"]
    popen_kwargs: dict[str, object] = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "stdin":  subprocess.DEVNULL,
    }
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP
            | getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
        )
    else:
        popen_kwargs["start_new_session"] = True
    proc = subprocess.Popen(argv, **popen_kwargs)
    _write_pidfile(proc.pid, host, port)

    # Poll for liveness up to 10s — uvicorn boot is usually <2s but cold
    # adapter discovery can stretch to 5–8s on first start.
    import time
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        if _port_listening(host, port):
            typer.secho(
                f"ascendo web started (pid={proc.pid}) on http://{host}:{port}/",
                fg=typer.colors.GREEN,
            )
            if open_browser:
                _open_browser(host, port)
            return
        time.sleep(0.25)
    typer.secho(
        f"ascendo web spawned (pid={proc.pid}) but health probe did not return within 10s — check `ascendo web status`",
        fg=typer.colors.YELLOW,
    )


@web_app.command("stop")
def web_stop(
    force: bool = typer.Option(
        False, "--force/-f",
        help="Send SIGKILL/taskkill /F if SIGTERM doesn't end the process within 5s.",
    ),
) -> None:
    """Stop the background web dashboard (one started via `ascendo web start`).

    Only kills the CLI-started dashboard process tracked by the pidfile.
    Tauri-spawned dashboard sidecars are left alone — their lifecycle is
    owned by the desktop app.
    """
    pid, meta = _read_pidfile()
    if pid is None:
        typer.secho("no pidfile — `ascendo web` is not running (or was started by something else).", fg=typer.colors.YELLOW)
        # Belt-and-suspenders: if the port is bound by SOMETHING, tell the user.
        host = str(meta.get("host", "127.0.0.1"))
        port = int(meta.get("port", 8765) or 8765)
        if _port_listening(host, port):
            typer.secho(
                f"tip: http://{host}:{port}/ is bound — likely the Tauri desktop app's sidecar (quit Ascendo.app to stop it).",
                fg=typer.colors.YELLOW,
            )
        raise typer.Exit(0)

    if not _pid_alive(pid):
        typer.secho(f"pidfile pointed at pid={pid} but no such process — cleaning up.", fg=typer.colors.YELLOW)
        _clear_pidfile()
        return

    # Graceful first: SIGTERM (POSIX) / taskkill (Win).
    typer.secho(f"stopping ascendo web (pid={pid})…", fg=typer.colors.WHITE)
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(pid)], capture_output=True, check=False)
        else:
            os.kill(pid, 15)  # SIGTERM
    except (OSError, ProcessLookupError):
        pass

    # Wait up to 5s for clean shutdown.
    import time
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            _clear_pidfile()
            typer.secho("stopped.", fg=typer.colors.GREEN)
            return
        time.sleep(0.2)

    if force:
        typer.secho("graceful stop timed out — escalating to force kill", fg=typer.colors.YELLOW)
        try:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, check=False)
            else:
                os.kill(pid, 9)  # SIGKILL
        except (OSError, ProcessLookupError):
            pass
        time.sleep(0.5)
        if not _pid_alive(pid):
            _clear_pidfile()
            typer.secho("force-stopped.", fg=typer.colors.GREEN)
            return
        typer.secho(f"pid={pid} still alive after force kill — investigate manually", fg=typer.colors.RED, err=True)
        raise typer.Exit(20)

    typer.secho(
        f"pid={pid} still alive after 5s. Re-run with --force to escalate to kill -9.",
        fg=typer.colors.YELLOW,
    )
    raise typer.Exit(1)


@web_app.command("restart")
def web_restart(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8765, "--port"),
    open_browser: bool = typer.Option(
        True, "--open/--no-open",
        help="Open the dashboard in your default browser after restart. Mirrors start default.",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Stop the running dashboard (if any) and start a fresh one.

    Equivalent to ``ascendo web stop && ascendo web start`` but with a
    single command and proper exit-code handling.
    """
    _setup_logging(verbose)
    pid, _ = _read_pidfile()
    if pid is not None and _pid_alive(pid):
        # Trigger graceful stop (ignore exit code — stop's own logging is enough).
        try:
            web_stop(force=True)
        except typer.Exit:
            pass
    web_start(host=host, port=port, open_browser=open_browser, verbose=verbose)


@web_app.command("status")
def web_status(
    json_output: bool = typer.Option(
        False, "--json",
        help="Emit a machine-readable JSON object instead of a human-friendly table.",
    ),
) -> None:
    """Report whether the web dashboard is running, on what port, and health.

    Sources truth from BOTH the pidfile (was started by `ascendo web start`)
    AND a live socket probe (something is listening), then reports any
    mismatch so you can untangle a stale pidfile from a Tauri-spawned
    sidecar.
    """
    pid, meta = _read_pidfile()
    host = str(meta.get("host", "127.0.0.1"))
    try:
        port = int(meta.get("port", 8765) or 8765)
    except (TypeError, ValueError):
        port = 8765
    pid_alive = pid is not None and _pid_alive(pid)
    bound = _port_listening(host, port)

    # Best-effort /health probe to confirm it's actually serving.
    health_ok: bool | None = None
    if bound:
        try:
            import urllib.request
            with urllib.request.urlopen(
                f"http://{host}:{port}/version", timeout=2,
            ) as r:
                health_ok = r.status == 200
        except Exception:
            health_ok = False

    if json_output:
        import json as _json
        payload = {
            "pidfile_present": pid is not None,
            "pid": pid,
            "pid_alive": pid_alive,
            "host": host,
            "port": port,
            "port_listening": bound,
            "health_ok": health_ok,
            "started_at": meta.get("started_at"),
        }
        typer.echo(_json.dumps(payload, indent=2))
        return

    if pid_alive and bound and health_ok:
        typer.secho(
            f"running  pid={pid}  http://{host}:{port}/  started={meta.get('started_at','?')}",
            fg=typer.colors.GREEN,
        )
        return
    if bound and not pid_alive:
        typer.secho(
            f"port {port} is bound but NOT by `ascendo web` (no pidfile / stale). "
            f"Likely the Tauri desktop app — quit Ascendo.app to release.",
            fg=typer.colors.YELLOW,
        )
        return
    if pid_alive and not bound:
        typer.secho(
            f"pid {pid} is alive but http://{host}:{port}/ is not responding — booting or stuck",
            fg=typer.colors.YELLOW,
        )
        return
    typer.secho(
        f"stopped  (pidfile: {'present but stale' if pid is not None else 'absent'};"
        f" {host}:{port} not bound)",
        fg=typer.colors.WHITE,
    )


@web_app.command("open")
def web_open() -> None:
    """Open the running dashboard in your default browser.

    Refuses to open if the dashboard isn't actually responding — better
    than leaving the user staring at a "can't connect" page.
    """
    _, meta = _read_pidfile()
    host = str(meta.get("host", "127.0.0.1"))
    try:
        port = int(meta.get("port", 8765) or 8765)
    except (TypeError, ValueError):
        port = 8765
    if not _port_listening(host, port):
        typer.secho(
            f"http://{host}:{port}/ is not responding. Run `ascendo web start` first.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)
    _open_browser(host, port)


def _open_browser(host: str, port: int) -> None:
    """Cross-platform `open URL in default browser`."""
    url = f"http://{host}:{port}/"
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif sys.platform == "win32":
            os.startfile(url)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        typer.secho(f"opened {url}", fg=typer.colors.GREEN)
    except OSError as exc:
        typer.secho(f"could not open browser: {exc}", fg=typer.colors.YELLOW, err=True)

