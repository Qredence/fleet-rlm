# HTTP API

Start with `uv run fleet serve`. The API defaults to `127.0.0.1:8000` and has no
caller authentication. Non-loopback binding requires explicit opt-in and a
separately secured boundary. Exact fields and validation are in
[`openapi.yaml`](../../openapi.yaml); interactive docs are at `/docs`.

## Routes

Paths below use `/sessions/{id}` as the Session prefix.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/health`, `/models` | Readiness/tracking destination; authorized chat-model discovery |
| GET | `/sessions` | Persisted Sessions |
| DELETE | `/sessions/{id}` | Stop runtime/release lease, retaining history; busy returns 409 |
| GET | `/sessions/{id}/config` | Safe effective settings and provenance |
| GET, PUT | `/sessions/{id}/mode` | Persisted execution mode |
| PUT | `/sessions/{id}/model` | Persist root/sub model selection |
| PUT, DELETE | `/sessions/{id}/context` | Select/clear content-addressed dataset |
| POST | `/sessions/{id}/turn` | Execute a Turn with SSE progress |
| GET | `/sessions/{id}/history`, `/sessions/{id}/turns/{turn_id}` | Durable history/outcome without a runtime lease |
| GET | `/sessions/{id}/files` | Workspace/artifact inventory |
| POST | `/sessions/{id}/attach` | Multipart `file` upload to workspace |
| GET | `/sessions/{id}/workspace/tree`, `/sessions/{id}/workspace/stat/{path}` | Refresh listing or inspect size/digest |
| GET, PUT, DELETE | `/sessions/{id}/workspace/files/{path}` | Read/write/delete workspace bytes |
| GET | `/sessions/{id}/memory` | Retrieve scoped memories, optional `q` |
| PUT, DELETE | `/sessions/{id}/memory/{memory_id}` | Write/review/delete memory |
| GET | `/skills?session_id=...` | Skill catalog |
| POST | `/skills/push`, `/skills/pull` | Sync local user Skills or export sandbox Skills |

The `/health` response identifies the local MLflow OSS server with
`mlflow.mode = "local"`, the tracking URI, experiment names, readiness, and the
initialized runtime experiment ID. Clients also check the runtime digest before
reusing the API.

Mutating operations and workspace reads share the Session lock with execution;
busy requests return 409 after two seconds. A PostgreSQL lease prevents concurrent
runtime owners. Managed roots are `files`, `artifacts`, `memory`, `skills`,
`buffers`, and `sessions`; symlinks are not traversed.

Workspace writes require exactly one non-null `content` or `content_base64`.
An empty string writes an empty file. Dataset hashes are rechecked at execution.
Local user Skills live in `.fleet/skills/`; `.agents/skills/` is excluded.
Bundled names are reserved.

## Run a Turn

```json
{"task":"Analyze the attached CSV and verify the totals.","idempotency_key":"analysis-1"}
```

| Optional field | Behavior |
| --- | --- |
| `timeout_seconds` | `0` disables Fleet's deadline; integer 10–3600 sets one; omitted/null inherits configuration |
| `record_replay` | Default true; capture bounded immutable inputs for later review |
| `requested_skills` | Up to three unique exact Skill names, in caller order |
| `message_id`, `file_ids` | Stable chat message and published attachments |
| `regenerates_turn_id` | Source Turn for a new attempt with a new idempotency key |
| `model`, `mode` | Session selection applied under the admission lock |

Required Skills resolve from trusted host sources, with bundled names taking
precedence. Each full instruction is limited to 20,000 characters; required and
up to three additional optional preloads share 60,000 characters. Hybrid forces
RLM, explicit RLM/Flex stays explicit, and Direct refuses required intent. Results
record names, sources and digests; Skills grant no additional permissions/budget.

Replay capture stores at most 8 MB of workspace input plus bounded conversation,
memory, dataset and Skills state after successful completion. Oversized input skips
capture with `replay_skipped`. Use only data approved for retention. See
[evaluation](evaluation.md#reviewed-replays-and-readiness).

### SSE contract

The response is `text/event-stream`. Each JSON data frame has `version: 1`,
`event`, `session_id`, `turn_id`, `agent_id`, `parent_agent_id`, `sandbox_id`,
monotonic `seq`, `timestamp`, and nested `payload`. SSE `id` follows `seq`.

```text
data: {"version":1,"event":"done","session_id":"s1","turn_id":"t1","agent_id":null,"parent_agent_id":null,"sandbox_id":null,"seq":3,"timestamp":0.0,"payload":{}}
id: 3

