# macOS adapter — M5.2 mas + MacElevation implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `python -m ascendo run --category mas --phase {check|plan|apply|verify|cleanup}` end-to-end on macOS, with one real `sudo mas upgrade` performed via the new `MacElevation` interface (CLI + dashboard `POST /elevation/auth` round-trip), and tag `v0.0.9-alpha`.

**Architecture:** Mirrors M5.1 (`brew`) and the Linux dashboard's askpass pattern (`app/backend/sudo.py`). Layer 4 core is unchanged except for one enum addition. New Layer 5 adds `MasManager` (mirrors `BrewManager`) and `MacElevation` (concrete `IElevation` impl with in-memory password + ephemeral `SUDO_ASKPASS` helper). New Layer 6 adds 5 bash phase scripts + `lib/ascendo_mas.sh`. New Layer 3 adds 3 dashboard endpoints (`POST /elevation/auth`, `POST /elevation/invalidate`, `GET /elevation/status`). Capability flag flips to `PACKAGE_MANAGEMENT | ELEVATION`.

**Tech Stack:** Python 3.11+ (Pydantic v2, FastAPI), Bash 3.2+ (macOS system shell), `mas >= 4`, `jq`, `sudo`. No new core dependencies. Tests: pytest (mock-based unit + dashboard contract) + bash on macOS (real-hardware via `bin/validate-macos.sh`).

**Branch:** `claude/musing-herschel-b52e7e` (current worktree). Merge to `main` is the last task.

**Spec reference:** [docs/superpowers/specs/2026-05-03-macos-mas-elevation-design.md](../specs/2026-05-03-macos-mas-elevation-design.md)

**Working directory:** `/Users/mk/Dev_Env/Ascendo/.claude/worktrees/musing-herschel-b52e7e`. All commands assume this CWD.

---

## Task 1: Add `SourceType.MAS` enum value + regenerate schema

The `SourceType` enum already has `MAC_APP_STORE` (item-level legacy name). Per M5.1 precedent (`BREW` added alongside `BREW_FORMULA`/`BREW_CASK`), we add `MAS` as the manager-level category. `MasManager.category == SourceType.MAS`; individual items can still tag themselves as `MAC_APP_STORE` for namespace clarity if ever needed. Sidecar JSON Schema is regenerated to include the new enum value.

**Files:**
- Modify: `core/ascendo/models/package.py` (one enum line)
- Modify: `docs/architecture/schemas/sidecar.v1.schema.json` (regenerated)
- Test: `tests/contract/test_sidecar_v1.py` (new test)

- [ ] **Step 1: Write failing test asserting `SourceType.MAS` exists**

Append to `tests/contract/test_sidecar_v1.py`:

```python
def test_source_type_has_mas_value() -> None:
    """MasManager.category == SourceType.MAS. Required by M5.2."""
    from ascendo.models.package import SourceType
    assert SourceType.MAS.value == "mas"
    # MAC_APP_STORE retained for future item-level namespace tagging.
    assert SourceType.MAC_APP_STORE.value == "mac_app_store"
```

- [ ] **Step 2: Run the test to confirm it fails**

```bash
PYTHONPATH=$(pwd)/core python -m pytest tests/contract/test_sidecar_v1.py::test_source_type_has_mas_value -v
```

Expected: `FAILED` with `AttributeError: MAS`.

- [ ] **Step 3: Add the enum value**

In `core/ascendo/models/package.py`, locate the `SourceType` class (line 22). Insert one line after `MAC_APP_STORE = "mac_app_store"`:

```python
    MAC_APP_STORE = "mac_app_store"
    MAS = "mas"                   # manager-level category for MasManager
    NPM = "npm"
```

- [ ] **Step 4: Run the test to confirm pass**

```bash
PYTHONPATH=$(pwd)/core python -m pytest tests/contract/test_sidecar_v1.py::test_source_type_has_mas_value -v
```

Expected: `PASSED`.

- [ ] **Step 5: Regenerate sidecar JSON Schema**

```bash
PYTHONPATH=$(pwd)/core python scripts/export-sidecar-schema.py
```

Expected: `wrote docs/architecture/schemas/sidecar.v1.schema.json (NNN bytes)`.

Verify the schema contains `"mas"`:

```bash
grep '"mas"' docs/architecture/schemas/sidecar.v1.schema.json
```

Expected: at least one match (under the `SourceType` enum block).

- [ ] **Step 6: Run full contract suite for regression check**

```bash
PYTHONPATH=$(pwd)/core python -m pytest tests/contract/ -q
```

Expected: all green (additive enum change is regression-safe).

- [ ] **Step 7: Commit**

```bash
git add core/ascendo/models/package.py
git add docs/architecture/schemas/sidecar.v1.schema.json
git add tests/contract/test_sidecar_v1.py
git commit -m "$(cat <<'EOF'
feat(core): add SourceType.MAS for macOS adapter (M5.2.1)

M5.2 prerequisite. MasManager.category will be SourceType.MAS
(manager-level), mirroring how BrewManager.category is BREW
(M5.1 precedent). Existing MAC_APP_STORE retained for any future
item-level namespace tagging within mas sidecars.

Sidecar JSON Schema regenerated.

Refs docs/superpowers/specs/2026-05-03-macos-mas-elevation-design.md
EOF
)"
```

---

## Task 2: `MacElevation` Python impl + 10 unit tests

Concrete `IElevation` implementation for macOS. Holds an in-memory password verified via `sudo -S -p '' -v` and exposes it to child processes via an ephemeral `SUDO_ASKPASS` helper at `$TMPDIR/ascendo/askpass-*.sh` (mode 0700). When no password is registered, `run()` falls back to letting `sudo` prompt the controlling TTY. Argv-only contract enforced (T4 mitigation per ADR-0005). Concrete-only surface (`register_password`, `invalidate`, `askpass_path`, `has_password_registered`) layered on top of the `IElevation` ABC.

**Files:**
- Create: `adapters/macos/ascendo_macos/managers/elevation.py` (~220 LOC)
- Create: `adapters/macos/tests/test_elevation_smoke.py` (~10 tests)

- [ ] **Step 1: Write failing tests**

Create `adapters/macos/tests/test_elevation_smoke.py`:

```python
"""Smoke tests for MacElevation.

Tests cover: argv-only contract, allow-list normalisation, askpass
helper shape (mode + content + escape), state lifecycle. No real
sudo invocations; subprocess.run is mocked.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from ascendo.interfaces.elevation import ElevationDenied
from ascendo.models.host import ElevationMethod, HostInfo, OperatingSystem
from ascendo_macos.managers.elevation import MacElevation


@pytest.fixture
def host() -> HostInfo:
    return HostInfo(
        hostname="testmac.local",
        os=OperatingSystem.MACOS,
        os_version="14.5",
        arch="arm64",
        user="mk",
        is_elevated=False,
        elevation_method=ElevationMethod.NONE,
    )


def test_register_allowlist_lowercases_basenames():
    e = MacElevation()
    e.register_allowlist(["/usr/bin/MAS", "Foo.SH", "/Users/x/Bar"])
    assert e._allowlist == frozenset({"mas", "foo.sh", "bar"})


def test_run_with_empty_argv_raises_elevation_denied(host):
    e = MacElevation()
    e.register_allowlist(["mas"])
    with pytest.raises(ElevationDenied):
        e.run(host, [])


def test_run_with_shell_string_argv_raises_typeerror(host):
    e = MacElevation()
    e.register_allowlist(["mas"])
    with pytest.raises(TypeError):
        e.run(host, "mas upgrade Foo")  # type: ignore[arg-type]


def test_run_rejects_command_not_in_allowlist(host):
    e = MacElevation()
    e.register_allowlist(["mas"])
    with pytest.raises(ElevationDenied):
        e.run(host, ["rm", "-rf", "/"])


def test_register_password_verifies_via_sudo_v(monkeypatch):
    """register_password calls `sudo -S -p '' -v` and stores on success."""
    e = MacElevation()
    captured = {}

    def fake_run(argv, *, input=None, capture_output=None, text=None, timeout=None):
        captured["argv"] = argv
        captured["input"] = input
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)
    ok, detail = e.register_password("hunter2")
    assert ok is True
    assert captured["argv"] == ["sudo", "-S", "-p", "", "-v"]
    assert captured["input"] == "hunter2\n"
    assert e.has_password_registered() is True


def test_register_password_returns_false_on_bad_password(monkeypatch):
    e = MacElevation()

    def fake_run(*a, **kw):
        return MagicMock(returncode=1, stdout="", stderr="Sorry, try again.")

    monkeypatch.setattr("subprocess.run", fake_run)
    ok, detail = e.register_password("wrong")
    assert ok is False
    assert "try again" in detail.lower() or "1" in detail
    assert e.has_password_registered() is False


def test_register_password_creates_0700_helper(monkeypatch, tmp_path):
    e = MacElevation()
    monkeypatch.setattr("subprocess.run",
                        lambda *a, **kw: MagicMock(returncode=0, stdout="", stderr=""))
    monkeypatch.setenv("TMPDIR", str(tmp_path))

    e.register_password("hunter2")
    p = e.askpass_path()
    assert p is not None and p.is_file()

    # Mode is 0700
    mode = stat.S_IMODE(p.stat().st_mode)
    assert mode == 0o700, f"expected 0700, got 0o{mode:o}"

    # Content prints the password
    body = p.read_text()
    assert body.startswith("#!/usr/bin/env bash\n")
    assert "printf '%s\\n' 'hunter2'\n" in body


def test_helper_escapes_single_quotes(monkeypatch, tmp_path):
    e = MacElevation()
    monkeypatch.setattr("subprocess.run",
                        lambda *a, **kw: MagicMock(returncode=0, stdout="", stderr=""))
    monkeypatch.setenv("TMPDIR", str(tmp_path))

    e.register_password("O'Brien42")
    body = e.askpass_path().read_text()
    # Single-quote escape rule: ' -> '\''
    # So O'Brien42 -> 'O'\''Brien42'
    assert "'O'\\''Brien42'" in body


def test_invalidate_wipes_state_and_is_idempotent(monkeypatch, tmp_path):
    e = MacElevation()
    monkeypatch.setattr("subprocess.run",
                        lambda *a, **kw: MagicMock(returncode=0, stdout="", stderr=""))
    monkeypatch.setenv("TMPDIR", str(tmp_path))

    e.register_password("hunter2")
    helper = e.askpass_path()
    assert helper.is_file()

    e.invalidate()
    assert e.has_password_registered() is False
    assert e.askpass_path() is None
    assert not helper.exists()

    # Second invalidate is a no-op
    e.invalidate()
    assert e.has_password_registered() is False


def test_available_methods_empty_when_sudo_missing(monkeypatch):
    monkeypatch.setattr("shutil.which",
                        lambda name: None if name == "sudo" else "/x")
    e = MacElevation()
    assert e.available_methods == ()
```

- [ ] **Step 2: Run the tests to confirm they all fail**

```bash
PYTHONPATH=$(pwd)/core python -m pytest adapters/macos/tests/test_elevation_smoke.py -v
```

Expected: 10 FAILED with `ModuleNotFoundError: ascendo_macos.managers.elevation`.

- [ ] **Step 3: Write the `MacElevation` impl**

Create `adapters/macos/ascendo_macos/managers/elevation.py`:

```python
"""MacElevation — concrete IElevation impl for macOS.

Holds an in-memory password and exposes it to child sudo processes via
an ephemeral SUDO_ASKPASS helper script. When no password is registered,
falls back to letting sudo prompt the controlling TTY.

Mirrors the proven app/backend/sudo.py (Linux dashboard) pattern; layers
the IElevation ABC + allow-list T4 guard from ADR-0005 on top.
"""
from __future__ import annotations

import atexit
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time
from collections.abc import Iterable, Sequence
from pathlib import Path

from ascendo.interfaces.elevation import (
    ElevationDenied,
    ElevationResult,
    ElevationTimeout,
    IElevation,
)
from ascendo.models.host import ElevationMethod, HostInfo

_log = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_SEC = 1800
_VERIFY_TIMEOUT_SEC = 15


class MacElevation(IElevation):
    """sudo-driven elevation with optional askpass cache.

    Lifecycle:
        register_password(pw)   verify + store + create askpass helper
        run(host, argv, ...)    sudo -A argv (uses SUDO_ASKPASS) when
                                password registered, else sudo argv
                                (TTY prompt fallback)
        invalidate()            wipe in-memory password + unlink helper
                                + sudo -k
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._password: str | None = None
        self._askpass_path: Path | None = None
        self._allowlist: frozenset[str] = frozenset()
        atexit.register(self._cleanup_at_exit)

    # ── IElevation surface ────────────────────────────────────────────────

    @property
    def available_methods(self) -> tuple[ElevationMethod, ...]:
        return (ElevationMethod.SUDO,) if shutil.which("sudo") else ()

    def is_currently_elevated(self, host: HostInfo) -> bool:
        try:
            return os.geteuid() == 0  # type: ignore[attr-defined]
        except AttributeError:
            return False

    def register_allowlist(self, allowed_commands: Iterable[str]) -> None:
        self._allowlist = frozenset(
            Path(c).name.lower() for c in allowed_commands if c
        )

    def run(
        self,
        host: HostInfo,
        argv: Sequence[str],
        *,
        timeout_sec: int | None = None,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        method: ElevationMethod | None = None,
    ) -> ElevationResult:
        if isinstance(argv, str):
            raise TypeError("argv must be Sequence[str], not a shell string")
        if not argv:
            raise ElevationDenied("argv is empty")
        head = Path(argv[0]).name.lower()
        if self._allowlist and head not in self._allowlist:
            raise ElevationDenied(
                f"command {head!r} not in allow-list "
                f"{sorted(self._allowlist)!r}"
            )
        if shutil.which("sudo") is None:
            raise ElevationDenied("sudo not on PATH")

        full_env = dict(os.environ)
        if env:
            full_env.update(env)

        with self._lock:
            askpass = self._askpass_path
        if askpass is not None:
            full_env["SUDO_ASKPASS"] = str(askpass)
            sudo_argv = ["sudo", "-A", *argv]
        else:
            sudo_argv = ["sudo", *argv]

        timeout = timeout_sec if timeout_sec is not None else _DEFAULT_TIMEOUT_SEC
        started = time.monotonic()
        try:
            res = subprocess.run(  # noqa: S603
                sudo_argv,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=full_env,
                cwd=cwd,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ElevationTimeout(
                f"sudo {argv[0]} exceeded {timeout}s"
            ) from exc
        duration_ms = int((time.monotonic() - started) * 1000)
        return ElevationResult(
            exit_code=res.returncode,
            stdout=res.stdout or "",
            stderr=res.stderr or "",
            method=ElevationMethod.SUDO,
            duration_ms=duration_ms,
        )

    # ── Concrete-only surface (extra) ─────────────────────────────────────

    def register_password(
        self, password: str, *, verify: bool = True, timeout: int = _VERIFY_TIMEOUT_SEC
    ) -> tuple[bool, str]:
        if not password:
            return False, "empty password"
        if verify:
            try:
                res = subprocess.run(  # noqa: S603, S607
                    ["sudo", "-S", "-p", "", "-v"],
                    input=password + "\n",
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
            except subprocess.TimeoutExpired:
                return False, "sudo -S -v timed out"
            except FileNotFoundError:
                return False, "sudo not installed"
            if res.returncode != 0:
                err = (res.stderr or res.stdout).strip()
                return False, err[:300] or f"sudo -S -v exited {res.returncode}"
        with self._lock:
            self._password = password
            self._askpass_path = self._create_askpass_helper(password)
        return True, "password verified and stored"

    def invalidate(self) -> None:
        with self._lock:
            self._password = None
            old = self._askpass_path
            self._askpass_path = None
        if old is not None and old.exists():
            try:
                old.unlink()
            except OSError:
                pass
        try:
            subprocess.run(  # noqa: S603, S607
                ["sudo", "-k"],
                capture_output=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass

    def has_password_registered(self) -> bool:
        with self._lock:
            return self._password is not None

    def askpass_path(self) -> Path | None:
        with self._lock:
            return self._askpass_path

    # ── Internals ─────────────────────────────────────────────────────────

    def _askpass_dir(self) -> Path:
        base = Path(os.environ.get("TMPDIR") or tempfile.gettempdir())
        d = base / "ascendo"
        d.mkdir(parents=True, exist_ok=True)
        try:
            d.chmod(0o700)
        except OSError:
            pass
        return d

    def _create_askpass_helper(self, password: str) -> Path:
        # Single-quote escape rule: ' -> '\''
        quoted = "'" + password.replace("'", "'\\''") + "'"
        body = "#!/usr/bin/env bash\nprintf '%s\\n' " + quoted + "\n"
        fd, path = tempfile.mkstemp(
            prefix="askpass-", suffix=".sh", dir=str(self._askpass_dir())
        )
        try:
            with os.fdopen(fd, "w") as fh:
                fh.write(body)
            os.chmod(path, 0o700)
        except OSError:
            try:
                os.unlink(path)
            except OSError:
                pass
            raise
        return Path(path)

    def _cleanup_at_exit(self) -> None:
        with self._lock:
            old = self._askpass_path
            self._askpass_path = None
            self._password = None
        if old is not None:
            try:
                old.unlink()
            except OSError:
                pass
```

