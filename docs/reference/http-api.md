# HTTP API reference

Fleet exposes a FastAPI application with the following routes:

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Return status and application version. |
| `GET` | `/sessions` | List persisted sessions (up to 100). |
| `GET` | `/skills?session_id=...` | Return the selected session's Skill catalog. |
| `POST` | `/skills/pull?session_id=...` | Export sandbox Skills to the configured local user Skill directory. |
| `GET` | `/sessions/{session_id}/files` | List workspace files and generated artifacts. |
| `POST` | `/sessions/{session_id}/attach` | Attach a multipart file to `files/` in the session workspace. |
| `POST` | `/sessions/{session_id}/turn` | Run a Turn and stream progress as SSE. |

There is no caller authentication. The service binds to loopback by default;
binding elsewhere does not add authentication. Only use on a trusted host or
behind a separately secured boundary.

## Attach a file

Send a `multipart/form-data` body with a `file` field. The upload limit is
controlled by `FLEET_MAX_UPLOAD_BYTES` (default 5,000,000 bytes). The response
contains the mounted path and relative workspace path, for example
`/workspace/files/input.csv` and `files/input.csv`.

## Run a Turn

The JSON request has one required `task` string:

```json
{"task":"Summarize the attached CSV and verify the totals with Python."}
```

The response uses `text/event-stream`. Each data event is JSON prefixed by
`data: ` and separated by a blank line. The server initially emits a
`turn_status` preparation event, then DSPy callback events such as
`module_start`, `tool_start`, and `tool_end`. Completion is reported as
`turn_complete` with `answer`, `route`, optional `learned_skill_note`, and an
`artifacts` list. When MLflow tracing is configured, the completion event also
includes the recorded `trace_id`. Failures produce a sanitized `error` event
followed by stream completion. The connection may emit SSE comment keep-alives.

Sessions serialize their own Turns while a run is in progress. Session IDs are
validated by the runtime; use simple URL-safe identifiers.

## Skills and files

`GET /skills` and `POST /skills/pull` accept an optional `session_id` query
parameter, defaulting to `default`. Local user Skills are read from
`.fleet/skills/` by default. Developer skills in `.agents/skills/` are not
synced as user Skills.

`GET /sessions/{session_id}/files` returns `files` and an `artifacts` subset;
it does not download file contents. The workspace is session-scoped on Daytona
and persisted through the Postgres VFS.