```

Preparation emits `turn_status`; activity includes module/model/tool start/end,
REPL code/output, routing, child lifecycle and budget updates. Correlation uses
`call_id`/`parent_call_id` and `execution_id`. Usage appears only when reported.
Tool previews are bounded/redacted; provider reasoning may stream separately.
These previews are not a complete secret-filtering boundary.

Exactly one outcome (`turn_complete`, `error`, or `turn_cancelled`) precedes
`done` after teardown. Activity is best effort and may drop under pressure;
terminal events apply bounded backpressure. SSE comments keep idle connections
alive. This stream is not a durable activity log.

Success commits once and retains the warm root. Failure/cancellation fences work
and stops children/root before acknowledgment; unconfirmed cleanup blocks reuse.
Disconnect before commit requests cancellation and drains the worker. Disconnect
after commit preserves success. Recover lost completion using the status route
or the same idempotency key. See [Turn results](turn-results.md).

Completed idempotent requests return their original result without executing
again. Changed intent conflicts; failed/running/uncertain attempts never replay
side effects. Completed required-Skill intent replays the original receipt even
if source files changed. Use a new key to execute changed content.

## Durable chat and files

| Method | Session-relative path | Purpose |
| --- | --- | --- |
| GET | `/messages?before=<sequence>&limit=100` | Chronological durable messages and attachments |
| POST | `/messages/{message_id}/activity` | Replace bounded saved activity on a completed assistant message |
| GET | `/updates` | Read-only saved-state SSE; reconnect refreshes state |
| POST | `/attachments` | Multipart upload to private storage; returns a stable file ID |
| GET | `/published-files`, `/published-files/{file_id}` | Metadata or private download redirect |
| GET | `/workspace/index` | Passive cached inventory without starting Daytona |

`/updates` neither owns execution nor cancels on disconnect. `/workspace/tree`
explicitly refreshes inventory and may start Daytona. Published downloads need no
sandbox. See [database](database.md) for file ownership and persistence.

## Improve

These global routes do not acquire a Session runtime lease. Queue previews cover
all Sessions; protect them at the same boundary as the API.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/improve/readiness` | Counts, split sizes, missing requirements and installed-program checks |
| GET | `/improve/queue?offset=0&limit=50` | Captured Turns and labels; limit 1–200, nullable `next_offset` |
| POST | `/improve/labels` | Label by `turn_id`; resolve trace and synchronize MLflow/PostgreSQL |
| GET, POST | `/improve/jobs` | List jobs or submit idempotent `request_id` and options |
| GET | `/improve/jobs/{run_id}` | Job snapshot and bounded comparison |
| POST | `/improve/jobs/{run_id}/cancel` | Cooperative cancellation |
| POST | `/improve/jobs/{run_id}/review` | Explicit candidate approval/rejection |
| GET | `/improve/history` | Approved versions and evaluation results |
| GET, PUT | `/improve/settings` | Auto-run thresholds/budgets and default token cap |

Job kinds are `optimize`, `evaluate_rescore` and `evaluate_replay`;
`evaluate_synthetic` is refused. One active job/review is allowed without queuing.
Same request ID/options returns the existing job; changed options return 409.
Jobs continue after client disconnect. Poll snapshots for progress; terminal status
follows process exit and output draining. Token caps are cooperative, not cost caps.

Labels accept status, expected answer/values, critical and group. Unset fields
preserve existing gold; an approval without a prior expected answer uses the full
stored answer. Unknown Turn returns 404, invalid/missing trace 422, partial sync
503 with retry guidance. Settings updates merge supplied fields; null restores an
environment default. Auto-run defaults off, requires a nonzero token cap, and never
installs candidates. See [evaluation](evaluation.md) for gates and worker ownership.
