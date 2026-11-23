---
name: document-extraction
description: Use when extracting page-cited text, figures, or tables from PDFs, or synthesizing facts across long text documents.
---

Inspect PDFs with `pypdf.PdfReader` and keep 1-based page numbers. Identify pages with no extractable text as needing OCR; leave their figures unverified. Verify each extracted figure against an exact quote on a text-bearing page. Reconcile reconstructed tables against the source. For text files, keep section or line references. Use bounded `llm_query_batched` for semantic chunks and handle failed items.
