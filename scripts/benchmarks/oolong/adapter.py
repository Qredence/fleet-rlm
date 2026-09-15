"""Shared Oolong predict adapter helpers for Fleet RLM."""

from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
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
from fleet_rlm.sessions.context import build_session_context_manifest
from fleet_rlm.sessions.models import SessionHistory
from scripts.benchmarks.oolong.scoring import (
    OOLONG_EVAL_HELPERS_REVISION,
    dnd_process_response,
    synth_process_response,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_FIXTURE = Path(__file__).with_name("fixture_validation_row.json")
DATASET_IDS = {
    "synth": "oolongbench/oolong-synth",
    "real": "oolongbench/oolong-real",
}
SYNTH_SPLITS = frozenset({"validation", "test"})
REAL_SPLITS = frozenset({"test"})
DEFAULT_HF_DATASET_REVISION = "main"
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
    }
)
# Dry-only request concatenation cap; production/live must use AttachmentContextCapsule.
DRY_REQUEST_CONCAT_CAP = 100_000
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
        from datasets import load_dataset
    except ImportError as exc:
        raise OolongAdapterError(
            "the `datasets` package is required for Hugging Face loads; install benchmark extras or pass --fixture"
        ) from exc
    hf_id = DATASET_IDS[dataset]
    resolved_revision = revision or DEFAULT_HF_DATASET_REVISION
    try:
        loaded = load_dataset(hf_id, split=f"{split}[{index}:{index + 1}]", revision=resolved_revision)
        row = loaded[0]
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


def resolve_datapoints(
    *,
    dataset: str,
    split: str,
    start_index: int,
    limit: int,
    fixture: Path | None,
    hf_revision: str | None = None,
) -> tuple[LoadedDatapoint, ...]:
    """Load up to ``limit`` datapoints from a fixture or Hugging Face."""
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
) -> dict[str, Any]:
    """Build locked Fleet RLM kwargs for one Oolong row."""
    question = str(datapoint.get("question", "")).strip()
    if not question:
        raise OolongAdapterError("datapoint is missing question")
    context_text = str(datapoint.get("context_window_text", ""))
    sid = session_id or uuid4()
    history = dspy.History(messages=[])
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
            history=history,
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
            history=history,
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
        return dnd_process_response(dict(datapoint), answer, model_name)
    return synth_process_response(dict(datapoint), answer, model_name)


def build_native_program(settings: Any, *, sub_lm: Any | None = None) -> Any:
    """Construct the locked native RLM program for one predict call."""
    if sub_lm is None:
        from fleet_rlm.rlm.program import build_model_bundle

        sub_lm = build_model_bundle(settings).sub_lm
    return build_native_rlm(
        signature=FleetRLMSignature,
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
) -> str:
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
        turn_models = bundle.bind_turn_deadline(
            deadline=deadline,
            reserve_seconds=wrap_up_seconds,
            budget=budget,
        )
        resolved_root = turn_models.root_lm
        resolved_sub = turn_models.sub_lm
    else:
        turn_models = RLMModelBundle(root_lm=root_lm, sub_lm=sub_lm).bind_turn_deadline(
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
    rlm = build_native_program(settings, sub_lm=resolved_sub)
    invoke_kwargs = {key: value for key, value in kwargs.items() if key != "attachment_context"}
    adapter = FleetJSONAdapter(
        deadline=deadline,
        wrap_up_seconds=wrap_up_seconds,
        budget=budget,
    )
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
    return str(getattr(prediction, "answer", ""))


def summarize_scores(scores: Sequence[Mapping[str, object]]) -> dict[str, object]:
    """Aggregate official score payloads for receipts."""
    values = [float(item["score"]) for item in scores if isinstance(item.get("score"), (int, float))]
    if not values:
        return {"count": 0, "mean": 0.0}
    return {"count": len(values), "mean": sum(values) / len(values)}


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
) -> dict[str, object]:
    """Build the bounded Oolong predict receipt envelope."""
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
    if error_category:
        receipt["error_category"] = error_category
    return receipt


__all__ = [
    "DEFAULT_FIXTURE",
    "DEFAULT_HF_DATASET_REVISION",
    "DRY_REQUEST_CONCAT_CAP",
    "LoadedDatapoint",
    "OolongAdapterError",
    "build_native_program",
    "build_predict_kwargs",
    "build_receipt",
    "git_identity",
    "invoke_live_prediction",
    "kwargs_context_mode",
    "load_fixture",
    "load_hf_row",
    "receipt_safe_score",
    "release_ephemeral_lease",
    "resolve_datapoints",
    "score_prediction",
    "stage_attachment_context_on_lease",
    "stage_context_capsule",
    "summarize_scores",
]
