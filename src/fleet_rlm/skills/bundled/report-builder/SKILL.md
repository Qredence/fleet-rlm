---
name: report-builder
description: Create, save, read back, and verify reports from trusted source data.
compatibility: Durable Session Workspace writes and Artifact promotion require the Daytona run environment.
metadata:
  version: "1.0.0"
allowed-tools: list_workspace_files stat_workspace_file read_workspace_text write_workspace_text create_artifact
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
   existing file.
5. Read the same path with `read_workspace_text(path=...)`. Verify the returned
   string byte-for-byte where practical, including required headings and
   requested values, before reporting success.
6. Create an Artifact only when the user asks for a downloadable public output
   and `create_artifact` is available. Require `ok: true`; its result is a
   private Artifact Candidate, not proof of public publication until Turn
   Commit succeeds.
7. Submit only after verification succeeds, using exactly the fields accepted
   by the current `SUBMIT` binding. Never report host paths, provider details,
   or an unpublished Artifact as public output.
