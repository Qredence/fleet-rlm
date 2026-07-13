# Verified framework contracts

Verified on 2026-07-11 through Context7 against current official DSPy, Daytona, and FastAPI sources.

This file records framework assumptions that Fleet converts into executable contract tests. It is not a substitute for a dependency lockfile.

## DSPy `dspy.RLM`

Current documented constructor:

```python
dspy.RLM(
    signature,
    max_iterations=20,  # installed dspy==3.3.0b1; docs sometimes say max_iters
    max_llm_calls=50,
    max_output_chars=10_000,
    verbose=False,
    tools=None,
    sub_lm=None,
    interpreter=None,
)
```

Documented behavior:

- `dspy.RLM` is a REPL-style recursive module for programmatic exploration of large context.
- The root LM generates Python actions iteratively.
- `llm_query(prompt)` and `llm_query_batched(prompts)` call `sub_lm`.
- `sub_lm` can be a different, cheaper model from the root LM.
- Plain callable functions and explicit `dspy.Tool` objects can be exposed to
  generated interpreter code.
- `SandboxSerializable` values can reconstruct host-approved data once inside
  the interpreter.
- `max_iterations`, `max_llm_calls`, and `max_output_chars` are built-in safety limits.
- If the loop ends without explicit submission, an extractor pass can derive final outputs from the trajectory.
- `aforward()` is the asynchronous counterpart to `forward()`.

Fleet implications:

1. `RLMModelBundle` separates root and sub-model roles.
2. `RLMFactory` always passes explicit finite budgets (`max_iterations`, not `max_iters`).
3. Tool names are stable Python identifiers.
4. Public SSE may expose bounded, sanitized model-authored RLM reasoning,
   generated code, and interpreter output. It never exposes provider-hidden
   chain-of-thought, full prompts, credentials, or raw provider traces.
5. Dependency upgrades fail contract tests when constructor parameters or result behavior change.
6. A fresh RLM instance is used for each concurrent turn; custom interpreter state is not shared between runs.
7. Fleet persists application-managed Session History and passes it to RLM as
   sandbox-safe `list[dict]`; `dspy.History` is not required by RLM.
8. Typed task contracts may replace the default Signature per Turn, and
   `SandboxSerializable` values are constructed only by registered host adapters.

Required contract tests:

```text
tests/contracts/test_dspy_contract.py
  - constructor exposes required parameters
  - sub_lm receives llm_query calls
  - max_llm_calls is enforced
  - custom tools are callable from interpreter code
  - result fields match the Fleet signature
  - concurrent runs do not share mutable RLM state
```

Official references:

- https://dspy.ai/api/modules/RLM/
- https://dspy.ai/diving-deeper/rlm/
- https://dspy.ai/tutorials/conversation_history/

## Daytona

Documented behavior:

- `DaytonaInterpreter` can be supplied as the `interpreter` for `dspy.RLM`.
- Generated Python executes inside an isolated Sandbox.
- REPL variables, imports, and functions persist across iterations while the interpreter context remains active.
- Host-side custom tools and sub-LM callbacks can be bridged into the Sandbox.
- Sandboxes expose create, start, stop, and delete operations.
- A stopped Sandbox must be started before reuse.
- A stopped Sandbox can be archived; archive preserves the full filesystem in lower-cost object storage.
- Snapshots preserve filesystem state for reuse.
- Volumes are mounted as read-write directories into Sandboxes.
- Volume content persists independently of Sandbox lifecycle and can be shared across Sandboxes.

Fleet implications:

1. Generated code and approved Skill scripts execute only in Daytona.
2. `RLMRunner` receives an `InterpreterLease`; `DaytonaSessionManager` owns lifecycle decisions.
3. Python globals are optimization state, not the recovery source.
4. Durable state is checkpointed to Fleet DB and the mounted Volume.
5. Shared Volume writes use unique staging paths and database-coordinated promotion because the filesystem is not a transaction manager.
6. Stop/start, pause/resume when supported, archive/restore when supported, and replacement are distinct recovery lanes.
7. Deleting a Sandbox must not delete the workspace Volume.

Required contract tests:

```text
tests/contracts/test_daytona_contract.py
  - create a Sandbox with the expected Volume mount
  - write through Sandbox A and read through replacement Sandbox B
  - stop and restart a Sandbox
  - recreate the interpreter context after lifecycle transition
  - generated Python cannot escape approved roots
  - releasing a lease does not imply Sandbox deletion
```

Official references:

- https://www.daytona.io/docs/en/guides/rlm/dspy-rlms/
- https://www.daytona.io/docs/en/sandboxes/
- https://www.daytona.io/docs/en/volumes/

## FastAPI SSE

Documented behavior:

- FastAPI supports Server-Sent Events through `EventSourceResponse`.
- An async iterable can yield typed event data.
- SSE support applies keepalive comments and anti-buffering headers.
- Application resources should be created and cleaned up through FastAPI lifespan.
- Dependencies using `yield` should perform cleanup in `finally`.
- `TestClient` should run inside a context manager when lifespan behavior matters.

Fleet implications:

1. `POST /api/chat` returns one typed async event stream.
2. SSE serialization lives behind `SSEProjector`; runtime code has no FastAPI imports.
3. The stream emits exactly one terminal event.
4. Client disconnect closes the upstream async iterator and releases the interpreter lease.
5. Application clients and pools are lifespan-managed, not created at import time.
6. The route authenticates and validates before beginning the stream whenever possible.

Required contract tests:

```text
tests/contracts/test_sse_contract.py
  - content type is text/event-stream
  - sequence numbers are monotonic
  - keepalive does not alter RuntimeEvent ordering
  - exactly one terminal event is emitted
  - disconnect closes the upstream iterator
  - lifespan creates and closes shared resources
```

Official references:

- https://fastapi.tiangolo.com/tutorial/server-sent-events/
- https://fastapi.tiangolo.com/advanced/events/
- https://fastapi.tiangolo.com/tutorial/dependencies/dependencies-with-yield/

## Upgrade policy

For every DSPy, Daytona, FastAPI, Starlette, or Pydantic upgrade:

1. Update the lockfile in one commit.
2. Run all framework contract tests.
3. Run the live RLM kernel smoke.
4. Run Daytona lifecycle and Volume recovery evidence.
5. Run the generated API-contract check.
6. Update this file if documented behavior changed.
7. Do not merge compatibility shims that silently drop unsupported constructor arguments.
