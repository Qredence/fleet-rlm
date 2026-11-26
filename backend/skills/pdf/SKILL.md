---
name: pdf
description: Assemble an appendix, merge PDF exhibits, split a packet, reorder or rotate selected pages, and deliver a verified PDF with its source page mapping.
---

Map each requested exhibit to its input filename and one-based physical page. Record that mapping before transforming anything. When an exhibit is identified by content, check its passage or image on the source page.

When the identifying phrase is on a scan, render that physical page with Poppler and read it with Tesseract. For example, pass the PNG bytes from `pdftoppm -f N -l N -r 200 -singlefile -png input.pdf` to `tesseract stdin stdout`. Confirm the matching page and move on to assembly.

Read the inputs with pypdf. Add the selected pages to a fresh PdfWriter in the requested order. Rotate the output page relative to its existing rotation. Save the new file under /workspace/artifacts.

Reopen the output. Compare each output page's text or image content, orientation, and position with the mapped source. A page count alone cannot detect swapped exhibits. Once the output matches, finish the turn with `SUBMIT(answer, artifact_paths)`. Include the verified PDF in the artifact list, even when the user asks for a JSON answer containing only source pages.
