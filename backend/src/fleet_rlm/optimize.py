"""Offline GEPA and ReAnchor optimization CLI using isolated Neon branches."""

import argparse
import asyncio
import json
import logging
import math
import random
import sys
import threading
from collections.abc import Callable, Generator, Sequence
from contextlib import AbstractContextManager, contextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol, cast
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import dspy
import mlflow
import mlflow.dspy
from dspy.experimental import ReAnchor
from dspy.teleprompt.gepa.gepa_utils import ScoreWithFeedback
from sqlalchemy import select

from . import agent, db, interpreter, skills
from .config import Settings

_LOG = logging.getLogger(__name__)

TargetKind = Literal["rlm", "skills", "memory"]
ProgramKind = Literal["rlm", "flex"]

DEFAULT_OUTPUTS: dict[TargetKind, Path] = {
    "rlm": Path("artifacts/rlm_program.json"),
    "skills": Path("artifacts/skill_selector.json"),
    "memory": Path("artifacts/memory_gate.json"),
}


class _MlflowTracking(Protocol):
    def set_tracking_uri(self, uri: str) -> None: ...
    def set_experiment(self, experiment_name: str) -> Any: ...
    def start_run(self, *, run_name: str | None = None) -> AbstractContextManager[Any]: ...
    def log_params(self, params: dict[str, Any]) -> None: ...
    def log_metrics(self, metrics: dict[str, float]) -> None: ...
    def log_artifact(self, local_path: str) -> None: ...


def _mlflow_api() -> _MlflowTracking:
    # MLflow exports tracking helpers conditionally for its tracing-only SDK, so access
    # the module through a typed protocol while preserving monkeypatchable module lookups.
    return cast(_MlflowTracking, mlflow)


class DatabaseSafetyError(ValueError):
    """Raised when an optimization run targets the configured primary database."""


@dataclass(frozen=True)
class OptimizationResult:
    target: TargetKind
    output_path: Path
    saved: bool
    baseline_score: float | None
    optimized_score: float | None
    train_size: int
    holdout_size: int
    val_size: int = 0
    reminder: str | None = None
    report: dict[str, Any] = field(default_factory=dict)


def _normalize_database_url(url: str) -> str:
    raw = url.strip()
    if raw.startswith("postgresql://"):
        raw = raw.replace("postgresql://", "postgresql+psycopg://", 1)
    if not raw.startswith("postgresql+psycopg://"):
        raise ValueError("Expected a PostgreSQL URL using psycopg")
    parts = urlsplit(raw)
    query = urlencode(sorted(parse_qsl(parts.query, keep_blank_values=True)))
    netloc = parts.netloc
    if "@" in netloc:
        userinfo, hostport = netloc.rsplit("@", 1)
        netloc = f"{userinfo}@{hostport.lower()}"
    else:
        netloc = netloc.lower()
    return urlunsplit((parts.scheme.lower(), netloc, parts.path, query, ""))


def _endpoint_key(normalized_url: str) -> tuple[str | None, int | None, str]:
    parts = urlsplit(normalized_url)
    return (parts.hostname.lower() if parts.hostname else None, parts.port, parts.path)


def guard_database_url(
    database_url: str,
    settings: Settings,
    *,
    allow_direct: bool = False,
) -> str:
    """Refuse to run against configured primary Neon URLs unless explicitly allowed."""
    normalized = _normalize_database_url(database_url)
    if allow_direct:
        return normalized

    configured = (
        _normalize_database_url(settings.database_url),
        _normalize_database_url(settings.database_direct_url),
    )
    endpoint = _endpoint_key(normalized)
    configured_endpoints = {_endpoint_key(item) for item in configured}
    if normalized in configured or endpoint in configured_endpoints:
        raise DatabaseSafetyError(
            "Refusing to run optimization against configured database_url/database_direct_url. "
            "Pass an ephemeral Neon branch URL or --allow-direct to override."
        )
    return normalized


