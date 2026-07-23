---
name: workspace-files
description: Use when a Turn must inspect, create, or update durable Session files, consume an authorized Attachment, or return a downloadable Artifact.
compatibility: Durable Session Workspace writes and Artifact promotion require the Daytona run environment.
metadata:
  version: "1.0.0"
allowed-tools: list_workspace_files stat_workspace_file read_workspace_text write_workspace_text append_workspace_text publish_workspace_artifact read_attachment create_artifact
---

# Workspace files

Use Fleet's bound tools instead of inventing host paths. The Turn context reports whether durable workspace tools are available.

## Session Workspace

Workspace paths are canonical POSIX-relative paths rooted at `.`. Session Workspace is tool-only: it is not visible to Python `open()`, `os`, or `pathlib`, and a sandbox-local file never satisfies a Session Workspace request. List or inspect before writing, use `overwrite=true` only when replacement is intended, and handle a tool error before trying a different operation. Session Workspace is append/update-only: there is no delete Tool; replace content with `write_workspace_text(..., overwrite=True)`.

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

Never report a workspace operation as successful unless its tool call succeeds
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

Writing a workspace file or a sandbox file directly never creates a public Artifact.

Read `references/filesystem-contract.md` only when exact durability, layout, or path-boundary details are needed.
