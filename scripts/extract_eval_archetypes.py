"""Read-only empirical archetype extractor (MLflow + Neon Postgres) and dataset validator.

This tool inspects recent Fleet-RLM runs, memories, and MLflow traces in strict read-only
mode to summarize failure and friction archetypes into a PRIVATE JSON report outside Git
(default: ``~/.local/share/fleet-rlm/optimization/drafts/archetypes.json``).

It also provides ``--validate-datasets`` to verify sanitized JSONL datasets and programmatic
fixtures under ``backend/eval_datasets/`` before committing or running optimization.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PRIVATE_OUTPUT = (
    Path.home()
    / ".local"
    / "share"
    / "fleet-rlm"
    / "optimization"
    / "drafts"
    / "archetypes.json"
)
DEFAULT_MLFLOW_URI = "http://127.0.0.1:5001"
DEFAULT_SKILLS_DIR = REPO_ROOT / "backend" / "skills"
DEFAULT_EVAL_DIR = REPO_ROOT / "backend" / "eval_datasets"

# Patterns redacted in private draft summaries and flagged during Git dataset validation.
_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("openai_like_key", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b")),
    ("tavily_key", re.compile(r"\btvly-[A-Za-z0-9_-]{12,}\b")),
    ("daytona_key", re.compile(r"\bdtn_[A-Za-z0-9_-]{12,}\b")),
    ("github_pat", re.compile(r"\b(?:ghp|gho|ghu|github_pat)_[A-Za-z0-9_]{16,}\b")),
    ("hf_token", re.compile(r"\bhf_[A-Za-z0-9]{16,}\b")),
    (
        "postgres_uri",
        re.compile(r"postgres(?:ql)?(?:\+psycopg)?://[^\s\"']+", re.IGNORECASE),
    ),
    (
        "url_secret_param",
        re.compile(r"[?&](?:api_?key|token|secret|sig|auth)=[^&\s\"']+", re.IGNORECASE),
    ),
    ("bearer_token", re.compile(r"\bBearer\s+[A-Za-z0-9._-]{16,}\b")),
    ("local_user_path", re.compile(r"/(?:Users|home)/[A-Za-z0-9._-]+/")),
)

_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_URL_QUERY_RE = re.compile(r"(https?://[^\s?#]+)\?[^\s\"'<>)]+")
_UUID_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)

_ALLOWED_VERIFIER_TYPES = frozenset(
    {"exact", "json", "fact_checklist", "artifact", "rubric"}
)


def _redact_text(text: str, *, max_chars: int = 160) -> str:
    """Redact potential secrets, emails, query strings, and UUIDs from diagnostic excerpts."""
    cleaned = " ".join(text.split())
    for label, pattern in _SECRET_PATTERNS:
        cleaned = pattern.sub(f"<REDACTED:{label}>", cleaned)
    cleaned = _URL_QUERY_RE.sub(r"\1?<REDACTED_QUERY>", cleaned)
    cleaned = _EMAIL_RE.sub("<REDACTED_EMAIL>", cleaned)
    cleaned = _UUID_RE.sub("<UUID>", cleaned)
    if len(cleaned) > max_chars:
        return cleaned[: max_chars - 3] + "..."
    return cleaned


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def _resolve_database_url(
    explicit_url: str | None, env_file: Path | None
) -> str | None:
    if explicit_url:
        return explicit_url.strip()
    if env_file is not None:
        parsed = _parse_env_file(env_file)
        if parsed.get("FLEET_DATABASE_URL"):
            return parsed["FLEET_DATABASE_URL"]
    if os.environ.get("FLEET_DATABASE_URL"):
        return os.environ["FLEET_DATABASE_URL"]
    local_env = REPO_ROOT / "backend" / ".env"
    if local_env.is_file():
        parsed = _parse_env_file(local_env)
        if parsed.get("FLEET_DATABASE_URL"):
            return parsed["FLEET_DATABASE_URL"]
    return None


def _normalize_psycopg_url(url: str) -> str:
    cleaned = url.strip()
    if cleaned.startswith("postgresql+psycopg://"):
        return cleaned.replace("postgresql+psycopg://", "postgresql://", 1)
    return cleaned


def _ensure_private_output_path(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    repo_resolved = REPO_ROOT.resolve()
    try:
        resolved.relative_to(repo_resolved)
    except ValueError:
        return resolved
    raise ValueError(
        f"Refusing to write private archetype report inside the Git repository: {resolved}. "
        f"Use an external path such as {DEFAULT_PRIVATE_OUTPUT}."
    )


def _summarize_ui_parts(parts: Sequence[Mapping[str, Any]] | None) -> dict[str, Any]:
    """Extract measured execution telemetry from saved run ui_parts without raw code."""
    skills: list[str] = []
    completion = "unknown"
    root_actions: int | None = None
    child_rlms = 0
    sub_lm_queries = 0
    python_errors = 0
    web_outcomes: Counter[str] = Counter()
    opened_targets: Counter[str] = Counter()
    file_count = 0

    for part in parts or ():
        if not isinstance(part, Mapping):
            continue
        ptype = part.get("type")
        data = part.get("data")
        if ptype == "data-turn-details" and isinstance(data, Mapping):
            comp = data.get("completion")
            if isinstance(comp, str):
                completion = comp
            if isinstance(data.get("root_actions"), int):
                root_actions = data["root_actions"]
            if isinstance(data.get("child_rlms"), int):
                child_rlms = data["child_rlms"]
            if isinstance(data.get("sub_lm_queries"), int):
                sub_lm_queries = data["sub_lm_queries"]
        elif ptype == "data-skills" and isinstance(data, Mapping):
            raw_skills = data.get("skills")
            if isinstance(raw_skills, list):
                skills = [
                    item["name"]
                    for item in raw_skills
                    if isinstance(item, Mapping) and isinstance(item.get("name"), str)
                ]
        elif ptype == "data-file-tree" and isinstance(data, Mapping):
            raw_files = data.get("files")
            if isinstance(raw_files, list):
                file_count = len(raw_files)
        elif ptype == "data-web-call" and isinstance(data, Mapping):
            tool = data.get("tool")
            outcome = data.get("outcome")
            if isinstance(tool, str) and isinstance(outcome, str):
                web_outcomes[f"{tool}.{outcome}"] += 1
                if tool == "web_open" and outcome in {"opened", "truncated"}:
                    target = data.get("target")
                    if isinstance(target, str) and target:
                        opened_targets[target] += 1
        elif ptype == "tool-python_exec" and part.get("state") == "output-error":
            python_errors += 1

    repeated_opens = sum(count > 1 for count in opened_targets.values())
    return {
        "skills": skills,
        "completion": completion,
        "root_actions": root_actions,
        "child_rlms": child_rlms,
        "sub_lm_queries": sub_lm_queries,
        "python_errors": python_errors,
        "web_outcomes": dict(web_outcomes),
        "repeated_web_opens": repeated_opens,
        "file_count": file_count,
    }


def _prompt_shape(prompt: str) -> dict[str, Any]:
    """Compute structural features of a prompt without storing raw private text verbatim."""
    stripped = prompt.strip()
    words = stripped.split()
    has_url = bool(re.search(r"https?://", stripped, re.IGNORECASE))
    has_question = "?" in stripped
    mentions_file = bool(
        re.search(
            r"\b\w+\.(?:csv|xlsx|xls|pdf|parquet|json|svg|png|md|txt)\b",
            stripped,
            re.IGNORECASE,
        )
    )
    return {
        "char_len": len(stripped),
        "word_count": len(words),
        "has_url": has_url,
        "has_question": has_question,
        "mentions_file": mentions_file,
        "is_short_followup": len(stripped) <= 90 and len(words) <= 14,
    }


def extract_db_archetypes(database_url: str, *, limit: int = 300) -> dict[str, Any]:
    """Run strictly read-only SELECT queries against Postgres to summarize friction archetypes."""
    import psycopg

    conn_url = _normalize_psycopg_url(database_url)
    archetypes: dict[str, Any] = {
        "iteration_limit": [],
        "python_errors": [],
        "repeated_web_open": [],
        "web_exhausted_or_truncated": [],
        "multi_turn_web_followup": [],
        "skill_selection_ambiguities": [],
        "memory_edge_cases": [],
    }
    summary: dict[str, Any] = {
        "available": False,
        "source_host": urlsplit(conn_url).hostname,
        "runs_inspected": 0,
        "memories_inspected": 0,
        "completion_counts": {},
        "status_counts": {},
        "skill_activation_counts": {},
        "archetypes": archetypes,
        "memory_stats": {},
    }

    try:
        with psycopg.connect(conn_url, prepare_threshold=None) as conn:
            conn.read_only = True
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id::text, session_id::text, status, prompt, answer, ui_parts,
                           created_at, finished_at
                    FROM runs
                    WHERE parent_run_id IS NULL
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
                run_rows = cur.fetchall()

                cur.execute(
                    """
                    SELECT id::text, session_id::text, source_run_id::text, skill_name,
                           content, approved, created_at
                    FROM memories
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
                memory_rows = cur.fetchall()
    except psycopg.Error as exc:
        summary["error"] = f"{type(exc).__name__}: {exc}"
        return summary

    summary["available"] = True
    summary["runs_inspected"] = len(run_rows)
    summary["memories_inspected"] = len(memory_rows)

    completion_counts: Counter[str] = Counter()
    status_counts: Counter[str] = Counter()
    skill_counts: Counter[str] = Counter()
    sessions_chronological: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for (
        run_id,
        session_id,
        status,
        prompt,
        answer,
        ui_parts,
        created_at,
        finished_at,
    ) in run_rows:
        status_counts[str(status)] += 1
        tel = _summarize_ui_parts(ui_parts if isinstance(ui_parts, list) else [])
        completion_counts[tel["completion"]] += 1
        for skill in tel["skills"]:
            skill_counts[skill] += 1
        if not tel["skills"]:
            skill_counts["<none>"] += 1

        duration_s: float | None = None
        if created_at is not None and finished_at is not None:
            duration_s = round((finished_at - created_at).total_seconds(), 2)

        prompt_text = str(prompt or "")
        answer_text = str(answer or "")
        record = {
            "run_id_prefix": str(run_id)[:8],
            "status": str(status),
            "duration_s": duration_s,
            "prompt_shape": _prompt_shape(prompt_text),
            "prompt_redacted": _redact_text(prompt_text, max_chars=120),
            "answer_len": len(answer_text),
            **tel,
        }
        sessions_chronological[str(session_id)].append(
            {**record, "created_at": created_at.isoformat() if created_at else ""}
        )

        if tel["completion"] == "iteration_limit":
            summary["archetypes"]["iteration_limit"].append(record)
        if tel["python_errors"] >= 1:
            summary["archetypes"]["python_errors"].append(record)
        if tel["repeated_web_opens"] >= 1:
            summary["archetypes"]["repeated_web_open"].append(record)
        if (
            tel["web_outcomes"].get("web_search.exhausted", 0)
            + tel["web_outcomes"].get("web_open.exhausted", 0)
            + tel["web_outcomes"].get("web_open.truncated", 0)
            > 0
        ):
            summary["archetypes"]["web_exhausted_or_truncated"].append(record)

        # Skill ambiguity signals: multi-skill activation, or file-mention with no skill,
        # or short prompt with unexpected skill activation.
        shape = record["prompt_shape"]
        if (
            len(tel["skills"]) >= 2
            or (shape["mentions_file"] and not tel["skills"])
            or (shape["has_url"] and "web-research" not in tel["skills"])
            or (shape["word_count"] <= 4 and bool(tel["skills"]))
        ):
            summary["archetypes"]["skill_selection_ambiguities"].append(record)

    # Detect multi-turn web follow-ups (chronological order per session).
    for turns in sessions_chronological.values():
        turns.sort(key=lambda item: item["created_at"])
        for idx in range(1, len(turns)):
            prev_turn = turns[idx - 1]
            curr_turn = turns[idx]
            prev_used_web = "web-research" in prev_turn["skills"] or bool(
                prev_turn["web_outcomes"]
            )
            if prev_used_web and curr_turn["prompt_shape"]["is_short_followup"]:
                summary["archetypes"]["multi_turn_web_followup"].append(
                    {
                        "prev_skills": prev_turn["skills"],
                        "curr_skills": curr_turn["skills"],
                        "curr_shape": curr_turn["prompt_shape"],
                        "curr_prompt_redacted": curr_turn["prompt_redacted"],
                        "curr_completion": curr_turn["completion"],
                        "curr_web_outcomes": curr_turn["web_outcomes"],
                    }
                )

    # Summarize memory table distributions and edge cases.
    scope_counts: Counter[str] = Counter()
    approval_counts: Counter[str] = Counter()
    source_kind_counts: Counter[str] = Counter()
    normalized_contents: Counter[str] = Counter()
    edge_cases: list[dict[str, Any]] = []

    for (
        mem_id,
        session_id,
        source_run_id,
        skill_name,
        content,
        approved,
        _created_at,
    ) in memory_rows:
        scope = "global" if session_id is None else "session"
        scope_counts[scope] += 1
        approval_counts["approved" if approved else "pending"] += 1
        source_kind_counts[
            "refinement_lesson" if source_run_id else "consolidated_memory"
        ] += 1
        text = str(content or "").strip()
        norm = " ".join(text.casefold().split())
        normalized_contents[norm] += 1

        flags: list[str] = []
        if re.search(
            r"\b(?:error|failed|exception|traceback|unable to)\b", text, re.IGNORECASE
        ):
            flags.append("error_or_refusal_like")
        if re.search(r"\b(?:pending approval|memory candidate)\b", text, re.IGNORECASE):
            flags.append("legacy_status_label")
        if len(text) < 25:
            flags.append("very_short_memory")
        if any(pat.search(text) for _, pat in _SECRET_PATTERNS):
            flags.append("sensitive_pattern_detected")
        if flags:
            edge_cases.append(
                {
                    "id_prefix": str(mem_id)[:8],
                    "scope": scope,
                    "approved": bool(approved),
                    "skill_name": skill_name,
                    "flags": flags,
                    "content_redacted": _redact_text(text, max_chars=120),
                }
            )

    duplicate_clusters = sum(count > 1 for count in normalized_contents.values())
    summary["completion_counts"] = dict(completion_counts)
    summary["status_counts"] = dict(status_counts)
    summary["skill_activation_counts"] = dict(skill_counts)
    summary["memory_stats"] = {
        "scope_counts": dict(scope_counts),
        "approval_counts": dict(approval_counts),
        "source_kind_counts": dict(source_kind_counts),
        "duplicate_content_clusters": duplicate_clusters,
    }
    summary["archetypes"]["memory_edge_cases"] = edge_cases[:40]

    # Cap per-archetype lists so the private summary stays focused and concise.
    for key in (
        "iteration_limit",
        "python_errors",
        "repeated_web_open",
        "web_exhausted_or_truncated",
        "multi_turn_web_followup",
        "skill_selection_ambiguities",
    ):
        items = summary["archetypes"][key]
        summary["archetypes"][f"{key}_count"] = len(items)
        summary["archetypes"][key] = items[:25]

    return summary


