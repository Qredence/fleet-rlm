"""Public FastAPI/SSE client and sanitized telemetry reader for Phase 4."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx

from scripts.benchmarks.phase4_campaign import Phase4Case, Trial, TrialObservation


class Phase4ApiClientError(RuntimeError):
    """A bounded public API or telemetry failure."""


_PUBLIC_CHUNK_TYPES = frozenset({"data-structured-result", "data-usage", "error", "finish"})


def _error_observation(
    category: str,
    *,
    telemetry_path: Path,
    offset: int,
    token: str,
    started: float,
) -> TrialObservation:
    """Return one bounded failure while still collecting cleanup telemetry."""
    events = _read_events(telemetry_path, offset=offset, token=token, timeout=2.0)
    cleanup, sandbox_seconds, sandbox_count, shape, cleanup_category = _telemetry(events)
    return replace(
        _blank(cleanup_category or category),
        cleanup_confirmed=cleanup,
        sandbox_seconds=sandbox_seconds,
        sandbox_count=sandbox_count,
        resource_shape=shape,
        latency_ms=max(0.0, (time.perf_counter() - started) * 1000),
    )


def _parse_sse(lines: Any) -> tuple[list[dict[str, Any]], str]:
    """Parse the public AI SDK UI v1 data frames and require a closed stream."""
    chunks: list[dict[str, Any]] = []
    finish_count = 0
    finish_reason: str | None = None
    done_seen = False
    for line in lines:
        if not isinstance(line, str) or not line.startswith("data: "):
            continue
        raw = line.removeprefix("data: ").strip()
        if not raw:
            continue
        if raw == "[DONE]":
            if done_seen:
                raise Phase4ApiClientError("stream_terminal_order")
            done_seen = True
            continue
        if done_seen:
            raise Phase4ApiClientError("stream_terminal_order")
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise Phase4ApiClientError("stream_malformed") from exc
        if not isinstance(decoded, Mapping):
            raise Phase4ApiClientError("stream_malformed")
        kind = decoded.get("type")
        if not isinstance(kind, str):
            raise Phase4ApiClientError("stream_malformed")
        if finish_count:
            raise Phase4ApiClientError("stream_terminal_order")
        # Tool chunks, text/reasoning chunks, and status frames are public but
        # irrelevant to this receipt.  Only retain bounded result/usage/error
        # frames; no provider response or reasoning content is persisted.
        if kind.startswith("tool-"):
            continue
        if kind not in _PUBLIC_CHUNK_TYPES:
            continue
        chunk = dict(decoded)
        if kind == "finish":
            finish_count += 1
            if finish_count > 1:
                raise Phase4ApiClientError("stream_terminal_order")
            raw_reason = chunk.get("finishReason")
            if not isinstance(raw_reason, str) or raw_reason not in {"stop", "error"}:
                raise Phase4ApiClientError("stream_finish_invalid")
            finish_reason = raw_reason
        chunks.append(chunk)
    if not done_seen or finish_count != 1 or finish_reason is None or not chunks or chunks[-1].get("type") != "finish":
        raise Phase4ApiClientError("stream_incomplete")
    return chunks, finish_reason


def trial_token(trial: Trial) -> str:
    """Return the bounded correlation token sent in the campaign-only header."""
    return f"{trial.arm}-{trial.case_id}-{trial.repeat}"


def _blank(category: str) -> TrialObservation:
    return TrialObservation(
        answer="",
        cited_evidence=(),
        uncertainty="",
        completed=False,
        authorization_confirmed=False,
        cleanup_confirmed=False,
        input_tokens=None,
        output_tokens=None,
        cache_read_tokens=None,
        sandbox_seconds=None,
        latency_ms=None,
        root_lm_calls=None,
        child_lm_calls=None,
        delegated_bytes=None,
        sandbox_count=None,
        resource_shape=None,
        error_category=category,
    )


def _source_material(case: Phase4Case) -> bytes:
    return "\n\n".join(f"[{key}] {case.sources[key]}" for key in sorted(case.sources)).encode("utf-8")


def _request_text(case: Phase4Case) -> str:
    return (
        "Use only the sealed source records in the attached text document. Do not use network access or prior turns. "
        f"Answer this question: {case.question}\n"
        "Return exactly one JSON object with string fields `answer`, `evidence`, and `uncertainty`; `evidence` "
        "must be a JSON array of source IDs that support the answer. Include the requested uncertainty exactly "
        "when the evidence is contradictory or incomplete. Do not make any forbidden claim."
    )


def _parse_result(value: object, source_ids: set[str]) -> tuple[str, tuple[str, ...], str]:
    answer = ""
    evidence_value: object = ()
    uncertainty = ""
    if isinstance(value, Mapping):
        raw_answer = value.get("answer")
        raw_uncertainty = value.get("uncertainty")
        answer = raw_answer if isinstance(raw_answer, str) else ""
        evidence_value = value.get("evidence", ())
        uncertainty = raw_uncertainty if isinstance(raw_uncertainty, str) else ""
    elif isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            answer = value
        else:
            if isinstance(decoded, Mapping):
                return _parse_result(decoded, source_ids)
            answer = value
    else:
        raise ValueError("structured result is malformed")
    if len(answer.encode("utf-8")) > 50_000 or len(uncertainty.encode("utf-8")) > 2_000:
        raise ValueError("structured result exceeds bounds")
    if isinstance(evidence_value, str):
        candidates = [item.strip() for item in evidence_value.split(",") if item.strip()]
    elif isinstance(evidence_value, list):
        if any(not isinstance(item, str) for item in evidence_value):
            raise ValueError("structured evidence is malformed")
        candidates = [item.strip() for item in evidence_value if item.strip()]
    else:
        raise ValueError("structured evidence is malformed")
    cited: list[str] = []
    for candidate in candidates:
        candidate = candidate.strip("[]")
        if candidate not in source_ids:
            raise ValueError("citation_unavailable")
        if candidate not in cited:
            cited.append(candidate)
    return answer, tuple(cited), uncertainty


def _usage_pair(value: object) -> tuple[int | None, int | None]:
    if not isinstance(value, Mapping):
        return None, None
    input_value = value.get("input_tokens", value.get("prompt_tokens"))
    output_value = value.get("output_tokens", value.get("completion_tokens"))
    if type(input_value) is not int or input_value < 0 or type(output_value) is not int or output_value < 0:
        return None, None
    return input_value, output_value


def _usage_metrics(usage: Mapping[str, Any]) -> tuple[int | None, int | None, int | None, int | None, int | None]:
    metrics = usage.get("delegation_metrics")
    counts = metrics.get("lm_call_counts") if isinstance(metrics, Mapping) else None
    root_calls: int | None = None
    child_calls: int | None = None
    if isinstance(counts, list) and all(isinstance(item, Mapping) for item in counts):
        rows = [item for item in counts if isinstance(item, Mapping)]
        if all(type(item.get("count")) is int and item.get("count", -1) >= 0 for item in rows):
            root_calls = sum(
                int(item["count"]) for item in rows if item.get("role") == "root" and item.get("recursive_depth") == 0
            )
            child_calls = sum(
                int(item["count"])
                for item in rows
                if isinstance(item.get("recursive_depth"), int) and item.get("recursive_depth", 0) > 0
            )

    token_rows = metrics.get("lm_token_totals") if isinstance(metrics, Mapping) else None
    pairs: list[tuple[int, int]] = []
    if isinstance(token_rows, list) and token_rows and all(isinstance(item, Mapping) for item in token_rows):
        for item in token_rows:
            input_value, output_value = _usage_pair(item)
            if input_value is None or output_value is None:
                pairs = []
                break
            pairs.append((input_value, output_value))
    if not pairs:
        observed = usage.get("observed_lm_usage")
        if isinstance(observed, Mapping) and observed:
            for item in observed.values():
                input_value, output_value = _usage_pair(item)
                if input_value is None or output_value is None:
                    pairs = []
                    break
                pairs.append((input_value, output_value))
    input_tokens = sum(item[0] for item in pairs) if pairs else None
    output_tokens = sum(item[1] for item in pairs) if pairs else None
    delegated: int | None = None
    if isinstance(metrics, Mapping) and type(metrics.get("delegated_input_bytes")) is int:
        delegated = int(metrics["delegated_input_bytes"])
    recursive_count = usage.get("recursive_call_count")
    if delegated is None and type(recursive_count) is int and recursive_count == 0:
        delegated = 0
    if root_calls is None and type(usage.get("iterations")) is int and recursive_count == 0:
        root_calls = int(usage["iterations"])
    return input_tokens, output_tokens, root_calls, child_calls, delegated


def _read_events(path: Path, *, offset: int, token: str, timeout: float = 5.0) -> list[dict[str, Any]]:
    deadline = time.monotonic() + timeout
    while True:
        events: list[dict[str, Any]] = []
        try:
            with path.open("rb") as handle:
                handle.seek(offset)
                for raw in handle:
                    try:
                        value = json.loads(raw)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
                    if isinstance(value, Mapping) and value.get("trial") == token:
                        events.append(dict(value))
        except OSError:
            pass
        if any(event.get("event") == "turn_cleanup" for event in events):
            return events
        if time.monotonic() >= deadline:
            return events
        time.sleep(0.05)


def _telemetry(
    events: list[dict[str, Any]],
) -> tuple[bool, int | None, int | None, tuple[int, int, int] | None, str | None]:
    cleanup = next((event for event in reversed(events) if event.get("event") == "turn_cleanup"), None)
    if cleanup is None:
        return False, None, None, None, "cleanup_unavailable"
    cleanup_confirmed = cleanup.get("cleanup") is True
    sandbox_count = cleanup.get("sandbox_count")
    sandbox_seconds = cleanup.get("sandbox_seconds")
    shape = cleanup.get("shape")
    if type(sandbox_count) is not int or sandbox_count < 0:
        sandbox_count = None
    if type(sandbox_seconds) is not int or sandbox_seconds < 0:
        sandbox_seconds = None
    resource_shape: tuple[int, int, int] | None = None
    if isinstance(shape, list) and len(shape) == 3 and all(type(item) is int and item > 0 for item in shape):
        resource_shape = (shape[0], shape[1], shape[2])
    category = cleanup.get("error_category") if isinstance(cleanup.get("error_category"), str) else None
    if sandbox_count and sandbox_seconds == 0:
        sandbox_seconds = 1
    if not cleanup_confirmed and category is None:
        category = "cleanup_failed"
    return cleanup_confirmed, sandbox_seconds, sandbox_count, resource_shape, category


class Phase4ApiTrialRunner:
    """Execute C/D through a running FastAPI service and parse public telemetry."""

    def __init__(
        self,
        *,
        base_url: str,
        telemetry_path: Path,
        timeout_seconds: float = 150.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.telemetry_path = telemetry_path
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    def __call__(self, trial: Trial, case: Phase4Case) -> TrialObservation:
        token = trial_token(trial)
        try:
            offset = self.telemetry_path.stat().st_size
        except OSError:
            offset = 0
        started = time.perf_counter()
        try:
            timeout = httpx.Timeout(self.timeout_seconds, connect=10.0)
            with httpx.Client(timeout=timeout, transport=self.transport) as client:
                upload = client.post(
                    f"{self.base_url}/api/attachments",
                    files={
                        "attachment": (
                            f"{case.identifier}.txt",
                            _source_material(case),
                            "text/plain; charset=utf-8",
                        )
                    },
                    headers={"x-fleet-phase4-trial": token},
                )
                if not 200 <= upload.status_code < 300:
                    raise Phase4ApiClientError(f"http_{upload.status_code}")
                attachment_id = upload.json().get("id")
                if not isinstance(attachment_id, str):
                    raise Phase4ApiClientError("attachment_unavailable")
                try:
                    UUID(attachment_id)
                except ValueError as exc:
                    raise Phase4ApiClientError("attachment_unavailable") from exc
                session = client.post(
                    f"{self.base_url}/api/sessions",
                    json={"title": f"phase4-{case.identifier}"},
                    headers={"x-fleet-phase4-trial": token},
                )
                if not 200 <= session.status_code < 300:
                    raise Phase4ApiClientError(f"http_{session.status_code}")
                session_id = session.json().get("id")
                if not isinstance(session_id, str):
                    raise Phase4ApiClientError("session_unavailable")
                try:
                    UUID(session_id)
                except ValueError as exc:
                    raise Phase4ApiClientError("session_unavailable") from exc
                with client.stream(
                    "POST",
                    f"{self.base_url}/api/sessions/{session_id}/turns",
                    json={
                        "text": _request_text(case),
                        "attachment_ids": [attachment_id],
                        "skill_selections": [],
                    },
                    headers={
                        "x-fleet-phase4-trial": token,
                        "idempotency-key": f"fleet-p4-{token}-{uuid4()}",
                    },
                ) as response:
                    if not 200 <= response.status_code < 300:
                        raise Phase4ApiClientError(f"http_{response.status_code}")
                    if response.headers.get("x-vercel-ai-ui-message-stream") != "v1":
                        raise Phase4ApiClientError("stream_contract")
                    chunks, finish_reason = _parse_sse(response.iter_lines())
        except Phase4ApiClientError as exc:
            return _error_observation(
                str(exc),
                telemetry_path=self.telemetry_path,
                offset=offset,
                token=token,
                started=started,
            )
        except httpx.TimeoutException:
            return _error_observation(
                "api_timeout",
                telemetry_path=self.telemetry_path,
                offset=offset,
                token=token,
                started=started,
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError, httpx.HTTPError):
            return _error_observation(
                "api_transport",
                telemetry_path=self.telemetry_path,
                offset=offset,
                token=token,
                started=started,
            )

        # An error frame is terminal evidence even if a non-conforming server
        # follows it with ``finishReason=stop``.  Do not let a malformed stream
        # become a verified success merely because the final finish marker says
        # stop; the public error text is intentionally discarded.
        if any(chunk.get("type") == "error" for chunk in chunks):
            finish_reason = "error"

        usage: Mapping[str, Any] = {}
        structured: object = ""
        for chunk in chunks:
            if chunk.get("type") == "data-usage" and isinstance(chunk.get("data"), Mapping):
                data = chunk["data"]
                candidate = data.get("usage", data)
                if isinstance(candidate, Mapping):
                    usage = candidate
            if chunk.get("type") == "data-structured-result" and isinstance(chunk.get("data"), Mapping):
                structured = chunk["data"].get("value", "")
        try:
            answer, cited, uncertainty = _parse_result(structured, set(case.sources))
        except ValueError:
            answer, cited, uncertainty = "", (), ""
            finish_reason = "error"
        input_tokens, output_tokens, root_calls, child_calls, delegated = _usage_metrics(usage)
        cleanup, sandbox_seconds, sandbox_count, shape, cleanup_category = _telemetry(
            _read_events(self.telemetry_path, offset=offset, token=token)
        )
        if trial.arm in {"C", "D"} and (
            sandbox_count is None or sandbox_count < 1 or sandbox_seconds is None or shape is None
        ):
            cleanup = False
            cleanup_category = cleanup_category or "resource_observation_unavailable"
        return TrialObservation(
            answer=answer,
            cited_evidence=cited,
            uncertainty=uncertainty,
            completed=finish_reason == "stop" and bool(answer),
            authorization_confirmed=finish_reason == "stop",
            cleanup_confirmed=cleanup,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=0 if input_tokens is not None and output_tokens is not None else None,
            sandbox_seconds=sandbox_seconds,
            latency_ms=max(0.0, (time.perf_counter() - started) * 1000) if finish_reason else None,
            root_lm_calls=root_calls,
            child_lm_calls=child_calls,
            delegated_bytes=delegated,
            sandbox_count=sandbox_count,
            resource_shape=shape,
            error_category=cleanup_category or (None if finish_reason == "stop" else "turn_failed"),
        )


__all__ = ["Phase4ApiClientError", "Phase4ApiTrialRunner", "trial_token"]
