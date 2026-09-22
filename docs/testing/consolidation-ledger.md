# Test suite consolidation ledger

Supersedes the [Phase 3 consolidation ledger](phase3-consolidation-ledger.md),
which remains the dated record of the 2026-09-10 reorganization. This ledger
records the 2026-09-20 consolidation.

## Scope and constraints

Test organization and test-layout enforcement only. No `src/` behavior change.
Preserved: every live entry point, receipt schema, evidence environment
variable, documented operator pytest node ID, and the 75% coverage floor.

## Measured result

Baseline captured on `feat/large-source-broker-safety` @ `46b4e199d`, clean tree.

| Metric | Before | After | Target |
| --- | ---: | ---: | ---: |
| Collected test files | 283 | **257** | ≤ 240 |
| Collected cases | 3 151 | **3 011** | no unexplained loss |
| Flat files in `tests/unit/backend/` root | 90 | **0** | 0 |
| Files with ≤ 2 cases (mergeable lanes) | 26 | **0** | 0 |
| Files with ≤ 2 cases (protected lanes) | 27 | 27 | unchanged |
| Coverage, local gate | 80.7 % | **83.2 %** | ≥ 83 % |
| Non-live gate wall time | 24.3 s | **~24 s** | ≤ 24 s |

Case accounting: 3 151 → 3 011 is −145 from collapsing the LiteLLM invariant
parametrization and +5 from the new test-layout tests. Every other merge
preserved its case count exactly; this was verified by `pytest --collect-only`
after each phase.

The ≤ 240 file target was **not** reached (257). Reaching it would require
merging three-case files, which is a larger and riskier refactor than the
evidence supported. See "Deliberate non-actions".

## Phase dispositions

### Gate repair

`tests/unit/optimization` (75 cases, 9 files) was executed by CI's `pytest-unit`
discovery but absent from `PYTEST_FAST_PATHS` and `PYTEST_UNIT_PATHS`. The local
gate therefore reported four optimization modules at 0 % and could not detect an
optimization regression.

Added the lane to both Makefile path lists and to the suite-inventory table in
[Testing Strategy](../how-to-guides/testing-strategy.md).

| Module | Before | After |
| --- | ---: | ---: |
| `optimization/gepa_runner.py` | 0.0 % | 57.1 % |
| `optimization/maintenance.py` | 0.0 % | 80.6 % |
| `optimization/metric.py` | 0.0 % | 75.8 % |
| `optimization/mlflow_observability.py` | 0.0 % | 73.1 % |
| Package total | 80.7 % | 83.2 % |

### Dead artifact removal

Removed the empty `tests/unit/backend/benchmarking/` directory and four
`.DS_Store` files (gitignored, disk hygiene only).

### Structural move

All 90 flat `test_*.py` files in `tests/unit/backend/` were moved into
behavior-owner sub-packages. Placement rule: the source module the file
primarily exercises. Verified as 90 Git renames with an identical case count.

| Destination | Files | Destination | Files |
| --- | ---: | --- | ---: |
| `turn/` | 22 | `cli/` | 3 |
| `observability/` | 11 | `api/` | 2 |
| `persistence/` | 13 | `composition/` | 2 |
| `workspace/` | 7 | `rlm/` | 2 |
| `daytona/` | 7 | `tests/unit/scripts/` | 1 |
| `sessions/` | 7 | | |

`tests/unit/backend/__init__.py` and a new `__init__.py` in every sub-package
make the whole subtree regular packages. This removes a latent import-mismatch
risk: previously some directories were packages and others were namespace
packages, so a single module could be imported twice under two names.

Two plan deviations, both for module alignment:

- `cli/` was added as its own owner (`test_cli`, `test_cli_supervisor`,
  `test_bind_safety`) rather than folded into `config/`, matching
  `fleet_rlm/cli/`.
- `test_adapter_benchmark_v2.py` was **misplaced**: it imports
  `scripts.benchmarks.*`, not the backend, so it moved to `tests/unit/scripts/`.

### Fragmentation collapse

Merges are listed as `target <- sources`. Every merge preserved all assertions;
none were rewritten or dropped.

