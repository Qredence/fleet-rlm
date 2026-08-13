# Canonical Backend Guide

This directory is the sole Fleet RLM Python backend. Root workflow and validation
rules remain authoritative from [AGENTS.md](../../AGENTS.md); this guide narrows
them for backend work. Current code, tests, committed policy, generated
contracts, and tracked docs remain authoritative.

## Architecture

- Read `CONTEXT.md` before changing domain names or lifecycle ownership.
- Keep FastAPI routes as HTTP translators. Retrieve runtime modules through
  aliases in `api/dependencies.py`; do not construct stores, engines, LMs, or
  provider clients in routes.
- `create_app()` installs routers, error/OpenAPI handlers, and the static
  in-memory bundled Skill catalog. Lifespan composition installs and disposes
  one complete Daytona or explicitly injected private-test inventory.
- Production startup resolves one required profile from `config/fleet.toml`.
  `config.py` owns strict runtime resolution; `config_policy.py` and the
  loopback-only `/api/settings` routes edit only non-secret policy for restart.
  Do not restore ambient environment aliases or expose referenced secret values.
- Keep Daytona SDK imports inside `daytona/`.
- Create a fresh native DSPy RLM per Turn through `rlm.dspy_contract`. Inject and
  `FinalOutput` protocol knowledge lives in `rlm.dspy_interpreter_contract`.
  Daytona supplies a fresh custom interpreter. Native calls use the supported
  `await rlm.acall(interpreter, **named_inputs)` surface; deterministic testing
  doubles remain keyword-only. Fleet owns shutdown for caller-provided
  interpreters.
- `RecursiveRLMExecutor` owns bounded child delegation. Root receives
  `rlm_query` and ordered `rlm_query_batched`; a child receives only `rlm_query`
  plus native semantic sub-LM tools. The shared call budget is reserved under a
  lock, child LM runtimes are copied, and each child owns a fresh Sandbox lease.
- Every Signature receives request text, bounded `session_context`, bounded
  `skill_cards`, and bounded Attachment metadata. Older committed messages
  remain behind the Session-scoped `read_session_history` Tool.
- The default `FleetRLMSignature` uses strict Pydantic DTOs local to `rlm/`;
  `rlm.inputs` validates and JSON-serializes the bounded payload once before
  native `rlm.acall(interpreter, ...)`. Custom Skill Signatures retain JSON-compatible common
  input annotations and declared output schemas.
- Runtime-specific Session Workspace availability is bounded inside context;
  Daytona registers list/stat/paged-read/write/append/delete/edit workspace
  Tools (`delete_workspace_path`, `edit_workspace_text`, and the
  `delete_project_path`/`edit_project_text` pairs on the projects root) plus
  direct Workspace Artifact Candidate publication and the Workspace Memory
  Tools (`read_workspace_memory`, `remember`, `list_memories`, `search_memories`,
  `edit_memory`, `forget`, plus the `update_workspace_memory` back-compat alias). The former
  append/update-only "no delete Tool" invariant ended by user-approved WS-7
  deviation: delete/edit target regular files (delete also removes EMPTY
  directories; never recurses, never follows symlinks, FIFOs and other
  non-regular nodes fail closed), with optional `expected_sha256` checksum
  preconditions. Write/append preconditions compare and mutate inside one
  mounted Workspace agent operation using target locks plus inode revalidation;
  no separate host read introduces a cross-Sandbox TOCTOU window. Workspace Memory is
  the `memory/MEMORIES.md` log (migrated from the legacy root `MEMORIES.md`),
  not Session History. Listed rows always have one addressable id: v3 stores a
  fresh id and v1 derives one from canonical text plus occurrence; duplicate ids fail closed, while an
  edit upgrades legacy rows to v3 preserving that id and timestamp. `remember` is
  idempotent for the same record, and edit/forget perform one mounted-agent
  read-modify-publish operation. Each Turn additionally receives its bounded
  4 KiB relevant+recent `workspace_memory tail` digest inside
  `session_context`.
- Resolve zero to four exact Skill selections against the immutable bundled
  catalog. Skill instructions and resources load progressively; bundled Skills
  never register executable tools. Runtime execution uses a typed
  `RLMExecutionSpec` containing explicit host-owned `dspy.Tool` objects.
- Keep Runtime Events transport-neutral. `api/sse.py` alone projects the public
  AI SDK UI v1 stream. Observe live code/output at the interpreter boundary and
  host-tool activity through fresh wrapped Tools; use the completed native
  trajectory only to fill observation gaps.
- The interpreter returns bounded corrective feedback instead of stopping on
  recoverable mistakes: empty or oversized code returns a direct repair message,
  and a repeated identical action that makes no progress returns one bounded
  repair before a second consecutive identical repeat raises `RunNoProgressError`
  and terminates the Turn. Any different action resets the counter.
