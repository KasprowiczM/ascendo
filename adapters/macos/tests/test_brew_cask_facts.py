"""Homebrew cask facts in the phase scripts: running-app protection for EVERY
cask, greedy auto-updating casks, orphan casks, rebuilt downgrade guard and
sudo-requiring casks.

Fake brew / pgrep / lsappinfo on PATH; temp Applications dir through
ASCENDO_BREW_APPLICATIONS_DIR. Nothing real is ever upgraded, quit or killed.
"""
from __future__ import annotations

import json
import os
import plistlib
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
LIB = ADAPTER_ROOT / "lib" / "ascendo_brew.sh"
SCRIPTS = ADAPTER_ROOT / "scripts" / "brew"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("jq") is None,
    reason="bash + jq required",
)


def cask(token, version="2.0", installed="1.0", artifacts=None):
    return {"token": token, "version": version, "installed": installed,
            "artifacts": artifacts if artifacts is not None else []}


def outdated_entry(token, cur="1.0", cand="2.0"):
    return {"name": token, "installed_versions": [cur], "current_version": cand}


class Sandbox:
    """Fake-binary sandbox around brew / pgrep / lsappinfo."""

    def __init__(self, tmp: Path, *, outdated=(), greedy_outdated=(), installed=(),
                 info=(), apps=(), running_apps=(), running_ids=(),
                 outdated_rc=0, greedy_rc=0, greedy_stderr="", greedy_file=None, info_sleep=0,
                 lsappinfo_mode="ok"):
        self.tmp = tmp
        tmp.mkdir(parents=True, exist_ok=True)
        self.bin = tmp / "bin"
        self.bin.mkdir()
        self.brew_log = tmp / "brew.log"
        self.proc_log = tmp / "proc.log"
        self.apps = tmp / "Applications"
        self.apps.mkdir()
        for name in apps:
            self.add_app(name, "1.0")
        (tmp / "outdated.json").write_text(json.dumps({"formulae": [], "casks": list(outdated)}))
        (tmp / "greedy.json").write_text(json.dumps({"formulae": [], "casks": list(greedy_outdated)}))
        (tmp / "greedy.err").write_text(greedy_stderr)
        (tmp / "info.json").write_text(json.dumps({"formulae": [], "casks": list(info)}))
        (tmp / "installed.txt").write_text("".join(f"{t} {v}\n" for t, v in installed))
        self._script(self.bin / "brew", f'''
echo "$@" >> "{self.brew_log}"
case "$1" in
  --version) echo "Homebrew 9.9.9" ;;
  --prefix) echo "{tmp}/prefix" ;;
  outdated)
    case "$*" in
      *--greedy-auto-updates*) cat "{tmp}/greedy.json"; cat "{tmp}/greedy.err" >&2; exit {greedy_rc} ;;
    esac
    cat "{tmp}/outdated.json"; exit {outdated_rc} ;;
  list) case "$*" in *--cask*--versions*) cat "{tmp}/installed.txt" ;; esac; exit 0 ;;
  info)
    [ {info_sleep} -gt 0 ] && sleep {info_sleep}
    # like real brew: only the requested casks, and a single unknown token
    # rejects the whole call (rc 1, no JSON).
    shift; [ "$1" = "--json=v2" ] && shift; [ "$1" = "--cask" ] && shift
    want="$(printf '%s\\n' "$@" | jq -R . | jq -sc .)"
    if ! jq -e --argjson w "$want" '[.casks[].token] as $have | ($w - $have) | length == 0' \
         "{tmp}/info.json" >/dev/null; then
      echo "Error: Cask unavailable" >&2; exit 1
    fi
    jq -c --argjson w "$want" '{{formulae: [], casks: [.casks[] | select(.token as $t | $w | index($t))]}}' \
       "{tmp}/info.json"
    exit 0 ;;
  upgrade) echo "==> upgraded $*"; exit 0 ;;
esac
exit 0
''')
        running = " ".join(f'"{n}"' for n in running_apps) or '"__none__"'
        self._script(self.bin / "pgrep", f'''
echo "pgrep $*" >> "{self.proc_log}"
for n in {running}; do case "$*" in *"$n"*) exit 0 ;; esac; done
exit 1
''')
        # A real session always lists some apps; an EMPTY listing means "the
        # probe could not answer" (unknown), so the idle default is a benign line.
        ids = "\n".join(f'bundleID="{i}"' for i in ["com.apple.finder", *running_ids])
        if lsappinfo_mode == "empty":
            ids = ""
        self._script(self.bin / "lsappinfo", f'''
echo "lsappinfo $*" >> "{self.proc_log}"
[ "{lsappinfo_mode}" = fail ] && exit 1
[ "$1" = "list" ] && cat <<'EOF_LS'
{ids}
EOF_LS
exit 0
''')
        for name in ("osascript", "pkill", "kill", "killall"):
            self._script(self.bin / name, f'echo "{name} $*" >> "{self.proc_log}"\nexit 0\n')
        self.env = dict(os.environ)
        self.env["PATH"] = f"{self.bin}:{self.env['PATH']}"
        self.env["ASCENDO_BREW_APPLICATIONS_DIR"] = str(self.apps)
        self.env["ASCENDO_BREW_GREEDY_FILE"] = str(greedy_file) if greedy_file else str(tmp / "no-greedy.txt")
        self.env.pop("ASCENDO_STREAM_LOG", None)
        self.env.pop("SUDO_ASKPASS", None)
        self.env.pop("ASCENDO_LSAPPINFO_BIN", None)
        if lsappinfo_mode == "missing":
            self.env["ASCENDO_LSAPPINFO_BIN"] = str(tmp / "no-such-lsappinfo")

    @staticmethod
    def _script(path: Path, body: str) -> None:
        path.write_text("#!/usr/bin/env bash\n" + body)
        path.chmod(0o755)

    def add_app(self, name: str, version: str) -> Path:
        contents = self.apps / name / "Contents"
        contents.mkdir(parents=True)
        (contents / "Info.plist").write_bytes(plistlib.dumps({"CFBundleShortVersionString": version}))
        return self.apps / name

    def greedy_file(self, *tokens: str) -> None:
        path = self.tmp / "greedy.txt"
        path.write_text("# greedy\n" + "\n".join(tokens) + "\n")
        self.env["ASCENDO_BREW_GREEDY_FILE"] = str(path)

    def brew_calls(self):
        return self.brew_log.read_text().splitlines() if self.brew_log.exists() else []

    def upgrades(self):
        return [c for c in self.brew_calls() if c.startswith("upgrade")]

    def killed(self):
        if not self.proc_log.exists():
            return []
        return [ln for ln in self.proc_log.read_text().splitlines()
                if not ln.startswith(("pgrep", "lsappinfo"))]

    def bash(self, script: str) -> subprocess.CompletedProcess:
        return subprocess.run(["bash", "-c", f'. "{LIB}"; {script}'],
                              capture_output=True, text=True, env=self.env)

    def run_phase(self, phase: str, *extra: str):
        out = self.tmp / "out"
        rid = str(uuid.uuid4())
        res = subprocess.run(
            ["bash", str(SCRIPTS / f"{phase}.sh"), "--run-id", rid, "--trigger", "cli",
             "--profile", "default", "--output-dir", str(out), *extra],
            capture_output=True, text=True, env=self.env, check=False)
        return res, json.loads((out / rid / f"{phase}__brew.json").read_text())


