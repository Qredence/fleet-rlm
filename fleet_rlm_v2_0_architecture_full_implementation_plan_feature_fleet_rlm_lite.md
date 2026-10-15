# Fleet RLM v2.0: Final Architecture and Implementation Plan

> **Status:** core implemented and exercised in an Orb; full-plan acceptance is
> not yet signed off. Section 17 distinguishes evidence from remaining work.
> **Baseline:** Fleet RLM Lite in `src/fleet_rlm/`; DSPy **3.4.1**, Daytona SDK
> **0.224.0**, FastAPI **0.142.2**, MLflow **3.17.1**, PostgreSQL, and a Python
> Rich/Prompt Toolkit terminal client. DSPy 3.4.1 is required.
>
> This document supersedes the original copy-paste blueprint. It preserves its
> product scope while correcting interpreter contracts, session ownership,
> recursive sandbox provisioning, persistence, and optimization safety. Implement
> it incrementally against the existing code, not by replacing working modules
> with the previous sample implementations.

## 1. Outcome and capabilities

Fleet RLM will be a **Recursive Agentic System** that can inspect large datasets
outside the prompt, plan and execute complex investigations, construct DSPy
sub-agents from inside its Python REPL, delegate work into actual Daytona child
sandboxes, retain evidence-backed memory between sessions, and improve reusable
workflows through measured, reversible optimization.

| Capability | User-visible contract |
| --- | --- |
| Large-context analysis | Attach data larger than the model context window; inspect, filter, aggregate, and select evidence in Python without embedding the complete dataset in a prompt. |
| Recursive reasoning | A root `dspy.RLM` can decompose a task, build `Predict`, `ChainOfThought`, `ReActV2`, and nested `RLM` agents through DSPy 3.4.1's facade, and combine their verified results. |
| Real sandbox delegation | An agent can request a fresh Daytona sandbox for a child investigation, whose descendants can also request fresh sandboxes under the same host-enforced policy. A Python namespace alone is never described as sandbox isolation. |
| Flexible execution | Strict RLM, strict Flex, and hybrid routing are explicit modes. Strict RLM always enters the primary RLM; hybrid can use a direct answer or a Flex workflow when appropriate. |
| Workspace and artifacts | Inspect and manage files, buffers, reports, memories, and Skills through the CLI, API, and in-REPL workspace helper. Downloads refer to actual persisted files. |
| Long memory | Resume sessions and retrieve bounded, relevant prior findings with sources, revisions, and scope; distinguish remembered claims from newly verified evidence. |
| Reusable Skills | Discover Skills progressively, run their scripts in Daytona, create candidate Skills, and explicitly synchronize reviewed Skills. |
| Observability | Follow routing, generated code, outputs, sub-agent/sandbox lifecycle, resource consumption, file sync, and trace links in a live terminal. |
| Self-improvement | Curate reviewed examples, optimize instructions and Flex programs with GEPA, optionally calibrate routing with ReAnchor, evaluate on an untouched holdout, and explicitly promote or roll back versioned candidates. |

These are bounded capabilities, not promises of unlimited context, unlimited
recursion, guaranteed correctness, automatic model-weight training, or guaranteed
quality improvement. Large files still consume storage, I/O, and sandbox memory.
Verification and provenance reduce errors; they do not eliminate them.

## 2. Baseline to preserve

Read [ARCHITECTURE.md](ARCHITECTURE.md) before implementation. The existing runtime
already provides session-scoped managers, per-session Daytona volume subpaths,
an authenticated host-tool bridge, namespace locking, bounded transfers,
PostgreSQL file/turn persistence, validated progressive-disclosure Skills,
session turn locks, MLflow tracing, and an explicit GEPA evaluation script.

The current engine uses triage, a direct answer path, Flex, and a bounded child
investigation tool. It does **not** yet establish the full primary RLM,
workspace-dataset, memory-retrieval, recursive sandbox-tree, or terminal contracts
specified below. Keep existing functionality working while introducing them.

Preserve these decisions:

- Python 3.11–3.13 and uv; no Node terminal application or legacy package revival.
- Native DSPy model construction using the repository's `openai/<service-id>`
  Unity Gateway routes and `engine="lm15"`. Do not introduce a direct LiteLLM
  integration or replace working gateway routing with old example identifiers.
- Separate root, sub-agent, reflection, judge, and optional TypeSafe model roles.
  Sonnet 5.5/Haiku 5-5 may be configured using actual available service IDs;
  names in an example do not establish endpoint availability. MLflow judge URIs
  are a separate contract from DSPy service identifiers.
- Local MLflow remains supported for development; Databricks-managed tracking,
  Unity Catalog trace storage, and registry integration remain deployment options.
- User Skills live in `.fleet/skills/`, never `.agents/skills/`.
- Retain existing `fleet_lite_sessions` and `fleet_lite_vfs_files` data. Any schema
  additions require an additive, tested upgrade path, not silent table renaming.
- No destructive directory cleanup, version bump, production migration, or
  remote deployment is implied by this plan.

## 3. Ownership and trust boundaries

```text
Rich / Prompt Toolkit client
            | HTTP + native SSE
FastAPI session registry and turn lifecycle
            | session ownership, locks, cancellation, events
AdaptiveFleetEngine: primary RLM / Flex / hybrid
            | fresh interpreter factories and host-mediated tools
DaytonaSandboxManager: session root + allocated child sandboxes
            | isolated workspaces and explicit artifact promotion
PostgreSQL durable VFS / memory       MLflow traces / evaluation / candidates
```

The host owns model credentials, provider credentials, allocation policy, tool
dispatch, budgets, persistence, and artifact promotion. Generated Python executes
in Daytona. DSPy's facade requests predictor construction and execution on the
host; it is not permission to execute arbitrary generated host Python.

Every turn has a session ID, turn ID, cancellation scope, event stream, runtime
configuration snapshot, and resource ledger. Every agent has an agent ID,
logical parent ID, depth, interpreter/context ID, sandbox ID, and permissions.
Provider linkage and logical parentage are recorded separately.

Session IDs are not authentication. Keep loopback-by-default deployment. Remote
exposure requires an authenticated boundary and principal-to-session authorization
for files, memory, turns, events, trace access, and optimization. Do not describe
session isolation as sufficient for a hostile multi-tenant public service.

## 4. Engine modes and model contracts

### 4.1 Primary RLM and hybrid composition

