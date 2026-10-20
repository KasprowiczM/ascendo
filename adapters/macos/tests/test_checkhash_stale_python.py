"""B1: stale python3 hash after brew upgrade must not crash the sidecar.

Mirrors upstream macOS_updates tests/test_run_log_regressions_20261011.py
(CheckhashTests).
"""
from __future__ import annotations

import os
import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]  # adapters/macos/
ASCENDO_JSON_SH = REPO / "lib" / "ascendo_json.sh"


def _run_bash(script: str, env=None, timeout: int = 30):
    return subprocess.run(
        ["/bin/bash", "-c", script],
        capture_output=True,
        text=True,
        cwd=str(REPO),
        env=env or {**os.environ, "PATH": os.environ.get("PATH", "/usr/bin:/bin")},
        timeout=timeout,
    )


class TestCheckhashStalePython(unittest.TestCase):
    """PATH dir A has python3 stub -> json_init -> delete stub -> json_add_item + json_save must still work."""

    def _run(self, prelude: str) -> subprocess.CompletedProcess:
        with tempfile.TemporaryDirectory() as tmp:
            dir_a = Path(tmp) / "a"
            dir_b = Path(tmp) / "b"
            dir_a.mkdir()
            dir_b.mkdir()
            out_dir = Path(tmp) / "out"
            out_dir.mkdir()
            for d, label in ((dir_a, "A"), (dir_b, "B")):
                fake = d / "python3"
                # Functional python3 that can actually run _json_emit.py
                fake.write_text("#!/bin/sh\nexec /usr/bin/python3 \"$@\"\n")
                fake.chmod(0o755)
            script = (
                f'set -e\n'
                f'export PATH="{dir_a}:{dir_b}:/usr/bin:/bin"\n'
                f'source "{ASCENDO_JSON_SH}"\n'
                f'{prelude}\n'
                f'json_init check brew run1 cli default brew 1.0 macbook macos 14.5 arm64 user false\n'
                # hash python3 -> dir_a
                f'python3 --version 2>/dev/null || true\n'
                # Simulate Homebrew dropping the symlink
                f'rm -f "{dir_a}/python3"\n'
                # This must NOT fail with "No such file or directory"
                f'json_add_item test 1.0 1.1 success\n'
                f'json_save "{out_dir}"\n'
                f'test -f "{out_dir}/run1/check__brew.json"\n'
                f'echo "EXIT:$?"\n'
            )
            proc = _run_bash(script)
            return proc

    def test_checkhash_falls_through_to_next_path_entry(self):
        proc = self._run(":")  # ascendo_json.sh has its own checkhash now
        self.assertEqual(proc.returncode, 0, f"stderr: {proc.stderr}")
        self.assertIn("EXIT:0", proc.stdout)

    def test_without_checkhash_the_stale_path_fails(self):
        """Negative control: proves the scenario reproduces the run-log failure."""
        proc = self._run("shopt -u checkhash")
        # Should fail because the hash table points to deleted dir_a/python3
        self.assertNotEqual(proc.returncode, 0)


class TestCheckhashStaticGuards(unittest.TestCase):
    """Static analysis: every phase script enables checkhash after pipefail."""

    def test_all_phase_scripts_have_checkhash_after_pipefail(self):
        scripts_dir = REPO / "scripts"
        for sh in sorted(scripts_dir.rglob("*.sh")):
            text = sh.read_text(encoding="utf-8")
            if "set -o pipefail" not in text:
                continue
            with self.subTest(script=str(sh.relative_to(REPO))):
                pipefail_pos = text.index("set -o pipefail")
                self.assertIn("shopt -s checkhash", text,
                    f"{sh.name} has pipefail but no checkhash")
                checkhash_pos = text.index("shopt -s checkhash")
                self.assertGreater(checkhash_pos, pipefail_pos,
                    f"{sh.name}: checkhash must come after pipefail")

    def test_brew_apply_has_hash_r_after_upgrade(self):
        text = (REPO / "scripts" / "brew" / "apply.sh").read_text()
        self.assertIn("hash -r", text, "brew/apply.sh needs hash -r after brew upgrade")

    def test_pip_apply_has_hash_r_after_install(self):
        text = (REPO / "scripts" / "pip" / "apply.sh").read_text()
        self.assertIn("hash -r", text, "pip/apply.sh needs hash -r after pip install")

    def test_ascendo_json_has_checkhash(self):
        text = ASCENDO_JSON_SH.read_text()
        self.assertIn("shopt -s checkhash", text)