@contextmanager
def _mlflow_tracking(settings: Settings, target: TargetKind) -> Generator[bool]:
    if not settings.mlflow_tracking_uri:
        yield False
        return
    tracking = _mlflow_api()
    tracking.set_tracking_uri(settings.mlflow_tracking_uri)
    tracking.set_experiment(settings.mlflow_experiment)
    mlflow.dspy.autolog(
        log_compiles=True,
        log_evals=True,
        log_traces_from_compile=True,
    )
    try:
        with tracking.start_run(run_name=f"optimize-{target}"):
            yield True
    finally:
        mlflow.dspy.autolog(disable=True)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
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


def split_examples(
    examples: Sequence[dspy.Example],
    *,
    train_ratio: float = 0.7,
    seed: int = 0,
) -> tuple[list[dspy.Example], list[dspy.Example]]:
    """Deterministically split examples into non-empty train and holdout subsets."""
    if not 0.0 < train_ratio < 1.0:
        raise ValueError("train_ratio must be strictly between 0 and 1")
    if len(examples) < 2:
        raise ValueError("At least 2 examples are required to split train and holdout sets")
    items = list(examples)
    random.Random(seed).shuffle(items)
    split_at = max(1, min(len(items) - 1, round(len(items) * train_ratio)))
    return items[:split_at], items[split_at:]


def split_train_val_holdout(
    examples: Sequence[dspy.Example],
    *,
    train_ratio: float = 0.6,
    val_ratio: float = 0.2,
    seed: int = 0,
) -> tuple[list[dspy.Example], list[dspy.Example], list[dspy.Example]]:
    """Deterministically split examples into non-empty, disjoint train/val/holdout sets."""
    if not math.isfinite(train_ratio) or not math.isfinite(val_ratio):
        raise ValueError("train_ratio and val_ratio must be finite numbers")
    if train_ratio <= 0.0 or val_ratio <= 0.0 or train_ratio + val_ratio >= 1.0:
        raise ValueError(
            "train_ratio and val_ratio must be positive and sum to strictly less than 1"
        )
    total = len(examples)
    if total < 3:
        raise ValueError(
            "At least 3 examples are required to split train, validation, and holdout sets"
        )
    items = list(examples)
    random.Random(seed).shuffle(items)
    train_count = max(1, min(total - 2, round(total * train_ratio)))
    remaining = total - train_count
    val_count = max(1, min(remaining - 1, round(total * val_ratio)))
    return (
        items[:train_count],
        items[train_count : train_count + val_count],
        items[train_count + val_count :],
    )


def load_rlm_dataset(path: Path) -> list[dspy.Example]:
    """Load labeled RLM evaluation examples from a JSONL file."""
    examples: list[dspy.Example] = []
    for index, row in enumerate(_read_jsonl(path), 1):
        task = row.get("task")
        answer = row.get("answer")
        if not isinstance(task, str) or not task.strip() or not isinstance(answer, str):
            raise ValueError(f"{path}:{index}: RLM rows require string 'task' and 'answer'")
        examples.append(
            dspy.Example(
                task=task.strip(),
                files_manifest=list(row.get("files_manifest") or []),
                memories=list(row.get("memories") or []),
                active_skills=str(row.get("active_skills") or ""),
                prior_turns=list(row.get("prior_turns") or []),
                answer=answer,
                artifact_paths=list(row.get("artifact_paths") or []),
            ).with_inputs("task", "files_manifest", "memories", "active_skills", "prior_turns")
        )
    return examples


def _extract_files_manifest(ui_parts: Sequence[dict[str, Any]] | None) -> list[dict[str, Any]]:
    manifest: list[dict[str, Any]] = []
    for part in ui_parts or ():
        if not isinstance(part, dict) or part.get("type") != "data-file-tree":
            continue
        data = part.get("data")
        if isinstance(data, dict) and isinstance(data.get("files"), list):
            manifest = [dict(item) for item in data["files"] if isinstance(item, dict)]
    return manifest


