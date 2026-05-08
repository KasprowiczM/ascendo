"""omaha.sh handler tests.

Covers the Omaha v3 XML and v4 JSON response parsers, plus the
OmahaConfig Pydantic schema validation.
"""
from __future__ import annotations

import http.server
import json
import os
import socket
import subprocess
import textwrap
import threading
from pathlib import Path

import pytest
from pydantic import ValidationError

REPO_ROOT = Path(__file__).resolve().parents[3]
LIB = REPO_ROOT / "adapters" / "macos" / "lib"


def _run(snippet: str, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ}
    if env_extra:
        env.update(env_extra)
    full = textwrap.dedent(f"""\
        set -eo pipefail
        source {LIB}/handlers/omaha.sh
        {snippet}
    """)
    return subprocess.run(["bash", "-c", full], capture_output=True, text=True, env=env)


# ---------------- Live HTTP fixture ----------------------------------------

def _make_server(handler_cls):
    """Bind on 127.0.0.1:0 so the OS picks a free port."""
    httpd = http.server.HTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread


def _stop_server(httpd, thread):
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=2)


class _OmahaV3Handler(http.server.BaseHTTPRequestHandler):
    response_body = b""
    capture_path: list = []
    capture_body: list = []

    def do_POST(self):  # noqa: N802 — http.server requires this name
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else b""
        type(self).capture_path.append(self.path)
        type(self).capture_body.append(body)
        self.send_response(200)
        self.send_header("Content-Type", "text/xml; charset=UTF-8")
        self.send_header("Content-Length", str(len(self.response_body)))
        self.end_headers()
        self.wfile.write(self.response_body)

    def log_message(self, format, *args):  # silence test output
        pass


# ---------------- Tests ----------------------------------------------------

def test_omaha_handler_parses_v3_xml_response(tmp_path):
    """Omaha 3.0 XML: extract version from manifest/@version."""
    response = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<response protocol="3.0" server="prod">'
        b'<daystart elapsed_days="7067" elapsed_seconds="49190"/>'
        b'<app appid="com.google.drivefs" cohort="1:0:" cohortname="Stable" status="ok">'
        b'<updatecheck status="ok">'
        b'<urls><url codebase="https://example.com/dl/"/></urls>'
        b'<manifest version="124.0.3.0">'
        b'<actions><action event="update" run="GoogleDrive.dmg"/></actions>'
        b'<packages><package name="GoogleDrive.dmg" required="true" size="100"/></packages>'
        b'</manifest>'
        b'</updatecheck>'
        b'</app>'
        b'</response>'
    )

    class H(_OmahaV3Handler):
        response_body = response
        capture_path = []
        capture_body = []

    httpd, thread = _make_server(H)
    try:
        port = httpd.server_address[1]
        cfg = json.dumps({
            "slug": "gdrive",
            "handler": "omaha",
            "omaha": {
                "endpoint": f"http://127.0.0.1:{port}/service/update2",
                "appid": "com.google.drivefs",
                "protocol": "3.0",
            },
        })
        snippet = f"omaha_check 'gdrive' {json.dumps(cfg)!r}"
        r = _run(snippet)
    finally:
        _stop_server(httpd, thread)

    assert r.returncode == 0, f"stderr={r.stderr!r}"
    assert r.stdout.strip() == "124.0.3.0"
    # Confirm the request shape: POST with XML body containing the appid.
    assert len(H.capture_body) == 1
    request_body = H.capture_body[0].decode("utf-8")
    assert request_body.startswith("<?xml")
    assert 'appid="com.google.drivefs"' in request_body
    assert 'version="0.0.0.0"' in request_body
    assert "<updatecheck/>" in request_body


def test_omaha_handler_handles_noupdate_status(tmp_path):
    """Omaha 3.0 response with status=noupdate -> empty stdout, rc=28."""
    response = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<response protocol="3.0" server="prod">'
        b'<app appid="com.google.chrome" cohort="1:gu/i19:" cohortname="Stable" status="ok">'
        b'<updatecheck status="noupdate"/>'
        b'</app>'
        b'</response>'
    )

    class H(_OmahaV3Handler):
        response_body = response
        capture_path = []
        capture_body = []

    httpd, thread = _make_server(H)
    try:
        port = httpd.server_address[1]
        cfg = json.dumps({
            "slug": "chrome",
            "handler": "omaha",
            "omaha": {
                "endpoint": f"http://127.0.0.1:{port}/service/update2",
                "appid": "com.google.chrome",
                "protocol": "3.0",
            },
        })
        snippet = f"omaha_check 'chrome' {json.dumps(cfg)!r} || echo \"rc=$?\""
        r = _run(snippet)
    finally:
        _stop_server(httpd, thread)

    # Empty stdout + the rc=28 sentinel from `|| echo`
    assert "124.0" not in r.stdout
    assert "rc=28" in r.stdout, f"expected rc=28 in {r.stdout!r}"


