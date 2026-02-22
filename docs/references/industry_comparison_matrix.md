# Core Capabilities: Industry Comparison Matrix

Comparing `fleet-rlm` against every major RLM implementation in the ecosystem.

> [!NOTE]
> Sources: [dspy.ai RLM](https://dspy.ai/api/modules/RLM/) · [Prime Intellect](https://www.primeintellect.ai/blog/rlm) · [Daytona Deep RLMs](https://www.daytona.io/docs/en/guides/recursive-language-models/) · [Daytona DSPy RLMs](https://github.com/daytonaio/daytona/tree/main/guides/python/dspy-rlms)

| Feature                    | DSPy RLM           | Daytona Deep RLMs      | Daytona DSPy RLMs        | Prime Intellect      | fleet-rlm (v0.4)       | **fleet-rlm (v0.5)**   |
| :------------------------- | :----------------- | :--------------------- | :----------------------- | :------------------- | :--------------------- | :--------------------- |
| **Sandboxed REPL**         | ✅ Deno/Pyodide    | ✅ Daytona SDK         | ✅ `DaytonaInterpreter`  | ✅ Prime Sandboxes   | ✅ Modal               | ✅ Modal (Persistent)  |
| **`llm_query()`**          | ✅                 | ✅ `rlm_query`         | ✅ (Flask broker)        | ✅                   | ✅                     | ✅                     |
| **`llm_query_batched()`**  | ✅                 | ✅ `rlm_query_batched` | ✅ (Flask broker)        | ✅ `llm_batch`       | ✅                     | ✅                     |
| **`SUBMIT()` / `FINAL()`** | ✅ `SUBMIT`        | ✅ `FINAL`             | ✅ `SUBMIT` (typed)      | ✅ `answer["ready"]` | ✅ `SUBMIT`/`Final`    | ✅ `SUBMIT`/`Final`    |
| **Recursive sub-agents**   | ❌                 | ✅ depth-tracked       | ❌                       | ❌ (sub-LLMs only)   | ❌                     | ✅ `RLMEngine`         |
| **`edit_file()`**          | ❌                 | ✅                     | ❌                       | ❌                   | ✅                     | ✅                     |
| **`sub_lm` (cheaper)**     | ✅                 | ❌                     | ✅ (via DSPy)            | ❌                   | ✅                     | ✅                     |
| **Custom tools**           | ✅                 | ❌                     | ✅ (Flask broker)        | ✅ (sub-LLM only)    | ✅                     | ✅                     |
| **`dspy.Module` subclass** | ✅                 | N/A                    | ✅                       | N/A                  | ✅                     | ✅                     |
| **Tool bridge method**     | In-process         | N/A                    | Flask server (port 3000) | Sandbox API          | JSON over stdin/stdout | JSON over stdin/stdout |
| **State persistence**      | ❌ Ephemeral       | ✅ Per workspace       | ✅ Per session           | ❌ Per rollout       | ✅ Volume              | ✅ Volume              |
| **Context Guard**          | `max_output_chars` | ❌                     | ❌                       | Implicit             | ❌                     | ✅ Hard 2000-char      |
| **Evolutive Memory**       | ❌                 | ❌                     | ❌                       | ❌                   | ❌                     | ✅ Neon pgvector       |
| **Live UI Telemetry**      | ❌                 | ❌                     | ❌                       | ❌                   | ❌                     | ✅ Multiplexed WS      |
| **Native Web UI**          | ❌                 | ❌                     | ❌                       | ❌                   | ❌                     | ✅ React Dual-Pane     |

### Key Differentiator: Daytona DSPy RLMs vs fleet-rlm

The Daytona DSPy guide implements `DaytonaInterpreter` as a drop-in `CodeInterpreter` for `dspy.RLM`, using a **Flask broker server** inside the sandbox (port 3000) to bridge `llm_query` and custom tool calls via HTTP polling. This is elegant but:

- Introduces HTTP latency per tool call (vs fleet-rlm's zero-latency stdin/stdout JSON protocol)
- Has no persistent filesystem across sessions (vs fleet-rlm's Modal Volumes)
- Has no memory layer, context guards, or UI telemetry
- Does not support recursive sub-agents (flat RLM only)
