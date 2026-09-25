# Daytona snapshots

Fleet uses immutable Daytona Snapshots. The current operator policy resolves
the Daytona organization from `FLEET_DAYTONA_ORG_ID`, the Session image from the
`FLEET_DAYTONA_SNAPSHOT` environment reference, and
the lean, Volume-less SemanticChild image from
`FLEET_DAYTONA_CHILD_SNAPSHOT`. Snapshot values are not embedded in Python or
passed as credentials on a command line.

The settings loader accepts referenced values from the process or repository
`.env`. Snapshot and organization identities prefer `.env` and reject a
process value that disagrees with it. This guide does not assert which snapshot
an operator currently has selected.

The historical `.env`-resolved Session and SemanticChild identities are
`fleet-rlm-python313-v7` and `fleet-rlm-python313-child-v2`. They remain
immutable rollback references, but their provider image definitions currently
drift from this checkout. The previously verified immutable candidates
`fleet-rlm-python313-v9` and `fleet-rlm-python313-child-v4` carry Python
3.13.13, the pinned `python:3.13.13-slim-bookworm` base, the `daytona`
non-root user, `/home/daytona` as the working directory, the pinned DSPy
3.3.1 runtime plus the repository dependency manifest, and the 4 CPU / 8 GiB /
8 GiB Session or lean 2 CPU / 4 GiB / 4 GiB SemanticChild resource shape.
SemanticChild cannot mount a Workspace Volume. WorkspaceChild remains
Volume-scoped and uses the Session image contract.

The P2.7 source definition now omits remote DSPy: orchestration stays on the
host, Session/WorkspaceChild retain the four analysis packages, and
SemanticChild uses the standard library. Immutable Session
`fleet-rlm-python313-v10` and SemanticChild `fleet-rlm-python313-child-v5` were
created and certified on 2026-09-10 (receipt
`.fleet-evidence/receipts/adr006/p27-reduced-snapshots-20260910-r4.json`).
At the time of that receipt, the configured `.env` references and code
fallbacks remained `fleet-rlm-python313-v7` / `fleet-rlm-python313-child-v2`.
The receipt is historical; consult the active [Fleet configuration](../../config/fleet.toml)
and deployed environment for current snapshot selection.

Snapshot provisioning is an explicit operator action. Application startup does
not create, overwrite, or delete snapshots, and an existing immutable name is
never mutated.

The prior `fleet-rlm-python313-v6` and `fleet-rlm-python313-child-v1` snapshots
also remain immutable rollback targets. No image is a selected deployment
value until its provider checks, disposable probes, sealed receipt, and
deployment-reference change are complete.

## Plan, create, and check

The command loads `.env` through Fleet's settings loader; no shell `export` is
needed:

```bash
uv run python scripts/daytona_snapshot.py plan \
  --profile session --name fleet-rlm-python313-v10
uv run python scripts/daytona_snapshot.py plan \
  --profile semantic-child --name fleet-rlm-python313-child-v5

uv run python scripts/daytona_snapshot.py create \
  --profile session --name fleet-rlm-python313-v10
uv run python scripts/daytona_snapshot.py create \
  --profile semantic-child --name fleet-rlm-python313-child-v5

uv run python scripts/daytona_snapshot.py check \
  --profile session --name fleet-rlm-python313-v10
uv run python scripts/daytona_snapshot.py check \
  --profile semantic-child --name fleet-rlm-python313-child-v5
```

`plan` is credential-free and prints only the profile, resource contract, and
manifest/dependency digests. `create` is idempotent: an existing name is
checked against its public image and resource contract, never rebuilt in place.
`check` fails closed on a missing, inactive, or drifting snapshot.

## Disposable runtime verification

After creation, verify each image in a disposable no-Volume Sandbox:

```bash
uv run python scripts/daytona_snapshot.py verify-runtime \
  --profile session --name fleet-rlm-python313-v10
uv run python scripts/daytona_snapshot.py verify-runtime \
  --profile semantic-child --name fleet-rlm-python313-child-v5
```

The probe verifies the baked manifest, each declared package's import and exact
version, Python version, non-root user, working
directory, and `git` toolchain, then deletes the disposable Sandbox and checks
that the identity is gone. This is separate from the Fleet doctor: the doctor
also checks configured Volume/database/LLM readiness and can stop at an earlier
prerequisite.

## Historical provider evidence

The Phase 5 public-network policy waiver remains an accepted risk in the
[architecture guidance](../../ARCHITECTURE.md). It does not establish network
isolation. Historical P2.7 snapshot
certification evidence remains in its original receipt. The retired Phase 5
and P2.7 verifier commands are no longer operator entrypoints.

For current checks, `daytona_snapshot.py verify-runtime` verifies an image's
baked package/import contract in a disposable Sandbox. The
`live_daytona_verify.py` command covers native semantic FastAPI calls and
attachment/artifact durability. The separately gated recursive-batch canary is
documented in the [DSPy integration guide](dspy-integration.md); neither live
contract is a snapshot promotion, containment, release, or deployment proof.

## Runtime profiles and rollback

The profile contract is deliberately explicit:

| Profile | Volume | Intended use |
| --- | --- | --- |
| `session` | Workspace-scoped mount | Root Turn execution |
| `semantic-child` | None | Selected-input iterative child; generic warm capacity may be eligible |
| `workspace-child` | Scoped `workspaces/<workspace_id>` mount | Child work that truly needs durable Workspace files |

These are environment-manifest roles, not selectable `runtime.variant` values.
The recursive factory passes `semantic-child` through its selected-profile
seam for capsule children, so they are volume-less SemanticChild sandboxes;
it falls back to the scoped `workspace-child` only when that snapshot is not
configured. Snapshot creation alone does not certify containment, mounts, or
warm pools.

Create and verify a new immutable name before changing `.env`. A Session
replacement retains the authorized Workspace Volume identity and subpath; it
does not copy a Python namespace or a full Session into a child. Roll back by
restoring the preceding immutable value in `.env` and restarting the owning
composition after active Runs drain. Do not delete the prior snapshot while a
binding, warm pool, or rollback procedure can still reference it.
