"""Shared optimization dataset validation, secret scanning/scrubbing, and DB endpoint guards."""

from __future__ import annotations

import json
import re
from collections.abc import Collection, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal, overload
from urllib.parse import urlsplit

from .config import Settings

type OptimizationTarget = Literal["rlm", "skills", "memory"]

VALID_OPT_TARGETS: tuple[OptimizationTarget, ...] = ("skills", "memory", "rlm")
VALID_SPLITS: frozenset[str] = frozenset({"train", "val", "holdout"})
VALID_VERIFIER_TYPES: frozenset[str] = frozenset(
    {"exact", "json", "fact_checklist", "artifact", "rubric"}
)
BUILTIN_DATASETS: dict[OptimizationTarget, tuple[str, str]] = {
    "skills": ("builtin:skills", "skills.jsonl"),
    "memory": ("builtin:memory", "memory.jsonl"),
    "rlm": ("builtin:rlm", "rlm.jsonl"),
}
MAX_DATASET_BYTES = 10 * 1024 * 1024
MAX_DATASET_ROWS = 2000

_SECRET_SCAN_RE = re.compile(
    r"(?:"
    r"sk-[A-Za-z0-9_-]{16,}"
    r"|dtn_[A-Za-z0-9_-]{12,}"
    r"|tvly-[A-Za-z0-9_-]{12,}"
    r"|napi_[A-Za-z0-9_-]{12,}"
    r"|xai-[A-Za-z0-9_-]{12,}"
    r"|gsk_[A-Za-z0-9_-]{12,}"
    r"|AIza[0-9A-Za-z_-]{20,}"
    r"|gh[pousr]_[A-Za-z0-9]{20,}"
    r"|github_pat_[A-Za-z0-9_]{20,}"
    r"|(?:AKIA|ASIA)[0-9A-Z]{16}"
    r"|-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----(?:[\s\S]*?-----END [A-Z0-9 ]*PRIVATE KEY-----)?"
    r"|postgres(?:ql)?(?:\+psycopg)?://[^\s\"'<>]+"
    r"|https?://[^/\s\"'@:]+:[^/\s\"'@]+@[^\s\"'<>]+"
    r"|Bearer\s+[A-Za-z0-9._~+/-]{16,}=*"
    r"|(?:api[_-]?key|token|secret|password)=[^\s&\"']{6,}"
    r"|[?&](?:api_?key|access_?token|auth_?token|token|secret|password|key)=[^\s&\"'#<>]+"
    r")",
    re.IGNORECASE,
)
SECRET_SCAN_RE = _SECRET_SCAN_RE


class DatasetValidationError(ValueError):
    """Raised when an optimization JSONL dataset fails schema, split, size, or secret validation."""


def contains_secret(text: str) -> bool:
    """Return True if *text* matches any raw credential or secret pattern."""
    return bool(_SECRET_SCAN_RE.search(text))


@overload
def scrub_secrets(value: str, extra_secrets: Sequence[str] = ()) -> str: ...


@overload
def scrub_secrets(value: dict[str, Any], extra_secrets: Sequence[str] = ()) -> dict[str, Any]: ...


@overload
def scrub_secrets(value: list[Any], extra_secrets: Sequence[str] = ()) -> list[Any]: ...


@overload
def scrub_secrets(value: Any, extra_secrets: Sequence[str] = ()) -> Any: ...


def scrub_secrets(value: Any, extra_secrets: Sequence[str] = ()) -> Any:
    """Recursively redact secrets in strings, dicts, lists, and tuples."""
    ordered_extras = sorted({s for s in extra_secrets if s}, key=len, reverse=True)
    return _scrub_recursive(value, ordered_extras)


def _scrub_string(text: str, ordered_extras: Sequence[str]) -> str:
    cleaned = _SECRET_SCAN_RE.sub("[REDACTED]", text)
    for secret in ordered_extras:
        cleaned = cleaned.replace(secret, "[REDACTED]")
    return cleaned


def _scrub_recursive(value: Any, ordered_extras: Sequence[str]) -> Any:
    if isinstance(value, str):
        return _scrub_string(value, ordered_extras)
    if isinstance(value, dict):
        return {
            (_scrub_string(k, ordered_extras) if isinstance(k, str) else k): _scrub_recursive(
                v, ordered_extras
            )
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_scrub_recursive(item, ordered_extras) for item in value]
    if isinstance(value, tuple):
        return tuple(_scrub_recursive(item, ordered_extras) for item in value)
    return value


