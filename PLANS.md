# Fleet RLM Role-First Daytona Volume Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the deeply Session/Run-shaped Daytona Volume tree with a small role-first layout that is easy to browse while preserving durable Attachments, Artifacts, Session Workspace files, reserved memory, and recovery-safe staging.

**Architecture:** One Daytona Volume subpath remains mounted per Fleet workspace. The database owns relationships such as which Session or Run created an object; the Volume organizes bytes by their semantic role. Only unavoidable temporary durable state uses a hidden `.internal/staging/<run-id>/` path. No new memory subsystem, filesystem browser, migration framework, or shared cross-Session workspace is introduced.

**Tech Stack:** Python 3.11-3.13, DSPy 3.3.0b1, Daytona 0.199.0, FastAPI 0.139.0, SQLAlchemy async, pytest.

## Global Constraints

- Focus only on the Daytona Volume layout and its direct path consumers.
- Keep `dspy==3.3.0b1`, `daytona==0.199.0`, and `fastapi[standard]==0.139.0` pinned.
- Keep one mounted Daytona Volume subpath per Fleet workspace.
- Keep Attachments immutable and Artifacts commit-gated.
- Keep Session Workspace files isolated by Session.
- Keep the database authoritative for ownership, Session/Run relationships, status, metadata, and committed Turn results.
- Do not write automatic per-Run `result.json` snapshots.
- Do not copy the Python Skills package into the mounted Volume.
- Do not add a memory engine; preserve only an empty reserved `memory/` root.
- Do not add dual-write or permanent legacy-path compatibility.
- For `dev-0.7`, use a new or disposable Volume for validation. Existing legacy Volume migration is a separate operational task.
- Do not introduce a public Volume browser or expose absolute provider paths through the API.
- Keep Run-specific paths only under hidden implementation storage.
- Every phase must leave the backend runnable and independently testable.

---

## Why Change the Current Layout

The current tree encodes the same Session and Run relationships in both the database and filesystem:

```text
/home/daytona/fleet/
├── attachments/
├── artifacts/
├── memory/
└── sessions/
    └── <session-id>/
        ├── workspace/
        ├── exports/
        ├── staging/
        └── runs/
            └── <run-id>/
                ├── attachments/
                ├── artifacts/
                └── staging/
```

This causes four problems:

1. A human must traverse opaque UUIDs before learning what kind of data a file represents.
2. Attachment and Artifact roles appear both globally and inside Run directories.
3. Durable user data and temporary implementation state look equally important.
4. The filesystem duplicates relationships already stored in `fleet_runs`, `fleet_turns`, `fleet_attachments`, and `fleet_artifacts`.

The target layout answers “what kind of data is this?” at the first path segment.

---

## Target Durable Volume Layout

```text
/home/daytona/fleet/
├── attachments/
│   └── <attachment-id>/
│       └── blob
│
├── artifacts/
│   └── <artifact-id>/
│       └── blob
│
├── workspace/
│   └── <session-id>/
│       └── <user-managed files and directories>
│
├── memory/
│   └── .keep
│
└── .internal/
    └── staging/
        └── <run-id>/
            ├── attachments/
            └── artifacts/
```

### Explicitly removed from the durable layout

```text
skills/
sessions/
sessions/<session-id>/exports/
sessions/<session-id>/staging/
sessions/<session-id>/runs/
result.json
attachment meta.json
artifact meta.json
```

Attachment and Artifact metadata stay in the database. Skill instructions remain host-owned and load through the existing Skill tools. JSON exports are created only as explicit Artifacts or Workspace files.

---

## Storage Ownership Contract

| Data | Canonical durable location | Relationship owner | Lifetime |
|---|---|---|---|
| Attachment bytes | `attachments/<attachment-id>/blob` | Database Attachment row | Until Attachment deletion |
| Artifact bytes | `artifacts/<artifact-id>/blob` | Database Artifact row | Until Artifact deletion |
| User-managed Session files | `workspace/<session-id>/...` | Session + Workspace database ownership | Across Runs and Sandbox replacement |
| Future curated memory | `memory/...` | Future memory contract | Not implemented in this plan |
| Run Attachment copies | `.internal/staging/<run-id>/attachments/...` | Active Run | Removed after Run cleanup |
| Artifact candidates | `.internal/staging/<run-id>/artifacts/...` | Active Run | Promoted or removed at finalization |
| Session/Run result and trajectory | Database committed Turn | Database | Across deployments |

