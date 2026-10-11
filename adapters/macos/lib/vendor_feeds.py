#!/usr/bin/env python3
"""lib/python/vendor_feeds.py — vendor feeds parsing, evaluation and version comparison (v1.5.0)."""

from __future__ import annotations

import datetime
import json
import re
import xml.etree.ElementTree as ET

SPARKLE_NS = "http://www.andymatuschak.org/xml-namespaces/sparkle"


def normalize_vendor_version(value: str) -> str:
    """Strip whitespace; drop leading 'v'/'V'; drop cask build suffix after the first ','
    ('2.7032.0,6c46…' -> '2.7032.0'); drop a trailing parenthesised build ('7.2.1 (88329)' -> '7.2.1');
    replace a trailing '.(stable|beta|dev|canary|preview)_<digits>' by '.<digits>'
    ('v0.2026.09.16.08.27.stable_02' -> '0.2026.09.16.08.27.02'). Empty/None -> ''."""
    if not value:
        return ""
    s = str(value).strip()
    if s.startswith(("v", "V")):
        s = s[1:]
    # Drop cask build suffix after the first ','
    s = s.split(",", 1)[0]
    # Drop trailing parenthesised build: '7.2.1 (88329)' -> '7.2.1'
    s = re.sub(r"\s*\([^)]*\)\s*$", "", s)
    # Replace trailing '.(stable|beta|dev|canary|preview)_<digits>' by '.<digits>'
    s = re.sub(r"\.(?:stable|beta|dev|canary|preview)_(\d+)$", r".\1", s)
    return s.strip()


def version_key(value: str) -> tuple | None:
    """Same algorithm as internet_version_relation() in lib/version.sh (12 numeric segments, prerelease rank
    dev<alpha<beta<rc<final, 4 suffix numbers), applied AFTER normalize_vendor_version. None if no digits."""
    norm = normalize_vendor_version(value)
    match = re.search(r"\d+(?:\.\d+)*", norm or "")
    if not match:
        return None
    numbers = [int(part) for part in match.group(0).split(".")]
    numbers = (numbers + [0] * 12)[:12]
    suffix = (norm[match.end():] or "").lower().split("+", 1)[0]
    prerelease = suffix.lstrip("-._")
    prerelease_rank = 4
    for marker, rank in (("dev", 0), ("alpha", 1), ("a", 1), ("beta", 2), ("b", 2), ("rc", 3)):
        if prerelease.startswith(marker):
            prerelease_rank = rank
            break
    suffix_numbers = [int(part) for part in re.findall(r"\d+", suffix)]
    suffix_numbers = (suffix_numbers + [0] * 4)[:4]
    return tuple(numbers + [prerelease_rank] + suffix_numbers)


def version_compare(a: str, b: str) -> int | None:
    """-1 if a<b, 0 if equal keys, 1 if a>b, None if either is unparseable."""
    ka = version_key(a)
    kb = version_key(b)
    if ka is None or kb is None:
        return None
    if ka < kb:
        return -1
    elif ka > kb:
        return 1
    return 0


