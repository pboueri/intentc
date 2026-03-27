#!/usr/bin/env python3
"""Take screenshots of all experiment runs using Playwright and create a 5x5 grid."""

import asyncio
import os
import sys
import http.server
import threading
import socket
from pathlib import Path
from PIL import Image

# Try playwright import
try:
    from playwright.async_api import async_playwright
except ImportError:
    print("Installing playwright...")
    os.system(f"{sys.executable} -m pip install playwright Pillow")
    os.system(f"{sys.executable} -m playwright install chromium")
    from playwright.async_api import async_playwright


EXPERIMENT_DIR = Path(__file__).parent
RUNS_DIR = EXPERIMENT_DIR / "output" / "runs"
SCREENSHOT_DIR = EXPERIMENT_DIR / "output" / "screenshots"
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

GRID_OUTPUT = EXPERIMENT_DIR / "output" / "analysis" / "screenshot_grid.png"
GRID_OUTPUT.parent.mkdir(parents=True, exist_ok=True)

WIDTH = 800
HEIGHT = 600
WAIT_MS = 3000  # Wait for game to render


def find_servable_path(run_dir: Path) -> tuple[Path, bool]:
    """Find the best path to serve for a run. Returns (path, needs_server)."""
    src = run_dir / "src"
    dist = src / "dist"

    # If dist/ exists with index.html, serve from dist/
    if (dist / "index.html").exists():
        return dist, True

    # Otherwise serve the src/ directory directly
    if (src / "index.html").exists():
        return src, False

    return src, False


def start_server(directory: Path, port: int) -> http.server.HTTPServer:
    """Start a simple HTTP server in a thread."""
    handler = http.server.SimpleHTTPRequestHandler

    class QuietHandler(handler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(directory), **kwargs)

        def log_message(self, format, *args):
            pass  # Suppress logging

    server = http.server.HTTPServer(("127.0.0.1", port), QuietHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def get_free_port() -> int:
    """Get a free port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


async def take_screenshot(page, url: str, output_path: Path):
    """Navigate to URL and take a screenshot."""
    try:
        await page.goto(url, wait_until="networkidle", timeout=15000)
    except Exception:
        try:
            await page.goto(url, wait_until="load", timeout=10000)
        except Exception:
            pass

    # Wait for rendering
    await page.wait_for_timeout(WAIT_MS)
    await page.screenshot(path=str(output_path))


async def screenshot_all_runs():
    """Take screenshots of all runs."""
    runs_by_spec = {}
    for spec_level in range(1, 6):
        spec_dir = RUNS_DIR / f"specificity_{spec_level}"
        if not spec_dir.exists():
            print(f"WARNING: {spec_dir} does not exist, skipping")
            continue

        runs = sorted([d for d in spec_dir.iterdir() if d.is_dir()])
        runs_by_spec[spec_level] = runs

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        context = await browser.new_context(viewport={"width": WIDTH, "height": HEIGHT})
        page = await context.new_page()

        for spec_level, runs in sorted(runs_by_spec.items()):
            for i, run_dir in enumerate(runs):
                run_num = i + 1
                screenshot_path = SCREENSHOT_DIR / f"spec{spec_level}_run{run_num}.png"

                if screenshot_path.exists():
                    print(f"  Already exists: {screenshot_path.name}")
                    continue

                serve_path, needs_server = find_servable_path(run_dir)

                if not (serve_path / "index.html").exists():
                    print(f"  SKIP {run_dir.name}: no index.html found")
                    continue

                # Always use a server to avoid file:// CORS issues
                port = get_free_port()
                server = start_server(serve_path, port)
                url = f"http://127.0.0.1:{port}/index.html"

                print(f"  Screenshotting spec{spec_level}_run{run_num} ({run_dir.name})...")
                try:
                    await take_screenshot(page, url, screenshot_path)
                    print(f"    -> {screenshot_path.name}")
                except Exception as e:
                    print(f"    ERROR: {e}")
                finally:
                    server.shutdown()

        await browser.close()


def create_grid():
    """Create a 5x5 grid image from individual screenshots."""
    cols = 5  # runs
    rows = 5  # specificity levels

    # Determine cell size from first available screenshot
    cell_w, cell_h = WIDTH, HEIGHT

    grid = Image.new("RGB", (cols * cell_w, rows * cell_h), color=(30, 30, 30))

    for spec_level in range(1, 6):
        for run_num in range(1, 6):
            path = SCREENSHOT_DIR / f"spec{spec_level}_run{run_num}.png"
            row = spec_level - 1
            col = run_num - 1

            if path.exists():
                img = Image.open(path).resize((cell_w, cell_h))
                grid.paste(img, (col * cell_w, row * cell_h))
            else:
                # Draw placeholder
                placeholder = Image.new("RGB", (cell_w, cell_h), color=(60, 60, 60))
                grid.paste(placeholder, (col * cell_w, row * cell_h))

    # Add labels
    try:
        from PIL import ImageDraw, ImageFont

        draw = ImageDraw.Draw(grid)
        try:
            font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 24)
            small_font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 18)
        except Exception:
            font = ImageFont.load_default()
            small_font = font

        # Row labels (specificity levels)
        for row in range(rows):
            label = f"Spec {row + 1}"
            draw.text((10, row * cell_h + 10), label, fill="white", font=font,
                       stroke_width=2, stroke_fill="black")

        # Column labels (run numbers)
        for col in range(cols):
            label = f"Run {col + 1}"
            draw.text((col * cell_w + cell_w - 100, 10), label, fill="white", font=small_font,
                       stroke_width=2, stroke_fill="black")
    except Exception as e:
        print(f"Warning: Could not add labels: {e}")

    grid.save(str(GRID_OUTPUT), quality=95)
    print(f"\nGrid saved to: {GRID_OUTPUT}")
    print(f"Grid size: {grid.size[0]}x{grid.size[1]}")


async def main():
    print("=== Taking screenshots of all runs ===")
    await screenshot_all_runs()

    print("\n=== Creating 5x5 grid ===")
    create_grid()


if __name__ == "__main__":
    asyncio.run(main())
