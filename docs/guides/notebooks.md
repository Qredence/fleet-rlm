# Jupyter Notebook Workflows

While the CLI is great for automation, Jupyter Notebooks provide an interactive environment for experimenting with RLM.

## Setting Up

Ensure you have configured your environment as described in [Getting Started](../getting-started.md). Then launch Jupyter Lab:

```bash
uv run jupyter lab notebooks/rlm-dspy-modal.ipynb
```

## Notebook Structure

The provided notebook `notebooks/rlm-dspy-modal.ipynb` is a comprehensive guide covering:

1.  **Setup**: Imports and environment configuration.
2.  **Modal Sandbox Driver**: Defining the `driver.py` code that runs remotely.
3.  **ModalInterpreter**: The Python class bridging DSPy and Modal.
4.  **Demos**:
    - Basic Fibonacci generation.
    - Long Document Analysis.
    - Parallel Processing.
    - Stateful Multi-Step Logic.
    - Persistent Storage with Volumes.
    - Custom Tools.

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
