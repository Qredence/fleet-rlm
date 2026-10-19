# FleetTask GEPA Instruction Optimization

**Status:** Planned, not implemented. Decisions Q1–Q4 await user confirmation; budget and session id are open (see Decisions).

**Baseline:** Confirm `HEAD` before editing. Last recorded at session start: `99455fb42`.

**Working tree:** Preserve any staged, unstaged, or untracked edits. Check `git status` before the first change.

## Objective

Improve the `FleetTask` instructions that drive the RLM route, using the existing `fleet optimize` pipeline (native `dspy.GEPA` with the shared `fleet_metric`). Rewrites are limited to the RLM `generate_action` predictor. Candidate creation stays separate from installation: a candidate is installed only after a human runs `--approve`.

Out of scope: `TurnTriage`, `SkillRelevance`, `ChildTask`, `AnswerJudge`, `TurnJudge`, `fleet_metric`, and the Flex route's module source.

## Facts from read-only inspection

Line references are from a 2026-10-18 read-only pass. Re-verify before editing.

- `FleetTask` is used by `dspy.Flex` and `dspy.RLM` (`src/fleet_rlm/engine/programs.py:91`, `:96`).
- `STOPPING_RULES` is defined at `src/fleet_rlm/engine/signatures.py:35` and embedded in `FleetTask` (`:107`). It is also copied into sandbox execution instructions (`src/fleet_rlm/sandbox/manager.py:794`). That copy is not optimized.
- Tunable components (`src/fleet_rlm/optimize/gepa.py:227`):
  - RLM: `generate_action` (the FleetTask docstring plus `STOPPING_RULES`) and `extract` (a second copy of the objective text).
  - Flex: a single `self` component, the generated module source. No instruction text is exposed.
- dspy `GEPA` has no predictor filter. `component_selector` defaults to `"round_robin"` and only schedules which component is proposed each step (`dspy/teleprompt/gepa/gepa.py:415`). Built-in selectors do not remove predictors from the candidate.
- `fleet optimize` builds `dspy.GEPA` at `src/fleet_rlm/optimize/gepa.py:~435` with no component selector.
- `promotion_gate` (`src/fleet_rlm/optimize/artifacts.py:67–117`) accepts any non-empty `changed_components`. A Flex source change can therefore pass the gate.
- `--approve` (`artifacts.py:138–159`) recomputes the gate, rechecks dependency identity, the incumbent digest, and the candidate digest.
- Prior run `gepa-s1`: holdout 0.984 → 0.978, `changed_components` empty, GEPA returned the seed. The manifest is dated 2026-10-16 and lacks `policy_version`, so the current gate treats it as obsolete. Nothing was installed.
- Synthetic dataset: `fleet eval dataset` produces 30 cases (`src/fleet_rlm/optimize/evaluation.py:98`, `src/fleet_rlm/cli.py:160`). `curate_dataset` (`src/fleet_rlm/optimize/dataset.py:198–232`) splits by hash order, not by seed failures. The prior split was 10 train / 4 dev / 7 holdout.

## Decisions

| ID | Question | Recommended | Status |
| --- | --- | --- | --- |
| Q1 | Restrict rewrites to `generate_action` via a custom selector? | Yes. Requires a small new code path and tests; the approved plan's "no source edits" assumption changes. | Pending |
| Q2 | Hold all live Daytona and model spend until budget and session id are confirmed? | Yes | Pending |
| Q3 | Use the synthetic eval set only, excluding Oolong and LongCoT? | Yes | Pending |
| Q4 | Stop at the report, with the user running `--approve`? | Yes | Pending |
| Q5 | Budget: `--gepa-budget light` or an explicit `--max-metric-calls`? | Open | Open |
| Q6 | Session id for the run? | Open | Open |
| Q7 | Accept the 10 / 4 / 7 split, or add reviewed replays with approved gold labels? | Open | Open |

## Open discrepancy to resolve first

`docs/plans/fleet-rlm-audit-and-platform-alignment.md` states that current GEPA uses DSPy's built-in code proposer. `gepa.py` passes an `InstructionProposer` and no code proposer. Also, the audit's P2.6 says a frozen Flex proposer fails closed; the code has no such check. Resolve which behavior is current before relying on either claim.

## Phase 0 — Preflight (no code changes)

**Dependency:** None.

- [ ] **P0.1 — Settle decisions.** Get answers to Q1–Q7. Q1 changes the scope of Phase 1.
- [ ] **P0.2 — Resolve the proposer discrepancy.** Read `gepa.py` around the `dspy.GEPA` call and confirm which proposer runs. Update the audit doc's GEPA contract if it is wrong.
- [ ] **P0.3 — Baseline.** Record `HEAD`, `git status`, and the focused optimize test result.

**Exit criteria:** Decisions are recorded, the proposer question is answered, and the baseline is reproducible.

## Phase 1 — Scope the rewrites to `generate_action`

