# Canonical Backend Guide

This directory is the sole Fleet RLM Python backend.
Root repository workflow and validation rules remain authoritative from
[AGENTS.md](../../AGENTS.md); this guide narrows them for backend work.

This guide records the current backend. Unchecked targets in `PLANS.md` are not
implemented until their corresponding phase exit gates pass; preserve current
`history`, local-scope, and runtime-composition guidance until those changes
are actually delivered and reviewed.

## Architecture

- Read `CONTEXT.md` before changing domain names or lifecycle ownership.
- Keep FastAPI routes as HTTP translators. Retrieve application modules through
  aliases in `api/dependencies.py`; construct process resources only in lifespan
  composition.
- Keep Daytona SDK imports inside `daytona/`.
- Create a fresh native DSPy RLM per Turn through `rlm.dspy_contract`. Daytona supplies a fresh custom interpreter;
  Deno passes `interpreter=None` so DSPy constructs its default Deno/Pyodide
  interpreter. Preserve `history: list[dict]` and call the supported
  `await rlm.acall(**named_inputs)` surface.
- Compose zero to four authorized Skills through the host-owned capability
  registry. Capability tools may be plain callables or explicit `dspy.Tool`
  objects; HTTP never supplies executable Python or serialized objects.
- Keep Runtime Events transport-neutral. `api/sse.py` alone projects the public
  AI SDK UI 7 v1 stream. Observe live code/output at the Daytona interpreter
  boundary and host-tool activity through fresh wrapped `dspy.Tool` objects;
  supplement missing details from the completed native trajectory.
- Bound and sanitize public code, output, and post-run reasoning. Project
  protected tool inputs and outputs without publishing attachment, Skill,
  artifact, or subquery bodies.
- `TurnCoordinator` owns candidate promotion, Turn Commit, terminal projection,
  final UI part ordering, and Interpreter Lease release.
- `CommittedTurn` is the only replay source. A successful Daytona Run may retain
  one private commit-gated `result.json` derivative under its unique Run path;
  Deno has no result-snapshot sink, and the derivative is not an Artifact or API
  resource.
- Alembic owns live schema evolution. `create_tables` is test/offline-only.
- Do not add `/api/v1`, WebSocket, legacy-backend, or environment compatibility
  aliases.

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

Live L1/L2 requires explicit `FLEET_LIVE=1` and provider credentials. Never use
Daytona credentials as Fleet API bearer tokens.

## Generated contract

`openapi.yaml` is the backend-only generated contract. Run `make api-sync`.
