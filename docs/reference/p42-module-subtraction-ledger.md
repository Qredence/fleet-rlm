# P42 module-subtraction ledger

**Status:** implementation ledger; P42–P47 are implemented, P48 remains
incomplete, and the P49.1–P49.3 canonical owners are implemented but not yet
fully certified. The current working tree contains uncommitted P48/P49
migration work.
**Baseline:** the current checkout after the sealed P36/P41 work, assessed against
`plans/PLANS.md` P44–P51.
**Replacement PRs:** the phase commits are recorded in repository history; rows
that remain planned are deliberately TBD and require separately reviewed
implementation work.

## Current implementation status

This ledger was authored as the P42 pre-change inventory. It is retained as the
responsibility map, but its status is now updated by the implementation frontier:

- **P42–P45:** Session-state contract, complete committed History, and resident
  Root RLM/interpreter runtime are implemented.
- **P46–P47:** the native DSPy kernel contraction and isolated child snapshot
  path are implemented.
- **P48:** broker consolidation and Workspace Agent packaging are present in the
  working tree; Root/child lifecycle migration and full parity are still in
  progress. L-22 and L-24 are the completed P48 moves; L-20, L-21, L-23, L-25,
  and L-26 remain compatibility-backed or planned.
- **P49:** the first preparation/TurnRuntime ownership slice is implemented;
  finalization race coverage and current owner documentation are in progress,
  so no full P49 completion claim is made.
- **P50–P53:** no implementation or certification claim is made yet.

This is the P42.5 companion to the [P42 session-state behavior
freeze](p42-session-state-behavior-freeze.md). It makes the proposed subtraction
work reviewable before a file moves or is deleted. It does **not** change the
sealed [P41 behavior freeze](behavior-freeze.md), production code, public HTTP
contract, OpenAPI, generated artifacts, or the current runtime topology.

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
| P50 | `workspace/{models,paths,storage,workspace,projects,memory,url}.py`, `attachments/`, and `persistence/repositories/outbox.py` | L-32–L-39 |
| P51 | `config/{settings,loader,policy}.py`, `composition/{live,testing}.py`, `observability/{diagnostics,tracing,dspy_callbacks,mlflow,posthog}.py`, `optimization/daytona.py`, and narrowed persistence/effect seams | L-40–L-45 |

The public `api/`, `artifacts/`, `skills/`, `events/canonical.py`, and durable
repository contracts are intentionally not deletion targets. Individual rows
below name them only when they are real callers or adapters whose behavior must
remain unchanged.

## P44 — durable committed Session History

