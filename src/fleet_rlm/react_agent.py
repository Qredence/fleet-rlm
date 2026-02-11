"""Interactive DSPy ReAct chat agent backed by ModalInterpreter + RLM tools.

This module provides a stateful chat agent that uses ``dspy.ReAct`` for
reasoning/tool selection while delegating long-context computation to the
existing ``ModalInterpreter`` and ``dspy.RLM`` workflows in this project.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Callable, Iterable, cast

import dspy
from dspy.primitives.code_interpreter import FinalOutput
from dspy.streaming.messages import StatusMessage, StatusMessageProvider, StreamResponse
from dspy.streaming.streaming_listener import StreamListener

from .chunking import (
    chunk_by_headers,
    chunk_by_json_keys,
    chunk_by_size,
    chunk_by_timestamps,
)
from .interpreter import ModalInterpreter
from .interactive.models import StreamEvent
from .signatures import AnalyzeLongDocument, ExtractFromLogs, SummarizeLongDocument

# Logger for structured streaming error logging
logger = logging.getLogger(__name__)


class RLMReActChatSignature(dspy.Signature):
    """Interactive ReAct chat signature with explicit conversation history."""

    user_request: str = dspy.InputField(desc="Current user request in the chat session")
    history: dspy.History = dspy.InputField(
        desc="Prior chat turns using keys user_request and assistant_response"
    )
    assistant_response: str = dspy.OutputField(desc="Final assistant response to user")


class RLMReActChatAgent:
    """Interactive ReAct agent that can orchestrate RLM workflows via tools.

    The agent is intentionally stateful:
        - Conversation memory is stored as ``dspy.History``.
        - Sandbox state is preserved in one long-lived Modal interpreter session.
        - Optional Modal volume persistence survives across runs.
    """

    def __init__(
        self,
        *,
        react_max_iters: int = 10,
        rlm_max_iterations: int = 30,
        rlm_max_llm_calls: int = 50,
        timeout: int = 900,
        secret_name: str = "LITELLM",
        volume_name: str | None = None,
        verbose: bool = False,
        history_max_turns: int | None = None,
        extra_tools: list[Callable[..., Any]] | None = None,
        interpreter: ModalInterpreter | None = None,
    ) -> None:
        self.react_max_iters = react_max_iters
        self.rlm_max_iterations = rlm_max_iterations
        self.rlm_max_llm_calls = rlm_max_llm_calls
        self.verbose = verbose
        self.history_max_turns = history_max_turns

        self.interpreter = interpreter or ModalInterpreter(
            timeout=timeout,
            secret_name=secret_name,
            volume_name=volume_name,
            max_llm_calls=rlm_max_llm_calls,
        )

        self.history = dspy.History(messages=[])
        # LRU cache for documents to prevent unbounded growth in long-running sessions
        self._document_cache: dict[str, str] = {}
        self._document_access_order: list[str] = []
        self._max_documents = 100  # LRU eviction threshold
        self.active_alias: str | None = None

        self._started = False
        self._extra_tools: list[Callable[..., Any]] = list(extra_tools or [])

        self.react_tools: list[Callable[..., Any]] = []
        self.agent = self._build_agent()

    def _get_document(self, alias: str) -> str:
        """Get document with LRU cache tracking."""
        if alias not in self._document_cache:
            raise KeyError(f"Document alias '{alias}' not found")
        # Update access order (LRU)
        if alias in self._document_access_order:
            self._document_access_order.remove(alias)
        self._document_access_order.append(alias)
        return self._document_cache[alias]

    def _set_document(self, alias: str, content: str) -> None:
        """Set document with LRU eviction if needed."""
        # Evict oldest if at capacity and this is a new entry
        if alias not in self._document_cache and len(self._document_cache) >= self._max_documents:
            oldest = self._document_access_order.pop(0)
            del self._document_cache[oldest]

        self._document_cache[alias] = content
        if alias in self._document_access_order:
            self._document_access_order.remove(alias)
        self._document_access_order.append(alias)

    def _delete_document(self, alias: str) -> None:
        """Delete document from cache."""
        if alias in self._document_cache:
            del self._document_cache[alias]
        if alias in self._document_access_order:
            self._document_access_order.remove(alias)

    @property
    def documents(self) -> dict[str, str]:
        """Backward-compatible document access."""
        return self._document_cache

    # ---------------------------------------------------------------------
    # Lifecycle
    # ---------------------------------------------------------------------

    def __enter__(self) -> "RLMReActChatAgent":
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        self.shutdown()
        return False

    def start(self) -> None:
        """Start the underlying Modal interpreter session if needed."""
        if self._started:
            return
        self.interpreter.start()
        self._started = True

    def shutdown(self) -> None:
        """Shutdown the interpreter and mark this agent session as stopped."""
        self.interpreter.shutdown()
        self._started = False

    def reset(self, *, clear_sandbox_buffers: bool = True) -> dict[str, Any]:
        """Reset chat history and (optionally) sandbox buffers."""
        self.history = dspy.History(messages=[])
        if clear_sandbox_buffers:
            self.clear_buffer()
        return {
            "status": "ok",
            "history_turns": 0,
            "buffers_cleared": clear_sandbox_buffers,
        }

    # ---------------------------------------------------------------------
    # Public chat API
    # ---------------------------------------------------------------------

    def chat_turn(self, message: str) -> dict[str, Any]:
        """Process one interactive chat turn through the ReAct agent."""
        if not message or not message.strip():
            raise ValueError("message cannot be empty")

        self.start()

        prediction = self.agent(user_request=message, history=self.history)
        assistant_response = str(getattr(prediction, "assistant_response", "")).strip()

        self._append_history(message, assistant_response)

        return {
            "assistant_response": assistant_response,
            "trajectory": getattr(prediction, "trajectory", {}),
            "history_turns": len(self.history.messages),
        }

    def iter_chat_turn_stream(
        self,
        message: str,
        trace: bool,
        cancel_check: Callable[[], bool] | None = None,
    ) -> Iterable[StreamEvent]:
        """Yield typed streaming events for one chat turn.

        This is the canonical streaming surface used by the Textual runtime.
        The compatibility ``chat_turn_stream`` method is implemented as a
        collector over this iterator.
        """
        if not message or not message.strip():
            raise ValueError("message cannot be empty")

        self.start()

        stream_listeners = [StreamListener(signature_field_name="assistant_response")]
        if trace:
            stream_listeners.append(
                StreamListener(signature_field_name="next_thought", allow_reuse=True)
            )

        try:
            stream_program = cast(
                Any,
                dspy.streamify(
                    self.agent,
                    status_message_provider=_ReActStatusProvider(),
                    stream_listeners=stream_listeners,
                    include_final_prediction_in_output_stream=True,
                    async_streaming=False,
                ),
            )
        except Exception as exc:
            # Structured logging for debugging without breaking fallback
            logger.warning(
                "Streaming init failed, falling back: %s",
                exc,
                exc_info=True,
                extra={"error_type": type(exc).__name__},
            )
            fallback = self.chat_turn(message)
            yield StreamEvent(
                kind="status",
                text=f"streaming unavailable; fell back to non-streaming ({exc})",
                payload={"fallback": True, "error_type": type(exc).__name__},
            )
            yield StreamEvent(
                kind="final",
                text=str(fallback.get("assistant_response", "")),
                payload={
                    "trajectory": fallback.get("trajectory", {}),
                    "history_turns": fallback.get("history_turns", len(self.history.messages)),
                    "fallback": True,
                },
            )
            return

        assistant_chunks: list[str] = []
        final_prediction: dspy.Prediction | None = None

        try:
            stream = stream_program(user_request=message, history=self.history)
            for value in stream:
                if cancel_check is not None and cancel_check():
                    partial = "".join(assistant_chunks).strip()
                    marked_partial = (
                        f"{partial}\n\n[cancelled]" if partial else "[cancelled]"
                    )
                    self._append_history(message, marked_partial)
                    yield StreamEvent(
                        kind="cancelled",
                        text=marked_partial,
                        payload={"history_turns": len(self.history.messages)},
                    )
                    return

                if isinstance(value, StreamResponse):
                    if value.signature_field_name == "assistant_response":
                        assistant_chunks.append(value.chunk)
                        yield StreamEvent(kind="assistant_token", text=value.chunk)
                    elif value.signature_field_name == "next_thought" and trace:
                        yield StreamEvent(
                            kind="reasoning_step",
                            text=value.chunk,
                            payload={"source": "next_thought"},
                        )
                elif isinstance(value, StatusMessage):
                    text = value.message
                    yield StreamEvent(kind="status", text=text)
                    tool_call = self._parse_tool_call_status(text)
                    if tool_call:
                        yield StreamEvent(kind="tool_call", text=tool_call)
                    tool_result = self._parse_tool_result_status(text)
                    if tool_result:
                        yield StreamEvent(kind="tool_result", text=tool_result)
                elif isinstance(value, dspy.Prediction):
                    final_prediction = value
        except Exception as exc:
            # Structured logging for stream errors without breaking fallback
            logger.error(
                "Streaming error, falling back: %s",
                exc,
                exc_info=True,
                extra={"error_type": type(exc).__name__},
            )
            # Fall back to non-streaming path if listener wiring fails for a model/backend.
            fallback = self.chat_turn(message)
            yield StreamEvent(
                kind="status",
                text=f"stream error; fell back to non-streaming ({exc})",
                payload={"fallback": True, "error_type": type(exc).__name__},
            )
            yield StreamEvent(
                kind="final",
                text=str(fallback.get("assistant_response", "")),
                payload={
                    "trajectory": fallback.get("trajectory", {}),
                    "history_turns": fallback.get("history_turns", len(self.history.messages)),
                    "fallback": True,
                },
            )
            return

        if final_prediction is not None:
            assistant_response = str(getattr(final_prediction, "assistant_response", "")).strip()
            trajectory = getattr(final_prediction, "trajectory", {})
        else:
            assistant_response = "".join(assistant_chunks).strip()
            trajectory = {}

        self._append_history(message, assistant_response)
        yield StreamEvent(
            kind="final",
            text=assistant_response,
            payload={
                "trajectory": trajectory,
                "history_turns": len(self.history.messages),
            },
        )

    def chat_turn_stream(self, *, message: str, trace: bool = False) -> dict[str, Any]:
        """Compatibility stream collector for existing CLI/tests."""
        assistant_chunks: list[str] = []
        thought_chunks: list[str] = []
        status_messages: list[str] = []
        trajectory: dict[str, Any] = {}
        assistant_response = ""
        cancelled = False

        for event in self.iter_chat_turn_stream(message=message, trace=trace):
            if event.kind == "assistant_token":
                assistant_chunks.append(event.text)
            elif event.kind == "reasoning_step":
                thought_chunks.append(event.text)
            elif event.kind == "status":
                status_messages.append(event.text)
            elif event.kind == "final":
                assistant_response = event.text
                trajectory = dict(event.payload.get("trajectory", {}) or {})
            elif event.kind == "cancelled":
                cancelled = True
                assistant_response = event.text

        if not assistant_response:
            assistant_response = "".join(assistant_chunks).strip()

        return {
            "assistant_response": assistant_response,
            "trajectory": trajectory,
            "history_turns": len(self.history.messages),
            "stream_chunks": assistant_chunks,
            "thought_chunks": thought_chunks if trace else [],
            "status_messages": status_messages,
            "cancelled": cancelled,
        }

    def register_extra_tool(self, tool: Callable[..., Any]) -> dict[str, Any]:
        """Register an additional tool and rebuild the ReAct agent."""
        self._extra_tools.append(tool)
        self.agent = self._build_agent()
        return {"status": "ok", "tool_name": getattr(tool, "__name__", str(tool))}

    # ---------------------------------------------------------------------
    # Tool implementations
    # ---------------------------------------------------------------------

    def load_document(self, path: str, alias: str = "active") -> dict[str, Any]:
        """Load a text document from host filesystem into agent document memory."""
        docs_path = Path(path)
        if not docs_path.exists():
            raise FileNotFoundError(f"Document not found: {docs_path}")
        content = docs_path.read_text()
        self._set_document(alias, content)
        self.active_alias = alias
        return {
            "status": "ok",
            "alias": alias,
            "path": str(docs_path),
            "chars": len(content),
            "lines": len(content.splitlines()),
        }

    def set_active_document(self, alias: str) -> dict[str, Any]:
        """Set which loaded document alias should be used by default tools."""
        if alias not in self.documents:
            raise ValueError(f"Unknown document alias: {alias}")
        self.active_alias = alias
        return {"status": "ok", "active_alias": alias}

    def list_documents(self) -> dict[str, Any]:
        """List loaded document aliases and active document metadata."""
        docs = []
        for alias, text in self._document_cache.items():
            docs.append({"alias": alias, "chars": len(text), "lines": len(text.splitlines())})
        return {"documents": docs, "active_alias": self.active_alias, "cache_size": len(self._document_cache), "cache_limit": self._max_documents}

    def chunk_host(
        self,
        strategy: str,
        alias: str = "active",
        size: int = 200_000,
        overlap: int = 0,
        pattern: str = "",
    ) -> dict[str, Any]:
        """Chunk document on host using size/headers/timestamps/json-keys strategies."""
        text = self._resolve_document(alias)
        chunks = self._chunk_text(text, strategy, size=size, overlap=overlap, pattern=pattern)
        preview = chunks[0] if chunks else ""
        return {
            "status": "ok",
            "strategy": strategy,
            "chunk_count": len(chunks),
            "preview": str(preview)[:400],
        }

    def chunk_sandbox(
        self,
        strategy: str,
        variable_name: str = "active_document",
        buffer_name: str = "chunks",
        size: int = 200_000,
        overlap: int = 0,
        pattern: str = "",
    ) -> dict[str, Any]:
        """Chunk the active document inside sandbox and store chunks in a buffer."""
        text = self._resolve_document("active")
        strategy_norm = self._normalize_strategy(strategy)

        code = """
