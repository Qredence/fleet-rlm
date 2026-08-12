"""Public interpreter output projection: stdout deltas, stream closure, final flush.

Mechanically extracted from ``interpreter.py`` (WS-8 readability split, no
behavior change). The interpreter owns execution and observation; this module
owns how one step's public ``RLMOutput`` stream is projected: marker-hiding
stdout replay, capped per-step deltas, idempotent closing flush, and the
private repair-feedback type that must never leak into public frames.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from fleet_rlm.daytona.broker_source import FINAL_OUTPUT_MARKER, final_output_frame
from fleet_rlm.rlm.dspy_interpreter_contract import is_final_output
from fleet_rlm.rlm.events import RLMOutput

OutputCallback = Callable[[str], None]


class _RepairFeedback(str):
    """Detailed interpreter feedback returned to RLM but not public projection."""

    category: str

    def __new__(cls, value: str, *, category: str = "execution_error") -> _RepairFeedback:
        result = super().__new__(cls, value)
        result.category = category
        return result


class _PublicStdoutProjector:
    """Forward ordinary stdout while hiding the known SUBMIT stdout frame.

    The marker may also occur in ordinary user stdout, so a marker alone is not
    treated as a control frame. The private frame is removed only when it exactly
    matches the final payload returned by the execution backend.
    """

    def __init__(self, emit: OutputCallback) -> None:
        self._emit = emit
        self._marker = FINAL_OUTPUT_MARKER
        self._buffer = ""

    def feed(self, value: str) -> None:
        if not value:
            return
        pending = self._buffer + value
        self._buffer = ""
        start = pending.find(self._marker)
        if start >= 0:
            if start:
                self._emit(pending[:start])
            self._buffer = pending[start:]
            return
        suffix = self._marker_prefix_suffix(pending)
        if suffix:
            self._emit(pending[: -len(suffix)])
            self._buffer = suffix
        else:
            self._emit(pending)

    def finish(self, *, expected_final: Mapping[str, Any] | None = None) -> None:
        pending = self._buffer
        self._buffer = ""
        if not pending:
            return
        if expected_final is None:
            self._emit(pending)
            return

        frame = final_output_frame(expected_final, marker=self._marker)
        offset = 0
        while True:
            start = pending.find(frame, offset)
            if start < 0:
                self._emit(pending[offset:])
                return
            self._emit(pending[offset:start])
            offset = start + len(frame)
            if pending.startswith("\r\n", offset):
                offset += 2
            elif pending.startswith(("\n", "\r"), offset):
                offset += 1

    def _marker_prefix_suffix(self, value: str) -> str:
        for length in range(min(len(value), len(self._marker) - 1), 0, -1):
            if value.endswith(self._marker[:length]):
                return value[-length:]
        return ""


@dataclass(slots=True)
class _OutputStreamState:
    """Per-step public output-stream tracking (RC-4b).

    Streamed delta text is remembered in ``streamed_chunks`` so the terminal
    flush never repeats already-streamed content, and ``closed`` blocks
    straggler stdout deltas after a terminal frame closed the stream.
    """

    emitted_chars: int = 0
    streamed_chunks: list[str] = field(default_factory=list)
    closed: bool = False


def _emit_output_delta(
    value: str,
    *,
    step: int,
    stream_id: str,
    state: _OutputStreamState,
    max_chars: int,
    observe: Callable[[RLMOutput], None],
) -> None:
    if state.closed or not value:
        # A step's terminal output frame (e.g. the "FINAL submitted" label
        # or an error frame) closes its stream; late stdout from a
        # straggler backend callback must not emit after it (RC-4b).
        return
    remaining = max_chars - state.emitted_chars
    if remaining <= 0:
        return
    chunk = value[:remaining]
    state.emitted_chars += len(chunk)
    if chunk:
        state.streamed_chunks.append(chunk)
        observe(RLMOutput(chunk, step, stream_id, True, False))


def _close_output_stream(
    text: str,
    *,
    step: int,
    stream_id: str,
    state: _OutputStreamState,
    observe: Callable[[RLMOutput], None],
) -> None:
    state.closed = True
    observe(RLMOutput(text, step, stream_id, False, True))


def _flush_step_output(
    result: Any,
    *,
    step: int,
    stream_id: str,
    state: _OutputStreamState,
    public_output: Callable[[Any], str],
    observe: Callable[[RLMOutput], None],
) -> None:
    """Close one step's output stream without repeating streamed content.

    RC-4b: stdout deltas are tracked in the step's stream state so the final
    flush is idempotent. When deltas already cover the full public output
    no closing frame is emitted — the TUI contract
    (tools/fleet-tui/src/tui/live-projection.ts `projectRlm`) does not require
    a terminal ``is_final`` frame per rlm-output stream
    (``fleet-turn-stream.ts`` lifecycle only tracks reasoning/text/tool
    streams, and ``store.ts`` settles leftover streaming cards at the run
    terminal), and an empty non-delta frame would REPLACE the accumulated
    content with "" both in the TUI and in the durable
    ``turn_detail_policy`` projection. A partially streamed step emits
    only the unsent tail as a closing delta (a non-delta tail frame would
    replace the stream with just the tail). Distinct terminal texts — the
    SUBMIT label and repair feedback — still replace the stream with one
    canonical non-delta final frame.
    """
    if state.closed:
        return
    public = public_output(result)
    if is_final_output(result) or isinstance(result, _RepairFeedback):
        _close_output_stream(public, step=step, stream_id=stream_id, state=state, observe=observe)
        return
    streamed = "".join(state.streamed_chunks)
    if public == streamed:
        state.closed = True
        return
    if public.startswith(streamed):
        tail = public[len(streamed) :]
        state.closed = True
        observe(RLMOutput(tail, step, stream_id, True, True))
        return
    _close_output_stream(public, step=step, stream_id=stream_id, state=state, observe=observe)
