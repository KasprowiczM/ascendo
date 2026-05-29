"""Dashboard FastAPI application — local 127.0.0.1 service."""
from __future__ import annotations

import asyncio
import json
import shlex
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import (
    config,
    db,
    settings as settings_mod,
    sudo as sudo_mod,
    hosts as hosts_mod,
    inventory as inv_mod,
    audit as audit_mod,
    auth as auth_mod,
    metrics as metrics_mod,
    report as report_mod,
    diff as diff_mod,
    suggestions as sugg_mod,
    health as health_mod,
    backup as backup_mod,
    telemetry as telem_mod,
    exclusions as excl_mod,
    hosts_edit as hosts_edit_mod,
)
from .runner import get_runner

app = FastAPI(title="Ascendo - Unified Updates", version="0.1.0")
app.add_middleware(auth_mod.TokenAuthMiddleware)


class StartRunRequest(BaseModel):
    profile: str | None = None
    only: str | None = None
    phase: str | None = None
    dry_run: bool = False
    # extra_args are forwarded verbatim to update-all.sh after sanitisation.
    # Whitelisted to a known set so the UI cannot inject arbitrary flags.
    extra_args: list[str] = []


class SudoAuthRequest(BaseModel):
    password: str


@app.on_event("startup")
def _startup() -> None:
    db.init_db(config.db_path())
    # Pull in any runs created from the CLI / scheduler so they appear in
    # history immediately on dashboard boot.
    try:
        with db.connect(config.db_path()) as con:
            db.import_disk_runs(con, config.runs_dir())
    except Exception as exc:  # pragma: no cover — never block startup
        print(f"warning: import_disk_runs failed: {exc}")


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "repo_root": str(config.repo_root()),
        "db_path": str(config.db_path()),
        "runs_dir": str(config.runs_dir()),
    }


@app.get("/categories")
def list_categories() -> dict[str, Any]:
    cats = config.load_categories()
    return {"categories": [c.__dict__ for c in cats.values()]}


@app.get("/profiles")
def list_profiles() -> dict[str, Any]:
    profs = config.load_profiles()
    return {"profiles": [p.__dict__ for p in profs.values()]}


@app.get("/preflight")
def preflight() -> dict[str, Any]:
    repo = config.repo_root()
    items: list[dict[str, Any]] = []
    # Required tools
    import shutil
    for tool in ["bash", "python3", "git", "flock", "apt-get", "snap", "flatpak", "brew", "npm", "pipx"]:
        items.append({"tool": tool, "present": shutil.which(tool) is not None})
    reboot_required = Path("/var/run/reboot-required").exists()
    return {
        "items": items,
        "needs_reboot": reboot_required,
        "repo_clean": (repo / ".git").exists(),
    }


@app.get("/runs")
def runs(limit: int = 100) -> dict[str, Any]:
    with db.connect(config.db_path()) as con:
        # Reconcile filesystem-only runs (CLI / scheduler) before listing.
        imported = db.import_disk_runs(con, config.runs_dir())
        return {"runs": db.list_runs(con, limit=limit), "imported": imported}


@app.post("/runs")
async def start_run(req: StartRunRequest) -> dict[str, Any]:
    # Mutating phases need sudo. Pre-flight check protects from non-TTY failure.
    mutating = (req.phase in (None, "apply", "cleanup")) and not req.dry_run
    if mutating:
        st = sudo_mod.status()
        if not st.cached:
            raise HTTPException(
                status_code=401,
                detail={
                    "code": "SUDO-REQUIRED",
                    "msg": "sudo cache not warm; call POST /sudo/auth with password first",
                    "sudo_detail": st.detail,
                },
            )
    # Whitelist for extra_args — we never pass through unknown flags.
    _ALLOWED_EXTRA = {"--nvidia", "--snapshot", "--no-drivers", "--no-health"}
    safe_extra = [a for a in (req.extra_args or []) if a in _ALLOWED_EXTRA]
    runner = get_runner()
    try:
        h = runner.start(
            loop=asyncio.get_running_loop(),
            profile=req.profile,
            only=req.only,
            phase=req.phase,
            dry_run=req.dry_run,
            extra_args=safe_extra,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    audit_mod.log("run.start", details={
        "run_id": h.run_id, "profile": req.profile, "only": req.only,
        "phase": req.phase, "dry_run": req.dry_run, "extra": safe_extra,
    })
    return {"run_id": h.run_id, "started_at": h.started_at}


@app.get("/sudo/status")
def sudo_status() -> dict[str, Any]:
    s = sudo_mod.status()
    return {"cached": s.cached, "detail": s.detail}


@app.post("/sudo/auth")
def sudo_auth(req: SudoAuthRequest) -> dict[str, Any]:
    ok, detail = sudo_mod.store(req.password)
    if not ok:
        raise HTTPException(status_code=401, detail={"code": "SUDO-AUTH-FAIL", "msg": detail})
    return {"cached": True, "detail": detail}


@app.post("/sudo/invalidate")
def sudo_invalidate() -> dict[str, Any]:
    ok = sudo_mod.invalidate()
    return {"invalidated": ok}


# ── Multi-host (SSH read-only) ────────────────────────────────────────────────

@app.get("/hosts")
def list_hosts() -> dict[str, Any]:
    hosts = hosts_mod.load_hosts()
    return {"hosts": [
        {**h.__dict__} for h in hosts.values()
    ]}


@app.get("/hosts/{host_id}/preflight")
def host_preflight(host_id: str) -> dict[str, Any]:
    hosts = hosts_mod.load_hosts()
    h = hosts.get(host_id)
    if h is None:
        raise HTTPException(status_code=404, detail=f"unknown host: {host_id}")
    return hosts_mod.preflight(h)


# ── Inventory (live package scan) ─────────────────────────────────────────────

@app.get("/inventory/summary")
def inventory_summary() -> dict[str, Any]:
    return inv_mod.summary()


@app.get("/inventory")
def inventory_all() -> dict[str, Any]:
    return {"categories": inv_mod.scan_all()}


@app.get("/inventory/{category}")
def inventory_category(category: str) -> dict[str, Any]:
    try:
        items = inv_mod.scan_category(category)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"category": category, "items": items}


