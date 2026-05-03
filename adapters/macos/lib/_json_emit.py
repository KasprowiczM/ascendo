#!/usr/bin/env python3
"""Helper for adapters/macos/lib/ascendo_json.sh — phase-result sidecar buffer.

Subcommands:
  init        --bufdir DIR --phase PHASE --category CAT --run-id ID
              --trigger TRIG --profile-name NAME --tool-name TOOL
              --tool-version VER --started-at ISO
              [--host-name STR] [--host-os STR] [--host-os-version STR]
              [--host-arch STR] [--host-user STR]
              [--host-is-elevated true|false]
              [--host-elevation-method STR] [--host-locale STR]
  add-item    --bufdir DIR --id ID --status STATUS
              [--name STR] [--category STR]
              [--current-version STR] [--target-version STR]
              [--resolved-version STR] [--source-type STR]
              [--source-feed STR] [--source-url STR]
              [--exit-code N] [--duration-ms N]
  add-message --bufdir DIR --level LEVEL --text STR [--timestamp STR]
  set-flag    --bufdir DIR --key needs_reboot --value true|false
  count       --bufdir DIR --bucket {success,skipped,failed} [--n N]
  finalize    --bufdir DIR --out PATH --exit-code N --ended-at ISO

Buffer layout (compatible with the legacy emitter):
  meta.json    -- header set by init
  items.jsonl  -- one JSON object per line
  msgs.jsonl   -- one JSON object per line
  counters.env -- KEY=VAL lines for success/skipped/failed/needs_reboot

Atomic finalize writes via tempfile + os.replace.

Output conforms to ascendo/v1 sidecar schema (parse_sidecar() compatible).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

SCHEMA_ID = "ascendo/v1"
VALID_PHASES = {"check", "plan", "apply", "verify", "cleanup"}
VALID_STATUSES = {"success", "skipped", "failed", "planned", "up_to_date", "partial"}
VALID_LEVELS = {"debug", "info", "warn", "error"}
COUNTER_BUCKETS = {"success", "skipped", "failed"}


def _bufdir(arg: str) -> Path:
    p = Path(arg)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def _read_counters(path: Path) -> dict[str, int | bool]:
    out: dict[str, int | bool] = {
        "success": 0, "skipped": 0, "failed": 0, "needs_reboot": False,
    }
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k in {"success", "skipped", "failed"}:
            out[k] = int(v)
        elif k == "needs_reboot":
            out[k] = v.lower() in {"true", "1", "yes"}
    return out


def _write_counters(path: Path, counters: dict[str, int | bool]) -> None:
    lines = [
        f"success={counters['success']}",
        f"skipped={counters['skipped']}",
        f"failed={counters['failed']}",
        f"needs_reboot={'true' if counters['needs_reboot'] else 'false'}",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def cmd_init(args: argparse.Namespace) -> int:
    if args.phase not in VALID_PHASES:
        print(f"init: invalid phase {args.phase!r}", file=sys.stderr)
        return 2
    bufdir = _bufdir(args.bufdir)

    host = {
        "hostname": args.host_name or "unknown",
        "os": args.host_os or "macos",
        "os_version": args.host_os_version or "unknown",
        "arch": args.host_arch or "unknown",
        "user": args.host_user or "unknown",
        "is_elevated": (args.host_is_elevated or "false").lower() == "true",
    }
    if args.host_elevation_method:
        host["elevation_method"] = args.host_elevation_method
    if args.host_locale:
        host["locale"] = args.host_locale

    meta = {
        "schema": SCHEMA_ID,
        "run": {
            "id": args.run_id,
            "trigger": args.trigger,
            "profile": args.profile_name,
            "dry_run": (args.dry_run or "false").lower() == "true",
            "started_at": args.started_at,
        },
        "host": host,
        "tool": {
            "name": args.tool_name,
            "version": args.tool_version,
        },
        "phase": args.phase,
        "category": args.category,
        "started_at": args.started_at,
    }
    (bufdir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    (bufdir / "items.jsonl").write_text("", encoding="utf-8")
    (bufdir / "msgs.jsonl").write_text("", encoding="utf-8")
    _write_counters(bufdir / "counters.env",
                    {"success": 0, "skipped": 0, "failed": 0, "needs_reboot": False})
    return 0


def cmd_add_item(args: argparse.Namespace) -> int:
    if args.status not in VALID_STATUSES:
        print(f"add-item: invalid status {args.status!r}", file=sys.stderr)
        return 2
    bufdir = _bufdir(args.bufdir)

    # category defaults to source_type if not specified
    category = args.category or args.source_type or "brew"

    item: dict = {
        "id": args.id,
        "name": args.name or args.id,
        "category": category,
        "source": {
            "type": args.source_type or category,
        },
        "status": args.status,
    }
    # source sub-fields
    if args.source_feed:  item["source"]["feed"] = args.source_feed
    if args.source_url:   item["source"]["url"]  = args.source_url

    if args.current_version:  item["current_version"]  = args.current_version
    if args.target_version:   item["target_version"]   = args.target_version
    if args.resolved_version: item["resolved_version"] = args.resolved_version
    if args.exit_code is not None:   item["exit_code"]   = args.exit_code
    if args.duration_ms is not None: item["duration_ms"] = args.duration_ms

    with (bufdir / "items.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(item, ensure_ascii=False) + "\n")

    # Auto-tally counters for success/skipped/failed (mirrors legacy)
    counters = _read_counters(bufdir / "counters.env")
    if args.status in COUNTER_BUCKETS:
        counters[args.status] += 1
        _write_counters(bufdir / "counters.env", counters)
    return 0


def cmd_add_message(args: argparse.Namespace) -> int:
    if args.level not in VALID_LEVELS:
        print(f"add-message: invalid level {args.level!r}", file=sys.stderr)
        return 2
    bufdir = _bufdir(args.bufdir)
    msg: dict = {"level": args.level, "text": args.text}
    if args.timestamp: msg["timestamp"] = args.timestamp
    with (bufdir / "msgs.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(msg, ensure_ascii=False) + "\n")
    return 0


def cmd_set_flag(args: argparse.Namespace) -> int:
    bufdir = _bufdir(args.bufdir)
    counters = _read_counters(bufdir / "counters.env")
    if args.key == "needs_reboot":
        counters["needs_reboot"] = args.value.lower() in {"true", "1", "yes"}
    else:
        print(f"set-flag: unknown key {args.key!r}", file=sys.stderr)
        return 2
    _write_counters(bufdir / "counters.env", counters)
    return 0


def cmd_count(args: argparse.Namespace) -> int:
    if args.bucket not in COUNTER_BUCKETS:
        print(f"count: invalid bucket {args.bucket!r}", file=sys.stderr)
        return 2
    bufdir = _bufdir(args.bufdir)
    counters = _read_counters(bufdir / "counters.env")
    counters[args.bucket] += args.n
    _write_counters(bufdir / "counters.env", counters)
    return 0


def cmd_finalize(args: argparse.Namespace) -> int:
    bufdir = _bufdir(args.bufdir)
    meta_path = bufdir / "meta.json"
    if not meta_path.exists():
        print("finalize: no meta.json -- call init first", file=sys.stderr)
        return 2
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    items = _read_jsonl(bufdir / "items.jsonl")
    msgs = _read_jsonl(bufdir / "msgs.jsonl")
    counters = _read_counters(bufdir / "counters.env")

    total = len(items)
    success = int(counters["success"])
    skipped = int(counters["skipped"])
    failed = int(counters["failed"])

    # Count items by status for the summary buckets
    up_to_date = sum(1 for it in items if it.get("status") == "up_to_date")
    planned = sum(1 for it in items if it.get("status") == "planned")
    partial = sum(1 for it in items if it.get("status") == "partial")

    # Status heuristic mirrors AscendoJson.psm1 Save-Sidecar:
    # - any failed -> "failed"
    # - all skipped -> "skipped"
    # - else -> "success"
    if failed > 0:
        status = "failed"
    elif total > 0 and skipped == total:
        status = "skipped"
    else:
        status = "success"

    sidecar = {
        **meta,
        "finished_at": args.ended_at,
        "status": status,
        "summary": {
            "total": total,
            "success": success,
            "up_to_date": up_to_date,
            "failed": failed,
            "skipped": skipped,
            "planned": planned,
            "partial": partial,
            "exit_code": args.exit_code,
        },
        "items": items,
        "messages": msgs,
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write: tmp file in same dir -> os.replace
    fd, tmp = tempfile.mkstemp(prefix=".sidecar_", dir=str(out_path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(sidecar, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, out_path)
    except Exception:
        try: os.unlink(tmp)
        except OSError: pass
        raise
    return 0


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="_json_emit", description="ascendo/v1 sidecar emitter helper")
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("init")
    pi.add_argument("--bufdir", required=True)
    pi.add_argument("--phase", required=True)
    pi.add_argument("--category", required=True)
    pi.add_argument("--run-id", required=True)
    pi.add_argument("--trigger", required=True)
    pi.add_argument("--profile-name", required=True)
    pi.add_argument("--tool-name", required=True)
    pi.add_argument("--tool-version", required=True)
    pi.add_argument("--started-at", required=True)
    pi.add_argument("--dry-run", default="false")
    pi.add_argument("--host-name")
    pi.add_argument("--host-os")
    pi.add_argument("--host-os-version")
    pi.add_argument("--host-arch")
    pi.add_argument("--host-user")
    pi.add_argument("--host-is-elevated")
    pi.add_argument("--host-elevation-method")
    pi.add_argument("--host-locale")
    pi.set_defaults(func=cmd_init)

    pa = sub.add_parser("add-item")
    pa.add_argument("--bufdir", required=True)
    pa.add_argument("--id", required=True)
    pa.add_argument("--status", required=True)
    pa.add_argument("--name")
    pa.add_argument("--category")
    pa.add_argument("--current-version")
    pa.add_argument("--target-version")
    pa.add_argument("--resolved-version")
    pa.add_argument("--source-type")
    pa.add_argument("--source-feed")
    pa.add_argument("--source-url")
    pa.add_argument("--exit-code", type=int)
    pa.add_argument("--duration-ms", type=int)
    pa.set_defaults(func=cmd_add_item)

    pm = sub.add_parser("add-message")
    pm.add_argument("--bufdir", required=True)
    pm.add_argument("--level", required=True)
    pm.add_argument("--text", required=True)
    pm.add_argument("--timestamp")
    pm.set_defaults(func=cmd_add_message)

    pf = sub.add_parser("set-flag")
    pf.add_argument("--bufdir", required=True)
    pf.add_argument("--key", required=True)
    pf.add_argument("--value", required=True)
    pf.set_defaults(func=cmd_set_flag)

    pc = sub.add_parser("count")
    pc.add_argument("--bufdir", required=True)
    pc.add_argument("--bucket", required=True)
    pc.add_argument("--n", type=int, default=1)
    pc.set_defaults(func=cmd_count)

    pe = sub.add_parser("finalize")
    pe.add_argument("--bufdir", required=True)
    pe.add_argument("--out", required=True)
    pe.add_argument("--exit-code", type=int, required=True)
    pe.add_argument("--ended-at", required=True)
    pe.set_defaults(func=cmd_finalize)

    return p


def main(argv: list[str] | None = None) -> int:
    p = _build_parser()
    args = p.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
