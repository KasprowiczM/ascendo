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

    def test_shell_version_matches_python_core(self) -> None:
        """CFBundleShortVersionString must match core/ascendo/__version__.py.

        1.0.1 shipped a 0.2.0 Info.plist because tauri.conf.json drifted.
        """
        import re

        core = ROOT.parents[1] / "core" / "ascendo" / "__version__.py"
        py_ver = re.search(r'__version__\s*=\s*"([^"]+)"', core.read_text(encoding="utf-8"))
        self.assertIsNotNone(py_ver)
        version = py_ver.group(1)
        conf = json.loads((ROOT / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))
        self.assertEqual(conf["version"], version)
        cargo = (ROOT / "src-tauri" / "Cargo.toml").read_text(encoding="utf-8")
        self.assertIn(f'version = "{version}"', cargo)

    def test_tauri_conf_identifier_and_no_static_windows(self) -> None:
        p = ROOT / "src-tauri" / "tauri.conf.json"
        self.assertTrue(p.exists(), f"missing {p}")
        data = json.loads(p.read_text(encoding="utf-8"))
        # We deliberately keep app.windows empty so the only window is built
        # at runtime in main.rs with a port-resolved URL. A static window
        # entry with the same label "main" would conflict and crash the app
        # with: "a webview with label `main` already exists".
        self.assertEqual(data["app"]["windows"], [])
        self.assertEqual(data["identifier"], "dev.ascendo.desktop")
        self.assertEqual(data["productName"], "Ascendo")

    def test_main_rs_window_dimensions(self) -> None:
        # The window builder lives in main.rs now (not tauri.conf.json) so
        # that's where we pin the dimensions.
        p = ROOT / "src-tauri" / "src" / "main.rs"
        text = p.read_text(encoding="utf-8")
        self.assertIn("inner_size(1280.0, 800.0)", text)
        self.assertIn("min_inner_size(960.0, 600.0)", text)
        self.assertIn('"Ascendo - Unified Updates"', text)

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
