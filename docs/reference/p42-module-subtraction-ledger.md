# P42 module-subtraction ledger

**Status:** active P53 close-out ledger. P42–P52 implementation is present in
this checkout. Rows record the phase commits and current owners; they are not
evidence for an unrun deterministic or credentialed live lane. P53 certification closes when the P35-E gate verifies the current clean candidate (ignored `.fleet-evidence/` evidence).
**Baseline:** the sealed P36/P41 work, assessed against `plans/PLANS.md` P44–P53.
**Replacement PRs:** the phase commits are recorded in repository history.

## Current implementation status

This ledger began as the P42 pre-change inventory. Later phase commits realized
its target ownership; the rows below are kept as an auditable responsibility map:

- **P42–P45:** Session-state contract, complete committed History, and resident
  Root RLM/interpreter runtime are implemented.
- **P46–P47:** the native DSPy kernel contraction and isolated child snapshot
  path are implemented.
- **P48:** Daytona broker, root/child lifecycle, lease cleanup, and Workspace
  Agent ownership are implemented behind the current Daytona runtime facade.
- **P49:** preparation, TurnRuntime orchestration, lifecycle finalization, and
  current-owner settlement paths are implemented.
- **P50:** provider-neutral Workspace, Projects, Memory, Attachments, URL,
  storage, composition, and outbox owners are implemented. The legacy
  `fleet_rlm.files` package and obsolete Daytona domain modules are deleted.
- **P51–P52:** configuration/composition/observability simplification and the
  behavior-oriented Session-state, dependency, security, recovery, concurrency,
  and child-isolation tests are implemented.
- **P53:** final deterministic/live integration and public compatibility evidence
  remain open. L-28 and L-45 have been reassessed: their independent lifecycle,
  policy, and persistence owners remain KEEP/DEEPEN; no wholesale merge is
  justified. No certification claim is made for an unrun lane or for receipts
  from another candidate SHA.

This is the P42.5 companion to the [P42 session-state behavior
freeze](p42-session-state-behavior-freeze.md). It records the proposed
subtraction and the realized P48–P52 ownership so later changes remain
reviewable. It does not change the sealed [P41 behavior freeze](behavior-freeze.md)
or the public HTTP contract, OpenAPI, generated artifacts, and Runtime Event
vocabulary.

## How to read this ledger

A ledger row records the starting source responsibility, rather than treating a
filename as the contract. Where several current paths implement one indivisible
responsibility, they deliberately appear in one row; the **current paths** list
is exhaustive for that row and the disposition applies to each listed path.
A path may occur again in a later-phase row when a later phase deepens a prior
move. That is intentional sequencing, not permission to delete it twice.

- **Callers** are the current production consumers or composition entry points.
  They exclude tests and package re-exports. They are qualified enough to find
  the static import/call boundary; an entry such as “turn preparation” names the
  concrete module in the row.
- **Real production adapters** means a concrete path that talks to DSPy,
  Daytona, SQLAlchemy/Alembic, FastAPI, or the installed Workspace Agent. A
  pure DTO/policy has **none**; the deterministic test composition is never
  counted as a production adapter.
- **KEEP** retains the named ownership; **DEEPEN** puts the same behavior behind
  a smaller, deeper owner; **MERGE** absorbs it into the target after parity;
  **MOVE** changes its home without an algorithm change; and **DELETE** removes
  the old path only after callers, tests, and documentation use the target.
  “DELETE” is always *after parity*, never a P42 action.

Every destructive row remains gated by the applicable evidence in the
[P36 ownership and deletion contract](../how-to-guides/p36-ownership-deletion-inventory.md):
P37 for complete Turn ownership, P38 for native DSPy/result/event seams, P39
for children, and P40 for Workspace/Project hosts. P43 must also prove the
pinned DSPy/Daytona History and reuse assumptions before P44–P51 begins.

## Target-module register

The following is the complete P44–P51 target set. A target with no current path
is an ADD-only destination; its provenance is listed in the relevant row.

| Phase | Target module or owned surface | Ledger rows |
| --- | --- | --- |
| P44 | `sessions/history.py`; existing History Tool, checkpoint, Signature and context seams | L-01–L-06 |
| P45 | `rlm/session_runtime.py`; compatible runtime acquisition and current-Turn binding | L-07–L-09 |
| P46 | `rlm/program.py`, `runtime.py`, `result.py`, `events.py`, `_dspy_compat.py`, and `optimization/routing.py` | L-10–L-17 |
| P47 | `rlm/recursion.py` and the disposable Daytona child boundary | L-18–L-19 |
| P48 | `daytona/runtime.py`, `interpreter.py`, `platform.py`, `provisioning.py`, `broker.py`, `_lease.py`, `_cleanup.py`, and `daytona/workspace_agent/` | L-20–L-26 |
| P49 | `chat/preparation.py`, `chat/turn_runtime.py`, `chat/models.py`, and `chat/committed_events.py` | L-27–L-31 |
| P50 | `fleet_rlm/paths.py`; `workspace/{models,paths,storage,workspace,projects,memory,url}.py`, `attachments/`, and `persistence/repositories/outbox.py` | L-32–L-39 |
| P51 | `config/{settings,loader,policy}.py`, `composition/{live,testing}.py`, `observability/{diagnostics,tracing,dspy_callbacks,mlflow,posthog}.py`, `optimization/daytona.py`, and narrowed persistence/effect seams | L-40–L-45 |

