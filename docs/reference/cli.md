# Fleet CLI

The `fleet` command has these modes:

```bash
uv run fleet cli [databricks] [--mlflow local|databricks] [--session SESSION_ID] [--url URL]
                 [--mlflow-port 5001] [--mlflow-experiment NAME] [--startup-timeout 180]
uv run fleet serve [--host 127.0.0.1] [--port 8000] [--mlflow local|databricks]
uv run fleet eval dataset|run|label ... [--mlflow local|databricks]
uv run fleet optimize --session SESSION_ID ... [--mlflow local|databricks]
uv run fleet trace verify <trace-id> [--scope runtime|evaluation|optimization] [--mlflow local|databricks]
```

Every command defaults to local MLflow. `uv run fleet cli databricks` is the
shorthand for `uv run fleet cli --mlflow databricks`. The command selects the
backend; environment variables override values only inside that backend.
Conflicting selectors are errors, and `--mlflow env` has been retired.

`cli` starts the API server, waits for readiness, opens the terminal client,
and stops services it owns when the client exits:

- Local MLflow starts at `127.0.0.1:5001` by default, storing SQLite metadata
  and artifacts under `.fleet/mlflow`. Configure its port/data directory and
  three experiments in `[mlflow.local]`. Data survives shutdown.
- Databricks mode starts no tracking server. It uses `[mlflow.databricks]`,
  including the workspace experiments and Unity Catalog trace destinations.
  For the default `databricks` URI, Fleet supplies the configured `.env`
  `DATABRICKS_HOST` and `DATABRICKS_TOKEN` to MLflow SDK authentication.
  `databricks://<profile>` keeps explicit SDK profile selection.
- `--mlflow-port` overrides the local port and is rejected for Databricks.
  `--mlflow-experiment` overrides the selected runtime experiment.
- A healthy API already on the configured port is reused only if its MLflow
  backend, tracking URI, experiments, and trace location match. `--url` starts
  nothing and performs the same check. Unknown metadata, unready tracing, or
  a mismatch aborts the connection. Stop the existing server or choose a
  different `FLEET_PORT` when switching destinations.
- Tracking initialization failure aborts API startup. A service that exits or
  misses `--startup-timeout` aborts the launch and reports its log. Logs are
  `.fleet/logs/server.log` and `.fleet/logs/mlflow.log`.
- The launcher stops the API first to flush traces, then its owned MLflow
  process. Reused services remain running. During shutdown, further Ctrl-C
  presses cannot interrupt service cleanup. An uncaught Ctrl-C during startup
  or client operation exits with status 130 without a traceback after cleanup.

## Startup setup

For a new local server, `fleet cli` first starts a private setup-mode FastAPI app.
The Textual readiness checklist covers serving models, an existing PostgreSQL or
Lakebase database, the selected local/Databricks MLflow profile, and Daytona.
It opens automatically when checks require attention. A healthy configuration
proceeds directly to the normal client. Checks do not run model inference, build
snapshots, create Volumes, apply migrations, or provision experiments.

Expand a section to edit missing values. Secret fields are masked; **Save .env**
explicitly merges edits into the launch project's `.env` with mode `0600`, keeping
unrelated entries. Process environment values stay authoritative and are shown as
read-only. Use **Discard edits** to abandon a draft or reload externally changed values.
Verify after saving; individual sections can also be rechecked. Select an existing Daytona Volume or explicitly
create one; build a missing snapshot or activate an inactive one. These provider
actions and the optional root-model inference test show a confirmation before
starting. Long operations stream progress through FastAPI; reconnecting reads the
existing operation rather than starting it again.

**Start / Apply and Restart** shuts down the bootstrap app and starts the normal
API/MLflow services. Normal startup applies Fleet's existing additive migrations
and initializes tracking destinations. The checklist does not provision PostgreSQL.
Use `/setup` from chat between Turns to edit settings and request a controlled
restart with the same Session ID. No active Turn is interrupted by setup apply.
For an existing API or `--url`, setup belongs to the server operator; stop the
operator-managed server or use a different port to launch a new owned instance.

Non-TTY input never prompts for setup credentials or consumes task input as setup
answers. It reports unmet prerequisites; complete setup interactively first.

Standalone `serve`, `eval`, `optimize`, and `trace` commands connect to an
existing tracking server. For local operation, keep `fleet cli` running or
start MLflow separately using the same port/storage configuration:

