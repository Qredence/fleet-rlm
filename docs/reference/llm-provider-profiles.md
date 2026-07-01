# LLM Provider Profiles

This document explains the architecture and canonical types of the **LLM Provider Profiles** system in Fleet-RLM.

In Fleet-RLM, we route LLM queries through standard, provider-neutral wire formats. When you create or update an LLM provider profile (such as from the Web UI or via the API), you select one of exactly three canonical wire-format provider types.

---

## Canonical Provider Types

Every LLM profile maps to one of three canonical types, representing the precise wire format used to speak to the LLM endpoint:

### 1. `openai_responses`
*   **Format Name:** OpenAI Responses API
*   **Default Endpoint:** `https://api.openai.com/v1`
*   **DSPy Mapping:** Maps to `dspy.LM(model_type="responses")`
*   **Description:** Speaks the standard OpenAI Responses wire-format. This is the optimal format for native OpenAI endpoints where the model parameter is prefixed (e.g. `openai/gpt-4o`).

### 2. `openai_chat_completion`
*   **Format Name:** OpenAI Chat Completion API
*   **Default Endpoint:** None (Requires user input, or defaults to specific third-party base URLs)
*   **DSPy Mapping:** Maps to `dspy.LM(model_type="chat")` with `custom_llm_provider="openai"`
*   **Description:** A generalized, highly compatible wire format for any endpoint that speaks an OpenAI-compatible Chat Completion API. This is used for OpenRouter, DeepInfra, local gateways (like vLLM or Ollama), and Gemini models (since Gemini's OpenAI-compatibility layer folds into `openai_chat_completion`).

### 3. `anthropic_messages`
*   **Format Name:** Anthropic Messages API
*   **Default Endpoint:** `https://api.anthropic.com`
*   **DSPy Mapping:** Maps to `dspy.LM(model_type="chat")` with `custom_llm_provider="anthropic"`
*   **Description:** Speaks the Anthropic Messages wire format. Calls to endpoints of this type map to `POST /v1/messages` internally.

---

## Wire Format Configuration & Mapping

The backend maps these three choices to DSPy/LiteLLM properties as follows:

| Provider Type (`LlmProviderType`) | DSPy `model_type` | LiteLLM `custom_llm_provider` | Default `api_base` |
| :--- | :--- | :--- | :--- |
| `openai_responses` | `"responses"` | `None` *(Inferred from model name prefix)* | `https://api.openai.com/v1` |
| `openai_chat_completion` | `"chat"` | `"openai"` | *(User provided or empty)* |
| `anthropic_messages` | `"chat"` | `"anthropic"` | `https://api.anthropic.com` |

---

## Key Mapping Nuances

1.  **Gemini is Folded into `openai_chat_completion`:** Gemini endpoints are resolved to `openai_chat_completion` as they communicate via the OpenAI-compatible Chat Completion format, which simplifies routing and ensures robust streaming performance.
2.  **No "messages" `model_type` in DSPy:** Under DSPy, there is no separate `"messages"` value for `model_type`. Instead, Anthropic is mapped to `"chat"` mode and routed using the model prefix (e.g., `anthropic/claude-3-5-sonnet`) to target `POST /v1/messages`.
3.  **Tenant BYOK Encryption:** Provider credentials stored in the hosted database are encrypted using Fernet cryptography (`FLEET_SECRET_ENCRYPTION_KEY`) in the `neon` auth mode to protect API keys. Missing or masked keys are ignored during updates to prevent key wipes.