- [ ] **Step 4: Run the tests to confirm pass**

```bash
PYTHONPATH=$(pwd)/core python -m pytest adapters/macos/tests/test_elevation_smoke.py -v
```

Expected: `10 passed`.

- [ ] **Step 5: Commit**

```bash
git add adapters/macos/ascendo_macos/managers/elevation.py
git add adapters/macos/tests/test_elevation_smoke.py
git commit -m "$(cat <<'EOF'
feat(macos): MacElevation impl for M5.2.2

IElevation impl for macOS — sudo with optional askpass cache:

  register_password(pw)  -> verify via `sudo -S -p '' -v`,
                            store in memory, create 0700 helper at
                            $TMPDIR/ascendo/askpass-<random>.sh
  run(host, argv, ...)   -> sudo -A argv when password registered
                            (uses SUDO_ASKPASS), else sudo argv
                            (TTY prompt fallback)
  invalidate()           -> wipe state + unlink helper + sudo -k

Argv-only contract enforced (T4 mitigation per ADR-0005):
  - Shell strings rejected (TypeError)
  - Empty argv rejected (ElevationDenied)
  - argv[0] basename must be in allow-list

10 mock-based smoke tests cover identity, allow-list normalisation,
helper shape (mode 0700 + correct shell escape including O'Brien
single-quote case), invalidate idempotency.

Refs docs/superpowers/specs/2026-05-03-macos-mas-elevation-design.md §4
EOF
)"
```

---

## Task 3: `lib/ascendo_mas.sh` — bash helpers + parser tests

Wraps `mas` CLI calls and emits canonical JSON + exit-code classification. All Bash 3.2-safe (no `declare -A`, no `mapfile`, no `readarray`). Sourced by every phase script. Pure parsers tested against captured fixture output.

**Files:**
- Create: `adapters/macos/lib/ascendo_mas.sh` (~180 LOC)
- Create: `adapters/macos/tests/test_ascendo_mas_helpers.py` (~6 tests)
- Create: `adapters/macos/tests/fixtures/mas-list.txt`
- Create: `adapters/macos/tests/fixtures/mas-outdated.txt`

- [ ] **Step 1: Capture fixture output**

If you have access to the host Mac with mas installed, capture real output:

```bash
mas list 2>/dev/null > adapters/macos/tests/fixtures/mas-list.txt
mas outdated 2>/dev/null > adapters/macos/tests/fixtures/mas-outdated.txt
```

If running in an agent environment without `mas`, write these synthetic fixtures verbatim:

`adapters/macos/tests/fixtures/mas-list.txt`:
```
497799835 Xcode (15.4)
1333542190 1Password 7 — Password Manager (7.9.11)
1153157709 Keka (1.4.2)
682658836 GarageBand (10.4.11)
408981434 iMovie (10.4.1)
```

`adapters/macos/tests/fixtures/mas-outdated.txt`:
```
1333542190 1Password 7 — Password Manager (7.9.11 -> 7.9.12)
1153157709 Keka (1.4.2 -> 1.4.3)
```

- [ ] **Step 2: Write failing tests for the helpers**

Create `adapters/macos/tests/test_ascendo_mas_helpers.py`:

```python
"""Tests for adapters/macos/lib/ascendo_mas.sh — pure parsers.

Each test invokes one helper via `bash -c '. lib/ascendo_mas.sh; <fn>'`
and pipes a captured fixture in via stdin. No real mas calls.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
LIB = ADAPTER_ROOT / "lib" / "ascendo_mas.sh"
FIX = ADAPTER_ROOT / "tests" / "fixtures"


@pytest.fixture(scope="module", autouse=True)
def _require_jq() -> None:
    if shutil.which("jq") is None:
        pytest.skip("jq not on PATH")


def _bash(snippet: str, stdin: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", f". '{LIB}'; {snippet}"],
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
    )


def test_lib_exists_and_sources_clean():
    assert LIB.is_file()
    res = _bash(":")
    assert res.returncode == 0, res.stderr


def test_mas_list_json_parses_id_name_version():
    text = (FIX / "mas-list.txt").read_text()
    res = _bash("mas_list_json_from_stdin", stdin=text)
    assert res.returncode == 0, res.stderr
    arr = json.loads(res.stdout)
    assert isinstance(arr, list)
    ids = {e["id"] for e in arr}
    assert "497799835" in ids
    xc = next(e for e in arr if e["id"] == "497799835")
    assert xc["name"] == "Xcode"
    assert xc["version"] == "15.4"


def test_mas_outdated_json_parses_arrow():
    text = (FIX / "mas-outdated.txt").read_text()
    res = _bash("mas_outdated_json_from_stdin", stdin=text)
    assert res.returncode == 0, res.stderr
    arr = json.loads(res.stdout)
    assert len(arr) == 2
    keka = next(e for e in arr if e["id"] == "1153157709")
    assert keka["name"] == "Keka"
    assert keka["current_version"] == "1.4.2"
    assert keka["target_version"] == "1.4.3"


def test_mas_classify_exit_known_codes():
    # 0 -> success
    res = _bash("mas_classify_exit 0")
    assert res.returncode == 0
    assert res.stdout.strip() == "success"
    # 1 -> failed
    res = _bash("mas_classify_exit 1")
    assert res.stdout.strip() == "failed"
    # 6 -> failed-not-signed-in
    res = _bash("mas_classify_exit 6")
    assert res.stdout.strip() == "failed-not-signed-in"


def test_mas_version_at_least_compares_correctly():
    # mas major from 4.x >= 4 -> 0
    res = _bash("echo '4.3.0' | mas_version_at_least 4 && echo PASS || echo FAIL")
    assert res.stdout.strip() == "PASS"
    # mas major from 3.x >= 4 -> 1
    res = _bash("echo '3.1.0' | mas_version_at_least 4 && echo PASS || echo FAIL")
    assert res.stdout.strip() == "FAIL"


def test_signed_in_probe_runs_without_real_mas(monkeypatch):
    """mas_signed_in: returns the exit code of `mas list >/dev/null 2>&1`.

    We can't easily mock `mas` from inside a sourced bash function in pytest,
    so we test the function shape: when MAS_BIN points to a fake script that
    exits 0, mas_signed_in returns 0; when it exits 1, returns 1.
    """
    import os
    fake_ok = ADAPTER_ROOT / "tests" / "fixtures" / "_fake_mas_ok.sh"
    fake_fail = ADAPTER_ROOT / "tests" / "fixtures" / "_fake_mas_fail.sh"
    fake_ok.write_text("#!/usr/bin/env bash\nexit 0\n"); os.chmod(fake_ok, 0o755)
    fake_fail.write_text("#!/usr/bin/env bash\nexit 1\n"); os.chmod(fake_fail, 0o755)

    res = _bash(f"export MAS_BIN={fake_ok}; mas_signed_in && echo PASS || echo FAIL")
    assert res.stdout.strip() == "PASS"

    res = _bash(f"export MAS_BIN={fake_fail}; mas_signed_in && echo PASS || echo FAIL")
    assert res.stdout.strip() == "FAIL"
```

- [ ] **Step 3: Run the tests to confirm they fail**

```bash
PYTHONPATH=$(pwd)/core python -m pytest adapters/macos/tests/test_ascendo_mas_helpers.py -v
```