The database explains what an object belongs to. The Volume stores the object bytes according to their role.

---

## Target Repository Changes

```text
src/fleet_rlm/
├── daytona/
│   ├── paths.py                 # role-first path facade
│   ├── volume_layout.py         # create only required role roots
│   ├── orphan_cleanup.py        # scan artifacts + hidden staging
│   └── run_environment.py       # use new staging and workspace paths
├── files/
│   ├── paths.py                 # Attachment canonical + staging paths
│   ├── lifecycle.py             # unchanged semantics, new path policy
│   ├── local_catalog.py         # no Daytona layout logic
│   └── workspace_tools.py       # unchanged public tool contract
├── artifacts/
│   ├── promotion.py             # use hidden staging and role-first durable path
│   └── daytona_catalog.py       # read database storage_ref
├── composition/
│   └── daytona.py               # new cleanup roots and no Skill tree materialization
└── persistence/
    └── repositories/
        ├── attachments.py       # storage_ref remains opaque
        └── artifacts.py         # storage_ref remains opaque

tests/
├── unit/backend/
│   ├── test_daytona_paths.py
│   ├── test_daytona_volume_layout.py
│   ├── test_attachment_paths.py
│   ├── test_artifact_promotion.py
│   └── test_orphan_cleanup.py
├── contracts/backend/
│   └── test_daytona_storage_contract.py
└── live/backend/
    └── test_b5_attachment_artifact_durability.py
```

---

## Task 1: Define the Role-First Path Contract

**Files:**
- Modify: `src/fleet_rlm/daytona/paths.py`
- Modify: `src/fleet_rlm/files/paths.py`
- Test: `tests/unit/backend/test_daytona_paths.py`
- Test: `tests/unit/backend/test_attachment_paths.py`

**Interfaces:**
- Consumes: validated workspace-scoped mount path and UUID identifiers.
- Produces: one immutable `VolumePaths` facade with role-first paths.

### Target `VolumePaths` API

```python
@dataclass(frozen=True, slots=True)
class VolumePaths:
    mount_path: PurePosixPath

    def attachments_root(self) -> PurePosixPath: ...
    def attachment_dir(self, attachment_id: str | UUID) -> PurePosixPath: ...
    def attachment_blob_path(self, attachment_id: str | UUID) -> PurePosixPath: ...

    def artifacts_root(self) -> PurePosixPath: ...
    def artifact_dir(self, artifact_id: str | UUID) -> PurePosixPath: ...
    def artifact_blob_path(self, artifact_id: str | UUID) -> PurePosixPath: ...

    def workspace_root(self) -> PurePosixPath: ...
    def session_workspace_dir(self, session_id: str | UUID) -> PurePosixPath: ...

    def memory_root(self) -> PurePosixPath: ...

    def internal_root(self) -> PurePosixPath: ...
    def staging_root(self) -> PurePosixPath: ...
    def run_staging_dir(self, run_id: str | UUID) -> PurePosixPath: ...
    def run_attachment_staging_dir(self, run_id: str | UUID) -> PurePosixPath: ...
    def run_artifact_staging_dir(self, run_id: str | UUID) -> PurePosixPath: ...
    def run_attachment_file(
        self,
        run_id: str | UUID,
        attachment_id: str | UUID,
        filename: str,
    ) -> PurePosixPath: ...
```

Remove these methods:

```text
skills_root
sessions_root
session_dir
session_exports_dir
session_staging_dir
session_runs_dir
run_dir
run_artifacts_dir
run_attachments_dir
run_result_path
attachment_meta_path
artifact_meta_path
```

- [ ] **Step 1: Write failing role-first path tests**

```python
def test_volume_paths_are_role_first() -> None:
    paths = VolumePaths.from_mount("/home/daytona/fleet")
    session_id = UUID("11111111-1111-1111-1111-111111111111")
    run_id = UUID("22222222-2222-2222-2222-222222222222")
    attachment_id = UUID("33333333-3333-3333-3333-333333333333")
    artifact_id = UUID("44444444-4444-4444-4444-444444444444")

    assert str(paths.attachment_blob_path(attachment_id)) == (
        "/home/daytona/fleet/attachments/33333333-3333-3333-3333-333333333333/blob"
    )
    assert str(paths.artifact_blob_path(artifact_id)) == (
        "/home/daytona/fleet/artifacts/44444444-4444-4444-4444-444444444444/blob"
    )
    assert str(paths.session_workspace_dir(session_id)) == (
        "/home/daytona/fleet/workspace/11111111-1111-1111-1111-111111111111"
    )
    assert str(paths.run_staging_dir(run_id)) == (
        "/home/daytona/fleet/.internal/staging/22222222-2222-2222-2222-222222222222"
    )
```

