<div align="center">

# Fleet-RLM

**A recursive AI system for investigating files, executing code, and delegating work.**

</div>

Fleet combines DSPy's native RLM and Flex with a persistent workspace: inspect files, run Python, and split complex tasks into bounded subtasks. Instead of pushing a large dataset into a prompt, Fleet keeps it in workspace files and lets the agent investigate what it needs. Work happens in a Session, with a Textual interface in a terminal or browser and live Turn updates from the Fleet server.

<div align="center">

## What you can do with Fleet

</div>

- **Turn data and documents into answers.** Upload a sales report, spreadsheet, or document and ask Fleet to summarize findings, compare results, or identify trends in plain language.
- **Explore large inputs with DSPy RLM.** The agent writes Python, inspects execution results, and chooses what to examine next. It can work through files in smaller pieces and delegate focused questions to a sub-model, keeping large inputs outside the main prompt. Generated Python runs in Daytona sandboxes and can produce downloadable artifacts.
- **Tune execution logic with DSPy Flex.** Flex runs a DSPy module whose Python source can be optimized with GEPA alongside model instructions. With Fleet's tools, Flex initially wraps an RLM; reviewed examples can guide changes to its execution logic. Candidates require evaluation and approval before installation.
- **Delegate complex work.** RLM and Flex can hand subtasks to isolated child sandboxes. They share the Turn's budgets; only successful child artifacts are promoted to the Session workspace.
- **Continue where you left off.** Session history, files, saved memory, and model/mode choices persist. Download generated artifacts when you need them.
- **Improve from reviewed work.** Label completed Turns, evaluate approved examples, and create GEPA candidates for review. Candidates are not installed automatically.

The default `hybrid` mode chooses a route for each Turn: a direct answer for self-contained questions, RLM for iterative exploration, or Flex for execution through an optimizable DSPy module. You can also select a mode explicitly with `/mode`.

<div align="center">

## Get started

</div>