@app.post("/inventory/refresh")
def inventory_refresh(category: str | None = None) -> dict[str, Any]:
    inv_mod.invalidate(category)
    return {"refreshed": category or "all"}


# IMPORTANT: register literal /runs/active routes BEFORE /runs/{run_id} so
# FastAPI's path-matching prefers the static route.
@app.get("/runs/active")
def get_active_run() -> dict[str, Any]:
    h = get_runner().active
    if not h:
        return {"active": None}
    return {
        "active": {
            "run_id": h.run_id,
            "started_at": h.started_at,
            "profile": h.profile,
            "only": h.only_cat,
            "phase": h.only_phase,
            "dry_run": h.dry_run,
            "finished": h.finished,
            "exit_code": h.exit_code,
        }
    }


@app.post("/runs/active/stop")
def stop_active_run() -> dict[str, Any]:
    runner = get_runner()
    ok = runner.stop()
    return {"stopped": ok, "run_id": runner.active.run_id if runner.active else None}


@app.get("/runs/active/stream")
async def stream_active_run() -> StreamingResponse:
    runner = get_runner()
    h = runner.active
    if not h:
        raise HTTPException(status_code=404, detail="no active run")

    async def gen():
        try:
            while True:
                if h.queue.empty() and h.finished:
                    yield f"event: done\ndata: {json.dumps({'exit_code': h.exit_code})}\n\n"
                    break
                try:
                    msg = await asyncio.wait_for(h.queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                if msg.get("type") == "done":
                    yield f"event: done\ndata: {json.dumps(msg)}\n\n"
                    break
                yield f"event: log\ndata: {json.dumps(msg)}\n\n"
        except asyncio.CancelledError:
            return

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    with db.connect(config.db_path()) as con:
        r = db.get_run(con, run_id)
        if r is None:
            # Lazy reconcile: the run may have been created from CLI after
            # the last `/runs` listing. Importing it inserts the row + phases.
            db.import_disk_runs(con, config.runs_dir())
            r = db.get_run(con, run_id)
    if r is None:
        run_dir = config.runs_dir() / run_id
        rj = run_dir / "run.json"
        if rj.exists():
            return {"run": json.loads(rj.read_text(encoding="utf-8")), "from_disk": True}
        raise HTTPException(status_code=404, detail="run not found")
    return {"run": r}


@app.get("/runs/{run_id}/phase/{category}/{phase}")
def get_phase_sidecar(run_id: str, category: str, phase: str) -> dict[str, Any]:
    p = config.runs_dir() / run_id / category / f"{phase}.json"
    if not p.exists():
        raise HTTPException(status_code=404, detail="sidecar not found")
    return json.loads(p.read_text(encoding="utf-8"))


@app.get("/runs/{run_id}/phase/{category}/{phase}/log")
def get_phase_log(run_id: str, category: str, phase: str) -> FileResponse:
    p = config.runs_dir() / run_id / category / f"{phase}.log"
    if not p.exists():
        raise HTTPException(status_code=404, detail="log not found")
    return FileResponse(p, media_type="text/plain")


def _git_run(args: list[str], *, check: bool = True) -> tuple[int, str, str]:
    import subprocess
    repo = config.repo_root()
    res = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True
    )
    if check and res.returncode != 0:
        raise HTTPException(status_code=500, detail=res.stderr.strip() or res.stdout.strip())
    return res.returncode, res.stdout, res.stderr


