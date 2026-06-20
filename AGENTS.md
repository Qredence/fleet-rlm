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

- Always use the `zsh` terminal profile for CLI commands. Adjust column width via `--an-max-width` in `agent-ui.css` instead of Tailwind, and use `pnpm run check` in `src/frontend` to verify format, types, linter, and unit tests.
- Always include direct absolute markdown links to `.canvas.tsx` files when creating or mentioning an IDE Canvas, e.g. using the format: `[Descriptive Label]` immediately followed by `(/absolute/path/to/canvas.canvas.tsx)`.
- For landing page suggestions in `workspace-message-list.tsx`, use the canonical `/agent-elements` `Suggestion` chip component with uncolored icon fills and a larger "Qredence Fleet" title, avoiding card-like empty states or colored icon accents.
- Prefer the built-in Cursor Browser (`cursor-ide-browser` MCP server) over Playwright tools when requested to perform browser automation.

## Learned Workspace Facts

- **Local Ports & Runtime Services**: Local development runs on ports `:8000` (FastAPI), `:5173` (Vite dev), and `:5001` (MLflow on Python 3.13+); chat runtime runs in Daytona via `dspy.RLM` with multi-provider dropdown selection. The offline dev workspace has no external internet access (blocked at the DNS level), though `user-Neon` MCP operates fine.
- **Database & Multi-Tenant Isolation**: Multi-tenant database isolation relies on Postgres Row-Level Security (RLS) policies (setting transaction local context variables `app.tenant_id`, `app.user_id`, `app.workspace_id` via `set_config` and `set_local`). Alembic schema drift checking requires importing all active SQLModel models inside `migrations/env.py`, while local fallback uses SQLModel/SQLite.
- **Database Security & Hardening**: Neon Postgres database hardening requires fixing Advisor-reported vulnerabilities: pg_catalog and system functions (such as `app.uuid_v7`, `app.set_updated_at`, and `public.show_db_tree`) should be secured by setting an explicit, safe `search_path` (e.g., `SET search_path = app, pg_temp` or similar) to prevent mutable search-path exploits, and relocating extensions (`pgcrypto`, `uuid-ossp`) to the secure `app` schema.
- **Neon Auth & Token Decoding**: Neon Auth is integrated under `auth_mode="neon"`, using `@neondatabase/auth-ui` on the frontend (catch-alls `/auth/$pathname` and `/account/$pathname`). Its EdDSA-signed tokens require explicitly passing `algorithms=["EdDSA"]` in Python's `joserfc` JWT decoder (RFC 9864) to prevent signature rejection; browser WebSockets must use `POST /api/v1/auth/ws-ticket` and never raw Neon JWT query parameters.
- **Session Lifetime & WebSocket Sync**: Chat sessions auto-create/save on first message, sorted descending (`createdAt`), guarded by `lastSavedStateRef` in `workspace-screen.tsx` to prevent redundant saves. WebSocket execution emits `"db_session_id"` inside the `"execution_started"` event `summary` payload, which `use-workspace-runtime.ts` captures dynamically to bind local and remote session state.
- **Background Syncing & Restore**: On user authentication (`isAuthenticated`), `app-sidebar.tsx` triggers a background synchronizer that fetches and merges remote Neon database sessions (`sessionsEndpoints.list`) into the TanStack store to seamlessly restore conversation history. Keep FastAPI and `FleetRepository` responsible for runtime authorization rather than bypassing via Neon Data API.
- **Frontend Custom Auth Styling**: Custom authentication layouts are best achieved by rendering granular `@neondatabase/auth-ui` forms (`SignInForm`, `SignUpForm`) directly rather than `AuthView`, stripping default borders/shadows using `classNames={{ base: "border-0 bg-transparent p-0 shadow-none w-full !max-w-none" }}` to integrate seamlessly.
- **UI styling, Canvas & Tool Components**: Global styling overrides in `globals.css` target sidebar subtrees to completely suppress focus outlines, rings, and borders, and implement hover-reveal transitions (`opacity 150ms`) for session list scrollbars using `.scroll-area-hover-reveal`. Tool names align horizontally via `ToolRowBase` (Daytona parses with `extractRawToolName`). Standalone analytical outputs use IDE Canvases with categories (`gray`, `purple`, `green`, `yellow`, `pink`, `blue`, and `orange`).
- **Build & Package Pipeline**: Frontend uses `pnpm` exclusively (see `CLAUDE.md` dev commands). Under `uv build`, packaging delegates to `src/fleet_rlm/ui/build.py` which runs `scripts/ensure_frontend_entrypoint.py` to reconstruct `index.html` and copy assets to `src/fleet_rlm/ui/dist` for serving on port `:8000`.
- **TanStack Start Hydration**: Under TanStack Start, client entry `client.tsx` falls back to `createRoot` for static non-SSR serving while using `hydrateRoot` in SSR mode to avoid blank screens, sets `(window as any).__hydrated = true` for E2E tests, renders `PostHogProvider` unconditionally, and guards SSR manifest mutations via `WeakSet`.
- **Streaming & Terminal TUI**: Streaming responses in the runtime are standardized on `RuntimeEvent` (`runtime/events.py`) and projected using `project_chat`, while legacy `StreamEvent` DTOs are cleaned up and unused. The interactive terminal TUI in `chat.py` implements a Claude Code-inspired layout using `prompt_toolkit` with a boxed input prompt sticking to the bottom, active thinking/connection states, and a scrollable transcript.
- **FastAPI Cloud Deployment**: On FastAPI Cloud, delete locked env vars first via `uv run fastapi cloud env delete <VAR> -y` and deploy with `--no-wait`; production and staging environments enforce `AUTH_REQUIRED=true` at startup.
