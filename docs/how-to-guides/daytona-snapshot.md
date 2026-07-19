# Daytona Snapshot

Fleet Daytona Sandboxes use an explicit immutable Snapshot rather than the
provider default. The current contract is `fleet-rlm-python313-v2`: Python
3.13.13 from a linux/amd64 digest-pinned `python:3.13.13-slim-bookworm` image,
a `daytona` non-root user, `/home/daytona` working directory, and 1 vCPU, 1 GiB
memory, and 3 GiB disk. It contains no Fleet source, provider credentials,
DSPy, uv, or additional packages.

Snapshot provisioning is an operator action, never an application-startup side
effect. It does not alter the Daytona Workspace Volume: each Sandbox still
mounts only `workspaces/<workspace_id>` at `/home/daytona/fleet`.

## Provision and check

```bash
export FLEET_DAYTONA_API_KEY='...'
uv run python scripts/daytona_snapshot.py create --name fleet-rlm-python313-v2
uv run python scripts/daytona_snapshot.py check --name fleet-rlm-python313-v2
export FLEET_DAYTONA_SNAPSHOT=fleet-rlm-python313-v2
uv run fleet doctor daytona
```

`create` never deletes or overwrites a Snapshot. Re-running it checks a
pre-existing Snapshot's public name, state, and resource contract. The doctor
then proves the configured Snapshot identity, mount, non-root execution,
working directory, and Python version in a disposable Sandbox.

## Upgrade and rollback

Create a new immutable name such as `fleet-rlm-python313-v3`, verify it, and
change only `FLEET_DAYTONA_SNAPSHOT`. Existing Session Sandboxes with the old
identity are replaced lazily while retaining their Workspace Volume ID and
`workspaces/<workspace_id>` scope. Roll back by restoring the previous setting;
do not mutate or overwrite either Snapshot.
