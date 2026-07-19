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

Workspace paths are canonical POSIX-relative paths rooted at `.`. List or inspect before writing, use `overwrite=true` only when replacement is intended, and check each tool's `ok` result. Session Workspace is append/update-only: there is no delete Tool; replace content with `write_workspace_text(..., overwrite=True)`.

```python
listing = list_workspace_files(path=".", limit=100)
current = read_workspace_text(path="notes/analysis.md", max_chars=10000)
saved = write_workspace_text(path="notes/analysis.md", content=updated_text, overwrite=True)
```

Workspace writes are immediate private Session state. They survive a failed or cancelled Run and are not published as Artifacts.

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