async def load_rlm_examples_from_db(
    database_url: str,
    settings: Settings,
    *,
    limit: int = 200,
) -> list[dspy.Example]:
    """Load completed root runs from an isolated Neon branch database."""
    if limit <= 0:
        raise ValueError("limit must be positive")
    branch_settings = settings.model_copy(
        update={"database_url": database_url, "database_direct_url": database_url}
    )
    engine, sessionmaker = db.create_db(branch_settings)
    try:
        async with sessionmaker() as session:
            runs = (
                await session.scalars(
                    select(db.Run)
                    .where(
                        db.Run.parent_run_id.is_(None),
                        db.Run.status == "completed",
                        db.Run.answer.is_not(None),
                    )
                    .order_by(db.Run.id.desc())
                    .limit(limit)
                )
            ).all()
    finally:
        await engine.dispose()

    examples: list[dspy.Example] = []
    for run in runs:
        if not run.prompt or run.answer is None:
            continue
        examples.append(
            dspy.Example(
                task=run.prompt,
                files_manifest=_extract_files_manifest(run.ui_parts),
                memories=[],
                active_skills="",
                prior_turns=[],
                answer=run.answer,
                artifact_paths=[],
            ).with_inputs("task", "files_manifest", "memories", "active_skills", "prior_turns")
        )
    return examples


def load_skills_dataset(path: Path, registry: skills.SkillRegistry) -> list[dspy.Example]:
    """Load labeled (task, expected_skill) rows from JSONL."""
    known = {skill.name for skill in registry.skills}
    examples: list[dspy.Example] = []
    for index, row in enumerate(_read_jsonl(path), 1):
        task = row.get("task")
        if not isinstance(task, str) or not task.strip():
            raise ValueError(f"{path}:{index}: Skill rows require a nonempty 'task'")
        raw_skill = row.get("expected_skill", row.get("primary_skill"))
        expected_skill = (
            raw_skill.removeprefix("@").strip()
            if isinstance(raw_skill, str) and raw_skill.strip()
            else None
        )
        if expected_skill is not None and expected_skill not in known:
            raise ValueError(f"{path}:{index}: unknown skill {expected_skill!r}")
        needs_skill = (
            bool(row["needs_skill"]) if "needs_skill" in row else expected_skill is not None
        )
        if not needs_skill:
            expected_skill = None
        elif expected_skill is None:
            raise ValueError(f"{path}:{index}: rows with needs_skill=True require 'expected_skill'")
        examples.append(
            dspy.Example(
                task=task.strip(),
                needs_skill=needs_skill,
                expected_skill=expected_skill,
            ).with_inputs("task")
        )
    return examples


def load_memory_dataset(path: Path) -> list[dspy.Example]:
    """Load labeled (turn_exchange, should_save, action, scope) rows from JSONL."""
    examples: list[dspy.Example] = []
    for index, row in enumerate(_read_jsonl(path), 1):
        exchange = row.get("turn_exchange")
        if "task" in row or "answer" in row:
            task = str(row.get("task") or "").strip()
            answer = str(row.get("answer") or "").strip()
        elif isinstance(exchange, dict):
            task = str(exchange.get("task") or "").strip()
            answer = str(exchange.get("answer") or "").strip()
        elif isinstance(exchange, str):
            task = exchange.strip()
            answer = ""
        else:
            task, answer = "", ""
        if not task:
            raise ValueError(f"{path}:{index}: Memory rows require 'task' or 'turn_exchange'")

        if "worth_saving" in row:
            worth_saving = bool(row["worth_saving"])
        elif "should_save" in row:
            worth_saving = bool(row["should_save"])
        else:
            raise ValueError(f"{path}:{index}: Memory rows require 'worth_saving' or 'should_save'")

        action = str(row.get("action") or ("insert" if worth_saving else "skip")).strip()
        if action not in {"insert", "update", "skip"}:
            raise ValueError(f"{path}:{index}: invalid action {action!r}")
        scope = str(row.get("scope") or "session").strip()
        if scope not in {"global", "session"}:
            raise ValueError(f"{path}:{index}: invalid scope {scope!r}")

        examples.append(
            dspy.Example(
                task=task,
                answer=answer,
                existing_memories=list(row.get("existing_memories") or []),
                worth_saving=worth_saving,
                action=action,
                scope=scope,
            ).with_inputs("task", "answer", "existing_memories")
        )
    return examples


