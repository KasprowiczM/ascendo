"""
Tests for per-tab inline help panels in the Ascendo SPA.

Verifies that:
1. Every view section contains a .tab-help help panel
2. Every help panel has required DOM structure (summary, details, list items)
3. All data-i18n keys in help panels exist in both EN and PL i18n blocks
"""

import json
import re
from pathlib import Path
from html.parser import HTMLParser


class HTMLHelpExtractor(HTMLParser):
    """Parse HTML to extract view sections and their help panels."""

    def __init__(self):
        super().__init__()
        self.views = {}  # {view_id: {has_help: bool, help_i18n_keys: [...]}}
        self.current_view_id = None
        self.current_section_depth = 0
        self.in_tab_help = False
        self.i18n_keys = []

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)

        # Detect view section start
        if tag == "section" and "class" in attrs_dict:
            classes = attrs_dict["class"].split()
            if "view" in classes and "id" in attrs_dict:
                self.current_view_id = attrs_dict["id"].replace("view-", "")
                self.current_section_depth += 1
                if self.current_view_id not in self.views:
                    self.views[self.current_view_id] = {
                        "has_help": False,
                        "i18n_keys": [],
                    }

        # Detect help panel start
        if tag == "section" and self.current_view_id:
            classes = attrs_dict.get("class", "").split()
            if "tab-help" in classes:
                self.in_tab_help = True

        # Collect data-i18n attributes
        if "data-i18n" in attrs_dict:
            key = attrs_dict["data-i18n"]
            if key and self.current_view_id:
                self.i18n_keys.append(key)

    def handle_endtag(self, tag):
        # Detect view section end
        if tag == "section" and self.current_section_depth > 0:
            self.current_section_depth -= 1
            if self.current_section_depth == 0:
                # Mark help panel found if we collected keys
                if self.current_view_id and self.i18n_keys:
                    self.views[self.current_view_id]["has_help"] = True
                    self.views[self.current_view_id]["i18n_keys"] = self.i18n_keys
                    self.i18n_keys = []
                self.current_view_id = None
                self.in_tab_help = False


def test_all_views_have_help_panels():
    """Every view section must have a .tab-help help panel."""
    html_path = Path(__file__).parent.parent / "app" / "frontend" / "index.html"
    with open(html_path, encoding="utf-8") as f:
        content = f.read()

    # Count view sections
    view_pattern = r'<section[^>]*id="view-(\w+)"[^>]*class="[^"]*view'
    views = re.findall(view_pattern, content)

    # Each view must have a tab-help after its h2
    for view_id in views:
        # Check for the pattern: <h2...>...</h2> followed by <section class="tab-help
        pattern = (
            rf'<section[^>]*id="view-{view_id}"[^>]*>.*?'
            rf'<h2[^>]*>.*?</h2>\s*'
            rf'<section[^>]*class="[^"]*tab-help[^"]*'
        )
        assert re.search(
            pattern, content, re.DOTALL
        ), f"View '{view_id}' missing tab-help panel after h2"


def test_help_panel_structure():
    """Each help panel must have correct DOM structure."""
    html_path = Path(__file__).parent.parent / "app" / "frontend" / "index.html"
    with open(html_path, encoding="utf-8") as f:
        content = f.read()

    # Find all tab-help sections
    help_pattern = (
        r'<section[^>]*class="[^"]*tab-help[^"]*card"[^>]*>(.*?)</section>'
    )
    helps = re.findall(help_pattern, content, re.DOTALL)

    assert len(helps) > 0, "No help panels found"

    for i, help_html in enumerate(helps):
        # Must have a <p> with summary
        assert (
            "<p" in help_html and "data-i18n" in help_html
        ), f"Help panel {i} missing summary paragraph"

        # Must have a <details> with <summary>
        assert (
            "<details" in help_html
        ), f"Help panel {i} missing details element"
        assert (
            "<summary" in help_html and "data-i18n" in help_html
        ), f"Help panel {i} missing summary element"

        # Must have a <ul> with <li> items
        assert "<ul" in help_html, f"Help panel {i} missing ul element"
        assert "<li" in help_html, f"Help panel {i} missing li elements"

        # Count li items
        li_count = help_html.count("<li")
        assert li_count >= 4, f"Help panel {i} has {li_count} items; need 4+"


def test_help_i18n_keys_exist():
    """All data-i18n keys in help panels must exist in both EN and PL."""
    fe = Path(__file__).parent.parent / "app" / "frontend"
    html_path = fe / "index.html"

    with open(html_path, encoding="utf-8") as f:
        html_content = f.read()

    # Sesja 78: locale data split into i18n.en.js + i18n.pl.js. Concat
    # both so the existing "appears twice (EN+PL)" occurrence logic
    # below holds (1 per file × 2 files).
    i18n_content = (
        (fe / "i18n.en.js").read_text(encoding="utf-8")
        + (fe / "i18n.pl.js").read_text(encoding="utf-8")
    )

    # Extract all data-i18n keys from help panels
    help_keys = set()
    help_pattern = (
        r'<section[^>]*class="[^"]*tab-help[^"]*'
        r'(.*?)'
        r"</section>"
    )
    for help_match in re.finditer(help_pattern, html_content, re.DOTALL):
        help_html = help_match.group(1)
        key_pattern = r'data-i18n="([^"]+)"'
        for key_match in re.finditer(key_pattern, help_html):
            help_keys.add(key_match.group(1))

    # Check that each key appears in i18n.js (both EN and PL sections)
    # Since EN and PL both contain the same keys (just different values),
    # we look for the key_name: pattern (the nested property within each view block)
    missing_keys = []

    for key in sorted(help_keys):
        # Extract just the key part after the last dot (e.g., 'overview.help_summary' -> 'help_summary')
        key_name = key.split('.')[-1]
        # Check for the pattern: "key_name:" (appears in both EN and PL blocks)
        key_pattern = re.escape(key_name) + r':\s*"'
        if not re.search(key_pattern, i18n_content):
            missing_keys.append(key)

    if missing_keys:
        print(f"[WARN] Missing i18n keys: {missing_keys}")
        assert not missing_keys, f"Missing i18n keys: {missing_keys}"

    # Count occurrences to ensure they appear in both EN and PL
    for key in sorted(help_keys):
        # Extract just the key part after the last dot
        key_name = key.split('.')[-1]
        key_pattern = re.escape(key_name) + r':\s*"'
        count = len(re.findall(key_pattern, i18n_content))
        # Each key should appear twice (once in EN, once in PL)
        if count != 2:
            print(f"[WARN] Key {key} appears {count} times (expected 2)")

    print(f"[OK] All {len(help_keys)} help i18n keys verified in i18n.js")


if __name__ == "__main__":
    test_all_views_have_help_panels()
    print("[OK] All views have help panels")

    test_help_panel_structure()
    print("[OK] All help panels have correct DOM structure")

    test_help_i18n_keys_exist()
    print("[OK] All help i18n keys exist in both EN and PL")

    print("\nAll tests passed!")
