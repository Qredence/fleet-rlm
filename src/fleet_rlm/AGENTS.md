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
  Daytona supplies a fresh custom interpreter. Call only the supported
  `await rlm.acall(**named_inputs)` surface.
- Every Signature receives request text, bounded `session_context`, bounded
  `skill_cards`, and bounded Attachment metadata. Older committed messages
  remain behind the Session-scoped `read_session_history` Tool.
- The default `FleetRLMSignature` uses strict Pydantic DTOs local to `rlm/`;
  `rlm.inputs` validates and JSON-serializes the bounded payload once before
  native `rlm.acall()`. Custom Skill Signatures retain JSON-compatible common
  input annotations and declared output schemas.
- Runtime-specific Session Workspace availability is bounded inside context;
  Daytona registers list/stat/paged-read/write/append workspace Tools plus
  direct Workspace Artifact Candidate publication and the on-demand
  `read_workspace_memory`/`update_workspace_memory` Tools. Session Workspace is
  append/update-only; there is no delete Tool. Workspace Memory is the fixed
  root `MEMORIES.md` log, not Session History or a Turn-start prompt payload.
- Resolve zero to four exact Skill selections against the immutable bundled
  catalog. Skill instructions and resources load progressively; bundled Skills
  never register executable tools. Runtime execution uses a typed
  `RLMExecutionSpec` containing explicit host-owned `dspy.Tool` objects.
- Keep Runtime Events transport-neutral. `api/sse.py` alone projects the public
  AI SDK UI v1 stream. Observe live code/output at the interpreter boundary and
  host-tool activity through fresh wrapped Tools; use the completed native
  trajectory only to fill observation gaps.
- Preserve explicit public reasoning, generated code, output, and successful
  answer text up to configured bounds. Tool event views expose only bounded
  allowlisted metadata; Tools without a view expose no arguments or results.
  Provider failures use closed public messages.
- Databricks MLflow (`observability/tracing.py`, `observability/turn_tracing.py`)
  is fail-soft engineering observability controlled by the selected TOML
  profile. It must never change Turn outcomes. When policy enables trace
  exposure, public `traceId` may appear only as optional `messageMetadata` on
  existing `start`/`finish` chunks — never as a new RuntimeEvent kind or
  credential-bearing payload.
- `TurnLifecycle.finish()` owns result-snapshot handling, Artifact publication,
  and atomic Turn Commit or failure settlement. `TurnCoordinator` owns stream
  orchestration, terminal ordering, heartbeat coordination, and final resource
  cleanup.
- `TurnLifecycleService` translates lifecycle outcomes into typed Claim commands;
  in-memory and SQL Turn stores share one `transition_claim()` operation and
  pure policy, while successful commit and cancellation remain separate.
- `CommittedTurn` is the only replay source. A successful Daytona Run may retain
  one private commit-gated `result.json` derivative; the derivative is not an
  Artifact or API resource.
- Session Workspace files are immediate private state under
  `sessions/{session_id}/workspace/`. They survive failed Runs and Sandbox
  replacement independently of Turn Commit. Use paged reads for large files,
  `append_workspace_text` for incremental output, and
  `write_workspace_text(..., overwrite=True)` for replacement. Direct
  Workspace Artifact publication stages bytes privately; deletion is not
  exposed as a Tool. Workspace Memory appends are immediate workspace-wide
  state under the fixed `MEMORIES.md` root and are independent of Turn Commit;
  the Daytona-only `/api/volume/tree` route exposes only a bounded read-only
  logical path view, not a general-purpose filesystem browser.
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
uv run ty check src/fleet_rlm
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
