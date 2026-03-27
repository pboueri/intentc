# Specification Sensitivity Experiment Results

**Date:** 1774578625.2415962

## Summary Table

| Specificity | Runs | Mean LOC | LOC CV | Mean NCD | NCD Std |
|---|---|---|---|---|---|
| 1 | 5 | 688 | 0.183 | 0.730 | 0.019 |
| 2 | 5 | 778 | 0.103 | 0.915 | 0.022 |
| 3 | 5 | 1153 | 0.105 | 0.964 | 0.008 |
| 4 | 5 | 2603 | 0.105 | 0.988 | 0.001 |
| 5 | 0 | 0 | 0.000 | 0.000 | 0.000 |

## Interpretation

- **NCD (Normalized Compression Distance):** Lower values indicate 
more similar code between runs. If specificity reduces variance, NCD 
should decrease as specificity increases.

- **LOC CV (Coefficient of Variation):** Measures how spread out the 
lines of code are. Lower CV means more consistent output size.

- **Structural Similarity:** Jaccard distance on file/function names 
measures agreement on project structure and API surface.


## Plots

- `specificity_vs_ncd.png` — Core variance metric
- `specificity_vs_raw_lines_of_code.png` — Size variance
- `specificity_vs_structural_similarity.png` — Structure agreement
- `specificity_vs_file_count.png` — File count distribution
- `screenshot_grid.png` — Visual comparison (5x5 grid)