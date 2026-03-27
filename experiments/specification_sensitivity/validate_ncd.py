#!/usr/bin/env python3
"""Validate NCD implementation and investigate the counterintuitive results."""

import gzip
import re
import statistics
from pathlib import Path

RUNS_DIR = Path(__file__).parent / "output" / "runs"


def ncd(x: str, y: str) -> float:
    """Normalized Compression Distance."""
    xb = x.encode("utf-8")
    yb = y.encode("utf-8")
    xyb = xb + yb

    cx = len(gzip.compress(xb, compresslevel=9))
    cy = len(gzip.compress(yb, compresslevel=9))
    cxy = len(gzip.compress(xyb, compresslevel=9))

    return (cxy - min(cx, cy)) / max(cx, cy)


def collect_source_text(src_dir: Path) -> str:
    texts = []
    for ext in ("*.js", "*.html", "*.css"):
        for f in sorted(src_dir.rglob(ext)):
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
    text = re.sub(r"//.*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    text = re.sub(r'"[^"]*"', '""', text)
    text = re.sub(r"'[^']*'", "''", text)
    text = re.sub(r"`[^`]*`", "``", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


# === Test 1: Sanity check NCD with known inputs ===
print("=== NCD Sanity Checks ===")
identical = "hello world " * 100
different = "abcdefghijk " * 100
random1 = "the quick brown fox jumps over the lazy dog " * 50
random2 = "pack my box with five dozen liquor jugs now " * 50

print(f"  identical vs identical:   {ncd(identical, identical):.4f}  (should be ~0)")
print(f"  identical vs different:   {ncd(identical, different):.4f}  (should be ~1)")
print(f"  random1 vs random2:       {ncd(random1, random2):.4f}")
print(f"  random1 vs random1+noise: {ncd(random1, random1 + ' extra stuff here'):.4f}")
print()

# === Test 2: Check raw sizes ===
print("=== Raw Text Sizes ===")
for spec_level in range(1, 5):
    spec_dir = RUNS_DIR / f"specificity_{spec_level}"
    runs = sorted([d for d in spec_dir.iterdir() if d.is_dir()])
    sizes = []
    for run in runs:
        raw = collect_source_text(run / "src")
        anon = anonymize_source(raw)
        sizes.append((len(raw), len(anon)))

    raw_sizes = [s[0] for s in sizes]
    anon_sizes = [s[1] for s in sizes]
    print(f"  Spec {spec_level}:")
    print(f"    Raw chars:  {[s for s in raw_sizes]}")
    print(f"    Anon chars: {[s for s in anon_sizes]}")
    print(f"    Compressed sizes (anon): ", end="")
    for run in runs:
        anon = anonymize_source(collect_source_text(run / "src"))
        c = len(gzip.compress(anon.encode("utf-8"), compresslevel=9))
        print(f"{c} ", end="")
    print()

# === Test 3: NCD matrix for each spec level ===
print("\n=== Pairwise NCD Matrices ===")
for spec_level in range(1, 5):
    spec_dir = RUNS_DIR / f"specificity_{spec_level}"
    runs = sorted([d for d in spec_dir.iterdir() if d.is_dir()])

    texts = []
    for run in runs:
        anon = anonymize_source(collect_source_text(run / "src"))
        texts.append(anon)

    print(f"\n  Spec {spec_level} (text lengths: {[len(t) for t in texts]}):")
    ncds = []
    for i in range(len(texts)):
        for j in range(i + 1, len(texts)):
            d = ncd(texts[i], texts[j])
            ncds.append(d)
            print(f"    run{i+1} vs run{j+1}: {d:.4f}")
    print(f"    Mean: {statistics.mean(ncds):.4f}, Std: {statistics.stdev(ncds):.4f}")

# === Test 4: NCD WITHOUT anonymization ===
print("\n=== NCD WITHOUT Anonymization ===")
for spec_level in range(1, 5):
    spec_dir = RUNS_DIR / f"specificity_{spec_level}"
    runs = sorted([d for d in spec_dir.iterdir() if d.is_dir()])

    texts = [collect_source_text(run / "src") for run in runs]

    ncds = []
    for i in range(len(texts)):
        for j in range(i + 1, len(texts)):
            d = ncd(texts[i], texts[j])
            ncds.append(d)
    print(f"  Spec {spec_level}: Mean NCD = {statistics.mean(ncds):.4f} (raw, no anonymization)")

# === Test 5: gzip window size issue ===
print("\n=== Gzip Window Size Analysis ===")
print("  gzip uses a 32KB sliding window. If texts > 32KB, distant matches are missed.")
for spec_level in range(1, 5):
    spec_dir = RUNS_DIR / f"specificity_{spec_level}"
    runs = sorted([d for d in spec_dir.iterdir() if d.is_dir()])
    anon = anonymize_source(collect_source_text(runs[0] / "src"))
    size_kb = len(anon.encode("utf-8")) / 1024
    print(f"  Spec {spec_level}: ~{size_kb:.1f} KB per run (gzip window = 32KB)")
