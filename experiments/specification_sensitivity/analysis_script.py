#!/usr/bin/env python3
"""
Analysis script for the specification sensitivity experiment.

Measures code variance across specificity levels using:
1. Normalized Compression Distance (NCD) between pairs of runs
2. Raw lines of code / file count metrics
3. Structural similarity (shared filenames, shared function names)

Outputs plots to experiments/specification_sensitivity/output/analysis/
"""

import gzip
import json
import os
import re
import statistics
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import numpy as np


EXPERIMENT_DIR = Path(__file__).parent
RUNS_DIR = EXPERIMENT_DIR / "output" / "runs"
ANALYSIS_DIR = EXPERIMENT_DIR / "output" / "analysis"
ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def collect_source_text(src_dir: Path) -> str:
    """Concatenate all source files into a single string for comparison."""
    texts = []
    for ext in ("*.js", "*.html", "*.css", "*.json"):
        for f in sorted(src_dir.rglob(ext)):
            # Skip node_modules, dist, .claude
            rel = f.relative_to(src_dir)
            if any(part.startswith(".") or part == "node_modules" or part == "dist"
                   for part in rel.parts):
                continue
            try:
                texts.append(f.read_text(errors="replace"))
            except Exception:
                pass
    return "\n".join(texts)


def anonymize_source(text: str) -> str:
    """Remove variable names and normalize whitespace for NCD."""
    # Remove comments
    text = re.sub(r"//.*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    # Remove string literals
    text = re.sub(r'"[^"]*"', '""', text)
    text = re.sub(r"'[^']*'", "''", text)
    text = re.sub(r"`[^`]*`", "``", text)
    # Normalize whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def ncd(x: str, y: str) -> float:
    """Normalized Compression Distance between two strings."""
    xb = x.encode("utf-8")
    yb = y.encode("utf-8")
    xyb = xb + yb

    cx = len(gzip.compress(xb, compresslevel=9))
    cy = len(gzip.compress(yb, compresslevel=9))
    cxy = len(gzip.compress(xyb, compresslevel=9))

    return (cxy - min(cx, cy)) / max(cx, cy)


def count_lines(src_dir: Path) -> int:
    """Count lines of source code (excluding node_modules, dist, etc.)."""
    total = 0
    for ext in ("*.js", "*.html", "*.css"):
        for f in sorted(src_dir.rglob(ext)):
            rel = f.relative_to(src_dir)
            if any(part.startswith(".") or part == "node_modules" or part == "dist"
                   for part in rel.parts):
                continue
            try:
                total += len(f.read_text(errors="replace").splitlines())
            except Exception:
                pass
    return total


def count_files(src_dir: Path) -> int:
    """Count source files."""
    total = 0
    for ext in ("*.js", "*.html", "*.css"):
        for f in sorted(src_dir.rglob(ext)):
            rel = f.relative_to(src_dir)
            if any(part.startswith(".") or part == "node_modules" or part == "dist"
                   for part in rel.parts):
                continue
            total += 1
    return total


def get_filenames(src_dir: Path) -> set:
    """Get set of source file names (without path)."""
    names = set()
    for ext in ("*.js", "*.html", "*.css"):
        for f in sorted(src_dir.rglob(ext)):
            rel = f.relative_to(src_dir)
            if any(part.startswith(".") or part == "node_modules" or part == "dist"
                   for part in rel.parts):
                continue
            names.add(f.name)
    return names


def extract_functions(src_dir: Path) -> set:
    """Extract function names from JS files."""
    funcs = set()
    patterns = [
        r"function\s+(\w+)",
        r"(?:const|let|var)\s+(\w+)\s*=\s*(?:function|\()",
        r"(\w+)\s*\([^)]*\)\s*\{",
    ]
    for f in sorted(src_dir.rglob("*.js")):
        rel = f.relative_to(src_dir)
        if any(part.startswith(".") or part == "node_modules" or part == "dist"
               for part in rel.parts):
            continue
        try:
            text = f.read_text(errors="replace")
            for pattern in patterns:
                funcs.update(re.findall(pattern, text))
        except Exception:
            pass
    # Filter out common JS keywords
    keywords = {"if", "for", "while", "switch", "catch", "return", "new", "get", "set"}
    return funcs - keywords


def jaccard(a: set, b: set) -> float:
    """Jaccard similarity between two sets."""
    if not a and not b:
        return 1.0
    intersection = len(a & b)
    union = len(a | b)
    return intersection / union if union else 0.0


# ---------------------------------------------------------------------------
# Data collection
# ---------------------------------------------------------------------------

def load_all_runs():
    """Load data for all runs grouped by specificity level."""
    data = {}
    for spec_level in range(1, 6):
        spec_dir = RUNS_DIR / f"specificity_{spec_level}"
        if not spec_dir.exists():
            print(f"WARNING: {spec_dir} does not exist")
            continue

        runs = sorted([d for d in spec_dir.iterdir() if d.is_dir()])
        spec_data = []

        for run_dir in runs:
            src_dir = run_dir / "src"
            if not src_dir.exists():
                continue

            raw_text = collect_source_text(src_dir)
            anon_text = anonymize_source(raw_text)
            lines = count_lines(src_dir)
            files = count_files(src_dir)
            filenames = get_filenames(src_dir)
            functions = extract_functions(src_dir)

            spec_data.append({
                "run_dir": run_dir,
                "raw_text": raw_text,
                "anon_text": anon_text,
                "lines": lines,
                "files": files,
                "filenames": filenames,
                "functions": functions,
            })

        data[spec_level] = spec_data
        print(f"Specificity {spec_level}: {len(spec_data)} runs loaded")

    return data


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def compute_pairwise_ncd(runs: list) -> list[float]:
    """Compute NCD between all pairs of runs."""
    ncds = []
    for i in range(len(runs)):
        for j in range(i + 1, len(runs)):
            d = ncd(runs[i]["anon_text"], runs[j]["anon_text"])
            ncds.append(d)
    return ncds


def compute_pairwise_jaccard_files(runs: list) -> list[float]:
    """Compute Jaccard distance for filenames between all pairs."""
    dists = []
    for i in range(len(runs)):
        for j in range(i + 1, len(runs)):
            sim = jaccard(runs[i]["filenames"], runs[j]["filenames"])
            dists.append(1.0 - sim)  # Convert similarity to distance
    return dists


def compute_pairwise_jaccard_functions(runs: list) -> list[float]:
    """Compute Jaccard distance for function names between all pairs."""
    dists = []
    for i in range(len(runs)):
        for j in range(i + 1, len(runs)):
            sim = jaccard(runs[i]["functions"], runs[j]["functions"])
            dists.append(1.0 - sim)
    return dists


# ---------------------------------------------------------------------------
# Plotting
# ---------------------------------------------------------------------------

def plot_ncd(data: dict):
    """Plot NCD vs specificity level."""
    levels = []
    means = []
    stds = []
    all_ncds = {}

    for spec_level in sorted(data.keys()):
        runs = data[spec_level]
        if len(runs) < 2:
            continue
        ncds = compute_pairwise_ncd(runs)
        levels.append(spec_level)
        means.append(statistics.mean(ncds))
        stds.append(statistics.stdev(ncds) if len(ncds) > 1 else 0)
        all_ncds[spec_level] = ncds

    fig, ax = plt.subplots(figsize=(10, 6))
    ax.errorbar(levels, means, yerr=stds, fmt="o-", capsize=5, capthick=2,
                markersize=8, linewidth=2, color="#2196F3")

    # Scatter individual points
    for spec_level, ncds in all_ncds.items():
        ax.scatter([spec_level] * len(ncds), ncds, alpha=0.3, color="#2196F3", s=30)

    ax.set_xlabel("Specificity Level", fontsize=14)
    ax.set_ylabel("Normalized Compression Distance (mean pairwise)", fontsize=14)
    ax.set_title("Code Variance vs. Specification Specificity\n(Lower NCD = More Similar Code)", fontsize=16)
    ax.set_xticks(levels)
    ax.set_xticklabels([f"Level {l}" for l in levels])
    ax.grid(True, alpha=0.3)
    ax.set_ylim(0, 1)

    fig.tight_layout()
    fig.savefig(str(ANALYSIS_DIR / "specificity_vs_ncd.png"), dpi=150)
    plt.close(fig)
    print("Saved: specificity_vs_ncd.png")

    return {l: {"mean": m, "std": s} for l, m, s in zip(levels, means, stds)}


def plot_lines_of_code(data: dict):
    """Plot lines of code distribution vs specificity level."""
    levels = []
    means = []
    stds = []
    all_lines = {}

    for spec_level in sorted(data.keys()):
        runs = data[spec_level]
        if not runs:
            continue
        lines = [r["lines"] for r in runs]
        levels.append(spec_level)
        means.append(statistics.mean(lines))
        stds.append(statistics.stdev(lines) if len(lines) > 1 else 0)
        all_lines[spec_level] = lines

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 6))

    # Mean LOC with error bars
    ax1.errorbar(levels, means, yerr=stds, fmt="s-", capsize=5, capthick=2,
                 markersize=8, linewidth=2, color="#4CAF50")
    for spec_level, lines in all_lines.items():
        ax1.scatter([spec_level] * len(lines), lines, alpha=0.3, color="#4CAF50", s=30)

    ax1.set_xlabel("Specificity Level", fontsize=14)
    ax1.set_ylabel("Lines of Code", fontsize=14)
    ax1.set_title("Lines of Code vs. Specificity", fontsize=16)
    ax1.set_xticks(levels)
    ax1.grid(True, alpha=0.3)

    # Coefficient of variation (CV) — normalized measure of spread
    cvs = []
    for spec_level in levels:
        lines = all_lines[spec_level]
        m = statistics.mean(lines)
        s = statistics.stdev(lines) if len(lines) > 1 else 0
        cvs.append(s / m if m > 0 else 0)

    ax2.bar(levels, cvs, color="#FF9800", alpha=0.8, width=0.6)
    ax2.set_xlabel("Specificity Level", fontsize=14)
    ax2.set_ylabel("Coefficient of Variation (σ/μ)", fontsize=14)
    ax2.set_title("LOC Variance (Normalized) vs. Specificity", fontsize=16)
    ax2.set_xticks(levels)
    ax2.grid(True, alpha=0.3, axis="y")

    fig.tight_layout()
    fig.savefig(str(ANALYSIS_DIR / "specificity_vs_raw_lines_of_code.png"), dpi=150)
    plt.close(fig)
    print("Saved: specificity_vs_raw_lines_of_code.png")

    return {l: {"mean": m, "std": s, "cv": c}
            for l, m, s, c in zip(levels, means, stds, cvs)}


