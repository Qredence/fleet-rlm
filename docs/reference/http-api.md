# HTTP API reference

Fleet exposes a FastAPI application with the following routes:

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Return status, application version, and initialized MLflow destination. |
| `GET` | `/sessions` | List persisted sessions (up to 100). |
| `GET` | `/models?scope=system.ai` | Discover authorized chat-capable Unity Gateway model services (up to 1000). |
| `DELETE` | `/sessions/{session_id}` | Stop the session sandbox and release its runtime and lease; history is kept. 404 without a runtime, 409 during a Turn. |
| `GET` | `/sessions/{session_id}/config` | Inspect non-secret models, mode, dataset context, configuration, and limits. |
| `PUT` | `/sessions/{session_id}/model` | Select and persist an accessible model for the `root` or `sub` role. |
| `GET` | `/skills?session_id=...` | Return the selected session's Skill catalog. |
| `POST` | `/skills/pull?session_id=...` | Export sandbox Skills to the configured local user Skill directory. |
| `GET` | `/sessions/{session_id}/files` | List workspace files and generated artifacts. |
| `POST` | `/sessions/{session_id}/attach` | Attach a multipart file to `files/` in the session workspace. |
| `POST` | `/sessions/{session_id}/turn` | Run a Turn and stream progress as SSE. |
| `GET` | `/improve/readiness` | Report what the labelled examples support, what is missing, and `new_labels_since_last_run`. |
| `GET` | `/improve/queue` | List replay-captured Turns across Sessions with label state and pre-extracted figures. |
| `POST` | `/improve/labels` | Label one Turn by `turn_id`; the server resolves its trace. |
| `GET`, `POST` | `/improve/jobs` | List persisted jobs or submit an idempotent job request. |
| `GET` | `/improve/jobs/{run_id}` | Read one job snapshot and its bounded saved comparison. |
| `POST` | `/improve/jobs/{run_id}/cancel` | Request cooperative cancellation of a running job. |
| `POST` | `/improve/jobs/{run_id}/review` | Explicitly approve or reject a candidate. |
| `GET` | `/improve/history` | Approved versions and completed evaluation jobs, for the quality trend. |
| `GET`, `PUT` | `/improve/settings` | Auto-run switch, its thresholds and budgets, and the default token cap. |
| `GET`, `PUT` | `/sessions/{session_id}/mode` | Read/change persisted `direct`, `flex`, `rlm`, or `hybrid` mode. |
| `PUT`, `DELETE` | `/sessions/{session_id}/context` | Select a content-addressed dataset or clear the selection. |
| `GET` | `/sessions/{session_id}/history` | Read bounded committed turn history directly from Postgres; no runtime lease. |
| `GET` | `/sessions/{session_id}/turns/{turn_id}` | Recover a durable running/terminal turn outcome directly from Postgres without replaying it; available while another worker owns the Session. |
| `GET` | `/sessions/{session_id}/workspace/tree` | List workspace paths. |
| `GET` | `/sessions/{session_id}/workspace/stat/{rel_path}` | Get a file's size and SHA-256. |
| `GET`, `PUT`, `DELETE` | `/sessions/{session_id}/workspace/files/{rel_path}` | Download/write/delete workspace files. |
| `GET` | `/sessions/{session_id}/memory?q=...` | Retrieve scoped memories. |
| `PUT`, `DELETE` | `/sessions/{session_id}/memory/{memory_id}` | Create/review/update/delete memories. |
| `POST` | `/skills/push?session_id=...` | Synchronize validated local user Skills. |

The `/health` MLflow metadata includes `workspace_host`, the resolved non-secret
Databricks tracking workspace URL (`null` for local tracking). API reuse and
evaluation preflight compare it along with the experiment and trace destination.
Named tracking profiles resolve their own workspace rather than the model gateway host.

Workspace listings and Turn input inventories include regular files beneath
`files/`, `artifacts/`, `memory/`, `skills/`, `buffers/`, and `sessions/` only.
Unmanaged top-level scratch files are left intact but are not advertised or
captured in replay snapshots. Symbolic links are not traversed. Invalid managed
paths or malformed inventory responses fail rather than becoming empty listings.