**Dependency:** Phase 0 with Q1 = yes. If Q1 = no, skip to Phase 2 with the default selector and accept Flex and `extract` changes.

- [ ] **P1.1 — Add a component selector.** Implement a selector that returns only the `generate_action` predictor. Pass it as `component_selector=` in `dspy.GEPA` construction in `src/fleet_rlm/optimize/gepa.py`. Verify the callable matches dspy's `ReflectionComponentSelector` protocol (`dspy/teleprompt/gepa/`, `gepa/strategies/component_selector.py`).
- [ ] **P1.2 — Keep Flex out.** Confirm the selector never returns the Flex `self` component.
- [ ] **P1.3 — Record the scope.** Write the selected component names into the manifest so approval can check scope.
- [ ] **P1.4 — Tests.** Add offline tests in `tests/optimize/`:
  - The selector returns only `generate_action`.
  - The name matches `named_predictors()` for the RLM program.
  - `changed_components` is a subset of `{generate_action}` on a stubbed run.

**Exit criteria:** New tests pass. No live call is made. The manifest records the scope.

## Phase 2 — Data and budget

**Dependency:** Q3, Q5, Q6, Q7.

- [ ] **P2.1 — Build the dataset.** Run `fleet eval dataset --seed 7` (synthetic only). Confirm 30 cases.
- [ ] **P2.2 — Check for seed failures.** Before any GEPA run, score the seed on the trainset. If the seed passes every trainset example, stop: GEPA returns the seed unchanged.
- [ ] **P2.3 — Set the budget.** Use `--gepa-budget light` or an explicit `--max-metric-calls` (Q5). Record the value. Budget bounds metric calls only; it does not cap spend.

**Exit criteria:** The seed has failures in the trainset, and the budget and session id are written down.

## Phase 3 — Offline verification

**Dependency:** Phase 1 and Phase 2 (if any code changed).

- [ ] **P3.1 — Focused tests.** `uv run pytest tests/optimize` with the new selector tests.
- [ ] **P3.2 — Full gate.** `make check` if code changed.
- [ ] **P3.3 — Docs.** After any doc update, run `make check-docs` and `git diff --check`.

**Exit criteria:** Tests and checks pass with no live calls.

## Phase 4 — Live canary (requires explicit go-ahead)

**Dependency:** Phase 3 passed, Q2 released, a spend cap agreed, and Phase 2 complete.

- [ ] **P4.1 — Preconditions.** Agree a paid-validation cap. Confirm the Daytona and model settings. If any step touches Databricks endpoints, load `databricks-core` and the matching product skill first.
- [ ] **P4.2 — Baseline.** Run the native GEPA canary (`scripts/verify_gepa_live.py`) or `fleet eval run` for the seed.
- [ ] **P4.3 — Optimize.** `fleet optimize --session <id>` with the agreed budget.
- [ ] **P4.4 — Read the manifest.** Check:
  - `changed_components` is a subset of `{generate_action}`.
  - Gate reasons are empty, or they are recorded.
  - Usage evidence is present on every split.
- [ ] **P4.5 — Do not reuse the prior config.** The `gepa-s1` manifest is legacy. Start from a fresh run.

**Exit criteria:** A manifest with `policy_version` 3, scope and budget recorded, and a gate outcome. Nothing installed.

## Phase 5 — Approval (user-run)

**Dependency:** Phase 4 with a gate-passing candidate. Q4.

- [ ] **P5.1 — Stop at the report.** The user runs `fleet optimize ... --approve`.
- [ ] **P5.2 — Identity recheck.** Approval rechecks the incumbent, configuration, code, and dependency identities. Any code change after candidate creation invalidates the candidate, so rerun.

**Exit criteria:** Either the user approves, or the candidate is recorded as rejected with its reasons.

## Phase 6 — Documentation

**Dependency:** Phase 5 outcome.

- [ ] **P6.1 — Behavior docs.** If the component scope changes public behavior, update the GEPA contract in `docs/plans/fleet-rlm-audit-and-platform-alignment.md` and the relevant reference doc.
- [ ] **P6.2 — Changelog.** Add an entry if public behavior changed.
- [ ] **P6.3 — Results.** Record measured results separately from intended behavior, with the seed, budget, split sizes, and gate reasons.

## Risks

- **Saturation.** If the seed passes the trainset, GEPA returns the seed and the run is wasted. Covered by P2.2.
- **Small holdout.** Seven holdout examples mean a single example can move the mean. Report per-example results.
- **Legacy manifests.** The `gepa-s1` manifest is rejected by the current gate. Do not reuse it.
- **Budget is not spend.** Reflection, judging, sandbox, and validation cost are outside the metric-call budget.
- **Scope mismatch.** Without Q1 = yes, Flex source and `extract` can change and still pass the gate.
- **Doc and code drift.** The audit doc's proposer claims do not match `gepa.py`. Resolve in P0.2.