Introduce `FLEET_EXECUTION_MODE=rlm|flex|hybrid` and session-scoped mode overrides.
Use `hybrid` as the compatibility-oriented default; `rlm` is the strict recursive
mode. Persist the selected mode and emit the actual route on every turn.

- **rlm:** invoke a first-class primary `dspy.RLM`, not Flex with a hopeful prompt
  asking it to use an RLM. Allow deterministic code, Skills, DSPy sub-agents, and
  sandbox delegation. Never silently route to direct CoT.
- **flex:** invoke `dspy.Flex` with the approved toolset and interpreter factory.
  Its baseline and evolved decomposition may differ; do not claim Flex inherently
  guarantees a particular recursive architecture.
- **hybrid:** triage between `direct_cot`, `flex_pipeline`, and `deep_rlm`. Missing
  or failed optional triage falls back to bounded primary RLM and emits a reason.
  Dataset-dependent tasks cannot bypass evidence inspection through direct CoT.

The task signature includes `context: Any`, task, bounded history, memory evidence,
workspace manifest, and Skill catalog. Output includes the answer, artifact
references, and memory/Skill proposals. A nonempty `learned_skill_note` is not
proof that a valid Skill was created or tested.

### 4.2 TypeSafe and calibration

Import `TypeSafe` from `dspy.experimental`. Use native `Literal` route outputs
with field descriptions, or documented decision types where they add value.
TypeSafe is a constrained decision backend, not the free-form reasoning model.
Keep ordinary typed routing when optional TypeSafe is not configured; report a
configuration error rather than silently ignoring explicitly requested support.

Use context-local DSPy configuration and explicit role bindings. Sub-agents
inherit the approved host backend through DSPy's facade; model-generated code
cannot supply credentials, API bases, or alternate routing. Fleet chooses any
supported role assignment at the host boundary, not via an unrestricted facade
override. Confirm nested behavior with the pinned DSPy release in phase 1.

ReAnchor is an **offline, optional routing calibration step** for supported
numeric thresholds, cuts, and weights. It does not calibrate arbitrary RLM prose.
Use an explicitly configured compatible cached calibration client, separate from
uncached serving clients. Record applied/skipped/failed status and why; never
swallow all calibration exceptions or claim calibration happened when it did not.

## 5. DSPy 3.4.1 interpreter and in-REPL sub-agent contract

### 5.1 Interpreter behavior

The interpreter must satisfy the pinned DSPy RLM and Flex contracts:

1. A zero-argument factory returns a fresh interpreter namespace per invocation,
   including nested RLMs. Namespace ownership and sandbox ownership are separate.
2. `execute(code, variables=...)` applies **every** variables update. DSPy installs
   facade state before uploading task inputs; an upload-once flag loses context.
3. Preserve supported JSON values and `SandboxSerializable` values; reject
   unsupported values clearly. Transfer large payloads through filesystem APIs,
   not shell arguments, command-line literals, or unsafe deserialization.
4. Preserve state across iterations in one namespace; evaluate the final Python
   expression as required by Flex, capture stdout/stderr separately, and propagate
   structured exceptions and DSPy's `FinalOutput`/submission semantics.
5. Honor DSPy's dynamically registered interpreter tools, facade construction and
   calls, and supported query/batch tools. Do not freeze the tool registry before
   DSPy installs its facade.
6. Dispatch bridge calls by session, turn, namespace, and authenticated capability.
   Use manager-owned dispatch rather than competing per-interpreter pollers.
7. Serialize execution within a namespace. Concurrent namespaces must not share
   mutable globals, stdout capture, event listeners, or per-turn budgets.
8. Shutdown releases owned namespaces and bridge resources exactly once. Shutting
   down a child interpreter must not delete a shared session root sandbox.

Assess Daytona's native `code_interpreter.create_context/run_code/delete_context`
against these requirements, including expression values, tool callbacks,
serialization, cancellation, and facade setup. Prefer it only if the full contract
works. A native execution API does not by itself provide the reverse host-tool
bridge. Retain the authenticated daemon where needed instead of rewriting it
solely for API uniformity. Python contexts isolate variables, not filesystems.

### 5.2 DSPy-built agents are a required feature

Support model-generated REPL code using DSPy 3.4.1's supported facade to construct
and invoke `dspy.Predict`, `dspy.ChainOfThought`, `dspy.ReActV2`, and nested
`dspy.RLM`. Validate their exact supported constructors/tools against the pinned
release, including batched calls and structured results. Do not substitute a
single fixed `investigate` prompt for this capability.

Keep the logical recursion path distinct from sandbox provisioning:

- Lightweight predictors may run through the facade without a new sandbox.
- Nested RLMs always obtain fresh interpreter state. Fleet's default allocation
  policy places nested RLM execution in a fresh child sandbox; same-sandbox
  namespaces are an explicit cheaper trusted-workload policy, not equivalent
  isolation. Verify that the facade's host factory can enforce this allocation.
- Expose a documented Fleet host tool, provisionally `spawn_agent`, for an explicit
  child task, approved input references, allowed tools, and resource slice. It
  invokes a host-owned DSPy agent in a newly allocated sandbox and returns a
  bounded result plus verified artifact references. It does not accept arbitrary
  host source code or arbitrary sandbox/provider parameters.
- Children receive the same bounded delegation capability, enabling genuine
  multi-level recursion. Spawn/join or batched delegation uses bounded concurrency;
  release host dispatch locks before waiting on descendants to avoid deadlocks.

Phase 1 must demonstrate that facade-created nested RLMs reach the allocation
policy, rather than merely documenting the explicit tool. If DSPy's public hook
is insufficient, isolate and test the smallest version-pinned integration at the
interpreter/factory boundary; do not fork DSPy or claim transparent support until
the nested allocation test passes.

## 6. Daytona sandboxes, child sandboxes, and lifecycle

### 6.1 Concrete placement policy

Distinguish four things throughout code, UI, and documentation:

| Mechanism | Meaning and policy |
| --- | --- |
| DSPy logical child | A predictor/agent invocation, with parentage and accounting; not automatically a new computer. |
| Interpreter namespace/context | Fresh Python variables; not a filesystem or security boundary. |
| Independent Daytona child | A real separately provisioned sandbox whose logical parent is recorded by Fleet; the host enforces lifecycle and cleanup. |
| Daytona linked child | A provider-linked, ephemeral sandbox sharing the parent's link network and cascading deletion lifecycle. |

