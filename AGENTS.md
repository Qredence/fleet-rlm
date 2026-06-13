# Repository Agent Map

`fleet-rlm` is a Web UI-first adaptive recursive language model workspace built around a
Daytona-backed DSPy ReAct agent runtime. The root guide is intentionally short: it tells agents
where the durable rules live and which commands prove a change.

## Operating Model

- Treat `docs/agent-harness/README.md` as the agent-first hub for this repository.
- Use the closest applicable `AGENTS.md` before editing files; deeper guides override this map.
- Keep repo docs, generated contracts, and `.codex/` actions aligned with implementation changes.
- Prefer the smallest validation lane that covers the change, then escalate when contracts move.
- Do not hand-edit generated or synced artifacts; use the commands listed below.
- Do not mutate user-level Codex config. Repo-local automation belongs under `.codex/`.
- Ask before deploy, push, commit, migrations, or deletion unless explicitly requested.

## Reading Path

1. `docs/agent-harness/README.md` - harness model, reading order, and quality bar.
2. `docs/agent-harness/feedback-loop.md` - local Codex loop and report expectations.
3. `docs/agent-harness/architecture-invariants.md` - backend, frontend, generated-file rules.
4. `docs/agent-harness/drift-control.md` - checks that keep docs and contracts honest.
5. `docs/agent-harness/quality-score.md` - current quality grade and cleanup targets.
6. `docs/reference/codebase-map.md` - source layout and ownership map.
7. `docs/how-to-guides/testing-strategy.md` - validation lanes by change type.
8. `docs/how-to-guides/codex-environment.md` - `.codex` actions, hooks, and subagent roles.

## Deeper Agent Guides

- `src/fleet_rlm/AGENTS.md` - backend, runtime, API, persistence, Daytona, and package rules.
- `src/frontend/AGENTS.md` - React, TanStack Router, Vite+, styling, and frontend checks.

## Setup

```bash
# from repo root
uv sync --all-extras --dev
cd src/frontend && pnpm install --frozen-lockfile
```

```bash
# from repo root
zsh .codex/workspace-bootstrap.zsh
```

## Run

```bash
# from repo root
uv run fleet web
uv run fleet-rlm serve-api --port 8000
uv run fleet-rlm chat --trace-mode compact
```

```bash
# from src/frontend
pnpm run dev
```

## Validation

```bash
# from repo root
make format-check
make lint
make typecheck
make test
make check-docs
make quality-gate
```

Frontend-only lane:

```bash
# from src/frontend
pnpm run api:check
pnpm run type-check
pnpm run lint:robustness
pnpm run test:unit
pnpm run build
```

## Generated Artifacts

Do not hand-edit these files:

- `openapi.yaml`
- `src/frontend/src/lib/rlm-api/generated/openapi.ts`
- `src/frontend/openapi/fleet-rlm.openapi.yaml`
- `src/frontend/src/routeTree.gen.ts`
- `src/frontend/dist`
- `src/fleet_rlm/ui/dist`

Use these commands instead:

```bash
# from repo root
make api-sync
make api-check
make build-ui
```

## Drift Checks

Run the harness lane when docs, commands, Codex config, generated contracts, or script inventory change:

```bash
# from repo root
uv run python scripts/check_harness_engineering.py
uv run python scripts/check_agents_md_freshness.py
uv run python scripts/check_docs_quality.py
```

`make check-docs` runs the docs and harness checks together.

## Maintenance Checklist

When changing workflow, contracts, or architecture, update the durable docs before finishing:

- `AGENTS.md` and subsystem files under `docs/agent-harness/*`.
- `docs/README.md`, `docs/index.md`, and `docs/SUMMARY.md`.
- `scripts/README.md`, `Makefile`, `pyproject.toml`, and `src/frontend/package.json` when commands move.
- `openapi.yaml` and frontend API artifacts when backend request or response shapes move.

## Learned User Preferences

- Always use the `zsh` terminal profile when running CLI commands in the workspace.
- Always include direct absolute markdown links to `.canvas.tsx` files when creating or mentioning an IDE Canvas, e.g. using the format: `[Descriptive Label]` immediately followed by `(/absolute/path/to/canvas.canvas.tsx)`.
- Adjust column width via `--an-max-width` in `agent-ui.css` instead of Tailwind, and use `pnpm run check` in `src/frontend` to verify format, types, linter, and unit tests.

## Learned Workspace Facts

- The development workspace infrastructure runs on local ports: API on `:8000`, Vite dev server on `:5173`, and MLflow on `:5001` (Python 3.13+).
- The chat runtime runs in Daytona via `dspy.RLM` and supports major LLM providers as well as custom OpenAI-compatible endpoints with dropdown model selection.
- Database queries use Row-Level Security (RLS) policies with session tenant IDs to prevent leaks; Alembic schema drift checks require importing all active SQLModel models inside `migrations/env.py`.
- IDE Canvases are for standalone analytical outputs (not internal files) and support specific category colors: `gray`, `purple`, `green`, `yellow`, `pink`, `blue`, and `orange`.
- Streaming responses in the runtime are standardized on `RuntimeEvent` (`runtime/events.py`) and projected using `project_chat`, while legacy `StreamEvent` DTOs are cleaned up and unused.
- The linked Neon Postgres project is `fleet-rlm-postgres-cutover` (ID: `old-bird-44339002`) in region `aws-eu-central-1` (Frankfurt) under the `Qredence` organization.
- On FastAPI Cloud, delete locked environment variables first via `uv run fastapi cloud env delete <VAR> -y` to avoid `400 Bad Request`, and deploy in non-interactive terminals using `--no-wait` to bypass interactive spinners.
- Production and staging environments (`APP_ENV=production` or `staging`) enforce `AUTH_REQUIRED=true` at startup, raising a `ValueError` if set to `false`.
