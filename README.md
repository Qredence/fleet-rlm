# Fleet-RLM

**A recursive AI system for code-driven reasoning, task delegation, and durable work.**

Fleet-RLM combines language models, executable workspaces, and recursive agents
to explore data, solve tasks, and produce artifacts. Agents can delegate subtasks
to child agents in separate Daytona sandboxes, while the system persists session
state and exposes execution through live events and traces.

Rather than putting every byte of a dataset into a prompt, it keeps data in
workspace files and gives the model bounded previews and handles it can use to
explore the source programmatically.

The current implementation is a compact Python runtime, also called **Fleet RLM
Lite**: DSPy orchestrates the agents, Daytona runs generated Python, PostgreSQL
persists session state, and a streaming terminal client makes execution visible.

## What you can do

- **Work with file-backed context.** Attach data, select an input file, and ask
  the agent to inspect, calculate, or produce artifacts. Dataset handles include
  paths, metadata, source digests, and bounded previews—not the entire dataset.
- **Delegate recursively.** DSPy RLM agents can create child agents with fresh
  Daytona sandboxes and separate workspace subpaths. Explicit delegation can
  restrict copied inputs and further delegation, within shared execution budgets.
- **Continue a session.** Workspace files, turn history, outcomes, model choices,
  execution mode, and context selection persist in PostgreSQL. Restarting recovers
  durable state, not the Python interpreter's in-memory variables.
- **Reuse Skills and reviewed memory.** Sync user-managed Agent Skills from
  `.fleet/skills/` and retain source-backed memories with review and staleness
  information. `.agents/skills/` is developer guidance, not user Skill content.
- **Inspect execution.** Follow model and tool activity, generated code, execution
  results, agent lifecycles, and budgets in the terminal; correlate turns with
  MLflow traces.
- **Evaluate program changes deliberately.** Use reviewed examples and GEPA to
  produce versioned candidate programs, then explicitly promote or roll them back.
  Optimization does not silently replace the active program.

## How it fits together

```text
Terminal client ── HTTP / SSE ──> FastAPI server ──> DSPy engine
                                      │                 │
                                      │                 └── Daytona sandboxes
                                      ├── PostgreSQL        ├── root agent
                                      └── MLflow            └── child agents
```

The runtime pins **DSPy 3.4.1** and **Daytona 0.224.0**. Generated Python executes
in Daytona; model credentials and host-mediated tools remain on the server.
See [Architecture](ARCHITECTURE.md) for ownership and trust boundaries.

### Execution modes

| Mode | Entry point | Use |
| --- | --- | --- |
| `hybrid` (default) | Route between direct chain-of-thought, Flex, and deep RLM | Let the engine choose a path for each task. |
| `rlm` | DSPy RLM | Start with recursive, code-driven exploration. |
| `flex` | DSPy Flex | Start with a generated DSPy program. |

Change modes with `/mode rlm`, `/mode flex`, or `/mode hybrid`. Tasks with a
selected dataset cannot take the context-free direct-answer route.

## Quickstart

### 1. Prepare the external services

