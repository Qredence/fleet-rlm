# Fleet-RLM

**A Python agent runtime for working with files, executing code, and delegating tasks.**

Fleet-RLM combines DSPy reasoning with Daytona execution, PostgreSQL persistence,
and MLflow observability. Use its streaming terminal client to inspect data,
run calculations, create artifacts, and delegate independent investigations to
child agents. The current implementation is also called **Fleet RLM Lite**.

Large inputs stay in workspace files. The model receives paths, metadata, digests,
and bounded previews, then explores the data through Python. Sessions retain
history, files, reviewed memory, and runtime selections across restarts;
interpreter variables are not durable session state.

## Runtime at a glance

```text
Terminal client ── HTTP / SSE ──> FastAPI ──> DSPy Turn program
                                    │              │
                                    │              └── Daytona: generated Python,
                                    │                  root and child sandboxes
                                    ├── PostgreSQL: durable session state
                                    └── MLflow: traces and evaluation
```

The dependency manifest supports DSPy `>=3.4.1,<3.5` and pins Daytona `0.224.0`
and MLflow `3.17.1`; [uv.lock](uv.lock) records resolved versions. The integration
uses DSPy 3.4.1's RLM, Flex, and native GEPA contracts. See
[ARCHITECTURE.md](ARCHITECTURE.md) for runtime ownership and execution boundaries.

## RLM, Flex, and routing

| Session mode | Execution |
| --- | --- |
| `hybrid` (default) | A router selects `direct`, `flex`, or `rlm` for each Turn. |
| `rlm` | Enter the primary RLM directly for iterative code-driven reasoning. |
| `flex` | Execute the current Flex module implementation directly. |

The `direct` route uses chain-of-thought answer generation without code execution.
A selected dataset prevents this context-free route. Routing errors or unknown
labels fall back to RLM. Change modes with `/mode hybrid`, `/mode rlm`, or
`/mode flex`.

**RLM is the iterative reasoning loop.** It generates Python, observes execution
results, and continues until it submits an answer or reaches its limits. GEPA
can tune the primary RLM's action-generation and fallback-extraction instructions.

**Flex makes a module's source code optimizable.** Fleet supplies Flex with the
same task signature, delegation tools, and a Daytona interpreter. Its initial
implementation wraps an RLM because tools are present. A GEPA candidate can
replace that implementation with different Python control flow and DSPy calls.
Choosing Flex does not by itself imply a deterministic or faster workflow.

Both execution routes can delegate. `spawn_agent` runs a subtask in a fresh child
sandbox; `spawn_agents` runs independent subtasks concurrently within the shared
limits. Explicit delegation selects copied inputs and promotes resulting artifacts
with provenance. Dynamically created child RLMs are not separate persisted GEPA
instruction components.

## Quickstart

### 1. Prepare services