| Target | Sources merged in | Files | Cases |
| --- | --- | ---: | ---: |
| `persistence/test_claim_contention.py` | `test_contention_scenarios`, `test_claim_constraint_classification` | 2 → 1 | 16 |
| `persistence/test_query_contracts.py` | `test_query_plan_scenarios`, `test_persistence_query_contracts` | 2 → 1 | 10 |
| `persistence/test_schema_migrations.py` | `test_binding_lineage_migration`, `test_database_compatibility`, `turn/test_turn_lineage_migration` | 3 → 1 | 10 |
| `persistence/test_database_policy.py` | `test_engine_pool_policy`, `test_managed_database_policy`, `test_persistence_observations` | 3 → 1 | 19 |
| `persistence/test_memory_promotion.py` | `test_memory_promotion_intents`, `test_memory_promotion_outbox` | 2 → 1 | 18 |
| `persistence/test_database_preflight.py` | `test_ensure_database_compatible` | 2 → 1 | 7 |
| `rlm/test_runtime_execution.py` | `test_session_runtime_reuse`, `runtime/test_execution_context` | 3 → 1 | 11 |
| `rlm/test_program_inputs.py` | `test_selected_artifact_input`, `chat/test_session_context` | 3 → 1 | 26 |
| `rlm/test_recursion_isolation.py` | `test_recursion_content_safety` | 2 → 1 | 11 |
| `rlm/test_recursion_tools.py` | `test_live_capsule_capture` | 2 → 1 | 48 |
| `rlm/test_events_execution_trace.py` | `test_events_observation` | 2 → 1 | 25 |
| `daytona/test_memory_candidate_wiring.py` | `test_memory_candidate_promotion_flow` | 2 → 1 | 4 |
| `daytona/test_runtime.py` | `test_daytona_platform` | 2 → 1 | 4 |
| `runtime/test_sandbox_lifecycle.py` | `test_sandbox_binding_repository` | 2 → 1 | 25 |
| `workspace/test_workspace_volume_gateway.py` | `daytona/test_workspace_gateway` | 2 → 1 | 6 |
| `turn/test_turn_coordinator_commit.py` | `test_turn_coordinator_concurrency`, `test_turn_coordinator_replay` | 3 → 1 | 6 |
| `chat/test_turn_coordinator_execution.py` | `turn/test_open_turn_command` | 2 → 1 | 14 |
| `api/test_ui_stream.py` | `test_sse_trace_id` | 2 → 1 | 10 |
| `observability/test_mlflow_tracing_config.py` | `test_validate_mlflow_tracing` | 2 → 1 | 38 |
| `optimization/test_gepa_production.py` | `test_mlflow_observability` | 2 → 1 | 6 |
| `scripts/test_release_tooling.py` | `test_circleci_trigger_release`, `test_normalize_release_artifacts` | 2 → 1 | 3 |

LiteLLM invariant: `test_no_direct_litellm_usage` was parametrized over every
source file, emitting ~145 node IDs for one policy check. Collapsed to a single
aggregating assertion with the same detection and a better failure message.
148 → 3 cases in that file.

Two duplications were found and removed by the merges:

- `_lm()` was defined identically in `test_recursion_isolation.py` and
  `test_recursion_content_safety.py`; the second copy was dropped.
- `RecursiveRLMExecutor` resolved to **two different classes** — the production
  executor in `fleet_rlm.rlm.recursion` and a test double in
  `tests/support/recursion_scheduler`. The merge would have silently shadowed
  the production class. The production import is now aliased to
  `ProductionRecursiveRLMExecutor`, matching the support module's own naming.

### Adversarial suite audit — hypothesis refuted

The plan hypothesized that three agent-authored "Challenger" suites overlapped
their domain-owner files. A body-level comparison (normalized AST bodies of
every `test_*` function, cross-checked against every owner file in the same
package) found:

- **zero** identical bodies across the three suites and their owners;
- one exact-name collision, whose bodies **differ**.

`test_sandbox_backend_rejects_non_positive_timeout` exists in both
`daytona/test_interpreter_output_cap.py` and the adversarial suite. The
adversarial version is parametrized and asserts the typed `DaytonaAdapterError`
with `cause_type == "InterpreterConfigurationError"`; the owner version asserts
a bare `pytest.raises(Exception, match="positive")`. **The owner test is the
weaker of the two.** No deletion was performed; removing the weaker assertion is
a test-strength decision, not a redundancy one, and is left to the maintainer.

Conclusion: the "Challenger" files are not redundant. Their problem was
labelling, not coverage, and it was resolved by renaming (below).

### Name hygiene

Renamed to describe behavior rather than a completed project phase:

| Before | After |
| --- | --- |
| `rlm/test_phase2_invariants.py` | `rlm/test_rlm_core_invariants.py` |
| `rlm/test_phase2_stress_adversarial.py` | `rlm/test_rlm_core_adversarial.py` |
| `rlm/test_portable_adapter_salvage.py` | `rlm/test_adapter_protocol.py` |
| `contracts/backend/test_phase4_fastapi_sse_compatibility.py` | `contracts/backend/test_ai_sdk_sse_compatibility.py` |
| `daytona/test_daytona_adversarial.py` | `daytona/test_interpreter_adversarial.py` |
| `daytona/test_stress_direct_daytona.py` | `daytona/test_direct_daytona_stress.py` |

