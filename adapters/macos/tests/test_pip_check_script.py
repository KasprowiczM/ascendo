"""End-to-end tests for adapters/macos/scripts/pip/check.sh.

Each test invokes the script with ASCENDO_PYTHON_PIP_OVERRIDE pointing
at a fake pip that emits captured fixture output, and overrides curl
via PATH so PyPI lookups return canned JSON.

The check script must:
  * emit one item per non-comment manifest row,
  * classify status as missing/planned/up_to_date based on installed
    + latest version,
  * never crash when pip is missing or curl is missing.
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ADAPTER_ROOT / "scripts" / "pip" / "check.sh"


@pytest.fixture(autouse=True)
def _require_jq() -> None:
    if shutil.which("jq") is None:
        pytest.skip("jq required to parse pip list JSON")


def _write_exec(path: Path, body: str) -> Path:
    path.write_text(body)
    os.chmod(path, stat.S_IRWXU)
    return path


def _make_fake_pip(tmp_path: Path, list_json: str) -> Path:
    """Fake pip that returns canned `pip list --format=json` output."""
    p = tmp_path / "fake_pip"
    body = f"""#!/usr/bin/env bash
case "$1" in
  --version) echo 'pip 24.3.1 from /tmp/pip (python 3.12)' ;;
  list)
    # Emit our canned JSON regardless of subsequent flags.
    cat <<'EOF_LIST'
{list_json}
EOF_LIST
    ;;
  install) echo 'fake install: $@' ;;
  cache) echo 'fake cache: $@' ;;
  *) ;;
