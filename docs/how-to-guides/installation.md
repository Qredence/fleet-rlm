# Installation Guide

This guide covers installation and setup for `fleet-rlm`.

## Prerequisites

- Python 3.10+
- [uv](https://docs.astral.sh/uv/) package manager
- [pnpm](https://pnpm.io/) (for frontend development from source)
- Modal account (for sandbox execution)

## Option A: Install from PyPI

Add the published package to a `uv`-managed project:

```bash
uv init
uv add fleet-rlm
```

Verify the installation:

```bash
uv run fleet --help
uv run fleet-rlm --help
```

Launch the Web UI:

```bash
uv run fleet web
```

If you already have a `uv` project, skip `uv init` and just run `uv add fleet-rlm`.

If you need the optional MCP surface, install the extra:

```bash
uv add "fleet-rlm[mcp]"
```

## Option B: Install from Source (Contributors)

### 1. Clone and Install Dependencies

```bash
# Clone the repository
git clone https://github.com/qredence/fleet-rlm.git
cd fleet-rlm

# Install all dependencies (runtime + dev + all extras)
uv sync --all-extras --dev
```

> **Note:** The `--all-extras` flag includes the `dev`, `mcp`, `server`, and `full` optional dependency groups.

### 2. Set Up Environment Variables

Copy the example environment file and configure your secrets:

```bash
cp .env.example .env
```

Edit `.env` and set at minimum:

```ini
# Required: LLM Configuration
DSPY_LM_MODEL=openai/gpt-4o
DSPY_LLM_API_KEY=sk-...
```

See [Environment Variables](#environment-variables) below for the full configuration reference.

If you plan to use MLflow locally, keep `MLFLOW_TRACKING_URI=http://127.0.0.1:5001`
aligned with the `make mlflow-server` target. In `APP_ENV=local`, the API server
now auto-starts that localhost MLflow target by default unless you set
`MLFLOW_AUTO_START=false`.

### 3. Frontend Setup (Optional)

For frontend development, install dependencies in the `src/frontend/` directory:

```bash
cd src/frontend
pnpm install --frozen-lockfile
```

Verify the frontend setup:

```bash
pnpm run api:check
pnpm run type-check
pnpm run lint:robustness
pnpm run test:unit
```

### 4. Configure Modal

Modal credentials are configured per-user. Run:

```bash
modal setup
```

See [Configuring Modal](configuring-modal.md) for detailed configuration options.

## Verify the Installation

Run help commands to verify your setup:

```bash
# CLI entrypoint
uv run fleet-rlm --help

# Launcher
uv run fleet --help
```

Test terminal chat:

```bash
uv run fleet-rlm chat --trace-mode compact
```

Launch the Web UI:

```bash
uv run fleet web
```

## Environment Variables

The `.env.example` file contains all configurable environment variables. Key categories:

### Required: LLM Configuration

| Variable           | Description                                         | Example         |
| ------------------ | --------------------------------------------------- | --------------- |
| `DSPY_LM_MODEL`    | LLM model for the planner (DSPy's reasoning engine) | `openai/gpt-4o` |
| `DSPY_LLM_API_KEY` | API key for the LLM provider                        | `sk-...`        |

### Optional: API Server & Database

| Variable         | Description                                   | Default     |
| ---------------- | --------------------------------------------- | ----------- |
| `DATABASE_URL`   | PostgreSQL connection string (Neon)           | -           |
| `AUTH_MODE`      | Auth mode: `dev` or `entra`                   | `dev`       |
| `APP_ENV`        | Environment: `local`, `staging`, `production` | `local`     |
| `AUTH_REQUIRED`  | Require authentication on API routes          | `false`     |
| `DEV_JWT_SECRET` | Secret for local development tokens           | `change-me` |

### Optional: MLflow Tracing

| Variable              | Description                | Default                 |
| --------------------- | -------------------------- | ----------------------- |
| `MLFLOW_ENABLED`      | Enable MLflow tracing      | `true`                  |
| `MLFLOW_TRACKING_URI` | MLflow server URL          | `http://127.0.0.1:5001` |
| `MLFLOW_EXPERIMENT`   | Experiment name for traces | `fleet-rlm`             |

### Optional: Analytics

| Variable          | Description              | Default                    |
| ----------------- | ------------------------ | -------------------------- |
| `POSTHOG_ENABLED` | Enable PostHog analytics | `false`                    |
| `POSTHOG_HOST`    | PostHog host URL         | `https://eu.i.posthog.com` |

> **Security Note:** Never commit your `.env` file with real secrets. Use Modal's secret management for team setups.

## Frontend Development Commands

| Command                    | Description                                                       |
| -------------------------- | ----------------------------------------------------------------- |
| `pnpm run dev`             | Start development server                                          |
| `pnpm run api:check`       | Verify committed frontend OpenAPI artifacts match `openapi.yaml`  |
| `pnpm run type-check`      | Run TypeScript type checks                                        |
| `pnpm run lint:robustness` | Run the frontend lint lane used by repo validation                |
| `pnpm run test:unit`       | Run unit tests                                                    |
| `pnpm run build`           | Build for production                                              |
| `pnpm run check`           | Full frontend suite: type-check, lint, unit tests, build, and e2e |

## Common Makefile Targets

The project includes a `Makefile` for common development tasks:

| Target               | Command                                                                             |
| -------------------- | ----------------------------------------------------------------------------------- |
| `make sync-all`      | Install all dependencies (`uv sync --all-extras --dev`)                             |
| `make test-fast`     | Run tests excluding `live_llm` and `benchmark`                                      |
| `make quality-gate`  | Run backend lint/type/tests, metadata/docs checks, and the repo frontend gate       |
| `make release-check` | Run release-oriented validation: quality gate, security checks, UI build, packaging |
| `make mlflow-server` | Start local MLflow server on port 5001                                              |

## Docs-Only Validation

For docs-only changes, run:

```bash
uv run python scripts/check_docs_quality.py
uv run python scripts/validate_release.py hygiene
uv run python scripts/validate_release.py metadata
```

## Next Steps

- [Terminal Chat Tutorial](../tutorials/03-interactive-chat.md) - Learn to use the CLI chat interface
- [Configuring Modal](configuring-modal.md) - Set up sandbox execution
- [CLI reference](../reference/cli.md) - Review the current `fleet` and `fleet-rlm` surfaces
- [MLflow Workflows](mlflow-workflows.md) - DSPy tracing, evaluation, and optimization with MLflow
