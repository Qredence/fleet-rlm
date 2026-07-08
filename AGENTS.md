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
- Prefer preserving Agent Elements design tokens (`--an-max-width`) rather than introducing arbitrary Tailwind classes for chat width adjustments.
- Prefer running Python scripts/commands using `uv run` over raw `python3` or `python` (aligned with user CLI tooling preferences).
- Do not amend commits already pushed to remote; use narrow follow-up commits for fixes discovered after push.
- Never use `DAYTONA_API_KEY` as the `Authorization: Bearer` token for Fleet-RLM API requests; authenticated clients must use Neon JWT. Daytona API keys are server-side credentials for Daytona Cloud only.
- Sanitize client-facing prepare/startup errors; never expose raw `str(exc)`, stack traces, credentials, or Daytona/provider internals to API clients.
- Avoid introducing direct `litellm` usage in application code; reach LLM providers through `dspy.LM` instead.
- Prefer wire-protocol-named Literal unions (`openai_responses`, `openai_chat_completion`, `anthropic_messages`) over vendor-flavored or `_compatible`-suffixed provider-type enums, and keep LLM profiles flat (profile name, provider type, base endpoint, API key) rather than over-abstracting.
- Cite ONLY DSPy (installed 3.3.0b1 source + dspy.ai docs) as the reference contract for LLM/runtime design; do NOT cite the `/daytona` or `daytona-signature` skill as authority for DSPy/RLM decisions.
- When asked for a plan, make it code-tree-explicit: exact file paths, line ranges, and ADD/REMOVE/EDIT tables describing what to clean, remove, edit, or add — not generic prose.

## Learned Workspace Facts

- Local development runs API on `:8000`, Vite dev server on `:5173`, and MLflow telemetry on `:5001` (Python 3.13+), standardizing streaming responses on `RuntimeEvent` (`runtime/events.py`) projected using `project_chat` (WebSocket) or `project_sse` (`POST /api/chat`).
- Chat execution defaults to `legacy_agent_runtime` (`EXECUTION_BACKEND` env; `direct_rlm` is opt-in server-side only and not accepted on `ChatRequest`). Opt-in `direct_rlm` runs real `dspy.RLM` turns via the pooled Daytona interpreter (Phase 2C); Phase 2D (`59b76422`) emits `TURN_INPUTS` and enriches terminal `DONE` with `schema_version` and `history_turns`; live `TurnProgressRelay`/`MLFLOW_SPAN` parity remains deferred. Under `execution_mode=auto`, most turns route to direct/tools rather than `dspy.RLM`; use `rlm_only` to force RLM. `POST /api/chat` SSE and WebSocket execution share the same `InterpreterPoolDeps` interpreter-pool lifecycle.
- `PLANS.md` is the canonical backend roadmap; do not maintain `PLANS_REORGANIZED.md` as a parallel plan.
- Phase 3 Skills package lives at `src/fleet_rlm/skills/` (`ActiveSkills`, loader, selection, catalog, sync); read-only HTTP API at `/api/v1/skills/*` (Phase 3D/3D.1) for catalog, load, validate, and safe resource reads with visibility gating and typed domain errors; preserve `SandboxSerializable` contract and legacy flat `skills/system`/`skills/user` paths.
- Any `config.yaml` work requires `docs/config-audit.md` first (audit-first; `PLANS.md` Phase 7). Run MLflow/trace parity audit (`docs/audits/mlflow-trace-parity.md`) before observability implementation changes.
- Daytona sandboxes use snapshot `fleet-rlm-01` by default (`fleet-rlm-browser` when browser skills are selected), mount persistent storage at `/home/daytona/memory`, and resolve volume name from `VOLUME_NAME` (server default `rlm-volume-dspy`).
- Database schema drift checking (`alembic check`) requires importing all active SQLAlchemy models inside `migrations/env.py`.
- `make plans-canvas-sync` / `make plans-canvas-check` (`scripts/sync_plans_canvas.py`) regenerate the `plans-roadmap` canvas generated `PHASES` block from `PLANS.md`; Cursor canvases cannot fetch at runtime (re-sync after phase status changes).
- Authentication uses `@neondatabase/auth-ui` locked to Neon Project ID `old-bird-44339002` (`https://ep-broad-water-al4k5bh7.neonauth.c-3.eu-central-1.aws.neon.tech/neondb/auth`) with JWT EdDSA token verification, trusting origin `https://fleet-rlm.fastapicloud.dev`.
- Tenant BYOK data is secured via Postgres RLS on `llm_provider_profiles` and Fernet encrypted via `FLEET_SECRET_ENCRYPTION_KEY` in `neon` auth mode, skipping empty/masked values to prevent key wipes.
- Under DSPy 3.3.Xb (normalized LM API), any `BaseLM` or bounded LM must have its provider explicitly resolved (via prefix or `provider` kwargs). Prefer stock `dspy.LM` with stateless config overrides passed directly to predictors/LM calls (using `dspy.settings.context` if needed) over stateful `copy()` or custom wrappers to ensure thread/session safety.