- [ ] **Step 2: Run the tests and verify they fail against the legacy API**

```bash
uv run pytest tests/unit/backend/test_daytona_paths.py tests/unit/backend/test_attachment_paths.py -q
```

Expected: failures because `workspace_root()`, `internal_root()`, and the new Run-only staging methods do not exist.

- [ ] **Step 3: Implement the minimal role-first `VolumePaths` facade**

Use `resolve_under_root()` and `validate_path_id()` for every UUID segment. Validate `filename` exactly as the current Attachment path implementation does.

- [ ] **Step 4: Update `DaytonaAttachmentPathPolicy`**

```python
@dataclass(frozen=True, slots=True)
class DaytonaAttachmentPathPolicy:
    paths: VolumePaths

    def attachment_blob(self, attachment_id: UUID) -> str:
        return as_posix(self.paths.attachment_blob_path(attachment_id))

    def run_attachment(self, run: AttachmentRun, attachment_id: UUID, filename: str) -> str:
        return as_posix(
            self.paths.run_attachment_file(
                run.run_id,
                attachment_id,
                filename,
            )
        )
```

`session_id` remains in `AttachmentRun` for authorization and domain identity but no longer appears in the staging path.

- [ ] **Step 5: Run focused tests**

```bash
uv run pytest tests/unit/backend/test_daytona_paths.py tests/unit/backend/test_attachment_paths.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/fleet_rlm/daytona/paths.py src/fleet_rlm/files/paths.py tests/unit/backend/test_daytona_paths.py tests/unit/backend/test_attachment_paths.py
git commit -m "refactor(daytona): define role-first volume paths"
```

---

## Task 2: Simplify Acquire-Time Volume Provisioning

**Files:**
- Modify: `src/fleet_rlm/daytona/volume_layout.py`
- Modify: `src/fleet_rlm/daytona/session_manager.py`
- Test: `tests/unit/backend/test_daytona_volume_layout.py`

**Interfaces:**
- Consumes: `VolumePaths`, `session_id`, and `run_id`.
- Produces: the smallest required directory set for one Run.

### Required directory contract

```python
def shared_volume_directories(paths: VolumePaths) -> tuple[str, ...]:
    return (
        str(paths.attachments_root()),
        str(paths.artifacts_root()),
        str(paths.workspace_root()),
        str(paths.memory_root()),
        str(paths.internal_root()),
        str(paths.staging_root()),
    )


def session_volume_directories(paths: VolumePaths, *, session_id: UUID) -> tuple[str, ...]:
    return (str(paths.session_workspace_dir(session_id)),)


def run_volume_directories(paths: VolumePaths, *, run_id: UUID) -> tuple[str, ...]:
    return (
        str(paths.run_staging_dir(run_id)),
        str(paths.run_attachment_staging_dir(run_id)),
        str(paths.run_artifact_staging_dir(run_id)),
    )
```

- [ ] **Step 1: Write a failing exact-directory test**

```python
def test_required_volume_directories_are_small_and_role_first() -> None:
    paths = VolumePaths.from_mount("/home/daytona/fleet")
    session_id = UUID("11111111-1111-1111-1111-111111111111")
    run_id = UUID("22222222-2222-2222-2222-222222222222")

    assert required_volume_directories(paths, session_id=session_id, run_id=run_id) == (
        "/home/daytona/fleet/attachments",
        "/home/daytona/fleet/artifacts",
        "/home/daytona/fleet/workspace",
        "/home/daytona/fleet/memory",
        "/home/daytona/fleet/.internal",
        "/home/daytona/fleet/.internal/staging",
        "/home/daytona/fleet/workspace/11111111-1111-1111-1111-111111111111",
        "/home/daytona/fleet/.internal/staging/22222222-2222-2222-2222-222222222222",
        "/home/daytona/fleet/.internal/staging/22222222-2222-2222-2222-222222222222/attachments",
        "/home/daytona/fleet/.internal/staging/22222222-2222-2222-2222-222222222222/artifacts",
    )
```

- [ ] **Step 2: Run the test and verify the legacy Session tree appears**

```bash
uv run pytest tests/unit/backend/test_daytona_volume_layout.py -q
```

Expected: FAIL because the current implementation creates `skills/`, `sessions/`, `exports/`, and Run-scoped public directories.