Routes that change or read workspace, context, memory, Skill, mode, or model state take the
session lock. While a Turn holds it they answer `409` after two seconds rather than queueing.
`POST /sessions/{session_id}/turn` takes the same lock and answers `409` the same way when another Turn
or workspace change is running; it holds the lock until the stream ends and the Turn has drained.

## Improve

The program and the labelled dataset belong to no Session: one installed program
serves every Session, and labels from all Sessions feed one dataset. The Improve
routes do not acquire a Session runtime lease.

**Readiness.** `GET /improve/readiness` returns `ready`, the counts (`examples`,
`groups`, `critical`), the `split_sizes` the examples would form, `missing`
requirements in plain words, and `new_labels_since_last_run` (approved labels
written after the latest completed or approved optimize job was created). It uses the same loader and
checks as a job, so a green report means a job will not refuse for data.

**What can improve.** Readiness also returns `min_split_examples` (read-only, `FLEET_MIN_SPLIT_EXAMPLES`,
default 5: dev and holdout each need that many examples, enforced in the one `readiness()` that curation,
`fleet optimize --check` and this report share), `components` (each tunable program part with a one-line
description), `improvable_examples` (labelled examples whose newest finished rescore or replay scored below 1.0,
`null` until one has run), and a `note` when that count is 0 (advisory, never a blocker). The total example
requirement scales with the dev share, `ceil(min_split_examples / validation_ratio)` and never below 8, and splits
are by source group, so more Sessions can be needed than the arithmetic suggests.

**Labelling.** `GET /improve/queue?limit=` (1–200, default 50) lists completed,
replay-captured Turns across Sessions, newest first: `turn_id`, `session_id`,
task and answer previews, the answer's number literals as `figures`, and the
label state (`unlabelled` or a review status with the stored gold). A Session's
older Turns fall out of the task preview once they leave its bounded history.
`POST /improve/labels` takes `turn_id`, `status` (default `approved`),
`expected_answer`, `expected_values`, `critical`, and `group`; unset fields keep the
Turn's existing label. An approved label with no `expected_answer` and no earlier one takes the Turn's stored full
answer (the queue shows a whitespace-collapsed preview). The server resolves the trace from the stored Turn, then
writes MLflow expectations and PostgreSQL curation exactly as `fleet eval label`
does. An unknown Turn is `404`; a Turn without a trace, or invalid labels, `422`;
a partial synchronization `503` with the retry hint.

Each queue item also carries `flags` (stored trouble signals: sandbox errors, rejected SUBMITs, or a
below-1.0 latest evaluation) and `evaluation_score`; flagged Turns are listed first because they teach GEPA the most.

**Jobs.** A `POST /improve/jobs` body contains a `kind`, the shared optimizer
options (`gepa_budget` or `max_metric_calls`, `threads`,
`reflection_minibatch_size`, `max_candidate_proposals`, `validation_ratio`,
`trace_search`, and `max_total_tokens`) plus a caller-generated `request_id`. `kind` is `optimize` (the default),
`evaluate_rescore`, `evaluate_replay`, or `evaluate_synthetic`; other values return `422`. `validation_ratio` is inclusive 0.1–0.5 (default 0.35); invalid controls
return `422` before run creation. Unusable curated splits, including a missing
critical holdout, fail preparation before search.
Repeating the same request ID with the same kind and options returns the existing
job; reusing it with different ones returns `409`. The API owns the accepted job
across client disconnection. It runs the existing optimizer as a subprocess,
records lifecycle state in PostgreSQL, and saves traces/evidence to the configured
MLflow experiment and candidates to the configured artifact directory.

`max_total_tokens` caps the generative LM tokens a job may spend. Omitted, it
inherits `FLEET_JOB_MAX_TOKENS`; `0` is unlimited. The worker sums the usage the
LM calls report; usage is counted after each call, so in-flight calls can push
`tokens_used` past the cap. When the cap is reached it stops search and validation at
the next safe boundary and the job ends `budget_exhausted` with no candidate. TypeSafe
decision calls are not counted. The cap is telemetry: it never affects the
persistence failure latch. `progress` carries `tokens_used` and `token_cap`.

