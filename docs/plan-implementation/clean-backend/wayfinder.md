# Fleet RLM backend-foundation wayfinder

> Historical planning guide. The hard cutover is complete; active code is
> `src/fleet_rlm/`. References to `fleet_rlm_clean` describe the pre-cutover
> delivery sequence only.

## Purpose

Use this file to decide where a change belongs, which delivery phase owns it, what evidence is required, and which architectural boundaries may not move.

Every proposal must answer:

> How does this change improve, operate, protect, or evaluate the FastAPI SSE -> `dspy.RLM` -> Daytona Sandbox/Volume loop?

If it does none of those things, defer it.

## Find the owning document

| Question | Read or update |
|---|---|
| What should the product do? | `to-spec.md` |
| Which package owns a responsibility? | `codebase-design.md` |
| What is the implementation order? | `to-tickets.md` |
| What blocks approval? | `code-review.md` |
| What do current frameworks guarantee? | `context7-contracts.md` |
| What supersedes the migration roadmap for a clean build? | This directory |
| Where is active backend code now? | `src/fleet_rlm/` |

## Priority ladder

Use this order when scope competes:

1. A real `dspy.RLM` turn through FastAPI SSE and Daytona.
2. Stateful recovery with `dspy.History`, transactional checkpoints, and mounted Volume content.
3. Progressive Skills, attachments, and artifacts.
4. Authentication, budgets, cancellation, redaction, idempotency, and observability.
5. Long-context memory retrieval and consolidation.
6. Skill evolution and GEPA optimization.

A later layer must not delay an earlier working layer unless it closes a concrete correctness or security blocker.

## Decision tree

```text
Does the change affect public chat behavior?
  yes -> to-spec.md + Phase 1 or 4 + SSE/API contract tests
  no

Does it change one recursive RLM turn?
  yes -> rlm/ ownership + Phase 1 + DSPy contract tests
  no

Does it change Sandbox, interpreter, Volume, or lifecycle behavior?
  yes -> daytona/ ownership + Phase 1 or 2 + live Daytona evidence
  no

Does it change session restore or durable state?
  yes -> sessions/ and persistence/ + Phase 2
  no

Does it add a capability visible to the RLM?
  yes -> Phase 3 + deterministic authorization + RuntimeEvent evidence
  no

Does it learn from histories or trajectories?
  yes -> Phase 5 or 6; never normal chat execution
```

## Runtime roles

### Root LM

Owns task interpretation, recursive trajectory strategy, Python generation, Skill activation decisions, evaluation of observations, and final synthesis.

### Sub-LM

Owns bounded semantic work initiated through `llm_query` or `llm_query_batched`: extraction, classification, summarization, comparison, and chunk analysis.

The sub-LM does not gain authorization, inspect hidden state automatically, or choose its own model role.

### Utility LM

Optional after the kernel exists. It may rank already-authorized SkillCards or produce a rolling summary. It does not control the recursive trajectory.

## State tiers

| Tier | Examples | Durability rule |
|---|---|---|
| Active interpreter | Python variables, imports, functions | Useful while active; never the sole durable copy |
| Sandbox-local filesystem | checkout, caches, installed packages | Retained according to Sandbox lifecycle; replaceable |
| Mounted Daytona Volume | artifacts, Skill bundles, session exports, large memory bodies | Durable independently of Sandbox lifecycle; not transactional |
| Fleet database | identities, permissions, checkpoints, versions, indexes, provenance | Transactional governance and recovery source |

## Lifecycle ownership

`RLMRunner` acquires and releases an `InterpreterLease`. Releasing a lease does not mean deleting a Sandbox.

`DaytonaSessionManager` owns lifecycle transitions:

```text
missing -> create -> running
stopped -> start -> running
paused -> resume -> running, when supported
archived -> restore -> running, when supported
unrecoverable -> replace -> mount existing Volume -> restore checkpoint
```

Before stop, pause, archive, or replacement, important state must already exist in the database or mounted Volume.

## Capability disclosure

```text
authorized SkillCards
  -> RLM requests load_skill
  -> Fleet checks scope, trust, and version
  -> SKILL.md becomes available
  -> RLM requests one resource
  -> Fleet exposes only that resource
  -> approved scripts execute only in Daytona
```

The model may choose among authorized capabilities. It never grants itself visibility or trust.

## Change-impact map

| Change | Required companion work |
|---|---|
| RuntimeEvent schema | SSE tests, UI contract, JSON schema, review checklist |
| DSPy version | `context7-contracts.md`, constructor test, live RLM smoke |
| Daytona version | lifecycle and Volume tests, live recovery evidence |
| Session schema | migration, checkpoint compatibility, restore test |
| Skill format | loader, authorization, and progressive-disclosure tests |
| Artifact path policy | traversal tests, Volume recovery, download contract |
| Model roles | usage attribution, budgets, traces, promotion evidence |

## Reject or defer before the foundation is complete

- A generic multi-agent framework around `dspy.RLM`.
- A user-selectable model provider, Sandbox ID, or Volume path.
- Automatic production Skill mutation.
- Online GEPA inside chat turns.
- Direct provider calls from generated Sandbox code.
- A second transcript transport.
- A second implementation of session state.
- Shared mutable `dspy.RLM` instances.
- Broad abstract brokers without concrete capability users.
- Rich trace, memory, or Skill administration UI before the backend acceptance flow passes.

## Definition of ready

A ticket is ready only when it has one owning phase, exact files, defined interfaces, a failing test first, observable behavior, failure and rollback behavior, and named evidence for closure.

## Definition of done

A ticket is done only when code and tests are committed together, required static and live lanes pass, generated contracts are synchronized, evidence names the exact commit and configuration, and `code-review.md` has no unresolved blocker owned by the ticket.