def test_omaha_handler_parses_v4_json_response(tmp_path):
    """Omaha 4.0 JSON: extract version from updatecheck.nextversion.

    Includes the Chromium )]}' safety prefix.
    """
    body_raw = ")]}'\n" + json.dumps({
        "response": {
            "protocol": "4.0",
            "apps": [
                {
                    "appid": "ai.perplexity.comet",
                    "status": "ok",
                    "updatecheck": {
                        "status": "ok",
                        "nextversion": "147.0.7727.1858",
                        "pipelines": [
                            {
                                "operations": [
                                    {"type": "download",
                                     "urls": [{"url": "https://example.com/u.crx"}]}
                                ],
                                "pipeline_id": "default",
                            }
                        ],
                    },
                }
            ],
        }
    })

    class H(_OmahaV3Handler):
        response_body = body_raw.encode("utf-8")
        capture_path = []
        capture_body = []

    httpd, thread = _make_server(H)
    try:
        port = httpd.server_address[1]
        cfg = json.dumps({
            "slug": "comet",
            "handler": "omaha",
            "omaha": {
                "endpoint": f"http://127.0.0.1:{port}/rest/browser/update2",
                "appid": "ai.perplexity.comet",
                "protocol": "4.0",
            },
        })
        snippet = f"omaha_check 'comet' {json.dumps(cfg)!r}"
        r = _run(snippet)
    finally:
        _stop_server(httpd, thread)

    assert r.returncode == 0, f"stderr={r.stderr!r}"
    assert r.stdout.strip() == "147.0.7727.1858"
    # Confirm we sent JSON
    request_body = H.capture_body[0].decode("utf-8")
    parsed = json.loads(request_body)
    assert parsed["request"]["protocol"] == "4.0"
    assert parsed["request"]["apps"][0]["appid"] == "ai.perplexity.comet"
    assert parsed["request"]["apps"][0]["version"] == "0.0.0.0"


def test_omaha_handler_sends_tag_for_channel():
    """tag config field becomes the `tag=` attribute on the v3 request."""

    class H(_OmahaV3Handler):
        response_body = (
            b'<?xml version="1.0" encoding="UTF-8"?>'
            b'<response protocol="3.0" server="prod">'
            b'<app appid="com.google.geminimacos" status="ok">'
            b'<updatecheck status="ok">'
            b'<manifest version="1.53.0.262"/>'
            b'</updatecheck>'
            b'</app>'
            b'</response>'
        )
        capture_path = []
        capture_body = []

    httpd, thread = _make_server(H)
    try:
        port = httpd.server_address[1]
        cfg = json.dumps({
            "slug": "gemini",
            "handler": "omaha",
            "omaha": {
                "endpoint": f"http://127.0.0.1:{port}/service/update2",
                "appid": "com.google.geminimacos",
                "protocol": "3.0",
                "tag": "m1-prod",
            },
        })
        snippet = f"omaha_check 'gemini' {json.dumps(cfg)!r}"
        r = _run(snippet)
    finally:
        _stop_server(httpd, thread)

    assert r.returncode == 0
    assert r.stdout.strip() == "1.53.0.262"
    request_body = H.capture_body[0].decode("utf-8")
    assert 'tag="m1-prod"' in request_body


def test_omaha_handler_unknown_app_returns_empty():
    """Server replies with status=error-unknownApplication -> empty + rc=28."""

    class H(_OmahaV3Handler):
        response_body = (
            b'<?xml version="1.0" encoding="UTF-8"?>'
            b'<response protocol="3.0">'
            b'<app appid="com.bogus.app" status="error-unknownApplication">'
            b'</app>'
            b'</response>'
        )
        capture_path = []
        capture_body = []

    httpd, thread = _make_server(H)
    try:
        port = httpd.server_address[1]
        cfg = json.dumps({
            "slug": "bogus",
            "handler": "omaha",
            "omaha": {
                "endpoint": f"http://127.0.0.1:{port}/service/update2",
                "appid": "com.bogus.app",
                "protocol": "3.0",
            },
        })
        snippet = f"omaha_check 'bogus' {json.dumps(cfg)!r} || echo \"rc=$?\""
        r = _run(snippet)
    finally:
        _stop_server(httpd, thread)

    assert "rc=28" in r.stdout