def by_id(sc):
    return {i["id"]: i for i in sc["items"]}


def message(sc, code):
    return next(m for m in sc["messages"] if m.get("code") == code)


# ── running-app protection for EVERY cask (not just the table) ───────────────

FANCY = cask("zzz-fancy", artifacts=[{"app": ["Fancy Thing.app"]}])


def test_non_table_cask_with_running_app_is_skipped(tmp_path):
    """zzz-fancy is in no hard-coded table: its .app target comes from brew's own facts."""
    sb = Sandbox(tmp_path, outdated=[outdated_entry("zzz-fancy")], info=[FANCY],
                 apps=["Fancy Thing.app"], running_apps=["Fancy Thing"])
    res, sc = sb.run_phase("apply")
    assert res.returncode == 0, res.stderr
    item = by_id(sc)["zzz-fancy"]
    assert item["status"] == "skipped"
    assert (item["current_version"], item["target_version"]) == ("1.0", "2.0")  # candidate kept
    assert sb.upgrades() == []
    assert sb.killed() == []
    msg = message(sc, "CASK_APP_RUNNING")
    assert "ASCENDO-ACTION-REQUIRED reason=app_in_use cur=1.0 cand=2.0" in msg["text"]


def test_non_table_cask_upgrades_when_its_app_is_idle(tmp_path):
    sb = Sandbox(tmp_path, outdated=[outdated_entry("zzz-fancy")], info=[FANCY], apps=["Fancy Thing.app"])
    res, sc = sb.run_phase("apply")
    assert res.returncode == 0, res.stderr
    assert by_id(sc)["zzz-fancy"]["status"] == "success"
    assert sb.upgrades() == ["upgrade --cask zzz-fancy"]


def test_dict_target_form_is_used_for_running_detection(tmp_path):
    c = cask("zzz-renamed", artifacts=[{"app": [{"target": "Renamed Bundle.app"}]}])
    sb = Sandbox(tmp_path, outdated=[outdated_entry("zzz-renamed")], info=[c],
                 apps=["Renamed Bundle.app"], running_apps=["Renamed Bundle"])
    res, sc = sb.run_phase("apply")
    assert by_id(sc)["zzz-renamed"]["status"] == "skipped"
    assert sb.upgrades() == []


def test_pkg_cask_running_app_via_uninstall_delete_hint(tmp_path):
    """A pkg cask has no `app` artifact: /Applications/X.app comes from uninstall.delete."""
    c = cask("zzz-pkgapp", artifacts=[
        {"pkg": ["setup.pkg"]},
        {"uninstall": [{"delete": ["/Applications/Pkg Thing.app"]}]},
    ])
    sb = Sandbox(tmp_path, outdated=[outdated_entry("zzz-pkgapp")], info=[c],
                 apps=["Pkg Thing.app"], running_apps=["Pkg Thing"])
    # running -> deferred as app_in_use even though it also needs admin
    res, sc = sb.run_phase("apply")
    assert res.returncode == 0, res.stderr
    assert by_id(sc)["zzz-pkgapp"]["status"] == "skipped"
    assert any(m.get("code") == "CASK_APP_RUNNING" for m in sc["messages"])
    assert sb.upgrades() == []


