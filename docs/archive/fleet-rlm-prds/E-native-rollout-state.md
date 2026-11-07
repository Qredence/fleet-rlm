# PRD E — Native rollout state

**Status:** implemented; offline acceptance recorded below. **Priority:** medium. **Baseline:** [`61bea5095dbbc992db2aeb8049a05e079b0bda10`](https://github.com/Qredence/fleet-rlm/commit/61bea5095dbbc992db2aeb8049a05e079b0bda10), DSPy 3.4.1. [Index](README.md).

**Revision:** 2 - review amendments applied directly, 29 October 2026. This is a source-based document update, not a new repository audit or implementation. Evidence labels and baseline limitations are defined in [README](README.md#evidence-and-baseline).

## 1. Problem, users and evidence

Optimization maintainers should see that each rollout is isolated because it owns a fresh runtime and workspace. Temporary candidate files obscure that guarantee and add file-descriptor, path, reload and cleanup branches to every example execution.

**current-source (input PRD):** `build_isolated_engine_factory` validates an immutable replay, constructs a disposable manager, creates a temporary JSON path, saves the candidate, restores inputs and constructs an engine with `compiled_path=program_path`. `IsolatedFleetProgram.forward` later shuts down the manager and unlinks `OptimizationInputs.artifact_path`. Setup failures have a parallel file-cleanup branch. [Factory and wrapper](https://github.com/Qredence/fleet-rlm/blob/61bea5095dbbc992db2aeb8049a05e079b0bda10/src/fleet_rlm/optimize/validation.py#L32-L152).

`CompiledProgramFile` provides a useful durable-file loading boundary and legacy wrapper-key normalization. [Loader](https://github.com/Qredence/fleet-rlm/blob/61bea5095dbbc992db2aeb8049a05e079b0bda10/src/fleet_rlm/engine/programs.py#L114-L132). Candidate publication and approval still require real durable artifacts; they are separate from in-process rollout transport. Existing artifact/native-GEPA tests support both engine state and wrapper-prefixed saved state.

## 2. Native-framework-first decision and rationale

Subject to the native-state parity spike below, create a fresh `AdaptiveFleetEngine` with `compiled_path=None`, then apply an independent supported `candidate.dump_state()` value using native `load_state`. `deepcopy` is the proposed isolation operation, not a guarantee for arbitrary state objects. The inspected [native BaseModule implementation](https://github.com/stanfordnlp/dspy/blob/099982b3c3eec9fc4ec4796d49915af42367a253/dspy/primitives/base_module.py#L154-L171) exposes these methods and trial-loads a copy before applying state. Do not copy DSPy's serialization internals or use whole-engine deepcopy as the isolation boundary: it may copy/share managers, locks or live providers incorrectly.

Fleet still supplies fresh managers, reviewed replay restoration, model-role configuration, budgets and teardown because native state serialization does not provision Daytona or implement Fleet's input/evidence policy. Retain the serving compiled-file loader and durable publication save path. This slice removes transport plumbing, not persistence safeguards.

## 3. Scope and non-goals

Scope: state handoff inside isolated rollout creation, fresh mutable candidate state per rollout, temporary-file cleanup deletion, `OptimizationInputs.artifact_path` removal after caller audit, and state/behavior parity tests.

Non-goals: new serialization format, wholesale engine cloning, shared live interpreter reuse, dependency upgrade, removing `IsolatedFleetProgram`, changing native candidate publication bytes, changing the approval digest or deleting bounded serving reload checks. F separately strengthens provider cleanup; E must preserve and expose its existing cleanup outcome rather than treating file deletion as proof that compute ended.

## 4. Affected code tree

```text
src/fleet_rlm/
  optimize/validation.py      # factory and forward teardown
  contracts.py               # remove artifact_path after all uses audited
  engine/programs.py         # preserve native load_state and durable loader
  optimize/gepa.py           # call site/state assumptions, no transport tempfile
tests/optimize/{test_optimization,test_native_gepa,test_artifacts}.py
tests/engine/test_programs.py
tests/fixtures/pre_consolidation_program.json
```

## 5. Concrete requirements and tasks

1. **E1 — Run the parity spike before deletion.** Freeze native state and existing file-loader behavior for the real locked RLM/Flex/typed-decision program. Exercise actual dump/load and copying methods with external providers mocked, including a copy/load failure. This is a blocking gate: if a supported state element loses its type, behavior or isolation, stop the replacement and report the specific native gap; do not add a custom serializer or cloning layer. Compare `named_parameters`, `dump_state` and saved JSON for a bare engine, `IsolatedFleetProgram(engine, factory)`, and the actual object returned by GEPA. Identify the real engine passed to the factory. Native wrapper state may prefix keys with `program.`; normalize only at the existing durable-file boundary or unwrap the actual object explicitly. Do not strip prefixes from arbitrary nested native state.
2. **E2 — Build fresh then load native state.** Keep the current replay digest/transport checks before work. Instantiate the manager/engine with the same settings, LMs, modes and budgets, with no compiled path. Apply an independent state copy to the fresh engine; leave candidate/template state unchanged. Preserve Flex module source rebinding and typed decision components.
3. **E3 — Preserve runtime configuration.** Explicitly test which LM/configuration properties native state stores versus constructors supply. Keep root/sub/Jev role settings and native unsafe-LM-state defaults. Do not enable unsafe loading to make a test pass or let serialized state override a protected provider configuration without the existing policy.
4. **E4 — Delete incidental transport.** Remove `mkstemp`, descriptor close, candidate-save call, path cleanup branches and `artifact_path` after a repository-wide use search. Remove only now-unused imports. Keep real published candidate files and `compiled_path` on serving engines.
5. **E5 — Prove isolation and error ownership.** Two concurrent rollouts need separate state copies, managers, namespace/context files and mutable demos/source. A state-load, workspace-restore or execution failure must invoke the same manager teardown path. No failure can mutate the incumbent/candidate or silently reuse a partially loaded engine.

Proposed core handoff, inserted into the existing factory and exception boundary:

```python
from copy import deepcopy

engine = AdaptiveFleetEngine(
    sandbox_mgr=manager,
    root_lm=root_lm,
    sub_lm=sub_lm,
    jev_lm=jev_lm,
    compiled_path=None,
    execution_mode=settings.execution_mode,
)
# The current factory receives the bare engine via IsolatedFleetProgram.forward.
if not isinstance(program, AdaptiveFleetEngine):
    raise TypeError("Isolated rollout requires a Fleet engine candidate")
engine.load_state(deepcopy(program.dump_state()))
engine.optimization = OptimizationInputs(
    context=WorkspaceDataset(**replay_context) if replay_context else None,
    verifications=reviewed_verifications,
)
```

The snippet is a conditional implementation sketch, not proof that arbitrary state is safely deepcopyable. Trace the current object boundary explicitly: native GEPA returns the compiled `IsolatedFleetProgram`; `optimized.program` is its bare engine, and validation wraps that engine for `forward`, which passes the bare engine to the factory. The factory therefore requires a bare engine, not arbitrary wrapper state. Retain the type guard and a regression for this actual call chain. Supporting wrapped factory inputs is unnecessary unless a real caller requires it. Historical wrapper-prefixed JSON remains a separate durable-loader compatibility concern. Keep replay input restoration and `manager.shutdown()` on setup failure around this block. Native state methods, not JSON round-tripping, define the in-process transfer. If parity fails for a supported state element, stop the transport replacement and identify the native API gap before inventing another serializer.

## 6. Before / after flow

Before: candidate → native save to temporary JSON → bounded serving loader → fresh engine → execute replay → manager teardown + unlink candidate path.

After: candidate → native dump → per-rollout deep copy → native load into freshly constructed engine → execute replay → manager teardown. Durable candidates still use native save → manifest/digest → explicit approval → serving loader. No in-process transport optimization relaxes that installation flow.

## 7. Acceptance tests and measurable outcomes

| ID | Required scenario | Passing condition |
|---|---|---|
| E-T1 | Old tempfile path versus new state path with fixed offline fixtures | Same predictor instructions/demos, typed decisions, Flex source, native parameter keys and fake-execution result |
| E-T2 | Native engine and wrapper-prefixed historical artifacts | Existing serving loader accepts supported formats; approval digest expectations remain unchanged |
| E-T3 | Concurrent rollouts mutate instructions/demos/source/context | No mutation leaks to sibling, candidate or incumbent; distinct managers/namespaces |
| E-T4 | State-load/replay-write/execution failure | Cleanup attempted once by the correct owner; partially loaded engine never returned; cleanup uncertainty remains visible |
| E-T5 | Model roles, explicit Flex budgets and modes | Constructor policy preserved for hybrid/flex/rlm execution modes and direct/flex/rlm routes; no unsafe LM state enabling |
| E-T6 | Instrument file operations during rollout | Zero candidate transport save/tempfile/unlink calls; real publication save still exercised separately |
| E-T7 | Supported state copy/load failure and wrapper boundary | Replacement remains gated on parity; correct cleanup runs; no custom serialization fallback or prefix-stripping heuristic is introduced |

Run `uv run pytest tests/optimize/test_optimization.py tests/optimize/test_native_gepa.py tests/optimize/test_artifacts.py tests/engine/test_programs.py` and repository gates. Use real native dump/load methods with mocked providers rather than testing only a dictionary copy. Metrics: per-rollout candidate transport files 1→0, temporary candidate descriptors 1→0, transport cleanup branches removed, parity fixtures all passing. Do not assert a latency gain without measurement; replay restoration/provider work may dominate.

## 8. Migration and rollback

No SQL migration or published-artifact format change is intended. Existing approved candidate JSON and historical wrapper formats remain readable. Keep a pre-change native state fixture and use it in parity checks. If a published format must change for an unrelated reason, separate that work with an evidence/version migration rather than hiding it here.

Roll back factory transport and the `OptimizationInputs` field removal together if parity fails. No user artifact conversion should be required. Do not retain two indefinitely selectable rollout transports: use a short-lived test/reference implementation for comparison, then remove it after acceptance. Changed evaluation implementation affects code identity; update fingerprint coverage and collect fresh evidence before approving candidates under the changed runtime.

## 9. Risks, dependencies and gates

Native state may include values with behavior beyond JSON equality; compare actual native components and offline behavior. Flex source membership/rebinding and optional predictors require explicit fixtures. Shared LMs are already factory-level policy and should not be confused with sharing mutable program/manager state. A supplies related native compatibility tests; D owns progress; F owns confirmed cleanup. G may use native state comparison later, but E must not introduce an early no-change branch.

## 10. Codex-ready checklist

- [x] Audit native engine/wrapper state topology and supported historical artifacts.
- [x] Complete the E1 parity spike and E-T1–E-T5/E-T7 using actual locked native methods before deleting transport; stop on unsupported state.
- [x] Apply copied state into fresh engine with no compiled path; preserve reviewed-input checks.
- [x] Remove temporary candidate lifecycle and artifact_path consumers; retain publication/serving loader.
- [x] Pass E-T1–E-T7 and repository gates, report isolation and identity effects (exporter setting below).
- [x] Keep rollback bounded to transport; make no unmeasured latency claim.

Use the [common acceptance report](README.md#acceptance-report). Retain the existing factory as the owner of the small state handoff; no general clone API, selectable transport registry, or parallel native-state format is authorized.

## Implementation acceptance — 30 October 2026

| Item | Evidence |
|---|---|
| Coordinates | Start SHA `b429cf1cf3303fd0d16f6d2adce04b393202c650`; final implementation is the PR E diff on `codex/native-rollout-cleanup-publication`. Installed locked versions: DSPy 3.4.1, GEPA 0.1.4, Daytona 0.224.0. Existing untracked `.codex/` work is untouched. |
| Native reuse | Eight parity tests passed before transport deletion, using actual engine dump/load and bare/wrapped durable saves. Native Flex rebinding, predictor demos, typed field annotations/criteria and safe LM loading match the durable loader across hybrid/flex/rlm modes. Native load reconstructs signature classes; field annotations and decision state, rather than class identity, define parity. |
| Simplification | Removed candidate `mkstemp`, descriptor close, save, two unlink cleanup paths, three imports and `OptimizationInputs.artifact_path`. The existing factory owns copied native state loading; setup and execution retain their respective manager shutdown owners. No new runtime module or clone abstraction. |
| Contracts/data | No SQL, HTTP or published JSON format changes. Removed an internal optimization-only field after caller audit. Historical wrapper-prefixed and pre-consolidation fixtures still load. Publication bytes and digest approval remain on the existing path. |
| Correctness | `tests/optimize/test_rollout_state.py`: 19 offline tests cover E-T1–E-T7, including actual native GEPA wrapper return/unwrap, native typed Choice parsing for every route, direct/Flex execution and an RLM execution double, concurrent demos/instructions/source/workspace mutation, copy/load/restore/execution failures and visible cleanup uncertainty. The PRD focused command plus this file passed: 182 tests; the expanded route assertions also passed in the 19-test state suite. |
| Identity | Existing code fingerprint coverage includes `optimize/validation.py`, `contracts.py` and `engine/programs.py`; the implementation changes that identity. Fresh evaluation evidence is required before approval. |
| Repository gates | `MLFLOW_ENABLE_ASYNC_TRACE_LOGGING=false make check` passed: 1,813 tests passed, one skipped, 84% coverage, lint, formatting, types, docs, OpenAPI and build. The default invocation stalled at `test_failed_download_span_omits_private_exception_details` after 1,504 passing tests and was interrupted; the remaining tests passed separately (309 passed, one skipped). The exporter flag applies only to the validation invocation; no project setting changed. |
| Measurement | Instrumented rollout tests observe zero candidate save/tempfile/unlink calls: transport files and descriptors each fall from one to zero per rollout. Provider latency, cost and performance gains were not measured. |
| Rollback | Revert factory transport and internal field removal together. Existing published artifacts require no conversion; publication and serving-loader safeguards are retained. |
| Limits | Offline tests do not establish live provider cleanup confirmation or workload gains. An initial fake-interpreter test omitted provider mocking and attempted a snapshot lookup with dummy credentials; authentication failed. The final suite explicitly rejects sandbox provisioning and mocks interpreter/provider work. |