There is one active job-or-review slot and no queue. An occupied or draining slot
returns `409`. List returns persisted jobs, `active_run_id`, and whether the
manager is accepting new work. Detail returns a snapshot plus a bounded comparison
document when one exists. During search `progress` also carries `best_score` (the
best validation average GEPA has seen) and a `score_trajectory` of at most 200
`[iteration, average_score]` points. After validation the job carries `scores`
(baseline and candidate dev/holdout means). The TUI polls these snapshots every
second; job progress is not an SSE stream.

Cancellation requires a running job and requests a cooperative stop. The worker
checks both its stop signal and the PostgreSQL stop flag at safe boundaries;
terminal status is written only after the worker exits and output drains. A
disconnect has no cancellation effect. The launcher drains API-owned jobs
before a normal service shutdown. If the API process restarts unexpectedly,
abandoned managed work is marked interrupted and is never automatically rerun.
Interrupted approval is reconciled against the installed program's digest before
the candidate can be reviewed again.

Approval uses the same artifact, incumbent, configuration, source, and installed
dependency identity checks as standalone `fleet optimize --approve`, and installs
`fleet.json` for every Session. Review is an explicit action; a passing candidate
is not installed automatically. Jobs recorded before the program became global keep
their Session as `legacy_session_id`: they stay readable and can be rejected, but
approval returns `409` because their incumbent was a per-Session program.

**Progress and fail-fast.** During paired validation the worker persists `validation_done` and
`validation_total` in `progress` (rollouts finished of 2 stages x 3 passes x dev+holdout examples). When the search
returns a candidate that changes no instruction, Flex code, or calibrated decision fields, validation is skipped and the job completes with the
gate's own reason, `candidate changes no instructions, Flex code, or calibrated decision fields`. Job detail adds `summary`, computed from the saved
comparison: `changed` components (of `components_total`), per-split baseline and candidate means with `improved`,
`regressed` and `ties` counts over the paired per-example scores, the critical count, and a one-line `headline`.

**Evaluate jobs.** The three evaluate kinds run in the same worker process, ledger, stage and
progress reporting, stop checks, and token cap as `optimize`, but create no candidate and install nothing
(approving one returns `409`). `evaluate_rescore` re-judges the saved traces of labelled Turns with the
current judges and runs no Turn again. It uses the optimization split over every curated example (PostgreSQL and
MLflow); an example whose Turn has no readable trace carrying an approved MLflow label cannot be re-judged and is
counted in `evaluation.excluded_without_trace` rather than silently dropped. `evaluate_replay` runs the labelled examples against the installed
global program (the baseline when none is installed) in isolated rollouts on the same grouped
train/dev/holdout split as optimization. `evaluate_synthetic` is the `fleet eval run` path: it runs the
generated cases against this server and, like that command, refuses a server whose trace destination is not
the evaluation experiment, so the job ends `failed` instead of mixing benchmark traces into curation.
The finished job carries `evaluation`: `kind`, the `program` measured (`digest` and the approved job `version`,
or `baseline`), per-split `splits` (`n`, `mean`, `errors`, and `critical` `passed`/`n`), and a `headline` of
the `holdout` mean and `critical_pass_rate`. Synthetic cases are not split, so they report one `synthetic`
entry and a `null` holdout. Work is scored in chunks of 8 examples and the stop flags and token cap are checked
between chunks, so a stop or the cap overshoots by at most one chunk; a synthetic job only counts the judge calls it
makes, not the server's Turns.
A job's `trigger` is `manual` or `auto`.

**Rationale.** After an optimize job's paired validation, one bounded reflection-model call summarizes the
saved comparison in a few plain sentences. It appears as `rationale` beside `manifest` and `report` in the
detail `comparison`, counts toward the token cap, is skipped once the cap is spent, and is empty on any failure.
It is a reader's summary, never evidence: the promotion gate and approval never read it.

