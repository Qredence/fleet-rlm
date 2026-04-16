# Developer Setup Guide

This guide walks through setting up a complete local development environment for contributing to fleet-rlm. For user-focused installation instructions, see the [Installation Guide](installation.md).

## Prerequisites

| Requirement   | Version | Purpose                           |
| ------------- | ------- | --------------------------------- |
| Python        | 3.10+   | Runtime and development           |
| uv            | Latest  | Package and dependency management |
| pnpm          | Latest  | Frontend development              |
| Git           | 2.x     | Version control                   |
| Daytona Account | -     | Sandbox execution                 |

## 1. Install Python

fleet-rlm requires Python 3.10 or higher. The project officially supports Python 3.10, 3.11, and 3.12.

### Check Your Python Version

```bash
python3 --version
```

Expected output: `Python 3.10.x` or higher.

### Install Python (if needed)

**macOS (Homebrew):**

```bash
brew install python@3.12
```

**Ubuntu/Debian:**

```bash
sudo apt update
sudo apt install python3.12 python3.12-venv python3-pip
```

**Windows (winget):**

```powershell
winget install Python.Python.3.12
```

## 2. Install uv

[uv](https://docs.astral.sh/uv/) is the package manager used for this project. It replaces pip, pip-tools, and virtualenv with a single fast tool.

### Install uv

**macOS/Linux:**

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Windows:**

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

### Verify Installation

```bash
uv --version
```

## 3. Clone and Set Up the Repository

### Fork and Clone

```bash
# Fork on GitHub, then clone your fork
git clone https://github.com/YOUR_USERNAME/fleet-rlm.git
cd fleet-rlm

# Add upstream remote for syncing
git remote add upstream https://github.com/qredence/fleet-rlm.git
```

### Install Dependencies

Install all dependencies including development tools and optional extras:

```bash
uv sync --all-extras
```

This command:

- Creates a virtual environment in `.venv/`
- Installs all runtime dependencies
- Installs development dependencies (`pytest`, `ruff`, `ty`, etc.)
- Installs optional extras (`mcp`, `server`, `full`)

### Install Pre-commit Hooks

```bash
uv run pre-commit install
uv run pre-commit install --hook-type pre-push
```

This installs both fast `pre-commit` hooks for each commit and stronger `pre-push`
checks before code leaves your machine.

## 4. Configure Environment Variables

### Create .env File

```bash
cp .env.example .env
```

### Required Variables

At minimum, configure the LLM provider:

```ini
# LLM Configuration - REQUIRED
DSPY_LM_MODEL=openai/gpt-4o
DSPY_LLM_API_KEY=sk-your-api-key-here
```

### Environment Variable Categories

| Category     | Variables                                            | Required        |
| ------------ | ---------------------------------------------------- | --------------- |
| **LLM**      | `DSPY_LM_MODEL`, `DSPY_LLM_API_KEY`                  | Yes             |
| **Database** | `DATABASE_URL`                                       | For persistence |
| **Auth**     | `AUTH_MODE`, `AUTH_REQUIRED`                         | For API server  |
| **MLflow**   | `MLFLOW_ENABLED`, `MLFLOW_TRACKING_URI`              | For tracing     |
| **PostHog**  | `POSTHOG_ENABLED`, `POSTHOG_API_KEY`, `POSTHOG_HOST` | For analytics   |

### Key Configuration Options

```ini
# LLM Model Selection
# Examples: openai/gpt-4o, anthropic/claude-3-sonnet, google/gemini-pro
DSPY_LM_MODEL=openai/gpt-4o

# LLM API Key (provider-specific)
DSPY_LLM_API_KEY=sk-...

# Optional: Custom API endpoint (for LiteLLM proxy or self-hosted)
# DSPY_LM_API_BASE=https://your-proxy.com/v1

# Development Auth Mode
AUTH_MODE=dev
AUTH_REQUIRED=false
APP_ENV=local

# MLflow Tracing (enabled by default)
MLFLOW_ENABLED=true
MLFLOW_TRACKING_URI=http://127.0.0.1:5001
MLFLOW_EXPERIMENT=fleet-rlm

# Optional: disable local MLflow auto-start and manage it yourself with
# `make mlflow-server`
# MLFLOW_AUTO_START=false

# PostHog Analytics
POSTHOG_ENABLED=false
```

> **Security:** Never commit your `.env` file. It is already in `.gitignore`. For team setups, use your normal secret-management flow instead of checking shared credentials into the repo.

> **MLflow note:** The local MLflow default is `http://127.0.0.1:5001`, which
> matches `make mlflow-server`. In local development, the API will auto-start that
> localhost MLflow target by default. If you change the port in `.env`, keep your
> manual MLflow server command aligned, or set `MLFLOW_AUTO_START=false` if you
> want to manage MLflow yourself.

## 5. Configure Daytona Credentials

Daytona provides the maintained sandbox execution environment.

Set the required runtime variables:

```bash
export DAYTONA_API_KEY="..."
export DAYTONA_API_URL="https://app.daytona.io/api"
```

Optional:

```bash
export DAYTONA_TARGET="default"
```

Then verify the runtime path:

```bash
uv run fleet-rlm daytona-smoke --repo https://github.com/qredence/fleet-rlm.git --ref main
```

For runtime details, see [Daytona Runtime Architecture](../reference/daytona-runtime-architecture.md).

## 6. Frontend Setup (Optional)

If you're working on the web UI, install frontend dependencies:

```bash
cd src/frontend
pnpm install --frozen-lockfile
```

### Verify Frontend Setup

```bash
pnpm run api:check
pnpm run type-check
pnpm run lint:robustness
pnpm run test:unit
pnpm run build
```

## 7. Verify Your Setup

### Test the CLI

```bash
uv run fleet-rlm --help
uv run fleet --help
```

### Run Tests

```bash
# Fast tests (excludes live_llm and benchmark)
make test-fast

# Or directly:
uv run pytest -q -m "not live_llm and not benchmark"
```

### Run Quality Checks

```bash
# Repo-aligned validation gate
make quality-gate

# When backend request/response or OpenAPI-facing schema metadata changes
uv run python scripts/openapi_tools.py generate
cd src/frontend && pnpm run api:check
```

### Start Development Server

```bash
# Terminal chat
uv run fleet

# Web UI
uv run fleet web
```

## 8. IDE/Editor Setup (Recommended)

### VS Code

The project includes VS Code configuration in `.vscode/`.

**Recommended Extensions:**

```json
{
  "recommendations": [
    "ms-python.python",
    "ms-python.vscode-pylance",
    "charliermarsh.ruff",
    "esbenp.prettier-vscode",
    "dbaeumer.vscode-eslint"
  ]
}
```

**Settings (add to `.vscode/settings.json`):**

```json
{
  "python.defaultInterpreterPath": "${workspaceFolder}/.venv/bin/python",
  "[python]": {
    "editor.defaultFormatter": "charliermarsh.ruff",
    "editor.formatOnSave": true,
    "editor.codeActionsOnSave": {
      "source.fixAll": "explicit",
      "source.organizeImports": "explicit"
    }
  },
  "python.analysis.typeCheckingMode": "basic",
  "ruff.lint.args": ["--config=pyproject.toml"],
  "markdown.validate.enabled": true
}
```

### PyCharm

1. Open the project directory
2. Go to **Settings > Project > Python Interpreter**
3. Add interpreter: **Existing environment** → `.venv/bin/python`
4. Enable **Ruff** plugin for linting

### Neovim/Vim

For Neovim with LSP support:

```lua
-- Using mason.nvim
require('mason').setup()
require('mason-lspconfig').setup {
  ensure_installed = { 'pyright', 'ruff' }
}

-- Configure Pyright
require('lspconfig').pyright.setup {
  settings = {
    python = {
      analysis = {
        typeCheckingMode = "basic"
      }
    }
  }
}
```

## 9. Common Development Commands

### Makefile Targets

| Command                                       | Description                                                                   |
| --------------------------------------------- | ----------------------------------------------------------------------------- |
| `make sync-all`                               | Install all dependencies                                                      |
| `make test-fast`                              | Run the default fast backend test suite                                       |
| `make quality-gate`                           | Run backend lint/type/tests, metadata/docs checks, and the repo frontend gate |
| `make release-check`                          | Run release-oriented validation, including security and packaging             |
| `make format`                                 | Format code with ruff                                                         |
| `make lint`                                   | Check linting with ruff                                                       |
| `make typecheck`                              | Run type checker                                                              |
| `uv run python scripts/check_docs_quality.py` | Run docs-only validation                                                      |
| `make mlflow-server`                          | Start local MLflow server                                                     |

### Frontend Commands

| Command                    | Description                                                       |
| -------------------------- | ----------------------------------------------------------------- |
| `pnpm run dev`             | Start development server                                          |
| `pnpm run api:sync`        | Copy the root OpenAPI spec and regenerate TS types                |
| `pnpm run api:check`       | Verify committed frontend OpenAPI artifacts are up to date        |
| `pnpm run type-check`      | Run TypeScript type checks                                        |
| `pnpm run lint:robustness` | Run the repo lint lane                                            |
| `pnpm run test:unit`       | Run unit tests                                                    |
| `pnpm run test:e2e`        | Run e2e tests                                                     |
| `pnpm run build`           | Build for production                                              |
| `pnpm run check`           | Full frontend suite: type-check, lint, unit tests, build, and e2e |

## Troubleshooting

### uv sync fails

**Symptom:** Dependency resolution errors.

**Solution:** Clear uv cache and retry:

```bash
uv cache clean
uv sync --all-extras
```

### Daytona authentication fails

**Symptom:** Daytona config or connectivity errors.

**Solution:** Verify environment variables:

```bash
echo $DAYTONA_API_KEY
echo $DAYTONA_API_URL
echo $DAYTONA_TARGET
```

### Tests fail with import errors

**Symptom:** `ModuleNotFoundError: No module named 'fleet_rlm'`

**Solution:** Ensure you're running with `uv run`:

```bash
uv run pytest -q
```

### Frontend commands fail

**Symptom:** `pnpm run dev` or `pnpm run check` fails inside `src/frontend`.

**Solution:** Reinstall frontend dependencies with the repo-standard package manager:

```bash
cd src/frontend
pnpm install --frozen-lockfile
```

### Pre-commit hooks fail

**Symptom:** Hooks fail on commit.

**Solution:** Run manually to see details:

```bash
uv run pre-commit run --all-files
uv run pre-commit run --hook-stage pre-push --all-files
```

## Next Steps

- Read [CONTRIBUTING.md](../../CONTRIBUTING.md) for contribution guidelines and testing overview
- Review [AGENTS.md](../../AGENTS.md) for project architecture and conventions
- Check [Daytona Runtime Architecture](../reference/daytona-runtime-architecture.md) for runtime details