def test_quit_bundle_id_running_defers_a_cask_without_app_targets(tmp_path):
    c = cask("zzz-agent", artifacts=[{"binary": ["agentctl"]},
                                     {"uninstall": [{"quit": "com.example.zzzagent"}]}])
    sb = Sandbox(tmp_path, outdated=[outdated_entry("zzz-agent")], info=[c],
                 running_ids=["com.example.zzzagent"])
    res, sc = sb.run_phase("apply")
    assert res.returncode == 0, res.stderr
    assert by_id(sc)["zzz-agent"]["status"] == "skipped"
    assert message(sc, "CASK_APP_RUNNING")
    assert sb.upgrades() == []

    idle = Sandbox(tmp_path / "idle", outdated=[outdated_entry("zzz-agent")], info=[c],
                   running_ids=["com.example.someone.else"])
    _, sc2 = idle.run_phase("apply")
    assert by_id(sc2)["zzz-agent"]["status"] == "success"


@pytest.mark.parametrize("mode", ["missing", "fail", "empty"])
def test_cask_with_unprobeable_running_state_is_deferred_never_upgraded(tmp_path, mode):
    """F2 / Ruling 26: lsappinfo missing, failing or empty => state `unknown` =>
    the cask upgrade is deferred (skipped + app_in_use action-required), brew is
    never asked to upgrade, and nothing is quit or killed."""
    c = cask("zzz-agent", artifacts=[{"binary": ["agentctl"]},
                                     {"uninstall": [{"quit": "com.example.zzzagent"}]}])
    sb = Sandbox(tmp_path, outdated=[outdated_entry("zzz-agent")], info=[c], lsappinfo_mode=mode)
    res, sc = sb.run_phase("apply")
    assert res.returncode == 0, res.stderr
    item = by_id(sc)["zzz-agent"]
    assert item["status"] == "skipped"
    assert (item["current_version"], item["target_version"]) == ("1.0", "2.0")  # candidate kept
    assert sb.upgrades() == []
    assert sb.killed() == []
    msg = message(sc, "CASK_APP_RUNNING_UNKNOWN")
    assert "could not determine" in msg["text"]
    assert "ASCENDO-ACTION-REQUIRED reason=app_in_use cur=1.0 cand=2.0" in msg["text"]
    assert not any(m.get("code") == "CASK_APP_RUNNING" for m in sc["messages"])


def test_cask_running_state_is_tri_state(tmp_path):
    c = cask("zzz-agent", artifacts=[{"binary": ["agentctl"]},
                                     {"uninstall": [{"quit": "com.example.zzzagent"}]}])
    facts = 'ascendo_brew_prepare_cask_facts "$TMP_OUTDATED" 2>/dev/null; '
    for mode, kw, expect in (("ok", {"running_ids": ["com.example.zzzagent"]}, "true"),
                             ("ok", {}, "false"),
                             ("missing", {}, "unknown"),
                             ("fail", {}, "unknown")):
        sb = Sandbox(tmp_path / f"{mode}-{expect}", outdated=[outdated_entry("zzz-agent")], info=[c],
                     lsappinfo_mode=mode, **kw)
        res = sb.bash('TMP_OUTDATED="$(mktemp)"; brew outdated --cask --json=v2 > "$TMP_OUTDATED" 2>/dev/null; '
                      + facts + 'ascendo_brew_cask_running_state zzz-agent')
        assert res.stdout.strip() == expect, (mode, expect, res.stdout, res.stderr)


def test_app_paths_come_from_facts_and_table_is_only_a_fallback(tmp_path):
    sb = Sandbox(tmp_path, info=[FANCY], apps=["Fancy Thing.app", "Brave Browser.app"])
    facts = sb.bash('ascendo_brew_cask_app_paths zzz-fancy; echo ---; ascendo_brew_cask_app_paths brave-browser')
    assert facts.stdout.split("---")[0].strip() == str(sb.apps / "Fancy Thing.app")
    # brave has no facts (info.json lacks it) -> last-resort table
    assert facts.stdout.split("---")[1].strip() == str(sb.apps / "Brave Browser.app")

    # with facts that declare targets, a table entry must NOT resurrect a deleted bundle
    gone = Sandbox(tmp_path / "g", info=[cask("brave-browser", artifacts=[{"app": ["Other.app"]}])],
                   apps=["Brave Browser.app"])
    assert gone.bash("ascendo_brew_cask_app_paths brave-browser").stdout.strip() == ""


def test_running_probe_is_read_only(tmp_path):
    sb = Sandbox(tmp_path, info=[FANCY], apps=["Fancy Thing.app"], running_apps=["Fancy Thing"])
    res = sb.bash("ascendo_brew_cask_app_running zzz-fancy; echo rc=$?")
    assert "rc=0" in res.stdout
    assert sb.killed() == []


def test_facts_are_loaded_with_one_batched_brew_info(tmp_path):
    outdated = [outdated_entry("zzz-fancy"), outdated_entry("zzz-two")]
    sb = Sandbox(tmp_path, outdated=outdated, info=[FANCY, cask("zzz-two", artifacts=[{"app": ["Two.app"]}])],
                 apps=["Fancy Thing.app", "Two.app"])
    res, sc = sb.run_phase("apply")
    assert res.returncode == 0, res.stderr
    infos = [c for c in sb.brew_calls() if c.startswith("info")]
    assert infos == ["info --json=v2 --cask zzz-fancy zzz-two"]


# ── greedy auto-updating casks ───────────────────────────────────────────────

