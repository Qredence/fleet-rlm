# AGENTS.md - RLM with Modal Sandbox

## Overview

This directory contains the package, data, and support assets for a comprehensive Jupyter notebook demonstrating **DSPy's Recursive Language Model (RLM)** with **Modal** for secure, cloud-based code execution.

**File**: `../notebooks/rlm-dspy-modal.ipynb`
**Purpose**: Full showcase of RLM capabilities for long-context document analysis
**Runtime**: ~30 minutes for all cells
**Requirements**: Modal account, DSPy-compatible LLM

---

## Command Quick Reference

Run commands from repository root:

```bash
# from /Volumes/Samsung-SSD-T7/Workspaces/Github/qredence/agent-framework/v0.5/_WORLD/_RLM/fleet-rlm-dspy

# install/sync dependencies for notebook work
uv sync

# add or update key deps when needed
uv add "dspy==3.1.3" modal jupyter ipykernel

# authenticate Modal locally (one-time per machine/session)
uv run modal setup

# ensure required Modal resources exist
uv run modal volume create rlm-volume-dspy
uv run modal volume list

# run notebook interactively
uv run jupyter lab notebooks/rlm-dspy-modal.ipynb

# execute notebook headlessly (CI/smoke validation)
uv run jupyter nbconvert \
  --to notebook \
  --execute \
  --inplace \
  --ExecutePreprocessor.timeout=3600 \
  notebooks/rlm-dspy-modal.ipynb
```

---

## Python Package + CLI Workflows

Use the extracted package implementation for repeatable runs:

```bash
# from /Volumes/Samsung-SSD-T7/Workspaces/Github/qredence/agent-framework/v0.5/_WORLD/_RLM/fleet-rlm-dspy

# install package + dev deps
uv sync --extra dev

# inspect commands
uv run fleet-rlm --help

# secret checks
uv run fleet-rlm check-secret
uv run fleet-rlm check-secret-key --key DSPY_LLM_API_KEY

# run demos
uv run fleet-rlm run-basic --question "What are the first 12 Fibonacci numbers?"
uv run fleet-rlm run-architecture --docs-path rlm_content/dspy-knowledge/dspy-doc.txt --query "Extract all modules and optimizers"
uv run fleet-rlm run-api-endpoints --docs-path rlm_content/dspy-knowledge/dspy-doc.txt
uv run fleet-rlm run-error-patterns --docs-path rlm_content/dspy-knowledge/dspy-doc.txt
uv run fleet-rlm run-trajectory --docs-path rlm_content/dspy-knowledge/dspy-doc.txt --chars 3000
uv run fleet-rlm run-custom-tool --docs-path rlm_content/dspy-knowledge/dspy-doc.txt --chars 10000

# run tests
uv run pytest
```

---

## What is RLM?

Recursive Language Models (RLM) are an inference strategy where:

- LLMs treat long contexts as an **external environment** (not input)
- The model writes Python code to programmatically explore data
- Code executes in a sandboxed environment
- Only relevant snippets are sent to sub-LLMs for semantic analysis

**Reference**: "Recursive Language Models" (Zhang, Kraska, Khattab, 2025)

---

## Notebook Structure

### Setup (Cells 0-4)

| Cell | Content          | Purpose                         |
| ---- | ---------------- | ------------------------------- |
| 0    | Title & overview | Introduction                    |
| 1-2  | Dependencies     | Install dspy, modal via uv      |
| 3-4  | Configuration    | Load .env, configure planner LM |

### Infrastructure (Cells 5-12)

| Cell  | Content          | Purpose                             |
| ----- | ---------------- | ----------------------------------- |
| 5-8   | Modal secrets    | Sanity-check LITELLM secret         |
| 9-10  | Sandbox driver   | Python driver for Modal container   |
| 11-12 | ModalInterpreter | CodeInterpreter with volume support |

**Key Feature**: Volume `rlm-volume-dspy` mounted at `/data` for persistence

### RLM Demonstrations (Cells 13-26)

#### Cell 14: Basic Code Generation

- **Task**: Calculate Fibonacci sequence
- **Shows**: Iterative code writing, execution, SUBMIT

#### Cell 16: Long Document Analysis

- **Task**: Extract DSPy architecture from 83KB docs
- **Shows**: Code navigation → targeted llm_query() → structured output
- **Signature**: `ExtractArchitecture(docs, query) -> (modules, optimizers, principles)`

#### Cell 18: Volume Caching Demo

- **Task**: Cache documents to `/data`
- **Shows**: Persistent storage across sandbox restarts

#### Cell 20: Parallel Processing

- **Task**: Extract API endpoints using batched queries
- **Shows**: `llm_query_batched()` for speed

