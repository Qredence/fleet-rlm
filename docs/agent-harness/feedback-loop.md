# Local Codex Feedback Loop

This loop gives Codex a local, repeatable path from bootstrap to evidence. The default lane avoids
live Daytona and live LLM requirements; use explicit live commands when testing the runtime
substrate.

## Safe Loop

```bash
# from repo root
zsh .codex/workspace-bootstrap.zsh
uv run python scripts/codex_feedback_loop.py --profile safe
```

The safe profile runs configuration and docs checks, confirms `.codex` syntax, verifies harness
drift controls, and records a concise JSON report under `artifacts/codex-feedback-loop/`.

Equivalent manual lane:

```bash
# from repo root
uv run python scripts/check_harness_engineering.py
uv run python scripts/check_agents_md_freshness.py
uv run python scripts/check_docs_quality.py
make format-check
```

## App Boot

Start the local app in a separate terminal:

```bash
# from repo root
uv run fleet web
```

Or run the API directly:

```bash
# from repo root
uv run fleet-rlm serve-api --port 8000
```

For frontend-only iteration:

```bash
# from src/frontend
pnpm run dev
```

## Browser Smoke

With the app running, smoke-test:

1. Workbench loads and the composer is usable.
2. One secondary surface loads: `Settings` or full-page `Volumes`.
3. The Workbench sidepanel can show its `Trajectories`, `Graph`, and `Volume`
   tabs without replacing chat as the primary surface.
4. `GET /health` returns a healthy response.
5. `GET /api/v1/runtime/status` returns structured runtime status, even when live Daytona or LLM
   credentials are not configured.

Use the Codex browser, Playwright, or the local browser tool already available in the session. Do
not invent a second UI test framework for this smoke path.

## Runtime And MLflow Evidence

Capture these values when available:

- server URL,
- `/health` status,
- `/api/v1/runtime/status` summary,
- MLflow tracking URI,
- trace IDs created by the run,
- Daytona diagnostic result when a live lane was explicitly requested.

MLflow is optional in the safe loop. Start it only when the task needs trace evidence:

```bash
# from repo root
make mlflow
```

## Live Lane

Only run live Daytona or LLM checks when the user requested them or the task changes runtime
execution behavior:

```bash
# from repo root
uv run python scripts/validate_env.py daytona --skip-smoke
uv run fleet-rlm daytona-smoke
uv run python scripts/validate_rlm_e2e_trace.py --server-url http://127.0.0.1:8000
```

## Final Report

Every Codex completion should name:

- changed files,
- validation commands that ran,
- important command failures or skips,
- generated artifacts touched by sync commands,
- live surfaces that were not verified.
