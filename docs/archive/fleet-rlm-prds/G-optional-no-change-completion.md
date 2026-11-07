# PRD G — Optional no-change completion

**Status:** optional feature, proposed after core correctness slices. **Baseline:** [`61bea5095dbbc992db2aeb8049a05e079b0bda10`](https://github.com/Qredence/fleet-rlm/commit/61bea5095dbbc992db2aeb8049a05e079b0bda10). [Index](README.md).

**Revision:** 2 - review amendments applied directly, 29 October 2026. This is a source-based document update, not a new repository audit or implementation. Evidence labels and baseline limitations are defined in [README](README.md#evidence-and-baseline).

## 1. Problem, users and evidence

When native GEPA returns a program that is provably unchanged, users should see a successful search with no improvement candidate. They should not pay for redundant paired validation or be offered approval that can only fail because no component changed.

**current-source (input PRD):** `_compile` always enters three paired validation passes, computes differences afterward, writes candidate/manifest/reports, and prints a saved candidate. The finalizer assigns `candidate_version=run_id` for every completed search. [Validation/publication](https://github.com/Qredence/fleet-rlm/blob/61bea5095dbbc992db2aeb8049a05e079b0bda10/src/fleet_rlm/optimize/gepa.py#L840-L960), [finalizer](https://github.com/Qredence/fleet-rlm/blob/61bea5095dbbc992db2aeb8049a05e079b0bda10/src/fleet_rlm/optimize/gepa.py#L575-L610).

`promotion_gate` rejects an empty changed-component set; API review initially admits completed runs, so no-change is not a complete lifecycle outcome. [Gate/approval](https://github.com/Qredence/fleet-rlm/blob/61bea5095dbbc992db2aeb8049a05e079b0bda10/src/fleet_rlm/optimize/artifacts.py#L279-L312), [review admission](https://github.com/Qredence/fleet-rlm/blob/61bea5095dbbc992db2aeb8049a05e079b0bda10/src/fleet_rlm/experiments.py#L237-L239).

`component_diffs` compares predictor instructions and Flex source, but iterates only candidate component names. A removed component is not reported. It is also not a full native state comparison: demos/model state and other native state may matter. [Component comparison](https://github.com/Qredence/fleet-rlm/blob/61bea5095dbbc992db2aeb8049a05e079b0bda10/src/fleet_rlm/optimize/artifacts.py#L175-L207). Therefore an empty current diff is insufficient proof for skipping validation.

The earlier branch plan explicitly deferred this feature because it spans finalization, history, API, approval and UI. That separation remains: A–F must be independently mergeable without G.

## 2. Native-framework-first decision and rationale

Keep native GEPA search, native traversal/state methods and native `dspy.Evaluate` for changed or unproven candidates. Fleet supplies only its post-search decision and existing run lifecycle. Native GEPA does not know Fleet's artifact/approval/history contract.

This feature is **optional and measurement-gated**. Before implementation, inspect existing experiment evidence or a bounded offline instrumentation result to establish how often candidates are genuinely unchanged and the paired-validation work that would be avoided. Record a go/no-go decision against the broad consumer changes required. No arbitrary savings percentage is imposed, and A-F are not blocked when G is deferred.

If approved, use frozen pre-search native state, existing program/configuration identity checks and one small fail-closed proof at the existing artifact owner. The predicate returns true only when equality is established for the actually supported state types. Unequal or uncomparable/unknown state follows normal validation. No tri-state result framework, extensible comparator registry, generic serialization scheme or new report/status hierarchy is needed.

## 3. Scope and non-goals

Scope, only after the G0 go decision: exact equality decision immediately after native search, completed/no-change lifecycle, diagnostic report without candidate, finalizer/history/API/client compatibility, disabled approval and honest saved-work metrics.

Non-goals: generalized equality infrastructure, a separate terminal status enum, new approval workers, skip native search, weaken admission/train/dev/holdout requirements, normalize prompts/source semantically, treat whitespace changes as equal, suppress demos or model differences, bypass three paired validation passes for changed candidates, auto-install, or turn failure/cancellation into no-change. Do not add a whole optimizer state machine package.

## 4. Affected code tree

```text
src/fleet_rlm/
  optimize/artifacts.py       # membership-aware component diff + conservative identity
  optimize/gepa.py            # early branch, report, finalizer candidate assignment
  optimization_runs.py       # outcome vocabulary/history/read compatibility
  store.py                   # existing JSON metadata/CAS; migration only if schema needs it
  experiments.py             # synchronous review refusal; managed outcome handoff
  api/schemas.py             # new outcome with C
  api/routes/experiments.py   # documented response/error semantics
  tui/experiments.py          # no-change rendering and no approval action
  cli.py                     # outcome messaging/exit behavior if currently centralized
tests/optimize/{test_optimization,test_artifacts,test_native_gepa}.py
tests/api/test_experiments.py
tests/store/test_optimization_runs.py
tests/tui/test_tui.py
```

## 5. Requirements and concrete tasks

0. **G0 - Qualify the benefit.** Use retained run evidence where available; otherwise collect bounded offline instrumentation without changing success or approval behavior. Count proven unchanged outcomes, actual dev/holdout sizes and observed validation work. Separate predicted avoided calls from measured cost/latency. Record the decision to implement or defer, the state types covered and the cross-consumer maintenance cost. Missing evidence is a reason to leave this optional feature disabled, not to fabricate a savings estimate.
1. **G1 - Compare membership correctly.** Freeze the incumbent component description before compile. Traverse the union of before/after component names; distinguish added, removed and modified, including empty-string versus absent membership. Preserve native names and exact instructions/Flex source. Use one result for changed names and display diffs. Do not compare against a template that search may already have mutated.
2. **G2 - Prove equality narrowly.** Freeze a deep-copied native incumbent state at the same bare-engine boundary before search; E's parity/copy tests apply. Compare the complete supported native values, preserving types, demos, typed fields and Flex source. Model-role/runtime/mode attributes outside native state must match existing pre-search identity evidence. A simple supported-value comparison or existing stable fingerprint is sufficient when it faithfully represents those values. Unsupported/custom objects, lossy coercions, unstable `repr`, incomplete identity coverage or unprovable equality must take normal validation. No general comparator registry, semantic normalization or new state format. The default excludes no behavioral state.
3. **G3 - Branch before paired validation.** After compile, check cancellation, owner state, D's required-write failure and current cleanup outcome. Compute the membership/state proof and recheck incumbent/config/code/dependency identities. Identity drift is the existing failure/invalidation path, not no-change. Keep dataset curation and critical-holdout admission before search; no-change skips final paired evaluation, not all preparation or native search.
4. **G4 - Add one outcome, not a new status family.** Keep `status='completed'`, add `outcome='no_change'`, and record `validation_skipped_reason='unchanged_program'`. Candidate identity/path/manifest fields remain absent or null under the documented current schema; candidate_version must not be inferred from completed status. Candidate-bearing new completions use `outcome='candidate'` after old-reader compatibility is established. Reuse existing eligibility/reason fields for non-promotability; do not introduce a parallel approval policy model. Validation scores are absent/unknown, never invented zeros. If the existing record cannot express this additive change, stop and scope the smallest compatibility change explicitly rather than defaulting to a new status enum.
5. **G5 - Use the existing run-report path.** Write bounded diagnostics under the existing per-run directory: native search observations, identities, comparison result, skipped-validation reason/count. Do not create candidate JSON/manifest, a report hierarchy, or fabricated paired scores. The current report owner may have one no-change rendering branch; report access must not depend on candidate_path.
6. **G6 - Integrate every existing consumer.** Pass the additive outcome through worker finalization and API exit arbitration. Review/approval rejects no-change synchronously without launching an approval worker. CLI/TUI/history state 'Completed - no program changes' and omit approval controls. Legacy missing-outcome rows retain candidate semantics only when their actual required candidate identity exists; missing files alone do not imply historical no-change.
7. **G7 - Preserve races and failure precedence.** Cancellation, required persistence failure, identity drift and unresolved cleanup cannot become successful no-change. Changed/unproven candidates retain all three paired passes and current promotion checks. Coordinate comparison-policy/evidence identity updates with existing owners.

Proposed control flow; all helper names describe existing/local responsibilities rather than new abstractions:

```python
before_state = deepcopy(template.dump_state(json_mode=True))
before_components = capture_existing_component_descriptions(template)
optimized = native_gepa.compile(baseline, trainset=trainset, valset=devset)
check_existing_cancel_owner_required_write_and_cleanup_state()
changes = compare_components(before_components, optimized.program)
recheck_existing_search_identities()

# One fail-closed predicate: unsupported/unknown is False, not a new state type.
unchanged_proven = not changes and native_values_proven_equal(
    before_state, optimized.program.dump_state(json_mode=True)
)
if unchanged_proven:
    # Use existing worker-outcome/finalizer fields, with no candidate identity.
    return complete_existing_run(
        outcome="no_change",
        validation_skipped_reason="unchanged_program",
        candidate_version=None,
    )

# Existing changed/unproven path, including all paired evaluation and gates.
evaluation = run_existing_three_paired_passes(...)
return publish_candidate_with_existing_gate(evaluation, changes)
```

Do not implement a new `SearchOutcome` class merely because a sketch needs a return value. The finalizer receives an explicit existing-contract outcome and never derives candidate existence from completed status. 'Unknown' describes the inability to prove equality; it does not require a persisted comparison enum or another backend state machine.

## 6. Before / after flow

Before: native search → always baseline/candidate dev+holdout paired passes → diff → gate rejects unchanged → candidate and manifest still published → completed/review offered → approval rejects.

After, unchanged: native search → cancellation/identity/equality checks → diagnostic no-change report → completed/no-change → no validation, no candidate, no review/approval. After, changed or equality unknown: native search → existing three paired passes → gate/evidence → candidate/report → explicit review and approval. Holdout remains outside search.

## 7. Acceptance tests and measurable outcomes

| ID | Required case | Passing condition |
|---|---|---|
| G-T0 | Benefit assessment and explicit go/defer decision | Evidence identifies unchanged frequency, avoided validation work and implementation cost; no gain claim from unsupported comparisons |
| G-T1 | Identical native candidate, full managed and standalone run | Completed/no_change; 0 validation rollouts; no candidate JSON/manifest/version; report available; active program unchanged |
| G-T2 | Add/remove component, including empty source/instructions | Change detected; normal validation path |
| G-T3 | Instructions same but demos/typed/native state differs; separately mutate plain model-role/mode attributes or shared incumbent state | No false early exit: native differences take validation path; configuration/identity drift fails the run under G3, and incomplete identity proof falls back to validation |
| G-T4 | Unsupported state or uncertain equality | Conservative normal validation, no crash or false no-change |
| G-T5 | Changed candidate passes/fails existing gate | Three paired passes preserved; ordinary candidate/report and explicit approval behavior unchanged |
| G-T6 | Cancel, persistence failure or incumbent identity changes near branch/finalization | Existing cancellation/failure/CAS wins; no successful no-change/candidate |
| G-T7 | API/history/TUI/CLI and review attempt | Accurate no-change message, no approve action, synchronous review rejection; old records readable |
| G-T8 | Worker exits nonzero after reporting a no-change outcome | Managed supervisor does not expose successful completion before exit/required cleanup confirmation |

Use real native state with fake external boundaries. Run optimize/native/artifact tests, API experiments, Store optimization-run tests and relevant TUI tests, followed by `make api-sync`, `make api-check` and required repository gates. Include one end-to-end offline worker/finalizer fixture, not only a unit test of the boolean comparator.

For each proven unchanged run, expected avoided validation rollouts are `2 × VALIDATION_PASSES × (N_dev + N_holdout)`, currently `6 × (N_dev + N_holdout)`. Assert observed validation calls equal zero and report the formula against actual split sizes. Native search still consumes budget; GEPA's metric-call limit does not cap all system costs. Do not report dollar/latency savings without observed usage. Success metric: zero false no-change exits in all mutation fixtures and zero promotable artifacts/actions for no-change outcomes.

## 8. Migration and rollback

Prefer additive metadata in the existing durable run representation; inspect Store columns/JSON constraints before concluding no SQL migration is needed. New readers accept absent outcome for historical candidate records, and new writers always specify it. Deploy compatible readers/API/UI before enabling the early exit. Reports must not be coupled to candidate paths, and historical failed-gate unchanged candidates remain intact and unapprovable under existing gates.

Rollback may disable the early branch and restore ordinary validation for new runs, but must retain readers and review refusal for previously written no-change records. Never synthesize missing candidate artifacts/version values to satisfy an old finalizer. Keep additive outcome/report fields and existing active program unchanged. Do not introduce a dedicated SQL status for this selected design. Any unavoidable schema compatibility work must be separately justified; additive no-change readers and review refusal remain even if the optimization shortcut is disabled.

## 9. Risks, dependencies and implementation gates

False equality is the main risk. Instruction/source-only comparisons are inadequate; wrapper-prefix mismatches can also create false differences. Exact native state equality does not prove nondeterministic executions would yield identical answers; the feature relies on identical program/config identities and explicitly declines to collect a new validation estimate for an unchanged program. That is why its report has no fresh holdout score or promotion claim.

D establishes durable worker ownership and failure handling; E supplies native state parity tests and a clear engine boundary. C must add outcome/error schemas and F's cleanup guarantees must remain true for search resources. F's durable redesign need not be a hidden prerequisite for merging A–E, but no-change completion cannot mask unresolved cleanup. Resolve exact state-comparison support and legacy report access in implementation before enabling G.

## 10. Codex-ready checklist

- [ ] Record the G0 evidence and go/defer decision; keep core A–F independently complete.
- [ ] Freeze pre-search native/component snapshots and test membership/state mutations before early-exit code; use one small fail-closed proof, not an equality framework.
- [ ] Branch after native search and identity/cancel/failure checks, before validation.
- [ ] Carry explicit outcome through worker/finalizer/API; never derive candidate existence from completed status.
- [ ] Produce diagnostic report without candidate; refuse approval and update CLI/TUI/schema/history.
- [ ] Pass G-T0–G-T8 plus repository/API gates; measure avoided calls using actual split sizes.
- [ ] Preserve old records and new no-change readers during rollback; no fabricated validation metrics.

Use the [common acceptance report](README.md#acceptance-report). Explain why measured avoided validation justifies every changed consumer; a completed no-change run supplies no new holdout score or evidence of quality improvement.