esac
exit 0
"""
    return _write_exec(p, body)


def _make_fake_curl(tmp_path: Path, mapping: dict[str, str]) -> Path:
    """Fake curl that returns canned PyPI JSON keyed by package name in URL.

    `mapping` maps package_name → JSON string (e.g. '{"info":{"version":"0.5.0"}}').
    Any URL containing `/pypi/<pkg>/json` returns the matching JSON;
    others return empty + exit 22 (curl's "HTTP error" code).
    """
    p = tmp_path / "fake_curl"
    cases = []
    for pkg, json_body in mapping.items():
        # Encode body in base64-of-JSON-via-heredoc to keep quoting sane.
        cases.append(
            f'    *"/pypi/{pkg}/json"*) cat <<\'EOF_CURL\'\n{json_body}\nEOF_CURL\n        exit 0 ;;'
        )
    body = "#!/usr/bin/env bash\n# Skip flags, find the URL.\nfor a in \"$@\"; do url=\"$a\"; done\ncase \"$url\" in\n"
    body += "\n".join(cases)
    body += '\n    *) exit 22 ;;\nesac\n'
    return _write_exec(p, body)


def _make_manifest(tmp_path: Path, rows: list[str]) -> Path:
    """Materialise a temp manifest file in pipe-delimited format.

    rows is a list of `display|package|method|description` strings
    (header row included by caller, NOT auto-added).
    """
    m = tmp_path / "pip_global_clis.txt"
    m.write_text("\n".join(rows) + "\n")
    return m


def _run_check(
    tmp_path: Path,
    *,
    fake_pip: Path | None,
    fake_curl: Path | None,
    manifest: Path | None,
    output_dir: Path,
    run_id: str,
    extra: list[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    # Build a minimal PATH that has jq + the fake curl, dropping real curl/pip.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    if fake_curl is not None:
        os.symlink(fake_curl, bin_dir / "curl")
    # bring in jq from the host
    jq_path = shutil.which("jq")
    assert jq_path is not None
    os.symlink(jq_path, bin_dir / "jq")
    # bring in basic utils
    for tool in ("awk", "sed", "head", "sort", "printf", "cat", "echo",
                 "scutil", "hostname", "sw_vers", "uname", "whoami",
                 "id", "mkdir", "mktemp", "rm", "tee", "date"):
        path = shutil.which(tool)
        if path is not None and not (bin_dir / tool).exists():
            try:
                os.symlink(path, bin_dir / tool)
            except FileExistsError:
                pass
    env["PATH"] = f"{bin_dir}:/usr/bin:/bin"
    if fake_pip is not None:
        env["ASCENDO_PYTHON_PIP_OVERRIDE"] = str(fake_pip)
    if manifest is not None:
        env["ASCENDO_PIP_MANIFEST_OVERRIDE"] = str(manifest)
    argv = [
        "bash", str(SCRIPT),
        "--run-id", run_id,
        "--trigger", "cli",
        "--profile", "default",
        "--output-dir", str(output_dir),
    ]
    if extra:
        argv.extend(extra)
    return subprocess.run(argv, capture_output=True, text=True, env=env, check=False)


def _parse_sidecar(path: Path):
    sys.path.insert(0, str(ADAPTER_ROOT.parent.parent / "core"))
    try:
        from ascendo.models.sidecar import parse_sidecar
        return parse_sidecar(path.read_text())
    finally:
        sys.path.pop(0)


# ── Tests ────────────────────────────────────────────────────────────────


def test_check_script_with_empty_manifest_emits_zero_items(tmp_path):
    """Empty manifest (header only) -> sidecar with 0 items."""
    manifest = _make_manifest(tmp_path, ["display_name|package_name|method|description"])
    fake_pip = _make_fake_pip(tmp_path, "[]")
    fake_curl = _make_fake_curl(tmp_path, {})
    out = tmp_path / "out"
    rid = str(uuid.uuid4())

    res = _run_check(
        tmp_path,
        fake_pip=fake_pip,
        fake_curl=fake_curl,
        manifest=manifest,
        output_dir=out,
        run_id=rid,
    )
    assert res.returncode == 0, f"stderr: {res.stderr}\nstdout: {res.stdout}"
    sc = _parse_sidecar(out / rid / "check__pip.json")
    assert sc.phase.value == "check"
    assert sc.category.value == "pip"
    assert len(sc.items) == 0


def test_check_script_emits_planned_when_installed_below_latest(tmp_path):
    """pkg installed at v1.0.0, latest is v2.0.0 -> status=planned."""
    manifest = _make_manifest(
        tmp_path,
        [
            "display_name|package_name|method|description",
            "ruff|ruff|pip|Linter",
        ],
    )
    fake_pip = _make_fake_pip(
        tmp_path,
        '[{"name":"ruff","version":"0.5.0"}]',
    )
    fake_curl = _make_fake_curl(
        tmp_path,
        {"ruff": '{"info":{"version":"0.7.4"}}'},
    )
    out = tmp_path / "out"
    rid = str(uuid.uuid4())

    res = _run_check(
        tmp_path,
        fake_pip=fake_pip,
        fake_curl=fake_curl,
        manifest=manifest,
        output_dir=out,
        run_id=rid,
    )
    assert res.returncode == 0, f"stderr: {res.stderr}\nstdout: {res.stdout}"
    sc = _parse_sidecar(out / rid / "check__pip.json")
    assert len(sc.items) == 1
    item = sc.items[0]
    assert item.id == "ruff"
    assert item.status.value == "planned"
    assert item.current_version == "0.5.0"
    assert item.target_version == "0.7.4"


def test_check_script_emits_up_to_date_when_match(tmp_path):
    """Installed == latest -> status=up_to_date."""
    manifest = _make_manifest(
        tmp_path,
        [
            "display_name|package_name|method|description",
            "uv|uv|pip|Fast Python pkg manager",
        ],
    )
    fake_pip = _make_fake_pip(
        tmp_path,
        '[{"name":"uv","version":"0.5.10"}]',
    )
    fake_curl = _make_fake_curl(
        tmp_path,
        {"uv": '{"info":{"version":"0.5.10"}}'},
    )
    out = tmp_path / "out"
    rid = str(uuid.uuid4())

    res = _run_check(
        tmp_path,
        fake_pip=fake_pip,
        fake_curl=fake_curl,
        manifest=manifest,
        output_dir=out,
        run_id=rid,
    )
    assert res.returncode == 0, res.stderr
    sc = _parse_sidecar(out / rid / "check__pip.json")
    assert len(sc.items) == 1
    item = sc.items[0]
    assert item.id == "uv"
    assert item.status.value == "up_to_date"


def test_check_script_emits_missing_when_not_installed(tmp_path):
    """Not in pip list output -> status=missing."""
    manifest = _make_manifest(
        tmp_path,
        [
            "display_name|package_name|method|description",
            "black|black|pip|Formatter",
        ],
    )
    # pip list returns no entry for black
    fake_pip = _make_fake_pip(tmp_path, "[]")
    fake_curl = _make_fake_curl(
        tmp_path,
        {"black": '{"info":{"version":"24.10.0"}}'},
    )
    out = tmp_path / "out"
    rid = str(uuid.uuid4())

    res = _run_check(
        tmp_path,
        fake_pip=fake_pip,
        fake_curl=fake_curl,
        manifest=manifest,
        output_dir=out,
        run_id=rid,
    )
    assert res.returncode == 0, res.stderr
    sc = _parse_sidecar(out / rid / "check__pip.json")
    assert len(sc.items) == 1
    item = sc.items[0]
    assert item.id == "black"
    assert item.status.value == "missing"
    assert not item.current_version  # empty / unknown
    assert item.target_version == "24.10.0"


def _make_fake_brew_pip(tmp_path: Path, list_json: str) -> Path:
    """Fake pip whose `--version` advertises a Homebrew install path.

    ``_ascendo_pip_flavour`` matches either the absolute path of the
    pip binary OR the ``pip --version`` output against ``*homebrew*``
    /``*Homebrew*``/``*Cellar*``/``*Caskroom*``. Since pytest's
    ``tmp_path`` does NOT contain "homebrew", we steer detection via
    the version string instead.
    """
    p = tmp_path / "fake_pip"
    body = f"""#!/usr/bin/env bash
case "$1" in
  --version)
    # The substring "homebrew" forces _ascendo_pip_flavour -> brew.
    echo 'pip 26.1 from /opt/homebrew/lib/python3.13/site-packages/pip (python 3.13)'
    ;;
  list)
    cat <<'EOF_LIST'
{list_json}
EOF_LIST
    ;;
  install) echo 'fake install: $@' ;;
  cache) echo 'fake cache: $@' ;;
  *) ;;