def test_greedy_tokens_are_the_intersection_with_installed_casks(tmp_path):
    sb = Sandbox(tmp_path, installed=[("brave-browser", "1.0"), ("zoom", "6.0"), ("other", "1")])
    sb.greedy_file("brave-browser", "obsidian", "zoom", "brave-browser  # dup")
    assert sb.bash("ascendo_brew_greedy_tokens").stdout.split() == ["brave-browser", "zoom"]
    # missing config file -> nothing greedy, no error
    sb.env["ASCENDO_BREW_GREEDY_FILE"] = str(tmp_path / "nope.txt")
    res = sb.bash("ascendo_brew_greedy_tokens; echo rc=$?")
    assert res.stdout.strip() == "rc=0"


def test_shipped_greedy_config_lists_the_brew_managed_auto_updating_casks():
    tokens = [ln.split("#")[0].strip()
              for ln in (ADAPTER_ROOT / "config" / "brew_greedy_casks.txt").read_text().splitlines()]
    tokens = [t for t in tokens if t]
    assert {"brave-browser", "zoom", "obsidian", "spotify", "inkscape"} <= set(tokens)
    assert len(tokens) == len(set(tokens))


def test_outdated_json_unions_greedy_query_deduped_by_token(tmp_path):
    sb = Sandbox(tmp_path,
                 outdated=[outdated_entry("slack", "3", "4"), outdated_entry("shared", "1", "2")],
                 greedy_outdated=[outdated_entry("brave-browser", "1.0", "2.0"),
                                  outdated_entry("shared", "1", "2")])
    res = sb.bash("ascendo_brew_outdated_json brave-browser zoom")
    assert res.returncode == 0, res.stderr
    names = sorted(c["name"] for c in json.loads(res.stdout)["casks"])
    assert names == ["brave-browser", "shared", "slack"]
    assert "outdated --json=v2 --cask --greedy-auto-updates brave-browser zoom" in sb.brew_calls()


def test_outdated_json_without_tokens_runs_only_the_plain_query(tmp_path):
    sb = Sandbox(tmp_path, outdated=[outdated_entry("slack")])
    res = sb.bash("ascendo_brew_outdated_json")
    assert res.returncode == 0
    assert not any("--greedy-auto-updates" in c for c in sb.brew_calls())


def test_greedy_query_rc1_without_error_line_is_success_but_error_line_fails(tmp_path):
    ok = Sandbox(tmp_path / "ok", greedy_outdated=[outdated_entry("brave-browser")], greedy_rc=1,
                 greedy_stderr="==> Downloading Homebrew API data\n")
    res = ok.bash("ascendo_brew_outdated_json brave-browser")
    assert res.returncode == 0
    assert [c["name"] for c in json.loads(res.stdout)["casks"]] == ["brave-browser"]

    bad = Sandbox(tmp_path / "bad", greedy_rc=1, greedy_stderr="Error: cask 'x' is unavailable\n")
    res = bad.bash("ascendo_brew_outdated_json x")
    assert res.returncode == 1
    assert json.loads(res.stdout) == {"formulae": [], "casks": []}
    assert "Error: cask 'x' is unavailable" in res.stderr


def test_check_reports_greedy_only_cask_as_planned(tmp_path):
    brave = cask("brave-browser", version="1.93.138.0", installed="1.93.137.0",
                 artifacts=[{"app": ["Brave Browser.app"]}])
    sb = Sandbox(tmp_path, greedy_outdated=[outdated_entry("brave-browser", "1.93.137.0", "1.93.138.0")],
                 installed=[("brave-browser", "1.93.137.0")], info=[brave], apps=["Brave Browser.app"])
    sb.greedy_file("brave-browser")
    res, sc = sb.run_phase("check")
    assert res.returncode == 0, res.stderr
    item = by_id(sc)["brave-browser"]
    assert (item["status"], item["target_version"]) == ("planned", "1.93.138.0")


def test_plan_and_apply_use_the_greedy_query_too(tmp_path):
    brave = cask("brave-browser", version="2.0", installed="1.0", artifacts=[{"app": ["Brave Browser.app"]}])
    sb = Sandbox(tmp_path, greedy_outdated=[outdated_entry("brave-browser")],
                 installed=[("brave-browser", "1.0")], info=[brave], apps=["Brave Browser.app"])
    sb.greedy_file("brave-browser")
    _, plan = sb.run_phase("plan")
    assert by_id(plan)["brave-browser"]["status"] == "planned"
    _, applied = sb.run_phase("apply")
    assert by_id(applied)["brave-browser"]["status"] == "success"
    assert sb.upgrades() == ["upgrade --cask brave-browser"]


def _verify(sb: Sandbox, applied_ids, targets=None):
    """applied_ids: ids apply marked success; targets: optional {id: target_version}."""
    targets = targets or {}
    out = sb.tmp / "out"
    rid = str(uuid.uuid4())
    (out / rid).mkdir(parents=True)
    (out / rid / "apply__brew.json").write_text(json.dumps(
        {"items": [{"id": i, "status": "success", "target_version": targets.get(i)}
                   for i in applied_ids]}))
    res = subprocess.run(
        ["bash", str(SCRIPTS / "verify.sh"), "--run-id", rid, "--trigger", "cli",
         "--profile", "default", "--output-dir", str(out)],
        capture_output=True, text=True, env=sb.env)
    return res, json.loads((out / rid / "verify__brew.json").read_text())


