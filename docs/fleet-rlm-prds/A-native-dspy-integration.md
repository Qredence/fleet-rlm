# PRD A — Native DSPy integration

**Status:** implemented with fixture-backed native-first gateway compatibility. **Priority:** high. **Baseline:** [`61bea5095dbbc992db2aeb8049a05e079b0bda10`](https://github.com/Qredence/fleet-rlm/commit/61bea5095dbbc992db2aeb8049a05e079b0bda10), DSPy 3.4.1. Read [the index](README.md) for source provenance and common guardrails.

**Revision:** 2 - review amendments applied directly, 29 October 2026. This is a source-based document update, not a new repository audit or implementation. Evidence labels and baseline limitations are defined in [README](README.md#evidence-and-baseline).

Implementation evidence and remaining parser-removal conditions: [acceptance report](../runbooks/native-dspy-integration.md). The requirements below retain their original baseline context.

## 1. Problem, users and evidence

Fleet users need valid model answers and code to survive parsing unchanged. Maintainers need DSPy to own formatting/recovery and batching, with only bounded application glue around it.

**current-source (input PRD):** `FleetRLMChatAdapter` disables native JSON fallback, strips content through `</think>`, recognizes whole-response code fences and implements a corrective retry before returning to DSPy. These policies can run before the native parser has accepted a valid response. [Adapter implementation](https://github.com/Qredence/fleet-rlm/blob/61bea5095dbbc992db2aeb8049a05e079b0bda10/src/fleet_rlm/adapters.py#L18-L104). It is selected around Flex/RLM and explicit children; direct execution has a separate context. [Engine use](https://github.com/Qredence/fleet-rlm/blob/61bea5095dbbc992db2aeb8049a05e079b0bda10/src/fleet_rlm/engine/programs.py#L378-L399), [child use](https://github.com/Qredence/fleet-rlm/blob/61bea5095dbbc992db2aeb8049a05e079b0bda10/src/fleet_rlm/engine/children.py#L84-L94).

**previously-reproduced (not rerun):** 36 offline parsing fixtures included eight valid native responses: native ChatAdapter preserved 8/8, Fleet preserved 5/8. Literal `print("</think>")` and native markers inside fenced content exposed deterministic failures. These historical counts were not rerun for this PRD; implement fresh regression fixtures rather than treating the counts as a current test report.

**current-source (input PRD):** the outer-Flex fallback `_llm_query_batched` uses `ThreadPoolExecutor.map` without copying application ContextVars. It already precharges the aggregate budget and delegates when native `llm_query_batched` is injected. `_run_jev_batch` and skill relevance use native `.batch()` without an explicit straggler timeout. [Interpreter paths](https://github.com/Qredence/fleet-rlm/blob/61bea5095dbbc992db2aeb8049a05e079b0bda10/src/fleet_rlm/sandbox/interpreter.py#L227-L307), [skill batch](https://github.com/Qredence/fleet-rlm/blob/61bea5095dbbc992db2aeb8049a05e079b0bda10/src/fleet_rlm/engine/programs.py#L274-L300).

## 2. Native-framework-first decision and rationale

Use **native `dspy.ChatAdapter()` as the intended default**, with its native recovery behavior. Native JSONAdapter fallback can legitimately issue another LM request; deleting Fleet's corrective retry does not imply one physical request per prediction. Keep logical budget units and observed LM requests distinct, and test their relationship through Fleet's actual metered wrappers. Do not equate native migration with merely enabling native function calling on Fleet's subclass: that leaves the custom parser/retry and disabled fallback in place. The [versioned ChatAdapter reference](https://dspy.ai/3.4.1/api/adapters/ChatAdapter/) and [pinned parallelizer](https://github.com/stanfordnlp/dspy/blob/099982b3c3eec9fc4ec4796d49915af42367a253/dspy/utils/parallelizer.py#L207-L232) remain the implementation references supplied by the original PRD. Confirm their behavior at the implementation checkout before editing. Where the pinned batch timeout causes speculative resubmission, use native `.batch(..., timeout=0)` at the two prepaid decision batches; retain Fleet's independent deadline.

Retain a bounded thread executor for **only the existing semantic-query fallback**, submitting each item with a fresh `copy_context()`. Native Parallel's settings propagation alone does not establish preservation of all Fleet ContextVars. This is a concrete application requirement, not justification for a new executor framework. Keep native injected batching as the first branch.

If sanitized, recorded gateway fixtures demonstrate a required malformed-response format, retain only a temporary native-first compatibility parser: native parsing first, no transformation of a valid reply, and no extra Fleet corrective request. Record the exact supported fixture class, owner, removal condition, and regression test. Select the adapter at the existing engine/child context sites; do not introduce global configuration, a registry, or a provider-selection service. Adapter deletion is gated on actual compatibility evidence, not on the historical fixture count alone. If no representative gateway corpus is available, record deletion as not yet qualified; land the independent context/batch fixes without claiming provider compatibility.

## 3. Scope and non-goals

Scope: adapter call sites and sync/async behavior, two prepaid batches, full fallback worker context, one logical precharge with correctly observed request accounting, and regression coverage. Preserve `engine/signatures.py`, native RLM/Flex/typed decisions, direct route behavior, child isolation and source/state keys. Measure native fallback calls through the metered LM; removing Fleet retry must not bypass aggregate accounting.

Non-goals: dependency upgrade, provider registry, signature/module substitutes, rewrite of child orchestration, generalized retry service, global `dspy.configure`, conversion of all batching to host threads, or wholesale `streamify`/`asyncify` adoption. Previously completed Flex construction changes are prerequisites, not new tasks.

## 4. Affected code tree

```text
src/fleet_rlm/
  adapters.py                    # delete if compatibility evidence allows; otherwise shrink
  engine/programs.py             # adapter context and skill batch
  engine/children.py             # adapter context only
  sandbox/interpreter.py         # semantic fallback + typed item batch
  observability.py               # preserve existing usage bridge; edit only if needed
tests/engine/{test_adapter,test_programs}.py
tests/sandbox/test_interpreter.py
tests/lite/{test_dspy_contracts,test_lm_usage}.py
docs/reference/                  # affected model/runtime documentation
```

## 5. Concrete requirements and tasks

1. **A1 — Characterize native behavior.** Freeze sanitized recorded gateway replies by route and required output type, with provenance and fixture IDs. Compare native versus current parsing for valid-field preservation, parse failures, fallback dispatches and observed request counts. Use recorded latency only when it exists; a parser replay is not an end-to-end latency measurement. Declare acceptance criteria before evaluating the corpus: all known valid native responses pass, and required gateway outputs are at least as correctly handled without weakening the expected fields. Build fixtures for ordinary native fields, literal thinking delimiters inside code/answers, fenced code inside native fields, typed values, missing fields, malformed fences and leaked reasoning. Separate required compatibility from previously tolerated malformed output. Exercise both sync and async paths; do not regenerate live model answers in ordinary CI.
2. **A2 — Switch the execution contexts.** Instantiate native ChatAdapter at existing Flex/RLM and child contexts. Audit imports/callers before deleting `adapters.py`. Keep direct execution's existing adapter policy unless fixtures demonstrate a required correction; document the scope explicitly. Verify native JSON fallback and provider errors use existing deadline and budget wrappers. Include a budget-exhaustion fixture between the initial request and a native fallback request; it must not bypass the host limit. Count only observable provider retries as actual requests, explicitly marking unavailable transport-level retry data as unknown.
3. **A3 — Preserve fallback context.** Capture a context separately for each prompt; submit bounded work in input order; preserve the native-tool delegation branch and one aggregate precharge. Preserve exception propagation and cleanup.
4. **A4 — Stop speculative duplicate decisions.** Reproduce or inspect the pinned speculative-resubmission behavior, then set `timeout=0` on skill and item predictor batches, retaining thread bounds, input validation, optional-skill catalog fallback, cancellation propagation and TypeSafe usage bridging. This disables native straggler resubmission at these call sites only; it does not claim to eliminate wrapper/provider retries, duplicate delivery, or cancellation races.
5. **A5 — Remove redundant tests/docs.** Replace tests that demand Fleet-only disabled fallback or malformed tolerance with externally observable native behavior and explicit compatibility fixtures. Do not remove a test simply because it fails the new design; document the intentional behavior change.

Proposed fallback shape, adapting existing names/imports:

```python
from contextvars import copy_context

# After native-tool delegation, validation, and consume_budget(len(prompts)).
with ThreadPoolExecutor(max_workers=min(8, len(prompts))) as pool:
    futures = [
        pool.submit(copy_context().run, self._complete_sub_lm, prompt, active_lm)
        for prompt in prompts
    ]
    return [future.result() for future in futures]

# At both existing prepaid decision batches:
predictions = predictor.batch(examples, num_threads=min(8, len(examples)), timeout=0)
```

Never create one Context and enter it concurrently from multiple workers. Existing Turn and child submission paths already copy context and should not be rewritten merely for uniformity.

## 6. Before / after flow

Before: Fleet adapter preprocesses reply → optional Fleet corrective call → native field parsing; fallback prompt list → prepaid budget → raw threads; decision list → prepaid budget → native batch with default straggler policy.

After: native adapter/fallback owns reply handling → optional fixture-backed native-first compatibility parsing; injected native semantic batching stays native; outer fallback → prepaid budget → bounded workers with independent full contexts; decision list → prepaid budget → native batch without speculative resubmission. Shared deadlines and host resource ownership continue to apply.

## 7. Acceptance tests and measurable outcomes

| ID | Required test | Passing condition |
|---|---|---|
| A-T1 | Valid native fixture matrix, sync + async | 100% expected fields/content preserved, including literal `</think>` |
| A-T2 | Malformed reply and provider error fixtures | Native fallback policy observed; no Fleet corrective LM request; provider errors not converted into parsing success |
| A-T3 | Fallback workers with sentinel ContextVar, active manager, callback and usage tracker | Every item sees its submitting Turn context; two simultaneous Turns do not cross-contaminate |
| A-T4 | Slow decision batch instrumenting dispatch | Both call sites pass `timeout=0`; no speculative second dispatch; aggregate logical precharge remains once |
| A-T5 | Budget/deadline/cancel and optional skill failure | Host stop propagates; ordinary optional failure retains catalog; counters remain correct |
| A-T6 | Native injected semantic batching | Delegates once; host fallback executor is not invoked |
| A-T7 | Recorded gateway corpus, by route | Native policy preserves all valid fixtures and handles required gateway cases at least as correctly; any retained shim has a bounded fixture-backed contract and removal condition |
| A-T8 | Native fallback, remaining budget and wrapper retries | Each observable request is accounted for under existing policy; fallback cannot bypass exhaustion; logical items are reported separately from observed requests |

Run focused existing tests with `uv run pytest tests/engine/test_adapter.py tests/engine/test_programs.py tests/sandbox/test_interpreter.py tests/lite/test_lm_usage.py tests/lite/test_dspy_contracts.py`, then repository gates from the index. Rename/remove an adapter test file only with the corresponding coverage migration. Report removed parser/retry lines, remaining compatibility code, logical items versus actual requests, and fixture pass counts. Production quality, cost and latency improvements require a separately authorized gateway canary; no invented percentage target.

## 8. Migration, rollout and rollback

No database or native-program state migration is intended. First land the fixture corpus, context copying and bounded batch-policy changes; review the adapter switch independently. The offline gateway corpus is a prerequisite to deletion, and a live canary, when authorized and required for rollout, is a separate gate. Compare parsing correctness, fallback frequency, observed metered requests and any actually measured latency by route. Required recorded gateway behavior must not regress; a retained compatibility shim needs an explicit sunset criterion. Do not invent latency or cost from static fixtures.

Rollback the adapter patch independently if the canary establishes a material incompatibility; retain context and timeout fixes if their tests pass. Do not restore a parser that corrupts valid fields without a tracked remediation. Changing adapter behavior can invalidate optimization evidence: inspect `optimization_code_digest` coverage and require reevaluation for new approvals when identity changes. Do not claim old evaluations measure the new parser.

## 9. Risks, dependencies and open gates

Gateway output compatibility is the principal risk; native recovery may issue a different number of requests. Full ContextVars may carry application state that must remain Turn-scoped, and native callbacks can have separate thread-local concerns. Test actual metered wrappers rather than a trivial executor-only example. Depend on the locked native APIs, not unversioned docs. B–G do not require a new adapter abstraction. Approval identity coverage and the live gateway corpus must be settled before declaring adapter deletion ready for deployment.

## 10. Codex-ready checklist

- [x] Confirm pinned baseline, rules, scans and dirty-state preservation.
- [x] Record native primitive versus remaining Fleet glue, required gateway formats, corpus provenance, and native-fallback budget tests.
- [x] Implement A1 fixtures before A2; preserve signature/source/native state compatibility.
- [x] Implement A3/A4 at the exact fallback and prepaid batch call sites.
- [x] Pass A-T1–A-T8 and required repository gates; distinguish offline from live checks.
- [x] Update docs, fingerprint coverage and import consumers; report deleted custom behavior.
- [x] Record canary status and independent rollback patch; make no unsupported cost claim.

Use the [common acceptance report](README.md#acceptance-report) to record code deleted, compatibility code retained, and any new ownership/configuration surface. No additional adapter manager or global policy layer is authorized.