**Auto-run and settings.** `GET /improve/settings` returns `auto_run` (default off), `min_new_labels` (default 5),
`max_metric_calls` for automatic jobs (default unset: the light budget), and `max_total_tokens` (the default
cap for every job unless a request sets its own). Auto-run spends unattended, so `PUT` refuses `auto_run: true`
with `422` while the effective `max_total_tokens` is `0`, and an automatic check skips (logging why) if the cap
later becomes `0`; `auto_run_blocked` carries that reason. Saving merges only the supplied keys in SQL, so
concurrent updates do not overwrite each other. Saved values override `FLEET_AUTO_OPTIMIZE`,
`FLEET_AUTO_OPTIMIZE_MIN_NEW_LABELS`, `FLEET_AUTO_OPTIMIZE_MAX_METRIC_CALLS`, and `FLEET_JOB_MAX_TOKENS`.
`PUT` saves only the supplied fields; an explicit `null` returns that field to its environment default.
After each successful `POST /improve/labels`, if auto-run is on, readiness is green, at least
`min_new_labels` labels are new since the last finished optimize job, and no job is active, the server starts an
`optimize` job with the automatic budgets and `trigger: auto`. Its request ID derives from the label state, so
a repeated check cannot start a second job and a failed job is retried only after another label arrives. A failure
of this check never fails the label. Automatic jobs only create candidates; nothing is approved or installed
without an explicit review.

**History.** `GET /improve/history` returns `versions` (approved, non-legacy
optimize jobs with their `scores`) and `evaluations` (completed evaluate jobs with their `evaluation`, so the
trend shows which installed program each result measured), newest first.

**Installed program.** Once any global job was approved, only the file that
approval installed may serve: if `fleet.json` is missing or differs from the latest
approved job's digest, new Session runtimes and every later Turn are refused with a
clear error, however long the process has run, and `GET /improve/readiness` lists
the problem in `missing` with `ready: false`. Baseline serves only while no global
job was ever approved. Per-Session artifacts from before the program became global
are ignored.

**Exposure.** `/improve/queue` returns task and answer previews of Turns from every
Session, so exposing the API beyond loopback needs the secured boundary described
below; the routes add no authentication of their own.

There is no caller authentication. The service binds to loopback by default;
binding elsewhere does not add authentication. Only use on a trusted host or
behind a separately secured boundary.

Model selection accepts `{"role":"root","model":"openai/system.ai.service"}`.
The service must appear in discovery for its catalog/schema. Selection is
session-scoped; a busy turn returns 409, an inaccessible/non-chat model returns
422, and discovery/persistence failures return a sanitized 503 without replacing
the current engine. Model, mode, and context settings survive independent updates
and server restart. Operator-owned secrets and resource limits are not writable
through this endpoint. Accessible metadata does not guarantee inference readiness.

## Attach a file

Send a `multipart/form-data` body with a `file` field. The upload limit is
controlled by `FLEET_MAX_UPLOAD_BYTES` (default 25,000,000 bytes). The response
contains the mounted path and relative workspace path, for example
`/workspace/files/input.csv` and `files/input.csv`.

## Run a Turn

The JSON request has one required `task` string:

```json
{"task":"Summarize the attached CSV and verify the totals with Python.","idempotency_key":"ledger-review-1"}
```

`requested_skills` optionally lists up to three unique exact Skill names in caller
order (lowercase single-hyphen-separated words, 1–64 characters). Omitted and `[]`
are equivalent; invalid names, duplicates, non-string items and excess names return
`422`. Paths, digests and approval assertions are not request inputs.
Required Skills resolve only from configured host user Skills (overriding bundled
Skills), never from remote-only authored Skills. Fleet captures their validated
files once before remote work and preloads the entire UTF-8-decoded `SKILL.md`
from those exact bytes, without newline normalization or clipping. Full instructions
including frontmatter must fit 20,000 Unicode characters each; required plus optional
raw preload text fits 60,000 characters. Existing file-count/byte/link/path limits
also apply. NUL and the reserved closing preload delimiter are invalid.
Up to three **additional optional** Skills may fill remaining space. Required names
are excluded before the optional first-20 relevance selection, and incur no relevance
charge. Explicit Direct refuses required intent; Hybrid forces RLM without a routing
model call; explicit RLM/Flex keeps its mode. Source/size/mode refusals inside the
stream are durable error outcomes followed by `done`, not late HTTP errors.
Completion and stored results carry ordered `requested_skills` and host-derived
`required_skills` entries (`name`, `source`, `sha256`). Source identity hashes all
captured files, not only instructions. These guarantees do not make the guest copy
immutable or certify later script execution: Gate C review/activation and host-enforced
consumption are not implemented. Skills never grant host permissions or extra budget.
The client supports `/skills require <names...>` and `/skills clear` for subsequent Turns.