def parse_sparkle_appcast(
    xml_text: str,
    os_version: str | None = None,
    channels: set[str] | None = None,
) -> dict | None:
    """Parse with xml.etree.ElementTree. For every <item>:
       channel = text of <sparkle:channel> (None if absent). Allowed channels: {None} ∪ (channels or {'stable'}).
       min_os  = <sparkle:minimumSystemVersion> text or enclosure attribute sparkle:minimumSystemVersion;
                 skip the item if os_version is given and version_compare(min_os, os_version) == 1.
       short   = <sparkle:shortVersionString> text or enclosure attr sparkle:shortVersionString.
       build   = <sparkle:version> text or enclosure attr sparkle:version.
       url     = enclosure attr url.
       version = short or build.
     Return the item with the MAXIMUM version_key (never the first one) as
       {'version': version, 'build': build, 'url': url, 'checksum_kind': None, 'checksum': None}.
     Return None on ParseError or when no item qualifies."""
    if not xml_text or not xml_text.strip():
        return None
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None

    allowed_channels = {None, "stable"}
    if channels:
        allowed_channels |= set(channels)

    best_item = None
    best_key = None

    for item in root.iter("item"):
        # Channel check
        channel_el = item.find(f"{{{SPARKLE_NS}}}channel")
        if channel_el is None:
            channel_el = item.find("channel")
        channel = channel_el.text.strip() if (channel_el is not None and channel_el.text) else None
        if channel not in allowed_channels:
            continue

        enclosure = item.find("enclosure")

        # Minimum OS check
        min_os_el = item.find(f"{{{SPARKLE_NS}}}minimumSystemVersion")
        if min_os_el is None:
            min_os_el = item.find("minimumSystemVersion")
        min_os = min_os_el.text.strip() if (min_os_el is not None and min_os_el.text) else None
        if not min_os and enclosure is not None:
            min_os = (
                enclosure.attrib.get(f"{{{SPARKLE_NS}}}minimumSystemVersion")
                or enclosure.attrib.get("sparkle:minimumSystemVersion")
            )
        if min_os:
            min_os = min_os.strip()
            if os_version and version_compare(min_os, os_version) == 1:
                # min_os > os_version: skip
                continue

        # Short version
        short_el = item.find(f"{{{SPARKLE_NS}}}shortVersionString")
        if short_el is None:
            short_el = item.find("shortVersionString")
        short = short_el.text.strip() if (short_el is not None and short_el.text) else None
        if not short and enclosure is not None:
            short = (
                enclosure.attrib.get(f"{{{SPARKLE_NS}}}shortVersionString")
                or enclosure.attrib.get("sparkle:shortVersionString")
            )
        if short:
            short = short.strip()

        # Build version
        build_el = item.find(f"{{{SPARKLE_NS}}}version")
        if build_el is None:
            build_el = item.find("version")
        build = build_el.text.strip() if (build_el is not None and build_el.text) else None
        if not build and enclosure is not None:
            build = (
                enclosure.attrib.get(f"{{{SPARKLE_NS}}}version")
                or enclosure.attrib.get("sparkle:version")
            )
        if build:
            build = build.strip()

        url = enclosure.attrib.get("url") if enclosure is not None else None
        version = short or build
        if not version:
            continue

        k = version_key(version)
        if k is None:
            continue

        if best_key is None or k > best_key:
            best_key = k
            best_item = {
                "version": version,
                "build": build,
                "url": url,
                "checksum_kind": None,
                "checksum": None,
            }

    return best_item


def select_claude_releases(obj: dict) -> dict | None:
    if not isinstance(obj, dict):
        return None
    curr = obj.get("currentRelease")
    if not curr:
        return None
    url = None
    for r in obj.get("releases", []):
        if r.get("version") == curr:
            url = r.get("updateTo", {}).get("url")
            break
    return {
        "version": curr,
        "url": url,
        "checksum_kind": None,
        "checksum": None,
    }


def select_cursor_update(obj: dict) -> dict | None:
    if not isinstance(obj, dict):
        return None
    name = obj.get("name")
    if not name:
        return None
    return {
        "version": name,
        "url": obj.get("url"),
        "checksum_kind": None,
        "checksum": None,
    }


def select_warp_channels(obj: dict) -> dict | None:
    if not isinstance(obj, dict):
        return None
    stable = obj.get("stable", {})
    raw_ver = stable.get("version")
    if not raw_ver:
        return None
    return {
        "version": normalize_vendor_version(raw_ver),
        "url": None,
        "checksum_kind": None,
        "checksum": None,
    }


def select_antigravity_ide(obj: dict) -> dict | None:
    if not isinstance(obj, dict):
        return None
    url = obj.get("url")
    if not url:
        return None
    m = re.search(r"/stable/(\d+(?:\.\d+)+)-", url)
    if not m:
        return None
    return {
        "version": m.group(1),
        "url": url,
        "checksum_kind": "sha256hex",
        "checksum": obj.get("sha256hash"),
    }


def select_tauri_latest(obj: dict, platform: str = "darwin-aarch64") -> dict | None:
    if not isinstance(obj, dict):
        return None
    ver = obj.get("version")
    if not ver:
        return None
    platforms = obj.get("platforms", {})
    url = platforms.get(platform, {}).get("url") if isinstance(platforms, dict) else None
    return {
        "version": ver,
        "url": url,
        "checksum_kind": None,
        "checksum": None,
    }


