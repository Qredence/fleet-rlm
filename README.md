# Fleet-RLM

**A Python agent runtime for analyzing files, executing code, and delegating tasks.**

Fleet-RLM combines DSPy reasoning with Daytona execution, PostgreSQL persistence,
and MLflow tracing. Its Textual terminal client streams activity while agents
explore data, run calculations, and produce downloadable artifacts. The current
application is also called **Fleet RLM Lite**.

Large inputs stay in workspace files. The model receives paths, metadata, and
bounded previews, then investigates through Python. Sessions retain history,
files, reviewed memory, and runtime selections across restarts. Python variables
belong to the live interpreter and are not durable Session state.

## How it works

| Session mode | Behavior |
| --- | --- |
| `hybrid` (default) | Routes each Turn to a direct answer, Flex, or RLM. |
| `rlm` | Enters an iterative code-and-observation reasoning loop. |
| `flex` | Executes a DSPy module whose source GEPA can optimize. |

Strict `rlm` and `flex` modes bypass routing. A selected dataset prevents the
context-free direct-answer route; routing errors fall back to RLM. Flex initially
wraps an RLM when tools are supplied. Both routes can delegate to isolated child
sandboxes, share Turn budgets, and promote successful child artifacts with
provenance. Choosing Flex does not establish a speed or quality advantage.

See [architecture](ARCHITECTURE.md) for execution and persistence boundaries.
[pyproject.toml](pyproject.toml) declares dependencies; [uv.lock](uv.lock) records
resolved versions.

## Quickstart

### 1. Prepare your services