`record_replay` defaults to `true`; set it to `false` for a Turn whose inputs must
not be retained as a possible reviewed GEPA example. Fleet captures
the exact bounded conversation, memory, selected dataset handle, Skills
catalog/revision, non-secret runtime configuration, and workspace file bytes
(up to 8 MB total). The snapshot
is stored in Lakebase only after the Turn completes successfully and is not
eligible for GEPA until its MLflow trace is explicitly labeled `approved` with
gold expectations. This option can retain sensitive workspace content; use it
only for data approved for that purpose. When capture is enabled, an oversized
workspace skips it instead of failing the Turn: the stream carries a `replay_skipped` event (`bytes`,
`limit`) and the stored Turn reports `replay_snapshot_selected: false`. The TUI
exposes the same switch with `/replay on` and `/replay off`.
New snapshots use replay v2 and retain ordered required intent, origins and captured
bytes. Isolated reconstruction verifies those bytes rather than reading current host
Skills. Historical v1 remains readable as empty required intent, without invented origins.

`timeout_seconds` is an optional strict integer and the only per-Turn deadline
override. Omitting it or sending `null` (the default) inherits
`turn_timeout_seconds`, or `0` (no Fleet deadline) when configuration does not set it. `0` disables
the Fleet wall-clock deadline for this Turn. A positive value must be `10`–`3600`
and sets this Turn's duration. Booleans, floats, numeric strings, negatives,
`1`–`9`, and values above `3600` return `422` with validation errors for
`timeout_seconds`. OpenAPI describes both permitted integer ranges. The resolved
source is reported as `request`, `configuration`, or `default`, never `command`.
A deadline-free Turn reports `effective_seconds: null`; it still reports the
provider-owned `ttl_minutes` and `auto_stop_interval` bounds, which Fleet does not
remove or shorten.

The response uses `text/event-stream`. Each data event is JSON prefixed by
`data: ` and separated by a blank line. The server initially emits a
`turn_status` preparation event, then DSPy callback events such as
`module_start`, `lm_end`, `tool_start`, and `tool_end`. An `lm_end` event carries a `usage` object with
numeric `prompt_tokens`, `completion_tokens`, `total_tokens`, and, when the provider reports them,
`reasoning_tokens`; it is omitted when the provider reports no usage. Provider thinking and reasoning
text (extracted from DSPy `ThinkingPart` or `reasoning_content`) is streamed in activity updates.
Completion is reported as `turn_complete` with `answer`, `route` (`direct`, `flex`, or `rlm`), optional `learned_skill_note`, and an
`artifacts` list. When MLflow tracing is configured, the completion event also
includes the recorded `trace_id`. A Turn that provisioned its sandbox also reports
the preflight-resolved environment identity as `snapshot_name`, `snapshot_id`,
`snapshot_cpu`, `snapshot_mem`, `snapshot_disk`, and `snapshot_definition_digest`.
When Daytona returns build metadata, `snapshot_build_digest` records a hash of
the provider-reported image reference, build context hashes, and Dockerfile
content. This is observed provider evidence, not a claim that an arbitrary
custom snapshot matches Fleet's recipe. Custom snapshot names have a null
`snapshot_definition_digest`; only the repository-owned name receives Fleet's
intended recipe digest.
The identity fields appear on the `fleet_turn` trace span, in the stored Turn
record, and in the result returned by `GET /sessions/{session_id}/turns/{turn_id}`;
snapshot name, ID, and resource values come from the provider DTO. The definition
digest comes from Fleet's versioned definition; provider build metadata is hashed
separately. The same `turn_id` from the
terminal envelope joins the stored `fleet_lite_turns` primary key, the status response,
and the `fleet_turn` span, and those surfaces also agree on `route`, `outcome` (or
`answer`), `deadline_source`, `effective_seconds` (an explicit `null` when the Turn was
deadline-free), `duration_seconds`, and `cleanup`. `duration_seconds` is measured once
with a monotonic clock; MLflow derives its span duration from timestamps taken just
outside that measurement, so the two agree within two seconds. A Turn or replay row
with incomplete snapshot fields and no `deadline_source` remains readable as
`provenance_state: "pre-provenance"`, without backfilling absent values. A newer Turn with `deadline_source` but
incomplete required snapshot fields reports `provenance_state: "unresolved"`, for example
when snapshot preflight refused the Turn or a sandbox-free `known_empty` `Direct` Turn completed without provisioning Daytona. All six snapshot fields must be present
for `provenance_state: "recorded"`; the optional provider build digest is
present only when Daytona returns build metadata.
The identity is recorded the
moment preflight resolves it, so a failure in a later preparation step (for example an
oversized replay payload) still reports the same six fields on the span, in the stored
failure, and in the `error` event. A Turn that fails before preflight resolves records
no snapshot fields. A Turn that executed against a root sandbox also reports the live
provider handle id as `root_sandbox_id` on the completion or failure event, on the
`fleet_turn` span, in the stored Turn record, and in the Turn status result. It is
omitted when the Turn never provisioned a sandbox.

