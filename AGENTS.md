# Repository Agent Map

`fleet-rlm` is a Web UI-first adaptive recursive language model workspace built around a Daytona-backed DSPy ReAct agent runtime.

## Operating Model

- Use closest applicable `AGENTS.md` before editing files; deeper guides override this map.
- Keep repo docs, generated contracts, and `.codex/` actions aligned with implementation changes.
- Prefer smallest validation lane that covers the change, then escalate when contracts move.
- Do not hand-edit generated/synced artifacts; use the commands listed below.
- Do not mutate user-level Codex config. Ask before deploy, push, migrations, or deletion.

## Reading Path

1. `docs/agent-harness/README.md` - harness model, reading order, and quality bar.
2. `docs/agent-harness/feedback-loop.md` - local Codex loop and report expectations.
3. `docs/agent-harness/architecture-invariants.md` - backend, frontend, generated-file rules.
4. `docs/reference/codebase-map.md` - source layout and ownership map.
5. `docs/how-to-guides/testing-strategy.md` - validation lanes by change type.

## Deeper Agent Guides

- `src/fleet_rlm/AGENTS.md` - backend, runtime, API, persistence, Daytona, and package rules.
- `src/frontend/AGENTS.md` - React, TanStack Router, Vite+, styling, and frontend checks.

## Durable Detail Locations

- Auth, DB, websocket, runtime, and deploy details live in `src/fleet_rlm/AGENTS.md` or matching docs.
- Frontend routing, Agent Elements, styling, session restore, and package rules live in `src/frontend/AGENTS.md`.
- Local Codex actions, ports, browser smoke expectations, and tool preferences live in `.codex/` and loop docs.

## Setup

```bash
uv sync --all-extras --dev
cd src/frontend && pnpm install --frozen-lockfile
zsh .codex/workspace-bootstrap.zsh
```

## Run

```bash
uv run fleet web
uv run fleet-rlm serve-api --port 8000
pnpm --filter frontend dev
```

## Validation

```bash
make format-check && make lint && make typecheck && make test
```

## Generated Artifacts

Do not hand-edit: `openapi.yaml`, `src/frontend/src/lib/rlm-api/generated/openapi.ts`, `src/frontend/openapi/fleet-rlm.openapi.yaml`, `src/frontend/src/routeTree.gen.ts`, `src/frontend/dist`, `src/fleet_rlm/ui/dist`.

Use: `make api-sync`, `make api-check`, `make build-ui`.

## Drift Checks

Run `make check-docs` when docs, commands, Codex config, generated contracts, or scripts change.

## Learned User Preferences

- Always use the `zsh` terminal profile for CLI commands.
- Secure production deployments strictly on Bring-Your-Own-Key (BYOK) model; never leak server-level secrets (like Gemini API keys or Daytona keys) to authenticated users.
- Do not edit `.plan.md` or any attached implementation plans while executing a task, prioritizing marked-in-progress to-dos sequentially.
- Support public clones and local development by enabling local instances to connect to the FastAPI Cloud hosted Neon Auth.
- Always include direct absolute markdown links to `.canvas.tsx` files when creating or mentioning an IDE Canvas.
- Prefer preserving Agent Elements design tokens (`--an-max-width`) rather than introducing arbitrary Tailwind classes for chat width adjustments.
- Use `pnpm run check` in `src/frontend` to verify formats, types, lints, and unit tests in a single pass.

## Learned Workspace Facts

- Local development runs API on `:8000`, Vite dev server on `:5173`, and MLflow on `:5001` (Python 3.13+).
- The Daytona-backed recursive chat runtime runs via `dspy.RLM` with support for major LLM providers and OpenAI-compatible models.
- Database schema drift checking (`alembic check`) requires importing all active SQLAlchemy models inside `migrations/env.py`.
- Custom IDE Canvases (.canvas.tsx) are designed for standalone analytical outputs, supporting category colors: `gray`, `purple`, `green`, `yellow`, `pink`, `blue`, and `orange`.
- Streaming responses in the runtime are standardized on `RuntimeEvent` (`runtime/events.py`) and projected using `project_chat`.
- The Agent Elements conversation column width can be adjusted by changing the design token `--an-max-width` in `src/frontend/src/components/agent-elements/agent-ui.css`.
- FastAPI Cloud's packaging engine relies on a `.fastapicloudignore` file in the repository root, which takes absolute precedence over `.gitignore` during deployment.
- Serving compiled Web UI on FastAPI Cloud requires `.fastapicloudignore` explicitly allowing the frontend build output directory (`!src/frontend/dist/` or `!src/frontend/dist/client`).
- The production Neon Auth instance URL for the deployed FastAPI Cloud app is `https://ep-broad-water-al4k5bh7.neonauth.c-3.eu-central-1.aws.neon.tech/neondb/auth`.
- Authentication is locked specifically to Neon Project ID `old-bird-44339002` using `@neondatabase/auth-ui` on catch-all paths with JWT EdDSA token verification.
- `https://fleet-rlm.fastapicloud.dev` (and the variant without trailing slash) is a configured trusted origin in Neon project `old-bird-44339002` to prevent INVALID_ORIGIN rejection.
- Postgres Row-Level Security (RLS) on `llm_provider_profiles` secures user BYOK data via context-derived tenant/user/workspace scope.