@app.get("/git/status")
def git_status() -> dict[str, Any]:
    import subprocess
    repo = config.repo_root()
    try:
        branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo, text=True
        ).strip()
        dirty = subprocess.check_output(
            ["git", "status", "--porcelain=v1", "--untracked-files=no"],
            cwd=repo, text=True,
        ).strip()
        ahead_behind = "0\t0"
        try:
            ahead_behind = subprocess.check_output(
                ["git", "rev-list", "--left-right", "--count", "HEAD...@{u}"],
                cwd=repo, text=True,
            ).strip()
        except subprocess.CalledProcessError:
            pass
        ahead, behind = (ahead_behind.split() + ["0", "0"])[:2]
        return {
            "branch": branch,
            "dirty": bool(dirty),
            "ahead": int(ahead),
            "behind": int(behind),
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/git/fetch")
def git_fetch() -> dict[str, Any]:
    rc, out, err = _git_run(["fetch", "--prune"])
    return {"ok": rc == 0, "stdout": out, "stderr": err}


@app.post("/git/pull")
def git_pull() -> dict[str, Any]:
    """Fast-forward only pull. Refuses if working tree dirty."""
    import subprocess
    repo = config.repo_root()
    dirty = subprocess.check_output(
        ["git", "status", "--porcelain=v1", "--untracked-files=no"],
        cwd=repo, text=True,
    ).strip()
    if dirty:
        raise HTTPException(status_code=409, detail="working tree is dirty; commit or stash first")
    rc, out, err = _git_run(["pull", "--ff-only"])
    return {"ok": rc == 0, "stdout": out, "stderr": err}


@app.post("/git/push")
def git_push() -> dict[str, Any]:
    """Push current branch to its upstream. Never force."""
    rc, out, err = _git_run(["push"])
    return {"ok": rc == 0, "stdout": out, "stderr": err}


@app.get("/sync/status")
def sync_status() -> dict[str, Any]:
    """Read latest dev-sync verification log if present."""
    log_dir = config.repo_root() / "dev_sync_logs"
    if not log_dir.exists():
        return {"available": False, "reason": "dev_sync_logs/ missing"}
    logs = sorted(log_dir.glob("*-verify-full.log"), reverse=True)
    if not logs:
        return {"available": False, "reason": "no verify-full logs"}
    latest = logs[0]
    head = latest.read_text(encoding="utf-8", errors="replace").splitlines()[:40]
    overall = "unknown"
    for line in head:
        if line.startswith("OVERALL "):
            overall = line.split(maxsplit=1)[1].strip()
            break
    return {
        "available": True,
        "log_path": str(latest.relative_to(config.repo_root())),
        "overall": overall,
        "head": head,
    }


@app.get("/settings")
def get_settings() -> dict[str, Any]:
    return settings_mod.load()


@app.put("/settings")
def put_settings(payload: dict[str, Any]) -> dict[str, Any]:
    return settings_mod.save(payload)


@app.post("/scheduler/install")
def scheduler_install(payload: dict[str, Any]) -> dict[str, Any]:
    """Install/update systemd timer using the user's preferences."""
    import subprocess
    s = settings_mod.load()
    sched = {**s.get("scheduler", {}), **payload}
    cmd = [
        str(config.repo_root() / "scripts" / "scheduler" / "install.sh"),
        "--calendar", sched.get("calendar", "Sun *-*-* 03:00:00"),
        "--profile",  sched.get("profile",  "safe"),
    ]
    if sched.get("no_drivers"):
        cmd.append("--no-drivers")
    res = subprocess.run(cmd, capture_output=True, text=True)
    s["scheduler"] = {**sched, "enabled": res.returncode == 0}
    settings_mod.save(s)
    return {
        "ok": res.returncode == 0,
        "stdout": res.stdout[-2000:],
        "stderr": res.stderr[-2000:],
        "settings": s,
    }


@app.post("/scheduler/remove")
def scheduler_remove() -> dict[str, Any]:
    import subprocess
    cmd = [str(config.repo_root() / "scripts" / "scheduler" / "install.sh"), "--remove"]
    res = subprocess.run(cmd, capture_output=True, text=True)
    s = settings_mod.load()
    s["scheduler"]["enabled"] = False
    settings_mod.save(s)
    return {"ok": res.returncode == 0, "stdout": res.stdout, "stderr": res.stderr}


@app.post("/sync/export")
def sync_export(dry_run: bool = True) -> dict[str, Any]:
    """Run dev-sync export. Defaults to dry-run for safety."""
    import subprocess
    repo = config.repo_root()
    cmd = [str(repo / "dev-sync-export.sh")]
    if dry_run:
        cmd.extend(["--dry-run", "--verbose"])
    res = subprocess.run(cmd, cwd=repo, capture_output=True, text=True)
    return {
        "ok": res.returncode == 0,
        "dry_run": dry_run,
        "stdout": res.stdout[-4000:],
        "stderr": res.stderr[-4000:],
        "exit_code": res.returncode,
    }


@app.get("/apps/detect")
def apps_detect() -> dict[str, Any]:
    import subprocess
    res = subprocess.run(
        ["bash", str(config.repo_root() / "scripts" / "apps" / "detect.sh"), "--json"],
        capture_output=True, text=True,
    )
    if res.returncode != 0:
        raise HTTPException(status_code=500, detail=res.stderr.strip())
    return json.loads(res.stdout)


class AppEditRequest(BaseModel):
    package: str
    category: str


@app.post("/apps/add")
def apps_add(req: AppEditRequest) -> dict[str, Any]:
    import subprocess
    res = subprocess.run(
        ["bash", str(config.repo_root() / "scripts" / "apps" / "add.sh"),
         req.package, "--category", req.category],
        capture_output=True, text=True,
    )
    audit_mod.log("apps.add", details={"package": req.package, "category": req.category, "rc": res.returncode})
    return {"ok": res.returncode == 0, "stdout": res.stdout, "stderr": res.stderr}


@app.post("/apps/remove")
def apps_remove(req: AppEditRequest) -> dict[str, Any]:
    import subprocess
    res = subprocess.run(
        ["bash", str(config.repo_root() / "scripts" / "apps" / "remove.sh"),
         req.package, "--category", req.category],
        capture_output=True, text=True,
    )
    audit_mod.log("apps.remove", details={"package": req.package, "category": req.category, "rc": res.returncode})
    return {"ok": res.returncode == 0, "stdout": res.stdout, "stderr": res.stderr}


@app.get("/i18n/{lang}")
def i18n_catalog(lang: str) -> dict[str, Any]:
    """Read i18n/<lang>.txt and return as flat key→value JSON. Used by the
    dashboard to load CLI-side translations for the wizard's language pick."""
    if lang not in {"en", "pl"}:
        raise HTTPException(status_code=404, detail="unsupported lang")
    p = config.repo_root() / "i18n" / f"{lang}.txt"
    if not p.exists():
        return {"lang": lang, "strings": {}}
    out: dict[str, str] = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return {"lang": lang, "strings": out}


@app.get("/metrics", response_class=FileResponse)
def prometheus_metrics() -> Any:
    """Prometheus text-format metrics for fleet monitoring."""
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(metrics_mod.render(), media_type="text/plain; version=0.0.4")


@app.get("/audit")
def audit_tail(limit: int = 200) -> dict[str, Any]:
    return {"records": audit_mod.tail(limit=limit)}


@app.get("/auth/status")
def auth_status() -> dict[str, Any]:
    return {"token_configured": auth_mod.read_token() is not None,
            "token_path": str(auth_mod.token_path())}


@app.post("/auth/generate-token")
def auth_generate_token() -> dict[str, Any]:
    token = auth_mod.generate_and_store_token()
    audit_mod.log("auth.token.generate", details={"path": str(auth_mod.token_path())})
    return {"token": token, "path": str(auth_mod.token_path())}


@app.post("/auth/revoke-token")
def auth_revoke_token() -> dict[str, Any]:
    ok = auth_mod.revoke_token()
    audit_mod.log("auth.token.revoke", details={"ok": ok})
    return {"ok": ok}


@app.get("/runs/{run_id}/report.md", response_class=FileResponse)
def run_report_md(run_id: str) -> Any:
    from fastapi.responses import PlainTextResponse
    md = report_mod.render_run_id(run_id, config.runs_dir())
    if md is None:
        raise HTTPException(status_code=404, detail="run not found")
    return PlainTextResponse(md, media_type="text/markdown")


@app.get("/runs/diff")
def runs_diff(a: str, b: str) -> dict[str, Any]:
    runs_dir = config.runs_dir()
    pa = runs_dir / a
    pb = runs_dir / b
    if not pa.exists() or not pb.exists():
        raise HTTPException(status_code=404, detail="run(s) not found")
    return diff_mod.diff_runs(pa, pb)


# ── Onboarding state ─────────────────────────────────────────────────────────
@app.get("/onboarding/state")
def onboarding_state() -> dict[str, Any]:
    import os
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    p = Path(base) / "ascendo" / "onboarded.json"
    return {"onboarded": p.exists(), "path": str(p)}


@app.post("/onboarding/complete")
def onboarding_complete(payload: dict[str, Any]) -> dict[str, Any]:
    import os, json as _json, datetime as _dt
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    p = Path(base) / "ascendo" / "onboarded.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "completed_at": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "choices": payload,
    }
    p.write_text(_json.dumps(record, indent=2), encoding="utf-8")
    audit_mod.log("onboarding.complete", details=payload)
    return {"ok": True, "path": str(p)}