The default Python 3.13 snapshot includes hash-locked packages for common data,
plotting, Excel/PDF, HTTP/HTML, and Python coding tasks. Git, Node.js/npm, pnpm,
TypeScript, ripgrep, and jq are available as commands. Bundled Skills prepare
specialist document, scientific/ML, and DSPy/GEPA dependencies only when needed,
using isolated uv environments outside the workspace. Optional packages do not
change the recorded base snapshot identity. The interpreter reports installed
modules and available commands as observations. See
[sandbox environment](sandbox-environment.md) for the package list, dependency
isolation, and staged snapshot verification.

Fleet's guarded workspace helper and Python file APIs provide sandbox file
access. Git CLI operations run inside the sandbox. The snapshot does not grant
private repository credentials or enable MarkItDown's cloud integrations;
package installation alone does not configure those services.

A completed Turn keeps its warm
root for the next Turn and reports the same id; after a failed or cancelled Turn the
stopped root is discarded, so the next Turn provisions a new sandbox with a new id and
does not recover the terminal Turn's interpreter state. Failures produce a sanitized `error` event
(which also carries the `trace_id` when tracing is configured) followed by stream completion.
The one surfaced exception is a snapshot preflight refusal: its event and stored failure carry
the repository-owned snapshot name, the observed state/resources, the required floors, and the
build command, because that text is constructed by Fleet rather than taken from an arbitrary
exception. Every other failure text remains sanitized. Every Turn's stream
ends with exactly one outcome event followed by the terminal `done` envelope, which
carries the same `session_id`, `turn_id`, `seq`, and SSE `id` as the rest of the
stream and is delivered after teardown. If the Turn's worker stops without reporting
an outcome, the server sends a sanitized `error` and closes the stream. The connection
may emit SSE comment keep-alives.

Fleet validates the complete version-1 JSON envelope before native SSE framing.
Terminal events (`turn_complete`, `error`, `turn_cancelled`, and `done`) use explicit
payload models; dynamic activity cannot substitute for a malformed terminal event.
Event-specific data lives inside nested `payload`, with envelope headers carrying
`version`, `event`, `session_id`, `turn_id`, `agent_id`, `parent_agent_id`,
`sandbox_id`, `seq`, and `timestamp`.
Historical replay preserves unknown fields and absent versus explicit null values;
it does not restore a live interpreter. The OpenAPI stream schema describes the
framed text, rather than claiming the whole response is one JSON object.

For example, a terminal `done` frame has the following form (the blank line ends
the frame; sequence and timestamp values vary):

```text
data: {"version":1,"event":"done","session_id":"session-a","turn_id":"turn-a","agent_id":null,"parent_agent_id":null,"sandbox_id":null,"seq":3,"timestamp":0.0,"payload":{}}
id: 3

```

Job list responses contain `jobs`, nullable `active_run_id`, and `accepting`. Start,
cancel, and review responses contain `job`; detail adds nullable `comparison`.
Public job models retain `kind`, progress, options, failure, gate, scores, and review
information while excluding private worker settings.

Workspace writes require exactly one non-null `content` or `content_base64` field.
An empty string writes an empty file. Neither or both returns the existing safe
422 detail, without echoing file content. OpenAPI expresses this cross-field rule.
Other workspace-write validation errors return only error type, location, and message;
rejected input and validation context are excluded from the response.

