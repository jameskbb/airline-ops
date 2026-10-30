"""Re-shoot the README screenshots in docs/screenshots/ (dev only, not a runtime dependency).

Run from the repository root:

    uv run --no-project --with-requirements requirements.txt --with playwright==1.62.0 \
        python scripts/capture_screenshots.py

``--no-project`` keeps uv from writing a lockfile; Playwright never becomes a project dependency.
The first run may need the browser: ``uv run --no-project --with playwright==1.62.0 playwright install chromium``.
The script starts the app headless on a free port, opens every page in one browser session (so the
Data Explorer lists the queries the other pages ran, as a visitor would see it) and writes each PNG
at the size the README has always used: 1600 px wide, 1900 px tall (1700 for the two detail shots).
"""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "screenshots"
WIDTH = 1600

# (file name, sidebar url_path or "" for the default page, viewport height)
PAGES = [
    ("overview", "", 1900),
    ("network-map", "network-map", 1900),
    ("signals", "signals", 1900),
    ("airports", "airports", 1900),
    ("routes", "routes", 1900),
    ("carriers", "carriers", 1900),
    ("delay-drivers", "delay-drivers", 1900),
    ("methodology", "methodology", 1900),
    ("data-explorer", "explorer", 1700),
]
# Under WSLg, headless Chromium that inherits DISPLAY/WAYLAND_DISPLAY never returns a screenshot.
BROWSER_ENV = {k: v for k, v in os.environ.items() if k not in ("DISPLAY", "WAYLAND_DISPLAY")}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for_server(url: str, timeout: float = 60) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{url}/_stcore/health", timeout=2) as resp:
                if resp.status == 200:
                    return
        except OSError:
            time.sleep(0.5)
    raise RuntimeError(f"Streamlit did not become healthy at {url}")


def settle(page: Page, quiet_ms: int = 1500, timeout_s: float = 90) -> None:
    """Wait until the script run finishes and the page stops changing."""
    page.wait_for_selector("h1", timeout=timeout_s * 1000)
    deadline = time.time() + timeout_s
    quiet_since = None
    while time.time() < deadline:
        running = page.locator('[data-testid="stStatusWidget"]').count() > 0
        skeleton = page.locator('[data-testid="stSkeleton"]').count() > 0
        if running or skeleton:
            quiet_since = None
        elif quiet_since is None:
            quiet_since = time.time()
        elif (time.time() - quiet_since) * 1000 >= quiet_ms:
            return
        page.wait_for_timeout(250)
    raise RuntimeError("page did not settle")


def go(page: Page, url_path: str) -> None:
    if url_path:
        page.locator(f'[data-testid="stSidebarNav"] a[href$="/{url_path}"]').first.click()
    else:
        page.locator('[data-testid="stSidebarNav"] a').first.click()
    page.wait_for_timeout(500)
    settle(page)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=OUT, help="output directory (default: docs/screenshots)")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    port = free_port()
    url = f"http://127.0.0.1:{port}"
    server = subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", "app.py", "--server.port", str(port),
         "--server.headless", "true", "--browser.gatherUsageStats", "false"],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        wait_for_server(url)
        with sync_playwright() as p:
            browser = p.chromium.launch(env=BROWSER_ENV)
            page = browser.new_page(viewport={"width": WIDTH, "height": 1900})
            page.goto(url)
            settle(page)
            for name, url_path, height in PAGES:
                page.set_viewport_size({"width": WIDTH, "height": height})
                go(page, url_path)
                if name == "data-explorer":
                    # Inspect the headline KPI query, the one every other page builds on.
                    page.get_by_label("Inspect query").click()
                    page.get_by_role("option").filter(has_text="Headline KPIs (current)").first.click()
                    page.wait_for_timeout(500)
                    settle(page)
                if name == "network-map":
                    page.wait_for_timeout(3000)  # map tiles and deck.gl layers draw after the run ends
                page.screenshot(path=str(args.out / f"{name}.png"))
                print(f"wrote {name}.png")
                if name == "overview":
                    # The metric help popover on the On-Time Arrival card.
                    page.set_viewport_size({"width": WIDTH, "height": 1700})
                    page.locator('[data-testid="stPopoverButton"]').locator("visible=true").nth(1).click()  # 2nd KPI card
                    page.wait_for_timeout(1200)
                    page.screenshot(path=str(args.out / "metric-help.png"))
                    print("wrote metric-help.png")
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(500)
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=10)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
