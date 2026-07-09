# Why This Refactor Exists

## The current problem

Fleet already contains the ingredients of a strong agent backend:

- FastAPI transport;
- WebSocket execution;
- a legacy AgentRuntime;
- DSPy modules and RLM usage;
- Daytona sandbox execution;
- scaffold and volume-backed skills;
- runtime tools;
- traces and performance summaries;
- artifacts and attachments.

The issue is that many of these capabilities grew around the legacy runtime path. That makes the backend harder to reason about, harder to test, and harder to evolve toward direct `dspy.RLM` execution.

## The main architectural pain

The existing system has historically blurred boundaries between:

```text
AgentRuntime
EscalatingFleetModule
DSPy RLM
Daytona interpreter lifecycle
runtime tools
skill selection/loading
transport streaming
trace/debug/performance capture
frontend projection
```

That makes future changes risky because one change can accidentally affect several layers.

## The refactor solves this by making backend subsystems first-class

The target backend has explicit packages and ownership:

```text
api/          FastAPI transport, routes, schemas, dependencies, runtime seams
rlm/          direct RLM backend
runtime/      legacy runtime compatibility and RuntimeEvent contracts
daytona/      stable Daytona facade
skills/       skill catalog, loader, selection, script execution, writes, installs
tools/        canonical RLM-facing tools and policy registry
runtime/tools compatibility @tool_fn stubs and binding
files/        uploads, AttachmentRef, AttachedFiles, attachment resolution
artifacts/    artifact storage, safe refs, large-output spill
observability runtime spans/events/redaction/MLflow adapter
traces/       classifier, transcript mapping, performance, feedback
quality/      GEPA/evaluation lane
```

## Why preserve the legacy runtime

The legacy runtime should not be deleted early because it is the proven compatibility path.

It still protects:

- current WebSocket behavior;
- existing runtime event behavior;
- session restore behavior;
- existing tool execution paths;
- established test coverage;
- fallback during direct RLM rollout.

The goal is not to pretend legacy code is bad. The goal is to isolate it so the new direct RLM path can become clean.

## Why direct RLM is the target

The long-term product shape is closer to:

```text
user request + context + skills + attachments + tools
  -> dspy.RLM
  -> sandboxed Daytona execution
  -> traceable RuntimeEvents
  -> durable artifacts
```

That is a cleaner architecture than continuing to route every future capability through a broad legacy AgentRuntime.

## Why Skills, Tools, Files, and Artifacts had to come before Phase 6

Observability is only useful if it observes the right things.

Before Phase 6, Fleet needed clean backend subsystems for:

- skill selection and loading;
- trusted skill script execution;
- safe Daytona-backed tools;
- uploaded attachments;
- generated artifacts;
- large output spill;
- direct RLM inputs.

Now Phase 6 can trace real backend concepts instead of tracing legacy-specific implementation details.

## Why config.yaml is delayed

Typed config matters, but it should not be implemented before the architecture is stable.

Delaying config prevents premature modeling of settings that Phase 6 may clarify:

- MLflow config;
- trace/observability settings;
- runtime backend settings;
- quality/GEPA settings;
- Daytona lifecycle settings;
- tool exposure policy.

The plan intentionally moves config to an audit-first later phase.
