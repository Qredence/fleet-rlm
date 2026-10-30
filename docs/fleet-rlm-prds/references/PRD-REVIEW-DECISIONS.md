# Fleet-RLM PRDs A–G — Critical review and revision decisions

**Baseline:** supplied `fleet-rlm-prds(1).zip`; pinned repository baseline declared by those PRDs: `61bea5095dbbc992db2aeb8049a05e079b0bda10`. **Status:** editorial/architectural review, not implementation or a new repository verification. Evaluate against the actual implementation SHA before coding.

## Decision

Adopt the new A–G pack in preference to the earlier broad architecture review. Amend the PRDs as below before implementation. Do not add framework layers just to satisfy a failure-mode checklist. Keep the seven slice identifiers for continuity, but divide PRD F into independent delivery stages.

## Global changes required

1. **Evidence hierarchy:** label each claim `current-source`, `previously-reproduced`, `hypothesis`, or `acceptance-test`, with test artifacts for previously reproduced claims. The ZIP currently labels historical results, which is good; implementation should regenerate relevant results at its actual SHA.
2. **Success criterion:** measure *net removed custom code and ownership paths*, not only passing tests or module count. Record new models, records, fields, cross-layer imports, and migrations introduced against code eliminated.
3. **Scope caps:** maintain present package organization, native DSPy state and FastAPI SSE. Any new manager/registry/worker/DTO layer requires an explicit upstream/native insufficiency demonstration and approval.
4. **Make reversibility real:** per-PRD rollout/rollback instructions must describe whether data written under the new contract will remain readable after code rollback. Version new durable records only when necessary, and test old/new readers.
5. **Contract-first:** freeze representative successful and failure responses, current/replayed turn results, optimizer run records, native DSPy state, and artifact listings before edits. These fixtures define compatibility.
6. **Quality gate:** the use case remains an end-to-end task. After each slice, exercise at least one direct, primary RLM, Flex, and managed evaluation path in offline boundary tests, as appropriate. No improvement claim without actual measured deltas.

## A — Native DSPy integration: accept with two conditions

**Keep:** native-first adapter, literal `</think>` regression fixtures, no custom corrective retry, per-item independent context copy, `timeout=0` for prepaid batches if native speculative dispatch is observed at the pinned version.

**Revise:** (1) Benchmark a recorded gateway fixture corpus before deciding to delete the compatibility adapter. Compare valid-field preservation, parse failures, fallback calls, latency and actual metered requests, by route; native ChatAdapter's JSONAdapter fallback can itself trigger a second *legitimate* LM request. (2) Make a narrow owner for adapter selection under existing engine context, rather than introducing global configuration; test the changed native fallback's relation to the aggregate host budget. (3) Do not claim `timeout=0` solves all duplicate requests: wrapper/provider retries and cancellations remain separate sources.

**Gate:** native output must be at least as correct on real gateway samples; known valid native fixtures must all pass. If not, retain a documented native-first compatibility shim with a clear sunset criterion.

## B — Canonical turn result: accept and constrain

**Keep:** one result assembled once, committed once, emitted without rebuilding; explicit precommit, confirmed-commit, and uncertain-commit branches.

**Revise:** avoid creating a general transaction-recovery subsystem as part of a presentation/refactor change. First use existing idempotency readback and terminal-row lookup; require a separate, focused failure-mode addition only if a test demonstrates the existing facilities cannot recover ambiguous acknowledgment. Define exactly one canonical field inventory and the historical normalization boundary. Distinguish success in storage from success in delivery without inventing a new status hierarchy.

**Gate:** in fault injection after commit, durable result remains successful; no duplicate DSPy execution; live and replay semantics match; old rows remain readable.

## C — FastAPI contracts: substantially simplify the documentation strategy

**Keep:** actual Pydantic models for stable HTTP responses, strict boundary validation, explicit wire tests, and native SSE transport.

**Revise:** do not make an `x-fleet-item-schema` extension plus generator normalization a mandatory centerpiece. It is nonstandard and many clients ignore it, increasing code without guaranteeing useful typing. Prefer the simplest specification supported by pinned FastAPI and actual consumers: a valid `text/event-stream` response schema, concrete named event payload models defined in `api/schemas.py`, and validated example frames plus separate contract tests. If a machine-readable event-union extension materially benefits an identified client, add it **only with a consumer test**, and keep the OpenAPI generator change minimal. Do not change OpenAPI versions solely to express SSE item schemas. Avoid duplicating the same constraint in unrelated `mode=before` validators and JSON Schema unless tests establish necessity; verify Python `bool` and integer union behavior directly.

**Gate:** runtime and documentation agree, known error responses are typed/sanitized, supported old clients still parse streams, and generated OpenAPI validates against its declared version.

## D — Optimizer ownership: accept with failure-state simplification

**Keep:** worker owns durable search progress, API owns process supervision, stdout always drained, terminal exit arbitration and stop callbacks preserved.