The public `api/`, `artifacts/`, `skills/`, `rlm/events.py`, and durable
repository contracts are intentionally not deletion targets. The former Python
canonical-event shadow layer was an X1 wire-or-delete candidate and is deleted;
the public SSE wire remains governed by `FleetUIMessageChunk`. This ledger makes
no separate X1 certification claim.

## P44 — durable committed Session History

| ID | Current path and responsibility | Callers; real production adapters | Invariants | Target module; disposition; planned replacement |
| --- | --- | --- | --- | --- |
| L-01 | `sessions/committed_turn.py`, `sessions/models.py` — validate and model the durable committed Turn and Session history records. | Callers: `persistence/repositories/{turns,session_catalog,run_codec}.py`, `chat/{run_lifecycle,turn_detail_policy}.py`, API UI projection. Adapter: SQLAlchemy repositories. | Only committed user-facing request/answer data may become conversation; failed, cancelled, and uncommitted output never advances it; workspace-and-Session isolation holds. | `sessions/history.py`; **DEEPEN**. **P44 implemented; proof: Session History contract lanes.** |
| L-02 | `sessions/catalog.py`, `persistence/repositories/{session_catalog,turns,run_claim_decisions,run_final_state,run_liveness}.py` — load the claimed checkpoint and enforce durable claim/CAS state. | Callers: `api/dependencies.py`, `chat/{run_lifecycle,preparation,turn_runtime}.py`. Adapter: SQLAlchemy transaction and repository implementations. | Claim → immutable snapshot → RLM → CAS commit order; replay returns the existing result without another RLM call or History append. | Existing repositories plus `sessions/history.py`; **KEEP/DEEPEN** (not a repository merger). **P44/P49 implemented; proof: lifecycle and History contract lanes.** |
| L-03 | `sessions/history_tools.py` — exposes `read_session_history` through `SessionHistoryToolHost`. | Caller: `chat/capability_preparation.py`. Adapter: the same SQLAlchemy-backed Session snapshot used by L-02. | Fixed 256 KiB aggregate UTF-8 budget, whole-message omission metadata, authorization, and result schema remain unchanged. | `sessions/history_tools.py` bound to the P44 checkpoint; **KEEP**. **P44 implemented; proof: history-tool contract lanes.** |
| L-04 | `chat/session_context.py` — creates bounded session metadata and six recent previews. | Callers: `chat/preparation.py`, `rlm/program.py`. Adapter: none; it consumes the claimed durable model. | Context stays bounded navigation metadata; it is not a transcript and does not replace complete `dspy.History`. | `chat/session_context.py`; **KEEP/DEEPEN**. **P44/P49 implemented; proof: Session-state contract lanes.** |
| L-05 | `rlm/program.py` — declares named RLM fields and serializes request, context, Skill cards, and Attachments. | Callers: `rlm/{runtime,recursion}.py`, `chat/preparation.py`, `skills/signatures.py`. Adapter: native `dspy.RLM.acall(...)` named-input boundary. | Current request stays separate from prior History; every Signature has stable common fields; no transcript is expanded into public events. | `rlm/program.py`; **MERGE**. **P44/P46 implemented; proof: native-kernel and History contract lanes.** |
| L-06 | `skills/signatures.py` and `skills/catalog.py` — declares and validates the bundled `DataAnalysisSignature` binding. | Callers: `skills/resolver.py`, `rlm/program.py`, and the fixed catalog. Adapter: DSPy Signature construction only. | All custom Signatures require `request`, `history`, `session_context`, `skill_cards`, `attachments`, and `answer`; structured result data remains durable output, not History. | `skills/signatures.py` and catalog binding; **DEEPEN**. **P44 implemented; proof: Skill-signature and public Tool contract lanes.** |

## P45 — compatible resident Root runtime