import json

clear_buffer(buffer_name)

if strategy_norm == "size":
    chunks = chunk_by_size(active_document, size=size, overlap=overlap)
elif strategy_norm == "headers":
    chunks = chunk_by_headers(active_document, pattern=pattern or r"^#{1,3} ")
elif strategy_norm == "timestamps":
    chunks = chunk_by_timestamps(active_document, pattern=pattern or r"^\\d{4}-\\d{2}-\\d{2}[T ]")
elif strategy_norm == "json_keys":
    chunks = chunk_by_json_keys(active_document)
else:
    raise ValueError(f"Unsupported strategy: {strategy_norm}")

for chunk in chunks:
    add_buffer(buffer_name, chunk)

SUBMIT(
    status="ok",
    strategy=strategy_norm,
    chunk_count=len(chunks),
    buffer_name=buffer_name,
)
"""
        variables = {
            variable_name: text,
            "active_document": text,
            "strategy_norm": strategy_norm,
            "buffer_name": buffer_name,
            "size": size,
            "overlap": overlap,
            "pattern": pattern,
        }
        return self._execute_submit(code, variables=variables)

    def parallel_semantic_map(
        self,
        query: str,
        chunk_strategy: str = "headers",
        max_chunks: int = 24,
        buffer_name: str = "findings",
    ) -> dict[str, Any]:
        """Run parallel semantic analysis over chunks via llm_query_batched."""
        text = self._resolve_document("active")
        chunks = self._chunk_text(
            text,
            chunk_strategy,
            size=80_000,
            overlap=1_000,
            pattern="",
        )
        chunk_texts = [self._chunk_to_text(c) for c in chunks][:max_chunks]

        prompts = []
        for idx, chunk in enumerate(chunk_texts):
            prompts.append(
                (
                    f"Query: {query}\n"
                    f"Chunk index: {idx}\n"
                    "Return concise findings as plain text.\n\n"
                    f"{chunk[:6000]}"
                )
            )

        code = """