Sessions run one Turn at a time; a second Turn on a busy Session gets `409` after two seconds. Session IDs are
validated by the runtime; use simple URL-safe identifiers.
An optional idempotency key persists intent before execution. Completed requests
replay their recorded outcome; failed/uncertain requests do not replay side effects.
Empty required intent retains the legacy task-only digest. With required Skills,
the existing digest column records versioned task/ordered-name intent and captured
source identities (or a bounded admission refusal). Completed requests match **intent**:
the same task and ordered names replay the original receipt even after source edits
or deletion, without executing today's files. Other claims remain **source-bound**:
changed origins/resource bytes conflict under the old key; unchanged failed/running
claims still do not rerun. Changed task, name or order always conflicts, as does adding
required intent to a legacy task-only key. Use a new key to execute new source content.
This deliberately favors recovering completed work over invalidating its receipt when
deployment files change; a completed replay is not evidence that current files ran.
Completed required-intent receipts are checked before reading current Skill sources.
Receipt lookup and source comparison do not own live execution or arm its watchdog.
The execution deadline starts after a new Turn is claimed. Disconnect during admission
is recorded and drained: replay/conflict preserves the warm root, while a new claim is
cancelled before model or sandbox execution starts. Admission and disconnect cancellation
share the same lock; source capture itself remains synchronous and must drain before
the Session is unlocked.
Replay outcome events carry the original persisted `turn_id`, so the status route
can recover that same outcome rather than looking up the retry's preparation ID.
A successful completion is assembled once and committed with the Turn record,
memory note and optional replay snapshot in one transaction. New result JSON includes
`turn_id`; historical completion reads supply it from the enclosing row without
rewriting history. Live completion and idempotent replay use the same committed
payload; the status route additionally derives `provenance_state`. See the
[result field and legacy inventory](turn-results.md).
Post-commit tracing, learned-note and delivery errors cannot downgrade success or
request cancellation. Disconnect cancellation is serialized with commit/readback;
a lost connection may deliver no completion, which a reconnect can recover. A lost
commit acknowledgment triggers a bounded readback of the claimed Turn. If it cannot
establish the outcome, Fleet stops remote work, leaves durable intent untouched and
blocks reexecution. Workspace file bytes do not participate in the SQL transaction.
A PostgreSQL advisory lease rejects a second worker owning the same session.
The lease covers Turn execution and mutations. History and Turn status are
durable reads that use the shared pool and do not take it, so they remain
available from any worker. A worker that loses its lease evicts the stale
runtime and rebuilds it on the next request once any draining Turn finishes.

Events include `version`, `session_id`, `turn_id`, agent/parent/sandbox IDs,
monotonic per-stream `seq`, timestamp, and nested `payload`. Generated code, bounded outputs, allocation/deletion, routing,
and call-budget events are available. Nonterminal activity is best effort: bounded
queue and cross-thread handoff saturation may drop events, including either half
of an activity pair, without delaying execution or cleanup. Progress is coalesced.
Only outcome events (`turn_complete`, `error`, `turn_cancelled`) and `done` apply
producer backpressure, waiting up to 30 seconds for queue space. Delivery failure
cannot reverse durable completion; recover the outcome through Turn status or
idempotent completion lookup, not by automatically retrying side effects.
Module/model/tool activity has paired `*_start` and `*_end` events with `call_id`;
DSPy callbacks also include `parent_call_id`. End events report `ok`, `error_type`,
and `duration_ms`. Tool `inputs` and `output` are bounded, redacted text previews;
`source` distinguishes `dspy` callbacks from `sandbox_bridge` operations. These
may be nested observations, not additional model calls. Predictor payloads are
omitted. Provider reasoning is streamed separately when returned by the model. Previews and provider reasoning redact labelled credentials; this is not a DLP
boundary and cannot detect every secret in arbitrary text.
`repl_code`/`repl_output` share an `execution_id`, namespace, and `phase`
(`bootstrap` or `task`); output includes timing, stdout/stderr, a bounded `value`
preview, and error status. `triage_decision.summary` describes the selected route
or enforced fallback; it is not a model's private chain-of-thought. These are
live activity events, not a durable replay log; `/history` retains turn outcomes.
Long-running Python and host-tool calls have no independent 120-second bridge
or 180-second execution-read limit. Host-call waiting derives from the Turn's
remaining deadline, so a bridged call never outlives a bounded Turn, and only a
bounded Turn arms the watchdog. A deadline-free Turn sends `run_code(timeout=0)`
explicitly and pauses host-call waits unboundedly until the call returns or the
Turn is cancelled, while unbridged local Python execution inside a single cell
has a 60-second safety cap (`DEFAULT_EXEC_TIMEOUT_SECONDS`) unless overridden
(`exec_timeout`); Daytona's provider TTL and idle bounds still apply. Closing a
stream before durable completion requests remote cancellation and drains the
worker, serialized against commit/readback. Request cleanup retains the Session lock until the worker drains, including when HTTP
forwarding is blocked after consuming a yielded event. After durable completion,
disconnecting preserves a successful Turn's warm root while the worker finishes.
Reconnection is not an automatic side-effect retry.

