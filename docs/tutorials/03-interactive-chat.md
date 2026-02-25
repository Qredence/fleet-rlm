# Tutorial 03: Interactive Chat

This tutorial covers terminal and WebSocket chat flows.

## Terminal Chat (`fleet-rlm chat`)

```bash
uv run fleet-rlm chat --trace-mode compact
```

Useful slash commands include:

- `/help`
- `/status`
- `/settings`
- `/docs <path> [alias]`
- `/analyze <query>`
- `/summarize <focus>`
- `/exit`

## Launcher Chat (`fleet`)

```bash
fleet --trace-mode verbose
```

## WebSocket Chat

With server running (`uv run fleet web` or `uv run fleet-rlm serve-api`), connect to:

- `ws://127.0.0.1:8000/api/v1/ws/chat`

Send:

```json
{
  "type": "message",
  "content": "Analyze README architecture",
  "trace": true,
  "trace_mode": "compact",
  "session_id": "tutorial-session"
}
```

Expect streamed `event` envelopes and a final payload containing the assistant response.

## Execution Stream (Optional)

Subscribe to:

- `ws://127.0.0.1:8000/api/v1/ws/execution?workspace_id=default&user_id=anonymous&session_id=tutorial-session`

Use this for live execution graph/step telemetry alongside chat.