# ── Open URL in default browser ──────────────────────────────────────────────
@app.post("/open-url")
def open_url(payload: dict[str, Any]) -> dict[str, Any]:
    import webbrowser
    url = payload.get("url", "")
    if not url:
        raise HTTPException(status_code=400, detail="url required")
    # Resolve relative paths (like "/runs/<id>/report")
    if url.startswith("/"):
        host = os.environ.get("UA_DASHBOARD_HOST", "127.0.0.1")
        port = os.environ.get("UA_DASHBOARD_PORT", "8765")
        url = f"http://{host}:{port}{url}"
    webbrowser.open(url)
    return {"ok": True}


# ── Snapshot restore ─────────────────────────────────────────────────────────
@app.get("/snapshots")
def snapshots_list() -> dict[str, Any]:
    import subprocess
    res = subprocess.run(
        ["bash", str(config.repo_root() / "scripts" / "snapshot" / "list.sh")],
        capture_output=True, text=True,
    )
    return {"ok": res.returncode == 0, "raw": res.stdout, "stderr": res.stderr.strip()}


@app.post("/snapshots/restore")
def snapshots_restore(payload: dict[str, Any]) -> dict[str, Any]:
    import os, subprocess
    snap_id = (payload or {}).get("id")
    confirm = (payload or {}).get("confirm")
    if not snap_id or confirm != "RESTORE":
        raise HTTPException(status_code=400, detail={
            "code": "CONFIRM-REQUIRED",
            "msg": "send {\"id\":\"<snapshot>\",\"confirm\":\"RESTORE\"}",
        })
    st = sudo_mod.status()
    if not st.cached:
        raise HTTPException(status_code=401, detail={"code": "SUDO-REQUIRED",
            "msg": "POST /sudo/auth first"})
    helper = sudo_mod.make_askpass()
    env = os.environ.copy()
    env["SUDO_ASKPASS"] = str(helper)
    cmd = ["bash", str(config.repo_root() / "scripts" / "snapshot" / "restore.sh"), snap_id]
    res = subprocess.run(cmd, env=env, capture_output=True, text=True)
    audit_mod.log("snapshot.restore", details={"id": snap_id, "rc": res.returncode})
    return {"ok": res.returncode == 0, "stdout": res.stdout[-2000:],
            "stderr": res.stderr[-2000:], "exit_code": res.returncode}


