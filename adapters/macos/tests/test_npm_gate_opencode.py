"""npm category: only-installed gate, OpenCode native-first, sanitising, stdin hygiene.

Task 8 of the macOS_updates parity plan (cli.md P5, P6, P8, P9, row 17 plus the
upstream v1.5.3/v1.5.4 amendments T8-a..T8-e, F4):

  * a CLI / managed Node / bun that is not installed is `skipped` unless
    ASCENDO_BOOTSTRAP_CLI=1 or it is named in --filter; verify does not fail a
    not-installed CLI; `brew install node` never happens unasked;
  * OpenCode is NATIVE-FIRST (`opencode upgrade --method curl` or the downloaded
    https://opencode.ai/install --no-modify-path with a hidden PATH) and runs in
    pass 1 BEFORE Node/npm; the npm distribution is only a guarded fallback;
  * recorded output tails are credential-redacted;
  * every mutating child gets stdin </dev/null (an installer that reads stdin
    must not eat the remaining manifest rows).

Everything runs against fakes: temp HOME, a hermetic PATH (the developer's real
claude/codex/agy/opencode are never visible), fake curl/npm/CLIs, a one-row
manifest via ASCENDO_NPM_MANIFEST. Nothing real is installed or updated.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ADAPTER_ROOT / "scripts" / "npm"
APPLY = SCRIPTS / "apply.sh"
CHECK = SCRIPTS / "check.sh"
PLAN = SCRIPTS / "plan.sh"
VERIFY = SCRIPTS / "verify.sh"
LIB = ADAPTER_ROOT / "lib" / "ascendo_npm.sh"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")

OPENCODE_ROW = "opencode-cli|opencode-ai|self-update|opencode|opencode"
CODEX_ROW = "codex-cli|@openai/codex|native-installer||codex"
CLAUDE_ROW = "claude-code|@anthropic-ai/claude-code|native-installer||claude"
AGY_ROW = "agy-cli|agy|native-installer||agy"
NODE_ROW = "node|node|native-node|node|node"
NPM_ROW = "npm|npm|npm||npm"
PNPM_ROW = "pnpm|pnpm|npm||pnpm"
BUN_ROW = "bun|bun|native-bun|bun|bun"

_FAKE_NPM = r"""#!/bin/sh
echo "npm $*" >> "$FAKE_EVENTS"
slug() { printf '%s' "$1" | tr '/@' '__'; }
case "$1" in
  --version) echo 10.0.0 ;;
  outdated) echo '{}' ;;
  view)
    f="$FAKE_STATE/view_$(slug "$2")"
    if [ -f "$f" ]; then cat "$f"; else echo 1.0.0; fi ;;
  ls)
    printf '{"dependencies":{'; first=1
    for f in "$FAKE_STATE"/pkg_*; do
      [ -f "$f" ] || continue
      read n v < "$f"
      [ "$first" = 1 ] || printf ','
      first=0
      printf '"%s":{"version":"%s"}' "$n" "$v"
    done
    printf '}}\n' ;;
  install)
    cat >/dev/null          # an unguarded stdin would eat the manifest here
    for a in "$@"; do last="$a"; done
    name="${last%@latest}"
    f="$FAKE_STATE/view_$(slug "$name")"
    if [ -f "$f" ]; then ver="$(cat "$f")"; else ver=1.0.0; fi
    echo "$name $ver" > "$FAKE_STATE/pkg_$(slug "$name")"
    if [ -n "${FAKE_NPM_BIN_PATH:-}" ]; then
      mkdir -p "$(dirname "$FAKE_NPM_BIN_PATH")"
      printf '#!/bin/sh\ncase "$1" in --version|-v) [ %s -eq 0 ] && echo 1.0.0; exit %s ;; esac\nexit 0\n' "${FAKE_NPM_BIN_RC:-0}" "${FAKE_NPM_BIN_RC:-0}" > "$FAKE_NPM_BIN_PATH"
      chmod 755 "$FAKE_NPM_BIN_PATH"
    fi
    [ -n "${FAKE_NPM_FIXES_STUB:-}" ] && rm -f "$FAKE_NPM_FIXES_STUB"
    echo "NPM_INSTALL_ENV ignore_scripts=${NPM_CONFIG_IGNORE_SCRIPTS:-unset}" >> "$FAKE_EVENTS"
    ;;
  cache) ;;
esac
exit 0
"""


class Env:
    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.home = tmp / "home"
        self.home.mkdir()
        self.bin = tmp / "bin"
        self.bin.mkdir()
        self.tools = tmp / "tools"          # hermetic PATH: only python3 borrowed
        self.tools.mkdir()
        py = shutil.which("python3")
        assert py
        (self.tools / "python3").symlink_to(py)
        self.fixtures = tmp / "fixtures"
        self.fixtures.mkdir()
        self.state = tmp / "npmstate"
        self.state.mkdir()
        self.events = tmp / "events.log"
        self.events.touch()
        self.curl_log = tmp / "curl.log"
        self.curl_log.touch()
        self.out = tmp / "out"
        self.toolchain = tmp / "toolchain"
        self.prefix = self.toolchain / "npm-global"
        self.nprefix = self.toolchain / "node"
        self._fake_curl()
        npm = self.bin / "npm"
        npm.write_text(_FAKE_NPM)
        npm.chmod(0o755)
        # A real `brew install node` must never be reachable from a test: this
        # fake (failing, logging) is the only brew on the hermetic PATH.
        self.script(self.bin / "brew",
                    f'#!/bin/sh\ncat >/dev/null\necho "brew $*" >> "{self.events}"\nexit 1\n')
        self.extra_path: list[Path] = []

    # ---- fakes ---------------------------------------------------------
    def _fake_curl(self) -> None:
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

    def serve(self, url: str, body: str) -> None:
        key = url.replace("/", "_").replace(":", "_").replace(".", "_")
        (self.fixtures / key).write_text(body)

    def script(self, path: Path, body: str) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
        path.chmod(0o755)
        return path

    def cli(self, path: Path, name: str, version: str, *, sub_rc: int = 0,
            sub_to: str | None = None, version_rc: int = 0, extra_out: str = "",
            log_version: bool = False, sub_removes_self: bool = False,
            version_hang: bool = False) -> Path:
        """A fake CLI. Version lives in `<real path>.ver`; `update` / `upgrade`
        are logged to events.log (with env bits), may bump the version, may fail."""
        bump = f'echo "{sub_to}" > "$self.ver"' if sub_to else ":"
        if sub_removes_self:
            bump = 'rm -f "$self"'
        # log_version: record EVERY invocation (also --version) so a test can
        # prove a command was never executed at all.
        pre = f'echo "{name} $*" >> "{self.events}"\n' if log_version else ""
        hang = "sleep 300\n" if version_hang else ""
        body = f"""#!/bin/sh
self="$0"; [ -L "$self" ] && self="$(readlink "$self")"
{pre}case "$1" in
  --version|-v)
    {hang}[ {version_rc} -eq 0 ] || exit {version_rc}
    printf '%s\\n' "$(cat "$self.ver" 2>/dev/null)"; exit 0 ;;
