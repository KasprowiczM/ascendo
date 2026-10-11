#!/usr/bin/env python3
"""Glue between the bash web handlers and ``vendor_feeds`` (Spec A/D/E).

``vendor_feeds.py`` is a byte-identical copy of the upstream module and is
never edited. Everything Ascendo-specific lives here:

* Ascendo's arm64/x86 Sparkle item filter (``hardwareRequirements`` + url
  substrings) as a *separate wrapper* that removes non-matching ``<item>``s
  before the shared parser runs. ON for ``sparkle_check`` / the sparkle apply
  path, OFF for the vendor-feed lookup.
* The 7-field vendor line ``version|url|checksum_kind|checksum|artifact|host|policy``
  (``-`` for absent fields, policy LAST so it can never contaminate
  artifact/host).
* Three-way compare ``newer|equal|older|unknown`` for the web check/plan/apply
  phases.

CLI (used by ``lib/ascendo_web.sh``; all bounded, stdlib only)::

    web_feed.py sparkle --field version|url [--no-arch-filter] [--os-version V]   # XML on stdin
    web_feed.py vendor [--os-version V]   # body on stdin, cfg JSON in $ASCENDO_WEB_VENDOR_FEED_JSON
    web_feed.py vendor-cfg                # cfg JSON in $ASCENDO_WEB_CFG -> vendor_feed url + JSON
    web_feed.py cmp CANDIDATE INSTALLED

Exit codes: sparkle 0 always (empty output = no candidate); vendor 0 = line
printed, 1 = feed unreachable/unparseable, 2 = bad config; cmp 0.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import vendor_feeds  # noqa: E402  (sibling module, byte-identical upstream copy)

SPARKLE_NS = vendor_feeds.SPARKLE_NS
ET.register_namespace("sparkle", SPARKLE_NS)

_ARM_HW = ("arm64", "aarch64", "universal")
_X86_URL = ("x86_64", "amd64", "x64", "intel")
_ARM_URL = ("arm64", "aarch64", "universal")
_ARM_ONLY_URL = ("arm64", "aarch64", "apple-silicon")
_X86_OK_URL = ("x86_64", "x64", "amd64", "universal")


def host_is_arm(env: dict | None = None) -> bool:
    """True on Apple Silicon (also under a Rosetta-translated Python).

    ``ASCENDO_WEB_HOST_ARCH`` (arm64|x86_64) overrides detection so tests
    never depend on the machine they run on.
    """
    env = os.environ if env is None else env
    forced = (env.get("ASCENDO_WEB_HOST_ARCH") or "").strip().lower()
    if forced:
        return forced in ("arm64", "aarch64")
    if platform.machine().lower() in ("arm64", "aarch64"):
        return True
    if sys.platform == "darwin":
        try:
            import subprocess
            out = subprocess.run(["sysctl", "-n", "hw.optional.arm64"], capture_output=True,
                                 text=True, timeout=5).stdout.strip()
            return out == "1"
        except Exception:
            return False
    return False


def _item_matches_arch(item: ET.Element, is_arm: bool) -> bool:
    hw_el = item.find(f"{{{SPARKLE_NS}}}hardwareRequirements")
    hw = hw_el.text.strip().lower() if hw_el is not None and hw_el.text else ""
    enclosure = item.find("enclosure")
    url = (enclosure.attrib.get("url", "") if enclosure is not None else "").lower()
    if hw:
        if is_arm:
            return hw in _ARM_HW
        return hw not in ("arm64", "aarch64")
    if is_arm:
        return not (any(x in url for x in _X86_URL) and not any(x in url for x in _ARM_URL))
    return not (any(x in url for x in _ARM_ONLY_URL) and not any(x in url for x in _X86_OK_URL))


def filter_sparkle_arch(xml_text: str, is_arm: bool) -> str:
    """Drop appcast ``<item>``s built for the other architecture.

    Unparseable XML is returned unchanged so the shared parser decides (it
    returns None; there is deliberately no regex "guess a version" fallback).
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return xml_text
    parent = {child: p for p in root.iter() for child in p}
    for item in list(root.iter("item")):
        if not _item_matches_arch(item, is_arm):
            parent[item].remove(item)
    return ET.tostring(root, encoding="unicode")


