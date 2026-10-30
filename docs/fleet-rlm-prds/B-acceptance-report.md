# PRD B implementation acceptance report

Implementation date: 29 October 2026. Scope: [canonical Turn result](B-canonical-turn-result.md).

## Coordinates and native reuse

Start and final checkout HEAD: `45795ffe26ef1d61ce671a6b5beb917a74b9d1c0`.
Implementation is an uncommitted working-tree patch; no commit, push or deployment
was requested. The pre-existing untracked `.codex/` and PRD documents were
preserved. Only B's status/checklist and this report change the plan documents.

The unchanged lock pins DSPy 3.4.1, GEPA 0.1.4, FastAPI 0.162.0, Pydantic 2.13.5,
MLflow 3.17.2 and Daytona 0.224.0. Native DSPy `Prediction`, scoped context,
callbacks and tracing remain in place. Store retains its existing single
transaction, idempotent connection retry, claim lookup, terminal-row lookup and
completed-row downgrade guard. No SQL migration or new persisted status was added.

## Ownership and contract changes

Session is the one successful-payload assembler. Its commit method returns the
completion after confirmation, with separate nested-data ownership for caller
records, Store, telemetry and delivery. The coordinator's second construction was
deleted, rather than relocated. New result JSON includes `turn_id`; completed
historical reads obtain it from the enclosing row and preserve unknown fields.
The [single field/legacy inventory](../reference/turn-results.md) records every
result, record-only and envelope field and its absence/null policy.

The coordinator uses two local helpers for exception resolution and one-time
execution finalization across its existing inner/outer scopes. The API supplies
one in-memory Event and lock to serialize disconnect cancellation with claim,
commit, readback and success signaling. These primitives cover a concrete race
that an outcome queued later through SSE cannot cover. The asyncio worker wrapper
also retrieves its exception so optional diagnostics cannot emit raw provider text.
There is no new runtime module, recovery service, DTO hierarchy or persistence
adapter. The runtime diff adds 189 lines and removes 116 (73 net); most added
patch lines are fault-injection tests, fixtures and documentation.

The optional `artifact_paths` argument is B's single F3 handoff. Omitting it uses
the existing listing. Supplied paths use the existing path validation and record policies and
sorting without relisting. This does not implement publication or prove immutable
filesystem bytes. C can consume the result while retaining the documented legacy
reader. Successful execution ends its deadline before optional observation;
failed execution still stops remote work before acknowledgment.

## Acceptance evidence

The initial ten new regressions failed against the original implementation before
the Session/coordinator edits, reproducing payload inequality and success-to-failure
paths. The final regressions use real Session policies and offline JSON-owning
Store fakes, with the existing Store transaction tests retained.

| ID | Evidence | Observed result |
|---|---|---|
| B-T1 | `test_canonical_completion_survives_postcommit_observation_faults`, managed-inventory/replay test | Live event content, stored JSON, same-key replay and status content agree (status adds only derived provenance state); one engine execution and one commit |
| B-T2 | Ten fault cases: phase outputs/exit, root outputs/attributes/final attribute/exit, combined inner/outer fault, trace update, note handling, finalization | Zero committed-success downgrades or cancellation requests; success reaches usable channel, with sanitized diagnostics |
| B-T3 | Direct terminal emit failure; poll and generator-close disconnect while Store has committed but not returned and during postcommit note handling | One durable success, no failed write or root cancellation, reconnect equality without another DSPy invocation |
| B-T4 | Execution failure, rejected lease and cancellation before commit; existing deadline/transport/teardown regressions | No false completion; cleanup precedes terminal outcome, `done` remains last; one finalization owner |
| B-T5 | Lost acknowledgment with completed readback; unavailable readback; separate running/missing/unavailable readbacks before proven completion | Claimed identity recovers provable success; one coordinator readback; unknown intent is retained; same-key requests do not reexecute |
| B-T6 | Frozen live success/failure and old-row fixtures, copy-isolation tests, provenance/deadline/provider-bound tests | Counters, nested budgets, trace/root IDs, null deadlines/provider bounds and unknown historical fields survive; only documented read normalization |

Focused validation: **211 passed** across `tests/api/test_turns.py`,
`tests/lite/test_session.py`, `tests/store/test_store.py`,
`tests/store/test_history.py` and `tests/api/test_routes.py`. An additional focused
post-finalization check passed all 23 selected completion/disconnect/cleanup cases.
One upstream MLflow/SQLAlchemy deprecation warning remains in focused tests.

Full `make check`: **passed**. The final run completed with **1,644 passed,
1 skipped**, and **83% overall branch-aware coverage** (80% required). It also
passed Ruff, formatting (138 files), typing, local documentation links (39 Markdown
files), OpenAPI consistency and source-distribution/wheel builds. The 150 warnings
are upstream DSPy, MLflow/SQLAlchemy and pandas deprecations.

`git diff --check`, `make api-sync` and `make api-check` pass independently.
OpenAPI regeneration updates the disconnect description without changing schema
shapes; result JSON remains extensible. The final documentation-only status/report
updates were checked again with `make check-docs` and `git diff --check`.

## Limits and rollback

Validation is offline, including the repository's direct, primary RLM, Flex and
managed-evaluation boundary tests. No live Daytona, paid model, production
database or evaluation canary was authorized or run. Provider latency/cost and
filesystem publication guarantees are unmeasured. A successful database commit
is not a distributed exactly-once delivery guarantee.

The new lock governs API disconnect cancellation. Existing watchdog and lease
fencing retain their own owners; this patch does not redesign a lease lost or
deadline expired concurrently with an already-running database transaction.
Those sources may stop remote resources, but cannot make the coordinator rewrite
a provably committed success. Store changes needed for stronger cross-resource
atomicity belong to separately reviewed lifecycle work.

Rollback Session, coordinator and API signal/caller changes together. Retain
completed rows and additive `turn_id` keys; do not rewrite outcomes. Preserve the
legacy reader if C later introduces strict contracts. The implementation does not
restore live Python state from stored history. F2 publication and F3 capture
semantics remain deferred, with only the optional handoff supplied here.
