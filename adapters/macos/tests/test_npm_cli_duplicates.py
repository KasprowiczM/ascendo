"""npm category: native vs npm / Homebrew duplicate handling (Task 8b).

Port of upstream macOS_updates v1.5.3 ``cleanup_native_npm_duplicates`` with
Ascendo's rulings applied:

  * REPORT-ONLY by default. A duplicate is a phase-level warn MESSAGE carrying
    ``ASCENDO-ACTION-REQUIRED reason=cli_duplicate cur=<native> cand=<dup>`` and
    the remedy text -- never a synthetic sidecar item (no fake inventory row);
  * removal only in apply, only with ASCENDO_REMOVE_CLI_DUPLICATES=1, and only
    when every upstream guard holds: native proof (vendor root + digit-bearing
    ``--version``), the active command is the native one, known npm prefix,
    ``removal_allowed`` (not older, no running process), a process re-check
    immediately before the delete, ``npm uninstall -g --prefix P --ignore-scripts
    PKG`` bounded by a timeout, and a post-check that the native CLI still runs;
  * a Homebrew-owned copy is reported with ``brew uninstall <name>`` and is
    NEVER removed, with or without the opt-in; nothing is ever killed.

Everything runs on fakes (temp HOME, hermetic PATH, fake npm/ps/brew/CLIs);
nothing real is installed, removed or run.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from ascendo.orchestrator.report import ACTION_RE, collect_action_required

from .test_npm_gate_opencode import (
    APPLY, CHECK, CLAUDE_ROW, CODEX_ROW, OPENCODE_ROW, PLAN, VERIFY, Env, _item, _msgs, _status,
)

US = "\x1f"
OPT_IN = {"ASCENDO_REMOVE_CLI_DUPLICATES": "1"}
UNINSTALL = "npm uninstall -g --prefix"

_NPM_WRAPPER = r"""#!/bin/sh
if [ "$1" = uninstall ]; then
  echo "npm $*" >> "$FAKE_EVENTS"
  [ -z "$FAKE_UNINSTALL_SLEEP" ] || sleep "$FAKE_UNINSTALL_SLEEP"
  if [ "${FAKE_UNINSTALL_RC:-0}" -ne 0 ]; then echo "npm ERR! boom" >&2; exit "$FAKE_UNINSTALL_RC"; fi
  prefix=""; pkg=""
  while [ $# -gt 0 ]; do
    case "$1" in
      uninstall) shift ;;
      --prefix) prefix="$2"; shift 2 ;;
      -*) shift ;;
      *) pkg="$1"; shift ;;
    esac
  done
  rm -rf "$prefix/lib/node_modules/$pkg"
  [ -z "$FAKE_UNINSTALL_BREAKS" ] || rm -f "$FAKE_UNINSTALL_BREAKS"
  exit 0
fi
exec "$(dirname "$0")/npm.orig" "$@"
"""


class DupEnv(Env):
    """Env + a fake `ps` (counted `-axo` calls) + a fake npm that can uninstall."""

    def __init__(self, tmp: Path):
        super().__init__(tmp)
        (self.bin / "npm").rename(self.bin / "npm.orig")
        self.script(self.bin / "npm", _NPM_WRAPPER)
        self.psdir = tmp / "psdir"
        self.psdir.mkdir()
        self.script(self.bin / "ps", f"""#!/bin/sh
[ "$1" = "-axo" ] || exit 0
d="{self.psdir}"
n=$(cat "$d/n" 2>/dev/null || echo 0); n=$((n + 1)); echo "$n" > "$d/n"
k=$(ls "$d" | grep -c '^[0-9][0-9]*$')
i=$n; [ "$i" -le "$k" ] || i=$k
[ "$(cat "$d/$i")" != "__FAIL__" ] || exit 1
cat "$d/$i"
""")
        self.processes([""])      # no running process, ever
        # python3 wrapper: FAKE_DUP_PY_FAIL=<mode> makes `cli_duplicates.py <mode>`
        # crash (traceback on stderr, exit 1); everything else is the real python.
        real_py = (self.tools / "python3").resolve()
        (self.tools / "python3").unlink()
        self.script(self.tools / "python3", f"""#!/bin/sh
case "$1" in
  */cli_duplicates.py)
    if [ "$2" = "${{FAKE_DUP_PY_FAIL:-}}" ]; then
      echo "ImportError: boom" >&2
      echo "token=abc123secret" >&2
      exit 1
    fi ;;
