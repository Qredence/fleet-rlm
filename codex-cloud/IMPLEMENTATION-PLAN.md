# Fleet-RLM: final implementation direction, current assessment, and completion plan

> **Active plan.** This file replaces the extracted v2 `IMPLEMENTATION-PLAN.md`;
> that historical version remains in `fleet-daytona-implementation-bundle-v2.zip`
> and Git history.
>
> **Implementation status (2026-10-01):** Follow-ups A and B are implemented on
> branch `fix/daytona-attachment-parity-and-broker`. Verification also found and
> fixed issues this audit did not list: the Sandbox loader stayed callable from
> later model actions, loader failures were classified as `HostSetupError`, and the
> broker accepted a `/result` before a lease was issued. Current local and live
> evidence is in [Daytona cleanup acceptance](../docs/testing/daytona-cleanup-acceptance.md).
> The rest of this document is the audit as written, against `e45bff1`.

- **Audit date:** 2026-10-01
- **Repository:** `Qredence/fleet-rlm`
- **Audited `main`:** `e45bff132e5b2fdfe14ff81f7cc66ac89aedd536`
- **Original plan baseline:** `c3f3d536242c7663cfeecdfbf63670b51630e9aa`
- **Deliverable:** one consolidated implementation direction, code assessment, and bounded completion plan.
- **Objective clarification:** the original Plans A-D were alternative drafts; Monty-style simplicity is the design objective, not a requirement to implement their combined contents.
- **Status:** recommendations only; this document did not modify, commit, or merge repository code. This revision clarifies the goal and acceptance criteria; it does not claim a new code audit or test run beyond the evidence recorded below.

## 0. Original objective and how to use this final plan

The user clarified that Plans A, B, C, and D were iterations toward **one final implementation plan**, selected for relevance, efficiency, maintainability, and the simplicity of the DSPy Monty drop-in interpreter example. They are alternative inputs, not four roadmaps, not four implementations to retain, and not a union of features to implement.

The design question is therefore:

> What is the smallest clear Daytona-specific implementation of the native DSPy interpreter contract that satisfies Fleet's actual execution, persistence, and cleanup requirements?

Completing PRs 1-3 is evidence of progress toward that goal. It is not, by itself, proof that every surviving helper is necessary, that all integration behavior is correct, or that the implementation is globally optimal. Likewise, a test suite passing does not establish that the simplest design has been chosen.

### 0.1 Decision hierarchy

Use the user's product requirements and preserved safety invariants to evaluate the final plan. Use current source and tests to establish what exists and what changes would break supported behavior. Do not preserve an unnecessary abstraction merely because it is already implemented. Do not restore an obsolete design merely because an initial draft mentioned it.

This document is the single current implementation handoff for the audited scope. The architecture reference describes enduring ownership; the acceptance receipt records what was tested; original plans and patches provide historical context. None is an instruction to rerun completed migrations.

A hybrid is justified only when selected ideas compose into one simpler design. Combining whole frameworks or leaving competing implementations behind a switch is not the goal.

### 0.2 What the Monty-style goal means here

Use a small DSPy-facing contract and a fresh invocation lifetime as the reference pattern. Do not attempt to match Monty's line count or copy its local runtime assumptions into Daytona. For this plan, the intended ownership is:

```text
Native dspy.RLM
  owns reasoning, iteration, native semantic tools, and invocation finalization
    |
    v
DaytonaCodeInterpreter
  adapts invocation configuration, execution results, and errors
    |
    v
Existing sandbox backend and broker
  own remote execution state and registered host-tool transport

Separately: Fleet's existing runtime owns Sandbox leases, mounts, and settlement.
```

Keep a layer only when its distinct responsibility earns its existence. Reject a second reasoning loop, a provider registry for hypothetical backends, duplicate result contracts, repeated binding refresh, or a new transport framework added merely to make a diagram look cleaner. Retain execution exclusion, cleanup ownership, scoped persistence, and authorization because removing those changes the required behavior. This interpretation is consistent with the existing decision record and current ownership model. [R11](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/codex-cloud/DECISION-RECORD.md) [R03](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/ARCHITECTURE.md)

### 0.3 Comparison limits

The user supplied page references named Plan A, Plan B, Plan C, and Plan D. Their original contents were not retrievable for this revision. The `codex-cloud` documents inspected in the audit are later consolidated proposals and archives, not a substitute for reading those four originals. No comparative ranking or claim to have selected the best elements of every original is made here.

The recommendation below is supported by the audited implementation, the available consolidated documents, and the user's clarified objective. It does not depend on unreadable page links. A future source-by-source comparison could revise a decision, but an unavailable draft is not a reason to restore complexity or block a demonstrated correctness repair.

## 1. Decision

**Retain the proven DSPy/Daytona ownership boundaries, not every incidental implementation detail. PRs 1-3 are implemented at the audited SHA and should not be repeated. Complete the remaining behavioral corrections, remove only demonstrated redundancy, and assess the result against the simplicity goal as well as the tests.**

The repository has the intended native-DSPy factory boundary, invocation-local configuration, first-execution sealing, one backend call shape, one structured backend result, and backend-owned broker configuration and cleanup. The later cleanup follow-up in #579 is also merged. These are substantive implementation changes, not merely plans. [R01](https://github.com/Qredence/fleet-rlm/commit/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536) [R02](https://github.com/Qredence/fleet-rlm/compare/c3f3d536242c7663cfeecdfbf63670b51630e9aa...e45bff132e5b2fdfe14ff81f7cc66ac89aedd536) [R14](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/src/fleet_rlm/daytona/interpreter.py) [R15](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/src/fleet_rlm/daytona/broker.py) [R16](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/src/fleet_rlm/daytona/runtime.py)

However, the conclusion is **not** that every requirement is fully demonstrated end to end. This audit identified three concrete residual issues in current source, plus a current-revision live-validation gap:

| ID | Priority and evidence | Assessment | Required next action |
| --- | --- | --- | --- |
| F1 | High; source inspection and isolated differential reproduction | Prepared attachments have different behavior in the sandbox and offline backends. Automatic attachment-access accounting also drains the retained template rather than the fresh invocation. | Restore attachment contract parity and connect access recording to the actual invocation. |
| F2 | Medium; isolated source-branch reproduction | Scratch cleanup can replay deletion after a body-level `TypeError` and then clear its retained path. | Make one deletion call; retain the path on failure. |
| F3 | Medium; isolated protocol-branch reproduction | A second result with the same call ID and lease can overwrite the first result before the waiting tool consumes it. | Enforce one accepted result under the existing broker lock. |
| V1 | Acceptance gap, not proof of a runtime defect | Current cleanup changes have local evidence, but the latest acceptance record explicitly leaves broader live verification pending. | Run the authorized current-candidate live gates and retain revision-bound receipts. |
| D1 | Documentation maintenance | `codex-cloud` correctly declares an archive, but its prominent historical status and apply instructions are still easy to detach from that warning. | Add a concise current-status index and link the actual merged work and current acceptance record. |

