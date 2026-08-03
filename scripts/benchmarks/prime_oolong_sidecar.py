"""Isolated JSONL bridge to the pinned PrimeIntellect Oolong environment."""

from __future__ import annotations

import contextlib
import hashlib
import importlib
import json
import math
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

MAX_EXAMPLES = 12
MAX_JSONL_BYTES = 16 * 1024 * 1024
PINNED_OOLONG_SOURCE_SHA256 = "eb915d4201e8dd2bdcbe8480e1761e1f0eb8978d1b97c35ab582f9d81f705c20"

ENVIRONMENT_ARGS: dict[str, Any] = {
    "subset": "synth",
    "split": "validation",
    "dataset_name": "trec_coarse",
    "context_len": 131_072,
    "filter_numerical": True,
    "shuffle": False,
    "include_env_tips": False,
    "prompt_in_context_file": False,
    "reward_mode": "oolong",
}


class SidecarProtocolError(ValueError):
    """A sidecar request or pinned environment response was malformed."""


def _verify_installed_environment() -> None:
    import oolong_rlm

    module_path = Path(str(getattr(oolong_rlm, "__file__", "")))
    try:
        digest = hashlib.sha256(module_path.read_bytes()).hexdigest()
    except OSError as exc:
        raise SidecarProtocolError("installed environment source is unavailable") from exc
    if digest != PINNED_OOLONG_SOURCE_SHA256:
        raise SidecarProtocolError("installed environment source hash drifted")


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _required_string(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise SidecarProtocolError(f"{field} must be a string")
    return value


def _question_from_prompt(prompt: object) -> str:
    if not isinstance(prompt, Sequence) or isinstance(prompt, (str, bytes)) or not prompt:
        raise SidecarProtocolError("environment prompt must be a non-empty message sequence")
    message = prompt[-1]
    if not isinstance(message, Mapping):
        raise SidecarProtocolError("environment prompt message must be an object")
    return _required_string(message.get("content"), field="prompt content")


def export_examples(limit: int) -> list[dict[str, Any]]:
    """Load the exact fixed task set through the Prime environment contract."""
    if isinstance(limit, bool) or not 1 <= limit <= MAX_EXAMPLES:
        raise SidecarProtocolError(f"limit must be between 1 and {MAX_EXAMPLES}")

    load_environment = vars(importlib.import_module("oolong_rlm"))["load_environment"]

    # Third-party dataset builders may log progress to stdout. Keep stdout JSONL-only.
    with contextlib.redirect_stdout(sys.stderr):
        dataset = load_environment(**ENVIRONMENT_ARGS).get_dataset(n=limit)

    records: list[dict[str, Any]] = []
    for source in dataset:
        if not isinstance(source, Mapping):
            raise SidecarProtocolError("environment dataset row must be an object")
        info = source.get("info")
        if not isinstance(info, Mapping):
            raise SidecarProtocolError("environment dataset row is missing info")
        context = _required_string(info.get("context"), field="info.context")
        question = _question_from_prompt(source.get("prompt"))
        answer = _required_string(source.get("answer"), field="answer")
        example_id = source.get("example_id")
        if not isinstance(example_id, (str, int)) or isinstance(example_id, bool):
            raise SidecarProtocolError("example_id must be a string or integer")
        answer_type = _required_string(info.get("answer_type", ""), field="info.answer_type")
        context_len = info.get("context_len")
        if context_len != ENVIRONMENT_ARGS["context_len"]:
            raise SidecarProtocolError("environment returned an unexpected context length")
        dataset_name = info.get("dataset")
        if dataset_name != ENVIRONMENT_ARGS["dataset_name"]:
            raise SidecarProtocolError("environment returned an unexpected dataset name")
        records.append(
            {
                "type": "example",
                "example_id": str(example_id),
                "question": question,
                "context": context,
                "answer": answer,
                "answer_type": answer_type,
                "context_len": context_len,
                "dataset": dataset_name,
                "question_sha256": _sha256(question),
                "context_sha256": _sha256(context),
            }
        )
    if len(records) != limit:
        raise SidecarProtocolError("environment returned fewer examples than requested")
    return records


def score_response(request: Mapping[str, Any]) -> dict[str, Any]:
    """Score one Fleet answer directly with Prime's deterministic OolongRubric."""
    rubric_type = vars(importlib.import_module("oolong_rlm"))["OolongRubric"]

    request_id = _required_string(request.get("request_id"), field="request_id")
    answer = _required_string(request.get("answer"), field="answer")
    answer_type = _required_string(request.get("answer_type", ""), field="answer_type")
    output = _required_string(request.get("output"), field="output")
    state = {
        "final_answer": output,
        "answer": answer,
        "info": {"answer_type": answer_type},
    }
    score = float(rubric_type(subset="synth").oolong_reward(state))
    if not math.isfinite(score) or not 0.0 <= score <= 1.0:
        raise SidecarProtocolError("OolongRubric returned an invalid score")
    return {"type": "score", "request_id": request_id, "score": score}


def handle_request(request: Mapping[str, Any]) -> list[dict[str, Any]]:
    operation = request.get("op")
    if operation == "export":
        limit = request.get("limit")
        if not isinstance(limit, int) or isinstance(limit, bool):
            raise SidecarProtocolError("limit must be an integer")
        records = export_examples(limit)
        return [*records, {"type": "export_complete", "count": len(records)}]
    if operation == "score":
        return [score_response(request)]
    raise SidecarProtocolError("unsupported sidecar operation")


def _write_jsonl(value: Mapping[str, Any]) -> None:
    encoded = (json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8")
    if len(encoded) > MAX_JSONL_BYTES:
        raise SidecarProtocolError("sidecar response exceeds the JSONL line limit")
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()


def main() -> int:
    try:
        _verify_installed_environment()
        for raw in sys.stdin.buffer:
            if len(raw) > MAX_JSONL_BYTES:
                raise SidecarProtocolError("sidecar request exceeds the JSONL line limit")
            try:
                request = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise SidecarProtocolError("sidecar request is not valid JSON") from exc
            if not isinstance(request, Mapping):
                raise SidecarProtocolError("sidecar request must be an object")
            for response in handle_request(request):
                _write_jsonl(response)
        return 0
    except (SidecarProtocolError, KeyError, TypeError, ValueError) as exc:
        print(f"Prime Oolong sidecar failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
