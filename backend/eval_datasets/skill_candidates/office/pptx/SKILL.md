---
name: pptx
description: Create a PowerPoint briefing deck from findings or update a supplied PPTX while preserving other slides and their layout.
---

Map each requested message to a slide. For an edit, open the original with python-pptx, identify the target slide and shape, and change only the requested content. Preserve unrelated slides, notes and layouts.

For a briefing, give each slide a takeaway, the supporting figure and its source. Use the requested slide order. Save the editable deck under /workspace/artifacts and reopen it to verify titles, figures and order.

Convert a temporary copy to PDF with headless LibreOffice and render every slide. Inspect the render for overflow, missing elements and illegible labels. Correct layout problems before returning the PPTX path.