Expected: 6 FAILED (file `lib/ascendo_mas.sh` doesn't exist).

- [ ] **Step 4: Write `lib/ascendo_mas.sh`**

Create `adapters/macos/lib/ascendo_mas.sh`:

```bash
# adapters/macos/lib/ascendo_mas.sh
# mas helper functions for the macOS adapter. Bash 3.2-safe.
#
# Public functions:
#   mas_signed_in                 0 if `mas list` succeeds with output, else 1
#   mas_list_json                 emits JSON array of installed apps to stdout
#   mas_outdated_json             emits JSON array of outdated apps to stdout
#   mas_version_at_least <major>  reads version from stdin or `mas version`
#   mas_classify_exit <code>      maps mas exit code -> ascendo status string
#
# Stdin variants (test seams; do not use in production scripts):
#   mas_list_json_from_stdin
#   mas_outdated_json_from_stdin
#
# Override knob:
#   MAS_BIN  if set, used instead of `mas` (for testing).

# shellcheck shell=bash

: "${MAS_BIN:=mas}"

mas_signed_in() {
    "$MAS_BIN" list >/dev/null 2>&1
}

# Parse `mas list` output. Each line:
#   <id> <name> (<version>)
# id is numeric; version is the substring inside the LAST parentheses.
# Name is everything between the first space after id and the LAST `(`.
mas_list_json_from_stdin() {
    awk '
        function trim(s) { sub(/^[[:space:]]+/, "", s); sub(/[[:space:]]+$/, "", s); return s }
        function jsesc(s) { gsub(/\\/, "\\\\", s); gsub(/"/,  "\\\"", s); return s }
        BEGIN { print "["; first = 1 }
        /^[0-9]+[[:space:]]/ {
            id = $1
            rest = $0
            sub(/^[0-9]+[[:space:]]+/, "", rest)
            # last "(" starts the version
            n = length(rest)
            paren = 0
            for (i = n; i > 0; i--) { if (substr(rest, i, 1) == "(") { paren = i; break } }
            if (paren == 0) next
            name = trim(substr(rest, 1, paren - 1))
            version = substr(rest, paren + 1, length(rest) - paren - 1)
            if (!first) print ","
            first = 0
            printf "{\"id\":\"%s\",\"name\":\"%s\",\"version\":\"%s\"}", id, jsesc(name), jsesc(version)
        }
        END { print "]" }
    '
}

mas_list_json() {
    "$MAS_BIN" list 2>/dev/null | mas_list_json_from_stdin
}

# Parse `mas outdated` output. Each line:
#   <id> <name> (<current> -> <target>)
mas_outdated_json_from_stdin() {
    awk '
        function trim(s) { sub(/^[[:space:]]+/, "", s); sub(/[[:space:]]+$/, "", s); return s }
        function jsesc(s) { gsub(/\\/, "\\\\", s); gsub(/"/,  "\\\"", s); return s }
        BEGIN { print "["; first = 1 }
        /^[0-9]+[[:space:]]/ {
            id = $1
            rest = $0
            sub(/^[0-9]+[[:space:]]+/, "", rest)
            n = length(rest)
            paren = 0
            for (i = n; i > 0; i--) { if (substr(rest, i, 1) == "(") { paren = i; break } }
            if (paren == 0) next
            name = trim(substr(rest, 1, paren - 1))
            inner = substr(rest, paren + 1, length(rest) - paren - 1)
            arrow = index(inner, "->")
            if (arrow == 0) next
            current = trim(substr(inner, 1, arrow - 1))
            target  = trim(substr(inner, arrow + 2))
            if (!first) print ","
            first = 0
            printf "{\"id\":\"%s\",\"name\":\"%s\",\"current_version\":\"%s\",\"target_version\":\"%s\"}", \
                   id, jsesc(name), jsesc(current), jsesc(target)
        }
        END { print "]" }
    '
}

mas_outdated_json() {
    "$MAS_BIN" outdated 2>/dev/null | mas_outdated_json_from_stdin
}

# mas_version_at_least <required-major>
# Reads version string from stdin (e.g. "4.3.0") or from `$MAS_BIN version`.
# Returns 0 if major >= required, 1 otherwise.
mas_version_at_least() {
    local required="$1"
    local version
    if [ ! -t 0 ]; then
        IFS= read -r version
    else
        version="$("$MAS_BIN" version 2>/dev/null || echo 0.0.0)"
    fi
    local major
    major="$(printf '%s' "$version" | awk -F. '{print $1+0}')"
    [ "${major:-0}" -ge "${required:-0}" ]
}

# mas_classify_exit <code>
# Maps mas exit code to a single token usable as ascendo item status:
#   0 -> success
#   6 -> failed-not-signed-in     (mas convention; observed on signed-out hosts)
#   * -> failed
mas_classify_exit() {
    case "$1" in
        0) printf 'success\n' ;;
        6) printf 'failed-not-signed-in\n' ;;
        *) printf 'failed\n' ;;
    esac
}
```

- [ ] **Step 5: Run the tests to confirm pass**

```bash
PYTHONPATH=$(pwd)/core python -m pytest adapters/macos/tests/test_ascendo_mas_helpers.py -v
```

Expected: 6 passed (or 6 skipped if `jq` missing — that's fine; `jq` is required only at runtime, our parser tests use `awk`).

- [ ] **Step 6: Make the lib executable-friendly + shellcheck-clean**

```bash
shellcheck -s bash adapters/macos/lib/ascendo_mas.sh
```

Expected: no errors. (If `shellcheck` not installed, `brew install shellcheck` first; if you skip this on agent machines without shellcheck, document it but don't block.)

- [ ] **Step 7: Commit**

```bash
git add adapters/macos/lib/ascendo_mas.sh
git add adapters/macos/tests/test_ascendo_mas_helpers.py
git add adapters/macos/tests/fixtures/mas-list.txt
git add adapters/macos/tests/fixtures/mas-outdated.txt
git commit -m "$(cat <<'EOF'
feat(macos): lib/ascendo_mas.sh helpers (M5.2.3)

Bash 3.2-safe wrappers around the mas CLI:

  mas_signed_in           0 iff `mas list` succeeds with output
  mas_list_json           JSON array of installed apps
  mas_outdated_json       JSON array of pending updates
  mas_version_at_least N  0 iff major >= N
  mas_classify_exit C     mas exit code -> ascendo status token

Awk parsers handle the legacy `mas list` shape (id name (ver))
and `mas outdated` shape (id name (cur -> tgt)). Last-paren rule
tolerates spaces in app names.

MAS_BIN env override + *_from_stdin variants enable test seams.

6 parser tests using captured fixtures. No real mas calls required.

Refs docs/superpowers/specs/2026-05-03-macos-mas-elevation-design.md §5.1
EOF
)"
```

---

## Task 4: `scripts/mas/check.sh` — read-only sign-in probe + inventory

`check.sh` is the contract entry point — it answers the "is mas usable on this host?" question. Sign-in probe runs first; on failure, emits a single failed item (`mas:not-signed-in`) and exits with the synthesised sidecar (status='failed' from items, exit code 0 so orchestrator continues). On success, walks `mas_outdated_json` for `planned` items and `mas_list_json` minus outdated for `up_to_date` items.

**Files:**
- Create: `adapters/macos/scripts/mas/check.sh` (~110 LOC)
- Create: `adapters/macos/tests/test_check_mas_script.py` (~5 tests)

- [ ] **Step 1: Write failing tests for `check.sh` shape**

Create `adapters/macos/tests/test_check_mas_script.py`:

```python
"""End-to-end tests for adapters/macos/scripts/mas/check.sh.

Each test invokes the script with MAS_BIN pointing to a fake script that
emits captured fixture output, then validates the produced sidecar
through Pydantic parse_sidecar().
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ADAPTER_ROOT / "scripts" / "mas" / "check.sh"
FIX = ADAPTER_ROOT / "tests" / "fixtures"


@pytest.fixture(autouse=True)
def _require_jq() -> None:
    if shutil.which("jq") is None:
        pytest.skip("jq not on PATH")


def _make_fake_mas(tmp_path: Path, behaviour: str) -> Path:
    """Make a fake mas binary with one of:
        signed_out    -> `list`/`outdated` exit 1
        signed_in     -> `list` returns mas-list.txt, `outdated` returns mas-outdated.txt
        no_outdated   -> `list` returns mas-list.txt, `outdated` returns empty
    """
    p = tmp_path / "fake_mas"
    list_text = (FIX / "mas-list.txt").read_text()
    out_text = (FIX / "mas-outdated.txt").read_text()
    if behaviour == "signed_out":
        body = "#!/usr/bin/env bash\nexit 1\n"
    elif behaviour == "signed_in":
        body = (
            "#!/usr/bin/env bash\n"
            "case \"$1\" in\n"
            f"  list)     cat <<'EOF_LIST'\n{list_text}EOF_LIST\n            ;;\n"
            f"  outdated) cat <<'EOF_OUT'\n{out_text}EOF_OUT\n            ;;\n"
            "  version)  echo '4.3.0' ;;\n"
            "  *) exit 1 ;;\n"
            "esac\n"
        )
    elif behaviour == "no_outdated":
        body = (
            "#!/usr/bin/env bash\n"
            "case \"$1\" in\n"
            f"  list)     cat <<'EOF_LIST'\n{list_text}EOF_LIST\n            ;;\n"
            "  outdated) exit 0 ;;\n"
            "  version)  echo '4.3.0' ;;\n"
            "  *) exit 1 ;;\n"
            "esac\n"
        )
    else:
        raise ValueError(behaviour)
    p.write_text(body)
    os.chmod(p, 0o755)
    return p


def _run_check(fake_mas: Path, output_dir: Path, run_id: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["MAS_BIN"] = str(fake_mas)
    return subprocess.run(
        ["bash", str(SCRIPT),
         "--run-id", run_id, "--trigger", "cli",
         "--profile", "default", "--output-dir", str(output_dir)],
        capture_output=True, text=True, env=env, check=False,
    )


def _parse_sidecar(path: Path):
    sys.path.insert(0, str(ADAPTER_ROOT.parent.parent / "core"))
    try:
        from ascendo.models.sidecar import parse_sidecar
        return parse_sidecar(path.read_text())
    finally:
        sys.path.pop(0)


def test_signed_in_emits_planned_and_up_to_date_items(tmp_path):
    fake = _make_fake_mas(tmp_path, "signed_in")
    out = tmp_path / "out"
    rid = str(uuid.uuid4())
    res = _run_check(fake, out, rid)
    assert res.returncode == 0, res.stderr
    sidecar = out / rid / "check__mas.json"
    assert sidecar.is_file(), f"sidecar missing\nstdout: {res.stdout}\nstderr: {res.stderr}"

    sc = _parse_sidecar(sidecar)
    assert sc.phase.value == "check"
    assert sc.category.value == "mas"
    statuses = [i.status.value for i in sc.items]
    assert "planned" in statuses        # outdated -> planned
    assert "up_to_date" in statuses     # installed-but-not-outdated -> up_to_date


def test_signed_out_emits_failed_item(tmp_path):
    fake = _make_fake_mas(tmp_path, "signed_out")
    out = tmp_path / "out"
    rid = str(uuid.uuid4())
    res = _run_check(fake, out, rid)
    # Phase exits 0; the FAILURE shows up in sidecar status, not exit code
    assert res.returncode == 0, res.stderr
    sc = _parse_sidecar(out / rid / "check__mas.json")
    assert sc.status.value == "failed"
    assert any(i.id == "mas:not-signed-in" for i in sc.items)


def test_no_outdated_emits_only_up_to_date(tmp_path):
    fake = _make_fake_mas(tmp_path, "no_outdated")
    out = tmp_path / "out"
    rid = str(uuid.uuid4())
    res = _run_check(fake, out, rid)
    assert res.returncode == 0, res.stderr
    sc = _parse_sidecar(out / rid / "check__mas.json")
    statuses = {i.status.value for i in sc.items}
    assert statuses <= {"up_to_date"}


def test_check_with_dry_run_flag_is_no_op_for_check(tmp_path):
    """--dry-run must be accepted (build_argv passes it for all phases)
    but check is already side-effect-free, so behaviour is identical."""
    fake = _make_fake_mas(tmp_path, "signed_in")
    out = tmp_path / "out"
    rid = str(uuid.uuid4())
    env = dict(os.environ); env["MAS_BIN"] = str(fake)
    res = subprocess.run(
        ["bash", str(SCRIPT),
         "--run-id", rid, "--trigger", "cli",
         "--profile", "default", "--output-dir", str(out),
         "--dry-run"],
        capture_output=True, text=True, env=env, check=False,
    )
    assert res.returncode == 0, res.stderr
    assert (out / rid / "check__mas.json").is_file()


def test_filter_limits_planned_items(tmp_path):
    """--filter <csv> restricts planned items to listed ids."""
    fake = _make_fake_mas(tmp_path, "signed_in")
    out = tmp_path / "out"
    rid = str(uuid.uuid4())
    env = dict(os.environ); env["MAS_BIN"] = str(fake)
    res = subprocess.run(
        ["bash", str(SCRIPT),
         "--run-id", rid, "--trigger", "cli",
         "--profile", "default", "--output-dir", str(out),
         "--filter", "1153157709"],   # only Keka
        capture_output=True, text=True, env=env, check=False,
    )
    assert res.returncode == 0, res.stderr
    sc = _parse_sidecar(out / rid / "check__mas.json")
    planned = [i for i in sc.items if i.status.value == "planned"]
    assert all(i.id == "1153157709" for i in planned)
```

- [ ] **Step 2: Run the tests to confirm they fail**

```bash
PYTHONPATH=$(pwd)/core python -m pytest adapters/macos/tests/test_check_mas_script.py -v
```

Expected: 5 FAILED (script doesn't exist).

- [ ] **Step 3: Write `scripts/mas/check.sh`**

Create `adapters/macos/scripts/mas/check.sh`:

```bash
#!/usr/bin/env bash
# adapters/macos/scripts/mas/check.sh
# Read-only Mac App Store inventory phase. Bash 3.2-safe.
#
# Args (long-form only, repeatable in any order):
#   --run-id <uuid>        run identifier
#   --trigger <name>       cli | scheduler | dashboard | manual
#   --profile <slug>       profile name (default | quick | safe | full)
#   --output-dir <path>    base dir for sidecars (<run-id>/<phase>__<cat>.json)
#   --dry-run              accepted (check is already side-effect-free)
#   --filter <csv>         comma-separated app id list (limits planned items)

set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ADAPTER_LIB="$SCRIPT_DIR/../../lib"
# shellcheck disable=SC1091
. "$ADAPTER_LIB/ascendo_json.sh"
# shellcheck disable=SC1091
. "$ADAPTER_LIB/ascendo_mas.sh"

# ── parse args ────────────────────────────────────────────────
RUN_ID=""; TRIGGER=""; PROFILE_NAME=""; OUTPUT_DIR=""
DRY_RUN=0; FILTER_CSV=""
while [ $# -gt 0 ]; do
    case "$1" in
        --run-id)     RUN_ID="$2"; shift 2 ;;
        --trigger)    TRIGGER="$2"; shift 2 ;;
        --profile)    PROFILE_NAME="$2"; shift 2 ;;
        --output-dir) OUTPUT_DIR="$2"; shift 2 ;;
        --dry-run)    DRY_RUN=1; shift ;;
        --filter)     FILTER_CSV="$2"; shift 2 ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done
for v in RUN_ID TRIGGER PROFILE_NAME OUTPUT_DIR; do
    eval "[ -n \"\$$v\" ]" || { echo "missing --${v//_/-}" >&2; exit 2; }
done

# ── init sidecar buffer ───────────────────────────────────────
MAS_VER="$("$MAS_BIN" version 2>/dev/null || echo unknown)"
json_init "ascendo/v1" "check" "mas" \
    "$RUN_ID" "$TRIGGER" "$PROFILE_NAME" \
    "mas" "$MAS_VER"
trap 'json_save_on_exit "$OUTPUT_DIR" "$RUN_ID" "check" "mas"' EXIT

# ── sign-in probe ─────────────────────────────────────────────
if ! mas_signed_in; then
    json_add_message err "Not signed into Mac App Store. Open App Store.app and sign in."
    json_add_item "mas:not-signed-in" "" "" "failed" "mas"
    exit 0
fi

# ── outdated -> planned ───────────────────────────────────────
OUTDATED_JSON="$(mas_outdated_json)"
# Build CSV-of-allowed-ids set if --filter set
in_filter() {
    [ -z "$FILTER_CSV" ] && return 0
    case ",$FILTER_CSV," in (*,$1,*) return 0 ;; (*) return 1 ;; esac
}

# id, name, current, target
echo "$OUTDATED_JSON" | jq -r '.[] | [.id, .current_version, .target_version, .name] | @tsv' | \
while IFS="$(printf '\t')" read -r id cur tgt name; do
    [ -n "$id" ] || continue
    in_filter "$id" || continue
    json_add_item "$id" "$cur" "$tgt" "planned" "mas"
done

# ── installed-but-not-outdated -> up_to_date ──────────────────
LIST_JSON="$(mas_list_json)"
OUTDATED_IDS="$(echo "$OUTDATED_JSON" | jq -r '.[].id' | tr '\n' ' ')"

echo "$LIST_JSON" | jq -r '.[] | [.id, .version] | @tsv' | \
while IFS="$(printf '\t')" read -r id ver; do
    [ -n "$id" ] || continue
    case " $OUTDATED_IDS " in (*" $id "*) continue ;; esac
    json_add_item "$id" "$ver" "$ver" "up_to_date" "mas"
done

exit 0
```

- [ ] **Step 4: Make the script executable**

```bash
chmod +x adapters/macos/scripts/mas/check.sh
```

- [ ] **Step 5: Run the tests to confirm pass**

```bash
PYTHONPATH=$(pwd)/core python -m pytest adapters/macos/tests/test_check_mas_script.py -v
```

Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
git add adapters/macos/scripts/mas/check.sh
git add adapters/macos/tests/test_check_mas_script.py
git commit -m "$(cat <<'EOF'
feat(macos): scripts/mas/check.sh — sign-in + inventory phase

First mas phase script (read-only). Pattern matches scripts/brew/check.sh:

  - parse_args (--run-id --trigger --profile --output-dir [--dry-run] [--filter csv])
  - json_init "ascendo/v1" "check" "mas" ...
  - trap json_save_on_exit ... EXIT
  - sign-in probe via mas_signed_in:
      not signed in -> emit failed item id=mas:not-signed-in, exit 0
                       (phase status='failed' from items, not exit code;
                        keeps GUI side effects out of phase scripts)
  - mas_outdated_json -> planned items
  - mas_list_json minus outdated -> up_to_date items
  - --filter <csv> restricts planned items to listed ids

5 fake-mas-binary integration tests (signed_in, signed_out, no_outdated,
--dry-run no-op, --filter restriction).

Refs docs/superpowers/specs/2026-05-03-macos-mas-elevation-design.md §5.2
EOF
)"
```

---

## Task 5: `MasManager` Python adapter + 14 unit tests

Mirrors `BrewManager` exactly with two additions: takes an `IElevation` dependency in `__init__`, and for `Phase.APPLY` only injects `SUDO_ASKPASS` env var into the child process when `elevation.has_password_registered()` is True. Version-floor enforced in `is_available()` (`mas >= 4`).

**Files:**
- Create: `adapters/macos/ascendo_macos/managers/mas.py` (~280 LOC)
- Create: `adapters/macos/tests/test_mas_manager_smoke.py` (~14 tests)

- [ ] **Step 1: Write failing tests**

Create `adapters/macos/tests/test_mas_manager_smoke.py`:

```python
"""Mock-based smoke tests for MasManager.

No real mas / sudo / bash invocations — every external call is patched.
Covers the IPC contract (argv shape, env shape, sidecar parse round-trip)
and the elevation handshake (SUDO_ASKPASS env injection on apply only).
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from ascendo.interfaces.elevation import IElevation
from ascendo.interfaces.package_manager import ManagerError
from ascendo.models.host import ElevationMethod, HostInfo, OperatingSystem
from ascendo.models.package import SourceType
from ascendo.models.run import Phase, RunInfo, Trigger
from ascendo_macos.managers.mas import MasManager


# ── fixtures ─────────────────────────────────────────────────

ADAPTER_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def mac_host() -> HostInfo:
    return HostInfo(
        hostname="testmac.local", os=OperatingSystem.MACOS,
        os_version="14.5", arch="arm64", user="mk",
        is_elevated=False, elevation_method=ElevationMethod.NONE,
    )


@pytest.fixture
def linux_host() -> HostInfo:
    return HostInfo(
        hostname="testlin", os=OperatingSystem.LINUX_OTHER,
        os_version="24.04", arch="x86_64", user="x",
        is_elevated=False, elevation_method=ElevationMethod.NONE,
    )


@pytest.fixture
def windows_host() -> HostInfo:
    return HostInfo(
        hostname="testwin", os=OperatingSystem.WINDOWS,
        os_version="11.0", arch="x86_64", user="x",
        is_elevated=False, elevation_method=ElevationMethod.NONE,
    )


@pytest.fixture
def run_info() -> RunInfo:
    return RunInfo(
        id=uuid.uuid4(), trigger=Trigger.CLI, profile="default", dry_run=False,
    )


class _FakeElevation:
    """Minimal IElevation-shaped fake — only the bits MasManager touches."""
    def __init__(self, *, has_pw: bool = False, helper: Path | None = None):
        self._has_pw = has_pw
        self._helper = helper
    def has_password_registered(self) -> bool: return self._has_pw
    def askpass_path(self) -> Path | None: return self._helper


def _make_manager(elev: _FakeElevation = None) -> MasManager:
    return MasManager(
        scripts_dir=ADAPTER_ROOT / "scripts",
        lib_dir=ADAPTER_ROOT / "lib",
        elevation=elev or _FakeElevation(),
    )


# ── identity ──────────────────────────────────────────────────

def test_category_is_mas():
    m = _make_manager()
    assert m.category == SourceType.MAS


# ── is_available matrix ───────────────────────────────────────

def test_is_available_false_on_linux(linux_host):
    m = _make_manager()
    assert m.is_available(linux_host) is False


def test_is_available_false_on_windows(windows_host):
    m = _make_manager()
    assert m.is_available(windows_host) is False


def test_is_available_false_when_mas_missing(monkeypatch, mac_host):
    monkeypatch.setattr("shutil.which",
                        lambda n: None if n == "mas" else "/usr/local/bin/" + n)
    m = _make_manager()
    assert m.is_available(mac_host) is False


def test_is_available_false_when_jq_missing(monkeypatch, mac_host):
    monkeypatch.setattr("shutil.which",
                        lambda n: None if n == "jq" else "/usr/local/bin/" + n)
    m = _make_manager()
    assert m.is_available(mac_host) is False


def test_is_available_false_when_mas_too_old(monkeypatch, mac_host):
    monkeypatch.setattr("shutil.which", lambda n: "/usr/local/bin/" + n)
    fake = MagicMock(returncode=0, stdout="3.0.1\n", stderr="")
    monkeypatch.setattr("subprocess.run", lambda *a, **kw: fake)
    m = _make_manager()
    assert m.is_available(mac_host) is False


def test_is_available_true_when_all_present_and_recent(monkeypatch, mac_host):
    monkeypatch.setattr("shutil.which", lambda n: "/usr/local/bin/" + n)
    fake = MagicMock(returncode=0, stdout="4.3.0\n", stderr="")
    monkeypatch.setattr("subprocess.run", lambda *a, **kw: fake)
    m = _make_manager()
    assert m.is_available(mac_host) is True


# ── argv dispatch per phase ───────────────────────────────────

@pytest.mark.parametrize("phase,relpath", [
    (Phase.CHECK,   "scripts/mas/check.sh"),
    (Phase.PLAN,    "scripts/mas/plan.sh"),
    (Phase.APPLY,   "scripts/mas/apply.sh"),
    (Phase.VERIFY,  "scripts/mas/verify.sh"),
    (Phase.CLEANUP, "scripts/mas/cleanup.sh"),
])
def test_run_phase_dispatches_correct_script(phase, relpath, run_info, mac_host):
    captured = {}
    def fake_run_streaming(self, argv, log_path, timeout):
        captured["argv"] = argv
        captured["env"] = dict(os.environ)
        # Write a minimal valid sidecar so read_sidecar works
        run_id = run_info.id
        out_dir = Path(argv[argv.index("--output-dir") + 1])
        sidecar_path = out_dir / str(run_id) / f"{phase.value}__mas.json"
        sidecar_path.parent.mkdir(parents=True, exist_ok=True)
        sidecar_path.write_text(json.dumps(_minimal_sidecar(phase, run_id)))
        return MagicMock(returncode=0, stdout="", stderr="")

    import os
    with patch.object(MasManager, "_run_streaming", fake_run_streaming):
        m = _make_manager()
        m.run_phase(phase, run_info, mac_host)
    assert captured["argv"][1].endswith(relpath), captured["argv"]


# ── apply env injection ───────────────────────────────────────

def test_apply_exports_sudo_askpass_when_password_registered(
    run_info, mac_host, tmp_path,
):
    helper = tmp_path / "askpass-x.sh"; helper.write_text("#!/usr/bin/env bash\necho secret\n")
    captured_env = {}

    def fake_popen(self, argv, *, log_path, timeout):
        # Capture the env passed to subprocess via environment of the popen
        # In our impl, env is passed via subprocess kwargs — we patch _run_streaming
        captured_env.update(getattr(self, "_last_env_for_test", {}))
        run_id = run_info.id
        out_dir = Path(argv[argv.index("--output-dir") + 1])
        sidecar_path = out_dir / str(run_id) / "apply__mas.json"
        sidecar_path.parent.mkdir(parents=True, exist_ok=True)
        sidecar_path.write_text(json.dumps(_minimal_sidecar(Phase.APPLY, run_id)))
        return MagicMock(returncode=0, stdout="", stderr="")

    elev = _FakeElevation(has_pw=True, helper=helper)
    with patch.object(MasManager, "_run_streaming", fake_popen):
        m = _make_manager(elev)
        m.run_phase(Phase.APPLY, run_info, mac_host)
    assert m._last_env_for_test.get("SUDO_ASKPASS") == str(helper)


def test_apply_does_not_export_sudo_askpass_when_no_password(run_info, mac_host):
    captured = {}
    def fake_run_streaming(self, argv, log_path, timeout):
        captured["env"] = getattr(self, "_last_env_for_test", {})
        run_id = run_info.id
        out_dir = Path(argv[argv.index("--output-dir") + 1])
        sidecar_path = out_dir / str(run_id) / "apply__mas.json"
        sidecar_path.parent.mkdir(parents=True, exist_ok=True)
        sidecar_path.write_text(json.dumps(_minimal_sidecar(Phase.APPLY, run_id)))
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch.object(MasManager, "_run_streaming", fake_run_streaming):
        m = _make_manager(_FakeElevation(has_pw=False))
        m.run_phase(Phase.APPLY, run_info, mac_host)
    assert "SUDO_ASKPASS" not in captured["env"]


# ── error paths ───────────────────────────────────────────────

def test_run_phase_raises_manager_error_when_no_sidecar(run_info, mac_host):
    """Bash exits non-zero AND no sidecar produced -> ManagerError."""
    def fake_run_streaming(self, argv, log_path, timeout):
        return MagicMock(returncode=2, stdout="boom", stderr="")
    with patch.object(MasManager, "_run_streaming", fake_run_streaming):
        m = _make_manager()
        with pytest.raises(ManagerError):
            m.run_phase(Phase.CHECK, run_info, mac_host)


# ── helpers ───────────────────────────────────────────────────

def _minimal_sidecar(phase: Phase, run_id):
    return {
        "schema": "ascendo/v1",
        "run": {
            "id": str(run_id), "trigger": "cli", "profile": "default",
            "dry_run": False,
        },
        "host": {
            "hostname": "test", "os": "macos", "os_version": "14.5",
            "arch": "arm64", "user": "mk", "is_elevated": False,
            "elevation_method": "none",
        },
        "tool": {"name": "mas", "version": "4.3.0"},
        "phase": phase.value,
        "category": "mas",
        "started_at": "2026-05-03T12:00:00Z",
        "finished_at": "2026-05-03T12:00:01Z",
        "status": "success",
        "summary": {"total": 0, "success": 0, "failed": 0, "skipped": 0,
                    "up_to_date": 0, "planned": 0, "needs_reboot": False},
        "items": [], "messages": [],
    }
```

- [ ] **Step 2: Run the tests to confirm they all fail**

```bash
PYTHONPATH=$(pwd)/core python -m pytest adapters/macos/tests/test_mas_manager_smoke.py -v
```

Expected: All FAILED with `ModuleNotFoundError: ascendo_macos.managers.mas`.

- [ ] **Step 3: Write `MasManager`**

Create `adapters/macos/ascendo_macos/managers/mas.py`:

```python
"""MasManager — IPackageManager for Mac App Store via the `mas` CLI.

Mirrors BrewManager. Two additions beyond brew:
  - __init__ takes an IElevation (we use the concrete-only
    has_password_registered + askpass_path methods to inject
    SUDO_ASKPASS env on Phase.APPLY only).
  - is_available enforces mas major >= 4.

CVE-2025-43411: macOS 26+ requires `sudo mas upgrade`. The bash phase
script (apply.sh) always invokes `sudo -A mas upgrade`; the Python side
only decides whether SUDO_ASKPASS is present in the child env.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Iterable
from pathlib import Path
from typing import ClassVar

from ascendo.interfaces.elevation import IElevation
from ascendo.interfaces.package_manager import IPackageManager, ManagerError
from ascendo.models.host import HostInfo, OperatingSystem
from ascendo.models.package import SourceType
from ascendo.models.run import Phase, RunInfo
from ascendo.models.sidecar import Sidecar
from ascendo.orchestrator.sidecar_io import (
    SidecarIOError,
    SidecarReadError,
    read_sidecar,
)

_log = logging.getLogger(__name__)


class MasManager(IPackageManager):
    """Mac App Store per-source manager via `mas` CLI."""

    SCRIPT_BY_PHASE: ClassVar[dict[Phase, str]] = {
        Phase.CHECK:   "mas/check.sh",
        Phase.PLAN:    "mas/plan.sh",
        Phase.APPLY:   "mas/apply.sh",
        Phase.VERIFY:  "mas/verify.sh",
        Phase.CLEANUP: "mas/cleanup.sh",
    }
    DEFAULT_TIMEOUT_SEC: ClassVar[int] = 1800
    MIN_MAS_MAJOR: ClassVar[int] = 4

    def __init__(
        self,
        *,
        scripts_dir: Path,
        lib_dir: Path,
        elevation: IElevation,
        bash_path: str | None = None,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        self._scripts_dir = Path(scripts_dir)
        self._lib_dir = Path(lib_dir)
        self._elevation = elevation
        self._bash_override = bash_path
        self._timeout_sec = timeout_sec
        # Test seam: MasManager._run_streaming is patched in unit tests.
        # The patched version reads MasManager._last_env_for_test to
        # observe the env we'd have passed to a real subprocess.
        self._last_env_for_test: dict[str, str] = {}

    # ── Identity ────────────────────────────────────────────────────────

    @property
    def category(self) -> SourceType:
        return SourceType.MAS

    @property
    def display_name(self) -> str:
        return "Mac App Store (mas CLI)"

    # ── Availability ────────────────────────────────────────────────────

    def is_available(self, host: HostInfo) -> bool:
        if host.os is not OperatingSystem.MACOS:
            return False
        if shutil.which("mas") is None:
            return False
        if shutil.which("jq") is None:
            return False
        return self._mas_major_at_least(self.MIN_MAS_MAJOR)

    def _mas_major_at_least(self, required: int) -> bool:
        try:
            res = subprocess.run(  # noqa: S603, S607
                ["mas", "version"],
                capture_output=True, text=True, timeout=5, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        if res.returncode != 0:
            return False
        first = (res.stdout or "").strip().splitlines()[:1]
        if not first:
            return False
        try:
            major = int(first[0].split(".")[0])
        except (ValueError, IndexError):
            return False
        return major >= required

    # ── Phase execution ─────────────────────────────────────────────────

    def run_phase(
        self,
        phase: Phase,
        run: RunInfo,
        host: HostInfo,
        *,
        item_filter: Iterable[str] | None = None,
    ) -> Sidecar:
        script_rel = self.SCRIPT_BY_PHASE.get(phase)
        if script_rel is None:
            raise ManagerError(
                f"MasManager does not support phase {phase.value!r}; "
                f"supported: {sorted(p.value for p in self.SCRIPT_BY_PHASE)}"
            )
        script_path = self._scripts_dir / script_rel
        bash = self._resolve_bash()

        with tempfile.TemporaryDirectory(prefix="ascendo-mas-") as tmp:
            output_dir = Path(tmp)
            argv = self._build_argv(
                bash=bash, script_path=script_path, run=run,
                output_dir=output_dir, item_filter=item_filter,
            )
            log_path = output_dir / str(run.id) / f"{phase.value}__mas.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)

            # Build env: parent env + (for APPLY only, when password registered)
            #            SUDO_ASKPASS pointing at the helper.
            self._last_env_for_test = self._build_env(phase)

            _log.debug(
                "MasManager.run_phase phase=%s run_id=%s argv=%r askpass=%s",
                phase.value, run.id, argv,
                self._last_env_for_test.get("SUDO_ASKPASS"),
            )

            try:
                completed = self._run_streaming(argv, log_path, self._timeout_sec)
            except subprocess.TimeoutExpired as exc:
                raise ManagerError(
                    f"mas {phase.value} script timed out after "
                    f"{self._timeout_sec}s: {script_path}"
                ) from exc
            except OSError as exc:
                raise ManagerError(
                    f"failed to spawn bash for mas {phase.value}: {exc}"
                ) from exc

            sidecar_path = output_dir / str(run.id) / f"{phase.value}__mas.json"
            if not sidecar_path.exists():
                raise ManagerError(self._missing_sidecar_error(
                    phase=phase, script_path=script_path,
                    sidecar_path=sidecar_path, completed=completed,
                ))
            try:
                sc = read_sidecar(sidecar_path)
            except (SidecarReadError, SidecarIOError) as exc:
                raise ManagerError(
                    f"mas {phase.value} script wrote unparseable sidecar "
                    f"at {sidecar_path}: {exc}"
                ) from exc

            if completed.returncode != 0:
                _log.warning(
                    "mas %s script exited %d but produced a valid sidecar; "
                    "trusting sidecar (status=%s)",
                    phase.value, completed.returncode, sc.status.value,
                )
            return sc

    # ── Internals ───────────────────────────────────────────────────────

    def _build_argv(
        self,
        *,
        bash: str,
        script_path: Path,
        run: RunInfo,
        output_dir: Path,
        item_filter: Iterable[str] | None,
    ) -> list[str]:
        argv: list[str] = [
            bash, str(script_path),
            "--run-id", str(run.id),
            "--trigger", run.trigger.value,
            "--profile", run.profile,
            "--output-dir", str(output_dir),
        ]
        if run.dry_run:
            argv.append("--dry-run")
        if item_filter is not None:
            cleaned = [s.strip() for s in item_filter
                       if s and isinstance(s, str) and s.strip()]
            if cleaned:
                argv.extend(["--filter", ",".join(cleaned)])
        return argv

    def _build_env(self, phase: Phase) -> dict[str, str]:
        env = dict(os.environ)
        if phase is Phase.APPLY and self._elevation.has_password_registered():
            helper = self._elevation.askpass_path()
            if helper is not None:
                env["SUDO_ASKPASS"] = str(helper)
        return env

    def _run_streaming(
        self,
        argv: list[str],
        log_path: Path,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        proc = subprocess.Popen(  # noqa: S603
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=self._last_env_for_test or None,
        )
        captured: list[str] = []
        started = time.monotonic()
        try:
            with log_path.open("a", encoding="utf-8") as fh:
                for line in iter(proc.stdout.readline, ""):
                    if time.monotonic() - started > timeout:
                        proc.kill()
                        raise subprocess.TimeoutExpired(argv, timeout)
                    if not line:
                        break
                    captured.append(line)
                    try:
                        fh.write(line); fh.flush()
                    except OSError:
                        pass
        finally:
            proc.stdout.close()
        try:
            rc = proc.wait(
                timeout=max(1.0, timeout - (time.monotonic() - started)),
            )
        except subprocess.TimeoutExpired:
            proc.kill(); raise
        return subprocess.CompletedProcess(
            args=argv, returncode=rc, stdout="".join(captured), stderr="",
        )

    def _missing_sidecar_error(
        self, *,
        phase: Phase, script_path: Path, sidecar_path: Path,
        completed: subprocess.CompletedProcess[str],
    ) -> str:
        def _tail(s: str | None, limit: int = 800) -> str:
            if not s: return "<empty>"
            return s if len(s) <= limit else f"...<truncated {len(s) - limit}>...{s[-limit:]}"
        return (
            f"mas {phase.value} script produced no sidecar.\n"
            f"  script:        {script_path}\n"
            f"  expected at:   {sidecar_path}\n"
            f"  exit code:     {completed.returncode}\n"
            f"  stdout (tail): {_tail(completed.stdout)}"
        )

    def _resolve_bash(self) -> str:
        if self._bash_override is not None:
            return self._bash_override
        for cand in ("bash", "/bin/bash"):
            found = shutil.which(cand) if not cand.startswith("/") \
                else (cand if Path(cand).is_file() else None)
            if found:
                return found
        raise ManagerError("no bash on PATH and /bin/bash missing")
```

- [ ] **Step 4: Run the tests to confirm pass**

```bash
PYTHONPATH=$(pwd)/core python -m pytest adapters/macos/tests/test_mas_manager_smoke.py -v
```

Expected: 14 passed.

- [ ] **Step 5: Run the macOS tests overall to confirm no regressions**

```bash
PYTHONPATH=$(pwd)/core python -m pytest adapters/macos/tests/ -q
```

Expected: all green (M5.1 tests still pass).

- [ ] **Step 6: Commit**

```bash
git add adapters/macos/ascendo_macos/managers/mas.py
git add adapters/macos/tests/test_mas_manager_smoke.py
git commit -m "$(cat <<'EOF'
feat(macos): MasManager Python adapter (M5.2.4)

Mirrors BrewManager with two additions:

  __init__ takes an IElevation. We touch only the concrete-only
    surface (has_password_registered, askpass_path) — the IPackage
    Manager layer doesn't need IElevation.run() since `sudo -A` in
    the bash phase script IS the elevation.

  Phase.APPLY env injection: when elevation.has_password_registered(),
    SUDO_ASKPASS=<helper-path> is added to the child env. Otherwise
    no env override; the bash script's `sudo -A` falls back to a TTY
    prompt (CLI flow).

is_available() enforces mas major >= 4 (CVE-2025-43411 chain
requires the modern sudo-aware mas).

14 mock-based smoke tests cover identity, OS gate, mas/jq presence
matrix, version-floor reject, parametrized 5-phase argv dispatch,
SUDO_ASKPASS env injection on apply only, ManagerError on missing
sidecar.

Refs docs/superpowers/specs/2026-05-03-macos-mas-elevation-design.md §3
EOF
)"
```

---

## Task 6: `MacOSAdapter` wire-up — capability flip + `MasManager` + `MacElevation`

Capability flag flips from `PACKAGE_MANAGEMENT` to `PACKAGE_MANAGEMENT | ELEVATION`. `package_managers()` returns `[BrewManager, MasManager]`. `elevation()` returns a singleton `MacElevation` instance (constructed lazily and cached). `health_check()` adds `mas` component.

**Files:**
- Modify: `adapters/macos/ascendo_macos/adapter.py` (capabilities, package_managers, elevation, health_check)
- Modify: `adapters/macos/tests/test_adapter_smoke.py` (extend)

- [ ] **Step 1: Read current adapter test to see what's already covered**

```bash
cat adapters/macos/tests/test_adapter_smoke.py
```

- [ ] **Step 2: Append failing tests for the new wiring**

Append to `adapters/macos/tests/test_adapter_smoke.py`:

```python
def test_capabilities_includes_elevation():
    """M5.2: ELEVATION flag added to PACKAGE_MANAGEMENT."""
    from ascendo.interfaces import AdapterCapability
    from ascendo_macos.adapter import MacOSAdapter
    a = MacOSAdapter()
    assert AdapterCapability.PACKAGE_MANAGEMENT in a.capabilities
    assert AdapterCapability.ELEVATION in a.capabilities


def test_package_managers_includes_brew_and_mas(mac_host):
    """M5.2: package_managers returns [BrewManager, MasManager] in that order."""
    from ascendo_macos.adapter import MacOSAdapter
    from ascendo_macos.managers.brew import BrewManager
    from ascendo_macos.managers.mas import MasManager
    a = MacOSAdapter()
    pkgs = a.package_managers(mac_host)
    types = [type(p).__name__ for p in pkgs]
    assert types == ["BrewManager", "MasManager"]


def test_elevation_returns_macelevation():
    """M5.2: elevation() returns MacElevation singleton, cached across calls."""
    from ascendo_macos.adapter import MacOSAdapter
    from ascendo_macos.managers.elevation import MacElevation
    a = MacOSAdapter()
    e1 = a.elevation()
    assert isinstance(e1, MacElevation)
    e2 = a.elevation()
    assert e1 is e2  # cached


def test_health_check_includes_mas_component():
    """M5.2: doctor reports a `mas` line."""
    from ascendo_macos.adapter import MacOSAdapter
    a = MacOSAdapter()
    h = a.health_check()
    assert "mas" in h
```

If `mac_host` fixture isn't already present in this file, add at top:

```python
import pytest
from ascendo.models.host import ElevationMethod, HostInfo, OperatingSystem


@pytest.fixture
def mac_host() -> HostInfo:
    return HostInfo(
        hostname="testmac.local", os=OperatingSystem.MACOS,
        os_version="14.5", arch="arm64", user="mk",
        is_elevated=False, elevation_method=ElevationMethod.NONE,
    )
```

- [ ] **Step 3: Run the new tests to confirm they fail**

```bash
PYTHONPATH=$(pwd)/core python -m pytest adapters/macos/tests/test_adapter_smoke.py -v -k "capabilities_includes_elevation or package_managers_includes_brew_and_mas or elevation_returns_macelevation or health_check_includes_mas_component"
```

Expected: 4 FAILED.

- [ ] **Step 4: Modify `adapter.py`**

Open `adapters/macos/ascendo_macos/adapter.py`. Apply these changes:

1. Update import block to add `MasManager` and `MacElevation`:

```python
from .managers.brew import BrewManager
from .managers.elevation import MacElevation
from .managers.mas import MasManager
```

2. In `__init__`, add the elevation singleton cache:

```python
    def __init__(self) -> None:
        self._cached_host: HostInfo | None = None
        self._cached_elevation: MacElevation | None = None
```

3. Replace the `capabilities` property:

```python
    @property
    def capabilities(self) -> AdapterCapability:
        return AdapterCapability.PACKAGE_MANAGEMENT | AdapterCapability.ELEVATION
```

4. Replace `package_managers()` body:

```python
    def package_managers(self, host: HostInfo) -> list[IPackageManager]:
        return [
            BrewManager(scripts_dir=self.SCRIPTS_DIR, lib_dir=self.LIB_DIR),
            MasManager(
                scripts_dir=self.SCRIPTS_DIR,
                lib_dir=self.LIB_DIR,
                elevation=self.elevation(),
            ),
        ]
```

5. Replace `elevation()` body:

```python
    def elevation(self) -> IElevation | None:
        if self._cached_elevation is None:
            self._cached_elevation = MacElevation()
        return self._cached_elevation
```

6. In `health_check()`, after the `out["jq"] = self._jq_status()` line, add:

```python
        out["mas"] = self._mas_status()
```

7. Add a new helper method (alongside `_jq_status`):

```python
    def _mas_status(self) -> str:
        path = shutil.which("mas")
        if path is None:
            return "unavailable: mas not on PATH (install: brew install mas)"
        try:
            res = subprocess.run(
                [path, "version"],
                capture_output=True, text=True, timeout=5, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"error: {exc}"
        if res.returncode != 0:
            return f"error: mas version exited {res.returncode}"
        v = (res.stdout or "").strip().splitlines()[:1]
        ver = v[0] if v else ""
        if not ver:
            return "ok"
        # Enforce mas major >= 4
        try:
            major = int(ver.split(".")[0])
        except (ValueError, IndexError):
            return f"ok: {ver}"
        if major < 4:
            return f"degraded: mas {ver} found, need >=4 (brew upgrade mas)"
        return f"ok: {ver}"
```

- [ ] **Step 5: Run all macOS adapter tests**

```bash
PYTHONPATH=$(pwd)/core python -m pytest adapters/macos/tests/ -q
```

Expected: all green (existing M5.1 brew tests + new M5.2 tests pass).

- [ ] **Step 6: Smoke `python -m ascendo doctor`**

If running on the host Mac:

```bash
PYTHONPATH=$(pwd)/core python -m ascendo doctor
```

Expected: output includes `macos (macOS) tier=1`, `capabilities: AdapterCapability.PACKAGE_MANAGEMENT|ELEVATION`, and a `mas` line (`ok: 4.x.y` or `unavailable`).

- [ ] **Step 7: Commit**

```bash
git add adapters/macos/ascendo_macos/adapter.py
git add adapters/macos/tests/test_adapter_smoke.py
git commit -m "$(cat <<'EOF'
feat(macos): wire MasManager + MacElevation into MacOSAdapter (M5.2.5)

Capability flag flipped: PACKAGE_MANAGEMENT | ELEVATION
  (was PACKAGE_MANAGEMENT only).

package_managers() returns [BrewManager, MasManager] in that order
  (brew first because mas itself is brew-installed).

elevation() returns a cached MacElevation singleton.

health_check() now reports a `mas` component line — `ok: <version>`,
`degraded` if mas <4, or `unavailable` with an install hint.

Tests extended with 4 new wiring assertions.

Refs docs/superpowers/specs/2026-05-03-macos-mas-elevation-design.md §3
EOF
)"
```

---

## Task 7: `scripts/mas/{plan,verify,cleanup}.sh` — read-only triplet

`plan.sh` is `check.sh` minus the up-to-date sweep (only emits planned items). `verify.sh` reads the sibling apply sidecar and re-checks with `mas_outdated_json`. `cleanup.sh` is a no-op (mas has no caches to prune).

**Files:**
- Create: `adapters/macos/scripts/mas/plan.sh` (~80 LOC)
- Create: `adapters/macos/scripts/mas/verify.sh` (~100 LOC)
- Create: `adapters/macos/scripts/mas/cleanup.sh` (~50 LOC)
- Create: `adapters/macos/tests/test_mas_triplet.py` (~6 tests)

- [ ] **Step 1: Write failing tests for the triplet**

Create `adapters/macos/tests/test_mas_triplet.py`:

```python
"""Tests for plan.sh / verify.sh / cleanup.sh."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
PLAN = ADAPTER_ROOT / "scripts" / "mas" / "plan.sh"
VERIFY = ADAPTER_ROOT / "scripts" / "mas" / "verify.sh"
CLEANUP = ADAPTER_ROOT / "scripts" / "mas" / "cleanup.sh"
FIX = ADAPTER_ROOT / "tests" / "fixtures"


@pytest.fixture(autouse=True)
def _require_jq() -> None:
    if shutil.which("jq") is None:
        pytest.skip("jq not on PATH")


def _make_fake_mas(tmp_path: Path, *, signed_in: bool, outdated_text: str = "") -> Path:
    list_text = (FIX / "mas-list.txt").read_text()
    p = tmp_path / "fake_mas"
    if not signed_in:
        body = "#!/usr/bin/env bash\nexit 1\n"
    else:
        body = (
            "#!/usr/bin/env bash\n"
            "case \"$1\" in\n"
            f"  list)     cat <<'EOF_LIST'\n{list_text}EOF_LIST\n            ;;\n"
            f"  outdated) cat <<'EOF_OUT'\n{outdated_text}EOF_OUT\n            ;;\n"
            "  version)  echo '4.3.0' ;;\n"
            "  *) exit 1 ;;\n"
            "esac\n"
        )
    p.write_text(body); os.chmod(p, 0o755)
    return p


def _parse(p: Path):
    sys.path.insert(0, str(ADAPTER_ROOT.parent.parent / "core"))
    try:
        from ascendo.models.sidecar import parse_sidecar
        return parse_sidecar(p.read_text())
    finally:
        sys.path.pop(0)


def _run(script: Path, fake_mas: Path, output_dir: Path, run_id: str, *extra: str):
    env = dict(os.environ); env["MAS_BIN"] = str(fake_mas)
    return subprocess.run(
        ["bash", str(script),
         "--run-id", run_id, "--trigger", "cli",
         "--profile", "default", "--output-dir", str(output_dir),
         *extra],
        capture_output=True, text=True, env=env, check=False,
    )


def test_plan_emits_only_planned_items(tmp_path):
    fake = _make_fake_mas(tmp_path, signed_in=True,
                          outdated_text=(FIX / "mas-outdated.txt").read_text())
    out = tmp_path / "out"; rid = str(uuid.uuid4())
    res = _run(PLAN, fake, out, rid)
    assert res.returncode == 0, res.stderr
    sc = _parse(out / rid / "plan__mas.json")
    statuses = {i.status.value for i in sc.items}
    assert statuses <= {"planned"}    # NO up_to_date items in plan
    assert len(sc.items) == 2          # Keka + 1Password


def test_plan_signed_out_emits_failed_item(tmp_path):
    fake = _make_fake_mas(tmp_path, signed_in=False)
    out = tmp_path / "out"; rid = str(uuid.uuid4())
    res = _run(PLAN, fake, out, rid)
    assert res.returncode == 0
    sc = _parse(out / rid / "plan__mas.json")
    assert sc.status.value == "failed"
    assert any(i.id == "mas:not-signed-in" for i in sc.items)


def test_verify_reads_sibling_apply_sidecar(tmp_path):
    """verify.sh marks each apply item success/failed by re-checking outdated."""
    fake = _make_fake_mas(tmp_path, signed_in=True, outdated_text="")  # nothing outdated now
    out = tmp_path / "out"; rid = str(uuid.uuid4())
    apply_dir = out / rid; apply_dir.mkdir(parents=True)

    # Synthesise an apply sidecar with two success items
    apply_sidecar = {
        "schema": "ascendo/v1",
        "run": {"id": rid, "trigger": "cli", "profile": "default", "dry_run": False},
        "host": {"hostname": "x", "os": "macos", "os_version": "14.5",
                 "arch": "arm64", "user": "mk", "is_elevated": False,
                 "elevation_method": "none"},
        "tool": {"name": "mas", "version": "4.3.0"},
        "phase": "apply", "category": "mas",
        "started_at": "2026-05-03T12:00:00Z",
        "finished_at": "2026-05-03T12:01:00Z",
        "status": "success",
        "summary": {"total": 2, "success": 2, "failed": 0, "skipped": 0,
                    "up_to_date": 0, "planned": 0, "needs_reboot": False},
        "items": [
            {"id": "1153157709", "current_version": "1.4.2",
             "target_version": "1.4.3", "resolved_version": "1.4.3",
             "status": "success",
             "source": {"type": "mas", "feed": ""}},
            {"id": "1333542190", "current_version": "7.9.11",
             "target_version": "7.9.12", "resolved_version": "7.9.12",
             "status": "success",
             "source": {"type": "mas", "feed": ""}},
        ],
        "messages": [],
    }
    (apply_dir / "apply__mas.json").write_text(json.dumps(apply_sidecar))

    res = _run(VERIFY, fake, out, rid)
    assert res.returncode == 0, res.stderr
    sc = _parse(out / rid / "verify__mas.json")
    # both apply items disappeared from outdated -> verify success
    assert all(i.status.value == "success" for i in sc.items)


def test_verify_softnoop_when_no_apply_sidecar(tmp_path):
    """verify can run after check-only; no apply sidecar -> success status, zero items."""
    fake = _make_fake_mas(tmp_path, signed_in=True, outdated_text="")
    out = tmp_path / "out"; rid = str(uuid.uuid4())
    res = _run(VERIFY, fake, out, rid)
    assert res.returncode == 0
    sc = _parse(out / rid / "verify__mas.json")
    assert sc.status.value == "success"
    assert sc.items == []


def test_cleanup_emits_success_zero_items(tmp_path):
    fake = _make_fake_mas(tmp_path, signed_in=True)
    out = tmp_path / "out"; rid = str(uuid.uuid4())
    res = _run(CLEANUP, fake, out, rid)
    assert res.returncode == 0, res.stderr
    sc = _parse(out / rid / "cleanup__mas.json")
    assert sc.status.value == "success"
    assert sc.items == []


def test_cleanup_dry_run_is_identical(tmp_path):
    """No-op script: --dry-run produces identical sidecar."""
    fake = _make_fake_mas(tmp_path, signed_in=True)
    out = tmp_path / "out"; rid = str(uuid.uuid4())
    res = _run(CLEANUP, fake, out, rid, "--dry-run")
    assert res.returncode == 0
    sc = _parse(out / rid / "cleanup__mas.json")
    assert sc.status.value == "success"
    assert sc.items == []
```

- [ ] **Step 2: Run the tests to confirm they fail**

```bash
PYTHONPATH=$(pwd)/core python -m pytest adapters/macos/tests/test_mas_triplet.py -v
```

Expected: 6 FAILED (scripts don't exist).

- [ ] **Step 3: Write `plan.sh`**

Create `adapters/macos/scripts/mas/plan.sh`:

```bash
#!/usr/bin/env bash
# adapters/macos/scripts/mas/plan.sh
# Side-effect-free planned-upgrade list. Bash 3.2-safe.

set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ADAPTER_LIB="$SCRIPT_DIR/../../lib"
# shellcheck disable=SC1091
. "$ADAPTER_LIB/ascendo_json.sh"
# shellcheck disable=SC1091
. "$ADAPTER_LIB/ascendo_mas.sh"

RUN_ID=""; TRIGGER=""; PROFILE_NAME=""; OUTPUT_DIR=""
DRY_RUN=0; FILTER_CSV=""
while [ $# -gt 0 ]; do
    case "$1" in
        --run-id)     RUN_ID="$2"; shift 2 ;;
        --trigger)    TRIGGER="$2"; shift 2 ;;
        --profile)    PROFILE_NAME="$2"; shift 2 ;;
        --output-dir) OUTPUT_DIR="$2"; shift 2 ;;
        --dry-run)    DRY_RUN=1; shift ;;
        --filter)     FILTER_CSV="$2"; shift 2 ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done

MAS_VER="$("$MAS_BIN" version 2>/dev/null || echo unknown)"
json_init "ascendo/v1" "plan" "mas" \
    "$RUN_ID" "$TRIGGER" "$PROFILE_NAME" \
    "mas" "$MAS_VER"
trap 'json_save_on_exit "$OUTPUT_DIR" "$RUN_ID" "plan" "mas"' EXIT

if ! mas_signed_in; then
    json_add_message err "Not signed into Mac App Store. Open App Store.app and sign in."
    json_add_item "mas:not-signed-in" "" "" "failed" "mas"
    exit 0
fi

OUTDATED_JSON="$(mas_outdated_json)"
in_filter() {
    [ -z "$FILTER_CSV" ] && return 0
    case ",$FILTER_CSV," in (*,$1,*) return 0 ;; (*) return 1 ;; esac
}

echo "$OUTDATED_JSON" | jq -r '.[] | [.id, .current_version, .target_version, .name] | @tsv' | \
while IFS="$(printf '\t')" read -r id cur tgt name; do
    [ -n "$id" ] || continue
    in_filter "$id" || continue
    json_add_item "$id" "$cur" "$tgt" "planned" "mas"
done

exit 0
```

- [ ] **Step 4: Write `verify.sh`**

Create `adapters/macos/scripts/mas/verify.sh`:

```bash
#!/usr/bin/env bash
# adapters/macos/scripts/mas/verify.sh
# Re-check vs sibling apply__mas.json sidecar. Bash 3.2-safe.

set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ADAPTER_LIB="$SCRIPT_DIR/../../lib"
# shellcheck disable=SC1091
. "$ADAPTER_LIB/ascendo_json.sh"
# shellcheck disable=SC1091
. "$ADAPTER_LIB/ascendo_mas.sh"

RUN_ID=""; TRIGGER=""; PROFILE_NAME=""; OUTPUT_DIR=""
DRY_RUN=0; FILTER_CSV=""
while [ $# -gt 0 ]; do
    case "$1" in
        --run-id)     RUN_ID="$2"; shift 2 ;;
        --trigger)    TRIGGER="$2"; shift 2 ;;
        --profile)    PROFILE_NAME="$2"; shift 2 ;;
        --output-dir) OUTPUT_DIR="$2"; shift 2 ;;
        --dry-run)    DRY_RUN=1; shift ;;
        --filter)     FILTER_CSV="$2"; shift 2 ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done

MAS_VER="$("$MAS_BIN" version 2>/dev/null || echo unknown)"
json_init "ascendo/v1" "verify" "mas" \
    "$RUN_ID" "$TRIGGER" "$PROFILE_NAME" \
    "mas" "$MAS_VER"
trap 'json_save_on_exit "$OUTPUT_DIR" "$RUN_ID" "verify" "mas"' EXIT

APPLY_SIDECAR="$OUTPUT_DIR/$RUN_ID/apply__mas.json"
if [ ! -f "$APPLY_SIDECAR" ]; then
    json_add_message info "No sibling apply__mas.json sidecar; verify is a soft no-op."
    exit 0
fi

if ! mas_signed_in; then
    json_add_message err "Not signed into Mac App Store. Open App Store.app and sign in."
    json_add_item "mas:not-signed-in" "" "" "failed" "mas"
    exit 0
fi

OUTDATED_JSON="$(mas_outdated_json)"
STILL_OUTDATED_IDS="$(echo "$OUTDATED_JSON" | jq -r '.[].id' | tr '\n' ' ')"

# For each apply item with status=success, mark verify success when its id
# is no longer outdated; else failed.
jq -c '.items[] | select(.status == "success")' "$APPLY_SIDECAR" | \
while IFS= read -r item; do
    id="$(echo "$item" | jq -r '.id')"
    cur="$(echo "$item" | jq -r '.current_version // ""')"
    tgt="$(echo "$item" | jq -r '.target_version // ""')"
    case " $STILL_OUTDATED_IDS " in
        (*" $id "*) json_add_item "$id" "$cur" "$tgt" "failed"  "mas" ;;
        (*)         json_add_item "$id" "$cur" "$tgt" "success" "mas" ;;
    esac
done

exit 0
```

- [ ] **Step 5: Write `cleanup.sh`**

Create `adapters/macos/scripts/mas/cleanup.sh`:

```bash
#!/usr/bin/env bash
# adapters/macos/scripts/mas/cleanup.sh
# No-op: mas has no caches to prune. Emits a success sidecar with one info
# message so the orchestrator's per-(phase, category) accounting still works.

set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ADAPTER_LIB="$SCRIPT_DIR/../../lib"
# shellcheck disable=SC1091
. "$ADAPTER_LIB/ascendo_json.sh"
# shellcheck disable=SC1091
. "$ADAPTER_LIB/ascendo_mas.sh"

RUN_ID=""; TRIGGER=""; PROFILE_NAME=""; OUTPUT_DIR=""
DRY_RUN=0; FILTER_CSV=""
while [ $# -gt 0 ]; do
    case "$1" in
        --run-id)     RUN_ID="$2"; shift 2 ;;
        --trigger)    TRIGGER="$2"; shift 2 ;;
        --profile)    PROFILE_NAME="$2"; shift 2 ;;
        --output-dir) OUTPUT_DIR="$2"; shift 2 ;;
        --dry-run)    DRY_RUN=1; shift ;;
        --filter)     FILTER_CSV="$2"; shift 2 ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done

MAS_VER="$("$MAS_BIN" version 2>/dev/null || echo unknown)"
json_init "ascendo/v1" "cleanup" "mas" \
    "$RUN_ID" "$TRIGGER" "$PROFILE_NAME" \
    "mas" "$MAS_VER"
trap 'json_save_on_exit "$OUTPUT_DIR" "$RUN_ID" "cleanup" "mas"' EXIT

json_add_message info "mas has no cleanup; no-op completed"
exit 0
```

- [ ] **Step 6: Make all three executable**

```bash
chmod +x adapters/macos/scripts/mas/plan.sh adapters/macos/scripts/mas/verify.sh adapters/macos/scripts/mas/cleanup.sh
```

- [ ] **Step 7: Run the tests to confirm pass**

```bash
PYTHONPATH=$(pwd)/core python -m pytest adapters/macos/tests/test_mas_triplet.py -v
```

Expected: 6 passed.

- [ ] **Step 8: Commit**

```bash
git add adapters/macos/scripts/mas/plan.sh
git add adapters/macos/scripts/mas/verify.sh
git add adapters/macos/scripts/mas/cleanup.sh
git add adapters/macos/tests/test_mas_triplet.py
git commit -m "$(cat <<'EOF'
feat(macos): plan/verify/cleanup mas scripts (M5.2.6)

Read-only triplet completing the 5-phase mas contract:

  plan.sh    — side-effect-free upgrade list. Same as check.sh
               minus the up-to-date sweep (planned items only).
  verify.sh  — reads sibling apply__mas.json, re-runs
               mas_outdated_json, marks each apply success
               item as verify success or failed based on whether
               it's still outdated. Soft no-op when apply sidecar
               missing (verify can run after check-only).
  cleanup.sh — no-op. mas has no caches to prune; emits success
               sidecar + one info message + zero items so the
               orchestrator's per-(phase,category) accounting
               works uniformly.

All three Bash 3.2-safe. 6 fake-mas-binary integration tests.

Refs docs/superpowers/specs/2026-05-03-macos-mas-elevation-design.md §§5.3, 5.5, 5.6
EOF
)"
```

---

## Task 8: `scripts/mas/apply.sh` — first mutating script

The only mutating mas phase. Always invokes `sudo -A mas upgrade` (CVE-2025-43411). When `--dry-run`, enumerates outdated and emits `planned` items without invoking sudo. When `--filter <csv>` set, per-id loop; else bulk upgrade. Maps mas exit codes via `mas_classify_exit`.

**Files:**
- Create: `adapters/macos/scripts/mas/apply.sh` (~150 LOC)
- Create: `adapters/macos/tests/test_apply_mas_script.py` (~5 tests)

- [ ] **Step 1: Write failing tests**

Create `adapters/macos/tests/test_apply_mas_script.py`:

```python
"""Tests for adapters/macos/scripts/mas/apply.sh.

The mutating phase. We test:
  1. --dry-run produces planned items, NEVER invokes sudo
  2. real apply path invokes `sudo -A mas upgrade` (validated via fake sudo)
  3. signed-out fail-fast (no sudo invocation)
  4. --filter restricts upgrades to listed ids
  5. successful apply emits items with status=success
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ADAPTER_ROOT / "scripts" / "mas" / "apply.sh"
FIX = ADAPTER_ROOT / "tests" / "fixtures"


@pytest.fixture(autouse=True)
def _require_jq() -> None:
    if shutil.which("jq") is None:
        pytest.skip("jq not on PATH")


def _make_fake_sudo(tmp_path: Path, *, log_path: Path) -> Path:
    """Fake sudo that logs argv and forwards to argv[2:] if argv[1] == '-A',
    else argv[1:]. Always exits 0."""
    p = tmp_path / "fake_sudo"
    body = (
        "#!/usr/bin/env bash\n"
        f"echo \"$@\" >> {log_path}\n"
        "if [ \"$1\" = '-A' ]; then shift; fi\n"
        "\"$@\"\n"
    )
    p.write_text(body); os.chmod(p, 0o755)
    return p


def _make_fake_mas(tmp_path: Path, *, signed_in: bool,
                   outdated_text: str = "", upgrade_log: Path | None = None) -> Path:
    list_text = (FIX / "mas-list.txt").read_text()
    p = tmp_path / "fake_mas"
    if not signed_in:
        body = "#!/usr/bin/env bash\nexit 1\n"
    else:
        body = (
            "#!/usr/bin/env bash\n"
            f"[ -n \"{upgrade_log or ''}\" ] && echo \"$@\" >> {upgrade_log or '/dev/null'}\n"
            "case \"$1\" in\n"
            f"  list)     cat <<'EOF_LIST'\n{list_text}EOF_LIST\n            ;;\n"
            f"  outdated) cat <<'EOF_OUT'\n{outdated_text}EOF_OUT\n            ;;\n"
            "  upgrade)  shift; for id in \"$@\"; do echo \"==> upgrading $id\"; done; exit 0 ;;\n"
            "  version)  echo '4.3.0' ;;\n"
            "  *) exit 1 ;;\n"
            "esac\n"
        )
    p.write_text(body); os.chmod(p, 0o755)
    return p


def _parse(p: Path):
    sys.path.insert(0, str(ADAPTER_ROOT.parent.parent / "core"))
    try:
        from ascendo.models.sidecar import parse_sidecar
        return parse_sidecar(p.read_text())
    finally:
        sys.path.pop(0)


def _run_apply(fake_mas: Path, fake_sudo: Path, output_dir: Path,
               run_id: str, *extra: str):
    env = dict(os.environ)
    env["MAS_BIN"] = str(fake_mas)
    # Prepend a dir containing fake sudo to PATH
    bindir = fake_sudo.parent
    env["PATH"] = f"{bindir}:{env['PATH']}"
    # Rename our fake to literally "sudo" so PATH lookup finds it first
    sudo_link = bindir / "sudo"
    if not sudo_link.exists():
        sudo_link.symlink_to(fake_sudo.name)
    return subprocess.run(
        ["bash", str(SCRIPT),
         "--run-id", run_id, "--trigger", "cli",
         "--profile", "default", "--output-dir", str(output_dir),
         *extra],
        capture_output=True, text=True, env=env, check=False,
    )


def test_dry_run_emits_planned_items_no_sudo(tmp_path):
    sudo_log = tmp_path / "sudo.log"
    fake_sudo = _make_fake_sudo(tmp_path, log_path=sudo_log)
    fake_mas = _make_fake_mas(tmp_path, signed_in=True,
                              outdated_text=(FIX / "mas-outdated.txt").read_text())
    out = tmp_path / "out"; rid = str(uuid.uuid4())
    res = _run_apply(fake_mas, fake_sudo, out, rid, "--dry-run")
    assert res.returncode == 0, res.stderr

    sc = _parse(out / rid / "apply__mas.json")
    statuses = {i.status.value for i in sc.items}
    assert statuses <= {"planned"}
    assert sudo_log.exists() is False or sudo_log.read_text() == ""


def test_real_apply_invokes_sudo_a_mas_upgrade(tmp_path):
    sudo_log = tmp_path / "sudo.log"
    upgrade_log = tmp_path / "upgrade.log"
    fake_sudo = _make_fake_sudo(tmp_path, log_path=sudo_log)
    fake_mas = _make_fake_mas(tmp_path, signed_in=True,
                              outdated_text=(FIX / "mas-outdated.txt").read_text(),
                              upgrade_log=upgrade_log)
    out = tmp_path / "out"; rid = str(uuid.uuid4())
    res = _run_apply(fake_mas, fake_sudo, out, rid)
    assert res.returncode == 0, res.stderr

    # First sudo call should be `-A mas upgrade ...`
    log = sudo_log.read_text().strip().splitlines()
    assert any("-A" in line and "upgrade" in line for line in log), log


def test_signed_out_fail_fast_no_sudo(tmp_path):
    sudo_log = tmp_path / "sudo.log"
    fake_sudo = _make_fake_sudo(tmp_path, log_path=sudo_log)
    fake_mas = _make_fake_mas(tmp_path, signed_in=False)
    out = tmp_path / "out"; rid = str(uuid.uuid4())
    res = _run_apply(fake_mas, fake_sudo, out, rid)
    assert res.returncode == 0  # phase exits 0; failure in sidecar
    sc = _parse(out / rid / "apply__mas.json")
    assert sc.status.value == "failed"
    assert any(i.id == "mas:not-signed-in" for i in sc.items)
    assert (not sudo_log.exists()) or sudo_log.read_text() == ""


def test_filter_restricts_to_listed_ids(tmp_path):
    sudo_log = tmp_path / "sudo.log"
    upgrade_log = tmp_path / "upgrade.log"
    fake_sudo = _make_fake_sudo(tmp_path, log_path=sudo_log)
    fake_mas = _make_fake_mas(tmp_path, signed_in=True,
                              outdated_text=(FIX / "mas-outdated.txt").read_text(),
                              upgrade_log=upgrade_log)
    out = tmp_path / "out"; rid = str(uuid.uuid4())
    res = _run_apply(fake_mas, fake_sudo, out, rid,
                     "--filter", "1153157709")
    assert res.returncode == 0, res.stderr

    # Only Keka was upgraded; 1Password was not
    upg = upgrade_log.read_text() if upgrade_log.exists() else ""
    assert "1153157709" in upg
    assert "1333542190" not in upg


def test_apply_emits_success_items_for_upgraded(tmp_path):
    sudo_log = tmp_path / "sudo.log"
    fake_sudo = _make_fake_sudo(tmp_path, log_path=sudo_log)
    fake_mas = _make_fake_mas(tmp_path, signed_in=True,
                              outdated_text=(FIX / "mas-outdated.txt").read_text())
    out = tmp_path / "out"; rid = str(uuid.uuid4())
    res = _run_apply(fake_mas, fake_sudo, out, rid)
    assert res.returncode == 0, res.stderr
    sc = _parse(out / rid / "apply__mas.json")
    statuses = [i.status.value for i in sc.items]
    assert any(s == "success" for s in statuses)
```

- [ ] **Step 2: Run the tests to confirm they fail**

```bash
PYTHONPATH=$(pwd)/core python -m pytest adapters/macos/tests/test_apply_mas_script.py -v
```

Expected: 5 FAILED (apply.sh doesn't exist).

- [ ] **Step 3: Write `apply.sh`**

Create `adapters/macos/scripts/mas/apply.sh`:

```bash
#!/usr/bin/env bash
# adapters/macos/scripts/mas/apply.sh
# Mutating phase: `sudo -A mas upgrade` (CVE-2025-43411). Bash 3.2-safe.

set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ADAPTER_LIB="$SCRIPT_DIR/../../lib"
# shellcheck disable=SC1091
. "$ADAPTER_LIB/ascendo_json.sh"
# shellcheck disable=SC1091
. "$ADAPTER_LIB/ascendo_mas.sh"

RUN_ID=""; TRIGGER=""; PROFILE_NAME=""; OUTPUT_DIR=""
DRY_RUN=0; FILTER_CSV=""
while [ $# -gt 0 ]; do
    case "$1" in
        --run-id)     RUN_ID="$2"; shift 2 ;;
        --trigger)    TRIGGER="$2"; shift 2 ;;
        --profile)    PROFILE_NAME="$2"; shift 2 ;;
        --output-dir) OUTPUT_DIR="$2"; shift 2 ;;
        --dry-run)    DRY_RUN=1; shift ;;
        --filter)     FILTER_CSV="$2"; shift 2 ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done

MAS_VER="$("$MAS_BIN" version 2>/dev/null || echo unknown)"
json_init "ascendo/v1" "apply" "mas" \
    "$RUN_ID" "$TRIGGER" "$PROFILE_NAME" \
    "mas" "$MAS_VER"
trap 'json_save_on_exit "$OUTPUT_DIR" "$RUN_ID" "apply" "mas"' EXIT

# ── sign-in probe (fail-fast, no sudo invocation) ─────────────
if ! mas_signed_in; then
    json_add_message err "Not signed into Mac App Store. Open App Store.app and sign in."
    json_add_item "mas:not-signed-in" "" "" "failed" "mas"
    exit 0
fi

OUTDATED_JSON="$(mas_outdated_json)"

in_filter() {
    [ -z "$FILTER_CSV" ] && return 0
    case ",$FILTER_CSV," in (*,$1,*) return 0 ;; (*) return 1 ;; esac
}

# ── --dry-run path: enumerate planned items, exit ─────────────
if [ "$DRY_RUN" -eq 1 ]; then
    echo "$OUTDATED_JSON" | jq -r '.[] | [.id, .current_version, .target_version, .name] | @tsv' | \
    while IFS="$(printf '\t')" read -r id cur tgt name; do
        [ -n "$id" ] || continue
        in_filter "$id" || continue
        json_add_item "$id" "$cur" "$tgt" "planned" "mas"
    done
    json_add_message info "dry-run: no real upgrades performed"
    exit 0
fi

# ── real apply path ───────────────────────────────────────────
# Collect target ids
TARGET_IDS=""
TARGET_VERSIONS=""    # parallel arrays via space-separated strings (Bash 3.2)
while IFS="$(printf '\t')" read -r id cur tgt name; do
    [ -n "$id" ] || continue
    in_filter "$id" || continue
    TARGET_IDS="$TARGET_IDS $id"
    TARGET_VERSIONS="$TARGET_VERSIONS $id|$cur|$tgt"
done < <(echo "$OUTDATED_JSON" | jq -r '.[] | [.id, .current_version, .target_version, .name] | @tsv')

if [ -z "$(echo "$TARGET_IDS" | tr -d ' ')" ]; then
    json_add_message info "nothing to upgrade"
    exit 0
fi

# Per-id loop when --filter set; bulk otherwise.
if [ -n "$FILTER_CSV" ]; then
    for id in $TARGET_IDS; do
        # find current/target for this id from TARGET_VERSIONS
        cur=""; tgt=""
        for entry in $TARGET_VERSIONS; do
            case "$entry" in ("$id|"*)
                cur="$(printf '%s' "$entry" | awk -F'|' '{print $2}')"
                tgt="$(printf '%s' "$entry" | awk -F'|' '{print $3}')"
                break
            ;; esac
        done
        if sudo -A "$MAS_BIN" upgrade "$id" 2>&1 | tee -a "$OUTPUT_DIR/$RUN_ID/apply__mas.log"; then
            json_add_item "$id" "$cur" "$tgt" "success" "mas"
        else
            rc=$?
            STATUS="$(mas_classify_exit "$rc")"
            json_add_item "$id" "$cur" "$tgt" "$STATUS" "mas"
            json_add_message err "mas upgrade $id exited $rc -> $STATUS"
        fi
    done
else
    if sudo -A "$MAS_BIN" upgrade $TARGET_IDS 2>&1 | tee -a "$OUTPUT_DIR/$RUN_ID/apply__mas.log"; then
        for entry in $TARGET_VERSIONS; do
            id="$(printf '%s' "$entry" | awk -F'|' '{print $1}')"
            cur="$(printf '%s' "$entry" | awk -F'|' '{print $2}')"
            tgt="$(printf '%s' "$entry" | awk -F'|' '{print $3}')"
            json_add_item "$id" "$cur" "$tgt" "success" "mas"
        done
    else
        rc=$?
        STATUS="$(mas_classify_exit "$rc")"
        for entry in $TARGET_VERSIONS; do
            id="$(printf '%s' "$entry" | awk -F'|' '{print $1}')"
            cur="$(printf '%s' "$entry" | awk -F'|' '{print $2}')"
            tgt="$(printf '%s' "$entry" | awk -F'|' '{print $3}')"
            json_add_item "$id" "$cur" "$tgt" "$STATUS" "mas"
        done
        json_add_message err "mas upgrade exited $rc -> $STATUS"
    fi
fi

exit 0
```

- [ ] **Step 4: Make the script executable**

```bash
chmod +x adapters/macos/scripts/mas/apply.sh
```

- [ ] **Step 5: Run the tests to confirm pass**

```bash
PYTHONPATH=$(pwd)/core python -m pytest adapters/macos/tests/test_apply_mas_script.py -v
```

Expected: 5 passed.

- [ ] **Step 6: Run the full mas test suite for regression check**

```bash
PYTHONPATH=$(pwd)/core python -m pytest adapters/macos/tests/ -q
```

Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add adapters/macos/scripts/mas/apply.sh
git add adapters/macos/tests/test_apply_mas_script.py
git commit -m "$(cat <<'EOF'
feat(macos): scripts/mas/apply.sh — first mutating phase (M5.2.7)

The only mutating mas phase. Pattern:

  --dry-run path: enumerate outdated, emit planned items, no sudo
                  invocation. Operator-safe trial-run.
  real path:      sign-in probe (fail-fast); collect target ids;
                  --filter set -> per-id loop calling
                  `sudo -A mas upgrade <id>`; else bulk
                  `sudo -A mas upgrade <id1> <id2> ...`. Emit
                  success items on rc=0, mapped via mas_classify_exit
                  on non-zero.

CVE-2025-43411 enforced — apply NEVER calls bare `mas upgrade`.

`sudo -A` falls back to TTY prompt when SUDO_ASKPASS env is unset
(CLI flow), uses the askpass helper when set (dashboard flow). The
bash script doesn't inspect SUDO_ASKPASS; MasManager._build_env in
Python decides whether to inject it.

5 fake-sudo + fake-mas integration tests cover dry-run no-sudo,
real apply argv shape, signed-out fail-fast, --filter restriction,
success-item emission.

Refs docs/superpowers/specs/2026-05-03-macos-mas-elevation-design.md §5.4
EOF
)"
```

---

## Task 9: Dashboard `/elevation/{auth,invalidate,status}` endpoints + 6 contract tests

Three new HTTP endpoints under `/elevation/*`. Mounted only when `app.state.adapter.elevation() is not None`. CORS allow-list inherits from `/runs`. Reads/writes the singleton `MacElevation` (or any future concrete `IElevation`) via the adapter.

**Files:**
- Create: `core/ascendo/dashboard/routes/elevation.py` (~140 LOC)
- Modify: `core/ascendo/dashboard/app.py` (mount the router conditionally)
- Create: `tests/contract/test_dashboard_elevation.py` (~6 tests)

- [ ] **Step 1: Write failing contract tests**

Create `tests/contract/test_dashboard_elevation.py`:

```python
"""Contract tests for /elevation/* dashboard endpoints (M5.2.8)."""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from ascendo.dashboard.app import create_app
from ascendo.interfaces import AdapterCapability, IAdapter
from ascendo.interfaces.elevation import IElevation


class _FakeElevation:
    """IElevation-shaped fake exposing the dashboard's required methods."""
    def __init__(self):
        self._registered = False
        self._method = "sudo"
        self.last_call: dict[str, Any] = {}

    def has_password_registered(self) -> bool:
        return self._registered

    def register_password(self, pw: str, *, verify: bool = True, timeout: int = 15):
        self.last_call["password"] = pw
        if pw == "rightpw":
            self._registered = True
            return True, "verified"
        return False, "Sorry, try again."

    def invalidate(self):
        self._registered = False

    @property
    def available_methods(self):
        from ascendo.models.host import ElevationMethod
        return (ElevationMethod.SUDO,)


class _FakeAdapterWithElevation(IAdapter):
    @property
    def name(self) -> str: return "macos"
    @property
    def display_name(self) -> str: return "macOS"
    @property
    def tier(self) -> int: return 1
    @property
    def capabilities(self) -> AdapterCapability:
        return AdapterCapability.PACKAGE_MANAGEMENT | AdapterCapability.ELEVATION
    def package_managers(self, host): return []
    def inventory(self): return None
    def snapshot(self): return None
    def scheduler(self): return None
    def source(self): return None
    _elev = _FakeElevation()
    def elevation(self): return self._elev
    def detect_host(self):
        from ascendo.models.host import ElevationMethod, HostInfo, OperatingSystem
        return HostInfo(
            hostname="test", os=OperatingSystem.MACOS, os_version="14.5",
            arch="arm64", user="mk", is_elevated=False,
            elevation_method=ElevationMethod.NONE,
        )
    def health_check(self): return {"adapter": "ok"}


class _FakeAdapterNoElevation(IAdapter):
    @property
    def name(self) -> str: return "linux"
    @property
    def display_name(self) -> str: return "Linux"
    @property
    def tier(self) -> int: return 1
    @property
    def capabilities(self) -> AdapterCapability:
        return AdapterCapability.PACKAGE_MANAGEMENT
    def package_managers(self, host): return []
    def inventory(self): return None
    def snapshot(self): return None
    def scheduler(self): return None
    def source(self): return None
    def elevation(self): return None
    def detect_host(self):
        from ascendo.models.host import ElevationMethod, HostInfo, OperatingSystem
        return HostInfo(
            hostname="test", os=OperatingSystem.LINUX_OTHER, os_version="24.04",
            arch="x86_64", user="x", is_elevated=False,
            elevation_method=ElevationMethod.NONE,
        )
    def health_check(self): return {"adapter": "ok"}


@pytest.fixture
def client_with_elevation(tmp_path):
    adapter = _FakeAdapterWithElevation()
    app = create_app(adapter=adapter, runs_dir=tmp_path)
    return TestClient(app), adapter


@pytest.fixture
def client_no_elevation(tmp_path):
    adapter = _FakeAdapterNoElevation()
    app = create_app(adapter=adapter, runs_dir=tmp_path)
    return TestClient(app)


def test_status_before_auth(client_with_elevation):
    client, _ = client_with_elevation
    r = client.get("/elevation/status")
    assert r.status_code == 200
    body = r.json()
    assert body == {"registered": False, "method": "sudo"}


def test_auth_with_correct_password_returns_200(client_with_elevation):
    client, _ = client_with_elevation
    r = client.post("/elevation/auth", json={"password": "rightpw"})
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_auth_with_wrong_password_returns_401(client_with_elevation):
    client, _ = client_with_elevation
    r = client.post("/elevation/auth", json={"password": "wrongpw"})
    assert r.status_code == 401
    body = r.json()
    assert "detail" in body


def test_status_after_auth_reports_registered(client_with_elevation):
    client, _ = client_with_elevation
    client.post("/elevation/auth", json={"password": "rightpw"})
    r = client.get("/elevation/status")
    assert r.json() == {"registered": True, "method": "sudo"}


def test_invalidate_is_idempotent(client_with_elevation):
    client, _ = client_with_elevation
    client.post("/elevation/auth", json={"password": "rightpw"})
    r1 = client.post("/elevation/invalidate")
    r2 = client.post("/elevation/invalidate")
    assert r1.status_code == 200 and r2.status_code == 200
    assert client.get("/elevation/status").json()["registered"] is False


def test_endpoints_503_when_adapter_has_no_elevation(client_no_elevation):
    r = client_no_elevation.get("/elevation/status")
    assert r.status_code == 503
    r = client_no_elevation.post("/elevation/auth", json={"password": "x"})
    assert r.status_code == 503
    r = client_no_elevation.post("/elevation/invalidate")
    assert r.status_code == 503
```

- [ ] **Step 2: Run the tests to confirm they fail**

```bash
PYTHONPATH=$(pwd)/core python -m pytest tests/contract/test_dashboard_elevation.py -v
```

Expected: 6 FAILED with `404 Not Found` on every route (router not mounted yet).

- [ ] **Step 3: Write the elevation router**

Create `core/ascendo/dashboard/routes/elevation.py`:

```python
"""Dashboard endpoints for elevation (sudo askpass) — POST /elevation/auth,
POST /elevation/invalidate, GET /elevation/status.

Mounted only when ``app.state.adapter.elevation() is not None``. When the
adapter has no IElevation, every endpoint returns 503 so the SPA can
gracefully degrade.

Security:
    * Loopback-only (the dashboard binds 127.0.0.1 by default).
    * Password is held in the IElevation impl's process memory only.
    * 401 on bad password — no info leak about user accounts.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

router = APIRouter(prefix="/elevation", tags=["elevation"])


class AuthRequest(BaseModel):
    password: str = Field(..., min_length=1, description="Sudo password.")

    model_config = {"extra": "forbid"}


class StatusResponse(BaseModel):
    registered: bool
    method: str | None


def _elevation_or_503(request: Request):
    adapter = getattr(request.app.state, "adapter", None)
    if adapter is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="no adapter configured",
        )
    elev = adapter.elevation()
    if elev is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="adapter does not provide elevation",
        )
    return elev


@router.get("/status", response_model=StatusResponse)
def get_status(request: Request) -> StatusResponse:
    elev = _elevation_or_503(request)
    methods = elev.available_methods
    return StatusResponse(
        registered=elev.has_password_registered(),
        method=methods[0].value if methods else None,
    )


@router.post("/auth")
def post_auth(request: Request, body: AuthRequest) -> dict[str, Any]:
    elev = _elevation_or_503(request)
    ok, detail = elev.register_password(body.password)
    if not ok:
        raise HTTPException(status_code=401, detail=detail)
    return {"ok": True}


@router.post("/invalidate")
def post_invalidate(request: Request) -> dict[str, Any]:
    elev = _elevation_or_503(request)
    elev.invalidate()
    return {"ok": True}
```

- [ ] **Step 4: Mount the router in `app.py`**

Open `core/ascendo/dashboard/app.py`. Add the import alongside the other routers:

```python
from .routes import elevation as elevation_routes
```

In the function that builds the app (typically `create_app`), find where other routers are included (e.g. `app.include_router(runs_routes.router)`). Add the elevation router mount, conditional on the adapter providing it:

```python
    # Mount /elevation/* only when the adapter supports it.
    # Endpoints themselves return 503 if the adapter is None at request
    # time — but we still mount the routes so the API surface is
    # discoverable in OpenAPI.
    app.include_router(elevation_routes.router)
```

(We mount unconditionally. The 503 logic in the route handlers covers the no-elevation adapter case; this keeps the OpenAPI surface stable across adapters.)

- [ ] **Step 5: Run the tests to confirm pass**

```bash
PYTHONPATH=$(pwd)/core python -m pytest tests/contract/test_dashboard_elevation.py -v
```

Expected: 6 passed.

- [ ] **Step 6: Run the full dashboard contract suite for regression**

```bash
PYTHONPATH=$(pwd)/core python -m pytest tests/contract/ -q
```

Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add core/ascendo/dashboard/routes/elevation.py
git add core/ascendo/dashboard/app.py
git add tests/contract/test_dashboard_elevation.py
git commit -m "$(cat <<'EOF'
feat(dashboard): /elevation/{auth,invalidate,status} endpoints (M5.2.8)

Three new endpoints supporting the macOS sudo askpass flow (and
any future IElevation adapter):

  GET  /elevation/status      {"registered": bool, "method": "sudo"|null}
  POST /elevation/auth        {"password": "..."} -> 200 {"ok": true}
                              or 401 {"detail": "..."}
  POST /elevation/invalidate  200 {"ok": true} (idempotent)

503 returned on every endpoint when the adapter has no IElevation
(Linux/Windows dashboards unaffected — endpoints exist in OpenAPI but
return 503 at request time).

Loopback-only — dashboard binds 127.0.0.1 by default. Password
flows from request body to IElevation.register_password and lives
in process memory only (per app/backend/sudo.py pattern).

6 contract tests cover happy/wrong/missing paths.

Refs docs/superpowers/specs/2026-05-03-macos-mas-elevation-design.md §4.2
EOF
)"
```

---

## Task 10: `bin/validate-macos.sh` Stage 8 — mas + dashboard askpass round-trip

Adds an end-to-end Stage 8 to the existing validate harness. Steps 8.1–8.6 exercise the 5 mas phases against a live `mas` (read-only + dry-run; no real apply). Step 8.7 fires the dashboard askpass round-trip via `curl`, gated on `$SUDO_PW` env var being set (skipped with `[skip]` otherwise — CI-friendly).

**Files:**
- Modify: `bin/validate-macos.sh`

- [ ] **Step 1: Read existing validate-macos.sh structure**

```bash
grep -n "^# ==> \|^==> " bin/validate-macos.sh | head -20
wc -l bin/validate-macos.sh
```

Note the existing stage layout. The Stage 8 we add must come AFTER the brew + dashboard stages so we can re-use any started-and-stopped dashboard process pattern.

- [ ] **Step 2: Append Stage 8 to `bin/validate-macos.sh`**

Open `bin/validate-macos.sh`. Find the line just before the final summary print (`echo "ALL CHECKS PASSED."`). Insert the following block above it:

```bash
# ============================================================
# Stage 8 — mas + elevation
# ============================================================
echo
echo "==> [Stage 8] mas + elevation"

