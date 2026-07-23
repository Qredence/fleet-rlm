---
name: report-builder
description: Create, save, read back, and verify reports from trusted source data.
compatibility: Durable Session Workspace writes and Artifact promotion require the Daytona run environment.
metadata:
  version: "1.0.0"
allowed-tools: list_workspace_files stat_workspace_file read_workspace_text write_workspace_text append_workspace_text publish_workspace_artifact create_artifact
---

# Report builder

Create the requested report from verified source data.

1. Build the complete report in memory from values already verified by the
   current Turn. Do not invent missing source data.
2. Check that every required section and requested value is present before
   writing anything.
3. Inspect `session_context["workspace"]["available"]`. If it is false, do
   not attempt Workspace tools, use a Python-local file as a substitute, or
   claim that a durable report was saved.
4. When Workspace is available, use only POSIX-relative paths rooted at `.`.
   List or stat the target when needed, then call
   `write_workspace_text(path=..., content=..., overwrite=False)`. Require
   `ok: true`; use `overwrite=True` only when replacing an intentionally chosen
   existing file. Use `append_workspace_text` when producing the report across
   incremental steps.
5. If the report is at most 10,000 characters, read the same path with
   `read_workspace_text(path=..., max_chars=10000)` and verify exact equality
   from the returned `content` before reporting success. For larger reports,
   continue page reads with each returned `next_cursor` until `eof`; never
   invent or edit a cursor. The read tool never returns a truncated page without
   continuation metadata.
6. Create an Artifact only when the user asks for a downloadable public output
   and a publication tool is available. For an existing Workspace report, use
   `publish_workspace_artifact` so the body is not resent; use `create_artifact`
   only for newly generated content. Require `ok: true`; the result is a
   private Artifact Candidate, not proof of public publication until Turn
   Commit succeeds.
7. For a report longer than 10,000 characters, write or append it once, then call
   `stat_workspace_file` on the same path. Treat this as metadata confirmation
   only when both the write receipt's `byte_size` and
   `stat_result["entry"]["byte_size"]` equal
   `len(content.encode("utf-8"))`; do not claim byte-for-byte read verification.
   Optionally call `create_artifact` when the user wants a downloadable output.
   Then `SUBMIT` only a short summary that references the relative workspace
   path. Never stuff an oversized report into `answer`.
8. Submit only after verification succeeds, using exactly the fields accepted
   by the current `SUBMIT` binding. Never report host paths, provider details,
   or an unpublished Artifact as public output.
