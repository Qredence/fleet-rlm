# Daytona snapshots

Fleet uses immutable Daytona Snapshots. The current operator policy resolves
the Session image from the `FLEET_DAYTONA_SNAPSHOT` key in the local `.env` and
the lean, Volume-less SemanticChild image from
`FLEET_DAYTONA_CHILD_SNAPSHOT`. Snapshot values are not embedded in Python or
passed as credentials on a command line.

The Session image is the `fleet-rlm-python313-v7` contract: Python 3.13.13,
the pinned `python:3.13.13-slim-bookworm` base, the `daytona` non-root user,
`/home/daytona` as the working directory, the repository dependency manifest,
and the 4 CPU / 8 GiB / 8 GiB resource shape. The SemanticChild image is
`fleet-rlm-python313-child-v2`, uses the lean 2 CPU / 4 GiB / 4 GiB shape, and
cannot mount a Workspace Volume. WorkspaceChild remains Volume-scoped and uses
the Session image contract.

Snapshot provisioning is an explicit operator action. Application startup does
not create, overwrite, or delete snapshots, and an existing immutable name is
never mutated.

The prior `fleet-rlm-python313-v6` and `fleet-rlm-python313-child-v1` snapshots
remain immutable rollback targets. The larger `v7`/`child-v2` contracts are the
active `.env` values only after their provider checks and disposable probes pass.

## Plan, create, and check

The command loads `.env` through Fleet's settings loader; no shell `export` is
needed:

```bash
uv run python scripts/daytona_snapshot.py plan \
  --profile session --name fleet-rlm-python313-v7
uv run python scripts/daytona_snapshot.py plan \
  --profile semantic-child --name fleet-rlm-python313-child-v2

uv run python scripts/daytona_snapshot.py create \
  --profile session --name fleet-rlm-python313-v7
uv run python scripts/daytona_snapshot.py create \
  --profile semantic-child --name fleet-rlm-python313-child-v2

uv run python scripts/daytona_snapshot.py check \
  --profile session --name fleet-rlm-python313-v7
uv run python scripts/daytona_snapshot.py check \
  --profile semantic-child --name fleet-rlm-python313-child-v2
```

`plan` is credential-free and prints only the profile, resource contract, and
manifest/dependency digests. `create` is idempotent: an existing name is
checked against its public image and resource contract, never rebuilt in place.
`check` fails closed on a missing, inactive, or drifting snapshot.

## Disposable runtime verification

After creation, verify each image in a disposable no-Volume Sandbox:

```bash
uv run python scripts/daytona_snapshot.py verify-runtime \
  --profile session --name fleet-rlm-python313-v7
uv run python scripts/daytona_snapshot.py verify-runtime \
  --profile semantic-child --name fleet-rlm-python313-child-v2
```

The probe verifies the baked manifest, Python version, non-root user, working
directory, and `git` toolchain, then deletes the disposable Sandbox and checks
that the identity is gone. This is separate from the Fleet doctor: the doctor
also checks configured Volume/database/LLM readiness and can stop at an earlier
prerequisite.

## Runtime profiles and rollback

The profile contract is deliberately explicit:

| Profile | Volume | Intended use |
| --- | --- | --- |
| `session` | Workspace-scoped mount | Root Turn execution |
| `semantic-child` | None | Selected-input iterative child; generic warm capacity may be eligible |
| `workspace-child` | Scoped `workspaces/<workspace_id>` mount | Child work that truly needs durable Workspace files |

Create and verify a new immutable name before changing `.env`. A Session
replacement retains the authorized Workspace Volume identity and subpath; it
does not copy a Python namespace or a full Session into a child. Roll back by
restoring the preceding immutable value in `.env` and restarting the owning
composition after active Runs drain. Do not delete the prior snapshot while a
binding, warm pool, or rollback procedure can still reference it.
