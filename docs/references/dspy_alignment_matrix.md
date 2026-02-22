# DSPy RLM Alignment Matrix

A parameter-by-parameter mapping of every `dspy.RLM` API surface to its corresponding implementation in the `fleet-rlm` codebase.

> [!NOTE]
> Sources: [dspy.ai/api/modules/RLM](https://dspy.ai/api/modules/RLM/), [primeintellect.ai/blog/rlm](https://www.primeintellect.ai/blog/rlm), [daytona.io/docs/en/guides/recursive-language-models](https://www.daytona.io/docs/en/guides/recursive-language-models/)

## Constructor Parameter Alignment

| `dspy.RLM` Parameter | Default                   | fleet-rlm Equivalent                                           | Location                                          |
| :------------------- | :------------------------ | :------------------------------------------------------------- | :------------------------------------------------ |
| `signature`          | `str \| Signature`        | `RLMReActChatSignature` + `CodeGenerationSignature`            | `react/agent.py` / `stateful/sandbox.py`          |
| `max_iterations`     | `20`                      | `rlm_max_iterations=30`                                        | `react/agent.py:66`                               |
| `max_llm_calls`      | `50`                      | `rlm_max_llm_calls=50`                                         | `react/agent.py:67` / `core/interpreter.py:158`   |
| `max_output_chars`   | `10000`                   | `stdout_summary_threshold=10000` + hard `MAX_CHARS=2000` guard | `core/interpreter.py:148` / `stateful/sandbox.py` |
| `verbose`            | `False`                   | `verbose=False`                                                | `react/agent.py:71`                               |
| `tools`              | `list[Callable] \| None`  | `build_tool_list()` + `_extra_tools`                           | `react/tools.py`                                  |
| `sub_lm`             | `dspy.LM \| None`         | `sub_lm` on `ModalInterpreter`                                 | `core/interpreter.py:150` / `core/llm_tools.py`   |
| `interpreter`        | `CodeInterpreter \| None` | `ModalInterpreter` (custom `CodeInterpreter`)                  | `core/interpreter.py`                             |

## Built-in Tool Alignment

| DSPy Built-in                       | fleet-rlm Implementation                   | Notes                                                     |
| :---------------------------------- | :----------------------------------------- | :-------------------------------------------------------- |
| `llm_query(prompt)`                 | ✅ `llm_query` via `LLMQueryMixin`         | Proxied through JSON over `stdin/stdout` to Modal sandbox |
| `llm_query_batched(prompts)`        | ✅ `llm_query_batched` via `LLMQueryMixin` | Concurrent sub-LLM calls with `ThreadPoolExecutor`        |
| `SUBMIT(...)`                       | ✅ `SUBMIT()` raises `FinalOutput`         | `core/driver_factories.py`                                |
| `Final` variable                    | ✅ `Final` variable convention             | `core/driver.py:278-287` — checked after each `exec()`    |
| `print()` output capture            | ✅ `StringIO` redirect                     | `core/driver.py:269`                                      |
| `re`, `json`, `math`, `collections` | ✅ Available (full Python 3.13 in Modal)   | Modal image has unrestricted stdlib                       |

## Execution Loop Alignment

| RLM Pattern            | DSPy Default                     | Prime Intellect                    | Daytona Deep RLMs                  | Daytona DSPy RLMs                    | **fleet-rlm**                               |
| :--------------------- | :------------------------------- | :--------------------------------- | :--------------------------------- | :----------------------------------- | :------------------------------------------ |
| **REPL Loop**          | Deno/Pyodide WASM                | Prime Sandboxes (Docker)           | Daytona SDK Workspaces             | `DaytonaInterpreter` (cloud sandbox) | Modal Sandbox (persistent Volume)           |
| **Iteration Cadence**  | Code → execute → stdout → repeat | Same + `answer["ready"]` diffusion | Same + `FINAL()` termination       | Same as DSPy (`SUBMIT()`)            | Same + `SUBMIT()` / `Final` var             |
| **Sub-LLM Routing**    | In-process tools                 | Tools only to sub-LLMs             | `rlm_query()` spawns depth+1 agent | Flask broker HTTP polling            | JSON over stdin/stdout                      |
| **State Persistence**  | Ephemeral (per-call)             | Ephemeral per rollout              | Per-workspace                      | Per-session (sandbox lifetime)       | ✅ Modal Volume at `/data/workspace`        |
| **Context Defense**    | `max_output_chars=10000`         | Implicit (sandbox isolation)       | Implicit truncation                | None                                 | Hard `MAX_CHARS=2000` + `_summarize_stdout` |
| **Answer Termination** | `SUBMIT(output)`                 | `answer["ready"] = True`           | `FINAL(answer)`                    | `SUBMIT(answer)` typed               | `SUBMIT()` / `Final` variable               |

## Unique fleet-rlm Extensions (Beyond DSPy Core)

| Extension              | Description                                                                         | Status         |
| :--------------------- | :---------------------------------------------------------------------------------- | :------------- |
| **Execution Profiles** | `ROOT_INTERLOCUTOR` / `RLM_DELEGATE` / `MAINTENANCE` profiles control tool exposure | ✅ Implemented |
| **Evolutive Memory**   | `@dspy.tool search_evolutive_memory` backed by Neon pgvector                        | ✅ Phase 1-2   |
| **Session History**    | `log_execution()` / `get_session_history()` persists execution traces               | ✅ Implemented |
| **Volume Ops**         | `workspace_write/read/list/append` + `save_to_volume/load_from_volume`              | ✅ Implemented |
| **Core Memory**        | Persona/Human/Scratchpad blocks via `CoreMemoryMixin`                               | ✅ Implemented |
| **Document Cache**     | `DocumentCacheMixin` for in-session document management                             | ✅ Implemented |
| **Streaming**          | `StreamEvent` typed events for real-time UI                                         | ✅ Implemented |
| **ReAct Supervisor**   | `dspy.ReAct` wrapping the RLM for conversational routing                            | ✅ Implemented |
