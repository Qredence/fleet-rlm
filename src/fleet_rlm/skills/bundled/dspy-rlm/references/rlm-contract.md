# dspy.RLM contract (Fleet / DSPy 3.3.0b1)

Authority: installed `dspy==3.3.0b1` and [dspy.ai RLM](https://dspy.ai/api/modules/RLM/). Do not treat Daytona provider docs as DSPy module authority.

## Name

**Recursive Language Model** — a REPL-style code agent. It is **not**:

- a Retrieval Language Model
- RAG / `dspy.Retrieve`
- `dspy.ReAct` (different module: reasoning + tool-calling loop)

## How it works

1. The Root LM inspects bound inputs and REPL history, then emits reasoning plus Python code.
2. Code runs in a sandboxed interpreter. Variables persist for the Run.
3. Built-ins include `llm_query(prompt)`, `llm_query_batched(prompts)`, and `SUBMIT(...)`.
4. Host Tools (Fleet) are additional callables registered for the Turn.
5. `SUBMIT(...)` ends the RLM loop with typed Signature outputs.
6. If the loop ends without SUBMIT, DSPy may extract outputs from the trajectory.

For deterministic computation, parsing, search, or aggregation, use Python
directly. Reserve `llm_query(prompt)` for one bounded semantic judgment and
`llm_query_batched(prompts)` for multiple independent semantic judgments with
self-contained prompts. Load Fleet Host Capability bodies only when their
discovery metadata establishes relevance to the current request.
Use keyword arguments for the one typed submission and provide every active
Signature output; for the default Signature, call `SUBMIT(answer=answer)`.
For nontrivial deterministic work, keep the initial computation and later
independent verification in separate iterations.

## Constructor knobs (DSPy defaults)

| Parameter | Default | Role |
|-----------|--------:|------|
| `max_iterations` | 20 | Max REPL iterations |
| `max_llm_calls` | 50 | Max sub-LM calls (`llm_query` / batched) |
| `max_output_chars` | 10000 | Truncates **REPL step output** fed back into the loop (not a silent truncate of SUBMIT) |

Fleet follows the installed DSPy 3.3.0b1 constructor, which uses
`max_iterations`. The current upstream documentation still shows `max_iters`;
do not copy that spelling into Fleet while this pin is active.

Fleet maps these via `FLEET_RLM_MAX_ITERATIONS`, `FLEET_RLM_MAX_LLM_CALLS`, and `FLEET_RLM_MAX_OUTPUT_CHARS`.

## Fleet mapping

- Every primary Turn builds a fresh native `dspy.RLM` with the active Fleet Signature. The default is
  `FleetRLMSignature` (`answer: str`), but a selected Skill may supply additional required output fields.
- Fleet scopes DSPy's stock native `JSONAdapter` to each Turn. RLM action output
  contains `reasoning` and `code`; `completed` is internal loop state, not a
  Signature output field.
- **Daytona** (primary durable path): custom interpreter, Session Workspace tools, Artifact candidates promoted on Turn Commit.
- **Deno**: vanilla local interpreter path without durable Artifact promotion; do not invent Deno-specific workflows here.
- Declared `answer` JSON must fit the Turn commit budget. Oversized SUBMIT fails with public message `Turn output is too large`. Prefer writing long reports to Session Workspace, then SUBMIT a short summary.

## SUBMIT

Use the typed binding for the active Signature and provide every required output field it exposes; for example,
the default accepts `SUBMIT(answer=...)`, while a selected Skill may require additional fields. Prefer keyword
arguments. Do not SUBMIT an entire oversized `llm_query` blob as `answer`.
