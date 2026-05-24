"""Multi-host management — read-only SSH wrappers.

config/hosts.toml lists remote machines. Each query uses `ssh -o
BatchMode=yes` so missing keys/agents fail fast rather than prompting.
Only safe, read-only commands are executed remotely:

  • hostname / lsb_release / uname     — for preflight summary
  • cat <repo>/logs/runs/<latest>/run.json   — last run status

Mutating runs are NOT supported here by design — too risky from a single
dashboard. Use SSH directly or set up a per-host scheduler instead.
"""
from __future__ import annotations

import json
import shlex
import subprocess
import tomllib
from dataclasses import dataclass, asdict
from pathlib import Path

from . import config


@dataclass
class HostDef:
    id: str
    display_name: str
    ssh_alias: str
    repo_path: str
    description: str = ""


def hosts_file() -> Path:
    return config.repo_root() / "config" / "hosts.toml"


def load_hosts() -> dict[str, HostDef]:
    p = hosts_file()
    if not p.exists():
        return {}
    raw = tomllib.loads(p.read_text(encoding="utf-8"))
    out: dict[str, HostDef] = {}
    for hid, body in raw.items():
        out[hid] = HostDef(
            id=hid,
            display_name=body.get("display_name", hid),
            ssh_alias=body.get("ssh_alias", hid),
            repo_path=body.get("repo_path", "~/Dev_Env/Ascendo"),
            description=body.get("description", ""),
        )
    return out


def _ssh_exec(alias: str, remote_cmd: str, *, timeout: int = 10) -> tuple[int, str, str]:
    cmd = ["ssh",
           "-o", "BatchMode=yes",
           "-o", "ConnectTimeout=5",
           "-o", "StrictHostKeyChecking=accept-new",
           alias, remote_cmd]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return res.returncode, res.stdout, res.stderr
    except subprocess.TimeoutExpired:
        return 124, "", f"ssh {alias}: timeout"
    except FileNotFoundError:
        return 127, "", "ssh not installed"


def preflight(host: HostDef) -> dict:
    """Best-effort remote read-only health check."""
    repo = host.repo_path.replace("'", "'\\''")
    cmd = (
        f"set -e; "
        f"hostname; "
        f"lsb_release -ds 2>/dev/null || cat /etc/os-release | grep PRETTY_NAME | cut -d= -f2-; "
        f"uname -r; "
        f"echo '---REPO---'; "
        f"if [ -d '{repo}' ]; then "
        f"  cd '{repo}'; "
        f"  git rev-parse --short HEAD 2>/dev/null || echo no-git; "
        f"  if ls -t logs/runs/*/run.json 2>/dev/null | head -1 | xargs cat 2>/dev/null; then :; else echo '{{}}'; fi; "
        f"else echo missing; fi"
    )
    rc, out, err = _ssh_exec(host.ssh_alias, cmd)
    info: dict = {
        "host_id": host.id,
        "ssh_alias": host.ssh_alias,
        "ok": rc == 0,
        "exit_code": rc,
        "error": err.strip() if rc != 0 else "",
    }
    if rc == 0:
        parts = out.strip().split("---REPO---", 1)
        head = parts[0].strip().splitlines()
        repo_part = parts[1].strip().splitlines() if len(parts) > 1 else []
        info["hostname"] = head[0] if len(head) > 0 else ""
        info["os"]       = head[1] if len(head) > 1 else ""
        info["kernel"]   = head[2] if len(head) > 2 else ""
        if repo_part and repo_part[0] == "missing":
            info["repo_present"] = False
        else:
            info["repo_present"] = True
            info["git_head"] = repo_part[0] if repo_part else ""
            tail = "\n".join(repo_part[1:])
            try:
                info["last_run"] = json.loads(tail) if tail.strip() else None
            except Exception:
                info["last_run"] = None
    return info