esac
exec "{real_py}" "$@"
""")

    def processes(self, listings: list[str]) -> None:
        """The i-th `ps -axo command=` call prints listings[i] (last one repeats);
        '__FAIL__' makes it exit 1 (process state unknown)."""
        for old in self.psdir.glob("*"):
            old.unlink()
        for i, text in enumerate(listings, start=1):
            (self.psdir / str(i)).write_text(text)

    def ps_calls(self) -> int:
        f = self.psdir / "n"
        return int(f.read_text()) if f.exists() else 0

    def npm_dup(self, pkg: str, version: str, prefix: Path | None = None) -> Path:
        d = (prefix or self.prefix) / "lib" / "node_modules" / pkg
        d.mkdir(parents=True, exist_ok=True)
        (d / "package.json").write_text(json.dumps({"name": pkg, "version": version}))
        return d

    def brew_keg(self, kind: str, name: str, version: str, command: str) -> Path:
        """A Homebrew install of ``command``: <hb>/Cellar|Caskroom/<name>/<ver>/bin/<cmd>
        linked from <hb>/bin. Returns the keg's binary."""
        hb = self.tmp / "hb"
        keg = hb / ("Cellar" if kind == "formula" else "Caskroom") / name / version / "bin" / command
        self.cli(keg, f"brew-{command}", version)
        (hb / "Cellar").mkdir(exist_ok=True)
        (hb / "bin").mkdir(exist_ok=True)
        link = hb / "bin" / command
        if link.is_symlink():
            link.unlink()
        link.symlink_to(keg)
        return keg

    def brew_prefix_npm(self, pkg: str, version: str, command: str) -> Path:
        """An npm global under a Homebrew prefix (one with a Cellar):
        <hb>/lib/node_modules/<pkg>/{package.json,cli.js}, linked from <hb>/bin."""
        hb = self.tmp / "hb"
        d = hb / "lib" / "node_modules" / pkg
        d.mkdir(parents=True, exist_ok=True)
        (d / "package.json").write_text(json.dumps({"name": pkg, "version": version}))
        self.cli(d / "cli.js", f"npm-{command}", version)
        (hb / "Cellar").mkdir(exist_ok=True)
        (hb / "bin").mkdir(exist_ok=True)
        link = hb / "bin" / command
        if link.is_symlink():
            link.unlink()
        link.symlink_to(d / "cli.js")
        return d

    def run(self, script, rows, *extra, env_extra=None, **kw):
        merged = {"ASCENDO_NPM_BREW_DIRS": str(self.tmp / "hb" / "bin")
                  if (self.tmp / "hb" / "bin").is_dir() else ""}
        merged.update(env_extra or {})
        res = super().run(script, rows, *extra, env_extra=merged, **kw)
        res.run_dir = next(self.out.iterdir(), None)  # type: ignore[attr-defined]
        return res

    def uninstalls(self) -> list[str]:
        return [ln for ln in self.ev().splitlines() if ln.startswith("npm uninstall")]


@pytest.fixture
def env(tmp_path: Path) -> DupEnv:
    return DupEnv(tmp_path)


def _tokens(res) -> list[re.Match]:
    return [m for m in (ACTION_RE.search(x.get("text") or "") for x in res.sidecar["messages"]) if m]


def _dup_msgs(res) -> list[str]:
    return [x["text"] for x in res.sidecar["messages"] if "reason=cli_duplicate" in (x.get("text") or "")]


def _claude_with_npm_dup(env: DupEnv, native="2.0.0", dup="1.0.0", **kw) -> Path:
    env.claude(native, **kw)
    return env.npm_dup("@anthropic-ai/claude-code", dup)


# =============================================================================
# check: report only
# =============================================================================

def test_check_reports_npm_duplicate_as_message_with_token_and_remedy(env: DupEnv) -> None:
    dup = _claude_with_npm_dup(env)
    res = env.run(CHECK, [CLAUDE_ROW])
    assert res.returncode == 0, res.stderr
    texts = _dup_msgs(res)
    assert len(texts) == 1, _msgs(res)
    t = texts[0]
    assert "reason=cli_duplicate cur=2.0.0 cand=1.0.0" in t
    assert f"{UNINSTALL} {env.prefix} @anthropic-ai/claude-code" in t
    assert "claude-code" in t.split(": ", 1)[0]
    # message level warn, no synthetic item: exactly the manifest row
    assert [m["level"] for m in res.sidecar["messages"] if "cli_duplicate" in m["text"]] == ["warn"]
    assert [i["id"] for i in res.sidecar["items"]] == ["claude-code"]
    assert dup.exists() and env.uninstalls() == []