# Step 8.1 — doctor reports `mas`
if PYTHONPATH="$REPO_ROOT/core" python3 -m ascendo doctor | grep -E "^\s*mas\s+" >/dev/null; then
    echo "  Step 8.1 doctor reports mas component         OK"
else
    echo "  Step 8.1 doctor reports mas component         FAIL"
    FAIL_COUNT=$((FAIL_COUNT+1))
fi

# Skip 8.2-8.6 if mas itself is missing — we already reported the failure.
if ! command -v mas >/dev/null 2>&1; then
    echo "  Step 8.2-8.6 [skip] mas not installed (brew install mas)"
else
    RUN_DIR="$(mktemp -d "${TMPDIR:-/tmp}/ascendo-validate-mas-XXXXXX")"
    for phase in check plan; do
        rid="$(uuidgen 2>/dev/null || python3 -c 'import uuid; print(uuid.uuid4())')"
        if PYTHONPATH="$REPO_ROOT/core" python3 -m ascendo run \
                --category mas --phase "$phase" \
                --runs-dir "$RUN_DIR" >/dev/null 2>&1; then
            echo "  Step 8.${phase}_X mas $phase                              OK"
        else
            echo "  Step 8.${phase}_X mas $phase                              FAIL"
            FAIL_COUNT=$((FAIL_COUNT+1))
        fi
    done

    rid="$(uuidgen 2>/dev/null || python3 -c 'import uuid; print(uuid.uuid4())')"
    if PYTHONPATH="$REPO_ROOT/core" python3 -m ascendo run \
            --category mas --phase apply --dry-run \
            --runs-dir "$RUN_DIR" >/dev/null 2>&1; then
        echo "  Step 8.4 mas apply --dry-run                  OK"
    else
        echo "  Step 8.4 mas apply --dry-run                  FAIL"
        FAIL_COUNT=$((FAIL_COUNT+1))
    fi

    for phase in verify cleanup; do
        if PYTHONPATH="$REPO_ROOT/core" python3 -m ascendo run \
                --category mas --phase "$phase" \
                --runs-dir "$RUN_DIR" >/dev/null 2>&1; then
            echo "  Step 8.${phase}_X mas $phase                              OK"
        else
            echo "  Step 8.${phase}_X mas $phase                              FAIL"
            FAIL_COUNT=$((FAIL_COUNT+1))
        fi
    done
