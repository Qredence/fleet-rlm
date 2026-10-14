# Fleet CLI

The `fleet` command has two modes:

```bash
uv run fleet serve [--host 127.0.0.1] [--port 8000]
uv run fleet cli [--url http://127.0.0.1:8000] [--session SESSION_ID]
```

`serve` starts the FastAPI server. `cli` connects the interactive Rich client
to an already running server. If `--session` is omitted, the client generates
a short session ID. If `--url` is omitted, it uses the configured host and
port.

When MLflow tracing is configured, the Rich client prints the trace ID after
each completed Turn so it can be located in the configured MLflow experiment.

The CLI supports `/help`, `/attach <path>`, `/files`, `/artifacts`, `/skills`,
`/skills pull`, `/sessions`, `/optimize`, and `/exit`. `/optimize` invokes the
source-checkout GEPA/MLflow script and requires the corresponding model,
Daytona, database, and tracking services.

The server defaults to `127.0.0.1:8000`. A non-loopback bind is rejected unless
`FLEET_ALLOW_NON_LOOPBACK_BIND=true` is configured. The API has no caller
authentication, so non-loopback binding must only be used behind a separately
authenticated trusted boundary.

Configuration fields and defaults are listed in the
[configuration reference](configuration.md). Live Daytona execution requires
valid API key, org ID, and Volume ID. No Daytona connectivity is asserted by
this reference.