def test_check_never_removes_even_with_the_opt_in(env: DupEnv) -> None:
    dup = _claude_with_npm_dup(env)
    res = env.run(CHECK, [CLAUDE_ROW], env_extra=OPT_IN)
    assert len(_dup_msgs(res)) == 1
    assert dup.exists() and env.uninstalls() == []


def test_check_without_a_duplicate_emits_no_token(env: DupEnv) -> None:
    env.claude("2.0.0")
    res = env.run(CHECK, [CLAUDE_ROW])
    assert _tokens(res) == []


def test_duplicate_without_proven_native_is_not_reported(env: DupEnv) -> None:
    """A npm copy alone (no vendor-native install) is the user's only install."""
    env.npm_dup("@anthropic-ai/claude-code", "1.0.0")
    res = env.run(CHECK, [CLAUDE_ROW])
    assert _tokens(res) == []


def test_npm_wrapper_linked_into_local_bin_is_not_native_so_no_duplicate(env: DupEnv) -> None:
    wrapper = env.prefix / "lib" / "node_modules" / "@anthropic-ai" / "claude-code" / "cli.js"
    env.cli(wrapper, "claude-npm", "1.0.0")
    env.npm_dup("@anthropic-ai/claude-code", "1.0.0")
    env.local_bin.mkdir(parents=True)
    (env.local_bin / "claude").symlink_to(wrapper)
    res = env.run(CHECK, [CLAUDE_ROW])
    assert _tokens(res) == []


def test_native_whose_version_does_not_parse_is_not_proven(env: DupEnv) -> None:
    _claude_with_npm_dup(env, version_rc=1)
    res = env.run(CHECK, [CLAUDE_ROW])
    assert _tokens(res) == []


def test_filter_for_another_row_does_not_scan(env: DupEnv) -> None:
    _claude_with_npm_dup(env)
    env.codex("0.1.0")
    res = env.run(CHECK, [CLAUDE_ROW, CODEX_ROW], "--filter", "codex-cli")
    assert _tokens(res) == []


@pytest.mark.parametrize("script", [PLAN, VERIFY])
def test_plan_and_verify_do_not_report_duplicates(env: DupEnv, script: Path) -> None:
    _claude_with_npm_dup(env)
    res = env.run(script, [CLAUDE_ROW])
    assert _tokens(res) == []


def test_codex_and_opencode_duplicates_use_their_own_packages(env: DupEnv) -> None:
    env.codex("0.5.0")
    env.npm_dup("@openai/codex", "0.4.0")
    env.oc_native("3.0.0")
    env.npm_dup("opencode-ai", "2.0.0")
    res = env.run(CHECK, [CODEX_ROW, OPENCODE_ROW])
    texts = "\n".join(_dup_msgs(res))
    assert f"{UNINSTALL} {env.prefix} @openai/codex" in texts
    assert f"{UNINSTALL} {env.prefix} opencode-ai" in texts
    assert "cur=0.5.0 cand=0.4.0" in texts and "cur=3.0.0 cand=2.0.0" in texts


def test_two_prefixes_give_two_distinct_messages(env: DupEnv) -> None:
    _claude_with_npm_dup(env)
    nvm = env.home / ".nvm/versions/node/v22.1.0"
    env.npm_dup("@anthropic-ai/claude-code", "1.5.0", nvm)
    res = env.run(CHECK, [CLAUDE_ROW])
    texts = _dup_msgs(res)
    assert len(texts) == 2
    slugs = {t.split(": ", 1)[0] for t in texts}
    assert len(slugs) == 2


def test_duplicate_in_unknown_prefix_is_not_found(env: DupEnv) -> None:
    env.claude("2.0.0")
    env.npm_dup("@anthropic-ai/claude-code", "1.0.0", env.home / "somewhere" / "else")
    res = env.run(CHECK, [CLAUDE_ROW])
    assert _tokens(res) == []