# ── Notification settings (test send) ────────────────────────────────────────
@app.post("/notify/test")
def notify_test(payload: dict[str, Any]) -> dict[str, Any]:
    import subprocess
    cmd = [
        "bash", str(config.repo_root() / "scripts" / "notify.sh"),
        "--title", payload.get("title", "Ascendo test"),
        "--time",  payload.get("time",  "0m 0s"),
    ]
    if payload.get("reboot"):
        cmd.append("--reboot")
    res = subprocess.run(cmd, capture_output=True, text=True)
    audit_mod.log("notify.test", details={"rc": res.returncode})
    return {"ok": res.returncode == 0, "stdout": res.stdout, "stderr": res.stderr}


@app.post("/system/reboot")
def system_reboot(delay: int = 5) -> dict[str, Any]:
    """Schedule a reboot in `delay` seconds. Requires sudo cache (POST
    /sudo/auth first). Always returns 200 with the action taken — the actual
    reboot kills the dashboard mid-flight, so the client should not wait
    for the reboot to complete."""
    import subprocess, os
    st = sudo_mod.status()
    if not st.cached:
        raise HTTPException(
            status_code=401,
            detail={"code": "SUDO-REQUIRED", "msg": "POST /sudo/auth first"},
        )
    delay = max(0, min(int(delay), 300))
    helper = sudo_mod.make_askpass()
    env = os.environ.copy()
    env["SUDO_ASKPASS"] = str(helper)
    # `shutdown -r +0` reboots immediately; we use `at`-style scheduling via
    # `sleep && shutdown` so the HTTP response can fly out before init kills us.
    cmd = ["bash", "-c", f"(sleep {delay} && sudo -A /sbin/shutdown -r now 'ascendo dashboard reboot') >/dev/null 2>&1 &"]
    subprocess.Popen(cmd, env=env, start_new_session=True)
    return {"ok": True, "scheduled_in_seconds": delay}


@app.post("/system/cancel-reboot")
def system_cancel_reboot() -> dict[str, Any]:
    import subprocess, os
    helper = sudo_mod.make_askpass() if sudo_mod.have_password() else None
    env = os.environ.copy()
    if helper:
        env["SUDO_ASKPASS"] = str(helper)
    res = subprocess.run(["sudo", "-A", "/sbin/shutdown", "-c"], env=env,
                         capture_output=True, text=True)
    return {"ok": res.returncode == 0, "stderr": res.stderr.strip()}


