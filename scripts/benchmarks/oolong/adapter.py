"""Shared Oolong predict adapter helpers for Fleet RLM."""

from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4

import dspy

from fleet_rlm.rlm.program import (
    AttachmentContextCapsule,
    AttachmentContextEntry,
    FleetRLMSignature,
    build_native_rlm,
    build_rlm_input_kwargs,
    rlm_options,
)
from fleet_rlm.rlm.result import observed_usage
from fleet_rlm.sessions.context import build_session_context_manifest
from fleet_rlm.sessions.history_transport import CommittedSessionHistory
from fleet_rlm.sessions.models import SessionHistory
from scripts.benchmarks.oolong.scoring import (
    OOLONG_EVAL_HELPERS_REVISION,
    dnd_process_response,
    synth_process_response,
)
from scripts.benchmarks.usage_cost import observed_spend

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_FIXTURE = Path(__file__).with_name("fixture_validation_row.json")
DATASET_IDS = {
    "synth": "oolongbench/oolong-synth",
    "real": "oolongbench/oolong-real",
}
SYNTH_SPLITS = frozenset({"validation", "test"})
REAL_SPLITS = frozenset({"test"})
DEFAULT_HF_DATASET_REVISIONS = {
    "synth": "f0d59eaf0febf130664cfceb710436c8e3216b2b",
    "real": "6bc9ef04866fcf005c9749b70649be69dd37fffb",
}
# Pinned HF builder configs; None means single-config dataset (omit the name argument).
DEFAULT_HF_DATASET_CONFIGS: dict[str, str | None] = {
    "synth": None,
    "real": "dnd",
}
RECEIPT_SCORE_FIELDS = frozenset(
    {
        "id",
        "context_window_id",
        "dataset",
        "model",
        "attempted_parse",
        "parse_confidence",
        "score",
        "answer",
        "answer_normalized",
    }
)
# Dry-only request concatenation cap; production/live must use AttachmentContextCapsule.
DRY_REQUEST_CONCAT_CAP = 100_000

_ANSWER_EXTRAS = FleetRLMSignature.fields["answer"].json_schema_extra
_BASE_ANSWER_DESC = str(_ANSWER_EXTRAS.get("desc", "")) if isinstance(_ANSWER_EXTRAS, dict) else ""
# The Oolong real-split (DND) rubric extracts answers with a \boxed{...} pattern; the task's own
# context template asks for that wrapper, but Fleet's answer field asks for a "concise user-facing
# answer", so a compliant-looking bare value is unparseable and scores zero even when nearly right.
_DND_ANSWER_FORMAT_NOTE = (
    " This prediction is scored by the Oolong real-split (DND) rubric, which extracts the final answer with a "
    "\\boxed{...} pattern: submit the answer exactly as the task instruction requires it, wrapper included. "
    "A bare value is unparseable and scores zero even when it is nearly correct."
)

# Verbatim answer-format sentence from the real-split context template. The template lives in the
# attached context, so restate it alongside the question: otherwise a model can answer correctly and
# still be unparseable, which zeroes even near-correct numeric credit on the official rubric.
_DND_REQUEST_FORMAT_LINE = "Return the final answer in \\boxed{}."


@dataclass(frozen=True, slots=True)
class LivePrediction:
    """One live prediction: the submitted answer plus observed usage, if any.

    ``usage`` is ``None`` when the runtime observed nothing; DSPy's usage tracker is
    thread-local, so absence is not evidence of zero tokens.
    """

    answer: str
    usage: Mapping[str, object] | None = None


class OolongDNDRLMSignature(FleetRLMSignature):
    """Fleet Root RLM contract carrying the Oolong real-split answer-format requirement."""

    answer: str = dspy.OutputField(desc=_BASE_ANSWER_DESC + _DND_ANSWER_FORMAT_NOTE)


OolongDNDRLMSignature.instructions = FleetRLMSignature.instructions
ContextMode = Literal["production", "dry_shortcut"]


class OolongAdapterError(RuntimeError):
    """Bounded Oolong adapter failure."""


@dataclass(frozen=True, slots=True)
class LoadedDatapoint:
    """One HF-shaped Oolong row plus provenance metadata."""

    row: dict[str, object]
    dataset: str
    split: str
    index: int
    source: str
    dataset_revision: str | None = None