def _normalize_text(text: str) -> str:
    return " ".join(text.split()).casefold()


def rlm_metric(
    gold: dspy.Example,
    pred: Any,
    trace: Any = None,
    pred_name: str | None = None,
    pred_trace: Any = None,
    program_trace: Any = None,
) -> ScoreWithFeedback:
    """GEPA feedback metric comparing normalized answers with token-F1 partial credit."""
    expected_raw = str(getattr(gold, "answer", "") or "").strip()
    actual_raw = str(getattr(pred, "answer", "") or "").strip()
    expected = _normalize_text(expected_raw)
    actual = _normalize_text(actual_raw)

    if expected == actual and expected:
        return ScoreWithFeedback(score=1.0, feedback="Exact answer match.")
    if not expected or not actual:
        return ScoreWithFeedback(
            score=0.0,
            feedback=f"Expected {expected_raw!r}, got {actual_raw!r} (empty answer).",
        )

    expected_tokens = expected.split()
    actual_tokens = actual.split()
    expected_counts: dict[str, int] = {}
    for token in expected_tokens:
        expected_counts[token] = expected_counts.get(token, 0) + 1
    overlap = 0
    for token in actual_tokens:
        remaining = expected_counts.get(token, 0)
        if remaining > 0:
            overlap += 1
            expected_counts[token] = remaining - 1
    if overlap == 0:
        score = 0.0
    else:
        precision = overlap / len(actual_tokens)
        recall = overlap / len(expected_tokens)
        score = 2 * precision * recall / (precision + recall)
    return ScoreWithFeedback(
        score=score,
        feedback=f"Expected {expected_raw!r}, got {actual_raw!r} (token F1={score:.2f}).",
    )


_MISSING = object()


def _decision_value(value: Any) -> Any:
    return getattr(value, "value", value)


def skill_metric(gold: dspy.Example, pred: Any, trace: Any = None) -> float:
    """Binary accuracy for SkillSelector (needs_skill, plus primary_skill when present)."""
    pred_needs = bool(_decision_value(getattr(pred, "needs_skill", False)))
    gold_needs = bool(getattr(gold, "needs_skill", True))
    if not gold_needs:
        return 1.0 if not pred_needs else 0.0
    raw_primary = getattr(pred, "primary_skill", _MISSING)
    if raw_primary is _MISSING:
        return 1.0 if pred_needs else 0.0
    pred_skill = _decision_value(raw_primary)
    if isinstance(pred_skill, str):
        pred_skill = pred_skill.removeprefix("@").strip()
    return 1.0 if pred_needs and pred_skill == getattr(gold, "expected_skill", None) else 0.0


def memory_metric(gold: dspy.Example, pred: Any, trace: Any = None) -> float:
    """Weighted accuracy for MemoryGate (worth_saving, action, scope)."""
    pred_worth = bool(_decision_value(getattr(pred, "worth_saving", False)))
    pred_action = _decision_value(getattr(pred, "action", None))
    pred_scope = _decision_value(getattr(pred, "scope", None))
    gold_worth = bool(getattr(gold, "worth_saving", False))

    if not gold_worth:
        return 1.0 if (not pred_worth or pred_action == "skip") else 0.0
    if not pred_worth:
        return 0.0
    score = 0.5
    if pred_action == getattr(gold, "action", None):
        score += 0.25
    if pred_scope == getattr(gold, "scope", None):
        score += 0.25
    return score


def _clear_predictor_lms(module: Any) -> None:
    if hasattr(module, "lm"):
        module.lm = None
    if hasattr(module, "predictors"):
        for predictor in module.predictors():
            if hasattr(predictor, "lm"):
                predictor.lm = None


