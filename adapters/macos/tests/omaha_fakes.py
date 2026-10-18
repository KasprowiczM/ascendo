"""Shared fakes for the chromium_updater tests: updater_history.jsonl builder, fake app
bundles, fake updater executables and a hermetic bash runner. No real updater, browser,
network or /Applications access."""
from __future__ import annotations

import json
import os
import subprocess
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
LIB = REPO_ROOT / "adapters" / "macos" / "lib"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "vendor_feeds"
WIN_EPOCH_OFFSET = 11644473600


def win_us(unix_seconds: float) -> str:
    return str(int((unix_seconds + WIN_EPOCH_OFFSET) * 1_000_000))


def history_text(events, started: float) -> str:
    """events: (appId, token, final_state, nextVersion) all in one updater process."""
    lines = []
    for tok in sorted({e[1] for e in events}):
        lines.append(json.dumps({
            "bound": "START", "eventType": "UPDATER_PROCESS", "eventId": "0",
            "processToken": tok, "timestamp": win_us(started),
        }))
    for i, (appid, tok, state, ver) in enumerate(events, start=5):
        lines.append(json.dumps({
            "appId": appid, "bound": "START", "eventType": "UPDATE",
            "eventId": str(i), "processToken": tok,
        }))
        lines.append(json.dumps({
            "bound": "END", "eventType": "UPDATE", "eventId": str(i),
            "processToken": tok, "result": "SUCCESS", "nextVersion": ver,
            "updateStates": [{"state": "CHECKING_FOR_UPDATES"}, {"state": state}],
        }))
    return "\n".join(lines) + "\n"


def response_line(appid: str, status: str = "noupdate") -> str:
    """A GoogleUpdater text-log Omaha RESPONSE line (prefixed, same-line body)."""
    return ('[1:2:0923/094646.163000:VERBOSE2:components/update_client/request_sender.cc:200] '
            '{"response":{"apps":[{"appid":"%s","status":"ok","ping":{"status":"ok"},'
            '"updatecheck":{"status":"%s"}}]}}\n' % (appid, status))


def make_app(root: Path, name: str, short: str, build: str | None = None,
             bundle_id: str | None = None) -> Path:
    app = root / f"{name}.app"
    (app / "Contents").mkdir(parents=True, exist_ok=True)
    set_app_version(app, short, build, bundle_id)
    return app


def set_app_version(app: Path, short: str, build: str | None = None,
                    bundle_id: str | None = None) -> None:
    extra = f"<key>CFBundleVersion</key><string>{build}</string>" if build else ""
    if bundle_id:
        extra += f"<key>CFBundleIdentifier</key><string>{bundle_id}</string>"
    (app / "Contents" / "Info.plist").write_text(
        '<?xml version="1.0"?><plist version="1.0"><dict>'
        f'<key>CFBundleShortVersionString</key><string>{short}</string>{extra}'
        '</dict></plist>')


def make_exe(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/bash\n" + textwrap.dedent(body))
    path.chmod(0o755)
    return path


def hermetic_env(tmp: Path, extra: dict | None = None) -> dict:
    """PATH = fake bin dir first + system dirs (+ the python3 dir); ASCENDO_* dropped."""
    import shutil
    py = shutil.which("python3")
    bin_dir = tmp / "fakebin"
    bin_dir.mkdir(exist_ok=True)
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("ASCENDO_", "MAC_UPDATE_"))}
    env["PATH"] = ":".join([str(bin_dir), os.path.dirname(py) if py else "",
                            "/usr/bin:/bin:/usr/sbin:/sbin"]).replace("::", ":")
    env["HOME"] = str(tmp / "home")
    (tmp / "home").mkdir(exist_ok=True)
    env["ASCENDO_NPM_BREW_DIRS"] = ""
    env["ASCENDO_OMAHA_WAIT"] = "1"
    env["ASCENDO_OMAHA_VERIFY_WAIT"] = "0"
    env["ASCENDO_WEB_CURL_BIN"] = str(make_exe(bin_dir / "curl-fail", "exit 22\n"))
    env["ASCENDO_LSAPPINFO_BIN"] = str(make_exe(
        bin_dir / "lsappinfo-fake", "echo 'bundleID=\"com.apple.finder\"'\n"))
    if extra:
        env.update(extra)
    return env


def fake_curl_serving(tmp: Path, body_file: Path) -> Path:
    # Honors `-o <file>` like the real curl (_web_fetch_feed downloads to a temp file).
    return make_exe(tmp / "fakebin" / "curl-vh", f'''out=""
while [ $# -gt 0 ]; do
  if [ "$1" = "-o" ]; then out="$2"; shift; fi
  shift
done
if [ -n "$out" ]; then cat "{body_file}" > "$out"; else cat "{body_file}"; fi
''')


PRELUDE = f"""
set -o pipefail
source "{LIB}/ascendo_json.sh"
source "{LIB}/ascendo_web.sh"
source "{LIB}/handlers/chromium_updater.sh"
"""


def run_bash(snippet: str, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-c", PRELUDE + textwrap.dedent(snippet)],
                          capture_output=True, text=True, env=env, timeout=120)
