#!/usr/bin/env python3
"""Helper for lib/json.sh — read/write phase-result JSON sidecar buffer.

Subcommands:
  init       --bufdir DIR --kind KIND --category CAT --host HOST --started-at TS [--log-path PATH]
  add-item   --bufdir DIR --id ID --action ACT --result RESULT [--from FROM] [--to TO] [--duration-ms N] [--details STR]
  add-diag   --bufdir DIR --level LEVEL --code CODE --msg MSG
  add-advisory --bufdir DIR --msg MSG
  count      --bufdir DIR --bucket {ok,warn,err} [--n N]
  set-flag   --bufdir DIR --key needs_reboot --value 0|1
  finalize   --bufdir DIR --out PATH --exit-code N --ended-at TS

The buffer directory holds:
  meta.json     -- stable header set by `init`
  items.jsonl   -- one JSON object per line
  diags.jsonl   -- one JSON object per line
  counters.env  -- KEY=VAL lines for ok/warn/err/needs_reboot
  advisory.txt  -- one advisory line per row
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import re

SCHEMA_ID = "ubuntu-aktualizacje/v1"
VALID_KINDS = {"check", "plan", "apply", "verify", "cleanup"}
_BUILTIN_CATEGORIES = {
    "apt", "snap", "brew", "npm", "pip", "flatpak", "drivers", "inventory",
    "web",
}
_PLUGIN_CATEGORY_RE = re.compile(r"^plugin:[a-z0-9][a-z0-9_-]{0,40}$")
VALID_LEVELS = {"info", "warn", "error"}
VALID_RESULTS = {"ok", "warn", "skipped", "failed", "noop"}
COUNTER_BUCKETS = {"ok", "warn", "err"}


def _is_valid_category(name: str) -> bool:
    return name in _BUILTIN_CATEGORIES or bool(_PLUGIN_CATEGORY_RE.match(name))


def _bufdir(arg: str) -> Path:
    p = Path(arg)
    p.mkdir(parents=True, exist_ok=True)
    return p


def cmd_init(args: argparse.Namespace) -> int:
    if args.kind not in VALID_KINDS:
        print(f"invalid kind: {args.kind}", file=sys.stderr)
        return 2
    if not _is_valid_category(args.category):
        print(f"invalid category: {args.category}", file=sys.stderr)
        return 2
    bd = _bufdir(args.bufdir)
    meta = {
        "schema": SCHEMA_ID,
        "kind": args.kind,
        "category": args.category,
        "host": args.host,
        "started_at": args.started_at,
        "log_path": args.log_path or None,
    }
    (bd / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    (bd / "items.jsonl").write_text("", encoding="utf-8")
    (bd / "diags.jsonl").write_text("", encoding="utf-8")
    (bd / "advisory.txt").write_text("", encoding="utf-8")
    (bd / "counters.env").write_text("ok=0\nwarn=0\nerr=0\nneeds_reboot=0\n", encoding="utf-8")
    return 0


def _append_json_line(path: Path, obj: dict) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(obj, ensure_ascii=False) + "\n")


def cmd_add_item(args: argparse.Namespace) -> int:
    if args.result not in VALID_RESULTS:
        print(f"invalid result: {args.result}", file=sys.stderr)
        return 2
    bd = _bufdir(args.bufdir)
    item = {
        "id": args.id,
        "action": args.action,
        "result": args.result,
    }
    if args.from_ is not None:
        item["from"] = args.from_
    if args.to is not None:
        item["to"] = args.to
    if args.duration_ms is not None:
        item["duration_ms"] = int(args.duration_ms)
    if args.details is not None:
        item["details"] = args.details
    _append_json_line(bd / "items.jsonl", item)
    return 0


def cmd_add_diag(args: argparse.Namespace) -> int:
    if args.level not in VALID_LEVELS:
        print(f"invalid level: {args.level}", file=sys.stderr)
        return 2
    bd = _bufdir(args.bufdir)
    obj = {"level": args.level, "code": args.code, "msg": args.msg}
    _append_json_line(bd / "diags.jsonl", obj)
    return 0


def cmd_add_advisory(args: argparse.Namespace) -> int:
    bd = _bufdir(args.bufdir)
    with (bd / "advisory.txt").open("a", encoding="utf-8") as fh:
        fh.write(args.msg.replace("\n", " ") + "\n")
    return 0


def _read_counters(bd: Path) -> dict[str, int]:
    counters = {"ok": 0, "warn": 0, "err": 0, "needs_reboot": 0}
    path = bd / "counters.env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line:
                k, _, v = line.partition("=")
                try:
                    counters[k] = int(v)
                except ValueError:
                    pass
    return counters


def _write_counters(bd: Path, counters: dict[str, int]) -> None:
    (bd / "counters.env").write_text(
        "".join(f"{k}={v}\n" for k, v in counters.items()),
        encoding="utf-8",
    )


def cmd_count(args: argparse.Namespace) -> int:
    if args.bucket not in COUNTER_BUCKETS:
        print(f"invalid bucket: {args.bucket}", file=sys.stderr)
        return 2
    bd = _bufdir(args.bufdir)
    counters = _read_counters(bd)
    counters[args.bucket] = counters.get(args.bucket, 0) + int(args.n)
    _write_counters(bd, counters)
    return 0


def cmd_set_flag(args: argparse.Namespace) -> int:
    if args.key != "needs_reboot":
        print(f"unsupported flag: {args.key}", file=sys.stderr)
        return 2
    bd = _bufdir(args.bufdir)
    counters = _read_counters(bd)
    counters["needs_reboot"] = 1 if str(args.value) == "1" else 0
    _write_counters(bd, counters)
    return 0


def cmd_finalize(args: argparse.Namespace) -> int:
    bd = _bufdir(args.bufdir)
    meta_path = bd / "meta.json"
    if not meta_path.exists():
        print("buffer not initialized (missing meta.json)", file=sys.stderr)
        return 2
    meta = json.loads(meta_path.read_text(encoding="utf-8"))

    items: list[dict] = []
    items_path = bd / "items.jsonl"
    if items_path.exists():
        for line in items_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                items.append(json.loads(line))

    diags: list[dict] = []
    diags_path = bd / "diags.jsonl"
    if diags_path.exists():
        for line in diags_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                diags.append(json.loads(line))

    advisory: list[str] = []
    adv_path = bd / "advisory.txt"
    if adv_path.exists():
        advisory = [
            line for line in adv_path.read_text(encoding="utf-8").splitlines() if line
        ]

    counters = _read_counters(bd)

    out_obj = {
        "schema": meta.get("schema", SCHEMA_ID),
        "kind": meta["kind"],
        "category": meta["category"],
        "host": meta["host"],
        "started_at": meta["started_at"],
        "ended_at": args.ended_at,
        "exit_code": int(args.exit_code),
        "summary": {
            "ok": counters.get("ok", 0),
            "warn": counters.get("warn", 0),
            "err": counters.get("err", 0),
        },
        "items": items,
        "diagnostics": diags,
        "log_path": meta.get("log_path") or None,
        "needs_reboot": bool(counters.get("needs_reboot", 0)),
    }
    if advisory:
        out_obj["advisory"] = advisory

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(out_path.suffix + ".partial")
    tmp.write_text(json.dumps(out_obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, out_path)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="_json_emit.py")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init")
    s.add_argument("--bufdir", required=True)
    s.add_argument("--kind", required=True)
    s.add_argument("--category", required=True)
    s.add_argument("--host", required=True)
    s.add_argument("--started-at", dest="started_at", required=True)
    s.add_argument("--log-path", dest="log_path", default=None)
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("add-item")
    s.add_argument("--bufdir", required=True)
    s.add_argument("--id", required=True)
    s.add_argument("--action", required=True)
    s.add_argument("--result", required=True)
    s.add_argument("--from", dest="from_", default=None)
    s.add_argument("--to", dest="to", default=None)
    s.add_argument("--duration-ms", dest="duration_ms", default=None)
    s.add_argument("--details", default=None)
    s.set_defaults(func=cmd_add_item)

    s = sub.add_parser("add-diag")
    s.add_argument("--bufdir", required=True)
    s.add_argument("--level", required=True)
    s.add_argument("--code", required=True)
    s.add_argument("--msg", required=True)
    s.set_defaults(func=cmd_add_diag)

    s = sub.add_parser("add-advisory")
    s.add_argument("--bufdir", required=True)
    s.add_argument("--msg", required=True)
    s.set_defaults(func=cmd_add_advisory)

    s = sub.add_parser("count")
    s.add_argument("--bufdir", required=True)
    s.add_argument("--bucket", required=True)
    s.add_argument("--n", default=1)
    s.set_defaults(func=cmd_count)

    s = sub.add_parser("set-flag")
    s.add_argument("--bufdir", required=True)
    s.add_argument("--key", required=True)
    s.add_argument("--value", required=True)
    s.set_defaults(func=cmd_set_flag)

    s = sub.add_parser("finalize")
    s.add_argument("--bufdir", required=True)
    s.add_argument("--out", required=True)
    s.add_argument("--exit-code", dest="exit_code", required=True)
    s.add_argument("--ended-at", dest="ended_at", required=True)
    s.set_defaults(func=cmd_finalize)

    return p


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