def plot_structural_similarity(data: dict):
    """Plot structural similarity metrics."""
    levels = []
    file_jaccards = {}
    func_jaccards = {}

    for spec_level in sorted(data.keys()):
        runs = data[spec_level]
        if len(runs) < 2:
            continue
        levels.append(spec_level)
        file_jaccards[spec_level] = compute_pairwise_jaccard_files(runs)
        func_jaccards[spec_level] = compute_pairwise_jaccard_functions(runs)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 6))

    # File name Jaccard distance
    file_means = [statistics.mean(file_jaccards[l]) for l in levels]
    file_stds = [statistics.stdev(file_jaccards[l]) if len(file_jaccards[l]) > 1 else 0
                 for l in levels]
    ax1.errorbar(levels, file_means, yerr=file_stds, fmt="D-", capsize=5, capthick=2,
                 markersize=8, linewidth=2, color="#9C27B0")
    for l in levels:
        ax1.scatter([l] * len(file_jaccards[l]), file_jaccards[l],
                    alpha=0.3, color="#9C27B0", s=30)
    ax1.set_xlabel("Specificity Level", fontsize=14)
    ax1.set_ylabel("Jaccard Distance (1 - similarity)", fontsize=14)
    ax1.set_title("File Name Variance vs. Specificity\n(Lower = More Agreement on File Structure)",
                  fontsize=14)
    ax1.set_xticks(levels)
    ax1.set_ylim(0, 1)
    ax1.grid(True, alpha=0.3)

    # Function name Jaccard distance
    func_means = [statistics.mean(func_jaccards[l]) for l in levels]
    func_stds = [statistics.stdev(func_jaccards[l]) if len(func_jaccards[l]) > 1 else 0
                 for l in levels]
    ax2.errorbar(levels, func_means, yerr=func_stds, fmt="D-", capsize=5, capthick=2,
                 markersize=8, linewidth=2, color="#E91E63")
    for l in levels:
        ax2.scatter([l] * len(func_jaccards[l]), func_jaccards[l],
                    alpha=0.3, color="#E91E63", s=30)
    ax2.set_xlabel("Specificity Level", fontsize=14)
    ax2.set_ylabel("Jaccard Distance (1 - similarity)", fontsize=14)
    ax2.set_title("Function Name Variance vs. Specificity\n(Lower = More Agreement on API Surface)",
                  fontsize=14)
    ax2.set_xticks(levels)
    ax2.set_ylim(0, 1)
    ax2.grid(True, alpha=0.3)

    fig.tight_layout()
    fig.savefig(str(ANALYSIS_DIR / "specificity_vs_structural_similarity.png"), dpi=150)
    plt.close(fig)
    print("Saved: specificity_vs_structural_similarity.png")


