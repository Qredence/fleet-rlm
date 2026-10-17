# Fleet RLM Lite architecture

Fleet RLM Lite is a small Python system. The current runtime implementation is
under `src/fleet_rlm/`; it is not the former multi-package backend or
TypeScript terminal application.

## Request path

```text
Rich CLI ──HTTP/SSE──> FastAPI server ──> DSPy engine
                              │                 │
                              │                 └── Daytona Python execution
                              └── PostgreSQL VFS, MLflow traces/evaluation
```

`cli.py` is the `fleet` entry point; `tui/` is the terminal client. `api/` owns the FastAPI
endpoints (sync `def` routes in FastAPI's threadpool; only the Turn stream is
async) and SSE streaming; `session.py` owns the Session runtime registry, and
`engine/turn.py` runs one Turn on a worker thread. A Session
runtime combines a Daytona sandbox manager, PostgreSQL VFS harness, Skill
manager, and DSPy engine. `sandbox/manager.py` creates the per-session Daytona
workspace and runs Python there. `store/` holds every SQL statement over one
connection pool, plus the harness that mirrors the workspace and committed Turns.
`engine/` holds the route vocabulary, every DSPy signature, the Turn program
(`programs.py`) and the child-agent tools (`children.py`); `optimize/` holds
datasets, scorers, the one `fleet_metric` and the GEPA runner (`fleet optimize`), and
the single compiled-program loader. `skills.py` validates and synchronizes Skills. `config.py`
defines the typed `FleetSettings` configuration.

## State and workspace

PostgreSQL holds Session turn history and file content. The `VolumePostgresHarness` (in `store/db.py`)
creates the tables it needs at startup and synchronizes workspace data through
the Daytona Volume. Store schema changes use numbered additive migrations
recorded in a migration ledger. Startup applies pending migrations under a
transaction lock, using `FLEET_DATABASE_MIGRATION_URL` when configured and
otherwise the runtime DSN.
Daytona uses per-session subpaths for its workspaces. The supported workspace
areas include `files`, `artifacts`, `memory`, `skills`, `buffers`, and `sessions`.
Root sandboxes also mount a shared notes subpath at `/learned`
(`FLEET_SHARED_NOTES_SUBPATH`). Each committed Turn's learned note is written there as
an unreviewed, redacted Markdown file. Later Sessions see the newest summaries as hints
to verify. These notes are not Skills and are never synced to `.fleet/skills`.
Additive Lite tables retain reviewed source-backed memories, idempotent turn
intents/outcomes, and persisted mode/dataset settings without renaming old tables.
A PostgreSQL session advisory lock gives one worker ownership of a runtime and
is released on close or database disconnect. One connection is held per runtime.

User Skills are stored locally under `.fleet/skills/` and synchronized to the
session workspace. `.agents/skills/` is reserved for developer-facing agent
skills and is not a Fleet user-Skills directory.

## Reasoning, evaluation, and delegation

DSPy provides the reasoning engine and model calls. Daytona is the execution
boundary for generated Python. TypeSafe routing is optional and configured via
`FLEET_TYPESAFE_API_KEY`. MLflow is used for tracing/evaluation; GEPA optimization is
an explicit operation rather than an automatic CI or release action.

Strict `rlm` enters a primary DSPy 3.4.1 RLM; `flex` enters Flex. Hybrid triage
selects one route from the single vocabulary `direct` / `flex` / `rlm`
(`engine/signatures.py`), with `rlm` as the fallback on routing errors or unknown
labels. The triage label is the route that runs; there is no mapping step.
Dataset tasks cannot take a context-free direct answer path. Dataset handles
carry source digests and bounded previews; files stay in the sandbox.

DSPy's in-REPL facade constructs host-mediated predictors and agents. Fresh
nested RLM interpreters allocate real child sandboxes, linked to the session
root by default but mounted to distinct `agents/{session}/{agent}` Volume
subpaths. Only input files and Skills are copied; artifacts are promoted with
agent provenance. Namespaces alone are not filesystem isolation. The host
enforces depth, active/total children, call budget, and a remote-stop deadline.
Deletion waits for provider confirmation; ambiguous creation is not blindly retried.
Local allocation journals support expired-allocation reconciliation on the next
root provisioning for that session; provider TTL covers loss of local journals.

The explicit `spawn_agent` host tool accepts a subtask, selected `files/` or
`skills/` input paths, approved delegation tools, and bounded DSPy iteration/query
limits. It copies no input files by default, stages child outputs until success,
and returns bounded findings plus promoted paths, digests, and agent IDs.
`spawn_agents` runs several independent `spawn_agent` children concurrently on a bounded
thread pool (at most the active-child capacity), each with a copy of the caller's context,
and returns one result per subtask in order. A child that fails for ordinary reasons comes
back as `{findings: "", error, artifacts: []}`; deadline, cancellation, and shared-budget
exhaustion still abort the Turn. `FleetRLMChatAdapter` covers root Flex/RLM predictors,
spawned children, and host-mediated predictors. It accepts an unambiguous Python fence
for string-valued `reasoning`/`code` outputs, including one redundant closing fence.
An unparseable reply is re-asked once with field-format feedback, through the same
metered LM. Fleet disables DSPy's automatic JSON fallback, limiting each adapter
request to two model invocations (one when `parse_retries=0`). Provider errors,
deadlines, cancellation, and budget exhaustion propagate without format retries.
`allowed_tools=[]` disables further Fleet tool delegation; it is not a Python
or network sandbox policy. All generated Python still runs under Daytona's
deployment policy. Native facade-created RLMs retain the compatibility policy
of copying the current parent's `files/` and `skills/`, not its artifacts/memory.