esac
echo "{name} $*" >> "{self.events}"
echo "ENV {name} allow_scripts=${{NPM_CONFIG_ALLOW_SCRIPTS:-unset}} prefix=${{NPM_CONFIG_PREFIX:-unset}} npm_config_prefix=${{npm_config_prefix:-unset}} codex_ni=${{CODEX_NON_INTERACTIVE:-unset}}" >> "{self.events}"
cat >/dev/null
{bump}
[ ! -f "$self.out" ] || cat "$self.out" >&2
[ {sub_rc} -eq 0 ] || {{ echo "{name} subcommand exploded" >&2; exit {sub_rc}; }}
exit 0
"""
        self.script(path, body)
        Path(str(path) + ".ver").write_text(version + "\n")
        if extra_out:
            Path(str(path) + ".out").write_text(extra_out + "\n")
        return path

    # ---- layout helpers -------------------------------------------------
    @property
    def local_bin(self) -> Path:
        return self.home / ".local" / "bin"

    def native_cli(self, name: str, version: str, root: str, **kw) -> Path:
        """Vendor-native install: real file inside the vendor root, linked from
        ~/.local/bin (cli_duplicates.supported_native_path accepts it)."""
        real = self.home / root / name
        self.cli(real, name, version, **kw)
        link = self.local_bin / name
        link.parent.mkdir(parents=True, exist_ok=True)
        if link.exists() or link.is_symlink():
            link.unlink()
        link.symlink_to(real)
        return link

    def claude(self, version: str = "2.0.0", **kw) -> Path:
        return self.native_cli("claude", version, ".local/share/claude/versions/2.0.0", **kw)

    def codex(self, version: str = "0.1.0", **kw) -> Path:
        return self.native_cli("codex", version,
                               ".codex/packages/standalone/releases/0.1.0", **kw)

    def oc_native(self, version: str, *, link: bool = True, **kw) -> Path:
        real = self.home / ".opencode" / "bin" / "opencode"
        self.cli(real, "opencode", version, **kw)
        if link:
            self.local_bin.mkdir(parents=True, exist_ok=True)
            (self.local_bin / "opencode").symlink_to(real)
        return real

    def oc_npm(self, version: str, *, package_dir: bool = True, **kw) -> Path:
        """The npm distribution of OpenCode in the managed prefix."""
        p = self.prefix / "bin" / "opencode"
        self.cli(p, "opencode-npm", version, **kw)
        if package_dir:
            d = self.prefix / "lib" / "node_modules" / "opencode-ai"
            d.mkdir(parents=True, exist_ok=True)
            (d / "package.json").write_text(json.dumps({"name": "opencode-ai"}))
        return p

    def oc_installer(self, version: str, *, rc: int = 0) -> None:
        """Fake https://opencode.ai/install: refuses a missing --no-modify-path,
        exits 8 if ANY opencode is still visible on PATH (the real script exits
        early then), otherwise drops the native binary."""
        self.serve("https://opencode.ai/install", f"""#!/bin/bash
echo "opencode-installer $*" >> "{self.events}"
test "$1" = --no-modify-path || exit 9
command -v opencode >/dev/null && exit 8
mkdir -p "$HOME/.opencode/bin"
cat > "$HOME/.opencode/bin/opencode" <<'CLI'
#!/bin/sh
self="$0"; [ -L "$self" ] && self="$(readlink "$self")"
case "$1" in --version|-v) cat "$self.ver"; exit 0 ;; esac
echo "opencode $*" >> "{self.events}"
exit 0
CLI
chmod 755 "$HOME/.opencode/bin/opencode"
echo {version} > "$HOME/.opencode/bin/opencode.ver"
exit {rc}
""")

    def installer(self, cmd: str, url: str, version: str) -> None:
        self.serve(url, f"""#!/bin/sh
