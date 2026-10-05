# Fleet RLM

Fleet RLM is a terminal-based assistant for work that needs reasoning, Python,
and files. Ask a question, watch its work stream into the terminal, and return
to a saved Session later. Fleet uses [DSPy](https://github.com/stanfordnlp/dspy)
for the RLM reasoning loop, [Daytona](https://www.daytona.io/) for isolated
execution, and FastAPI for its backend.

[![CircleCI](https://dl.circleci.com/status-badge/img/gh/Qredence/fleet-rlm/tree/main.svg?style=svg)](https://dl.circleci.com/status-badge/redirect/gh/Qredence/fleet-rlm/tree/main)
[![PyPI](https://img.shields.io/pypi/v/fleet-rlm?style=flat-square&logo=pypi&logoColor=white)](https://pypi.org/project/fleet-rlm/)
[![Python](https://img.shields.io/badge/python-3.11%20|%203.12%20|%203.13-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-2EA44F?style=flat-square)](LICENSE)

## What you can do

- **See the work as it happens.** The terminal shows reasoning, generated
  Python, tool activity, and results in one timeline.
- **Continue a Session.** With a configured database, committed conversation
  history survives a restart.
- **Work with files.** Attach local files to a Turn, inspect workspace files,
  and download committed Artifacts.
- **Choose how much delegation to use.** The configuration enables DSPy's
  native semantic calls and bounded, isolated child RLMs for independent
  investigations. Set `rlm.recursion_enabled = false` for native-only operation.

## Run a live local Session

You need Python 3.11–3.13, [uv](https://docs.astral.sh/uv/), Node 22.19+,
pnpm, a Daytona account, and a key for the model provider in the shipped
configuration. The repository includes a local SQLite configuration; a separate
PostgreSQL setup is not needed to try Fleet.

### 1. Install

```bash
git clone https://github.com/Qredence/fleet-rlm.git
cd fleet-rlm
uv sync --dev
pnpm --dir tools/fleet-tui install --frozen-lockfile
```

### 2. Add credentials

```bash
cp .env.example .env
```

Open `.env` and fill in `DATABRICKS_TOKEN`, `FLEET_LLM_BASE_URL`,
`FLEET_DAYTONA_API_KEY`, and `FLEET_DAYTONA_ORG_ID`. The example supplies an
explicit local SQLite URL; replace the gateway placeholder with your Databricks
workspace `/ai-gateway/mlflow/v1` base. Keep secrets out of Git. To configure
another provider, use the [configuration guide](docs/reference/configuration.md)
and [environment reference](docs/reference/configuration-environment.md).

### 3. Initialize and start

```bash
uv run python scripts/database.py upgrade
uv run fleet cli
```

Fleet does not migrate the database on startup. `fleet cli` starts the backend
and opens the terminal client. Try a prompt such as “Use Python to calculate
the first 20 Fibonacci numbers and explain the result.” `/help` shows the
available commands. Fleet prints the Session ID so you can return later:

```bash
uv run fleet cli -- --session <session-uuid>
```

The first live Turn uses the configured model provider and Daytona Sandbox.
`uv run fleet doctor daytona` is an optional, disposable connectivity and
mount probe when setup fails. See the [CLI guide](docs/reference/cli.md) for
diagnostics and launch options.

## In the terminal

| Command | Use it to |
| --- | --- |
| `/help` | Find commands and keyboard shortcuts. |
| `/attach <path>` | Add a local file to the next Turn. |
| `/files` | Browse the Workspace `files/` area. |
| `/artifacts` | List Artifacts from the conversation. |
| `/sessions` | Switch between saved Sessions. |
| `/settings` | Edit the single runtime configuration; restart to apply. |

The [terminal guide](docs/how-to-guides/terminal-tui.md) covers file downloads,
themes, Skills, cancellation, and other controls.

## How it works

```text
Terminal client → FastAPI/SSE backend → DSPy RLM → Daytona Sandbox
                         ↘ durable Sessions and committed results
```

The backend prepares each request, runs model-authored Python in Daytona, and
streams progress to the terminal. It commits an answer and any Artifacts only
after the Run settles. Bounded child RLMs are enabled by the shipped
configuration. Runtime policy lives in `config/fleet.toml`, and changes take
effect after a restart.

Fleet also exposes the backend without the terminal through `uv run fleet web`
or `uv run fleet-rlm serve-api --port 8000`. The API binds to loopback by
default and has no caller authentication. See the [HTTP API reference](docs/reference/http-api.md)
and generated [OpenAPI contract](openapi.yaml).

## Learn more

- [Documentation home](docs/index.md) — guides and reference pages.
- [Architecture](ARCHITECTURE.md) — component ownership and trust boundaries.
- [Contributing](CONTRIBUTING.md) — development setup, tests, and change workflow.

## License

MIT — see [LICENSE](LICENSE).
