# Fleet RLM Lite architecture

Fleet RLM Lite is a small Python system. The current runtime implementation is
under `src/fleet_rlm/`; it is not the former multi-package backend or
TypeScript terminal application.

## Request path

```text
Rich CLI ──HTTP/SSE──> FastAPI server ──> DSPy engine
                              │                 │
                              │                 └── Daytona Python execution
                              └── PostgreSQL state, MLflow traces/evaluation
```

`cli.py` is the `fleet` entry point; `tui/` is the terminal client. `api/` owns the FastAPI
endpoints (sync `def` routes in FastAPI's threadpool; only the Turn stream is
async) and SSE streaming; `session.py` owns the Session runtime registry, and
`engine/turn.py` runs one Turn on a worker thread. A Session
runtime combines a Daytona sandbox manager, PostgreSQL Session harness, Skill
manager, and DSPy engine. `sandbox/manager.py` creates the per-session Daytona
workspace and runs Python there. `store/` holds every SQL statement over one
connection pool, plus the harness that commits Turns; workspace files stay on the Daytona Volume.
`engine/` holds the route vocabulary, every DSPy signature, the Turn program
(`programs.py`) and the child-agent tools (`children.py`); `optimize/` holds
datasets, scorers, the one `fleet_metric` and the GEPA runner (`fleet optimize`), and
the single compiled-program loader. External benchmark datasets and official verifiers
belong to development notebooks, with only shared API/receipt helpers in `scripts/`.
The runtime and optimization policies do not depend on individual benchmark families.
`skills.py` validates and synchronizes Skills. `config.py` defines the typed
`FleetSettings` configuration.

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
Eviction fences the runtime before releasing its Turn lock, so requests holding
stale references cannot use it. Lease renewal cannot publish a connection after closure begins.

Fleet ships bundled Skills in `src/fleet_rlm/bundled_skills/` (tabular-analysis,
long-context-search, delegation). Each Turn synchronizes them, then user Skills
from `.fleet/skills/`, into `/workspace/skills` on the Session Volume, so root and
child sandboxes can `list_skills`/`use_skill` them. A user Skill with the same
name replaces a bundled one. A changed bundled file replaces the session copy;
user Skills keep the conflict check. `.agents/skills/` is reserved for
developer-facing agent skills and is not a Fleet user-Skills directory.

With a TypeSafe Jev backend, the engine's `skill_selector` predictor
(`SkillRelevance`, a typed `relevant | not_relevant` Choice) judges each catalog
Skill after routing (never for direct answers) and appends up to three relevant
`SKILL.md` files to `skills_catalog` as `<preloaded_skill>` blocks. Selection
consumes one budget unit per Skill, failures fall back to the catalog, and
GEPA can tune the predictor like any other.

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
Context selection, file stat, and pre-execution revision checks calculate size
and SHA-256 remotely, with the existing 25 MB limit and path checks. They do not
download file contents just to obtain metadata.

`WorkspaceDataset` implements the public `dspy.SandboxSerializable` contract.
Native RLM serializes the metadata and reconstructs a `WorkspaceDatasetView`
once per invocation; the view persists across that invocation's REPL iterations.
It is a file-backed view, not an eager load of the whole dataset. Flex uses a small
`dspy.Type` wrapper so its native facade can restore the host handle before an
inner RLM runs the same four serialization hooks. The wrapper avoids a collision
between the existing dataset `description` field and DSPy's `Type.description()`.
The public dataset constructor and persisted four-field descriptor remain unchanged.

DSPy 3.4.1's facade (`SandboxLM`, `SHIM_SETUP`), callback call identity, usage
tracker, and GEPA proposer/component enumeration remain version-sensitive
integration points without equivalent top-level exports. They are covered by
native contract tests; upgrades must revalidate them. Public exports are used
for serialization, interpreter results/errors, parsing errors, and usage scopes.

DSPy's in-REPL facade constructs host-mediated predictors and agents. Fresh
nested RLM interpreters allocate real child sandboxes, linked to the session
root by default but mounted to distinct `agents/{session}/{agent}` Volume
subpaths. Only input files and Skills are copied; artifacts are promoted with
agent provenance. Namespaces alone are not filesystem isolation. The host
enforces depth, active/total children, call budget, and a remote-stop deadline.
Deletion waits for provider confirmation; ambiguous creation is not blindly retried.
Local allocation journals support expired-allocation reconciliation on the next
root provisioning for that session; provider TTL covers loss of local journals.

The explicit `spawn_agent` host tool accepts a subtask, selected `files/`,
`skills/` or earlier `artifacts/` input paths, approved delegation tools, and
bounded DSPy iteration/query limits. It always copies the Session's `skills/` and
otherwise only the listed inputs (none by default), stages child outputs until success, and returns bounded findings plus promoted paths,
digests, and agent IDs. Copied-in artifacts that the child leaves unchanged are
not promoted back as its output.
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
Their `workspace_manifest` is rebuilt at the host bridge from permitted child
inputs, so it does not advertise root-only artifacts. Explicit factory inputs
remain selected, with ambient Skills included; host-only predictors keep their
original inputs.

