# Fleet CLI

The `fleet` command has these modes:

```bash
uv run fleet cli [--session SESSION_ID] [--url URL] [--mlflow local|env]
                 [--mlflow-port 5001] [--mlflow-experiment fleet-rlm-dev] [--startup-timeout 180]
uv run fleet serve [--host 127.0.0.1] [--port 8000]
uv run fleet eval dataset|run|label
uv run fleet trace verify <trace-id> [--scope runtime|evaluation|optimization]
```

For Databricks operations with credentials in `.env`, use
`uv run --env-file .env fleet ...` so SDK authentication receives them. For
evaluation, follow the [evaluation canary runbook](../runbooks/evaluation-canary.md):
the server must use the evaluation experiment and UC prefix before executing
the task. `fleet eval run` prints recovery identifiers, uses fresh Sessions per
invocation, and exits unsuccessfully on incomplete collection or scoring.

`cli` is the one-command entry point. Unless `--url` is given, it starts the API
server, waits for its `/health`, opens the interactive Rich client, and stops the
server when the client exits:

- **MLflow** (`--mlflow env`, the default): the configured tracking destination.
  `--mlflow local` starts a local MLflow server on
  `127.0.0.1:{--mlflow-port}` with SQLite storage under `.fleet/mlflow`. The API server it
  starts traces to experiment `--mlflow-experiment` via environment overrides; `.env` is
  not modified. `--mlflow env` starts no MLflow server and leaves the configured tracking URI.
- **API server**: `python -m fleet_rlm.cli serve` on the configured host and port, logging
  to `.fleet/logs/server.log` (MLflow logs to `.fleet/logs/mlflow.log`).
- A healthy API server already on that port is reused and left running. `--url`
  connects to that server only.
- A service that exits or is not ready within `--startup-timeout` seconds aborts the
  launch, prints the end of its log, and exits non-zero.

`serve` starts only the FastAPI server. If `--session` is omitted, the client generates
a short session ID.

When MLflow tracing is configured, the Rich client prints a trace ID delivered
with the completed Turn. `/trace` looks for an ID in the saved `/history`
records. Older or untraced turns may have no ID; the client reports that explicitly.
A completed Turn is reported as persisted only
after the server has successfully synchronized it to Session history.

`fleet trace verify <trace-id>` flushes pending async writes and checks that a
stored trace can be read with its spans. Use `--scope evaluation` or
`--scope optimization` for those configured experiments. In Databricks mode,
the command also verifies the experiment is bound to the configured Unity
Catalog schema and table prefix; pass the fully qualified `trace:/...` ID for a
Unity Catalog trace. This explicit operator check may issue a read to the
configured tracking destination; it is not run during serving.

Commands include `/help`, `/attach <path>`, `/files`, `/artifacts`, `/skills`,
`/tree`, `/cat`, `/stat`, `/put`, `/get`, `/rm`, `/context`, `/mode`,
`/memory [query]`, `/history`, `/trace`, `/sessions`, `/optimize`,
`/config`, `/settings`, `/models`, `/model`, and `/exit`.
`/memory [query]` reads this Session's saved memories, optionally
filtered by query. `/optimize [args]` runs `fleet optimize --session <this session> [args]`.
It compiles and gates a GEPA candidate from self-contained, explicitly selected
Turn snapshots with approved gold labels; it does not automatically score
historical traces. Traces without a matching immutable Lakebase snapshot are
skipped. It requires at least 8 examples from 3 source groups, as well as the
corresponding model, Daytona, database, and tracking services. It never installs
a candidate.
`/optimize --approve <version>` (or the same CLI flag) installs one only when
the current incumbent, configuration, code, and dependencies match the
candidate's recorded identities. Old manifests require reevaluation. The engine
loads an approved candidate at the start of the next Turn.

`fleet eval label <trace-id> --answer "..." [--value V ...] [--route R] [--critical]`
writes gold labels onto a trace; partial updates preserve existing expectations.
`--critical` requires at least one supplied or previously saved expected value and pins the complete source group
to GEPA holdout; a candidate must achieve quality 1 for every critical case in
each of three paired validation passes. A promotion is refused if no critical
holdout case is labeled. `--status` defaults to
`approved` for a new label; updates preserve status and source group unless supplied.
A new source group defaults to the trace's session id. The trace ID is printed after
each Turn and by `/trace`.

`fleet optimize` flags: `--gepa-budget light|medium|heavy` (default `light`) or
`--max-metric-calls N` for the GEPA search metric-call budget, `--threads`,
`--validation-ratio`, and `--approve VERSION`. Reflection calls and the three paired
final validation passes are additional work, not covered by the search budget.

## Interactive tasks, settings, and model selection

Enter any free-form task without a leading slash and press Enter. The client
renders live server events (routing, tools, Python code/output, child sandboxes,
budgets, final answer, errors, and cancellation). Activity includes model/module
start/end status, correlated call IDs, elapsed milliseconds, and bounded tool
arguments/results. Sandbox bridge and DSPy tool events identify their source;
they can describe nested layers of the same operation, not extra model calls.
Python panels distinguish DSPy bootstrap from task code and include expression
results and execution failures, even when no stdout was produced. Routing panels
provide factual decision summaries, not private model thoughts or chain-of-thought.
The answer panel preserves literal line breaks and markup; learned notes are
separate from the answer. Activity previews redact sensitive dictionary keys
and labelled credentials, limit depth/size, and omit predictor payloads. This is
best-effort redaction, not a guarantee against unlabelled secrets in free text;
only stream trusted workspace content to authorized operators.

Each submission has an idempotency
key. Ctrl+C while editing clears input; Ctrl+C while streaming closes the stream
and the server requests remote cancellation. A lost connection is not proof of
rollback: check `/history` before retrying. An incomplete stream is labeled unknown,
not persisted. EOF or `/exit` exits. Tab completes commands and supported arguments.
Use `/replay on` to opt in to bounded immutable snapshot capture for subsequent
Turns in this client process; `/replay off` disables it. Capture is off by default.

- `/config` or `/settings`: inspect non-secret session models, mode, dataset,
  and operator-owned resource limits. Credentials are never returned.
- `/config mode rlm` (also `/settings mode rlm`): change mode; `/mode` remains supported.
- `/models [catalog.schema]`: discover accessible chat-capable Unity Gateway
  model services, defaulting to `system.ai`. This uses the Databricks model-services
  API, not an assumed OpenAI `/models` endpoint. Discovery requires catalog/schema
  permissions; an empty list or authorization failure is not a fabricated catalog.
- `/model` or `/model root`: show a numbered interactive root-model picker.
  `/model sub` selects the recursive-agent model. Enter cancels without changing it.
  `/model root team.models` opens another catalog/schema; `/model root
  openai/team.models.service` sets a discovered model explicitly.

Root/sub selections are validated by the server, isolated to this session, applied
at turn boundaries, and persisted across restarts alongside mode/context. Busy
sessions reject model changes rather than silently changing a running turn.
Discovery does not guarantee inference capacity; an inference failure is still
reported as a turn error. Reflection/judge models, credentials, deployment policy,
and resource ceilings remain operator configuration, not TUI-editable secrets.

The server defaults to `127.0.0.1:8000`. A non-loopback bind is rejected unless
`FLEET_ALLOW_NON_LOOPBACK_BIND=true` is configured. The API has no caller
authentication, so non-loopback binding must only be used behind a separately
authenticated trusted boundary.

Configuration fields and defaults are listed in the
[configuration reference](configuration.md). Live Daytona execution requires
valid API key, org ID, and Volume ID. No Daytona connectivity is asserted by
this reference.