```bash
mkdir -p .fleet/mlflow
uv run mlflow server --host 127.0.0.1 --port 5001 \
  --backend-store-uri sqlite:///$PWD/.fleet/mlflow/mlflow.db \
  --default-artifact-root "$PWD/.fleet/mlflow/artifacts"
```

`fleet eval run` checks its API destination before creating work. The API must
trace into the selected evaluation experiment and, for Databricks, its evaluation
UC prefix. Follow the [evaluation canary runbook](../runbooks/evaluation-canary.md).
The command prints recovery identifiers and exits unsuccessfully on incomplete
collection or scoring. A successful startup does not guarantee later asynchronous
trace export; use `trace verify` for explicit readback.

Local MLflow changes tracking and artifact storage. Models, Daytona, judges,
and PostgreSQL retain their separately configured services and credentials.

`serve` starts only the FastAPI server. If `--session` is omitted, the client generates
a short session ID.

When MLflow tracing is configured, the client prints a trace ID delivered
with the completed Turn. The Textual Turn card and the non-TTY Rich fallback
also print `Turn persisted to Session history.` after the server commits the
Turn. `/trace` looks for an ID in the saved `/history` records, and
`/turn <turn-id>` reads the durable Turn status. Older or untraced turns may
have no ID; the client reports that explicitly. A completed Turn is reported
as persisted only after the server has successfully synchronized it to Session
history.

`fleet trace verify <trace-id>` flushes pending async writes and checks that a
stored trace can be read with its spans. Use `--scope evaluation` or
`--scope optimization` for those configured experiments. In Databricks mode,
the command also verifies the experiment is bound to the configured Unity
Catalog schema and table prefix; pass the fully qualified `trace:/...` ID for a
Unity Catalog trace. This explicit operator check may issue a read to the
configured tracking destination; it is not run during serving.

Commands include `/setup`, `/help`, `/attach <path>`, `/files`, `/artifacts`, `/skills`,
`/tree`, `/cat`, `/stat`, `/put`, `/get`, `/rm`, `/context`, `/mode`,
`/memory [query]`, `/history`, `/trace`, `/turn [turn-id]`, `/replay [on|off]`,
`/timeout <seconds|off|default>`, `/sessions`, `/optimize`, `/config`,
`/settings`, `/models`, `/model`, and `/exit`.
`/memory [query]` reads this Session's saved memories, optionally
filtered by query. `/turn [turn-id]` inspects the durable status and outcome of
the latest or specified Turn (`GET /sessions/{session_id}/turns/{turn_id}`).
`/optimize [args]` runs `fleet optimize --session <this session> [args]`.
It inherits the session’s MLflow backend and effective profile settings; a conflicting
`--mlflow` argument is rejected. It compiles and gates a GEPA candidate from self-contained, explicitly selected
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
results and execution failures, even when no stdout was produced. In an
interactive terminal, task REPL cells expand while executing and collapse to a
one-line summary on clean completion (failed cells stay expanded); click any
`▸`/`▾` disclosure row or press `Ctrl+O` (`e` while streaming), `Alt+A` (`a` to
toggle all), `Ctrl+T` (`t` for Thinking blocks), or `Alt+1`–`Alt+9` (`1`–`9`
while streaming) to expand or collapse code, outputs, tool calls, and thinking
blocks in place. Routing panels provide factual decision summaries, not private
model thoughts or chain-of-thought.
The answer panel preserves literal line breaks and markup; learned notes are
separate from the answer. Activity previews redact sensitive dictionary keys
and labelled credentials, limit depth/size, and omit predictor payloads. This is
best-effort redaction, not a guarantee against unlabelled secrets in free text;
only stream trusted workspace content to authorized operators.

Each submission has an idempotency
key. Ctrl+C while editing clears input; Ctrl+C while streaming closes the stream
and the server requests remote cancellation. A lost connection is not proof of
rollback: check `/history` before retrying. An incomplete stream is labeled unknown,
not persisted. EOF or `/exit` exits.

### Command menu and input history

In the Textual client, type `/` to browse all commands or keep typing to filter them.
Up/Down wraps through suggestions; PageUp/PageDown moves by a page. The list scrolls
with its highlighted item, including in an 80-column terminal. Tab or clicking an
item fills it without running it. Enter accepts an incomplete highlighted command
or argument first; Enter on an explicitly complete command executes it. For example,
`/ti` → Enter fills `/timeout `, another Enter chooses `off`, and a final Enter runs
`/timeout off`. Fully typed commands such as `/config` still run directly on Enter.
Escape closes the menu without clearing the draft; editing or Tab enables suggestions
again. The footer shows the active menu keys and the menu shows the selection count.