def _save_program(module: Any, output_path: Path) -> None:
    _clear_predictor_lms(module)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    module.save(output_path)


def _eval_score(result: Any) -> float:
    return float(result.score if hasattr(result, "score") else result)


def _pin_reminder(env_var: str, output_path: Path, decision_model: str) -> str:
    return (
        f"IMPORTANT: Before deploying {output_path} via {env_var}, pin the exact "
        f"TypeSafe Jev model snapshot in FLEET_DECISION_MODEL (currently {decision_model!r}; "
        "do not keep floating 'typesafe:jev-latest') so decision probabilities match calibrated "
        "thresholds."
    )


@contextmanager
def _ephemeral_daytona_interpreter(
    settings: Settings,
) -> Generator[Callable[..., interpreter.DaytonaCodeInterpreter]]:
    """Run an ephemeral Daytona sandbox on a background event loop and always delete it."""
    loop = asyncio.new_event_loop()
    ready = threading.Event()
    release_event: asyncio.Event | None = None
    factory: Callable[..., interpreter.DaytonaCodeInterpreter] | None = None
    startup_error: BaseException | None = None
    cleanup_error: BaseException | None = None

    async def lifecycle() -> None:
        nonlocal release_event, factory, startup_error, cleanup_error
        release_event = asyncio.Event()
        try:
            async with (
                interpreter.make_daytona_client(settings) as daytona,
                interpreter.ephemeral_sandbox(daytona, settings) as sandbox,
            ):
                factory = interpreter.make_interpreter_factory(sandbox, settings, loop)
                ready.set()
                await release_event.wait()
        except BaseException as error:
            if not ready.is_set():
                startup_error = error
                ready.set()
            else:
                cleanup_error = error

    def run_loop() -> None:
        nonlocal startup_error, cleanup_error
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(lifecycle())
        except BaseException as error:
            if not ready.is_set():
                startup_error = error
            else:
                cleanup_error = error
        finally:
            ready.set()
            try:
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                with suppress(Exception):
                    loop.run_until_complete(loop.shutdown_asyncgens())
                with suppress(Exception):
                    loop.run_until_complete(loop.shutdown_default_executor())
            finally:
                loop.close()

    thread = threading.Thread(target=run_loop, name="fleet-optimize-daytona", daemon=True)
    thread.start()
    ready.wait()
    if startup_error is not None:
        thread.join()
        raise startup_error
    if factory is None:
        thread.join()
        raise RuntimeError("Failed to initialize ephemeral Daytona interpreter factory")

    body_failed = False
    try:
        yield factory
    except BaseException:
        body_failed = True
        raise
    finally:
        if release_event is not None and not loop.is_closed():
            with suppress(RuntimeError):
                loop.call_soon_threadsafe(release_event.set)
        thread.join()
        if cleanup_error is not None:
            if body_failed:
                _LOG.warning(
                    "Failed to tear down ephemeral Daytona sandbox after optimization error",
                    exc_info=cleanup_error,
                )
            else:
                raise cleanup_error


@contextmanager
def _rlm_execution_context(
    settings: Settings,
    root_lm: dspy.BaseLM,
    *,
    program_kind: ProgramKind,
    local_interpreter: bool,
) -> Generator[None]:
    if program_kind == "rlm" and not local_interpreter:
        with (
            _ephemeral_daytona_interpreter(settings) as interpreter_factory,
            dspy.context(lm=root_lm, interpreter_factory=interpreter_factory),
        ):
            yield
        return
    with dspy.context(lm=root_lm):
        yield


