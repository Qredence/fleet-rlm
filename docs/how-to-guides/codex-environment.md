# Codex Local Environment

This repo ships a Codex-native focus pack under `.codex/`. It is project-local
configuration for trusted Codex App/CLI work in `fleet-rlm`; it does not require
or expect edits to `~/.codex/config.toml`.

## Bootstrap

Codex uses `.codex/environments/environment.toml` for the workspace setup entry.
The setup delegates to:

```bash
# from repo root
zsh .codex/workspace-bootstrap.zsh
```

The bootstrap is intentionally limited to dependency preparation:

- `uv sync --all-extras --dev --frozen` when `uv.lock` is present
- `pnpm install --frozen-lockfile` inside `src/frontend`

It does not start the app, run tests, or rely on exported shell state persisting
after setup. Use the environment actions for those workflows.

## Actions

`.codex/environments/environment.toml` exposes the practical repo command
surface as Codex actions:

- App runtime: `uv run fleet web`, `uv run fleet-rlm serve-api --port 8000`,
  and terminal chat.
- Backend validation: format, format-check, lint, typecheck, unit,
  integration, and full quality gate.
- Frontend validation: install, dev server, typecheck, lint, unit tests, build,
  e2e, and full frontend check.
- Contract and release lanes: OpenAPI check/sync, release hygiene, build UI,
  build release artifacts, and full release check.
- Operational lanes: security, dependency checks, Daytona diagnostics/smoke,
  MLflow server, RLM capability benchmarks, FastAPI cloud preflight, and CLI
  help.

Prefer one responsibility per action. Keep long-running server actions separate
from one-shot validation actions.

## Hooks

`.codex/config.toml` enables Codex `hooks`, and `.codex/hooks.json` declares the
repo-owned lifecycle hooks:

- `PreToolUse` on `Edit|Write`: blocks direct edits to `.env`; edit
  `.env.example` or docs instead.
- `PostToolUse` on `Edit|Write`: formats edited Python files with
  `uv run ruff format` and applies quiet Ruff fixes for that file.
- `Stop`: reports dirty generated/synced artifacts such as `openapi.yaml`,
  frontend generated OpenAPI types, route tree output, and packaged UI assets.
  It does not regenerate or modify these files.

Hook scripts live in `.codex/hooks/` and should stay small, deterministic, and
safe to run repeatedly.

## Focused Agents

`.codex/config.toml` declares project-scoped multi-agent roles backed by
`.codex/agents/*.toml`:

- `backend-runtime` for FastAPI, websocket, DSPy, persistence, and runtime work.
- `frontend-ui` for React/TanStack/Vite+/Agent Elements work.
- `api-contract` for OpenAPI, generated clients, and websocket payload shape.
- `testing-strategist` for choosing the smallest meaningful validation lane.
- `security-reviewer` for auth, tokens, middleware, settings, and secret risks.
- `release-manager` for packaging, release hygiene, and bundled UI assets.
- `daytona-runtime` for sandbox lifecycle, volumes, child isolation, and
  diagnostics.
- `docs-maintainer` for AGENTS.md, PLANS.md, README, and docs consistency.

These roles are Codex-native replacements for only the useful parts of older
`.claude`, `.agents`, and `.factory` material. Do not copy legacy prompts
wholesale unless they match the current repo contract.

## Validation

After changing `.codex/`, run:

```bash
# from repo root
uv run python -c "import json, pathlib, tomllib; [tomllib.loads(p.read_text()) for p in pathlib.Path('.codex').rglob('*.toml')]; json.loads(pathlib.Path('.codex/hooks.json').read_text()); print('codex-config-ok')"
zsh -n .codex/workspace-bootstrap.zsh .codex/hooks/*.zsh
uv run python scripts/check_agents_md_freshness.py
uv run python scripts/check_docs_quality.py
make format-check
codex features list | grep -E '^(hooks|multi_agent)[[:space:]]'
```

Run `zsh .codex/workspace-bootstrap.zsh` when dependency refresh is acceptable.