def extract_mlflow_archetypes(
    tracking_uri: str,
    *,
    experiments: Sequence[str] = (),
    limit: int = 200,
) -> dict[str, Any]:
    """Inspect recent MLflow experiments and traces in read-only mode."""
    import mlflow
    from mlflow import MlflowClient
    from mlflow.exceptions import MlflowException

    summary: dict[str, Any] = {
        "available": False,
        "tracking_uri": tracking_uri,
        "experiments_scanned": [],
        "traces_inspected": 0,
        "trace_status_counts": {},
        "span_name_counts": {},
        "slow_traces_gt_30s": 0,
        "high_lm_call_traces_ge_10": 0,
        "repeated_web_open_traces": 0,
        "warnings": [],
    }
    try:
        mlflow.set_tracking_uri(tracking_uri)
        client = MlflowClient(tracking_uri=tracking_uri)
        all_experiments = client.search_experiments(max_results=50)
    except (MlflowException, OSError, RuntimeError, ValueError) as exc:
        summary["warnings"].append(f"MLflow unavailable: {type(exc).__name__}: {exc}")
        return summary

    summary["available"] = True
    selected = []
    filter_set = set(experiments)
    for exp in all_experiments:
        if exp.name.startswith("gateway/"):
            continue
        if (
            filter_set
            and exp.name not in filter_set
            and exp.experiment_id not in filter_set
        ):
            continue
        selected.append(exp)
        if not filter_set and len(selected) >= 8:
            break

    trace_statuses: Counter[str] = Counter()
    span_names: Counter[str] = Counter()
    total_traces = 0
    slow_traces = 0
    high_lm_calls = 0
    repeated_web_open_traces = 0

    per_exp_limit = max(10, limit // max(1, len(selected)))
    for exp in selected:
        summary["experiments_scanned"].append(
            {"id": exp.experiment_id, "name": exp.name}
        )
        try:
            traces = client.search_traces(
                locations=[exp.experiment_id],
                max_results=per_exp_limit,
                order_by=["timestamp_ms DESC"],
            )
        except (MlflowException, OSError, RuntimeError, ValueError) as exc:
            summary["warnings"].append(
                f"Failed to scan traces for {exp.name}: {type(exc).__name__}"
            )
            continue

        for trace in traces:
            total_traces += 1
            status = getattr(trace.info, "status", "UNKNOWN")
            trace_statuses[str(status)] += 1
            duration_ms = getattr(trace.info, "execution_duration", None)
            if isinstance(duration_ms, (int, float)) and duration_ms > 30_000:
                slow_traces += 1

            lm_calls = 0
            opened_urls: Counter[str] = Counter()
            spans = getattr(getattr(trace, "data", None), "spans", ()) or ()
            for span in spans:
                span_names[span.name] += 1
                if span.name == "LM.__call__":
                    lm_calls += 1
                if "web_open" in span.name.casefold():
                    inputs = span.inputs if isinstance(span.inputs, Mapping) else {}
                    url = inputs.get("url")
                    if isinstance(url, str) and url:
                        opened_urls[urlsplit(url).netloc + urlsplit(url).path] += 1
            if lm_calls >= 10:
                high_lm_calls += 1
            if any(count > 1 for count in opened_urls.values()):
                repeated_web_open_traces += 1

    summary["traces_inspected"] = total_traces
    summary["trace_status_counts"] = dict(trace_statuses)
    summary["span_name_counts"] = dict(span_names.most_common(25))
    summary["slow_traces_gt_30s"] = slow_traces
    summary["high_lm_call_traces_ge_10"] = high_lm_calls
    summary["repeated_web_open_traces"] = repeated_web_open_traces
    return summary


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = raw.strip()
        if not stripped:
            continue
        parsed = json.loads(stripped)
        if not isinstance(parsed, dict):
            raise TypeError(f"{path}:{line_no}: expected JSON object")
        rows.append(parsed)
    return rows


def _canonical_skills(skills_dir: Path) -> set[str]:
    names = {p.parent.name for p in skills_dir.glob("*/SKILL.md")}
    if not names:
        raise ValueError(f"No bundled skills found under {skills_dir}")
    return names


def _check_disjoint_lineages(
    rows: Sequence[Mapping[str, Any]],
    *,
    allowed_splits: set[str],
    label: str,
) -> dict[str, set[str]]:
    by_split: dict[str, set[str]] = {split: set() for split in allowed_splits}
    for idx, row in enumerate(rows, 1):
        split = row.get("split")
        lineage = row.get("lineage")
        if split not in allowed_splits:
            raise ValueError(f"{label}:{idx}: invalid split {split!r}")
        if not isinstance(lineage, str) or not lineage.strip():
            raise ValueError(f"{label}:{idx}: missing nonempty lineage")
        by_split[split].add(lineage.strip())

    splits = sorted(allowed_splits)
    for i, left in enumerate(splits):
        for right in splits[i + 1 :]:
            overlap = by_split[left] & by_split[right]
            if overlap:
                raise ValueError(
                    f"{label}: lineage overlap between {left} and {right}: {sorted(overlap)}"
                )
    return by_split


def _validate_prior_turns(value: Any, *, label: str) -> None:
    if value is None:
        return
    if not isinstance(value, list):
        raise TypeError(f"{label}: prior_turns must be a list")
    for idx, turn in enumerate(value):
        if (
            not isinstance(turn, dict)
            or not isinstance(turn.get("user"), str)
            or not isinstance(turn.get("assistant"), str)
        ):
            raise TypeError(
                f"{label}: prior_turns[{idx}] must have string 'user' and 'assistant'"
            )


def _norm_clause(text: str) -> str:
    return " ".join(text.casefold().split()).rstrip(".!?")


def _check_disjoint_prior_turns(
    rows: Sequence[Mapping[str, Any]],
    *,
    allowed_splits: set[str],
    label: str,
) -> None:
    """Reject cross-split leakage of prior_turns text or prior_turns matching other-split tasks."""
    prior_texts: dict[str, set[str]] = {split: set() for split in allowed_splits}
    task_texts: dict[str, set[str]] = {split: set() for split in allowed_splits}

    for row in rows:
        split = row["split"]
        task = row.get("task")
        if isinstance(task, str) and task.strip():
            task_texts[split].add(_norm_clause(task))
        for turn in row.get("prior_turns") or ():
            for role in ("user", "assistant"):
                text = turn.get(role)
                if isinstance(text, str) and text.strip():
                    prior_texts[split].add(_norm_clause(text))

    splits = sorted(allowed_splits)
    for i, left in enumerate(splits):
        for right in splits[i + 1 :]:
            direct = prior_texts[left] & prior_texts[right]
            if direct:
                raise ValueError(
                    f"{label}: prior_turns overlap between {left} and {right}: "
                    f"{sorted(direct)[:3]}"
                )
            for src, dst in ((left, right), (right, left)):
                for p_text in prior_texts[src]:
                    if len(p_text) < 25:
                        continue
                    for t_text in task_texts[dst]:
                        if p_text in t_text or (len(t_text) >= 25 and t_text in p_text):
                            raise ValueError(
                                f"{label}: prior_turns in {src} overlaps task in {dst}: "
                                f"{p_text[:80]!r}"
                            )


def _scan_text_for_secrets(files: Iterable[Path]) -> list[str]:
    findings: list[str] = []
    for path in files:
        if not path.is_file():
            continue
        for line_no, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), 1
        ):
            for label, pattern in _SECRET_PATTERNS:
                if pattern.search(line):
                    findings.append(f"{path.name}:{line_no}: matched {label}")
    return findings