New completion events and their persisted idempotent outcomes also include
host-owned `lm_calls`, `sandbox_errors`, `submit_rejections`, and
`budget_breakdown`. The legacy `lm_calls` name means shared budget units rather
than visible model requests. Breakdown keys are `generation_query`, `routing`,
`skill_selection`, and `other_decisions`; their values sum to `lm_calls`.
`sandbox_errors` counts remote Python failures, while `submit_rejections` counts
blocking host rejections. Missing counters in historical outcomes remain unknown.
`budget_update` events include the same current counters and breakdown.
One `sandbox_environment_incomplete` event per durable Session lists `missing`:
the probed modules absent from the snapshot. The marker is stored on the Session
row, so `DELETE /sessions/{id}` and a rebuilt runtime do not repeat the event.
It is observational only. The model-visible environment string still reports the
installed and missing sets, and a missing optional package never fails a Turn or
increments `sandbox_errors`.
`repl_output.outcome` distinguishes `remote_code_error` from
`ungrounded_submit_rejected`, so a host rejection is not displayed as a remote
Python failure during recovery.

`replay_skipped` is emitted once during preparation when replay capture is enabled and
the workspace exceeds the replay byte limit; it carries `bytes` and `limit`.

`learned_note_warning` carries a `namespace`, `field` (`learned_skill_note`),
and bounded `figures` list. It does not block submission. Existing
`ungrounded_submit` events describe substantive-output rejections or acceptance
after the retry cap. Learned-note warnings do not establish note correctness.

Workspace writes accept exactly one of `content` (UTF-8 text) or
`content_base64`. Reads return bytes with an attachment disposition. Mode and
dataset selection persist across server restarts. Dataset hashes are checked
again at execution, so changing a source requires selecting its new revision.
Memory writes accept `content`, `kind`, source path-to-digest mappings, and an
explicit `reviewed` flag; only reviewed retrieval is included as turn evidence.

## Skills and files

`GET /skills` and `POST /skills/pull` accept an optional `session_id` query
parameter, defaulting to `default`. Local user Skills are read from
`.fleet/skills/` by default. Developer skills in `.agents/skills/` are not
synced as user Skills.

`GET /sessions/{session_id}/files` returns `files` and an `artifacts` subset;
it does not download file contents. The workspace is session-scoped and
persisted on the Session's Daytona Volume.

## Diagnostics and operator setup

The FastAPI server always runs in a single serving mode and exposes no `/setup/*`
routes. Validate configuration, database readiness, MLflow destinations, and Daytona
snapshots via `fleet doctor` and `fleet snapshot preflight|build|activate` before
starting the server. During normal shutdown, the API drains any active API-owned
experiment job cleanly in its FastAPI lifespan handler.


Synthetic jobs are deferred until isolated evaluation and complete serving/judge
usage accounting are supported. API and CLI requests are refused before a worker starts; the TUI
shows Synthetic disabled. Historical Synthetic job records remain readable.
Rescore judges saved historical executions and does not measure the current installed program;
its results are excluded from program-version trends. Replay measures the installed program.
Token caps use reported generative usage and cooperative safe-point checks, with in-flight overshoot;
metric-call budgets are separate and do not establish a dollar ceiling.
The readiness badge indicates readiness; counts and missing requirements appear in the Readiness tab.
Bare `/optimize` opens Runs in fullscreen mode; the fallback prints guidance without starting a job.

`GET /improve/queue` accepts nonnegative `offset` and `limit` (1–200, default 50).
The response includes nullable `next_offset`; null means the final page.
The canonical `turn_complete` payload includes the effective `replay_snapshot_selected` boolean.