fi

# Step 8.7 — Dashboard askpass round-trip (gated on $SUDO_PW)
if [ -z "${SUDO_PW:-}" ]; then
    echo "  Step 8.7 [skip] dashboard askpass round-trip (set \$SUDO_PW to enable)"
else
    PORT="${ASCENDO_VALIDATE_PORT:-8765}"
    PYTHONPATH="$REPO_ROOT/core" python3 -m ascendo dashboard --port "$PORT" --background >/dev/null 2>&1 &
    DASH_PID=$!
    sleep 2

    fail_step() { echo "  Step 8.7   $1                                       FAIL"; FAIL_COUNT=$((FAIL_COUNT+1)); }

    # 8.7a status -> registered: false
    body="$(curl -fsS "http://127.0.0.1:$PORT/elevation/status" || echo '')"
    if echo "$body" | grep -q '"registered": *false'; then
        echo "  Step 8.7a status before auth        OK"
    else
        fail_step "status before auth"
    fi

    # 8.7b POST /elevation/auth -> 200
    if curl -fsS -X POST "http://127.0.0.1:$PORT/elevation/auth" \
        -H 'Content-Type: application/json' \
        -d "{\"password\":\"$SUDO_PW\"}" >/dev/null; then
        echo "  Step 8.7b POST /elevation/auth      OK"
    else
        fail_step "POST /elevation/auth"
    fi

    # 8.7c status -> registered: true
    body="$(curl -fsS "http://127.0.0.1:$PORT/elevation/status" || echo '')"
    if echo "$body" | grep -q '"registered": *true'; then
        echo "  Step 8.7c status after auth         OK"
    else
        fail_step "status after auth"
    fi

    # 8.7d kick a mas dry-run apply via /runs/async
    rj="$(curl -fsS -X POST "http://127.0.0.1:$PORT/runs/async" \
        -H 'Content-Type: application/json' \
        -d '{"categories":["mas"],"phases":["apply"],"dry_run":true}' || echo '')"
    rid="$(echo "$rj" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("run_id",""))' 2>/dev/null)"
    if [ -n "$rid" ]; then
        for _ in 1 2 3 4 5 6 7 8 9 10; do
            st="$(curl -fsS "http://127.0.0.1:$PORT/runs/$rid/status" 2>/dev/null \
                  | python3 -c 'import json,sys; print(json.load(sys.stdin).get("status",""))' 2>/dev/null)"
            case "$st" in completed|failed) break ;; esac
            sleep 1
        done
        if [ "$st" = "completed" ]; then
            echo "  Step 8.7d POST /runs/async + poll   OK"
        else
            fail_step "POST /runs/async + poll (status=$st)"
        fi
    else
        fail_step "POST /runs/async returned no run_id"
    fi

    # 8.7e POST /elevation/invalidate
    if curl -fsS -X POST "http://127.0.0.1:$PORT/elevation/invalidate" >/dev/null; then
        echo "  Step 8.7e POST /elevation/invalidate OK"
    else
        fail_step "POST /elevation/invalidate"
    fi

    # 8.7f status -> registered: false
    body="$(curl -fsS "http://127.0.0.1:$PORT/elevation/status" || echo '')"
    if echo "$body" | grep -q '"registered": *false'; then
        echo "  Step 8.7f status after invalidate   OK"
    else
        fail_step "status after invalidate"
    fi

    kill "$DASH_PID" 2>/dev/null || true
    wait "$DASH_PID" 2>/dev/null || true