def normalize_pg_url(url: str) -> str:
    """Normalize a PostgreSQL URL to use the psycopg driver prefix."""
    raw = url.strip()
    if raw.startswith("postgresql://"):
        raw = raw.replace("postgresql://", "postgresql+psycopg://", 1)
    if not raw.startswith("postgresql+psycopg://"):
        raise ValueError("Database URL must start with postgresql:// or postgresql+psycopg://")
    return raw


def db_endpoint_key(url: str) -> tuple[str | None, int, str]:
    """Return canonical (host, port, path) normalizing default port 5432 and trailing '.'."""
    parsed = urlsplit(normalize_pg_url(url))
    host = parsed.hostname.lower().rstrip(".") if parsed.hostname else None
    port = parsed.port if parsed.port is not None else 5432
    path = parsed.path.rstrip("/") or "/"
    return (host, port, path)


def same_db_endpoint(a: str, b: str | Settings) -> bool:
    """Compare two Postgres endpoints, normalizing default port 5432 and trailing DNS dots."""
    key_a = db_endpoint_key(a)
    if isinstance(b, Settings):
        return key_a in {db_endpoint_key(b.database_url), db_endpoint_key(b.database_direct_url)}
    return key_a == db_endpoint_key(b)


def redact_db_label(url: str) -> str:
    """Format a credential-free host/database label for UI display."""
    try:
        parsed = urlsplit(url)
    except ValueError:
        return "configured-branch"
    host = parsed.hostname or "branch"
    dbname = parsed.path.lstrip("/") or "neondb"
    return f"{host}/{dbname}"


def _validate_prior_turns(raw: Any, *, line_no: int) -> None:
    if raw is None or raw == []:
        return
    if not isinstance(raw, list):
        raise DatasetValidationError(f"Line {line_no}: 'prior_turns' must be a list of objects")
    for idx, turn in enumerate(raw, 1):
        if not isinstance(turn, Mapping):
            raise DatasetValidationError(f"Line {line_no}: prior_turns[{idx}] must be an object")


def _validate_existing_memories(raw: Any, *, line_no: int) -> None:
    if raw is None or raw == []:
        return
    if not isinstance(raw, list):
        raise DatasetValidationError(f"Line {line_no}: 'existing_memories' must be a list")
    for idx, mem in enumerate(raw, 1):
        if not isinstance(mem, Mapping):
            raise DatasetValidationError(
                f"Line {line_no}: existing_memories[{idx}] must be an object"
            )
        scope = mem.get("scope")
        if scope is not None:
            if not isinstance(scope, str) or scope.strip() not in {"global", "session"}:
                raise DatasetValidationError(
                    f"Line {line_no}: existing_memories[{idx}] has invalid scope {scope!r}"
                )
        status = mem.get("status")
        if status is not None:
            if not isinstance(status, str) or status.strip() not in {"approved", "pending"}:
                raise DatasetValidationError(
                    f"Line {line_no}: existing_memories[{idx}] has invalid status {status!r}"
                )


def _validate_skill_list(
    raw: Any,
    *,
    field_name: str,
    line_no: int,
    available: set[str],
) -> None:
    if raw is None or raw == []:
        return
    if not isinstance(raw, list):
        raise DatasetValidationError(f"Line {line_no}: '{field_name}' must be a list of strings")
    for idx, item in enumerate(raw, 1):
        if not isinstance(item, str) or not item.strip():
            raise DatasetValidationError(
                f"Line {line_no}: {field_name}[{idx}] must be a nonempty string"
            )
        cleaned = item.removeprefix("@").strip()
        if available and cleaned not in available:
            raise DatasetValidationError(
                f"Line {line_no}: unknown skill {cleaned!r} in '{field_name}'"
            )


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError(f"Dataset file not found: {path}")
    rows: list[dict[str, Any]] = []
    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = raw.strip()
        if not stripped:
            continue
        parsed = json.loads(stripped)
        if not isinstance(parsed, dict):
            raise ValueError(f"{path}:{line_no}: expected a JSON object")
        rows.append(parsed)
    return rows