Use a per-session root sandbox. For each allocated child, prefer a linked child
of that **session root**, where enabled and verified. Pass
`linked_sandbox=root.id`, `ephemeral=True`, and explicitly
`auto_delete_interval=0`. Daytona documents the zero delete interval as required;
do not rely on a contradictory/minimal example that omits it.

Logical grandchildren can also be separate linked children of the session root:
logical agent topology need not equal provider linkage topology. The reviewed
documentation establishes one-to-many linked children, not arbitrary nesting of
linked parents. Do not promise nested provider linkage. When linkage is disabled
or unavailable, explicitly configured independent sandboxes still support the
full logical recursion tree; record the placement. Do not silently downgrade a
required linked/network policy.

Linked children have separate compute/filesystems but share a local network.
They are not network-isolated hostile tenants. Authenticate every bridge endpoint,
scope credentials to its agent/turn, and enforce input/tool restrictions at the
host. Nested virtualization and VM forks are different Daytona features and are
not prerequisites for recursive agents or part of this container-based baseline.

### 6.2 Data and privileges

- Keep Daytona, Databricks/model, PostgreSQL, MLflow, and registry credentials on
  the host. Generated code requests approved operations through host tools.
- Root volume mount uses a session-specific subpath. Child mounts, if used, use
  distinct agent subpaths. Do not mount the writable parent workspace or all
  sessions into a child. Never rely only on helper path checks to isolate code
  that can directly access its mounted filesystem.
- Copy only approved, content-addressed inputs into the child, or mount an
  appropriately restricted input location when the provider actually enforces
  the required access. Do not assume volume mounts are read-only.
- Child outputs stay in a staging workspace. Promote explicitly selected regular
  files after containment, type, size, digest, and ownership checks. A child may
  propose memory/Skills but cannot overwrite parent state or compiled programs.
- Bound returned text and manifests; keep large results as artifact references.
  Preserve source hashes and child identity in promoted artifact provenance.
- Restrict network egress through supported deployment policy; record what was
  actually enforced. Do not claim isolation stronger than provider guarantees.

### 6.3 Provisioning, cancellation, and recovery

Implement an owned allocation lifecycle: requested → provisioning → ready →
running → collecting → deleting → deleted, with failure and cancellation states.
Reserve resources atomically before provision; release reservations exactly once.
Record allocation intent, unique labels, provider ID, lease/deadline, and logical
parentage durably enough to reconcile a host crash or a lost create response.
Discover matching owned allocations before retrying an ambiguous create.

Delete ephemeral children after success, failure, timeout, cancellation, or parent
termination, after collecting authorized outputs. Track delete completion rather
than equating accepted deletion with released provider resources. Reconcile owned
orphans on startup; never delete unrelated sandboxes. Keep cleanup failures visible
and retryable. Provider cascade deletion is a backstop, not the only cleanup path.

Linked children cannot be persisted or resumed after stop. Checkpoint artifacts
before cleanup. Configure bounded auto-stop/activity policy and wall-clock TTL;
background code alone does not keep Daytona active. A Fleet deadline and TTL
must not be indefinitely extended by agent code.

Stopping/restarting a root loses Python process state. Recreate interpreter and
bridge state and rehydrate durable files/memory; do not advertise process-memory
resume or blindly replay side-effecting code after uncertain completion.

## 7. Tree-wide budgets and scheduling

Use DSPy's native RLM limits for query tools and facade sub-agent completions.
Add a Fleet turn ledger across **all** root and descendant sandboxes for the
resources that DSPy's native accounting does not cover. Do not give each child
a fresh full turn allowance or double-charge the same completion.

Enforce finite, validated limits for LM calls, tokens where observable, estimated
cost, recursion depth, total/active children, execution iterations, wall time,
output bytes, input transfer bytes, persisted bytes, and provider resources.
Initial development policy: 150 total LM calls, depth 3 with root at depth 0,
8 total child allocations, 4 simultaneously active child sandboxes, and a
15-minute turn deadline. These are tunable policy defaults, not measured capacity
or quality claims; establish file/token/cost caps from deployment constraints.

Routing, root, sub-agent, reflection, and judge usage have separate categories.
Serving counts routing/root/sub-agent work against the turn allowance;
optimization/judging uses a separately authorized job budget. Reservations for
parallel requests are atomic. Report unavailable token/cost data as unknown, not
zero. Check limits before work and settle actual usage after completion.

Cancellation propagates down the logical tree. Count every live child sandbox,
including a parent waiting for descendants, against provider capacity. Separate
host execution permits from sandbox reservations; releasing a host permit does
not free a live sandbox. Reserve descendant capacity before admitting recursive
work, or reject a spawn that would create a capacity wait cycle. Never stop an
ephemeral linked parent merely to free a slot and pretend its state can resume.
Bound queue wait by the turn deadline; after a limit, emit a structured exhaustion
result and stop new work while preserving already verified outputs. Test both
exhausted and remaining-budget branches with concurrent children.

## 8. Large context and first-class workspace

### 8.1 WorkspaceDataset

Implement `WorkspaceDataset` using DSPy 3.4.1's `SandboxSerializable` hooks:
`sandbox_setup`, `to_sandbox`, `sandbox_assignment`, and `rlm_preview`, with the
exact signatures established by contract tests. Store a session-authorized path,
description, size, content digest/revision, and media/schema hints. Preview only
bounded metadata, not the full document.

Resolve handles on the host before sandbox serialization and again against the
allocated workspace. A model-supplied path is not authorization. A child sees
its materialized approved path, not a parent host path. Verify the digest before
analysis to detect changed inputs.

Provide bounded preview, chunk iteration, text search, structured loading where
appropriate, and slicing/aggregation through Python. Small contexts can use
ordinary variables; large inputs use uploaded files. Do not promise zero-copy
transport, infinite RAM, or eager whole-file hydration for every dataset.
Include a >10 MB persistence case and an authorized analysis case whose input
exceeds the chosen model's context window.

### 8.2 Workspace API and persistence

Expose consistent helpers for `tree`, stat, bounded text/binary reads, writes,
append, diff, delete, buffers, artifact creation, memory proposals, and dataset
handles. Standard areas: `files/`, `artifacts/`, `memory/`, `skills/`, `buffers/`,
and `sessions/`. Session mount boundaries enforce ownership; helpers enforce
path rules and quotas within that boundary.

