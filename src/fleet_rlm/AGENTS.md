# Canonical Backend Guide

This directory is the sole Fleet RLM Python backend. Root workflow and validation
rules remain authoritative from [AGENTS.md](../../AGENTS.md); this guide narrows
them for backend work. Current code and tests outrank the local phase record in
`PLANS.md`.

## Architecture

- Read `CONTEXT.md` before changing domain names or lifecycle ownership.
- Keep FastAPI routes as HTTP translators. Retrieve runtime modules through
  aliases in `api/dependencies.py`; do not construct stores, engines, LMs, or
  provider clients in routes.
- `create_app()` installs routers, error/OpenAPI handlers, and the static
  in-memory bundled Skill catalog. Lifespan composition installs and disposes
  one complete Deno, Daytona, or explicitly injected private-test inventory.
- Keep Daytona SDK imports inside `daytona/`.
- Create a fresh native DSPy RLM per Turn through `rlm.dspy_contract`. Daytona
  supplies a fresh custom interpreter; Deno passes `interpreter=None` so DSPy
  creates its default Deno/Pyodide interpreter. Call only the supported
  `await rlm.acall(**named_inputs)` surface.
- The default Signature receives request text, bounded `session_context`,
  authorized `skill_cards`, and bounded Attachment metadata. Older committed
  messages remain behind the Session-scoped `read_session_history` Tool.
  Custom Task Contracts receive only their declared host-bounded inputs.
- Runtime-specific Session Workspace availability is bounded inside context;
  Daytona registers four text workspace Tools (list, stat, read, write with
  overwrite) and Deno advertises the feature as unavailable. Session Workspace
  is append/update-only; there is no delete Tool.
- Compose zero to four exact authorized Skill selections. Skill instructions and
  resources load progressively. The production host `CapabilityRegistry` is an
  empty extension seam; bundled Skills do not register executable capabilities.
- The registry may accept a plain callable only at registration and normalizes
  it immediately. Turn blueprints, runners, and factories contain explicit
  `dspy.Tool` objects. HTTP never supplies executable Python or serialized tools.
- Keep Runtime Events transport-neutral. `api/sse.py` alone projects the public
  AI SDK UI v1 stream. Observe live code/output at the interpreter boundary and
  host-tool activity through fresh wrapped Tools; use the completed native
  trajectory only to fill observation gaps.
- Preserve explicit public reasoning, generated code, output, and successful
  answer text up to configured bounds. Tool event views expose only bounded
  allowlisted metadata; Tools without a view expose no arguments or results.
  Provider failures use closed public messages.
- `TurnLifecycle.finish()` owns result-snapshot handling, Artifact publication,
  and atomic Turn Commit or failure settlement. `TurnCoordinator` owns stream
  orchestration, terminal ordering, heartbeat coordination, and final resource
  cleanup.
- `CommittedTurn` is the only replay source. A successful Daytona Run may retain
  one private commit-gated `result.json` derivative; Deno has no result-snapshot
  sink, and the derivative is not an Artifact or API resource.
- Session Workspace files are immediate private state under
  `sessions/{session_id}/workspace/`. They survive failed Runs and Sandbox
  replacement independently of Turn Commit. Updates replace existing files via
  `write_workspace_text(..., overwrite=True)`; deletion is not exposed as a Tool.
- Alembic owns live schema evolution. `create_tables` is test/local SQLite only.
- Do not add `/api/v1`, WebSocket, legacy-backend, auth, or environment aliases.

## Commands

```bash
uv run fleet cli
uv run fleet deno
uv run fleet doctor daytona
uv run fleet web
uv run fleet-rlm serve-api
uv run pytest tests/unit/backend tests/contracts/backend -q
uv run ty check src/fleet_rlm
uv run python scripts/check_codebase_tree.py
make api-check
```

Credentialed Daytona proof requires explicit `FLEET_LIVE=1`. Use the named MVP
verifier and live durability/workspace tests; never use Daytona credentials as
Fleet API bearer tokens.

## Generated contract

`make api-sync` owns both `openapi.yaml` and
`tools/fleet-tui/src/generated/openapi.ts`. Do not hand-edit either artifact;
run `make api-check` after any HTTP contract change.