**Revise:** a standalone `reporter failure latch` is acceptable as a field on the existing reporter, not another state service. Distinguish required Store updates from best-effort MLflow/telemetry; do not abort search on failures that the product policy treats as optional observations. Specify only one stage/progress snapshot writer, but do not claim this means only one actor writes the whole run record: API still owns process and cancellation intents. Do not rely solely on GEPA callbacks for durability: verify failure after compile and before finalization.

**Gate:** out-of-order worker stdout cannot regress persisted counters; failure or lost ownership cannot publish success; a stage-only update cannot erase prior counters.

## E — Native rollout state: accept; likely high code deletion value

**Keep:** fresh execution engine, native `dump_state`/`load_state`, deep-copied state per rollout, separate durable JSON publication, existing replay/cleanup boundaries.

**Revise:** first perform a strict parity spike with real pinned native modules and Flex source. Do not assume `deepcopy` is always safe for arbitrary state; preserve supported native state loading and stop if type parity fails. The wrapper-versus-bare program state distinction is correct and must remain explicit. Avoid a new generic cloning abstraction; keep the operation in the existing isolated factory.

**Gate:** old and new paths agree on instructions, demos, type-safe fields, Flex source and offline outcome; zero candidate tempfile lifecycle in repeated rollouts; no cross-run mutation.

## F — Cleanup/publication: critical but MUST be broken into smaller milestones

**F0 immediate fence:** retain unresolved remote identity in the current manager; block new root provisioning/writable reuse after unconfirmed stop. Request root interruption promptly, attempt all required cleanup, and report honestly. This is the first safety fix and can be independently merged.

**F1 restart durability:** map existing Store session/lease fields and Daytona allocation identifiers first. Add *one* minimal durable cleanup/ownership record only if existing data cannot identify uncertainty across process restart. Test crash at allocation-before-ack, stop-before-ack and takeover. Avoid a new general reconciler service; perform bounded reconciliation on admission or existing cleanup paths.

**F2 artifact publication:** start by preflighting all selected files and quotas before copying. Then prove publication semantics of actual mounted Daytona Volume before designing staging. If same-filesystem directory rename is atomic and visible consistently to all relevant readers, use it. Otherwise choose one authoritative committed manifest and teach **every** public listing/evidence path to respect it; do not combine two competing publication protocols. Never call a multi-file copy transactionally atomic.

**F3 artifact snapshot:** after F2, collect one immutable *logical* publication view for B. Do not promise immutable bytes unless blobs are versioned/hashed and remote writes are fenced. Keep cleanup uncertainty independent from committed publication success.

**Gate:** no new mutable root after uncertain cleanup; crash/retry cannot expose half a child batch as completed; every published artifact has a host receipt. Prefer separate PRs for F0, F1, F2, F3 with measurable deletion and added persistence cost.

## G — No-change completion: simplify, optional last

**Keep:** never treat no instruction diff alone as proof of unchanged behavior; include component add/remove, native state and relevant configuration identity; no promotable artifact; managed/UI/CLI terminal behavior consistent.

**Revise:** do not construct a general tri-state equality framework if conservative native value equality and an explicit `unknown` fallback cover observed state. Cache a pre-search native snapshot and use existing program fingerprint contracts. Avoid adding a separate status enum, report hierarchy or approval worker path; additive `outcome=no_change` on completed runs is preferable after verifying existing readers. If equality is not provable, use existing full validation. No-change does not show new evidence of quality improvement: display that plainly.

**Gate:** true unchanged: zero paired final-validation rollouts and no candidate manifest; changed/unknown: existing paired evaluation; cancel, missing progress, and stale incumbent cannot become no-change. Keep this optional because it touches the broadest set of consumers for an efficiency optimization.

## Suggested sequence

1. **F0** unresolved-root reuse fence (release-critical) and **A** native adapter/context tests in parallel.
2. **B** canonical result owner, followed by **C** ordinary HTTP schemas and only the minimal SSE contract consumers require.
3. **D** progress single-writer and **E** in-memory rollout state, independently with no new abstraction layer.
4. **F1–F3** durable ownership and publication, separated into small PRs following provider-semantic verification.
5. **G** only after observed unchanged-search frequency justifies lifecycle changes.

Avoid sequencing C before B's result is stable; avoid letting G block F; avoid combining F1 and F2 in a single database/provider migration.

## Acceptance report for each PR

Record baseline/final SHA; changed files; modules/functions/lines removed vs introduced; native API reused; new persistent fields; public wire changes; tests added (including failure-mode probes); check results; rollback compatibility; and a concrete behavioral outcome. If the PR adds substantially more glue than it removes, require a correctness proof or reduce scope.

## Recommendation

**Use this updated pack, not the earlier general plan.** The main edits are to narrow C, stage F, and make A/E conditional on measured compatibility. B/D are already strong. G is useful but optional. Keep `engine/signatures.py`, existing native RLM/Flex/GEPA/Evaluate architecture, and the current FastAPI application structure.