Reject absolute user paths, `..`, sibling-prefix escapes, symlinks, special files,
and unauthorized cross-area writes. Use resolved containment, not string-prefix
comparison. Avoid check/use races through exclusive mutation and safe filesystem
operations. Never interpolate filenames/content into a shell command.

Stream uploads/downloads with validated names, byte limits, and content types;
use Daytona filesystem APIs and temporary files with guaranteed cleanup. Raise
the existing 5 MB upload ceiling only with corresponding transfer, storage,
hydration, and test coverage. Do not silently drop files above an internal cap.

Synchronize changed file bytes and SHA-256/revision metadata to PostgreSQL,
including deletions/tombstones. A partial traversal or failed transfer must not
mark unvisited files deleted. Commit a turn record and its completed persistence
status transactionally; expose failures and make retries idempotent. Use bounded
batches and explicit total quotas, not unbounded recursive scans or hidden skips.

The Daytona volume is a working layer; PostgreSQL is the durable recovery layer.
FUSE volume writes are not a transaction or cross-sandbox lock. Enforce one
workspace writer per session and staged child promotion; extend existing turn
locking with cross-process ownership/leases before supporting multiple workers.
Checkpoint long turns at safe boundaries. Hydration must not overwrite a newer
live workspace with stale database content.

## 9. Persistent memory and Skills

Retain turn history and add structured memory records with ID, session/project
scope, type, content, source artifact/turn references, source hashes, creation and
update times, confidence/status, and supersession links. Separate observations,
user preferences, unresolved hypotheses, and reusable procedures.

Begin with bounded PostgreSQL metadata/full-text retrieval and recency/relevance
ranking; embeddings are not required for the first implementation. Retrieve only
authorized relevant memories within a prompt budget. Surface contradictions and
stale source revisions. A remembered number must not become current evidence
without validating its source. Support explicit review, correction, deletion,
and session resume; shared/project memory is opt-in and separately authorized.

Keep three-stage Skill disclosure: catalog metadata → selected instructions →
references/scripts on demand. Validate names, YAML/frontmatter, paths, bounded
content, and script execution policy. Execute scripts only in Daytona. A new
Skill is a candidate until its workflow has been exercised and artifacts checked.
Provide explicit push/pull with conflict reporting; avoid overwriting locally
edited Skills. Approved shared Skills and session-created Skills have different
ownership. Never automatically sync developer agent skills.

## 10. FastAPI, SSE, and terminal experience

### 10.1 API and lifecycle

Use FastAPI lifespan for runtime initialization and cleanup. Keep lazy per-session
allocation; health, API schema, and ordinary CI must work without provider keys.
Offload blocking DSPy/Daytona/database operations from the async event loop.

Use native `fastapi.sse.EventSourceResponse` and `ServerSentEvent`; preserve the
POST turn-stream contract. Rely on documented ping/no-cache/no-buffer behavior
and test deployment proxy behavior. Use bounded per-turn queues, thread-safe
producer handoff, monotonic event sequence IDs, and one terminal outcome event.
Never let slow viewers grow memory without bounds. Coalesce/truncate explicitly
marked output chunks; never silently discard lifecycle/error/terminal events.

On disconnect, cancel the turn and descendants and close listeners/resources.
Reconnection can fetch persisted status/history, but must not implicitly rerun
the turn. Define turn idempotency keys and distinguish running, completed,
cancelled, failed, and persistence-incomplete outcomes. Python thread cancellation
alone does not stop remote execution; enforce remote deadlines and cleanup.

Retain existing routes. Add session-scoped contracts for mode read/update,
approved program reload, history, workspace tree/stat/read/write/delete/download,
context selection, memory review, Skill push/pull, and turn/agent status. Define
exact methods/request schemas in OpenAPI during implementation; avoid documenting
proposed endpoints as current ones. Mutations serialize with turns; mode/reload
changes take effect at a turn boundary. Read-only health/listing never boots a
sandbox. Promotion/optimization must not be an unauthenticated arbitrary job API.

### 10.2 Events

Retain current compatible events and introduce a versioned envelope containing
session/turn/agent/parent/sandbox IDs, sequence, timestamp, and payload. Support:

- `triage_decision` with mode, route, fallback reason, and timing.
- `repl_iter_start`, `repl_code`, `repl_output` with bounded stdout/stderr/errors.
- `bridge_call` plus compatible tool/module events; redact secrets and bound inputs.
- `agent_spawn`, `agent_result`, and sandbox provisioning/ready/cleanup/failure.
- `budget_update` and structured limit/cancellation events.
- `vfs_sync` with changed/deleted files, bytes, revision, and persistence status.
- `mlflow_trace` with an actual captured trace ID/link, or unavailable status.
- Exactly one terminal result: completion, failure, or cancellation.

Generated code and evidence are observable. Do not claim to expose private model
chain-of-thought; redact credentials, sensitive payloads, and hidden model reasoning.

### 10.3 Rich / Prompt Toolkit client

Add Prompt Toolkit only for interactive editing/history/completion while keeping
Rich rendering and noninteractive use. Provide panels for route, active agent
tree, code/output, budget, files, and trace status. Retain readable narrow-terminal
and redirected-output modes. Output from background events must not corrupt input.

Preserve `/attach`, `/files`, `/artifacts`, `/skills`, `/sessions`, `/optimize`, and
`/exit`. Add `/tree`, `/cat`, `/put`, `/get`, `/context`, `/mode`, `/history`,
`/memory`, `/trace`, `/reload`, and `/skills push|pull` using the same API contracts.
Free-form task/query submission and rendering live events are required, not just
slash commands. Include `/config` and `/settings` for non-secret session settings,
`/models [catalog.schema]` for authorized chat-model discovery, and `/model
[root|sub] [model-id|catalog.schema]` for interactive or explicit selection.
Validate and persist session model changes at turn boundaries; preserve mode and
dataset settings. Exercise completion, model/configuration flows, live progress,
malformed/error/disconnected streams, and cancellation. Operator secrets and
deployment resource ceilings remain server-owned.
Optimization requires an explicit job budget and reviewed dataset; invoking it
does not automatically approve program promotion or shared writes.

Verify the terminal through a pseudo-terminal with realistic concurrent output,
Ctrl-C, resize, narrow width, failures, nested delegation, and reconnect/status
recovery. Inspect rendered terminal captures for visual changes. No new web UI
is required by this plan.

