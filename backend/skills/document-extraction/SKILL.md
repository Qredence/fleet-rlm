---
name: document-extraction
description: Extract passages, figures, or tables with page or section citations from PDFs and text documents.
---

For PDFs use pypdf.PdfReader and record one-based page numbers while extracting. A page with no extracted text may be blank, image-based, or contain graphics. Inspect the page if possible. Identify content that still requires OCR or visual verification.

Anchor each reported value to its page and supporting text or visible table cell. Preserve row and column associations when reconstructing tables, and reconcile totals where available. For plain text, use section names or line references that the reader can locate.