Use Python **3.11–3.13** and [uv](https://docs.astral.sh/uv/). Live execution needs:

| Service | Configuration |
| --- | --- |
| Existing PostgreSQL database | `FLEET_DATABASE_URL` |
| Daytona organization and Volume | `FLEET_DAYTONA_API_KEY`, `FLEET_DAYTONA_ORG_ID`, `FLEET_DAYTONA_VOLUME_ID` |
| Authorized Databricks Unity Gateway model services | `DATABRICKS_TOKEN`, plus `DATABRICKS_HOST` or `FLEET_LLM_BASE_URL`, and accessible model IDs |

The execution snapshot is a repository-defined artifact in
`src/fleet_rlm/sandbox/snapshots.py`; it is not configurable through `.env` or TOML.
The pin is `fleet-rlm-python313-v13` (Python 3.13 with 4 CPUs, 8 GB memory, and
8 GB disk). Fleet preflights it before provisioning and refuses a missing,
inactive, or under-sized snapshot with the build command.

DSPy runtime model IDs use `openai/<service-id>` through the native `lm15` engine.
The Gateway URL must use HTTPS; Fleet does not fall back to LiteLLM. Example model
IDs do not guarantee availability in your workspace. Evaluation and optimization
also require access to their configured judge and reflection models.

### 2. Install and configure

```bash
git clone https://github.com/Qredence/fleet-rlm.git
cd fleet-rlm
uv sync --dev
cp .env.example .env
${EDITOR:-vi} .env
```

Fill in your resources and credentials. Fleet creates its tables in the existing
database at startup. Never commit `.env` or credentials.

Non-secret defaults live in [config/fleet.toml](config/fleet.toml). Precedence is
built-in defaults, TOML, `.env`, environment, then explicit overrides; persisted
Session selections can override process settings for that Session. See the
[configuration reference](docs/reference/configuration.md) for details.

### 3. Start a session

```bash
uv run fleet cli --session research-1
```

The client starts a server on the configured loopback port (default `8000`), logs
it to `.fleet/logs/server.log`, and stops it when you exit. If a server is already
running on that port, the client connects and leaves it running. Pass `--url` to
connect to a specific server without starting one, or run `uv run fleet serve`
separately.

`uv run fleet cli` starts local MLflow with persistent data under `.fleet/mlflow`.
Use `uv run fleet cli databricks` for Databricks managed MLflow and Unity Catalog
trace storage. Configure the two destinations in `[mlflow.local]` and
`[mlflow.databricks]` in `config/fleet.toml`. Standalone commands accept
`--mlflow local|databricks` and default to local; they require an existing
tracking server. See the [CLI reference](docs/reference/cli.md).

### 4. Work with a file

For a CSV with `region` and `revenue` columns:

```text
/attach ./sales.csv
/context files/sales.csv
Calculate revenue by region and save artifacts/revenue_by_region.csv.
/artifacts
/get artifacts/revenue_by_region.csv ./revenue_by_region.csv
/trace
```

Inspect the answer and artifact before relying on them. Use `/help` for available
commands and Tab completion for commands and arguments.

| Workflow | Commands |
| --- | --- |
| Workspace and artifacts | `/attach`, `/files`, `/tree`, `/cat`, `/stat`, `/put`, `/get`, `/rm`, `/artifacts` |
| Context and execution | `/context <path>`, `/context clear`, `/mode` |
| Turn deadlines | `/timeout <seconds\|off\|default>` |
| Models and configuration | `/models`, `/model root`, `/model sub`, `/config`, `/settings` |
| Sessions and memory | `/sessions`, `/history`, `/memory [query]` |
| Skills | `/skills`, `/skills push`, `/skills pull` |
| Traces and optimization | `/trace`, `/optimize` |

Bundled Skills and user Skills from `.fleet/skills/` are synchronized into the
workspace. An optional TypeSafe Jev backend selects relevant Skills for preloading.
`.agents/skills/` contains developer guidance and is never user Skill content.

## Turn lifecycle and observability

A failing or cancelled Turn tears down its sandbox before the terminal event; a
successful Turn keeps the warm root sandbox and records `cleanup=not_required`.
A teardown discards the cached root handle, so the next Turn cold-resumes into a
new sandbox.

`turn_timeout_seconds` accepts exactly `0` or `10..3600` and defaults to `900`.
`0` opts into deadline-free execution via configuration, per-Turn
`timeout_seconds` on `POST /sessions/{id}/turn`, or the client-local
`/timeout <seconds|off|default>` command. Resolution is request, then
configuration, then default. Daytona `ttl_minutes` and `auto_stop_interval` still
apply and are reported; host-call waiting derives from the remaining deadline.

Labels `waiting_on_host`, `progress_unknown`, `stalled`, `interrupted`, and
`fenced` appear in SSE and the TUI. They are advisory and never cancel, pause, or
retry a Turn; uninstrumented work stays `progress_unknown`. Generated code can
emit observation-only checkpoints with
`with progress_scope("label", expect_seconds=45) as progress: progress.checkpoint(n)`.
See [ARCHITECTURE.md](ARCHITECTURE.md) for lifecycle ownership.

## Evaluation: what is measured

Fleet evaluates complete Turn results against reviewed expectations. The shared
`fleet_metric` supplies GEPA with a scalar score and natural-language feedback.
It can also be reported through MLflow.

| Check | Evidence | When applied |
| --- | --- | --- |
| Answer correctness and safety | Judge compares task, answer, and reviewed reference | Every nonempty answer; reference-free judging is available outside curated gold examples |
| Expected values | Required values appear in the answer | Values are labeled |
| Route correctness | Effective route matches the label | A route is labeled |
| Artifact correctness | Text, JSON, CSV, or declared Python verification matches expectations | Artifacts are declared |
| Delegation correctness | Child count, inputs, success, and promoted artifact receipts match | Children are declared |
| Execution efficiency | Host-recorded model-call usage and sandbox errors | Usage is available |

An empty answer or any failed applicable quality check scores zero. Otherwise:

```text
efficiency = max(0, 1 - 0.015 × max(0, model_calls - 3) - 0.15 × sandbox_errors)
score      = 0.9 + 0.1 × efficiency
```

Missing usage earns no efficiency credit and cannot support promotion. A labeled
route is a hard quality requirement, even when another route produces the right
answer. Artifact and delegation checks require explicit structured expectations.
Efficiency is a call/error proxy, not a token-cost or latency measurement.

The broader MLflow suite also reports relevance, retrieval groundedness,
sandbox-verification discipline, Skill-use discipline, and trace-based efficiency.
These additional metrics do not automatically become GEPA objectives. Retrieval
groundedness applies only when retrieval spans exist. See the
[evaluation reference](docs/reference/evaluation.md) for labeling and scoring.

## Optimization with GEPA

GEPA improves DSPy program instructions and Flex source code; it does not train
model weights or rewrite Fleet's host runtime. Fleet uses native `dspy.GEPA` and
its built-in proposers. Serving models execute candidates, the judge scores
answers, and the reflection model proposes revisions.

```text
Selected immutable replays + approved gold labels
                       │
                       ▼
             Split by source group
                       │
             Train / dev GEPA search
                       │
               Selected candidate
                       │
        Three baseline/candidate validation passes
                  on dev + holdout
                       │
              Save report and candidate
                       │
          Passing gate + explicit approval
                       │
              Serve from the next Turn
```

The curated dataset needs at least eight approved examples from three source
groups. Replay capture must have been selected for the original Turn; labeling
a trace afterward cannot reconstruct missing workspace inputs. Critical source
groups are reserved for holdout. Benchmark-labeled traces are excluded from the
curated optimization dataset.

After collecting and reviewing eligible examples:

```bash
uv run fleet eval label TRACE_ID --answer "Reviewed answer" --value "42"
uv run fleet optimize --session research-1 --gepa-budget light
```

Use `--expectations-file` when labeling artifact/delegation requirements, and
`--critical` with deterministic expectations for critical holdout cases. The
[HTTP API reference](docs/reference/http-api.md) describes replay capture; the
[evaluation runbook](docs/runbooks/evaluation-canary.md) covers live collection.

Search uses train/dev examples only. Each replay gets a fresh disposable runtime
restored from its immutable snapshot, using current deployment settings and
Session overrides. After search, baseline and candidate each receive three
validation passes with serving response caches disabled.

Promotion requires improved mean dev score, no holdout score or aggregate
quality regression, measured usage, no candidate holdout errors, passing critical
holdout cases in every pass, and changed instructions or Flex code. A dataset
without critical holdout cases cannot produce an approvable candidate.

A passing candidate is saved but remains inactive until explicitly approved:

```bash
uv run fleet optimize --session research-1 --approve CANDIDATE_VERSION
```

Approval checks the candidate bytes and recorded incumbent, configuration, code,
and dependency identities. Stale evidence requires reevaluation.

`--max-metric-calls` can replace `--gepa-budget`; it is a search budget, not a
whole-run spending limit. Reflection, judging, retries, sandbox lifecycle, and
final validation incur additional work. Final validation alone runs
`6 × (dev examples + holdout examples)` Turn replays. Passing tests or a promotion
gate is not proof of general improvement beyond the evaluated workload.

## Operational boundaries

- The HTTP API has no caller authentication. It binds to loopback by default;
  `FLEET_ALLOW_NON_LOOPBACK_BIND=true` permits another address but adds no access
  control. Deploy behind a secured boundary.
- Host-enforced Turn budgets bound model calls, runtime, recursion depth, and
  child sandboxes. See [configuration](docs/reference/configuration.md) for the
  current defaults and file limits.
- Generated Python runs in Daytona. Delegation-tool restrictions are not a
  Python or network security policy; provider-side deployment policy governs
  execution isolation.
- Streaming events and MLflow traces expose execution activity, not a model's
  private chain-of-thought. Redaction is best-effort; captures, traces, and
  artifacts may contain sensitive data.
- Live Gateway, Daytona, database, and evaluation behavior requires validation
  with the actual deployment resources.

## Development and documentation

```bash
make install-dev
make check
```

`make check` runs non-live tests, lint, formatting, type checks, documentation
links, OpenAPI validation, and a package build. For documentation-only changes,
run `make check-docs` and `git diff --check`. Live evaluation and optimization
require external resources and are not part of the ordinary quality gate.

- [Documentation index](docs/index.md)
- [CLI reference](docs/reference/cli.md)
- [Configuration](docs/reference/configuration.md)
- [Evaluation](docs/reference/evaluation.md)
- [HTTP API](docs/reference/http-api.md) and [OpenAPI contract](openapi.yaml)
- [Database and persistence](docs/reference/database.md)
- [Architecture](ARCHITECTURE.md)
- [Contributing](CONTRIBUTING.md) and [agent instructions](AGENTS.md)
- DSPy 3.4.1: [RLM](https://dspy.ai/3.4.1/diving-deeper/rlm/),
  [Flex](https://dspy.ai/3.4.1/diving-deeper/flex/),
  [GEPA](https://dspy.ai/3.4.1/diving-deeper/gepa-in-depth/), and
  [metrics](https://dspy.ai/3.4.1/diving-deeper/metrics-and-evaluation/)

## License

[MIT](LICENSE).
