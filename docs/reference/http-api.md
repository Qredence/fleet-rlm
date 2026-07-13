# Backend HTTP API

The generated source of truth is [`openapi.yaml`](../../openapi.yaml).

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/sessions/{session_id}/turns` | Execute one idempotent Turn and stream Runtime Events over SSE |
| `POST` | `/api/sessions` | Create a Session |
| `GET` | `/api/sessions` | List owned Sessions |
| `GET/PATCH` | `/api/sessions/{session_id}` | Read, rename, or archive an owned Session |
| `GET` | `/api/sessions/{session_id}/turns` | Read committed Turn history |
| `POST` | `/api/attachments` | Upload one durable Attachment |
| `GET` | `/api/attachments/{attachment_id}` | Read owned Attachment metadata |
| `GET` | `/api/artifacts/{artifact_id}` | Read committed Artifact metadata |
| `GET` | `/api/artifacts/{artifact_id}/content` | Download verified committed Artifact bytes |
| `GET` | `/api/skills` | List authorized Skill Cards |
| `GET` | `/api/skills/{skill_id}` | Read one authorized Skill Card |
| `PUT` | `/api/runs/{run_id}/cancellation` | Request cancellation of an owned Run |

Authentication is synthetic `X-Fleet-*` identity only in explicit dev mode.
Neon mode requires a Bearer JWT and derives Workspace identity server-side.
Turn creation requires an `Idempotency-Key` header. Attachment ownership is validated before SSE begins. Provider exceptions,
credentials, and storage paths never enter public responses.

`POST /api/artifacts` does not exist. Artifacts become public only through Turn
Commit after host-mediated `create_artifact` produces a private candidate.
