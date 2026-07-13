# Phase 8 design audit — DSPy GEPA evaluation integrity

## Finding

The pre-Phase-8 runner split examples into training and a set described as
validation/holdout, then passed that same set to `dspy.GEPA.compile(valset=...)`
and treated its score as promotion evidence. In DSPy 3.3.0b1, GEPA uses
`valset` during Pareto candidate selection. It is therefore internal selection
data, not a sealed external holdout. A winner selected on it must be evaluated
on separate, unseen promotion-test data before promotion can be considered.

## Current implementation versus target contract

| Surface | Existing capability | Phase 8 correction |
| --- | --- | --- |
| Engine | Offline `dspy.GEPA` runner | State instruction-only scope explicitly |
| Targets | Registered modules plus local arbitrary workflows | Server managed IDs only; local CLI retains arbitrary paths |
| Data | Runtime ratio split | Immutable versions with explicit training/selection/promotion-test partitions |
| Models | Reflection LM plus ambient task behavior | Explicit profile/model IDs and captured adapter/wire format |
| Budget | Flat `auto`/metric-call inputs | Discriminated union plus wall-clock bound |
| Evidence | GEPA selection score presented as validation | Sealed baseline/winner test scorecard and round-trip gate |
| Drafts | Filesystem JSON source of truth | Database artifact version; file is referenced payload |
| Runtime | No workspace activation contract | Tenant/workspace target pointer with atomic rollback |

## Compatibility conclusion

Existing rows and runs must stay readable, but neither can silently acquire
promotion eligibility. Legacy datasets are unapproved with unassigned rows;
legacy runs carry `protocol_version="legacy"`; legacy drafts remain read-only.
No schema migration changes runtime behavior or creates an active pointer.

## Evidence already added in this slice

- Typed immutable run, budget, search, partition, fingerprint, and promotion
  policy modules under `src/fleet_rlm/quality/`.
- Explicit partition isolation in the runner: only training and selection enter
  GEPA; sealed promotion-test rows are evaluated after compilation.
- Exact-match resume fingerprint helper and promotion gates that fail closed.
- Persistence schema for dataset version metadata, per-example hashes and
  partitions, run lifecycle/fingerprint fields, artifact versions, and
  activation pointers.
- Managed Dataset Version review and approval endpoints now fail closed on
  local persistence, unassigned rows, missing approval state, and canonical
  content-digest drift.
- Canonical optimization requests resolve an immutable run spec and fingerprint
  before persistence, validate the target's qualified Metric Profile, and cap
  the requested wall-clock budget at the process ceiling.
- Promotion readiness reports missing evidence explicitly; file existence,
  placeholder failure rates, and non-metric-call budgets cannot authorize
  promotion.

This document records design evidence, not phase-completion evidence. The
unchecked acceptance items in the dossier remain required.
