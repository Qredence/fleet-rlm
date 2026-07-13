# ADR-005: Staged Inline Context for RLM

## Status

Accepted

## Context

DSPy RLM is designed to keep large data in the REPL variable space while the
action prompt receives compact variable metadata and previews. Fleet-RLM already
uses `WorkspaceContext` / `SandboxSerializable` for this boundary, but oversized
pasted chat messages could still enter the RLM as the full `user_request`.

That shape is expensive and brittle: the same payload can be repeated in every
action-generation prompt, parse errors become more likely, and fallback paths can
accidentally send the original full text to a generic ChainOfThought responder.

## Decision

Fleet-RLM stages oversized inline payloads into `WorkspaceContext.document_text`
when a turn routes to `large_context_rlm`.

- `context_routing.py` detects large inline payloads, preferring an explicit
  `CONTEXT:` delimiter when present.
- The prompt-facing `user_request` is shortened to the user instruction plus a
  pointer to `context["document_text"]`.
- `EscalatingFleetModule` passes the shortened request to the workspace RLM and
  passes the full payload through `WorkspaceContext`.
- Staged long-context RLM failures do not fall back to generic CoT with the
  original payload.
- MLflow spans record staging and prompt-size metadata.

## Consequences

### Positive

- RLM action prompts stop repeatedly carrying oversized pasted payloads.
- Long-context analysis uses DSPy RLM's intended variable-space inspection model.
- Fallback behavior avoids a costly full-payload CoT retry after staged RLM
  failure.
- Trace metadata can prove whether a turn used staging and how much text was
  staged.

### Negative

- Staged requests depend on RLM availability. If the RLM path fails completely,
  Fleet returns a degraded response instead of attempting a generic fallback
  answer from the original payload.
- The shortened request must preserve enough instruction text for the RLM to
  understand the task before inspecting `context["document_text"]`.

### Neutral

- Existing file/path-based large-context routing continues to use the same
  `WorkspaceContext` transport.
- Scaffold skills must teach `context["document_text"]`,
  `context["manifest"]`, `context["metadata"]`, and `SUBMIT(response=...)` so
  downstream RLM users follow the same contract.

## References

- `src/fleet_rlm/runtime/modules/context_routing.py`
- `src/fleet_rlm/runtime/modules/escalating.py`
- `src/fleet_rlm/runtime/modules/factory.py`
- `src/fleet_rlm/runtime/sandbox_types.py`
- https://dspy.ai/api/modules/RLM/
- https://dspy.ai/diving-deeper/rlm/