- Preserve explicit public reasoning, generated code, output, and successful
  answer text up to configured bounds. Tool event views expose only bounded
  allowlisted metadata; Tools without a view expose no arguments or results.
  Provider failures use closed public messages.
- Databricks MLflow (`observability/mlflow_runtime.py`, `tracing.py`, and
  `turn_tracing.py`) is fail-soft engineering observability controlled by the
  selected TOML profile. `mlflow_runtime.py` is owned by FastAPI lifespan for
  explicit startup state and flush; `tracing.py` owns configuration and
  sanitation; `turn_tracing.py` owns Turn spans. Tracing must never change Turn
  outcomes. When policy enables trace
  exposure, public `traceId` may appear only as optional `messageMetadata` on
  existing `start`/`finish` chunks — never as a new RuntimeEvent kind or
  credential-bearing payload.
- `RunLifecycle.finish()` owns result-snapshot handling, Artifact publication,
  and atomic Turn Commit or failure settlement. `TurnCoordinator` owns stream
  orchestration, terminal ordering, heartbeat coordination, and final resource
  cleanup.
- `RunLifecycleService` translates lifecycle outcomes into typed Claim commands;
  in-memory and SQL Run state stores share one `transition_claim()` operation and
  pure policy, while successful commit and cancellation remain separate. Internal
  persistence mapping, claim/liveness, final-state, and query helpers stay behind
  the same facade; only facades own locks, AsyncSessions, and transactions.
- `OwnedPostCommitMemoryPromotion` keeps a started promotion task owned until it
  settles before Run resources release; its wait has a bounded post-commit
  deadline, but it never detaches work that still needs the Run lease.
- `CommittedTurn` is the only replay source. Durable assistant content is
  validated through the closed Pydantic `AssistantPart` discriminated union in
  `sessions/assistant_parts.py`; keep that durable vocabulary separate from
  live SSE transport chunks. `api/ui_stream.py` owns the typed live transport
  union consumed by SSE and OpenAPI. A successful Daytona Run may retain one private
  commit-gated `result.json` derivative; the derivative is not an Artifact or
  API resource.
- Session Workspace files are immediate private state under
  `sessions/{session_id}/workspace/`. They survive failed Runs and Sandbox
  replacement independently of Turn Commit. Use paged reads for large files,
  `append_workspace_text` for incremental output, and
  `write_workspace_text(..., overwrite=True)` for replacement;
  `delete_workspace_path` and `edit_workspace_text` mutate the same scope
  (WS-7 deviation; strict file-and-empty-directory semantics). Direct
  Workspace Artifact publication stages bytes privately; no Tool deletes or
  edits Attachments or Artifacts. Workspace Memory appends are immediate workspace-wide
  state under `memory/MEMORIES.md` and are independent of Turn Commit;
  the Daytona-only `/api/volume/tree` route exposes only a bounded read-only
  logical path view, not a general-purpose filesystem browser.
  Deployment contract: append serialization is process-local to one Fleet
  host; statefulness lives in the shared Volume and survives Sandbox
  replacement; appends from multiple host processes are not coordinated
  today.
- Daytona composition owns one process-scoped `AsyncDaytona`. Provisioning,
  lifecycle, filesystem, and FastAPI-facing Workspace operations remain native
  async. Only DSPy's synchronous interpreter and host-tool execution receive
  the explicit allowlisted sync view on the DSPy worker thread. Grouped Volume
  operations use one ephemeral, Workspace-subpath-mounted I/O Sandbox; the
  gateway deletes it when the operation context exits.
- Alembic owns live schema evolution. `create_tables` is test/local SQLite only.
- Do not add `/api/v1`, WebSocket, legacy-backend, auth, or environment aliases.

## Commands

```bash
uv run fleet cli
uv run fleet doctor daytona
uv run fleet web
uv run fleet-rlm serve-api
uv run pytest tests/unit/backend tests/contracts/backend -q
uv run ty check src
uv run python scripts/check_codebase_tree.py
make api-check
```

Credentialed Daytona proof uses the selected TOML policy's
`runtime.live_enabled` switch (true by default; false fails closed). Use the
named MVP verifier and live durability/workspace tests; never use Daytona
credentials as Fleet API bearer tokens. The command itself remains an explicit
operator action.

## Generated contract

`make api-sync` owns both `openapi.yaml` and
`tools/fleet-tui/src/generated/openapi.ts`. Do not hand-edit either artifact;
run `make api-check` after any HTTP contract change.

## DSPy RLM history contract

Under the supported, pinned DSPy 3.3.x line, `RLM` owns one immutable
`REPLHistory` per
`RLM.acall`/`RLM.forward` invocation. The caller-owned Daytona interpreter
provides execution state and lifecycle only; it must not mirror or reset
DSPy's history. Completed interactions are exposed by DSPy as
`Prediction.trajectory`, which Fleet may normalize for SSE and durable
observation projection without mutating the prediction.