# ── Smart suggestions ────────────────────────────────────────────────────────
@app.get("/suggestions")
def suggestions_list() -> dict[str, Any]:
    return {"items": sugg_mod.list_all()}


class SuggestionApplyRequest(BaseModel):
    id: str
    diff: list[dict[str, Any]] = []


@app.post("/suggestions/apply")
def suggestions_apply(req: SuggestionApplyRequest) -> dict[str, Any]:
    res = sugg_mod.apply_diff(req.diff)
    audit_mod.log("suggestion.apply", details={"id": req.id, "changes": res.get("changes")})
    return res


@app.post("/suggestions/dismiss")
def suggestions_dismiss(payload: dict[str, Any]) -> dict[str, Any]:
    sid = (payload or {}).get("id")
    if not sid:
        raise HTTPException(status_code=400, detail="id required")
    sugg_mod.dismiss(sid)
    audit_mod.log("suggestion.dismiss", details={"id": sid})
    return {"ok": True}


# ── Post-run health ──────────────────────────────────────────────────────────
@app.get("/health/check")
def health_check(run_id: str | None = None) -> dict[str, Any]:
    h = health_mod.latest_health(run_id)
    if h is None:
        return {"available": False}
    return {"available": True, **h}


@app.post("/health/run")
def health_run() -> dict[str, Any]:
    return health_mod.run_now()


# ── Settings backup/restore ──────────────────────────────────────────────────
@app.get("/backup/export")
def backup_export() -> Any:
    """Stream a tar.gz of the user's config (lists, exclusions, settings)."""
    from fastapi.responses import Response
    blob = backup_mod.export_bundle()
    fname = f"ascendo-backup-{int(__import__('time').time())}.tar.gz"
    audit_mod.log("backup.export", details={"bytes": len(blob)})
    return Response(content=blob, media_type="application/gzip",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@app.post("/backup/import")
async def backup_import(request: Request) -> dict[str, Any]:
    """Accept a raw tar.gz body and restore it."""
    blob = await request.body()
    if not blob:
        raise HTTPException(status_code=400, detail="empty upload")
    res = backup_mod.import_bundle(blob)
    audit_mod.log("backup.import", details={"restored": len(res.get("restored", []))})
    return res


# ── Telemetry: ETA from history ──────────────────────────────────────────────
@app.get("/telemetry/eta")
def telemetry_eta() -> dict[str, Any]:
    return {"profiles": telem_mod.averages_by_profile()}


# ── Exclusions ───────────────────────────────────────────────────────────────
@app.get("/exclusions")
def exclusions_list() -> dict[str, Any]:
    return excl_mod.load()


class ExclusionEdit(BaseModel):
    category: str
    package: str


@app.post("/exclusions/add")
def exclusions_add(req: ExclusionEdit) -> dict[str, Any]:
    res = excl_mod.add(req.category, req.package)
    audit_mod.log("exclusion.add", details={"category": req.category, "package": req.package})
    return res


@app.post("/exclusions/remove")
def exclusions_remove(req: ExclusionEdit) -> dict[str, Any]:
    res = excl_mod.remove(req.category, req.package)
    audit_mod.log("exclusion.remove", details={"category": req.category, "package": req.package})
    return res


# ── Hosts edit (write to config/hosts.toml) ──────────────────────────────────
@app.get("/hosts/list")
def hosts_list_full() -> dict[str, Any]:
    return hosts_edit_mod.list_hosts()


class HostUpsert(BaseModel):
    id: str
    display_name: str = ""
    ssh_alias: str = ""
    repo_path: str = ""
    description: str = ""
    orig_id: str | None = None


@app.post("/hosts/upsert")
def hosts_upsert(req: HostUpsert) -> dict[str, Any]:
    try:
        res = hosts_edit_mod.upsert_host(
            req.id, {
                "display_name": req.display_name,
                "ssh_alias": req.ssh_alias,
                "repo_path": req.repo_path,
                "description": req.description,
            }, orig_id=req.orig_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    audit_mod.log("hosts.upsert", details={"id": req.id})
    return res


@app.post("/hosts/delete")
def hosts_delete(payload: dict[str, Any]) -> dict[str, Any]:
    hid = (payload or {}).get("id")
    if not hid:
        raise HTTPException(status_code=400, detail="id required")
    res = hosts_edit_mod.delete_host(hid)
    audit_mod.log("hosts.delete", details={"id": hid})
    return res


# ── AI provider test connection ──────────────────────────────────────────────
@app.post("/suggestions/test")
def suggestions_test() -> dict[str, Any]:
    return sugg_mod.test_provider()


# ── About: version + system + release notes ────────────────────────────────
@app.get("/about")
def about() -> dict[str, Any]:
    import os, platform, subprocess
    repo = config.repo_root()
    rn = repo / "RELEASE_NOTES.md"
    rn_text = rn.read_text(encoding="utf-8") if rn.exists() else ""
    # Version from RELEASE_NOTES top heading or from packaging/deb/DEBIAN/control
    ver = ""
    if rn_text:
        for L in rn_text.splitlines():
            if L.startswith("## v"):
                ver = L.split()[1]; break
    if not ver:
        ctrl = repo / "packaging" / "deb" / "DEBIAN" / "control"
        if ctrl.exists():
            for L in ctrl.read_text(encoding="utf-8").splitlines():
                if L.startswith("Version:"):
                    ver = L.split(":", 1)[1].strip(); break
    git_head = ""
    try:
        git_head = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "--short", "HEAD"], text=True).strip()
    except Exception:
        pass
    distro = ""
    try:
        if Path("/etc/os-release").exists():
            for L in Path("/etc/os-release").read_text().splitlines():
                if L.startswith("PRETTY_NAME="):
                    distro = L.split("=", 1)[1].strip().strip('"')
                    break
    except Exception:
        pass
    return {
        "name": "Ascendo",
        "tagline": "unified updates",
        "version": ver or "0.0.0-dev",
        "git_head": git_head,
        "host": platform.node(),
        "kernel": platform.release(),
        "arch": platform.machine(),
        "python": platform.python_version(),
        "distro": distro,
        "release_notes_md": rn_text,
    }


