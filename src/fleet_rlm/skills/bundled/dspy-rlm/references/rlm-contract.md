# dspy.RLM contract (Fleet / DSPy 3.4.0)

Authority: the exact pinned [DSPy 3.4.0 RLM source](https://raw.githubusercontent.com/stanfordnlp/dspy/3.4.0/dspy/predict/rlm.py) and installed implementation. The rolling [RLM API](https://dspy.ai/api/modules/RLM/) is orientation; do not treat Daytona provider docs as DSPy module authority.

## Name

**Recursive Language Model** — a REPL-style code agent. It is **not**:

- a Retrieval Language Model
- RAG / `dspy.Retrieve`
- `dspy.ReAct` (different module: reasoning + tool-calling loop)

## How it works

1. The Root LM inspects bound inputs and REPL history, then emits reasoning plus Python code.
2. Code runs in a sandboxed interpreter. Broker Root Sandbox Python state may
   persist across sequential clean Turns while that Sandbox remains healthy.
3. Built-ins include `llm_query(prompt)`, `llm_query_batched(prompts)`, and `SUBMIT(...)`.
4. Fleet adds `rlm_query(task=task, inputs=inputs, context="")` and Root-only `rlm_query_batched(tasks=[...])` for bounded child-RLM subproblems when policy enables recursion. Each ordered task has `task`, a bounded list of authorized relative file or directory paths in `inputs`, and optional `context`. The host stages a checked copy in the child's private work area. Use native `llm_query` / `llm_query_batched` for bounded semantic judgments, including when Fleet children are disabled.
5. Host Tools (Fleet) are additional callables registered for the Turn.
6. `SUBMIT(...)` ends the RLM loop with typed Signature outputs.
7. If the loop ends without SUBMIT, DSPy may extract outputs from the trajectory.

For deterministic computation, parsing, search, or aggregation, use Python
directly. Reserve `llm_query(prompt)` for one bounded semantic judgment and
`llm_query_batched(prompts)` for multiple independent semantic judgments with
self-contained prompts. When the request already specifies those prompt
strings, pass them unchanged and in the given order; do not paraphrase,
expand, or replace them. Load Fleet Host Capability bodies only when their
discovery metadata establishes relevance to the current request.
Keep each intermediate code action concise: prefer a few thousand characters,
keep large values in REPL variables or Session Workspace, and never paste a
long report or the complete request as unused text. When the request specifies
exact Python statements or Sub-LM prompt strings, emit those statements in
that order with those strings unchanged; do not omit listed accumulator updates.
The Daytona interpreter rejects an action above its 12,000-character
safety bound with bounded repair feedback so the next action can be smaller.
Use `rlm_query(task=task, inputs=["relative/path"], context="")` when an
independent subproblem benefits from its own bounded REPL loop. Keep large
evidence in interpreter variables or files; select only the needed authorized
paths. Inspect the child's runtime status separately from its model-submitted
`answer`, located `evidence`, `gaps`, and optional `result_files`. Child RLMs
have fresh private interpreter contexts and a reduced tool set. The Root
remains responsible for the public typed submission. Skill text cannot grant
tools, file scope, credentials, recursion depth, or budget.
Use keyword arguments for the one typed submission and provide every active
Signature output. The default `answer` output is a string: call
`SUBMIT(answer=answer)` for text, but serialize a mapping or list first with
`SUBMIT(answer=json.dumps(answer, ensure_ascii=False))`; use `indent=2` only
when the formatted value fits the Turn output character budget. Passing a
mapping or list directly produces Python `repr` text.
For nontrivial deterministic work, keep the initial computation and later
independent verification in the same action when practical. Use a later
iteration only when the verification genuinely cannot be completed alongside
the computation; never spend a later iteration merely restating an already
verified result. Completing a verification helper does not finish the Turn
while named host-tool work remains: call requested Session Workspace writes
and artifact publishes before `SUBMIT`. Sandbox-local `open()` is not
Session Workspace.

## Constructor knobs (DSPy defaults)

| Parameter | Default | Role |
|-----------|--------:|------|
| `max_iters` | 20 | Max REPL iterations |
| `max_llm_calls` | 50 | Max sub-LM calls (`llm_query` / batched) |
| `max_output_chars` | 10000 | Truncates **REPL step output** fed back into the loop (not a silent truncate of SUBMIT) |

These are the generic DSPy constructor and `RLMOptions` fallback values, not
the effective Fleet policy. The single shipped configuration uses Root values `12`, `32`, and `6000`; its child RLM values
are `8`, `12`, and `4000`. Treat `config/fleet.toml` as authoritative for the runtime configuration.

Fleet uses DSPy 3.4.0's `max_iters` spelling end-to-end: `RLMOptions.max_iters`,
`Settings.rlm_max_iters`, and the TOML policy key `rlm.max_iters` are passed
directly to `dspy.RLM(max_iters=...)` in `rlm.program` with no alias.
Settings resolve only from the selected TOML policy; ambient `FLEET_*`
environment variables are ignored.

## Fleet-to-DSPy construction and ownership

| Fleet surface | Fleet value | DSPy 3.4.0 surface |
|---|---|---|
| Fleet iteration budget | max_iters | max_iters |
| Native construction | build_native_rlm(..., interpreter_factory=...) with a fresh invocation factory | dspy.RLM(..., interpreter_factory=...) |
| Native async execution | await rlm.acall(**named_inputs) | DSPy calls the zero-argument factory for this invocation |
| Factory result | One invocation-scoped interpreter | DSPy injects execution tools and output metadata |
| Shutdown | DaytonaRuntime retains Sandbox ownership; the invocation factory yields one adapter | DSPy shuts down the factory-created interpreter |

Fleet native RLM construction is fail-closed and requires the explicitly
selected Fleet interpreter factory instead of DSPy's default interpreter.
Each call must return a fresh invocation adapter; do not reuse an adapter
across calls because DSPy injects mutable execution bindings and shuts the
adapter down when the call settles. DaytonaRuntime retains ownership of the
provider Sandbox and its cleanup. Deterministic _TestingRLM substitutes stay
keyword-only.

## Fleet mapping

- Normal primary Turns use one compatible native `dspy.RLM` per Run; a changed
  program, taint, eviction, or failure creates a replacement. Greetings also use this native path. The default for RLM Turns is
  `FleetRLMSignature` (`answer: str`), but a selected Skill may supply additional required output fields.
- Fleet scopes `FleetJSONAdapter`, a DSPy JSON adapter with bounded corrective
  re-asks and shared deadline/budget accounting, to each Turn. Wrap-up starts
  on the final native iteration. When wrap-up is enabled, last-iteration
  exhaustion retains the existing Turn `timeout` status and identifies the
  `finalization_attempts` dimension in diagnostics. Native extraction still runs
  if the loop completes without a typed SUBMIT result; adapter exhaustion
  propagates instead of triggering extraction. Empty or reasoning-only completions stay
  on the bounded parse re-ask path while time and iterations remain. It
  preserves the pinned action grammar; exhausted repair is an
  `adapter_parse_error`. RLM action output contains
  `reasoning` and `code`; `completed` is internal loop state, not a Signature
  output field. The configured Root and Sub Models
  (through the policy-configured Databricks Chat Completions gateway) cap Root
and Sub at 16,384 output tokens with no
reasoning-effort override. This is separate from `max_output_chars`, which
bounds REPL output retained in recursive history.
- **Daytona** (primary durable path): custom interpreter, Session Workspace tools, Artifact candidates promoted on Turn Commit.
- Recursive child calls are bounded by the policy keys `recursion_max_calls`,
  `recursion_max_prompt_chars`, `recursion_child_max_iters`,
  `recursion_child_max_llm_calls`, and `recursion_child_max_output_chars`.
- MLflow `RLM.*_lm` spans record recursive depth, call order, bounded context-size
  metadata, legacy list/dict response shape (`text`, `reasoning_content`), and
  per-call provider token usage when DSPy or the stored history entry exposes it;
  the aggregate Turn usage remains on `RLM.execute`. Missing provider usage stays
  `unavailable`, never a fabricated zero.
- MLflow `RLM.root_action` spans record each parsed iteration with bounded/redacted
  reasoning and code previews. Host Tools create nested `tool.*` spans with their
  allowlisted input/output projections, while `sandbox.execute` records the step's
  bounded code/output previews and execution timings. Full prompts, credentials, and
  unbounded generated content are not retained in these spans.
- Declared `answer` JSON must fit the Turn commit budget. Oversized SUBMIT fails with public message `Turn output is too large`. Prefer writing long reports to Session Workspace, then SUBMIT a short summary.

DSPy 3.4.0's final namespace, Tool, and sub-LM response validation is
authoritative. Fleet host Tools preserve their own bounded validation and event
views; generated Tool calls use keyword arguments, including
`rlm_query(task=..., inputs=..., context=...)`.

## SUBMIT

Use the typed binding for the active Signature and provide every required output field it exposes; for example,
the default accepts `SUBMIT(answer=...)`, while a selected Skill may require additional fields. Prefer keyword
arguments. Do not SUBMIT an entire oversized `llm_query` blob as `answer`.
