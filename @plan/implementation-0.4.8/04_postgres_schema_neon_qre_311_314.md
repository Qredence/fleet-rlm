# Group 4 — Postgres Schema / Neon Optimization (QRE-311 to QRE-314)

## Scope

This group evolves the backend persistence model for RLM/DSPy workflows on Neon/Postgres by:

- removing deprecated schema debt (`QRE-311`)
- adding new RLM/DSPy and Modal infrastructure schema entities (`QRE-312`, `QRE-313`)
- applying Neon-oriented schema/index/performance conventions (`QRE-314`)

This is the most migration-sensitive group in v0.4.8 and requires tight sequencing around Alembic generation/application.

## Ticket Inventory

| Ticket | Title | Status | Priority | Labels | Duplicate? | Explicit blockers | Explicit blocked items |
| --- | --- | --- | --- | --- | --- | --- | --- |
| QRE-311 | Remove Deprecated "Skills" Taxonomy from Postgres Schema | Triage | Unspecified | `backend`, `v0.4.8`, `developer-experience`, `architecture`, `dspy` | No | None | `QRE-312`, `QRE-313` |
| QRE-312 | Implement Core RLM & DSPy Tables in Postgres | Triage | Unspecified | `backend`, `dspy`, `v0.4.8`, `architecture`, `performance`, `enhancement`, `infrastructure`, `developer-experience`, `e2e` | No | `QRE-311` | None |
| QRE-313 | Implement Modal Infrastructure Tracking in Postgres | Triage | Unspecified | `infrastructure`, `backend`, `enhancement`, `v0.4.8`, `architecture`, `performance`, `developer-experience` | No | `QRE-311` | None |
| QRE-314 | Apply Neon Postgres Performance Optimizations | Triage | Unspecified | `backend`, `performance`, `architecture`, `v0.4.8`, `developer-experience` | No | None | None |

## Chain of Command (Dependencies)

### Explicit Linear blockers / duplicates

- `QRE-311` explicitly **blocks** `QRE-312`.
- `QRE-311` explicitly **blocks** `QRE-313`.
- No duplicates in this group.

### Inferred prerequisites (**Assumptive Logic**)

1. **`QRE-312` and `QRE-313` should precede `QRE-314`**
   - Neon optimization decisions (indexes, UUID strategy, type choices) are most useful once the new schema additions are known.
   - Running `QRE-314` first risks duplicate migrations and rework.

2. **Model design for `QRE-312` and `QRE-313` can parallelize, but Alembic migration generation/application must be serialized**
   - Both tickets touch `src/fleet_rlm/db/models.py` and likely produce overlapping migration diffs.
   - Parallel autogen without coordination creates migration conflicts and invalid upgrade paths.

3. **`QRE-314` should document which optimizations are applied vs deferred**
   - Some PK strategy changes may be invasive on existing tables and better staged post-v0.4.8.

### Sequencing rationale tied to file/module overlap

- All four tickets converge on `src/fleet_rlm/db/models.py` and Alembic migrations.
- `QRE-311` reduces schema clutter first, lowering ambiguity when adding `QRE-312`/`QRE-313` models.
- `QRE-314` may also touch `db/models.py`, base UUID utilities, and migrations, so it should be an explicit final schema-tuning pass.

## Technical Deep Dive (Per Ticket)

### QRE-311 — Remove Deprecated "Skills" Taxonomy from Postgres Schema

#### Technical Risks

- Hidden runtime/script references to removed models may fail at import or runtime after schema removal.
- Alembic autogeneration may not fully capture enum cleanup or table drop ordering.
- RLS / tenant-scoped FK assumptions can break if migration removal order is incorrect.

#### Edge Cases

- Legacy tables still referenced in compatibility-only code paths or scripts.
- Existing environments with populated legacy data requiring cautious drop order or staged migration.
- Enum drops that fail because dependent objects still exist.

#### Architectural Impact

- Major cleanup of schema debt and clearer alignment to current RLM/DSPy runtime architecture.
- Reduces confusion in `db/models.py` and future migration diffs.
- Creates cleaner foundation for `QRE-312`/`QRE-313` schema additions.

#### Dependency Notes

- **Explicit blocker** for `QRE-312` and `QRE-313`.
- Related to `QRE-314` via schema simplification before optimization decisions.

#### Parallelization Notes

- Should be treated as the first DB track implementation item.
- Avoid parallel migration authoring with `QRE-312`/`QRE-313` before the cleanup migration is finalized.

### QRE-312 — Implement Core RLM & DSPy Tables in Postgres

#### Technical Risks

- Schema naming and relationships may not match future runtime/state manager abstractions.
- Large JSONB trace/program payloads can create storage/performance pressure without limits/index strategy.
- UUID strategy inconsistencies (app-side vs DB-generated) can complicate inserts/migrations.

#### Edge Cases

- Linking traces to runs/steps where existing entities may not fully represent trajectory granularity.
- JSONB payloads with large nested structures requiring deferred indexing strategy.
- Migration autogen creating incomplete/undesired indexes or constraints.

#### Architectural Impact

- High strategic value: establishes persistence primitives for RLM programs and traces.
- Unlocks future analysis/self-improvement workflows beyond passive session logging.
- Tightens coupling between execution semantics and database schema design decisions.

#### Dependency Notes