def test_verify_greedy_residue_unverified_is_skipped_others_still_fail(tmp_path):
    """GC5: brew still lists the cask and the applied version is NOT installed -> unverified."""
    sb = Sandbox(tmp_path,
                 outdated=[outdated_entry("slack", "3", "4")],
                 greedy_outdated=[outdated_entry("brave-browser", "1.0", "2.0")],
                 installed=[("brave-browser", "1.0")])
    sb.greedy_file("brave-browser")
    res, sc = _verify(sb, ["brave-browser", "slack", "gone-cask"], {"brave-browser": "2.0"})
    items = by_id(sc)
    brave = items["brave-browser"]
    assert brave["status"] == "skipped"                       # never `success` / up_to_date
    assert (brave["current_version"], brave["target_version"]) == ("1.0", "2.0")  # measurement kept
    msg = message(sc, "CASK_GREEDY_RESIDUE")
    assert msg["level"] == "warn" and "unverified" in msg["text"]
    assert "applied version 2.0 is not installed" in msg["text"]
    assert items["slack"]["status"] == "failed"      # a normal cask still outdated = real failure
    assert items["gone-cask"]["status"] == "success"  # no longer outdated
    assert res.returncode == 1                        # only because of slack


def test_verify_greedy_residue_without_applied_target_is_unverified(tmp_path):
    sb = Sandbox(tmp_path, greedy_outdated=[outdated_entry("brave-browser", "1.0", "2.0")],
                 installed=[("brave-browser", "1.0")])
    sb.greedy_file("brave-browser")
    res, sc = _verify(sb, ["brave-browser"])          # apply item carries no target_version
    assert res.returncode == 0
    assert by_id(sc)["brave-browser"]["status"] == "skipped"
    assert sc["status"] != "failed"


def test_verify_greedy_residue_is_success_when_applied_version_is_measured_installed(tmp_path):
    """brew still lists it (auto_updates/:latest), but installed_versions contains what apply installed."""
    still_listed = {"name": ["brave-browser"], "installed_versions": ["2.0"], "current_version": "2.1"}
    sb = Sandbox(tmp_path, greedy_outdated=[still_listed], installed=[("brave-browser", "2.0")])
    sb.greedy_file("brave-browser")
    res, sc = _verify(sb, ["brave-browser"], {"brave-browser": "2.0"})
    assert res.returncode == 0, res.stderr
    brave = by_id(sc)["brave-browser"]
    assert brave["status"] == "success"
    assert brave["current_version"] == "2.0"
    msg = message(sc, "CASK_GREEDY_RESIDUE")
    assert msg["level"] == "info" and "2.0 is installed (measured)" in msg["text"]

    # string-shaped installed_versions works too
    string_shape = Sandbox(tmp_path / "s", greedy_outdated=[
        {"name": "brave-browser", "installed_versions": "2.0", "current_version": "2.1"}],
        installed=[("brave-browser", "2.0")])
    string_shape.greedy_file("brave-browser")
    _, sc2 = _verify(string_shape, ["brave-browser"], {"brave-browser": "2.0"})
    assert by_id(sc2)["brave-browser"]["status"] == "success"


# ── orphan casks ─────────────────────────────────────────────────────────────

GONE = cask("zzz-gone", artifacts=[{"app": ["Gone Away.app"]}])


@pytest.mark.parametrize("phase", ["check", "plan", "apply"])
def test_outdated_orphan_cask_is_skipped_with_remedy_and_never_upgraded(tmp_path, phase):
    sb = Sandbox(tmp_path, outdated=[outdated_entry("zzz-gone")], info=[GONE], installed=[("zzz-gone", "1.0")])
    res, sc = sb.run_phase(phase)
    assert res.returncode == 0, res.stderr
    item = by_id(sc)["zzz-gone"]
    assert item["status"] == "skipped"
    assert (item["current_version"], item["target_version"]) == ("1.0", "2.0")
    msg = message(sc, "CASK_ORPHAN")
    assert msg["text"].startswith("zzz-gone: ")
    assert "brew uninstall --cask --force zzz-gone" in msg["text"]
    assert "ASCENDO-ACTION-REQUIRED reason=cask_orphan cur=1.0 cand=2.0" in msg["text"]
    assert sb.upgrades() == []


def test_check_does_not_list_an_up_to_date_orphan_as_installed(tmp_path):
    present = cask("zzz-present", artifacts=[{"app": ["Present.app"]}])
    pkg_only = cask("zzz-pkg", artifacts=[{"pkg": ["x.pkg"]}])  # never an orphan
    sb = Sandbox(tmp_path, info=[GONE, present, pkg_only], apps=["Present.app"],
                 installed=[("zzz-gone", "1.0"), ("zzz-present", "1.0"), ("zzz-pkg", "1.0")])
    res, sc = sb.run_phase("check")
    assert res.returncode == 0, res.stderr
    items = by_id(sc)
    assert items["zzz-gone"]["status"] == "skipped"
    assert items["zzz-present"]["status"] == "up_to_date"
    assert items["zzz-pkg"]["status"] == "up_to_date"
    assert "ASCENDO-ACTION-REQUIRED reason=cask_orphan" in message(sc, "CASK_ORPHAN")["text"]
    # facts for every installed cask came from ONE batched info call
    assert [c for c in sb.brew_calls() if c.startswith("info")] == [
        "info --json=v2 --cask zzz-gone zzz-present zzz-pkg"]