| ID | Current path and responsibility | Callers; real production adapters | Invariants | Target module; disposition; planned replacement |
| --- | --- | --- | --- | --- |
| L-01 | `sessions/committed_turn.py`, `sessions/models.py` — validate and model the durable committed Turn and Session history records. | Callers: `persistence/repositories/{turns,session_catalog,run_codec}.py`, `chat/{run_lifecycle,turn_detail_policy}.py`, API UI projection. Adapter: SQLAlchemy repositories. | Only committed user-facing request/answer data may become conversation; failed, cancelled, and uncommitted output never advances it; workspace-and-Session isolation holds. | `sessions/history.py`; **DEEPEN**. Planned P44 History PR (TBD). |
| L-02 | `sessions/catalog.py`, `persistence/repositories/{session_catalog,turns,run_claim_decisions,run_final_state,run_liveness}.py` — load the claimed checkpoint and enforce durable claim/CAS state. | Callers: `api/dependencies.py`, `chat/{run_lifecycle,run_preparation,turn_coordinator}.py`. Adapter: SQLAlchemy transaction and repository implementations. | Claim → immutable snapshot → RLM → CAS commit order; replay returns the existing result without another RLM call or History append. | Existing repositories plus `sessions/history.py`; **KEEP/DEEPEN** (not a repository merger). Planned P44 and P49 PRs (TBD). |
| L-03 | `sessions/history_tools.py` — exposes `read_session_history` through `SessionHistoryToolHost`. | Caller: `chat/capability_preparation.py`. Adapter: the same SQLAlchemy-backed Session snapshot used by L-02. | Fixed 256 KiB aggregate UTF-8 budget, whole-message omission metadata, authorization, and result schema remain unchanged. | `sessions/history_tools.py` bound to the P44 checkpoint; **KEEP**. Planned P44 compatibility PR (TBD). |
| L-04 | `chat/session_context.py` — creates bounded session metadata and six recent previews. | Callers: `chat/run_preparation.py`, `rlm/{context,inputs}.py`. Adapter: none; it consumes the claimed durable model. | Context stays bounded navigation metadata; it is not a transcript and does not replace complete `dspy.History`. | `chat/session_context.py`, later `chat/preparation.py`; **KEEP/DEEPEN**. Planned P44 then P49 PRs (TBD). |
| L-05 | `rlm/{signature,input_models,inputs}.py` — declares named RLM fields and serializes request, context, Skill cards, and Attachments. | Callers: `rlm/{factory,runner,context}.py`, `chat/run_preparation.py`, `skills/signatures.py`. Adapter: native `dspy.RLM.acall(...)` named-input boundary. | Current request stays separate from prior History; every Signature has stable common fields; no transcript is expanded into public events. | P44 extends the Signature; P46 consolidates it in `rlm/program.py`; **MERGE** only in P46. Planned P44 Signature PR and P46 contraction PR (TBD). |
| L-06 | `skills/signatures.py` and `skills/catalog.py` — declares and validates the bundled `DataAnalysisSignature` binding. | Callers: `skills/resolver.py`, `rlm/signature.py`, and the fixed catalog. Adapter: DSPy Signature construction only. | All custom Signatures require `request`, `history`, `session_context`, `skill_cards`, `attachments`, and `answer`; structured result data remains durable output, not History. | `skills/signatures.py` and catalog binding; **DEEPEN**. Planned P44 Skill-signature PR (TBD). |

## P45 — compatible resident Root runtime

| ID | Current path and responsibility | Callers; real production adapters | Invariants | Target module; disposition; planned replacement |
| --- | --- | --- | --- | --- |
| L-07 | No current single owner. Per-Turn Root construction is split across `rlm/{factory,runner,context,worker_execution}.py`. | Callers: `composition/{common,daytona}.py`, `chat/{run_preparation,turn_coordinator}.py`. Adapters: native DSPy and `DaytonaCodeInterpreter`. | One compatible RLM/interpreter/Sandbox is reused only sequentially, after result validation and durable commit; each invocation gets fresh `REPLHistory`, budgets, request, history, and metadata. | New `rlm/session_runtime.py`; **DEEPEN**. Planned P45 Session-runtime PR (TBD). |
| L-08 | `daytona/{session_manager,sandbox_lease,interpreter,run_environment,admission,platform}.py` — currently acquires a fresh Root lease/interpreter and accountably closes it. | Callers: `composition/daytona.py`, `chat/run_cleanup.py`, `daytona/recursive_child_runtime.py`. Adapters: `LiveDaytonaPlatform`, `AsyncDaytona`, broker-backed `DaytonaCodeInterpreter`. | Full `(workspace_id, session_id)` key; one execution lane; explicit admission restoration; clean close is distinct from failed cleanup; Volume scope survives rotation. | `rlm/session_runtime.py` consumes later `daytona/runtime.py`; **DEEPEN**, not deletion in P45. Planned P45 then P48 PRs (TBD). |
| L-09 | `chat/{run_claim,run_authority,run_ownership}.py` and `rlm/tool_guards.py` — claim validity, heartbeats, cancellation shielding, and run-scoped Tool guards. | Callers: `chat/{run_lifecycle,turn_coordinator}.py`, `persistence/repositories/{turns,run_codec,run_liveness}.py`, `rlm/runner.py`. Adapters: SQLAlchemy claim persistence; Tool calls reach the current host capability. | A retained Python Tool alias resolves current Turn authorization, never prior authorization; claim loss, cancellation, timeout, commit/authorization failure, or uncertain settlement taints the resident runtime. | `rlm/session_runtime.py` current-capability binding, then `chat/turn_runtime.py`; **MERGE** after P36 P37 parity. Planned P45/P49 PRs (TBD). |

