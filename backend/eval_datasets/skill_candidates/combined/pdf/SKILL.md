---
name: pdf
description: Read, cite, transcribe, assemble, split, reorder, or rotate PDF pages and exhibits. Use for questions about a PDF and for producing a transformed PDF.
---

# Reading or citing a PDF

For a cited value, keep the exact passage and its one-based physical page together through submission. For a scan or an OCR inventory, examine each page with no usable text layer separately. Render that physical page with pdftoppm and run tesseract on its pixels. Inspect ambiguous glyphs against that page. Record the page when the image contains a passage that requires OCR, even after successful transcription. An illustration or decoration with no writing contributes no entry. A shared PDF image resource alone cannot establish what the page shows.

For example, with a one-based page number and a PDF path:

```python
import subprocess

page = str(page_number)
image = subprocess.run(
    ["pdftoppm", "-f", page, "-l", page, "-r", "200", "-singlefile", "-png", str(source)],
    capture_output=True,
    check=True,
).stdout
text = (
    subprocess.run(
        ["tesseract", "stdin", "stdout"],
        input=image,
        capture_output=True,
        check=True,
    )
    .stdout.decode()
    .strip()
)
```

Use the rendered page and recognized passage to classify it. An empty text layer alone is insufficient. For short identifiers, compare uncertain punctuation and digits at a second resolution or against a tightly cropped image. Carry the verified string and page together into the answer.

# Assembling a PDF

Map each requested exhibit to its input filename and one-based physical page. Record that mapping before transforming anything. When an exhibit is identified by content, check its passage or image on the source page.

Read the inputs with pypdf. Add the selected pages to a fresh PdfWriter in the requested order. Rotate the output page relative to its existing rotation. Save the new file under /workspace/artifacts.

Reopen the output. Compare each output page's text or image content, orientation, and position with the mapped source. A page count alone cannot detect swapped exhibits. Return the artifact path and original physical pages when requested.