| ID | Current path and responsibility | Callers; real production adapters | Invariants | Target module; disposition; planned replacement |
| --- | --- | --- | --- | --- |
| L-07 | `rlm/{program,runtime,session_runtime}.py` — construct and reuse the compatible resident Root RLM, caller-owned interpreter, and Session runtime state. | Callers: `composition/{live,testing}.py`, `chat/{preparation,turn_runtime}.py`. Adapters: native DSPy and Daytona interpreter. | One compatible RLM/interpreter/Sandbox is reused only sequentially, after result validation and durable commit; each invocation gets fresh `REPLHistory`, budgets, request, history, and metadata. | `rlm/session_runtime.py`; **DEEPEN**. **P45 implemented; proof: Session-runtime contract and reuse lanes.** |
| L-08 | `runtime/daytona/run_environment.py` plus `daytona/{runtime,session_manager,sandbox_lease,interpreter,admission,platform}.py` — acquire, bind, reuse, and close the Root runtime. | Callers: `composition/live.py`, `runtime/daytona/run_environment.py`, `daytona/recursive_child_runtime.py`. Adapters: `LiveDaytonaPlatform`, `AsyncDaytona`, broker-backed `DaytonaCodeInterpreter`. | Full `(workspace_id, session_id)` key; one execution lane; explicit admission restoration; clean close is distinct from failed cleanup; Volume scope survives rotation. | `daytona/runtime.py` and `runtime/daytona/run_environment.py`; **DEEPEN**, not deletion in P45. **P45/P48 implemented; proof: Session-runtime and Daytona lifecycle lanes.** |
| L-09 | `chat/{run_claim,run_authority,run_ownership}.py` and `rlm/session_runtime.py` — claim validity, heartbeats, cancellation shielding, and run-scoped Tool guards. | Callers: `chat/{run_lifecycle,turn_runtime}.py`, `persistence/repositories/{turns,run_codec,run_liveness}.py`, `rlm/runtime.py`. Adapters: SQLAlchemy claim persistence; Tool calls reach the current host capability. | A retained Python Tool alias resolves current Turn authorization, never prior authorization; claim loss, cancellation, timeout, commit/authorization failure, or uncertain settlement taints the resident runtime. | `rlm/session_runtime.py` current-capability binding, then `chat/turn_runtime.py`; **MERGE** after P36 P37 parity. **P45/P49 implemented; proof: tool-authority and taint contract lanes.** |

## P46 — thin native DSPy kernel

| ID | Current path and responsibility | Callers; real production adapters | Invariants | Target module; disposition; planned replacement |
| --- | --- | --- | --- | --- |
| L-10 | `rlm/{program,result,_dspy_compat}.py` — exact DSPy version gate, RLM option mapping, Prediction normalization/result trust, trajectory and usage types. | Callers: app, CLI, composition, chat lifecycle/preparation, runner, persistence codecs, and diagnostics. Adapter: pinned native `dspy.RLM`/`dspy.Prediction`. | Fleet owns validation and sanitation but never a second RLM iteration loop or alternate Prediction constructor; `max_iters`, `max_llm_calls`, and output limits retain certified meaning. | `rlm/{program,result,_dspy_compat}.py`; **MERGE**. **P46 implemented; proof: native-kernel contract lanes.** |
| L-11 | `rlm/_dspy_compat.py` — narrow interpreter injection, output-field refresh, FinalOutput wrapping. | Callers: `daytona/{interpreter,interpreter_output}.py`, `rlm/{program,recursion,runtime}.py`. Adapter: private/public DSPy interpreter seam. | Caller-owned interpreter is never shut down by DSPy; stale Tools/output metadata are refreshed; no `_tools_registered` access is introduced. | `rlm/_dspy_compat.py`; **MOVE/DEEPEN**. **P46 implemented; proof: DSPy compatibility contract lanes.** |
| L-12 | `rlm/program.py` — constructs Root/Sub DSPy LMs, model roles, bundles, and Root RLM. | Callers: `composition/{live,testing}.py`, `rlm/{runtime,recursion}.py`, `optimization/{daytona,routing}.py`. Adapter: `dspy.LM` and native `dspy.RLM` construction. | Configuration does no provider I/O; exact role model policy, normalized IDs, secret redaction, and Root/Sub separation remain. | `rlm/program.py`; **MERGE**. **P46 implemented; proof: model-bundle and native-constructor lanes.** |
| L-13 | `rlm/program.py` — Signature schema, instructions, DTOs, and one-time input validation/serialization. | Callers: `chat/preparation.py`, `rlm/{runtime,recursion}.py`, `optimization/gepa_runner.py`. Adapter: DSPy Signature/input adaptation. | Named fields and bounded input contract stay stable; P44 `history` is canonical conversation; no Pydantic→dict→Pydantic loop survives without a semantic boundary. | `rlm/program.py`; **MERGE**. **P46 implemented; proof: Signature/input contract lanes.** |
| L-14 | `rlm/result.py` and narrow package error/sanitation seams — typed RLM outcome/failures and public-safe result/error text. | Callers: chat lifecycle/TurnRuntime, runner, interpreter, observation. Adapter: none independent; provider errors enter through Daytona/DSPy. | Invalid, oversized, secret-bearing, or malformed output never commits; public errors remain closed and bounded. | `rlm/result.py` plus `daytona/errors.py`; **MERGE**. **P46 implemented; proof: result and failure-taxonomy lanes.** |
| L-15 | `rlm/events.py`, `api/sse.py`, `daytona/interpreter.py`, and `observability/dspy_callbacks.py` — callback/interpreter/Tool observation and trajectory reconciliation. | Callers: API SSE, chat projection, runner, interpreter, Files/Skills/History Tool hosts. Adapters: interpreter observation, `dspy.Tool`, and MLflow trace hooks. | Live evidence wins; native trajectory only fills gaps; bounded Tool event views never expose raw arguments/results; Runtime Event vocabulary/order stay unchanged. | `rlm/events.py` feeding `api/sse.py`; **KEEP**. **P46 implemented; proof: observation and public-stream lanes.** |
| L-16 | `rlm/runtime.py` and `rlm/session_runtime.py` — worker-thread invocation, cancellation/deadline checks, execution spec, and integrity guard. | Callers: composition, chat, recursion, diagnostics. Adapters: `dspy.RLM.acall(interpreter, **inputs)` and Daytona interpreter. | Native call remains immediately visible; Fleet retains worker ownership, absolute deadline, cancellation, cleanup, and current Tool authorization. | `rlm/{runtime,session_runtime}.py`; **MERGE**. **P46 implemented; proof: runtime and Session-taint lanes.** |
| L-17 | `optimization/routing.py` — evaluation-only routing scenarios and scoring. | Callers: no production Turn caller; development/evaluation code imports it directly. Adapter: none in the production runtime. | No routing algorithm or production execution behavior changes. | `optimization/routing.py`; **MOVE**. **P46/P51 implemented; proof: optimization routing lanes.** |