fi
```

- [ ] **Step 3: Run `validate-macos.sh` and confirm Stage 8 prints (skipped or full)**

```bash
bash bin/validate-macos.sh
```

Expected outcomes:
* If `$SUDO_PW` is unset and `mas` is installed: Stage 8 shows steps 8.1–8.6 OK, 8.7 `[skip]`.
* If `$SUDO_PW` is set: full Stage 8 incl. all 8.7 sub-steps OK.
* Final line: `ALL CHECKS PASSED.`

If anything fails, the script's final exit code is non-zero and the failed steps are visible.

- [ ] **Step 4: Commit**

```bash
git add bin/validate-macos.sh
git commit -m "$(cat <<'EOF'
feat(bin): validate-macos.sh Stage 8 — mas + dashboard askpass (M5.2.9)

End-to-end validation of M5.2 in the existing harness:

  Step 8.1   doctor reports `mas` component
  Steps 8.2-8.6  mas check / plan / apply --dry-run / verify / cleanup
  Step 8.7   dashboard askpass round-trip:
             - GET  /elevation/status (registered=false)
             - POST /elevation/auth   (200)
             - GET  /elevation/status (registered=true)
             - POST /runs/async       (mas dry-run apply)
             - poll /runs/<id>/status (completed)
             - POST /elevation/invalidate
             - GET  /elevation/status (registered=false)