You need Python **3.11–3.13**, [uv](https://docs.astral.sh/uv/), and:

| Resource | Required configuration |
| --- | --- |
| An existing PostgreSQL database | `FLEET_DATABASE_URL` |
| A Daytona organization, Volume, and usable snapshot | `FLEET_DAYTONA_API_KEY`, `FLEET_DAYTONA_ORG_ID`, `FLEET_DAYTONA_VOLUME_ID`, `FLEET_DAYTONA_SNAPSHOT` |
| Authorized Databricks Unity Gateway model services | `DATABRICKS_TOKEN`, plus `DATABRICKS_HOST` or `FLEET_LLM_BASE_URL`; accessible root, sub, and reflection model IDs |

Runtime model IDs use `openai/<service-id>` through DSPy's native `lm15` engine.
Fleet requires an HTTPS Gateway URL and does not fall back to LiteLLM. Example
model IDs in the configuration do not guarantee access in your workspace.
MLflow defaults to local SQLite; remote Databricks tracing needs additional
configuration.

### 2. Install and configure

```bash
git clone https://github.com/Qredence/fleet-rlm.git
cd fleet-rlm
uv sync --dev
cp .env.example .env
${EDITOR:-vi} .env
```

Replace the placeholders in `.env` with your resources and credentials. Fleet
creates its own PostgreSQL tables at startup, but does not create the database.
Never commit `.env` or credentials. See the
[configuration reference](docs/reference/configuration.md) for settings and limits.

### 3. Start Fleet

```bash
uv run fleet cli --session research-1
```

One command starts a local MLflow server (data in `.fleet/mlflow`, UI at
`http://127.0.0.1:5001`), starts the API server on the configured loopback port
(default 8000, log in `.fleet/logs/server.log`), opens the terminal client, and
stops both when you exit. Traces go to the local MLflow experiment `fleet-rlm-dev`
instead of the tracking URI in `.env`; pass `--mlflow env` to keep the configured one.
If an API server is already running on that port (for example from `uv run fleet
serve`), the client connects to it and leaves it running; pass `--url` to connect to a
specific server without starting anything. Reuse a session ID to continue its saved
work; omit `--session` to create a new short ID.

### 4. Work with a file

For example, with a local CSV containing `region` and `revenue` columns:

```text
/attach ./sales.csv
/files
/context files/sales.csv
Calculate total revenue by region and save the summary as artifacts/revenue_by_region.csv.
/artifacts
/get artifacts/revenue_by_region.csv ./revenue_by_region.csv
/history
/trace
```

The prompt is a task for the agent, not a guaranteed output. Inspect the result
and any generated artifact before relying on it.

## Terminal workflow and observability

The Rich / Prompt Toolkit client accepts free-form tasks and supports Tab
completion for slash commands and arguments. Use `/help` for the full command list.

| Area | Commands |
| --- | --- |
| Workspace | `/attach`, `/files`, `/tree`, `/cat`, `/stat`, `/put`, `/get`, `/rm`, `/artifacts` |
| Task context and execution | `/context <path>`, `/context clear`, `/mode` |
| Models and settings | `/models`, `/model root`, `/model sub`, `/config`, `/settings` |
| Durable state | `/sessions`, `/history`, `/memory [query]` |
| Skills | `/skills`, `/skills push`, `/skills pull` |
| Inspection and program updates | `/trace`, `/optimize`, `/reload` |

Streaming events expose route summaries, model/module/tool start and end events,
IDs and timing, bounded tool arguments and results, generated Python, execution
output and errors, sandbox lifecycle events, and budget usage. **These are execution
records, not access to a model's private chain-of-thought.** A request for a terse
final answer does not hide the client's execution-event panels.

Tool payload redaction is best-effort, not a data-loss-prevention guarantee. Treat
terminal captures, traces, and artifacts as potentially sensitive.

## Evaluation and optimization

`/optimize` runs the source checkout's GEPA evaluation workflow against reviewed,
gold-labeled session examples. It requires configured external services and
complete source lineage and input hashes. Source-grouped train/dev/holdout splits
and fresh evaluation runtimes keep candidate evaluation separate from live
session state.

Candidates and evaluation manifests are saved separately from the active program;
promotion and rollback are explicit, gated operations. `/reload` asks for approval
before loading the saved Flex program at a turn boundary. This optimizes DSPy
programs, not model weights, and does not establish a quality improvement without
measured results. ReAnchor calibration is currently skipped without a compatible
client.

## Limits and security

Fleet is intended for **trusted workloads behind a secured boundary**, not an
unvalidated hostile multi-tenant deployment.

- **The HTTP API has no caller authentication.** It binds to loopback by default.
  `FLEET_ALLOW_NON_LOOPBACK_BIND=true` permits another bind address; it does not
  secure the service. Do not expose it directly to an untrusted network.
- Default turn limits are 150 model calls, recursive depth 3, 8 total child
  sandboxes, 4 active children, and a 900-second whole-turn watchdog. File-backed
  context is not unlimited: uploads are capped at 25 MB; workspace synchronization
  is bounded to 25 MB per file, 100 MB total, and 5,000 files.
- Delegation tool restrictions are not a Python or network security policy.
  Daytona deployment policy governs generated code execution.
- Local tests do not certify your live Gateway, Daytona resources, database,
  provider-side isolation, or answer correctness.

## API, documentation, and development

The FastAPI interface supports session management, streaming turns, workspace
files, Skills, memory review, model and mode selection, and program reloads.
Use the [HTTP API reference](docs/reference/http-api.md) and checked-in
[OpenAPI contract](openapi.yaml) rather than treating this README as an endpoint list.

- [Documentation index](docs/index.md)
- [CLI reference](docs/reference/cli.md)
- [Configuration](docs/reference/configuration.md)
- [Database and persistence](docs/reference/database.md)
- [Architecture](ARCHITECTURE.md)
- [Contributing](CONTRIBUTING.md)

For development:

```bash
make install-dev
make check
```

`make check` runs non-live tests, lint, formatting, type checks, documentation links,
OpenAPI validation, and a package build. Live provider and evaluation runs require
external resources and are not part of this gate.

## License

[MIT](LICENSE).
