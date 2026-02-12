# Configuring Modal

`fleet-rlm` relies on **Modal** for its secure, sandboxed cloud execution environment. Correct configuration of Modal is essential for the system to work.

## Initial Setup

1.  **Create a Modal Account**: Sign up at [modal.com](https://modal.com).
2.  **Install the Client**: `uv pip install modal` (included in `fleet-rlm`).
3.  **Authenticate**:
    ```bash
    uv run modal setup
    ```

## Managing Secrets

The code running in the cloud (the RLM Agent) often needs to call back to an LLM provider (like OpenAI or Anthropic). You must provide these API keys via Modal Secrets.

### Creating the Secret

Use the `init` command or manual creation:

```bash
uv run modal secret create LITELLM \
    DSPY_LM_MODEL=openai/gpt-4o \
    DSPY_LLM_API_KEY=sk-proj-... \
    DSPY_LM_API_BASE=https://api.openai.com/v1  # Optional
```

_Note: The default secret name expected by `fleet-rlm` is `LITELLM`. You can change this using the `--secret-name` flag in CLI commands._

## Persistent Volumes

To allow the RLM agent to remember information or access large datasets across sessions, we use **Modal Volumes**.

### Creating the Volume

Create a V2 volume named `rlm-volume-dspy`:

```bash
uv run modal volume create rlm-volume-dspy
```

### Usage in RLM

When running the agent, pass the volume name:

```bash
uv run fleet-rlm code-chat --volume-name rlm-volume-dspy
```

Inside the sandbox, this volume is mounted at `/data`.

- **Knowledge**: Upload files to `/data/knowledge/` for the agent to read.
- **Memory**: The agent writes state to `/data/memory/`.
