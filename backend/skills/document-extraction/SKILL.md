---
name: document-extraction
description: Extract passages, numerical values, illustrations, or tables with page or section citations from PDFs and text documents.
---

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

Use these computed variables when serializing the requested result. Avoid retyping a page number from memory. Apply the same binding to table cells and visually read values, recording the cell or visible passage on its physical page.

Keep the list of pages needing OCR separate from citations. When only that list is requested, identify pages with unresolved image text and stop. PDF pages can share resources; a referenced image is located on a page by its drawing operations or by rendering the page. Use visual inspection when the answer depends on visual evidence. Keep raw bytes out of tool output.

For plain text, cite the section or line that contains the supporting passage.
