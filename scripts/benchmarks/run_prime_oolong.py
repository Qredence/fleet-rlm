"""Run the pinned PrimeIntellect Oolong task set against a live Fleet API."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import AsyncIterator, Iterator, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import dspy
import httpx
from dotenv import load_dotenv

from fleet_rlm.config import FleetConfigurationError, require_live_execution
from fleet_rlm.rlm.dspy_interpreter_contract import PUBLIC_FINAL_OUTPUT_LABEL
from fleet_rlm.rlm.signature import FleetRLMSignature

RECEIPT_SCHEMA = "fleet.prime-oolong/v1"
DEFAULT_API_URL = "http://127.0.0.1:8000"
DEFAULT_PROFILE = "daytona-bench"
MAX_EXAMPLES = 12

PRIME_ENVIRONMENT = "primeintellect/oolong-rlm"
PRIME_VERSION = "0.1.11"
PRIME_REFERENCE = f"{PRIME_ENVIRONMENT}@{PRIME_VERSION}"
PRIME_HUB_HASH = "97d47526"
PRIME_VERSION_ID = "zixnre6tq4e4drk82nm2ebph"
PRIME_SOURCE_SHA256 = {
    "oolong_rlm.py": "eb915d4201e8dd2bdcbe8480e1761e1f0eb8978d1b97c35ab582f9d81f705c20",
    "pyproject.toml": "acf0483d63c1b23adfde6a8036f355f56799cac788b840f9a25e9ce4c4c2e06f",
    "README.md": "e4479f35a33660b87261cd9712e678e471f41519f00cf855f8d464f64026329c",
}
PRIME_SIMPLE_INDEX = "https://hub.primeintellect.ai/primeintellect/simple/"
PRIME_RLM_RUBRIC_COMMIT = "c874da629ed56887d7bfacc095c3a7adb58f7000"
PRIME_RLM_RUBRIC_SHA256 = "e4687e0644d42dbe7f725c6506ff7f32befc044d0d56dc0946931b0b6c63dac0"
DATASET_ARGS: dict[str, Any] = {
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

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SIDECAR_PATH = Path(__file__).with_name("prime_oolong_sidecar.py")
_SIDECAR_CACHE_ROOT = _REPO_ROOT / ".scratch" / "prime-oolong-cache"
_MAX_SIDECAR_LINE_BYTES = 16 * 1024 * 1024
_MAX_SIDECAR_OUTPUT_BYTES = 192 * 1024 * 1024
_COMMAND_TIMEOUT_SECONDS = 300.0
_SIDECAR_TIMEOUT_SECONDS = 1_800.0
_USAGE_FIELDS = frozenset({"iterations", "duration_ms", "llm_calls", "root_lm_calls", "sub_lm_calls"})
_TRAJECTORY_ITEM_LIMIT = 64
_TRAJECTORY_CHAR_LIMIT = 64 * 1024
_SIDECAR_ENV_ALLOWLIST = frozenset(
    {
        "PATH",
        "LANG",
        "LC_ALL",
        "TZ",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "HF_TOKEN",
        "HUGGINGFACE_HUB_TOKEN",
    }
)


class PrimeTrajectoryDiagnostic(dspy.Signature):
    """Apply Fleet's pinned mapping of the Prime RLM trajectory rubric."""

    codes: list[str] = dspy.InputField(desc="Bounded public Python actions with context bodies absent")
    outputs: list[str] = dspy.InputField(desc="Bounded public execution observations with context bodies absent")
    programmatic_tool_calling: bool = dspy.OutputField()
    bounded_output: bool = dspy.OutputField()
    stateful_variables: bool = dspy.OutputField()
    self_formatted_status: bool = dspy.OutputField()
    subagent_delegation: bool = dspy.OutputField()


class PrimeOolongError(RuntimeError):
    """A pinned-environment, sidecar, live API, or receipt contract failed."""