Step 8.7 skipped with `[skip]` when $SUDO_PW unset (CI-friendly).
For the v0.0.9-alpha tag, operator must export $SUDO_PW so the
round-trip actually runs (see run-tag-release-macos.sh).

Refs docs/superpowers/specs/2026-05-03-macos-mas-elevation-design.md §6.2
EOF
)"
```

---

## Task 11: `bin/run-tag-release-macos.sh` — `--mas` flag + tag bump

Adds a `--mas` flag to the existing harness. When set, the script performs a real `sudo mas upgrade` (or `sudo mas install <id>` fallback when nothing is outdated) between the brew step and the tag step. Tag bumps from `v0.0.8-alpha` → `v0.0.9-alpha`.

**Files:**
- Modify: `bin/run-tag-release-macos.sh`

- [ ] **Step 1: Read existing harness shape**

```bash
grep -n "TAG=\|^# ==> \|^==> \|^TAG_NAME" bin/run-tag-release-macos.sh | head -30
```

Note where the brew apply happens, where the tag is created, and what flags already exist.

- [ ] **Step 2: Add the `--mas` flag and the mas step**

Open `bin/run-tag-release-macos.sh`. Apply these changes:

1. Find the arg-parse loop. Add the new flag:

```bash
        --mas)        DO_MAS=1; shift ;;
