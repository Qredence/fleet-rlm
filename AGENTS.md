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
- Prefer running Python scripts/commands using `uv run` over raw `python3` or `python` (aligned with user CLI tooling preferences).
- Avoid introducing direct `litellm` usage in application code; reach LLM providers through `dspy.LM` instead.
- Prefer wire-protocol-named Literal unions (`openai_responses`, `openai_chat_completion`, `anthropic_messages`) over vendor-flavored or `_compatible`-suffixed provider-type enums, and keep LLM profiles flat (profile name, provider type, base endpoint, API key) rather than over-abstracting.

## Learned Workspace Facts

- Local development runs API on `:8000`, Vite dev server on `:5173`, and MLflow telemetry on `:5001` (Python 3.13+), standardizing streaming responses on `RuntimeEvent` (`runtime/events.py`) projected using `project_chat`.
- The Daytona-backed recursive chat runtime runs via `dspy.RLM` with support for major LLM providers and OpenAI-compatible models.
- Database schema drift checking (`alembic check`) requires importing all active SQLAlchemy models inside `migrations/env.py`.
- Custom IDE Canvases (.canvas.tsx) are designed for standalone analytical outputs, supporting category colors: `gray`, `purple`, `green`, `yellow`, `pink`, `blue`, and `orange`.
- The Agent Elements conversation column width is controlled via `--an-max-width` in `src/frontend/src/components/agent-elements/agent-ui.css` rather than raw Tailwind class overrides.
- FastAPI Cloud's packaging and deployment rely on `.fastapicloudignore` (preceding `.gitignore`) which must explicitly allow the compiled Web UI build output directory (`!src/frontend/dist/` or `!src/frontend/dist/client`) to be served.
- Authentication uses `@neondatabase/auth-ui` locked to Neon Project ID `old-bird-44339002` (`https://ep-broad-water-al4k5bh7.neonauth.c-3.eu-central-1.aws.neon.tech/neondb/auth`) with JWT EdDSA token verification, trusting origin `https://fleet-rlm.fastapicloud.dev`.
- Tenant BYOK data is secured via Postgres RLS on `llm_provider_profiles` and Fernet encrypted via `FLEET_SECRET_ENCRYPTION_KEY` in `neon` auth mode, skipping empty/masked values to prevent key wipes.
- Scratch or evaluation directories (`mlartifacts/`, `artifacts/`, `logs/`, `FINDINGS_REPORT.md`) are untracked by `.gitignore`. Codex hooks must be configured inline under `[hooks]` in `.codex/config.toml`, as `.codex/hooks.json` is deprecated.
- The backend falls back to local SQLite store (`integrations/local_store.py`) if `DATABASE_URL` is unset, unless `DATABASE_REQUIRED=true` (staging/production). Local settings are patchable via `PATCH /api/v1/runtime/settings` (local-only).
- To prevent cascading timeouts and state leakage in test suites: intercept/stub out external auth network calls like `NeonAuthProvider._fetch_jwks` returning empty keys, and reset global singletons/semaphores using aggressive `autouse` teardown fixtures.
- Under DSPy 3.3.Xb (normalized LM API), any `BaseLM` or bounded LM must have its provider explicitly resolved (via prefix or `provider` kwargs). Prefer stock `dspy.LM` with stateless config overrides passed directly to predictors/LM calls (using `dspy.settings.context` if needed) over stateful `copy()` or custom wrappers to ensure thread/session safety.