- **Explicitly blocked by `QRE-311`.**
- Related to `QRE-300`/`QRE-301` as future persistence validation surfaces.
- **Assumptive Logic:** sequence before `QRE-314` so optimizations can include new tables/indexes.

#### Parallelization Notes

- Can parallelize design with `QRE-313`.
- Do not parallelize Alembic autogen/application with `QRE-313`; serialize migration creation.

### QRE-313 — Implement Modal Infrastructure Tracking in Postgres

#### Technical Risks

- Premature schema fields for Modal infra/cost metadata may not match eventual runtime telemetry model.
- FK/cascade choices can accidentally remove useful infra history during cleanup operations.
- Cost/metric precision choices may conflict with later Neon tuning decisions.

#### Edge Cases

- Multiple Modal volumes/resources per tenant/session requiring unique keys and sync metadata conventions.
- Correlation fields on `RunStep` or related tables that are optional for some execution paths.
- Backfilling or null-handling for pre-existing rows if columns are added to existing tables.

#### Architectural Impact

- Improves observability linkage between runtime orchestration and persisted state.
- Enables future cost/performance/resource analytics tied to execution runs.
- Introduces infrastructure-domain schema concerns into the core runtime persistence model.

#### Dependency Notes

- **Explicitly blocked by `QRE-311`.**
- Related to `QRE-312` (shared schema wave) and `QRE-314` (performance/index/type tuning).
- **Assumptive Logic:** sequence before `QRE-314` to optimize newly introduced columns/indexes intentionally.

#### Parallelization Notes

- Same as `QRE-312`: design can be parallel, migrations should be serialized.
- Coordinate column additions to existing tables with any concurrent repository/model serialization changes.

### QRE-314 — Apply Neon Postgres Performance Optimizations

#### Technical Risks

- PK strategy changes on existing tables can be invasive and may exceed v0.4.8 migration risk budget.
- Added indexes can improve reads but degrade writes and increase migration time.
- Type changes can be lossy or require data migration scripts if existing data is present.

#### Edge Cases

- Existing UUID defaults and data volume make UUIDv7 migration impractical mid-release.
- Neon-specific guidance conflicts with current cross-environment assumptions.
- Query hot paths are assumed rather than measured, leading to low-value index additions.

#### Architectural Impact

- Establishes schema conventions (UUID locality, index strategy, type sizing) for future RLM schema work.
- Improves long-term Neon fit of the persistence layer.
- Can reduce future migration churn if convention decisions are documented clearly.

#### Dependency Notes

- No explicit blocker in Linear.
- **Assumptive Logic:** run after `QRE-312` and `QRE-313` so optimization pass covers new schema additions and avoids repeated migrations.

#### Parallelization Notes

- Not a good parallel candidate with active `db/models.py` / migration authoring on `QRE-312`/`313`.
- Can parallelize analysis/audit work (query path review, index candidates) while schema additions are being designed.

## Execution Strategy for This Group

### Phase 1: Foundation

1. `QRE-311` — remove deprecated skills taxonomy schema and references
2. Validate cleanup migration and code references before adding new schema entities

### Phase 2: Features

1. `QRE-312` — add core RLM/DSPy tables (models + migration)
2. `QRE-313` — add Modal infrastructure tracking model/correlation fields (models + migration)

Implementation note:

- Model design may proceed in parallel.
- Alembic migration generation/review/application must be serialized and explicitly ordered.

### Phase 3: Optimization

1. `QRE-314` — Neon-focused PK/index/type optimization pass after schema additions stabilize
2. Document applied vs deferred optimization decisions (especially invasive PK strategy changes)

### Phase 4: Schema Validation & Handoff

- Local migration upgrade verification
- Model import/startup sanity checks
- Targeted DB repository insert/read validation where feasible
- Handoff to RLM assessment (`QRE-301`) for integrated persistence validation

## Parallel Tracks (Group-Local)

### Safe parallel work

- `QRE-312` and `QRE-313` design/spec work (relationships, fields, index candidates)
- `QRE-314` analysis/prep (Neon audit, candidate index list) while `QRE-312`/`313` are in progress

### Parallel with coordination

- `QRE-312` + `QRE-313` implementation
  - Shared file risk: `src/fleet_rlm/db/models.py`
  - Shared artifact risk: Alembic autogen diffs
  - Coordination rule: one owner serializes final model merge and migration creation order

### Unsafe / not recommended parallelism

- Parallel Alembic migration autogen/application for `QRE-312` and `QRE-313`
- `QRE-314` landing concurrently with active `QRE-312`/`313` migration rewrites (high rebase/churn risk)

## Integration / Handoff Points

- **To Group 2 (RLM Assessment):** `QRE-301` should validate persistence/output behavior against the evolved schema.
- **To milestone-wide execution strategy:** this group is backend-isolated from most frontend work, making it a strong parallel track, but internally it must be carefully serialized around migrations.
- **To docs/architecture:** schema docs should remove deprecated skills taxonomy references and add new RLM/Modal entities plus Neon conventions.

## Validation Checklist

- [ ] Explicit blocker chain `QRE-311 -> QRE-312` and `QRE-311 -> QRE-313` is documented.
- [ ] `QRE-312`/`QRE-313` before `QRE-314` is marked as **Assumptive Logic**.
- [ ] Alembic migration serialization requirement is explicit.
- [ ] `db/models.py` + migration conflict hotspot is called out.
- [ ] Risks around UUID strategy, indexes, and type changes are included for `QRE-314`.