def optimize_rlm(
    settings: Settings,
    *,
    database_url: str,
    dataset: Path | None = None,
    output_path: Path | None = None,
    program_kind: ProgramKind = "rlm",
    train_ratio: float = 0.6,
    val_ratio: float | None = None,
    seed: int = 0,
    auto: Literal["light", "medium", "heavy"] = "light",
    num_threads: int | None = None,
    limit: int = 200,
    force: bool = False,
    allow_direct: bool = False,
    local_interpreter: bool = False,
    log_dir: Path | None = None,
) -> OptimizationResult:
    """Compile dspy.RLM or dspy.Flex with GEPA and save only if holdout improves (or --force)."""
    safe_url = guard_database_url(database_url, settings, allow_direct=allow_direct)
    examples = (
        load_rlm_dataset(dataset)
        if dataset is not None
        else asyncio.run(load_rlm_examples_from_db(safe_url, settings, limit=limit))
    )
    if not examples:
        raise ValueError("No labeled RLM examples found")
    resolved_val_ratio = val_ratio if val_ratio is not None else (1.0 - train_ratio) / 2.0
    trainset, valset, holdout = split_train_val_holdout(
        examples,
        train_ratio=train_ratio,
        val_ratio=resolved_val_ratio,
        seed=seed,
    )

    target_path = output_path or DEFAULT_OUTPUTS["rlm"]
    root_lm = agent._model(settings, settings.llm_model)

    with _mlflow_tracking(settings, "rlm") as mlflow_active:
        with _rlm_execution_context(
            settings,
            root_lm,
            program_kind=program_kind,
            local_interpreter=local_interpreter,
        ):
            student: dspy.Module = (
                dspy.Flex(agent.WorkspaceTask)
                if program_kind == "flex"
                else agent.build_program(settings, [])
            )
            evaluator = dspy.Evaluate(
                devset=holdout,
                metric=rlm_metric,
                num_threads=num_threads,
                display_progress=False,
            )
            baseline_score = _eval_score(evaluator(student))
            gepa = dspy.GEPA(
                metric=rlm_metric,
                auto=auto,
                reflection_lm=root_lm,
                num_threads=num_threads,
                log_dir=str(log_dir) if log_dir else None,
                seed=seed,
            )
            compiled = gepa.compile(student, trainset=trainset, valset=valset)
            optimized_score = _eval_score(evaluator(compiled))

        saved = optimized_score > baseline_score or force
        if saved:
            _save_program(compiled, target_path)

        if mlflow_active:
            tracking = _mlflow_api()
            tracking.log_params(
                {
                    "target": "rlm",
                    "program_kind": program_kind,
                    "auto": auto,
                    "train_size": len(trainset),
                    "val_size": len(valset),
                    "holdout_size": len(holdout),
                    "local_interpreter": local_interpreter,
                    "force": force,
                }
            )
            tracking.log_metrics(
                {
                    "baseline_holdout_score": baseline_score,
                    "optimized_holdout_score": optimized_score,
                    "saved": 1.0 if saved else 0.0,
                }
            )
            if saved:
                tracking.log_artifact(str(target_path))

    return OptimizationResult(
        target="rlm",
        output_path=target_path,
        saved=saved,
        baseline_score=baseline_score,
        optimized_score=optimized_score,
        train_size=len(trainset),
        holdout_size=len(holdout),
        val_size=len(valset),
    )


