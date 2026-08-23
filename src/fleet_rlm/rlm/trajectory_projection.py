"""Pure trajectory and live-detail reconciliation for RLM observation projection."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from fleet_rlm.rlm.dspy_contract import TrajectoryStep
from fleet_rlm.rlm.events import ObservationDetail, RLMCode, RLMOutput, RLMReasoning, StepFinished, StepStarted
from fleet_rlm.rlm.outcome import ExecutionDetail
from fleet_rlm.rlm.sanitize import truncate_public_text

_StreamDetail = RLMReasoning | RLMCode | RLMOutput


def trajectory_details(steps: Sequence[TrajectoryStep], *, max_chars: int) -> list[ObservationDetail]:
    """Project strictly normalized DSPy trajectory steps into public details."""
    details: list[ObservationDetail] = []
    for step in steps:
        output = step.output
        if output.startswith("FINAL:"):
            output = "FINAL submitted"
        details.extend(
            (
                StepStarted(step.index),
                RLMReasoning(truncate_public_text(step.reasoning, max_len=max_chars), step.index),
                RLMCode(truncate_public_text(step.code, max_len=max_chars), step.index),
                RLMOutput(truncate_public_text(output, max_len=max_chars), step.index),
                StepFinished(step.index),
            )
        )
    return details


def _stream_text(detail: ExecutionDetail) -> str:
    if isinstance(detail, RLMReasoning):
        return detail.text
    if isinstance(detail, RLMCode):
        return detail.code
    if isinstance(detail, RLMOutput):
        return detail.output
    return ""


def _stream_step(detail: ExecutionDetail) -> int | None:
    if isinstance(detail, (RLMReasoning, RLMCode, RLMOutput)):
        return detail.step
    return None


def _stream_id(detail: ExecutionDetail) -> str | None:
    if isinstance(detail, (RLMReasoning, RLMCode, RLMOutput)):
        return detail.stream_id or None
    return None


def _is_delta(detail: ExecutionDetail) -> bool:
    return isinstance(detail, (RLMReasoning, RLMCode, RLMOutput)) and detail.is_delta


def _preserve_stream_id(target: ObservationDetail, details: Sequence[ExecutionDetail], step: int) -> ObservationDetail:
    """Keep one live stream identity when canonical trajectory data is emitted."""
    if not isinstance(target, (RLMReasoning, RLMCode, RLMOutput)):
        return target
    stream_id = next(
        (
            detail.stream_id
            for detail in details
            if isinstance(detail, _StreamDetail)
            and type(detail) is type(target)
            and detail.step == step
            and isinstance(detail.stream_id, str)
            and bool(detail.stream_id)
        ),
        None,
    )
    if stream_id is None:
        return target
    return replace(target, stream_id=stream_id, is_delta=False, is_final=True)


def _align_trajectory_detail(
    details: Sequence[ExecutionDetail],
    target: ObservationDetail,
    *,
    used_positions: set[int],
) -> ObservationDetail:
    """Align canonical text with a live observation when setup consumed a step.

    The interpreter may execute a host context/bootstrap capsule before DSPy's
    first trajectory action.  That setup observation owns an earlier step number
    even though DSPy's canonical trajectory starts at action one.  Matching the
    exact public payload across steps lets reconciliation update the real live
    action rather than emitting a duplicate canonical action stream.
    """
    text = _stream_text(target)
    if not text:
        return target
    for index, detail in enumerate(details):
        if index in used_positions or type(detail) is not type(target) or _stream_text(detail) != text:
            continue
        observed_step = _stream_step(detail)
        target_step = _stream_step(target)
        if isinstance(observed_step, int) and observed_step != target_step:
            target = replace(target, step=observed_step)
        used_positions.add(index)
        return target
    return target


def _same_stream_payload(
    details: Sequence[ExecutionDetail],
    positions: Sequence[int],
    target: ObservationDetail,
) -> bool:
    """Payload identity between live rows and one canonical trajectory detail.

    Live deltas and the canonical full-text trajectory row describe the same
    stream content when (type, step, stream_id, public text) match, ignoring
    ``is_delta``/``is_final`` flag drift (RC-4a). The live public text is the
    in-order stream projection: delta rows concatenate; a non-delta row
    replaces the content accumulated so far.
    """
    text = ""
    stream_id: str | None = None
    target_step = _stream_step(target)
    for position in positions:
        detail = details[position]
        if type(detail) is not type(target) or _stream_step(detail) != target_step:
            return False
        row_stream_id = _stream_id(detail)
        if stream_id is None:
            stream_id = row_stream_id
        elif row_stream_id is not None and row_stream_id != stream_id:
            return False
        value = _stream_text(detail)
        text = text + value if _is_delta(detail) else value
    return stream_id == _stream_id(target) and text == _stream_text(target)


def _detail_position(details: Sequence[ExecutionDetail], detail_type: type[object], step: int) -> int | None:
    return next(
        (
            index
            for index, detail in enumerate(details)
            if isinstance(detail, detail_type) and getattr(detail, "step", None) == step
        ),
        None,
    )


def _trajectory_insertion(details: Sequence[ExecutionDetail], target: ObservationDetail, step: int, finish: int) -> int:
    if isinstance(target, RLMReasoning):
        start = _detail_position(details, StepStarted, step)
        assert start is not None
        return start + 1
    if isinstance(target, RLMCode):
        reasoning = _detail_position(details, RLMReasoning, step)
        if reasoning is not None:
            return reasoning + 1
        start = _detail_position(details, StepStarted, step)
        assert start is not None
        return start + 1
    return finish


def _missing_step_insertion(details: Sequence[ExecutionDetail], step: int) -> int:
    """Place a missing canonical step before the next live step."""
    return next(
        (index for index, detail in enumerate(details) if isinstance(detail, StepStarted) and detail.step > step),
        len(details),
    )


def has_reasoning(details: Sequence[ExecutionDetail], text: str, max_chars: int) -> bool:
    """True when durable details already contain this truncated public reasoning."""
    return any(
        isinstance(detail, RLMReasoning) and truncate_public_text(detail.text, max_len=max_chars) == text
        for detail in details
    )


def reconcile_trajectory(
    details: list[ExecutionDetail],
    trajectory: Sequence[TrajectoryStep],
    *,
    max_chars: int,
) -> list[ObservationDetail]:
    """Reconcile completed DSPy trajectory details with live observations.

    Observations with an identical public payload (type, step, stream_id, and
    projected text) keep their position: the durable row is upserted to the
    canonical flags without any re-emission. A differing same-step RLM detail
    is replaced in the durable list and re-emitted with the same stable step
    ID so live TUI projection upserts it rather than appending a second card.

    Step-marker positions are indexed once and maintained under local
    insert/delete shifts instead of re-scanning the list per step (P33: one
    derivation per bounded collection).
    """
    step_starts: dict[int, int] = {}
    step_finishes: dict[int, int] = {}
    reasoning_first: dict[int, int] = {}
    for index, detail in enumerate(details):
        if isinstance(detail, StepStarted):
            step_starts.setdefault(detail.step, index)
        elif isinstance(detail, StepFinished):
            step_finishes.setdefault(detail.step, index)
        elif isinstance(detail, RLMReasoning):
            reasoning_step = _stream_step(detail)
            if reasoning_step is not None:
                reasoning_first.setdefault(reasoning_step, index)

    def shift_positions(removed_asc: Sequence[int], *, inserted_at: int | None = None) -> None:
        """Keep marker maps exact across local deletions/insertions."""
        if not removed_asc and inserted_at is None:
            return
        for mapping in (step_starts, step_finishes, reasoning_first):
            for step_index, position in list(mapping.items()):
                adjusted = position - sum(1 for removed in removed_asc if removed < position)
                if inserted_at is not None and adjusted >= inserted_at:
                    adjusted += 1
                mapping[step_index] = adjusted

    emissions: list[ObservationDetail] = []
    aligned_positions: set[int] = set()
    for trajectory_step in trajectory:
        step = trajectory_step.index
        step_details = trajectory_details((trajectory_step,), max_chars=max_chars)
        start = step_starts.get(step)
        finish = step_finishes.get(step)
        if start is None or finish is None or start >= finish:
            insertion = _missing_step_insertion(details, step)
            for detail in step_details:
                details.insert(insertion, detail)
                shift_positions((), inserted_at=insertion)
                insertion += 1
            emissions.extend(step_details)
            continue

        canonical = step_details[1:-1]
        for raw_target in canonical:
            target = _align_trajectory_detail(details, raw_target, used_positions=aligned_positions)
            target_step = _stream_step(target)
            target = _preserve_stream_id(target, details, target_step if isinstance(target_step, int) else step)
            target_step = _stream_step(target)
            if isinstance(target_step, int) and target_step != step:
                aligned_start = step_starts.get(target_step)
                aligned_finish = step_finishes.get(target_step)
                if aligned_start is not None and aligned_finish is not None and aligned_start < aligned_finish:
                    step = target_step
                    start, finish = aligned_start, aligned_finish
            target_type = type(target)
            existing_positions = [
                index
                for index in range(start + 1, finish)
                if isinstance(details[index], target_type) and _stream_step(details[index]) == step
            ]
            if existing_positions:
                first = existing_positions[0]
                # Identical public payload upserts the canonical flags
                # (is_delta=False, is_final=True) into the durable row without
                # re-emitting already-delivered content; a true correction is
                # still re-emitted so the TUI upserts the same stream.
                if not _same_stream_payload(details, existing_positions, target):
                    emissions.append(target)
                details[first] = target
                removed = existing_positions[1:]
                for duplicate in reversed(removed):
                    del details[duplicate]
                shift_positions(sorted(removed))
                start = step_starts[step]
                finish = step_finishes[step]
                assert start < finish
                continue

            # Live observation may publish reasoning before interpreter StepStarted.
            if isinstance(target, RLMReasoning):
                outside = reasoning_first.get(step)
                if outside is not None:
                    if not _same_stream_payload(details, (outside,), target):
                        emissions.append(target)
                    details[outside] = target
                    continue
            insertion = _trajectory_insertion(details, target, step, finish)
            details.insert(insertion, target)
            emissions.append(target)
            shift_positions((), inserted_at=insertion)
            start = step_starts[step]
            finish = step_finishes[step]
            assert start < finish
    return emissions


__all__ = [
    "has_reasoning",
    "reconcile_trajectory",
    "trajectory_details",
]
