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

Create a `.env` file in the repository root by copying the provided template:

```bash
# Copy the template
cp .env.example .env

# Edit with your API keys and model configuration
vim .env
```

The template (`.env.example`) documents all available variables:

**Required variables:**

- `DSPY_LM_MODEL` - LLM model identifier (e.g., `openai/gpt-4`, `google/gemini-3-flash-preview`)
- `DSPY_LLM_API_KEY` - API key for your LLM provider

**Optional variables:**

- `DSPY_LM_API_BASE` - Custom API endpoint (if using proxy or self-hosted)
- `DSPY_LM_MAX_TOKENS` - Maximum response length (default: 8192)

⚠️ **Security**: The `.env` file is gitignored and will never be committed. Keep your API keys safe!

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

## Skills and Agents Installation

`fleet-rlm` includes custom Claude skills and agents optimized for RLM workflows. Install them to your user directory for use across all projects:

**Prerequisites**: Complete Modal setup (above) before installing skills/agents. They require Modal credentials to function.

```bash
# List available skills and agents
uv run fleet-rlm init --list

# Install all skills and agents to ~/.claude/
uv run fleet-rlm init

# Or install to a custom directory
uv run fleet-rlm init --target ~/.config/claude

# Force overwrite existing files
uv run fleet-rlm init --force
```

This command copies:

- **10 Skills**: RLM workflow patterns, debugging, testing, execution
- **4 Agents**: Specialized agents for RLM orchestration and execution

Once installed, these skills and agents are available in all your Claude-enabled projects.

**Important**: Skills and agents are workflow definitions only—they don't include credentials. Each user must configure their own Modal authentication using the steps above.

## Verifying Setup

Run the following command to check if your secrets are correctly accessible:

```bash
uv run fleet-rlm check-secret
```

If everything is compliant, you are ready to run your first agent! check out the [Basic Usage Tutorial](tutorials/basic-usage.md).
