"""DSPy callback for PostHog LLM analytics."""

from __future__ import annotations

import os
import threading
import time
import contextvars
from typing import Any

from dspy.utils.callback import BaseCallback

from .client import get_posthog_client
from .config import PostHogConfig
from .sanitization import sanitize_text, to_safe_text
from .trace_context import (
    LLMTraceContext,
    get_current_trace,
    get_runtime_distinct_id,
    pop_current_trace,
    push_current_trace,
)


class PostHogLLMCallback(BaseCallback):
    """Capture DSPy LM call metadata and emit $ai_generation events to PostHog."""

    def __init__(
        self,
        config: PostHogConfig,
        *,
        distinct_id: str | None = None,
    ) -> None:
        self.config = config
        self.distinct_id = distinct_id
        self._in_dspy_optimization = False
        self._pending_traces: dict[str, LLMTraceContext] = {}
        self._pending_inputs: dict[str, dict[str, Any]] = {}
        self._context_tokens: dict[str, contextvars.Token[LLMTraceContext | None]] = {}
        self._lock = threading.RLock()

    @classmethod
    def from_env(cls, *, distinct_id: str | None = None) -> "PostHogLLMCallback":
        """Construct callback using POSTHOG_* environment variables."""
        return cls(PostHogConfig.from_env(), distinct_id=distinct_id)

    def enter_optimization_mode(self) -> None:
        """Disable event emission while DSPy optimization loops are running."""
        self._in_dspy_optimization = True

    def exit_optimization_mode(self) -> None:
        """Re-enable event emission after optimization mode exits."""
        self._in_dspy_optimization = False

    def on_lm_start(self, call_id: str, instance: Any, inputs: dict[str, Any]) -> None:
        """Record start metadata for an LM call."""
        if not self._is_tracking_enabled():
            return

        parent = get_current_trace()
        trace = LLMTraceContext.create(
            call_id=call_id,
            parent_trace_id=parent.trace_id if parent is not None else None,
        )
        trace.model, trace.provider = self._extract_model_info(instance)

        token = push_current_trace(trace)
        with self._lock:
            self._pending_traces[call_id] = trace
            self._pending_inputs[call_id] = dict(inputs)
            self._context_tokens[call_id] = token

    def on_lm_end(
        self,
        call_id: str,
        outputs: dict[str, Any] | None,
        exception: Exception | None = None,
    ) -> None:
        """Emit a PostHog event when an LM call completes."""
        trace: LLMTraceContext | None = None
        inputs: dict[str, Any] = {}
        token: contextvars.Token[LLMTraceContext | None] | None = None
        with self._lock:
            trace = self._pending_traces.pop(call_id, None)
            inputs = self._pending_inputs.pop(call_id, {})
            token = self._context_tokens.pop(call_id, None)

        if token is not None:
            try:
                pop_current_trace(token)
            except Exception as exc:
                # Errors while cleaning up trace context must never affect LLM execution.
                # We intentionally ignore these, but keep a minimal diagnostic for debugging.
                print(f"[PostHogLLMCallback] Failed to pop trace context: {exc}")

        if trace is None:
            return
        if not self._is_tracking_enabled():
            return

        client = get_posthog_client(self.config)
        if client is None:
            return

        duration_ms = int(max(0.0, time.monotonic() - trace.start_time) * 1000)
        input_tokens, output_tokens, total_tokens = self._extract_token_usage(outputs)
        properties: dict[str, Any] = {
            "$ai_trace_id": trace.trace_id,
            "$ai_parent_trace_id": trace.parent_trace_id,
            "$ai_model": trace.model or "unknown",
            "$ai_provider": trace.provider or "unknown",
            "$ai_latency": duration_ms,
            "$ai_input": self._sanitize_payload(
                self._render_input(inputs),
                truncation_chars=self.config.input_truncation_chars,
            ),
            "$ai_output_choices": self._extract_output_choices(outputs),
            "$ai_input_tokens": input_tokens,
            "$ai_output_tokens": output_tokens,
            "$ai_total_tokens": total_tokens,
            "$ai_success": exception is None,
        }

        if exception is not None:
            properties["$ai_error"] = self._sanitize_payload(
                f"{type(exception).__name__}: {exception}",
                truncation_chars=self.config.output_truncation_chars,
            )
            properties["$ai_error_type"] = type(exception).__name__

        try:
            client.capture(
                "$ai_generation",
                distinct_id=self._resolve_distinct_id(),
                properties=properties,
            )
        except Exception:
            # Analytics must never break LLM execution.
            return

    def _resolve_distinct_id(self) -> str:
        runtime_id = get_runtime_distinct_id()
        if runtime_id:
            return runtime_id

        explicit = (self.distinct_id or "").strip()
        if explicit:
            return explicit

        env_distinct = (os.getenv("POSTHOG_DISTINCT_ID") or "").strip()
        if env_distinct:
            return env_distinct

        return "anonymous"

    def _is_tracking_enabled(self) -> bool:
        if not self.config.enabled:
            return False
        if not self.config.api_key:
            return False
        if self._in_dspy_optimization and not self.config.enable_dspy_optimization:
            return False
        return True

    @staticmethod
    def _extract_model_info(instance: Any) -> tuple[str | None, str | None]:
        model_value = getattr(instance, "model", None)
        provider_value = getattr(instance, "provider", None)

        model = to_safe_text(model_value).strip() or None
        provider = to_safe_text(provider_value).strip() or None

        if provider is None and model and "/" in model:
            provider = model.split("/", 1)[0]

        return model, provider

    def _sanitize_payload(self, value: str, *, truncation_chars: int) -> str:
        return sanitize_text(
            value,
            redact=self.config.redact_sensitive,
            truncation_chars=truncation_chars,
        )

    def _extract_output_choices(self, outputs: dict[str, Any] | None) -> list[str]:
        raw_choices: list[Any] = []

        if outputs is None:
            return []

        choices = outputs.get("choices") if isinstance(outputs, dict) else None
        if isinstance(choices, list):
            raw_choices.extend(choices)

        if not raw_choices:
            completions = (
                outputs.get("completions") if isinstance(outputs, dict) else None
            )
            if isinstance(completions, list):
                raw_choices.extend(completions)

        if not raw_choices and isinstance(outputs, dict):
            raw_choices.append(outputs)

        sanitized: list[str] = []
        for item in raw_choices:
            candidate = ""
            if isinstance(item, dict):
                if "text" in item:
                    candidate = to_safe_text(item.get("text"))
                elif "content" in item:
                    candidate = to_safe_text(item.get("content"))
                elif "message" in item and isinstance(item.get("message"), dict):
                    candidate = to_safe_text(item["message"].get("content"))
                else:
                    candidate = to_safe_text(item)
            else:
                candidate = to_safe_text(item)
            sanitized.append(
                self._sanitize_payload(
                    candidate,
                    truncation_chars=self.config.output_truncation_chars,
                )
            )
        return sanitized

    @staticmethod
    def _render_input(inputs: dict[str, Any]) -> str:
        prompt = inputs.get("prompt")
        if isinstance(prompt, str):
            return prompt

        messages = inputs.get("messages")
        if isinstance(messages, list):
            lines: list[str] = []
            for message in messages:
                if isinstance(message, dict):
                    role = to_safe_text(message.get("role") or "message")
                    content = to_safe_text(message.get("content") or "")
                    lines.append(f"{role}: {content}")
                else:
                    lines.append(to_safe_text(message))
            return "\n".join(lines)

        return to_safe_text(inputs)

    @staticmethod
    def _extract_token_usage(
        outputs: dict[str, Any] | None,
    ) -> tuple[int | None, int | None, int | None]:
        if not isinstance(outputs, dict):
            return None, None, None

        usage = outputs.get("usage")
        if not isinstance(usage, dict):
            usage = outputs.get("token_usage") if isinstance(outputs, dict) else None
        if not isinstance(usage, dict):
            return None, None, None

        def _int_or_none(value: Any) -> int | None:
            if isinstance(value, bool) or value is None:
                return None
            if isinstance(value, int):
                return value
            if isinstance(value, float):
                return int(value)
            if isinstance(value, str) and value.isdigit():
                return int(value)
            return None

        input_tokens = _int_or_none(
            usage.get("prompt_tokens")
            or usage.get("input_tokens")
            or usage.get("promptTokens")
            or usage.get("inputTokens")
        )
        output_tokens = _int_or_none(
            usage.get("completion_tokens")
            or usage.get("output_tokens")
            or usage.get("completionTokens")
            or usage.get("outputTokens")
        )
        total_tokens = _int_or_none(
            usage.get("total_tokens") or usage.get("total") or usage.get("totalTokens")
        )

        if (
            total_tokens is None
            and input_tokens is not None
            and output_tokens is not None
        ):
            total_tokens = input_tokens + output_tokens

        return input_tokens, output_tokens, total_tokens