## P47 — isolated native child recursion

| ID | Current path and responsibility | Callers; real production adapters | Invariants | Target module; disposition; planned replacement |
| --- | --- | --- | --- | --- |
| L-18 | `rlm/recursion.py` — recursive Tool policy, reservation/batch settlement, metrics, and child lease protocol. | Callers: `rlm/runtime.py`, `chat/preparation.py`, `chat/turn_runtime.py`, and execution trace projection. Adapters: child `dspy.RLM`, copied `dspy.LM`, and the Daytona child factory. | Root depth 0; native child depth 1; deeper Sub-LM fallback; Root-only ordered all-or-nothing batch; shared budget/deadline; no child Session or mutable Root state. | `rlm/recursion.py`; **MERGE**. **P47 implemented; proof: recursion policy and child-isolation lanes.** |
| L-19 | `daytona/recursive_child_runtime.py` — actual child Sandbox acquisition, late ownership, close, absence confirmation, and permit release. | Callers: `runtime/daytona/run_environment.py`, `daytona/diagnostics.py`; factory used through L-18. Adapters: `LiveDaytonaPlatform`, broker, interpreter, admission permits. | Fresh child RLM/interpreter/Sandbox and child Volume subpath; shutdown → deletion → provider absence → admission restoration; failed cleanup prevents clean success. | Public `daytona/runtime.py::open_child` with private `_lease.py`/`_cleanup.py`; **DEEPEN**. **P47/P48 implemented; proof: child cleanup and live ownership lanes.** |

## P48 — Daytona Root Session and Workspace-Agent lifecycle

