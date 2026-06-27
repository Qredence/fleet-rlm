# OpenAI Response API Migration - Implementation Summary

## Overview

Successfully migrated fleet-rlm from DSPy's legacy Chat Completions API to the new OpenAI Response API using a custom `ResponseAPILM` class that implements DSPy's typed LM interface (`forward_contract = "typed_lm"`).

## Changes Made

### 1. Core Implementation: `ResponseAPILM` Class
**File**: `src/fleet_rlm/runtime/lm.py` (NEW)

Created a custom DSPy BaseLM subclass that:
- Uses `forward_contract = "typed_lm"` (normalized LM API)
- Calls OpenAI's `responses.create()` instead of `chat.completions.create()`
- Implements the full typed LM interface: `forward(request: LMRequest) -> LMResponse`
- Supports LM-native tool calling for ReAct/FleetAgent
- Maintains backward compatibility with `lm.history` and usage tracking
- Implements state serialization (`dump_state`/`load_state`) for DSPy program serialization

### 2. Provider Routing Logic
**File**: `src/fleet_rlm/runtime/config.py`

Updated `_build_lm()` to:
- Use `ResponseAPILM` for OpenAI providers (model strings starting with "openai/")
- Fall back to stock `dspy.LM` for non-OpenAI providers (Anthropic, Google, etc.)
- Updated guard comment to document the new policy

### 3. LM Construction Sites Updated
Updated 4 additional LM construction sites to use the same routing logic:
- `src/fleet_rlm/runtime/config.py:455` - Judge role
- `src/fleet_rlm/quality/optimization_runner.py:228` - Reflection LM
- `src/fleet_rlm/api/runtime_services/llm_profiles.py:346` - Profile smoke tests
- `src/fleet_rlm/api/runtime_services/diagnostics.py:389` - Diagnostics smoke tests

### 4. Unit Tests
**File**: `tests/unit/runtime/test_response_api_lm.py` (NEW)

Comprehensive test suite covering:
- Forward contract declaration
- OpenAI Response API integration
- Tool conversion (LMToolSpec → OpenAI format)
- Usage tracking and history population
- Config overrides (temperature, max_tokens)
- State serialization (dump_state/load_state)

### 5. Test Updates
**File**: `tests/unit/runtime/test_config.py`

Updated test expectations to account for ResponseAPILM's kwargs structure (includes `temperature: None` from BaseLM).

## Key Design Decisions

1. **Selective Migration**: Only OpenAI providers use ResponseAPILM; other providers continue using stock dspy.LM to preserve multi-provider support.

2. **Model String Handling**: Strip the `"openai/"` prefix before passing to OpenAI client (e.g., `"openai/gpt-4o"` becomes `"gpt-4o"`) to match the bare model name expected by the Response API.

3. **Message Format Conversion**: Convert DSPy's `LMMessage` format (with `parts` list) to OpenAI's Response API input format.

4. **Tool Support**: Full support for LM-native tool calling, converting `LMToolSpec` objects to OpenAI's function-calling format.

5. **Backward Compatibility**: Maintains `lm.history` property and usage tracking for observability callbacks.

## Testing Results

All tests passing:
- **542 unit tests** passed
- **17 contracts tests** passed (4 skipped)
- **6 new ResponseAPILM tests** passed

## Architecture

```
┌─────────────────────────────────────────┐
│         _build_lm() routing              │
│                                          │
│  model.startswith("openai/") ──┐        │
│                                 ↓        │
│                    ┌──────────────────┐  │
│                    │  ResponseAPILM   │  │
│                    │  (typed_lm)      │  │
│                    │  responses API   │  │
│                    └──────────────────┘  │
│                                          │
│  other providers ──────────┐            │
│                             ↓            │
│                ┌──────────────────┐      │
│                │   dspy.LM        │      │
│                │   (legacy)       │      │
│                │   chat API       │      │
│                └──────────────────┘      │
└─────────────────────────────────────────┘
```

## Benefits

1. **Modern API**: Uses OpenAI's latest Response API with better tool calling support
2. **Type Safety**: Implements DSPy's typed LM interface with proper request/response types
3. **Observability**: Maintains full compatibility with existing usage tracking and history
4. **Flexibility**: Preserves multi-provider support with intelligent routing
5. **Future-Proof**: Aligned with DSPy's normalized LM API migration strategy

## Migration Path

For users:
- No changes required - the migration is transparent
- OpenAI models automatically use ResponseAPILM
- Non-OpenAI models continue using stock dspy.LM

For developers:
- New LM implementations should subclass `dspy.BaseLM` with `forward_contract = "typed_lm"`
- Use `LMRequest` and `LMResponse` types for type safety
- Follow the pattern in `ResponseAPILM` for provider-specific implementations

## References

- DSPy Normalized LM API Migration: https://dspy.ai/community/normalized-lm-api-migration/
- DSPy BaseLM API: https://dspy.ai/api/models/BaseLM/
- OpenAI Response API: https://platform.openai.com/docs/api-reference/responses
