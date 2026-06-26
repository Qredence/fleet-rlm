"""Custom DSPy BaseLM subclasses.

This module provides:

- ``ResponseAPILM`` — a BaseLM using OpenAI's Response API (typed_lm contract),
  used for OpenAI providers while non-OpenAI providers use stock dspy.LM.
- ``BoundedChatLM`` — a BaseLM using OpenAI-compatible Chat Completions
  (legacy contract) with a real HTTP timeout, no litellm, and optional qwen
  extended-thinking disable. Used to bound action-generation and skill-selection
  calls that would otherwise run on the unbounded planner LM (qwen3.7-max,
  max_tokens=65536) and retry past 60s. See:
  https://dspy.ai/api/models/BaseLM/

See: https://dspy.ai/community/normalized-lm-api-migration/
"""

from __future__ import annotations

import logging
from typing import Any

import dspy
import openai

logger = logging.getLogger(__name__)

# Chat-completions request params we forward to ``openai.chat.completions.create``.
# dspy merges LMConfig fields + self.kwargs into the forward kwargs; we allowlist
# only valid OpenAI params and drop the rest (custom_llm_provider, api_key,
# model_type, reasoning, cache, rollout_id, ...).
_OPENAI_CHAT_PARAMS: set[str] = {
    "max_tokens",
    "max_completion_tokens",
    "temperature",
    "top_p",
    "n",
    "stop",
    "response_format",
    "logprobs",
    "top_logprobs",
    "tools",
    "tool_choice",
    "user",
    "seed",
    "stream",
    "presence_penalty",
    "frequency_penalty",
}

# Substrings (lowercased) in a provider BadRequestError that indicate the prompt
# exceeded the context window — re-raised as dspy.ContextWindowExceededError so
# adapters do not retry.
_CONTEXT_WINDOW_MARKERS: tuple[str, ...] = (
    "context length",
    "context window",
    "maximum context",
    "too long",
    "reduce the length",
    "maximum_number_of_tokens",
)


def _is_context_window_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in _CONTEXT_WINDOW_MARKERS)