| ID | Current path and responsibility | Callers; real production adapters | Invariants | Target module; disposition; planned replacement |
| --- | --- | --- | --- | --- |
| L-20 | `daytona/{runtime,admission,provisioning,platform}.py` and `runtime/daytona/run_environment.py` — root acquisition, SDK specification, admission, bindings, mounts, and retries. | Callers: `composition/live.py`, `daytona/diagnostics.py`, `daytona/recursive_child_runtime.py`, `runtime/daytona/workspace_gateway.py`. Adapters: `LiveDaytonaPlatform` and `AsyncDaytona` SDK. | Callers above Daytona do not learn permits, provisioning retries, provider bindings, mount preparation, or delete polling; root lease is reusable only while healthy. | `daytona/runtime.py` public `DaytonaRuntime.acquire_root_session`; **MERGE/DEEPEN**. **P48 implemented; proof: Daytona runtime and provider-boundary lanes.** |
| L-21 | `daytona/{sandbox_lease,lifecycle,_lease,_cleanup}.py` — owned close, purge, provider deletion and absence state. | Callers: `daytona/runtime.py`, `daytona/recursive_child_runtime.py`, `runtime/daytona/run_environment.py`. Adapters: live provider deletion/status checks. | `OPEN`, `CLOSING`, `CLOSED`, and `FAILED` remain behaviorally distinct; one close joins; all cleanup failures are re-observable; admission is restored only after required ownership settles. | `daytona/{_lease,_cleanup}.py`; **MOVE**. **P48 implemented; proof: lease and cleanup ownership lanes.** |
| L-22 | `daytona/{broker,interpreter_output}.py` — code generation, submit framing, HTTP Tool dispatch, synchronous bridge, and output decoding. | Callers: `daytona/interpreter.py`, composition, session/child managers, outbox reconciliation. Adapters: sandbox HTTP broker and DSPy synchronous interpreter interface. | Tool dispatch remains current-Turn authorized; FinalOutput extraction and bounded corrective feedback stay exact; broker shutdown precedes provider cleanup. | `daytona/broker.py` (with `interpreter_output.py` kept separate); **MERGE**. **P48 implemented.** |
| L-23 | `daytona/interpreter.py` — `DaytonaCodeInterpreter`, backend selection, execution observation and recoverable/terminal error taxonomy. | Callers: `runtime/daytona/run_environment.py`, child runtime, diagnostics, `optimization/daytona.py`, `daytona/workspace_agent/`. Adapter: Daytona Sandbox interpreter and broker. | Existing repair/no-progress behavior, error classification, caller-owned shutdown, live code/output evidence, and public sanitation remain. | `daytona/interpreter.py` under `DaytonaRuntime`; **DEEPEN**. **P48 implemented; proof: interpreter and provider-boundary lanes.** |
| L-24 | `daytona/workspace_agent/{protocol,client,runtime}.py` — installed agent protocol/source and host-side response validation. | Callers: `workspace/{workspace,storage,memory}.py`, `runtime/daytona/workspace_gateway.py`. Adapter: code running inside a mounted Daytona Sandbox. | Relative-path-only requests, symlink/nonregular-node rejection, bounded pages/bytes, checksum preconditions, and fail-closed protocol parsing stay intact. | `daytona/workspace_agent/{protocol,client,runtime}.py`; **MOVE**. **P48 implemented.** |
| L-25 | `runtime/daytona/workspace_gateway.py` and `workspace/{workspace,storage}.py` — Volume/Session Workspace adapter, I/O Sandbox gateway, orphan cleanup. | Callers: `composition/live.py`, `runtime/daytona/run_environment.py`, and `api/routes/workspace_files.py`; Workspace domain interfaces. Adapters: mounted Daytona Volume and ephemeral I/O Sandbox. | Workspace/Project writes are immediate and scope-safe; reads retain cursor/byte bounds; no caller gets a provider path; Artifact publication keeps its separate commit gate. | `workspace/storage.py` behind `daytona/workspace_agent/`; **MOVE**. **P50 implemented; proof: storage and Workspace Agent contract tests.** |
| L-26 | `daytona/{diagnostics,errors}.py`, `optimization/daytona.py`, and `observability/diagnostics.py` — disposable doctor/probe behavior, provider error mapping, and strict evaluation Sandbox lifecycle. | Callers: CLI, Daytona diagnostics, development optimization. Adapters: disposable live Daytona Sandbox and provider SDK; no Turn settlement adapter. | Diagnostics stay bounded, sanitized, explicit, and non-authoritative for Turns; strict evaluation remains disposable and provider-clean. | `daytona/errors.py` **KEEP**; `optimization/daytona.py` **MOVE**; `observability/diagnostics.py` **MOVE**. **P51 implemented; proof: doctor, diagnostics, and optimization lanes.** |

## P49 — one durable TurnRuntime

