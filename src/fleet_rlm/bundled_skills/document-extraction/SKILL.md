---
name: document-extraction
description: Extract text, tables, and structure from workspace PDFs and Office documents, or inspect and resize workspace images. Use for document-specific parsing, image dimensions, and bounded image thumbnails.
---

# Document extraction

Use this Skill when working with binary or structured documents (`.pdf`, `.docx`, `.pptx`, `.xlsx`, `.html`, `.md`, `.txt`) or workspace images (`.png`, `.jpg`, `.jpeg`, `.gif`, `.webp`).

## Quick start (REPL)

Load the module once and inspect structure before reading full contents:

```python
doc = load_skill_module("document-extraction")

meta = doc.inspect_document("files/report.pdf")
print(meta)  # format, page_count / sheet_names / slide_count / image dimensions

# Extract specific PDF pages (1-based) or dispatch automatically via doc(path)
pdf = doc.extract_pdf("files/report.pdf", pages=[1, 2], extract_tables=True)
print(pdf["pages"][0]["text"][:1000])
```

## Programmatic extraction vs multimodal `workspace.load_image`

- **Use `doc_tools` (`extract_pdf`, `extract_office`, `inspect_document`)** for text-bearing PDFs, tables, multi-sheet `.xlsx` workbooks, `.docx` documents, `.pptx` decks, and exact code-computed counts or quotes.
- **Use `workspace.load_image("files/...")` + `llm_query` / `dspy.Predict`** when information lives visually in charts, diagrams, screenshots, UI captures, or scanned PDF pages (e.g., when `extract_pdf` returns empty/near-empty text).
- For large images, call `doc.inspect_image("files/scan.png", resize_max_dim=1600, output_path="artifacts/scan_resized.png")` first, then load the bounded image with `workspace.load_image("artifacts/scan_resized.png")`.

## Helpers and CLI

- `doc.inspect_document(path)` — detects format and returns structural metadata without dumping text.
- `doc.extract_pdf(path, *, pages=None, extract_tables=True, max_chars_per_page=10_000)`
- `doc.extract_office(path, *, max_rows_per_sheet=500)` — `.docx`, `.pptx`, `.xlsx`.
- `doc.inspect_image(path, *, resize_max_dim=None, output_path=None)`
- `doc(path, *, pages=None, extract_tables=True)` — dispatches by extension.

CLI usage via `run_skill_script`:

```python
run_skill_script("document-extraction", "scripts/doc_tools.py", ["inspect", "files/deck.pptx"])
```

PDF text extraction requires `pdfplumber` or `pypdf`; only `pdfplumber` extracts
PDF tables. These helpers do not perform OCR or render PDF pages. The stdlib
Office fallback extracts basic text and stored cell values, not formatting or
formula recalculation. Check the reported extractor and truncation before
claiming complete extraction.

Read `references/extraction-recipes.md` for page-chunking, table cleanup, and scanned-PDF handling patterns.

Image resizing encodes to a temporary sibling before replacing its destination.
On Volumes without rename support, it removes the previous destination only after
encoding succeeds, then copies the completed image. That fallback is not atomic;
a copy failure can leave the destination absent or incomplete.
