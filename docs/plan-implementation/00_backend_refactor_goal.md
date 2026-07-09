# Fleet RLM Backend Refactor — Explicit Goal

## One-sentence goal

The goal of the Fleet RLM backend refactor is to turn the current legacy-runtime-centered backend into a clean, observable, sandboxed, tool-capable, direct-`dspy.RLM` execution platform while preserving existing FastAPI, WebSocket, Skills, Daytona, trace, and artifact contracts until the new path proves parity.

## What this refactor is trying to achieve

Fleet should end with this backend shape:

```text
FastAPI transport
  -> ChatExecutionContext
  -> stream_turn()
  -> ExecutionBackend
  -> direct dspy.RLM by default, legacy AgentRuntime as fallback
  -> Daytona sandbox/interpreter
  -> RuntimeEvent
  -> trace/transcript/performance/MLflow recording
  -> AI SDK UIMessage SSE projection
  -> durable Skills, Tools, Attachments, Artifacts, Files
```

## Product goal

Fleet should become a backend where a user can ask for serious work, and the system can safely:

- reason through the task;
- use selected skills;
- inspect files and attachments;
- run code inside Daytona;
- create durable artifacts;
- record trace/performance/debug data;
- support later GEPA/quality optimization;
- stream output to the UI without coupling the UI to runtime internals.

## Engineering goal

The backend should stop mixing these concerns in the same legacy execution path:

```text
transport
runtime orchestration
DSPy/RLM execution
Daytona lifecycle
skills
files
tools
artifacts
trace/debug/performance
MLflow/quality
frontend projection
```

Each subsystem should have a clear package owner and a stable seam.

## Why not a single rewrite

A single broad rewrite would create too much simultaneous risk:

- `/api/chat` could break;
- WebSocket execution could break;
- Daytona session state could regress;
- Skills could stop loading;
- existing trace/debug contracts could drift;
- frontend projection could become backend-specific;
- default tests could start requiring live LLM, live Daytona, or MLflow;
- direct RLM could become default before parity.

Therefore the refactor follows:

```text
create seam -> preserve behavior -> migrate one runtime concern -> validate -> repeat
```

## Non-negotiable final contracts

By the final backend state, these must remain stable or be explicitly migrated:

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

## Current roadmap position

The backend is complete through Phase 5. The next named phase is:

```text
Phase 6 — Trace, Transcript, Performance, and MLflow
```

Phase 6 is necessary before the direct RLM path can safely become the default.
