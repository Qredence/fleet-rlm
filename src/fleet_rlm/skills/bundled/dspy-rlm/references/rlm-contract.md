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
4. Fleet adds `rlm_query(prompt)` for one bounded child-RLM subproblem.
5. Host Tools (Fleet) are additional callables registered for the Turn.
6. `SUBMIT(...)` ends the RLM loop with typed Signature outputs.
7. If the loop ends without SUBMIT, DSPy may extract outputs from the trajectory.

For deterministic computation, parsing, search, or aggregation, use Python
directly. Reserve `llm_query(prompt)` for one bounded semantic judgment and
`llm_query_batched(prompts)` for multiple independent semantic judgments with
self-contained prompts. Load Fleet Host Capability bodies only when their
discovery metadata establishes relevance to the current request.
Keep each intermediate code action concise: prefer a few thousand characters,
keep large values in REPL variables or Session Workspace, and never paste a
long report or repeat the complete request in generated code. The Daytona
interpreter rejects an action above its 12,000-character safety bound with
bounded repair feedback so the next action can be smaller.
Use `rlm_query(prompt)` when the selected subproblem benefits from its own
bounded REPL loop. Keep large input-specific values in parent REPL variables,
pass only the smallest sufficient slice, and retain the child answer in a
parent variable. Child RLMs have fresh interpreter contexts and no Fleet
durable Tools; the Root remains responsible for the public typed submission.
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

- Normal primary Turns build a fresh native `dspy.RLM` with the active Fleet Signature. Greetings also
  use this native path. The default for RLM Turns is
  `FleetRLMSignature` (`answer: str`), but a selected Skill may supply additional required output fields.
- Fleet scopes the stock `dspy.JSONAdapter()` to each Turn. Provider-native
  token streams and sectioned text are not silently salvaged into RLM actions;
  malformed output is an `adapter_parse_error`. RLM action output contains
  `reasoning` and `code`; `completed` is internal loop state, not a Signature
  output field. Production Daytona model roles cap each response at 8,000
  tokens. The configured Databricks DeepSeek v4-free Root Model and Qwen Sub Model use the
  compatible Chat Completions path without a reasoning-effort override. This is
  separate from `max_output_chars`, which bounds REPL output retained in
  recursive history.
- **Daytona** (primary durable path): custom interpreter, Session Workspace tools, Artifact candidates promoted on Turn Commit.
- Recursive child calls are bounded by the policy keys `recursion_max_depth`, `recursion_max_calls`,
  `recursion_max_prompt_chars`, `recursion_child_max_iterations`,
  `recursion_child_max_llm_calls`, and `recursion_child_max_output_chars`.
- MLflow `RLM.*_lm` spans record recursive depth, call order, bounded context-size
  metadata, response shape, and per-call provider token usage when DSPy exposes it;
  the aggregate Turn usage remains on `RLM.execute`.
- MLflow `RLM.root_action` spans record each parsed iteration with bounded/redacted
  reasoning and code previews. Host Tools create nested `tool.*` spans with their
  allowlisted input/output projections, while `sandbox.execute` records the step's
  bounded code/output previews and execution timings. Full prompts, credentials, and
  unbounded generated content are not retained in these spans.
- Declared `answer` JSON must fit the Turn commit budget. Oversized SUBMIT fails with public message `Turn output is too large`. Prefer writing long reports to Session Workspace, then SUBMIT a short summary.

## SUBMIT

Use the typed binding for the active Signature and provide every required output field it exposes; for example,
the default accepts `SUBMIT(answer=...)`, while a selected Skill may require additional fields. Prefer keyword
arguments. Do not SUBMIT an entire oversized `llm_query` blob as `answer`.