def sparkle_candidate(
    xml_text: str,
    os_version: str | None = None,
    arch_filter: bool = True,
    is_arm: bool | None = None,
    channels: str = "-",
) -> dict | None:
    """Newest qualifying Sparkle item as ``{'version', 'url', ...}`` or None."""
    if not xml_text or not xml_text.strip():
        return None
    body = xml_text
    if arch_filter:
        body = filter_sparkle_arch(body, host_is_arm() if is_arm is None else is_arm)
    return vendor_feeds.evaluate_feed("sparkle", body, channels, os_version=os_version or None)


def _clean(value: object) -> str | None:
    """Field value for the '|' line, or None when it would corrupt the framing."""
    text = "-" if value in (None, "") else str(value)
    if "|" in text or "\n" in text or "\r" in text:
        return None
    return text


def vendor_lookup(vendor_feed: dict, body: str, os_version: str | None) -> str | None:
    """7-field vendor line for one ``[app.vendor_feed]`` table, or None.

    ``version|url|checksum_kind|checksum|artifact|host|policy``. artifact/host
    come from the configuration (what the installer will be allowed to
    fetch); policy is ``rollout_hold`` or ``eligible``.
    """
    res = vendor_feeds.evaluate_feed(
        str(vendor_feed.get("kind", "")), body, str(vendor_feed.get("arg", "-")),
        os_version=os_version or None,
    )
    if not res or not res.get("version"):
        return None
    policy = "rollout_hold" if res.get("rollout_hold") else "eligible"
    fields = [
        res.get("version"), res.get("url"), res.get("checksum_kind"), res.get("checksum"),
        vendor_feed.get("artifact"), vendor_feed.get("allowed_host"), policy,
    ]
    cleaned = [_clean(f) for f in fields]
    if any(f is None for f in cleaned):
        return None
    return "|".join(cleaned)  # type: ignore[arg-type]


def version_relation(candidate: str, installed: str) -> str:
    """``newer|equal|older|unknown`` for CANDIDATE relative to INSTALLED."""
    rel = vendor_feeds.version_compare(candidate or "", installed or "")
    return {None: "unknown", -1: "older", 0: "equal", 1: "newer"}[rel]


def _main_sparkle(args: argparse.Namespace) -> int:
    cand = sparkle_candidate(sys.stdin.read(), os_version=args.os_version,
                             arch_filter=not args.no_arch_filter)
    if cand:
        value = cand.get(args.field)
        if value:
            print(value)
    return 0


def _main_vendor_cfg(args: argparse.Namespace) -> int:
    """Print the ``vendor_feed`` url (line 1) and compact JSON (line 2) from $ASCENDO_WEB_CFG."""
    raw = os.environ.get("ASCENDO_WEB_CFG", "")
    try:
        cfg = json.loads(raw)
        if isinstance(cfg, str):
            cfg = json.loads(cfg)
    except Exception:
        return 1
    feed = cfg.get("vendor_feed") if isinstance(cfg, dict) else None
    if not isinstance(feed, dict) or not feed.get("url"):
        return 1
    print(feed["url"])
    print(json.dumps(feed, separators=(",", ":")))
    return 0


def _main_vendor(args: argparse.Namespace) -> int:
    raw = os.environ.get("ASCENDO_WEB_VENDOR_FEED_JSON", "")
    try:
        cfg = json.loads(raw)
        if isinstance(cfg, str):  # tolerate a JSON-encoded JSON string
            cfg = json.loads(cfg)
    except Exception:
        return 2
    if not isinstance(cfg, dict):
        return 2
    line = vendor_lookup(cfg, sys.stdin.read(), args.os_version)
    if line is None:
        return 1
    print(line)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("sparkle")
    sp.add_argument("--field", choices=("version", "url"), required=True)
    sp.add_argument("--no-arch-filter", action="store_true")
    sp.add_argument("--os-version", default=None)
    sp.set_defaults(func=_main_sparkle)

    vp = sub.add_parser("vendor")
    vp.add_argument("--os-version", default=None)
    vp.set_defaults(func=_main_vendor)

    vc = sub.add_parser("vendor-cfg")
    vc.set_defaults(func=_main_vendor_cfg)

    cp = sub.add_parser("cmp")
    cp.add_argument("candidate")
    cp.add_argument("installed")
    cp.set_defaults(func=lambda a: (print(version_relation(a.candidate, a.installed)), 0)[1])

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
