---
name: document-extraction
description: Extract passages, figures, or tables with page or section citations from PDFs and text documents.
---

For PDFs, bind each passage to its physical page as you read it. Use `enumerate(reader.pages, start=1)` with `pypdf.PdfReader` and store each page number alongside its extracted text. For each figure, locate the supporting sentence or table cell in one of those records and cite that record’s number. Build the answer from the matched record. Before submitting, reopen the proposed source page and verify the reported value in its supporting sentence or cell. If the check fails, search the page records again.

Track pages requiring OCR separately. A page without extracted text may be blank or contain an image or vector graphics. Inspect its contents before classifying it. PDF pages can share a resource dictionary. To locate an image on a page, check the page’s drawing operations (including invoked form objects) or render that page. An image listed in shared resources does not establish where it was drawn. An adjacent image page is evidence for a figure only if you can read the figure there. Read PDFs as binary files through the parser and print short text passages for verification. Keep raw PDF bytes and decoded binary streams out of tool output.

Anchor each reported value to its page and supporting text or visible table cell. Preserve row and column associations when reconstructing tables, and reconcile totals where available. For plain text, use section names or line references that the reader can locate.
