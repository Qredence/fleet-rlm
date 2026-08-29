"""Fail-soft DSPy 3.3.1 interpreter callback shadow evidence.

The callback API is an engineering-observability probe only.  It deliberately
does not publish Runtime Events, mutate a Turn result, or replace the manual
Fleet observers.  Call identities are normalized by
``compare_callback_records`` because DSPy generates a fresh id per invocation.
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Any, Literal

from dspy import CodeExecutionError, CodeInterpreterError
from dspy.utils import callback_context
from dspy.utils.callback import BaseCallback

CallbackOperation = Literal["startup", "execute", "tool_call", "shutdown"]

_SAFE_CATEGORY = re.compile(r"^[A-Za-z0-9_.-]{1,96}$")


def _safe_category(exception: BaseException | None) -> str | None:
    if exception is None:
        return None
    if isinstance(exception, CodeExecutionError):
        return "CodeExecutionError"
    if isinstance(exception, CodeInterpreterError):
        return "CodeInterpreterError"
    name = type(exception).__name__
    return name if _SAFE_CATEGORY.fullmatch(name) else "CallbackError"


@dataclass(frozen=True, slots=True)
class CallbackRecord:
    """One normalized, completed DSPy interpreter callback operation."""

    operation: CallbackOperation
    call_id: str
    parent_call_id: str | None
    status: Literal["completed", "failed"]
    duration_ms: int
    exception_category: str | None = None
    tool_name: str | None = None

    def with_duration(self, duration_ms: int) -> CallbackRecord:
        """Return a copy with a test-controlled duration."""
        return replace(self, duration_ms=max(0, int(duration_ms)))


@dataclass(frozen=True, slots=True)
class CallbackComparison:
    """Semantic and timing-only differences between two normalized graphs."""

    timing_only_differences: tuple[str, ...]
    semantic_differences: tuple[str, ...]


@dataclass(slots=True)
class _OpenCallback:
    operation: CallbackOperation
    parent_call_id: str | None
    started_at: float
    tool_name: str | None


class CallbackShadowRecorder(BaseCallback):
    """Record DSPy's interpreter callback lifecycle without product side effects.

    DSPy invokes start hooks before setting ``ACTIVE_CALL_ID`` to the current
    call, so the context value observed by each start hook is the parent
    callback id.  End hooks receive the same current id and close exactly one
    open record.  Any malformed callback payload is reduced to bounded
    structural metadata.
    """

    def __init__(self, *, exporter: Any | None = None) -> None:
        self._open: dict[str, _OpenCallback] = {}
        self._order: list[str] = []
        self._completed: dict[str, CallbackRecord] = {}
        self._exporter = exporter

    def _start(
        self,
        operation: CallbackOperation,
        call_id: str,
        inputs: Mapping[str, Any] | None,
    ) -> None:
        try:
            parent = callback_context.ACTIVE_CALL_ID.get()
            parent_id = str(parent) if parent is not None else None
            raw_tool = inputs.get("tool_name") if isinstance(inputs, Mapping) else None
            tool_name = str(raw_tool)[:96] if raw_tool is not None else None
            self._open[str(call_id)] = _OpenCallback(
                operation=operation,
                parent_call_id=parent_id,
                started_at=time.perf_counter(),
                tool_name=tool_name,
            )
            self._order.append(str(call_id))
        except Exception:
            # Shadow evidence is never allowed to affect a Turn.
            return

    def _end(
        self,
        call_id: str,
        *,
        exception: BaseException | None,
    ) -> None:
        try:
            state = self._open.pop(str(call_id), None)
            if state is None:
                return
            record = CallbackRecord(
                operation=state.operation,
                call_id=str(call_id),
                parent_call_id=state.parent_call_id,
                status="failed" if exception is not None else "completed",
                duration_ms=max(0, int((time.perf_counter() - state.started_at) * 1_000)),
                exception_category=_safe_category(exception),
                tool_name=state.tool_name,
            )
            self._completed[str(call_id)] = record
            if self._exporter is not None:
                try:
                    self._exporter(record)
                except Exception:
                    return
        except Exception:
            return

    def on_interpreter_startup_start(
        self,
        call_id: str,
        instance: Any,
        inputs: dict[str, Any],
    ) -> None:
        del instance
        self._start("startup", call_id, inputs)

    def on_interpreter_startup_end(
        self,
        call_id: str,
        outputs: Any | None,
        exception: BaseException | None = None,
    ) -> None:
        del outputs
        self._end(call_id, exception=exception)

    def on_interpreter_execute_start(
        self,
        call_id: str,
        instance: Any,
        inputs: dict[str, Any],
    ) -> None:
        del instance
        self._start("execute", call_id, inputs)

    def on_interpreter_execute_end(
        self,
        call_id: str,
        outputs: Any | None,
        exception: BaseException | None = None,
    ) -> None:
        del outputs
        self._end(call_id, exception=exception)

    def on_interpreter_tool_call_start(
        self,
        call_id: str,
        instance: Any,
        inputs: dict[str, Any],
    ) -> None:
        del instance
        self._start("tool_call", call_id, inputs)

    def on_interpreter_tool_call_end(
        self,
        call_id: str,
        outputs: Any | None,
        exception: BaseException | None = None,
    ) -> None:
        del outputs
        self._end(call_id, exception=exception)

    def on_interpreter_shutdown_start(
        self,
        call_id: str,
        instance: Any,
        inputs: dict[str, Any],
    ) -> None:
        del instance
        self._start("shutdown", call_id, inputs)

    def on_interpreter_shutdown_end(
        self,
        call_id: str,
        outputs: Any | None,
        exception: BaseException | None = None,
    ) -> None:
        del outputs
        self._end(call_id, exception=exception)

    def records(self) -> tuple[CallbackRecord, ...]:
        """Return completed records in callback start order."""
        return tuple(self._completed[call_id] for call_id in self._order if call_id in self._completed)

    def open_call_count(self) -> int:
        """Return the number of callback starts without a matching end."""
        return len(self._open)


def _normalized_parent_index(
    records: tuple[CallbackRecord, ...],
    index: int,
) -> tuple[str, int | None]:
    """Normalize local, external, and root parentage without losing semantics."""
    parent_id = records[index].parent_call_id
    if parent_id is None:
        return ("root", None)
    for candidate_index, candidate in enumerate(records):
        if candidate.call_id == parent_id:
            return ("local", candidate_index)
    # DSPy may parent an interpreter callback under an RLM/module callback that
    # is intentionally not recorded by this interpreter-only shadow probe.
    # Preserve that fact as an external parent rather than collapsing it into
    # a root, while ignoring generated external ids during parity comparison.
    return ("external", None)


def compare_callback_records(
    expected: Iterable[CallbackRecord],
    observed: Iterable[CallbackRecord],
) -> CallbackComparison:
    """Compare two callback graphs while ignoring generated ids and timing.

    Differences in duration are explicitly classified as timing-only.  Any
    lifecycle operation, status, exception category, Tool identity, or parent
    relation difference is semantic.
    """
    left = tuple(expected)
    right = tuple(observed)
    timing: list[str] = []
    semantic: list[str] = []
    if len(left) != len(right):
        semantic.append(f"record_count:{len(left)}!={len(right)}")
    for index, (a, b) in enumerate(zip(left, right, strict=False)):
        prefix = f"record[{index}]"
        if a.operation != b.operation:
            semantic.append(f"{prefix}.operation:{a.operation}!={b.operation}")
        if a.status != b.status:
            semantic.append(f"{prefix}.status:{a.status}!={b.status}")
        if a.exception_category != b.exception_category:
            semantic.append(f"{prefix}.exception_category")
        if a.tool_name != b.tool_name:
            semantic.append(f"{prefix}.tool_name:{a.tool_name}!={b.tool_name}")
        if _normalized_parent_index(left, index) != _normalized_parent_index(right, index):
            semantic.append(f"{prefix}.parent")
        if a.duration_ms != b.duration_ms:
            timing.append(f"{prefix}.duration_ms:{a.duration_ms}!={b.duration_ms}")
    return CallbackComparison(tuple(timing), tuple(semantic))


__all__ = [
    "CallbackComparison",
    "CallbackRecord",
    "CallbackShadowRecorder",
    "compare_callback_records",
]
