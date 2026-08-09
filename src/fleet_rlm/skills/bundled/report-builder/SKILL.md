---
name: report-builder
description: Create, save, read back, and verify reports from trusted source data.
compatibility: Durable Project and Session Workspace writes and Artifact promotion require the Daytona run environment.
metadata:
  version: "1.1.0"
allowed-tools: list_project_files stat_project_file read_project_text write_project_text list_workspace_files stat_workspace_file read_workspace_text write_workspace_text append_workspace_text publish_workspace_artifact create_artifact
---

# Report builder

Create the requested report from verified source data.

1. Build the complete report in memory from values already verified by the
   current Turn. Do not invent missing source data.
2. Check that every required section and requested value is present before
   writing anything.
3. Inspect `session_context["workspace"]["available"]`. If it is false, do
   not attempt Project or Workspace tools, use a Python-local file as a
   substitute, or claim that a durable report was saved.
4. Pick the durability target before writing. Scratch and intermediate drafts
   belong in the Session Workspace
   (`write_workspace_text`/`append_workspace_text`); they are private to this
   Session. A finished deliverable the user should keep belongs in a browsable
   Project: choose a short, repo/task-derived slug (for example `fleet-rlm`)
   and call
   `write_project_text(path="<slug>/<report-path>", content=..., overwrite=False)`.
   The slug is the first path segment and nested subdirectories are allowed;
   `projects/` is implicit. List or stat the target when needed. Require
   `ok: true`; use `overwrite=True` only when replacing an intentionally
   chosen existing file.
5. If the report is at most 10,000 characters, read the same path back with
   `read_project_text(path="<slug>/<report-path>", max_chars=10000)` (or
   `read_workspace_text` for Session-scoped scratch) and verify exact equality
   from the returned `content` before reporting success. For larger reports,
   continue page reads with each returned `next_cursor` until `eof`; never
   invent or edit a cursor. The read tool never returns a truncated page without
   continuation metadata.
6. Create an Artifact only when the user asks for a downloadable public output
   and a publication tool is available. For an existing Session Workspace
   report, use `publish_workspace_artifact` so the body is not resent; use
   `create_artifact` only for newly generated content. Require `ok: true`; the
   result is a private Artifact Candidate, not proof of public publication
   until Turn Commit succeeds.
7. For a report longer than 10,000 characters, write it once, then call
   `stat_project_file` (or `stat_workspace_file` for Session scratch) on the
   same path. Treat this as metadata confirmation only when both the write
   receipt's `byte_size` and `stat_result["entry"]["byte_size"]` equal
   `len(content.encode("utf-8"))`; do not claim byte-for-byte read verification.
   Optionally call `create_artifact` when the user wants a downloadable output.
   Then `SUBMIT` only a short summary that references the Project-relative
   path. Never stuff an oversized report into `answer`.
8. Submit only after verification succeeds, using exactly the fields accepted
   by the current `SUBMIT` binding. Never report host paths, provider details,
   or an unpublished Artifact as public output.
