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

Run the harness lane when docs, commands, Codex config, generated contracts, or script inventory
change:

```bash
# from repo root
uv run python scripts/check_harness_engineering.py
uv run python scripts/check_agents_md_freshness.py
uv run python scripts/check_docs_quality.py
```

`make check-docs` runs the docs and harness checks together.

## Maintenance Checklist

When changing workflow, contracts, or architecture, update the durable docs before finishing:

- `AGENTS.md` and subsystem `AGENTS.md` files.
- `docs/agent-harness/*`.
- `docs/README.md`, `docs/index.md`, and `docs/SUMMARY.md`.
- `scripts/README.md`, `Makefile`, `pyproject.toml`, and `src/frontend/package.json` when commands move.
- `openapi.yaml` and frontend API artifacts when backend request or response shapes move.

## Cursor Cloud specific instructions

### Toolchain notes

- Cloud VMs may not have `zsh`; use `bash` for shell commands. Ensure `uv` is on `PATH` via
  `export PATH="$HOME/.local/bin:$PATH"` (install with `curl -LsSf https://astral.sh/uv/install.sh | sh`
  when missing).
- `uv sync` creates `.venv` with Python 3.13 (see `.python-version`). Node 22+ and pnpm 10 are
  sufficient for `src/frontend`.

### Secrets and local config

- Copy `.env.example` to `.env` or populate from injected secrets: `DSPY_LM_MODEL`, `DSPY_LLM_API_KEY`
  (or `DSPY_LM_API_KEY`), `DAYTONA_API_KEY`, and `DAYTONA_API_URL` are required for real chat /
  sandbox turns. Local dev defaults: `AUTH_MODE=dev`, `APP_ENV=local`, `AUTH_REQUIRED=false`.
- Set `MLFLOW_ENABLED=false` and `POSTHOG_ENABLED=false` in `.env` for faster API startup when
  tracing/analytics are not needed.

### Running services

| Service | Command | Port |
| --- | --- | --- |
| Web UI + API (bundled) | `uv run fleet web --host 127.0.0.1 --port 8000` | 8000 |
| Frontend dev (hot reload) | `cd src/frontend && pnpm run dev` | Vite (proxies API to 8000) |

Use tmux for long-running servers. Verify with `curl http://127.0.0.1:8000/health` and
`curl http://127.0.0.1:8000/ready`. The bundled UI is served from `src/fleet_rlm/ui/dist`; rebuild
with `make build-ui` after frontend contract changes.

### Validation caveats

- `make lint`, `make typecheck`, and `make format-check` are reliable smoke checks on Cloud VMs.
- `make test-fast` uses `pytest-xdist` (`-n auto`); on memory-constrained VMs some workers may crash.
  Re-run failing modules sequentially: `uv run pytest -q <path> -m "not live_llm and not live_daytona"`.
- Frontend `pnpm run type-check` may fail on the current branch independently of environment setup;
  use `pnpm run test:unit` and `pnpm run build` when validating UI changes.
