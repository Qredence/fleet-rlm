# Why Focus on the RLM Mode

## One-sentence rationale

Fleet focuses on direct `dspy.RLM` because the product is fundamentally a sandboxed reasoning-and-action system, and RLM is the cleanest execution primitive for turning user intent, skills, tools, attachments, and Daytona filesystem state into traceable work.

## What “RLM mode” means here

RLM mode means the backend executes a turn through a direct DSPy RLM path:

```text
ChatExecutionContext
  -> direct_rlm ExecutionBackend
  -> DirectRLMRunner
  -> dspy.RLM
  -> DaytonaInterpreter
  -> RuntimeEvent stream
```

It does not mean removing legacy runtime immediately.

The correct transition is:

```text
legacy_agent_runtime default today
  -> direct_rlm opt-in golden path
  -> direct_rlm parity
  -> direct_rlm default later
  -> legacy fallback retained
```

## Why RLM fits Fleet better than a generic chat agent loop

Fleet needs more than answer generation.

It needs to:

- inspect files;
- use selected skills;
- call tools;
- run code;
- work inside a sandbox;
- reason over intermediate results;
- produce durable artifacts;
- record trajectory and performance;
- support quality optimization later.

That is exactly the shape of an RLM-style execution loop.

## Why direct RLM should own the future hot path

The legacy AgentRuntime grew to orchestrate many concerns. It works, but it is broad.

Direct RLM gives a cleaner execution center:

```text
input construction
active skills
attached files
tool registry
Daytona interpreter
RLM trajectory
RuntimeEvent conversion
trace/performance capture
```

Those concepts map directly to Fleet’s product requirements.

## Why not focus on the legacy AgentRuntime instead

Continuing to add all future features to AgentRuntime would keep increasing coupling between:

- transport;
- skill selection;
- runtime modules;
- DSPy RLM;
- Daytona interpreter lifecycle;
- streaming;
- trace/debug spans;
- artifacts;
- frontend projection.

That would make the backend harder to test and harder to optimize.

The plan is not to discard AgentRuntime. The plan is to make it an explicit compatibility backend.

## Why direct RLM is still opt-in

Direct RLM must not become default until it proves parity.

It must support:

- simple chat;
- Daytona workspace/file tasks;
- selected skills;
- trusted skill scripts;
- attachments;
- artifacts;
- structured errors;
- RuntimeEvent parity;
- trace/performance summaries;
- MLflow spans when enabled;
- live Daytona + LLM smoke tests;
- legacy fallback.

Until then, `legacy_agent_runtime` remains default.

## Why custom RLM lifecycle matters

When using a custom interpreter, RLM instances must not be shared unsafely across concurrent turns.

Fleet should create per-turn/per-interpreter RLM instances rather than keeping a global custom-interpreter RLM singleton.

This protects:

- Daytona interpreter state;
- sandbox session isolation;
- concurrent user turns;
- tool execution correctness;
- trace attribution.

## Why RLM mode depends on Skills, Tools, Files, Artifacts, and Traces

Direct RLM is only valuable when it has controlled capabilities.

That is why the roadmap built these first:

```text
Skills      -> task-specific instructions/resources/scripts
Tools       -> safe callable operations
Files       -> uploaded attachment metadata and staging
Artifacts   -> durable generated outputs
Daytona     -> sandbox execution substrate
RuntimeEvent -> backend-neutral stream/event contract
```

Phase 6 now adds:

```text
Observability -> traces, transcript mapping, performance, MLflow
```

## Final RLM default-switch condition

Make direct RLM default only when:

- direct RLM handles common user flows;
- direct RLM and legacy produce compatible runtime events;
- trace/debug/performance works for both paths;
- MLflow remains optional;
- frontend chat can rely on `/api/chat` SSE;
- legacy fallback is still available.
