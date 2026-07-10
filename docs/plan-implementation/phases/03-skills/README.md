# Skills dossier

The Skills module is complete overall through remote lifecycle support. Phase 3F
remains explicitly partial because scaffold-script materialization is deferred;
trusted selected-skill scripts already execute inside Daytona.

## Phase 3A — Skills package foundation

- **Order:** `3`
- **Status:** `complete`
- **Track:** `Skills`
- **Summary:** Establish canonical Skills schemas, validation, permissions, and compatibility exports.
- **Commit:** `50027fe5`

### Acceptance criteria

- [x] `src/fleet_rlm/skills/` owns schemas, validation, permissions, and paths.
- [x] `ActiveSkills` remains safely serializable with empty defaults.
- [x] Compatibility re-exports preserve old runtime imports.

### Validation

```bash
uv run pytest tests/unit/skills/
```

## Phase 3B — Catalog, repository, and loader

- **Order:** `3.1`
- **Status:** `complete`
- **Track:** `Skills`
- **Summary:** Load visible directory and legacy-flat Skills with deterministic precedence.
- **Commit:** `50027fe5`

### Acceptance criteria

- [x] Catalog precedence is session, user, project, org, system, then scaffold.
- [x] Repository listing and bundle/resource loading enforce visibility.
- [x] Legacy flat and directory-style Skills remain compatible.

## Phase 3C — Skill selection integration

- **Order:** `3.2`
- **Status:** `complete`
- **Track:** `Skills`
- **Summary:** Make explicit and automatic selection context-aware across both runtimes.
- **Commit:** `50027fe5`

### Acceptance criteria

- [x] Explicit visible IDs take priority and invisible IDs are rejected.
- [x] Direct and legacy runtimes honor selected Skill IDs.
- [x] Selector failure degrades to deterministic candidates.

## Phase 3D — FastAPI Skill interfaces, read-only

- **Order:** `3.3`
- **Status:** `complete`
- **Track:** `Skills`
- **Summary:** Expose visibility-aware catalog and bundle metadata without mutation.
- **Commit:** `d29dc042,1d99636c`

### Acceptance criteria

- [x] Read interfaces return only visible Skills and typed safe metadata.
- [x] Domain errors map to stable sanitized HTTP responses.
- [x] Read-only scope performs no writes or script execution.

## Phase 3E — RLM Skill tools, read-only

- **Order:** `3.4`
- **Status:** `complete`
- **Track:** `Skills`
- **Summary:** Provide thin RLM tools for listing, loading, and reading visible Skill resources.
- **Commit:** `fd8ac583`

### Acceptance criteria

- [x] Skill tools delegate to the canonical Skills module.
- [x] Visibility and resource-path validation are preserved.
- [x] Compatibility facades do not become a second implementation.

## Phase 3F — Trusted Skill script execution

- **Order:** `3.5`
- **Status:** `partial`
- **Track:** `Skills`
- **Summary:** Execute trusted selected-Skill scripts inside Daytona while deferring scaffold materialization.
- **Commit:** `53227b95`

### Stable interfaces

`run_skill_script` resolves trusted selected-Skill scripts and executes them in
Daytona, never on the FastAPI host. `_active_skills` wiring is not required for
the shipped selected-Skill path. Public failures do not expose stdout/stderr.

### Acceptance criteria

- [x] Trusted selected-Skill scripts execute only in Daytona.
- [x] Requests enforce visibility, trust, selection, and safe script paths.
- [x] Default tests use fakes and require no live sandbox.
- [ ] Scaffold Skill scripts are materialized when product requirements demand it.

### Evidence

- [Skill script runtime preparation](evidence-skill-script-runtime.md)

## Phase 3G — Skill writes and approval workflow

- **Order:** `3.6`
- **Status:** `complete`
- **Track:** `Skills`
- **Summary:** Add staged writes, explicit approval, audit, and policy-aware mutation.
- **Commit:** `c9041c68`

### Acceptance criteria

- [x] Writes are staged and require the appropriate approval path.
- [x] Mutation is visibility-, scope-, and policy-aware.
- [x] Audit records contain safe metadata without exposing secrets.

## Phase 3H — Remote Skill lifecycle

- **Order:** `3.7`
- **Status:** `complete`
- **Track:** `Skills`
- **Summary:** Add remote install, provenance, security validation, and update lifecycle.
- **Commit:** `8b49092c`

### Acceptance criteria

- [x] Remote installs record provenance and pass security validation.
- [x] Update/removal lifecycle preserves scope and authorization rules.
- [x] Public serialization remains typed and safe.

## Deferred gaps

- Emit selected Skill metadata into traces and performance summaries in Phase 6.
- Continue catalog-selection warnings, symlink escape defense, and package-seam cleanup.
- Keep scaffold script materialization deferred until it becomes a product requirement.
