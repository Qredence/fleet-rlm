# Fleet CLI

Run `uv run fleet --help` or `uv run fleet <command> --help` for exact flags.

| Command | Purpose |
| --- | --- |
| `cli` | Start owned API/local MLflow services, open the terminal client, stop owned services on exit |
| `web` | Start the same services and expose the Textual client through Textual Serve |
| `serve` | Start only the FastAPI server |
| `doctor` | Inspect configuration and service readiness; `--probe-models` adds live inference |
| `snapshot preflight\|build\|activate` | Inspect or explicitly manage the Daytona snapshot |
| `eval dataset\|run\|label\|score` | Prepare cases, collect execution, label gold or score saved traces |
| `optimize` | Check readiness, run GEPA, evaluate or approve a candidate |
| `trace` | Inspect recorded traces |

```bash
uv run fleet cli
uv run fleet web
# Open http://127.0.0.1:8080
```

`cli` and `web` verify the API's MLflow destination. Pass `--url` to connect to an
existing API. Standalone `serve`, `eval`, `optimize`, and `trace` use an existing
tracking server. For a separate local MLflow process:

```bash
mkdir -p .fleet/mlflow
MLFLOW_SERVER_ENABLE_CUSTOM_SCORERS=true uv run --env-file .env mlflow server \
  --host 127.0.0.1 --port 5001 \
  --backend-store-uri sqlite:///$PWD/.fleet/mlflow/mlflow.db \
  --default-artifact-root "$PWD/.fleet/mlflow/artifacts"
```

Local MLflow changes tracking/storage only. Models, Daytona and PostgreSQL retain
their configured connections. Edit host `.env`/`config/fleet.toml` and restart to
apply process settings. Non-TTY input never prompts for credentials.

## Terminal tasks and commands

Enter a task and press Enter. The client renders routing, model/tool activity,
Python code/output, child sandboxes, budgets and the final outcome. Code panels
collapse after success; click disclosure rows or use `Ctrl+O` to toggle them.
`Ctrl+C` clears a draft or requests cancellation while streaming; `/exit` exits.
A lost connection can leave a completed Turn: check `/history` or `/turn` before
retrying. Persistence is reported only after the server commits.

| Commands | Use |
| --- | --- |
| `/help`, `/sessions`, `/history`, `/turn [id]`, `/trace` | Help and durable Session/Turn inspection |
| `/attach <path>`, `/files`, `/artifacts`, `/tree`, `/cat`, `/stat`, `/put`, `/get`, `/rm` | Upload, inspect and transfer workspace files |
| `/context`, `/memory [query]` | Select a dataset or retrieve Session memories |
| `/mode`, `/models`, `/model`, `/config`, `/settings` | Inspect/change supported Session settings |
| `/skills`, `/skills require <names...>`, `/skills clear` | Inspect Skills and set ordered required intent (up to three names) |
| `/replay on\|off` | Control immutable input capture for subsequent Turns; default on |
| `/timeout <seconds\|off\|default>` | Set a request deadline or inherit configuration |
| `/improve`, `/experiments`, `/optimize` | Open Improve; bare `/optimize` opens Runs |

Required Skills force Hybrid to RLM, preserve explicit RLM/Flex, and are refused
in Direct. See [Turn admission](http-api.md#run-a-turn) for source and size limits.
Root/sub model, mode and context choices persist across restarts. Busy Sessions
reject changes. Reflection/judge roles, secrets and resource ceilings remain
operator configuration.

Type `/` to browse commands. Up/Down selects, Tab fills, Enter completes missing
arguments or runs a complete command, and Escape closes the menu. With the menu
closed, Up/Down at the composer's first/last line browses input history and
restores the unsent draft after the newest entry.

### Local file and directory mentions

Type `@` to browse local paths and cached workspace files. Relative, absolute and
`~/` paths work; nested bare-name search refreshes in one-minute windows. Tab,
Enter and mouse selection do not upload. Submission uploads selected local paths
and rewrites mentions to workspace paths. Cached workspace mentions reuse files;
`@./files/report.csv` explicitly selects a local file. Spaces are quoted.

Directory uploads skip hidden, symlink and Git-ignored paths, including tracked
files matching ignore rules. Git ignore-check failures block uploads. Limits are
1,000 files and 100 MB per submission, plus server per-file limits; empty or
oversized selections are refused. Upload failure preserves the draft and sends
no Turn; completed uploads remain. The Rich fallback uses `/attach` instead.

## Improve and evaluation

Improve has four tabs: **Review** labels captured Turns, **Readiness** lists
missing data, **Runs** starts/cancels jobs, and **Results** compares and reviews
candidates. After a captured Turn, `Ctrl+L` approves its answer/figures as gold;
`Ctrl+N` opens Review for correction. Inspect gold before approving it.

```bash
uv run fleet eval label TRACE_ID --answer 'Reviewed answer' --value 22.2 --critical
uv run fleet eval score TRACE_ID
uv run fleet optimize --check
```

`--check` reads PostgreSQL, MLflow and the installed file, without probing models
or Daytona. Optimization requires grouped approved replays, nonempty train/dev/
holdout splits, minimum dev/holdout sizes and a critical holdout. See
[evaluation](evaluation.md) for the metric, budgets, native state and promotion gates.

Useful optimize flags are `--gepa-budget light|medium|heavy` or
`--max-metric-calls N`, `--threads`, `--validation-ratio` (0.1–0.5, default 0.35),
`--max-total-tokens N`, `--reflection-minibatch-size N`,
`--max-candidate-proposals N`, and `--trace-search`. Reflection and final paired
validation add work beyond the search budget; token caps are cooperative and may
overshoot. `--evaluate rescore` judges saved executions; `--evaluate replay`
measures the installed program. Synthetic jobs are currently refused.

API-owned jobs continue after client disconnect; use explicit cancel. One job or
review runs at a time. A candidate needs separate approval:
`uv run fleet optimize --approve VERSION`. Once approved, every Session serves it
at its next Turn. A missing/changed installed file after approval refuses execution.

For saved diagnostics use `eval score --execution-diagnostics-only TRACE_ID`.
To select trusted registered definitions, supply both `--scorer-experiment-id ID`
and repeatable `--registered-scorer NAME`; these cannot be combined with
`--execution-diagnostics-only`. Neither workflow reruns Turns.

## Browser transport and exposure

The web listener defaults to `127.0.0.1:8080`, separately from the API's
`127.0.0.1:8000`. Each browser gets a Session unless `--session` shares one.
`/attach`, `/put`, and `/get` use paths on the server host, not the visitor's computer.

Use `--public-url` for the browser-facing URL behind a reverse proxy. WebSockets
require a matching Origin and public Host header. A non-loopback listener requires
`FLEET_ALLOW_NON_LOOPBACK_BIND=true` and an environment-only `FLEET_WEB_AUTH_TOKEN`
of at least 32 non-whitespace characters. An authenticated TLS proxy must replace
the WebSocket Authorization header with `Bearer <secret>` and be the only route to
the listener. Never put the secret in URLs or client commands.

The API has no caller authentication. Non-loopback API binding also requires
explicit opt-in and a separately secured boundary. See [configuration](configuration.md).
