# GEPA quality dossier

## Phase 8 — Typed GEPA quality and promotion lane

- **Order:** `8`
- **Status:** `partial`
- **Track:** `Config`
- **Summary:** Complete the offline GEPA lane with sealed promotion tests, typed immutable run contracts, and workspace-scoped activation.
- **Owner:** `src/fleet_rlm/quality/`
- **Dependency contract:** exactly `dspy==3.3.0b1` and `gepa==0.1.1`

Fleet already has an offline GEPA runner, managed DSPy modules, datasets, run
evidence, promotion drafts, API/UI surfaces, and optional MLflow tracking. The
remaining work is to make evaluation and promotion integrity explicit and
durable. In particular, GEPA's `valset` is the **selection** set used by Pareto
search; it is not an external holdout and cannot by itself authorize promotion.

### Ownership and terminology

`training` examples drive reflection, `selection` is passed to `GEPA.compile`
as `valset`, and the sealed `promotion_test` remains invisible until the
baseline and selected winner are evaluated once. A **Managed Target** is a
registered module or catalog-resolved Skill. Its versioned **Metric Profile**
owns the five-argument metric, normalized score, actionable sanitized feedback,
subscores, hard gates, critical slices, and default promotion thresholds.

The server accepts only managed IDs, approved immutable Dataset Versions, and
explicit Task/Reflection Model profile and model IDs. Arbitrary imports and
filesystem paths remain local CLI workflows. Optimization is instruction-only:
Fleet uses `dspy.GEPA` against `named_predictors()` and makes no claim to
optimize control flow, tools, demonstrations, or an entire agent.

### Locked protocol

- Exactly one budget mode is selected: `auto`, `max_metric_calls`, or
  `max_full_evals`, plus a Fleet wall-clock bound. Promotion-grade runs require
  an explicit metric-call cap.
- Ordinary modules use DSPy's proposer. Only eligible trace-aware Skill targets
  use Fleet's constrained Daytona/RLM proposer.
- Task LM, Reflection LM, task adapter, and provider wire format are captured in
  the immutable run spec. Ambient process settings are never evidence-bearing.
- Search defaults are Pareto selection, round-robin component selection, merge
  enabled with five attempts, minibatch 3, seed 0, perfect-score skipping,
  statistics enabled, best-output tracking disabled, and format-failure
  feedback disabled.
- Expected target/parse failures are scored by the Metric Profile. Provider,
  auth, judge, metric, persistence, checkpoint, or sandbox failures fail the run
  or block promotion.
- Checkpoints are trusted, run-scoped, non-promotable artifacts. Explicit resume
  requires an exact fingerprint match; recovery is never automatic.
- Module artifacts are state-only JSON loaded into a fresh registered factory;
  Skill artifacts are Markdown. Pickles are not deployment artifacts.
- MLflow uses `fleet-gepa-optimization` without process-global experiment
  mutation. Payload logging is sanitized by default and restricted opt-in.

### Promotion and activation

GEPA's winner is only a candidate. Promotion requires a strict positive sealed
test delta, zero hard-gate failures, no critical-slice regression, no expected
task-failure regression, cost and p95 latency within 20% of baseline unless the
Metric Profile overrides them, adequate test coverage, and a successful
save/load/re-evaluation round trip. Human approval creates an approved artifact.
Activation and rollback are separate atomic actions keyed by tenant, workspace,
target kind, and target ID; one previous artifact is retained. With quality
activation disabled or no pointer present, runtime behavior is a literal code or
catalog default.

### Migration rules

- Existing datasets become unapproved legacy versions with `unassigned` rows.
- Existing runs remain readable as `protocol_version="legacy"` and cannot be
  promoted without a new three-way run.
- Filesystem promotion drafts remain viewable but not approvable or activatable.
- The deprecated blocking endpoint accepts the new managed request for one
  release and delegates to the same engine.
- Migration never creates an activation pointer.

### Acceptance gates

- [x] Typed managed request, model, budget, search, partition, fingerprint, and
  promotion-gate contracts exist.
- [x] Runner keeps promotion-test rows out of GEPA selection/reflection and
  evaluates baseline and winner afterward.
- [x] Dataset approval, cancel request, persisted candidate artifacts, atomic
  activation, rollback, and LocalStore fail-closed stubs are complete.
- [x] API exposes managed scorecard plus approve/activate/rollback surfaces
  (`GET .../scorecard`, artifact approve/activate, target rollback/get).
- [x] Runtime resolve helpers load workspace activation at module ownership
  seams (`quality/activation_resolve.py`, `build_module_with_optional_activation`).
- [x] Resume-from-checkpoint with exact fingerprint match is complete
  (`POST .../resume`, `resume_optimization_run`, no automatic recovery).
- [x] UI scorecard + approve/activate/resume actions for managed promotion path
  (filesystem drafts demoted to legacy secondary action).
- [ ] Focused and full validation gates pass, followed by an opt-in live smoke.

Evidence required before `complete`: exact-version contract tests, isolation
tests proving no promotion-test leakage, model/context isolation, failure and
resume tests, artifact round trips, sanitized MLflow tests, promotion scorecards,
tenant/workspace authorization tests, API/UI tests, full repository validation,
and live approval/activation/rollback evidence stored beside this dossier.

**Backend slice (2026-07-11):** Postgres Dataset Version upload/review/seal,
managed-run resolution, sealed promotion-test evaluation (GEPA `valset` =
selection only per DSPy docs), promotion gate evidence producers (including
artifact round-trip), candidate `optimization_artifact_versions` on run success,
approve → activate → rollback APIs, cancel-before-execution, LocalStore
unsupported stubs, and unit coverage for activation resolve + promotion gates.
Still open: opt-in live smoke evidence file, and CI Postgres runs of
`tests/integration/database/test_optimization_activation_lifecycle.py`
(skipped locally without `DATABASE_URL`).

Structural SQLAlchemy package ownership (model registry, `src/fleet_rlm/db/`
move, re-exports) is tracked in
[Phase 8.5 — Persistence DB](../08.5-persistence-db/README.md). Phase 8.5 does
not replace any Phase 8 GEPA acceptance item and must not block Phase 9.
When practical, freeze further optimization schema churn before large 8.5A
module renames so GEPA migrations and package moves do not thrash each other.

See [evidence-design-audit.md](evidence-design-audit.md).
The activation boundary is recorded in
[ADR-0006](../../../adr/0006-workspace-scoped-optimized-artifacts.md).
