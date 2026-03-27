"""
Analysis script for the different_models experiment.

1. Take screenshots of each application with playwright
2. Generate LOC summary
3. Quality evaluation with Opus 4.6
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

EXPERIMENT_DIR = Path(__file__).parent
RUNS_DIR = EXPERIMENT_DIR / "outputs" / "runs"
ANALYSIS_DIR = EXPERIMENT_DIR / "outputs" / "analysis"
SCREENSHOTS_DIR = ANALYSIS_DIR / "screenshots"

# Ordered for display
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
    """Map model_effort labels to their run directories."""
    runs = {}
    if not RUNS_DIR.exists():
        return runs
    for d in sorted(RUNS_DIR.iterdir()):
        if not d.is_dir():
            continue
        name = d.name
        # Strip timestamp suffix: e.g. sonnet_low_20260326-215420 -> sonnet_low
        parts = name.rsplit("_", 1)
        if len(parts) == 2 and len(parts[1]) > 10:
            label = parts[0]
        else:
            label = name
        runs[label] = d
    return runs


def count_loc(run_dir: Path) -> dict:
    """Count lines of code in the src/ output directory."""
    src_dir = run_dir / "src"
    if not src_dir.exists():
        return {"total": 0, "files": 0, "by_ext": {}}

    total = 0
    file_count = 0
    by_ext: dict[str, int] = {}

    for root, _, files in os.walk(src_dir):
        for f in files:
            fp = Path(root) / f
            # Skip node_modules, dist, package-lock
            rel = fp.relative_to(src_dir)
            parts = rel.parts
            if any(p in ("node_modules", "dist", ".claude", ".intentc") for p in parts):
                continue
            if f == "package-lock.json":
                continue
            ext = fp.suffix or "(no ext)"
            try:
                lines = len(fp.read_text(encoding="utf-8", errors="ignore").splitlines())
            except Exception:
                continue
            total += lines
            file_count += 1
            by_ext[ext] = by_ext.get(ext, 0) + lines

    return {"total": total, "files": file_count, "by_ext": by_ext}


def take_screenshots(run_dirs: dict[str, Path]) -> dict[str, str | None]:
    """Launch each app, take a screenshot with playwright, return paths."""
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    results = {}

    for label in MODEL_ORDER:
        if label not in run_dirs:
            results[label] = None
            continue

        run_dir = run_dirs[label]
        src_dir = run_dir / "src"
        screenshot_path = SCREENSHOTS_DIR / f"{label}.png"

        if not src_dir.exists():
            results[label] = None
            continue

        print(f"  Screenshotting {label}...")

        # Determine how to start the server
        pkg_json = src_dir / "package.json"
        if not pkg_json.exists():
            results[label] = None
            continue

        pkg = json.loads(pkg_json.read_text())
        scripts = pkg.get("scripts", {})
        dev_cmd = scripts.get("dev", "")

        # Use a consistent port, run one at a time
        port = 4567

        # Start server
        server_proc = None
        try:
            if "vite" in dev_cmd:
                server_proc = subprocess.Popen(
                    ["npx", "vite", "--port", str(port), "--strictPort"],
                    cwd=str(src_dir),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                url = f"http://localhost:{port}"
                time.sleep(4)  # Wait for vite to start
            elif "server.js" in dev_cmd or "node" in dev_cmd:
                # Patch server.js to use our port
                server_js = src_dir / "server.js"
                if server_js.exists():
                    original = server_js.read_text()
                    import re
                    patched = re.sub(
                        r'const\s+PORT\s*=\s*\d+',
                        f'const PORT = {port}',
                        original,
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

                # Restore original server.js
                if server_js.exists():
                    server_js.write_text(original)
            else:
                # Try opening index.html directly
                index_path = src_dir / "index.html"
                if index_path.exists():
                    url = f"file://{index_path}"
                else:
                    results[label] = None
                    continue

            # Take screenshot with playwright
            screenshot_script = f"""
import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={{"width": 1280, "height": 720}})
        try:
            await page.goto("{url}", timeout=10000)
            await page.wait_for_timeout(3000)  # Let the game render
            await page.screenshot(path="{screenshot_path}")
        except Exception as e:
            print(f"Screenshot failed: {{e}}")
            # Create a failure marker
            open("{screenshot_path}.failed", "w").write(str(e))
        finally:
            await browser.close()