# =============================================================================
# Homebrew copies: reported, never removed
# =============================================================================

def test_check_reports_homebrew_copy_with_brew_remedy(env: DupEnv) -> None:
    env.oc_native("2.0.0")
    env.brew_keg("formula", "opencode", "1.0.0", "opencode")
    res = env.run(CHECK, [OPENCODE_ROW])
    t = "\n".join(_dup_msgs(res))
    assert "reason=cli_duplicate cur=2.0.0 cand=1.0.0" in t
    assert "brew uninstall opencode" in t
    assert [i["id"] for i in res.sidecar["items"]] == ["opencode-cli"]


def test_homebrew_cask_remedy_uses_cask_flag(env: DupEnv) -> None:
    env.claude("2.0.0")
    env.brew_keg("cask", "claude-code", "1.9.0", "claude")
    res = env.run(CHECK, [CLAUDE_ROW])
    assert "brew uninstall --cask claude-code" in "\n".join(_dup_msgs(res))


def test_apply_never_removes_homebrew_copy_even_with_opt_in(env: DupEnv) -> None:
    env.oc_native("2.0.0")
    keg = env.brew_keg("formula", "opencode", "1.0.0", "opencode")
    res = env.run(APPLY, [OPENCODE_ROW], env_extra=OPT_IN)
    assert res.returncode == 0, res.stderr
    t = "\n".join(_dup_msgs(res))
    assert "brew uninstall opencode" in t and "never removes Homebrew" in t
    assert "brew " not in env.ev(), "brew must not be invoked at all"
    assert keg.exists() and env.uninstalls() == []
    assert [i["id"] for i in res.sidecar["items"]] == ["opencode-cli"]


def test_homebrew_copy_is_never_executed(env: DupEnv) -> None:
    env.oc_native("2.0.0")
    env.brew_keg("formula", "opencode", "1.0.0", "opencode")
    res = env.run(CHECK, [OPENCODE_ROW])
    assert "brew-opencode" not in env.ev()
    assert res.returncode == 0


# =============================================================================
# apply: report-only by default
# =============================================================================

def test_apply_reports_but_keeps_duplicate_by_default(env: DupEnv) -> None:
    dup = _claude_with_npm_dup(env)
    res = env.run(APPLY, [CLAUDE_ROW])
    assert res.returncode == 0, res.stderr
    assert len(_dup_msgs(res)) == 1
    assert dup.exists() and env.uninstalls() == []
    assert [i["id"] for i in res.sidecar["items"]] == ["claude-code"]


def test_apply_report_is_collected_as_action_required_for_npm(env: DupEnv) -> None:
    _claude_with_npm_dup(env)
    res = env.run(APPLY, [CLAUDE_ROW])
    items = collect_action_required(res.run_dir)
    dups = [a for a in items if a.reason == "cli_duplicate"]
    assert len(dups) == 1
    a = dups[0]
    assert a.category == "npm" and (a.current, a.candidate) == ("2.0.0", "1.0.0")
    assert a.slug.startswith("claude-code")
    assert "second copy" in a.reason_text.lower()


def test_dry_run_neither_reports_nor_removes(env: DupEnv) -> None:
    dup = _claude_with_npm_dup(env)
    res = env.run(APPLY, [CLAUDE_ROW], "--dry-run", env_extra=OPT_IN)
    assert _tokens(res) == []
    assert dup.exists() and env.uninstalls() == []


# =============================================================================
# apply: opt-in removal, every guard
# =============================================================================

def test_opt_in_removes_only_the_named_package_with_ignore_scripts(env: DupEnv) -> None:
    dup = _claude_with_npm_dup(env)
    unrelated = env.npm_dup("unrelated-pkg", "9.9.9")
    res = env.run(APPLY, [CLAUDE_ROW], env_extra=OPT_IN)
    assert res.returncode == 0, res.stderr
    assert env.uninstalls() == [
        f"npm uninstall -g --prefix {env.prefix} --ignore-scripts @anthropic-ai/claude-code"]
    assert not dup.exists() and unrelated.exists()
    assert (env.local_bin / "claude").exists()
    assert _dup_msgs(res) == [], "a removed duplicate is no longer action-required"
    assert "Removed npm duplicate @anthropic-ai/claude-code 1.0.0" in _msgs(res)
    assert [i["id"] for i in res.sidecar["items"]] == ["claude-code"]


