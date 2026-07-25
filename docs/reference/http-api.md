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
| `GET` | `/api/files` | List the independent Workspace `files/` namespace |
| `GET` | `/api/files/stat` | Read file metadata and SHA-256 |
| `GET` | `/api/files/content` | Read one bounded UTF-8 page |
| `PUT` | `/api/files/content` | Create or explicitly overwrite a UTF-8 file |
| `POST` | `/api/files/append` | Append UTF-8 text |
| `GET` | `/api/skills` | List bounded system Skill Cards |
| `GET` | `/api/skills/{skill_id}` | Read one bounded system Skill Card |
| `PUT` | `/api/runs/{run_id}/cancellation` | Request cancellation of an owned Run |
| `GET/PATCH` | `/api/settings` | Read or revision-update non-secret `config/fleet.toml` policy from a loopback client |

The API uses one deterministic local User and Workspace scope. It accepts no
Authorization or caller-supplied identity headers. The settings endpoint is a
separate local administration surface: it rejects non-loopback clients,
including when the API has been explicitly bound to a network interface.
Turn creation requires an `Idempotency-Key` header. Its JSON body accepts
`text`, `attachment_ids`, and up to four unique `skill_selections` entries,
each containing an exact Skill `id` and `expected_version`. Explicit selections
are authoritative for that Turn and are included in its idempotency
fingerprint. Missing, unauthorized, or version-mismatched selections fail
before SSE begins with the generic
`invalid_skill_selection` response; the response does not reveal hidden
catalog entries.

Attachment ownership and explicit Skill selections are validated before SSE
begins. Provider exceptions, credentials, Skill instructions, resource bodies,
and storage paths never enter public responses or Skill lifecycle projections.

The files API always resolves the process-local Workspace. Callers cannot
select a Workspace or address Daytona Volume, mount, Sandbox, Attachment,
Artifact, Session, or Run identifiers. It exposes only the durable `files/`
root, has no delete or rename operation, and accepts an optional current
SHA-256 on overwrite/append; stale preconditions return `409`.

`POST /api/artifacts` does not exist. Artifacts become public only through Turn
Commit after host-mediated `create_artifact` produces a private candidate.