def validate_datasets(
    eval_dir: Path,
    *,
    skills_dir: Path = DEFAULT_SKILLS_DIR,
) -> dict[str, Any]:
    """Validate skills.jsonl, memory.jsonl, rlm.jsonl, and fixtures.py integrity."""
    canonical = _canonical_skills(skills_dir)
    if len(canonical) != 8:
        raise ValueError(
            f"Expected 8 canonical bundled skills, found {sorted(canonical)}"
        )

    skills_path = eval_dir / "skills.jsonl"
    memory_path = eval_dir / "memory.jsonl"
    rlm_path = eval_dir / "rlm.jsonl"
    fixtures_path = eval_dir / "fixtures.py"

    for required in (skills_path, memory_path, rlm_path, fixtures_path):
        if not required.is_file():
            raise FileNotFoundError(f"Required dataset artifact missing: {required}")

    # 1. Validate skills.jsonl
    skill_rows = _load_jsonl(skills_path)
    skill_lineages = _check_disjoint_lineages(
        skill_rows, allowed_splits={"train", "holdout"}, label="skills.jsonl"
    )
    skill_dist: dict[str, Counter[str]] = {"train": Counter(), "holdout": Counter()}
    multi_turn_counts: Counter[str] = Counter()
    seen_skill_keys: set[tuple[str, str]] = set()

    for idx, row in enumerate(skill_rows, 1):
        task = row.get("task")
        needs_skill = row.get("needs_skill")
        expected = row.get("expected_skill")
        secondary = row.get("secondary_skills")
        prior_turns = row.get("prior_turns")
        split = row["split"]

        if not isinstance(task, str) or not task.strip():
            raise ValueError(f"skills.jsonl:{idx}: empty task")
        if not isinstance(needs_skill, bool):
            raise TypeError(f"skills.jsonl:{idx}: needs_skill must be bool")
        if needs_skill:
            if expected not in canonical:
                raise ValueError(
                    f"skills.jsonl:{idx}: invalid expected_skill {expected!r}"
                )
        elif expected is not None:
            raise ValueError(
                f"skills.jsonl:{idx}: expected_skill must be null when negative"
            )
        if secondary is not None:
            if not isinstance(secondary, list) or any(
                s not in canonical for s in secondary
            ):
                raise ValueError(
                    f"skills.jsonl:{idx}: invalid secondary_skills {secondary!r}"
                )
            if expected in secondary:
                raise ValueError(
                    f"skills.jsonl:{idx}: secondary_skills repeats expected_skill"
                )
        _validate_prior_turns(prior_turns, label=f"skills.jsonl:{idx}")
        if prior_turns:
            multi_turn_counts["positive" if needs_skill else "negative"] += 1

        dedup_key = (
            task.strip().casefold(),
            json.dumps(prior_turns or [], sort_keys=True),
        )
        if dedup_key in seen_skill_keys:
            raise ValueError(f"skills.jsonl:{idx}: duplicate task+prior_turns")
        seen_skill_keys.add(dedup_key)

        label = expected if needs_skill else "<negative>"
        skill_dist[split][str(label)] += 1

    for split in ("train", "holdout"):
        missing = (canonical | {"<negative>"}) - set(skill_dist[split].keys())
        if missing:
            raise ValueError(
                f"skills.jsonl: split {split!r} missing classes {sorted(missing)}"
            )
    _check_disjoint_prior_turns(
        skill_rows, allowed_splits={"train", "holdout"}, label="skills.jsonl"
    )

    # 2. Validate memory.jsonl
    memory_rows = _load_jsonl(memory_path)
    memory_lineages = _check_disjoint_lineages(
        memory_rows, allowed_splits={"train", "holdout"}, label="memory.jsonl"
    )
    memory_dist: dict[str, Counter[str]] = {"train": Counter(), "holdout": Counter()}
    seen_memory_keys: set[tuple[str, str]] = set()

    for idx, row in enumerate(memory_rows, 1):
        task = row.get("task")
        answer = row.get("answer")
        existing = row.get("existing_memories")
        worth = row.get("worth_saving")
        action = row.get("action")
        scope = row.get("scope")
        split = row["split"]

        if not isinstance(task, str) or not task.strip():
            raise ValueError(f"memory.jsonl:{idx}: empty task")
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError(f"memory.jsonl:{idx}: empty answer")
        if not isinstance(worth, bool):
            raise TypeError(f"memory.jsonl:{idx}: worth_saving must be bool")
        if action not in {"insert", "update", "skip"}:
            raise ValueError(f"memory.jsonl:{idx}: invalid action {action!r}")
        if scope not in {"global", "session"}:
            raise ValueError(f"memory.jsonl:{idx}: invalid scope {scope!r}")
        if worth and action == "skip":
            raise ValueError(
                f"memory.jsonl:{idx}: worth_saving=True cannot have action='skip'"
            )
        if not worth and action != "skip":
            raise ValueError(
                f"memory.jsonl:{idx}: worth_saving=False requires action='skip'"
            )
        if not isinstance(existing, list):
            raise TypeError(f"memory.jsonl:{idx}: existing_memories must be list")
        for m_idx, mem in enumerate(existing):
            if (
                not isinstance(mem, dict)
                or not isinstance(mem.get("id"), str)
                or not isinstance(mem.get("content"), str)
                or mem.get("scope") not in {"global", "session"}
                or not isinstance(mem.get("approved"), bool)
            ):
                raise ValueError(
                    f"memory.jsonl:{idx}: existing_memories[{m_idx}] invalid schema: {mem!r}"
                )
        if action == "update" and not any(m["scope"] == scope for m in existing):
            raise ValueError(
                f"memory.jsonl:{idx}: action='update' needs an existing memory in scope {scope!r}"
            )

        dedup_key = (task.strip().casefold(), answer.strip().casefold())
        if dedup_key in seen_memory_keys:
            raise ValueError(f"memory.jsonl:{idx}: duplicate task+answer")
        seen_memory_keys.add(dedup_key)

        bucket = "skip" if action == "skip" else f"{action}/{scope}"
        memory_dist[split][bucket] += 1

    required_mem_buckets = {
        "insert/global",
        "insert/session",
        "update/global",
        "update/session",
        "skip",
    }
    for split in ("train", "holdout"):
        missing = required_mem_buckets - set(memory_dist[split].keys())
        if missing:
            raise ValueError(
                f"memory.jsonl: split {split!r} missing classes {sorted(missing)}"
            )

    # 3. Validate rlm.jsonl + fixtures.py
    spec = importlib.util.spec_from_file_location("fleet_eval_fixtures", fixtures_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load fixtures module from {fixtures_path}")
    fixtures_mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = fixtures_mod
    spec.loader.exec_module(fixtures_mod)
    if not hasattr(fixtures_mod, "get_case_fixtures"):
        raise ValueError("fixtures.py must define get_case_fixtures(fixture_id: str)")

    rlm_rows = _load_jsonl(rlm_path)
    rlm_lineages = _check_disjoint_lineages(
        rlm_rows, allowed_splits={"train", "val", "holdout"}, label="rlm.jsonl"
    )
    _check_disjoint_prior_turns(
        rlm_rows, allowed_splits={"train", "val", "holdout"}, label="rlm.jsonl"
    )
    seen_ids: set[str] = set()
    rlm_splits: Counter[str] = Counter()
    rlm_verifiers: Counter[str] = Counter()
    rlm_blocking: Counter[str] = Counter()
    rlm_skills: Counter[str] = Counter()
    fixtures_resolved = 0

    for idx, row in enumerate(rlm_rows, 1):
        case_id = row.get("id")
        if not isinstance(case_id, str) or not case_id.strip():
            raise ValueError(f"rlm.jsonl:{idx}: missing id")
        if case_id in seen_ids:
            raise ValueError(f"rlm.jsonl:{idx}: duplicate id {case_id!r}")
        seen_ids.add(case_id)

        for field_name, expected_type in (
            ("task", str),
            ("answer", str),
            ("memories", list),
            ("skill_names", list),
            ("prior_turns", list),
            ("artifact_paths", list),
            ("blocking", bool),
            ("enable_web_tools", bool),
            ("enable_spawn_agent", bool),
            ("verifier", dict),
        ):
            if not isinstance(row.get(field_name), expected_type):
                raise TypeError(f"rlm.jsonl:{idx} ({case_id}): invalid {field_name}")

        if not row["task"].strip():
            raise ValueError(f"rlm.jsonl:{idx} ({case_id}): empty task")
        if any(not isinstance(m, str) for m in row["memories"]):
            raise ValueError(f"rlm.jsonl:{idx} ({case_id}): memories must be list[str]")
        if any(s not in canonical for s in row["skill_names"]):
            raise ValueError(
                f"rlm.jsonl:{idx} ({case_id}): invalid skill_names {row['skill_names']!r}"
            )
        _validate_prior_turns(row["prior_turns"], label=f"rlm.jsonl:{idx} ({case_id})")

        max_actions = row.get("max_expected_actions")
        if max_actions is not None and (
            not isinstance(max_actions, int) or max_actions <= 0
        ):
            raise ValueError(
                f"rlm.jsonl:{idx} ({case_id}): invalid max_expected_actions"
            )

        fixture_id = row.get("fixture_id")
        if fixture_id is not None:
            if not isinstance(fixture_id, str) or not fixture_id.strip():
                raise ValueError(f"rlm.jsonl:{idx} ({case_id}): invalid fixture_id")
            bundle = fixtures_mod.get_case_fixtures(fixture_id)
            if not isinstance(bundle, dict) or set(bundle.keys()) != {"files", "web"}:
                raise ValueError(
                    f"rlm.jsonl:{idx} ({case_id}): fixture {fixture_id!r} "
                    "must return {files, web}"
                )
            files = bundle["files"]
            if not isinstance(files, dict) or any(
                not isinstance(k, str)
                or k.startswith("/")
                or ".." in Path(k).parts
                or not isinstance(v, bytes)
                or len(v) == 0
                for k, v in files.items()
            ):
                raise ValueError(
                    f"rlm.jsonl:{idx} ({case_id}): invalid files in {fixture_id!r}"
                )
            web = bundle["web"]
            if web is not None:
                if (
                    not isinstance(web, dict)
                    or not isinstance(web.get("searches"), dict)
                    or not isinstance(web.get("pages"), dict)
                    or not isinstance(web.get("limit"), int)
                ):
                    raise ValueError(
                        f"rlm.jsonl:{idx} ({case_id}): invalid web in {fixture_id!r}"
                    )
                for q_key, items in web["searches"].items():
                    if not isinstance(q_key, str) or not isinstance(items, list):
                        raise TypeError(
                            f"fixture {fixture_id!r}: malformed search entry"
                        )
                    for item in items:
                        if not isinstance(item, dict) or not {
                            "title",
                            "url",
                            "snippet",
                        } <= set(item.keys()):
                            raise ValueError(
                                f"fixture {fixture_id!r}: malformed search item"
                            )
                for page_url, page_payload in web["pages"].items():
                    if not isinstance(page_url, str) or not isinstance(
                        page_payload, dict
                    ):
                        raise TypeError(f"fixture {fixture_id!r}: malformed page entry")
                    has_ok = isinstance(
                        page_payload.get("markdown"), str
                    ) and isinstance(page_payload.get("truncated"), bool)
                    has_err = isinstance(page_payload.get("error"), str)
                    if not (has_ok or has_err):
                        raise ValueError(
                            f"fixture {fixture_id!r}: page {page_url!r} "
                            "needs markdown+truncated or error"
                        )
            elif row["enable_web_tools"]:
                raise ValueError(
                    f"rlm.jsonl:{idx} ({case_id}): enable_web_tools=True requires web fixture"
                )
            fixtures_resolved += 1
        elif row["enable_web_tools"]:
            raise ValueError(
                f"rlm.jsonl:{idx} ({case_id}): enable_web_tools=True requires fixture_id"
            )

        verifier = row["verifier"]
        vtype = verifier.get("type")
        if vtype not in _ALLOWED_VERIFIER_TYPES:
            raise ValueError(
                f"rlm.jsonl:{idx} ({case_id}): unknown verifier type {vtype!r}"
            )
        if vtype == "artifact":
            if (
                not isinstance(verifier.get("artifacts"), dict)
                or not verifier["artifacts"]
            ):
                raise ValueError(
                    f"rlm.jsonl:{idx} ({case_id}): artifact verifier needs artifacts"
                )
            for apath in row["artifact_paths"]:
                if apath not in verifier["artifacts"]:
                    raise ValueError(
                        f"rlm.jsonl:{idx} ({case_id}): artifact_path {apath!r} missing in verifier"
                    )
        if vtype == "rubric" and (
            not isinstance(verifier.get("criteria"), list) or not verifier["criteria"]
        ):
            raise ValueError(
                f"rlm.jsonl:{idx} ({case_id}): rubric verifier needs criteria"
            )

        rlm_splits[row["split"]] += 1
        rlm_verifiers[vtype] += 1
        if row["blocking"]:
            rlm_blocking[row["split"]] += 1
        for sname in row["skill_names"]:
            rlm_skills[sname] += 1

    # 4. Scan all dataset files for accidental secrets or local private paths
    secret_hits = _scan_text_for_secrets(
        [skills_path, memory_path, rlm_path, fixtures_path]
    )
    if secret_hits:
        raise ValueError(
            f"Sensitive pattern(s) detected in eval_datasets: {secret_hits}"
        )

    return {
        "canonical_skills": sorted(canonical),
        "skills": {
            "total": len(skill_rows),
            "splits": {k: sum(v.values()) for k, v in skill_dist.items()},
            "lineages": {k: len(v) for k, v in skill_lineages.items()},
            "multi_turn": dict(multi_turn_counts),
            "class_distribution": {
                k: dict(sorted(v.items())) for k, v in skill_dist.items()
            },
        },
        "memory": {
            "total": len(memory_rows),
            "splits": {k: sum(v.values()) for k, v in memory_dist.items()},
            "lineages": {k: len(v) for k, v in memory_lineages.items()},
            "class_distribution": {
                k: dict(sorted(v.items())) for k, v in memory_dist.items()
            },
        },
        "rlm": {
            "total": len(rlm_rows),
            "splits": dict(rlm_splits),
            "lineages": {k: len(v) for k, v in rlm_lineages.items()},
            "verifiers": dict(rlm_verifiers),
            "blocking_by_split": dict(rlm_blocking),
            "skill_coverage": dict(sorted(rlm_skills.items())),
            "fixtures_resolved": fixtures_resolved,
        },
        "secret_scan": "clean",
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mlflow-uri",
        default=os.environ.get("FLEET_MLFLOW_TRACKING_URI", DEFAULT_MLFLOW_URI),
        help="Read-only MLflow tracking URI (default: http://127.0.0.1:5001).",
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="Explicit PostgreSQL URL for read-only inspection (defaults to backend/.env).",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=None,
        help="Optional .env file providing FLEET_DATABASE_URL (read-only).",
    )
    parser.add_argument(
        "--experiment",
        action="append",
        default=[],
        help="Optional MLflow experiment name or ID to inspect (repeatable).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=300,
        help="Maximum recent runs/memories/traces to inspect (default: 300).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_PRIVATE_OUTPUT,
        help="Private output JSON path outside Git.",
    )
    parser.add_argument(
        "--validate-datasets",
        type=Path,
        nargs="?",
        const=DEFAULT_EVAL_DIR,
        default=None,
        help="Validate sanitized datasets and fixtures under the given directory.",
    )
    parser.add_argument(
        "--skills-dir",
        type=Path,
        default=DEFAULT_SKILLS_DIR,
        help="Canonical bundled skills directory (default: backend/skills).",
    )
    args = parser.parse_args(argv)

    if args.validate_datasets is not None:
        report = validate_datasets(args.validate_datasets, skills_dir=args.skills_dir)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0

    output_path = _ensure_private_output_path(args.output)
    db_url = _resolve_database_url(args.database_url, args.env_file)

    db_report: dict[str, Any]
    if db_url:
        try:
            db_report = extract_db_archetypes(db_url, limit=args.limit)
        except (ImportError, OSError, RuntimeError, ValueError) as exc:
            db_report = {
                "available": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
    else:
        db_report = {
            "available": False,
            "error": "No FLEET_DATABASE_URL found via --database-url, --env-file, or backend/.env",
        }

    mlflow_report = extract_mlflow_archetypes(
        args.mlflow_uri,
        experiments=args.experiment,
        limit=args.limit,
    )

    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "note": "PRIVATE read-only empirical summary. Never commit this file to Git.",
        "database": db_report,
        "mlflow": mlflow_report,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    # Print aggregate counts only to stdout (no raw private text).
    counts = {
        "output": str(output_path),
        "db_available": db_report.get("available", False),
        "runs_inspected": db_report.get("runs_inspected", 0),
        "memories_inspected": db_report.get("memories_inspected", 0),
        "completion_counts": db_report.get("completion_counts", {}),
        "skill_activation_counts": db_report.get("skill_activation_counts", {}),
        "archetype_counts": {
            k: v
            for k, v in (db_report.get("archetypes") or {}).items()
            if k.endswith("_count")
        },
        "memory_stats": db_report.get("memory_stats", {}),
        "mlflow_available": mlflow_report.get("available", False),
        "mlflow_traces_inspected": mlflow_report.get("traces_inspected", 0),
        "mlflow_slow_traces_gt_30s": mlflow_report.get("slow_traces_gt_30s", 0),
        "mlflow_high_lm_call_traces_ge_10": mlflow_report.get(
            "high_lm_call_traces_ge_10", 0
        ),
        "mlflow_repeated_web_open_traces": mlflow_report.get(
            "repeated_web_open_traces", 0
        ),
    }
    print(json.dumps(counts, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
