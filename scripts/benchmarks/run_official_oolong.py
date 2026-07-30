"""Run the official Oolong scorers against a live Fleet API."""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import subprocess
import sys
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

RECEIPT_SCHEMA = "fleet.official-oolong/v1"
DEFAULT_API_URL = "http://127.0.0.1:8000"
_DATASET_ROWS_URL = "https://datasets-server.huggingface.co/rows"
_LIVE_VALUES = frozenset({"1", "true", "yes"})
_REPO_ROOT = Path(__file__).resolve().parents[2]

DND_PROMPT = """The attached file contains a long transcript. Read it with the interpreter;
do not attempt to hold it all in reasoning. ``read_attachment(attachment_id)``
returns a mapping: assign its text with ``content = read_attachment(attachment_id)["content"]``.

Question: {question}

Answer inline. Do not write a report or create an artifact. Give the final answer wrapped
in \\boxed{{}}; use \\boxed{{\\text{{Alice}}}} for a name. Submit the semantic answer
with `SUBMIT(answer=final_answer)` as soon as you have it. Use concise interpreter code
without print statements and do not call `llm_query`."""
SYNTH_PROMPT = """The attached file contains a long list of records. Read it with the
interpreter and aggregate over all records. ``read_attachment(attachment_id)``
returns a mapping: assign its text with ``content = read_attachment(attachment_id)["content"]``.

Question: {question}

Answer inline. Do not write a report or create an artifact. Your final action must be
`SUBMIT(answer=final_answer)` as soon as you have it. Use concise interpreter code without
print statements and do not call `llm_query`."""


class OolongPreflightError(ValueError):
    """The local official scorer checkout cannot be used safely."""


class TurnStreamError(RuntimeError):
    """A live Fleet turn reached an unsuccessful terminal SSE event."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=("real", "synth"), default="real")
    parser.add_argument("--api-url", default=DEFAULT_API_URL)
    parser.add_argument("--oolong-root", type=Path, default=_REPO_ROOT.parent / "oolong")
    parser.add_argument("--min-len", type=int, default=0)
    parser.add_argument("--max-len", type=int, default=132_000)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--source-page-size", type=int, default=3)
    parser.add_argument("--skill-id")
    parser.add_argument("--skill-version")
    parser.add_argument("--expected-profile")
    parser.add_argument("--output", type=Path, required=True)
    return parser


def _require_live_environment() -> None:
    if os.environ.get("FLEET_LIVE", "").strip().lower() not in _LIVE_VALUES:
        raise OolongPreflightError("FLEET_LIVE=1 is required")


def _official_helpers(oolong_root: Path) -> tuple[Callable[..., dict[str, Any]], Callable[..., dict[str, Any]], str]:
    helper_path = oolong_root / "src" / "eval" / "eval_helpers.py"
    if not helper_path.is_file():
        raise OolongPreflightError(f"official scorer not found at {helper_path}")
    spec = importlib.util.spec_from_file_location("fleet_official_oolong_helpers", helper_path)
    if spec is None or spec.loader is None:
        raise OolongPreflightError("official scorer could not be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise OolongPreflightError(f"official scorer dependencies are unavailable: {type(exc).__name__}") from exc
    real = getattr(module, "dnd_process_response", None)
    synth = getattr(module, "synth_process_response", None)
    if not callable(real) or not callable(synth):
        raise OolongPreflightError("official scorer exports are incomplete")
    return real, synth, _git_revision(oolong_root)


def _git_revision(root: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"], check=False, capture_output=True, text=True
    )
    return completed.stdout.strip() if completed.returncode == 0 else "unknown"


def _validate_args(args: argparse.Namespace) -> None:
    if args.min_len < 0 or args.max_len < args.min_len or args.limit < 1 or args.source_page_size < 1:
        raise OolongPreflightError("length bounds and limit are invalid")
    if bool(args.skill_id) != bool(args.skill_version):
        raise OolongPreflightError("--skill-id and --skill-version must be supplied together")


def _dataset_identity(split: str) -> tuple[str, str]:
    return ("oolongbench/oolong-real", "dnd") if split == "real" else ("oolongbench/oolong-synth", "default")


def _load_rows(
    split: str,
    *,
    min_len: int,
    max_len: int,
    limit: int,
    source_page_size: int,
) -> list[dict[str, Any]]:
    """Read bounded pages from Oolong's official test split and filter exactly."""
    import tiktoken

    dataset, config = _dataset_identity(split)
    encoder = tiktoken.get_encoding("o200k_base")
    selected: list[dict[str, Any]] = []
    context_lengths: dict[str, int] = {}
    offset = 0
    total_rows: int | None = None
    while total_rows is None or offset < total_rows:
        response = httpx.get(
            _DATASET_ROWS_URL,
            params={
                "dataset": dataset,
                "config": config,
                "split": "test",
                "offset": offset,
                "length": source_page_size,
            },
            timeout=60.0,
        )
        response.raise_for_status()
        payload = response.json()
        page = payload.get("rows")
        if not isinstance(page, list) or not page:
            break
        total_rows = int(payload.get("num_rows_total", offset + len(page)))
        print(f"[oolong] selecting {split} rows {offset + 1}-{offset + len(page)} of {total_rows}", file=sys.stderr)
        for item in page:
            if not isinstance(item, Mapping) or not isinstance(item.get("row"), Mapping):
                continue
            row = dict(item["row"])
            context_window_id = str(row["context_window_id"])
            context_len = context_lengths.get(context_window_id)
            if context_len is None:
                context_len = len(encoder.encode(str(row["context_window_text"])))
                context_lengths[context_window_id] = context_len
            if min_len <= context_len <= max_len:
                selected.append({**row, "context_len": context_len})
            if len(selected) >= limit:
                return select_rows(selected, min_len=min_len, max_len=max_len, limit=limit)
        offset += len(page)
    return select_rows(selected, min_len=min_len, max_len=max_len, limit=limit)


