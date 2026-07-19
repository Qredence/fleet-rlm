# Daytona Sandbox Snapshot Implementation Plan

## Objective

Replace Daytona's implicit provider-default Sandbox with an explicit, immutable Fleet snapshot that provides:

- Python `3.13.13`;
- a non-root `daytona` user;
- `/home/daytona` as home and working directory;
- predictable Sandbox resources;
- no unnecessary Python or system dependencies;
- consistent use across Session and Volume I/O Sandboxes;
- safe snapshot upgrades and rollback.

## Snapshot contract

```text
Snapshot: fleet-rlm-python313-v2
Base: python:3.13.13-slim-bookworm@sha256:<verified-linux-amd64-digest>
User: daytona
Home: /home/daytona
Working directory: /home/daytona
Resources: 1 vCPU, 1 GiB RAM, 3 GiB disk
Environment: PYTHONUNBUFFERED=1
Additional Python packages: none
```

Do not install:

- `dspy`;
- `uv`;
- Daytona SDK;
- Fleet source or backend dependencies;
- compilers or build tooling;
- provider credentials.

`dspy.RLM` remains in the backend process. The Sandbox executes generated Python, the standard-library broker, host-tool wrappers, and `SUBMIT`.

## Runtime invariants

1. Daytona composition requires `FLEET_DAYTONA_SNAPSHOT`.
2. Every Fleet-created Daytona Sandbox uses the configured snapshot.
3. Existing Session Sandboxes are reused only when `sandbox.snapshot` matches.
4. A missing or mismatched snapshot identity causes safe Sandbox replacement.
5. Replacement preserves the existing Workspace Volume and scoped subpath.
6. Snapshot creation never occurs during application startup.
7. Snapshot versions are immutable; upgrades use a new name.
8. Snapshot provisioning never deletes or overwrites an existing snapshot.
9. No provider credential enters the snapshot or Sandbox environment.
10. Deno behavior remains unchanged.

# Implementation

## ADD

| File | Purpose |
|---|---|
| `src/fleet_rlm/daytona/sandbox_spec.py` | Own snapshot naming, Python version, base image, resources, settings conversion, image construction, and Sandbox provenance verification. |
| `scripts/daytona_snapshot.py` | Help-safe operator command for creating and checking the canonical snapshot. |
| `tests/unit/backend/daytona/test_sandbox_spec.py` | Test specification validation, image contents, resources, and runtime provenance checks. |
| `tests/unit/scripts/test_daytona_snapshot.py` | Test command parsing, create/check behavior, idempotency, and sanitized failures. |
| `docs/how-to-guides/daytona-snapshot.md` | Document provisioning, verification, upgrades, rollback, and resource sizing. |

## EDIT