## P46 — thin native DSPy kernel

| ID | Current path and responsibility | Callers; real production adapters | Invariants | Target module; disposition; planned replacement |
| --- | --- | --- | --- | --- |
| L-10 | `rlm/dspy_contract.py` — exact DSPy version gate, RLM option mapping, Prediction normalization/result trust, trajectory and usage types. | Callers: app, CLI, composition, chat lifecycle/preparation, runner, persistence codecs, and diagnostics. Adapter: pinned native `dspy.RLM`/`dspy.Prediction`. | Fleet owns validation and sanitation but never a second RLM iteration loop or alternate Prediction constructor; `max_iters`, `max_llm_calls`, and output limits retain certified meaning. | Split by responsibility into `rlm/{program,result,_dspy_compat}.py`; **MERGE**. Planned P46 contraction PR (TBD). |
| L-11 | `rlm/dspy_interpreter_contract.py` — narrow interpreter injection, output-field refresh, FinalOutput wrapping. | Callers: `daytona/{interpreter,interpreter_output}.py`, `rlm/{dspy_contract,context,child_runtime,provider_probe}.py`. Adapter: private/public DSPy interpreter seam. | Caller-owned interpreter is never shut down by DSPy; stale Tools/output metadata are refreshed; no `_tools_registered` access is introduced. | `rlm/_dspy_compat.py`; **MOVE/DEEPEN**. Planned P46 PR (TBD). |
| L-12 | `rlm/{factory,lm_factory,model_bundle}.py` — constructs Root/Sub DSPy LMs, model roles, bundles, and Root RLM. | Callers: `composition/daytona.py`, runner, provider probe, recursion, optimization. Adapter: `dspy.LM` and native `dspy.RLM` construction. | Configuration does no provider I/O; exact role model policy, normalized IDs, secret redaction, and Root/Sub separation remain. | `rlm/program.py`; **MERGE**. Planned P46 PR (TBD). |
| L-13 | `rlm/{signature,instructions,input_models,inputs}.py` — Signature schema, instructions, DTOs, and one-time input validation/serialization. | Callers: L-05 callers plus `optimization/gepa_runner.py`. Adapter: DSPy Signature/input adaptation. | Named fields and bounded input contract stay stable; P44 `history` is canonical conversation; no Pydantic→dict→Pydantic loop survives without a semantic boundary. | `rlm/program.py`; **MERGE**. Planned P46 PR (TBD). |
| L-14 | `rlm/{outcome,errors,sanitize}.py` — typed RLM outcome/failures and public-safe result/error text. | Callers: chat lifecycle/coordinator/detail policy, runner, interpreter, observation. Adapter: none independent; provider errors enter through Daytona/DSPy. | Invalid, oversized, secret-bearing, or malformed output never commits; public errors remain closed and bounded. | `rlm/result.py` plus narrow package errors; **MERGE**. Planned P46 PR (TBD). |
| L-15 | `rlm/{events,observation,tool_observer,trajectory_projection,execution_trace}.py` — current fragmented callback/interpreter/Tool observation and trajectory reconciliation. | Callers: `api/sse.py`, chat projection, runner, interpreter, Files/Skills/History Tool hosts. Adapters: interpreter observation, `dspy.Tool`, and MLflow trace hooks. | Live evidence wins; native trajectory only fills gaps; bounded Tool event views never expose raw arguments/results; Runtime Event vocabulary/order stay unchanged. | Canonical `rlm/events.py` feeding `events/canonical.py`; **MERGE**. Planned P46 PR, gated by P36 P38 evidence (TBD). |
| L-16 | `rlm/{runner,worker_execution,context,tool_guards}.py` — worker-thread invocation, cancellation/deadline checks, execution spec, and integrity guard. | Callers: composition, chat, recursion, diagnostics. Adapters: `dspy.RLM.acall(interpreter, **inputs)` and Daytona interpreter. | Native call remains immediately visible; Fleet retains worker ownership, absolute deadline, cancellation, cleanup, and current Tool authorization. | `rlm/runtime.py` with `session_runtime.py`; **MERGE**. Planned P46 PR after P45 proof (TBD). |
| L-17 | `rlm/routing_eval.py` — evaluation-only routing scenarios and scoring. | Callers: no production Turn caller; development/evaluation code imports it directly. Adapter: none in the production runtime. | No routing algorithm or production execution behavior changes. | `optimization/routing.py`; **MOVE**. Planned P46/P51 evaluation move PR (TBD). |