def select_rows(rows: Sequence[Mapping[str, Any]], *, min_len: int, max_len: int, limit: int) -> list[dict[str, Any]]:
    selected = [dict(row) for row in rows if min_len <= int(row["context_len"]) <= max_len]
    selected.sort(key=lambda row: (str(row["context_window_id"]), str(row["id"])))
    return selected[:limit]


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


def _answer_for_official_scorer(answer: str, *, split: str) -> str:
    """Translate Fleet's scalar structured result to Oolong's text protocol."""
    stripped = answer.strip()
    if split == "real":
        return stripped if "\\boxed{" in stripped else f"\\boxed{{{stripped}}}"
    return stripped if stripped.startswith("Answer:") else f"Answer: {stripped}"


def _active_policy_metadata(payload: Mapping[str, Any]) -> tuple[str, str, int]:
    profile = payload.get("active_profile")
    scopes = payload.get("scopes")
    if not isinstance(profile, str) or not profile or not isinstance(scopes, list):
        raise OolongPreflightError("Fleet settings response does not identify the active profile")
    active_scope = next(
        (scope for scope in scopes if isinstance(scope, Mapping) and scope.get("name") == profile),
        None,
    )
    if not isinstance(active_scope, Mapping) or not isinstance(active_scope.get("fields"), list):
        raise OolongPreflightError("Fleet settings response does not include the active profile policy")
    values = {
        field["path"]: field.get("value")
        for field in active_scope["fields"]
        if isinstance(field, Mapping) and isinstance(field.get("path"), str)
    }
    model = values.get("llm.root.model")
    max_iterations = values.get("rlm.max_iterations")
    if not isinstance(model, str) or not model:
        raise OolongPreflightError("Fleet active profile does not identify the root model")
    if not isinstance(max_iterations, int) or isinstance(max_iterations, bool) or max_iterations < 1:
        raise OolongPreflightError("Fleet active profile does not identify the RLM iteration ceiling")
    return profile, model, max_iterations


async def _server_policy_metadata(
    client: httpx.AsyncClient,
    *,
    expected_profile: str | None,
) -> tuple[str, str, int]:
    try:
        response = await client.get("/api/settings")
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise OolongPreflightError("Fleet active benchmark policy could not be verified") from exc
    if not isinstance(payload, Mapping):
        raise OolongPreflightError("Fleet settings response is invalid")
    profile, model, max_iterations = _active_policy_metadata(payload)
    if expected_profile is not None and profile != expected_profile:
        raise OolongPreflightError(f"expected Fleet profile {expected_profile!r}, but the live server uses {profile!r}")
    return profile, model, max_iterations


async def _upload_context(client: httpx.AsyncClient, context: str, context_window_id: str) -> str:
    response = await client.post(
        "/api/attachments",
        files={"attachment": (f"oolong-{context_window_id}.txt", context.encode("utf-8"), "text/plain")},
    )
    response.raise_for_status()
    return str(response.json()["id"])