```

Default it near the top of the script alongside the other flag defaults:

```bash
DO_MAS=0
```

2. Bump the tag near the top:

```bash
TAG_NAME="v0.0.9-alpha"     # was v0.0.8-alpha
TAG_MESSAGE="macOS adapter M5.2 — mas + MacElevation; v0.0.9-alpha"
```

3. Find the spot just AFTER the real brew apply succeeded and BEFORE the verify/cleanup/tag steps. Insert:

```bash
# ============================================================
# Stage 5b — real mas apply (M5.2)
# ============================================================
if [ "$DO_MAS" -eq 1 ]; then
    echo
    echo "==> [Stage 5b] real mas apply"
    if [ -z "${SUDO_PW:-}" ]; then
        echo "  ERROR: --mas requires \$SUDO_PW for the dashboard askpass round-trip"
        echo "         (validate-macos.sh Step 8.7 must run for the v0.0.9-alpha tag)"
        echo "         export SUDO_PW='...' and re-run."
        exit 2
    fi

    # Probe outdated; if any, real upgrade. Else: re-install one already-installed
    # app to exercise the same elevation+mas surface.
    OUTDATED_RAW="$(mas outdated 2>/dev/null || echo '')"
    if [ -n "$OUTDATED_RAW" ]; then
        FIRST_ID="$(echo "$OUTDATED_RAW" | awk 'NR==1 {print $1}')"
        echo "  upgrading first outdated id=$FIRST_ID"
        if PYTHONPATH="$REPO_ROOT/core" python3 -m ascendo run \
                --category mas --phase apply \
                --filter "$FIRST_ID"; then
            echo "  mas apply --filter $FIRST_ID                  OK"
        else
            echo "  mas apply failed                              FAIL"
            exit 30
        fi
    else
        # Re-install fallback: pick first id from `mas list`
        FIRST_ID="$(mas list 2>/dev/null | awk 'NR==1 {print $1}')"
        if [ -z "$FIRST_ID" ]; then
            echo "  WARN: no installed App Store apps; mas validation skipped."
        else
            echo "  no outdated; re-installing first listed id=$FIRST_ID (same elevation surface)"
            if sudo mas install "$FIRST_ID"; then
                echo "  sudo mas install $FIRST_ID                    OK"
            else
                echo "  sudo mas install failed                       FAIL"
                exit 30
            fi
        fi
    fi
fi
```

- [ ] **Step 3: Smoke the harness with `--what-if --mas` (dry-run path)**

```bash
bash bin/run-tag-release-macos.sh --what-if --mas
```

Expected: prints planned brew + mas steps without mutating; exits 0.

- [ ] **Step 4: Commit**

```bash
git add bin/run-tag-release-macos.sh
git commit -m "$(cat <<'EOF'
feat(bin): run-tag-release-macos.sh --mas flag + v0.0.9-alpha (M5.2.10)

Adds the mas step to the tag-release harness:

  --mas       Performs a real `sudo mas upgrade <first-outdated>`
              between the brew step and the verify/cleanup/tag steps.
              Falls back to `sudo mas install <first-listed>` (same
              elevation surface) when nothing outdated.

  Requires $SUDO_PW so the dashboard askpass round-trip in Step 8.7
  of validate-macos.sh actually runs — that's the M5.2 tag exit bar
  (real elevation flow validated end-to-end).

Tag bumped: v0.0.8-alpha -> v0.0.9-alpha.
Tag message: "macOS adapter M5.2 — mas + MacElevation; v0.0.9-alpha".

Refs docs/superpowers/specs/2026-05-03-macos-mas-elevation-design.md §6.3
EOF
)"
```

---

## Task 12: Real-hardware validation + tag `v0.0.9-alpha`

Run the full smoke harness on the host Mac. Real `sudo mas upgrade` (or `install` fallback). Real dashboard askpass round-trip. Tag `v0.0.9-alpha` once everything's green.

**Pre-reqs:**
- macOS host with `mas >= 4`, `jq`, signed in to App Store, `sudo` working
- Operator able to type sudo password into `$SUDO_PW` env var
- Latest worktree branch

**Files:** none (validation + tag only).

- [ ] **Step 1: Install mas + jq if missing**

```bash
brew install mas jq
brew upgrade mas    # ensure mas >= 4
mas version         # expect 4.x.y
```

- [ ] **Step 2: Sign in to Mac App Store**

Open App Store.app, sign in if not already. Run:

```bash
mas account 2>&1 || true   # may be unstable on macOS 26+
mas list >/dev/null         # MUST succeed
echo "signed in: $?"        # expect 0
```

- [ ] **Step 3: Run install-dev to refresh editable installs**

```bash
bash bin/install-dev-macos.sh
```

Expected: prints `INSTALL OK`, `validate-macos.sh ALL CHECKS PASSED.` at the end.

- [ ] **Step 4: Set `$SUDO_PW` and run validate-macos.sh fully**

```bash
read -s -p "sudo password: " SUDO_PW; export SUDO_PW; echo
bash bin/validate-macos.sh
```

Expected: every Stage 8 step OK (incl. 8.7 round-trip). Final line: `ALL CHECKS PASSED.` Exit code 0.

If Step 8.7d fails with `status=failed`, look at the latest run sidecar:

```bash
ls -t logs/runs | head -3
cat "logs/runs/$(ls -t logs/runs | head -1)/apply__mas.json" | jq .
```

Most likely cause: signed-out (re-login App Store), wrong password (re-export `$SUDO_PW`), or mas < 4 (re-run `brew upgrade mas`).

- [ ] **Step 5: Run the tag-release harness**

```bash
bash bin/run-tag-release-macos.sh --mas
```

You'll be prompted for `apply` confirmation at each gate. Type the literal word `apply` to proceed. Expected ending:

```
==> [Stage 7] tag
  tagged v0.0.9-alpha.
```

The script does NOT push.

- [ ] **Step 6: Verify the tag locally**

```bash
git tag -l v0.0.9-alpha
git show v0.0.9-alpha --stat | head -20
```

Expected: tag exists; commit message matches `M5.2 — mas + MacElevation; v0.0.9-alpha`.

- [ ] **Step 7: No commit needed (tag is the artifact)**

Tag is created by the harness. The push step is deferred to Task 13's docs commit.

---

## Task 13: Update `HANDOFF.md` + `PLAN.md` + push

Wrap up the milestone. New `Sesja 21` entry in `HANDOFF.md` summarising what shipped. `PLAN.md` flips M5.2 to ✅ done. Final push of all commits + tag.

**Files:**
- Modify: `HANDOFF.md` (prepend new Sesja 21 entry)
- Modify: `PLAN.md` (M5.2 row → done; add what landed)

- [ ] **Step 1: Prepend the new Sesja 21 entry to `HANDOFF.md`**

Open `HANDOFF.md`. Insert this block immediately AFTER the line `# Ascendo — Implementation Handoff` and the intro blockquote, BEFORE the existing `## Sesja 20 ...` section:

```markdown
## Sesja 21 (2026-05-03) — macOS adapter M5.2: mas + MacElevation + v0.0.9-alpha

Second milestone of the macOS adapter. Mac App Store updates land via
`sudo mas upgrade`, driven by a new `MacElevation` (`IElevation` impl
with in-memory password + ephemeral `SUDO_ASKPASS` helper), exercised
both from the CLI (TTY prompt fallback) and from the dashboard via
`POST /elevation/auth`. Tag `v0.0.9-alpha` created locally.

### Architecture confirmed end-to-end

- Layer 4 core unchanged except for `SourceType.MAS` enum addition.
- `MacOSAdapter.capabilities` now `PACKAGE_MANAGEMENT | ELEVATION`.
  `package_managers()` returns `[BrewManager, MasManager]`.
  `elevation()` returns a cached `MacElevation` singleton.
- `bin/validate-macos.sh` Stage 8 (mas + dashboard askpass) prints all
  green when `$SUDO_PW` is exported.
- The `sudo mas upgrade` rule (CVE-2025-43411) is enforced — apply.sh
  always invokes `sudo -A mas upgrade`, never bare `mas upgrade`.

### Files added (per M5.2.x sub-milestone)

- `core/ascendo/models/package.py` — added `SourceType.MAS` (M5.2.1)
- `docs/architecture/schemas/sidecar.v1.schema.json` — regenerated (M5.2.1)
- `adapters/macos/ascendo_macos/managers/elevation.py` — `MacElevation` (M5.2.2)
- `adapters/macos/lib/ascendo_mas.sh` — bash mas helpers (M5.2.3)
- `adapters/macos/scripts/mas/check.sh` (M5.2.4)
- `adapters/macos/ascendo_macos/managers/mas.py` — `MasManager` (M5.2.5)
- `adapters/macos/ascendo_macos/adapter.py` — capabilities flip + wiring (M5.2.5)
- `adapters/macos/scripts/mas/{plan,verify,cleanup}.sh` — read-only triplet (M5.2.6)
- `adapters/macos/scripts/mas/apply.sh` — first mas mutation (M5.2.7)
- `core/ascendo/dashboard/routes/elevation.py` — 3 endpoints (M5.2.8)
- `bin/validate-macos.sh` — Stage 8 added (M5.2.9)
- `bin/run-tag-release-macos.sh` — `--mas` flag + v0.0.9-alpha bump (M5.2.10)

Total: ~14 mas-manager + ~10 elevation + ~6 mas-helpers + ~5 check-script
+ ~6 triplet + ~5 apply + ~6 dashboard = **~52 new tests** + Stage 8 e2e.

### What's next (M5.3+, separate specs)

- **M5.3** — `LaunchServicesInventory` + `INVENTORY` capability.
- **M5.4** — `softwareupdate` manager (the `-R` rule) + Time Machine
  read-only `ISnapshot`.
- **M5.5** — `launchd` `IScheduler`. After this, tag `v0.2.0` (full M5).
- **M5.2.x follow-ups (deferred during M5.2)**:
  Track 2 AppleScript GUI for iPad apps; osascript GUI password dialog;
  SPA modal extension to prompt sudo password when targeting mas.

### Spec + plan

- `docs/superpowers/specs/2026-05-03-macos-mas-elevation-design.md`
- `docs/superpowers/plans/2026-05-03-macos-mas-elevation.md`

---

```

- [ ] **Step 2: Flip the M5.2 row in `PLAN.md`**

Open `PLAN.md`. Find the M5 milestone table. Locate the `M5.2 ⏳ pending` row and replace with:

```markdown
| **M5.2** | ✅ done (2026-05-03, **v0.0.9-alpha**) | `MasManager` + `MacElevation` (sudo askpass cache for dashboard-driven sudo). The `sudo mas upgrade` rule (CVE-2025-43411) enforced. Track 2 AppleScript GUI for iPad apps deferred. ~52 new tests + 8/8 Stage 8 checks via `validate-macos.sh`. Spec/plan: `docs/superpowers/specs/2026-05-03-macos-mas-elevation-design.md` + `docs/superpowers/plans/2026-05-03-macos-mas-elevation.md`. See HANDOFF.md Sesja 21. |
```

In the "Last updated" line at the top, bump:

```markdown
> Last updated: 2026-05-03 (sesja 21) — macOS adapter M5.2 shipped (mas + MacElevation, v0.0.9-alpha).
```

In the per-manager scope table, flip rows for `mas.py` and `elevation.py` from `M5.2` to `✅ M5.2`.

- [ ] **Step 3: Commit + push everything**

```bash
git add HANDOFF.md PLAN.md
git commit -m "$(cat <<'EOF'
docs: HANDOFF Sesja 21 + PLAN M5.2 done (v0.0.9-alpha)

macOS adapter M5.2 complete on real Mac:
  - sudo mas upgrade end-to-end via MacElevation
  - dashboard POST /elevation/auth round-trip green in
    validate-macos.sh Stage 8.7
  - v0.0.9-alpha tagged locally
  - Track 2 (AppleScript GUI for iPad apps), osascript GUI
    password dialog, SPA modal sudo prompt: deferred follow-ups

Next: M5.3 — LaunchServicesInventory + INVENTORY capability.
EOF
)"

git push origin claude/musing-herschel-b52e7e
git push origin v0.0.9-alpha
```

- [ ] **Step 4: Final regression check**

```bash
PYTHONPATH=$(pwd)/core python -m pytest tests/contract/ adapters/macos/tests/ -q
```

Expected: all green. The branch is now ready for review + merge to `main`.

---

## Sesja 21 — what landed (summary for future reference)

This plan ships in 13 tasks (~6 days single-dev). Tasks 2 + 3 can parallelize; everything else is sequential. The done bar is `bin/validate-macos.sh` exit 0 with all 8 Stage-8 steps OK and a real `sudo mas upgrade` performed by `bin/run-tag-release-macos.sh --mas`.

After M5.2, `MacOSAdapter` declares `PACKAGE_MANAGEMENT | ELEVATION`. M5.3 adds `INVENTORY`, M5.4 adds `SNAPSHOTS`, M5.5 adds `SCHEDULING`. After M5.5 the macOS adapter is at parity with `WindowsAdapter`'s capability set, and we tag `v0.2.0`.

---

## Decisions log (carried from spec for plan-level reference)

| Q | Decision | Why |
|---|---|---|
| Apply-path scope | **A** — Track 1 (`sudo mas upgrade`) only | Smallest viable slice; mirrors M5.1's lean shape; Track 2 deferred |
| Elevation surface | **B** — in-memory password + ephemeral `SUDO_ASKPASS` helper, TTY fallback | Mirrors proven `app/backend/sudo.py` pattern; CLI gets terminal prompt via `sudo -A` |
| `mas` bootstrap | **A** — hard requirement | Phase scripts stay side-effect-pure; doctor instructs `brew install mas` |
| Sign-in probe location | **A** — `check.sh` only | Apply fail-fasts on mas's own error; no GUI side effects from phase scripts |
| Done bar / tag | **C** — real `sudo mas upgrade` + dashboard `POST /elevation/auth` round-trip; `v0.0.9-alpha` | Validates the elevation interface AND the dashboard wire-up end-to-end |
| osascript GUI password dialog | Deferred | Small follow-up; only matters in no-TTY no-dashboard no-password gap |
| Frontend modal extension | Deferred | Backend endpoints land in M5.2 so round-trip is `curl`-validated |
| Capability flag | `PACKAGE_MANAGEMENT \| ELEVATION` | Smallest delta from M5.1 |

---

## Risk + rollback notes

**Risk: Stage 8.7 flakes on slow runs.** The polling loop in `validate-macos.sh` waits ~10 seconds for `/runs/<id>/status` to reach `completed`. If a Mac is under heavy load this might race. Mitigation: bump the iteration count in the loop. Out of scope for this plan — file as M5.2.x if it actually flakes.

**Risk: `sudo -k` in `MacElevation.invalidate()` clears the user's whole sudo timestamp.** This is the same behaviour as `app/backend/sudo.py`. Documented; users running other sudo-needing tools in parallel will have to re-auth those after invalidate. Acceptable for the dashboard's "log out elevation" semantics.

**Risk: `register_password()` stores plaintext in process memory.** Documented in spec §1 and inline. Loopback-only dashboard + 0700 helper file mode + `atexit` cleanup are the mitigations. A future hardening milestone may add `mlock(2)` or kernel-keyring backing — out of scope for M5.2.

**Rollback path:** the worktree branch can be deleted; the local tag `v0.0.9-alpha` can be removed with `git tag -d v0.0.9-alpha`. No remote-state mutations until Task 13's `git push`. The brew M5.1 work + `v0.0.8-alpha` tag are independent and unaffected.