def test_orphan_is_never_uninstalled(tmp_path):
    sb = Sandbox(tmp_path, outdated=[outdated_entry("zzz-gone")], info=[GONE])
    sb.run_phase("apply")
    assert not [c for c in sb.brew_calls() if c.startswith(("uninstall", "remove", "zap"))]


# ── downgrade guard (recorded version + bundle relation) ─────────────────────

BRAVE_ARTIFACTS = [{"app": ["Brave Browser.app"]}]


def _brave_sandbox(tmp, *, cask_version, recorded, bundle_version):
    sb = Sandbox(tmp, outdated=[outdated_entry("brave-browser", recorded, cask_version)],
                 info=[cask("brave-browser", version=cask_version, installed=recorded,
                            artifacts=BRAVE_ARTIFACTS)],
                 installed=[("brave-browser", recorded)])
    sb.add_app("Brave Browser.app", bundle_version)
    return sb


def test_brave_bundle_with_chromium_prefix_is_not_skipped_forever(tmp_path):
    """Bundle 151.1.93.138 vs cask 1.93.138.0 is the SAME version: upgrade proceeds."""
    sb = _brave_sandbox(tmp_path, cask_version="1.93.139.0", recorded="1.93.138.0",
                        bundle_version="151.1.93.138")
    res, sc = sb.run_phase("apply")
    assert res.returncode == 0, res.stderr
    assert by_id(sc)["brave-browser"]["status"] == "success"
    assert sb.upgrades() == ["upgrade --cask brave-browser"]
    assert not any(m.get("code") == "CASK_DOWNGRADE_GUARD" for m in sc["messages"])
    _, chk = _brave_sandbox(tmp_path / "c", cask_version="1.93.139.0", recorded="1.93.138.0",
                            bundle_version="151.1.93.138").run_phase("check")
    assert by_id(chk)["brave-browser"]["status"] == "planned"


def test_recorded_version_newer_than_cask_is_a_downgrade(tmp_path):
    sb = _brave_sandbox(tmp_path, cask_version="1.0", recorded="2.0", bundle_version="2.0")
    for phase in ("check", "plan", "apply"):
        res, sc = sb.run_phase(phase)
        item = by_id(sc)["brave-browser"]
        assert item["status"] == "skipped", phase
        assert item["target_version"] == item["current_version"]
        assert "downgrade" in message(sc, "CASK_DOWNGRADE_GUARD")["text"]
    assert sb.upgrades() == []


def test_bundle_provably_newer_than_cask_is_a_downgrade(tmp_path):
    sb = _brave_sandbox(tmp_path, cask_version="4.0", recorded="3.0", bundle_version="5.0")
    res, sc = sb.run_phase("apply")
    assert by_id(sc)["brave-browser"]["status"] == "skipped"
    assert "installed app 5.0 is newer than cask 4.0" in message(sc, "CASK_DOWNGRADE_GUARD")["text"]
    assert sb.upgrades() == []


def test_incomparable_schemes_proceed_and_leave_a_note(tmp_path):
    sb = _brave_sandbox(tmp_path, cask_version="3.2.1", recorded="3.2.0", bundle_version="20260101")
    res, sc = sb.run_phase("apply")
    assert by_id(sc)["brave-browser"]["status"] == "success"
    assert "different version schemes" in message(sc, "CASK_VERSION_SCHEME")["text"]


def test_downgrade_helper_matrix(tmp_path):
    sb = Sandbox(tmp_path, info=[cask("brave-browser", version="1.93.138.0", installed="1.93.138.0",
                                      artifacts=BRAVE_ARTIFACTS)])
    sb.add_app("Brave Browser.app", "151.1.93.138")
    res = sb.bash('ascendo_brew_cask_would_downgrade brave-browser 1.93.138.0; echo same:$?; '
                  'ascendo_brew_cask_would_downgrade brave-browser 151.2.0; echo up:$?')
    assert "same:1" in res.stdout and "up:1" in res.stdout


def test_bundle_located_through_caskroom_when_cask_has_no_app_target(tmp_path):
    c = cask("zzz-room", version="1.0", installed="2.0", artifacts=[{"binary": ["x"]}])
    sb = Sandbox(tmp_path, info=[c])
    room = tmp_path / "prefix" / "Caskroom" / "zzz-room" / "2.0"
    (room / "Room.app" / "Contents").mkdir(parents=True)
    (room / "Room.app" / "Contents" / "Info.plist").write_bytes(
        plistlib.dumps({"CFBundleShortVersionString": "9.0"}))
    res = sb.bash("ascendo_brew_load_cask_facts zzz-room; ascendo_brew_cask_disposition zzz-room 1.0")
    assert res.stdout.strip().startswith("downgrade|")


# ── sudo-requiring casks ─────────────────────────────────────────────────────

PKG_CASK = cask("zzz-pkg", artifacts=[{"pkg": ["setup.pkg"]}])