Fleet needs Python **3.11–3.13**, [uv](https://docs.astral.sh/uv/), an existing PostgreSQL database, a Daytona organization and Volume, and access to a DSPy supported model. Databricks is optional; it can provide model access or MLflow tracking, independently of each other.

```bash
git clone https://github.com/Qredence/fleet-rlm.git
cd fleet-rlm
uv sync --dev
cp .env.example .env
```

Set your database, Daytona, and model-provider values in `.env` or your secret manager:

```dotenv
FLEET_DATABASE_URL=postgresql://USER:PASSWORD@HOST:5432/fleet
FLEET_DAYTONA_API_KEY=your-daytona-api-key
FLEET_DAYTONA_ORG_ID=your-organization-id
FLEET_DAYTONA_VOLUME_ID=your-volume-id
FLEET_LLM_MODEL=openai/gpt-4o-mini
FLEET_LLM_API_KEY=your-model-api-key
# Optional OpenAI-compatible endpoint:
# FLEET_LLM_BASE_URL=https://your-endpoint.example/v1
```

Then start a Session:

```bash
uv run fleet cli --session research-1
```

Run `uv run fleet doctor` to verify database, Daytona, MLflow, and model configuration read-only, and use `uv run fleet snapshot build` / `uv run fleet snapshot activate` when preparing Daytona's execution snapshot. Model inference probes (`--probe-models`), snapshot builds, and Volume mutations require an explicit command.

Fleet creates its tables in your existing PostgreSQL database; it does not provision a database. Model calls, Daytona execution, and snapshot builds may incur charges. Local MLflow tracking does not make model or sandbox usage local or free.

<div align="center">

## Try a file investigation

</div>

Attach a local CSV, select it as context, and ask Fleet to analyze it:

```text
/attach ./sales.csv
/context files/sales.csv
Calculate revenue by region and save artifacts/revenue_by_region.csv.
/artifacts
/get artifacts/revenue_by_region.csv ./revenue_by_region.csv
```

`/attach` uploads the file to the Session. `/context` selects an input for subsequent Turns, and `/get` downloads the artifact back to your machine. Clear the selection with `/context clear` when switching to an unrelated task.

<div align="center">

## Choose how Fleet works

</div>

Set a mode with `/mode` or use the default `hybrid` mode:

| Mode     | Use it when                                                                |
| :------: | :------------------------------------------------------------------------- |
| `hybrid` | You want Fleet to choose between a direct answer and code-based execution. |
| `direct` | The task is self-contained and does not need sandbox code execution.       |
| `rlm`    | The task benefits from iterative code, observations, and reasoning.        |
| `flex`   | You want a DSPy module execution path that can be tuned with GEPA.         |

A selected dataset prevents Hybrid from choosing a context-free direct answer. Routing failures fall back to RLM. RLM and Flex can delegate work to child sandboxes and create artifacts in the Session workspace.

<div align="center">

**Useful commands**

</div>

| Goal                      | Commands                                                                    |
| :-----------------------: | :-------------------------------------------------------------------------- |
| Get help or leave         | `/help`, `/exit`                                                            |
| See files and outputs     | `/files`, `/artifacts`, `/tree`                                             |
| Upload or download files  | `/attach <path>`, `/put <local-path>`, `/get <workspace-path> [local-path]` |
| Set or clear task context | `/context <workspace-path>`, `/context clear`                               |
| Change mode               | `/mode hybrid`, `/mode direct`, `/mode rlm`, `/mode flex`                   |
| Review Session state      | `/history`, `/memory [query]`, `/config`, `/sessions`                       |
| Inspect a Turn            | `/turn [turn-id]`, `/trace`                                                 |
| Manage Skills             | `/skills`, `/skills require <names...>`, `/skills clear`                    |
| Review and improve        | `/improve`                                                                  |

Bundled Skills and user Skills in `.fleet/skills/` are available in Session workspaces. A user Skill with the same name replaces a bundled Skill.

<div align="center">

## Use Fleet in a browser or connect to an API

</div>

```bash
# Browser interface
uv run fleet web

# API only
uv run fleet serve

# Connect a client to an existing API
uv run fleet cli --url http://127.0.0.1:8000 --session research-1
```

The browser interface defaults to `http://127.0.0.1:8080`. Each browser connection gets its own Session unless you pass `--session`; then clients share that Session. File paths refer to the Fleet host, not the visitor's computer.

The API and browser listeners default to loopback. The ordinary API has no caller authentication. External exposure requires explicit non-loopback opt-in and a separately secured boundary. Non-loopback browser access also requires `FLEET_WEB_AUTH_TOKEN` for an authenticating proxy. Do not expose either listener directly to the public internet.

<div align="center">

## Improve Fleet with reviewed examples

</div>

The `/improve` screen supports reviewing Turns, checking readiness, running evaluations, and managing GEPA candidates. Jobs are owned by the API and can continue after a client disconnects; use the explicit cancel action to request cooperative stopping.

Fleet builds optimization datasets from approved, gold-labeled immutable replays and keeps examples from the same source lineage together. Holdout examples stay outside search; benchmark traces are excluded. Candidate promotion is gated by paired validation, critical holdout cases, correctness, and measured usage. Candidates remain inactive until approval rechecks their artifact and deployment identities.

For a readiness check from the command line:

```bash
uv run fleet optimize --check
```

This reads the configured database and MLflow backend. It does not start local MLflow, and it is not an offline-only check. Live optimization and evaluation can incur additional model and sandbox costs; a GEPA metric-call budget does not cap total spending.

<div align="center">

## Development

</div>

```bash
make install-dev
uv run pytest tests/<focused-test>.py
make check
```

`make check` runs offline tests with coverage, Ruff, type checks, local Markdown link checks, OpenAPI validation, and a package build. Offline checks do not prove live provider compatibility or measured workload improvements. For documentation changes, run `make check-docs` and `git diff --check`.

<div align="center">

## License

[MIT](LICENSE).

</div>