- [ ] **Step 3: Replace the legacy directory functions**

Update `required_volume_directories()`, `shared_volume_directories()`, `session_volume_directories()`, and `run_volume_directories()` to match the target contract.

- [ ] **Step 4: Remove Skill package materialization**

Delete:

```text
_ensure_skill_tree
_iter_resource_files
_ensure_skill_parent_directories
```

Remove their imports and all calls from `ensure_volume_layout()` and `ensure_shared_volume_layout()`. Bundled Skills remain available through `SkillCatalog`, `load_skill`, and `read_skill_resource`.

- [ ] **Step 5: Update `session_manager.py` callers**

Pass `run_id` without `session_id` to `run_volume_directories()` while retaining `session_id` for `session_volume_directories()`.

- [ ] **Step 6: Run focused tests**

```bash
uv run pytest tests/unit/backend/test_daytona_volume_layout.py tests/unit/backend/test_daytona_session_manager.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/fleet_rlm/daytona/volume_layout.py src/fleet_rlm/daytona/session_manager.py tests/unit/backend/test_daytona_volume_layout.py tests/unit/backend/test_daytona_session_manager.py
git commit -m "refactor(daytona): simplify mounted volume layout"
```

---

## Task 3: Migrate Attachment and Artifact Storage Consumers

**Files:**
- Modify: `src/fleet_rlm/daytona/run_environment.py`
- Modify: `src/fleet_rlm/files/lifecycle.py`
- Modify: `src/fleet_rlm/files/paths.py`
- Modify: `src/fleet_rlm/artifacts/promotion.py`
- Modify: `src/fleet_rlm/artifacts/daytona_catalog.py`
- Test: `tests/unit/backend/test_attachment_paths.py`
- Test: `tests/unit/backend/test_artifact_promotion.py`
- Test: `tests/contracts/backend/test_daytona_storage_contract.py`

**Interfaces:**
- Consumes: role-first canonical paths and hidden Run staging paths.
- Produces: unchanged public Attachment and Artifact behavior with simpler storage references.

- [ ] **Step 1: Write a failing Attachment storage contract test**

```python
async def test_attachment_uses_canonical_blob_and_hidden_run_staging(...) -> None:
    uploaded = await attachment_module.upload(...)
    prepared = await attachment_module.prepare_run(...)

    assert uploaded.storage_ref.startswith("/home/daytona/fleet/attachments/")
    assert "/sessions/" not in uploaded.storage_ref
    assert prepared.staged[0].sandbox_path.startswith("/home/daytona/fleet/.internal/staging/")
    assert "/attachments/" in prepared.staged[0].sandbox_path
```

- [ ] **Step 2: Write a failing Artifact promotion contract test**

```python
async def test_artifact_candidate_promotes_from_hidden_staging_to_role_root(...) -> None:
    candidate = await create_candidate(...)
    promoted = await promote(candidate)

    assert candidate.staging_path.startswith("/home/daytona/fleet/.internal/staging/")
    assert promoted.storage_ref.startswith("/home/daytona/fleet/artifacts/")
    assert "/sessions/" not in promoted.storage_ref
```

- [ ] **Step 3: Run the tests and verify legacy paths fail assertions**

```bash
uv run pytest tests/unit/backend/test_attachment_paths.py tests/unit/backend/test_artifact_promotion.py tests/contracts/backend/test_daytona_storage_contract.py -q
```

Expected: FAIL on paths containing `sessions/<session-id>/runs/<run-id>`.

- [ ] **Step 4: Update Daytona Run preparation**

In `run_environment.py`:

- keep canonical Attachment reads at `attachments/<attachment-id>/blob`;
- stage selected Attachment bytes under `.internal/staging/<run-id>/attachments/<attachment-id>/<filename>`;
- keep Artifact candidates under `.internal/staging/<run-id>/artifacts/`;
- preserve current integrity checks and cleanup ownership.

- [ ] **Step 5: Keep database storage references opaque**

Do not derive Session or Run ownership by parsing storage paths. Continue using database fields for `workspace_id`, `session_id`, and `run_id` authorization.

- [ ] **Step 6: Run focused tests**