esac
exit 0
"""
    return _write_exec(p, body)


def test_check_script_brew_pip_self_skip_pins_candidate_to_installed(tmp_path):
    """Brew-managed pip self-upgrade is reclassified to up_to_date AND
    candidate is pinned to installed.

    Regression test for the Sesja-34 pip version-mismatch bug: the dashboard's
    ``_classify`` overlay re-runs ``_version_gt(candidate, installed)`` on
    the fly, so unless ``check.sh`` pins ``LATEST=INSTALLED`` in the
    brew-self-skip case, the row flips back to ``outdated`` even though
    the sidecar marked it ``up_to_date``.

    The contract this test guards:
        - status == up_to_date
        - current_version == target_version (both 26.1)
        - sidecar carries an explanatory message
    """
    manifest = _make_manifest(
        tmp_path,
        [
            "display_name|package_name|method|description",
            "pip|pip|pip|Python package installer",
        ],
    )
    # Brew-managed pip is at 26.1; PyPI advertises 26.1.1.
    fake_pip = _make_fake_brew_pip(
        tmp_path,
        '[{"name":"pip","version":"26.1"}]',
    )
    fake_curl = _make_fake_curl(
        tmp_path,
        {"pip": '{"info":{"version":"26.1.1"}}'},
    )
    out = tmp_path / "out"
    rid = str(uuid.uuid4())

    res = _run_check(
        tmp_path,
        fake_pip=fake_pip,
        fake_curl=fake_curl,
        manifest=manifest,
        output_dir=out,
        run_id=rid,
    )
    assert res.returncode == 0, f"stderr: {res.stderr}\nstdout: {res.stdout}"
    sc = _parse_sidecar(out / rid / "check__pip.json")
    assert len(sc.items) == 1
    item = sc.items[0]
    assert item.id == "pip"
    # The brew self-skip: status flipped to up_to_date.
    assert item.status.value == "up_to_date", (
        f"expected up_to_date, got {item.status.value}"
    )
    # Critical regression guard: candidate (target_version) must equal
    # installed (current_version), NOT the PyPI value 26.1.1. Otherwise
    # the dashboard's _classify overlay flips it back to outdated.
    assert item.current_version == "26.1"
    assert item.target_version == "26.1", (
        f"target_version must be pinned to installed; got {item.target_version!r}"
    )
    # The skip should be visible in sidecar messages so the operator
    # knows to use `brew upgrade python` instead.
    msg_texts = " ".join(getattr(m, "text", "") for m in (sc.messages or []))
    assert "brew" in msg_texts.lower(), (
        f"expected a brew-skip message in sidecar; got: {msg_texts!r}"
    )


def test_check_script_filter_restricts_to_named_rows(tmp_path):
    """--filter csv restricts emitted items to listed display_names."""
    manifest = _make_manifest(
        tmp_path,
        [
            "display_name|package_name|method|description",
            "ruff|ruff|pip|Linter",
            "uv|uv|pip|Fast Python pkg mgr",
        ],
    )
    fake_pip = _make_fake_pip(
        tmp_path,
        '[{"name":"ruff","version":"0.5.0"},{"name":"uv","version":"0.5.10"}]',
    )
    fake_curl = _make_fake_curl(
        tmp_path,
        {
            "ruff": '{"info":{"version":"0.7.4"}}',
            "uv": '{"info":{"version":"0.5.10"}}',
        },
    )
    out = tmp_path / "out"
    rid = str(uuid.uuid4())

    res = _run_check(
        tmp_path,
        fake_pip=fake_pip,
        fake_curl=fake_curl,
        manifest=manifest,
        output_dir=out,
        run_id=rid,
        extra=["--filter", "ruff"],
    )
    assert res.returncode == 0, res.stderr
    sc = _parse_sidecar(out / rid / "check__pip.json")
    assert len(sc.items) == 1
    assert sc.items[0].id == "ruff"
