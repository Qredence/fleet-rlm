---
name: document-extraction
description: Extract text, tables, and structured facts from PDFs and long documents using pypdf and batched sub-LM queries.
---

Read PDFs and documents from `/workspace/files` using `pypdf.PdfReader`. First inspect page counts and sample headers without printing full documents to stdout. Slice pages or sections in Python and use `llm_query_batched(prompts)` for concurrent map-reduce extraction or summarization across chunks. Cross-check extracted figures or citations against exact page numbers in code before calling `SUBMIT`.