Argument menus include nested choices such as `/config mode rlm` and
`/settings mode flex`, model roles, Skills actions, replay, timeout, and optimization
flags. Model identifiers and arbitrary numeric/text arguments remain free-form;
choosing a completion never starts optimization or another command by itself.
`/attach` and `/put` suggest local paths and descend into directories. Workspace-path
commands use the cached inventory populated by `/files`, `/tree`, `/attach`, and
`/put`; `/context clear` is available even with no cached files. Enter preserves
fully specified workspace paths and free-form transfer destinations; use Tab or
click to explicitly expand a destination suggestion.
`/put` and `/get` quote suggested paths containing spaces and accept quoted source
and destination arguments. The Rich / Prompt Toolkit fallback keeps Tab completion.

#### Local file and directory mentions

Type `@` at a word boundary to open local and cached workspace suggestions.
The menu follows the cursor, including when returning to an earlier mention.
Bare names search nested paths: `@fleet_rlm` finds `@./src/fleet_rlm`.
Relative paths start at the client's working directory; absolute and `~/` paths
also work. Nested name matching is case-insensitive and uses a bounded,
best-effort index refreshed on demand in one-minute windows. New nested files
may take up to a minute to appear in bare-name search. Explicit paths remain
available immediately and when a large tree exceeds the search window.

Choose a directory's **attach on submit** entry to select it, or its trailing-`/`
**browse** entry to open it. Tab, Enter, and mouse selection do not upload content.
Submitting uploads local mentions before starting the Turn and rewrites them to
`@files/...` workspace paths. Directory uploads preserve the selected directory's
name and subtree under a unique workspace root. Standalone files get unique names;
repeated and overlapping mentions share uploads within a submission.

Directory uploads and nested search skip hidden paths, symlinks, and Git-ignored
files, including tracked files matching ignore rules. Git working trees require
Git; ignore-check failures block uploads. Outside Git, hidden paths and symlinks
are still skipped. Immediate-folder browsing shows hidden names only when their
typed prefix starts with `.`. Directory uploads refuse empty or fully excluded
selections and selections above 1,000 files or 100 MB combined per submission;
the server's per-file limits also apply. Selections are never silently truncated.

Paths with spaces or quotes are quoted automatically: `@"./quarter report.csv"`.
Typed existing local paths work too. Exact cached workspace mentions are reused
without upload; use `@./files/report.csv` to distinguish a local file from cached
`@files/report.csv`. Email addresses do not trigger mentions.

Only submit files you intend to upload. Browsing reads no file contents.
Upload failure or cancellation before the Turn preserves the draft and sends no
Turn; completed uploads remain in the workspace. Cancellation does not interrupt
an in-flight upload. The Rich / Prompt Toolkit fallback requires `/attach`
instead of automatically attaching local `@` mentions.

With the menu closed, Up at the first composer line browses older input and Down at
the last line browses newer input. Returning past the newest history entry restores
the unsent draft. Down on a fresh draft does not clear it. History recall does not
reopen the menu; editing the recalled text or requesting Tab completion does.

Use `/replay on` to opt in to bounded immutable snapshot capture for subsequent
Turns in this client process; `/replay off` disables it. Capture is off by default.

- `/config` or `/settings` (add `json` for raw JSON): inspect non-secret session models, mode, dataset,
  and operator-owned resource limits. Credentials are never returned. Its
  `limits.timeout_seconds` is the server-resolved configured value; it does not
  change when `/timeout` is used.
- `/timeout <seconds|off|default>`: set or clear this client's deadline override
  for subsequent Turns. `<seconds>` must be `0` or `10`–`3600`; `off` sends `0`
  (no Fleet deadline) and `default` clears the override so Turns inherit the
  server configuration again. The override is client-local state, transmitted as
  `TurnRequest.timeout_seconds` on each later Turn and recorded by the server as
  `source=request`. It never mutates the persisted Session or the server
  configuration, and a new client against the same Session does not inherit it.
  A deadline-free Turn still obeys Daytona's provider TTL and idle bounds.
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