| File and range | Changes |
|---|---|
| `src/fleet_rlm/config.py:25-57` | Add `daytona_snapshot: str | None = None` and validate supplied snapshot names. |
| `src/fleet_rlm/composition/daytona.py:14-26` | Require `FLEET_DAYTONA_SNAPSHOT` for Daytona composition. |
| `src/fleet_rlm/composition/daytona.py:88-102` | Construct the canonical Sandbox specification and pass it to Daytona adapters. |
| `src/fleet_rlm/daytona/platform.py:49-98` | Accept the specification and always set `snapshot=spec.snapshot` in `CreateSandboxFromSnapshotParams`. |
| `src/fleet_rlm/daytona/run_environment.py`, `LiveKernelResources.__init__` | Resolve one specification and supply it to the platform and Session manager. |
| `src/fleet_rlm/daytona/workspace_volume.py:32-46` | Replace the optional snapshot string with the required specification. |
| `src/fleet_rlm/daytona/workspace_volume.py:119-147` | Use the configured snapshot for ephemeral Volume I/O Sandboxes. |
| `src/fleet_rlm/daytona/workspace_volume.py:210-223` | Require the specification in the gateway factory. |
| `src/fleet_rlm/daytona/session_manager.py:172-200` | Accept and retain the specification. |
| `src/fleet_rlm/daytona/session_manager.py:321-398` | Verify snapshot provenance before reuse and after start or restore. Route mismatch through the existing unrecoverable replacement path. |
| `src/fleet_rlm/daytona/session_manager.py:497-544` | Verify replacement provenance before persisting the new binding. |
| `src/fleet_rlm/daytona/diagnostics.py:86-154` | Create the doctor Sandbox from the configured snapshot and verify snapshot identity and Python `3.13.13`. |
| `tests/unit/backend/test_sandbox_lifecycle.py:159-210` | Assert exact snapshot propagation; update references to the pinned Daytona `0.197.0` contract. |
| `tests/unit/backend/test_workspace_volume_gateway.py:63-135` | Supply the specification and assert snapshot propagation. |
| `tests/unit/backend/test_session_manager.py` | Cover matching, mismatched, missing, replaced, and failed replacement provenance. |
| `tests/unit/backend/test_daytona_diagnostics.py` | Cover valid and invalid snapshot/Python outcomes. |
| `tests/unit/backend/test_live_composition.py:55-95` | Assert fail-closed behavior without `FLEET_DAYTONA_SNAPSHOT`. |
| `tests/live/backend/test_fleet_rlm_daytona_mvp.py` | Verify snapshot identity and Python version in bounded live evidence. |
| `tests/live/backend/test_b5_attachment_artifact_durability.py` | Configure the snapshot and verify durable data across replacement. |
| `tests/live/backend/test_phase7_workspace_durability.py` | Configure the snapshot and verify replacement uses it. |
| `.env.example:8-23` | Add `FLEET_DAYTONA_SNAPSHOT=fleet-rlm-python313-v2`. |
| `docs/reference/configuration.md:8-42` | Add the snapshot setting, prerequisite, and immutable naming policy. |
| `docs/how-to-guides/dspy-integration.md:26-55` | Link snapshot provisioning before live verification. |
| `docs/index.md` | Register the snapshot guide. |
| `docs/SUMMARY.md` | Register the snapshot guide in documentation navigation. |
| `README.md:45-66` | Add snapshot provisioning to Daytona setup. |
| `scripts/README.md:3-17` | Inventory `daytona_snapshot.py` and update the retired-script statement. |

## REMOVE / AVOID

| Item | Action |
|---|---|
| Implicit provider-default snapshot | Remove from every production creation path. |
| Optional snapshot behavior in `DaytonaWorkspaceVolumeGateway` | Make snapshot selection explicit and required. |
| Snapshot column in `fleet_sandbox_bindings` | Do not add; `sandbox.snapshot` is authoritative. |
| Alembic migration | Not required. |
| `Image.debian_slim(...)` | Do not use; it installs compilers and upgrades pip. |
| `pyproject.toml` or `uv.lock` changes | None required. |
| OpenAPI regeneration | Not required. |
| Automatic snapshot creation at startup | Do not introduce hidden provider mutation. |
| Host Python version alignment | Keep separate from the Sandbox runtime. |

# Sandbox specification module

`src/fleet_rlm/daytona/sandbox_spec.py` should expose a small interface:

```python
@dataclass(frozen=True, slots=True)
class DaytonaSandboxSpec:
    snapshot: str
    python_version: str
    base_image: str
    cpu: int
    memory_gib: int
    disk_gib: int
```

It should also provide:

```python
def sandbox_spec_from_settings(settings: Settings) -> DaytonaSandboxSpec: ...
def build_snapshot_image(spec: DaytonaSandboxSpec) -> Any: ...
def verify_sandbox_spec(sandbox: Any, spec: DaytonaSandboxSpec) -> None: ...
```

Implementation requirements:

- reject empty names;
- reject mutable names such as `latest`, `stable`, or `lts`;
- require a version suffix such as `-v1`;
- use a digest-pinned `linux/amd64` base;
- construct `/home/daytona`;
- create a non-root `daytona` user;
- set ownership before switching users;
- set `WORKDIR /home/daytona`;
- set `USER daytona`;
- avoid `apt`, `pip`, and copied build context;
- return sanitized errors through the Daytona adapter seam.

# Provisioning command

## Interface

```bash
uv run python scripts/daytona_snapshot.py create \
  --name fleet-rlm-python313-v2

uv run python scripts/daytona_snapshot.py check \
  --name fleet-rlm-python313-v2
```

## `create`

1. Resolve the canonical specification.
2. Check whether the name already exists.
3. If absent, create it with `CreateSnapshotParams`.
4. Stream build logs without credentials or environment values.
5. Wait for an active snapshot.
6. Verify image and resource metadata.
7. Exit nonzero on mismatch or build failure.
8. Never overwrite or delete an existing snapshot.

