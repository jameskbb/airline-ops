"""House writing rule: no em dash (U+2014) in any tracked text file."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EM_DASH = chr(0x2014)


def _tracked_files() -> list[Path]:
    try:
        out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    return [ROOT / name for name in out.decode().split("\0") if name]


def test_no_em_dash_in_tracked_text_files():
    offenders = []
    for path in _tracked_files():
        if not path.is_file():
            continue
        raw = path.read_bytes()
        if b"\0" in raw[:8192]:
            continue  # binary (images, Parquet)
        text = raw.decode("utf-8", errors="ignore")
        offenders += [f"{path.relative_to(ROOT)}:{n}" for n, line in enumerate(text.splitlines(), 1)
                      if EM_DASH in line]
    assert not offenders, "em dash found (reword with a colon, comma, parentheses or a new sentence): " + \
        ", ".join(offenders)