def test_sudo_cask_is_deferred_without_askpass(tmp_path):
    sb = Sandbox(tmp_path, outdated=[outdated_entry("zzz-pkg"), outdated_entry("zzz-fancy")],
                 info=[PKG_CASK, FANCY], apps=["Fancy Thing.app"])
    res, sc = sb.run_phase("apply")
    assert res.returncode == 0, res.stderr
    items = by_id(sc)
    assert items["zzz-pkg"]["status"] == "skipped"
    assert (items["zzz-pkg"]["current_version"], items["zzz-pkg"]["target_version"]) == ("1.0", "2.0")
    assert items["zzz-fancy"]["status"] == "success"
    assert sb.upgrades() == ["upgrade --cask zzz-fancy"]
    msg = message(sc, "CASK_NEEDS_ADMIN")
    assert msg["text"].startswith("zzz-pkg: ")
    assert "brew upgrade --cask zzz-pkg" in msg["text"]
    assert "ASCENDO-ACTION-REQUIRED reason=cask_needs_admin cur=1.0 cand=2.0" in msg["text"]


def test_sudo_cask_is_upgraded_when_askpass_is_wired(tmp_path):
    sb = Sandbox(tmp_path, outdated=[outdated_entry("zzz-pkg")], info=[PKG_CASK])
    helper = tmp_path / "askpass.sh"
    helper.write_text("#!/bin/sh\necho pw\n")
    helper.chmod(0o755)
    sb.env["SUDO_ASKPASS"] = str(helper)
    res, sc = sb.run_phase("apply")
    assert by_id(sc)["zzz-pkg"]["status"] == "success"
    assert sb.upgrades() == ["upgrade --cask zzz-pkg"]
    assert not any(m.get("code") == "CASK_NEEDS_ADMIN" for m in sc["messages"])


def test_non_executable_askpass_does_not_count(tmp_path):
    sb = Sandbox(tmp_path, outdated=[outdated_entry("zzz-pkg")], info=[PKG_CASK])
    sb.env["SUDO_ASKPASS"] = str(tmp_path / "missing-helper")
    _, sc = sb.run_phase("apply")
    assert by_id(sc)["zzz-pkg"]["status"] == "skipped"


def test_sudo_cask_stays_planned_in_plan_with_a_note(tmp_path):
    sb = Sandbox(tmp_path, outdated=[outdated_entry("zzz-pkg")], info=[PKG_CASK])
    _, sc = sb.run_phase("plan")
    assert by_id(sc)["zzz-pkg"]["status"] == "planned"
    assert "administrator rights" in message(sc, "CASK_NEEDS_ADMIN")["text"]


def test_dry_run_apply_never_upgrades_or_probes_admin(tmp_path):
    sb = Sandbox(tmp_path, outdated=[outdated_entry("zzz-pkg")], info=[PKG_CASK])
    _, sc = sb.run_phase("apply", "--dry-run")
    assert by_id(sc)["zzz-pkg"]["status"] == "planned"
    assert sb.upgrades() == []


# ── fail-safe: no facts => never upgrade blind ───────────────────────────────

def test_cask_without_facts_and_without_table_entry_is_not_upgraded(tmp_path):
    """brew info fails for it (unknown to the fake), and it is in no token table."""
    sb = Sandbox(tmp_path, outdated=[outdated_entry("zzz-mystery")], info=[])
    res, sc = sb.run_phase("apply")
    assert res.returncode == 0, res.stderr
    item = by_id(sc)["zzz-mystery"]
    assert item["status"] == "skipped"
    assert (item["current_version"], item["target_version"]) == ("1.0", "2.0")  # candidate kept
    msg = message(sc, "APP_FACTS_UNAVAILABLE")
    assert msg["text"].startswith("zzz-mystery: ")
    assert "cannot tell whether the app is running" in msg["text"]
    assert "reason=app_facts_unavailable cur=1.0 cand=2.0" in msg["text"]
    assert sb.upgrades() == []
    assert sb.killed() == []


def test_table_cask_without_facts_still_upgrades_when_idle(tmp_path):
    """Last-resort table keeps working: slack has no facts but a table entry."""
    sb = Sandbox(tmp_path, outdated=[outdated_entry("slack", "3", "4")], info=[], apps=["Slack.app"])
    res, sc = sb.run_phase("apply")
    assert by_id(sc)["slack"]["status"] == "success"
    assert sb.upgrades() == ["upgrade --cask slack"]


def test_cask_with_facts_but_no_app_or_quit_ids_upgrades(tmp_path):
    """Facts loaded (cli-only cask): known state, nothing to be running -> proceed."""
    cli = cask("zzz-cli", artifacts=[{"binary": ["x"]}])
    sb = Sandbox(tmp_path, outdated=[outdated_entry("zzz-cli")], info=[cli])
    _, sc = sb.run_phase("apply")
    assert by_id(sc)["zzz-cli"]["status"] == "success"


def test_per_token_fallback_when_one_unknown_token_rejects_the_batch(tmp_path):
    outdated = [outdated_entry("zzz-fancy"), outdated_entry("zzz-unknown"), outdated_entry("zzz-two")]
    sb = Sandbox(tmp_path, outdated=outdated,
                 info=[FANCY, cask("zzz-two", artifacts=[{"app": ["Two.app"]}])],
                 apps=["Fancy Thing.app", "Two.app"], running_apps=["Fancy Thing"])
    res, sc = sb.run_phase("apply")
    assert res.returncode == 0, res.stderr
    infos = [c for c in sb.brew_calls() if c.startswith("info")]
    # 1 rejected batch, then ONE call per token still missing facts (all 3: the
    # batch yielded nothing); after that facts exist for fancy/two, not unknown.
    assert infos[0] == "info --json=v2 --cask zzz-fancy zzz-unknown zzz-two"
    assert set(infos[1:]) == {"info --json=v2 --cask zzz-fancy", "info --json=v2 --cask zzz-unknown",
                              "info --json=v2 --cask zzz-two"}
    items = by_id(sc)
    assert items["zzz-fancy"]["status"] == "skipped"           # facts found -> running detected
    assert message(sc, "CASK_APP_RUNNING")["text"].startswith("zzz-fancy: ")
    assert items["zzz-two"]["status"] == "success"             # facts found -> idle -> upgraded
    assert items["zzz-unknown"]["status"] == "skipped"          # no facts -> fail-safe
    assert message(sc, "APP_FACTS_UNAVAILABLE")["text"].startswith("zzz-unknown: ")
    assert sb.upgrades() == ["upgrade --cask zzz-two"]


