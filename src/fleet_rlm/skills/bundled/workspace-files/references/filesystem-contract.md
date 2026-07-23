# Workspace filesystem contract

The Daytona sandbox sees its Workspace-scoped durable Volume at `/home/daytona/fleet`. The same backing Volume may serve multiple Workspaces, but Fleet mounts only the current Workspace's isolated subpath.

## Acquisition-created layout

Before a Run begins, Fleet creates the shared roots and the current Session and Run containers:

```text
/home/daytona/fleet/
├── artifacts/
├── attachments/
└── sessions/<session_uuid>/
    ├── workspace/
    └── runs/
        └── <run_uuid>/
            ├── artifacts/
            └── attachments/
```

Session and Run directory names are UUID-shaped. The containers exist at acquisition; workspace documents, `result.json`, and UUID-specific Attachment or Artifact files exist only after they are written.

## Boundaries

| Location | Meaning |
|---|---|
| `sessions/<session_uuid>/workspace/` | Immediate private Session working state; durable across Turns, failed Runs, and sandbox replacement. |
| `sessions/<session_uuid>/runs/<run_uuid>/attachments/` | Private Run staging for Attachments authorized to that Turn. Read them with `read_attachment`, not by guessing a path. |
| `sessions/<session_uuid>/runs/<run_uuid>/artifacts/` | Private Artifact Candidate bytes. Writing here directly does not publish or register an Artifact. |
| `artifacts/<artifact_uuid>/` | Durable promoted bytes. They represent a public Artifact only after successful Turn Commit; raw paths remain private. |

The host-owned bundled Skill catalog is not copied into the Volume. `memory/`,
Session `exports/` and `staging/`, and Run `staging/` are not provisioned
namespaces; Fleet has no production writer or reader for them.

`create_artifact` writes a private candidate under the current Run. On successful finalization, Fleet validates the candidate, promotes its bytes to the durable Artifact area, and commits its public identity with the Turn. Failure, cancellation, timeout, or commit failure does not publish that identity, even if private bytes were written before the metadata commit completed.

## Workspace path rules

Workspace, Session, Run, Attachment, and Artifact identities are UUID-shaped opaque values. The workspace tools accept relative paths such as `notes/analysis.md`. Do not pass absolute paths, backslashes, empty segments, `.` or `..` components, repeated slashes, trailing slashes, or the reserved `.fleet` component. Use `.` only as the root argument to `list_workspace_files`.

Session Workspace tools are append/update-only. Fleet exposes list, stat, paged
read, write (with `overwrite`), and append; there is no delete Tool. List pages
continue with the returned `next_cursor`, and text pages continue with their
opaque path-bound `next_cursor` until `eof`. To replace a file, call
`write_workspace_text(..., overwrite=True)`; to add incremental output, call
`append_workspace_text`.

The Session Workspace is a separate namespace from the Python sandbox filesystem. Use only the bound workspace tools for workspace files; Python file I/O cannot read, verify, or replace them.

REPL variables are per-Run and are not durable. Authorized clients can retrieve
committed Artifact bytes through the Artifact content API, but host storage
locations and raw sandbox paths must not appear in client-facing answers.
`publish_workspace_artifact` reads an existing Workspace document into a private
Run candidate without exposing its body or source path; only Turn Commit
promotes it.
