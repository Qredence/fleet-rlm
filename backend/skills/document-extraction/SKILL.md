---
name: document-extraction
description: Extract a passage, table or figure from a PDF or text document and cite its page or section. Read scanned text or identify pages requiring OCR.
---

For plain text, cite the section or line that contains the supporting passage. Preserve the exact source wording alongside its location.

When reporting a quantity, its source is the page where the quantity can be read in context. A “figure” can mean a numerical value. A request for a count and the source page for that figure asks where the count was stated. A request for the location of an illustration asks where the illustration was drawn. Keep those questions distinct.

For PDFs, keep the one-based physical page number attached to its evidence from extraction through submission. For a textual quantity, use a pattern that matches the requested value together with its unit or surrounding label. For example, given a compiled pattern with a named group `value`:

```python
pages = [(i, page.extract_text() or "") for i, page in enumerate(reader.pages, 1)]
matches = [
    (i, match.group("value"), match.group())
    for i, text in pages
    for match in pattern.finditer(text)
]
```

Choose the tuple that answers the question, checking the label, date, entity and unit when relevant. If several matches remain plausible, preserve that uncertainty. Construct the final quantity and citation directly from the same tuple. In the submission cell, verify that the supporting passage occurs on the cited page:

```python
page_number, value, passage = chosen_match
assert passage in (reader.pages[page_number - 1].extract_text() or "")
```

Use these computed variables when serializing the requested result. For a JSON answer, pass `json.dumps(result)` to `SUBMIT` so keys and strings are quoted correctly. Apply the same binding to table cells and visually read values, recording the cell or visible passage on its physical page.

## Scanned pages

Inspect any page without usable extracted text. A scanned passage needs OCR; an illustration without text does not. If transcription is requested and Tesseract is available, render the physical page with pdftoppm and run tesseract on that image. Inspect uncertain characters against the image. Keep the list of pages needing OCR separate from citations. When only that list is requested, identify pages with unresolved image text and stop. PDF pages can share resources; a referenced image is located on a page by its drawing operations or by rendering the page. Use visual inspection when the answer depends on visual evidence. Keep raw bytes out of tool output.

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

When the answer includes an OCR page list, make a table with one row per physical page before submitting. Record the embedded text first. For each page with no usable embedded text, render it and run OCR, even after finding the requested quantity on another page. Keep a page in the OCR list only when the rendered page contains readable words, labels or numbers absent from its text layer. Exclude illustrations even if OCR produces stray characters. Derive the quantity and its citation from the same source passage, and derive the OCR list from this separate page table.

For short identifiers, render the same physical page at 200 and 300 DPI. Crop each image to the line containing the label, leaving a small white margin, and run `tesseract stdin stdout --psm 7` on both crops. Compare the transcriptions before submitting. If they disagree, inspect the original glyphs or try a cleaner crop. Report ambiguous characters as uncertain if the evidence remains inconclusive. Carry the verified string and page together into the answer.