#### Cell 22: Multi-Step Reasoning

- **Task**: Find and categorize error patterns
- **Shows**: Stateful execution, variable persistence

#### Cell 24: Trajectory Inspection

- **Task**: Examine full execution history
- **Shows**: Debugging, observability, audit trail

#### Cell 26: Custom Tools

- **Task**: Regex pattern matching tool
- **Shows**: Extending sandbox capabilities

### Reference (Cells 27-29)

| Cell | Content                            |
| ---- | ---------------------------------- |
| 27   | RLM vs Direct LLM comparison table |
| 28   | Best practices guide               |
| 29   | Summary & next steps               |

---

## Prerequisites

### 1. Environment Variables

Create `.env` in project root:

```bash
DSPY_LM_MODEL=openai/gemini-3-flash-preview
DSPY_LM_API_BASE=https://your-litellm-proxy.com
DSPY_LLM_API_KEY=sk-...
DSPY_LM_MAX_TOKENS=65536
```

### 2. Modal Setup

```bash
# Authenticate
uv run modal setup

# Create volume (already done)
uv run modal volume create rlm-volume-dspy

# Create secret for LLM credentials
uv run modal secret create LITELLM \
  DSPY_LM_MODEL=... \
  DSPY_LM_API_BASE=... \
  DSPY_LLM_API_KEY=...
```

### 3. Long Context Document

The notebook uses `rlm_content/dspy-knowledge/dspy-doc.txt` (~83KB):

- Auto-loaded in Cell 16+
- Can be replaced with any large text file

---

## Running the Notebook

### With UV (Recommended)

```bash
cd /Volumes/Samsung-SSD-T7/Workspaces/Github/qredence/agent-framework/v0.5/_WORLD/_RLM/fleet-rlm-dspy/rlm_content
uv run jupyter lab /Volumes/Samsung-SSD-T7/Workspaces/Github/qredence/agent-framework/v0.5/_WORLD/_RLM/fleet-rlm-dspy/notebooks/rlm-dspy-modal.ipynb
```

### Execution Order

1. **Run Cells 0-12**: Setup infrastructure
2. **Run Cells 14-26**: RLM demonstrations
3. **Cells 27-29**: Reference material (optional)

---

## Key RLM Patterns Demonstrated

### Pattern 1: Navigate → Query → Synthesize

```python
# Cell 16: ExtractArchitecture
1. Code searches for "##" headers in docs
2. llm_query() extracts module names from relevant sections
3. SUBMIT(modules=list, optimizers=list, principles=str)
```

### Pattern 2: Parallel Chunk Processing

```python
# Cell 20: ExtractAPIEndpoints
1. Split docs into chunks by headers
2. llm_query_batched([chunk1, chunk2, ...])  # Parallel!
3. Aggregate results
```

### Pattern 3: Stateful Multi-Step

```python
# Cell 22: FindErrorPatterns
1. Search for error keywords
2. Save matches to variable (persists!)
3. Query LLM to categorize
4. Iterate with refined queries
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│ LOCAL (Jupyter)                                             │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │ Planner LM  │  │ RLM Module   │  │ ModalInterpreter │  │
│  │ (decides    │→ │ (builds      │→ │ (manages sandbox │  │
│  │  what code  │  │  signatures) │  │  lifecycle)      │  │
│  │  to write)  │  │              │  │                  │  │
│  └─────────────┘  └──────────────┘  └──────────────────┘  │
│           │                │                  │            │
│           │                │ JSON stdin       │ gRPC       │
│           │                ↓                  ↓            │
└───────────┼────────────────┼──────────────────┼────────────┘
            │                │                  │
            │                │                  ▼
            │                │     ┌──────────────────────┐
            │                │     │ MODAL CLOUD          │
            │                │     │  ┌────────────────┐  │
            │                └────→│  │ Sandbox        │  │
            │                      │  │ - Python 3.12  │  │
            │                      │  │ - Volume /data │  │
            │                      │  │ - Secrets      │  │
            │                      │  └────────────────┘  │
            │                      │           │          │
            │                      │           ▼          │
            │                      │  ┌────────────────┐  │
            │                      │  │ Driver Process │  │
            │                      │  │ - exec() code  │  │
            │                      │  │ - tool bridging│  │
            │                      │  └────────────────┘  │
            │                      └──────────────────────┘
            │                                │
            └────────────────────────────────┘
                        tool_call requests
                        (llm_query, etc.)
```

---

## Troubleshooting

### Issue: "Planner LM not configured"

**Fix**: Set environment variables in `.env` and restart kernel

### Issue: "Modal sandbox process exited unexpectedly"

**Fix**:

```bash
# Check Modal auth
uv run modal token set

# Check volume exists
uv run modal volume list
```