| ID | Current path and responsibility | Callers; real production adapters | Invariants | Target module; disposition; planned replacement |
| --- | --- | --- | --- | --- |
| L-27 | `chat/{turn_runtime,commands,committed_turn_events,turn_detail_policy}.py` — open stream, command input, event/replay projection, and terminal detail policy. | Callers: `api/{dependencies,routes/turns,routes/runs}.py`, composition inventory, lifecycle. Adapter: FastAPI/SSE consumes its `RuntimeEvent` stream. | Close-before-first-event, one absolute deadline, exactly one last terminal, replay determinism, and transport-neutral Runtime Events remain. | `chat/{turn_runtime,commands,committed_turn_events,turn_detail_policy}.py`; **MERGE** except thin commands. **P49 implemented; proof: TurnRuntime and public-stream contract lanes.** |
| L-28 | `chat/{run_lifecycle,run_claim,run_authority,run_ownership}.py` — durable start/settlement, transition policy, authority, heartbeat, and owned cleanup. | Callers: TurnRuntime, preparation, API, composition, persistence repositories. Adapters: SQLAlchemy commit/claim stores; Daytona cleanup. | Validate → Artifact staging → memory intents → atomic commit → snapshot → owned promotion; failure/cancel never publishes success or advances History; cleanup cannot detach. | `chat/{run_lifecycle,run_claim,run_authority,run_ownership}.py`; **KEEP/DEEPEN after P53 reassessment**: `run_lifecycle` remains the durable settlement owner, `run_claim` remains shared pure policy, `run_authority` remains the authorization value, and `run_ownership` remains the narrow heartbeat/cleanup primitive. No wholesale merge is justified; the obsolete `run_cleanup` shim is deleted. **P49 implemented; P53 reassessment recorded; proof: lifecycle settlement and cleanup lanes.** |
| L-29 | `chat/{preparation,capability_preparation,session_context,post_commit_memory}.py` — claimed input assembly, Tools/capabilities, bounded context, and post-commit Memory task. | Callers: TurnRuntime/lifecycle, `runtime/daytona/run_environment.py`, testing composition. Adapters: selected Tool hosts, Workspace Memory store, Attachment preparer. | Prepared History is checkpoint-aligned; current capabilities are authorization-scoped; Memory promotion stays owned past commit and no failed Turn promotes it. | `chat/{preparation,turn_runtime}.py`; **MERGE**. **P49 implemented; proof: preparation and Memory-promotion lanes.** |
| L-30 | `result_snapshot.py`, `snapshot_contract.py`, and `artifacts/{promotion,tools,reader}.py` — private result derivative and commit-gated candidate/Artifact handling. | Callers: run lifecycle, repositories, API Artifact route. Adapters: SQLAlchemy metadata and mounted Volume bytes. | Result snapshot is private/non-replay; Artifact bytes/metadata publish only through successful atomic commit; failed Runs expose no Artifact identity. | `chat/turn_runtime.py` owns order; Artifact modules remain authoritative; **KEEP/DEEPEN**, not deletion. **P49 implemented; proof: result, Artifact, and settlement lanes.** |
| L-31 | `runtime/{bindings,owned_effect}.py` — shared binding and joinable owned-effect primitives. | Callers: Workspace/Volume paths, Sandbox bindings repository, cleanup/post-commit paths. Adapters: none itself; underlying owners are Daytona and SQLAlchemy. | Shared machinery remains only if multiple independent owners need one cancellation-safe join/settlement contract; no pseudo-owner may report cleanup. | `runtime/{bindings,owned_effect}.py`; **KEEP/DEEPEN** after reassessment. **P49/P51 implemented; proof: owned-effect and binding lanes.** |

## P50 — distinct Workspace, Projects, Memory, Attachments, and URL domains