`DaytonaInterpreter` exposes `output_fields`, which DSPy's RLM fills per run, so
the sandbox `SUBMIT` takes the signature's output fields positionally or by
keyword (not both), as DSPy's own interpreters do. The host also rejects a
`SUBMIT` whose figures appear as literals in the submitting code but were never
shown by an execution or input (compared at the typed precision); the model gets
the rejection as iteration feedback. Figures computed from variables pass. After
two rejections per namespace the output is accepted and flagged as
`ungrounded_submit` on the `sandbox.exec` span and as a stream event. The explicit
`learned_skill_note` field produces a nonblocking `learned_note_warning` instead;
all substantive fields retain the guard. Numeric matching is a heuristic, not
proof of provenance or formula correctness. Notes may be empty and remain
unreviewed hints; the instructions forbid using a note or memory value as the
known reference for a supporting check. Shared notes reach the memory summary
newest first, with near-duplicate summaries dropped and at most eight kept. Shared instructions request submission after one supporting
check, reuse of computed values, and exact executed syntax in notes; compliance
is evaluated model behavior, not a host-enforced completion guarantee.

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
runs only in Daytona.

The reflection LM is explicitly labeled when used as the metric judge; MLflow
judge URIs are never passed as DSPy model IDs. ReAnchor is currently reported
as skipped without a compatible calibration client. Passing unit tests is not
evidence of provider compatibility or a measured quality improvement.

## Turn termination, deadlines, and observability

Four contracts govern a Turn, in dependency order.

**Termination.** A Turn has one terminal outcome and one teardown. On failure,
cancellation, disconnect, or deadline expiry, the runtime sets the cancellation
flag, fences outstanding host calls, shuts down children, stops the root, records
`cleanup` (`stopped`, or `unconfirmed` when the provider stop did not confirm),
and only then emits the terminal event and `done`. Teardown is never deferred to a
background task. A successful Turn records `cleanup=not_required` and does not stop
the root: children were already shut down when their tool returned, and the warm
REPL is what the next Turn reuses. A failed or cancelled Turn discards the stopped
root, so the next Turn cold-resumes a new sandbox and loses interpreter variables.

**Fence.** Every bridged host call belongs to one execution identity. A successful
reply is published only while that identity is live; after cancellation it is
refused once with a bounded warning and no retry, so a late result cannot land in a
later resumed REPL. Cancellation and error replies are always delivered, so the
sandbox never waits out the host-call bound for a message that will not come. The
fence does not interrupt an in-flight provider request: that request finishes and
its result is discarded.

**Deadline.** One policy is resolved at Turn start and never changes mid-Turn:

| Field | Values |
| --- | --- |
| `source` | `request` \| `configuration` \| `default` |
| `effective_seconds` | positive integer, or `None` for no Fleet deadline |

Request `timeout_seconds` beats `turn_timeout_seconds`, which beats the shipped
default of `900`. Omission and `null` inherit; `0` disables the Fleet deadline.
Validation is a strict integer of exactly `0` or `10`–`3600`; booleans, floats, and
other text are rejected. The terminal `/timeout` command is client-local state that
the client sends as `TurnRequest.timeout_seconds`, so the server records
`source=request` and never a command source. An absolute `time.monotonic()`
deadline propagates to child managers and copied contexts; a child never receives a
later deadline than its parent. Deadline-free is not the shipped default and is not
"unbounded": Daytona's provider-owned `ttl_minutes` and `auto_stop_interval` still
apply, are reported on the Turn, and are never removed or shortened by Fleet. A
deadline-free Turn arms no watchdog and sends `run_code(timeout=0)` explicitly; no
Fleet component substitutes a fallback bound. Host-call waiting derives from the
remaining deadline, so a bridged call cannot outlive a bounded Turn.

**Environment.** The execution snapshot is a repository artifact defined in
`sandbox/snapshots.py`, not an environment or TOML setting. Preflight resolves the
repo-owned name before provisioning and refuses a missing, non-`active`, or
below-floor snapshot with the observed values and the build command. Child
snapshots are exempt from the Turn floors. The resolved name, provider id, cpu,
mem, disk, and definition digest are recorded on the Turn span, the stored Turn,
and new replay snapshots. Missing probed packages are a soft observation, not a
Turn failure. Removing the former snapshot setting intentionally changes
`configuration()`'s digest, so dependent GEPA approval evidence must be revalidated.

Observability labels are derived from existing events. No label owns a timer, a
polling loop, or a background task, and nothing in the label layer may act.

| Label | Derived from | Acts? |
| --- | --- | --- |
| `waiting_on_host` | between a bridged call's `tool_start` and `tool_end` | No |
| `progress_unknown` | no active progress scope in the current cell | No |
| `stalled` | an active scope declared an expectation and no advancing checkpoint arrived within it | No |
| `interrupted` | a cell ended from a provider interruption or partial-state result | No |
| `fenced` | a late host-call reply was refused | No |