def test_opt_in_accepts_equal_versions(env: DupEnv) -> None:
    dup = _claude_with_npm_dup(env, native="1.0.0", dup="1.0.0")
    env.run(APPLY, [CLAUDE_ROW], env_extra=OPT_IN)
    assert not dup.exists()


@pytest.mark.parametrize("value", ["0", "", "no", "false", "true", "yes", "TRUE", "2"])
def test_only_the_exact_value_1_enables_removal(env: DupEnv, value: str) -> None:
    dup = _claude_with_npm_dup(env)
    env.run(APPLY, [CLAUDE_ROW], env_extra={"ASCENDO_REMOVE_CLI_DUPLICATES": value})
    assert dup.exists() and env.uninstalls() == []


def test_older_native_keeps_the_newer_npm_copy(env: DupEnv) -> None:
    dup = _claude_with_npm_dup(env, native="1.0.0", dup="2.0.0")
    res = env.run(APPLY, [CLAUDE_ROW], env_extra=OPT_IN)
    assert dup.exists() and env.uninstalls() == []
    t = "\n".join(_dup_msgs(res))
    assert "reason=cli_duplicate cur=1.0.0 cand=2.0.0" in t
    assert "older native or uncertain" in t


def test_unparseable_duplicate_version_is_retained(env: DupEnv) -> None:
    dup = _claude_with_npm_dup(env, dup="not-a-version")
    res = env.run(APPLY, [CLAUDE_ROW], env_extra=OPT_IN)
    assert dup.exists() and env.uninstalls() == []
    assert len(_dup_msgs(res)) == 1


def test_running_process_of_the_command_retains_the_duplicate(env: DupEnv) -> None:
    dup = _claude_with_npm_dup(env)
    env.processes(["/some/bin/claude run"])
    res = env.run(APPLY, [CLAUDE_ROW], env_extra=OPT_IN)
    assert dup.exists() and env.uninstalls() == []
    assert len(_dup_msgs(res)) == 1
    assert "pkill" not in env.ev() and "kill" not in env.ev()


def test_running_npm_wrapper_process_retains_the_duplicate(env: DupEnv) -> None:
    dup = _claude_with_npm_dup(env)
    env.processes(["node /p/lib/node_modules/@anthropic-ai/claude-code/cli.js"])
    env.run(APPLY, [CLAUDE_ROW], env_extra=OPT_IN)
    assert dup.exists() and env.uninstalls() == []


def test_unknown_process_state_retains_the_duplicate(env: DupEnv) -> None:
    dup = _claude_with_npm_dup(env)
    env.processes(["__FAIL__"])
    res = env.run(APPLY, [CLAUDE_ROW], env_extra=OPT_IN)
    assert dup.exists() and env.uninstalls() == []
    assert len(_dup_msgs(res)) == 1


def test_process_that_appears_after_discovery_blocks_the_delete(env: DupEnv) -> None:
    """The re-check immediately before deletion is a fresh `ps`."""
    dup = _claude_with_npm_dup(env)
    env.processes(["", "/some/bin/claude run"])
    res = env.run(APPLY, [CLAUDE_ROW], env_extra=OPT_IN)
    assert env.ps_calls() >= 2
    assert dup.exists() and env.uninstalls() == []
    t = "\n".join(_dup_msgs(res))
    assert "process state changed" in t


def test_failed_uninstall_keeps_fallback_and_reports(env: DupEnv) -> None:
    dup = _claude_with_npm_dup(env)
    res = env.run(APPLY, [CLAUDE_ROW], env_extra={**OPT_IN, "FAKE_UNINSTALL_RC": "3"})
    assert len(env.uninstalls()) == 1
    assert dup.exists()
    t = "\n".join(_dup_msgs(res))
    assert "uninstall failed" in t and "exited 3" in t
    assert (env.local_bin / "claude").exists()


def test_uninstall_is_bounded_by_a_timeout(env: DupEnv) -> None:
    dup = _claude_with_npm_dup(env)
    res = env.run(APPLY, [CLAUDE_ROW], timeout=120,
                  env_extra={**OPT_IN, "FAKE_UNINSTALL_SLEEP": "60",
                             "ASCENDO_CLI_DUPLICATE_TIMEOUT": "2"})
    assert dup.exists()
    assert "timed out" in "\n".join(_dup_msgs(res))


