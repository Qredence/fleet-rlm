# Fleet RLM Refactor — Final Intended Implementation State

## One-line architecture

Fleet should become:

```text
FastAPI transport
  -> ChatExecutionContext
  -> stream_turn()
  -> ExecutionBackend
  -> legacy AgentRuntime or direct dspy.RLM
  -> Daytona sandbox/interpreter
  -> RuntimeEvent
  -> run lifecycle + traces + MLflow + AI SDK SSE projection
  -> durable skills, tools, files, attachments, and artifacts
```

## Final backend shape

The final architecture should have clear package ownership.

### `api/`

Owns:

- FastAPI app assembly;
- routers;
- schemas;
- dependencies;
- auth;
- SSE/WebSocket transport surfaces;
- runtime service seams.

Does not own:

- business logic that belongs in Skills, Tools, Artifacts, Files, Daytona, RLM, or Observability.

### `rlm/`

Owns:

- direct RLM execution;
- RLM input construction;
- trajectory-to-runtime-event conversion;
- RLM budgets;
- structured direct RLM errors.

Final state:

- direct RLM becomes the clean default only after parity.
- legacy runtime remains available as fallback.

### `runtime/`

Owns:

- legacy AgentRuntime compatibility;
- shared runtime event definitions where still appropriate;
- compatibility execution behavior.

Final state:

- legacy runtime is isolated from direct RLM hot path.
- compatibility remains explicit and test-covered.

### `daytona/`

Owns:

- stable Daytona facade imports;
- interpreter;
- sandbox operations;
- volume operations;
- file/workspace helpers;
- session state;
- diagnostics.

Final state:

- new code imports `fleet_rlm.daytona.*`.
- old `integrations.daytona.*` remains compatibility until safely retired.

### `skills/`

Owns:

- skill schemas;
- catalog;
- repository;
- loader;
- selection;
- active skill runtime object;
- validator;
- permissions;
- sync/materialization;
- writes/approval/audit;
- remote install/provenance/security/update lifecycle.

Final state:

- Skills are first-class product primitives.
- Visibility and trust rules are enforced before LLM selection.
- Skill references/scripts/assets are resource-indexed and loaded on demand.
- Scripts execute only in Daytona.

### `tools/`

Owns:

- canonical RLM-facing tools;
- registry descriptors;
- policy filtering;
- filesystem tools;
- skill tools;
- artifact tools;
- sandbox tools;
- attachment tools;
- optional web tools by policy.

Final state:

- `discover_tools()` remains the policy gate.
- write, sandbox, web, and skill-script tools are explicitly policy-controlled.

### `files/`

Owns:

- attachment upload staging;
- `AttachmentRef`;
- `AttachedFiles`;
- attachment resolution.

Final state:

- attachments are metadata-only by default;
- raw file contents are not injected automatically;
- client-facing payloads do not expose raw host or Daytona paths.

### `artifacts/`

Owns:

- artifact schemas;
- approved roots;
- path safety;
- Daytona-backed artifact I/O;
- large-output spill behavior.

Final state:

- generated outputs live under approved roots;
- client-facing references are safe;
- large tool outputs are spillable to artifacts.

### `observability/`

Owns:

- canonical observability events;
- recorder seam;
- span model;
- token usage extraction;
- MLflow adapter;
- redaction.

Final state:

- observability is backend-owned and shared by legacy and direct RLM.
- MLflow is optional, configurable, and not required for default tests.

### `traces/`

Owns:

- trace classifier;
- transcript/render mapping;
- performance summaries;
- trace feedback;
- MLflow span ingestion into trace/debug models.

Final state:

- frontend rendering relies on backend-mapped render kinds and component types.
- debug-only spans do not pollute the main transcript.

### `quality/`

Owns:

- evaluation datasets;
- metrics;
- GEPA runner;
- optimization results;
- promotion workflows.

Final state:

- GEPA runs offline/outside normal chat runtime.
- quality MLflow experiments are separated from runtime trace experiments unless explicitly configured.

## Final runtime behavior

The final runtime should support:

- `/api/chat` SSE as the canonical chat transport;
- WebSocket compatibility for execution/terminal/live sandbox behavior;
- direct RLM as the default after parity;
- legacy runtime fallback;
- safe Daytona execution;
- first-class Skills;
- policy-filtered Tools;
- durable Artifacts;
- staged Attachments;
- backend canonical Traces;
- optional MLflow integration;
- offline GEPA/quality optimization.

## Final public contracts

These contracts should remain stable unless explicitly migrated:

```text
POST /api/chat
WebSocket execution path
RuntimeEvent
SessionTraceDebugSpan
SessionTracePerformanceSummary
SessionTracePerformanceSpanSummary
SessionTraceItem
SessionTraceListResponse
TraceFeedbackRequest
RunStepItem
ActiveSkills
load_skill
AttachmentRef
AttachedFiles
Artifact refs
```

## Final safety rules

The final implementation must not allow:

- LLM-selected invisible skills;
- script execution outside Daytona;
- path traversal in skills/files/artifacts;
- raw provider/runtime errors leaked to clients;
- shared custom-interpreter RLM instances across concurrent runs;
- MLflow credentials or tracking URIs leaked to clients;
- GEPA inside normal chat turns;
- config import side effects.