class BoundedChatLM(dspy.BaseLM):
    """OpenAI-compatible Chat Completions BaseLM with a real HTTP timeout.

    Owns the transport in ``forward()`` (no litellm), so an ``openai`` client
    ``timeout`` interrupts the call at the source — unlike a ThreadPoolExecutor
    future timeout, which cannot kill the running thread and is defeated by
    ``ThreadPoolExecutor.__exit__`` joining the worker. ``num_retries=0`` (BaseLM
    constructor param) prevents silent retries past the timeout.

    For qwen reasoning models, ``enable_thinking=False`` is sent via
    ``extra_body``: qwen's server-side ``reasoning_content`` is not counted
    against ``max_tokens`` on OpenAI-compatible endpoints, so a max_tokens cap
    alone does not bound thinking time. Disabling it keeps a trivial call (e.g.
    skill routing, a single RLM action step) to seconds.

    Provider context-window errors are re-raised as
    ``dspy.ContextWindowExceededError`` so adapters do not retry an over-long
    prompt.
    """

    def __init__(
        self,
        model: str,
        *,
        api_key: str,
        api_base: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        timeout: float | None = None,
        num_retries: int = 0,
        disable_thinking: bool | None = None,
        **kwargs: Any,
    ) -> None:
        if api_base is not None:
            kwargs["api_base"] = api_base
        if api_key is not None:
            kwargs["api_key"] = api_key
        # cache=False so the bounded LM always hits the provider and the real
        # timeout applies on every call (per-call configs vary).
        super().__init__(
            model,
            model_type="chat",
            temperature=temperature,
            max_tokens=max_tokens,
            cache=False,
            num_retries=num_retries,
            **kwargs,
        )
        self._api_key = api_key
        self._api_base = api_base
        self._timeout = timeout
        self._max_tokens = max_tokens
        self._temperature = temperature
        if disable_thinking is None:
            disable_thinking = "qwen" in str(model).lower()
        self._disable_thinking = disable_thinking
        logger.info(
            "BoundedChatLM constructed: model=%s, timeout=%s, max_tokens=%s, disable_thinking=%s",
            model,
            timeout,
            max_tokens,
            disable_thinking,
        )
        self._client = openai.OpenAI(
            api_key=api_key,
            base_url=api_base,
            timeout=timeout,
            max_retries=0,
        )

    def forward(
        self,
        prompt: str | None = None,
        messages: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ):
        """Call the provider via ``openai.chat.completions.create`` (legacy contract).

        Returns a raw OpenAI ChatCompletion; dspy's ``_process_completion`` reads
        ``choices[i].message.content`` from it.

        When ``self._timeout`` is set, the call is wrapped in a
        ``ThreadPoolExecutor`` with ``future.result(timeout=...)`` to enforce a
        **total wall-clock** deadline. The OpenAI client's ``timeout`` is a
        per-IO-operation httpx.Timeout (connect/read/write), which does not
        bound the total call duration — a slow server sending data steadily
        keeps the read timeout from firing. The executor wrapper adds a hard
        deadline on top. The worker thread is not joined after timeout
        (``shutdown(wait=False)``), so we raise immediately.
        """
        import concurrent.futures

        logger.debug(
            "BoundedChatLM.forward() called: model=%s, timeout=%s, messages=%d",
            self.model,
            self._timeout,
            len(messages) if messages else 0,
        )

        kwargs = {**self.kwargs, **kwargs}
        # Drop non-transport / non-OpenAI params dspy may have merged in.
        for drop_key in (
            "api_key",
            "api_base",
            "custom_llm_provider",
            "cache",
            "num_retries",
            "rollout_id",
            "model_type",
            "reasoning",
        ):
            kwargs.pop(drop_key, None)

        messages = messages or [{"role": "user", "content": prompt}]
        if self._max_tokens is not None:
            kwargs.setdefault("max_tokens", self._max_tokens)
        if self._temperature is not None:
            kwargs.setdefault("temperature", self._temperature)

        # Only forward OpenAI-valid params; everything else is dropped.
        call_kwargs = {k: v for k, v in kwargs.items() if k in _OPENAI_CHAT_PARAMS}
        extra_body = dict(kwargs.pop("extra_body", None) or {})
        if self._disable_thinking:
            extra_body.setdefault("enable_thinking", False)

        def _call() -> Any:
            return self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                extra_body=extra_body or None,
                **call_kwargs,
            )

        if self._timeout is not None:
            logger.info(
                "BoundedChatLM: enforcing wall-clock timeout=%ss via ThreadPoolExecutor",
                self._timeout,
            )
            executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            future = executor.submit(_call)
            try:
                result = future.result(timeout=self._timeout)
            except concurrent.futures.TimeoutError:
                future.cancel()
                logger.warning(
                    "BoundedChatLM: timeout fired after %ss (model=%s)",
                    self._timeout,
                    self.model,
                )
                raise dspy.LMError(f"BoundedChatLM timed out after {self._timeout}s (total wall-clock)") from None
            except openai.APITimeoutError as exc:
                raise dspy.LMError(f"BoundedChatLM timed out after {self._timeout}s: {exc}") from exc
            except openai.BadRequestError as exc:
                if _is_context_window_error(exc):
                    cw_err = dspy.ContextWindowExceededError()
                    cw_err.args = (str(exc),)
                    raise cw_err from exc
                raise dspy.LMError(f"BoundedChatLM bad request: {exc}") from exc
            except openai.APIStatusError as exc:
                raise dspy.LMError(f"BoundedChatLM provider error ({exc.status_code}): {exc}") from exc
            except openai.APIError as exc:
                raise dspy.LMError(f"BoundedChatLM transport error: {exc}") from exc
            finally:
                executor.shutdown(wait=False)
            return result
        else:
            try:
                return _call()
            except openai.APITimeoutError as exc:
                raise dspy.LMError(f"BoundedChatLM timed out after {self._timeout}s: {exc}") from exc
            except openai.BadRequestError as exc:
                if _is_context_window_error(exc):
                    cw_err = dspy.ContextWindowExceededError()
                    cw_err.args = (str(exc),)
                    raise cw_err from exc
                raise dspy.LMError(f"BoundedChatLM bad request: {exc}") from exc
            except openai.APIStatusError as exc:
                raise dspy.LMError(f"BoundedChatLM provider error ({exc.status_code}): {exc}") from exc
            except openai.APIError as exc:
                raise dspy.LMError(f"BoundedChatLM transport error: {exc}") from exc


def build_bounded_chat_lm(
    base: Any | None,
    *,
    max_tokens: int,
    temperature: float,
    timeout: float | None,
    num_retries: int = 0,
) -> BoundedChatLM | None:
    """Build a ``BoundedChatLM`` from an existing LM's credentials.

    Extracts ``model``/``api_key``/``api_base`` from a stock ``dspy.LM``
    (``base.kwargs``) or a ``ResponseAPILM`` (``_api_key``/``_api_base``), then
    constructs a ``BoundedChatLM`` with the given caps. Returns ``None`` if
    ``base`` is ``None`` or its credentials cannot be extracted, so callers can
    fall back to the global dspy LM.
    """
    if base is None:
        return None
    model = getattr(base, "model", None)
    base_kwargs = getattr(base, "kwargs", None) or {}
    api_key = base_kwargs.get("api_key") or getattr(base, "_api_key", None)
    api_base = base_kwargs.get("api_base") or getattr(base, "_api_base", None)
    if not model or not api_key:
        return None
    try:
        return BoundedChatLM(
            str(model),
            api_key=str(api_key),
            api_base=str(api_base) if api_base else None,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=timeout,
            num_retries=num_retries,
        )
    except Exception as exc:  # pragma: no cover - defensive construction fallback
        logger.debug("BoundedChatLM construction failed (%s); falling back", exc)
        return None


