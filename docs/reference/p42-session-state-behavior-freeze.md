# P42 session-state behavior freeze

**Status:** versioned target contract — certification pending P53.  
**Supersedes:** only the P41 *Native RLM execution per Turn* behavior after
implementation and certification.  It does **not** rewrite the sealed
[P41 behavior freeze](behavior-freeze.md), which remains the historical,
certified baseline for `1801d24a938eda37b53ebb2f543fd01e3c6bdaf6`.

This contract records the approved product behavior before implementation.
It permits private module restructuring but freezes the observable state
semantics that P43–P53 must prove. The exact evidence baseline is
`dspy==3.3.1`, the dependency pinned by `pyproject.toml`.

## Versioned behavior change

| P41 baseline | P42 target contract |
| --- | --- |
| One native Root `dspy.RLM` and caller-owned interpreter context per Turn. | One compatible native Root `dspy.RLM`, caller-owned interpreter, and Root Sandbox per active Session runtime. |
| Six recent Session previews are supplied; older committed content is available through `read_session_history`. | Complete committed `dspy.History` is supplied every Turn; recent previews and `read_session_history` remain compatible navigation and retrieval surfaces. |
| Interpreter globals end with each Turn. | User-created globals may persist through sequential clean Turns while the runtime is compatible, healthy, and resident. |

## Frozen target state contract

| Behavior | Required target behavior | Proof class |
| --- | --- | --- |
| Durable conversation | `dspy.History` contains every and only committed user-facing request/answer record for the claimed Session checkpoint. | P43/P44 deterministic and Daytona history tests |
| Session isolation | A History snapshot and resident state are keyed by Workspace plus Session; neither crosses a Session boundary. | P44/P45 isolation tests |
| Per-Turn reset | DSPy `REPLHistory`, iteration budget, LLM-call budget, current request, current capability binding, attachments, and output metadata are fresh each Turn. | P43/P45 tests |
| Clean reuse | The same Root RLM, caller-owned interpreter, and Root Sandbox are reused only after a validated, durably committed Turn and only in one sequential lane. | P43/P45 continuity tests |
| Taint and recovery | Failure, cancellation, timeout, claim loss, commit failure, authorization failure, or uncertain settlement taints resident state; the next Turn rotates it and rehydrates durable state only. | P45/P49 taint tests |
| Eviction | Idle eviction or process/Sandbox replacement may lose arbitrary Python globals but retains committed History and Volume-backed Workspace, Memory, Attachments, and Artifacts. | P45/P52 recovery tests |
| Child isolation | Native child RLMs use fresh child RLM/interpreter/Sandbox state and never share mutable Root interpreter globals. | P47/P52 child tests |
| Tool authority | A retained Tool object or Python alias resolves authorization for the current Turn and fails closed when no current capability authorizes it. | P45/P52 security tests |

## Public behavior retained

The following behavior remains unchanged throughout the migration:

- FastAPI routes, response schemas, OpenAPI, and generated TUI types;
- Runtime Event vocabulary, identity, ordering, and one-terminal semantics;
- SSE chunk vocabulary and projection, including the maintained pi-tui live and
  durable projections;
- model-facing Tool names, descriptions, schemas, availability policy,
  authorization policy, and bounded event projection;
- native child-RLM depth, batching, shared budgets, ordering, cleanup, and
  Sub-LM fallback semantics;
- Session Workspace, Workspace Memory, Skills, Attachments, Artifacts, Run
  claims, cancellation, deadlines, replay, packaging, and CLI contracts.

The P42 Tool-contract fixture will make the model-facing Tool portion
independently reviewable. It is a required P42 deliverable, not permission to
alter a Tool contract.

## Normative exclusions

This contract does not persist arbitrary Python objects across restart, carry
DSPy's private `REPLHistory` between Turns, save hidden reasoning as
conversation, remove child RLMs, rename Tools, or change public transport.
Important results that must survive rotation are written explicitly to Session
Workspace, Projects, Workspace Memory, or Artifacts.

## Framework basis

DSPy 3.3.1 defines `dspy.History` as Signature-keyed message dictionaries and
supports a caller-owned interpreter for sequential calls to one RLM instance;
its RLM creates `REPLHistory` inside each invocation. See the installed
`dspy/adapters/types/history.py` and `dspy/predict/rlm.py`, plus the
[DSPy History API](https://dspy.ai/api/utils/History/) and
[DSPy RLM API](https://dspy.ai/api/modules/RLM/). FastAPI remains the
application transport/lifespan boundary; see [FastAPI lifespan events](https://fastapi.tiangolo.com/advanced/events/).