def test_load_facts_keeps_facts_for_known_tokens_when_batch_is_rejected(tmp_path):
    sb = Sandbox(tmp_path, info=[FANCY])
    res = sb.bash("ascendo_brew_load_cask_facts zzz-fancy zzz-unknown; "
                  "ascendo_brew_cask_facts_line zzz-fancy; echo; "
                  "ascendo_brew_cask_facts_line zzz-unknown; echo END")
    lines = res.stdout.splitlines()
    assert lines[0] == "zzz-fancy|2.0|1.0|Fancy Thing.app||0"
    assert lines[1:] == ["END"]                       # unknown token: no line, no re-query
    assert [c for c in sb.brew_calls() if c.startswith("info")] == [
        "info --json=v2 --cask zzz-fancy zzz-unknown",   # rejected batch
        "info --json=v2 --cask zzz-fancy",               # per-token fallback ...
        "info --json=v2 --cask zzz-unknown",             # ... (fails, stays unknown)
    ]


def test_hanging_brew_info_is_bounded_by_the_timeout(tmp_path):
    import time

    sb = Sandbox(tmp_path, outdated=[outdated_entry("zzz-fancy")], info=[FANCY],
                 apps=["Fancy Thing.app"], info_sleep=30)
    sb.env["ASCENDO_BREW_INFO_TIMEOUT"] = "1"
    sb.env["ASCENDO_KILL_AFTER"] = "1"
    start = time.time()
    res, sc = sb.run_phase("apply")
    elapsed = time.time() - start
    assert res.returncode == 0, res.stderr
    assert elapsed < 20, f"phase took {elapsed:.1f}s: brew info must be killed at the deadline"
    assert by_id(sc)["zzz-fancy"]["status"] == "skipped"
    assert message(sc, "APP_FACTS_UNAVAILABLE")
    assert message(sc, "CASK_FACTS_UNAVAILABLE")["text"].startswith("zzz-fancy: ")
    assert sb.upgrades() == []


# ── facts-load failure is visible ────────────────────────────────────────────

@pytest.mark.parametrize("phase", ["check", "plan", "apply"])
def test_missing_cask_facts_emit_cask_facts_unavailable(tmp_path, phase):
    """slack has no facts (brew info rejects it) but a table entry: the fallback is announced."""
    sb = Sandbox(tmp_path, outdated=[outdated_entry("slack", "3", "4"), outdated_entry("zzz-fancy")],
                 info=[FANCY], apps=["Slack.app", "Fancy Thing.app"],
                 installed=[("slack", "3"), ("zzz-fancy", "1.0")])
    res, sc = sb.run_phase(phase)
    assert res.returncode == 0, res.stderr
    msgs = [m for m in sc["messages"] if m.get("code") == "CASK_FACTS_UNAVAILABLE"]
    assert [m["text"].split(":")[0] for m in msgs] == ["slack"]   # only the cask without facts
    assert msgs[0]["level"] == "warn"
    assert "built-in table" in msgs[0]["text"] and "admin rights are assumed not needed" in msgs[0]["text"]


def test_no_cask_facts_warning_when_all_facts_load(tmp_path):
    sb = Sandbox(tmp_path, outdated=[outdated_entry("zzz-fancy")], info=[FANCY], apps=["Fancy Thing.app"])
    _, sc = sb.run_phase("apply")
    assert not any(m.get("code") == "CASK_FACTS_UNAVAILABLE" for m in sc["messages"])


def test_brew_info_timeout_defaults_to_120s_and_is_overridable():
    text = LIB.read_text()
    assert 'ascendo_run_with_timeout "${ASCENDO_BREW_INFO_TIMEOUT:-120}" brew info' in text
    body = text[text.index("ascendo_brew_load_cask_facts() {"):]
    body = body[:body.index("\n}\n")]
    assert "brew info" not in body.replace("`brew info`", "")  # every call goes through the bounded wrapper
    assert body.count("_ascendo_brew_info") == 2               # batched + per-token fallback


# ── source hygiene ───────────────────────────────────────────────────────────

def test_phase_scripts_use_the_new_guard_helpers_not_the_table():
    for name in ("check.sh", "plan.sh", "apply.sh"):
        text = (SCRIPTS / name).read_text()
        assert "ascendo_brew_cask_disposition" in text, name
        assert "ascendo_brew_prepare_cask_facts" in text, name
        assert "ascendo_brew_greedy_tokens" in text, name
        assert "ascendo_brew_cask_app_name" not in text, name
    assert "ascendo_brew_greedy_tokens" in (SCRIPTS / "verify.sh").read_text()
