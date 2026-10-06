"""Native-installer CLIs in the npm category (claude, codex, agy, cursor-agent).

Task 7 of the macOS_updates parity plan (cli.md P1, P3, P4):

  * an installed native CLI updates with its OWN subcommand (`<cmd> update`);
    the vendor install script is never re-run for an installed `claude`;
  * the vendor installer (bootstrap / fallback) is downloaded to a temp file
    (https-only, size cap, non-empty) and executed with per-vendor env + args
    and a bounded timeout -- never `curl | sh`;
  * success requires a working `--version` that prints a digit-bearing version
    (a bare product name such as `codex-cli` is not a version).

Everything runs against fakes: a temp HOME, fake CLIs / curl / npm on PATH, and
the ASCENDO_NPM_MANIFEST seam (a one-row manifest). No real vendor installer
and no real `<cmd> update` is ever executed.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import pytest

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
APPLY = ADAPTER_ROOT / "scripts" / "npm" / "apply.sh"
VERIFY = ADAPTER_ROOT / "scripts" / "npm" / "verify.sh"
LIB = ADAPTER_ROOT / "lib" / "ascendo_npm.sh"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")

# display, package, method, brew, command
CLIS = {
    "claude": ("claude-code", "@anthropic-ai/claude-code", "claude",
               "https://claude.ai/install.sh", "latest"),
    "codex": ("codex-cli", "@openai/codex", "codex",
              "https://chatgpt.com/codex/install.sh", "--release latest"),
    "agy": ("agy-cli", "agy", "agy",
            "https://antigravity.google/cli/install.sh", ""),
    "agent": ("cursor-agent", "cursor-agent", "agent",
              "https://cursor.com/install", ""),
}


# --------------------------------------------------------------------------
# fixtures / helpers
# --------------------------------------------------------------------------

def _fake_cli_script(log: Path, version_line: str, *,
                     update_to: str | None = None,
                     update_rc: int = 0,
                     update_hang: bool = False,
                     version_rc: int = 0) -> str:
    """A fake vendor CLI. The version it prints lives in `<script>.ver` so an
    `update` can bump it. `version_line` uses `{v}` for the stored version."""
    update_body = ""
    if update_hang:
        update_body = "trap '' TERM\nsleep 300 &\nwait\n"
    elif update_to is not None:
        update_body = f"echo '{update_to}' > \"$0.ver\"\n"
    return f"""#!/bin/sh
echo "$(basename "$0") $*" >> "{log}"
case "$1" in
  --version|-v)
    [ {version_rc} -eq 0 ] || exit {version_rc}
    v="$(cat "$0.ver" 2>/dev/null)"
    printf '%s\\n' "{version_line}" | sed "s/{{v}}/$v/"
    exit 0 ;;
  update)
    cat >/dev/null
    {update_body}
    [ {update_rc} -eq 0 ] || {{ echo "update exploded: boom" >&2; exit {update_rc}; }}
    exit 0 ;;
esac
exit 0
"""


# Vendor install roots (cli_duplicates.supported_native_path) per command.
_NATIVE_ROOTS = {
    "claude": ".local/share/claude/versions/9.9.9",
    "codex": ".codex/packages/standalone/releases/9.9.9",
}


def _install_cli(home: Path, cmd: str, log: Path, version: str,
                 version_line: str = "{v}", **kw) -> Path:
    p = home / ".local" / "bin" / cmd
    p.parent.mkdir(parents=True, exist_ok=True)
    real = _NATIVE_ROOTS.get(cmd)
    if real is None:
        target = p
    else:
        # claude/codex count as native only inside the vendor's own install
        # root (F4): install the script there and link it from ~/.local/bin.
        target = home / real / cmd
        target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_fake_cli_script(log, version_line, **kw))
    target.chmod(0o755)
    if target != p:
        p.symlink_to(target)
    Path(str(p) + ".ver").write_text(version + "\n")
    return p


def _installer_script(home: Path, cmd: str, log: Path, cli_log: Path, *,
                      new_version: str = "9.9.9",
                      version_line: str = "{v}",
                      write_cli: bool = True,
                      hang: bool = False,
                      rc: int = 0) -> str:
    """Body of the fake vendor install script served by the fake curl."""
    cli = _fake_cli_script(cli_log, version_line)
    hang_body = "trap '' TERM\nsleep 300 &\nwait\n" if hang else ""
    write = ""
    if write_cli:
        write = (f'mkdir -p "{home}/.local/bin"\n'
                 f'cat > "{home}/.local/bin/{cmd}" <<\'CLI_EOF\'\n{cli}CLI_EOF\n'
                 f'chmod 755 "{home}/.local/bin/{cmd}"\n'
                 f'echo "{new_version}" > "{home}/.local/bin/{cmd}.ver"\n')
    return f"""#!/bin/sh
