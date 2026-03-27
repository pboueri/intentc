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

WIDTH = 1280
HEIGHT = 960
WAIT_MS = 5000  # Wait for game to render


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
    """Create a 5x5 grid image with clear axis labels, padding, and borders."""
    from PIL import ImageDraw, ImageFont

    cols = 5  # runs
    rows = 5  # specificity levels

    # Shrink screenshots and add generous padding
    thumb_w = WIDTH * 3 // 4  # 960
    thumb_h = HEIGHT * 3 // 4  # 720
    pad = 20  # padding around each thumbnail
    cell_w = thumb_w + pad * 2
    cell_h = thumb_h + pad * 2
    margin_left = 260  # space for row labels
    margin_top = 100   # space for column labels + title
    margin_bottom = 40
    bg_color = (240, 240, 245)       # light grey background
    cell_bg = (200, 200, 210)        # slightly darker cell background
    border_color = (100, 100, 120)   # visible border
    text_color = (30, 30, 40)

    total_w = margin_left + cols * cell_w + margin_bottom
    total_h = margin_top + rows * cell_h + margin_bottom

    grid = Image.new("RGB", (total_w, total_h), color=bg_color)
    draw = ImageDraw.Draw(grid)

    try:
        title_font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 48)
        label_font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 36)
        axis_font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 42)
    except Exception:
        title_font = ImageFont.load_default()
        label_font = title_font
        axis_font = title_font

    # Title
    draw.text((total_w // 2, 20), "Zoo Builder — Visual Variance by Specificity Level",
              fill=text_color, font=title_font, anchor="mt")

    # Column headers (Run 1..5)
    for col in range(cols):
        x = margin_left + col * cell_w + cell_w // 2
        draw.text((x, margin_top - 20), f"Run {col + 1}",
                  fill=text_color, font=label_font, anchor="mb")

    # Row labels and screenshots
    for spec_level in range(1, 6):
        row = spec_level - 1
        y_offset = margin_top + row * cell_h

        # Row label (rotated would be ideal, but just use horizontal)
        draw.text((margin_left - 20, y_offset + cell_h // 2),
                  f"Specificity {spec_level}",
                  fill=text_color, font=label_font, anchor="rm")

        for run_num in range(1, 6):
            col = run_num - 1
            x_offset = margin_left + col * cell_w

            # Cell background (creates visible separation)
            draw.rectangle(
                [x_offset, y_offset, x_offset + cell_w - 1, y_offset + cell_h - 1],
                fill=cell_bg
            )

            # Border
            draw.rectangle(
                [x_offset, y_offset, x_offset + cell_w - 1, y_offset + cell_h - 1],
                outline=border_color, width=2
            )

            path = SCREENSHOT_DIR / f"spec{spec_level}_run{run_num}.png"
            thumb_x = x_offset + pad
            thumb_y = y_offset + pad

            if path.exists():
                img = Image.open(path).resize((thumb_w, thumb_h), Image.LANCZOS)
                grid.paste(img, (thumb_x, thumb_y))
                # Inner border around the screenshot itself
                draw.rectangle(
                    [thumb_x - 1, thumb_y - 1, thumb_x + thumb_w, thumb_y + thumb_h],
                    outline=(60, 60, 70), width=1
                )
            else:
                # Placeholder
                draw.rectangle(
                    [thumb_x, thumb_y, thumb_x + thumb_w - 1, thumb_y + thumb_h - 1],
                    fill=(160, 160, 170)
                )
                draw.text((thumb_x + thumb_w // 2, thumb_y + thumb_h // 2),
                          "No Data", fill=(100, 100, 110), font=label_font, anchor="mm")

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