## P47 — isolated native child recursion

| ID | Current path and responsibility | Callers; real production adapters | Invariants | Target module; disposition; planned replacement |
| --- | --- | --- | --- | --- |
| L-18 | `rlm/{recursive_calls,recursive_batch,delegation_metrics,child_runtime}.py` — recursive Tool policy, reservation/batch settlement, metrics, and child lease protocol. | Callers: Root preparation/runner/context, provider probe, routing evaluation, execution trace. Adapters: child `dspy.RLM`, copied `dspy.LM`, and the Daytona child factory. | Root depth 0; native child depth 1; deeper Sub-LM fallback; Root-only ordered all-or-nothing batch; shared budget/deadline; no child Session or mutable Root state. | `rlm/recursion.py`; **MERGE**. Planned P47 recursion PR, gated by P36 P39 (TBD). |
| L-19 | `daytona/recursive_child_runtime.py` — actual child Sandbox acquisition, late ownership, close, absence confirmation, and permit release. | Callers: `daytona/{run_environment,diagnostics}.py`; factory used through L-18. Adapters: `LiveDaytonaPlatform`, broker, interpreter, admission permits. | Fresh child RLM/interpreter/Sandbox and child Volume subpath; shutdown → deletion → provider absence → admission restoration; failed cleanup prevents clean success. | Public `daytona/runtime.py::open_child` with private `_lease.py`/`_cleanup.py`; **DEEPEN**. Planned P47/P48 PRs (TBD). |

## P48 — Daytona Root Session and Workspace-Agent lifecycle