## 11. MLflow, evaluation, and self-improvement

### 11.1 Observability and deployment configuration

Keep MLflow 3.17.1. Configure local versus Databricks tracking explicitly, including
experiment path, registry URI where used, Unity Catalog trace location, and SQL
warehouse configuration required by the deployed environment. Validate relevant
permissions and APIs; a local client pin does not establish managed-service
feature availability. Add the Databricks dependency extra only if the exercised
configuration requires it.

Trace route, mode, runtime/program version, input hashes, dataset references,
agent tree, execution, tool calls, sandbox lifecycle, memory/Skill use, latency,
resource totals, and persistence status. Link child spans to the right turn across
worker boundaries; avoid duplicate autolog/manual spans and global session tags.
Tracing outages must not turn a correct answer into a failed serving request,
but evaluation requires sufficient evidence or reports an incomplete run.

Retain the seven-scorer suite: relevance, groundedness, safety, sandbox verification
discipline, Skill progressive disclosure, trajectory efficiency, and routing
efficiency. Add deterministic artifact correctness and isolation/budget checks
where appropriate. Evaluate efficiency conditional on correctness and task
complexity; do not reward skipping necessary evidence inspection. Missing traces
are unavailable evidence, not an automatic score of 1.0. Report metric direction,
sample counts, failures, uncertainty, and unavailable values honestly.

### 11.2 Dataset curation and GEPA

Fix the existing judge LM-binding issue: `gepa_mlflow_metric` must use an explicit
judge model/context rather than require an implicit global DSPy LM. Keep judge,
reflection, serving, and TypeSafe clients separate.

Curate only authorized session/project examples with a task, actual input/context
references and revisions, expected result or reviewed feedback, trace/provenance,
and applicable Skills. Production success is not a gold score of 1.0. Do not train
on missing-context answers, failed persistence, unreviewed generated Skills, or
leaked secrets. Seed examples are labeled as seeds, not claimed production data.

Split deterministically into training, development/calibration, and untouched
test sets, grouped by source/task/session lineage to prevent near-duplicate
leakage. GEPA and ReAnchor see only the permitted splits. Use the test holdout
once for a candidate decision; repeated tuning against it requires a new holdout.
Record hashes, split policy, random seeds, dependency versions, model settings,
and optimization budget for reproducibility.

Run each example/candidate in an isolated session workspace cloned from the
example's recorded inputs. Baseline and candidate must not share mutated files,
memory, Skills, interpreters, budgets, or callbacks. Threaded evaluation cannot
reuse one mutable engine/sandbox. Clean all evaluation sandboxes after each job.

Use GEPA's supported instruction optimization and Flex program evolution, with
execution-aware `program_trace` feedback where supported. Optimize RLM prompts
and Flex decomposition without allowing candidate programs to bypass strict mode,
authorization, budget, or sandbox policy. Generated programs are untrusted code;
validate supported serialized program formats, prohibit arbitrary pickle/import
loading, and keep generated execution in Daytona.

### 11.3 Evaluate before promotion

The workflow is: baseline evaluation → GEPA candidate → optional ReAnchor
calibration → isolated holdout evaluation → reviewable comparison → explicit
promotion. Store candidates separately from the active artifact throughout.

Require no isolation/security/budget violations, no deterministic golden-artifact
regression, and no quality regression beyond a tolerance chosen before the run.
Set deployment latency/cost ceilings before evaluation. Small or inconclusive
datasets cannot support a claim of improvement. Keep the baseline when criteria
fail, and report regressions rather than hiding missing metrics as zero.

Version each artifact with digest, dependency/configuration manifest, dataset
lineage, evaluation results, and calibration status. Promotion updates an approved
pointer atomically at a turn boundary; it never overwrites a running turn's
program. Verify integrity on reload, retain the previous version, and test
rollback. Promotion of shared/production programs requires explicit authorization.
This improves programs/workflows and memories, not underlying model weights.

## 12. Implementation sequence and code ownership

Do not create a prescribed number of files or replace the package wholesale.
Extract `engine.py` and `workspace_vfs.py` from `memory_and_eval.py` only at coherent
responsibility boundaries, preserving public imports where needed. Keep
`interpreter.py` responsible for remote execution/allocation, `server.py` for
session/turn orchestration, `skills.py` for Skills, and `config.py` for settings.
Add a separate module only when its ownership actually reduces complexity.

| Phase | Deliverable | Required exit evidence |
| --- | --- | --- |
| 0. Baseline and dependency upgrade | Pin `dspy[typesafe]==3.4.1`, refresh lock without unrelated upgrades, inventory existing behavior. | Installed version, lock consistency, full existing non-live quality gate. |
| 1. DSPy/Daytona contracts | Interpreter updates, final-expression and submission behavior, serialization, mutable tools, facade-built predictors and nested RLM, allocation-policy seam. | Tests reproduce old blueprint failures; real DSPy facade drives the adapter; nested RLM reaches a fresh sandbox allocation under policy. Provider behavior is separately live-gated. |
| 2. Context and workspace | Dataset handles, complete safe file API, large streaming transfer, bounded SHA-256 sync/deletion, recovery and additive schema changes. | Nontrivial large-file round trip, path/symlink rejection, partial-sync safety, restart recovery. |
| 3. Recursive sandbox fleet | Host-mediated spawn/join, child allocation, hierarchy, staged results, ledger, cancellation, orphan reconciliation. | Multi-level logical recursion allocates separate sandbox IDs; exhaustion, failure, crash, and cleanup cases pass. |
| 4. Engine and memory | Strict modes, hybrid triage, explicit role binding, scoped retrieval and provenance. | Strict RLM cannot bypass REPL; complex tasks use evidence; resume retrieves authorized relevant memory and surfaces stale/conflicting records. |
| 5. API and events | Native SSE, bounded queues, cancellation/idempotency, session APIs, mode/reload boundaries. | Concurrent sessions and slow/disconnected clients remain isolated; OpenAPI and terminal outcomes match the implementation. |
| 6. Terminal and Skills | Prompt Toolkit input, Rich panels/commands, safe Skill authoring and synchronization. | Pseudo-terminal scenario, inspected terminal captures, actual upload/download and Skill conflict handling. |
| 7. Evaluation and promotion | Explicit judge, reviewed reproducible data, isolated baseline/GEPA/ReAnchor/holdout, versioned promotion and rollback. | Known-bad candidate rejected; qualified candidate can be explicitly promoted and rolled back; calibration failures remain visible. |
| 8. Authorized full-stack acceptance | Live scenario below, actual provider settings, measured limits, docs/runbook. | Trace-linked evidence, persisted artifacts, real recursive sandbox IDs, cleanup confirmation, resume, and truthful comparison report. |

