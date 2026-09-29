#!/usr/bin/env python3
"""su_classify.py -- parse ``softwareupdate -l`` and classify what it offers.

Port of macOS_updates ``lib/python/system_updates.py`` (parse + classify), plus
a tiny CLI for the bash phase scripts::

    softwareupdate -l --include-config-data | su_classify.py --current 26.6.2

Output, one line per pending update, fields joined by the ASCII unit separator
(0x1f) -- NOT a tab: bash ``read`` collapses runs of whitespace IFS characters,
which would shift the columns whenever a field (e.g. Version) is empty::

    KIND <US> label <US> title <US> version <US> restart(0/1) <US> recommended(0/1)

KIND is ``SAME`` (an update for the installed macOS major release), ``OTHER``
(a standalone payload: Safari, XProtect, Command Line Tools, ...) or ``MAJOR``
(an upgrade to a newer macOS major release; Ascendo never installs these
automatically).

Fail-safe deviations from the original: when the installed macOS version cannot
be parsed, or an OS-type update's own major cannot be determined, that update is
reported ``MAJOR`` (blocked) instead of ``same_major``, so neither a failed
``sw_vers`` nor an odd listing can turn a major upgrade into an "ordinary"
update.

The caller must run ``softwareupdate`` with ``LANG=C LC_ALL=C``: the regexes
below match the English field names.
"""
from __future__ import annotations

import argparse
import re
import sys
from typing import Any

US = "\x1f"


def parse_softwareupdate_list(text: str) -> list[dict[str, Any]]:
    """Parse output of `softwareupdate -l` into a list of update dictionaries.

    Returns:
        list of dict with keys:
            label (str): Update identifier passed to `softwareupdate -i`
            title (str): Human-readable update name
            version (str): Update version string
            size_kib (int): Download size in KiB
            recommended (bool): Whether update is marked Recommended: YES
            restart (bool): Whether update requires a reboot
    """
    items: list[dict[str, Any]] = []
    if not text or "No new software available" in text:
        return items

    lines = text.splitlines()
    current_label: str | None = None
    current_block: list[str] = []

    def flush_item(label: str, block_lines: list[str]) -> None:
        block_text = "\n".join(block_lines)
        title = label
        version = ""
        size_kib = 0
        recommended = False
        restart = False

        m_title = re.search(r"Title:\s*([^,\n]+)", block_text)
        if m_title:
            title = m_title.group(1).strip()

        m_ver = re.search(r"Version:\s*([^,\n]+)", block_text)
        if m_ver:
            version = m_ver.group(1).strip()

        m_size = re.search(r"Size:\s*(\d+)\s*K(?:i)?B", block_text, re.IGNORECASE)
        if m_size:
            size_kib = int(m_size.group(1))

        m_rec = re.search(r"Recommended:\s*([^,\n]+)", block_text, re.IGNORECASE)
        if m_rec and m_rec.group(1).strip().upper() == "YES":
            recommended = True

        m_act = re.search(r"Action:\s*([^,\n]+)", block_text, re.IGNORECASE)
        if (m_act and "restart" in m_act.group(1).lower()) or re.search(
            r"\brestart\b", block_text, re.IGNORECASE
        ):
            restart = True

        items.append({
            "label": label,
            "title": title,
            "version": version,
            "size_kib": size_kib,
            "recommended": recommended,
            "restart": restart,
        })

    for line in lines:
        m_label = re.match(r"^\*\s*(?:Label:\s*)?(\S.*)$", line)
        if m_label:
            if current_label is not None:
                flush_item(current_label, current_block)
            current_label = m_label.group(1).strip()
            current_block = []
        elif current_label is not None:
            current_block.append(line)

    if current_label is not None:
        flush_item(current_label, current_block)

    return items


_OS_RE = re.compile(r"^(?:macOS|OS X)\b", re.IGNORECASE)
# "macOS 27", "macOS Tahoe 26.7", "macOS Sequoia 15.1" -> the number after an
# optional single release-name word.
_TITLE_MAJOR_RE = re.compile(r"macOS\s+(?:[A-Za-z]+\s+)?(\d+)", re.IGNORECASE)


def classify_updates(
    items: list[dict[str, Any]], current_version: str
) -> dict[str, list[dict[str, Any]]]:
    """Classify updates into same_major, major, and other.

    Args:
        items: update dicts from parse_softwareupdate_list
        current_version: installed macOS version ("26.6.2", "27.0", ...)
    """
    classified: dict[str, list[dict[str, Any]]] = {
        "same_major": [],
        "major": [],
        "other": [],
    }

    try:
        cur_major = int(str(current_version).split(".")[0])
    except (ValueError, IndexError):
        cur_major = 0

    for it in items:
        title = it.get("title", "")
        label = it.get("label", "")
        version = it.get("version", "").strip()

        is_os = bool(_OS_RE.match(title) or _OS_RE.match(label))
        if not is_os:
            classified["other"].append(it)
            continue

        if cur_major <= 0:
            # Fail safe: cannot compare, so never treat an OS update as routine.
            classified["major"].append(it)
            continue

        item_major = None
        if version:
            v_part = version.split(".")[0].strip()
            if v_part.isdigit():
                item_major = int(v_part)
        if item_major is None:
            for text in (title, label):
                m = _TITLE_MAJOR_RE.search(text)
                if m:
                    item_major = int(m.group(1))
                    break

        if item_major is None or item_major > cur_major:
            # Fail safe: an OS update whose own major cannot be determined is
            # treated like an unknown installed version -- never routine.
            classified["major"].append(it)
        else:
            classified["same_major"].append(it)

    return classified


def _clean(value: str) -> str:
    """Records are US-separated and newline-terminated: strip both."""
    return value.replace(US, " ").replace("\n", " ").replace("\r", " ")


def render_records(items: list[dict[str, Any]], current_version: str) -> list[str]:
    """One record per update, ordered same_major, other, major (macOS_updates order)."""
    groups = classify_updates(items, current_version)
    out: list[str] = []
    for kind, key in (("SAME", "same_major"), ("OTHER", "other"), ("MAJOR", "major")):
        for it in groups[key]:
            out.append(US.join([
                kind,
                _clean(it["label"]),
                _clean(it["title"]),
                _clean(it["version"]),
                "1" if it["restart"] else "0",
                "1" if it["recommended"] else "0",
            ]))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--current", default="", help="installed macOS version (sw_vers -productVersion)")
    ap.add_argument("--file", default="", help="read the listing from FILE instead of stdin")
    args = ap.parse_args(argv)
    if args.file:
        with open(args.file, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    else:
        text = sys.stdin.read()
    for rec in render_records(parse_softwareupdate_list(text), args.current):
        sys.stdout.write(rec + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