def git_identity(repo_root: Path = REPO_ROOT) -> dict[str, object]:
    """Return bounded repository identity for receipts."""
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            check=True,
            text=True,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo_root,
            capture_output=True,
            check=True,
            text=True,
        )
        dirty = bool(status.stdout)
    except (OSError, subprocess.CalledProcessError):
        return {"revision": "unknown", "dirty": True}
    return {"revision": revision[:64], "dirty": dirty}


def load_fixture(path: Path = DEFAULT_FIXTURE) -> dict[str, object]:
    """Load one bundled HF-shaped row for credential-free dry runs."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OolongAdapterError("fixture row could not be read as JSON") from exc
    if not isinstance(payload, Mapping):
        raise OolongAdapterError("fixture row must be a JSON object")
    return dict(payload)


def receipt_safe_score(score: Mapping[str, object]) -> dict[str, object]:
    """Project one official score payload to bounded receipt fields."""
    return {key: score[key] for key in RECEIPT_SCORE_FIELDS if key in score}


def _open_hf_stream(
    *,
    hf_id: str,
    config: str | None,
    split: str,
    revision: str,
    columns: Sequence[str] | None = None,
    filters: Sequence[tuple[str, str, object]] | None = None,
) -> Any:
    """Open the pinned split as a stream.

    ``columns``/``filters`` push down into the parquet reader, so a metadata pass costs
    the projected column bytes rather than the whole split. The previous slice-based
    fetch (``split="validation[i:i+1]"``) materialized every shard first: ~2GB per row
    for synth and ~9GB for real.
    """
    from datasets import load_dataset

    kwargs: dict[str, Any] = {"split": split, "revision": revision, "streaming": True}
    if columns is not None:
        kwargs["columns"] = list(columns)
    if filters is not None:
        kwargs["filters"] = list(filters)
    if config is not None:
        kwargs["name"] = config
    return load_dataset(hf_id, **kwargs)


def load_hf_row(
    *,
    dataset: str,
    split: str,
    index: int,
    revision: str | None = None,
) -> LoadedDatapoint:
    """Load one Hugging Face row from the official Oolong dataset ids."""
    if dataset not in DATASET_IDS:
        raise OolongAdapterError(f"unknown dataset {dataset!r}; expected one of {sorted(DATASET_IDS)}")
    allowed = REAL_SPLITS if dataset == "real" else SYNTH_SPLITS
    if split not in allowed:
        raise OolongAdapterError(f"split {split!r} is invalid for dataset {dataset!r}")
    if index < 0:
        raise OolongAdapterError("index must be non-negative")
    try:
        import datasets  # noqa: F401
    except ImportError as exc:
        raise OolongAdapterError(
            "the `datasets` package is required for Hugging Face loads; install benchmark extras or pass --fixture"
        ) from exc
    hf_id = DATASET_IDS[dataset]
    resolved_revision = revision or DEFAULT_HF_DATASET_REVISIONS[dataset]
    try:
        stream = _open_hf_stream(
            hf_id=hf_id,
            config=DEFAULT_HF_DATASET_CONFIGS[dataset],
            split=split,
            revision=resolved_revision,
        )
        row = next(iter(stream.skip(index).take(1)))
    except Exception as exc:
        raise OolongAdapterError(f"could not load {hf_id} split={split} index={index}") from exc
    return LoadedDatapoint(
        row=dict(row),
        dataset=dataset,
        split=split,
        index=index,
        source="huggingface",
        dataset_revision=resolved_revision,
    )


_SELECTOR_COLUMNS = ("id", "dataset", "context_len")


def _row_matches(row: Mapping[str, object], *, context_len: int | None, row_dataset: str | None) -> bool:
    """Return whether a synth row matches the requested tier."""
    if row_dataset is not None and str(row.get("dataset")) != row_dataset:
        return False
    if context_len is not None:
        observed = row.get("context_len")
        if not isinstance(observed, int) or isinstance(observed, bool) or observed != context_len:
            return False
    return True


def select_hf_offsets(
    *,
    dataset: str,
    split: str,
    limit: int,
    start_index: int = 0,
    context_len: int | None = None,
    row_dataset: str | None = None,
    revision: str | None = None,
) -> tuple[int, ...]:
    """Resolve absolute split offsets for a benchmark tier (``context_len`` / row dataset).

    The scan is column-projected, so it costs the projected bytes rather than the whole
    split: measured ~2.3KiB / 1.6s for the 128K ``trec_coarse`` tier versus ~2GB for the
    materialized alternative.
    """
    if dataset == "real":
        raise OolongAdapterError(
            "row selection by context_len/dataset needs synth metadata; oolong-real has no such columns"
        )
    hf_id = DATASET_IDS[dataset]
    resolved_revision = revision or DEFAULT_HF_DATASET_REVISIONS[dataset]
    try:
        stream = _open_hf_stream(
            hf_id=hf_id,
            config=DEFAULT_HF_DATASET_CONFIGS[dataset],
            split=split,
            revision=resolved_revision,
            columns=_SELECTOR_COLUMNS,
        )
        offsets: list[int] = []
        matched = 0
        for offset, row in enumerate(stream):
            if not _row_matches(row, context_len=context_len, row_dataset=row_dataset):
                continue
            if matched >= start_index:
                offsets.append(offset)
                if len(offsets) >= limit:
                    break
            matched += 1
    except OolongAdapterError:
        raise
    except Exception as exc:
        raise OolongAdapterError(f"could not scan {hf_id} split={split} for the requested tier") from exc
    if not offsets:
        raise OolongAdapterError(
            f"no {hf_id} rows in split={split} match context_len={context_len} dataset={row_dataset!r}"
        )
    return tuple(offsets)


def _fetch_selected_rows(
    *,
    dataset: str,
    split: str,
    offsets: Sequence[int],
    context_len: int | None,
    row_dataset: str | None,
    revision: str | None,
) -> tuple[LoadedDatapoint, ...]:
    """Fetch payloads for resolved offsets, pushing the tier filter into the reader."""
    hf_id = DATASET_IDS[dataset]
    resolved_revision = revision or DEFAULT_HF_DATASET_REVISIONS[dataset]
    filters: list[tuple[str, str, object]] = []
    if row_dataset is not None:
        filters.append(("dataset", "==", row_dataset))
    if context_len is not None:
        filters.append(("context_len", "==", context_len))
    try:
        stream = _open_hf_stream(
            hf_id=hf_id,
            config=DEFAULT_HF_DATASET_CONFIGS[dataset],
            split=split,
            revision=resolved_revision,
            filters=filters or None,
        )
        rows = []
        for offset, row in zip(offsets, stream, strict=False):
            rows.append(
                LoadedDatapoint(
                    row=dict(row),
                    dataset=dataset,
                    split=split,
                    index=offset,
                    source="huggingface",
                    dataset_revision=resolved_revision,
                )
            )
    except Exception as exc:
        raise OolongAdapterError(f"could not load {hf_id} split={split} offset(s)={list(offsets)}") from exc
    return tuple(rows)


def resolve_datapoints(
    *,
    dataset: str,
    split: str,
    start_index: int,
    limit: int,
    fixture: Path | None,
    hf_revision: str | None = None,
    context_len: int | None = None,
    row_dataset: str | None = None,
) -> tuple[LoadedDatapoint, ...]:
    """Load up to ``limit`` datapoints from a fixture or Hugging Face.

    ``context_len`` / ``row_dataset`` select a benchmark tier by metadata (for example the
    ``trec_coarse`` rows at 131072 tokens). Without them selection stays positional by
    ``start_index``, which cannot express a tier.
    """
    if limit < 1:
        raise OolongAdapterError("limit must be at least 1")
    if start_index < 0:
        raise OolongAdapterError("index must be non-negative")
    if dataset not in DATASET_IDS:
        raise OolongAdapterError(f"unknown dataset {dataset!r}; expected one of {sorted(DATASET_IDS)}")
    allowed = REAL_SPLITS if dataset == "real" else SYNTH_SPLITS
    if split not in allowed:
        raise OolongAdapterError(f"split {split!r} is invalid for dataset {dataset!r}")
    if fixture is not None:
        if context_len is not None or row_dataset is not None:
            raise OolongAdapterError("row selection requires --hf; the bundled fixture is a single fixed row")
        if start_index != 0 or limit != 1:
            raise OolongAdapterError("fixture mode supports only --index 0 --limit 1")
        if dataset == "real":
            raise OolongAdapterError("bundled synth fixture cannot be used with dataset=real; pass --hf")
        row = load_fixture(fixture)
        return (
            LoadedDatapoint(
                row=row,
                dataset=dataset,
                split=split,
                index=start_index,
                source="fixture",
            ),
        )
    if context_len is not None or row_dataset is not None:
        offsets = select_hf_offsets(
            dataset=dataset,
            split=split,
            limit=limit,
            start_index=start_index,
            context_len=context_len,
            row_dataset=row_dataset,
            revision=hf_revision,
        )
        return _fetch_selected_rows(
            dataset=dataset,
            split=split,
            offsets=offsets,
            context_len=context_len,
            row_dataset=row_dataset,
            revision=hf_revision,
        )
    return tuple(
        load_hf_row(
            dataset=dataset,
            split=split,
            index=start_index + offset,
            revision=hf_revision,
        )
        for offset in range(limit)
    )


def stage_context_capsule(
    context_text: str,
    *,
    staging_root: Path,
    attachment_id: UUID | None = None,
) -> AttachmentContextCapsule:
    """Stage ``context_window_text`` on a host path for dry/unit tests only."""
    staging_root.mkdir(parents=True, exist_ok=True)
    body = context_text.encode("utf-8")
    filename = "oolong_context.txt"
    context_path = staging_root / filename
    context_path.write_bytes(body)
    attachment_uuid = attachment_id or uuid4()
    return AttachmentContextCapsule(
        (
            AttachmentContextEntry(
                attachment_id=attachment_uuid,
                filename=filename,
                content_type="text/plain; charset=utf-8",
                byte_size=len(body),
                checksum_sha256=hashlib.sha256(body).hexdigest(),
                sandbox_path=str(context_path),
            ),
        ),
        mount_root=str(staging_root),
    )


async def stage_attachment_context_on_lease(
    lease: Any,
    context_text: str,
    *,
    filename: str = "oolong_context.txt",
    attachment_id: UUID | None = None,
    content_type: str = "text/plain; charset=utf-8",
) -> AttachmentContextCapsule:
    """Stage ``context_window_text`` on the lease volume using Turn path policy."""
    from fleet_rlm.attachments.models import AttachmentRun
    from fleet_rlm.attachments.paths import WorkspaceAttachmentPathPolicy
    from fleet_rlm.workspace.storage import AgentAsyncVolumeStorage

    body = context_text.encode("utf-8")
    attachment_uuid = attachment_id or uuid4()
    path_policy = WorkspaceAttachmentPathPolicy(lease.volume_paths)
    run = AttachmentRun(lease.session_id, lease.run_id)
    logical_path = path_policy.run_attachment(run, attachment_uuid, filename)
    storage = AgentAsyncVolumeStorage(lease.sandbox, mount_path=lease.context_mount_path)
    await storage.write_bytes(logical_path, body)
    return AttachmentContextCapsule(
        (
            AttachmentContextEntry(
                attachment_id=attachment_uuid,
                filename=filename,
                content_type=content_type,
                byte_size=len(body),
                checksum_sha256=hashlib.sha256(body).hexdigest(),
                sandbox_path=logical_path,
            ),
        ),
        mount_root=lease.context_mount_path,
    )


def build_predict_kwargs(
    datapoint: Mapping[str, object],
    *,
    mode: ContextMode,
    session_id: UUID | None = None,
    attachment_context: AttachmentContextCapsule | None = None,
    dataset: str = "synth",
) -> dict[str, Any]:
    """Build locked Fleet RLM kwargs for one Oolong row.

    Real-split rows restate the task's own ``\\boxed{}`` answer-format requirement in the request,
    because the official rubric cannot parse an unwrapped answer.
    """
    question = str(datapoint.get("question", "")).strip()
    if dataset == "real":
        question = f"{question}\n\n{_DND_REQUEST_FORMAT_LINE}"
    if not question:
        raise OolongAdapterError("datapoint is missing question")
    context_text = str(datapoint.get("context_window_text", ""))
    sid = session_id or uuid4()
    session_context = build_session_context_manifest(sid, 0, SessionHistory())

    if mode == "production":
        if attachment_context is None:
            raise OolongAdapterError("production mode requires a lease-staged attachment_context capsule")
        capsule = attachment_context
        kwargs = build_rlm_input_kwargs(
            request=question,
            session_context=session_context,
            skill_cards=(),
            attachments=(),
            attachment_context=capsule,
            history=CommittedSessionHistory([]),
            signature=FleetRLMSignature,
        )
        kwargs["attachment_context"] = capsule
        return kwargs

    if mode == "dry_shortcut":
        combined = f"{context_text}\n\n{question}".strip() if context_text else question
        if len(combined) > DRY_REQUEST_CONCAT_CAP:
            raise OolongAdapterError(
                "dry_shortcut request exceeds cap; use production AttachmentContextCapsule mode instead"
            )
        return build_rlm_input_kwargs(
            request=combined,
            session_context=session_context,
            skill_cards=(),
            attachments=(),
            history=dspy.History(messages=[]),
            signature=FleetRLMSignature,
        )

    raise OolongAdapterError(f"unknown context mode {mode!r}")


def kwargs_context_mode(kwargs: Mapping[str, Any]) -> str:
    """Return a receipt-safe label for how context reached the model."""
    if isinstance(kwargs.get("attachment_context"), AttachmentContextCapsule):
        return "attachment_context_capsule"
    return "dry_request_concat"


def score_prediction(
    datapoint: Mapping[str, object],
    answer: str,
    *,
    dataset: str,
    model_name: str,
) -> dict[str, object]:
    """Score one answer string with the official Oolong helpers."""
    if dataset == "real":
        scored_answer, normalized = normalize_dnd_answer(answer)
        payload = dnd_process_response(dict(datapoint), scored_answer, model_name)
        if normalized:
            payload["answer_normalized"] = True
        return payload
    return synth_process_response(dict(datapoint), answer, model_name)


def oolong_signature(dataset: str) -> type[Any]:
    """Return the RLM signature carrying the answer-format contract for ``dataset``."""
    if dataset not in DATASET_IDS:
        raise OolongAdapterError(f"unknown dataset {dataset!r}; expected one of {sorted(DATASET_IDS)}")
    return OolongDNDRLMSignature if dataset == "real" else FleetRLMSignature


_DND_BOXED_MARKER = "\\boxed"


def normalize_dnd_answer(answer: str) -> tuple[str, bool]:
    """Wrap a typed real-split answer so the official extractor can read it.

    The official real-split rubric reads free-text generations and therefore needs a ``\\boxed{...}``
    delimiter. Fleet's typed output contract already delivers the extracted value in a dedicated
    field, so a model that submits the bare value is not wrong -- the delimiter is a transport
    artifact. Wrapping an unwrapped value satisfies the official extractor without changing it.

    Returns:
        tuple[str, bool]: The answer to score, and whether wrapping was applied.
    """
    text = answer.strip()
    if not text or _DND_BOXED_MARKER in text:
        return answer, False
    return f"\\boxed{{{text}}}", True


def build_native_program(settings: Any, *, sub_lm: Any | None = None, dataset: str = "synth") -> Any:
    """Construct the locked native RLM program for one predict call."""
    if sub_lm is None:
        from fleet_rlm.rlm.program import build_model_bundle

        sub_lm = build_model_bundle(settings).sub_lm
    return build_native_rlm(
        signature=oolong_signature(dataset),
        options=rlm_options(settings),
        sub_lm=sub_lm,
    )


async def release_ephemeral_lease(
    lease: Any,
    *,
    staged_paths: Sequence[str] = (),
) -> None:
    """Shut down the interpreter, remove staged volume paths, and delete the sandbox."""
    from fleet_rlm.workspace.storage import AgentAsyncVolumeStorage

    cleanup_errors: list[BaseException] = []
    if staged_paths:
        storage = AgentAsyncVolumeStorage(lease.sandbox, mount_path=lease.context_mount_path)
        for logical_path in staged_paths:
            try:
                await storage.remove(logical_path)
            except BaseException as exc:
                cleanup_errors.append(exc)
    interpreter = getattr(lease, "interpreter", None)
    if interpreter is not None:
        shutdown = getattr(interpreter, "shutdown", None)
        if callable(shutdown):
            try:
                await asyncio.to_thread(shutdown, strict_broker_cleanup=True)
            except BaseException as exc:
                cleanup_errors.append(exc)
    try:
        await lease.platform.delete(lease.sandbox)
    except BaseException as exc:
        cleanup_errors.append(exc)
    if cleanup_errors:
        raise OolongAdapterError("ephemeral lease cleanup failed") from cleanup_errors[0]


def _run_prediction_on_worker(
    rlm: Any,
    interpreter: Any,
    resolved_root: Any,
    adapter: Any,
    invoke_kwargs: Mapping[str, Any],
) -> Any:
    """Execute one native RLM call on a private event loop outside the bridge guard."""

    async def _execute() -> Any:
        with dspy.context(lm=resolved_root, adapter=adapter, track_usage=True):
            return await rlm.acall(interpreter, **dict(invoke_kwargs))

    return asyncio.run(_execute())


async def invoke_live_prediction(
    settings: Any,
    kwargs: Mapping[str, Any],
    *,
    interpreter: Any,
    deadline: float,
    wrap_up_seconds: float,
    turn_budget: Any | None = None,
    root_lm: Any | None = None,
    sub_lm: Any | None = None,
    dataset: str = "synth",
) -> LivePrediction:
    """Invoke one live prediction through the owned worker / private-loop seam."""
    from fleet_rlm.rlm.budget import TurnBudget
    from fleet_rlm.rlm.compat_3_3_1 import assert_dspy_version
    from fleet_rlm.rlm.program import FleetJSONAdapter, RLMModelBundle, build_model_bundle
    from fleet_rlm.runtime.owned_effect import OwnedEffect

    assert_dspy_version()
    budget = turn_budget if turn_budget is not None else TurnBudget(deadline=deadline)
    if root_lm is None or sub_lm is None:
        bundle = build_model_bundle(settings)
        resolved_root = root_lm or bundle.root_lm
        resolved_sub = sub_lm or bundle.sub_lm
    else:
        resolved_root = root_lm
        resolved_sub = sub_lm
    turn_models = RLMModelBundle(root_lm=resolved_root, sub_lm=resolved_sub).bind_turn_deadline(
        deadline=deadline,
        reserve_seconds=wrap_up_seconds,
        budget=budget,
    )
    resolved_root = turn_models.root_lm
    resolved_sub = turn_models.sub_lm
    capsule = kwargs.get("attachment_context")
    if isinstance(capsule, AttachmentContextCapsule):
        bind = getattr(interpreter, "bind_context_capsule", None)
        if callable(bind):
            bind(capsule)
    rlm = build_native_program(settings, sub_lm=resolved_sub, dataset=dataset)
    invoke_kwargs = {key: value for key, value in kwargs.items() if key != "attachment_context"}
    adapter = FleetJSONAdapter(
        deadline=deadline,
        wrap_up_seconds=wrap_up_seconds,
        budget=budget,
    )
    started = time.perf_counter()
    effect = OwnedEffect.start(
        asyncio.to_thread(
            _run_prediction_on_worker,
            rlm,
            interpreter,
            resolved_root,
            adapter,
            invoke_kwargs,
        )
    )
    settled = await effect.settle()
    if settled.caller_cancelled:
        raise asyncio.CancelledError
    prediction = settled.result()
    answer = str(getattr(prediction, "answer", ""))
    usage = observed_usage(
        prediction,
        duration_ms=int((time.perf_counter() - started) * 1000),
        lms=(resolved_root, resolved_sub),
    )
    observed = usage.get("observed_lm_usage")
    return LivePrediction(answer=answer, usage=usage if observed else None)


def summarize_scores(scores: Sequence[Mapping[str, object]]) -> dict[str, object]:
    """Aggregate official score payloads for receipts."""
    values = [float(item["score"]) for item in scores if isinstance(item.get("score"), (int, float))]
    if not values:
        return {"count": 0, "mean": 0.0}
    return {"count": len(values), "mean": sum(values) / len(values)}


def _observed_int(value: object) -> int:
    """Coerce an observed counter, treating anything non-numeric as absent (zero)."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def sum_lm_usage(usages: Sequence[Mapping[str, object] | None]) -> dict[str, object] | None:
    """Sum observed per-model token counts across rows.

    Returns ``None`` when no row produced usage, so callers can report "unavailable"
    rather than a zeroed measurement.
    """
    merged: dict[str, dict[str, int]] = {}
    iterations = 0
    duration_ms = 0
    observed = False
    for usage in usages:
        if not usage:
            continue
        observed = True
        iterations += _observed_int(usage.get("iterations"))
        duration_ms += _observed_int(usage.get("duration_ms"))
        per_model = usage.get("observed_lm_usage")
        if not isinstance(per_model, Mapping):
            continue
        for model, fields in per_model.items():
            if not isinstance(fields, Mapping):
                continue
            bucket = merged.setdefault(str(model), {})
            for key, value in fields.items():
                if isinstance(value, int) and not isinstance(value, bool):
                    bucket[key] = bucket.get(key, 0) + value
    if not observed:
        return None
    return {"iterations": iterations, "duration_ms": duration_ms, "observed_lm_usage": merged}