| ID | Current path and responsibility | Callers; real production adapters | Invariants | Target module; disposition; planned replacement |
| --- | --- | --- | --- | --- |
| L-20 | `daytona/{session_manager,run_environment,admission,provisioning,platform}.py` — root acquisition, SDK specification, admission, bindings, mounts, and retries. | Callers: `composition/daytona.py`, diagnostics, recursive child runtime, Workspace gateway. Adapters: `LiveDaytonaPlatform` and `AsyncDaytona` SDK. | Callers above Daytona do not learn permits, provisioning retries, provider bindings, mount preparation, or delete polling; root lease is reusable only while healthy. | `daytona/runtime.py` public `DaytonaRuntime.acquire_root_session`; **MERGE/DEEPEN**. Planned P48 lifecycle PR (TBD). |
| L-21 | `daytona/{sandbox_lease,lifecycle}.py` — owned close, purge, provider deletion and absence state. | Callers: session manager, child runtime, run cleanup. Adapters: live provider deletion/status checks. | `OPEN`, `CLOSING`, `CLOSED`, and `FAILED` remain behaviorally distinct; one close joins; all cleanup failures are re-observable; admission is restored only after required ownership settles. | `daytona/{_lease,_cleanup}.py`; **MOVE**. Planned P48 PR, gated by P36 P37/P39 (TBD). |
| L-22 | `daytona/{broker_source,http_broker,dspy_sync_bridge,interpreter_output}.py` — code generation, submit framing, HTTP Tool dispatch, synchronous bridge, and output decoding. | Callers: `daytona/interpreter.py`, composition, session/child managers, outbox reconciliation. Adapters: sandbox HTTP broker and DSPy synchronous interpreter interface. | Tool dispatch remains current-Turn authorized; FinalOutput extraction and bounded corrective feedback stay exact; broker shutdown precedes provider cleanup. | `daytona/broker.py` (with `interpreter_output.py` kept separate); **MERGE**. **P48 implemented.** |
| L-23 | `daytona/interpreter.py` — `DaytonaCodeInterpreter`, backend selection, execution observation and recoverable/terminal error taxonomy. | Callers: runtime acquisition, child runtime, diagnostics, optimization evaluator, Workspace Agent. Adapter: Daytona Sandbox interpreter and broker. | Existing repair/no-progress behavior, error classification, caller-owned shutdown, live code/output evidence, and public sanitation remain. | `daytona/interpreter.py` under `DaytonaRuntime`; **DEEPEN**. Planned P48 PR; P36 P38 gate applies (TBD). |
| L-24 | `daytona/{workspace_agent,workspace_agent_runtime}.py` — installed agent protocol/source and host-side response validation. | Callers: `daytona/{workspace_fs,workspace_memory,memory_diagnostics}.py`. Adapter: code running inside a mounted Daytona Sandbox. | Relative-path-only requests, symlink/nonregular-node rejection, bounded pages/bytes, checksum preconditions, and fail-closed protocol parsing stay intact. | `daytona/workspace_agent/{protocol,client,runtime}.py`; **MOVE**. **P48 implemented.** |
| L-25 | `daytona/{workspace_fs,workspace_gateway}.py` — Volume/Session Workspace adapter, I/O Sandbox gateway, orphan cleanup. | Callers: Daytona composition/run environment and `api/routes/workspace_files.py`; Files Volume interfaces. Adapters: mounted Daytona Volume and ephemeral I/O Sandbox. | Workspace/Project writes are immediate and scope-safe; reads retain cursor/byte bounds; no caller gets a provider path; Artifact publication keeps its separate commit gate. | `workspace/storage.py` behind `daytona/workspace_agent/`; **MOVE** in P50 after the P48 protocol move. Planned P48/P50 PRs (TBD). |
| L-26 | `daytona/{diagnostics,errors,optimization_evaluator}.py` and `rlm/provider_probe.py` — disposable doctor/probe behavior, provider error mapping, and strict evaluation Sandbox lifecycle. | Callers: CLI, Daytona diagnostics, development optimization. Adapters: disposable live Daytona Sandbox and provider SDK; no Turn settlement adapter. | Diagnostics stay bounded, sanitized, explicit, and non-authoritative for Turns; strict evaluation remains disposable and provider-clean. | `daytona/errors.py` **KEEP**; `observability/diagnostics.py` and `optimization/daytona.py` **MOVE**. Planned P48/P51 PRs (TBD). |

## P49 — one durable TurnRuntime

| ID | Current path and responsibility | Callers; real production adapters | Invariants | Target module; disposition; planned replacement |
| --- | --- | --- | --- | --- |
| L-27 | `chat/turn_coordinator.py`, `chat/commands.py`, `chat/committed_turn_events.py`, `chat/turn_detail_policy.py` — open stream, command input, event/replay projection, and terminal detail policy. | Callers: `api/{dependencies,routes/turns,routes/runs}.py`, composition inventory, lifecycle. Adapter: FastAPI/SSE consumes its `RuntimeEvent` stream. | Close-before-first-event, one absolute deadline, exactly one last terminal, replay determinism, and transport-neutral Runtime Events remain. | `chat/{turn_runtime,models,committed_events}.py`; **MERGE** except thin commands/models. Planned P49 PR, gated by P36 P37 (TBD). |
| L-28 | `chat/{run_lifecycle,run_claim,run_authority,run_ownership,run_cleanup}.py` — durable start/settlement, transition policy, authority, heartbeat, and owned cleanup. | Callers: coordinator, preparation, API, composition, persistence repositories. Adapters: SQLAlchemy commit/claim stores; Daytona cleanup. | Validate → Artifact staging → memory intents → atomic commit → snapshot → owned promotion; failure/cancel never publishes success or advances History; cleanup cannot detach. | `chat/turn_runtime.py`; **MERGE** after P36 P37 parity. Planned P49 PR (TBD). |
| L-29 | `chat/{run_preparation,capability_preparation,session_context,post_commit_memory}.py` — claimed input assembly, Tools/capabilities, bounded context, and post-commit Memory task. | Callers: coordinator/lifecycle, Daytona run environment, testing composition. Adapters: selected Tool hosts, Workspace Memory store, Attachment preparer. | Prepared History is checkpoint-aligned; current capabilities are authorization-scoped; Memory promotion stays owned past commit and no failed Turn promotes it. | `chat/preparation.py` plus `chat/turn_runtime.py`; **MERGE**. Planned P49 PR (TBD). |
| L-30 | `result_snapshot.py`, `snapshot_contract.py`, `artifacts/{promotion,workspace_storage}.py` — private result derivative and commit-gated candidate/Artifact handling. | Callers: run lifecycle, repositories, API Artifact route. Adapters: SQLAlchemy metadata and mounted Volume bytes. | Result snapshot is private/non-replay; Artifact bytes/metadata publish only through successful atomic commit; failed Runs expose no Artifact identity. | `chat/turn_runtime.py` owns order; Artifact modules remain authoritative; **KEEP/DEEPEN**, not deletion. Planned P49 PR (TBD). |
| L-31 | `runtime/{bindings,owned_effect}.py` — shared binding and joinable owned-effect primitives. | Callers: Files Volume paths, Sandbox bindings repository, cleanup/post-commit paths. Adapters: none itself; underlying owners are Daytona and SQLAlchemy. | Shared machinery remains only if multiple independent owners need one cancellation-safe join/settlement contract; no pseudo-owner may report cleanup. | `chat/turn_runtime.py` or narrow neutral primitive; **DEEPEN/DELETE after reassessment**. Planned P49/P51 PR (TBD). |

