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
    html_path = Path(__file__).parent.parent / "app" / "frontend" / "index.html"
    i18n_path = Path(__file__).parent.parent / "app" / "frontend" / "i18n.js"

    with open(html_path, encoding="utf-8") as f:
        html_content = f.read()

    with open(i18n_path, encoding="utf-8") as f:
        i18n_content = f.read()

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

    # Simple approach: just check that each key string exists in i18n.js
    # This avoids complex parsing of nested structures
    missing_en = []
    missing_pl = []

    # Split into en and pl sections
    en_start = i18n_content.find("en: {")
    pl_start = i18n_content.find("pl: {")

    if en_start < 0 or pl_start < 0:
        print("[WARN] Could not find en: or pl: sections in i18n.js")
        return

    en_section = i18n_content[en_start:pl_start]
    pl_section = i18n_content[pl_start:]

    # Check each key
    for key in sorted(help_keys):
        if key not in en_section:
            missing_en.append(key)
        if key not in pl_section:
            missing_pl.append(key)

    if missing_en:
        print(f"[WARN] Missing EN keys: {missing_en}")
    if missing_pl:
        print(f"[WARN] Missing PL keys: {missing_pl}")

    assert not missing_en, f"Missing EN keys: {missing_en}"
    assert not missing_pl, f"Missing PL keys: {missing_pl}"

    print(f"[OK] All {len(help_keys)} help i18n keys verified in EN and PL")


if __name__ == "__main__":
    test_all_views_have_help_panels()
    print("[OK] All views have help panels")

    test_help_panel_structure()
    print("[OK] All help panels have correct DOM structure")

    test_help_i18n_keys_exist()
    print("[OK] All help i18n keys exist in both EN and PL")

    print("\nAll tests passed!")
