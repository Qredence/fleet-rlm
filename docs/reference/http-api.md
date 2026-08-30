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
| `PATCH` | `/api/files/content` | Replace one unique `old` fragment with `new` |
| `DELETE` | `/api/files/content` | Delete one file or one empty directory |
| `GET` | `/api/volume/tree` | List relative file paths from the mounted Workspace Volume |
| `GET` | `/api/skills` | List bounded system Skill Cards |
| `GET` | `/api/skills/{skill_id}` | Read one bounded system Skill Card |
| `PUT` | `/api/runs/{run_id}/cancellation` | Request cancellation of an owned Run |
| `GET/PATCH` | `/api/settings` | Read or revision-update non-secret `config/fleet.toml` policy from a loopback client |
| `GET` | `/health` | Liveness probe: process identity, no dependency checks |
| `GET` | `/health/ready` | Readiness probe: composition installed and the configured database answers |

The API uses one deterministic local User and Workspace scope. It accepts no
Authorization or caller-supplied identity headers. The settings endpoint is a
separate local administration surface: it rejects non-loopback clients,
including when the API has been explicitly bound to a network interface.
Turn creation requires an `Idempotency-Key` header. Its JSON body accepts
`text`, `attachment_ids`, and up to four unique `skill_selections` entries,
each containing an exact Skill `id` and `expected_version`. Explicit selections
are authoritative for that Turn and are included in its idempotency
fingerprint. Omitting `skill_selections` supplies the full bounded catalog
Cards and permits the RLM to load up to four advertised Skills progressively;
exact selections advertise only the authorized selected Cards and enforce the
same IDs/versions during progressive `load_skill` calls.
Providing selections preloads those exact versions and restricts loading to
that set. Structurally malformed selections still fail pre-stream with a 422
`invalid_skill_selection` JSON response. Catalog-rejected selections (missing,
unauthorized, or version-mismatched) resolve during in-stream Turn opening and
answer with the same generic `Invalid Skill selection` message as a stream
`error` chunk; both contracts avoid revealing hidden catalog entries.

## Turn streaming contract

`POST /api/sessions/{session_id}/turns` begins the AI SDK UI message stream
immediately instead of holding headers until preparation finishes. While the
Turn claim and preparation resolve, the server emits a transient
`data-status` chunk
(`{"type": "data-status", "data": {"phase": "preparation", "status": "running", "message": null}, "transient": true}`)
at once, then again every `runtime.heartbeat_seconds` until
`coordinator.open` completes. Prelude chunks are a client-facing keep-alive
only: they never enter the durable Turn history or the event log, they may
repeat, and consumers must not key state on their count.

After opening, one of three closings applies:

- Success streams Runtime Events and ends with `finish` then `[DONE]`.
- Claim or preparation failures end the stream with the closed `error` +
  `finish` chunks the previous prepare-before-headers boundary used to map to
  HTTP statuses (`Session not found`, `A Turn is already running`,
  `Idempotency key input mismatch`, `Invalid Skill selection`,
  `Turn preparation timed out`, `Turn is unavailable`, `Invalid request`),
  followed by `[DONE]`. Transport/status 200 therefore no longer implies a
  successful Turn; the Run id lives in the `start` chunk metadata, not in
  response headers.
- Run cancellation ends the live stream with one terminal `abort` chunk and
  nothing after it (no `finish`, `data-usage`, or checkpoint metadata). Once
  settlement completes, the cancelled attempt persists a bounded tombstone in
  committed history so `GET /api/sessions/{id}/turns` shows the attempt: the
  original user input plus one assistant message carrying only a
  `data-status` part
  (`{"type": "data-status", "data": {"phase": "cancelled", "status": "cancelled", "message": null}}`),
  observed usage, and the closed text "Turn cancelled" — never reasoning,
  code, output, or Tool evidence parts.

Malformed request bodies and explicit Skill-selection structure are validated
by FastAPI before SSE begins. Attachment and catalog ownership checks complete
through the Turn opening path; failures after headers are closed stream errors.
Provider exceptions, credentials, Skill instructions, resource bodies, and
storage paths never enter public responses or Skill lifecycle projections.

The files API always resolves the process-local Workspace. Callers cannot
select a Workspace or address Daytona Volume, mount, Sandbox, Attachment,
Artifact, Session, or Run identifiers. It exposes only the durable `files/`
root, has no rename operation, and accepts an optional current
SHA-256 on overwrite/append/delete/patch; stale preconditions return `409`.
`DELETE /api/files/content` removes one file or one empty directory
(non-empty directories return `409`), and `PATCH /api/files/content`
applies one unique find/replace whose old text must occur exactly once
(absent or ambiguous matches return `409`); PATCH returns the fresh
content checksum for precondition chaining.

`POST /api/artifacts` does not exist. Artifacts become public only through Turn
Commit after host-mediated `create_artifact` produces a private candidate.

## Health probes

`GET /health` answers liveness for any caller while the process serves HTTP,
even before lifespan composition completes: it returns the application name and
version and performs no dependency checks. `GET /health/ready` answers
readiness: before composition installs it returns the closed `service_not_ready`
JSON 503 on the shared error envelope (serving routes use `turn_unavailable`
there); once composed it probes the configured database with one bounded
`SELECT 1` round-trip and reports `database: "ok"`, or
`database: "not_configured"` when no database URL is set. An unreachable or
hung database degrades readiness to the same closed 503. Neither probe
requires an identity header, a loopback client, or an existing Session.