## P50 — distinct Workspace, Projects, Memory, Attachments, and URL domains

| ID | Current path and responsibility | Callers; real production adapters | Invariants | Target module; disposition; planned replacement |
| --- | --- | --- | --- | --- |
| L-32 | `files/{workspace_access,workspace_models,workspace_tools,workspace_validation,filesystem_tool_helpers}.py` — Session Workspace host, models, path validation, Tool construction, error/event serialization. | Callers: API Workspace files route, Daytona run environment/interpreter, capability preparation. Adapter: `DaytonaSessionWorkspaceFS`/Workspace Agent. | Seven Workspace Tools retain exact names/order/schemas; immediate durability, paged bounds, nonrecursive empty-dir deletion, checksum preconditions, and safe event views remain. | `workspace/{workspace,models,paths}.py`; **MOVE/MERGE**. Planned P50 PR, gated by P36 P40 (TBD). |
| L-33 | `files/project_tools.py` — explicit Project host and path policy. | Caller: `daytona/run_environment.py`. Adapter: mounted Workspace Volume through Workspace Agent. | Six Project Tools, `projects/<slug>/` scope, root browsing exception, no append, strict slug/path rules, and safe Tool event metadata remain. | `workspace/projects.py`; **MOVE**. Planned P50 PR, gated by P36 P40 (TBD). |
| L-34 | `files/{memory_models,memory_tools,memory_candidates,memory_candidate_tools}.py` and `daytona/{workspace_memory,memory_diagnostics,memory_outbox_reconcile}.py` — Memory records, Tool host, candidates, digest, durable store, diagnostics, and reconciliation. | Callers: run preparation/lifecycle, Daytona run environment, persistence memory intent repository. Adapter: mounted `memory/MEMORIES.md` Workspace Agent plus SQLAlchemy outbox. | Workspace Memory stays distinct from Session History; stable IDs/provenance/search/edit/delete/supersession/digest/malformed tolerance/current coordination limits and commit-gated promotion stay exact. | `workspace/{memory,models}.py` and `persistence/repositories/outbox.py`; **MOVE/MERGE**. Planned P50 PR (TBD). |
| L-35 | `files/{lifecycle,local_catalog,models,paths,errors,safety}.py` — Attachment catalog/blob lifecycle, models, path policy, and upload safety. | Callers: attachment API route, composition, run preparation, repositories. Adapters: SQLAlchemy metadata plus Workspace Volume blob store. | Attachments remain Workspace-owned authorized inputs; validation, integrity checks, and candidate isolation do not become Workspace-file Tools. | `attachments/` modules; **MOVE**. Planned P50 PR (TBD). |
| L-36 | `files/{volume_storage,volume_paths,host_volume}.py` — Volume interfaces, path identity/normalization, and deterministic local mirror. | Callers: API dependencies, artifacts, composition, Daytona FS/gateway, persistence sandbox bindings. Adapters: production mounted Daytona Volume; `HostVolumeMirror` is testing-only. | One canonical relative-path normalization; no provider path leaks; deterministic fake remains private-test-only and does not define public behavior. | `workspace/{storage,paths}.py`; test mirror to `composition/testing.py`; **MOVE**. Planned P50 PR (TBD). |
| L-37 | `files/{tools,url_tool}.py` — shared file host and URL Tool/source store. | Callers: Daytona run environment and deterministic composition. Adapter: public-text URL fetcher and Workspace-backed URL source store. | Tool names/schemas/event views and URL safety/time/size bounds remain; generic File host must not recreate an undifferentiated Workspace abstraction. | `workspace/url.py`; common host code absorbed into explicit hosts; **MERGE/DELETE after parity**. Planned P50 PR (TBD). |
| L-38 | `persistence/repositories/memory_promotion_intents.py` — durable intent query/write adapter. | Callers: chat lifecycle, Files Memory candidates, run state repositories. Adapter: SQLAlchemy. | Intent is atomically committed with the successful Turn, becomes promotable only after commit, and recovery remains idempotent. | `persistence/repositories/outbox.py`; **MERGE**. Planned P50 PR (TBD). |
| L-39 | `api/routes/workspace_files.py`, `api/dependencies.py`, and composition consumers of `files/` interfaces. | Callers: FastAPI router registration and TUI HTTP client. Adapters: FastAPI translation plus L-32 storage adapter. | Route URL/response schema, OpenAPI, generated TUI types, Volume tree read-only boundary, and no provider paths remain unchanged. | `api/routes/workspace.py` with new workspace owners; **MOVE** only after `make api-check`. Planned P50 PR (TBD). |

