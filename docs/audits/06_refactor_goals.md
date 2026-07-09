# Fleet RLM Refactor — Goals

## Primary goal

Turn Fleet RLM into a clean, testable, observable backend architecture where direct DSPy RLM can eventually become the default execution engine without losing the existing legacy runtime, Skills, Daytona, Tools, Artifacts, Attachments, Traces, MLflow, or frontend contracts.

## Product goals

### 1. Stable chat transport

Keep `/api/chat` as the canonical AI SDK UIMessage v1 SSE endpoint while preserving the WebSocket execution path during migration.

### 2. Runtime backend flexibility

Support both:

- `legacy_agent_runtime` as compatibility/default until parity;
- `direct_rlm` as the clean future runtime.

### 3. First-class Skills

Make Skills a product subsystem with:

- scoped catalog;
- visibility rules;
- trust and permission rules;
- progressive loading;
- resource inventories;
- safe script execution;
- write/approval workflows;
- provenance and install lifecycle.

### 4. Safe Daytona substrate

Expose Daytona through a stable facade package so execution, tools, files, artifacts, and skills all use consistent sandbox/volume primitives.

### 5. Policy-filtered Tools

Expose runtime tools through a registry that supports explicit policy filtering for:

- read tools;
- write tools;
- sandbox tools;
- skill script tools;
- web tools.

### 6. Durable artifacts

Make generated outputs durable, safe, discoverable, and editable through controlled artifact roots.

### 7. Structured attachments

Stage uploaded files safely and pass attachment metadata to the runtime without blindly injecting file contents.

### 8. Canonical observability

Make trace, transcript, performance, and MLflow behavior backend-owned and shared by both legacy and direct RLM.

### 9. Optional MLflow

Make MLflow useful for runtime observability and quality optimization without requiring it for local development or default tests.

### 10. Offline quality optimization

Keep GEPA and quality optimization outside normal chat runtime and make promotion explicit and reviewable.

## Engineering goals

### 1. Bounded phases

Each phase must have:

- one purpose;
- explicit non-goals;
- compatibility rules;
- tests;
- validation gate.

### 2. Compatibility-first migration

Do not remove compatibility imports or public tool names until tests prove they are no longer needed.

### 3. Clear package ownership

Each backend package should have a clear reason to exist and a clear dependency direction.

### 4. Testable boundaries

Imports and unit tests should not require:

- live Daytona credentials;
- live LLM credentials;
- MLflow server;
- database connection;
- frontend build.

### 5. Final-state validation

Temporary implementation breakage is acceptable, but final commits should restore public contracts and pass the agreed validation gate.

## Refactor non-goals

The refactor should not:

- rewrite the entire backend in one mission;
- make direct RLM default before parity;
- remove legacy runtime prematurely;
- add config import side effects;
- require MLflow for default tests;
- run GEPA inside normal chat turns;
- execute skill scripts on the FastAPI host;
- expose raw filesystem paths to clients;
- leak raw provider/runtime errors to clients;
- let LLMs select invisible skills.

## Phase-specific goals

### Completed through Phase 5

- Transport seam complete.
- Execution backend seam complete.
- Direct RLM opt-in golden path complete.
- Skills first-class subsystem complete enough for read/write/install lifecycle.
- Daytona facade complete.
- Tools, Artifacts, Attachments complete.

### Phase 6 goals

- Canonical observability event/span model.
- Recorder seam.
- Trace classifier.
- Transcript/render mapping.
- Performance summary for both backends.
- Optional MLflow adapter and ingest.
- Client-safe redaction.

### Phase 7 goals

- Typed config audit.
- Non-secret `config.yaml` defaults.
- Env override compatibility.
- No config import side effects.

### Phase 8 goals

- GEPA runner and quality lane.
- Separate quality/eval/optimization results.
- Optional MLflow logging for optimization.

### Phase 9 goals

- Switch default backend to direct RLM only after all parity preconditions are met.

### Phase 10 goals

- Move frontend chat to `/api/chat` SSE by default.
- Keep WebSocket for terminal/sandbox interactive control.
- Render trace/debug panels from backend-mapped trace data.