def select_proton_releases(obj: dict, os_version: str | None = None) -> dict | None:
    if not isinstance(obj, dict):
        return None
    releases = obj.get("Releases", [])
    if not isinstance(releases, list):
        return None

    best_rel = None
    best_key = None

    for r in releases:
        if not isinstance(r, dict) or r.get("CategoryName") != "Stable":
            continue
        minimum_os = r.get("MinimumOsVersion")
        if minimum_os and os_version and version_compare(str(minimum_os), os_version) == 1:
            continue
        v = r.get("Version")
        if not v:
            continue
        k = version_key(v)
        if k is None:
            continue
        if best_key is None or k > best_key:
            best_key = k
            best_rel = r

    if not best_rel:
        return None

    url = None
    checksum = None
    files = best_rel.get("File") or best_rel.get("Files") or []
    if isinstance(files, dict):
        files = [files]
    if isinstance(files, list):
        for f in files:
            if isinstance(f, dict) and (f.get("Identifier") == "Apple Disk Image" or
                                       str(f.get("Url", "")).split("?", 1)[0].endswith(".dmg")):
                url = f.get("Url")
                checksum = f.get("Sha512CheckSum")
                break

    return {
        "version": best_rel.get("Version"),
        # A published release is observable truth, but a staged release is not
        # permission to bypass the vendor's rollout with a direct installer.
        "rollout_hold": "RolloutProportion" in best_rel and best_rel["RolloutProportion"] != 1,
        "url": url,
        "checksum_kind": "sha512hex" if checksum else None,
        "checksum": checksum,
    }


def parse_electron_yml(text: str) -> dict | None:
    """no PyYAML: top-level 'version: X'; under 'files:' pairs '- url: U' + 'sha512: S';
    pick first url ending '.zip' (else first); checksum_kind='sha512b64'"""
    if not text:
        return None
    m_ver = re.search(r"(?m)^version:\s*[\"']?([^\"'\r\n]+)[\"']?", text)
    if not m_ver:
        return None
    version = m_ver.group(1).strip()

    # Look for files block
    files_match = re.search(r"(?ms)^files:\s*\n(.*?)(?:^\S|\Z)", text)
    files_text = files_match.group(1) if files_match else text

    # Parse individual file entries
    file_blocks = re.split(r"(?m)^\s*-\s*", files_text)
    files = []
    for fb in file_blocks:
        if not fb.strip():
            continue
        u_m = re.search(r"url:\s*[\"']?([^\"'\s\r\n]+)", fb)
        s_m = re.search(r"sha512:\s*[\"']?([^\"'\s\r\n]+)", fb)
        if u_m:
            files.append({
                "url": u_m.group(1),
                "sha512": s_m.group(1) if s_m else None,
            })

    if not files:
        # Fallback to single top-level url / path / sha512
        p_m = re.search(r"(?m)^path:\s*[\"']?([^\"'\s\r\n]+)", text)
        s_m = re.search(r"(?m)^sha512:\s*[\"']?([^\"'\s\r\n]+)", text)
        url = p_m.group(1) if p_m else None
        sha512 = s_m.group(1) if s_m else None
    else:
        # Pick first ending with .zip, else first
        chosen = next((f for f in files if f["url"].lower().endswith(".zip")), files[0])
        url = chosen["url"]
        sha512 = chosen.get("sha512")

    return {
        "version": version,
        "url": url,
        "checksum_kind": "sha512b64" if sha512 else None,
        "checksum": sha512,
    }


def parse_devolutions_productinfo(text: str, prefix: str) -> dict | None:
    """lines '<prefix>.Version=', '<prefix>.Url=', '<prefix>.hash=' ; checksum_kind='sha256hex'"""
    if not text or not prefix:
        return None
    ver_m = re.search(r"(?mi)^" + re.escape(prefix) + r"\.Version=([^\r\n]*)", text)
    url_m = re.search(r"(?mi)^" + re.escape(prefix) + r"\.Url=([^\r\n]*)", text)
    hash_m = re.search(r"(?mi)^" + re.escape(prefix) + r"\.hash=([^\r\n]*)", text)
    if not ver_m:
        return None
    return {
        "version": ver_m.group(1).strip(),
        "url": url_m.group(1).strip() if url_m else None,
        "checksum_kind": "sha256hex" if hash_m else None,
        "checksum": hash_m.group(1).strip() if hash_m else None,
    }


JSON_SELECTORS = {
    "claude_releases": select_claude_releases,
    "cursor_update": select_cursor_update,
    "warp_channels": select_warp_channels,
    "antigravity_ide": select_antigravity_ide,
    "tauri_latest": select_tauri_latest,
    "proton_releases": select_proton_releases,
}