# ── Sync provider config ─────────────────────────────────────────────────────
@app.get("/sync/provider")
def sync_provider_get() -> dict[str, Any]:
    s = settings_mod.load() or {}
    return s.get("sync") or {}


class SyncProviderRequest(BaseModel):
    provider: str = ""
    remote_name: str = ""
    remote_path: str = ""
    copy_only: bool = True


@app.post("/sync/provider")
def sync_provider_set(req: SyncProviderRequest) -> dict[str, Any]:
    s = settings_mod.load() or {}
    s["sync"] = {
        "provider": req.provider,
        "remote_name": req.remote_name,
        "remote_path": req.remote_path,
        "copy_only": bool(req.copy_only),
    }
    settings_mod.save(s)
    audit_mod.log("sync.provider.set", details=s["sync"])
    return s["sync"]


@app.get("/sync/remotes")
def sync_remotes() -> dict[str, Any]:
    """List rclone-configured remotes (one per line, name + colon)."""
    import subprocess
    try:
        res = subprocess.run(["rclone", "listremotes"], capture_output=True,
                             text=True, timeout=5)
        names = [L.rstrip(":").strip() for L in res.stdout.splitlines() if L.strip()]
        return {"ok": res.returncode == 0, "remotes": names}
    except FileNotFoundError:
        return {"ok": False, "remotes": [], "error": "rclone not installed"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "remotes": [], "error": "rclone listremotes timed out"}


@app.get("/sync/browse")
def sync_browse(path: str) -> dict[str, Any]:
    """Browse a remote folder. `path` is an rclone-style remote path
    (e.g. 'proton:/' or 'proton:/Backups'). Returns directories only — we
    pick a folder, never a file."""
    import subprocess
    if not path or ":" not in path:
        raise HTTPException(status_code=400, detail="path must include rclone remote name (remote:path)")
    try:
        res = subprocess.run(["rclone", "lsf", "--dirs-only", path],
                             capture_output=True, text=True, timeout=12)
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="rclone not installed")
    except subprocess.TimeoutExpired:
        return {"ok": False, "path": path, "dirs": [], "error": "rclone lsf timed out"}
    if res.returncode != 0:
        return {"ok": False, "path": path, "dirs": [], "error": res.stderr.strip()[-500:]}
    dirs = [d.rstrip("/") for d in res.stdout.splitlines() if d.strip()]
    return {"ok": True, "path": path, "dirs": dirs}


@app.post("/sync/provider/test")
def sync_provider_test() -> dict[str, Any]:
    """Best-effort connectivity test against the configured rclone remote.
    Just runs `rclone lsd <remote_path>` with a 10s timeout and reports."""
    import subprocess
    s = settings_mod.load() or {}
    sp = s.get("sync") or {}
    remote = sp.get("remote_path", "")
    if not remote:
        raise HTTPException(status_code=400, detail="remote_path not configured")
    try:
        res = subprocess.run(["rclone", "lsd", remote], capture_output=True,
                             text=True, timeout=12)
        return {"ok": res.returncode == 0, "stdout": res.stdout[-2000:],
                "stderr": res.stderr[-2000:], "exit_code": res.returncode}
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="rclone not installed")
    except subprocess.TimeoutExpired:
        return {"ok": False, "stderr": "timed out (12s) — auth or network issue"}


