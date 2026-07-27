# Daytona Snapshot

Fleet Daytona Sandboxes use an explicit immutable Snapshot rather than the
provider default. The current contract is `fleet-rlm-python313-v4`: Python
3.13.13 from a linux/amd64 digest-pinned `python:3.13.13-slim-bookworm` image,
a `daytona` non-root user, `/home/daytona` working directory, and 2 vCPU, 4 GiB
memory, and 8 GiB disk. Its repository-owned dependency manifest currently
bakes `mpmath==1.4.1` into system site-packages and records the manifest digest
in the image contract. It contains no Fleet source, provider credentials, DSPy,
or uv.

Snapshot provisioning is an operator action, never an application-startup side
effect. It does not alter the Daytona Workspace Volume: each Sandbox still
mounts only `workspaces/<workspace_id>` at `/home/daytona/fleet`.

## Provision and check

```bash
export FLEET_CONFIG_PROFILE=daytona
export FLEET_DAYTONA_API_KEY='...'
make daytona-snapshot-create
make daytona-snapshot-check
export FLEET_DAYTONA_SNAPSHOT=fleet-rlm-python313-v4
uv run fleet doctor daytona
```

`create` never deletes or overwrites a Snapshot. Re-running it checks a
pre-existing Snapshot's public name, state, and resource contract. The doctor
then proves the configured Snapshot identity, mount, non-root execution,
working directory, Python version, and every declared package import in a
disposable Sandbox without user-site repair.

## Upgrade and rollback

For a future upgrade, create a new immutable name such as
`fleet-rlm-python313-v5`, verify it, and
change only `FLEET_DAYTONA_SNAPSHOT`. Existing Session Sandboxes with the old
identity are replaced lazily while retaining their Workspace Volume ID and
`workspaces/<workspace_id>` scope. During the v4 rollout, roll back by restoring
`fleet-rlm-python313-v3`; for later upgrades, restore the preceding immutable
Snapshot. Do not mutate or overwrite an existing Snapshot.