def test_native_that_stops_running_after_removal_is_reported_loudly(env: DupEnv) -> None:
    dup = _claude_with_npm_dup(env)
    real = env.home / ".local/share/claude/versions/2.0.0/claude"
    res = env.run(APPLY, [CLAUDE_ROW],
                  env_extra={**OPT_IN, "FAKE_UNINSTALL_BREAKS": str(real)})
    assert not dup.exists()
    t = _msgs(res)
    assert "no longer runs" in t
    assert any(m["level"] in ("warn", "error") and "no longer runs" in m["text"]
               for m in res.sidecar["messages"])


def test_native_proof_needs_the_native_to_be_the_active_command(env: DupEnv) -> None:
    """Upstream guard: `command -v <cmd>` must be the native path, else the
    native copy is not what the user runs and nothing is reported / removed."""
    import subprocess
    env.claude("2.0.0")
    lib = Path(__file__).resolve().parents[1] / "lib" / "ascendo_npm.sh"
    script = (f'. "{lib}"; ascendo_npm_duplicate_native claude-code claude >/dev/null; echo "real=$?"; '
              'ascendo_npm_active_command() { echo /somewhere/else/claude; }; '
              'ascendo_npm_duplicate_native claude-code claude >/dev/null; echo "shadowed=$?"')
    out = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True,
        env={"HOME": str(env.home), "PATH": f"{env.bin}:{env.tools}:/usr/bin:/bin",
             "MAC_UPDATE_TOOLCHAIN_HOME": str(env.toolchain), "ASCENDO_NPM_BREW_DIRS": ""})
    assert "real=0" in out.stdout and "shadowed=1" in out.stdout, out.stdout + out.stderr


def test_no_node_npm_means_no_removal_but_still_reported(env: DupEnv) -> None:
    dup = _claude_with_npm_dup(env)
    (env.bin / "npm").unlink()
    (env.bin / "npm.orig").unlink()
    res = env.run(APPLY, [CLAUDE_ROW], env_extra=OPT_IN)
    assert dup.exists()
    assert "npm is not available" in "\n".join(_dup_msgs(res))


def test_lib_helpers_are_defined_and_syntax_clean() -> None:
    import subprocess
    lib = Path(__file__).resolve().parents[1] / "lib" / "ascendo_npm.sh"
    out = subprocess.run(
        ["bash", "-c", f'. "{lib}"; type ascendo_npm_duplicates_handle >/dev/null && echo ok'],
        capture_output=True, text=True)
    assert out.stdout.strip() == "ok", out.stderr


# =============================================================================
# fix round 1
# =============================================================================

def test_npm_global_under_a_brew_prefix_is_reported_once_as_an_npm_duplicate(env: DupEnv) -> None:
    """Ruling 19: /opt/homebrew/bin/claude -> lib/node_modules/.../cli.js is an
    npm copy in the brew prefix: ONE message, npm remedy, no bogus brew remedy."""
    env.claude("2.0.0")
    pkg = env.brew_prefix_npm("@anthropic-ai/claude-code", "1.0.0", "claude")
    hb = env.tmp / "hb"
    res = env.run(CHECK, [CLAUDE_ROW])
    texts = _dup_msgs(res)
    assert len(texts) == 1, _msgs(res)
    assert f"{UNINSTALL} {hb} @anthropic-ai/claude-code" in texts[0]
    assert "reason=cli_duplicate cur=2.0.0 cand=1.0.0" in texts[0]
    assert "brew uninstall" not in _msgs(res) and "homebrew" not in _msgs(res).lower()
    assert pkg.exists() and env.uninstalls() == []


def test_brew_prefix_npm_copy_is_removable_only_with_opt_in_and_all_guards(env: DupEnv) -> None:
    env.claude("2.0.0")
    pkg = env.brew_prefix_npm("@anthropic-ai/claude-code", "1.0.0", "claude")
    hb = env.tmp / "hb"
    # report-only default: kept
    res = env.run(APPLY, [CLAUDE_ROW])
    assert pkg.exists() and env.uninstalls() == []
    assert len(_dup_msgs(res)) == 1 and "brew uninstall" not in _msgs(res)
    # opt-in but a guard fails (running process): kept
    env.processes(["/some/bin/claude run"])
    env.run(APPLY, [CLAUDE_ROW], env_extra=OPT_IN)
    assert pkg.exists() and env.uninstalls() == []
    # opt-in and every guard holds: removed through the npm path, brew untouched
    env.processes([""])
    res = env.run(APPLY, [CLAUDE_ROW], env_extra=OPT_IN)
    assert env.uninstalls() == [
        f"npm uninstall -g --prefix {hb} --ignore-scripts @anthropic-ai/claude-code"]
    assert not pkg.exists()
    assert _dup_msgs(res) == []
    assert "brew " not in env.ev()


