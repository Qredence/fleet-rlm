# Local Codex Feedback Loop

## Safe Loop

```bash
# from repo root
zsh .codex/workspace-bootstrap.zsh
uv run python scripts/codex_feedback_loop.py --profile safe
make check
```

The safe loop requires no live Daytona or LLM credentials.

## Codex Cloud Loop

Cloud tasks for this repository start from `dev-0.7`; never select `main` or
`master`. The environment setup script is
`.codex/workspace-bootstrap.zsh`, and cached-container resumes use
`.codex/maintenance.zsh`. Both scripts install the locked Python and TUI
dependencies and enforce the branch guard.

Run the repository preflight before dispatching or resuming work:

```bash
zsh .codex/cloud-preflight.zsh
```

Cloud internet is limited to the configured environment policy. Use an app or
connector only when it is authorized in the Cloud/workspace surface, and never
store its credentials, tokens, or secrets in the repository. Refresh the Cloud
environment cache after changing setup, maintenance, or environment settings.

## API Smoke

Configure the selected runtime first. Deno requires an LLM key and Deno on
`PATH`; Daytona requires LLM and Daytona keys plus a database at Alembic head.
See [configuration](../reference/configuration.md).

```bash
uv run fleet web
# or
uv run fleet-rlm serve-api --port 8000
```

Verify that the ASGI application imports from `fleet_rlm.main:app` and that
`make api-check` confirms both OpenAPI and generated TUI HTTP types. Supported
routes are documented in `docs/reference/http-api.md`.

## Live Runtime Evidence

Run live checks only when runtime behavior changes or the exit bar requires it:

```bash
FLEET_LIVE=1 uv run pytest tests/live/backend/test_b5_attachment_artifact_durability.py -q
```

For complete MVP evidence, prefer `scripts/live_daytona_verify.py`; use the
focused live test above for durability changes. Record the exact git tip, test
result, model ids, and provider cleanup without secrets.

## Final Report

Report changed behavior, validation commands and results, generated artifacts,
the exact reviewed tip, and any explicitly deferred work.
