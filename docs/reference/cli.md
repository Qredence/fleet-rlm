# Fleet CLI

The `fleet` command has two modes:

```bash
uv run fleet serve [--host 127.0.0.1] [--port 8000]
uv run fleet cli [--url http://127.0.0.1:8000] [--session SESSION_ID]
```

`serve` starts the FastAPI server. `cli` connects the interactive Rich client
to an already running server. If `--session` is omitted, the client generates
a short session ID. If `--url` is omitted, it uses the configured host and
port.

When MLflow tracing is configured, the Rich client prints a trace ID delivered
with the completed Turn. `/trace` looks for an ID in the saved `/history`
records. Older or untraced turns may have no ID; the client reports that explicitly.
A completed Turn is reported as persisted only
after the server has successfully synchronized it to Session history.

Commands include `/help`, `/attach <path>`, `/files`, `/artifacts`, `/skills`,
`/tree`, `/cat`, `/stat`, `/put`, `/get`, `/rm`, `/context`, `/mode`,
`/memory [query]`, `/history`, `/trace`, `/reload`, `/sessions`, `/optimize`,
`/config`, `/settings`, `/models`, `/model`, and `/exit`.
`/memory [query]` reads this Session's saved memories, optionally
filtered by query. `/reload` asks for explicit approval before reloading the
saved Flex program at a turn boundary. `/optimize` invokes the source-checkout
GEPA/MLflow workflow, which curates only reviewed, gold-labeled Session
examples; it requires at least three usable examples as well as the corresponding
model, Daytona, database, and tracking services. It does not approve or promote
a candidate automatically.

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