clear_buffer(buffer_name)
responses = llm_query_batched(prompts)
for idx, response in enumerate(responses):
    add_buffer(buffer_name, {"chunk_index": idx, "response": response})

SUBMIT(
    status="ok",
    strategy=chunk_strategy,
    chunk_count=len(prompts),
    findings_count=len(responses),
    buffer_name=buffer_name,
)
"""
        return self._execute_submit(
            code,
            variables={
                "prompts": prompts,
                "buffer_name": buffer_name,
                "chunk_strategy": chunk_strategy,
            },
        )

    def analyze_long_document(self, query: str, alias: str = "active") -> dict[str, Any]:
        """Analyze a long document with the AnalyzeLongDocument signature."""
        self.start()
        document = self._resolve_document(alias)
        rlm = dspy.RLM(
            signature=AnalyzeLongDocument,
            interpreter=self.interpreter,
            max_iterations=self.rlm_max_iterations,
            max_llm_calls=self.rlm_max_llm_calls,
            verbose=self.verbose,
        )
        result = rlm(document=document, query=query)
        return {
            "status": "ok",
            "findings": result.findings,
            "answer": result.answer,
            "sections_examined": result.sections_examined,
            "doc_chars": len(document),
        }

    def summarize_long_document(self, focus: str, alias: str = "active") -> dict[str, Any]:
        """Summarize a long document with the SummarizeLongDocument signature."""
        self.start()
        document = self._resolve_document(alias)
        rlm = dspy.RLM(
            signature=SummarizeLongDocument,
            interpreter=self.interpreter,
            max_iterations=self.rlm_max_iterations,
            max_llm_calls=self.rlm_max_llm_calls,
            verbose=self.verbose,
        )
        result = rlm(document=document, focus=focus)
        return {
            "status": "ok",
            "summary": result.summary,
            "key_points": result.key_points,
            "coverage_pct": result.coverage_pct,
            "doc_chars": len(document),
        }

    def extract_from_logs(self, query: str, alias: str = "active") -> dict[str, Any]:
        """Extract structured patterns from log text via ExtractFromLogs signature."""
        self.start()
        logs = self._resolve_document(alias)
        rlm = dspy.RLM(
            signature=ExtractFromLogs,
            interpreter=self.interpreter,
            max_iterations=self.rlm_max_iterations,
            max_llm_calls=self.rlm_max_llm_calls,
            verbose=self.verbose,
        )
        result = rlm(logs=logs, query=query)
        return {
            "status": "ok",
            "matches": result.matches,
            "patterns": result.patterns,
            "time_range": result.time_range,
        }

    def read_buffer(self, name: str) -> dict[str, Any]:
        """Read the full contents of a sandbox buffer."""
        result = self._execute_submit('SUBMIT(items=get_buffer(name))', variables={"name": name})
        items = result.get("items", [])
        return {"status": "ok", "name": name, "items": items, "count": len(items)}

    def clear_buffer(self, name: str = "") -> dict[str, Any]:
        """Clear one sandbox buffer (or all buffers when name is empty)."""
        if name:
            code = 'clear_buffer(name)\nSUBMIT(status="ok", scope="single", name=name)'
            variables = {"name": name}
        else:
            code = 'clear_buffer()\nSUBMIT(status="ok", scope="all")'
            variables = {}
        return self._execute_submit(code, variables=variables)

    def save_buffer_to_volume(self, name: str, path: str) -> dict[str, Any]:
        """Persist a sandbox buffer to Modal Volume storage as JSON."""
        code = """