def _run_reanchor(
    settings: Settings,
    *,
    target: Literal["skills", "memory"],
    database_url: str,
    examples: Sequence[dspy.Example],
    predictor: dspy.Module,
    metric: Any,
    output_path: Path,
    env_var: str,
    train_ratio: float,
    seed: int,
    num_threads: int | None,
    allow_direct: bool,
    log_dir: Path | None,
    require_cache: bool,
) -> OptimizationResult:
    guard_database_url(database_url, settings, allow_direct=allow_direct)
    if len(examples) >= 2:
        trainset, holdout = split_examples(examples, train_ratio=train_ratio, seed=seed)
    else:
        trainset, holdout = list(examples), None

    with (
        _mlflow_tracking(settings, target) as mlflow_active,
        dspy.context(lm=dspy.LM(settings.decision_model)),
    ):
        reanchor = ReAnchor(
            metric,
            num_threads=num_threads,
            log_dir=log_dir,
            require_cache=require_cache,
        )
        compiled = reanchor.compile(predictor, trainset=trainset, valset=holdout)
        report = dict(reanchor.report)
        _save_program(compiled, output_path)

        baseline_score = report.get("val_score_before", report.get("train_score_before"))
        optimized_score = report.get("val_score", report.get("train_score"))

        if mlflow_active:
            tracking = _mlflow_api()
            tracking.log_params(
                {
                    "target": target,
                    "decision_model": settings.decision_model,
                    "train_size": len(trainset),
                    "holdout_size": len(holdout) if holdout else 0,
                }
            )
            metrics: dict[str, float] = {}
            if isinstance(baseline_score, (int, float)):
                metrics["baseline_score"] = float(baseline_score)
            if isinstance(optimized_score, (int, float)):
                metrics["optimized_score"] = float(optimized_score)
            if metrics:
                tracking.log_metrics(metrics)
            tracking.log_artifact(str(output_path))

    return OptimizationResult(
        target=target,
        output_path=output_path,
        saved=True,
        baseline_score=float(baseline_score) if isinstance(baseline_score, (int, float)) else None,
        optimized_score=(
            float(optimized_score) if isinstance(optimized_score, (int, float)) else None
        ),
        train_size=len(trainset),
        holdout_size=len(holdout) if holdout else 0,
        reminder=_pin_reminder(env_var, output_path, settings.decision_model),
        report=report,
    )


def optimize_skills(
    settings: Settings,
    *,
    database_url: str,
    dataset: Path,
    output_path: Path | None = None,
    train_ratio: float = 0.7,
    seed: int = 0,
    num_threads: int | None = None,
    allow_direct: bool = False,
    log_dir: Path | None = None,
    require_cache: bool = True,
) -> OptimizationResult:
    """Calibrate SkillSelector thresholds and Choice weights via 5-fold ReAnchor CV."""
    registry = skills.SkillRegistry.load(settings.skills_dir, fallback_to_bundled=True)
    if not registry.skills:
        raise ValueError(f"No skills found in {settings.skills_dir}")
    examples = load_skills_dataset(dataset, registry)
    if not examples:
        raise ValueError(f"No skill examples found in {dataset}")
    return _run_reanchor(
        settings,
        target="skills",
        database_url=database_url,
        examples=examples,
        predictor=skills.build_skill_selector(registry.skills),
        metric=skill_metric,
        output_path=output_path or DEFAULT_OUTPUTS["skills"],
        env_var="FLEET_SKILL_SELECTOR_PATH",
        train_ratio=train_ratio,
        seed=seed,
        num_threads=num_threads,
        allow_direct=allow_direct,
        log_dir=log_dir,
        require_cache=require_cache,
    )