asyncio.run(main())
"""
            result = subprocess.run(
                [sys.executable, "-c", screenshot_script],
                capture_output=True,
                text=True,
                timeout=30,
            )

            if screenshot_path.exists():
                results[label] = str(screenshot_path)
            else:
                results[label] = None
                print(f"    Failed: {result.stderr[:200] if result.stderr else 'unknown'}")

        except Exception as e:
            print(f"    Error for {label}: {e}")
            results[label] = None
        finally:
            if server_proc:
                server_proc.terminate()
                try:
                    server_proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    server_proc.kill()

    return results


def generate_screenshot_grid(screenshot_results: dict[str, str | None]):
    """Generate an HTML grid of screenshots."""
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
    return html_path


def evaluate_quality(run_dirs: dict[str, Path]) -> dict[str, dict]:
    """Use Claude Opus 4.6 to evaluate each output's quality."""
    results = {}

    for label in MODEL_ORDER:
        if label not in run_dirs:
            results[label] = {"score": "FAILED", "summary": "Build failed - no output produced"}
            continue

        run_dir = run_dirs[label]
        src_dir = run_dir / "src"

        if not src_dir.exists():
            results[label] = {"score": "FAILED", "summary": "No src directory produced"}
            continue

        print(f"  Evaluating {label}...")

        # Collect all source files
        source_contents = []
        for root, _, files in os.walk(src_dir):
            for f in sorted(files):
                fp = Path(root) / f
                rel = fp.relative_to(src_dir)
                parts = rel.parts
                if any(p in ("node_modules", "dist", ".claude", ".intentc") for p in parts):
                    continue
                if f == "package-lock.json":
                    continue
                try:
                    content = fp.read_text(encoding="utf-8", errors="ignore")
                    source_contents.append(f"=== {rel} ===\n{content}")
                except Exception:
                    continue

        all_source = "\n\n".join(source_contents)

        # Read the intent for context
        intent_file = run_dir / "intent" / "project.ic"
        intent = intent_file.read_text() if intent_file.exists() else "Zoo builder game"

        prompt = f"""You are evaluating the quality of a generated codebase for a zoo builder game.

The game specification:
{intent}

Here is the complete source code:

{all_source}

Evaluate this implementation on the following criteria:
1. Code correctness - are there obvious bugs or logic errors?
2. Completeness - does it implement the core features described in the spec?
3. Code quality - is the code well-organized, readable, and maintainable?
4. Game playability - would this produce a playable, functional game?

Provide:
1. A quality score: one of VERY_POOR, POOR, OK, GOOD, GREAT
2. A list of bugs or issues you can identify
3. A brief summary (2-3 sentences)

Respond in JSON format:
{{"score": "...", "bugs": ["bug1", "bug2"], "summary": "..."}}"""

        try:
            result = subprocess.run(
                [
                    "claude",
                    "-p", prompt,
                    "--model", "opus",
                    "--effort", "high",
                    "--output-format", "json",
                    "--dangerously-skip-permissions",
                ],
                capture_output=True,
                text=True,
                timeout=300,
            )

            if result.returncode == 0:
                try:
                    response = json.loads(result.stdout)
                    # The response from claude --output-format json has a "result" field
                    result_text = response.get("result", result.stdout)
                    # Try to parse the JSON from the result text
                    # Find JSON in the text
                    start = result_text.find("{")
                    end = result_text.rfind("}") + 1
                    if start >= 0 and end > start:
                        eval_data = json.loads(result_text[start:end])
                        results[label] = eval_data
                    else:
                        results[label] = {"score": "UNKNOWN", "summary": result_text[:500]}
                except json.JSONDecodeError:
                    results[label] = {"score": "UNKNOWN", "summary": result.stdout[:500]}
            else:
                results[label] = {"score": "ERROR", "summary": f"Claude returned exit code {result.returncode}: {result.stderr[:200]}"}

        except subprocess.TimeoutExpired:
            results[label] = {"score": "TIMEOUT", "summary": "Evaluation timed out"}
        except Exception as e:
            results[label] = {"score": "ERROR", "summary": str(e)}

    return results


