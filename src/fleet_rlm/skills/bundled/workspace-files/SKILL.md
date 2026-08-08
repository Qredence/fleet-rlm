---
name: workspace-files
description: Use when a Turn must inspect, create, or update durable Session files or Project deliverables, consume an authorized Attachment, or return a downloadable Artifact.
compatibility: Durable Project and Session Workspace writes and Artifact promotion require the Daytona run environment.
metadata:
  version: "1.1.0"
allowed-tools: list_project_files stat_project_file read_project_text write_project_text list_workspace_files stat_workspace_file read_workspace_text write_workspace_text append_workspace_text publish_workspace_artifact read_attachment create_artifact
---

# Workspace files

Use Fleet's bound tools instead of inventing host paths. The Turn context reports whether durable workspace tools are available.

## Projects (durable deliverables)

Finished deliverables the user should keep belong in a browsable Project on
the shared Volume at `projects/<slug>/`, not under
`sessions/<uuid>/workspace/`. You name the slug explicitly: pick a short,
repo/task-derived value (for example `fleet-rlm`) matching
`^[a-z0-9][a-z0-9._-]{0,63}$`; the backend sanitizes only and never invents a
slug for you. Reserved Volume roots (`sessions`, `files`, `artifacts`,
`attachments`, `memory`) cannot be slugs. Project paths are canonical
POSIX-relative paths whose first segment is the slug; `projects/` itself is
implicit and nested subdirectories are allowed. Project writes are immediate
private state; they survive a failed or cancelled Run and are not published as
Artifacts.

```python
listing = list_project_files(path="fleet-rlm", limit=100)
saved = write_project_text(path="fleet-rlm/reports/review.md", content=report, overwrite=False)
page = read_project_text(path="fleet-rlm/reports/review.md", max_chars=10000)
assert saved["ok"] is True
```

`write_project_text` requires `overwrite=True` to replace an existing file;
the read page contract (`max_chars` 1 through 10,000 characters, `next_cursor`
until `eof`), list cursors, and byte-size checks match the Session Workspace
tools below. There is no append or delete Project Tool; replace content with
`write_project_text(..., overwrite=True)`.

## Session Workspace (scratch)

Session Workspace paths are canonical POSIX-relative paths rooted at `.`. Session Workspace is tool-only: it is not visible to Python `open()`, `os`, or `pathlib`, and a sandbox-local file never satisfies a Session Workspace request. List or inspect before writing, use `overwrite=true` only when replacement is intended, and handle a tool error before trying a different operation. Session Workspace is append/update-only: there is no delete Tool; replace content with `write_workspace_text(..., overwrite=True)`.

```python
listing = list_workspace_files(path=".", limit=100)
page = read_workspace_text(path="notes/analysis.md", max_chars=10000)
saved = write_workspace_text(path="notes/analysis.md", content=updated_text, overwrite=True)
assert page["ok"] is True
```

`read_workspace_text` accepts `max_chars` from 1 through 10,000 characters and returns one
UTF-8 page with `content`, `byte_size`, `next_cursor`, and `eof`. Continue with
the opaque `next_cursor` until `eof` for large documents; never invent or edit a
cursor. `list_workspace_files` is immediate-child and can continue with its
`next_cursor`. Keep only the requested page in memory.

For exact write-size confirmation, compare metadata with
`len(content.encode("utf-8"))` rather than character count.

Use `append_workspace_text` for incremental generation. It writes only the new
content, enforces the Workspace size bound, and returns bounded metadata. Use
`write_workspace_text(..., overwrite=True)` when replacement is intended.

For an existing Workspace document that should be downloadable, call
`publish_workspace_artifact` with its relative path and kind. The host copies
the validated bytes into a private Artifact Candidate; do not resend the body
through `create_artifact`. Turn Commit remains the only publication boundary.

Workspace writes are immediate private Session state. They survive a failed or cancelled Run and are not published as Artifacts.

Never report a workspace or project operation as successful unless its tool call succeeds
and the applicable page iteration, append receipt, or metadata confirmation
completes. Do not retry a deterministic tool error unchanged.

## Attachments and Artifacts

Read only Attachment IDs supplied to the Turn:

```python
source = read_attachment(attachment_id=attachment_id)
```

The result contains UTF-8 text or base64-encoded binary content; check its `encoding` field before using the body.

To return a new downloadable result, call `create_artifact` with `text`,
`markdown`, or `json` content. This stages an Artifact Candidate; only a
successful Turn Commit publishes it. A successful tool result is not proof
that publication completed.

```python
candidate = create_artifact(kind="markdown", content=report, title="Analysis")
```

Writing a workspace file, project file, or sandbox file directly never creates a public Artifact.

Read `references/filesystem-contract.md` only when exact durability, layout, or path-boundary details are needed.