A cell with no progress scope stays `progress_unknown` forever and never becomes
`stalled`. Progress markers are the lowest-priority stream event class: when the SSE
queue is full they are coalesced or dropped, while lifecycle transitions,
cancellation, and the terminal `done` remain deliverable. Generated code may use the
injected `progress_scope(label, expect_seconds=...)` helper and report strictly
increasing integer checkpoints; checkpoints are observation counts, never
percentages or completion claims.

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
- `skills.py`: Skill validation and bundled plus `.fleet/skills/` synchronization.
- `sandbox/`: Daytona sandbox lifecycle (`manager.py`), the DSPy interpreter
  (`interpreter.py`), the `run_code` transport (`repl.py`) and the sandbox-side
  runtime (`runtime/repl.py`). Snapshot definitions, preflight, and provenance live
  in `snapshots.py`.
- `engine/`: routes and signatures (`signatures.py`), the Turn program, and child agents.
- `store/`: PostgreSQL access and Turn commits.
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
is one `run_code` call, bounded by the remaining Turn deadline when one exists
(`timeout=0` for a deadline-free Turn). There is no in-sandbox server, token, or
execution registry.

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
Declared Python artifact verification also executes once and reports an unknown
outcome on transport failure; maintenance-call retries do not replay artifacts.

Each `sandbox.exec` span covers one `run_code` call through result interpretation,
with bounded redacted code/output, identity, host-call count, and outcome. Remote Python
errors mark that span as failed even when DSPy corrects the error and completes
the Turn. Overlapping outer Flex orchestration spans must not be added to child
execution durations. Root spans record the resolved mode and Turn deadline.
Child preparation, sandbox provisioning/resume, input copying, and namespace
preparation have separate lifecycle spans with lineage and numeric transfer
metadata. Child input transfer contains separate `sandbox.input_read` and
`sandbox.input_write` spans with source/target lineage and byte/file counts;
failures preserve cleanup and do not publish partial receipts. Turn preparation
separates context loading, Skill synchronization, workspace inputs, reviewed
memories, and catalog generation. Replay reads/capture, persistence, and shared
note writes also have dedicated spans. These record timing without capturing
file contents or credentials. These expose overhead without changing sandbox isolation or retries.
Per-call native DSPy usage scopes feed both MLflow LM spans and streamed usage;
their native rollup preserves module totals without summing nested spans twice.
Missing usage and monetary cost remain unknown.
Sandbox Skill helpers also return bounded invocation receipts on `sandbox.exec`,
including failed invocations. These make actual activation, resource reads,
script calls, and Skill saves visible to diagnostics without TypeSafe selection.
Receipts record helper/Skill names and return status, not bodies or script arguments;
they do not establish script exit status or instruction compliance.
TypeSafe decision groups use separate native usage scopes and annotate their
manual spans. DSPy 3.4.1 supplies input/output decision counts without totals;
Fleet derives a total only when both counts are reported and rolls the entries
into native module tracking once. Failed calls retain any reported usage.

The root trace and persisted Turn record contain `artifact_changes`, comparing
pre-Turn digests with bounded host-collected artifact evidence. Entries distinguish
created, modified, retained, and deleted content; incomplete evidence leaves
unobserved inputs unknown. Unchanged bytes cannot prove a new write. The public
`artifacts` list remains the Session inventory, including retained files.

The host records `lm_calls` as shared budget units, including routing and Skill
decisions, with a `budget_breakdown` (`generation_query`, `routing`,
`skill_selection`, `other_decisions`). `sandbox_errors` counts remote Python
failures; `submit_rejections` separately counts blocking host rejections. These
counters cover the whole child tree and accompany root outputs and persisted
completion events. The same host evidence drives efficiency scoring.

## Validated process configuration and decision evidence

`config/fleet.toml` schema 4 supplies non-secret defaults to `FleetSettings`.
Precedence is defaults, TOML, `.env`, environment, then explicit overrides.
`FLEET_CONFIG` selects another file through process environment only. Credentials
and the PostgreSQL DSN remain environment-only. The loader rejects legacy/unknown
keys and invalid files rather than accepting inactive settings. MLflow has separate local and Databricks profiles; command selection precedes
backend validation. `fleet cli` manages local tracking by default, while
`fleet cli databricks` selects managed MLflow with Unity Catalog tracing.
Tracking initialization must succeed before readiness. `/health` exposes its
non-secret destination so clients reject incompatible API reuse without
acquiring a Session. Configuration is
frozen at startup; persisted Session mode/model selections remain overrides.
API shutdown attempts registry cleanup, closure of its owned Store, and trace
flushing independently, even when an earlier cleanup raises. Injected Stores
remain the caller's responsibility; trace flushing is best effort.
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
