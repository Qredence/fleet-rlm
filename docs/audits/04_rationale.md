# Fleet RLM Refactor — Rationale

## Why this refactor exists

Fleet RLM is moving from a legacy runtime-centered backend toward a clearer architecture where FastAPI, DSPy RLM, Daytona, Skills, Tools, Artifacts, Attachments, Traces, MLflow, and Quality each have explicit ownership boundaries.

The refactor is intentionally phased because a single broad rewrite would risk breaking transport behavior, runtime execution, tool access, skill visibility, Daytona state, and traceability at the same time.

## Core architectural rule

The central rule is:

```text
create seam -> preserve behavior -> migrate one runtime concern -> validate -> repeat
```

This keeps each phase bounded and reviewable.

## Why preserve the legacy runtime

The legacy AgentRuntime remains the default until direct RLM proves parity.

Reasons:

- it already supports existing WebSocket and chat behavior;
- it carries established runtime event behavior;
- it reduces migration risk;
- it provides a fallback while direct RLM matures;
- it allows direct RLM to become clean rather than becoming AgentRuntime v2.

## Why add direct RLM behind a seam

Direct RLM is the long-term cleaner execution path, but it must be opt-in until it proves golden paths and parity.

The execution backend seam allows Fleet to run:

```text
stream_turn() -> legacy_agent_runtime
stream_turn() -> direct_rlm
```

without changing public request schemas or transport projection.

## Why make Skills first-class

Skills are not just markdown snippets. They are runtime capability bundles that may include instructions, references, scripts, assets, trust metadata, visibility rules, and provenance.

Making Skills first-class enables:

- progressive disclosure;
- user/session/org/project scoped skills;
- safe resource reads;
- trusted script execution inside Daytona;
- skill write and approval workflows;
- remote installs with provenance and security scanning;
- traceable skill selection.

## Why make Daytona a facade package

Daytona is a substrate dependency, not an implementation detail of legacy runtime modules.

A canonical `fleet_rlm.daytona.*` facade gives direct RLM and future runtime code a stable import surface while preserving compatibility with `integrations.daytona.*`.

## Why Tools, Artifacts, and Attachments were separated

Tools, artifacts, and attachments are distinct concerns:

- tools are callable operations exposed to DSPy/RLM;
- artifacts are durable generated outputs;
- attachments are user-provided files staged into a safe runtime context.

Separating them makes it possible to enforce different policies for read tools, write tools, sandbox tools, artifact roots, upload roots, and path safety.

## Why Phase 6 is next

After Phase 5, Fleet has durable runtime capabilities. The next bottleneck is observability.

Phase 6 is necessary because trace, transcript, performance, and MLflow behavior must become canonical before:

- direct RLM can become the default;
- frontend rendering can rely on backend trace mapping;
- GEPA/quality work can reuse reliable runtime measurements;
- debugging can compare legacy and direct RLM runs.

## Why MLflow is explicit

MLflow is not merely optional logging. It should be a trace backend and quality/optimization integration point.

It is relevant for:

- runtime span timing;
- LLM/tool span tracking;
- RLM iteration tracking;
- adapter fallback tracking;
- parse error tracking;
- token usage;
- performance summaries;
- quality/evaluation runs;
- GEPA optimization tracking.

But MLflow must not become a hard runtime dependency. Default tests and local dev must work without an MLflow server.

## Why temporary breakage can be acceptable

During active refactor implementation, temporary local app breakage can be acceptable if it enables a cleaner final architecture.

However, the final committed state must restore public contracts unless the user explicitly approves a red validation state.

Temporary breakage is acceptable for:

- internal imports;
- local frontend usability;
- intermediate `/api/chat` state;
- intermediate OpenAPI drift;
- failing tests while modules are being moved.

Final committed breakage is not acceptable for:

- `POST /api/chat`;
- WebSocket execution;
- `RuntimeEvent` compatibility;
- trace/performance contracts;
- `ActiveSkills`;
- `load_skill`;
- MLflow optionality;
- client error sanitization.

## Why config is deferred

Typed config and `config.yaml` matter, but they should follow the observability foundation.

Reasons:

- Phase 6 will clarify observability and MLflow config needs;
- config imports must avoid runtime side effects;
- env-only workflows must remain supported;
- DB/runtime settings may remain authoritative for user/workspace/profile scope.

## Why GEPA is deferred

GEPA belongs in a quality/optimization lane, not the normal chat hot path.

It should reuse Phase 6 observability and MLflow foundations, then store optimization results separately from production chat traces.

## Why direct RLM default switch is later

Direct RLM should become the default only after:

- simple chat works;
- Daytona file tasks work;
- ActiveSkills work;
- attachments work;
- runtime event parity exists;
- trace/performance summaries work;
- MLflow spans work when enabled;
- manual live Daytona + LLM validation passes;
- legacy fallback remains available.
