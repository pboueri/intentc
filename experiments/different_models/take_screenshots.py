"""Re-run just the screenshot portion of analysis."""

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

EXPERIMENT_DIR = Path(__file__).parent
RUNS_DIR = EXPERIMENT_DIR / "outputs" / "runs"
ANALYSIS_DIR = EXPERIMENT_DIR / "outputs" / "analysis"
SCREENSHOTS_DIR = ANALYSIS_DIR / "screenshots"

MODEL_ORDER = [
    "haiku",
    "sonnet_low",
    "sonnet_medium",
    "sonnet_high",
    "opus_low",
    "opus_medium",
    "opus_high",
]


def find_run_dirs() -> dict[str, Path]:
    runs = {}
    if not RUNS_DIR.exists():
        return runs
    for d in sorted(RUNS_DIR.iterdir()):
        if not d.is_dir():
            continue
        name = d.name
        parts = name.rsplit("_", 1)
        if len(parts) == 2 and len(parts[1]) > 10:
            label = parts[0]
        else:
            label = name
        runs[label] = d
    return runs


def take_screenshot(label: str, src_dir: Path, screenshot_path: Path) -> bool:
    """Take a screenshot of one app. Returns True on success."""
    pkg_json = src_dir / "package.json"
    if not pkg_json.exists():
        return False

    pkg = json.loads(pkg_json.read_text())
    scripts = pkg.get("scripts", {})
    dev_cmd = scripts.get("dev", "")

    port = 4567
    server_proc = None
    original_server = None

    try:
        # Check if there's a pre-built dist directory we can serve directly
        dist_dir = src_dir / "dist"
        if dist_dir.exists() and (dist_dir / "index.html").exists():
            server_proc = subprocess.Popen(
                [sys.executable, "-m", "http.server", str(port), "--directory", str(dist_dir)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            url = f"http://localhost:{port}"
            time.sleep(2)
        elif "vite" in dev_cmd:
            server_proc = subprocess.Popen(
                [str(src_dir / "node_modules" / ".bin" / "vite"), "--port", str(port), "--strictPort"],
                cwd=str(src_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            url = f"http://localhost:{port}"
            time.sleep(5)
        elif "server.js" in dev_cmd or "node" in dev_cmd:
            server_js = src_dir / "server.js"
            if server_js.exists():
                original_server = server_js.read_text()
                patched = re.sub(
                    r'(const|let|var)\s+PORT\s*=\s*\d+',
                    f'const PORT = {port}',
                    original_server,
                )
                server_js.write_text(patched)

            server_proc = subprocess.Popen(
                ["node", "server.js"],
                cwd=str(src_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            url = f"http://localhost:{port}"
            time.sleep(2)
        else:
            index_path = src_dir / "index.html"
            if index_path.exists():
                url = f"file://{index_path}"
            else:
                return False

        # Use playwright via Python API directly
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 720})
            try:
                page.goto(url, timeout=15000)
                page.wait_for_timeout(3000)
                page.screenshot(path=str(screenshot_path))
                print(f"    Screenshot saved: {screenshot_path.name}")
                return True
            except Exception as e:
                print(f"    Playwright error: {e}")
                return False
            finally:
                browser.close()

    except Exception as e:
        print(f"    Error: {e}")
        return False
    finally:
        if server_proc:
            server_proc.terminate()
            try:
                server_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server_proc.kill()
        if original_server:
            server_js = src_dir / "server.js"
            server_js.write_text(original_server)


def generate_screenshot_grid(screenshot_results: dict[str, str | None]):
    html_path = ANALYSIS_DIR / "screenshot_grid.html"

    rows = []
    for label in MODEL_ORDER:
        path = screenshot_results.get(label)
        if path and Path(path).exists():
            rel = os.path.relpath(path, ANALYSIS_DIR)
            img = f'<img src="{rel}" style="width:100%;border:1px solid #ccc;">'
        else:
            img = '<div style="width:100%;height:300px;background:#fee;display:flex;align-items:center;justify-content:center;font-size:72px;color:red;border:1px solid #ccc;">X</div>'

        parts = label.split("_")
        model = parts[0].capitalize()
        effort = parts[1].capitalize() if len(parts) > 1 else "Default"
        rows.append(f"""
        <div style="text-align:center;">
            <h3>{model} - {effort}</h3>
            {img}
        </div>""")

    html = f"""<!DOCTYPE html>
<html>
<head><title>Different Models - Screenshot Grid</title></head>
<body style="font-family:sans-serif;padding:20px;">
<h1>Different Models Experiment - Visual Comparison</h1>
<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:20px;max-width:1600px;">
{"".join(rows)}
</div>
</body>
</html>"""

    html_path.write_text(html)
    print(f"  Grid saved to {html_path}")


def main():
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    run_dirs = find_run_dirs()
    print(f"Found runs: {list(run_dirs.keys())}")

    screenshot_results: dict[str, str | None] = {}

    for label in MODEL_ORDER:
        if label not in run_dirs:
            print(f"  {label}: NO OUTPUT (build failed)")
            screenshot_results[label] = None
            continue

        src_dir = run_dirs[label] / "src"
        if not src_dir.exists():
            print(f"  {label}: no src dir")
            screenshot_results[label] = None
            continue

        screenshot_path = SCREENSHOTS_DIR / f"{label}.png"
        print(f"  Screenshotting {label}...")
        success = take_screenshot(label, src_dir, screenshot_path)
        screenshot_results[label] = str(screenshot_path) if success else None

    print("\nGenerating grid...")
    generate_screenshot_grid(screenshot_results)

    # Update summary.md screenshots section
    summary_path = ANALYSIS_DIR / "summary.md"
    if summary_path.exists():
        content = summary_path.read_text()
        new_lines = []
        for label in MODEL_ORDER:
            has = screenshot_results.get(label) is not None
            status = "captured" if has else "FAILED (X)"
            new_lines.append(f"- {label}: {status}")
        # Replace the screenshot status lines
        import re
        pattern = r'(## Screenshots\n\nSee \[screenshot_grid\.html\].*?\n\n)(?:- .*\n?)+'
        replacement = r'\1' + '\n'.join(new_lines) + '\n'
        updated = re.sub(pattern, replacement, content)
        summary_path.write_text(updated)
        print("  Updated summary.md")

    print("\nDone!")


if __name__ == "__main__":
    main()