echo "ENV CODEX_NON_INTERACTIVE=${{CODEX_NON_INTERACTIVE:-unset}} ARGS=$*" >> "{log}"
cat >/dev/null
{hang_body}{write}exit {rc}
"""


class Env:
    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.home = tmp / "home"
        self.home.mkdir()
        self.bin = tmp / "bin"
        self.bin.mkdir()
        # Hermetic PATH: the developer's real claude/codex/agy/... must never
        # be found by the PATH-fallback locator. Only python3 is borrowed.
        self.tools = tmp / "tools"
        self.tools.mkdir()
        py = shutil.which("python3")
        if py:
            (self.tools / "python3").symlink_to(py)
        self.fixtures = tmp / "fixtures"
        self.fixtures.mkdir()
        self.curl_log = tmp / "curl.log"
        self.curl_log.touch()
        self.installer_log = tmp / "installer.log"
        self.installer_log.touch()
        self.cli_log = tmp / "cli.log"
        self.cli_log.touch()
        self.out = tmp / "out"
        self._write_fake_curl()
        self._write_fake_npm()
        # Never let a test reach the real brew (`brew install node` is a real,
        # mutating command): the only brew on PATH is this failing fake.
        brew = self.bin / "brew"
        brew.write_text("#!/bin/sh\ncat >/dev/null\nexit 1\n")
        brew.chmod(0o755)

    def _write_fake_curl(self) -> None:
        p = self.bin / "curl"
        p.write_text(f"""#!/bin/sh
echo "$@" >> "{self.curl_log}"
out=""; url=""
while [ $# -gt 0 ]; do
  case "$1" in
    -o) out="$2"; shift 2 ;;
    https://*|http://*) url="$1"; shift ;;
    *) shift ;;
  esac
done
key="$(printf '%s' "$url" | tr '/:.' '___')"
src="{self.fixtures}/$key"
[ -f "$src" ] || exit 22
if [ -n "$out" ]; then cp "$src" "$out"; else cat "$src"; fi
""")
        p.chmod(0o755)

    def _write_fake_npm(self) -> None:
        p = self.bin / "npm"
        p.write_text("""#!/bin/sh
case "$1" in
  --version) echo 10.0.0 ;;
  ls|outdated) echo '{}' ;;
  view) echo 1.0.0 ;;