Priorities above are this audit's engineering assessment, not published vulnerability ratings. F1-F3 are not established production incidents. Their causal attribution to an individual PR has not been determined. [R14](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/src/fleet_rlm/daytona/interpreter.py) [R15](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/src/fleet_rlm/daytona/broker.py) [R17](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/src/fleet_rlm/rlm/program.py) [R18](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/src/fleet_rlm/rlm/execution.py) [R19](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/src/fleet_rlm/rlm/events.py) [R27](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/docs/testing/daytona-cleanup-acceptance.md)

The next work should be **two small correctness changesets and one acceptance/documentation closeout**, not a new interpreter framework, execution engine, memory system, or source-tree reorganization.

### 1.1 Simplicity and efficiency acceptance

| Criterion | Required final result |
| --- | --- |
| Native framework use | DSPy remains the implementation of the reasoning loop; Fleet supplies the provider adapter and application policies. |
| Small invocation model | Configure, seal before execution, install once, reuse within the invocation, then settle cleanup. No repeated binding refresh. |
| One execution boundary | One backend call shape, one structured result, and one broker owner. No execute-and-retry signature guessing. |
| No unjustified duplication | Attachment behavior must not diverge between offline and real execution. Prefer an existing small reusable helper when it works in both environments; otherwise use one contract test matrix rather than inventing a source-generation framework. |
| Evidence-based removal | Remove an obsolete branch or declaration only after checking supported callers. Tests of an obsolete helper alone are not a product requirement to preserve it forever. |
| Necessary safety only | Keep each lock, state, and cleanup handle that enforces a demonstrated ownership invariant. No speculative manager, registry, or new configuration surface. |
| Honest efficiency claim | Demonstrate install-once and no-replay behavior. Do not assert lower latency, cost, or token use without measurements. |

These criteria are the final review gate for the same bounded work below, not a fourth architectural project. Record retained and removed responsibilities in the completion report; do not add a new metrics framework or a line-count target.

## 2. Scope, evidence, and limits

### 2.1 What was examined

The review covered the `codex-cloud` loose plans, decision record, checklist, baseline record, archive README, and file inventory; the current Daytona interpreter and broker; relevant runtime cleanup and lease code; the native DSPy builder; root/child invocation wiring; attachment serialization/materialization; attachment-access recording; related existing tests; the current cleanup acceptance document; merged PR metadata; and the current commit's CI status. [R03](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/ARCHITECTURE.md) [R06](https://github.com/Qredence/fleet-rlm/tree/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/codex-cloud) [R08](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/codex-cloud/IMPLEMENTATION-PLAN.md) [R14](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/src/fleet_rlm/daytona/interpreter.py) [R15](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/src/fleet_rlm/daytona/broker.py) [R16](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/src/fleet_rlm/daytona/runtime.py) [R17](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/src/fleet_rlm/rlm/program.py) [R18](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/src/fleet_rlm/rlm/execution.py) [R19](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/src/fleet_rlm/rlm/events.py) [R20](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/src/fleet_rlm/rlm/recursion.py) [R27](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/docs/testing/daytona-cleanup-acceptance.md) [R28](https://github.com/Qredence/fleet-rlm/pull/578) [R30](https://api.github.com/repos/Qredence/fleet-rlm/commits/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/status)

