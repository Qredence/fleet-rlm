# Local Codex Feedback Loop

## Safe Loop

```bash
# from repo root
zsh .codex/workspace-bootstrap.zsh
uv run python scripts/codex_feedback_loop.py --profile safe
make check
```

The safe loop requires no live Daytona or LLM credentials.

## API Smoke

```bash
uv run fleet web
# or
uv run fleet-rlm serve-api --port 8000
```

Verify that the ASGI application imports from `fleet_rlm.main:app` and that
`openapi.yaml` matches the running backend. The supported routes are documented
in `docs/reference/http-api.md`.

## Live Runtime Evidence

Run live checks only when runtime behavior changes or the exit bar requires it:

```bash
FLEET_LIVE=1 uv run pytest tests/live/backend/test_exit_bar_l1_promotion.py -q
FLEET_LIVE=1 uv run pytest tests/live/backend/test_exit_bar_l2_adversarial.py -q
```

Record the exact git tip, test result, and any provider-side cleanup needed.

## Final Report

Report changed behavior, validation commands and results, generated artifacts,
the exact reviewed tip, and any explicitly deferred work.
