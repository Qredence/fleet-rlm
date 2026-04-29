# DSPy 3.x API Reference (v3.2.0)

> Pinned version in fleet-rlm: `dspy[optuna]==3.2.0` (pyproject.toml line 34)
>
> Upgrade notes from 3.1.3 → 3.2.0 (2026-04-29):
> - RLM/PythonInterpreter hardened (PR #9341, #9351): kwargs-only tool dispatch,
>   structured JSONRPC errors, subprocess replay of tool/mount registration.
> - `litellm` pinned to `>=1.64.0,<=1.82.6` by DSPy (PR #9498). Our direct
>   `litellm.completion()` in runtime/quality/scorers.py uses stable APIs.
> - `optuna` moved to optional extra (PR #9397); required for MIPROv2.
> - New `dspy.ContextWindowExceededError` replaces litellm errors in DSPy internals.
> - Input field type-mismatch warnings via `typeguard` (PR #9313); disable with
>   `dspy.configure(warn_on_type_mismatch=False)`.
> - `dspy.configure_cache(restrict_pickle=True)` available for safer disk cache.

---

## dspy.ReAct

Reasoning and Acting agent — iteratively reasons and calls tools.

### Constructor

```python
dspy.ReAct(
    signature: type[Signature] | str,  # e.g. "question -> answer"
    tools: list[Callable],             # functions, callables, or dspy.Tool instances
    max_iters: int = 20,               # max tool-call iterations
)
```

### Key behavior
- Converts raw callables to `dspy.Tool` internally
- Adds a built-in `finish` tool automatically
- Builds a `react_signature` with `trajectory`, `next_thought`, `next_tool_name`, `next_tool_args`
- Uses a `dspy.Predict` for the action step and `dspy.ChainOfThought` for fallback extraction
- Returns `dspy.Prediction(trajectory=dict, **extracted_outputs)`

### Methods
- `forward(**input_args)` — sync execution
- `aforward(**input_args)` — async execution (uses `tool.acall()`)
- `acall(**input_args)` — async entry point (Module base)
- `truncate_trajectory(trajectory)` — override for custom truncation logic
- `set_lm(lm)` / `get_lm()` — per-module LM control
- `save(path)` / `load(path)` — state persistence
- `batch(examples, num_threads=...)` — parallel execution via `dspy.Parallel`

### Usage in fleet-rlm
```python
# src/fleet_rlm/runtime/agent/agent.py
self.react = dspy.ReAct(
    signature=signature,   # RLMReActChatSignature
    tools=tools,           # list[dspy.Tool]
    max_iters=max_iters,
)
```

---

## dspy.Tool

Wraps a Python function for LLM tool calling.

### Constructor

```python
dspy.Tool(
    func: Callable,
    name: str | None = None,       # defaults to func.__name__
    desc: str | None = None,       # defaults to func docstring
    args: dict | None = None,      # JSON schema per arg (auto-inferred)
    arg_types: dict | None = None, # Python types per arg (auto-inferred)
    arg_desc: dict[str, str] | None = None,  # descriptions per arg
)
```

### Key features
- Auto-infers name, description, args from function signature + docstring
- `__call__(**kwargs)` — sync execution with validation
- `acall(**kwargs)` — async execution
- `format_as_litellm_function_call()` — LiteLLM-compatible tool schema
- `Tool.from_langchain(lc_tool)` — convert LangChain tools
- `Tool.from_mcp_tool(session, tool)` — convert MCP tools
- Async tools: use `acall()` or enable `dspy.configure(allow_tool_async_sync_conversion=True)`

### No `@dspy.tool` decorator
There is **no** `@dspy.tool` decorator in DSPy 3.x. Tools are created by:
1. Passing raw functions directly to `ReAct(tools=[my_func])` (auto-wrapped)
2. Explicitly wrapping: `dspy.Tool(my_func)`

---

## dspy.RLM (Recursive Language Model)

LLM explores large contexts via sandboxed Python REPL. Implements arXiv 2512.24601v2.

### Constructor

```python
dspy.RLM(
    signature: type[Signature] | str,  # e.g. "context, query -> answer"
    max_iterations: int = 20,          # max REPL loops
    max_llm_calls: int = 50,           # max llm_query/llm_query_batched calls
    max_output_chars: int = 10_000,    # max chars from REPL output
    verbose: bool = False,
    tools: list[Callable] | None = None,      # additional tools callable from REPL
    sub_lm: dspy.LM | None = None,            # LM for sub-queries (cheaper model)
    interpreter: CodeInterpreter | None = None, # custom interpreter (default: PythonInterpreter/Deno)
)
```

### Built-in REPL tools
- `llm_query(prompt)` — sub-LLM call for semantic analysis
- `llm_query_batched(prompts)` — concurrent sub-LLM calls
- `print()` — show output to LLM
- `SUBMIT(...)` — return final answer
- Standard library: `re`, `json`, `collections`, `math`

### Key behavior
- Input fields stored as REPL variables (not in prompt) — separates variable space from token space
- LLM sees metadata (type, length, preview) not full context
- Iterative: write code → execute → see output → repeat
- Fallback extraction if max_iterations reached
- Returns `Prediction` with output fields + `trajectory` list

### Methods
- `forward(**input_args)` — sync
- `aforward(**input_args)` — async
- `tools` property — user-provided tools (excludes internal llm_query etc.)

### Usage in fleet-rlm
```python
# src/fleet_rlm/runtime/models/builders.py
return dspy.RLM(**kwargs)  # with custom DaytonaInterpreter

# src/fleet_rlm/cli/runners.py
rlm = dspy.RLM(signature=sig, ...)
```

### Thread safety
Not thread-safe with custom interpreter. Default PythonInterpreter creates fresh instance per `forward()`.

---

## dspy.History

Conversation history for multi-turn chat.

### Constructor

```python
dspy.History(messages: list[dict[str, Any]])
```

- `messages` — list of dicts with keys matching the signature fields
- Pydantic BaseModel (frozen, validated)

### Usage pattern

```python
class ChatSig(dspy.Signature):
    question: str = dspy.InputField()
    history: dspy.History = dspy.InputField()
    answer: str = dspy.OutputField()

history = dspy.History(messages=[
    {"question": "What is X?", "answer": "Y"},
])
predict = dspy.Predict(ChatSig)
result = predict(question="Are you sure?", history=history)
```

### Usage in fleet-rlm
```python
# src/fleet_rlm/runtime/agent/runtime.py
self.history = dspy.History(messages=[])

# src/fleet_rlm/runtime/agent/chat_session_state.py
agent.history = dspy.History(messages=_enforce_history_cap(messages, max_turns))

# src/fleet_rlm/runtime/agent/signatures.py
history: dspy.History = dspy.InputField(desc="Prior chat turns...")
```

---

## dspy.streamify

Wraps a DSPy program to stream outputs incrementally via async generator.

### Signature

```python
dspy.streamify(
    program: Module,
    status_message_provider: StatusMessageProvider | None = None,
    stream_listeners: list[StreamListener] | None = None,
    include_final_prediction_in_output_stream: bool = True,
    is_async_program: bool = False,
    async_streaming: bool = True,
) -> Callable[..., AsyncGenerator]
```

### Stream output types
- `dspy.streaming.StreamResponse` — token chunks (when using StreamListeners)
- `dspy.streaming.StatusMessage` — progress/status updates
- `dspy.Prediction` — final result (last item)
- `ModelResponseStream` — raw LM stream chunks (no listeners)

### StreamListener

```python
dspy.streaming.StreamListener(signature_field_name="answer")
```
Captures streaming for specific output fields.

### StatusMessageProvider

```python
class MyProvider(dspy.streaming.StatusMessageProvider):
    def module_start_status_message(self, instance, inputs): ...
    def tool_end_status_message(self, outputs): ...
```

### Usage in fleet-rlm
```python
# src/fleet_rlm/runtime/execution/streaming.py
dspy.streamify(agent.react, stream_listeners=[...])

# src/fleet_rlm/runtime/agent/recursive_runtime.py
dspy.streamify(child_module, ...)
```

### Key imports used
```python
from dspy.streaming.messages import StatusMessage, StreamResponse
from dspy.streaming.streaming_listener import StreamListener
from dspy.streaming.messages import StatusMessageProvider
```

---

## dspy.Adapter

Controls how DSPy formats prompts for LMs.

### Built-in adapters
- `dspy.ChatAdapter(use_native_function_calling=False)` — default, text-based tool parsing
- `dspy.JSONAdapter(use_native_function_calling=True)` — JSON mode, native tool calling by default

### Configuration

```python
dspy.configure(
    lm=dspy.LM("openai/gpt-4o"),
    adapter=dspy.ChatAdapter(use_native_function_calling=True)
)
```

### Native tool calling
- `use_native_function_calling=True` — uses LM's built-in function calling
- Automatically checks `lm.supports_function_calling`
- Falls back to text parsing if model doesn't support it
- Not guaranteed to be higher quality than text-based parsing

---

## dspy.Module

Base class for all DSPy programs.

### Key methods (inherited by ReAct, RLM, etc.)
- `forward(**kwargs)` / `aforward(**kwargs)` — implement logic
- `__call__(**kwargs)` / `acall(**kwargs)` — entry points (with callbacks, usage tracking)
- `save(path)` / `load(path)` — state persistence (.json or .pkl)
- `save(path, save_program=True)` — full program serialization via cloudpickle
- `set_lm(lm)` / `get_lm()` — per-module LM
- `deepcopy()` — deep copy parameters, shallow copy rest
- `reset_copy()` — deep copy + reset all parameters
- `named_parameters()` / `parameters()` / `predictors()`
- `batch(examples, num_threads=...)` — parallel execution
- `map_named_predictors(func)` — apply function to all predictors
- `inspect_history(n=1)` — debug LM call history

---

## Other Key APIs

### dspy.configure / dspy.context

```python
dspy.configure(lm=..., adapter=..., track_usage=True)

with dspy.context(lm=other_lm):
    result = module(...)
```

### dspy.LM

```python
lm = dspy.LM(
    model="openai/gpt-4o",
    model_type="chat",       # "chat" | "text" | "responses"
    temperature=None,
    max_tokens=None,
    cache=True,
    num_retries=3,
)
```

### dspy.Predict / dspy.ChainOfThought

```python
predict = dspy.Predict("question -> answer")
cot = dspy.ChainOfThought("question -> answer")  # adds reasoning
```

### dspy.Signature

```python
class MySig(dspy.Signature):
    """Instructions here."""
    question: str = dspy.InputField()
    answer: str = dspy.OutputField(desc="short answer")
```

### dspy.Refine / dspy.BestOfN (replaced dspy.Assert/Suggest in 2.6+)

```python
dspy.Refine(module=qa, N=3, reward_fn=fn, threshold=1.0)
dspy.BestOfN(module=qa, N=3, reward_fn=fn, threshold=1.0)
```

### dspy.CodeAct

```python
act = dspy.CodeAct("n -> factorial", tools=[factorial])
result = act(n=5)
```

### dspy.asyncify

```python
async_program = dspy.asyncify(sync_program)
```

### dspy.Parallel

```python
parallel = dspy.Parallel(num_threads=4)
results = parallel([(module, example), ...])
```

### dspy.configure_cache

```python
dspy.configure_cache(enable_disk_cache=False, enable_memory_cache=False)
```

### Primitives

```python
from dspy.primitives import FinalOutput, CodeInterpreterError
```

### Callbacks

```python
from dspy.utils.callback import BaseCallback
```

---

## Optimizers (used in fleet-rlm)

```python
from dspy.teleprompt import GEPA, MIPROv2
from dspy.teleprompt.gepa.gepa_utils import ScoreWithFeedback
from dspy import Example, Prediction
```

---

## Breaking Changes & Gotchas in 3.x

### 3.0.0 (Aug 2025)
- `dspy.Assert` / `dspy.Suggest` replaced by `dspy.Refine` / `dspy.BestOfN` (since 2.6)
- New adapter system (`ChatAdapter`, `JSONAdapter`)
- `dspy.Tool` class formalized with `from_langchain`, `from_mcp_tool`
- Native function calling support via adapters

### 3.1.0 (Jan 2026)
- `dspy.RLM` module added — REPL-based recursive LLM
- `FinalAnswerResult` renamed to `FinalOutput`
- System instructions now supported in RLM
- GEPA optimizer improvements

### 3.1.1 (Jan 2026)
- RLM improvements: PythonInterpreter, system instructions
- `FinalOutput` stabilized in `dspy.primitives`

### 3.1.2–3.1.3 (Jan–Feb 2026)
- ReAct stabilization and tool-calling improvements
- Streaming refinements
- `ToolCall.execute()` method (since 3.0.4b2)

### Key gotchas
1. **No `@dspy.tool` decorator** — use `dspy.Tool(func)` or pass raw functions
2. **ReAct `max_iters` default is 20** (docs say 10 in some places — code says 20)
3. **RLM is experimental** — API may change
4. **RLM not thread-safe** with custom interpreters
5. **Streaming yields mixed types** — check `isinstance(value, dspy.Prediction)` for final result
6. **`dspy.History` is frozen** (Pydantic `frozen=True`) — create new instances, don't mutate
7. **Native tool calling** doesn't guarantee better quality than text-based
8. **Async tools** require `acall()` or `allow_tool_async_sync_conversion=True`
9. **`dspy.configure(track_usage=True)`** needed for token tracking
10. **Cache bypass**: use `rollout_id` + non-zero `temperature`
