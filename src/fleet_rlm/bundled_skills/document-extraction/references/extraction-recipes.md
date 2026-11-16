# Document extraction recipes

## 1. Inspect before extracting

Always call `doc.inspect_document(path)` first:
- Large PDFs (50+ pages): extract in small page ranges (`pages="1-5"`, `pages=[6, 7]`) or iterate page batches and filter in Python variables before asking `llm_query_batched`.
- `.xlsx` workbooks: check `sheet_names`, then inspect row headers and compute aggregations in Python (avoid printing all 500 rows to stdout).

## 2. Tables vs text in PDFs

- When `pdfplumber` is installed in the sandbox, `extract_pdf(..., extract_tables=True)` populates `page["tables"]` alongside `page["text"]`.
- When only `pypdf` is installed, `extract_pdf` falls back to `pypdf` text extraction (`tables=[]`) and sets `result["extractor"] = "pypdf"`.

## 3. When to switch to multimodal inspection

If `extract_pdf` returns empty or garbled text on a page, the PDF is likely scanned or image-based:
1. Render or extract the page image to a bounded file under `/tmp` or `artifacts/`.
2. Check dimensions with `doc.inspect_image(img_path)` and resize if either dimension exceeds ~1600 px (`resize_max_dim=1600`).
3. Load with `img = workspace.load_image("artifacts/page_1.png")` (note `load_image` reads from managed workspace paths and enforces byte bounds) and pass `img` to `llm_query` or `dspy.Predict`.

## 4. Handling Office formats without heavy optional packages

`extract_office` prefers `python-docx`, `python-pptx`, and `openpyxl`, falls back to `markitdown` if installed, and includes a standard-library OOXML ZIP parser fallback for basic text and cell extraction.