Their module docstrings were rewritten to drop "Phase N" and "Authored by
Challenger N" framing while keeping the behavioral description.

48 `test_val_rec_NNN_*` functions were renamed to `test_*` and all 54
`VAL-REC-NNN` docstring codes were removed, preserving the prose. Verified
beforehand that no script, Makefile, CI config, or document references a
`val_rec` node ID.

## Deliberate non-actions

Each of these is a decision, not an oversight.

1. **Live canary filenames keep their phase numbers.**
   `tests/live/backend/test_phase1_daytona_stream.py` and
   `test_phase2_daytona_recursive.py` are named in an operator command in
   [Testing Strategy](../how-to-guides/testing-strategy.md).
   Renaming them would break a documented operator entry point, which the
   testing strategy explicitly protects.

2. **`QRE-*` codes inside `tests/live/` were left intact.** They appear in
   session titles and assertion messages that are written into live evidence
   receipts. The testing strategy requires that sharing fixtures "must not
   change the meaning of a previously recorded receipt".

3. **`test_live_composition.py` and `test_live_turn_preparation.py` were not
   renamed.** The plan called the `live_` prefix misleading. It is not: the
   first exercises the production composition, and the second imports
   `fleet_rlm.composition.live`. The prefix names the module, not the live lane.
   The plan's finding was wrong; the files moved to `composition/` and `turn/`
   with their names intact.

4. **Historical ledgers were not rewritten.**
   [Phase 3 consolidation ledger](phase3-consolidation-ledger.md),
   [Performance Budget](../reference/performance-budget.md),
   and
   [P41 behavior freeze](../reference/behavior-freeze.md)
   reference pre-move paths. They are dated evidence of what was true when
   written. Current-guidance documents
   ([Testing Strategy](../how-to-guides/testing-strategy.md),
   [Behavior freeze](../reference/behavior-freeze.md)) were updated.

5. **Three-case files were not merged.** The ≤ 240 file target needed them.
   Merging 30 three-case files is a substantially larger diff with the same
   class of risk as the merges above and materially less benefit, so it was not
   done under this scope.

## Growth control

`make check-codebase-tree` now also runs `check_test_layout`, which fails when:

- a `test_*.py` sits flat in `tests/unit/backend/`;
- a `test_*.py` outside `tests/live/`, `tests/contracts/`, `tests/freeze/`,
  `tests/e2e/`, and `tests/unit/backend/packaging/` holds fewer than three cases.

Both rules were mutation-tested (an injected flat file and an injected one-case
file each failed the check, and the check returned clean after removal). Five
unit tests cover the new function in
`tests/unit/scripts/test_check_codebase_tree.py`.

Without this check the suite regrows: the Phase 3 ledger recorded 3 027 cases in
299 files, and by 2026-09-20 the suite had grown to 3 151 cases in 283 files —
consolidation had run but growth outran it.

## Verification

| Check | Result |
| --- | --- |
| `make test` | pass, 0 failures |
| `pytest --collect-only` | 257 files, 3 011 cases, 0 collection errors |
| Coverage, local gate paths | 83.2 % (floor 75 %) |
| `uv run ruff check tests/` | pass |
| `uv run ruff format --check tests/` | pass |
| `make check-codebase-tree` | pass |
| Mutation: forbidden term injected into `src/` | `daytona/test_volume_paths.py` failed as intended |
| Mutation: flat / one-case test file injected | `check_test_layout` failed as intended |
| `make check-docs` | pass |
| `git diff --check` | pass |

## Evidence limits

- Counts come from `pytest --collect-only` on a clean tree at `46b4e199d`.
- Coverage figures are from local runs on one machine; CI may differ.
- No live, Daytona, provider, database, or benchmark lane was executed. Findings
  about `tests/live/` are structural only.
- The adversarial-suite audit compared normalized AST bodies for exact equality.
  It would not detect two tests that assert the same property with different
  code. No claim is made that the suites are free of *semantic* overlap.
- Moving files changes pytest node IDs. Three production certification scripts
  (`scripts/benchmarks/certify_mlflow.py`, `certify_daytona_sdk.py`) hardcoded
  pre-move test paths; they were updated. Any *external* tooling holding old
  node IDs for the moved files must be updated too.