import json
items = get_buffer(name)
payload = json.dumps(items, indent=2, ensure_ascii=False, default=str)
saved_path = save_to_volume(path, payload)
SUBMIT(status="ok", saved_path=saved_path, item_count=len(items))
"""
        return self._execute_submit(code, variables={"name": name, "path": path})

    def load_text_from_volume(self, path: str, alias: str = "active") -> dict[str, Any]:
        """Load text from Modal Volume into host-side document memory."""
        result = self._execute_submit(
            'text = load_from_volume(path)\nSUBMIT(status="ok", text=text)',
            variables={"path": path},
        )
        text = str(result.get("text", ""))
        self._set_document(alias, text)
        self.active_alias = alias
        return {
            "status": "ok",
            "alias": alias,
            "chars": len(text),
            "lines": len(text.splitlines()),
        }

    # ---------------------------------------------------------------------
    # Internal helpers
    # ---------------------------------------------------------------------

    def _build_agent(self) -> dspy.ReAct:
        self.react_tools = self._build_tool_list()
        react_tools = list(self.react_tools)
        agent = dspy.ReAct(
            signature=RLMReActChatSignature,
            tools=react_tools,
            max_iters=self.react_max_iters,
        )
        return agent

    def _build_tool_list(self) -> list[Callable[..., Any]]:
        tools: list[Callable[..., Any]] = [
            self.load_document,
            self.set_active_document,
            self.list_documents,
            self.chunk_host,
            self.chunk_sandbox,
            self.parallel_semantic_map,
            self.analyze_long_document,
            self.summarize_long_document,
            self.extract_from_logs,
            self.read_buffer,
            self.clear_buffer,
            self.save_buffer_to_volume,
            self.load_text_from_volume,
        ]
        tools.extend(self._extra_tools)
        return tools

    def _append_history(self, user_request: str, assistant_response: str) -> None:
        messages = list(self.history.messages)
        messages.append(
            {
                "user_request": user_request,
                "assistant_response": assistant_response,
            }
        )
        if self.history_max_turns is not None and self.history_max_turns > 0:
            messages = messages[-self.history_max_turns :]
        self.history = dspy.History(messages=messages)

    def _resolve_document(self, alias: str) -> str:
        if alias == "active":
            if self.active_alias is None:
                raise ValueError("No active document. Use load_document() first.")
            return self._get_document(self.active_alias)
        if alias not in self._document_cache:
            raise ValueError(f"Unknown document alias: {alias}")
        return self._get_document(alias)

    @staticmethod
    def _normalize_strategy(strategy: str) -> str:
        normalized = strategy.strip().lower().replace("-", "_")
        mapping = {
            "size": "size",
            "headers": "headers",
            "header": "headers",
            "timestamps": "timestamps",
            "timestamp": "timestamps",
            "json": "json_keys",
            "json_keys": "json_keys",
        }
        if normalized not in mapping:
            raise ValueError(
                "Unsupported strategy. Choose one of: size, headers, timestamps, json_keys"
            )
        return mapping[normalized]

    def _chunk_text(
        self,
        text: str,
        strategy: str,
        *,
        size: int,
        overlap: int,
        pattern: str,
    ) -> list[Any]:
        strategy_norm = self._normalize_strategy(strategy)
        if strategy_norm == "size":
            return chunk_by_size(text, size=size, overlap=overlap)
        if strategy_norm == "headers":
            return chunk_by_headers(text, pattern=pattern or r"^#{1,3} ")
        if strategy_norm == "timestamps":
            return chunk_by_timestamps(
                text, pattern=pattern or r"^\d{4}-\d{2}-\d{2}[T ]"
            )
        return chunk_by_json_keys(text)

    @staticmethod
    def _chunk_to_text(chunk: Any) -> str:
        """Convert a chunk to text using dispatch table for efficiency.

        Uses a lookup-based approach instead of multiple isinstance checks
        for better performance with large document collections.
        """
        # Fast path for string chunks (most common case)
        if isinstance(chunk, str):
            return chunk

        # Non-dict types: convert to string
        if not isinstance(chunk, dict):
            return str(chunk)

        # Dict chunks: use dispatch table for type-specific formatting
        # Ordered by likelihood for early return
        if "header" in chunk:
            return f"{chunk.get('header', '')}\n{chunk.get('content', '')}".strip()
        if "timestamp" in chunk:
            return chunk.get("content", "")
        if "key" in chunk:
            return f"{chunk.get('key', '')}\n{chunk.get('content', '')}".strip()

        # Fallback: JSON serialize unknown dict types
        return json.dumps(chunk, ensure_ascii=False, default=str)

    @staticmethod
    def _parse_tool_call_status(message: str) -> str | None:
        match = re.match(r"^Calling tool:\s*(.+)$", message.strip())
        if not match:
            return None
        tool_name = match.group(1).strip()
        return f"tool call: {tool_name}"

    @staticmethod
    def _parse_tool_result_status(message: str) -> str | None:
        if message.strip() == "Tool finished.":
            return "tool result: finished"
        return None

    def _execute_submit(
        self,
        code: str,
        *,
        variables: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.start()
        result = self.interpreter.execute(code, variables=variables or {})
        if isinstance(result, FinalOutput):
            output = result.output
            if isinstance(output, dict):
                return output
            return {"output": output}
        return {"output": str(result)}


def list_react_tool_names(tools: Iterable[Callable[..., Any]]) -> list[str]:
    """Return stable tool names for display/debugging."""
    names: list[str] = []
    for tool in tools:
        names.append(getattr(tool, "__name__", str(tool)))
    return names


class _ReActStatusProvider(StatusMessageProvider):
    """Concise status messaging for streamed ReAct sessions."""

    def tool_start_status_message(self, instance: Any, inputs: dict[str, Any]):
        return f"Calling tool: {instance.name}"

    def tool_end_status_message(self, outputs: Any):
        return "Tool finished."

    def module_start_status_message(self, instance: Any, inputs: dict[str, Any]):
        return f"Running module: {instance.__class__.__name__}"

    def module_end_status_message(self, outputs: Any):
        return None