# ---------------- Pydantic schema tests ------------------------------------

def test_omaha_config_validates_appid_format():
    """OmahaConfig accepts both reverse-DNS and UUID-in-braces appids,
    and rejects forms with shell metacharacters."""
    from ascendo_macos.web_registry import OmahaConfig

    # Reverse-DNS string
    cfg = OmahaConfig(
        endpoint="https://update.googleapis.com/service/update2",
        appid="com.google.drivefs",
    )
    assert cfg.appid == "com.google.drivefs"
    assert cfg.protocol == "3.0"  # default
    assert cfg.tag is None

    # UUID-in-braces (Chrome's appid format)
    cfg2 = OmahaConfig(
        endpoint="https://update.googleapis.com/service/update2",
        appid="{8A69D345-D564-463C-AFF1-A69D9E530F96}",
    )
    assert cfg2.appid == "{8A69D345-D564-463C-AFF1-A69D9E530F96}"

    # Bundle-id style
    cfg3 = OmahaConfig(
        endpoint="https://www.perplexity.ai/rest/browser/update2",
        appid="ai.perplexity.comet",
        protocol="4.0",
    )
    assert cfg3.protocol == "4.0"

    # Reject empty appid
    with pytest.raises(ValidationError):
        OmahaConfig(
            endpoint="https://update.googleapis.com/service/update2",
            appid="",
        )

    # Reject appid with shell metacharacters (e.g. semicolon, space)
    with pytest.raises(ValidationError):
        OmahaConfig(
            endpoint="https://update.googleapis.com/service/update2",
            appid="com.google.drivefs; rm -rf /",
        )

    # Reject http:// (https-only via T3 mitigation)
    with pytest.raises(ValidationError):
        OmahaConfig(
            endpoint="http://update.googleapis.com/service/update2",
            appid="com.google.drivefs",
        )

    # Reject unknown protocol
    with pytest.raises(ValidationError):
        OmahaConfig(
            endpoint="https://update.googleapis.com/service/update2",
            appid="com.google.drivefs",
            protocol="2.0",
        )


def test_webapp_omaha_handler_requires_omaha_subtable():
    """handler=omaha demands [app.omaha]; without it, ValidationError."""
    from ascendo_macos.web_registry import WebApp

    # Valid: handler=omaha + omaha sub-table
    app = WebApp(
        slug="gdrive",
        bundle_id="com.google.drivefs",
        display_name="Google Drive",
        handler="omaha",
        omaha={
            "endpoint": "https://update.googleapis.com/service/update2",
            "appid": "com.google.drivefs",
        },
    )
    assert app.handler == "omaha"
    assert app.omaha.appid == "com.google.drivefs"

    # Invalid: handler=omaha but no [app.omaha] table
    with pytest.raises(ValidationError, match="omaha handler requires"):
        WebApp(
            slug="gdrive",
            bundle_id="com.google.drivefs",
            display_name="Google Drive",
            handler="omaha",
        )

    # Invalid: omaha sub-table on a non-omaha handler
    with pytest.raises(ValidationError, match="omaha sub-table only valid"):
        WebApp(
            slug="zoom",
            bundle_id="us.zoom.xos",
            display_name="Zoom",
            handler="sparkle",
            appcast_url="https://zoom.us/appcast.xml",
            omaha={
                "endpoint": "https://update.googleapis.com/service/update2",
                "appid": "com.zoom.zoomus",
            },
        )


def test_webapp_omaha_can_carry_ksadmin_product_id():
    """Apply delegates to keystone_apply when ksadmin_product_id is set
    on an omaha-handler entry — so the validator must permit the field
    on omaha (in addition to keystone)."""
    from ascendo_macos.web_registry import WebApp

    # Valid combination — omaha + ksadmin_product_id (apply delegates)
    app = WebApp(
        slug="gdrive",
        bundle_id="com.google.drivefs",
        display_name="Google Drive",
        handler="omaha",
        ksadmin_product_id="com.google.drivefs",
        omaha={
            "endpoint": "https://update.googleapis.com/service/update2",
            "appid": "com.google.drivefs",
        },
    )
    assert app.ksadmin_product_id == "com.google.drivefs"

    # Still rejected on, say, a sparkle entry
    with pytest.raises(ValidationError, match="ksadmin_product_id only valid"):
        WebApp(
            slug="zoom",
            bundle_id="us.zoom.xos",
            display_name="Zoom",
            handler="sparkle",
            appcast_url="https://zoom.us/appcast.xml",
            ksadmin_product_id="us.zoom.xos",
        )