# ── apt downgrade per-package rollback ──────────────────────────────────────
@app.post("/apt/downgrade")
def apt_downgrade(payload: dict[str, Any]) -> dict[str, Any]:
    """Force apt to install a specific older version of a package.
    Body: {"package": "firefox", "version": "131.0+build1-0ubuntu1"}.
    Requires sudo cache. Caller is expected to confirm — there is no
    safety net beyond apt's own dependency check."""
    import os, subprocess
    pkg = (payload or {}).get("package", "")
    ver = (payload or {}).get("version", "")
    if not pkg or not ver:
        raise HTTPException(status_code=400, detail="package and version required")
    if not pkg.replace("-", "").replace(".", "").replace("+", "").isalnum():
        raise HTTPException(status_code=400, detail="package name has unsafe characters")
    st = sudo_mod.status()
    if not st.cached:
        raise HTTPException(status_code=401, detail={"code":"SUDO-REQUIRED",
            "msg":"POST /sudo/auth first"})
    helper = sudo_mod.make_askpass()
    env = os.environ.copy()
    env["SUDO_ASKPASS"] = str(helper)
    cmd = ["sudo", "-A", "apt-get", "install", "-y",
           "--allow-downgrades",
           "-o", "Dpkg::Options::=--force-confdef",
           "-o", "Dpkg::Options::=--force-confold",
           f"{pkg}={ver}"]
    res = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=300)
    audit_mod.log("apt.downgrade", details={"pkg": pkg, "ver": ver, "rc": res.returncode})
    return {"ok": res.returncode == 0, "stdout": res.stdout[-2000:],
            "stderr": res.stderr[-2000:], "exit_code": res.returncode}


# ── Profile templates ────────────────────────────────────────────────────────
@app.get("/profiles/templates")
def profile_templates() -> dict[str, Any]:
    p = config.repo_root() / "config" / "profiles"
    items: list[dict[str, Any]] = []
    if p.exists():
        for f in sorted(p.glob("*.list")):
            head = ""
            for L in f.read_text(encoding="utf-8").splitlines()[:6]:
                if L.strip().startswith("#"):
                    head += L.lstrip("# ") + " "
            items.append({"name": f.stem, "summary": head.strip(),
                          "lines": sum(1 for L in f.read_text().splitlines()
                                       if L.strip() and not L.lstrip().startswith("#"))})
    return {"items": items}


@app.post("/profiles/import")
def profile_import_endpoint(payload: dict[str, Any]) -> dict[str, Any]:
    import subprocess
    name = (payload or {}).get("name", "")
    dry = bool((payload or {}).get("dry_run", True))
    if not name or not name.replace("-", "").replace("_", "").isalnum():
        raise HTTPException(status_code=400, detail="invalid profile name")
    cmd = ["bash", str(config.repo_root() / "scripts" / "apps" / "profile-import.sh"), name]
    if dry: cmd.append("--dry-run")
    res = subprocess.run(cmd, capture_output=True, text=True)
    audit_mod.log("profile.import", details={"name": name, "dry_run": dry, "rc": res.returncode})
    return {"ok": res.returncode == 0, "stdout": res.stdout[-3000:], "stderr": res.stderr[-1000:]}


# ── GitHub Releases auto-update notifier ─────────────────────────────────────
@app.get("/updates/check")
def updates_check() -> dict[str, Any]:
    """Compare current Ascendo version with latest GH release tag for the
    configured repo (settings.updates.check_repo). Read-only; UI shows a
    badge if newer is available. Times out fast so it never stalls."""
    import urllib.request, json as _json, subprocess
    s = settings_mod.load() or {}
    repo = ((s.get("updates") or {}).get("check_repo") or "").strip()
    if not repo:
        return {"enabled": False}
    try:
        cur = subprocess.check_output(
            ["git", "-C", str(config.repo_root()), "describe", "--tags", "--always"],
            text=True, timeout=3).strip()
    except Exception:
        cur = "unknown"
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    try:
        with urllib.request.urlopen(url, timeout=4) as r:
            j = _json.loads(r.read().decode("utf-8"))
        latest = j.get("tag_name", "")
        return {"enabled": True, "repo": repo, "current": cur, "latest": latest,
                "newer_available": bool(latest and latest != cur and latest.lstrip("v") > cur.lstrip("v")),
                "url": j.get("html_url", "")}
    except Exception as exc:
        return {"enabled": True, "repo": repo, "current": cur, "error": str(exc)[:200]}


# ── Static frontend ──────────────────────────────────────────────────────────
_FRONT = Path(__file__).resolve().parent.parent / "frontend"
if _FRONT.exists():
    app.mount("/", StaticFiles(directory=str(_FRONT), html=True), name="frontend")