| ID | Current path and responsibility | Callers; real production adapters | Invariants | Target module; disposition; planned replacement |
| --- | --- | --- | --- | --- |
| L-32 | `workspace/{workspace,models,paths,storage}.py` — Session Workspace host, models, path validation, Tool construction, error/event serialization. | Callers: API Workspace files route, `runtime/daytona/run_environment.py`, capability preparation. Adapter: `DaytonaSessionWorkspaceFS`/Workspace Agent. | Seven Workspace Tools retain exact names/order/schemas; immediate durability, paged bounds, nonrecursive empty-dir deletion, checksum preconditions, and safe event views remain. | `workspace/{workspace,models,paths,storage}.py`; **MOVE/MERGE**. **P50 implemented; proof: Workspace and contract tests.** |
| L-33 | `workspace/projects.py` — explicit Project host and path policy. | Caller: `runtime/daytona/run_environment.py`. Adapter: mounted Workspace Volume through Workspace Agent. | Six Project Tools, `projects/<slug>/` scope, root browsing exception, no append, strict slug/path rules, and safe Tool event metadata remain. | `workspace/projects.py`; **MOVE**. **P50 implemented; proof: Project contract tests.** |
| L-34 | `workspace/{memory,models}.py` and `persistence/repositories/outbox.py` — Memory records, Tool host, candidates, digest, durable store, diagnostics, and reconciliation. | Callers: chat preparation/lifecycle, `runtime/daytona/run_environment.py`, persistence Memory intent repository. Adapter: mounted `memory/MEMORIES.md` Workspace Agent plus SQLAlchemy outbox. | Workspace Memory stays distinct from Session History; stable IDs/provenance/search/edit/delete/supersession/digest/malformed tolerance/current coordination limits and commit-gated promotion stay exact. | `workspace/{memory,models}.py` and `persistence/repositories/outbox.py`; **MOVE/MERGE**. **P50 implemented; proof: Memory/outbox tests and make check.** |
| L-35 | `attachments/{lifecycle,local_catalog,models,paths,errors,safety}.py` — Attachment catalog/blob lifecycle, models, path policy, and upload safety. | Callers: attachments API route, composition, preparation, repositories. Adapters: SQLAlchemy metadata plus Workspace Volume blob store. | Attachments remain Workspace-owned authorized inputs; validation, integrity checks, and candidate isolation do not become Workspace-file Tools. | `attachments/` modules; **MOVE**. **P50 implemented; proof: Attachment lifecycle/catalog tests and make check.** |
| L-36 | `paths.py`, `workspace/{paths,storage}.py` — Volume interfaces, path identity/normalization, and deterministic local mirror. | Callers: API dependencies, artifacts, composition, Daytona Workspace gateway, persistence Sandbox bindings. Adapters: production mounted Daytona Volume; `HostVolumeMirror` is testing-only. | One canonical relative-path normalization; no provider path leaks; deterministic fake remains private-test-only and does not define public behavior. | `paths.py` + `workspace/{paths,storage}.py`; test mirror to `composition/testing.py`; **MOVE/DEEPEN**. **P50 implemented; proof: storage/path tests and make check.** |
| L-37 | `workspace/url.py`, `attachments/tools.py`, and `artifacts/tools.py` — URL Tool/source store plus explicit file-domain hosts. | Callers: `runtime/daytona/run_environment.py` and deterministic composition. Adapter: public-text URL fetcher and Workspace-backed URL source store. | Tool names/schemas/event views and URL safety/time/size bounds remain; generic File host must not recreate an undifferentiated Workspace abstraction. | `workspace/url.py`, `attachments/tools.py`, and `artifacts/tools.py` explicit hosts; **MERGE/DELETE**. **P50 implemented; proof: URL/cache, Attachment/Artifact host, and make check tests.** |
| L-38 | `persistence/repositories/outbox.py` — durable intent query/write adapter. | Callers: chat lifecycle, Workspace Memory candidate promotion, Run state repositories. Adapter: SQLAlchemy. | Intent is atomically committed with the successful Turn, becomes promotable only after commit, and recovery remains idempotent. | `persistence/repositories/outbox.py`; **MERGE**. **P50 implemented; proof: outbox state-machine tests and make check.** |
| L-39 | `api/routes/workspace_files.py`, `api/dependencies.py`, and composition consumers of `workspace/` interfaces. | Callers: FastAPI router registration and TUI HTTP client. Adapters: FastAPI translation plus Workspace storage adapter. | Route URL/response schema, OpenAPI, generated TUI types, Volume tree read-only boundary, and no provider paths remain unchanged. | existing `api/routes/workspace_files.py` with the new Workspace owners; **KEEP/DEEPEN**. **P50 implemented; proof: make api-check.** |

`files/__init__.py` is therefore **DELETE after parity in P50**, not a separate
owner. Its complete current contents are represented by L-32–L-37; no `files/`
production module may remain after their imports, tests, docs, and composition
have migrated.

## P51 — configuration, composition, observability, evaluation, and residual seams

