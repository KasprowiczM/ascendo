"""Toolchain-free smoke test for the Tauri 2.x scaffold.

Verifies the file structure and that the key config artefacts reference
the right things. Intentionally does not invoke `cargo`, `npm`, or
`tauri` — those require Rust + Node + WebView2 which may not be present
on every contributor's machine.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class TestScaffold(unittest.TestCase):
    def test_package_json(self) -> None:
        p = ROOT / "package.json"
        self.assertTrue(p.exists(), f"missing {p}")
        data = json.loads(p.read_text(encoding="utf-8"))
        dev_deps = data.get("devDependencies", {})
        self.assertIn("@tauri-apps/cli", dev_deps)
        self.assertTrue(
            dev_deps["@tauri-apps/cli"].startswith("^2"),
            f"expected Tauri CLI 2.x, got {dev_deps['@tauri-apps/cli']}",
        )

    def test_cargo_toml(self) -> None:
        p = ROOT / "src-tauri" / "Cargo.toml"
        self.assertTrue(p.exists(), f"missing {p}")
        text = p.read_text(encoding="utf-8")
        self.assertIn("tauri", text)
        self.assertIn('tauri = { version = "2"', text)
        self.assertIn("ureq", text)

    def test_tauri_conf_window_size(self) -> None:
        p = ROOT / "src-tauri" / "tauri.conf.json"
        self.assertTrue(p.exists(), f"missing {p}")
        data = json.loads(p.read_text(encoding="utf-8"))
        win = data["app"]["windows"][0]
        self.assertEqual(win["width"], 1280)
        self.assertEqual(win["height"], 800)
        self.assertEqual(win["title"], "Ascendo - Unified Updates")
        self.assertEqual(data["identifier"], "dev.ascendo.app")

    def test_main_rs_spawns_python(self) -> None:
        p = ROOT / "src-tauri" / "src" / "main.rs"
        self.assertTrue(p.exists(), f"missing {p}")
        text = p.read_text(encoding="utf-8")
        # The sidecar invocation must reference both the module and subcommand.
        self.assertIn("ascendo", text)
        self.assertIn("dashboard", text)
        # Lifecycle hooks we depend on.
        self.assertIn("/health", text)
        self.assertIn("WebviewUrl::External", text)
        self.assertIn("WindowEvent::Destroyed", text)


if __name__ == "__main__":
    unittest.main()