def validate_and_summarize_jsonl(
    raw_bytes: bytes,
    target: OptimizationTarget,
    available_skills: Collection[str] | None = None,
    *,
    strict_secrets: bool = True,
    enforce_min_rows: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Validate an optimization JSONL payload and return parsed rows plus summary metadata."""
    if target not in VALID_OPT_TARGETS:
        raise DatasetValidationError(f"Unsupported optimization target: {target!r}")
    if len(raw_bytes) > MAX_DATASET_BYTES:
        raise DatasetValidationError("Dataset exceeds 10 MiB limit")

    try:
        text_body = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as error:
        raise DatasetValidationError("Dataset must be UTF-8 encoded JSONL") from error

    if strict_secrets and contains_secret(text_body):
        raise DatasetValidationError(
            "Dataset appears to contain raw credentials or API keys; "
            "remove secrets before uploading"
        )

    available = set(available_skills or ())
    rows: list[dict[str, Any]] = []
    splits: dict[str, int] = {"train": 0, "val": 0, "holdout": 0, "unspecified": 0}
    lineages: set[str] = set()
    lineage_to_split: dict[str, str] = {}
    group_keys: set[str] = set()
    positive_count = 0
    negative_count = 0
    blocking_count = 0

    for line_no, raw_line in enumerate(text_body.splitlines(), 1):
        stripped = raw_line.strip()
        if not stripped:
            continue
        if len(rows) >= MAX_DATASET_ROWS:
            raise DatasetValidationError(f"Dataset cannot exceed {MAX_DATASET_ROWS} rows")
        try:
            row = json.loads(stripped)
        except json.JSONDecodeError as error:
            raise DatasetValidationError(f"Line {line_no}: invalid JSON ({error.msg})") from error
        if not isinstance(row, dict):
            raise DatasetValidationError(f"Line {line_no}: each JSONL line must be a JSON object")

        raw_split = row.get("split")
        norm_split: str | None = None
        if raw_split is not None and raw_split != "":
            if not isinstance(raw_split, str) or raw_split.strip() not in VALID_SPLITS:
                raise DatasetValidationError(
                    f"Line {line_no}: invalid split {raw_split!r} (expected train, val, or holdout)"
                )
            norm_split = raw_split.strip()
            splits[norm_split] += 1
        else:
            splits["unspecified"] += 1

        raw_lineage = row.get("lineage")
        norm_lineage: str | None = None
        if raw_lineage is not None and raw_lineage != "":
            if not isinstance(raw_lineage, str) or not raw_lineage.strip():
                raise DatasetValidationError(
                    f"Line {line_no}: 'lineage' must be a nonempty string when set"
                )
            norm_lineage = raw_lineage.strip()
            lineages.add(norm_lineage)
            group_keys.add(f"lineage:{norm_lineage}")
        else:
            group_keys.add(f"row:{len(rows)}")

        if norm_lineage is not None and norm_split is not None:
            effective_split = (
                "holdout"
                if (target in {"skills", "memory"} and norm_split == "val")
                else norm_split
            )
            previous_split = lineage_to_split.get(norm_lineage)
            if previous_split is not None and previous_split != effective_split:
                raise DatasetValidationError(
                    f"Line {line_no}: lineage {norm_lineage!r} leaks across multiple dataset splits"
                )
            lineage_to_split[norm_lineage] = effective_split

        if target == "skills":
            task = row.get("task")
            if not isinstance(task, str) or not task.strip():
                raise DatasetValidationError(
                    f"Line {line_no}: skill rows require a nonempty 'task'"
                )
            _validate_prior_turns(row.get("prior_turns"), line_no=line_no)
            raw_expected = row.get("expected_skill", row.get("primary_skill"))
            needs_skill = (
                bool(row["needs_skill"])
                if "needs_skill" in row
                else (isinstance(raw_expected, str) and bool(raw_expected.strip()))
            )
            if needs_skill:
                positive_count += 1
                if not isinstance(raw_expected, str) or not raw_expected.strip():
                    raise DatasetValidationError(
                        f"Line {line_no}: rows with needs_skill=true require 'expected_skill'"
                    )
                cleaned = raw_expected.removeprefix("@").strip()
                if available and cleaned not in available:
                    raise DatasetValidationError(f"Line {line_no}: unknown skill {cleaned!r}")
            else:
                negative_count += 1
            _validate_skill_list(
                row.get("secondary_skills"),
                field_name="secondary_skills",
                line_no=line_no,
                available=available,
            )
        elif target == "memory":
            exchange = row.get("turn_exchange")
            has_task = isinstance(row.get("task"), str) and bool(row["task"].strip())
            has_exchange = (isinstance(exchange, str) and bool(exchange.strip())) or (
                isinstance(exchange, dict)
                and isinstance(exchange.get("task"), str)
                and bool(exchange["task"].strip())
            )
            if not (has_task or has_exchange):
                raise DatasetValidationError(
                    f"Line {line_no}: memory rows require 'task' or 'turn_exchange'"
                )
            if "worth_saving" not in row and "should_save" not in row:
                raise DatasetValidationError(
                    f"Line {line_no}: memory rows require 'worth_saving' or 'should_save'"
                )
            worth = bool(row.get("worth_saving", row.get("should_save")))
            if worth:
                positive_count += 1
            else:
                negative_count += 1
            action = row.get("action")
            if action is not None and action not in {"insert", "update", "skip"}:
                raise DatasetValidationError(f"Line {line_no}: invalid action {action!r}")
            scope = row.get("scope")
            if scope is not None and scope not in {"global", "session"}:
                raise DatasetValidationError(f"Line {line_no}: invalid scope {scope!r}")
            _validate_existing_memories(row.get("existing_memories"), line_no=line_no)
        else:
            task = row.get("task")
            if not isinstance(task, str) or not task.strip():
                raise DatasetValidationError(f"Line {line_no}: RLM rows require a nonempty 'task'")
            verifier = row.get("verifier")
            answer = row.get("answer")
            if verifier is not None:
                if (
                    not isinstance(verifier, dict)
                    or verifier.get("type") not in VALID_VERIFIER_TYPES
                ):
                    raise DatasetValidationError(
                        f"Line {line_no}: invalid verifier {verifier!r} "
                        f"(expected type in {sorted(VALID_VERIFIER_TYPES)})"
                    )
                if answer is not None and not isinstance(answer, str):
                    raise DatasetValidationError(
                        f"Line {line_no}: 'answer' must be a string when provided"
                    )
            else:
                if not isinstance(answer, str) or not answer.strip():
                    raise DatasetValidationError(
                        f"Line {line_no}: RLM rows require a nonempty string 'answer' "
                        "or a valid 'verifier' object"
                    )
            _validate_skill_list(
                row.get("skill_names"),
                field_name="skill_names",
                line_no=line_no,
                available=available,
            )
            _validate_prior_turns(row.get("prior_turns"), line_no=line_no)
            if (raw_input_files := row.get("input_files")) is not None:
                if not isinstance(raw_input_files, Mapping):
                    raise DatasetValidationError(f"Line {line_no}: 'input_files' must be a mapping")
                for key in raw_input_files:
                    if not isinstance(key, str) or not key.strip():
                        raise DatasetValidationError(
                            f"Line {line_no}: input_files keys must be nonempty strings"
                        )
            if (raw_web := row.get("web_fixtures")) is not None and not isinstance(
                raw_web, Mapping
            ):
                raise DatasetValidationError(
                    f"Line {line_no}: 'web_fixtures' must be a mapping or null"
                )
            max_actions = row.get("max_expected_actions")
            if max_actions is not None and (
                isinstance(max_actions, bool) or not isinstance(max_actions, int) or max_actions < 1
            ):
                raise DatasetValidationError(
                    f"Line {line_no}: 'max_expected_actions' must be a positive integer"
                )
            if row.get("blocking"):
                blocking_count += 1

        rows.append(row)

    if not rows:
        raise DatasetValidationError("Dataset contains zero JSONL rows")

    min_rows = 3 if target == "rlm" else 2
    if enforce_min_rows and len(rows) < min_rows:
        raise DatasetValidationError(
            f"{target.upper()} datasets require at least {min_rows} rows (found {len(rows)})"
        )

    specified_count = splits["train"] + splits["val"] + splits["holdout"]
    if specified_count > 0 and splits["unspecified"] > 0:
        raise DatasetValidationError("Either all rows or no rows may specify 'split'")

    if specified_count > 0 and enforce_min_rows:
        if target in {"skills", "memory"}:
            if splits["train"] < 1 or (splits["holdout"] + splits["val"]) < 1:
                raise DatasetValidationError(
                    "Explicit splits must contain at least one 'train' and one 'holdout' row"
                )
        else:
            if splits["train"] < 1 or splits["val"] < 1 or splits["holdout"] < 1:
                raise DatasetValidationError(
                    "Explicit RLM splits must include non-empty 'train', 'val', and 'holdout' sets"
                )
    elif specified_count == 0 and enforce_min_rows and lineages and len(group_keys) < min_rows:
        raise DatasetValidationError(
            f"At least {min_rows} distinct lineages/groups are required for '{target}' splits"
        )

    summary: dict[str, Any] = {
        "row_count": len(rows),
        "splits": splits,
        "lineage_count": len(lineages),
    }
    if target in {"skills", "memory"}:
        summary["positive_count"] = positive_count
        summary["negative_count"] = negative_count
    else:
        summary["blocking_count"] = blocking_count
    return rows, summary
