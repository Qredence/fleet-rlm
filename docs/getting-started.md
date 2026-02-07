# Getting Started with fleet-rlm

This guide will walk you through installing `fleet-rlm` and setting up the necessary environment to run Recursive Language Models.

## Prerequisites

- **Python**: >= 3.10
- **Package Manager**: `uv` (recommended)
- **Cloud Provider**: A [Modal](https://modal.com/) account for sandbox execution.

## Installation

1.  **Clone the repository**:

    ```bash
    git clone https://github.com/qredence/fleet-rlm.git
    cd fleet-rlm
    ```

2.  **Install dependencies**:

    ```bash
    uv sync
    ```

    For development (includes test tools):

    ```bash
    uv sync --extra dev
    ```

## Environment Configuration

### 1. Local Environment Variables

Create a `.env` file in the repository root to configure the Planner LM (the model that writes the code).

```bash
# Required
DSPY_LM_MODEL=openai/gemini-3-flash-preview
DSPY_LLM_API_KEY=sk-...

# Optional
DSPY_LM_API_BASE=https://your-litellm-proxy.com
DSPY_LM_MAX_TOKENS=65536
```

### 2. Modal Setup

The actual code execution happens in a Modal Sandbox. You need to setup authentication and secrets for the cloud environment.

1.  **Authenticate with Modal**:

    ```bash
    uv run modal setup
    ```

2.  **Create a Modal Volume**:
    This volume is used for persistent storage (e.g., caching documents) across runs.

    ```bash
    uv run modal volume create rlm-volume-dspy
    ```

3.  **Create Modal Secrets**:
    These secrets are injected into the sandbox so the Planner inside the sandbox can make LLM calls.

    ```bash
    uv run modal secret create LITELLM \
      DSPY_LM_MODEL=... \
      DSPY_LM_API_BASE=... \
      DSPY_LLM_API_KEY=... \
      DSPY_LM_MAX_TOKENS=...
    ```

    _Ensure these values match your provider configuration._

## Verifying Setup

Run the following command to check if your secrets are correctly accessible:

```bash
uv run fleet-rlm check-secret
```

If everything is compliant, you are ready to run your first agent! check out the [Basic Usage Tutorial](tutorials/basic-usage.md).