esac
exit 0
""")
        p.chmod(0o755)

    def serve(self, url: str, body: str) -> None:
        key = url.replace("/", "_").replace(":", "_").replace(".", "_")
        (self.fixtures / key).write_text(body)

    def manifest(self, name: str) -> Path:
        display, pkg, cmd, _url, _args = CLIS[name]
        m = self.tmp / "manifest.txt"
        m.write_text(f"display_name|package_name|method|brew_formula|command\n"
                     f"{display}|{pkg}|native-installer||{cmd}\n")
        return m

    def run(self, script: Path, name: str, *extra: str,
            env_extra: dict | None = None, timeout: float = 120):
        env = dict(os.environ)
        # A developer's own toggles (ASCENDO_BOOTSTRAP_CLI=1, BUN_INSTALL, ...)
        # must not change what these tests assert.
        for key in list(env):
            if key.startswith(("ASCENDO_", "MAC_UPDATE_")) or key == "BUN_INSTALL":
                del env[key]
        env.update({
            "HOME": str(self.home),
            "PATH": f"{self.bin}:{self.tools}:/usr/bin:/bin:/usr/sbin:/sbin",
            "MAC_UPDATE_TOOLCHAIN_HOME": str(self.tmp / "toolchain"),
            "ASCENDO_NPM_MANIFEST": str(self.manifest(name)),
            "ASCENDO_KILL_AFTER": "1",
            "ASCENDO_NPM_BREW_DIRS": "",
        })
        env.update(env_extra or {})
        rid = str(uuid.uuid4())
        t0 = time.monotonic()
        res = subprocess.run(
            ["bash", str(script), "--run-id", rid, "--trigger", "cli",
             "--profile", "default", "--output-dir", str(self.out), *extra],
            capture_output=True, text=True, env=env, check=False,
            stdin=subprocess.DEVNULL, timeout=timeout)
        res.elapsed = time.monotonic() - t0  # type: ignore[attr-defined]
        res.sidecar = None  # type: ignore[attr-defined]
        phase = "apply" if script == APPLY else "verify"
        sc = self.out / rid / f"{phase}__npm.json"
        if sc.exists():
            res.sidecar = json.loads(sc.read_text())  # type: ignore[attr-defined]
        return res


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return Env(tmp_path)


def _item(res, display: str) -> dict:
    assert res.sidecar is not None, res.stderr
    items = [i for i in res.sidecar["items"] if i["id"] == display]
    assert len(items) == 1, res.sidecar["items"]
    return items[0]


def _msgs(res) -> str:
    return "\n".join(m.get("text") or "" for m in res.sidecar["messages"])


def _read(p: Path) -> str:
    return p.read_text() if p.exists() else ""


# --------------------------------------------------------------------------
# P1: installed CLIs update with their own subcommand
# --------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["claude", "codex", "agy", "agent"])
def test_installed_cli_uses_update_subcommand_not_installer(env: Env, name: str) -> None:
    display, _pkg, cmd, url, _args = CLIS[name]
    _install_cli(env.home, cmd, env.cli_log, "1.0.0", update_to="1.1.0")
    env.serve(url, _installer_script(env.home, cmd, env.installer_log, env.cli_log))
    res = env.run(APPLY, name)
    assert res.returncode == 0, res.stderr
    assert f"{cmd} update" in _read(env.cli_log)
    assert _read(env.curl_log) == "", "installer must not be downloaded"
    assert _read(env.installer_log) == ""
    it = _item(res, display)
    assert it["status"] == "success"
    assert it["current_version"] == "1.0.0"
    assert it["target_version"] == "1.1.0"


def test_update_with_unchanged_version_is_up_to_date(env: Env) -> None:
    _install_cli(env.home, "codex", env.cli_log, "0.153.4",
                 version_line="codex-cli {v}", update_to="0.153.4")
    res = env.run(APPLY, "codex")
    it = _item(res, "codex-cli")
    assert it["status"] == "up_to_date"
    assert it["current_version"] == it["target_version"] == "0.153.4"


def test_claude_installed_never_runs_install_sh(env: Env) -> None:
    """`claude update` fails but the binary runs: the install.sh TUI (which
    ignores SIGTERM and hung the pipeline) must NOT be re-run."""
    _install_cli(env.home, "claude", env.cli_log, "2.0.0", update_rc=3)
    env.serve(CLIS["claude"][3],
              _installer_script(env.home, "claude", env.installer_log, env.cli_log))
    res = env.run(APPLY, "claude")
    assert res.returncode == 0, res.stderr
    assert _read(env.curl_log) == ""
    assert _read(env.installer_log) == ""
    it = _item(res, "claude-code")
    assert it["status"] == "failed"
    assert "update exploded" in _msgs(res)


def test_update_failure_falls_back_to_installer(env: Env) -> None:
    _install_cli(env.home, "codex", env.cli_log, "0.100.0",
                 version_line="codex-cli {v}", update_rc=1)
    env.serve(CLIS["codex"][3], _installer_script(
        env.home, "codex", env.installer_log, env.cli_log,
        new_version="0.153.4", version_line="codex-cli {v}"))
    res = env.run(APPLY, "codex")
    assert res.returncode == 0, res.stderr
    it = _item(res, "codex-cli")
    assert it["status"] == "success"
    assert it["target_version"] == "0.153.4"
    log = _read(env.installer_log)
    assert "CODEX_NON_INTERACTIVE=1" in log
    assert "ARGS=--release latest" in log


def test_hanging_update_subcommand_is_killed_and_reported(env: Env) -> None:
    _install_cli(env.home, "claude", env.cli_log, "2.0.0", update_hang=True)
    res = env.run(APPLY, "claude", env_extra={
        "ASCENDO_NATIVE_INSTALLER_TIMEOUT": "2",
        "ASCENDO_NATIVE_INSTALLER_TIMEOUT_FLOOR": "1",
        "ASCENDO_FORCE_TIMEOUT_FALLBACK": "1"}, timeout=40)
    assert res.elapsed < 30, res.elapsed
    it = _item(res, "claude-code")
    assert it["status"] == "failed"
    assert "124" in _msgs(res)


# --------------------------------------------------------------------------
# P3: safe download-then-exec installer (bootstrap / fallback)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["claude", "codex", "agy", "agent"])
def test_missing_cli_is_installed_via_downloaded_script_with_vendor_args(
        env: Env, name: str) -> None:
    display, _pkg, cmd, url, bargs = CLIS[name]
    env.serve(url, _installer_script(env.home, cmd, env.installer_log, env.cli_log,
                                     new_version="3.1.4"))
    res = env.run(APPLY, name, env_extra={"ASCENDO_BOOTSTRAP_CLI": "1"})
    assert res.returncode == 0, res.stderr
    it = _item(res, display)
    assert it["status"] == "success"
    assert it["target_version"] == "3.1.4"
    log = _read(env.installer_log)
    assert f"ARGS={bargs}\n" in log, log
    if name == "codex":
        assert "CODEX_NON_INTERACTIVE=1" in log
    else:
        assert "CODEX_NON_INTERACTIVE=unset" in log
    curl = _read(env.curl_log)
    assert "--proto =https" in curl and "--proto-redir =https" in curl
    assert " -o " in f" {curl} " and "--max-filesize" in curl


def test_native_installer_function_never_pipes_curl_into_sh() -> None:
    text = APPLY.read_text()
    start = text.index("apply_native_installer() {")
    body = text[start:text.index("\n}\n", start)]
    code = "\n".join(l for l in body.splitlines() if not l.lstrip().startswith("#"))
    assert "|" not in code.replace("||", "").replace("| awk", "").replace(
        "claude|codex)", ""), code
    assert "curl" not in code.replace("command -v curl", "").replace(
        "curl missing", "").replace("ascendo_npm_fetch_installer", "")
    assert "ascendo_npm_fetch_installer" in code


def test_download_failure_is_failed_and_installer_not_run(env: Env) -> None:
    # nothing served -> fake curl exits 22
    res = env.run(APPLY, "agy", env_extra={"ASCENDO_BOOTSTRAP_CLI": "1"})
    it = _item(res, "agy-cli")
    assert it["status"] == "failed"
    assert "download" in _msgs(res).lower()
    assert _read(env.installer_log) == ""


def test_term_ignoring_installer_is_killed_within_deadline(env: Env) -> None:
    env.serve(CLIS["claude"][3], _installer_script(
        env.home, "claude", env.installer_log, env.cli_log, hang=True))
    res = env.run(APPLY, "claude", env_extra={
        "ASCENDO_BOOTSTRAP_CLI": "1",
        "ASCENDO_NATIVE_INSTALLER_TIMEOUT": "2",
        "ASCENDO_NATIVE_INSTALLER_TIMEOUT_FLOOR": "1",
        "ASCENDO_FORCE_TIMEOUT_FALLBACK": "1"}, timeout=40)
    assert res.elapsed < 30, f"installer hung the run ({res.elapsed:.1f}s)"
    it = _item(res, "claude-code")
    assert it["status"] == "failed"
    assert "timed out" in _msgs(res) and "124" in _msgs(res)


# --------------------------------------------------------------------------
# P4: honest verification
# --------------------------------------------------------------------------

def test_installer_exit0_but_product_name_only_is_failed(env: Env) -> None:
    """Exit 0 and a binary that only prints `codex-cli` (no digit)."""
    env.serve(CLIS["codex"][3], _installer_script(
        env.home, "codex", env.installer_log, env.cli_log,
        new_version="", version_line="codex-cli"))
    res = env.run(APPLY, "codex", env_extra={"ASCENDO_BOOTSTRAP_CLI": "1"})
    it = _item(res, "codex-cli")
    assert it["status"] == "failed"
    assert "--version" in _msgs(res)


def test_installer_exit0_but_no_binary_is_failed(env: Env) -> None:
    env.serve(CLIS["agy"][3], _installer_script(
        env.home, "agy", env.installer_log, env.cli_log, write_cli=False))
    res = env.run(APPLY, "agy", env_extra={"ASCENDO_BOOTSTRAP_CLI": "1"})
    assert _item(res, "agy-cli")["status"] == "failed"


def test_installer_exit0_but_version_command_broken_is_failed(env: Env) -> None:
    broken = _fake_cli_script(env.cli_log, "{v}", version_rc=1)
    body = (f"#!/bin/sh\nmkdir -p '{env.home}/.local/bin'\n"
            f"cat > '{env.home}/.local/bin/agent' <<'X'\n{broken}X\n"
            f"chmod 755 '{env.home}/.local/bin/agent'\nexit 0\n")
    env.serve(CLIS["agent"][3], body)
    res = env.run(APPLY, "agent", env_extra={"ASCENDO_BOOTSTRAP_CLI": "1"})
    assert _item(res, "cursor-agent")["status"] == "failed"
    assert "--version" in _msgs(res)


def test_update_exit0_but_version_does_not_run_claude_is_failed(env: Env) -> None:
    _install_cli(env.home, "claude", env.cli_log, "2.0.0", version_rc=1)
    res = env.run(APPLY, "claude")
    assert _item(res, "claude-code")["status"] == "failed"


def test_verify_fails_when_cli_present_but_prints_no_version(env: Env) -> None:
    """Candidate-less row (pkg has no npm registry scope) used to be `success`
    for any non-empty installed string -- including the product name."""
    _install_cli(env.home, "codex", env.cli_log, "", version_line="codex-cli")
    m = env.tmp / "m2.txt"
    m.write_text("display_name|package_name|method|brew_formula|command\n"
                 "codex-cli|codex|native-installer||codex\n")
    res = env.run(VERIFY, "codex", env_extra={"ASCENDO_NPM_MANIFEST": str(m)})
    assert _item(res, "codex-cli")["status"] == "failed"
    assert "--version" in _msgs(res)


def test_verify_still_succeeds_for_working_cli_without_candidate(env: Env) -> None:
    _install_cli(env.home, "codex", env.cli_log, "0.153.4", version_line="codex-cli {v}")
    m = env.tmp / "m2.txt"
    m.write_text("display_name|package_name|method|brew_formula|command\n"
                 "codex-cli|codex|native-installer||codex\n")
    res = env.run(VERIFY, "codex", env_extra={"ASCENDO_NPM_MANIFEST": str(m)})
    assert _item(res, "codex-cli")["status"] == "success"


def test_dry_run_is_planned_and_touches_nothing(env: Env) -> None:
    _install_cli(env.home, "codex", env.cli_log, "1.0.0", update_to="2.0.0")
    res = env.run(APPLY, "codex", "--dry-run")
    assert _item(res, "codex-cli")["status"] == "planned"
    assert "update" not in _read(env.cli_log)
    assert _read(env.curl_log) == ""


# --------------------------------------------------------------------------
# helper unit tests (sourcing the lib)
# --------------------------------------------------------------------------

def _lib(script: str, env_extra: dict | None = None):
    e = dict(os.environ)
    e.update(env_extra or {})
    return subprocess.run(["bash", "-c", f'. "{LIB}"\n{script}'],
                          capture_output=True, text=True, env=e, check=False,
                          stdin=subprocess.DEVNULL)


def test_update_cmd_mapping() -> None:
    for c in ("claude", "codex", "agy", "agent"):
        assert _lib(f"ascendo_npm_native_update_cmd {c}").stdout == "update"
    assert _lib("ascendo_npm_native_update_cmd gemini").stdout == ""


def test_bootstrap_args_and_env_are_per_vendor() -> None:
    assert _lib("ascendo_npm_native_bootstrap_args codex").stdout == "--release latest"
    assert _lib("ascendo_npm_native_bootstrap_args claude").stdout == "latest"
    assert _lib("ascendo_npm_native_bootstrap_args agy").stdout == ""
    assert _lib("ascendo_npm_native_bootstrap_args agent").stdout == ""
    assert _lib("ascendo_npm_native_installer_env codex").stdout == "CODEX_NON_INTERACTIVE=1"
    assert _lib("ascendo_npm_native_installer_env claude").stdout == ""


@pytest.mark.parametrize("raw,expected", [("10", "360"), ("abc", "360"), ("", "360"),
                                          ("500", "500"), ("60", "60")])
def test_installer_timeout_knob(raw: str, expected: str) -> None:
    res = _lib("ascendo_npm_native_installer_timeout",
               {"ASCENDO_NATIVE_INSTALLER_TIMEOUT": raw})
    assert res.stdout.strip() == expected


def test_installer_timeout_default_is_360() -> None:
    e = {k: "" for k in ("ASCENDO_NATIVE_INSTALLER_TIMEOUT",
                         "ASCENDO_NPM_NATIVE_INSTALL_TIMEOUT",
                         "MAC_UPDATE_NATIVE_INSTALLER_TIMEOUT")}
    assert _lib("ascendo_npm_native_installer_timeout", e).stdout.strip() == "360"


def test_fetch_rejects_http_and_leaves_no_dest(env: Env, tmp_path: Path) -> None:
    dest = tmp_path / "dl"
    res = _lib(f'PATH="{env.bin}:$PATH" ascendo_npm_fetch_installer http://x.test/i.sh "{dest}"')
    assert res.returncode != 0 and not dest.exists()
    assert _read(env.curl_log) == "", "http:// must be rejected before curl runs"


def test_fetch_failure_and_empty_body_leave_no_dest(env: Env, tmp_path: Path) -> None:
    dest = tmp_path / "dl"
    res = _lib(f'PATH="{env.bin}:$PATH" ascendo_npm_fetch_installer https://x.test/i.sh "{dest}"')
    assert res.returncode != 0 and not dest.exists()
    env.serve("https://x.test/i.sh", "")
    res = _lib(f'PATH="{env.bin}:$PATH" ascendo_npm_fetch_installer https://x.test/i.sh "{dest}"')
    assert res.returncode != 0 and not dest.exists()
    assert not Path(str(dest) + ".part").exists()


def test_fetch_enforces_size_cap(env: Env, tmp_path: Path) -> None:
    dest = tmp_path / "dl"
    env.serve("https://x.test/big.sh", "x" * 5000)
    res = _lib(f'PATH="{env.bin}:$PATH" ascendo_npm_fetch_installer https://x.test/big.sh "{dest}" 60 1000')
    assert res.returncode != 0 and not dest.exists()


def test_fetch_success_writes_dest(env: Env, tmp_path: Path) -> None:
    dest = tmp_path / "dl"
    env.serve("https://x.test/ok.sh", "echo hi\n")
    res = _lib(f'PATH="{env.bin}:$PATH" ascendo_npm_fetch_installer https://x.test/ok.sh "{dest}"')
    assert res.returncode == 0, res.stderr
    assert dest.read_text() == "echo hi\n"


def _cli_file(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "cli"
    p.write_text(f"#!/bin/sh\n{body}\n")
    p.chmod(0o755)
    return p


@pytest.mark.parametrize("display,body,rc,out", [
    ("codex-cli", "echo 'codex-cli 0.153.4'", 0, "0.153.4"),
    ("claude-code", "echo '2.1.5 (Claude Code)'", 0, "2.1.5"),
    ("agy-cli", "echo 'agy 1.2.3'", 0, "1.2.3"),
    ("cursor-agent", "echo '2026.05.01-abc'", 0, "2026.05.01-abc"),
    ("codex-cli", "echo 'v0.9.0'", 0, "0.9.0"),
    ("codex-cli", "echo 'codex-cli'", 1, "?"),       # product name only
    ("codex-cli", "exit 1", 1, "?"),                  # does not run
    ("codex-cli", "echo ''", 1, "?"),                 # empty output
])
def test_report_cli_version_or_fail(tmp_path: Path, display, body, rc, out) -> None:
    p = _cli_file(tmp_path, body)
    res = _lib(f'ascendo_npm_report_cli_version_or_fail "{display}" "{p}"')
    assert res.returncode == rc, (res.stdout, res.stderr)
    assert res.stdout.strip() == out


def test_report_cli_version_missing_binary(tmp_path: Path) -> None:
    res = _lib(f'ascendo_npm_report_cli_version_or_fail codex-cli "{tmp_path}/nope"')
    assert res.returncode == 1 and res.stdout.strip() == "?"