class TurnStreamError(PrimeOolongError):
    """A live Fleet turn reached an unsuccessful terminal event."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default=DEFAULT_API_URL)
    parser.add_argument("--profile", "--expected-profile", dest="expected_profile", default=DEFAULT_PROFILE)
    parser.add_argument("--limit", type=int, default=MAX_EXAMPLES)
    parser.add_argument("--skill-id")
    parser.add_argument("--skill-version")
    parser.add_argument("--output", type=Path, required=True)
    return parser


def _validate_args(args: argparse.Namespace) -> None:
    if isinstance(args.limit, bool) or not 1 <= args.limit <= MAX_EXAMPLES:
        raise PrimeOolongError(f"--limit must be between 1 and {MAX_EXAMPLES}")
    if bool(args.skill_id) != bool(args.skill_version):
        raise PrimeOolongError("--skill-id and --skill-version must be supplied together")


def _require_live_policy() -> None:
    try:
        require_live_execution()
    except FleetConfigurationError as exc:
        raise PrimeOolongError("live execution is disabled or unavailable in Fleet policy") from exc


def _run_command(
    command: Sequence[str],
    *,
    input_text: str | None = None,
    timeout: float = _COMMAND_TIMEOUT_SECONDS,
    environment: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(command),
            check=False,
            capture_output=True,
            input=input_text,
            text=True,
            timeout=timeout,
            env=dict(environment) if environment is not None else None,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PrimeOolongError(f"required command failed to run: {command[0]}") from exc


def _validate_hub_inspection(payload: object, version_listing: str) -> dict[str, Any]:
    if not isinstance(payload, Mapping) or payload.get("kind") != "directory":
        raise PrimeOolongError("Prime environment inspection is malformed")
    if payload.get("version_id") != PRIME_VERSION_ID:
        raise PrimeOolongError("Prime environment version id drifted")
    entries = payload.get("entries")
    if not isinstance(entries, list):
        raise PrimeOolongError("Prime environment inspection is missing source entries")
    actual: dict[str, str] = {}
    for entry in entries:
        if not isinstance(entry, Mapping) or entry.get("is_directory") is not False:
            raise PrimeOolongError("Prime environment source entry is malformed")
        path = entry.get("path")
        digest = entry.get("content_hash")
        if not isinstance(path, str) or not isinstance(digest, str):
            raise PrimeOolongError("Prime environment source identity is malformed")
        actual[path] = digest
    if actual != PRIME_SOURCE_SHA256:
        raise PrimeOolongError("Prime environment source hashes drifted")
    version_line = next((line for line in version_listing.splitlines() if line.strip().startswith(PRIME_VERSION)), "")
    if not version_line or PRIME_HUB_HASH not in version_line:
        raise PrimeOolongError("Prime environment Hub hash drifted")
    return {
        "owner": "primeintellect",
        "name": "oolong-rlm",
        "version": PRIME_VERSION,
        "version_id": PRIME_VERSION_ID,
        "hub_hash": PRIME_HUB_HASH,
        "source_sha256": dict(PRIME_SOURCE_SHA256),
    }


def _inspect_prime_environment() -> dict[str, Any]:
    inspection = _run_command(["prime", "env", "inspect", PRIME_REFERENCE, "--output", "json", "--plain"])
    if inspection.returncode != 0:
        raise PrimeOolongError("Prime environment inspection failed; verify Prime CLI authentication")
    try:
        payload = json.loads(inspection.stdout)
    except json.JSONDecodeError as exc:
        raise PrimeOolongError("Prime environment inspection returned invalid JSON") from exc
    versions = _run_command(["prime", "env", "version", "list", PRIME_ENVIRONMENT, "--full-hashes", "--plain"])
    if versions.returncode != 0:
        raise PrimeOolongError("Prime environment version listing failed")
    return _validate_hub_inspection(payload, versions.stdout)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_pulled_environment(root: Path) -> None:
    for name, expected in PRIME_SOURCE_SHA256.items():
        path = root / name
        if not path.is_file() or _sha256_file(path) != expected:
            raise PrimeOolongError("pulled Prime environment source hashes drifted")
    metadata_path = root / ".prime" / ".env-metadata.json"
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PrimeOolongError("pulled Prime environment metadata is missing or malformed") from exc
    expected = {
        "owner": "primeintellect",
        "name": "oolong-rlm",
        "version": PRIME_VERSION,
    }
    if any(metadata.get(field) != value for field, value in expected.items()):
        raise PrimeOolongError("pulled Prime environment metadata drifted")


@contextlib.contextmanager
def _prepared_prime_environment() -> Iterator[tuple[Path, dict[str, Any]]]:
    identity = _inspect_prime_environment()
    with tempfile.TemporaryDirectory(prefix="fleet-prime-oolong-") as temporary:
        root = Path(temporary) / "environment"
        pulled = _run_command(["prime", "env", "pull", PRIME_REFERENCE, "--target", str(root), "--plain"])
        if pulled.returncode != 0:
            raise PrimeOolongError("Prime environment pull failed")
        _validate_pulled_environment(root)
        yield root, identity


def _sidecar_command(environment_root: Path) -> list[str]:
    del environment_root
    return [
        "uv",
        "run",
        "--isolated",
        "--no-project",
        "--with",
        f"oolong_rlm=={PRIME_VERSION}",
        "--index",
        PRIME_SIMPLE_INDEX,
        "python",
        str(_SIDECAR_PATH),
    ]


def _sidecar_environment() -> dict[str, str]:
    """Place transient package and dataset caches on the SSD7 workspace disk."""
    cache_root = _SIDECAR_CACHE_ROOT
    huggingface_root = cache_root / "huggingface"
    for path in (
        cache_root / "home",
        cache_root / "tmp",
        cache_root / "uv",
        cache_root / "xdg",
        huggingface_root / "datasets",
    ):
        path.mkdir(parents=True, exist_ok=True)
    environment = {name: os.environ[name] for name in _SIDECAR_ENV_ALLOWLIST if name in os.environ}
    environment.update(
        {
            "HOME": str(cache_root / "home"),
            "TMPDIR": str(cache_root / "tmp"),
            "UV_CACHE_DIR": str(cache_root / "uv"),
            "XDG_CACHE_HOME": str(cache_root / "xdg"),
            "HF_HOME": str(huggingface_root),
            "HF_DATASETS_CACHE": str(huggingface_root / "datasets"),
        }
    )
    return environment


def _invoke_sidecar(environment_root: Path, requests: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    input_text = "".join(
        json.dumps(request, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n" for request in requests
    )
    if any(len(line.encode("utf-8")) > _MAX_SIDECAR_LINE_BYTES for line in input_text.splitlines()):
        raise PrimeOolongError("Prime Oolong sidecar request exceeds the JSONL line limit")
    completed = _run_command(
        _sidecar_command(environment_root),
        input_text=input_text,
        timeout=_SIDECAR_TIMEOUT_SECONDS,
        environment=_sidecar_environment(),
    )
    if completed.returncode != 0:
        raise PrimeOolongError("Prime Oolong sidecar failed")
    if len(completed.stdout.encode("utf-8")) > _MAX_SIDECAR_OUTPUT_BYTES:
        raise PrimeOolongError("Prime Oolong sidecar output exceeds the aggregate limit")
    responses: list[dict[str, Any]] = []
    for line in completed.stdout.splitlines():
        if len(line.encode("utf-8")) > _MAX_SIDECAR_LINE_BYTES:
            raise PrimeOolongError("Prime Oolong sidecar response exceeds the JSONL line limit")
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise PrimeOolongError("Prime Oolong sidecar returned malformed JSONL") from exc
        if not isinstance(value, dict):
            raise PrimeOolongError("Prime Oolong sidecar response must be an object")
        responses.append(value)
    return responses


def _export_examples(environment_root: Path, *, limit: int) -> list[dict[str, Any]]:
    responses = _invoke_sidecar(environment_root, [{"op": "export", "limit": limit}])
    if not responses or responses[-1] != {"type": "export_complete", "count": limit}:
        raise PrimeOolongError("Prime Oolong export did not terminate with the expected count")
    examples = responses[:-1]
    required = {
        "type",
        "example_id",
        "question",
        "context",
        "answer",
        "answer_type",
        "context_len",
        "dataset",
        "question_sha256",
        "context_sha256",
    }
    malformed = any(set(example) != required or example.get("type") != "example" for example in examples)
    if len(examples) != limit or malformed:
        raise PrimeOolongError("Prime Oolong export rows are malformed")
    identities = [str(example["example_id"]) for example in examples]
    if len(set(identities)) != limit:
        raise PrimeOolongError("Prime Oolong export contains duplicate example ids")
    for example in examples:
        if (
            not isinstance(example["question"], str)
            or not isinstance(example["context"], str)
            or not isinstance(example["answer"], str)
            or example["context_len"] != DATASET_ARGS["context_len"]
            or example["dataset"] != DATASET_ARGS["dataset_name"]
        ):
            raise PrimeOolongError("Prime Oolong export row semantics drifted")
        for source_field, digest_field in (("question", "question_sha256"), ("context", "context_sha256")):
            digest = hashlib.sha256(example[source_field].encode("utf-8")).hexdigest()
            if not isinstance(example[digest_field], str) or not re.fullmatch(r"[0-9a-f]{64}", example[digest_field]):
                raise PrimeOolongError("Prime Oolong export digest is malformed")
            if example[digest_field] != digest:
                raise PrimeOolongError("Prime Oolong export digest does not match its content")
    return examples


def _score_outputs(
    environment_root: Path,
    rows: Sequence[Mapping[str, Any]],
    outputs: Sequence[str],
) -> dict[str, float]:
    if len(rows) != len(outputs):
        raise PrimeOolongError("Prime Oolong score inputs have inconsistent lengths")
    requests = [
        {
            "op": "score",
            "request_id": str(row["example_id"]),
            "answer": row["answer"],
            "answer_type": row["answer_type"],
            "output": output,
        }
        for row, output in zip(rows, outputs, strict=True)
    ]
    responses = _invoke_sidecar(environment_root, requests)
    scores: dict[str, float] = {}
    for response in responses:
        if set(response) != {"type", "request_id", "score"} or response.get("type") != "score":
            raise PrimeOolongError("Prime Oolong score response is malformed")
        request_id = response.get("request_id")
        score = response.get("score")
        if (
            not isinstance(request_id, str)
            or request_id in scores
            or not isinstance(score, (int, float))
            or isinstance(score, bool)
            or not math.isfinite(float(score))
            or not 0.0 <= float(score) <= 1.0
        ):
            raise PrimeOolongError("Prime Oolong score response is invalid")
        scores[request_id] = float(score)
    expected = {str(row["example_id"]) for row in rows}
    if set(scores) != expected:
        raise PrimeOolongError("Prime Oolong score responses are incomplete")
    return scores


async def _sse_chunks(response: httpx.Response) -> AsyncIterator[dict[str, Any]]:
    async for line in response.aiter_lines():
        if not line.startswith("data: "):
            continue
        payload = line[6:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            value = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            yield value


def _structured_answer(chunk: Mapping[str, Any]) -> str | None:
    data = chunk.get("data")
    if not isinstance(data, Mapping):
        return None
    value = data.get("value")
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping) and isinstance(value.get("answer"), str):
        return value["answer"]
    return None


def _sanitize_usage(value: Mapping[str, Any]) -> dict[str, int | float]:
    sanitized: dict[str, int | float] = {}
    for key in _USAGE_FIELDS:
        item = value.get(key)
        if isinstance(item, (int, float)) and not isinstance(item, bool) and math.isfinite(float(item)) and item >= 0:
            sanitized[key] = item
    return sanitized


def _active_policy_metadata(payload: Mapping[str, Any]) -> tuple[str, str, str, int]:
    profile = payload.get("active_profile")
    scopes = payload.get("scopes")
    if not isinstance(profile, str) or not profile or not isinstance(scopes, list):
        raise PrimeOolongError("Fleet settings response does not identify the active profile")
    active_scope = next(
        (scope for scope in scopes if isinstance(scope, Mapping) and scope.get("name") == profile),
        None,
    )
    if not isinstance(active_scope, Mapping) or not isinstance(active_scope.get("fields"), list):
        raise PrimeOolongError("Fleet settings response does not include the active profile policy")
    values = {
        field["path"]: field.get("value")
        for field in active_scope["fields"]
        if isinstance(field, Mapping) and isinstance(field.get("path"), str)
    }
    root_model = values.get("llm.root.model")
    sub_model = values.get("llm.sub.model")
    max_iters = values.get("rlm.max_iters")
    if not isinstance(root_model, str) or not root_model:
        raise PrimeOolongError("Fleet active profile does not identify the root model")
    if not isinstance(sub_model, str) or not sub_model:
        raise PrimeOolongError("Fleet active profile does not identify the Sub model")
    if not isinstance(max_iters, int) or isinstance(max_iters, bool) or max_iters < 1:
        raise PrimeOolongError("Fleet active profile does not identify the RLM iteration ceiling")
    return profile, root_model, sub_model, max_iters


async def _server_policy_metadata(
    client: httpx.AsyncClient,
    *,
    expected_profile: str,
) -> tuple[str, str, str, int]:
    try:
        response = await client.get("/api/settings")
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise PrimeOolongError("Fleet active benchmark policy could not be verified") from exc
    if not isinstance(payload, Mapping):
        raise PrimeOolongError("Fleet settings response is invalid")
    profile, root_model, sub_model, max_iters = _active_policy_metadata(payload)
    if profile != expected_profile:
        raise PrimeOolongError(f"expected Fleet profile {expected_profile!r}, but the live server uses {profile!r}")
    return profile, root_model, sub_model, max_iters


async def _upload_context(client: httpx.AsyncClient, context: str, context_sha256: str) -> str:
    response = await client.post(
        "/api/attachments",
        files={"attachment": (f"oolong-{context_sha256[:16]}.txt", context.encode("utf-8"), "text/plain")},
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, Mapping) or not isinstance(payload.get("id"), str):
        raise PrimeOolongError("Fleet attachment response is malformed")
    return payload["id"]


def _append_trajectory_value(values: list[str], value: object) -> None:
    if not isinstance(value, str) or len(values) >= _TRAJECTORY_ITEM_LIMIT:
        return
    remaining = _TRAJECTORY_CHAR_LIMIT - sum(len(item) for item in values)
    if remaining <= 0:
        return
    values.append(value[:remaining])


def _termination_mode(*, typed_submit_observed: bool, final_answer: str | None) -> str:
    if typed_submit_observed:
        return "typed_submit"
    if final_answer is not None:
        return "native_extraction_fallback"
    return "text_fallback"


async def run_row(
    client: httpx.AsyncClient,
    row: Mapping[str, Any],
    *,
    attachment_id: str,
    skills: list[dict[str, str]],
) -> tuple[str, dict[str, int | float], str, bool, dict[str, list[str]]]:
    session = await client.post("/api/sessions", json={"title": f"prime-oolong-{row['example_id']}"})
    session.raise_for_status()
    session_payload = session.json()
    if not isinstance(session_payload, Mapping) or not isinstance(session_payload.get("id"), str):
        raise PrimeOolongError("Fleet session response is malformed")
    session_id = session_payload["id"]
    answer_parts: list[str] = []
    final_answer: str | None = None
    usage: dict[str, int | float] = {}
    accessed = False
    settled = False
    typed_submit_observed = False
    trajectory = {"codes": [], "outputs": []}
    async with client.stream(
        "POST",
        f"/api/sessions/{session_id}/turns",
        json={"text": row["question"], "attachment_ids": [attachment_id], "skill_selections": skills},
        headers={"Idempotency-Key": f"prime-oolong-{uuid4()}"},
    ) as response:
        response.raise_for_status()
        async for chunk in _sse_chunks(response):
            chunk_type = chunk.get("type")
            if chunk_type == "text-delta" and isinstance(chunk.get("delta"), str):
                answer_parts.append(chunk["delta"])
            elif chunk_type == "data-structured-result":
                final_answer = _structured_answer(chunk) or final_answer
            elif chunk_type == "data-usage" and isinstance(chunk.get("data"), Mapping):
                raw_usage = chunk["data"].get("usage")
                if isinstance(raw_usage, Mapping):
                    usage = _sanitize_usage(raw_usage)
            elif chunk_type == "data-attachment" and isinstance(chunk.get("data"), Mapping):
                accessed = accessed or chunk["data"].get("attachment_id") == attachment_id
            elif chunk_type == "data-rlm-code" and isinstance(chunk.get("data"), Mapping):
                code = chunk["data"].get("code")
                _append_trajectory_value(trajectory["codes"], code)
            elif chunk_type == "data-rlm-output" and isinstance(chunk.get("data"), Mapping):
                output = chunk["data"].get("output")
                _append_trajectory_value(trajectory["outputs"], output)
                typed_submit_observed = typed_submit_observed or output == PUBLIC_FINAL_OUTPUT_LABEL
            elif chunk_type in {"error", "abort"}:
                raise TurnStreamError("Fleet turn reported an error")
            elif chunk_type == "finish":
                if chunk.get("finishReason") != "stop":
                    raise TurnStreamError("Fleet turn did not finish successfully")
                settled = True
    if not settled:
        raise TurnStreamError("Fleet turn stream ended without a successful finish")
    termination_mode = _termination_mode(typed_submit_observed=typed_submit_observed, final_answer=final_answer)
    return (
        final_answer if final_answer is not None else "".join(answer_parts),
        usage,
        termination_mode,
        accessed,
        trajectory,
    )


def _aggregate(results: Sequence[Mapping[str, Any]], *, iteration_ceiling: int) -> dict[str, Any]:
    count = len(results)
    errors = sum("error_type" in result for result in results)
    scores = [float(result["score"]) for result in results]
    return {
        "count": count,
        "mean_score": sum(scores) / count if count else 0.0,
        "error_rate": errors / count if count else 0.0,
        "typed_completion_rate": sum(result.get("termination_mode") == "typed_submit" for result in results) / count
        if count
        else 0.0,
        "prepared_rate": sum(result.get("context_prepared") is True for result in results) / count if count else 0.0,
        "accessed_rate": sum(result.get("context_accessed") is True for result in results) / count if count else 0.0,
        "iteration_ceiling_rate": sum(
            result.get("usage", {}).get("iterations") == iteration_ceiling for result in results
        )
        / count
        if count
        else 0.0,
    }


_DIAGNOSTIC_FIELDS = (
    "programmatic_tool_calling",
    "bounded_output",
    "stateful_variables",
    "self_formatted_status",
    "subagent_delegation",
)


def _trajectory_prediction(codes: Sequence[str], outputs: Sequence[str]) -> dspy.Prediction:
    source = "\n".join(codes)
    lowered = source.lower()
    programmatic = bool(
        re.search(r"\b(for|while)\b|\[[^\n]+\bfor\b|\b(len|sum|sorted|filter|map|find|count)\s*\(", source)
    )
    dumps_context = bool(re.search(r"print\s*\(\s*(context|attachments)\s*\)", source))
    bounded = not dumps_context and bool(
        re.search(r"\b(len|find|count|startswith|endswith)\s*\(|\[[^\]]*:[^\]]*\]", source)
    )
    assignment = bool(re.search(r"(?m)^\s*[A-Za-z_]\w*\s*=", source))
    stateful = ("context" in lowered or "attachments" in lowered) and assignment and len(codes) >= 2
    formatted = bool(outputs) and all(len(output) <= 2_048 for output in outputs)
    batch_count = len(re.findall(r"\bllm_query_batched\s*\(", source))
    single_count = len(re.findall(r"(?<![\w])llm_query\s*\(", source))
    recursive_count = len(re.findall(r"\brlm_query\s*\(", source))
    delegated = recursive_count == 0 and (single_count <= 1 or batch_count > 0)
    return dspy.Prediction(
        programmatic_tool_calling=programmatic,
        bounded_output=bounded,
        stateful_variables=stateful,
        self_formatted_status=formatted,
        subagent_delegation=delegated,
        semantic_call_counts={
            "llm_query": single_count,
            "llm_query_batched": batch_count,
            "rlm_query": recursive_count,
        },
    )


class _TrajectoryProgram(dspy.Module):
    signature = PrimeTrajectoryDiagnostic

    def forward(self, *, codes: list[str], outputs: list[str]) -> dspy.Prediction:
        return _trajectory_prediction(codes, outputs)


def _trajectory_metric(_example: dspy.Example, prediction: dspy.Prediction, trace: object = None) -> float:
    del trace
    return sum(bool(getattr(prediction, field)) for field in _DIAGNOSTIC_FIELDS) / len(_DIAGNOSTIC_FIELDS)


def _diagnostic_reason(field: str, passed: bool) -> str:
    labels = {
        "programmatic_tool_calling": "programmatic Python operations observed",
        "bounded_output": "bounded inspection observed without a direct context dump",
        "stateful_variables": "injected context and derived state were reused",
        "self_formatted_status": "execution observations stayed concise",
        "subagent_delegation": "delegation used the allowed native call pattern",
    }
    prefix = "pass" if passed else "not observed"
    return f"{prefix}: {labels[field]}"


def _evaluate_trajectory_diagnostics(
    example_ids: Sequence[str], trajectories: Sequence[Mapping[str, list[str]]]
) -> tuple[dict[str, dict[str, Any]], float]:
    devset = [
        dspy.Example(example_id=example_id, codes=list(value["codes"]), outputs=list(value["outputs"])).with_inputs(
            "codes", "outputs"
        )
        for example_id, value in zip(example_ids, trajectories, strict=True)
    ]
    evaluated = dspy.Evaluate(
        devset=devset,
        metric=_trajectory_metric,
        num_threads=1,
        display_progress=False,
        display_table=False,
    )(_TrajectoryProgram())
    diagnostics: dict[str, dict[str, Any]] = {}
    for example, prediction, score in evaluated.results:
        criteria = {
            field: {
                "passed": bool(getattr(prediction, field)),
                "reason": _diagnostic_reason(field, bool(getattr(prediction, field))),
            }
            for field in _DIAGNOSTIC_FIELDS
        }
        diagnostics[str(example.example_id)] = {
            "score": float(score),
            "criteria": criteria,
            "semantic_call_counts": dict(prediction.semantic_call_counts),
        }
    return diagnostics, float(evaluated.score)


def _dspy_contract() -> dict[str, Any]:
    rlm_type = type(dspy.RLM).__name__
    rlm_module = getattr(dspy.RLM, "__module__", "dspy")
    rlm_name = getattr(dspy.RLM, "__qualname__", getattr(dspy.RLM, "__name__", rlm_type))
    return {
        "rlm_type": f"{rlm_module}.{rlm_name}",
        "signature": {
            "name": FleetRLMSignature.__name__,
            "type": f"{FleetRLMSignature.__module__}.{FleetRLMSignature.__qualname__}",
            "input_fields": list(FleetRLMSignature.input_fields),
            "output_fields": list(FleetRLMSignature.output_fields),
        },
        "tool_names": [
            "llm_query",
            "llm_query_batched",
            "SUBMIT",
            "rlm_query",
            "read_attachment",
            "create_artifact",
            "publish_workspace_artifact",
            "read_session_history",
            "fetch_url",
        ],
        "usage_source": "Prediction.get_lm_usage()",
        "evaluation": {
            "example_type": "dspy.Example",
            "engine": "dspy.Evaluate",
            "signature": PrimeTrajectoryDiagnostic.__name__,
        },
    }


async def evaluate(
    args: argparse.Namespace,
    *,
    environment_root: Path,
    environment_identity: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    _validate_args(args)
    started_at = datetime.now(UTC).isoformat()
    selected = list(rows) if rows is not None else _export_examples(environment_root, limit=args.limit)
    if len(selected) != args.limit:
        raise PrimeOolongError("Prime Oolong selection count drifted")
    skills = [{"id": args.skill_id, "expected_version": args.skill_version}] if args.skill_id else []
    outputs: list[str] = []
    execution: list[dict[str, Any]] = []
    trajectories: list[dict[str, list[str]]] = []
    attachment_cache: dict[str, str] = {}
    timeout = httpx.Timeout(2_000.0)
    async with httpx.AsyncClient(base_url=args.api_url.rstrip("/"), timeout=timeout) as client:
        profile, root_model, sub_model, iteration_ceiling = await _server_policy_metadata(
            client,
            expected_profile=args.expected_profile,
        )
        for row in selected:
            context_sha256 = str(row["context_sha256"])
            try:
                attachment_id = attachment_cache.get(context_sha256)
                if attachment_id is None:
                    attachment_id = await _upload_context(client, str(row["context"]), context_sha256)
                    attachment_cache[context_sha256] = attachment_id
                answer, usage, termination_mode, accessed, trajectory = await run_row(
                    client,
                    row,
                    attachment_id=attachment_id,
                    skills=skills,
                )
                outputs.append(answer)
                trajectories.append(trajectory)
                execution.append(
                    {
                        "usage": usage,
                        "termination_mode": termination_mode,
                        "context_prepared": True,
                        "context_accessed": accessed,
                    }
                )
            except Exception as exc:
                outputs.append("")
                trajectories.append({"codes": [], "outputs": []})
                execution.append(
                    {
                        "usage": {},
                        "termination_mode": "error",
                        "context_prepared": False,
                        "context_accessed": False,
                        "error_type": type(exc).__name__,
                    }
                )

    scores = _score_outputs(environment_root, selected, outputs)
    rescored = _score_outputs(environment_root, selected, outputs)
    if rescored != scores:
        raise PrimeOolongError("Prime Oolong deterministic rescoring drifted")
    example_ids = [str(row["example_id"]) for row in selected]
    diagnostics, diagnostic_score = _evaluate_trajectory_diagnostics(example_ids, trajectories)
    results: list[dict[str, Any]] = []
    for row, evidence in zip(selected, execution, strict=True):
        result = {
            "example_id": str(row["example_id"]),
            "question_sha256": row["question_sha256"],
            "context_sha256": row["context_sha256"],
            "context_len": row["context_len"],
            "dataset": row["dataset"],
            "answer_sha256": hashlib.sha256(outputs[len(results)].encode("utf-8")).hexdigest(),
            "score": scores[str(row["example_id"])],
            "trajectory_diagnostic": diagnostics[str(row["example_id"])],
            **evidence,
        }
        results.append(result)
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "started_at": started_at,
        "finished_at": datetime.now(UTC).isoformat(),
        "environment": dict(environment_identity),
        "dataset": dict(DATASET_ARGS),
        "selection": {
            "algorithm": "prime-environment-first-n/v1",
            "limit": args.limit,
            "example_ids": [str(row["example_id"]) for row in selected],
        },
        "protocol": {
            "dataset_export": "load_environment(...).get_dataset()",
            "scoring": "OolongRubric.oolong_reward",
            "execution": "Fleet stock DSPy RLM through Attachments",
            "sidecar": "uv run --isolated --no-project with pinned Prime package JSONL",
            "deterministic_rescore": True,
        },
        "profile": profile,
        "model_roles": {"root": root_model, "sub": sub_model},
        "iteration_ceiling": iteration_ceiling,
        "dspy": _dspy_contract(),
        "trajectory_rubric": {
            "source_commit": PRIME_RLM_RUBRIC_COMMIT,
            "source_sha256": PRIME_RLM_RUBRIC_SHA256,
            "excluded": ["repl_native_shell"],
            "denominator": list(_DIAGNOSTIC_FIELDS),
            "mean_score": diagnostic_score / 100.0,
        },
        "results": results,
        "aggregate": _aggregate(results, iteration_ceiling=iteration_ceiling),
    }
    validate_receipt(receipt, expected_count=args.limit)
    return receipt


def validate_receipt(receipt: Mapping[str, Any], *, expected_count: int) -> None:
    environment = receipt.get("environment")
    selection = receipt.get("selection")
    results = receipt.get("results")
    aggregate = receipt.get("aggregate")
    if receipt.get("schema") != RECEIPT_SCHEMA or not isinstance(results, list):
        raise PrimeOolongError("Prime Oolong receipt is malformed")
    if not isinstance(environment, Mapping) or not isinstance(selection, Mapping) or not isinstance(aggregate, Mapping):
        raise PrimeOolongError("Prime Oolong receipt is malformed")
    if environment != {
        "owner": "primeintellect",
        "name": "oolong-rlm",
        "version": PRIME_VERSION,
        "version_id": PRIME_VERSION_ID,
        "hub_hash": PRIME_HUB_HASH,
        "source_sha256": PRIME_SOURCE_SHA256,
    }:
        raise PrimeOolongError("Prime Oolong receipt environment identity is invalid")
    if selection.get("algorithm") != "prime-environment-first-n/v1" or selection.get("limit") != expected_count:
        raise PrimeOolongError("Prime Oolong receipt selection identity is invalid")
    if len(results) != expected_count or aggregate.get("count") != expected_count:
        raise PrimeOolongError("Prime Oolong receipt result count is invalid")
    expected_ids = selection.get("example_ids")
    actual_ids = [result.get("example_id") for result in results if isinstance(result, Mapping)]
    if not isinstance(expected_ids, list) or actual_ids != expected_ids or len(set(actual_ids)) != expected_count:
        raise PrimeOolongError("Prime Oolong receipt example identity is invalid")
    forbidden = {"context", "question", "answer", "gold_answer", "full_answer", "trajectory"}
    if any(not isinstance(result, Mapping) or forbidden.intersection(result) for result in results):
        raise PrimeOolongError("Prime Oolong receipt exposes forbidden raw values")
    if any(
        not isinstance(result.get("score"), (int, float))
        or isinstance(result.get("score"), bool)
        or not 0.0 <= float(result["score"]) <= 1.0
        for result in results
    ):
        raise PrimeOolongError("Prime Oolong receipt score is invalid")


def _mechanics_gate_passes(receipt: Mapping[str, Any]) -> bool:
    aggregate = receipt.get("aggregate")
    return bool(
        isinstance(aggregate, Mapping)
        and aggregate.get("error_rate") == 0.0
        and aggregate.get("typed_completion_rate") == 1.0
        and aggregate.get("prepared_rate") == 1.0
        and aggregate.get("accessed_rate") == 1.0
        and receipt.get("protocol", {}).get("deterministic_rescore") is True
    )


async def _run(args: argparse.Namespace) -> int:
    _validate_args(args)
    _require_live_policy()
    with _prepared_prime_environment() as (environment_root, identity):
        receipt = await evaluate(args, environment_root=environment_root, environment_identity=identity)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"count": receipt["aggregate"]["count"], "output": str(args.output)}, sort_keys=True))
    return 0 if _mechanics_gate_passes(receipt) else 1


def main(argv: Sequence[str] | None = None) -> int:
    load_dotenv(_REPO_ROOT / ".env", override=False)
    try:
        return asyncio.run(_run(build_parser().parse_args(argv)))
    except PrimeOolongError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
