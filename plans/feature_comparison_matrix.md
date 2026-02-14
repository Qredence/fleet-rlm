# Feature Comparison Matrix — RLM Frameworks

> Comparing fleet-rlm v0.4.1 against DSPy RLM, Daytona RLM, and DSPy CodeAct.

---

## Core Capabilities

| Feature                  | DSPy RLM        | Daytona RLM              | fleet-rlm   | DSPy CodeAct           |
| ------------------------ | --------------- | ------------------------ | ----------- | ---------------------- |
| Sandboxed REPL           | ✅ Deno/Pyodide | ✅ Daytona SDK           | ✅ Modal    | ✅ PythonInterpreter   |
| `llm_query()`            | ✅              | ✅ (`rlm_query`)         | ✅          | ❌                     |
| `llm_query_batched()`    | ✅              | ✅ (`rlm_query_batched`) | ✅          | ❌                     |
| `SUBMIT()` / `FINAL()`   | ✅ `SUBMIT`     | ✅ `FINAL`               | ✅ `SUBMIT` | ❌ (returns from code) |
| Recursive sub-agents     | ❌              | ✅ depth-tracked         | ❌          | ❌                     |
| `edit_file()`            | ❌              | ✅                       | ✅          | ❌                     |
| `sub_lm` (cheaper model) | ✅              | ❌                       | ✅          | ❌                     |
| Custom tools             | ✅              | ❌                       | ✅          | ✅ pure functions only |
| `dspy.Module` subclass   | ✅              | N/A                      | ✅          | ✅                     |

---

## Output & Observability

| Feature            | DSPy RLM        | Daytona RLM                    | fleet-rlm                                                                                                                                             | DSPy CodeAct        |
| ------------------ | --------------- | ------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------- |
| `max_output_chars` | ✅              | ✅ (`result_truncation_limit`) | ⚠️ partial                                                                                                                                            | ❌                  |
| Trajectory output  | ✅              | ❌                             | ✅                                                                                                                                                    | ✅ via `trajectory` |
| Config file        | ❌              | ✅ YAML                        | ⚠️ [config/config.yaml](file:///Volumes/Samsung-SSD-T7/Workspaces/Github/qredence/agent-framework/v0.5/_WORLD/_RLM/fleet-rlm-dspy/config/config.yaml) | ❌                  |
| `async execute`    | ✅ `aforward()` | ❌                             | ❌                                                                                                                                                    | ❌                  |

---

## fleet-rlm Exclusive Features

| Feature                   | DSPy RLM | Daytona RLM | fleet-rlm | DSPy CodeAct |
| ------------------------- | -------- | ----------- | --------- | ------------ |
| Stdout summarization      | ❌       | ❌          | ✅        | ❌           |
| Document chunking helpers | ❌       | ❌          | ✅        | ❌           |
| Execution profiles        | ❌       | ❌          | ✅        | ❌           |
| Sensitive data redaction  | ❌       | ❌          | ✅        | ❌           |
| Volume persistence        | ❌       | ❌          | ✅        | ❌           |
| ReAct chat agent          | ❌       | ❌          | ✅        | ❌           |
| Streaming events          | ❌       | ❌          | ✅        | ❌           |

---

## Module & Optimization Compatibility

| Feature                         | DSPy RLM | Daytona RLM | fleet-rlm | DSPy CodeAct |
| ------------------------------- | -------- | ----------- | --------- | ------------ |
| `dspy.Module` composable        | ✅       | N/A         | ✅        | ✅           |
| `BootstrapFewShot` optimizable  | ✅       | N/A         | ✅        | ✅           |
| `save()` / `load()` persistence | ✅       | N/A         | ✅        | ✅           |
| `dspy.Tool` metadata            | ✅       | N/A         | ✅        | ❌           |
| Signature polymorphism          | ✅       | N/A         | ✅        | ✅           |
| `dspy.History` multi-turn       | ✅       | N/A         | ✅        | ❌           |
| `dspy.Suggest` / `Assert`       | ✅       | N/A         | ❌        | ✅           |

---

## Interpreter Compatibility

| Protocol                     | DSPy `PythonInterpreter` | `ModalInterpreter` | DSPy CodeAct needs           |
| ---------------------------- | ------------------------ | ------------------ | ---------------------------- |
| `execute(code) -> str`       | ✅                       | ✅                 | ✅ (via PythonInterpreter)   |
| `tools` property             | ✅                       | ✅                 | ❌ (tools are code-injected) |
| `shutdown()`                 | ✅                       | ✅                 | N/A                          |
| Context manager              | ❌                       | ✅                 | N/A                          |
| `CodeInterpreter` protocol   | ✅                       | ✅                 | ❌                           |
| `PythonInterpreter` protocol | ✅                       | ❌ (**gap**)       | ✅ (**required**)            |

---

## Legend

| Symbol     | Meaning                                     |
| ---------- | ------------------------------------------- |
| ✅         | Fully implemented                           |
| ⚠️ partial | Partially implemented or different approach |
| ❌         | Not implemented                             |
| N/A        | Not applicable to this framework            |