class ResponseAPILM(dspy.BaseLM):
    """Custom BaseLM using OpenAI's Response API with typed_lm forward contract.

    This LM is used for OpenAI providers. It implements the normalized LM API
    with forward_contract="typed_lm" and calls openai.responses.create() instead
    of the legacy chat completions API.
    """

    forward_contract = "typed_lm"

    def __init__(
        self,
        model: str,
        *,
        api_key: str,
        api_base: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        timeout: float | None = None,
        custom_llm_provider: str | None = None,
        **kwargs,
    ) -> None:
        # Pass api_base, api_key, and custom_llm_provider to parent for compatibility with dspy.LM interface
        if api_base is not None:
            kwargs["api_base"] = api_base
        if api_key is not None:
            kwargs["api_key"] = api_key
        if custom_llm_provider is not None:
            kwargs["custom_llm_provider"] = custom_llm_provider

        # Only pass temperature to parent if explicitly set (to match dspy.LM behavior)
        init_kwargs = {"model": model, "model_type": "responses"}
        if max_tokens is not None:
            init_kwargs["max_tokens"] = max_tokens
        if temperature is not None:
            init_kwargs["temperature"] = temperature
        init_kwargs.update(kwargs)

        # Initialize BaseLM with core params
        super().__init__(**init_kwargs)

        # Store config for OpenAI client
        self._api_key = api_key
        self._api_base = api_base
        self._timeout = timeout
        self._custom_provider = custom_llm_provider

        # Initialize OpenAI client
        self._client = openai.OpenAI(
            api_key=api_key,
            base_url=api_base,  # None uses default api.openai.com
            timeout=timeout,
        )

        # History is initialized by BaseLM.__init__ as self.history = []

    def forward(self, request: dspy.LMRequest) -> dspy.LMResponse:
        """Implement typed_lm forward contract.

        Converts LMRequest → OpenAI Response API call → LMResponse.
        Supports LM-native tool calling for ReAct/FleetAgent.
        """
        # Extract messages and convert to OpenAI format
        messages = self._convert_messages(request.messages)

        # Extract tools if present (for LM-native tool calling)
        tools = self._convert_tools(request.tools) if request.tools else None

        # Build request params
        request_kwargs: dict[str, Any] = {
            "model": request.model,
            "input": messages,
        }

        if tools:
            request_kwargs["tools"] = tools

        # Apply config overrides (temperature, max_tokens)
        if request.config:
            if request.config.temperature is not None:
                request_kwargs["temperature"] = request.config.temperature
            if request.config.max_tokens is not None:
                request_kwargs["max_output_tokens"] = request.config.max_tokens

        # Call OpenAI Response API
        response = self._client.responses.create(**request_kwargs)

        # Extract text from response
        text = response.output_text

        # Extract usage
        usage = {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        }

        # Build LMResponse
        lm_response = dspy.LMResponse.from_text(
            text=text,
            model=response.model,
            usage=usage,
        )

        # Append to history for backward compatibility
        self.history.append(
            {
                "usage": usage,
                "model": response.model,
            }
        )

        return lm_response

    def _convert_messages(self, messages: list) -> list[dict[str, Any]]:
        """Convert LMMessage list to OpenAI Response API input format."""
        # LMMessage has role and parts (list of parts with type/text)
        # OpenAI Response API accepts a list of message dicts with role and content
        converted = []
        for msg in messages:
            # Extract text from parts
            content = ""
            if hasattr(msg, "parts") and msg.parts:
                # Concatenate all text parts
                content = " ".join(
                    part.get("text", "") if isinstance(part, dict) else getattr(part, "text", "")
                    for part in msg.parts
                    if (isinstance(part, dict) and part.get("type") == "text")
                    or (hasattr(part, "type") and part.type == "text")
                )
            elif hasattr(msg, "content"):
                # Fallback for older LMMessage format
                content = msg.content

            converted.append(
                {
                    "role": msg.role,
                    "content": content,
                }
            )
        return converted

    def _convert_tools(self, tools: list) -> list[dict[str, Any]]:
        """Convert LMToolSpec list to OpenAI Response API tool format."""
        # LMToolSpec has name, description, parameters fields
        # OpenAI Response API expects tool definitions in function-calling format
        converted = []
        for tool in tools:
            converted.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters,
                    },
                }
            )
        return converted

    def dump_state(self) -> dict[str, Any]:
        """Serialize LM state for DSPy program serialization."""
        return {
            "model": self.model,
            "api_base": self._api_base,
            "max_tokens": self.kwargs.get("max_tokens"),
            "temperature": self.kwargs.get("temperature"),
        }

    def load_state(self, state: dict[str, Any]) -> None:
        """Deserialize LM state."""
        self.model = state.get("model", self.model)
        self._api_base = state.get("api_base")
        if state.get("max_tokens") is not None:
            self.kwargs["max_tokens"] = state["max_tokens"]
        if state.get("temperature") is not None:
            self.kwargs["temperature"] = state["temperature"]
        # Rebuild client with new config
        self._client = openai.OpenAI(
            api_key=self._api_key,
            base_url=self._api_base,
            timeout=self._timeout,
        )


__all__ = ["ResponseAPILM", "BoundedChatLM", "build_bounded_chat_lm"]
