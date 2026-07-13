# Fleet RLM target architecture

## Final objective

The final objective is to make `src/fleet_rlm/` a clean, coherent,
production-grade FastAPI backend whose canonical chat transport is SSE, whose AI
execution layer is built with DSPy and centered on `dspy.RLM`, whose generated
code runs safely inside Daytona sandboxes, and whose Skills system supplies
progressively disclosed instructions, resources, and approved scripts to the
RLM.

The completed backend is **RLM-native**: `dspy.RLM` is the primary engine for
open-ended agentic work, while bounded DSPy modules remain valid for narrow
classification, selection, extraction, and scoring. Deterministic Python owns
authentication, permissions, validation, trust, path safety, persistence,
storage policy, and redaction. RLM-native does not mean RLM-only.

The legacy runtime has been removed. `src/fleet_rlm/` is the RLM-native backend;
there is no compatibility runtime, dual-serve path, or backend-selection surface.
The former frontend has been removed. A new client and its contract adaptation
are a separately authorized follow-up.

## Canonical architecture

```text
Client
  -> POST /api/chat
  -> FastAPI authentication, validation, and turn preparation
  -> ChatExecutionContext
  -> direct DSPy execution path
  -> per-run dspy.RLM
  -> Daytona interpreter and isolated sandbox
  -> llm_query / llm_query_batched / policy-approved tools
  -> RuntimeEvent
  -> recorder, persistence, traces, and optional MLflow
  -> AI SDK-compatible SSE transcript
```

During migration, `stream_turn()` and server-owned `ExecutionBackend` preserve
the compatibility runtime without exposing backend choice on `ChatRequest`.
Both backends emit the same `RuntimeEvent` vocabulary and use the same transport
projectors.

## Final module ownership

- `api/` owns HTTP transport, authentication, request schemas,
  transport-neutral execution context, runtime dispatch, and response projection.
- `rlm/` owns the direct RLM runner, signatures, inputs, trajectory normalization,
  error mapping, and per-run RLM lifecycle.
- `runtime/` owns the compatibility agent runtime and shared event vocabulary.
- `daytona/` owns interpreter, sandbox, volume, workspace, session-state, and
  diagnostics facades; `integrations/daytona/` remains a compatibility adapter.
- `skills/` owns catalog, visibility, loading, selection, validation, writes,
  provenance, and install/update policy.
- `tools/` owns descriptors, registry, exposure policy, binding, and tool categories.
- `files/` owns attachment staging, metadata, resolution, and file safety rules.
- `artifacts/` owns approved durable roots, storage, indexes, and safe references.
- `observability/` owns provider-neutral recording, spans, usage, redaction, and
  the optional MLflow adapter.
- `traces/` owns render classification, performance aggregation, feedback, and
  MLflow ingestion compatibility.
- `quality/` owns offline datasets, metrics, GEPA runs, results, and promotion.
- `db/` owns the SQLAlchemy table catalog, async engine helpers, domain
  repositories, and the Postgres store façade. Live layout is domain modules
  under `db/models/` (schema only) and `db/repos/` (transactions, RLS context,
  and queries), with one model registry for Alembic
  (historical Phase 8.5, preserved under `docs/internal/legacy-backend/`). The former flat
  `integrations/database/` package is removed.
- `integrations/persistence_protocol.py` and `integrations/local_store.py` own
  the dual-backend persistence port and the limited local backend. Postgres is
  the system of record; LocalStore is not required to match Neon capability.

Compatibility packages may expose old import paths, but new implementation must
live in the owning module. Thin facades do not become a second implementation.

## Transport direction

SSE is the backend streaming transport because chat is primarily a one-way
server stream, aligns with AI SDK UIMessage parts, and decouples frontend
rendering from runtime execution. HTTP request handlers own client commands and
the SSE projector consumes the shared `RuntimeEvent` stream without branching
on execution backend.

## Runtime direction

Direct RLM is the target hot path because recursive reasoning, generated code,
sub-LM calls, Skills, and Daytona execution are its core workload. It remains
opt-in until Phase 9 proves golden flows, trace parity, safety, and performance.

Each run that uses a custom interpreter receives its own `dspy.RLM` instance.
The RLM receives typed task inputs, selected `ActiveSkills`, metadata-only
`AttachedFiles`, policy-filtered tools, explicit budgets, and the active Daytona
interpreter. Generated code and approved Skill scripts execute in Daytona, never
in the FastAPI host.

`ExecutionBackend` chooses the runtime implementation. `ExecutionMode` controls
behavior within the compatibility runtime. They remain distinct interfaces.

## Progressive capability disclosure

Skills and data enter the RLM in bounded stages:

```text
visible catalog metadata
  -> selected SKILL.md instructions
  -> resource inventory
  -> explicitly read resource content
  -> explicitly approved Daytona script execution
```

Attachments remain ID-addressed metadata until an approved tool reads them.
Large or durable outputs become artifacts under approved roots rather than
unbounded prompt or transport payloads.

## Safety rules

Do not break:

- `POST /api/chat` and required HTTP control paths;
- `RuntimeEvent` and its SSE projection;
- session trace, performance, run-step, and feedback schemas;
- `ActiveSkills`, visibility checks, and skill selection;
- Daytona volume/session state and approved durable roots;
- attachment-ID resolution and artifact references.

Do not allow:

- invisible Skills selected by an LLM;
- host execution of sandbox code or trusted Skill scripts;
- traversal outside approved Skill, file, or artifact roots;
- raw operational exceptions or secrets in client payloads;
- shared custom-interpreter RLM instances across concurrent runs;
- MLflow as a default-test or local-development dependency;
- GEPA inside normal chat turns;
- config imports that instantiate clients, engines, LMs, or applications.

## Upstream contracts

Runtime design follows installed DSPy and current DSPy documentation. A custom
interpreter bridges sandbox calls such as `llm_query()` back to the host layer,
and each concurrent custom-interpreter run receives its own RLM instance. GEPA
budget controls and optional MLflow settings belong to the offline quality lane.

Relevant references:

- [DSPy RLM](https://dspy.ai/api/modules/RLM/)
- [DSPy LM](https://dspy.ai/api/models/LM/)
- [DSPy GEPA](https://dspy.ai/api/optimizers/GEPA/overview/)

## Legacy destination

The compatibility runtime is a migration and rollback mechanism, not the final
hot path. The ordered destination is:

```text
preserve behavior while direct RLM earns parity
  -> promote direct RLM through explicit live evidence
  -> move workspace transcript execution to SSE
  -> inventory active legacy consumers and rollback requirements
  -> isolate compatibility ownership
  -> remove only paths proven dead by contract, browser, telemetry, and rollback evidence
```

Compatibility remains only where an active consumer or explicit rollback
contract still exists. It must not become an indefinite second implementation.

## Completion definition

The refactor is complete only when:

- `POST /api/chat` is the transcript path and backend streaming is SSE;
- direct `dspy.RLM` is the promoted primary agentic runtime and its live matrix
  proves Skills, attachments, artifacts, session restore, traces, safety, and
  performance;
- generated code and approved Skill scripts execute only in Daytona;
- RuntimeEvents are recorded and projected without backend-specific transport
  branches;
- MLflow remains optional and GEPA remains an offline, explicitly promoted
  quality workflow;
- legacy paths are isolated or removed according to Phase 10 evidence; and
- default tests require no live LLM, Daytona account, database, or MLflow server.
