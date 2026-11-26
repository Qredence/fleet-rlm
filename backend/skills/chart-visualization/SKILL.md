---
name: chart-visualization
description: Create a chart, plot, or publication figure from data when the deliverable is a visual artifact.
companion-skills: data-analysis
---

Choose a visual encoding that answers the question. Compare the plotted series and group order against the source calculation. Label units and distinguish series where needed.

Render with Matplotlib’s Agg backend and save the requested format under `/workspace/artifacts` (PNG by default). Reopen a PNG with Pillow and check dimensions and nonblank content. For SVG, parse the output and verify expected labels. Use visual review when available to check clipping or overlap. Return the verified artifact path.
