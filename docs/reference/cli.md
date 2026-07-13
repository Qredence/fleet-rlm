# Backend CLI

The canonical command surface contains two thin server launchers:

```bash
uv run fleet web [--host 127.0.0.1] [--port 8000] [--reload]
uv run fleet-rlm serve-api [--host 127.0.0.1] [--port 8000] [--reload]
```

Both import `fleet_rlm.main:app`. Live composition is controlled only through
`FLEET_*` settings. Legacy terminal chat, optimization, evaluation, snapshot,
and Daytona smoke commands are removed.