### Issue: "No module named 'modal'"

**Fix**:

```bash
uv sync  # or: uv add modal
```

### Issue: IndentationError in Cell 18

**Fix**: This was a previous bug. Current version should have proper:

```python
with open("rlm_content/dspy-knowledge/dspy-doc.txt", "r") as f:
    sample_text = f.read()
```

---

## Extending the Notebook

### Add New RLM Example

1. Define a `dspy.Signature` subclass
2. Create interpreter: `ModalInterpreter()`
3. Create RLM: `dspy.RLM(signature=..., interpreter=...)`
4. Call: `rlm(input_field=value)`
5. Always wrap in `try/finally` with `interpreter.shutdown()`

### Add Custom Tool

```python
def my_tool(data: str) -> dict:
    """Process data and return results."""
    import json
    return {"processed": len(data)}

rlm = dspy.RLM(
    signature=...,
    interpreter=interpreter,
    tools=[my_tool]  # Pass here
)
```

### Use Different Document

Replace `rlm_content/dspy-knowledge/dspy-doc.txt` with any large text:

```python
with open("your-document.md", "r") as f:
    long_context = f.read()

result = rlm(docs=long_context, query="...")
```

---

## Standard Workflows

### Workflow A: Fresh Machine Bootstrap

1. `cd /Volumes/Samsung-SSD-T7/Workspaces/Github/qredence/agent-framework/v0.5/_WORLD/_RLM/fleet-rlm-dspy`
2. `uv sync`
3. `uv run modal setup`
4. `uv run modal volume create rlm-volume-dspy` (safe to re-run; ignore "already exists")
5. `uv run modal secret create LITELLM ...` with current credentials
6. `uv run jupyter lab notebooks/rlm-dspy-modal.ipynb`

### Workflow B: Daily Development Run

1. `cd /Volumes/Samsung-SSD-T7/Workspaces/Github/qredence/agent-framework/v0.5/_WORLD/_RLM/fleet-rlm-dspy`
2. `uv sync`
3. `uv run modal volume list`
4. `uv run jupyter lab notebooks/rlm-dspy-modal.ipynb`
5. Execute notebook cells in documented order (0-12, then 14-26)

### Workflow C: Pre-Share/Pre-Commit Validation

1. `uv sync`
2. `uv run jupyter nbconvert --to notebook --execute --inplace --ExecutePreprocessor.timeout=3600 notebooks/rlm-dspy-modal.ipynb`
3. Re-open the notebook and confirm no execution errors in output cells

### Workflow D: Credential Rotation

1. Update local `.env` values (`DSPY_*`)
2. Recreate/update Modal secret:
   `uv run modal secret create LITELLM DSPY_LM_MODEL=... DSPY_LM_API_BASE=... DSPY_LLM_API_KEY=... DSPY_LM_MAX_TOKENS=...`
3. Re-run setup cells in the notebook (Cells 3-8)

---

## Repository Conventions

- Package/runtime management: use `uv` commands for dependency and execution tasks.
- Keep secrets out of notebook outputs and git-tracked files.
- Prefer validating changes with headless notebook execution before sharing.
- Keep this `AGENTS.md` updated whenever notebook workflow or command usage changes.

---

## Performance Notes

| Metric              | Typical Value                   |
| ------------------- | ------------------------------- |
| Sandbox startup     | ~2-5 seconds                    |
| Code execution      | ~100-500ms per iteration        |
| llm_query()         | Depends on LLM (500ms-5s)       |
| llm_query_batched() | Parallel, scales with workers   |
| Volume I/O          | ~10MB/s (Modal network storage) |

**Cost Optimization**:

- Increase `max_llm_calls` carefully (sub-LLM calls = main cost)
- Use `llm_query_batched()` for parallelizable work
- Cache results to `/data` for reuse

---

## References

- **RLM Paper**: [Recursive Language Models](https://arxiv.org/abs/2501.123)
- **DSPy Docs**: https://dspy-docs.vercel.app/
- **Modal Docs**: https://modal.com/docs
- **Notebook Path**: `notebooks/rlm-dspy-modal.ipynb`

---

## Changelog

| Date       | Change                                                       |
| ---------- | ------------------------------------------------------------ |
| 2026-02-06 | Initial notebook with 30 cells, volume support               |
| 2026-02-06 | Added comprehensive RLM demonstrations                       |
| 2026-02-06 | Integrated Modal volume `rlm-volume-dspy`                    |
| 2026-02-06 | Added command quick reference and standard workflows         |
| 2026-02-06 | Standardized commands on `uv` and removed `pip` run path     |
| 2026-02-06 | Added Python package + Typer CLI workflows and test commands |

---

_Last updated: 2026-02-06_
