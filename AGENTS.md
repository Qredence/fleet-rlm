# Repository Agent Map

`fleet-rlm` is a backend-first adaptive recursive language model workspace built around a Daytona-backed DSPy RLM runtime. The prior frontend has been removed; a new client will be introduced separately.

## Operating Model

- Use closest applicable `AGENTS.md` before editing files; deeper guides override this map.
- Keep repo docs, generated contracts, and `.codex/` actions aligned with implementation changes.
- Prefer smallest validation lane that covers the change, then escalate when contracts move.
- Do not hand-edit generated/synced artifacts; use the commands listed below.
- Do not mutate user-level Codex config. Ask before deploy, push, migrations, or deletion.

## Reading Path

1. `docs/agent-harness/README.md` - harness model, reading order, and quality bar.
2. `docs/agent-harness/feedback-loop.md` - local Codex loop and report expectations.
3. `docs/agent-harness/architecture-invariants.md` - backend and generated-file rules.
4. `docs/reference/codebase-map.md` - source layout and ownership map.
5. `docs/how-to-guides/testing-strategy.md` - validation lanes by change type.

## Deeper Agent Guides

- `src/fleet_rlm/AGENTS.md` - backend, runtime, API, persistence, Daytona, and package rules.

## Durable Detail Locations

- Auth, DB, websocket, runtime, and deploy details live in `src/fleet_rlm/AGENTS.md` or matching docs.
- Local Codex actions, ports, browser smoke expectations, and tool preferences live in `.codex/` and loop docs.

## Agent skills

### Issue tracker

Planning issues use local Markdown under `.scratch/<feature>/`; `.scratch/` is local-only and ignored. See `docs/agents/issue-tracker.md`.

### Triage labels

Use the default `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, and `wontfix` roles. See `docs/agents/triage-labels.md`.

### Domain docs

Use the root and backend contexts listed in `CONTEXT-MAP.md`, plus relevant ADRs. See `docs/agents/domain.md`.

## Setup

```bash
uv sync --all-extras --dev
zsh .codex/workspace-bootstrap.zsh
```

## Run

```bash
uv run fleet web
uv run fleet-rlm serve-api --port 8000
```

## Validation

```bash
make format-check && make lint && make typecheck && make test
```

The parallel backend test lane defaults to at most two pytest-xdist workers.
Override it only on a runner with verified capacity using
`make test PYTEST_XDIST_MAX_WORKERS=<count>`.

## Generated Artifacts

Do not hand-edit: `openapi.yaml`, `src/fleet_rlm/ui/dist`.

Use: `make api-sync`, `make api-check`, `make build-ui`.

## Drift Checks

Run `make check-docs` when docs, commands, Codex config, generated contracts, or scripts change.

## Learned User Preferences

- Always use the `zsh` terminal profile for CLI commands; prefer running Python scripts/commands using `uv run` over raw `python3` or `python`.
- Secure production deployments strictly on Bring-Your-Own-Key (BYOK) model; never leak server-level secrets (like Gemini API keys or Daytona keys) to authenticated users.
- Do not edit `.plan.md` or any attached implementation plans while executing a task, prioritizing marked-in-progress to-dos sequentially.
- Prefer preserving Agent Elements design tokens (`--an-max-width`) rather than introducing arbitrary Tailwind classes for chat width adjustments.
- Run the full validation gate (`make format-check`, `make lint`, `make typecheck`, `make test`, `make api-check`, `make check-docs`, `uv run python scripts/sync_plans_canvas.py --check`, `git diff --check`) before commits when the user or phase completion requires it.
- Do not amend commits already pushed to remote; use narrow follow-up commits for fixes discovered after push.
- Never use `FLEET_DAYTONA_API_KEY` as the `Authorization: Bearer` token for Fleet-RLM API requests (authenticated clients must use Neon JWT); sanitize client-facing prepare/startup errors—never expose raw `str(exc)`, stack traces, credentials, or Daytona/provider internals to API clients.
- Do not commit `AGENTS.md` unless changes are intentional team workflow guidance; continual-learning workspace-fact deltas may stay uncommitted.
- Avoid introducing direct `litellm` usage in application code; reach LLM providers through `dspy.LM` instead.
- Prefer wire-protocol-named Literal unions (`openai_responses`, `openai_chat_completion`, `anthropic_messages`) over vendor-flavored or `_compatible`-suffixed provider-type enums, and keep LLM profiles flat (profile name, provider type, base endpoint, API key) rather than over-abstracting.
- Cite ONLY DSPy (installed 3.3.0b1 source + dspy.ai docs) as the reference contract for LLM/runtime design; do NOT cite the `/daytona` or `daytona-signature` skill as authority for DSPy/RLM decisions. For Daytona sandbox/interpreter and FastAPI API work, use the `/daytona` and `/fastapi` skills for provider/framework best practices.
- When asked for a plan, make it code-tree-explicit: exact file paths, line ranges, and ADD/REMOVE/EDIT tables describing what to clean, remove, edit, or add — not generic prose. When grilling or collecting decisions, prefer AskUser/AskQuestion over long inline multi-question dumps when that tool is available.

## Learned Workspace Facts

- Local backend development runs on `:8000` through `fleet web` or `fleet-rlm serve-api`. `POST /api/chat` uses SSE `RuntimeEvent` projection; the legacy `/api/v1` and WebSocket surfaces are removed.
- `src/fleet_rlm/` is the canonical RLM-native backend. The parallel foundation package was cut over after exit-bar evidence on `71e79271`; there is no compatibility runtime or dual-serve path.
- Daytona SDK imports are confined to `fleet_rlm.daytona`. Durable Attachments and Artifacts use Workspace Volume Scope; Artifact Candidates become public only through Turn Commit.
- Settings use only `FLEET_*`. Neon auth requires explicit `FLEET_NEON_AUTH_URL`, exposes coarse public auth errors, and permits synthetic `X-Fleet-*` identity only in `auth_mode=dev`.
- Alembic owns the live schema through one fresh canonical baseline. `create_tables` is restricted to explicit SQLite test/offline helpers; run `alembic check` against an upgraded empty database for drift.
- Backend validation is `make check`; live promotion uses the canonical tests under `tests/live/backend/` with explicit `FLEET_LIVE=1`. Frontend adaptation and generated frontend contracts are separate work.
- Under DSPy 3.3.Xb (normalized LM API), any `BaseLM` or bounded LM must have its provider explicitly resolved (via prefix or `provider` kwargs). Prefer stock `dspy.LM` with stateless config overrides passed directly to predictors/LM calls (using `dspy.settings.context` if needed) over stateful `copy()` or custom wrappers to ensure thread/session safety.
