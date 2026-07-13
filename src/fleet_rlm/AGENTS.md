# Canonical Backend Guide

This directory is the sole Fleet RLM Python backend.
Root repository workflow and validation rules remain authoritative from
[AGENTS.md](../../AGENTS.md); this guide narrows them for backend work.

## Architecture

- Read `CONTEXT.md` before changing domain names or lifecycle ownership.
- Keep FastAPI routes as HTTP translators. Retrieve application modules through
  aliases in `api/dependencies.py`; construct process resources only in lifespan
  composition.
- Keep Daytona SDK imports inside `daytona/`.
- Create a fresh DSPy RLM and custom interpreter per Turn. Preserve
  `history: list[dict]` and call the supported `await rlm.acall(**named_inputs)` surface.
- Compose zero to four authorized Skills through the host-owned capability
  registry. Capability tools may be plain callables or explicit `dspy.Tool`
  objects; HTTP never supplies executable Python or serialized objects.
- Keep Runtime Events transport-neutral. `api/sse.py` alone projects the public
  AI SDK UI 7 v1 stream; sanitized RLM-authored reasoning is public, while
  provider-hidden chain-of-thought, full prompts, paths, and secrets are not.
- `TurnCoordinator` owns candidate promotion, Turn Commit, terminal projection,
  final UI part ordering, and Interpreter Lease release.
- Alembic owns live schema evolution. `create_tables` is test/offline-only.
- Do not add `/api/v1`, WebSocket, legacy-backend, or environment compatibility
  aliases.

## Commands

```bash
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