def evaluate_feed(kind: str, body: str, arg: str, os_version: str | None = None) -> dict | None:
    """kind: 'sparkle' (arg = comma channels or '-'), 'json' (arg = selector name), 'yml' (arg '-'),
    'kv' (arg = productinfo prefix). Never raises: any exception -> None.
    Post-process: result['version'] = normalize_vendor_version(result['version']);
    result['url'] spaces -> '%20'."""
    try:
        if not body:
            return None
        res = None
        if kind == "sparkle":
            ch = None
            if arg and arg != "-":
                ch = {c.strip() for c in arg.split(",") if c.strip()}
            res = parse_sparkle_appcast(body, os_version=os_version, channels=ch)
        elif kind == "json":
            selector = JSON_SELECTORS.get(arg)
            if not selector:
                return None
            data = json.loads(body)
            res = selector(data, os_version=os_version) if arg == "proton_releases" else selector(data)
        elif kind == "yml":
            res = parse_electron_yml(body)
        elif kind == "kv":
            res = parse_devolutions_productinfo(body, arg)
        else:
            return None

        if not res or not res.get("version"):
            return None

        norm_ver = normalize_vendor_version(res["version"])
        if not norm_ver or version_key(norm_ver) is None:
            return None

        res["version"] = norm_ver
        if res.get("url"):
            res["url"] = res["url"].replace(" ", "%20")

        return res
    except Exception:
        return None


def omaha_last_status(log_text: str, appid: str) -> str | None:
    r"""Return the LAST updatecheck status for appid in Omaha RESPONSES found in a Chromium-updater log.
    Regex per line (no DOTALL): r'"appid":"' + re.escape(appid) + r'".{0,600}?"updatecheck":\{"status":"([a-z]+)"'
    Request lines contain "updatecheck":{} and must never match. Values: 'noupdate', 'ok', 'error-…' or None."""
    if not log_text or not appid:
        return None
    pattern = re.compile(
        r'"appid":"' + re.escape(appid) + r'".{0,600}?"updatecheck":\{"status":"([a-z]+)"'
    )
    last_status = None
    for line in log_text.splitlines():
        # Skip request lines which have "updatecheck":{}
        if '"updatecheck":{}' in line and '"updatecheck":{"status"' not in line:
            continue
        m = pattern.search(line)
        if m:
            last_status = m.group(1)
    return last_status


def omaha_recent_status(
    log_text: str,
    appid: str,
    max_age_h: float = 6.0,
    now: datetime.datetime | None = None,
) -> tuple[str, str] | None:
    """Find the latest response status for appid from Omaha log lines.
    Lines start with prefix like [pid:tid:MMDD/HHMMSS.usec:LEVEL:file:line] {"response":...}
    Returns (status, time_str) if found and within max_age_h, where time_str is 'HH:MM'.
    If the status is older than max_age_h or not found, returns None.
    """
    if not log_text or not appid:
        return None

    if now is None:
        now = datetime.datetime.now()

    prefix_re = re.compile(r'\[\d+:\d+:(\d{2})(\d{2})/(\d{2})(\d{2})(\d{2})')
    app_re = re.compile(
        r'"appid":"' + re.escape(appid) + r'".{0,600}?"updatecheck":\{"status":"([a-z]+)"'
    )

    last_match = None
    # GoogleUpdater 156+ logs "Omaha response received: )]}'" on a prefixed
    # line and the JSON body on the NEXT, unprefixed line. The body inherits
    # the timestamp of the closest preceding prefixed line.
    last_prefix = None
    for line in log_text.splitlines():
        m_line_pre = prefix_re.search(line)
        if m_line_pre and line.startswith("["):
            last_prefix = m_line_pre
        if '"updatecheck":{}' in line and '"updatecheck":{"status"' not in line:
            continue
        m_app = app_re.search(line)
        if not m_app:
            continue
        m_pre = prefix_re.search(line) or last_prefix
        if not m_pre:
            continue
        month, day, hour, minute, second = map(int, m_pre.groups())
        status = m_app.group(1)
        last_match = (month, day, hour, minute, second, status)

    if not last_match:
        return None

    month, day, hour, minute, second, status = last_match
    try:
        log_dt = datetime.datetime(now.year, month, day, hour, minute, second)
        if log_dt > now + datetime.timedelta(days=1):
            log_dt = log_dt.replace(year=now.year - 1)
        age_hours = (now - log_dt).total_seconds() / 3600.0
        if 0 <= age_hours <= max_age_h:
            time_str = f"{hour:02d}:{minute:02d}"
            return (status, time_str)
    except Exception:
        pass
    return None


