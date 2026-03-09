# Quick Start Guide

Get Fleet-RLM running in minutes. This guide covers installation, basic configuration, and launching your first chat session.

## Prerequisites

Before you begin, ensure you have:

- **Python 3.10 or later** — Fleet-RLM requires Python 3.10+
- **uv package manager** — Recommended for installation. [Install uv](https://docs.astral.sh/uv/getting-started/installation/)
- **Modal account** — Required for sandbox execution. [Sign up for Modal](https://modal.com/)

## Installation

### Option 1: Install with uv (Recommended)

Install Fleet-RLM globally using uv:

```bash
uv tool install fleet-rlm
```

This makes `fleet` and `fleet-rlm` commands available system-wide.

### Option 2: Install in a Virtual Environment

For project-specific use, install within a virtual environment:

```bash
# Create and activate a virtual environment
uv venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install Fleet-RLM
uv pip install fleet-rlm
```

### Verify Installation

Confirm the installation was successful:

```bash
fleet --help
fleet-rlm --help
```

## Configuration

### 1. Set Up Modal Credentials

Fleet-RLM uses Modal for secure sandbox execution. Configure your Modal credentials:

```bash
modal setup
```

This opens a browser to authenticate with Modal and saves your credentials locally.

### 2. Configure LLM Provider

Create a `.env` file in your working directory with your LLM configuration:

```bash
# Required: LLM Configuration
DSPY_LM_MODEL=openai/gpt-4o-mini
DSPY_LLM_API_KEY=sk-your-api-key-here

# Or use an alternative provider:
# DSPY_LM_MODEL=anthropic/claude-3-sonnet
# DSPY_LM_API_KEY=your-anthropic-key
```

For other supported models, see the [LiteLLM model availability](../litellm-models.md) guide.

### 3. Environment Variables Reference

| Variable | Required | Description |
|----------|----------|-------------|
| `DSPY_LM_MODEL` | Yes | LLM model identifier (e.g., `openai/gpt-4o-mini`) |
| `DSPY_LLM_API_KEY` | Yes | API key for your LLM provider |
| `DSPY_LM_API_BASE` | No | Custom API endpoint (for LiteLLM proxy) |

## Launch the Web UI

Start the Fleet-RLM Web UI:

```bash
fleet web
```

This launches a local server. Open your browser to:

```
http://localhost:8000
```

### Web UI Options

The server runs with sensible defaults. For custom configuration:

```bash
# Custom host and port
fleet-rlm serve-api --host 0.0.0.0 --port 3000
```

## Terminal Chat

For command-line usage, start an interactive chat session:

```bash
fleet
```

### Terminal Chat Options

```bash
# Preload a document into context
fleet --docs-path README.md

# Enable verbose trace output
fleet --trace-mode verbose

# Use a custom Modal volume for persistence
fleet --volume-name my-volume --secret-name my-secrets
```

#### Trace Modes

| Mode | Description |
|------|-------------|
| `compact` | Condensed trace output (default) |
| `verbose` | Full thought/status display |
| `off` | Disable trace output |

## Example Session

Once the Web UI is running at `http://localhost:8000`:

1. **Type a prompt** in the chat input field
2. **Watch the agent reason** through your request
3. **Review the response** and continue the conversation

Example prompts to try:

- "Summarize the architecture of this project."
- "What are the main entry points?"
- "Explain the Modal integration."

## Troubleshooting

### "No module named 'fleet_rlm'"

**Solution:** Ensure you've installed Fleet-RLM and activated your virtual environment (if using one):

```bash
# Verify installation
uv pip list | grep fleet-rlm

# Or with global tool install
uv tool list
```

### "Modal authentication required"

**Solution:** Run Modal setup to configure your credentials:

```bash
modal setup
```

### "DSPY_LM_MODEL not set"

**Solution:** Create a `.env` file with your LLM configuration:

```bash
# Copy the example and edit
cp .env.example .env
```

### "Connection refused at localhost:8000"

**Solution:** The server may already be running on a different port. Check for running processes:

```bash
# Check what's using port 8000
lsof -i :8000

# Or use a different port
fleet-rlm serve-api --port 8001
```

### "Permission denied" errors

**Solution:** If using uv tool install, ensure your PATH includes the uv bin directory. On macOS/Linux:

```bash
# Add to your shell profile (~/.zshrc, ~/.bashrc, etc.)
export PATH="$HOME/.local/bin:$PATH"
```

### Python Version Mismatch

**Solution:** Fleet-RLM requires Python 3.10 or later. Check your version:

```bash
python3 --version
```

If needed, install a newer Python version or use uv's Python management:

```bash
uv python install 3.12
```

## Next Steps

- [Tutorial: Document Analysis](02-doc-analysis.md) — Analyze documents with Fleet-RLM
- [Tutorial: Interactive Chat](03-interactive-chat.md) — Deep dive into terminal chat
- [Installation Guide](../how-to-guides/installation.md) — Full installation details
- [Configuring Modal](../how-to-guides/configuring-modal.md) — Advanced Modal setup

## Getting Help

- **GitHub Issues:** [github.com/qredence/fleet-rlm/issues](https://github.com/qredence/fleet-rlm/issues)
- **Documentation:** [fleet-rlm.readthedocs.io](https://fleet-rlm.readthedocs.io/)
