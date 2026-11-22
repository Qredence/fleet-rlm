---
name: chart-visualization
description: Create publication-quality Matplotlib charts, inspect images with Pillow, and verify visual outputs with dspy.Image.
---

Use `matplotlib` (`Agg` backend) for labeled axes, units, titles, and tight layout (`dpi=150`). Save PNGs in `/workspace/artifacts` and close figures. Reopen each PNG with `PIL.Image.open()` and check dimensions and non-blank pixel variance. For visual review, load the local file with `dspy.Image.from_path(path)` and call `llm_query([prompt, image])` before `SUBMIT`.