_WIN_EPOCH_OFFSET_S = 11644473600


def omaha_history_status(
    history_text: str,
    appid: str,
    installed_versions: list[str],
    max_age_h: float = 6.0,
    now: float | None = None,
) -> tuple[str, str, str] | None:
    """Latest Omaha outcome for appid from a Chromium updater's updater_history.jsonl.

    UPDATE START events carry the appId, UPDATE END events carry the outcome
    (last updateStates entry) and nextVersion; both share processToken+eventId.
    The process START event carries the timestamp (Windows-epoch microseconds).
    Returns ("noupdate"|"updated", "HH:MM" local, version) only when the latest
    event is within max_age_h AND its version equals one of installed_versions
    (a NO_UPDATE for a different build proves nothing). Otherwise None.
    """
    if not history_text or not appid:
        return None
    if now is None:
        now = datetime.datetime.now().timestamp()

    proc_ts: dict[str, int] = {}
    starts: dict[tuple[str, str], str] = {}
    ends: list[dict] = []
    for line in history_text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue
        if not isinstance(ev, dict):
            continue
        etype, bound, tok = ev.get("eventType"), ev.get("bound"), ev.get("processToken")
        if etype == "UPDATER_PROCESS" and bound == "START" and tok:
            try:
                proc_ts[tok] = int(str(ev.get("timestamp", "")))
            except ValueError:
                pass
        elif etype == "UPDATE" and bound == "START" and tok:
            starts[(tok, str(ev.get("eventId")))] = str(ev.get("appId", ""))
        elif etype == "UPDATE" and bound == "END" and tok:
            ends.append(ev)

    best: tuple[int, str, str] | None = None
    for ev in ends:
        tok = ev.get("processToken")
        if starts.get((tok, str(ev.get("eventId")))) != appid:
            continue
        ts = proc_ts.get(tok)
        if ts is None:
            continue
        states = ev.get("updateStates") or []
        final = states[-1].get("state") if states and isinstance(states[-1], dict) else ""
        if best is None or ts >= best[0]:
            best = (ts, str(final), str(ev.get("nextVersion", "")))

    if best is None:
        return None
    ts, final, version = best
    unix_ts = ts / 1_000_000 - _WIN_EPOCH_OFFSET_S
    age_h = (now - unix_ts) / 3600.0
    if age_h < 0 or age_h > max_age_h:
        return None
    status = {"NO_UPDATE": "noupdate", "UPDATED": "updated"}.get(final)
    if status is None:
        return None
    vkey = version_key(version)
    if vkey is None or not any(version_key(v) == vkey for v in installed_versions if v):
        return None
    hhmm = datetime.datetime.fromtimestamp(unix_ts).strftime("%H:%M")
    return (status, hhmm, version)


def version_history_public(obj: dict | str) -> str | None:
    """Google VersionHistory response: max 'version' among releases whose float(fraction) >= 1.0;
    if none, max overall; None if empty."""
    if isinstance(obj, str):
        try:
            obj = json.loads(obj)
        except Exception:
            return None
    if not isinstance(obj, dict):
        return None
    releases = obj.get("releases", [])
    if not isinstance(releases, list) or not releases:
        return None

    full_rollout = []
    all_releases = []
    for r in releases:
        if not isinstance(r, dict):
            continue
        ver = r.get("version")
        if not ver:
            continue
        all_releases.append(ver)
        try:
            frac = float(r.get("fraction", 0))
            if frac >= 1.0:
                full_rollout.append(ver)
        except (ValueError, TypeError):
            pass

    pool = full_rollout if full_rollout else all_releases
    if not pool:
        return None

    best = None
    best_k = None
    for v in pool:
        k = version_key(v)
        if k is not None and (best_k is None or k > best_k):
            best_k = k
            best = v
    return best


def parse_config_line(line: str) -> dict | None:
    """config/vendor_feeds.txt row -> {'app','kind','url','arg','artifact','host'}; comments/blank -> None;
    requires exactly 6 '|' fields and url starting 'https://'."""
    if not line:
        return None
    s = line.strip()
    if not s or s.startswith("#"):
        return None
    parts = [p.strip() for p in s.split("|")]
    if len(parts) != 6:
        return None
    app, kind, url, arg, artifact, host = parts
    if not url.startswith("https://"):
        return None
    return {
        "app": app,
        "kind": kind,
        "url": url,
        "arg": arg,
        "artifact": artifact,
        "host": host,
    }
