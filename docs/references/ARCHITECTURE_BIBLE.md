# ARCHITECTURE BIBLE: Phase 0 Ground Truth

This document serves as the "Context Hydration" ground truth for the `fleet-rlm` agentic architecture. By anchoring to the explicit documentation below, we prevent hallucinations and ensure adherence to best practices for Recursive Language Models (RLMs), persistent workspaces, and observability integrations.

---

## 1. DSPy RLM API Paradigm

**Source:** [dspy.RLM Documentation](https://dspy.ai/api/modules/RLM/)

**Core Paradigm:** As LLM contexts grow, performance degrades (context rot). The RLM solves this by separating the "variable space" (information stored in a stateful Python REPL/workspace) from the "token space" (what the LLM processes). The RLM dynamically generates code, executes it in isolated sandboxes (via Deno/Modal), evaluates the stdout, and loops recursively until the task is complete.

**Key Syntax & Constraints:**

- The signature explicitly maps inputs to outputs without stuffing the prompt: `rlm = dspy.RLM("context, query -> answer")`
- Important hyperparameters in the constructor:
  - `max_iterations = 20` (Retry loops for syntax/logic errors)
  - `max_llm_calls = 50`
  - `max_output_chars = 10000` (Crucial: Truncates massive stdout to protect context window, we'll enforce a strict 2000 chars guard).
- **Built-in Python Tools:** The RLM REPL natively supports `llm_query(prompt)`, `print()`, `re`, `json`, `math`, and `collections`. Sub-LLMs can be spawned programmatically from within the sandbox.

---

## 2. DSPy Standard Language Models

**Source:** [DSPy Language Models Guide](https://dspy.ai/learn/programming/language_models/)

**Core Setup:** Rather than using complex proxies, we instantiate the standalone DSPy `LM` client and set it globally using `dspy.configure()`. All subsequent modules (like `ChainOfThought`, `Predict`, or `RLM` signatures) implicitely inherit this configuration.

**Key Code Snippet:**

```python
import dspy

# Initialize the LM (e.g., using LiteLLM underneath)
lm = dspy.LM("openai/gemini-3-flash-preview", api_key="[ENCRYPTION_KEY]")

# Set globally
dspy.configure(lm=lm)
```

---

## 3. PostHog DSPy Observability

**Source:** [PostHog LLM Analytics](https://posthog.com/docs/llm-analytics/installation/dspy)

**Core Paradigm:** PostHog directly hooks into DSPy’s internal LiteLLM engine. Because DSPy utilizes LiteLLM under the hood natively, tracking all AI telemetry (cost, tokens, latencies, streaming token-times) simply requires configuring PostHog as a LiteLLM callback.

**Key Code Snippet:**

```python
import os
import dspy
import litellm

# Litellm handles the callback natively with PostHog's system variables
# Requires: POSTHOG_API_KEY and POSTHOG_PROJECT_HOST

lm = dspy.LM("openai/gemini-3-flash-preview", api_key={{DSPY_LM_API_KEY}})
dspy.configure(lm=lm)

# All standard dspy.Predict modules will now automatically dispatch $ai_generation events to PostHog.
```

---

## 4. Recursive Language Models (Theory & State)

**Source:** [Prime Intellect RLM Blog](https://www.primeintellect.ai/blog/rlm)

**Core Paradigm:** The defining theoretical breakthrough of the RLM is that the LLM delegates the heavylifting. It does not inspect the data itself; it generates Python scripts that inspect the data.

- **Sub-LLM Delegation:** The REPL allows the model to spawn fresh sub-agents (`llm_query`) to process filtered data. Tools are exclusively given to the Sub-LLMs, keeping the main RLM purely focused on code generation and logical orchestration.
- **State Diffusion:** The RLM does not return a JSON string to end the turn. It defines an environment explicitly by initializing a variable:
  ```python
  answer = {"content": "", "ready": False}
  ```
  The loop continues until the executed Python script explicitly sets `answer["ready"] = True`, returning the final computed representation. This enables diffusion of reasoning across multiple files and Python executions.

---

### Implementation Anchors for `fleet-rlm`:

1. We will NOT use `dspy.CodeAct`. We will exclusively construct the `RLMEngine(dspy.Module)` utilizing strict DSPy Signatures and explicit Python REPL wrappers (via Modal).
2. The Truncation Guard is a structural necessity derived from the API docs (`max_output_chars`).
3. Telemetry relies on simple environment variables (`POSTHOG_API_KEY`) acting upon the global `dspy.configure(lm=lm)`.
