---
name: chart-visualization
description: Use when the task requests a chart, plot, or figure from data. Render and verify Matplotlib artifacts in the requested format.
---

Use Matplotlib with Agg. Verify plotted values against source data and label axes, units, and legends. Save the requested format in `/workspace/artifacts` (PNG by default). Reopen PNGs with `PIL.Image.open()` and check dimensions and nonblank content. Parse SVGs and check labels. Use `llm_query([prompt, dspy.Image.from_path(path)])` for visual review when supported; rely on local checks if unavailable. Return artifact paths.
