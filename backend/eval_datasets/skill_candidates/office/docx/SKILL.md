---
name: docx
description: Create an editable Word report or revise a supplied DOCX template while preserving its styles, sections, and tables.
---

Inspect the source and, for an edit, open the supplied template with python-docx. Identify the paragraph, table cell or run that owns the requested change. Edit the smallest applicable range; replacing a whole paragraph's text can discard run formatting.

For a new report use native heading styles and tables, lead with the finding and cite the source near each material figure. Save to /workspace/artifacts. Reopen the file and check the changed values and the surrounding structure against the original.

Convert a temporary copy to PDF using headless LibreOffice with an isolated user profile. Render and inspect each page for clipped text, split tables and unexpected blank pages. Return the DOCX path.