For every phase: update relevant docs and `.env.example` only for implemented
behavior; preserve aliases deliberately; regenerate OpenAPI when routes change;
run focused tests and then `make check`. Refactoring preserves behavior before
new behavior is introduced. Do not delete existing coverage to force green tests.

## 13. Verification matrix

Permanent tests cover observable contracts, not just code compilation or private
helper call order. Extend current test files before creating new suites. Use the
real pinned DSPy logic with deterministic fake LMs and an execution/provider
boundary double where possible; mocked provider success is not live validation.

| Area | Cases that must distinguish a correct implementation from a plausible wrong one |
| --- | --- |
| Interpreter | Facade token setup followed by later task variables; second variables update; expression result without print; stdout/stderr plus exception; submission; fresh nested namespace; dynamically registered tools. |
| In-REPL agents | Predict, CoT, ReActV2, nested RLM; inherited model policy; rejected credential overrides; facade budget exhaustion; nested RLM sandbox policy and multi-level spawn. |
| Isolation | Two concurrent sessions with different sentinel files, outputs, events, memory, and budgets; independent child writes; no parent credential exposure; wrong bridge capability rejected. |
| Dataset/paths | >10 MB file round trip; bounded preview without full content in root prompt; changed hash; sibling-prefix traversal; symlink escape; special files; oversized upload; interrupted transfer cleanup. |
| Persistence | Changed and deleted files; failed partial scan cannot delete remaining files; interrupted sync/retry; fresh root rehydration; lease conflict; existing schema/data retained. |
| Sandbox lifecycle | Create failure and ambiguous response; concurrency/depth/total limits; cancellation while provisioning/running/waiting; parent failure; delete completion and failure; startup orphan reconciliation; linked-child stop loses process state. |
| Routing/memory | Strict modes, triage failure fallback, context-dependent direct-path rejection, TypeSafe configured/unconfigured, source-scoped retrieval, stale/conflicting memory, resume. |
| API/SSE | Lazy health; concurrent sessions; ordered bounded stream; exactly one terminal event; slow client; disconnect cancels remote work; retry idempotency; no stale event listeners; locked reload. |
| Skills/client | Progressive disclosure, invalid paths/frontmatter, reviewed script execution, push/pull conflicts, input survives background events, Ctrl-C and narrow terminal. |
| Optimization | No global LM judge dependency, excluded unreviewed examples, grouped splits, isolated baseline/candidate state, explicit calibration skip/failure, missing metrics/traces, regressed candidate rejection, atomic promotion and rollback. |

Non-live checks: `uv lock --check`, focused `uv run pytest ...`, `make check`, and
`git diff --check`. Integration tests are separately marked and never run by
ordinary CI without resources and explicit authorization.

## 14. Authorized live acceptance scenario

Live Daytona, model, database, Databricks/MLflow, and optimization calls require
explicit authorization, supplied resources, and an agreed spend/time budget.
Until run, label provider behavior **not live-validated**, even if all unit tests
pass. Use a disposable test session and synthetic/non-sensitive data.

1. Preflight actual gateway service IDs, credentials, Daytona snapshot/volume,
   linked-child support, networking, PostgreSQL, tracking permissions, and limits.
   Never print secrets. Establish measured baseline latency/cost, not speculative
   sub-400 ms routing or sub-5 ms execution promises.
2. Generate a deterministic ledger/document corpus larger than the selected
   model context window with known asymmetric anomalies and independent expected
   aggregates. Stream the attachment, persist it, and create its dataset handle.
3. In strict RLM mode, inspect the dataset in Python. Construct and call supported
   DSPy sub-agents inside the REPL; delegate separate partitions to at least two
   real child sandboxes, including one deeper logical child with a distinct
   sandbox ID. Record logical IDs and provider linkage separately.
4. Join bounded child results, reproduce numerical claims in code, and promote
   only validated reports. Compare report bytes/aggregates against independently
   derived expected values. Inspect the trace to confirm full data was not pasted
   into the root prompt and every child's work was charged to the turn.
5. Create and test a reusable candidate Skill and source-backed memory. Exercise
   CLI/API download, context/mode, Skill push/pull, and trace inspection.
6. Interrupt a separate recursive turn and exhaust another's budget. Verify
   propagation, final status, no unauthorized artifact promotion, and deletion of
   all owned ephemeral children. Test a stopped linked child cannot be resumed as
   if its interpreter state survived.
7. Close and recreate the root runtime; recover files, history, and memory from
   durable storage. Ask a related question, retrieve the relevant memory, and
   detect a deliberately changed source rather than repeating a stale answer.
8. Run the separately authorized reviewed-example optimization job. Compare
   baseline and candidate on isolated holdout workspaces, demonstrate rejection
   of a regressed candidate, and exercise explicitly approved promotion/rollback.
   A neutral/regressed result is valid evidence, not permission to relabel it an
   improvement.
9. Save a compact evidence bundle: commands, settings with secrets removed, input
   hashes, expected/actual artifacts, trace/run links, sandbox IDs/lifecycle,
   resource totals, inspected terminal captures, and failed/skipped checks.
   Clean up all disposable provider resources and confirm cleanup status.

The implementation is complete only when all required phases and contracts have
evidence. Report separately what is implemented, non-live tested, live tested,
and still deployment-dependent. Local edits or passing tests do not imply that
anything has been committed, pushed, merged, or deployed.

## 15. Corrections to the previous blueprint

The goal was sound; the original sample implementations were not a safe drop-in
replacement. These corrections are mandatory, not scope reductions:

- Replace upload-once variables with per-execution updates; preserve final Python
  expression values required by Flex.
- Use documented `SandboxSerializable` integration rather than a custom `to_dict`
  convention alone. Validate actual task inputs, not only descriptor serialization.
- Preserve session-owned managers, dispatch, locks, event sinks, and budgets;
  do not replace them with a shared global runtime/workspace.
- Enforce resolved containment and mount boundaries instead of string-prefix
  path checks. Stream safe filesystem transfers rather than shell interpolation.