## `check`

1. Retrieve the snapshot by name.
2. Require an active state.
3. Verify expected image metadata.
4. Verify CPU, memory, and disk values.
5. Return a concise, secret-free result.
6. Perform no mutation.

The script must support `--help` without credentials.

# Runtime integration

## Session Sandboxes

`LiveDaytonaPlatform.create()` should always construct:

```python
CreateSandboxFromSnapshotParams(
    snapshot=spec.snapshot,
    language="python",
    labels=labels,
    volumes=volumes,
    ephemeral=ephemeral,
)
```

Snapshot provenance should be verified through `sandbox.snapshot`, not solely through mutable labels.

For an existing Sandbox:

1. Verify Workspace identity and scoped Volume mount.
2. Verify snapshot provenance.
3. Start or restore it if necessary.
4. Verify mount and snapshot again.
5. Replace it if runtime provenance is missing or mismatched.

For a newly created or replacement Sandbox:

1. Verify snapshot provenance.
2. Verify the scoped mount.
3. Create the canonical Volume layout.
4. Persist the existing binding fields.
5. Return the Interpreter Lease.

## Volume I/O Sandboxes

Short-lived I/O Sandboxes must:

- use the same snapshot;
- retain `ephemeral=True`;
- mount exactly `workspaces/<workspace_id>`;
- perform no package installation;
- be deleted after every operation;
- retain the existing provider cleanup backstop.

# Daytona doctor

`uv run fleet doctor daytona` should verify:

1. Daytona settings, including snapshot name.
2. Database compatibility.
3. Volume visibility.
4. Disposable Sandbox creation from the configured snapshot.
5. `sandbox.snapshot` identity.
6. Scoped Volume mount.
7. Python version:

   ```python
   sys.version_info[:3] == (3, 13, 13)
   ```

8. Stateful code-interpreter execution.
9. Sandbox deletion.

Failures remain categorized and sanitized. Raw provider errors, image metadata, credentials, and internal paths must not reach users.

# Upgrade and rollback

## Upgrade

1. Define a new immutable specification, for example:

   ```text
   fleet-rlm-python313-v2
   ```

2. Create it:

   ```bash
   uv run python scripts/daytona_snapshot.py create \
     --name fleet-rlm-python313-v2
   ```

3. Check it.
4. Configure `FLEET_DAYTONA_SNAPSHOT=fleet-rlm-python313-v2`.
5. Run `fleet doctor daytona`.
6. Run live promotion tests.
7. Existing Session Sandboxes are replaced lazily on their next lease.
8. Workspace Volume data remains intact.

## Rollback

1. Restore the previous snapshot configuration.
2. Run `fleet doctor daytona`.
3. Existing Sandboxes from the newer snapshot are replaced lazily.
4. No database or Volume rollback is required.

# Validation

## Focused non-live lane

```bash
uv run pytest \
  tests/unit/backend/daytona/test_sandbox_spec.py \
  tests/unit/backend/test_sandbox_lifecycle.py \
  tests/unit/backend/test_workspace_volume_gateway.py \
  tests/unit/backend/test_session_manager.py \
  tests/unit/backend/test_daytona_diagnostics.py \
  tests/unit/backend/test_live_composition.py \
  tests/unit/scripts/test_daytona_snapshot.py \
  -q -n 0
```

## Repository gates

```bash
make check
make check-docs
git diff --check
```

## Credentialed Daytona gates

```bash
FLEET_LIVE=1 uv run pytest \
  tests/live/backend/test_fleet_rlm_daytona_mvp.py \
  -q -n 0 --timeout=900

FLEET_LIVE=1 uv run pytest \
  tests/live/backend/test_b5_attachment_artifact_durability.py \
  tests/live/backend/test_phase7_workspace_durability.py \
  -q -n 0
```

## Acceptance criteria

- No Daytona Sandbox is created without an explicit snapshot.
- The snapshot runs Python `3.13.13`.
- The broker and stateful interpreter work without extra packages.
- Session and I/O Sandboxes use the same runtime specification.
- Snapshot mismatch triggers controlled replacement.
- Workspace data survives replacement and rollback.
- Snapshot provisioning is explicit and non-destructive.
- No credentials enter snapshot contents, Sandbox environment, logs, or evidence.
- No database, OpenAPI, DSPy dependency, or lockfile changes are introduced.