echo "installer {cmd} $*" >> "{self.events}"
cat >/dev/null
mkdir -p "{self.home}/.local/bin"
cat > "{self.home}/.local/bin/{cmd}" <<'CLI'
#!/bin/sh
case "$1" in --version|-v) cat "$0.ver"; exit 0 ;; esac
exit 0
CLI
chmod 755 "{self.home}/.local/bin/{cmd}"
echo "{version}" > "{self.home}/.local/bin/{cmd}.ver"
exit 0
""")

    # ---- running --------------------------------------------------------
    def manifest(self, rows: list[str]) -> Path:
        m = self.tmp / "manifest.txt"
        m.write_text("display_name|package_name|method|brew_formula|command\n"
                     + "\n".join(rows) + "\n")
        return m

    def run(self, script: Path, rows: list[str], *extra: str,
            env_extra: dict | None = None, timeout: float = 180):
        path = ":".join([str(self.bin), *map(str, self.extra_path), str(self.tools),
                         "/usr/bin:/bin:/usr/sbin:/sbin"])
        env = dict(os.environ)
        # a developer's own ASCENDO_* / MAC_UPDATE_* toggles must not leak in
        for k in list(env):
            if k.startswith(("ASCENDO_", "MAC_UPDATE_")) or k == "BUN_INSTALL":
                del env[k]
        env.update({
            "HOME": str(self.home),
            "PATH": path,
            "MAC_UPDATE_TOOLCHAIN_HOME": str(self.toolchain),
            "ASCENDO_NPM_MANIFEST": str(self.manifest(rows)),
            "ASCENDO_KILL_AFTER": "1",
            "ASCENDO_NPM_BREW_DIRS": "",     # do not re-prefix the real Homebrew bin
            "FAKE_EVENTS": str(self.events),
            "FAKE_STATE": str(self.state),
        })
        env.update(env_extra or {})
        rid = str(uuid.uuid4())
        res = subprocess.run(
            ["bash", str(script), "--run-id", rid, "--trigger", "cli",
             "--profile", "default", "--output-dir", str(self.out), *extra],
            capture_output=True, text=True, env=env, check=False,
            stdin=subprocess.DEVNULL, timeout=timeout)
        phase = script.stem
        sc = self.out / rid / f"{phase}__npm.json"
        res.sidecar = json.loads(sc.read_text()) if sc.exists() else None  # type: ignore[attr-defined]
        return res

    # ---- state ----------------------------------------------------------
    def set_npm_pkg(self, name: str, version: str, *, listed: bool = True,
                    registry: str | None = None) -> None:
        slug = name.replace("/", "_").replace("@", "_")
        if listed:
            (self.state / f"pkg_{slug}").write_text(f"{name} {version}\n")
        if registry is not None:
            (self.state / f"view_{slug}").write_text(registry + "\n")

    def registry(self, name: str, version: str) -> None:
        self.set_npm_pkg(name, "", listed=False, registry=version)

    def managed_pkg_dir(self, pkg: str) -> None:
        d = self.prefix / "lib" / "node_modules" / pkg
        d.mkdir(parents=True, exist_ok=True)
        (d / "package.json").write_text("{}")

    def ev(self) -> str:
        return self.events.read_text()

    def curl(self) -> str:
        return self.curl_log.read_text()


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


def _status(res, display: str) -> str:
    return _item(res, display)["status"]


# =============================================================================
# P5: only-installed gate
# =============================================================================

def test_missing_native_cli_is_skipped_without_bootstrap(env: Env) -> None:
    env.installer("codex", "https://chatgpt.com/codex/install.sh", "9.9.9")
    res = env.run(APPLY, [CODEX_ROW])
    assert res.returncode == 0, res.stderr
    assert _status(res, "codex-cli") == "skipped"
    assert "not installed" in _msgs(res)
    assert env.curl() == "", "installer must not be downloaded"
    assert not (env.local_bin / "codex").exists()


def test_bootstrap_env_installs_missing_native_cli(env: Env) -> None:
    env.installer("codex", "https://chatgpt.com/codex/install.sh", "9.9.9")
    res = env.run(APPLY, [CODEX_ROW], env_extra={"ASCENDO_BOOTSTRAP_CLI": "1"})
    assert _status(res, "codex-cli") == "success"
    assert (env.local_bin / "codex").exists()


def test_naming_the_cli_in_filter_installs_it(env: Env) -> None:
    env.installer("codex", "https://chatgpt.com/codex/install.sh", "9.9.9")
    res = env.run(APPLY, [CODEX_ROW], "--filter", "codex-cli")
    assert _status(res, "codex-cli") == "success"


def test_other_filter_does_not_bootstrap(env: Env) -> None:
    env.installer("codex", "https://chatgpt.com/codex/install.sh", "9.9.9")
    res = env.run(APPLY, [CODEX_ROW, CLAUDE_ROW], "--filter", "codex-cli,gemini-cli")
    assert _status(res, "codex-cli") == "success"
    assert not any(i["id"] == "claude-code" for i in res.sidecar["items"])


def test_npm_package_absent_from_managed_prefix_is_skipped(env: Env) -> None:
    res = env.run(APPLY, [PNPM_ROW])
    assert _status(res, "pnpm") == "skipped"
    assert "install" not in env.ev(), env.ev()


def test_npm_package_present_in_managed_prefix_is_updated(env: Env) -> None:
    env.managed_pkg_dir("pnpm")
    env.set_npm_pkg("pnpm", "1.0.0", registry="2.0.0")
    res = env.run(APPLY, [PNPM_ROW])
    it = _item(res, "pnpm")
    assert it["status"] == "success", _msgs(res)
    assert it["target_version"] == "2.0.0"
    assert "npm install -g pnpm" in env.ev()


def test_npm_package_named_in_filter_is_installed(env: Env) -> None:
    env.registry("pnpm", "2.0.0")
    res = env.run(APPLY, [PNPM_ROW], "--filter", "pnpm")
    assert _status(res, "pnpm") == "success"
    assert "npm install -g pnpm" in env.ev()


def test_npm_install_with_broken_binary_is_failed_not_success(env: Env) -> None:
    """`npm ls` reads package.json: a stub whose postinstall was skipped looks
    installed. The binary must run."""
    env.managed_pkg_dir("pnpm")
    env.set_npm_pkg("pnpm", "1.0.0", registry="2.0.0")
    res = env.run(APPLY, [PNPM_ROW], env_extra={
        "FAKE_NPM_BIN_PATH": str(env.prefix / "bin" / "pnpm"), "FAKE_NPM_BIN_RC": "1"})
    assert _status(res, "pnpm") == "failed"
    assert "--version" in _msgs(res)


def test_npm_missing_entirely_is_skipped_not_failed(env: Env) -> None:
    (env.bin / "npm").unlink()
    res = env.run(APPLY, [PNPM_ROW])
    assert _status(res, "pnpm") == "skipped"


def test_bun_absent_is_skipped_and_installer_not_downloaded(env: Env) -> None:
    res = env.run(APPLY, [BUN_ROW])
    assert _status(res, "bun") == "skipped"
    assert "bun.sh" not in env.curl()
    assert not (env.home / ".bun").exists()


def _bun(env: Env, version: str, *, to: str | None = None, rc: int = 0) -> Path:
    return env.cli(env.home / ".bun" / "bin" / "bun", "bun", version, sub_to=to, sub_rc=rc)


def test_installed_bun_uses_bun_upgrade(env: Env) -> None:
    _bun(env, "1.0.0", to="2.0.0")
    env.serve("https://github.com/oven-sh/bun/releases/latest",
              "HTTP/2 302\r\nlocation: https://github.com/oven-sh/bun/releases/tag/bun-v2.0.0\r\n")
    res = env.run(APPLY, [BUN_ROW])
    it = _item(res, "bun")
    assert it["status"] == "success", _msgs(res)
    assert (it["current_version"], it["target_version"]) == ("2.0.0", "2.0.0")
    assert "bun upgrade" in env.ev()
    assert "bun.sh/install" not in env.curl()


def test_failed_bun_upgrade_is_failed(env: Env) -> None:
    _bun(env, "1.0.0", rc=3)
    env.serve("https://github.com/oven-sh/bun/releases/latest",
              "HTTP/2 302\r\nlocation: https://github.com/oven-sh/bun/releases/tag/bun-v2.0.0\r\n")
    res = env.run(APPLY, [BUN_ROW])
    assert _status(res, "bun") == "failed"
    assert "exited 3" in _msgs(res)


def test_bun_bootstrap_downloads_installer_never_pipes(env: Env) -> None:
    env.serve("https://bun.sh/install", f"""#!/bin/bash
