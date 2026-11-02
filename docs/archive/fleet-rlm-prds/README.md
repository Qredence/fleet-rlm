# Fleet-RLM architecture simplification - revised PRDs A-G

**Revision:** 2, 29 October 2026. **Status:** proposed implementation requirements, with review amendments applied directly to every PRD. **Declared source baseline:** [`61bea5095dbbc992db2aeb8049a05e079b0bda10`](https://github.com/Qredence/fleet-rlm/commit/61bea5095dbbc992db2aeb8049a05e079b0bda10).

This ZIP replaces the earlier package whose individual PRDs were unchanged and whose recommendations lived only in a separate review note. Use the seven revised PRDs below as the implementation instructions. No application code was modified, no repository was newly audited, and no application tests or provider jobs were run to produce this document revision.

## Documents and implementation order

| Slice | Document | Intended result | Priority and dependencies |
|---|---|---|---|
| A | [Native DSPy integration](A-native-dspy-integration.md) | Native-first adapter with evidence-gated compatibility; preserved batch context and accounting | High; independent context/batch fixes, recorded gateway corpus before adapter deletion |
| B | [Canonical turn result](B-canonical-turn-result.md) | One committed completion for storage, live delivery and replay | High; existing idempotency readback first; no new recovery service |
| C | [FastAPI contracts](C-fastapi-contracts.md) | Typed ordinary responses, validated SSE JSON and usable native documentation | B fixes terminal shape; ordinary HTTP work can prepare independently; no mandatory custom SSE extension |
| D | [Optimizer ownership](D-optimizer-ownership.md) | Worker owns progress, API owns process supervision | High; required Store failures are distinct from optional telemetry |
| E | [Native rollout state](E-native-rollout-state.md) | Fresh engines receive isolated native state without temporary candidate files | Native parity/copy spike before deletion; can proceed independently of A rollout |
| F | [Cleanup and publication](F-cleanup-publication.md) | Four independent stages: F0 fence, F1 restart durability, F2 publication, F3 captured logical view | F0 is immediate; F1 requires ownership mapping; F2 requires provider proof; F3 follows B and F2 |
| G | [Optional no-change completion](G-optional-no-change-completion.md) | Proven unchanged search outcome without paired validation or candidate | Optional after benefit decision, D/E evidence and coordinated existing clients; never blocks A-F |

Recommended delivery sequence:

1. **F0 and A**: immediate unresolved-root fence plus native adapter/context characterization in separate patches. Adapter deletion waits for its compatibility gate.
2. **B, then C**: stabilize the canonical result before enforcing terminal schemas. Type ordinary HTTP responses without speculative future states.
3. **D and E**: remove duplicate progress ownership and incidental state-transport files in independent changes; D can be prepared earlier when owners do not conflict.
4. **F1, F2, F3**: separately prove restart ownership, choose one publication protocol, then hand a captured logical view to B. Do not merge F1/F2 into a broad database/provider redesign.
5. **G only after its go decision**: measured avoided validation must justify the finalizer/report/API/TUI changes; defer it otherwise.

These are logical slices, not a requirement for exactly seven commits. Avoid simultaneous conflicting edits to finalization, contracts or schema generation without an agreed owner.

### F stage mapping

The original PRD used F1-F4. This revision follows the review's F0-F3 notation:

| Original stage | Revised stage | Scope |
|---|---|---|
| F1 | F0 | Same-process reuse fence and truthful cleanup |
| F2 | F1 | Minimum durable ownership/restart reconciliation |
| F3 | F2 | Full preflight and one qualified publication authority |
| F4 | F3 | One logical artifact view passed into the canonical result |

F0 does not claim restart safety. F3 does not claim immutable bytes. B does not wait for F3 to eliminate its duplicate result assembly. A committed publication and a confirmed provider cleanup remain different facts.

## Evidence and baseline

This revision is grounded in the supplied `fleet-rlm-prds(1).zip` and [the review decisions](references/PRD-REVIEW-DECISIONS.md). The original eight Markdown files were read in full and matched to their entries in that ZIP before editing. The review is retained unchanged as **historical rationale**, not a second set of pending instructions; [CHANGELOG](CHANGELOG.md) records where its decisions were applied.

All repository/framework links and baseline behavior claims are inherited from those sources. This packaging task did not fetch a new GitHub HEAD, rerun historical probes, revalidate external URLs or execute the implementation snippets. Reconcile the actual target checkout before coding. The original pack declares DSPy 3.4.1, FastAPI 0.162.0, GEPA 0.1.4 and Pydantic 2.13.5; verify the implementation lockfile rather than assuming later compatibility.

Evidence labels:

| Label | Meaning in this revised pack |
|---|---|
| **current-source (input PRD)** | Static finding reported by the source PRD at its pinned baseline; not freshly verified in this revision |
| **previously-reproduced** | Historical probe/result reported by the supplied review or original PRD; not rerun here |
| **hypothesis** | Plausible mechanism requiring implementation-time inspection or a targeted test |
| **acceptance-test** | Required future test, not a passing result from this document task |
| **Proposed / sketch** | Intended contract or code shape; adapt and verify against actual locked APIs |

The original executable probe artifacts are not included in these source materials. Treat reported counts as historical and regenerate relevant regression evidence at the actual implementation SHA. All acceptance-table rows and code sketches in this pack are proposed work. Do not describe them as implemented or tested merely because document validation passes.

## Source reconciliation retained from the original pack

| Earlier direction | Source-pack assessment of the pinned main | Treatment |
|---|---|---|
| Replace Flex source rewriting | Native constructor-default integration is already present | Preserve it and its compatibility tests; do not implement it again |
| Merge signatures into contracts | Earlier recommendation was withdrawn; `engine/signatures.py` remains | Keep native task declarations in that coherent module |
| Remove harness, flatten Store/setup/snapshots, move optimizer helpers | Source pack reports the consolidation already completed | No repeated structural relocation |
| Reduce 70 to 61 Python files | Source pack reports the target attained | No additional file-count quota |
| Retain custom adapter pending evidence | Current adapter behavior remains a compatibility question | A supplies native fixture/gateway evidence and an explicit removal decision |
| Compute component diff once | Source pack reports candidate-only membership traversal | G, if approved, corrects membership and uses conservative complete-state proof |
| Defer no-change completion | Current lifecycle still validates unchanged candidates | G remains optional and spans all actual consumers |
| Defer durable cleanup/publication | Unresolved roots and partial child-copy paths remain concerns | F stages guarantees explicitly and minimally |

The older branch plan referenced `01072cf467a3e799236090f6f56e375c1cf8873c` and `2caa5e00d0fcb4f35fafde1e37167cfb9453b056`. Those are historical baselines, not current implementation coordinates for this pack.

## Architectural contract and scope caps

DSPy owns signatures, predictors/modules, native state application, reasoning, evaluation execution and optimization search. FastAPI owns application lifecycle, dependencies, HTTP validation and SSE framing. Fleet owns only its application-specific Daytona bridge, aggregate budgets, durable transactions/leases, reviewed replay preparation, publication and explicit installation policy.

Before adding an abstraction, state the native/existing primitive considered, the concrete requirement it cannot satisfy, the source/test evidence and the smallest remaining glue. Keep the existing package organization. No new manager, registry, worker framework, DTO hierarchy, serializer, general recovery service, equality registry or publication subsystem is preauthorized by a failure checklist.

Preserve route `direct`/`flex`/`rlm` versus execution mode `hybrid`/`flex`/`rlm`, strict-mode triage bypass, typed decisions, isolated children, success-only child publication, gold separation and holdout exclusion from search. Preserve existing deadline policy, including `0` disabling only Fleet's deadline. Keep native GEPA and Evaluate, current paired validation for changed/unproven candidates and explicit installation approval. No wholesale `asyncify`/`streamify` substitution without proof of cancellation/decision compatibility.

Correctness can justify a small new persistent fact; it does not justify unbounded infrastructure. Record code removed as distinct from code merely moved. A low file count is not a substitute for one owner per state transition.

## Contract-first implementation gates

Before modifying a slice, freeze the relevant successful/error HTTP bodies, live/replayed turn payloads, historical rows, optimizer run records, native DSPy state and artifact listings. Reuse existing test owners. These fixtures define supported behavior; update them deliberately when the PRD calls for a documented change.

Inspect the real `AGENTS.md`, working tree, dependency lock and focused producer/consumer paths. Preserve unrelated edits. Follow the repository's actual scanning, tooling and authorization rules; do not pretend an unavailable tool ran. Use `uv`, current Makefile commands and focused offline regressions, then required `make check`. API changes require `make api-sync`/`make api-check`; documentation changes require the applicable documentation/link checks and `git diff --check`.

Exercise applicable offline direct, primary-RLM, Flex and managed-evaluation paths after each slice. Ordinary CI must not depend on paid models, live Daytona or production databases. Separately authorized provider canaries establish facts that fakes cannot, especially adapter gateway compatibility and Volume publication semantics.

Migration and rollback must cover records already written under the new code. Do not drop cleanup intents, expose staging, synthesize nonexistent candidates or make historical results unreadable to undo a code patch. Prefer additive data only when necessary and test old/new readers.

## Acceptance report

Each implementation PR records:

| Item | Required evidence |
|---|---|
| Coordinates | Exact start/final SHA, lockfile versions, working-tree scope |
| Native reuse | Primitive/API retained; any proven insufficiency requiring glue |
| Simplification | Modules/functions/lines and ownership paths removed versus introduced; distinguish deletion from relocation |
| Contract/data changes | Added models/fields/migrations, wire/status changes and old/new reader behavior |
| Correctness | Requirement/test IDs, failure-injection artifacts, focused and full gate results |
| End-to-end coverage | Applicable direct/RLM/Flex/evaluation boundary tests; offline versus live separated |
| Measurement | Observed deltas only; unknown provider costs/latency clearly unknown |
| Rollback | Reader compatibility, durable-fence preservation and staged-output visibility |
| Remaining gaps | Unresolved compatibility/provider gates and exact deferred guarantees |

If a PR adds substantially more glue than it removes, justify it with the smallest correctness requirement or reduce scope. Passing documentation checks is not runtime acceptance.

## Package contents and validation

Seven revised PRDs, this README, [CHANGELOG](CHANGELOG.md), and the unchanged review source under `references/`. No duplicate original PRDs, generated code, application patch or repository checkout is bundled.

Package verification checks that all eight original active documents were revised, every PRD retains its ten main sections, local links resolve, code fences balance, requirements/test IDs remain consistent, F uses F0-F3, and the archive extracts intact. External links are preserved, not newly verified. The supplied original archive remains untouched.

## Common source references

- [Pinned architecture](https://github.com/Qredence/fleet-rlm/blob/61bea5095dbbc992db2aeb8049a05e079b0bda10/ARCHITECTURE.md)
- [Repository agreements](https://github.com/Qredence/fleet-rlm/blob/61bea5095dbbc992db2aeb8049a05e079b0bda10/AGENTS.md)
- [Dependency declarations](https://github.com/Qredence/fleet-rlm/blob/61bea5095dbbc992db2aeb8049a05e079b0bda10/pyproject.toml) and [lockfile](https://github.com/Qredence/fleet-rlm/blob/61bea5095dbbc992db2aeb8049a05e079b0bda10/uv.lock)
- [Review source used for this revision](references/PRD-REVIEW-DECISIONS.md)
