# Tools, artifacts, and attachments dossier

## Phase 5 — Tools, artifacts, and attachments

- **Order:** `5`
- **Status:** `complete`
- **Track:** `Daytona`
- **Summary:** Add policy-filtered tools, durable artifacts, and safe attachment staging.
- **Commit:** `ce1ef6b8..b8f15287`

### Goal and stable interfaces

The canonical `tools/` module owns descriptors, registry, exposure policy,
binding, and tool categories. `discover_tools()` is the single exposure-policy
filter and sandbox tools require explicit sandbox availability.

`POST /api/v1/files/upload` stages one attachment at a time under the approved
session upload root. It returns safe `AttachmentRef` metadata with a checksum and
no host/volume path. Chat accepts attachment IDs only; resolution completes before
SSE starts, and `TurnControls.attached_files` carries metadata rather than file
content or prompt injection.

Artifacts live under approved per-session roots and use safe references plus a
session artifact index. Large tool outputs spill into artifacts rather than
overrunning the event stream.

### Package ownership

- `tools/`: registry, descriptors, policy, binding, and implementations.
- `files/`: upload staging, attachment resolution, schemas, and path safety.
- `artifacts/`: roots, storage I/O, references, and indexes.
- `runtime/tools/`: compatibility facades only.

### Non-goals

- Execute code or scripts on the FastAPI host.
- Expose raw filesystem or durable-volume paths to clients.
- Read attachment content implicitly into prompts.
- Remove compatibility facades before callers migrate.

### Acceptance criteria

- [x] Tools are filtered by `ToolExposurePolicy` through `discover_tools()`.
- [x] Code and scripts execute inside Daytona, not the FastAPI host.
- [x] Large outputs spill into durable artifacts.
- [x] Generated files stay under approved volume roots.
- [x] Attachments stage through `POST /api/v1/files/upload` with safe metadata.
- [x] RLM receives `AttachedFiles` as `SandboxSerializable` context.

### Evidence

- [Backend structure after Phase 5](evidence-backend-structure.md)

### Validation

```bash
uv run pytest tests/unit/tools/ tests/unit/files/ tests/unit/artifacts/
```
