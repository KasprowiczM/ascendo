"""Hardened vendor installer (Task 10): vendor_direct_install + copy_verified_app.

Everything runs against fakes. No real installer, no real /Applications, no
network: curl / hdiutil / spctl / codesign / mv / lsappinfo are stand-ins (env
seams ASCENDO_WEB_*_BIN, ASCENDO_LSAPPINFO_BIN), the "Applications" root is a
temp dir (ASCENDO_WEB_APPS_ROOT) and PATH is fake-bin + system dirs only, with
python3 borrowed. `sudo`, `brew`, `osascript`, `pkill`, `killall`, `kill` on PATH
are logging stubs: any call to them fails the relevant assertion.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

ADAPTER = Path(__file__).resolve().parents[1]
LIB = ADAPTER / "lib"
SCRIPTS = ADAPTER / "scripts" / "web"
BID = "com.example.app"
TEAM = "ABCDE12345"
PYTHON3 = sys.executable  # whichever interpreter runs pytest (3.12 in CI)


def make_app(parent: Path, name: str = "Example.app", *, bid: str = BID, ver: str = "1.0",
             team: str | None = TEAM, payload: str = "old", sig_bid: str | None = None,
             sig_team_text: str | None = None) -> Path:
    app = parent / name
    (app / "Contents").mkdir(parents=True, exist_ok=True)
    (app / "Contents" / "Info.plist").write_bytes(plistlib.dumps(
        {"CFBundleIdentifier": bid, "CFBundleShortVersionString": ver}))
    (app / "Contents" / "payload.txt").write_text(payload)
    sig = f"Identifier={sig_bid or bid}\n"
    if sig_team_text is not None:
        sig += f"TeamIdentifier={sig_team_text}\n"
    elif team:
        sig += f"TeamIdentifier={team}\n"
    (app / "Contents" / "_sig").write_text(sig)
    return app


def zip_of(dest: Path, *apps: Path) -> Path:
    with zipfile.ZipFile(dest, "w") as z:
        for app in apps:
            for f in sorted(app.rglob("*")):
                z.write(f, f.relative_to(app.parent).as_posix())
    return dest


def targz_of(dest: Path, *apps: Path) -> Path:
    with tarfile.open(dest, "w:gz") as t:
        for app in apps:
            t.add(app, arcname=app.name)
    return dest


def script(path: Path, body: str) -> Path:
    path.write_text("#!/bin/bash\n" + body)
    path.chmod(0o755)
    return path


class Sandbox:
    def __init__(self, tmp: Path, ls_states=("false",)):
        self.tmp = tmp
        self.apps = tmp / "Applications"
        self.apps.mkdir()
        self.fx = tmp / "fixtures"
        self.fx.mkdir()
        self.bin = tmp / "bin"
        self.bin.mkdir()
        self.log = tmp / "logs"
        self.log.mkdir()
        (tmp / "home").mkdir()
        (tmp / "tmp").mkdir()
        self.set_ls(*ls_states)
        (self.fx / "curl.map").write_text("")
        for name in ("sudo", "brew", "osascript", "pkill", "killall", "kill", "open"):
            script(self.bin / name, f'echo "{name} $*" >> "{self.log}/forbidden.log"\nexit 1\n')
        os.symlink(PYTHON3, self.bin / "python3")
        script(self.bin / "curl", self._curl_body())
        script(self.bin / "hdiutil", self._hdiutil_body())
        script(self.bin / "spctl", self._spctl_body())
        script(self.bin / "codesign", self._codesign_body())
        script(self.bin / "mv", self._mv_body())
        script(self.bin / "ditto", self._ditto_body())
        script(self.bin / "lsappinfo", self._ls_body())

    # ---- fake tools -------------------------------------------------------
    def _curl_body(self):
        return f'''
echo "$*" >> "{self.log}/curl.log"
[ -n "$FAKE_CURL_RC" ] && exit "$FAKE_CURL_RC"
out=""; url=""
while [ $# -gt 0 ]; do
  case "$1" in -o) out="$2"; shift ;; https://*|http://*|file://*) url="$1" ;; esac
  shift
done
payload=""
while IFS="$(printf '\\t')" read -r pat file; do
  [ -n "$pat" ] || continue
  case "$url" in *"$pat"*) payload="$file"; break ;; esac
done < "{self.fx}/curl.map"
[ -n "$payload" ] || {{ echo "fake curl: no payload for $url" >&2; exit 22; }}
if [ -n "$out" ]; then cat "$payload" > "$out"; else cat "$payload"; fi
'''

    def _hdiutil_body(self):
        return f'''
echo "$*" >> "{self.log}/hdiutil.log"
case "$1" in
  verify) exit "${{FAKE_HDIUTIL_VERIFY_RC:-0}}" ;;
  attach)
    [ "${{FAKE_HDIUTIL_ATTACH_RC:-0}}" = 0 ] || exit "$FAKE_HDIUTIL_ATTACH_RC"
    echo "attach-stdin-bytes=$(cat | wc -c | tr -d ' ')" >> "{self.log}/hdiutil.stdin"
    dmg="$2"; shift 2; mp=""
    while [ $# -gt 0 ]; do [ "$1" = -mountpoint ] && {{ mp="$2"; shift; }}; shift; done
    [ -n "$mp" ] || exit 5
    cp -R "$(cat "$dmg")"/. "$mp"/
    exit 0 ;;
  detach) exit "${{FAKE_HDIUTIL_DETACH_RC:-0}}" ;;
esac
exit 1
'''

    def _spctl_body(self):
        return f'''
for a; do last="$a"; done
echo "spctl $*" >> "{self.log}/spctl.log"
n=$(( $(cat "{self.log}/spctl.count" 2>/dev/null || echo 0) + 1 )); echo $n > "{self.log}/spctl.count"
case ",${{FAKE_SPCTL_FAIL_ON:-}}," in *",$n,"*) exit 3 ;; esac
[ -f "$last/Contents/_spctl_fail" ] && exit 3
exit 0
'''

    def _codesign_body(self):
        return '''
for a; do last="$a"; done
f="$last/Contents/_sig"
[ -f "$f" ] || { echo "$last: code object is not signed at all" >&2; exit 1; }
cat "$f" >&2
exit 0
'''

    def _mv_body(self):
        return f'''
echo "mv $*" >> "{self.log}/mv.log"
n=$(( $(cat "{self.log}/mv.count" 2>/dev/null || echo 0) + 1 )); echo $n > "{self.log}/mv.count"
case ",${{FAKE_MV_FAIL_ON:-}}," in *",$n,"*) echo "fake mv: forced failure #$n" >&2; exit 1 ;; esac
exec /bin/mv "$@"
'''

    def _ditto_body(self):
        return f'''
echo "ditto $*" >> "{self.log}/ditto.log"
case "$*" in *"${{FAKE_DITTO_FAIL_MATCH:-__never__}}"*) exit 1 ;; esac
exec /usr/bin/ditto "$@"
'''

    def _ls_body(self):
        return f'''
n=$(( $(cat "{self.log}/ls.count" 2>/dev/null || echo 0) + 1 )); echo $n > "{self.log}/ls.count"
st=$(sed -n "${{n}}p" "{self.fx}/ls.seq"); [ -n "$st" ] || st=$(tail -n 1 "{self.fx}/ls.seq")
case "$st" in
  true) echo 'bundleID="com.apple.finder"'; echo 'bundleID="{BID}"' ;;
  false) echo 'bundleID="com.apple.finder"' ;;
  unknown) ;;
  fail) exit 1 ;;
esac
exit 0
'''

    # ---- controls ---------------------------------------------------------
    def set_ls(self, *states):
        (self.fx / "ls.seq").write_text("\n".join(states) + "\n")
        (self.log / "ls.count").unlink(missing_ok=True)

    def serve(self, url_part: str, payload: Path):
        with open(self.fx / "curl.map", "a") as f:
            f.write(f"{url_part}\t{payload}\n")

    def dmg_of(self, *apps: Path, name="Example.dmg") -> Path:
        root = self.fx / (name + ".contents")
        root.mkdir()
        for app in apps:
            shutil.copytree(app, root / app.name, symlinks=True)
        dmg = self.fx / name
        dmg.write_text(str(root))
        return dmg

    def env(self, **extra):
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("ASCENDO_", "MAC_UPDATE_", "FAKE_")) and k != "BUN_INSTALL"}
        env.update({
            "PATH": f"{self.bin}:/usr/bin:/bin:/usr/sbin:/sbin",
            "HOME": str(self.tmp / "home"),
            "TMPDIR": str(self.tmp / "tmp"),
            "ASCENDO_NPM_BREW_DIRS": "",
            "ASCENDO_WEB_APPS_ROOT": str(self.apps),
            "ASCENDO_WEB_TMP_DIR": str(self.tmp / "tmp"),
            "ASCENDO_WEB_CACHE_DIR": str(self.tmp / "cache"),
            "ASCENDO_WEB_CURL_BIN": str(self.bin / "curl"),
            "ASCENDO_WEB_HDIUTIL_BIN": str(self.bin / "hdiutil"),
            "ASCENDO_WEB_SPCTL_BIN": str(self.bin / "spctl"),
            "ASCENDO_WEB_CODESIGN_BIN": str(self.bin / "codesign"),
            "ASCENDO_WEB_MV_BIN": str(self.bin / "mv"),
            "ASCENDO_WEB_DITTO_BIN": str(self.bin / "ditto"),
            "ASCENDO_LSAPPINFO_BIN": str(self.bin / "lsappinfo"),
            "ASCENDO_WEB_HOST_OS_VERSION": "27.0",
            "ASCENDO_WEB_HOST_ARCH": "arm64",
        })
        env.update({k: str(v) for k, v in extra.items()})
        return env

    def bash(self, snippet: str, stdin: str | None = None, **extra) -> subprocess.CompletedProcess:
        return subprocess.run(["bash", "-c", f'. "{LIB}/ascendo_web.sh"; {snippet}'],
                              capture_output=True, text=True, env=self.env(**extra), input=stdin)

    def install(self, url="https://dl.example.com/App.zip", artifact="zip", ck="-", cs="-",
                host="dl.example.com", expected="", installed=None, stdin=None, **extra):
        installed = installed or (self.apps / "Example.app")
        return self.bash(
            f'_web_vendor_install example "{url}" "{artifact}" "{ck}" "{cs}" "{host}" "{expected}" "{installed}"; '
            'echo "rc=$?"', stdin=stdin, **extra)

    # ---- observations -----------------------------------------------------
    def rc(self, r) -> int:
        return int(r.stdout.strip().splitlines()[-1].split("=")[1])

    def read_log(self, name) -> list[str]:
        p = self.log / name
        return p.read_text().splitlines() if p.exists() else []

    def payload(self, name="Example.app") -> str:
        return (self.apps / name / "Contents" / "payload.txt").read_text()

    def leftovers(self) -> list[str]:
        return sorted(p.name for p in self.apps.iterdir() if p.name.startswith(".ascendo_"))

    def forbidden(self) -> list[str]:
        return self.read_log("forbidden.log")


@pytest.fixture
def sb(tmp_path):
    return Sandbox(tmp_path)


def prime(sb: Sandbox, *, new_ver="2.0", new_payload="new", new_team=TEAM, new_bid=BID, archive="zip"):
    """Installed 1.0 app + a vendor archive containing 2.0, served at /App.<ext>."""
    old = make_app(sb.apps, ver="1.0", payload="old")
    new_parent = sb.fx / "new"
    new_parent.mkdir(exist_ok=True)
    new = make_app(new_parent, ver=new_ver, payload=new_payload, team=new_team, bid=new_bid)
    if archive == "zip":
        sb.serve("/App.zip", zip_of(sb.fx / "App.zip", new))
    elif archive == "tar.gz":
        sb.serve("/App.tar.gz", targz_of(sb.fx / "App.tar.gz", new))
    else:
        sb.serve("/App.dmg", sb.dmg_of(new, name="App.dmg"))
    return old, new


# ============================================================================
# pure helpers
# ============================================================================

@pytest.mark.parametrize("url,host,ok", [
    ("https://dl.example.com/a.zip", "dl.example.com", True),
    ("https://dl.example.com:443/a.zip", "dl.example.com", True),
    ("http://dl.example.com/a.zip", "dl.example.com", False),
    ("https://evil.example.com/a.zip", "dl.example.com", False),
    ("https://dl.example.com.evil.net/a.zip", "dl.example.com", False),
    ("https://evil.net/dl.example.com/a.zip", "dl.example.com", False),
    ("https://dl.example.com@evil.net/a.zip", "dl.example.com", False),
    ("https://dl.example.com", "dl.example.com", True),
    ("ftp://dl.example.com/a.zip", "dl.example.com", False),
    ("https://dl.example.com/a.zip", "", False),
])
def test_vendor_direct_host_allowlist(sb, url, host, ok):
    r = sb.bash(f'vendor_direct_host_allowed "{url}" "{host}" && echo y || echo n')
    assert r.stdout.strip() == ("y" if ok else "n")


def test_vendor_direct_checksum_kinds(sb):
    f = sb.fx / "blob"
    f.write_bytes(b"hello vendor payload")
    sha256 = hashlib.sha256(f.read_bytes()).hexdigest()
    sha512 = hashlib.sha512(f.read_bytes()).hexdigest()
    b64 = base64.b64encode(hashlib.sha512(f.read_bytes()).digest()).decode()

    def ok(kind, val):
        r = sb.bash(f'vendor_direct_verify_checksum "{f}" "{kind}" "{val}" && echo y || echo n')
        return r.stdout.strip() == "y"

    assert ok("sha256hex", sha256) and ok("sha256hex", sha256.upper())
    assert ok("sha512hex", sha512) and ok("sha512hex", sha512.upper())
    assert ok("sha512b64", b64)
    assert not ok("sha512b64", b64.lower()) and not ok("sha512b64", b64.upper())  # case-sensitive
    assert not ok("sha256hex", "0" * 64) and not ok("sha512hex", "0" * 128)
    assert not ok("sha512b64", base64.b64encode(b"x" * 64).decode())
    # no kind / no value => nothing to verify; unknown kind => refuse
    assert ok("-", "") and ok("", "") and ok("sha256hex", "-") and ok("sha256hex", "")
    assert not ok("md5hex", "abcd")
    assert not ok("sha256hex", "z" * 64)


def test_fetch_refuses_http_and_never_reaches_curl(sb):
    for url in ("http://dl.example.com/a.zip", "file:///etc/passwd", "ftp://x/y", ""):
        r = sb.bash(f'_web_fetch_to_file "{url}" "{sb.fx}/out.bin" 60 1000; echo "rc=$?"')
        assert sb.rc(r) != 0, url
    assert sb.read_log("curl.log") == []


def test_fetch_is_https_bound_bounded_and_atomic(sb):
    (sb.fx / "p").write_text("payload!")
    sb.serve("/a.bin", sb.fx / "p")
    dest = sb.fx / "out.bin"
    r = sb.bash(f'_web_fetch_to_file "https://dl.example.com/a.bin" "{dest}" 123 4096; echo "rc=$?"')
    assert sb.rc(r) == 0 and dest.read_text() == "payload!"
    assert not Path(str(dest) + ".part").exists()
    (call,) = sb.read_log("curl.log")
    for flag in ("--proto =https", "--proto-redir =https", "--max-filesize 4096", "--max-time 123"):
        assert flag in call, call
    assert f"-o {dest}.part" in call


def test_fetch_enforces_size_cap_empty_file_and_curl_failure(sb):
    (sb.fx / "big").write_text("x" * 50)
    (sb.fx / "empty").write_text("")
    sb.serve("/big", sb.fx / "big")
    sb.serve("/empty", sb.fx / "empty")
    for url, extra in (("https://dl.example.com/big", {}), ("https://dl.example.com/empty", {}),
                       ("https://dl.example.com/big", {"FAKE_CURL_RC": 56})):
        dest = sb.fx / "o.bin"
        r = sb.bash(f'_web_fetch_to_file "{url}" "{dest}" 60 10; echo "rc=$?"', **extra)
        assert sb.rc(r) != 0, url
        assert not dest.exists() and not Path(str(dest) + ".part").exists(), url


# ============================================================================
# vendor_direct_install: pipeline
# ============================================================================

def test_vendor_direct_dry_run_returns_2_and_touches_nothing(sb):
    prime(sb)
    for var in ("ASCENDO_DRY_RUN", ):
        r = sb.install(**{var: "1"})
        assert sb.rc(r) == 2, r.stderr
    assert sb.read_log("curl.log") == [] and sb.payload() == "old" and sb.leftovers() == []


def test_vendor_direct_refuses_non_applications_path(sb):
    prime(sb)
    other = make_app(sb.fx, "Other.app")
    for path in (other, Path("/Applications/../tmp/X.app"), sb.apps / ".." / "x" / "Example.app"):
        r = sb.install(installed=path)
        assert sb.rc(r) == 1, (path, r.stderr)
    assert sb.read_log("curl.log") == [] and sb.payload() == "old"


def test_vendor_direct_rejects_host_mismatch_before_any_fetch(sb):
    prime(sb)
    r = sb.install(url="https://evil.example.net/App.zip")
    assert sb.rc(r) == 1 and "rejected" in r.stderr
    r = sb.install(url="http://dl.example.com/App.zip")
    assert sb.rc(r) == 1
    assert sb.read_log("curl.log") == [] and sb.payload() == "old"


def test_vendor_direct_missing_installed_app_is_refused(sb):
    prime(sb)
    shutil.rmtree(sb.apps / "Example.app")
    r = sb.install()
    assert sb.rc(r) == 1 and sb.read_log("curl.log") == []


@pytest.mark.parametrize("archive,ext,artifact", [("zip", "zip", "zip"), ("tar.gz", "tar.gz", "tar.gz"),
                                                  ("dmg", "dmg", "dmg")])
def test_install_succeeds_for_every_artifact_kind(sb, archive, ext, artifact):
    prime(sb, archive=archive)
    r = sb.install(url=f"https://dl.example.com/App.{ext}", artifact=artifact, expected="2.0")
    assert sb.rc(r) == 0, r.stderr
    assert sb.payload() == "new"
    assert (sb.apps / "Example.app" / "Contents" / "Info.plist").exists()
    assert sb.leftovers() == []
    assert sb.forbidden() == []
    assert sorted(p.name for p in sb.apps.iterdir()) == ["Example.app"]


def test_dmg_is_verified_attached_readonly_and_detached_without_force(sb):
    prime(sb, archive="dmg")
    r = sb.install(url="https://dl.example.com/App.dmg", artifact="dmg")
    assert sb.rc(r) == 0, r.stderr
    log = sb.read_log("hdiutil.log")
    verify = [ln for ln in log if ln.startswith("verify")]
    attach = [ln for ln in log if ln.startswith("attach")]
    detach = [ln for ln in log if ln.startswith("detach")]
    assert len(verify) == len(attach) == len(detach) == 1, log
    assert "-quiet" in verify[0]
    assert "-nobrowse" in attach[0] and "-readonly" in attach[0] and "-mountpoint" in attach[0]
    assert "-force" not in detach[0]
    assert log.index(verify[0]) < log.index(attach[0]) < log.index(detach[0])
    mnt = attach[0].split("-mountpoint ")[1].split()[0]
    assert detach[0].split()[1] == mnt


@pytest.mark.parametrize("how", ["no_app", "wrong_id", "ditto_fail"])
def test_dmg_is_always_detached_on_failure(sb, how):
    old = make_app(sb.apps, ver="1.0", payload="old")
    new_parent = sb.fx / "new"
    new_parent.mkdir()
    if how == "no_app":
        (new_parent / "readme.txt").write_text("no app here")
        dmg = sb.fx / "App.dmg"
        dmg.write_text(str(new_parent))
        sb.serve("/App.dmg", dmg)
    else:
        new = make_app(new_parent, ver="2.0", bid="com.other.app" if how == "wrong_id" else BID)
        sb.serve("/App.dmg", sb.dmg_of(new, name="App.dmg"))
    extra = {"FAKE_DITTO_FAIL_MATCH": "Example.app"} if how == "ditto_fail" else {}
    r = sb.install(url="https://dl.example.com/App.dmg", artifact="dmg", **extra)
    assert sb.rc(r) == 1, r.stderr
    log = sb.read_log("hdiutil.log")
    assert len([ln for ln in log if ln.startswith("attach")]) == 1
    assert len([ln for ln in log if ln.startswith("detach")]) == 1, log
    assert sb.payload() == "old" and sb.leftovers() == []


def test_dmg_failing_verify_never_attaches(sb):
    prime(sb, archive="dmg")
    r = sb.install(url="https://dl.example.com/App.dmg", artifact="dmg", FAKE_HDIUTIL_VERIFY_RC=1)
    assert sb.rc(r) == 1
    assert not [ln for ln in sb.read_log("hdiutil.log") if ln.startswith(("attach", "detach"))]
    assert sb.payload() == "old"


def test_checksum_mismatch_never_extracts_or_touches_the_app(sb):
    prime(sb)
    r = sb.install(ck="sha256hex", cs="0" * 64)
    assert sb.rc(r) == 1 and "checksum" in r.stderr.lower()
    assert sb.read_log("ditto.log") == [] and sb.payload() == "old" and sb.leftovers() == []


def test_checksum_match_installs(sb):
    prime(sb)
    digest = hashlib.sha256((sb.fx / "App.zip").read_bytes()).hexdigest()
    r = sb.install(ck="sha256hex", cs=digest)
    assert sb.rc(r) == 0, r.stderr
    assert sb.payload() == "new"


def test_wrong_bundle_id_archive_leaves_installed_app_untouched(sb):
    prime(sb, new_bid="com.attacker.app")
    r = sb.install()
    assert sb.rc(r) == 1 and "expected 1 app bundle with id" in r.stderr
    assert sb.payload() == "old" and sb.leftovers() == []
    assert not [ln for ln in sb.read_log("mv.log")]


def test_two_matching_bundles_are_ambiguous_and_refused(sb):
    old = make_app(sb.apps, ver="1.0", payload="old")
    d = sb.fx / "new"
    d.mkdir()
    a = make_app(d, "A.app", ver="2.0", payload="a")
    b = make_app(d, "B.app", ver="2.0", payload="b")
    sb.serve("/App.zip", zip_of(sb.fx / "App.zip", a, b))
    r = sb.install()
    assert sb.rc(r) == 1 and "found 2" in r.stderr
    assert sb.payload() == "old"


def test_rejects_older_or_equal_bundle_version(sb):
    prime(sb, new_ver="2.0")
    # feed promised 3.0 but the archive holds 2.0 => refused before touching apps
    assert sb.rc(sb.install(expected="3.0")) == 1
    assert sb.payload() == "old"
    # archive holds 2.0, installed already 2.0 => not newer => refused
    shutil.rmtree(sb.apps / "Example.app")
    make_app(sb.apps, ver="2.0", payload="old")
    assert sb.rc(sb.install(expected="2.0")) == 1
    assert sb.payload() == "old" and sb.leftovers() == []


def test_unparseable_versions_fail_closed_when_a_version_is_expected(sb):
    prime(sb, new_ver="nightly")
    assert sb.rc(sb.install(expected="2.0")) == 1
    assert sb.payload() == "old"


def test_download_failure_is_soft_and_clean(sb):
    prime(sb)
    r = sb.install(FAKE_CURL_RC=7)
    assert sb.rc(r) == 1 and sb.payload() == "old" and sb.leftovers() == []
    assert not any(p.name.startswith("vd.") for p in (sb.tmp / "tmp").iterdir()), "work dir leaked"


def test_work_dir_is_removed_on_success_and_failure(sb):
    prime(sb)
    assert sb.rc(sb.install(expected="9.9")) == 1  # feed promised more than the archive holds
    assert not any(p.name.startswith("vd.") for p in (sb.tmp / "tmp").iterdir())
    assert sb.rc(sb.install(expected="2.0")) == 0
    assert not any(p.name.startswith("vd.") for p in (sb.tmp / "tmp").iterdir())


def test_undetachable_mount_keeps_its_work_dir_instead_of_rm_through_it(sb):
    prime(sb, archive="dmg")
    r = sb.install(url="https://dl.example.com/App.dmg", artifact="dmg", FAKE_HDIUTIL_DETACH_RC=1)
    assert sb.rc(r) == 0, r.stderr  # the install itself is fine
    assert "mount still active" in r.stderr
    assert any(p.name.startswith("vd.") for p in (sb.tmp / "tmp").iterdir())


# ============================================================================
# copy_verified_app: identity, atomic swap, rollback
# ============================================================================

def test_signing_team_mismatch_is_refused_and_original_intact(sb):
    prime(sb, new_team="ZZZZZ99999")
    r = sb.install()
    assert sb.rc(r) == 3 and "identity" in r.stderr.lower()
    assert sb.payload() == "old" and sb.leftovers() == []
    assert sb.read_log("mv.log") == []


@pytest.mark.parametrize("old_team,new_team", [(None, TEAM), (TEAM, None), ("not set", "not set")])
def test_missing_or_unset_team_identifier_is_refused(sb, old_team, new_team):
    old = make_app(sb.apps, ver="1.0", payload="old", team=None,
                   sig_team_text=old_team if old_team else None)
    if old_team is None:
        pass
    elif old_team == TEAM:
        (old / "Contents" / "_sig").write_text(f"Identifier={BID}\nTeamIdentifier={TEAM}\n")
    d = sb.fx / "new"
    d.mkdir()
    new = make_app(d, ver="2.0", payload="new", team=None, sig_team_text=new_team)
    sb.serve("/App.zip", zip_of(sb.fx / "App.zip", new))
    r = sb.install()
    # Ruling 28: an app with no verifiable Team ID is refused SOFT (rc 1), reason
    # identity_unverifiable -- not the hard swap failure (rc 3) of a real mismatch.
    assert sb.rc(r) == 1, r.stderr
    assert "identity_unverifiable" in r.stderr
    assert sb.payload() == "old" and sb.leftovers() == [] and sb.read_log("mv.log") == []


def test_signature_bundle_identifier_mismatch_is_refused(sb):
    prime(sb)
    # Info.plist says the same id, but the signature identifier differs
    new = sb.fx / "new" / "Example.app"
    (new / "Contents" / "_sig").write_text(f"Identifier=com.evil.other\nTeamIdentifier={TEAM}\n")
    sb.serve("/App.zip", zip_of(sb.fx / "App.zip", new))
    r = sb.install()
    assert sb.rc(r) == 3 and sb.payload() == "old"


def test_gatekeeper_rejecting_the_source_stages_nothing(sb):
    prime(sb)
    r = sb.install(FAKE_SPCTL_FAIL_ON="1")
    assert sb.rc(r) == 3
    assert sb.payload() == "old" and sb.leftovers() == [] and sb.read_log("mv.log") == []
    # nothing was ever copied into the Applications root
    assert not [ln for ln in sb.read_log("ditto.log") if str(sb.apps) in ln.split(" ")[-1]]


def test_gatekeeper_rejecting_the_staged_copy_cleans_up(sb):
    prime(sb)
    r = sb.install(FAKE_SPCTL_FAIL_ON="2")
    assert sb.rc(r) == 3 and sb.payload() == "old" and sb.leftovers() == []
    assert sb.read_log("mv.log") == []


def test_staging_copy_failure_cleans_up(sb):
    prime(sb)
    r = sb.install(FAKE_DITTO_FAIL_MATCH=".ascendo_staging")
    assert sb.rc(r) == 3 and sb.payload() == "old" and sb.leftovers() == []


def test_swap_uses_same_volume_staging_and_backup_inside_the_apps_root(sb):
    prime(sb)
    r = sb.install()
    assert sb.rc(r) == 0
    mvs = sb.read_log("mv.log")
    assert len(mvs) == 2, mvs
    assert ".ascendo_backup." in mvs[0] and mvs[0].split()[1] == str(sb.apps / "Example.app")
    assert ".ascendo_staging." in mvs[1] and mvs[1].split()[-1] == str(sb.apps / "Example.app")
    assert sb.leftovers() == []


def test_second_mv_failure_restores_the_original_bundle(sb):
    prime(sb)
    r = sb.install(FAKE_MV_FAIL_ON="2")
    assert sb.rc(r) == 3, r.stderr
    assert sb.payload() == "old"
    assert (sb.apps / "Example.app" / "Contents" / "_sig").read_text().startswith(f"Identifier={BID}")
    assert sb.leftovers() == []
    assert len(sb.read_log("mv.log")) == 3  # backup, failed install, rollback


def test_failed_rollback_keeps_the_backup_and_says_how_to_restore(sb):
    prime(sb)
    r = sb.install(FAKE_MV_FAIL_ON="2,3")
    assert sb.rc(r) == 3
    assert "CRITICAL" in r.stderr and "Restore with: mv" in r.stderr
    backups = [p for p in sb.apps.iterdir() if p.name.startswith(".ascendo_backup.")]
    assert len(backups) == 1
    assert (backups[0] / "Example.app" / "Contents" / "payload.txt").read_text() == "old"
    assert not (sb.apps / "Example.app").exists()


def test_backup_move_failure_leaves_the_original_in_place(sb):
    prime(sb)
    r = sb.install(FAKE_MV_FAIL_ON="1")
    assert sb.rc(r) == 3 and sb.payload() == "old" and sb.leftovers() == []


def test_post_swap_gatekeeper_failure_rolls_back(sb):
    prime(sb)
    r = sb.install(FAKE_SPCTL_FAIL_ON="3")  # src ok, staged ok, installed copy rejected
    assert sb.rc(r) == 3, r.stderr
    assert sb.payload() == "old" and sb.leftovers() == []


def test_swap_falls_back_to_privilege_helper_only_after_plain_failure(sb):
    prime(sb)
    helper_log = sb.log / "priv.log"
    r = sb.bash('_ascendo_sudo() { echo "$*" >> "%s"; "$@"; }; '
                '_web_vendor_install example "https://dl.example.com/App.zip" zip - - dl.example.com "" '
                '"%s"; echo "rc=$?"' % (helper_log, sb.apps / "Example.app"), FAKE_MV_FAIL_ON="1")
    assert sb.rc(r) == 0, r.stderr
    assert sb.payload() == "new" and sb.leftovers() == []
    assert helper_log.exists() and "mv" in helper_log.read_text()
    # an uneventful run never reaches for it
    helper_log.unlink()
    shutil.rmtree(sb.apps / "Example.app")
    make_app(sb.apps, ver="1.0", payload="old")
    r = sb.bash('_ascendo_sudo() { echo "$*" >> "%s"; "$@"; }; '
                '_web_vendor_install example "https://dl.example.com/App.zip" zip - - dl.example.com "" '
                '"%s"; echo "rc=$?"' % (helper_log, sb.apps / "Example.app"))
    assert sb.rc(r) == 0 and not helper_log.exists()


# ============================================================================
# never touch / quit a running app (Ruling 25, delta rows 1-3)
# ============================================================================

@pytest.mark.parametrize("states", [("true",), ("unknown",), ("fail",)])
def test_running_or_unknown_app_is_never_copied_or_quit(sb, states):
    prime(sb)
    sb.set_ls(*states)
    r = sb.install()
    assert sb.rc(r) == 4, r.stderr
    assert sb.payload() == "old" and sb.leftovers() == []
    assert sb.read_log("curl.log") == [], "no download for an app we may not replace"
    assert sb.read_log("mv.log") == []
    assert sb.forbidden() == []


def test_missing_lsappinfo_is_treated_as_running(sb):
    prime(sb)
    r = sb.install(ASCENDO_LSAPPINFO_BIN=sb.tmp / "no-such-lsappinfo")
    assert sb.rc(r) == 4 and sb.payload() == "old" and sb.read_log("curl.log") == []


def test_user_launch_during_staging_preserves_existing_bundle(sb):
    prime(sb)
    # probes: before download (idle) -> before staging (idle) -> right before the swap (launched)
    sb.set_ls("false", "false", "true")
    r = sb.install()
    assert sb.rc(r) == 4, r.stderr
    assert sb.payload() == "old"
    assert sb.leftovers() == [], "staging + backup dirs must be removed on the second deferral"
    assert sb.read_log("mv.log") == [], "the existing bundle was never moved"
    assert int((sb.log / "ls.count").read_text()) >= 3
    assert sb.forbidden() == []


def test_ambiguous_running_probe_never_authorizes_quit(sb):
    prime(sb)
    sb.set_ls("false", "false", "unknown")
    r = sb.install()
    assert sb.rc(r) == 4 and sb.payload() == "old" and sb.leftovers() == []
    assert sb.forbidden() == []
    assert sb.read_log("mv.log") == []


def test_first_copy_probe_defers_before_anything_is_staged(sb):
    prime(sb)
    sb.set_ls("false", "unknown")
    r = sb.install()
    assert sb.rc(r) == 4 and sb.payload() == "old" and sb.leftovers() == []
    assert not [ln for ln in sb.read_log("ditto.log") if ".ascendo_staging" in ln]


def test_common_swap_path_contains_no_quit_or_kill():
    src = (LIB / "ascendo_vendor_direct.sh").read_text()
    code = "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith("#"))
    for needle in ("osascript", "pkill", "killall", "kill ", "quit", "SIGTERM", "terminate"):
        assert needle not in code, needle


def test_copy_verified_app_direct_defer_returns_2_without_side_effects(sb):
    old, new = prime(sb)
    sb.set_ls("true")
    r = sb.bash(f'copy_verified_app "{sb.fx / "new" / "Example.app"}" Example.app; echo "rc=$?"')
    assert sb.rc(r) == 2 and sb.payload() == "old" and sb.leftovers() == []


# ============================================================================
# legacy wrapper + handler wiring
# ============================================================================

def test_web_install_dmg_wrapper_uses_the_hardened_pipeline(sb):
    prime(sb)
    r = sb.bash(f'_web_install_dmg example "https://dl.example.com/App.zip" "{sb.apps / "Example.app"}"; echo "rc=$?"')
    assert sb.rc(r) == 0, r.stderr
    assert sb.payload() == "new"
    assert sb.read_log("mv.log"), "swap goes through mv with backup, not rm -rf + cp -R"
    code = (LIB / "ascendo_web.sh").read_text()
    inst = code[code.index("_web_install_dmg() {"):]
    inst = inst[:inst.index("\n}\n")]
    assert "rm -rf" not in inst and "cp -R" not in inst


def test_web_install_dmg_wrapper_sniffs_the_archive_kind_and_uses_the_resolved_app(sb):
    old, new = prime(sb)
    shutil.copy(sb.fx / "App.zip", sb.fx / "App.bin")  # vendor serves a zip under a neutral name
    sb.serve("/download/latest", sb.fx / "App.bin")
    r = sb.bash('_web_install_dmg example "https://dl.example.com/download/latest" ""; echo "rc=$?"',
                ASCENDO_WEB_INSTALLED_APP_PATH=sb.apps / "Example.app")
    assert sb.rc(r) == 0, r.stderr
    assert sb.payload() == "new"


def test_web_install_dmg_wrapper_refuses_without_an_installed_app(sb):
    prime(sb)
    shutil.rmtree(sb.apps / "Example.app")
    r = sb.bash('_web_install_dmg example "https://dl.example.com/App.zip" ""; echo "rc=$?"')
    assert sb.rc(r) == 1 and sb.read_log("curl.log") == []


YML = """version: 2.0
files:
  - url: Ledger-2.0-mac.zip
    sha512: ZIPSHA
    size: 1
  - url: Ledger-2.0-mac.dmg
    sha512: %s
    size: 2
