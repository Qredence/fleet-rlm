# Workspace filesystem contract

Your Sandbox mounts exactly one durable location: this Session's Workspace, at
`/workspace`. Everything else durable lives on the Workspace's Daytona Volume,
outside your Sandbox, and is reached only through host tools.

## What the Sandbox sees

```text
/workspace/          durable Session Workspace (survives Turns, failed Runs,
                     and Sandbox replacement)
/tmp/fleet/<run>/    private Run scratch; removed when the Run ends
└── attachments/     Run copies of the Attachments authorized to this Turn
```

Any other path in the Sandbox, including your home directory, is ephemeral
local disk: it is lost when the Sandbox is replaced, and nothing there is a
Workspace operation.

## Durable Workspace stores (host tools only)

| Logical location | Meaning |
|---|---|
| `sessions/<session_uuid>/workspace/` | The same files you see at `/workspace`. Use the Session Workspace tools with relative paths. |
| `projects/<slug>/` | Browsable durable deliverables named by an explicit model-chosen slug (`^[a-z0-9][a-z0-9._-]{0,63}$`; reserved roots `sessions`, `files`, `artifacts`, `attachments`, `memory` are not valid slugs). Write with `write_project_text`; replacement requires `overwrite=True`. |
| `memory/MEMORIES.md` | Workspace Memory; use the dedicated bounded memory tools. |
| `attachments/<attachment_uuid>/` | Durable Attachment originals. Read the Run copy with `read_attachment`, not by guessing a path. |
| `artifacts/<artifact_uuid>/` | Durable promoted bytes. They represent a public Artifact only after successful Turn Commit; raw paths remain private. |
| `files/` | Public Workspace files managed by the user through the API. |

Session and Run directory names are UUID-shaped. A document exists only after
its owning operation creates or migrates it.

The catalog remains host-owned. Loading an authorized Skill may install its
manifested resources under `skills/<name>/` in the Session Workspace;
`read_skill_resource` remains the fallback when installation is unavailable.
Session `exports/` and `staging/`, and Run `staging/`, are not general-purpose
tool namespaces.

`create_artifact` writes a private candidate under the current Run. On successful finalization, Fleet validates the candidate, promotes its bytes to the durable Artifact area, and commits its public identity with the Turn. Failure, cancellation, timeout, or commit failure does not publish that identity, even if private bytes were written before the metadata commit completed.

## Workspace path rules

Workspace, Session, Run, Attachment, and Artifact identities are UUID-shaped opaque values. The workspace tools accept relative paths such as `notes/analysis.md`. Do not pass absolute paths, backslashes, empty segments, `.` or `..` components, repeated slashes, trailing slashes, or the reserved `.fleet` component. Use `.` only as the root argument to `list_workspace_files`.

Session Workspace tools cover the full file lifecycle (the former
append/update-only, no-delete invariant ended with the WS-7 tool-surface
deviation). Fleet exposes list, stat, paged read, write (with `overwrite`),
append, unique-fragment edit, and delete. List pages continue with the
returned `next_cursor`, and text pages continue with their opaque path-bound
`next_cursor` until `eof`. To replace a file, call
`write_workspace_text(..., overwrite=True)`; to add incremental output, call
`append_workspace_text`. `edit_workspace_text(path, old, new)` replaces
exactly one occurrence—zero or ambiguous matches fail with `conflict`—and
`delete_workspace_path(path)` removes one file or one empty directory
(non-empty directories fail with `conflict`; symlinks and non-regular files
fail closed). Both accept an optional `expected_sha256` checksum
precondition that fails with `conflict` when current bytes differ. Project
tools (`edit_project_text`, `delete_project_path`) mirror these semantics
under `projects/<slug>/`; `attachments/` and `artifacts/` remain closed to
workspace-path deletes and edits.

Session Workspace tools resolve authorized logical paths. Use those tools for
workspace reads, verification, and writes; do not infer host or mounted paths
from a tool-relative path. Sandbox-local file I/O alone does not prove that a
durable Workspace operation succeeded.

REPL variables are not durable. The retained broker runtime may retain them
across sequential clean Turns, but rotation or replacement can lose them.
Authorized clients can retrieve
committed Artifact bytes through the Artifact content API, but host storage
locations and raw sandbox paths must not appear in client-facing answers.
`publish_workspace_artifact` reads an existing Workspace document into a private
Run candidate without exposing its body or source path; only Turn Commit
promotes it.