def plot_file_count(data: dict):
    """Plot file count distribution."""
    levels = []
    all_counts = {}

    for spec_level in sorted(data.keys()):
        runs = data[spec_level]
        if not runs:
            continue
        counts = [r["files"] for r in runs]
        levels.append(spec_level)
        all_counts[spec_level] = counts

    fig, ax = plt.subplots(figsize=(10, 6))

    positions = levels
    bp = ax.boxplot([all_counts[l] for l in levels], positions=positions,
                    widths=0.5, patch_artist=True)

    colors = ["#BBDEFB", "#C8E6C9", "#FFF9C4", "#FFCCBC", "#E1BEE7"]
    for patch, color in zip(bp["boxes"], colors):
        patch.set_facecolor(color)

    ax.set_xlabel("Specificity Level", fontsize=14)
    ax.set_ylabel("Number of Source Files", fontsize=14)
    ax.set_title("Source File Count vs. Specificity", fontsize=16)
    ax.set_xticks(levels)
    ax.grid(True, alpha=0.3, axis="y")

    fig.tight_layout()
    fig.savefig(str(ANALYSIS_DIR / "specificity_vs_file_count.png"), dpi=150)
    plt.close(fig)
    print("Saved: specificity_vs_file_count.png")


def generate_summary(data: dict, ncd_results: dict, loc_results: dict):
    """Generate a summary markdown file."""
    lines = ["# Specification Sensitivity Experiment Results\n"]
    lines.append(f"**Date:** {ANALYSIS_DIR.stat().st_mtime if ANALYSIS_DIR.exists() else 'N/A'}\n")
    lines.append("## Summary Table\n")
    lines.append("| Specificity | Runs | Mean LOC | LOC CV | Mean NCD | NCD Std |")
    lines.append("|---|---|---|---|---|---|")

    for spec_level in sorted(data.keys()):
        runs = data[spec_level]
        n = len(runs)
        loc = loc_results.get(spec_level, {})
        ncd_r = ncd_results.get(spec_level, {})
        lines.append(
            f"| {spec_level} | {n} | {loc.get('mean', 0):.0f} | "
            f"{loc.get('cv', 0):.3f} | {ncd_r.get('mean', 0):.3f} | "
            f"{ncd_r.get('std', 0):.3f} |"
        )

    lines.append("\n## Interpretation\n")
    lines.append("- **NCD (Normalized Compression Distance):** Lower values indicate ")
    lines.append("more similar code between runs. If specificity reduces variance, NCD ")
    lines.append("should decrease as specificity increases.\n")
    lines.append("- **LOC CV (Coefficient of Variation):** Measures how spread out the ")
    lines.append("lines of code are. Lower CV means more consistent output size.\n")
    lines.append("- **Structural Similarity:** Jaccard distance on file/function names ")
    lines.append("measures agreement on project structure and API surface.\n")

    lines.append("\n## Plots\n")
    lines.append("- `specificity_vs_ncd.png` — Core variance metric")
    lines.append("- `specificity_vs_raw_lines_of_code.png` — Size variance")
    lines.append("- `specificity_vs_structural_similarity.png` — Structure agreement")
    lines.append("- `specificity_vs_file_count.png` — File count distribution")
    lines.append("- `screenshot_grid.png` — Visual comparison (5x5 grid)")

    summary_path = ANALYSIS_DIR / "summary.md"
    summary_path.write_text("\n".join(lines))
    print(f"Saved: summary.md")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=== Loading experiment data ===")
    data = load_all_runs()

    if not data:
        print("ERROR: No data found")
        return

    print(f"\n=== Computing NCD (this may take a moment) ===")
    ncd_results = plot_ncd(data)

    print(f"\n=== Computing LOC metrics ===")
    loc_results = plot_lines_of_code(data)

    print(f"\n=== Computing structural similarity ===")
    plot_structural_similarity(data)

    print(f"\n=== Computing file counts ===")
    plot_file_count(data)

    print(f"\n=== Generating summary ===")
    generate_summary(data, ncd_results, loc_results)

    print(f"\n=== Quick Results ===")
    for spec_level in sorted(data.keys()):
        runs = data[spec_level]
        if not runs:
            print(f"  Specificity {spec_level}: no runs")
            continue
        lines = [r["lines"] for r in runs]
        ncd_r = ncd_results.get(spec_level, {})
        print(f"  Specificity {spec_level}: "
              f"{len(runs)} runs, "
              f"LOC {statistics.mean(lines):.0f}±{statistics.stdev(lines) if len(lines) > 1 else 0:.0f}, "
              f"NCD {ncd_r.get('mean', 0):.3f}±{ncd_r.get('std', 0):.3f}")

    print(f"\nAll outputs in: {ANALYSIS_DIR}")


if __name__ == "__main__":
    main()
