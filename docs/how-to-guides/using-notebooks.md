# Jupyter Notebook Workflows

While the CLI is great for automation, Jupyter Notebooks provide an interactive environment for experimenting with RLM.

## Setting Up

Ensure you have configured your environment as described in [Installation](installation.md). Then launch Jupyter Lab:

```bash
uv run jupyter lab notebooks/rlm-dspy-modal.ipynb
```

## Notebook Structure

The provided notebook `notebooks/rlm-dspy-modal.ipynb` is a comprehensive guide covering:

1.  **Setup**: Imports and environment configuration.
2.  **Modal Sandbox Driver**: Defining the `driver.py` code that runs remotely.
3.  **ModalInterpreter**: The Python class bridging DSPy and Modal (supports `with` context manager).
4.  **Demos**:
    - Basic Fibonacci generation.
    - Long Document Analysis (with sandbox helpers: `peek`, `grep`, `chunk_by_size`, `chunk_by_headers`).
    - Parallel Processing.
    - Stateful Multi-Step Logic (with `add_buffer` / `get_buffer`).
    - Persistent Storage with Volumes (with `save_to_volume` / `load_from_volume`).
    - Custom Tools.

## PDF Inputs

When a notebook flow calls the ReAct document tools, `load_document` and
`read_file_slice` can ingest PDF files directly via MarkItDown with a pypdf
fallback. If a PDF is scanned/image-only and no text can be extracted, the
tool returns guidance to run OCR first.

## Headless Execution

You can run the notebook as a script (headlessly) for testing or CI/CD purposes. This executes all cells in order.

```bash
uv run jupyter nbconvert \
  --to notebook \
  --execute \
  --inplace \
  --ExecutePreprocessor.timeout=3600 \
  notebooks/rlm-dspy-modal.ipynb
```
