---
name: workspace-files
description: Use when a Turn must inspect, create, or update durable Session files, consume an authorized Attachment, or return a downloadable Artifact.
compatibility: Durable Session Workspace writes and Artifact promotion require the Daytona run environment.
metadata:
  version: "1.0.0"
allowed-tools: list_workspace_files stat_workspace_file read_workspace_text write_workspace_text read_attachment create_artifact
---

# Workspace files

Use Fleet's bound tools instead of inventing host paths. The Turn context reports whether durable workspace tools are available.

## Session Workspace

Workspace paths are canonical POSIX-relative paths rooted at `.`. Session Workspace is tool-only: it is not visible to Python `open()`, `os`, or `pathlib`, and a sandbox-local file never satisfies a Session Workspace request. List or inspect before writing, use `overwrite=true` only when replacement is intended, and handle a tool error before trying a different operation. Session Workspace is append/update-only: there is no delete Tool; replace content with `write_workspace_text(..., overwrite=True)`.

```python
listing = list_workspace_files(path=".", limit=100)
current = read_workspace_text(path="notes/analysis.md", max_chars=10000)
saved = write_workspace_text(path="notes/analysis.md", content=updated_text, overwrite=True)
assert read_workspace_text(path="notes/analysis.md") == updated_text
```

`read_workspace_text` accepts `max_chars` from 1 through 10,000 and rejects a
file longer than the requested bound; it never truncates or returns a prefix.
For content of at most 10,000 characters, verify exact read-back equality. For
larger content, require a successful write receipt and call
`stat_workspace_file` on the same path. Treat the write as metadata confirmation
only when both `saved["byte_size"]` and `stated["entry"]["byte_size"]` equal
`len(content.encode("utf-8"))`; do not claim byte-for-byte read verification.

Workspace writes are immediate private Session state. They survive a failed or cancelled Run and are not published as Artifacts.

Never report a workspace operation as successful unless its tool call succeeds
and the applicable exact-read or large-file metadata confirmation completes. Do
not retry a deterministic tool error unchanged.

## Attachments and Artifacts

Read only Attachment IDs supplied to the Turn:

```python
source = read_attachment(attachment_id=attachment_id)
```

The result contains UTF-8 text or base64-encoded binary content; check its `encoding` field before using the body.

To return a downloadable result, call `create_artifact` with `text`, `markdown`, or `json` content. This stages an Artifact Candidate; only a successful Turn Commit publishes it. A successful tool result is not proof that publication completed.

```python
candidate = create_artifact(kind="markdown", content=report, title="Analysis")
```

Writing a workspace file or a sandbox file directly never creates a public Artifact.

Read `references/filesystem-contract.md` only when exact durability, layout, or path-boundary details are needed.
