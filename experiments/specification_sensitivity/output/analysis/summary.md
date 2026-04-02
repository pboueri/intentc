# Specification Sensitivity Experiment Results

**Date:** 1774907476.9276264

## Summary Table

| Specificity | Runs | Mean LOC | LOC CV | Mean NCD | NCD Std |
|---|---|---|---|---|---|
| 1 | 5 | 688 | 0.183 | 0.794 | 0.054 |
| 2 | 5 | 778 | 0.103 | 0.552 | 0.217 |
| 3 | 5 | 1153 | 0.105 | 0.648 | 0.104 |
| 4 | 5 | 2603 | 0.105 | 0.525 | 0.050 |
| 5 | 5 | 6748 | 0.035 | 0.462 | 0.101 |

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
- `specificity_vs_code_spec_ratio.png` — Code-to-spec line ratio
- `loc_vs_spec_lines.png` — Code output vs spec line count + ratio
- `files_vs_spec_files.png` — Output files vs spec file count + ratio
- `screenshot_grid.png` — Visual comparison (5x5 grid)