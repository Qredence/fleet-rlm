# Jupyter Notebook Workflows

Jupyter notebooks are useful for interactive experiments with the Daytona-backed
runtime, DSPy signatures, and document-processing helpers.

## Setup

Start from the same environment described in
[Installation](installation.md), then launch Jupyter Lab:

```bash
uv run jupyter lab
```

Create a notebook in your preferred workspace and import the maintained
Daytona-backed interfaces directly from `fleet_rlm`.

## Typical Notebook Flow

1. Configure environment variables for Daytona and your planner LM.
2. Import `DaytonaInterpreter` or higher-level helpers such as
   `run_long_context(...)`.
3. Use the sandbox helpers (`peek`, `grep`, `chunk_by_size`,
   `chunk_by_headers`, volume helpers, workspace helpers) through normal runtime
   calls rather than copying driver code into the notebook.
4. Keep durable state under the Daytona-mounted roots:
   `memory/`, `artifacts/`, `buffers/`, and `meta/`.

## PDF Inputs

When a notebook flow calls the ReAct document tools, `load_document` and
`read_file_slice` can ingest PDF files directly via MarkItDown with a pypdf
fallback. If a PDF is scanned/image-only and no text can be extracted, the tool
returns guidance to run OCR first.

## Headless Execution

You can execute notebooks in CI or from the terminal with `nbconvert`:

```bash
uv run jupyter nbconvert \
  --to notebook \
  --execute \
  --inplace \
  --ExecutePreprocessor.timeout=3600 \
  <your-notebook>.ipynb
```

## Recommendation

For production or repeatable validation flows, prefer the maintained CLI and
server surfaces over notebook-only logic:

- `uv run fleet web`
- `uv run fleet-rlm serve-api --port 8000`
- `uv run fleet-rlm daytona-smoke --repo <url> [--ref <branch>]`
