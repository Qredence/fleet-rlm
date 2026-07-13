# Backend HTTP API

The generated source of truth is [`openapi.yaml`](../../openapi.yaml).

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/chat` | Execute one Turn and stream Runtime Events over SSE |
| `POST` | `/api/sessions` | Create a Session |
| `GET` | `/api/sessions` | List owned Sessions |
| `GET/PATCH/DELETE` | `/api/sessions/{session_id}` | Read, rename/archive, or delete an owned Session |
| `GET` | `/api/sessions/{session_id}/turns` | Read committed Turn history |
| `POST` | `/api/files` | Upload one durable Attachment |
| `GET` | `/api/files/{file_id}` | Read owned Attachment metadata |
| `GET` | `/api/artifacts/{artifact_id}` | Read committed Artifact metadata |
| `GET` | `/api/skills` | List authorized Skill Cards |
| `GET` | `/api/skills/{skill_id}` | Read one authorized Skill Card |
| `POST` | `/api/runs/{run_id}/cancel` | Request cancellation of an owned Run |

Authentication is synthetic `X-Fleet-*` identity only in explicit dev mode.
Neon mode requires a Bearer JWT and derives Workspace identity server-side.
Attachment ownership is validated before SSE begins. Provider exceptions,
credentials, and storage paths never enter public responses.

`POST /api/artifacts` does not exist. Artifacts become public only through Turn
Commit after host-mediated `create_artifact` produces a private candidate.
