# ADR: Session-scoped RLM state

**Status:** accepted — implemented. P53 certification closes when the P35-E gate verifies the current clean candidate, including the separate P53.2 live Session manifest (`make p53-live-certification`, `make certification-gate`, then `make certification-verify`; ignored `.fleet-evidence/` evidence).
**Decision date:** 2026-08-11

Fleet will make committed Session conversation the durable authority and will pass
that conversation to later Turns as `dspy.History`.  A compatible, healthy,
resident Session runtime may reuse one Root `dspy.RLM`, one caller-owned
interpreter, and its Root Sandbox only for sequential successful Turns in the
same Workspace-and-Session scope.  This deliberately replaces the P41
per-Turn-Root behavior; [the sealed P41 freeze](../reference/behavior-freeze.md)
remains the historical baseline, while the [P42 versioned target freeze](../reference/p42-session-state-behavior-freeze.md)
records this change.

## Decision

Fleet distinguishes these four state classes:

| State | Scope | Required behavior |
| --- | --- | --- |
| Committed Session conversation | one durable Session | Every later Turn in that Session receives all committed user-facing exchanges; other Sessions do not. |
| Live Python state | one compatible resident Session runtime | Ordinary Python globals may survive sequential clean Turns only while the matching Root RLM/interpreter runtime remains healthy and resident. |
| DSPy `REPLHistory` | one RLM invocation | It is fresh for every Turn and retains only that invocation's iterative reasoning, code, and output. |
| Workspace and Memory | Session or Workspace Volume scope | They remain durable independently of the interpreter and are restored after runtime rotation. |

Only the canonical committed record `{"request": ..., "answer": ...}` belongs
in `dspy.History`. Hidden reasoning, generated Python, raw Tool data, provider
messages, internal errors, candidates, and uncommitted results are excluded.
The existing bounded previews and `read_session_history` Tool remain for
compatibility; neither replaces the complete committed History input.

A resident runtime is reusable only after RLM completion, result validation,
durable Turn commit, and a valid claim. A failure, cancellation, timeout, claim
loss, commit failure, authorization failure, or uncertain settlement taints it.
Before the next Turn Fleet rotates tainted state and rehydrates only durable
conversation, Workspace, Memory, Attachments, and Artifacts. Native child RLMs
always use isolated RLMs and interpreters; they never share the mutable Root
interpreter.

## Consequences

- The application, not DSPy, owns durable conversation construction and
  checkpoint-aligned History materialization.
- Reuse requires a per-Session sequential execution lane and per-Turn
  re-binding of request data, capabilities, output metadata, and Tool
  authorization.
- FastAPI remains the HTTP/SSE boundary. This decision does not change routes,
  OpenAPI, Runtime Event vocabulary/order, or SSE projection; its lifespan
  continues to own application-scoped resource composition and disposal.
- Eviction, replacement, restart, and a program-fingerprint change may remove
  arbitrary Python globals but must not remove committed conversation or
  Volume-backed state.
- The resident registry is process-local. The persistent Run claim prevents
  overlapping Turns for one Session across workers, but it does not promise one
  RLM/interpreter identity after a Session moves between processes. Deployments
  that require identity continuity must keep a Workspace+Session on one process
  owner or add an external resident-runtime registry.

## Evidence and references

- DSPy 3.3.1 `History` defines a frozen collection of message dictionaries
  keyed by its Signature fields: installed
  `dspy/adapters/types/history.py`; [DSPy History API](https://dspy.ai/api/utils/History/).
- DSPy 3.3.1 `RLM` documents sequential reuse of a caller-owned interpreter and
  creates a new `REPLHistory` for each `forward()`/`aforward()` call: installed
  `dspy/predict/rlm.py`; [DSPy RLM API](https://dspy.ai/api/modules/RLM/).
- FastAPI lifespan keeps startup/shutdown resource ownership explicit:
  [FastAPI lifespan events](https://fastapi.tiangolo.com/advanced/events/).
- The certified Fleet dependency is `dspy==3.3.1` in `pyproject.toml`; older
  plan references are not the current runtime evidence baseline.
