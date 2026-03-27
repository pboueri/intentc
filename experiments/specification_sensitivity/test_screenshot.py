#!/usr/bin/env python3
"""Debug script to test screenshot quality for different serving approaches."""

import asyncio
import http.server
import threading
import socket
from pathlib import Path
from playwright.async_api import async_playwright

RUNS_DIR = Path(__file__).parent / "output" / "runs"

def get_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]

def start_server(directory, port):
    class QuietHandler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(directory), **kwargs)
        def log_message(self, format, *args):
            pass
    server = http.server.HTTPServer(("127.0.0.1", port), QuietHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server

async def test_one(run_dir, label, serve_from):
    """Test screenshotting from a specific directory."""
    port = get_free_port()
    server = start_server(serve_from, port)
    url = f"http://127.0.0.1:{port}/index.html"

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 800, "height": 600})

        # Collect console messages
        console_msgs = []
        page.on("console", lambda msg: console_msgs.append(f"  [{msg.type}] {msg.text}"))
        page.on("pageerror", lambda err: console_msgs.append(f"  [PAGE ERROR] {err}"))

        try:
            response = await page.goto(url, wait_until="networkidle", timeout=15000)
            print(f"\n=== {label} ===")
            print(f"  URL: {url}")
            print(f"  Serve from: {serve_from}")
            print(f"  HTTP status: {response.status if response else 'None'}")
        except Exception as e:
            print(f"\n=== {label} ===")
            print(f"  LOAD ERROR: {e}")

        await page.wait_for_timeout(3000)

        # Take screenshot
        out = Path(__file__).parent / f"test_{label}.png"
        await page.screenshot(path=str(out))
        print(f"  Screenshot: {out}")

        if console_msgs:
            print(f"  Console ({len(console_msgs)} messages):")
            for msg in console_msgs[:10]:
                print(msg)
        else:
            print("  Console: (no messages)")

        # Check if canvas has content
        has_content = await page.evaluate("""() => {
            const canvas = document.querySelector('canvas');
            if (!canvas) return 'no canvas found';
            const ctx = canvas.getContext('2d');
            const data = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
            let nonZero = 0;
            for (let i = 0; i < data.length; i += 4) {
                if (data[i] > 0 || data[i+1] > 0 || data[i+2] > 0) nonZero++;
            }
            return `canvas ${canvas.width}x${canvas.height}, ${nonZero} non-black pixels`;
        }""")
        print(f"  Canvas: {has_content}")

        await browser.close()
    server.shutdown()

async def main():
    # Test spec 1 (direct HTML)
    spec1_runs = sorted(list((RUNS_DIR / "specificity_1").iterdir()))
    if spec1_runs:
        run = spec1_runs[0]
        await test_one(run, "spec1_src", run / "src")

    # Test spec 4 from dist/
    spec4_runs = sorted(list((RUNS_DIR / "specificity_4").iterdir()))
    if spec4_runs:
        run = spec4_runs[0]
        await test_one(run, "spec4_dist", run / "src" / "dist")
        # Also try serving from src/ root
        await test_one(run, "spec4_src", run / "src")

    # Test spec 2 from dist/
    spec2_runs = sorted(list((RUNS_DIR / "specificity_2").iterdir()))
    if spec2_runs:
        run = spec2_runs[0]
        await test_one(run, "spec2_dist", run / "src" / "dist")
        await test_one(run, "spec2_src", run / "src")

asyncio.run(main())