echo "bun-installer BUN_INSTALL=$BUN_INSTALL" >> "{env.events}"
cat >/dev/null
mkdir -p "$BUN_INSTALL/bin"
printf '#!/bin/sh\\necho 1.5.0\\n' > "$BUN_INSTALL/bin/bun"
chmod 755 "$BUN_INSTALL/bin/bun"
""")
    res = env.run(APPLY, [BUN_ROW], env_extra={"ASCENDO_BOOTSTRAP_CLI": "1"})
    assert _status(res, "bun") == "success", _msgs(res)
    assert f"BUN_INSTALL={env.home}/.bun" in env.ev()


def test_node_row_skipped_without_managed_toolchain_and_no_brew_install(env: Env) -> None:
    """Managed Node absent: the row is skipped and `brew install node` is never
    run unasked (F6: native CLIs still update)."""
    (env.bin / "npm").unlink()
    env.claude("2.0.0", sub_to="2.1.0")
    res = env.run(APPLY, [NODE_ROW, NPM_ROW, CLAUDE_ROW])
    assert _status(res, "node") == "skipped"
    assert "Managed Node toolchain not installed" in _msgs(res)
    assert _status(res, "claude-code") == "success"
    ev = env.ev()
    assert "claude update" in ev
    assert not [l for l in ev.splitlines() if l.startswith(("brew ", "npm "))], ev


def test_node_bootstrap_when_asked_may_use_brew(env: Env) -> None:
    """With bootstrap explicitly allowed the brew fallback still exists (and is
    stdin-safe)."""
    (env.bin / "npm").unlink()
    res = env.run(APPLY, [NODE_ROW], "--filter", "node")
    assert _status(res, "node") == "failed"
    assert "brew install node" in env.ev()


def test_verify_does_not_fail_a_not_installed_cli(env: Env) -> None:
    res = env.run(VERIFY, [CODEX_ROW, OPENCODE_ROW])
    assert _status(res, "codex-cli") == "skipped"
    assert _status(res, "opencode-cli") == "skipped"
    assert not any(i["status"] == "failed" for i in res.sidecar["items"])


def test_verify_fails_not_installed_cli_when_bootstrap_allowed(env: Env) -> None:
    res = env.run(VERIFY, [CODEX_ROW], env_extra={"ASCENDO_BOOTSTRAP_CLI": "1"})
    assert _status(res, "codex-cli") == "failed"


def test_plan_omits_missing_unless_bootstrap_allowed(env: Env) -> None:
    res = env.run(PLAN, [CODEX_ROW, PNPM_ROW])
    assert res.sidecar["items"] == []
    res = env.run(PLAN, [CODEX_ROW], env_extra={"ASCENDO_BOOTSTRAP_CLI": "1"})
    assert _status(res, "codex-cli") == "missing"


def test_check_still_reports_missing_honestly(env: Env) -> None:
    res = env.run(CHECK, [CODEX_ROW])
    assert _status(res, "codex-cli") == "missing"


# ---- PATH-only CLIs (Task 7 carry) and F4 ----------------------------------

def test_path_only_agy_is_updated_in_place_never_given_a_second_copy(env: Env) -> None:
    """A CLI found only on PATH (not ~/.local/bin) is updated through its own
    subcommand; the vendor installer must not drop a second copy."""
    other = env.tmp / "elsewhere"
    env.cli(other / "agy", "agy", "1.0.0", sub_to="1.1.0")
    env.extra_path.append(other)
    env.installer("agy", "https://antigravity.google/cli/install.sh", "9.9.9")
    res = env.run(APPLY, [AGY_ROW])
    it = _item(res, "agy-cli")
    assert it["status"] == "success", _msgs(res)
    assert "agy update" in env.ev()
    assert "installer agy" not in env.ev()
    assert not (env.local_bin / "agy").exists()


def test_path_only_agy_failed_update_does_not_install_second_copy(env: Env) -> None:
    other = env.tmp / "elsewhere"
    env.cli(other / "agy", "agy", "1.0.0", sub_rc=2)
    env.extra_path.append(other)
    env.installer("agy", "https://antigravity.google/cli/install.sh", "9.9.9")
    res = env.run(APPLY, [AGY_ROW])
    assert _status(res, "agy-cli") == "failed"
    assert "installer agy" not in env.ev()
    assert not (env.local_bin / "agy").exists()


def test_path_only_agy_failed_update_and_dead_version_does_not_install_second_copy(env: Env) -> None:
    other = env.tmp / "elsewhere"
    env.cli(other / "agy", "agy", "1.0.0", sub_rc=2, version_rc=1,
            extra_out="//registry.npmjs.org/:_authToken=abc123")
    env.extra_path.append(other)
    env.installer("agy", "https://antigravity.google/cli/install.sh", "9.9.9")
    res = env.run(APPLY, [AGY_ROW])
    assert _status(res, "agy-cli") == "failed"
    assert "installer agy" not in env.ev()
    assert env.curl() == ""
    assert not (env.local_bin / "agy").exists()
    assert "abc123" not in _msgs(res) and "REDACTED" in _msgs(res)


def test_npm_wrapper_symlinked_into_local_bin_is_not_updated_as_native(env: Env) -> None:
    """F4: only a realpath inside the vendor root is native."""
    wrapper = env.prefix / "lib" / "node_modules" / "@anthropic-ai" / "claude-code" / "cli.js"
    env.cli(wrapper, "claude-npm-wrapper", "1.0.0")
    env.local_bin.mkdir(parents=True)
    (env.local_bin / "claude").symlink_to(wrapper)
    env.installer("claude", "https://claude.ai/install.sh", "9.9.9")
    res = env.run(APPLY, [CLAUDE_ROW])
    assert _status(res, "claude-code") == "skipped"
    assert "unverified installation origin" in _msgs(res)
    assert "claude-npm-wrapper" not in env.ev(), "the wrapper must not be executed (beyond --version)"
    assert env.curl() == ""


def test_homebrew_style_codex_on_path_is_retained_not_duplicated(env: Env) -> None:
    other = env.tmp / "homebrew" / "bin"
    env.cli(other / "codex", "brew-codex", "0.9.0")
    env.extra_path.append(other)
    env.installer("codex", "https://chatgpt.com/codex/install.sh", "9.9.9")
    res = env.run(APPLY, [CODEX_ROW])
    assert _status(res, "codex-cli") == "skipped"
    assert "brew-codex" not in env.ev()
    assert env.curl() == ""
    assert not (env.local_bin / "codex").exists()


# =============================================================================
# P8: sanitised tails
# =============================================================================

def _sanitize(text: str) -> str:
    res = subprocess.run(["bash", "-c", f'. "{LIB}"; ascendo_npm_sanitize_output'],
                         input=text, capture_output=True, text=True, check=True)
    return res.stdout


def test_sanitizer_redacts_tokens_auth_and_url_credentials() -> None:
    out = _sanitize("//registry.npmjs.org/:_authToken=abc123\n"
                    "Authorization: Bearer sekrit\n"
                    "npm_token = xyz\n"
                    "password: hunter2\n"
                    "https://user:pw@host/x\n"
                    "plain line stays\n")
    assert "abc123" not in out and "sekrit" not in out and "xyz" not in out
    assert "hunter2" not in out and "pw@host" not in out
    assert out.count("[REDACTED sensitive diagnostic line]") == 4
    assert "[REDACTED URL credentials]" in out
    assert "plain line stays" in out


def test_sanitizer_caps_lines_at_500_chars() -> None:
    out = _sanitize("x" * 900 + "\n")
    assert len(out.strip()) == 500


def test_failed_update_tail_in_sidecar_is_redacted(env: Env) -> None:
    long_line = "L" * 700
    env.claude("2.0.0", sub_rc=3,
               extra_out=("//registry.npmjs.org/:_authToken=abc123\n"
                          "fetching https://user:pw@host/x now\n"
                          f"{long_line}"))
    res = env.run(APPLY, [CLAUDE_ROW])
    assert _status(res, "claude-code") == "failed"
    text = _msgs(res)
    assert "abc123" not in text and "pw@host" not in text
    assert "[REDACTED" in text
    long_lines = [l for l in text.splitlines() if l and set(l) == {"L"}]
    assert long_lines, text
    assert max(len(l) for l in long_lines) <= 500, [len(l) for l in long_lines]


# =============================================================================
# P9: stdin hygiene and the manifest seam
# =============================================================================

def test_installers_cannot_eat_the_manifest(env: Env) -> None:
    """Three npm rows; the fake `npm install` drains stdin. All three must be
    processed (a child that inherits the manifest pipe would eat the rest)."""
    rows = ["pnpm|pnpm|npm||pnpm",
            "gemini-cli|@google/gemini-cli|npm||gemini",
            "qwen-code|@qwen-code/qwen-code|npm||qwen"]
    for pkg in ("pnpm", "@google/gemini-cli", "@qwen-code/qwen-code"):
        env.managed_pkg_dir(pkg)
        env.set_npm_pkg(pkg, "1.0.0", registry="2.0.0")
    res = env.run(APPLY, rows)
    assert [i["id"] for i in res.sidecar["items"]] == ["pnpm", "gemini-cli", "qwen-code"]
    assert all(i["status"] == "success" for i in res.sidecar["items"]), _msgs(res)


def test_manifest_seam_overrides_shipped_manifest(env: Env) -> None:
    res = env.run(CHECK, [CODEX_ROW])
    assert [i["id"] for i in res.sidecar["items"]] == ["codex-cli"]


def test_shipped_manifest_opencode_is_self_update_and_documented() -> None:
    text = (ADAPTER_ROOT / "config" / "npm_global_clis.txt").read_text()
    assert OPENCODE_ROW in text.splitlines()
    header = text.split("\ndisplay_name|package_name|method|brew_formula|command\n")[0]
    assert "self-update" in header and "ASCENDO_BOOTSTRAP_CLI" in header


# =============================================================================
# helper units
# =============================================================================

def _bash(code: str, **env_vars: str) -> subprocess.CompletedProcess:
    e = dict(os.environ)
    e.update(env_vars)
    return subprocess.run(["bash", "-c", f'. "{LIB}"; {code}'], capture_output=True,
                          text=True, env=e, check=False)


def test_bootstrap_allowed_matrix() -> None:
    assert _bash('ascendo_npm_bootstrap_allowed x').returncode == 1
    assert _bash('ascendo_npm_bootstrap_allowed x', ASCENDO_BOOTSTRAP_CLI="1").returncode == 0
    assert _bash('ascendo_npm_bootstrap_allowed x', ASCENDO_BOOTSTRAP_CLI="yes").returncode == 0
    assert _bash('ascendo_npm_bootstrap_allowed x', ASCENDO_BOOTSTRAP_CLI="0").returncode == 1
    assert _bash('FILTER="a,codex-cli"; ascendo_npm_bootstrap_allowed codex-cli').returncode == 0
    assert _bash('FILTER="a,codex-cli"; ascendo_npm_bootstrap_allowed codex').returncode == 1
    assert _bash('FILTER=""; ascendo_npm_bootstrap_allowed codex-cli').returncode == 1


def test_preferred_path_brew_dirs_seam(tmp_path: Path) -> None:
    d = tmp_path / "brewbin"
    d.mkdir()
    res = _bash("ascendo_npm_preferred_path", HOME=str(tmp_path / "h"),
                MAC_UPDATE_TOOLCHAIN_HOME=str(tmp_path / "tc"), ASCENDO_NPM_BREW_DIRS=str(d),
                BUN_INSTALL=str(tmp_path / "nobun"))
    assert res.stdout == str(d)
    res = _bash("ascendo_npm_preferred_path", HOME=str(tmp_path / "h"),
                MAC_UPDATE_TOOLCHAIN_HOME=str(tmp_path / "tc"), ASCENDO_NPM_BREW_DIRS="",
                BUN_INSTALL=str(tmp_path / "nobun"))
    assert res.stdout == ""


def test_opencode_install_url() -> None:
    res = _bash("ascendo_npm_native_install_url opencode")
    assert res.stdout.strip() == "https://opencode.ai/install"


def test_repair_returns_2_when_binary_absent(tmp_path: Path) -> None:
    res = _bash(f'ascendo_npm_repair_broken_opencode "{tmp_path}" "{tmp_path}/nope"')
    assert res.returncode == 2


def test_version_newer_is_conservative() -> None:
    assert _bash('ascendo_npm_version_newer 1.2.3 1.2.2').returncode == 0
    assert _bash('ascendo_npm_version_newer 1.2.2 1.2.3').returncode == 1
    assert _bash('ascendo_npm_version_newer 1.2.3 1.2.3').returncode == 1
    assert _bash('ascendo_npm_version_newer "" 1.2.3').returncode == 1
    assert _bash('ascendo_npm_version_newer "?" 1.2.3').returncode == 1


# =============================================================================
# OpenCode: native-first (port of upstream NativeFirstTests)
# =============================================================================

def test_native_opencode_upgrades_with_curl_method_and_skips_npm_fallback(env: Env) -> None:
    env.oc_native("1.2.2", sub_to="1.2.3")
    env.oc_npm("1.2.3")          # an npm copy exists, must stay untouched
    res = env.run(APPLY, [OPENCODE_ROW])
    assert res.returncode == 0, res.stderr
    it = _item(res, "opencode-cli")
    assert it["status"] == "success", _msgs(res)
    assert (it["current_version"], it["target_version"]) == ("1.2.2", "1.2.3")
    ev = env.ev()
    assert "opencode upgrade --method curl" in ev
    assert "--method npm" not in ev and "opencode-npm" not in ev
    assert "npm install" not in ev


def test_native_opencode_unchanged_version_is_up_to_date(env: Env) -> None:
    env.oc_native("1.2.3")
    res = env.run(APPLY, [OPENCODE_ROW])
    assert _status(res, "opencode-cli") == "up_to_date"


def test_failed_native_migration_preserves_npm_fallback(env: Env) -> None:
    """Installer download fails (nothing served): no symlink is published and
    the existing npm install is updated through the guarded fallback."""
    npm = env.oc_npm("1.2.3", sub_to="1.2.4")
    res = env.run(APPLY, [OPENCODE_ROW])
    assert not (env.local_bin / "opencode").exists()
    assert npm.exists()
    it = _item(res, "opencode-cli")
    assert it["status"] == "success", _msgs(res)
    ev = env.ev()
    assert "opencode-npm upgrade --method npm" in ev
    assert "allow_scripts=opencode-ai" in ev
    assert f"prefix={env.prefix}" in ev and f"npm_config_prefix={env.prefix}" in ev
    assert "download failed" in _msgs(res)
    assert not (env.home / ".npmrc").exists()


def test_npm_fallback_requires_existing_package_dir_and_binary(env: Env) -> None:
    """The wrapper binary alone (no package dir) is not an npm install."""
    npm = env.oc_npm("1.2.3", package_dir=False, sub_to="1.2.4")
    npm_log = env.cli(env.prefix / "bin" / "opencode", "opencode-npm", "1.2.3",
                      sub_to="1.2.4", log_version=True)
    assert npm_log == npm
    res = env.run(APPLY, [OPENCODE_ROW])
    # Without the package dir the wrapper is NOT a verified (managed npm) origin:
    # skipped as unverified, never executed at all (T8-c beats T8-d).
    assert _status(res, "opencode-cli") == "skipped"
    assert "unverified_origin" in _msgs(res)
    assert "opencode-npm" not in env.ev()
    assert npm.exists()


def test_native_installed_but_failed_then_npm_exists_keeps_npm(env: Env) -> None:
    env.oc_native("1.2.2", sub_rc=5)
    env.oc_npm("1.2.3", sub_to="1.2.4")
    res = env.run(APPLY, [OPENCODE_ROW])
    assert _status(res, "opencode-cli") == "success"
    ev = env.ev()
    assert "opencode upgrade --method curl" in ev
    assert "opencode-npm upgrade --method npm" in ev
    # the fallback never touches the native command
    assert ev.count("opencode upgrade --method npm") == 0


def test_unknown_user_local_command_is_not_executed_or_replaced(env: Env) -> None:
    cmd = env.cli(env.local_bin / "opencode", "user-opencode", "1.2.3", log_version=True)
    env.oc_installer("9.9.9")
    res = env.run(APPLY, [OPENCODE_ROW])
    assert _status(res, "opencode-cli") == "skipped"
    assert "unverified installation origin; retaining existing distribution" in _msgs(res)
    assert "unverified_origin" in _msgs(res)
    assert "user-opencode" not in env.ev(), "never executed - not even --version"
    assert not cmd.is_symlink()
    assert env.curl() == "" and "opencode-installer" not in env.ev()


def test_dangling_user_local_link_is_retained(env: Env) -> None:
    env.local_bin.mkdir(parents=True)
    (env.local_bin / "opencode").symlink_to(env.home / "nowhere")
    env.oc_installer("9.9.9")
    res = env.run(APPLY, [OPENCODE_ROW])
    assert _status(res, "opencode-cli") == "skipped"
    assert (env.local_bin / "opencode").is_symlink()
    assert os.readlink(env.local_bin / "opencode") == str(env.home / "nowhere")
    assert env.curl() == ""


def test_unverified_local_command_with_npm_install_updates_only_the_npm_copy(env: Env) -> None:
    local = env.cli(env.local_bin / "opencode", "user-opencode", "1.0.0", log_version=True)
    env.oc_npm("1.2.3", sub_to="1.2.4")
    res = env.run(APPLY, [OPENCODE_ROW])
    assert _status(res, "opencode-cli") == "success"
    ev = env.ev()
    assert "opencode-npm upgrade --method npm" in ev
    assert "user-opencode" not in ev
    assert not local.is_symlink()


def test_older_native_release_retains_npm_and_does_not_publish_symlink(env: Env) -> None:
    env.oc_native("1.2.2", link=False)           # ~/.opencode/bin only, no publish yet
    env.oc_npm("1.2.3", sub_to="1.2.3")
    res = env.run(APPLY, [OPENCODE_ROW])
    assert not (env.local_bin / "opencode").exists()
    assert "older than the installed fallback" in _msgs(res)
    assert "opencode-npm upgrade --method npm" in env.ev()
    it = _item(res, "opencode-cli")
    assert (it["status"], it["current_version"], it["target_version"]) == ("up_to_date", "1.2.3", "1.2.3")


def test_older_native_without_npm_install_is_skipped_not_published(env: Env) -> None:
    other = env.tmp / "brew" / "bin"
    env.cli(other / "opencode", "brew-opencode", "1.2.3")
    env.extra_path.append(other)
    env.oc_native("1.2.2", link=False)
    # brew-owned opencode + the user asked for opencode explicitly => native
    # attempt runs, is older than the brew copy, and is not published.
    res = env.run(APPLY, [OPENCODE_ROW], "--filter", "opencode-cli",
                  env_extra={"ASCENDO_NPM_BREW_DIRS": str(other)})
    assert not (env.local_bin / "opencode").exists()
    assert _status(res, "opencode-cli") == "skipped"
    assert "older than the installed fallback" in _msgs(res)


def test_installer_migrates_equal_npm_version_hides_path_and_edits_no_profile(env: Env) -> None:
    npm = env.oc_npm("1.2.3")
    env.oc_installer("1.2.3")
    res = env.run(APPLY, [OPENCODE_ROW])
    assert res.returncode == 0, res.stderr
    link = env.local_bin / "opencode"
    assert link.is_symlink()
    assert os.readlink(link) == str(env.home / ".opencode" / "bin" / "opencode")
    assert npm.exists(), "migration itself does not delete the fallback"
    ev = env.ev()
    assert "opencode-installer --no-modify-path" in ev      # exit 8/9 would have failed it
    assert "opencode-npm upgrade" not in ev                 # native OK => npm fallback skipped
    assert _status(res, "opencode-cli") == "up_to_date"
    for profile in (".zshrc", ".zprofile", ".bashrc", ".bash_profile", ".profile"):
        assert not (env.home / profile).exists(), profile


def test_installer_failure_leaves_no_symlink_and_reports_failed(env: Env) -> None:
    env.oc_installer("1.2.3", rc=4)
    res = env.run(APPLY, [OPENCODE_ROW], env_extra={"ASCENDO_BOOTSTRAP_CLI": "1"})
    assert _status(res, "opencode-cli") == "failed"
    assert "exited 4" in _msgs(res)


def test_bootstrap_opencode_uses_native_installer_never_npm(env: Env) -> None:
    env.oc_installer("1.2.3")
    res = env.run(APPLY, [OPENCODE_ROW], env_extra={"ASCENDO_BOOTSTRAP_CLI": "1"})
    it = _item(res, "opencode-cli")
    assert it["status"] == "success", _msgs(res)
    assert it["target_version"] == "1.2.3"
    assert (env.local_bin / "opencode").is_symlink()
    assert "npm install" not in env.ev()
    assert "opencode.ai/install" in env.curl()


def test_filter_names_opencode_bootstraps_native(env: Env) -> None:
    env.oc_installer("1.2.3")
    res = env.run(APPLY, [OPENCODE_ROW], "--filter", "opencode-cli")
    assert _status(res, "opencode-cli") == "success"


def test_missing_opencode_without_bootstrap_is_skipped(env: Env) -> None:
    env.oc_installer("1.2.3")
    res = env.run(APPLY, [OPENCODE_ROW])
    assert _status(res, "opencode-cli") == "skipped"
    assert env.curl() == ""
    assert not (env.home / ".opencode").exists()


def test_dry_run_opencode_is_planned_once_and_touches_nothing(env: Env) -> None:
    env.oc_native("1.2.2", sub_to="1.2.3")
    res = env.run(APPLY, [OPENCODE_ROW, CODEX_ROW], "--dry-run")
    assert [i["status"] for i in res.sidecar["items"]].count("planned") == 2
    assert [i["id"] for i in res.sidecar["items"]].count("opencode-cli") == 1
    assert "upgrade" not in env.ev()


def test_native_work_precedes_node_and_npm(env: Env) -> None:
    """Pass 1 (claude, opencode native-first) runs before pass 2 (node, npm)
    even though the manifest lists node and npm first."""
    env.script(env.nprefix / "bin" / "node", "#!/bin/sh\necho v1.0.0\n")
    env.script(env.prefix / "bin" / "n", f'#!/bin/sh\necho "n $*" >> "{env.events}"\nexit 0\n')
    env.script(env.nprefix / "bin" / "n", "#!/bin/sh\necho 2.0.0\n")   # `n --lts` / `--latest`
    env.claude("2.0.0", sub_to="2.1.0")
    env.oc_native("1.2.2", sub_to="1.2.3")
    env.registry("npm", "11.0.0")
    res = env.run(APPLY, [NODE_ROW, NPM_ROW, CLAUDE_ROW, OPENCODE_ROW])
    assert res.returncode == 0, res.stderr
    ev = env.ev().splitlines()
    first_claude = next(i for i, l in enumerate(ev) if l.startswith("claude update"))
    first_oc = next(i for i, l in enumerate(ev) if l.startswith("opencode upgrade --method curl"))
    later = [i for i, l in enumerate(ev) if l.startswith("npm install") or l.startswith("n ")]
    assert later, ev
    assert max(first_claude, first_oc) < min(later), ev


# ---- broken npm stub: honesty + repair -----------------------------------------

def _broken_npm_opencode(env: Env, **kw) -> Path:
    p = env.oc_npm("1.2.3", **kw)
    marker = Path(str(p) + ".broken")
    marker.write_text("1")
    # --version fails while the marker exists
    p.write_text(p.read_text().replace(
        'case "$1" in\n  --version|-v)',
        f'case "$1" in\n  --version|-v)\n    [ -f "$self.broken" ] && exit 1', 1))
    return p


def test_broken_npm_stub_is_repaired_when_it_already_exists(env: Env) -> None:
    p = _broken_npm_opencode(env)
    env.set_npm_pkg("opencode-ai", "1.2.3", registry="1.2.3")
    res = env.run(APPLY, [OPENCODE_ROW], env_extra={
        "FAKE_NPM_FIXES_STUB": str(p) + ".broken"})
    assert _status(res, "opencode-cli") == "success", _msgs(res)
    ev = env.ev()
    assert f"npm install -g --prefix {env.prefix} --allow-scripts=opencode-ai opencode-ai@latest" in ev
    assert "NPM_INSTALL_ENV ignore_scripts=false" in ev


def test_stub_that_stays_broken_is_failed(env: Env) -> None:
    _broken_npm_opencode(env)
    res = env.run(APPLY, [OPENCODE_ROW])
    assert _status(res, "opencode-cli") == "failed"
    assert "--version" in _msgs(res)


# ---- check / plan / verify read the resolved command --------------------------

def test_check_reads_installed_from_binary_and_latest_from_registry(env: Env) -> None:
    env.oc_native("1.2.2")
    env.registry("opencode-ai", "1.2.3")
    res = env.run(CHECK, [OPENCODE_ROW])
    it = _item(res, "opencode-cli")
    assert (it["current_version"], it["target_version"], it["status"]) == ("1.2.2", "1.2.3", "planned")
    res = env.run(PLAN, [OPENCODE_ROW])
    assert _status(res, "opencode-cli") == "planned"


def test_check_flags_present_but_broken_command(env: Env) -> None:
    _broken_npm_opencode(env)
    env.registry("opencode-ai", "1.2.3")
    res = env.run(CHECK, [OPENCODE_ROW])
    assert _status(res, "opencode-cli") == "planned"
    assert "does not print a version" in _msgs(res)
    res = env.run(VERIFY, [OPENCODE_ROW])
    assert _status(res, "opencode-cli") == "failed"


def test_verify_success_when_native_is_current_or_newer(env: Env) -> None:
    env.oc_native("1.2.3")
    env.registry("opencode-ai", "1.2.3")
    assert _status(env.run(VERIFY, [OPENCODE_ROW]), "opencode-cli") == "success"
    env.oc_native("1.2.4", link=False)   # newer than the registry
    assert _status(env.run(VERIFY, [OPENCODE_ROW]), "opencode-cli") == "success"


# =============================================================================
# Fix round 1: T8-c beats T8-d in every phase (Ruling 16), brew-owned (Ruling 15),
# M1 repair rc 2, M8 bounded probes
# =============================================================================

UNVERIFIED = "unverified installation origin; retaining existing distribution"


def _unverified_local(env: Env) -> Path:
    return env.cli(env.local_bin / "opencode", "user-opencode", "1.2.3", log_version=True)


@pytest.mark.parametrize("script", [CHECK, PLAN, VERIFY])
def test_unverified_local_opencode_is_skipped_and_never_executed_in_every_phase(
        env: Env, script: Path) -> None:
    _unverified_local(env)
    env.registry("opencode-ai", "1.2.4")
    res = env.run(script, [OPENCODE_ROW])
    it = _item(res, "opencode-cli")
    assert it["status"] == "skipped"
    assert UNVERIFIED in _msgs(res) and "unverified_origin" in _msgs(res)
    assert "user-opencode" not in env.ev(), "an unverified command must never be executed"
    assert not (env.local_bin / "opencode").is_symlink()


@pytest.mark.parametrize("script", [CHECK, PLAN, VERIFY])
def test_unverified_path_only_opencode_is_skipped_and_never_executed(env: Env, script: Path) -> None:
    other = env.tmp / "mystery" / "bin"
    env.cli(other / "opencode", "mystery-opencode", "1.2.3", log_version=True)
    env.extra_path.append(other)
    res = env.run(script, [OPENCODE_ROW])
    assert _status(res, "opencode-cli") == "skipped"
    assert "unverified_origin" in _msgs(res)
    assert "mystery-opencode" not in env.ev()


@pytest.mark.parametrize("script", [CHECK, PLAN, VERIFY])
def test_installed_is_read_from_verified_origin_not_the_unverified_local(env: Env, script: Path) -> None:
    """Unverified ~/.local/bin/opencode + a managed npm wrapper: the wrapper is
    the (verified) origin the version is read from; the local file never runs."""
    _unverified_local(env)
    env.oc_npm("1.2.2")
    env.registry("opencode-ai", "1.2.3")
    res = env.run(script, [OPENCODE_ROW])
    assert "user-opencode" not in env.ev()
    if script is VERIFY:
        assert _status(res, "opencode-cli") == "failed"      # 1.2.2 != 1.2.3
    else:
        it = _item(res, "opencode-cli")
        assert (it["current_version"], it["target_version"], it["status"]) == ("1.2.2", "1.2.3", "planned")


def _brew_opencode(env: Env, version: str = "1.2.3") -> Path:
    brew = env.tmp / "homebrew" / "bin"
    env.cli(brew / "opencode", "brew-opencode", version)
    env.extra_path.append(brew)
    return brew


def test_brew_owned_opencode_is_skipped_and_no_native_copy_installed(env: Env) -> None:
    brew = _brew_opencode(env)
    env.oc_installer("9.9.9")
    res = env.run(APPLY, [OPENCODE_ROW], env_extra={"ASCENDO_NPM_BREW_DIRS": str(brew)})
    assert _status(res, "opencode-cli") == "skipped"
    assert "managed by Homebrew" in _msgs(res)
    assert env.curl() == ""
    assert "opencode-installer" not in env.ev() and "brew-opencode" not in env.ev()
    assert not (env.home / ".opencode").exists()
    assert not (env.local_bin / "opencode").exists()


@pytest.mark.parametrize("how", ["env", "filter"])
def test_brew_owned_opencode_gets_native_copy_only_when_asked(env: Env, how: str) -> None:
    brew = _brew_opencode(env)
    env.oc_installer("1.2.3")
    extra = {"ASCENDO_NPM_BREW_DIRS": str(brew)}
    args: list[str] = []
    if how == "env":
        extra["ASCENDO_BOOTSTRAP_CLI"] = "1"
    else:
        args = ["--filter", "opencode-cli"]
    res = env.run(APPLY, [OPENCODE_ROW], *args, env_extra=extra)
    assert _status(res, "opencode-cli") in ("up_to_date", "success"), _msgs(res)
    assert (env.local_bin / "opencode").is_symlink()
    assert "opencode-installer --no-modify-path" in env.ev()


def test_brew_owned_opencode_version_is_read_but_plan_and_verify_skip(env: Env) -> None:
    brew = _brew_opencode(env, "1.2.2")
    env.registry("opencode-ai", "1.2.3")
    extra = {"ASCENDO_NPM_BREW_DIRS": str(brew)}
    res = env.run(CHECK, [OPENCODE_ROW], env_extra=extra)
    it = _item(res, "opencode-cli")
    assert it["current_version"] == "1.2.2"         # reading --version of a brew-owned command is allowed
    assert "managed by Homebrew" in _msgs(res)
    for script in (PLAN, VERIFY):
        res = env.run(script, [OPENCODE_ROW], env_extra=extra)
        assert _status(res, "opencode-cli") == "skipped"
        assert "managed by Homebrew" in _msgs(res)


def test_brew_owned_opencode_plus_existing_npm_install_updates_only_the_npm_copy(env: Env) -> None:
    brew = _brew_opencode(env)
    env.oc_npm("1.2.3", sub_to="1.2.4")
    res = env.run(APPLY, [OPENCODE_ROW], env_extra={"ASCENDO_NPM_BREW_DIRS": str(brew)})
    assert _status(res, "opencode-cli") == "success"
    assert "opencode-npm upgrade --method npm" in env.ev()
    # the npm copy resolves first (managed prefix precedes Homebrew on PATH); the
    # brew command itself is never run beyond --version and never replaced
    assert not (env.home / ".opencode").exists()
    assert "brew-opencode" not in env.ev()


def test_origin_classification_units(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / ".opencode" / "bin").mkdir(parents=True)
    native = home / ".opencode" / "bin" / "opencode"
    native.write_text("x")
    brew = tmp_path / "brew" / "bin"
    brew.mkdir(parents=True)
    (brew / "opencode").write_text("x")
    cellar = tmp_path / "brew" / "Cellar" / "opencode" / "1" / "bin"
    cellar.mkdir(parents=True)
    (cellar / "opencode").write_text("x")
    elsewhere = tmp_path / "e" / "opencode"
    elsewhere.parent.mkdir()
    elsewhere.write_text("x")
    link = tmp_path / "link"
    link.symlink_to(cellar / "opencode")
    tc = tmp_path / "tc"
    env_vars = dict(HOME=str(home), MAC_UPDATE_TOOLCHAIN_HOME=str(tc),
                    ASCENDO_NPM_BREW_DIRS=str(brew))
    def origin(p: Path) -> str:
        return _bash(f'ascendo_npm_opencode_origin "{p}"', **env_vars).stdout.strip()
    assert origin(native) == "native"
    assert origin(brew / "opencode") == "brew"
    assert origin(link) == "brew"                      # realpath under <prefix>/Cellar
    assert origin(elsewhere) == "unverified"
    assert origin(tmp_path / "missing") == "unverified"


def test_repair_rc2_after_a_real_upgrade_is_failed_not_skipped(env: Env) -> None:
    """`upgrade --method npm` ran and the binary is gone afterwards: that is a
    failure, not "not installed"."""
    env.oc_npm("1.2.3", sub_removes_self=True)
    res = env.run(APPLY, [OPENCODE_ROW])
    assert _status(res, "opencode-cli") == "failed"
    assert "not installed" not in _msgs(res)
    assert "opencode-npm upgrade --method npm" in env.ev()


def _hermetic_bash(tmp_path: Path, code: str, **extra: str) -> tuple[subprocess.CompletedProcess, float]:
    import time
    tools = tmp_path / "tools"
    tools.mkdir(exist_ok=True)
    if not (tools / "python3").exists():
        (tools / "python3").symlink_to(shutil.which("python3"))
    e = dict(os.environ)
    e.update({"PATH": f"{tools}:/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(tmp_path / "home"),
              "MAC_UPDATE_TOOLCHAIN_HOME": str(tmp_path / "tc"), "ASCENDO_NPM_BREW_DIRS": "",
              "ASCENDO_KILL_AFTER": "1"})
    e.update(extra)
    t0 = time.monotonic()
    res = subprocess.run(["bash", "-c", f'. "{LIB}"; {code}'], capture_output=True, text=True,
                         env=e, check=False, timeout=60)
    return res, time.monotonic() - t0


def test_version_probe_is_bounded(tmp_path: Path) -> None:
    hang = tmp_path / "hang"
    hang.write_text("#!/bin/sh\nsleep 300\n")
    hang.chmod(0o755)
    res, took = _hermetic_bash(tmp_path, f'ascendo_npm_report_cli_version_or_fail x "{hang}"',
                               ASCENDO_CLI_VERSION_TIMEOUT="1")
    assert res.returncode == 1 and res.stdout.strip() == "?"
    assert took < 30, took


def test_repair_npm_install_is_bounded(tmp_path: Path) -> None:
    prefix = tmp_path / "prefix"
    (prefix / "bin").mkdir(parents=True)
    npm = prefix / "bin" / "npm"
    npm.write_text("#!/bin/sh\nsleep 300\n")
    npm.chmod(0o755)
    binary = tmp_path / "opencode"
    binary.write_text("#!/bin/sh\nexit 1\n")
    binary.chmod(0o755)
    res, took = _hermetic_bash(tmp_path, f'ascendo_npm_repair_broken_opencode "{prefix}" "{binary}"',
                               ASCENDO_OPENCODE_REPAIR_TIMEOUT="1")
    assert res.returncode != 0
    assert took < 30, took