def write_summary(run_dirs: dict, loc_data: dict, quality_data: dict, screenshot_results: dict):
    """Write a combined summary report."""
    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = ANALYSIS_DIR / "summary.md"

    lines = [
        "# Different Models Experiment - Summary",
        "",
        "## Build Results",
        "",
        "| Model | Effort | Build | LOC | Files | Quality Score |",
        "|-------|--------|-------|-----|-------|---------------|",
    ]

    for label in MODEL_ORDER:
        parts = label.split("_")
        model = parts[0]
        effort = parts[1] if len(parts) > 1 else "default"
        built = "PASS" if label in run_dirs and (run_dirs[label] / "src").exists() else "FAIL"
        loc = loc_data.get(label, {})
        total_loc = loc.get("total", 0)
        file_count = loc.get("files", 0)
        quality = quality_data.get(label, {}).get("score", "N/A")
        lines.append(f"| {model} | {effort} | {built} | {total_loc} | {file_count} | {quality} |")

    lines.extend([
        "",
        "## LOC Breakdown",
        "",
    ])

    for label in MODEL_ORDER:
        loc = loc_data.get(label, {})
        if loc.get("total", 0) == 0:
            continue
        lines.append(f"### {label}")
        lines.append(f"- Total: {loc['total']} lines across {loc['files']} files")
        if loc.get("by_ext"):
            for ext, count in sorted(loc["by_ext"].items(), key=lambda x: -x[1]):
                lines.append(f"  - {ext}: {count}")
        lines.append("")

    lines.extend([
        "## Quality Evaluations",
        "",
    ])

    for label in MODEL_ORDER:
        q = quality_data.get(label, {})
        lines.append(f"### {label}")
        lines.append(f"- **Score:** {q.get('score', 'N/A')}")
        lines.append(f"- **Summary:** {q.get('summary', 'N/A')}")
        bugs = q.get("bugs", [])
        if bugs:
            lines.append(f"- **Issues ({len(bugs)}):**")
            for bug in bugs:
                lines.append(f"  - {bug}")
        lines.append("")

    lines.extend([
        "## Screenshots",
        "",
        "See [screenshot_grid.html](screenshot_grid.html) for visual comparison.",
        "",
    ])

    for label in MODEL_ORDER:
        has_screenshot = screenshot_results.get(label) is not None
        status = "captured" if has_screenshot else "FAILED (X)"
        lines.append(f"- {label}: {status}")

    report_path.write_text("\n".join(lines))
    print(f"  Summary written to {report_path}")

    # Also save raw data as JSON
    raw_path = ANALYSIS_DIR / "raw_data.json"
    raw_data = {
        "loc": loc_data,
        "quality": quality_data,
        "screenshots": {k: v is not None for k, v in screenshot_results.items()},
    }
    raw_path.write_text(json.dumps(raw_data, indent=2))
    print(f"  Raw data written to {raw_path}")


def main():
    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)

    print("Finding run directories...")
    run_dirs = find_run_dirs()
    print(f"  Found: {list(run_dirs.keys())}")

    print("\nCounting lines of code...")
    loc_data = {}
    for label in MODEL_ORDER:
        if label in run_dirs:
            loc_data[label] = count_loc(run_dirs[label])
            print(f"  {label}: {loc_data[label]['total']} lines, {loc_data[label]['files']} files")
        else:
            loc_data[label] = {"total": 0, "files": 0, "by_ext": {}}
            print(f"  {label}: NO OUTPUT")

    print("\nTaking screenshots...")
    screenshot_results = take_screenshots(run_dirs)

    print("\nGenerating screenshot grid...")
    generate_screenshot_grid(screenshot_results)

    print("\nEvaluating quality with Opus 4.6...")
    quality_data = evaluate_quality(run_dirs)

    print("\nWriting summary report...")
    write_summary(run_dirs, loc_data, quality_data, screenshot_results)

    print("\nDone! Results in:", ANALYSIS_DIR)


if __name__ == "__main__":
    main()