Use Python **3.11–3.13** and [uv](https://docs.astral.sh/uv/). You need:

| Resource | Configuration |
| --- | --- |
| Reachable PostgreSQL database | `FLEET_DATABASE_URL` |
| Daytona organization and existing Volume | `FLEET_DAYTONA_API_KEY`, `FLEET_DAYTONA_ORG_ID`, `FLEET_DAYTONA_VOLUME_ID` |
| Authorized Databricks Unity Gateway models | `DATABRICKS_TOKEN`, plus `DATABRICKS_HOST` or `FLEET_LLM_BASE_URL`, and accessible model IDs |

Fleet creates its tables in an existing database; it does not provision
PostgreSQL. `fleet cli` manages local MLflow automatically. Selecting local
tracking still uses your configured remote models and Daytona resources.
Model calls, sandbox execution, and snapshot builds may incur provider charges.

### 2. Install and configure

```bash
git clone https://github.com/Qredence/fleet-rlm.git
cd fleet-rlm
uv sync --dev
cp .env.example .env
${EDITOR:-vi} .env
```

Set your database DSN, Gateway token and host, Daytona API key, organization ID,
and Volume ID, or enter missing values in the startup checklist. Replace example connection values and deployment identifiers;
keep credentials in `.env` or your deployment's secret configuration.

Non-secret defaults live in [config/fleet.toml](config/fleet.toml). Verify that
its root and sub-model IDs are available in your Gateway workspace. Runtime
model IDs use `openai/<service-id>` and the Gateway base URL must use HTTPS.
Evaluation and optimization additionally need their configured judge and
reflection models. TypeSafe Jev routing and Skill selection are optional via
`FLEET_TYPESAFE_API_KEY`.

Configuration precedence is built-in defaults, TOML, `.env`, environment, then
explicit overrides. Persisted Session selections can override process settings
for that Session. See the [configuration reference](docs/reference/configuration.md)
for model settings, database permissions, and tracking profiles.

The versioned execution snapshot is defined in
[sandbox/snapshots/definition.py](src/fleet_rlm/sandbox/snapshots/definition.py).
`fleet cli` checks service readiness through FastAPI and opens a setup checklist
when configuration or snapshot preparation needs attention. The checklist can
save missing settings to `.env`, select or create a Daytona Volume, build a
missing snapshot, or activate an inactive one. Provider mutations and the
optional model inference test require an explicit action.

### 3. Start a Session

```bash
uv run fleet cli --session research-1
```

Before starting a new local API, the launcher runs a private loopback setup API.
Healthy checks proceed to chat; incomplete checks open the Textual checklist.
Use `/setup` between Turns to review settings and apply a controlled restart
with the same Session ID. Existing or remote servers remain operator-managed.

The launcher starts missing local services: the API on loopback port `8000` and
MLflow on `5001` by default. Compatible healthy services are reused. On exit it
stops only processes it owns; MLflow data persists under `.fleet/mlflow`.
Logs are written under `.fleet/logs`.

Interactive terminals use the full-screen Textual client; non-TTY input uses a
Rich fallback. Run `uv run fleet web` to serve the same Textual app in a browser
at `http://127.0.0.1:8080`; each browser connection gets its own Session unless
`--session` is explicitly set. The web listener is loopback-only by default and
has no visitor authentication. External access requires a separately secured
boundary. Browser file paths refer to the host running Fleet, not the visitor's
computer. Use `fleet cli databricks` or `fleet web --mlflow databricks` for
managed MLflow with Unity Catalog tracing after configuring that profile. Pass
`--url` to connect to an existing compatible API without starting services.
Standalone `fleet serve` starts only the API and needs its tracking destination
available.

The TUI's `/optimize` starts an API-owned GEPA job that continues if the client
disconnects; use its explicit cancel action to request cooperative stopping.
On normal exit, the owning `fleet cli`/`fleet web` launcher drains the job before
it stops services it owns. `uv run fleet optimize --session SESSION_ID` remains
available as a standalone CLI workflow.

### 4. Analyze a file and save an artifact

For a local CSV containing `region` and `revenue` columns:

```text
/attach ./sales.csv
/context files/sales.csv
Calculate revenue by region and save artifacts/revenue_by_region.csv.
/artifacts
/get artifacts/revenue_by_region.csv ./revenue_by_region.csv
/trace
```

`/attach` uploads the file to this Session; `/context` selects it as the dataset
for subsequent tasks. Inspect the answer and downloaded artifact. Clear the
selection with `/context clear` when moving to another task.

| Action | Commands |
| --- | --- |
| Discover commands or exit | `/help`, `/exit` |
| Inspect workspace and results | `/files`, `/artifacts`, `/history`, `/trace` |
| Select execution mode | `/mode hybrid`, `/mode rlm`, `/mode flex` |
| Set the client deadline override | `/timeout <seconds\|off\|default>` |
| Inspect configuration and Skills | `/config`, `/models`, `/skills` |

Bundled Skills and user Skills from `.fleet/skills/` synchronize into the
workspace. `.agents/skills/` contains developer guidance. See the
[CLI reference](docs/reference/cli.md) for commands, completion, and Skill workflows.

## Evaluation and optimization

Fleet evaluates complete Turn results with `fleet_metric`; MLflow also provides
trace diagnostics. GEPA optimizes DSPy instructions and Flex source. Serving
models execute candidates, a separate judge scores correctness, and the reflection
model proposes revisions.

Optimization requires approved labels backed by selected immutable replays.
Train/dev search is separated from holdout; correctness, critical cases, execution
evidence, and measured usage gate promotion. A passing candidate remains inactive
until explicit approval rechecks its artifact and deployment identities.
Benchmark traces stay outside curated optimization data.

Follow the [evaluation reference](docs/reference/evaluation.md) and
[canary runbook](docs/runbooks/evaluation-canary.md) for collection, labeling,
search, and approval. These are live operations with additional costs; a GEPA
metric-call budget does not cap total spending.

## Operational boundaries

- The API has no caller authentication and defaults to loopback. Non-loopback
  binding requires explicit opt-in and a separately secured boundary.
- Turns default to a 900-second Fleet deadline. `/timeout off` disables that
  deadline; Daytona TTL and auto-stop bounds still apply. Progress labels are
  advisory and cannot control execution.
- Generated Python runs in Daytona under provider policy. Delegation-tool limits
  do not define Python or network isolation. Traces and artifacts may contain
  sensitive data despite bounded redaction.

## Development and documentation

```bash
make install-dev
make check
```

The quality gate runs non-live tests, static checks, documentation links, OpenAPI
validation, and a package build. It does not establish live provider compatibility
or measured workload gains. Documentation-only changes use `make check-docs` and
`git diff --check`.

- [Documentation index](docs/index.md)
- [HTTP API](docs/reference/http-api.md) and [OpenAPI contract](openapi.yaml)
- [Database and persistence](docs/reference/database.md)
- [Contributing](CONTRIBUTING.md) and [agent instructions](AGENTS.md)

## License

[MIT](LICENSE).