def optimize_memory(
    settings: Settings,
    *,
    database_url: str,
    dataset: Path,
    output_path: Path | None = None,
    train_ratio: float = 0.7,
    seed: int = 0,
    num_threads: int | None = None,
    allow_direct: bool = False,
    log_dir: Path | None = None,
    require_cache: bool = True,
) -> OptimizationResult:
    """Calibrate MemoryGate thresholds and choice weights via 5-fold ReAnchor CV."""
    examples = load_memory_dataset(dataset)
    if not examples:
        raise ValueError(f"No memory examples found in {dataset}")
    return _run_reanchor(
        settings,
        target="memory",
        database_url=database_url,
        examples=examples,
        predictor=dspy.Predict(agent.MemoryGate),
        metric=memory_metric,
        output_path=output_path or DEFAULT_OUTPUTS["memory"],
        env_var="FLEET_MEMORY_GATE_PATH",
        train_ratio=train_ratio,
        seed=seed,
        num_threads=num_threads,
        allow_direct=allow_direct,
        log_dir=log_dir,
        require_cache=require_cache,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fleet_rlm.optimize",
        description="Offline GEPA and ReAnchor optimizer for Fleet-RLM artifacts.",
    )
    parser.add_argument(
        "--target",
        required=True,
        choices=("rlm", "skills", "memory"),
        help="Optimization target to run.",
    )
    parser.add_argument(
        "--database-url",
        required=True,
        help="Ephemeral Neon branch Postgres URL (refuses primary URLs unless --allow-direct).",
    )
    parser.add_argument(
        "--allow-direct",
        action="store_true",
        help="Allow running against the configured database_url / database_direct_url.",
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=None,
        help="Path to JSONL evaluation dataset (required for skills and memory).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output artifact path (defaults to artifacts/<target>.json).",
    )
    parser.add_argument(
        "--program",
        choices=("rlm", "flex"),
        default="rlm",
        help="Program module to compile for --target rlm.",
    )
    parser.add_argument(
        "--train-ratio",
        type=float,
        default=None,
        help="Train split ratio (default: 0.6 for rlm, 0.7 for skills/memory).",
    )
    parser.add_argument(
        "--val-ratio",
        type=float,
        default=None,
        help=(
            "Validation split ratio for --target rlm "
            "(default: half of remaining examples after --train-ratio)."
        ),
    )
    parser.add_argument("--seed", type=int, default=0, help="Random seed for split/GEPA.")
    parser.add_argument(
        "--auto",
        choices=("light", "medium", "heavy"),
        default="light",
        help="GEPA auto budget for --target rlm.",
    )
    parser.add_argument("--num-threads", type=int, default=None, help="Evaluation thread count.")
    parser.add_argument(
        "--limit",
        type=int,
        default=200,
        help="Maximum completed root runs to load from Neon when --dataset is omitted.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Save RLM artifact even if holdout score does not beat baseline.",
    )
    parser.add_argument(
        "--local-interpreter",
        action="store_true",
        help=(
            "Skip ephemeral Daytona sandbox provisioning for --target rlm --program rlm "
            "and use DSPy's local interpreter."
        ),
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=None,
        help="Optional directory for GEPA / ReAnchor logs and reports.",
    )
    return parser


def main(argv: Sequence[str] | None = None, *, settings: Settings | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.target in {"skills", "memory"} and args.dataset is None:
        parser.error(f"--dataset is required for --target {args.target}")

    resolved_settings = settings or Settings.model_validate({})
    try:
        guard_database_url(args.database_url, resolved_settings, allow_direct=args.allow_direct)
        if args.target == "rlm":
            train_ratio = 0.6 if args.train_ratio is None else args.train_ratio
            result = optimize_rlm(
                resolved_settings,
                database_url=args.database_url,
                dataset=args.dataset,
                output_path=args.output,
                program_kind=args.program,
                train_ratio=train_ratio,
                val_ratio=args.val_ratio,
                seed=args.seed,
                auto=args.auto,
                num_threads=args.num_threads,
                limit=args.limit,
                force=args.force,
                allow_direct=args.allow_direct,
                local_interpreter=args.local_interpreter,
                log_dir=args.log_dir,
            )
        elif args.target == "skills":
            train_ratio = 0.7 if args.train_ratio is None else args.train_ratio
            result = optimize_skills(
                resolved_settings,
                database_url=args.database_url,
                dataset=args.dataset,
                output_path=args.output,
                train_ratio=train_ratio,
                seed=args.seed,
                num_threads=args.num_threads,
                allow_direct=args.allow_direct,
                log_dir=args.log_dir,
            )
        else:
            train_ratio = 0.7 if args.train_ratio is None else args.train_ratio
            result = optimize_memory(
                resolved_settings,
                database_url=args.database_url,
                dataset=args.dataset,
                output_path=args.output,
                train_ratio=train_ratio,
                seed=args.seed,
                num_threads=args.num_threads,
                allow_direct=args.allow_direct,
                log_dir=args.log_dir,
            )
    except DatabaseSafetyError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2

    status = f"saved to {result.output_path}" if result.saved else "discarded (no holdout gain)"
    val_segment = f" val={result.val_size}" if result.val_size else ""
    print(
        f"[{result.target}] train={result.train_size}{val_segment} holdout={result.holdout_size} "
        f"baseline={result.baseline_score} optimized={result.optimized_score} -> {status}"
    )
    if result.reminder:
        print(result.reminder)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