`files/__init__.py` is therefore **DELETE after parity in P50**, not a separate
owner. Its complete current contents are represented by L-32–L-37; no `files/`
production module may remain after their imports, tests, docs, and composition
have migrated.

## P51 — configuration, composition, observability, evaluation, and residual seams

| ID | Current path and responsibility | Callers; real production adapters | Invariants | Target module; disposition; planned replacement |
| --- | --- | --- | --- | --- |
| L-40 | `config.py`, `config_policy.py` — Settings schema, TOML/environment resolution, field policy, and loopback policy service. | Callers: app/CLI, API settings/dependencies, composition, Daytona, LM, MLflow and PostHog setup. Adapters: TOML loader and process environment; no provider client. | One authoritative Settings schema; only policy-referenced environment values resolve secrets/endpoints; no ambient selector aliases or secret echoing. | `config/{settings,loader,policy}.py`; **MOVE/MERGE**. Planned P51 PR (TBD). |
| L-41 | `composition/{common,daytona,inventory,testing}.py` — live/test wiring, process inventory, startup/disposal, and deterministic injection. | Callers: app lifespan, API dependencies, CLI; `testing.py` is a test factory. Adapters: live Daytona/SQL/DSPy constructors reside below wiring. | Composition constructs owners but contains no Turn behavior; one complete live inventory and explicit private deterministic inventory remain mutually exclusive. | `composition/{live,testing}.py`; **MERGE** (`testing.py` **KEEP/DEEPEN**). Planned P51 PR (TBD). |
| L-42 | `observability/{failure_diagnostics,tracing,turn_tracing,mlflow_runtime,callback_shadow}.py` — failure taxonomy, tracing config/spans, lifespan state, and shadow callback comparison. | Callers: app, chat, RLM runner/recursion, interpreter, Memory diagnostics. Adapters: MLflow client and DSPy callback shadow. | Tracing is fail-soft; callback remains shadow-only; one `fleet_turn` execution root has sanitized metadata/usage semantics; product Runtime Events never come from callbacks. | `observability/{diagnostics,tracing,dspy_callbacks,mlflow}.py`; **MERGE/MOVE**. Planned P51 PR, gated by P36 P38 (TBD). |
| L-43 | `posthog_client.py` — PostHog setup, client lifetime, and deterministic distinct ID. | Callers: app/CLI configuration paths. Adapter: PostHog SDK. | Optional telemetry cannot alter Turn outcomes or expose credentials; lifecycle remains explicit and fail-soft. | `observability/posthog.py`; **MOVE**. Planned P51 PR (TBD). |
| L-44 | `daytona/optimization_evaluator.py`, `optimization/{curated_input,dataset,evidence,gepa_runner,mlflow_observability,types}.py`, and L-17 routing evaluation. | Callers: evaluation commands/workflows only; no production Turn entry point. Adapters: disposable Daytona evaluator, MLflow evaluation tracing, local evidence store. | No optimization algorithm, evidence policy, or production runtime behavior changes; live evaluation remains explicit and disposable. | `optimization/{daytona,routing}.py` plus existing evaluation modules; **MOVE/KEEP**. Planned P51 PR (TBD). |
| L-45 | `persistence/repositories/{run_codec,run_queries,sandbox_bindings}` and L-31 shared effects — residual facades and representation conversions around durable state/resource bindings. | Callers: lifecycle, session manager, Volume paths, coordinator, repositories. Adapters: SQLAlchemy and Daytona binding persistence. | Persistence stays adapter-only; one canonical representation per semantic layer; no shallow Factory/Manager/Gateway/Facade survives unless multiple concrete production adapters and hidden invariants justify it. | `persistence/repositories/{turns,run_claims,run_liveness,run_final_state,outbox}.py` or the actual deep owner; **MERGE/DELETE after reassessment**. Planned P51 PR (TBD). |