path: Ledger-2.0-mac.zip
sha512: ZIPSHA
"""


def _ledger(sb, good: bool):
    old = make_app(sb.apps, "Ledger Wallet.app", ver="1.0", payload="old")
    d = sb.fx / "new"
    d.mkdir()
    new = make_app(d, "Ledger Wallet.app", ver="2.0", payload="new")
    dmg = sb.dmg_of(new, name="Ledger.dmg")
    digest = base64.b64encode(hashlib.sha512(dmg.read_bytes()).digest()).decode()
    (sb.fx / "latest.yml").write_text(YML % (digest if good else "A" * 86 + "=="))
    sb.serve("latest-mac.yml", sb.fx / "latest.yml")
    sb.serve("Ledger-2.0-mac.dmg", dmg)
    cfg = json.dumps({"slug": "ledger", "display_name": "Ledger Wallet",
                      "app_path": str(old),
                      "release_feed": {"url": "https://download.live.ledger.com/latest-mac.yml",
                                       "version_path": "version",
                                       "download_path": "files[dmg].url"}})
    return cfg


def test_release_feed_ledger_dmg_is_verified_against_its_own_files_entry(sb):
    cfg = _ledger(sb, good=True)
    r = sb.bash(f". '{LIB}/handlers/release_feed.sh'; release_feed_apply ledger '{cfg}'; echo \"rc=$?\"",
                ASCENDO_WEB_INSTALLED_APP_PATH=sb.apps / "Ledger Wallet.app")
    assert sb.rc(r) == 0, r.stderr
    assert sb.payload("Ledger Wallet.app") == "new"
    # the .dmg entry's digest was used, not the zip's / top-level one
    assert any("Ledger-2.0-mac.dmg" in ln for ln in sb.read_log("curl.log"))


def test_release_feed_ledger_wrong_digest_refuses_the_install(sb):
    cfg = _ledger(sb, good=False)
    r = sb.bash(f". '{LIB}/handlers/release_feed.sh'; release_feed_apply ledger '{cfg}'; echo \"rc=$?\"",
                ASCENDO_WEB_INSTALLED_APP_PATH=sb.apps / "Ledger Wallet.app")
    assert sb.rc(r) == 1 and "checksum" in r.stderr.lower()
    assert sb.payload("Ledger Wallet.app") == "old" and sb.leftovers() == []


# ============================================================================
# apply.sh: rc 4 => skipped + needs_restart (soft), orphan warning, unknown defers
# ============================================================================

APPCAST = """<?xml version="1.0"?>
<rss xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle"><channel>
<item><enclosure url="https://dl.example.com/App.zip" sparkle:shortVersionString="2.0" sparkle:version="2"
 type="application/octet-stream"/></item></channel></rss>"""


def run_apply(sb: Sandbox, **extra):
    app = sb.apps / "Example.app"
    reg = sb.tmp / "registry.toml"
    reg.write_text('schema = "ascendo-web-apps/v1"\n[[app]]\nslug = "example"\n'
                   f'bundle_id = "{BID}"\ndisplay_name = "Example"\nhandler = "sparkle"\n'
                   f'app_path = "{app}"\nappcast_url = "https://dl.example.com/appcast.xml"\n')
    (sb.fx / "appcast.xml").write_text(APPCAST)
    sb.serve("appcast.xml", sb.fx / "appcast.xml")
    out = sb.tmp / "out"
    env = sb.env(ASCENDO_WEB_REGISTRY_PATH=reg, ASCENDO_WEB_USER_REGISTRY_PATH="",
                 ASCENDO_SUDO_WARM_DISABLE="1", PYTHONPATH=f"{ADAPTER.parents[1]}/core:{ADAPTER}", **extra)
    res = subprocess.run(["bash", str(SCRIPTS / "apply.sh"), "--run-id", "r1", "--trigger", "cli",
                          "--profile", "full", "--output-dir", str(out)],
                         capture_output=True, text=True, env=env)
    return res, json.loads((out / "r1" / "apply__web.json").read_text())


def test_direct_defer_is_soft_without_launch(sb):
    prime(sb)
    # step-2 idle check says idle; the installer's own probe finds the app running
    sb.set_ls("false", "true")
    res, sc = run_apply(sb)
    assert res.returncode == 0, res.stderr
    (item,) = sc["items"]
    assert item["status"] == "skipped", sc
    msgs = [m["text"] for m in sc["messages"] if "example" in m["text"]]
    assert any("ASCENDO-ACTION-REQUIRED reason=needs_restart cur=1.0 cand=2.0" in m for m in msgs), msgs
    assert sb.payload() == "old" and sb.leftovers() == []
    assert sb.forbidden() == [], "neither open nor any quit/kill/sudo was invoked"
    assert not any("open" in ln for ln in sb.forbidden())
    assert sc["status"] != "failed"


def test_apply_defers_an_unprobeable_app_before_running_any_handler(sb):
    prime(sb)
    sb.set_ls("unknown")
    res, sc = run_apply(sb)
    (item,) = sc["items"]
    assert item["status"] == "skipped" and sb.payload() == "old"
    assert not [ln for ln in sb.read_log("curl.log") if "App.zip" in ln]
    assert any("state_unknown" in m["text"] or "could not be determined" in m["text"]
               for m in sc["messages"]), sc["messages"]
    assert sb.forbidden() == []


def test_apply_installs_end_to_end_when_idle(sb):
    prime(sb)
    sb.set_ls("false")
    res, sc = run_apply(sb)
    (item,) = sc["items"]
    assert item["status"] == "success", (sc, res.stderr)
    assert sb.payload() == "new" and sb.leftovers() == []


def test_apply_warns_about_orphaned_staging_and_backup_dirs(sb):
    prime(sb)
    (sb.apps / ".ascendo_staging.AbC123").mkdir()
    (sb.apps / ".ascendo_backup.xYz789").mkdir()
    res, sc = run_apply(sb)
    warns = [m["text"] for m in sc["messages"] if m["level"] == "warn" and "orphaned" in m["text"]]
    assert len(warns) == 2 and any(".ascendo_staging.AbC123" in w for w in warns)
    assert (sb.apps / ".ascendo_staging.AbC123").exists(), "report only: never delete another run's dirs"


# ============================================================================
# fix round 1
# ============================================================================

def test_nested_installed_path_is_updated_in_place_never_duplicated_at_the_root(sb):
    """I1: <root>/Utilities/X.app is swapped as itself; nothing appears at <root>/X.app."""
    nested = sb.apps / "Utilities"
    nested.mkdir()
    make_app(nested, ver="1.0", payload="old")
    d = sb.fx / "new"
    d.mkdir()
    new = make_app(d, ver="2.0", payload="new")
    sb.serve("/App.zip", zip_of(sb.fx / "App.zip", new))
    r = sb.install(installed=nested / "Example.app", expected="2.0")
    assert sb.rc(r) == 0, r.stderr
    assert (nested / "Example.app" / "Contents" / "payload.txt").read_text() == "new"
    assert not (sb.apps / "Example.app").exists(), "no duplicate bundle at the apps root"
    assert sb.leftovers() == []
    mvs = sb.read_log("mv.log")
    assert mvs[0].split()[1] == str(nested / "Example.app") and mvs[1].split()[-1] == str(nested / "Example.app")


def test_nested_installed_path_still_gets_the_identity_check(sb):
    nested = sb.apps / "Utilities"
    nested.mkdir()
    make_app(nested, ver="1.0", payload="old")
    d = sb.fx / "new"
    d.mkdir()
    new = make_app(d, ver="2.0", payload="new", team="ZZZZZ99999")
    sb.serve("/App.zip", zip_of(sb.fx / "App.zip", new))
    r = sb.install(installed=nested / "Example.app")
    assert sb.rc(r) == 3 and "identity" in r.stderr.lower()
    assert (nested / "Example.app" / "Contents" / "payload.txt").read_text() == "old"
    assert not (sb.apps / "Example.app").exists() and sb.leftovers() == []


def test_copy_verified_app_never_installs_fresh_when_the_destination_is_absent(sb):
    d = sb.fx / "new"
    d.mkdir()
    new = make_app(d, ver="2.0", payload="new")
    for dest in ("Example.app", str(sb.apps / "Utilities" / "Example.app")):
        r = sb.bash(f'copy_verified_app "{new}" "{dest}"; echo "rc=$?"')
        assert sb.rc(r) == 3, (dest, r.stderr)   # soft refusal, never rc 0
        assert "identity_unverifiable" in r.stderr
    assert sorted(p.name for p in sb.apps.iterdir()) == []
    assert sb.read_log("mv.log") == [] and sb.read_log("ditto.log") == []


def test_copy_verified_app_refuses_a_destination_outside_the_apps_root(sb):
    d = sb.fx / "new"
    d.mkdir()
    new = make_app(d, ver="2.0")
    outside = make_app(sb.fx, "Outside.app")
    for dest in (str(outside), str(sb.apps / ".." / "Example.app")):
        r = sb.bash(f'copy_verified_app "{new}" "{dest}"; echo "rc=$?"')
        assert sb.rc(r) == 1, dest
    assert sb.read_log("mv.log") == []


def test_unreadable_installed_identity_is_refused_soft_before_anything_is_staged(sb):
    prime(sb)
    (sb.apps / "Example.app" / "Contents" / "_sig").unlink()   # codesign: "not signed at all"
    r = sb.install()
    assert sb.rc(r) == 1 and "identity_unverifiable" in r.stderr
    assert sb.payload() == "old" and sb.leftovers() == [] and sb.read_log("mv.log") == []


def test_sha512b64_is_hashed_in_chunks_not_slurped(sb):
    f = sb.fx / "big.bin"
    f.write_bytes(os.urandom(3 * 1024 * 1024 + 17))
    good = base64.b64encode(hashlib.sha512(f.read_bytes()).digest()).decode()
    ok = sb.bash(f'vendor_direct_verify_checksum "{f}" sha512b64 "{good}" && echo y || echo n')
    bad = sb.bash(f'vendor_direct_verify_checksum "{f}" sha512b64 "{good[:-3]}AAA" && echo y || echo n')
    assert (ok.stdout.strip(), bad.stdout.strip()) == ("y", "n")
    code = "\n".join(ln for ln in (LIB / "ascendo_vendor_direct.sh").read_text().splitlines()
                     if not ln.lstrip().startswith("#"))
    assert ".read()" not in code, "whole-file read would hold up to 4 GiB in memory"


def test_hdiutil_never_inherits_stdin_so_a_licence_dmg_cannot_prompt(sb):
    prime(sb, archive="dmg")
    r = sb.install(url="https://dl.example.com/App.dmg", artifact="dmg", stdin="y\ny\nagree\n")
    assert sb.rc(r) == 0, r.stderr
    assert sb.read_log("hdiutil.stdin") == ["attach-stdin-bytes=0"], "attach must run with </dev/null"
    code = "\n".join(ln for ln in (LIB / "ascendo_vendor_direct.sh").read_text().splitlines()
                     if not ln.lstrip().startswith("#"))
    assert "yes |" not in code and "yes|" not in code, "licences are never auto-accepted"