The optimization runner curates approved gold answers, groups by source lineage,
and uses fresh disposable runtimes for each GEPA/evaluation example. Native DSPy
GEPA uses its built-in code proposer and the deployment's current settings,
including persisted Session overrides; manifests capture model configuration
provenance. Search uses train/dev data, with holdout evaluated only after search.
Candidate programs and manifests are separate from operator-approved active
pointers. Approval requires the current incumbent, configuration, code, and
dependency identities to match the candidate manifest. Older manifests require
reevaluation; approval does not provide an unchecked rollback path. Structured
artifact/delegation gold stays outside model inputs. Host-owned runtime receipts
are collected before sandbox disposal; declared Python artifact verification
runs only in Daytona. Governed acceptance adds experiment-registered scorers,
frozen UC datasets, judge controls, spend admission and durable paired-validation
receipts. A failed or budget-stopped campaign never authorizes replay of an
uncertain execution or installs a candidate. See the
[acceptance runbook](docs/runbooks/p55-acceptance.md).

The reflection LM is explicitly labeled when used as the metric judge; MLflow
judge URIs are never passed as DSPy model IDs. ReAnchor is currently reported
as skipped without a compatible calibration client. Passing unit tests is not
evidence of provider compatibility or a measured quality improvement.

## Deployment and trust boundaries

The HTTP API currently has no caller authentication. It binds to `127.0.0.1`
by default. A non-loopback listener requires the explicit
`FLEET_ALLOW_NON_LOOPBACK_BIND=true` setting, but that is not authentication.
Only expose it behind a separately secured boundary. Model-generated Python
is intended to execute in Daytona, not in the local server process.

Live Daytona behavior depends on valid API key, organization ID, Volume ID,
snapshot, network access, and provider-side policy. Documentation or local CI
does not establish that those external resources work or enforce a security
policy.

## Change ownership

- `config.py`: settings, defaults, validation, and model construction.
- `skills.py`: Skill validation and `.fleet/skills/` synchronization.
- `sandbox/`: Daytona sandbox lifecycle (`manager.py`), the DSPy interpreter
  (`interpreter.py`), the `run_code` transport (`repl.py`) and the sandbox-side
  runtime (`runtime/repl.py`).
- `engine/`: routes and signatures (`signatures.py`), the Turn program, and child agents.
- `store/`: PostgreSQL access and the workspace/Turn mirror.
- `optimize/`: datasets, scorers, the GEPA metric and runner, compiled-program artifacts.
- `observability.py`: redacted activity previews and MLflow tracing setup.
- `api/`: the FastAPI app (`app.py`), dependencies, request/response schemas,
  one router per area, and the Turn event stream (`events.py`).
- `session.py`: the per-Session runtime and its registry.
- `engine/turn.py`: one Turn end to end (claim, prepare, run, persist, report).
- `tui/`: terminal client (prompt loop, slash-command table, event rendering).
- `cli.py`: the `fleet` command-line entry point.

Keep documentation aligned with implemented routes and settings. Avoid
reintroducing claims about removed capabilities or a Node TUI,
or retired API surfaces.

## Execution transport and recovery

The DSPy interpreter remains synchronous and stateful. Each Fleet namespace is
one Daytona `code_interpreter` context (its own worker process), prepared once
with the Skill and workspace helpers and `sandbox/runtime/repl.py`. An execution
is one `run_code` call, bounded by the remaining Turn deadline. There is no
in-sandbox server, token, or execution registry.

The sandbox reports results and host-tool calls as marker lines on stdout.
Daytona sends every stdout write as its own websocket message and drops the
connection on bursts (about 2,000 writes per second), so user output is captured
per execution, bounded, and returned in one `FLEET_RESULT` line. Each host call
is one locked `FLEET_CALL` line; the host runs it on its own thread, so the
websocket keeps reading, and replies with `fs.upload_file`, which the sandbox
polls for.

A failed transport makes the outcome unknown: the Turn aborts, the root sandbox
is stopped, and code is never replayed. A deadline interrupts the running cell
and leaves the context usable; deleting a context ends anything it was running.

Each `sandbox.exec` span covers one `run_code` call through result interpretation,
with bounded redacted code/output, identity, host-call count, and outcome. Remote Python
errors mark that span as failed even when DSPy corrects the error and completes
the Turn. Overlapping outer Flex orchestration spans must not be added to child
execution durations. Root spans record the resolved mode and Turn deadline.

## Validated process configuration and decision evidence

`config/fleet.toml` schema 3 supplies non-secret defaults to `FleetSettings`.
Precedence is defaults, TOML, `.env`, environment, then explicit overrides.
`FLEET_CONFIG` selects another file through process environment only. Credentials
and the PostgreSQL DSN remain environment-only. The loader rejects legacy/unknown
keys and invalid files rather than accepting inactive settings. Configuration is
frozen at startup; persisted Session mode/model selections remain overrides.
`/config` reports effective safe values, sources and digest. A general editor and
hot reload are not implemented. Root traces record the effective configuration.

Invocation-injected DSPy query tools retain their local budget; Fleet also meters
the global Turn budget. Native predictor calls own their child lifecycle:
namespace shutdown is not evidence of successful completion, and only successful
invocations promote staged artifacts. Explicit Fleet spawn tools keep their own
success-only promotion. Native sub-agents provide structured DSPy tasks; explicit
spawn tools provide selected inputs, parallel investigations and artifact receipts.

Routing and batched Jev results preserve available probabilities/confidence,
backend and model. Routing traces distinguish raw choice, effective route and
policy override. Missing evidence is unavailable, and generative confidence is
self-reported, not demonstrated calibration. ReAnchor and reflection-adapter
changes remain explicit future evaluation work.
