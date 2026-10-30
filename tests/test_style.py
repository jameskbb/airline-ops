"""The page CSS must keep titles clear of Streamlit's fixed 60px header."""

from __future__ import annotations

import re

from flightops.ui.components import STYLE

HEADER_PX = 60


def test_main_block_top_padding_clears_the_header():
    css = STYLE.read_text()
    rules = re.findall(r"([^{}]*(?:stMainBlockContainer|block-container)[^{}]*)\{([^}]*)\}", css)
    assert rules, "no rule targets the main block container"
    for _, body in rules:
        match = re.search(r"padding-top:\s*([\d.]+)(px|rem)", body)
        if match:
            value, unit = float(match.group(1)), match.group(2)
            px = value if unit == "px" else value * 16
            assert px >= HEADER_PX + 12, f"padding-top {match.group(0)} hides the title under the header"