def test_brew_prefix_without_a_cellar_is_not_a_package_root(env: DupEnv) -> None:
    """/usr/local without Homebrew must not become an npm prefix (no Cellar)."""
    env.claude("2.0.0")
    hb = env.tmp / "hb"
    d = hb / "lib" / "node_modules" / "@anthropic-ai" / "claude-code"
    d.mkdir(parents=True)
    (d / "package.json").write_text(json.dumps({"name": "@anthropic-ai/claude-code", "version": "1.0.0"}))
    (hb / "bin").mkdir()
    res = env.run(CHECK, [CLAUDE_ROW])
    assert _tokens(res) == []


def test_helper_failure_is_surfaced_as_a_sanitised_warn_message(env: DupEnv) -> None:
    _claude_with_npm_dup(env)
    res = env.run(CHECK, [CLAUDE_ROW], env_extra={"FAKE_DUP_PY_FAIL": "list"})
    assert res.returncode == 0
    warns = [m["text"] for m in res.sidecar["messages"]
             if m["level"] == "warn" and "helper 'list'" in m["text"]]
    assert len(warns) == 1, _msgs(res)
    assert "ImportError" in warns[0] and "abc123secret" not in warns[0]
    assert "REDACTED" in warns[0]
    assert _tokens(res) == []
    assert [i["id"] for i in res.sidecar["items"]] == ["claude-code"]


def test_brew_helper_failure_is_surfaced_too(env: DupEnv) -> None:
    env.oc_native("2.0.0")
    env.brew_keg("formula", "opencode", "1.0.0", "opencode")
    res = env.run(CHECK, [OPENCODE_ROW], env_extra={"FAKE_DUP_PY_FAIL": "brew"})
    assert any("helper 'brew'" in m["text"] for m in res.sidecar["messages"]), _msgs(res)
    assert _tokens(res) == []


def test_removal_check_helper_crash_keeps_the_fallback_and_says_why(env: DupEnv) -> None:
    dup = _claude_with_npm_dup(env)
    res = env.run(APPLY, [CLAUDE_ROW],
                  env_extra={**OPT_IN, "FAKE_DUP_PY_FAIL": "removal-check"})
    assert dup.exists() and env.uninstalls() == []
    msgs = _msgs(res)
    assert "helper 'removal-check'" in msgs
    assert len(_dup_msgs(res)) == 1


def test_hanging_uninstall_keeps_fallback_and_is_still_reported(env: DupEnv) -> None:
    dup = _claude_with_npm_dup(env)
    res = env.run(APPLY, [CLAUDE_ROW], timeout=120,
                  env_extra={**OPT_IN, "FAKE_UNINSTALL_SLEEP": "60",
                             "ASCENDO_CLI_DUPLICATE_TIMEOUT": "2"})
    assert dup.exists() and len(env.uninstalls()) == 1
    t = "\n".join(_dup_msgs(res))
    assert "timed out (exit 124)" in t and "reason=cli_duplicate cur=2.0.0 cand=1.0.0" in t
    assert (env.local_bin / "claude").exists()


@pytest.mark.parametrize("value,expected", [("", "120"), ("abc", "120"), ("0", "120"),
                                            ("-5", "120"), ("7", "7")])
def test_duplicate_timeout_falls_back_to_120_on_bad_values(env: DupEnv, value: str, expected: str) -> None:
    import subprocess
    lib = Path(__file__).resolve().parents[1] / "lib" / "ascendo_npm.sh"
    out = subprocess.run(["bash", "-c", f'. "{lib}"; ascendo_npm_duplicate_timeout'],
                         capture_output=True, text=True,
                         env={"PATH": "/usr/bin:/bin", "HOME": str(env.home),
                              "ASCENDO_CLI_DUPLICATE_TIMEOUT": value})
    assert out.stdout.strip() == expected