def build_receipt(
    *,
    mode: Literal["dry", "live"],
    dataset: str,
    split: str,
    limit: int,
    rows: Sequence[Mapping[str, object]],
    scores: Sequence[Mapping[str, object]],
    context_mode: str,
    model_name: str,
    dataset_revision: str | None,
    source: str,
    status: Literal["ok", "failed"] = "ok",
    error_category: str | None = None,
    usage: Mapping[str, object] | None = None,
    selection: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Build the bounded Oolong predict receipt envelope.

    ``usage`` carries observed token telemetry when the runtime produced it. Absence is
    recorded as ``usage_status="unavailable"`` rather than zeroed tokens: DSPy's usage
    tracker is thread-local, so a missing reading is not a measurement of no usage.
    """
    receipt: dict[str, object] = {
        "schema": "fleet.oolong-predict/v1",
        "generated_at": __import__("datetime").datetime.now(__import__("datetime").UTC).isoformat(),
        "candidate": git_identity(),
        "mode": mode,
        "dataset": dataset,
        "dataset_id": DATASET_IDS.get(dataset, dataset),
        "dataset_revision": dataset_revision,
        "split": split,
        "limit": limit,
        "source": source,
        "context_mode": context_mode,
        "model_name": model_name,
        "status": status,
        "summary": summarize_scores(scores),
        "rows": rows,
        "scores": [receipt_safe_score(item) for item in scores],
        "scoring_source": {
            "repo": "https://github.com/abertsch72/oolong",
            "helpers": "src/eval/eval_helpers.py",
            "revision": OOLONG_EVAL_HELPERS_REVISION,
        },
    }
    if selection:
        receipt["selection"] = dict(selection)
    receipt["usage_status"] = "observed" if usage else "unavailable"
    if usage:
        receipt["usage"] = dict(usage)
        # Reuse the shared spend rules: reported cost wins over components, and an
        # incomplete observation fails closed (recorded as unknown, never as zero).
        cost, complete = observed_spend(usage)
        if complete:
            receipt["observed_cost_usd"] = cost
        else:
            receipt["cost_status"] = "unknown"
    if error_category:
        receipt["error_category"] = error_category
    return receipt


__all__ = [
    "DEFAULT_FIXTURE",
    "DEFAULT_HF_DATASET_CONFIGS",
    "DEFAULT_HF_DATASET_REVISIONS",
    "DRY_REQUEST_CONCAT_CAP",
    "LivePrediction",
    "LoadedDatapoint",
    "OolongAdapterError",
    "OolongDNDRLMSignature",
    "build_native_program",
    "build_predict_kwargs",
    "build_receipt",
    "git_identity",
    "invoke_live_prediction",
    "kwargs_context_mode",
    "load_fixture",
    "load_hf_row",
    "normalize_dnd_answer",
    "oolong_signature",
    "receipt_safe_score",
    "release_ephemeral_lease",
    "resolve_datapoints",
    "score_prediction",
    "select_hf_offsets",
    "stage_attachment_context_on_lease",
    "stage_context_capsule",
    "sum_lm_usage",
    "summarize_scores",
]
