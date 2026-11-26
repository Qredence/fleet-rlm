---
name: chart-visualization
description: Create a chart, plot, or publication figure from data when the deliverable is a visual artifact.
---

Choose a visual encoding that answers the question. Compare the actual bar heights, point coordinates or line series with the source calculation in plotting order. A correct label can sit on the wrong bar. Label units and distinguish series where needed.

Render with Matplotlib’s Agg backend and save the requested format under `/workspace/artifacts` (PNG by default). Reopen a PNG with Pillow and check dimensions and nonblank content. For SVG, parse the output and verify expected labels. Use visual review when available to check clipping or overlap. Return the verified artifact path.