async def run_row(
    client: httpx.AsyncClient,
    row: Mapping[str, Any],
    *,
    split: str,
    attachment_id: str,
    skills: list[dict[str, str]],
) -> tuple[str, dict[str, Any]]:
    session = await client.post("/api/sessions", json={"title": f"oolong-{row['id']}"})
    session.raise_for_status()
    session_id = session.json()["id"]
    prompt = (DND_PROMPT if split == "real" else SYNTH_PROMPT).format(question=row["question"])
    answer_parts: list[str] = []
    final_answer: str | None = None
    usage: dict[str, Any] = {}
    settled = False
    async with client.stream(
        "POST",
        f"/api/sessions/{session_id}/turns",
        json={"text": prompt, "attachment_ids": [attachment_id], "skill_selections": skills},
        headers={"Idempotency-Key": f"oolong-{uuid4()}"},
    ) as response:
        response.raise_for_status()
        async for chunk in _sse_chunks(response):
            chunk_type = chunk.get("type")
            if chunk_type == "text-delta" and isinstance(chunk.get("delta"), str):
                answer_parts.append(chunk["delta"])
            elif chunk_type == "data-structured-result":
                final_answer = _structured_answer(chunk) or final_answer
            elif chunk_type == "data-usage" and isinstance(chunk.get("data"), Mapping):
                value = chunk["data"].get("usage")
                if isinstance(value, Mapping):
                    usage = dict(value)
            elif chunk_type in {"error", "abort"}:
                raise TurnStreamError(str(chunk.get("errorText") or chunk.get("reason") or "turn failed"))
            elif chunk_type == "finish":
                if chunk.get("finishReason") != "stop":
                    raise TurnStreamError("turn did not finish successfully")
                settled = True
    if not settled:
        raise TurnStreamError("turn stream ended without a successful finish")
    return final_answer if final_answer is not None else "".join(answer_parts), usage


def _aggregate(results: Sequence[Mapping[str, Any]], *, iteration_ceiling: int) -> dict[str, Any]:
    total = len(results)
    scores = [float(row.get("score", 0.0)) for row in results]
    parse_failures = sum(str(row.get("parse_confidence", "")).lower() == "low" for row in results)
    errors = sum("error_type" in row for row in results)
    ceiling_hits = sum(row.get("iterations") == iteration_ceiling for row in results)
    return {
        "count": total,
        "mean_score": sum(scores) / total if total else 0.0,
        "parse_failure_rate": parse_failures / total if total else 0.0,
        "iteration_ceiling_rate": ceiling_hits / total if total else 0.0,
        "error_rate": errors / total if total else 0.0,
    }


async def evaluate(args: argparse.Namespace, *, rows: Sequence[Mapping[str, Any]] | None = None) -> dict[str, Any]:
    _validate_args(args)
    started_at = datetime.now(UTC).isoformat()
    real_scorer, synth_scorer, revision = _official_helpers(args.oolong_root)
    source_rows = (
        rows
        if rows is not None
        else _load_rows(
            args.split,
            min_len=args.min_len,
            max_len=args.max_len,
            limit=args.limit,
            source_page_size=args.source_page_size,
        )
    )
    selected = source_rows
    if not selected:
        raise OolongPreflightError(
            f"official {args.split} test split has no rows between {args.min_len} and {args.max_len} o200k tokens"
        )
    scorer = real_scorer if args.split == "real" else synth_scorer
    skills = [{"id": args.skill_id, "expected_version": args.skill_version}] if args.skill_id else []
    cache: dict[str, str] = {}
    results: list[dict[str, Any]] = []
    timeout = httpx.Timeout(2_000.0)
    async with httpx.AsyncClient(base_url=args.api_url.rstrip("/"), timeout=timeout) as client:
        profile, model, iteration_ceiling = await _server_policy_metadata(
            client,
            expected_profile=args.expected_profile,
        )
        for row in selected:
            context_window_id = str(row["context_window_id"])
            try:
                attachment_id = cache.setdefault(context_window_id, "")
                if not attachment_id:
                    attachment_id = await _upload_context(client, str(row["context_window_text"]), context_window_id)
                    cache[context_window_id] = attachment_id
                answer, usage = await run_row(client, row, split=args.split, attachment_id=attachment_id, skills=skills)
                scored = dict(scorer(row, _answer_for_official_scorer(answer, split=args.split), model))
                scored["full_answer"] = answer
                scored["score"] = float(scored.get("score", 0.0))
                scored["context_len"] = int(row["context_len"])
                scored["iterations"] = usage.get("iterations")
                results.append(scored)
            except Exception as exc:
                results.append(
                    {
                        "id": row["id"],
                        "context_len": row["context_len"],
                        "score": 0.0,
                        "error_type": type(exc).__name__,
                    }
                )
    return {
        "schema": RECEIPT_SCHEMA,
        "started_at": started_at,
        "finished_at": datetime.now(UTC).isoformat(),
        "profile": profile,
        "model": model,
        "oolong_revision": revision,
        "split": args.split,
        "min_len": args.min_len,
        "max_len": args.max_len,
        "limit": args.limit,
        "results": results,
        "aggregate": _aggregate(results, iteration_ceiling=iteration_ceiling),
    }


async def _run(args: argparse.Namespace) -> int:
    _require_live_environment()
    receipt = await evaluate(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "count": receipt["aggregate"]["count"]}, sort_keys=True))
    return 0 if receipt["aggregate"]["error_rate"] == 0 else 1


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return asyncio.run(_run(build_parser().parse_args(argv)))
    except OolongPreflightError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