This is a plan-to-code audit of the Daytona/DSPy boundary and its connected paths. It is **not an exhaustive security or correctness audit of every API route, SQL repository, TUI component, optimizer, or workspace operation**. Statements that those areas were not changed by the three-PR program come from the commit comparison, not from claiming to have reverified all of them. [R02](https://github.com/Qredence/fleet-rlm/compare/c3f3d536242c7663cfeecdfbf63670b51630e9aa...e45bff132e5b2fdfe14ff81f7cc66ac89aedd536) [R31](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/docs/reference/source-layout.md)

### 2.2 Evidence levels used in this document

- **Source-verified:** observed in files read at the immutable audited SHA.
- **Locally reproduced:** an isolated harness executed the relevant copied source logic, with explicit surrounding stand-ins and temporary files. This is narrower than importing Fleet and running its integration tests.
- **CI-observed:** GitHub returned successful statuses for the audited commit; those jobs were not rerun in this audit environment.
- **Recorded evidence:** a repository receipt or PR description reports a run. Local paths mentioned there were not automatically accessible to this audit.
- **Proposed:** work described below that has not been applied or validated as a fix.

A local repository clone failed because the execution environment could not resolve GitHub. Connected GitHub reads succeeded. The local environment did not contain the pinned Fleet/DSPy/Daytona runtime, so no full repository test suite, type checker, or live provider test was run here. Three isolated red-capable diagnostics were run; their results appear in Section 7.

GitHub code-search results were sometimes indexed at the previous merge. Search was used to discover locations; decisive implementation findings use files fetched at `e45bff1...`. A search result's absence alone is not treated as proof that a public or private symbol is dead.

## 3. What is already implemented

### 3.1 Merge and scope reconciliation

| Work | Current status | Evidence |
| --- | --- | --- |
| PR 1 / #576 | Merged: prevent execution-time `TypeError` replay and correct error-subclass classification | [R32](https://github.com/Qredence/fleet-rlm/pull/576), current dispatch and labels in [R14](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/src/fleet_rlm/daytona/interpreter.py) |
| PR 2 / #577 | Merged: invocation sealing/install-once, removed adapter generation/reservation machinery, preserved execution ownership, added deferred shutdown | [R33](https://github.com/Qredence/fleet-rlm/pull/577), current invocation and locking code in [R14](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/src/fleet_rlm/daytona/interpreter.py) |
| PR 3 / #578 | Merged as `a041d747f711747d527724ddeb0100318c2406b9`: typed backend/results, direct dispatch, backend-owned broker and port | [R28](https://github.com/Qredence/fleet-rlm/pull/578), [R14](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/src/fleet_rlm/daytona/interpreter.py) |
| Cleanup follow-up / #579 | Merged as audited `e45bff1...`: shutdown call-shape selection, retained failed cleanup handles, terminal release correction, acceptance/archive documentation | [R01](https://github.com/Qredence/fleet-rlm/commit/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536), [R16](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/src/fleet_rlm/daytona/runtime.py), [R27](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/docs/testing/daytona-cleanup-acceptance.md), [R34](https://github.com/Qredence/fleet-rlm/pull/579) |

From the original plan baseline to the audited commit, GitHub reports **10 commits and 32 changed files**. Only three production source files changed: [R02](https://github.com/Qredence/fleet-rlm/compare/c3f3d536242c7663cfeecdfbf63670b51630e9aa...e45bff132e5b2fdfe14ff81f7cc66ac89aedd536)

| Production file | Added | Removed | Net |
| --- | ---: | ---: | ---: |
| `src/fleet_rlm/daytona/interpreter.py` | 214 | 279 | -65 |
| `src/fleet_rlm/daytona/broker.py` | 22 | 6 | +16 |
| `src/fleet_rlm/daytona/runtime.py` | 73 | 15 | +58 |
| **Total** | **309** | **300** | **+9** |

The remaining changes are documentation, imported historical planning artifacts, and existing test modules. No new production module or test module was added by this comparison. Dependency manifests, persistence schemas, and public API schema source files are not in its changed-file set. Net line count is not a quality score: the improvement is fewer adapter states and owners while retaining required cleanup behavior. [R02](https://github.com/Qredence/fleet-rlm/compare/c3f3d536242c7663cfeecdfbf63670b51630e9aa...e45bff132e5b2fdfe14ff81f7cc66ac89aedd536)

The dependency baseline remains Fleet 0.7.10, DSPy 3.4.0, Daytona 0.218.0, and Python `>=3.11,<3.14`. No DSPy upgrade should be scheduled as unfinished work from these plans. [R05](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/pyproject.toml)

### 3.2 The implemented ownership chain

```text
Fleet Turn/application owners
  authorize inputs and capabilities; own durable settlement
  |
  +-- DaytonaRuntime
  |     owns root/child Sandbox leases, mounts, admission and containment
  |
  +-- native dspy.RLM
        owns reasoning, iterations and invocation interpreter finalization
        |
        +-- DaytonaCodeInterpreter: one invocation
              configure -> seal on first execute -> install bindings once
              |
              +-- InterpreterBackend.run: one direct call
                    |
                    +-- _SandboxProcessBackend
                          owns one DaytonaHttpToolBroker and its port
                          |
                          +-- Sandbox-local executable Python namespace
                              and registered host-tool transport
```

`build_native_rlm()` still constructs actual `dspy.RLM`, passing the interpreter factory, authorized tools, limits, and `sub_lm`. Root execution still captures Run-local data in the factory rather than rebinding the retained adapter. The child path also requests a fresh invocation and binds its output contract and scratch. There is no replacement RLM iteration engine in these changes. [R17](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/src/fleet_rlm/rlm/program.py) [R18](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/src/fleet_rlm/rlm/execution.py) [R20](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/src/fleet_rlm/rlm/recursion.py)

The retained Sandbox and the invocation namespace remain different lifetimes. `new_invocation()` constructs a new live backend against the retained Sandbox and copies backend port configuration; it does not share that backend's broker namespace. This is the relevant Monty-style lifecycle pattern, without copying Monty's runtime constraints into Daytona. [R11](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/codex-cloud/DECISION-RECORD.md) [R14](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/src/fleet_rlm/daytona/interpreter.py)

### 3.3 Requirement-by-requirement reconciliation

The IDs below follow the unsuffixed v2 PRD rather than inventing a new set of architectural requirements. [R09](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/codex-cloud/PRD-and-implementation-plan.md)

| Requirement | Current assessment |
| --- | --- |
| FR-01: native DSPy | Implemented. Native constructor and factory path remain. |
| FR-02: fresh interpreter per invocation | Implemented in `new_invocation()` and root/child callers; namespace tests and the narrow live canary exist. |
| FR-03: state within invocation | Implemented through the broker namespace; not the same as durable conversation or Volume persistence. |
| FR-04: at-most-once local backend dispatch | Implemented for `run()`. F2 is a separate residual deletion fallback, not a regression of `_run_backend()`. |
| FR-05: invocation-lifetime bindings | Implemented: guarded mutations, sealing at first execute, install-once, cached installation failure. |
| FR-06: native semantic tools | Preserved through native DSPy and `sub_lm`; do not recreate them as another Fleet tool API. |
| FR-07: authorized host tools | Registered host dispatch and async bridge remain. F3 leaves part of the stated result-delivery contract incomplete. |
| FR-08: structured backend result | Implemented: internal `run()` returns `BackendExecutionResult`; test doubles were migrated. |
| FR-09: structural final output | Implemented in `_finalize()`; ordinary marker-looking stdout is not promoted into `FinalOutput`. |
| FR-10: repairable versus terminal errors | Preserved at the adapter boundary. Broad failure containment still requires the pending live acceptance evidence. |
| FR-11: one live broker owner | Implemented: backend creates/stops broker; adapter property is inspection-only. |
| FR-12: resource ownership | Preserved and hardened by #579. F2 still needs correction; current live acceptance is pending. |
| FR-13: bounded output/events | Existing bounds remain. This review does not certify every output surface; automatic attachment-access propagation has the F1 gap. |
| FR-14: public contracts | No public schema source change in the reviewed program. The internal Python backend/port API did intentionally change. |

The original nonfunctional objectives of no new production module, dependency, permanent flag, or parallel execution architecture have been met in the reviewed changes. The documented final live release gate has **not** been demonstrated in full for the current revision. [R02](https://github.com/Qredence/fleet-rlm/compare/c3f3d536242c7663cfeecdfbf63670b51630e9aa...e45bff132e5b2fdfe14ff81f7cc66ac89aedd536) [R08](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/codex-cloud/IMPLEMENTATION-PLAN.md) [R14](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/src/fleet_rlm/daytona/interpreter.py) [R27](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/docs/testing/daytona-cleanup-acceptance.md) [R28](https://github.com/Qredence/fleet-rlm/pull/578)

### 3.4 Correct deviations from the literal historical proposals

**Additive tool injection is intentional.** `_BindingTools.update()` now merges rather than clearing first. Fresh invocation state replaces the old need to revoke omitted names on reused interpreter injection. Post-seal mutation is still rejected. Do not restore replacement semantics just to match an old implementation. [R14](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/src/fleet_rlm/daytona/interpreter.py) [R33](https://github.com/Qredence/fleet-rlm/pull/577)

**Port configuration moved to the actual owner.** Custom ports now enter through `sandbox_backend(..., broker_port=...)`; the adapter constructor option was removed. Zero is rejected because no actual brokerless live execution engine existed. This is an internal Python API migration, not evidence that HTTP/SSE schemas changed. [R03](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/ARCHITECTURE.md) [R14](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/src/fleet_rlm/daytona/interpreter.py) [R28](https://github.com/Qredence/fleet-rlm/pull/578)

**Deferred shutdown is necessary ownership, not unwanted complexity.** `_pending_shutdown` preserves a request while work finishes, and cleanup happens under the lifecycle locks. Removing it to achieve a smaller line count would undermine the implemented cancellation behavior. [R14](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/src/fleet_rlm/daytona/interpreter.py) [R25](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/tests/unit/backend/rlm/test_dspy_compat_seam.py) [R33](https://github.com/Qredence/fleet-rlm/pull/577)

**The cleanup follow-up is not missing anymore.** #579 already prevents shutdown-body `TypeError` from selecting another call shape; retains failed client/session handles; and fixes terminal release ownership. The remaining plan must not recreate those fixes. [R15](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/src/fleet_rlm/daytona/broker.py) [R16](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/src/fleet_rlm/daytona/runtime.py) [R27](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/docs/testing/daytona-cleanup-acceptance.md)

## 4. `codex-cloud/`: what to keep and what to edit

### 4.1 Complete current directory inventory

```text
codex-cloud/
|-- .gitattributes
|-- BASELINE-AND-EVIDENCE.md
|-- DECISION-RECORD.md
|-- IMPLEMENTATION-CHECKLIST.md
|-- IMPLEMENTATION-PLAN.md
|-- PRD-and-implementation-plan (1).md
|-- PRD-and-implementation-plan.md
|-- README.md
|-- fleet-daytona-correctness (1).patch
|-- fleet-daytona-correctness.patch
|-- fleet-daytona-implementation-bundle-v2.zip
`-- fleet-daytona-implementation-bundle.zip
```

This directory is an **archive of planning inputs**, not the current implementation specification. The README now correctly says so, explicitly warns against applying the patches to the current branch, explains the two versions, and explains that helper scripts/manifests/isolated receipts are inside the ZIPs. Their absence as loose files is not missing implementation. [R06](https://github.com/Qredence/fleet-rlm/tree/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/codex-cloud) [R07](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/codex-cloud/README.md)

The two PRDs are different versions: the `(1)` document preserves the original assessment with its older evidence limits; the unsuffixed document is the standalone v2 specification. Do not delete either simply because the filenames look duplicated. The ZIP contents and checksums were not independently extracted or certified in this audit. [R07](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/codex-cloud/README.md) [R09](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/codex-cloud/PRD-and-implementation-plan.md) [R10](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/codex-cloud/PRD-and-implementation-plan%20%281%29.md)

### 4.2 Required documentation change

Add a short current-status table immediately after the archive warning in `codex-cloud/README.md`:

| Historical work item | Current implementation | Current evidence |
| --- | --- | --- |
| PR 1 | Merged #576; direct typed dispatch now supersedes its temporary signature compatibility | Current source and retained no-replay tests |
| PR 2 | Merged #577, including deferred shutdown correction | Current source and lifecycle contracts |
| PR 3 | Merged #578; internal backend and port migration complete | Narrow live canaries reported for that PR |
| Cleanup follow-up | Merged #579 | Local/CI evidence; current broader live acceptance pending |

Link the current architecture and acceptance document. Label the preserved lower portion as **historical v2 instructions**, including its unchecked boxes and old patch commands. Those historical boxes must not be interpreted as current missing work. [R07](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/codex-cloud/README.md) [R27](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/docs/testing/daytona-cleanup-acceptance.md)

Update the current acceptance document's candidate/status section so it distinguishes **merged code revision**, **locally tested revision**, and **live-tested revision**. Its branch-preparation language is now stale relative to #579 being merged; the pending live status is not stale and must remain until evidence changes. [R01](https://github.com/Qredence/fleet-rlm/commit/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536) [R27](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/docs/testing/daytona-cleanup-acceptance.md)

### 4.3 What not to remove

Retain the original ZIPs, historical patch bytes, and distinct plan versions unless a separate archive-retention decision is made. Do not rewrite their old results to imply they tested newer code. Keep the scoped `.gitattributes` rule while unified patches are retained. Do not extract the isolated historical harness into the production test suite or reapply PR 1's patch. [R06](https://github.com/Qredence/fleet-rlm/tree/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/codex-cloud) [R07](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/codex-cloud/README.md)

Keep **one active implementation plan**, not several competing normative documents. When adopting this handoff in the repository, replace the extracted historical `codex-cloud/IMPLEMENTATION-PLAN.md` with the current consolidated plan and make the README link it as the sole active task specification. Its original proposal remains recoverable from the existing historical ZIP and Git history; do not present the revised file as an untouched historical artifact.

Keep the other original draft exports, patches, and bundles clearly archival. The README must distinguish that archive from the active plan; it must no longer label every file in the directory as historical once the active plan is installed. The architecture document remains the enduring ownership reference and the existing acceptance record remains the evidence log. No additional plan hierarchy, duplicate status manifest, or parallel checklist is necessary.

## 5. Remaining correctness work

### F1. Restore the prepared-attachment contract across real sandbox execution

**Priority: high.** This is the most important remaining behavior mismatch in the reviewed scope.

#### Source-observed discrepancy

`AttachmentContextCapsule.to_sandbox()` serializes a manifest containing each attachment's identity, filename, content type, byte size, checksum, and path. It does **not** serialize an `encoding` field. `_materialize_context_manifest()` in `rlm/program.py` is the current offline materializer, with mount, size, checksum, and binary handling. [R17](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/src/fleet_rlm/rlm/program.py)

The live `_SandboxProcessBackend.run()` separately emits a handwritten `_fleet_load_context_manifest()` implementation. Its output and validations differ. [R14](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/src/fleet_rlm/daytona/interpreter.py)

| Behavior | Offline path | Generated sandbox path at audited SHA |
| --- | --- | --- |
| Attachment record fields | `id`, `filename`, `content_type`, `byte_size`, `data`, `encoding` | `attachment_id`, `data`, `encoding` |
| Invalid UTF-8 bytes | Return bytes with `encoding="bytes"` | Default to UTF-8 and raise `UnicodeDecodeError` |
| UTF-8-decodable content containing NUL | Return bytes | Return text |
| Manifest mount identity | Compare resolved manifest root to trusted root | Trusted root is computed but not compared |
| Resolved file path containment | Check resolved path remains within trusted mount and is not its root | No equivalent containment check |
| Single text attachment convenience `context` | Populate `context` with that attachment's text | Initial `context=[]` is not replaced by loader |
| Automatic access IDs | Append verified attachment IDs | No corresponding population of backend access IDs in the loader path |

Checksums do not eliminate the containment mismatch: a lexically in-mount symlink can resolve outside the mount to bytes matching the expected digest. The offline materializer rejects that case; the generated loader accepts it. This is a missing loader integrity check, **not a demonstrated Daytona Sandbox escape**, and this audit does not claim a production attack occurred. [R14](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/src/fleet_rlm/daytona/interpreter.py) [R17](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/src/fleet_rlm/rlm/program.py)

The existing in-process attachment round-trip test submits `context` and therefore verifies only the offline behavior. It does not exercise the distinct generated loader used by the live sandbox backend. [R21](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/tests/unit/backend/rlm/test_program_inputs.py)

#### Additional recording gap at the invocation boundary

There is a second break in the automatic access path. Root execution builds a fresh adapter inside the native factory, but `ExecutionTraceAssembler._record_attachment_accesses()` later drains `context.execution.interpreter`, which is the retained template in that path. Simply populating the fresh backend's access list will not fix this consumer. [R18](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/src/fleet_rlm/rlm/execution.py) [R19](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/src/fleet_rlm/rlm/events.py)

This finding concerns automatic prepared-attachment access reporting. It does not assert that explicit attachment tools never record accesses by another route.

#### Required implementation

Preserve the existing offline contract as the target. Edit the generated loader in place to return the same complete record shape, infer binary/text handling from content, perform the existing manifest-root and resolved-path checks, and populate the single-text `context` convenience value. Do not add an `encoding` requirement to the manifest merely to preserve the current live bug. [R14](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/src/fleet_rlm/daytona/interpreter.py) [R17](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/src/fleet_rlm/rlm/program.py)

Add a bounded, explicit path for verified access IDs to reach the already-existing `BackendExecutionResult.context_accesses` field. Keep attachment bodies inside the sandbox namespace; do not JSON-serialize raw bytes through the broker's output envelope. Use the current attachment-count limits for this metadata rather than introducing a new unbounded log.

Connect recording to the actual factory-created invocation. Use the existing Run-local factory/trace ownership seam; do not write Run-local accesses back onto the retained adapter. Record the accesses after owned execution has settled, including the failure path where a verified attachment was read before a later error. Preserve the existing authorization and commit semantics of capability recording. [R18](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/src/fleet_rlm/rlm/execution.py) [R19](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/src/fleet_rlm/rlm/events.py)

**Keep this correction simple:** align the generated implementation with the existing materializer and drive both with one shared contract test matrix. Removing the duplicate implementation text through a source-generation framework is not required. If a small existing helper can safely be reused, it must still run without importing the host Fleet/DSPy package into the Sandbox. Behavioral equivalence is the acceptance criterion, not an abstraction quota.

#### Required regression coverage

Use existing `test_program_inputs.py`, `test_sandbox_variable_binding.py`, and the native vertical-slice owner. Exercise the actual generated setup through the sandbox backend with a deterministic embedded/fake broker; do not replace the result with a prebuilt `BackendExecutionResult`. [R21](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/tests/unit/backend/rlm/test_program_inputs.py) [R24](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/tests/unit/backend/daytona/test_sandbox_variable_binding.py) [R26](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/tests/contracts/backend/test_native_dspy_fastapi_vertical_slice.py)

The same fixtures must establish:

1. Complete and identical attachment metadata for one text and multiple attachments.
2. Identical byte handling for invalid UTF-8 and NUL-bearing data.
3. Correct single-text `context`; no stale context from a prior invocation.
4. Rejection of a mismatched manifest digest, manifest root, file length, file digest, and out-of-mount resolved symlink target.
5. Correct access IDs reaching the capability owner exactly once, on success and after a later execution failure.
6. No mutation of the retained template and no cross-invocation access leakage.

These are one parametrized behavioral matrix plus an invocation-wiring test, not a new test framework. A later authorized live attachment canary must exercise the same representation rather than only executing a hardcoded host tool.

### F2. Remove execution-time fallback from scratch deletion

**Priority: medium.** `_run_backend()` and runtime shutdown are now non-replaying, but `_SandboxProcessBackend.cleanup_run_scratch()` still has a neighboring version of the old pattern. [R14](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/src/fleet_rlm/daytona/interpreter.py) [R16](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/src/fleet_rlm/daytona/runtime.py)

It first calls `delete(path, recursive=True)`, catches a `TypeError` around that call, and calls `delete(path)` again. A body-level exception after the filesystem operation begins is indistinguishable from a signature error. If the second call returns, `_run_scratch_path` is cleared and the original error is lost.

The isolated reproduction produced two calls, first with recursive deletion and then without it, and left the path set to `None`. That demonstrates incorrect local call selection; it does not establish what a particular provider did remotely.

#### Required implementation

Call the supported filesystem deletion shape once. Retain the existing handling of `DaytonaFileNotFoundError` as confirmed path absence. Clear `_run_scratch_path` only after the deletion succeeds or that explicit not-found result is obtained.

Let a body-level `TypeError` propagate and retain the scratch path for the owning cleanup/retry path. Do not add a blanket catch or suppress it as already deleted. Update old fakes to accept the real deletion keyword. Only retain signature compatibility if a supported concrete caller requires it, and select the shape before invocation as #579 already does for shutdown. Do not build a generic dispatcher for one filesystem method.

This is a repair of scratch cleanup, not a change to Sandbox deletion, Volume ownership, or the deferred-shutdown algorithm. [R14](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/src/fleet_rlm/daytona/interpreter.py) [R16](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/src/fleet_rlm/daytona/runtime.py)

#### Required regression coverage

In an existing Daytona test owner, cover successful recursive deletion, explicit not-found, body-level `TypeError`, and an explicit subsequent cleanup attempt after the dependency recovers. The first failed attempt must have one call, preserve the path, and expose the error. Concurrent cleanup while execution is active must remain rejected.

### F3. Make broker result acceptance immutable after the first delivery

**Priority: medium.** The server's `/result` branch validates that a request exists and that the lease matches, then writes `_results[call_id]` and signals its event. The waiting `/tool_call` handler removes the pending request and moves the ID to `_completed` only when it consumes the result. [R15](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/src/fleet_rlm/daytona/broker.py)

Between those operations, a second `/result` with the same ID and lease passes the same checks and overwrites the first payload. Terminal duplicates are rejected later, but the result-ready interval is not covered.

The isolated reproduction deliberately held the request in that interval. Both deliveries returned 200 and the stored result became the second value. This is **duplicate result acceptance**, not a claim that Fleet executed the host tool twice.

#### Required implementation

While holding the existing `_lock`, reject a result when that pending call already has an accepted result. Use the existing `_results` membership or equivalent existing ready state. Preserve the first value and return the existing duplicate/conflict status rather than acknowledging a replacement.

Require a valid issued lease, preserving wrong-lease, unknown-call, and completed-call behavior. Do not introduce another identifier, deduplication service, replay log, provider retry, or database transaction. The existing per-invocation state and lock are sufficient. [R15](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/src/fleet_rlm/daytona/broker.py)

#### Required regression coverage

Extend `test_broker.py`, preferably using its existing embedded server or a deterministic controlled-handler fixture. Coordinate the waiter with events/barriers rather than sleeps. Prove that a duplicate arriving before consumption is rejected and the first value survives; also retain stale-lease, unknown-call, already-consumed duplicate, timeout, and ordinary successful delivery cases. [R22](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/tests/unit/backend/daytona/test_broker.py)

### What these findings do not justify

They do not justify reverting the typed protocol, restoring generation-based binding refresh, replacing the broker with native contexts, or introducing a second execution path. They are corrections within the already-selected ownership model.

## 6. Cleanup decisions: remove, edit, add, retain

| Action | Concrete scope | Reason |
| --- | --- | --- |
| **Remove** | Execute/catch/retry branch in scratch deletion | It can replay an operation and hide the original failure. |
| **Remove** | Result overwrite opportunity after a successful broker delivery | One pending call must have one accepted result. |
| **Edit** | Generated attachment loader in `interpreter.py` | Restore the established materialization contract and integrity checks. |
| **Edit** | Root invocation/access drain wiring in `execution.py` and `events.py` | Drain the created invocation, not its retained template. |
| **Edit** | Archive README and current cleanup receipt | Distinguish merged implementation from historical instructions and pending live evidence. |
| **Add** | Shared fixture-based parity tests and controlled result-delivery tests in existing modules | Current tests can pass without reaching the mismatching paths. |
| **Add** | Current-candidate acceptance receipts after authorized live runs | Historical canary success does not certify later cleanup changes. |
| **Retain** | Native DSPy, one backend result, sealed bindings, locks, pending shutdown, backend broker ownership | These are the successfully implemented architecture. |
| **Retain** | Historical ZIPs, distinct original/v2 plans and patches | Provenance is not production dead code; keep them explicitly archived. |

### Non-blocking cleanup candidates

**Port types.** Backend construction checks numeric range but not explicitly integer/non-boolean type. A Python boolean or fractional value can satisfy that comparison. Because the API is internally typed and no such production caller was demonstrated, this is lower priority than F1-F3. A small constructor validation plus parametrized test may reject booleans, fractional values, and strings with the existing configuration error. Do not add a setting or validation framework. [R14](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/src/fleet_rlm/daytona/interpreter.py)

**Legacy protocol/DTO declarations.** `DaytonaExecutionBackend` and `ExecutionResult` remain separate declarations in the interpreter module. Treat them as reference-scan candidates, not proven removable code: inspect imports, exports, diagnostics, scripts, packaging, and tests at the implementation revision. Remove them only if no supported consumer remains. Their names alone do not prove that another production execution engine exists.

**Standalone final-frame helpers.** Do not delete `extract_final_payload()` just because typed `_finalize()` no longer calls it. PR 3 deliberately retained its independent utility/tests. Removal requires a separate supported-use check. [R28](https://github.com/Qredence/fleet-rlm/pull/578)

**Shutdown flags.** The live backend is already strict; retain the existing shutdown signature unless a separate caller migration is justified. `_pending_shutdown` currently distinguishes `None` from `False`: `False` can still mean a pending request. A truthiness rewrite would lose that distinction. [R14](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/src/fleet_rlm/daytona/interpreter.py)

**Generation terminology.** Do not remove `InterpreterLease.binding_generation` or session `BindingGenerationAuthority` merely because PR 2 removed adapter binding generations. Those are a different runtime/session ownership concern. [R16](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/src/fleet_rlm/daytona/runtime.py)

No cleanup above requires splitting `interpreter.py` or `runtime.py` into a collection of small facade modules.

## 7. Verification performed during this audit

### 7.1 Isolated differential and branch results

Three local scripts were run against copied relevant source logic with explicit stand-ins. Their standalone diagnostics use no credentials and contact no provider. They are not Fleet's normal test suite.

| Diagnostic | Observed current-source result | Expected corrected behavior |
| --- | --- | --- |
| Text materialization | Offline keys: `byte_size, content_type, data, encoding, filename, id`; sandbox keys: `attachment_id, data, encoding` | Identical complete record shape |
| Invalid UTF-8 | Offline returns bytes; sandbox raises `UnicodeDecodeError` | Both return bytes |
| Embedded NUL | Offline returns bytes; sandbox returns text | Both follow the established bytes policy |
| Resolved symlink outside mount | Offline raises `ValueError`; sandbox source accepts matching bytes | Both reject the path |
| Duplicate result before consumption | Statuses `[200, 200]`; stored result is the second value | `[200, 409]`; first value retained |
| Scratch deletion body `TypeError` | Two calls; recursive `True` then `False`; scratch path cleared | One call; original error visible; path retained |

The scripts exited with assertions identifying the attachment shape mismatch, duplicate acceptance, and deletion replay respectively. No proposed fix was installed and no green-after-fix claim is made.

The symlink fixture uses a host-created manifest with a lexically valid in-mount path and matching digest. This isolates the missing resolved-path check. It does not bypass the manifest checksum or demonstrate access beyond the provider Sandbox.

The access-recording mismatch was established by tracing the factory and consumer in current source; a full native-Fleet reproduction of that wiring was not run here. [R18](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/src/fleet_rlm/rlm/execution.py) [R19](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/src/fleet_rlm/rlm/events.py)

### 7.2 Existing CI versus recorded local and live results

GitHub returned success for all eight CircleCI contexts on `e45bff1...`: unit tests, coverage gate, lint/type checks, quality, TUI, and Python 3.11/3.12/3.13 compatibility. GitBook also returned success. This is observed CI status, not an independent rerun of its commands or log contents. [R30](https://api.github.com/repos/Qredence/fleet-rlm/commits/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/status)

The cleanup receipt records 234 focused tests, a final complete local `make check`, approximately 83.1% Python coverage, 444 TUI tests, and 2,096 Python 3.11 backend/contract tests. These are the receipt's claims; raw local receipt paths were not mounted in this session. [R27](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/docs/testing/daytona-cleanup-acceptance.md)

PR 3 records two live Daytona tests. Its published scope explicitly excludes broader live LM/API, mounted Workspace durability, recursive children, cancellation/containment, security, and release certification. The newer cleanup acceptance record still leaves the broader live gates pending. **Do not aggregate those different scopes into a claim that current `main` is fully live-certified.** [R27](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/docs/testing/daytona-cleanup-acceptance.md) [R28](https://github.com/Qredence/fleet-rlm/pull/578)

The new live invocation test is useful, but it creates a Volume-less Sandbox and tests ordinary tools, a cached Python variable, async dispatch, structured SUBMIT, fresh namespace, and deletion. It does not exercise a prepared attachment capsule or shared Workspace persistence. [R29](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/tests/live/backend/test_daytona_deletion_lifecycle.py)

## 8. Bounded implementation sequence

### Follow-up A: prepared-attachment parity and actual-invocation accounting

**Outcome:** the same manifest has the same validated in-Sandbox representation as the offline contract, and automatic access metadata reaches the correct capability owner.

Primary changes are in `daytona/interpreter.py`, `rlm/execution.py`, and `rlm/events.py`. Treat `rlm/program.py` as the existing reference contract; do not change its field names to accommodate the divergent loader. Change it only if a small shared fixture/helper interface is genuinely needed and preserves behavior.

Implement the fixture matrix before changing the loader. First reproduce the discrepancy by running the actual generated source through the sandbox backend's deterministic test seam. Correct the loader; then connect access recording to the created invocation and test success, failure, and a second fresh invocation. Preserve canonical DSPy finalization and existing worker settlement.

**Acceptance:** all six F1 test requirements pass on the real pinned dependency stack, the existing adapter/native contracts remain green, and no body bytes or private filesystem paths are added to public event schemas.

### Follow-up B: one cleanup call and one accepted result

**Outcome:** cleanup dispatch cannot replay on a body exception, and a broker call's first accepted result cannot be replaced.

Modify the existing scratch cleanup method and embedded `/result` branch. Add the two small regression groups to existing Daytona test modules. Keep #579's broker handle retention and lease settlement unchanged.

A lower-priority broker-port type guard can be included only if it is a tiny independently tested constructor correction. Do not couple this changeset to DTO removal or a source-tree split.

**Acceptance:** original errors remain visible, failed cleanup retains its handle, explicit cleanup retry works, duplicate result delivery returns a conflict without changing the first payload, and deferred shutdown/cancellation contracts still pass.

A and B are independent enough to be reviewed separately. Neither should be called a reimplementation of PR 1, PR 2, or PR 3.

### Closeout C: archive index and current-candidate acceptance

Install the single current plan and update the README and existing acceptance record as described in Section 4. Review the simplicity criteria in Section 1.1 alongside the correctness criteria. Run the local final gate on the combined candidate. With explicit operator authorization, run the existing live gates on that immutable candidate using new receipt paths.

Record the exact SHA tested, commands, test counts/skips, relevant environment versions, receipt locations, confirmed cleanup outcomes, and any unresolved failures. Preserve earlier evidence as earlier evidence. A failed or unavailable live lane remains visible; do not mark it complete because local tests pass.

If a live failure exposes another concrete bug, use a separate regression and the smallest owner-local correction. Do not silently turn this closeout into native-context migration, new recursion behavior, or a release promotion.

## 9. Complete intended touch tree

This is the **complete planned touch set for the follow-ups**, not a representation of every file in the repository. Files marked conditional are not instructions to edit them without need.

```text
codex-cloud/
|-- README.md                                      # identify one active plan and separate historical archive
`-- IMPLEMENTATION-PLAN.md                         # replace extracted historical copy with this consolidated handoff

docs/testing/
`-- daytona-cleanup-acceptance.md                    # merged/local/live revision status and receipts

src/fleet_rlm/
|-- daytona/
|   |-- interpreter.py                             # attachment parity; scratch deletion; optional port validation
|   `-- broker.py                                  # one accepted result; bounded access metadata if required
`-- rlm/
    |-- execution.py                               # keep actual invocation available to its Run-local recorder
    |-- events.py                                  # drain correct invocation after settlement
    `-- program.py                                 # reference contract; edit only if a small shared seam is needed

tests/
|-- unit/backend/
|   |-- daytona/
|   |   |-- test_daytona_adapter.py                  # cleanup/error retention and access behavior
|   |   |-- test_sandbox_variable_binding.py         # actual generated loader contract and isolation
|   |   `-- test_broker.py                           # duplicate-result interval and first-value preservation
|   `-- rlm/
|       `-- test_program_inputs.py                  # shared attachment fixture expectations
|-- contracts/backend/
|   `-- test_native_dspy_fastapi_vertical_slice.py   # fresh-invocation access ownership
`-- live/backend/
    `-- test_daytona_deletion_lifecycle.py           # existing canary; edit only if extending its exact contract
```

The entire Daytona production package remains:

```text
src/fleet_rlm/daytona/
|-- __init__.py
|-- broker.py
|-- diagnostics.py
|-- errors.py
|-- interpreter.py
|-- runtime.py
`-- snapshot-requirements.txt
```

**New production modules required: zero. New test modules required: zero.** Runtime lease policy, snapshots, resource defaults, Volume mounts, dependency pins, and public schemas are not planned changes. Existing files outside this touch set may be read and tested without being edited.

## 10. Implementation validation commands and acceptance gates

### 10.1 Worktree and baseline discipline

Read the current `AGENTS.md`, confirm the starting branch and SHA, and do not reset a newer legitimate `main` to this audit snapshot. Preserve unrelated work. A suggested read-only baseline check is:

```bash
BASE=$(git rev-parse HEAD)
git status --short --untracked-files=all
git merge-base --is-ancestor e45bff132e5b2fdfe14ff81f7cc66ac89aedd536 "$BASE"
git show --no-patch --format=fuller "$BASE"
```

A failed ancestry check requires reconciliation of the checkout before assuming this audit matches it. It is not permission to overwrite the worktree.

### 10.2 Focused non-live gate

Use the pinned dependency environment and existing behavior-owning files. These are implementation-time commands, **not checks claimed to have run here**:

```bash
uv sync --frozen --dev
uv run pytest -q -n 0 \
  -m 'not live_llm and not live_daytona and not benchmark and not db and not packaging' \
  tests/unit/backend/daytona/test_daytona_adapter.py \
  tests/unit/backend/daytona/test_sandbox_variable_binding.py \
  tests/unit/backend/daytona/test_broker.py \
  tests/unit/backend/daytona/test_runtime.py \
  tests/unit/backend/daytona/test_interpreter_output_cap.py \
  tests/unit/backend/rlm/test_program_inputs.py \
  tests/unit/backend/rlm/test_dspy_compat_interpreter_contract.py \
  tests/unit/backend/rlm/test_dspy_compat_seam.py \
  tests/unit/backend/rlm/test_recursion_lease_cleanup.py \
  tests/contracts/backend/test_host_tool_submit_binding.py \
  tests/contracts/backend/test_native_dspy_fastapi_vertical_slice.py
uv run ruff check src tests scripts migrations
uv run ruff format --check src tests scripts migrations
uv run ty check src
make check-codebase-tree check-dependency-boundaries check-docs
make check
git diff --check
```

Use the Node/pnpm versions declared by the current TUI package. Fix environment launch/setup issues outside tracked production files when appropriate. If `make check` stops, record the exact failure and which constituents were subsequently run; do not relabel an incomplete aggregate invocation as having passed. [R04](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/AGENTS.md) [R27](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/docs/testing/daytona-cleanup-acceptance.md) [R28](https://github.com/Qredence/fleet-rlm/pull/578)

### 10.3 Live acceptance: explicitly authorized only

This audit authorizes no provider operations. The repository requires operator authorization and the documented entry points. Never request credentials in a pasted prompt or use unrelated credential attachments. [R04](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/AGENTS.md)

After A/B are complete and authorized, one grounded invocation/deletion gate is:

```bash
mkdir -p .scratch/daytona-current-acceptance
CANDIDATE=$(git rev-parse HEAD)
FLEET_LIVE=1 \
FLEET_LIVE_EVIDENCE_PATH="$PWD/.scratch/daytona-current-acceptance/$CANDIDATE.json" \
uv run pytest -q --tb=short -n 0 \
  tests/live/backend/test_daytona_deletion_lifecycle.py::test_live_interpreter_invocations_settle_before_sandbox_deletion \
  -o addopts='--strict-markers'
```

The test derives a suffixed receipt filename from that path and retains its existing timeout/cleanup policy. Do not claim this gate verifies mounted Workspace durability: its Sandbox is intentionally Volume-less. [R29](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/tests/live/backend/test_daytona_deletion_lifecycle.py)

Complete the additional existing gates named by the current acceptance document, using their documented profiles/options rather than inventing new ones:

| Gate | What it must establish |
| --- | --- |
| `scripts/live_daytona_verify.py` | Native semantic execution, prepared attachment behavior, artifact/workspace durability within the authorized profile |
| `scripts/live_recursive_batch_canary.py` | Ordered child outcomes, retained-root reuse, child isolation and cleanup |
| Existing FastAPI cancellation lane | Unresolved work blocks reuse; provider/runtime cleanup settles without a late successful commit |
| Interpreter/deletion lifecycle test | Strict invocation teardown and provider-confirmed Sandbox absence |

Use the existing receipt owners. If a required attachment parity case is not covered by an existing live entry point, extend the closest existing case without adding a new canary subsystem. [R27](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/docs/testing/daytona-cleanup-acceptance.md) [R29](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/tests/live/backend/test_daytona_deletion_lifecycle.py) [R36](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/scripts/live_daytona_verify.py) [R37](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/scripts/live_recursive_batch_canary.py)

### 10.4 What success means

A green suite must establish the new behavioral assertions, not merely preserve the old aggregate coverage percentage. Required outcomes are:

- Attachment representation, byte handling, context convenience, and mount verification match across both backends.
- Verified attachment accesses are delivered from the actual invocation exactly once without changing the retained template.
- Scratch deletion does not replay automatically; failure retains cleanup ownership.
- A call ID/lease receives at most one accepted result before or after consumption.
- Existing no-replay, sealing, single-flight, async bridge, deferred shutdown, strict cleanup retry, and child ownership tests still pass.
- Current-candidate live receipts establish the full documented gate, or outstanding lanes remain explicitly pending.

A code completion decision and an operational/release certification decision remain separate.

## 11. Things intentionally not added to this plan

Do not infer that the following are missing merely because the old plans discuss alternatives:

- A Daytona native-context execution engine: deliberately deferred until a separate parity experiment justifies replacement.
- A generic provider registry, new interpreter manager, or second planner: not required.
- A new memory or Session persistence system: outside this program.
- Deeper recursion, another child scheduler, or restoring older all-or-nothing batch semantics: current bounded child policy is owned elsewhere and must not be silently changed.
- A broad `interpreter.py`/`runtime.py` split: removing real defects and stale active guidance is more valuable than moving the same state among files.
- A new dependency, public schema, permanent compatibility feature flag, or performance promise: none is necessary for the identified work.

The archived decision record explicitly favors a small native-DSPy boundary over these alternatives. Current architecture records the actual child and persistence policies; those take precedence over earlier historical sketches. [R03](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/ARCHITECTURE.md) [R04](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/AGENTS.md) [R11](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/codex-cloud/DECISION-RECORD.md)

## 12. Final assessment

**The selected PR1-PR3 structural changes have been delivered at the audited SHA. The broader goal remains one simple, correct Daytona drop-in for native DSPy. PR completion alone does not certify full behavioral parity or the simplest possible implementation.**

The strongest improvements are the direct typed execution contract, fresh invocation ownership, sealed bindings, explicit broker owner, and retained cleanup responsibility. Preserve those proven boundaries. Continue to question incidental compatibility code and duplicated behavior, but require a supported-use check before deletion and do not substitute another framework for the current one.

The highest-value next change is to make prepared attachments behave the same in actual Sandbox execution as in the tests that currently define their contract. The deletion replay and result-ready duplicate window are separate small fixes. After those changes, apply the simplicity gate, finish the already-documented live gates, and maintain this as one active plan with an archive index instead of allowing historical PR checkboxes to become parallel obligations.

No repository patch is attached to this document: the review environment could not validate a full-checkout implementation. The file is a complete handoff specification with observed evidence, explicit change boundaries, regression requirements, validation commands, and immutable source references.

## 13. Reference register

All repository file links below are pinned to the audited SHA. Pull-request and status pages are external records that can gain later comments/status updates; claims about them reflect the audit date. The original baseline is retained only for comparison and provenance.

- **[R01]** [Audited main commit and cleanup follow-up #579](https://github.com/Qredence/fleet-rlm/commit/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536)
- **[R02]** [Change comparison from the original plan baseline to the audited main](https://github.com/Qredence/fleet-rlm/compare/c3f3d536242c7663cfeecdfbf63670b51630e9aa...e45bff132e5b2fdfe14ff81f7cc66ac89aedd536)
- **[R03]** [Current architecture and ownership boundaries](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/ARCHITECTURE.md)
- **[R04]** [Repository agent rules, scope, and validation requirements](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/AGENTS.md)
- **[R05]** [Dependency baseline](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/pyproject.toml)
- **[R06]** [codex-cloud archive inventory](https://github.com/Qredence/fleet-rlm/tree/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/codex-cloud)
- **[R07]** [Archive README and historical execution instructions](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/codex-cloud/README.md)
- **[R08]** [Archived v2 implementation plan](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/codex-cloud/IMPLEMENTATION-PLAN.md)
- **[R09]** [Archived v2 requirements document](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/codex-cloud/PRD-and-implementation-plan.md)
- **[R10]** [Archived original requirements document, distinct from v2](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/codex-cloud/PRD-and-implementation-plan%20%281%29.md)
- **[R11]** [Archived decision record](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/codex-cloud/DECISION-RECORD.md)
- **[R12]** [Archived baseline findings](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/codex-cloud/BASELINE-AND-EVIDENCE.md)
- **[R13]** [Archived implementation checklist](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/codex-cloud/IMPLEMENTATION-CHECKLIST.md)
- **[R14]** [Current interpreter: typed backend, generated loader, bindings, and finalization](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/src/fleet_rlm/daytona/interpreter.py)
- **[R15]** [Current broker: embedded server, result protocol, and retained cleanup handles](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/src/fleet_rlm/daytona/broker.py)
- **[R16]** [Current runtime: shutdown dispatch, resource leases, and provider ownership](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/src/fleet_rlm/daytona/runtime.py)
- **[R17]** [Current native DSPy builder, capsule serializer, and offline materializer](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/src/fleet_rlm/rlm/program.py)
- **[R18]** [Current root invocation factory and retained-template ownership](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/src/fleet_rlm/rlm/execution.py)
- **[R19]** [Current trace assembly and attachment-access drain](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/src/fleet_rlm/rlm/events.py)
- **[R20]** [Current recursive child invocation and output/scratch configuration](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/src/fleet_rlm/rlm/recursion.py)
- **[R21]** [Existing program-input tests, including in-process attachment round trip](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/tests/unit/backend/rlm/test_program_inputs.py)
- **[R22]** [Existing broker tests and embedded-server fixture](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/tests/unit/backend/daytona/test_broker.py)
- **[R23]** [Existing adapter regression tests](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/tests/unit/backend/daytona/test_daytona_adapter.py)
- **[R24]** [Existing sandbox namespace and broker-configuration tests](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/tests/unit/backend/daytona/test_sandbox_variable_binding.py)
- **[R25]** [Existing native DSPy lifecycle and cancellation contracts](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/tests/unit/backend/rlm/test_dspy_compat_seam.py)
- **[R26]** [Existing FastAPI/native-DSPy vertical-slice contract location](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/tests/contracts/backend/test_native_dspy_fastapi_vertical_slice.py)
- **[R27]** [Current cleanup acceptance record: local evidence and pending live gates](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/docs/testing/daytona-cleanup-acceptance.md)
- **[R28]** [PR 3 merged record and exact limits of its live validation](https://github.com/Qredence/fleet-rlm/pull/578)
- **[R29]** [Existing live interpreter/deletion canary source and receipt fields](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/tests/live/backend/test_daytona_deletion_lifecycle.py)
- **[R30]** [Current commit status endpoint, observed during this audit](https://api.github.com/repos/Qredence/fleet-rlm/commits/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/status)
- **[R31]** [Current documented backend source layout](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/docs/reference/source-layout.md)
- **[R32]** [PR 1 merge record](https://github.com/Qredence/fleet-rlm/pull/576)
- **[R33]** [PR 2 merge record, including deferred-shutdown correction](https://github.com/Qredence/fleet-rlm/pull/577)
- **[R34]** [Cleanup ownership follow-up merge record](https://github.com/Qredence/fleet-rlm/pull/579)
- **[R35]** [Existing runtime regression suite](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/tests/unit/backend/daytona/test_runtime.py)
- **[R36]** [Existing native semantic/durability verification entry point](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/scripts/live_daytona_verify.py)
- **[R37]** [Existing recursive live canary entry point](https://github.com/Qredence/fleet-rlm/blob/e45bff132e5b2fdfe14ff81f7cc66ac89aedd536/scripts/live_recursive_batch_canary.py)
