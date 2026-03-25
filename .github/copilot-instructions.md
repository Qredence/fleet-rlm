# fleet-rlm workspace instructions

Use this file as a lightweight bootstrap. The detailed source of truth lives in the repo's `AGENTS.md` hierarchy and existing docs.

## Start here

- Read `AGENTS.md` first for repo-wide rules and validation lanes.
- Then load the closest area guide before editing code:
	- `src/fleet_rlm/AGENTS.md` for backend, runtime, CLI, API, and provider work
	- `src/frontend/AGENTS.md` for React, routes, websocket UI, and generated API types
- When docs drift from code, trust `Makefile`, `pyproject.toml`, `src/frontend/package.json`, and `openapi.yaml`.

## Build and test

- Use `uv` for Python environment management and commands.
- Use `pnpm` for frontend work in `src/frontend/` (this repo overrides the common Bun default).
- Main local entrypoint: `uv run fleet web`.
- Canonical validation lanes:
	- `make test-fast`
	- `make quality-gate`
	- `make release-check`
- Frontend loop from `src/frontend/`:
	- `pnpm install --frozen-lockfile`
	- `pnpm run api:check`
	- `pnpm run type-check`
	- `pnpm run lint`
	- `pnpm run test:unit`
	- `pnpm run build`
- Before debugging stale lint/type/test failures, clear caches (`.ruff_cache`, `__pycache__`, `.pytest_cache`) and run `pre-commit clean`.

## Architecture guardrails

- Supported app surfaces are `Workbench`, `Volumes`, and `Settings`; retired routes should continue to fall through to `/404`.
- Keep transport and route wiring in `src/fleet_rlm/api/`; keep runtime behavior in `src/fleet_rlm/runtime/`; keep provider-specific logic under `src/fleet_rlm/integrations/providers/*`.
- `modal_chat` is the default runtime mode. `daytona_pilot` stays on the shared ReAct + `dspy.RLM` architecture rather than a separate orchestration stack.
- Treat `openapi.yaml` as the canonical HTTP contract. If you change request/response shapes or routes, keep frontend OpenAPI artifacts in sync.

## Project conventions and pitfalls

- Python target is 3.10+ with explicit type hints; use `ty`, not `mypy`.
- Do not hand-edit generated frontend files such as `src/frontend/src/routeTree.gen.ts` or `src/frontend/src/lib/rlm-api/generated/openapi.ts`.
- Reuse existing helpers and ownership boundaries instead of introducing parallel compatibility layers or duplicate utilities.
- For document ingestion, do not use raw `Path.read_text()` on PDFs or other binary docs; use the existing document-reading pipeline.
- For Daytona work, use `DAYTONA_API_URL` (not `DAYTONA_API_BASE_URL`) and validate with `uv run fleet-rlm daytona-smoke --repo <url> [--ref <branch>]` before deeper debugging.

## Where to look instead of duplicating guidance

- Repo map and validation: `AGENTS.md`
- Backend architecture/contracts: `src/fleet_rlm/AGENTS.md`
- Frontend architecture/contracts: `src/frontend/AGENTS.md`
- Product and contributor overview: `README.md`, `CONTRIBUTING.md`
- Documentation index: `docs/index.md`
- Setup and commands: `docs/how-to-guides/developer-setup.md`, `docs/how-to-guides/installation.md`
- Testing guidance: `docs/how-to-guides/testing-strategy.md`
- Architecture references: `docs/architecture.md`, `docs/reference/module-map.md`, `docs/reference/codebase-map.md`
