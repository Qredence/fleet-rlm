# LiteLLM Proxy Model Availability

This document lists models available through the hosted LiteLLM proxy for reference.

**Proxy URL:** `LITELLM_PROXY_BASE_URL` environment variable (e.g., `https://litellm-proxy-gojcb5mtua-uc.a.run.app`)

**Authentication:** Uses `DSPY_LLM_API_KEY` from `.env` as the Bearer token.

## Available Models

### DeepInfra Models

| Model Name | Mode | Max Input | Max Output | Features |
|------------|------|-----------|------------|----------|
| `deepinfra/deepseek-ai/DeepSeek-R1-0528` | chat | 164,000 | 164,000 | - |
| `deepinfra/deepseek-ai/DeepSeek-V3.2` | chat | - | - | - |
| `deepinfra/nvidia/Nemotron-3-Nano-30B-A3B` | chat | - | - | - |
| `deepinfra/moonshotai/Kimi-K2.5` | chat | - | - | - |

### Vertex AI Models

| Model Name | Mode | Max Input | Max Output | Features |
|------------|------|-----------|------------|----------|
| `vertex_ai/deepseek-v3.2-maas` | chat | 164,000 | 33,000 | reasoning |
| `vertex_ai/gemini-3-flash-preview` | chat | 1,049,000 | 66,000 | vision |
| `vertex_ai/kimi-k2-thinking-maas` | chat | 256,000 | 256,000 | web_search, thinking |

### Gemini Models

| Model Name | Mode | Max Input | Max Output | Features |
|------------|------|-----------|------------|----------|
| `gemini/gemini-2.5-computer-use-preview-10-2025` | chat | 128,000 | 64,000 | vision, computer_use |
| `gemini/gemini-3-flash-preview` | chat | 1,049,000 | 66,000 | vision |
| `gemini/gemini-3-pro-image-preview` | image_generation | 66,000 | 33,000 | vision |
| `gemini/gemini-3-pro-preview` | chat | 1,049,000 | 66,000 | vision |
| `gemini/gemini-embedding-001` | embedding | 2,000 | - | - |

### Nvidia NIM Models

| Model Name | Mode | Max Input | Max Output | Features |
|------------|------|-----------|------------|----------|
| `nvidia_nim/moonshotai/kimi-k2-instruct-0905` | chat | - | - | - |
| `nvidia_nim/nvidia/nemotron-3-nano-30b-a3b` | chat | - | - | - |
| `nvidia_nim/qwen/qwen3-next-80b-a3b-instruct` | chat | - | - | - |

## Endpoint Compatibility (Tested 2025-02-07)

### Working with /v1/responses

All DeepInfra, Nvidia NIM, Gemini Flash/Pro (chat), and Vertex DeepSeek models work with the `/v1/responses` endpoint.

### Known Issues

| Model | Issue | Workaround |
|-------|-------|------------|
| `gemini/gemini-2.5-computer-use-preview-10-2025` | 400 Bad Request | Use `/v1/chat/completions` |
| `gemini/gemini-3-pro-image-preview` | Image generation | Use `/v1/images/generations` |
| `vertex_ai/kimi-k2-thinking-maas` | Times out (>60s) | Increase timeout for complex queries |

## Environment Variables

```bash
# Required - API key for proxy authentication
DSPY_LLM_API_KEY=your-api-key

# Required - Proxy base URL
LITELLM_PROXY_BASE_URL=https://litellm-proxy-gojcb5mtua-uc.a.run.app

# Optional - Provider API keys (proxy needs these)
DEEPINFRA_API_KEY=your-deepinfra-key
GEMINI_API_KEY=your-gemini-key
VERTEX_PROJECT_ID=your-gcp-project
VERTEX_LOCATION=us-central1
NVIDIA_NIM_API_KEY=your-nvidia-key
```

## Usage in Fleet-RLM

Model configuration is managed through Hydra/OmegaConf settings, not this document. See:

- `src/fleet_rlm/conf/config.yaml` - Active runtime configuration
- `.env` - Environment variables for API keys and model defaults

To override the model:

```bash
# Via environment
DSPY_LM_MODEL=openai/gemini-3-pro-preview python -m fleet_rlm cli

# Via Hydra config override
python -m fleet_rlm cli dspy.lm.model=deepinfra/deepseek-ai/DeepSeek-V3.2
```