| ID | Current path and responsibility | Callers; real production adapters | Invariants | Target module; disposition; planned replacement |
| --- | --- | --- | --- | --- |
| L-40 | `config/{settings,loader,policy}.py` — Settings schema, TOML/environment resolution, field policy, and loopback policy service. | Callers: app/CLI, API settings/dependencies, composition, Daytona, LM, MLflow and PostHog setup. Adapters: TOML loader and process environment; no provider client. | One authoritative Settings schema; only policy-referenced environment values resolve secrets/endpoints; no ambient selector aliases or secret echoing. | `config/{settings,loader,policy}.py`; **MOVE/MERGE**. **P51 implemented; proof: configuration policy and settings API lanes.** |
| L-41 | `composition/{live,inventory,testing}.py` — live/test wiring, process inventory, startup/disposal, and deterministic injection. | Callers: app lifespan, API dependencies, CLI; `testing.py` is a test factory. Adapters: live Daytona/SQL/DSPy constructors reside below wiring. | Composition constructs owners but contains no Turn behavior; one complete live inventory and explicit private deterministic inventory remain mutually exclusive. | `composition/{live,inventory,testing}.py`; **KEEP/DEEPEN**. **P51 implemented; proof: composition and dependency-direction lanes.** |
| L-42 | `observability/{diagnostics,tracing,dspy_callbacks,mlflow,posthog}.py` — failure taxonomy, tracing config/spans, lifespan state, and shadow callback comparison. | Callers: app, chat, RLM runner/recursion, interpreter, Memory diagnostics. Adapters: MLflow client and DSPy callback shadow. | Tracing is fail-soft; callback remains shadow-only; one `fleet_turn` execution root has sanitized metadata/usage semantics; product Runtime Events never come from callbacks. | `observability/{diagnostics,tracing,dspy_callbacks,mlflow,posthog}.py`; **MERGE/MOVE**. **P51 implemented; proof: observability and trace contract lanes.** |
| L-43 | `observability/posthog.py` — PostHog setup, client lifetime, and deterministic distinct ID. | Callers: app/CLI configuration paths. Adapter: PostHog SDK. | Optional telemetry cannot alter Turn outcomes or expose credentials; lifecycle remains explicit and fail-soft. | `observability/posthog.py`; **MOVE**. **P51 implemented; proof: observability configuration lanes.** |
| L-44 | `optimization/daytona.py`, `optimization/{curated_input,dataset,evidence,gepa_runner,mlflow_observability,types,routing}.py` — evaluation-only Daytona lifecycle, datasets, evidence, and routing. | Callers: evaluation commands/workflows only; no production Turn entry point. Adapters: disposable Daytona evaluator, MLflow evaluation tracing, local evidence store. | No optimization algorithm, evidence policy, or production runtime behavior changes; live evaluation remains explicit and disposable. | `optimization/{daytona,routing}.py` plus existing evaluation modules; **MOVE/KEEP**. **P51 implemented; proof: optimization and evaluation lanes.** |
| L-45 | `persistence/repositories/{run_codec,run_queries,sandbox_bindings,run_claim_decisions,run_liveness,run_final_state}` and L-31 shared effects — durable conversion, query, binding, claim, liveness, and final-state adapters. | Callers: lifecycle, Session runtime, Volume paths, TurnRuntime, repositories. Adapters: SQLAlchemy and Daytona binding persistence. | Persistence stays adapter-only; one canonical representation per semantic layer; no shallow Factory/Manager/Gateway/Facade survives unless multiple concrete production adapters and hidden invariants justify it. | `persistence/repositories/{run_codec,run_queries,sandbox_bindings,run_claim_decisions,run_liveness,run_final_state}`; **KEEP/DEEPEN after P53 reassessment**: `run_codec` is pure durable conversion, `sandbox_bindings` is a distinct binding adapter, and `run_queries` is a narrow projection helper. No merge into `turns.py` or a facade owner is justified. **P51 implemented; P53 reassessment recorded; proof: persistence and dependency-direction lanes.** |

## Coverage and change-control checks

This ledger covers every explicit merge/move/delete/deepen source named in P44–P51
and every current supporting module required to make those target owners real:

- History: `sessions/{committed_turn,models,catalog,history_tools}.py`, relevant
  persistence checkpoint repositories, `chat/session_context.py`,
  `rlm/program.py`, and `skills/signatures.py` (L-01–L-06).
- Resident Root and native RLM contraction: current
  `rlm/{program,result,_dspy_compat,runtime,session_runtime,events,recursion}.py`
  (L-07–L-18), with evaluation-only routing mapped to `optimization/routing.py`
  (L-17).
- Child and Root Daytona lifecycle: current acquisition, admission,
  lease/cleanup, broker, interpreter, Workspace Agent, Workspace gateway,
  diagnostic, and disposable evaluation modules (L-19–L-26).
- Durable Turn control: every current `chat/` orchestration/preparation/claim
  module and the result/effect support seams (L-27–L-31).
- Former `files/`: every production responsibility is mapped to current
  Workspace, Project, Memory, Attachment, URL, and Artifact owners in L-32–L-39.
- Secondary architecture: current configuration, composition, observability,
  evaluation, persistence, and effect seams are mapped in L-40–L-45.

Before a later PR changes one row, its author must update that row with the PR
number, final target path, proof links, and whether the planned disposition was
realized. If an implementation discovers a new production adapter, hidden
invariant, or caller, it must add a ledger row before deleting the source.
A deletion PR must cite the P36 inventory IDs named above and record the
same-SHA deterministic and, where required, live evidence. A green unit test
alone does not authorize a provider-lifecycle deletion.

During P53 closeout, rows marked **KEEP/DEEPEN** remain in force: they are not
deletion candidates unless a later reviewed change updates the exact row first.
Only an exact current row explicitly marked **DELETE** may authorize removal,
and the removal must happen before the final clean-candidate P53/P35-E run.
Any candidate change after live evidence invalidates that evidence and requires
the affected certification sequence to run again.

## Historical P42 non-actions

P42 recorded a future responsibility map only. The following statements describe
that baseline, not the later P44–P52 implementation:

- it did **not** create target production modules;
- it did **not** move, merge, or delete production modules;
- it did **not** alter public routes, OpenAPI, generated types, SSE, Runtime
  Events, Tool names/schemas, Skills, Attachments, Artifacts, Workspace behavior,
  Memory, claims, cancellation, deadlines, replay, packaging, or CLI;
- it did **not** amend the P41 historical freeze; and
- it did **not** treat a planned disposition as successful parity evidence.

Later phase commits realize the dispositions recorded by the rows above. Current
certification still requires same-candidate deterministic and, where required,
live receipts; this document never substitutes for those receipts.