- Persist large files and deletions explicitly, with quotas and failure status;
  never silently omit files above 10 MB.
- Import experimental TypeSafe correctly and make ReAnchor outcomes explicit.
- Keep the existing native gateway route contract and configurable managed/local
  MLflow options; do not downgrade working configuration to outdated examples.
- Bind the judge LM, retain real context/provenance, isolate evaluation workspaces,
  and evaluate before promotion. Never fabricate gold scores or hide regressions.
- Add actual sandbox spawning and lifecycle ownership, not merely nested Python
  namespaces or a fixed child-investigation wrapper.
- Replace compile-only tests and performance guesses with behavioral contracts,
  full-stack acceptance, and measured results.

## 16. Authoritative documentation and implementation references

Use the pinned APIs and recheck contracts before coding. Relevant documentation
has been reviewed for this revision; that is not a claim that all vendor docs
have been exhaustively audited or the deployed services have been exercised.

- [DSPy 3.4.1 release: in-REPL DSPy sub-agents](https://github.com/stanfordnlp/dspy/releases/tag/3.4.1)
- [DSPy 3.4.1 documentation](https://dspy.ai/3.4.1/)
- [RLM, interpreter factories, facade, serialization, and budgets](https://dspy.ai/3.4.1/diving-deeper/rlm/)
- [Flex composition and optimization](https://dspy.ai/3.4.1/diving-deeper/flex/)
- [Experimental DecisionTypes](https://dspy.ai/3.4.1/api/experimental/DecisionTypes/)
- [Experimental ReAnchor](https://dspy.ai/3.4.1/api/experimental/ReAnchor/)
- [FastAPI native server-sent events](https://fastapi.tiangolo.com/advanced/server-sent-events/)
- [FastAPI lifespan](https://fastapi.tiangolo.com/advanced/events/)
- [FastAPI UploadFile](https://fastapi.tiangolo.com/tutorial/request-files/)
- [Daytona sandboxes: linked children, lifecycle, TTL, forks](https://www.daytona.io/docs/en/sandboxes.md)
- [Daytona volumes and subpath isolation](https://www.daytona.io/docs/en/volumes.md)
- [Daytona Python code interpreter](https://www.daytona.io/docs/en/python-sdk/sync/code-interpreter.md)
- [Daytona Python filesystem API](https://www.daytona.io/docs/en/python-sdk/sync/file-system.md)
- Repository sources: [configuration](src/fleet_rlm/config.py),
  [interpreter](src/fleet_rlm/interpreter.py),
  [engine, persistence, and evaluation](src/fleet_rlm/memory_and_eval.py),
  [server](src/fleet_rlm/server.py), [Skills](src/fleet_rlm/skills.py),
  [terminal client](src/fleet_rlm/cli.py),
  [optimization runner](scripts/optimize_and_evaluate_fleet.py),
  and [quality gates](Makefile).

## 17. Implementation checkpoint (2026-10-15)

The following is an evidence checkpoint, not a reduction of the requirements
above. All source changes remain local and uncommitted; no deployment or shared
program promotion has been performed.

Implemented: DSPy 3.4.1; strict RLM/Flex/hybrid composition; fresh nested
interpreters and real linked child sandboxes; authenticated dynamic tool bridges;
dataset handles and bounded workspace helpers; spooled HTTP/provider file
transfers; PostgreSQL file/history/outcome commits, mode/context persistence,
idempotent replay, and cross-worker session leases; reviewed source-backed
full-text memory; CLI controls and Skill sync conflict detection; isolated GEPA
rollouts, grouped splits, explicit judge binding, candidate manifests, integrity
checks, and explicit promotion/rollback gates.

Live acceptance uses existing Daytona credentials, an explicitly selected active
snapshot (`fleet-rlm-python313-v12`), and the verified Gateway service
`openai/system.ai.claude-haiku-5-5`. Databricks CLI OAuth succeeded against
`https://8259565402437752.2.gcp.databricks.com`. Extracted access tokens were
passed only through process environments, not printed or written by the test
harness. Databricks CLI itself uses its standard OAuth credential cache.

Run the explicit acceptance harness with:

```bash
uv run python scripts/verify_v2_live.py --live --trace --local-postgres \
  --snapshot fleet-rlm-python313-v12 \
  --model openai/system.ai.claude-haiku-5-5
```

`--local-postgres` requires the disposable Orb PostgreSQL instance on port 55432.
It avoids production writes: the configured shared database permits connection
but denied disposable schema creation. The harness uses a private disposable
schema, drops it, and deletes only its uniquely labeled provider resources.

Observed evidence:

- A 12,522,916-byte CSV containing 1,250,000 records round-tripped exactly.
  Streaming Python analysis produced independently checked sum 47,498,869 and
  128,870 negative values. It created an actual downloadable JSON artifact.
- Facade-built `Predict`, `ChainOfThought`, and `ReActV2` returned verified
  results. Nested RLMs reached separate child/grandchild sandboxes, returned
  221 for 13 × 17, and were deleted.
- Explicit `spawn_agent` received only the approved ledger, could not see the
  parent's private file, and returned a digest-checked promoted JSON artifact.
  The latest 17-case acceptance run passed in 132.33 seconds, including live
  discovery of 49 chat services and model/mode/context restart recovery.
- Completed-turn replay performed no second REPL execution. A simulated delivery
  failure did not downgrade the transactionally persisted completion.
- Deleting the working-copy files before reopening the app still recovered the
  artifact, history, and memory from PostgreSQL. A changed input digest marked
  the remembered observation stale.
- Call-budget exhaustion, child-capacity rejection, cancellation cleanup, and
  refusal to promote cancelled work passed. The final owned-resource list was empty.
- A real pseudo-terminal CLI turn verified a five-row ledger (sum 15, two
  negatives), persisted its trace ID, and downloaded the exact artifact bytes.
  Read commands were inspected at normal and narrow terminal widths.
- A second real interactive terminal selected sub/root models, displayed
  settings, submitted a Python verification of 17 × 19 = 323, rendered live
  progress, and retrieved its persisted trace. Command/argument completion,
  picker cancellation, API errors, malformed SSE, interrupted/failed/truncated
  streams, and failure-safe model changes have regression coverage.
- The subsequent 28.9-second free-form TUI recording (`fleet-tui-message.mp4`)
  shows submission, live Python execution, artifact creation, completion, and
  `/trace` retrieval. Downloaded `tui-audit.json` independently matched sum of
  squares 338350 and 14 multiples of 7 for integers 1 through 100. The recording
  retains an intermediate model-generated positional-`SUBMIT` TypeError before
  keyword-argument recovery; the final persisted success is not a claim of an
  error-free trajectory.
  The canonical persisted/API trace ID is
  `tr-84d52b19b9d2ccef23b9881aee723986`. A video-analysis transcription initially
  reported a different ID. Rechecking the raw PTY transcript, timestamped cast,
  current history API, and an enlarged actual video frame at 27 seconds confirmed
  that all contain this same canonical ID. The discrepancy was in the video
  transcription, not the recorded rendering. `fleet-tui-message-trace-frame.png`
  preserves the inspected crop; `fleet-tui-message-evidence.json` records the
  independently checked artifact and trace. The test sandbox was deleted while
  persisted history/artifacts were retained.
- A separate synthetic-data GEPA smoke generated and evaluated two instruction
  proposals with isolated runtimes and an untouched holdout. Both baseline and
  selected program were correct; the baseline was retained. This is workflow
  evidence, **not** evidence of production improvement. All 14 sandboxes from
  the two bounded attempts were confirmed deleted. Gateway-compatible reflection
  uses DSPy's supported `InstructionProposer` with `ChatAdapter` rather than the
  default schema-requesting `JSONAdapter`.

The review artifacts are under `.amp/in/artifacts/`, including
`v2-final-live.json`, `v2-gepa-live-fixed.json`, CLI transcripts and rendered
captures. They are local evidence, not package contents. `make check`,
`uv lock --check`, and `git diff --check` are the non-live gates.
The latest complete non-live gate passed 109 tests plus lint, formatting, types,
docs links, OpenAPI consistency, and package build. This is not full-plan sign-off.
The earlier 17-scenario live acceptance used Daytona SDK 0.220.0. The dependency
is now pinned to 0.224.0; that upgrade alone does not implement the proposed
deadline/cancellation fixes or establish every provider capability.
After the upgrade, `make check` passed all 106 tests and quality/build gates.
A live 0.224.0 smoke created a root and distinct linked child, copied a selected
file, verified Python execution returned 323 for 17 × 19, and confirmed deletion
with no remaining smoke-test sandboxes. The full 17-scenario live suite has not
been rerun on 0.224.0. The API has subsequently been restarted on the upgraded SDK.

The observability follow-up adds correlated module/model/tool start/end events,
elapsed milliseconds, bounded/redacted tool arguments and results, agent identity,
explicit route/fallback summaries, labelled DSPy bootstrap code, and execution
return values/errors. Predictor payloads are omitted; no private chain-of-thought
is exposed. Final answers preserve literal line breaks and learned notes are
separate. Previews are best-effort redaction, not a DLP boundary or durable event log.
The 39.4-second live `fleet-observability.mp4` recording shows a real child sandbox,
Python results 323 and 338350, child deletion, and the exact three-line persisted
answer. Trace `tr-beb8c8a9ad814057bf7fe54dd4f99b07` contains 49 OK spans including
two RLM invocations and one spawn tool. Python positional-SUBMIT errors were
visible and recovered with keyword arguments; OK spans do not mean an error-free
execution trajectory. Screenshots, PTY/cast, and API/trace evidence are alongside
the recording in `.amp/in/artifacts/`. These changes remain local and uncommitted.

The subsequent exact prompt `Tell me the 14922452th digits after the decimal point
of Pi` reproduced the fixed 120-second bridge timeout (138.1 seconds end to end).
That independent per-tool limit and the adjacent 180-second execution-read limit
are now removed. Daytona execution RPCs explicitly disable the SDK execution
timer; namespace closure, explicit cancellation, and the shared 900-second turn
watchdog remain. A delayed-reply regression fails with the old finite wait and
passes with the new behavior; namespace-cancellation coverage still passes.
The same prompt then completed in 859.5 seconds, returning `3`; independent
GMP/MPFR computation confirms the digit and surrounding `22932[3]08868`.
Trace `tr-6cab07082e0a623c3436ee7331d9ad1e` has 28 OK spans. The recordings,
PTY/cast, answer screenshot, and independent check are stored as
`fleet-pi-14922452-*` review artifacts. The 15× overview retains real elapsed
timestamps; the full recording retains real-time duration. `make check` still
passes all 109 tests and quality/build gates. Broader cancellation/crash recovery
requirements below are not signed off by this focused timeout removal.

Remaining before full-plan sign-off:

- Token/cost observations are unknown (`null`), not zero. Call/depth/sandbox/time
  and byte quotas are enforced; provider-aware token/cost ceilings and separate
  usage categories still need implementation and deployment pricing evidence.
- Allocation journals reconcile expired owned resources on the next provisioning
  of that session, not through a distributed startup/background reaper.
  An earlier acceptance recorded FUSE `FileNotFoundError` while removing an
  already-disappeared directory. Cleanup now tolerates missing entries but still
  reports other failures. The latest run recorded zero workspace-cleanup warnings
  and an empty final provider-owned resource list. Crash-time Volume reclamation
  still requires the broader reconciliation lifecycle.
- Memory currently has session scope, kinds, review status, sources/digests,
  timestamps, and stale detection. Confidence, explicit supersession/contradiction
  workflows, and opt-in shared/project memory are not complete.
- ReAnchor is explicitly skipped without a compatible calibration client.
  Governed Databricks/Unity Catalog trace storage has not been exercised; the
  acceptance traces used local MLflow storage. UC destination configuration and
  compatible calibration are prerequisites, not validated capabilities.
- The complete slow-client/disconnect/concurrent-session/crash/ambiguous-provider
  matrix, long-turn checkpointing, Skill candidate review lifecycle, and all
  versioned lifecycle/status events still require implementation or additional
  evidence. Unit success and the passing live scenarios do not establish these.
- Production-quality improvement needs an authorized reviewed corpus, a
  predeclared evaluation budget/ceilings, and adequate untouched holdout size.
  GEPA's configured metric-call target is coarse: the smoke observed seven metric
  callbacks for a target of four. It is not a hard financial spend ceiling.

These gaps prevent an honest claim that **every** requirement in this plan is
finished. The implemented core is usable for trusted, bounded Orb workloads;
public multi-tenant deployment and autonomous production self-promotion remain
outside the validated trust boundary.