```bash
uv run pytest tests/unit/backend/test_attachment_paths.py tests/unit/backend/test_artifact_promotion.py tests/contracts/backend/test_daytona_storage_contract.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/fleet_rlm/daytona/run_environment.py src/fleet_rlm/files/lifecycle.py src/fleet_rlm/files/paths.py src/fleet_rlm/artifacts/promotion.py src/fleet_rlm/artifacts/daytona_catalog.py tests/unit/backend/test_attachment_paths.py tests/unit/backend/test_artifact_promotion.py tests/contracts/backend/test_daytona_storage_contract.py
git commit -m "refactor(storage): use role-first attachment and artifact paths"
```

---

## Task 4: Move Session Workspace to `workspace/<session-id>`

**Files:**
- Modify: `src/fleet_rlm/daytona/run_environment.py`
- Modify: `src/fleet_rlm/daytona/workspace_fs.py`
- Modify: `src/fleet_rlm/files/workspace_tools.py`
- Test: `tests/unit/backend/test_daytona_session_workspace_fs.py`
- Test: `tests/live/backend/test_b5_attachment_artifact_durability.py`

**Interfaces:**
- Consumes: `VolumePaths.session_workspace_dir(session_id)`.
- Produces: the same Workspace tool contract over the flatter durable location.

- [ ] **Step 1: Update the Workspace root assertion**

```python
def test_workspace_root_is_role_first() -> None:
    paths = VolumePaths.from_mount("/home/daytona/fleet")
    session_id = UUID("11111111-1111-1111-1111-111111111111")
    assert str(paths.session_workspace_dir(session_id)) == (
        "/home/daytona/fleet/workspace/11111111-1111-1111-1111-111111111111"
    )
```

- [ ] **Step 2: Keep the public Workspace tools unchanged**

The following names and return contracts do not change:

```text
list_workspace_files
stat_workspace_file
read_workspace_text
write_workspace_text
```

- [ ] **Step 3: Update Workspace construction in `run_environment.py`**

Construct `DaytonaSessionWorkspaceFS` with the new Session root while keeping the trusted Volume root `/home/daytona/fleet`.

- [ ] **Step 4: Update the live durability layout assertions**

The live test must require:

```text
/home/daytona/fleet/workspace/<session-id>
/home/daytona/fleet/.internal/staging/<run-id>
```

It must reject the presence of:

```text
/home/daytona/fleet/sessions
/home/daytona/fleet/skills
```

- [ ] **Step 5: Run Workspace tests**

```bash
uv run pytest tests/unit/backend/test_daytona_session_workspace_fs.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/fleet_rlm/daytona/run_environment.py src/fleet_rlm/daytona/workspace_fs.py src/fleet_rlm/files/workspace_tools.py tests/unit/backend/test_daytona_session_workspace_fs.py tests/live/backend/test_b5_attachment_artifact_durability.py
git commit -m "refactor(workspace): flatten durable session workspace paths"
```

---

## Task 5: Simplify Orphan Cleanup and Run Finalization

**Files:**
- Modify: `src/fleet_rlm/daytona/orphan_cleanup.py`
- Modify: `src/fleet_rlm/composition/daytona.py`
- Modify: `src/fleet_rlm/chat/turn_cleanup.py`
- Test: `tests/unit/backend/test_orphan_cleanup.py`

**Interfaces:**
- Consumes: committed Artifact storage refs and active Run IDs.
- Produces: bounded cleanup limited to role-first Artifacts and hidden staging.

### Target cleanup roots

```text
artifacts/
.internal/staging/
```

Do not scan `workspace/`, `attachments/`, or `memory/` during orphan cleanup.

- [ ] **Step 1: Write a failing bounded cleanup test**

```python
async def test_cleanup_scans_only_artifacts_and_hidden_staging(...) -> None:
    report = await cleanup_orphan_bytes(...)

    assert gateway.listed_roots == (
        "/home/daytona/fleet/artifacts",
        "/home/daytona/fleet/.internal/staging",
    )
```

- [ ] **Step 2: Define keep rules**

- retain every committed Artifact path supplied by the database;
- retain every file beneath `.internal/staging/<active-run-id>/`;
- remove stale uncommitted Artifact blobs;
- remove stale staging files for inactive Runs after the configured grace period;
- never inspect or remove Workspace, Attachment, or memory files.

- [ ] **Step 3: Remove legacy Session-tree parsing**

Delete helpers that parse:

```text
sessions/<session-id>/runs/<run-id>/...
```

Replace them with Run ID extraction relative to `.internal/staging/`.

- [ ] **Step 4: Reconcile stale Runs before cleanup**

In `composition/daytona.py`, construct the Turn state store and reconcile stale settling Runs before querying active Run IDs and invoking orphan cleanup.