## Coverage and change-control checks

This ledger covers every explicit merge/move/delete/deepen source named in P44–P51
and every current supporting module required to make those target owners real:

- History: `sessions/{committed_turn,models,catalog,history_tools}.py`, relevant
  persistence checkpoint repositories, `chat/session_context.py`,
  `rlm/{signature,input_models,inputs}.py`, and `skills/signatures.py` (L-01–L-06).
- Resident Root and native RLM contraction: all current `rlm/` execution,
  construction, result, event, compatibility, worker, and routing modules
  (L-07–L-17); `rlm/provider_probe.py` is retained/moved only as L-26's
  diagnostic boundary, not deleted.
- Child and Root Daytona lifecycle: all current acquisition, admission,
  lease/cleanup, broker, interpreter, Workspace Agent, Workspace filesystem,
  diagnostic, and disposable evaluation modules (L-18–L-26).
- Durable Turn control: every `chat/` orchestration/preparation/claim/cleanup
  module and the current result/effect support seams (L-27–L-31).
- `files/`: every current production module is mapped in L-32–L-37, with its
  Memory persistence adapter in L-38 and HTTP translator in L-39.
- Secondary architecture: both flat configuration modules, all composition
  modules, all observability modules, root PostHog, evaluation modules, and
  residual persistence/effect seams are mapped in L-40–L-45.

Before a later PR changes one row, its author must update that row with the PR
number, final target path, proof links, and whether the planned disposition was
realized. If an implementation discovers a new production adapter, hidden
invariant, or caller, it must add a ledger row before deleting the source.
A deletion PR must cite the P36 inventory IDs named above and record the
same-SHA deterministic and, where required, live evidence. A green unit test
alone does not authorize a provider-lifecycle deletion.

## Explicit P42 non-actions

P42 records the future responsibility map only. It does **not**:

- create any of the target production modules;
- move, merge, or delete a production module;
- alter public routes, OpenAPI, generated types, SSE, Runtime Events, Tool
  names/schemas, Skills, Attachments, Artifacts, Workspace behavior, Memory,
  claims, cancellation, deadlines, replay, packaging, or CLI;
- amend the P41 historical freeze; or
- treat a planned disposition as successful parity evidence.