- [ ] **Step 5: Run cleanup tests**

```bash
uv run pytest tests/unit/backend/test_orphan_cleanup.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/fleet_rlm/daytona/orphan_cleanup.py src/fleet_rlm/composition/daytona.py src/fleet_rlm/chat/turn_cleanup.py tests/unit/backend/test_orphan_cleanup.py
git commit -m "refactor(daytona): bound cleanup to role-first storage"
```

---

## Task 6: Remove Legacy Layout References and Document the Contract

**Files:**
- Modify: `README.md`
- Modify: `docs/architecture.md`
- Modify: `docs/reference/codebase-map.md`
- Modify: `docs/how-to-guides/dspy-integration.md`
- Modify: `src/fleet_rlm/CONTEXT.md`
- Modify: `tests/contracts/backend/test_daytona_storage_contract.py`

**Interfaces:**
- Consumes: completed role-first implementation.
- Produces: one documented and enforced Volume contract.

- [ ] **Step 1: Add a contract test that rejects legacy production paths**

```python
def test_production_source_has_no_legacy_volume_layout_references() -> None:
    forbidden = (
        "session_exports_dir",
        "session_staging_dir",
        "session_runs_dir",
        "run_result_path",
        "skills_root",
    )
    source = "\n".join(path.read_text() for path in Path("src/fleet_rlm").rglob("*.py"))
    for value in forbidden:
        assert value not in source
```

- [ ] **Step 2: Run a repository search**

```bash
rg -n 'sessions/.+runs|session_exports_dir|session_staging_dir|session_runs_dir|run_result_path|skills_root|result\.json' src tests docs README.md
```

Expected: no production references. Historical migration notes are not added in this plan.

- [ ] **Step 3: Document the final layout**

Use this exact tree:

```text
/home/daytona/fleet/
├── attachments/<attachment-id>/blob
├── artifacts/<artifact-id>/blob
├── workspace/<session-id>/...
├── memory/.keep
└── .internal/staging/<run-id>/{attachments,artifacts}/...
```

Document that:

- the mounted subpath already supplies Fleet workspace isolation;
- database rows own all Session, Run, user, and workspace relationships;
- `.internal/` is not user-facing;
- `memory/` is reserved and has no automatic behavior;
- ordinary RLM scratch work should move to the Sandbox-local project directory in the separate Daytona capabilities plan.

- [ ] **Step 4: Run the full offline validation**

```bash
uv run pytest tests/unit/backend -q
uv run pytest tests/contracts/backend -q
make check
make api-check
git diff --check
```

Expected: PASS.

- [ ] **Step 5: Run the opt-in durability test when credentials are available**

```bash
FLEET_LIVE=1 uv run pytest tests/live/backend/test_b5_attachment_artifact_durability.py -q -n 0 --timeout=600
```

Expected: the same Workspace and canonical Attachment bytes survive Sandbox replacement, committed Artifacts remain readable, and hidden Run staging is cleaned.

- [ ] **Step 6: Commit**

```bash
git add README.md docs src/fleet_rlm/CONTEXT.md tests/contracts/backend/test_daytona_storage_contract.py
git commit -m "docs(daytona): define role-first durable storage contract"
```

---

## Final Acceptance Criteria

The implementation is complete only when all statements are true:

- The mounted Daytona Volume is organized by semantic role.
- There is no public durable `sessions/` or `runs/` hierarchy.
- Attachment bytes use `attachments/<attachment-id>/blob`.
- Artifact bytes use `artifacts/<artifact-id>/blob`.
- Session Workspace files use `workspace/<session-id>/...`.
- Run-specific durable state exists only under `.internal/staging/<run-id>/`.
- No automatic `result.json` is written.
- No Python Skills package is copied into the Volume.
- Attachment and Artifact metadata remain database-owned.
- Workspace, Attachments, Artifacts, and reserved memory survive Sandbox replacement through the mounted Volume.
- Python interpreter variables remain Run-local and are not treated as durable state.
- Orphan cleanup never scans or deletes Workspace, Attachment, or memory files.
- All focused unit, contract, API, and formatting checks pass.

## Explicitly Deferred

The following are intentionally outside this plan:

- a curated memory implementation;
- a shared cross-Session Workspace;
- a public Volume browser;
- automatic migration of legacy Volumes;
- compatibility dual-read or dual-write;
- remote repository cloning;
- Sandbox-local project helpers;
- codebase-analysis and long-document-Q&A Skills;
- vector indexes;
- distributed workers or queues.
