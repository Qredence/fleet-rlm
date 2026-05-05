#!/usr/bin/env python3
"""LongCoT benchmark runner for fleet-rlm.

Runs the LongCoT long-horizon reasoning benchmark against Qwen3.6-Flash
via Alibaba DashScope, then evaluates results.

Two modes:
  - direct: Calls DashScope directly via LongCoT's inference pipeline
  - rlm:    Routes questions through fleet-rlm's recursive RLM pipeline

Usage:
    uv run python scripts/run_longcot_eval.py --mode direct --difficulty longcot-mini
    uv run python scripts/run_longcot_eval.py --mode direct --difficulty easy --domain logic --max-questions 5
    uv run python scripts/run_longcot_eval.py --mode rlm --difficulty longcot-mini --domain logic --max-tasks 10

Requires:
    - ALIBABA_API_KEY set in .env
    - vendor/longcot/ cloned and installed (uv sync)
    - For rlm mode: DAYTONA_API_KEY, DAYTONA_API_URL set
"""

from __future__ import annotations

import argparse
import ast
import functools
import importlib.util
import json
import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
VENDOR_LONGCOT = ROOT / "vendor" / "longcot"
LONGCOT_SRC = VENDOR_LONGCOT / "src"

sys.path.insert(0, str(ROOT / "src"))

load_dotenv(ROOT / ".env")

DASHSCOPE_BASE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"

# Domain-specific format reminders injected into RLM prompts so the model knows
# what SUBMIT expects for each benchmark domain.  The BlocksWorld move schema
# applies only to the 'logic' domain; other domains receive a generic reminder.
_LONGCOT_FORMAT_REMINDER_BLOCKSWORLD = (
    "\n\nIMPORTANT: Your final answer MUST be submitted using SUBMIT() "
    "with the answer formatted EXACTLY as:\n  solution = [[block, from_stack, to_stack], ...]\n"
    "Each move is a list of three integers: [block_id, source_stack, destination_stack].\n"
    "Do not add any explanation around it. The solution field must be a Python list literal."
)
_LONGCOT_FORMAT_REMINDER_GENERIC = (
    "\n\nIMPORTANT: Submit your final answer using SUBMIT() with the answer "
    "in the solution field. Do not include extra explanation outside the submitted value."
)
# Per-domain mapping; unmapped domains fall back to the generic reminder.
_LONGCOT_DOMAIN_FORMAT_REMINDERS: dict[str, str] = {
    "logic": _LONGCOT_FORMAT_REMINDER_BLOCKSWORLD,
}

_RLM_FAILURE_MARKERS = (
    "adapterparseerror",
    "blocked by sandbox limitations",
    "broker server failed",
    "child_error",
    "failed to parse the lm response",
    "failed to inject tool",
    "lm response cannot be serialized to a json object",
    "needs_human_review",
    "requires human review",
    "sandbox limitations",
    "tool_error",
    "unable to create sandboxes",
    "unverified",
    "expected to find output fields",
    "verification blocked",
)

_INFRA_FAILURE_MARKERS = (
    "websocket",
    "http 401",
    "authentication failure",
    "broker server failed",
    "unable to create sandboxes",
    "sandbox limitations",
    "blocked by sandbox limitations",
)

_SLICE_DOMAIN_ORDER = ("logic", "cs", "chemistry", "chess", "math")


def _extract_solution(text: str) -> str:
    """Extract 'solution = [...]' from a verbose RLM answer, if present."""
    candidate = _extract_solution_candidate(text)
    return candidate if candidate is not None else text


def _extract_solution_candidate(text: str) -> str | None:
    """Return a complete ``solution = [...]`` candidate or ``None``."""
    match = re.search(r"solution\s*=\s*\[", text, re.IGNORECASE)
    if not match:
        return None

    depth = 0
    start = match.start()
    for index in range(match.end() - 1, len(text)):
        char = text[index]
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return text[start : index + 1].strip()
    return None


def _contains_rlm_failure_text(text: str) -> bool:
    """Return whether output contains a known child/adapter failure marker."""
    normalized = text.lower()
    return any(marker in normalized for marker in _RLM_FAILURE_MARKERS)


def _contains_infra_failure_text(text: str) -> bool:
    """Return whether output contains an infrastructure failure marker.

    Infra failures (WebSocket, auth, sandbox creation) are distinct from
    semantic failures and should not be recycled into planner/repair context.
    """
    normalized = text.lower()
    return any(marker in normalized for marker in _INFRA_FAILURE_MARKERS)


class _LocalEvidenceSink:
    """In-memory evidence sink for benchmark runs without a host repository.

    Satisfies the :class:`~fleet_rlm.runtime.modules.evidence.EvidenceSink`
    protocol so ``RecursiveWorkspaceModule`` can persist cross-pass evidence
    even when the websocket session layer (and its Neon-backed bridge) is not
    present.
    """

    def __init__(self) -> None:
        self._items: dict[str, dict[str, Any]] = {}

    def store(
        self,
        *,
        key: str,
        content: str,
        kind: str = "context",
        scope: str = "run",
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        self._items[key] = {
            "id": key,
            "scope_id": key,
            "content": str(content),
            "kind": str(kind),
            "scope": str(scope),
            "tags": list(tags or []),
        }
        return {"status": "ok", "id": key, "key": key}

    def list_items(self, *, scope: str = "run", limit: int = 50) -> dict[str, Any]:
        items = [
            {"id": v["id"], "scope_id": v["scope_id"], "kind": v["kind"]}
            for v in list(self._items.values())[-limit:]
        ]
        return {"status": "ok", "items": items}


def _extract_balanced_list_after(
    text: str, label: str, *, start_at: int = 0
) -> str | None:
    label_index = text.find(label, start_at)
    if label_index == -1:
        return None
    start = text.find("[", label_index)
    if start == -1:
        return None
    depth = 0
    for index in range(start, len(text)):
        char = text[index]
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def _parse_blocks_world_prompt(prompt: str) -> dict[str, Any] | None:
    """Extract BlocksWorld initial/goal states from a LongCoT prompt."""
    puzzle_start = prompt.find("Puzzle instance:")
    if puzzle_start == -1:
        puzzle_start = 0
    initial_literal = _extract_balanced_list_after(
        prompt, "Initial state:", start_at=puzzle_start
    )
    goal_literal = _extract_balanced_list_after(
        prompt, "Goal state:", start_at=puzzle_start
    )
    if initial_literal is None or goal_literal is None:
        return None
    try:
        initial_state = ast.literal_eval(initial_literal)
        goal_state = ast.literal_eval(goal_literal)
    except (SyntaxError, ValueError):
        return None
    if not _is_state(initial_state) or not _is_state(goal_state):
        return None
    stacks_match = re.search(r"Number of stacks:\s*(\d+)", prompt[puzzle_start:])
    stack_count = int(stacks_match.group(1)) if stacks_match else len(initial_state)
    return {
        "initial": [list(stack) for stack in initial_state],
        "goal": [list(stack) for stack in goal_state],
        "stack_count": stack_count,
    }


def _is_state(value: Any) -> bool:
    return isinstance(value, list) and all(
        isinstance(stack, list)
        and all(
            isinstance(block, int) and not isinstance(block, bool) for block in stack
        )
        for stack in value
    )


def _parse_solution_literal(candidate: str) -> list[Any] | None:
    _, _, literal = candidate.partition("=")
    if not literal:
        return None
    try:
        parsed = ast.literal_eval(literal.strip())
    except (SyntaxError, ValueError):
        return None
    return parsed if isinstance(parsed, list) else None


def _validate_blocksworld_solution(prompt: str, candidate: str) -> tuple[bool, str]:
    puzzle = _parse_blocks_world_prompt(prompt)
    if puzzle is None:
        return False, "Could not parse BlocksWorld puzzle instance from prompt."
    moves = _parse_solution_literal(candidate)
    if moves is None:
        return False, "Solution candidate is not a Python list literal."

    state = [list(stack) for stack in puzzle["initial"]]
    goal = [list(stack) for stack in puzzle["goal"]]
    stack_count = int(puzzle["stack_count"])
    if len(state) != stack_count or len(goal) != stack_count:
        return False, "Parsed stack count does not match prompt metadata."
    if not moves and state != goal:
        return False, "Empty solution does not transform initial state to goal state."

    for index, move in enumerate(moves):
        if (
            not isinstance(move, (list, tuple))
            or len(move) != 3
            or any(isinstance(item, bool) or not isinstance(item, int) for item in move)
        ):
            return False, f"Move {index} must be [block, from_stack, to_stack]."
        block, from_stack, to_stack = move
        if not (0 <= from_stack < stack_count and 0 <= to_stack < stack_count):
            return False, f"Move {index} references an invalid stack index."
        if from_stack == to_stack:
            return False, f"Move {index} moves to the same stack."
        if not state[from_stack]:
            return False, f"Move {index} moves from an empty stack."
        top = state[from_stack][-1]
        if top != block:
            return False, (
                f"Move {index} claims block {block}, but top of stack "
                f"{from_stack} is {top}."
            )
        state[from_stack].pop()
        state[to_stack].append(block)

    if state != goal:
        return False, "Solution moves do not reach the goal state."
    return True, "valid"


def _evaluate_rlm_answer(prompt: str, raw_answer: str) -> tuple[str, str, str | None]:
    """Return ``(status, answer, error)`` for a raw recursive RLM answer."""
    candidate = _extract_solution_candidate(raw_answer)
    if _contains_rlm_failure_text(raw_answer):
        return "error", raw_answer, "RLM output contained runtime failure text."
    if candidate is None:
        return (
            "error",
            raw_answer,
            "RLM output did not contain a complete solution = [...] candidate.",
        )
    valid, validation_error = _validate_blocksworld_solution(prompt, candidate)
    if not valid:
        return "error", candidate, validation_error
    # Valid answer, but flag if infrastructure failures tainted the output.
    if _contains_infra_failure_text(raw_answer):
        return (
            "ok_degraded",
            candidate,
            "Answer valid but infrastructure failures were reported during generation.",
        )
    return "ok", candidate, None


def _ensure_longcot():
    if not VENDOR_LONGCOT.exists():
        print(f"ERROR: LongCoT not found at {VENDOR_LONGCOT}")
        print(
            "Run: git clone https://github.com/LongHorizonReasoning/longcot.git vendor/longcot"
        )
        sys.exit(1)
    if not (VENDOR_LONGCOT / ".venv").exists():
        print(f"ERROR: LongCoT venv not found. Run: cd {VENDOR_LONGCOT} && uv sync")
        sys.exit(1)


def _ensure_api_key() -> str:
    key = os.environ.get("ALIBABA_API_KEY")
    if not key:
        print("ERROR: ALIBABA_API_KEY not set in environment or .env")
        sys.exit(1)
    return key


@functools.lru_cache(maxsize=1)
def _load_vendor_run_inference_module() -> ModuleType:
    """Dynamically load and cache LongCoT's inference runner for config/provider helpers."""
    script = VENDOR_LONGCOT / "run_inference.py"
    spec = importlib.util.spec_from_file_location(
        "vendor_longcot_run_inference", script
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load LongCoT inference module from {script}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_longcot_config(
    config_name: str,
    *,
    resolve_env: bool = True,
) -> tuple[dict[str, Any], Path]:
    """Load a vendor/longcot config by name."""
    module = _load_vendor_run_inference_module()
    path = Path(module.find_config(config_name))
    cfg = module.load_config(path, resolve_env=resolve_env)
    return dict(cfg), path


def _infer_provider(*, model_name: str, config_name: str | None = None) -> str:
    """Infer the provider label for summaries and artifacts."""
    if config_name:
        try:
            cfg, _ = _load_longcot_config(config_name, resolve_env=False)
        except Exception:
            cfg = {}
        provider = str(cfg.get("provider", "")).strip().lower()
        if provider:
            return provider

    normalized = model_name.lower()
    if normalized.startswith("bedrock/") or "bedrock" in normalized:
        return "bedrock"
    if normalized.startswith("openrouter/") or normalized.startswith("deepseek/"):
        return "openrouter"
    if normalized.startswith("openai/") or "qwen" in normalized:
        return "openai"
    return "unknown"


def _load_slice_manifest(slice_file: Path | None) -> dict[str, Any] | None:
    """Load an optional question-slice manifest."""
    if slice_file is None:
        return None
    data = json.loads(slice_file.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Slice manifest must be a JSON object.")
    domains = data.get("domains")
    if not isinstance(domains, dict) or not domains:
        raise ValueError("Slice manifest must contain a non-empty 'domains' object.")
    return data


def _select_questions_for_slice(
    questions: list[dict[str, Any]],
    slice_manifest: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Return questions filtered and ordered by an optional manifest."""
    if slice_manifest is None:
        return questions

    question_by_id = {str(q["question_id"]): q for q in questions}
    selected: list[dict[str, Any]] = []
    missing: list[str] = []

    domains = slice_manifest.get("domains", {})
    for domain_name in _SLICE_DOMAIN_ORDER:
        ids = domains.get(domain_name, [])
        if not isinstance(ids, list):
            raise ValueError(
                f"Slice manifest domain '{domain_name}' must map to a list of question IDs."
            )
        for question_id in ids:
            question = question_by_id.get(str(question_id))
            if question is None:
                missing.append(str(question_id))
                continue
            selected.append(question)

    if missing:
        raise ValueError(
            "Slice manifest referenced unknown question IDs: "
            + ", ".join(sorted(missing))
        )
    return selected


def _load_tips_text(tips_file: Path | None) -> str | None:
    """Load benchmark-only Fleet-RLM steering tips."""
    if tips_file is None:
        return None
    text = tips_file.read_text(encoding="utf-8").strip()
    return text or None


def _build_rlm_prompt(
    prompt: str, tips_text: str | None, domain: str | None = None
) -> str:
    """Append benchmark-specific steering and output requirements."""
    parts = [prompt.rstrip()]
    if tips_text:
        parts.append(f"\n\nRLM EXECUTION TIPS:\n{tips_text}")
    format_reminder = _LONGCOT_DOMAIN_FORMAT_REMINDERS.get(
        domain or "", _LONGCOT_FORMAT_REMINDER_GENERIC
    )
    parts.append(format_reminder)
    return "".join(parts)


# ---------------------------------------------------------------------------
# Mode A: Direct inference via LongCoT's own pipeline
# ---------------------------------------------------------------------------


def run_direct(
    *,
    config: str,
    difficulty: str,
    domain: str | None,
    max_questions: int | None,
    output_dir: Path,
    slice_file: Path | None = None,
    dry_run: bool = False,
) -> Path | None:
    """Run LongCoT inference on an optional deterministic question slice."""
    _ensure_longcot()

    module = _load_vendor_run_inference_module()
    cfg, config_path = _load_longcot_config(config, resolve_env=not dry_run)
    questions = _select_questions_for_slice(
        _load_longcot_questions(domain, difficulty, max_tasks=None),
        _load_slice_manifest(slice_file),
    )
    if max_questions is not None:
        questions = questions[:max_questions]

    output_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    domain_label = domain or "all"
    output_file = (
        output_dir / f"longcot_{config}_{domain_label}_{difficulty}_{ts}.jsonl"
    )

    print("Running LongCoT inference (direct mode)")
    print(f"  Config:     {config}")
    print(f"  Config path:{config_path}")
    print(f"  Difficulty: {difficulty}")
    print(f"  Domain:     {domain_label}")
    print(f"  Questions:  {len(questions)}")
    if slice_file is not None:
        print(f"  Slice:      {slice_file}")
    print(f"  Output:     {output_file}")
    print()

    if dry_run:
        print("Dry run — no provider calls were executed.")
        return None

    provider = module.create_provider(
        cfg["provider"],
        model=cfg["model"],
        api_key=cfg.get("api_key"),
        timeout=cfg.get("timeout", 900.0),
        headers=cfg.get("headers"),
        extra=cfg.get("extra"),
    )

    module.run_parallel(
        provider,
        questions,
        num_workers=cfg.get("num_workers", 8),
        max_retries=cfg.get("max_retries", 2),
        retry_timeouts=cfg.get("retry_timeouts", False),
        llm_kwargs=dict(cfg.get("llm_kwargs") or {}),
        output_path=str(output_file),
    )

    print(f"\nInference complete: {output_file}")
    return output_file


def run_eval(
    responses_path: Path,
    *,
    output_dir: Path,
    no_fallback: bool = False,
    mode: str = "direct",
    config_name: str | None = None,
) -> dict[str, Any] | None:
    """Run LongCoT evaluation on a responses JSONL file."""
    _ensure_longcot()

    env = os.environ.copy()

    cmd = [
        str(VENDOR_LONGCOT / ".venv" / "bin" / "python"),
        str(VENDOR_LONGCOT / "run_eval.py"),
        str(responses_path),
    ]
    if no_fallback:
        cmd.append("--no-fallback")

    print(f"\nRunning LongCoT evaluation on {responses_path.name}")
    result = subprocess.run(
        cmd, env=env, cwd=str(VENDOR_LONGCOT), capture_output=True, text=True
    )

    if result.returncode != 0:
        print(f"Evaluation failed: {result.stderr}")
        return None

    print(result.stdout)

    results_dir = VENDOR_LONGCOT / "results"
    if results_dir.exists():
        result_files = sorted(
            results_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True
        )
        if result_files:
            latest = result_files[0]
            eval_data = json.loads(latest.read_text(encoding="utf-8"))
            dest = output_dir / f"longcot-eval-{latest.name}"
            dest.write_text(json.dumps(eval_data, indent=2), encoding="utf-8")
            print(f"Evaluation results saved to: {dest}")

            # Enrich with model/difficulty from the JSONL responses
            resp_rows = [
                json.loads(line)
                for line in responses_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            first = resp_rows[0] if resp_rows else {}
            model_name = first.get("model", "unknown")
            provider = _infer_provider(model_name=model_name, config_name=config_name)
            difficulty = first.get("difficulty", "unknown")
            by_domain: dict[str, dict[str, int]] = {}
            for d in eval_data.get("details", []):
                dom = d.get("domain", "unknown")
                if dom not in by_domain:
                    by_domain[dom] = {"correct": 0, "incorrect": 0, "failed": 0}
                by_domain[dom][d.get("status", "failed")] = (
                    by_domain[dom].get(d.get("status", "failed"), 0) + 1
                )

            summary = {
                "benchmark": "longcot",
                "mode": mode,
                "model": model_name,
                "provider": provider,
                "difficulty": difficulty,
                "tasks_total": eval_data.get("total", 0),
                "tasks_successful": eval_data.get("correct", 0),
                "correct": eval_data.get("correct", 0),
                "incorrect": eval_data.get("incorrect", 0),
                "failed": eval_data.get("failed", 0),
                "wrong_formatting": eval_data.get("wrong_formatting", 0),
                "accuracy": eval_data.get("accuracy", 0.0),
                "overall_accuracy": eval_data.get("overall_accuracy", 0.0),
                "by_domain": by_domain,
            }
            log_to_mlflow(summary, output_dir, results_file=responses_path)
            return eval_data

    return None


# ---------------------------------------------------------------------------
# Mode B: RLM pipeline (routes through fleet-rlm's recursive execution)
# ---------------------------------------------------------------------------


def _load_longcot_questions(
    domain: str | None,
    difficulty: str,
    max_tasks: int | None,
) -> list[dict[str, Any]]:
    """Load LongCoT questions directly from JSON data files."""
    data_dir = VENDOR_LONGCOT / "src" / "data"
    domains = [domain] if domain else ["logic", "cs", "chemistry", "chess", "math"]
    if difficulty == "longcot":
        diffs = ["medium", "hard"]
    elif difficulty == "longcot-mini":
        diffs = ["easy"]
    else:
        diffs = [difficulty]

    questions: list[dict[str, Any]] = []
    for dom in domains:
        for diff in diffs:
            path = data_dir / dom / f"{diff}.json"
            if not path.exists():
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            for q in data.get("questions", []):
                if "prompt" not in q:
                    continue
                question = dict(q)
                question["domain"] = dom
                question["difficulty"] = diff
                questions.append(question)

    if max_tasks is not None:
        questions = questions[:max_tasks]
    return questions


def _configure_rlm_lm(config: str) -> str:
    """Set DSPy env vars for the given config and return a display label."""
    cfg, _ = _load_longcot_config(config)
    provider = str(cfg.get("provider", "")).strip().lower()
    model = str(cfg.get("model", "")).strip()
    llm_kwargs = dict(cfg.get("llm_kwargs") or {})

    if provider == "bedrock":
        # Use Bedrock bearer token (ANTHROPIC_OAUTH_KEY) — works for all Bedrock models
        bearer_token = os.environ.get("ANTHROPIC_OAUTH_KEY") or os.environ.get(
            "ANTHROPIC_API_KEY"
        )
        if not bearer_token:
            print("ERROR: ANTHROPIC_OAUTH_KEY not set in .env")
            sys.exit(1)
        os.environ.pop("DSPY_LM_API_BASE", None)
        os.environ.pop("OPENAI_API_KEY", None)
        os.environ.pop("OPENAI_BASE_URL", None)
        os.environ["AWS_REGION_NAME"] = os.environ.get("AWS_REGION", "us-east-1")
        import dspy as _dspy

        lm = _dspy.LM(
            f"bedrock/{model}",
            api_key=bearer_token,
            max_tokens=int(llm_kwargs.get("max_output_tokens", 32000)),
        )
        # Nemotron uses built-in <think> blocks; ChatAdapter's section-header
        # format ([[ ## code ## ]]) is never emitted, causing 100% parse failures.
        # JSONAdapter prompts for {"reasoning": "...", "code": "..."} which
        # the model handles reliably regardless of its CoT mode.
        _dspy.configure(lm=lm, adapter=_dspy.JSONAdapter())
        return f"bedrock/{model}"

    if provider == "openrouter":
        api_key = str(
            cfg.get("api_key") or os.environ.get("OPENROUTER_API_KEY") or ""
        ).strip()
        if not api_key:
            print("ERROR: OPENROUTER_API_KEY not set in environment or config")
            sys.exit(1)

        api_base = (
            os.environ.get("OPENROUTER_API_BASE") or "https://openrouter.ai/api/v1"
        ).strip()
        dspy_model = model if model.startswith("openrouter/") else f"openrouter/{model}"
        os.environ["OPENROUTER_API_KEY"] = api_key
        os.environ.setdefault("OPENROUTER_API_BASE", api_base)
        os.environ["DSPY_LM_MODEL"] = dspy_model
        os.environ["DSPY_LM_API_BASE"] = api_base
        os.environ["DSPY_LLM_API_KEY"] = api_key
        os.environ["DSPY_LM_MAX_TOKENS"] = str(
            llm_kwargs.get("max_completion_tokens")
            or llm_kwargs.get("max_output_tokens")
            or llm_kwargs.get("max_tokens")
            or 32000
        )
        return dspy_model

    if "dashscope" in config or provider == "openai":
        _ensure_api_key()
        os.environ["DSPY_LM_MODEL"] = "openai/qwen3.6-flash"
        os.environ["DSPY_LM_API_BASE"] = DASHSCOPE_BASE_URL
        os.environ["DSPY_LLM_API_KEY"] = os.environ["ALIBABA_API_KEY"]
        os.environ["DSPY_LM_MAX_TOKENS"] = "32000"
        return "openai/qwen3.6-flash (DashScope)"

    print(f"ERROR: Unsupported RLM config provider: {provider or 'unknown'}")
    sys.exit(1)


def run_rlm(
    *,
    config: str,
    difficulty: str,
    domain: str | None,
    max_tasks: int | None,
    output_dir: Path,
    slice_file: Path | None = None,
    tips_file: Path | None = None,
    num_workers: int = 4,
    rlm_max_passes: int = 1,
    rlm_max_repair_attempts: int = 0,
    rlm_subquery_budget: int = 2,
    rlm_max_llm_calls: int = 20,
    rlm_max_iterations: int = 20,
) -> Path | None:
    """Route LongCoT questions through fleet-rlm's RLM pipeline."""
    _ensure_longcot()

    model_label = _configure_rlm_lm(config)
    provider_label = _infer_provider(model_name=model_label, config_name=config)
    tips_text = _load_tips_text(tips_file)

    # Bedrock configures DSPy directly in _configure_rlm_lm; other providers use env vars.
    if provider_label != "bedrock":
        from fleet_rlm.runtime.config import configure_planner_from_env

        if not configure_planner_from_env():
            print("ERROR: Failed to configure planner LM")
            sys.exit(1)

    # Enable MLflow DSPy autolog before any RLM calls so every iteration is traced
    from fleet_rlm.integrations.observability.mlflow_runtime import (
        MlflowTraceRequestContext,
        flush_mlflow_traces,
        get_mlflow_config,
        initialize_mlflow,
        merge_trace_result_metadata,
        mlflow_request_context,
        new_client_request_id,
        update_current_mlflow_trace,
    )

    mlflow_config = get_mlflow_config()
    if initialize_mlflow(mlflow_config):
        import mlflow

        experiment_name = f"{mlflow_config.experiment}/longcot-benchmark"
        mlflow.set_experiment(experiment_name)
        print(f"MLflow tracing enabled → {experiment_name}")
    else:
        print("MLflow not configured — traces will not be recorded")

    from fleet_rlm.integrations.daytona.types import SandboxSpec

    from fleet_rlm.integrations.daytona.interpreter import DaytonaInterpreter
    from fleet_rlm.runtime.modules import RecursiveWorkspaceModule

    # Each thread gets its own interpreter + module to avoid shared state issues.
    _thread_local = threading.local()

    def _get_module() -> RecursiveWorkspaceModule:
        if not hasattr(_thread_local, "module"):
            try:
                interp = DaytonaInterpreter(
                    sandbox_spec=SandboxSpec(
                        disk=1
                    ),  # 1 GiB to stay under 30 GiB quota
                )
            except Exception as exc:
                raise RuntimeError(f"Daytona interpreter unavailable: {exc}") from exc
            _thread_local.module = RecursiveWorkspaceModule(
                interpreter=interp,
                max_iterations=rlm_max_iterations,
                max_llm_calls=rlm_max_llm_calls,
                max_passes=rlm_max_passes,
                max_repair_attempts=rlm_max_repair_attempts,
                subquery_budget=rlm_subquery_budget,
                verbose=True,
                evidence_sink=_LocalEvidenceSink(),
            )
        return _thread_local.module

    questions = _select_questions_for_slice(
        _load_longcot_questions(domain, difficulty, max_tasks=None),
        _load_slice_manifest(slice_file),
    )
    if max_tasks is not None:
        questions = questions[:max_tasks]
    total = len(questions)

    print("\nRunning LongCoT via fleet-rlm RecursiveWorkspaceModule (L4 orchestrator)")
    print(f"  Model:      {model_label}")
    print(f"  Difficulty: {difficulty}")
    print(f"  Domain:     {domain or 'all'}")
    print(f"  Tasks:      {total}")
    if slice_file is not None:
        print(f"  Slice:      {slice_file}")
    if tips_file is not None:
        print(f"  Tips:       {tips_file}")
    print(f"  Workers:    {num_workers}")
    print(
        "  RLM caps:   "
        f"passes={rlm_max_passes}, repairs={rlm_max_repair_attempts}, "
        f"subqueries={rlm_subquery_budget}, llm_calls={rlm_max_llm_calls}"
    )
    print()

    print_lock = threading.Lock()
    completed_count = 0

    def _run_one(idx_q: tuple[int, dict[str, Any]]) -> dict[str, Any]:
        nonlocal completed_count
        i, q = idx_q
        qid = q.get("question_id", f"q{i}")
        prompt = _build_rlm_prompt(q["prompt"], tips_text, domain=q.get("domain"))
        domain_name = q.get("domain", "unknown")
        started = time.time()
        response_text = ""
        error: str | None = None
        runtime_status = "not_started"
        transport_success = False
        trace_metadata: dict[str, Any] = {}
        mlflow_result_metadata: dict[str, str] = {}
        try:
            request_context = MlflowTraceRequestContext(
                client_request_id=new_client_request_id(prefix="longcot-rlm"),
                session_id=f"longcot:{qid}",
                app_env="benchmark",
                request_preview=prompt,
                model_id=model_label,
                metadata={
                    "benchmark": "longcot",
                    "mode": "rlm",
                    "question_id": str(qid),
                    "domain": str(domain_name),
                    "difficulty": str(q.get("difficulty", "unknown")),
                    "model": model_label,
                },
            )
            with mlflow_request_context(request_context):
                mod = _get_module()
                prediction = mod(user_request=prompt, context="")
                runtime_status = str(getattr(prediction, "status", "unknown"))
                raw_answer = str(getattr(prediction, "answer", ""))
                pass_count = int(getattr(prediction, "passes", 0) or 0)
                response_text = raw_answer
                transport_success = bool(raw_answer.strip())
                transport_status = "ok" if transport_success else "empty_response"
                error = None if transport_success else "RLM returned an empty response."
                trace_metadata = {
                    "runtime_status": runtime_status,
                    "transport_status": transport_status,
                    "transport_error": error or "",
                    "pass_count": pass_count,
                    "response_bytes": len(response_text),
                    "prompt_bytes": len(prompt),
                }
                update_current_mlflow_trace(
                    response_preview=response_text,
                    trace_metadata=trace_metadata,
                )
                mlflow_result_metadata = merge_trace_result_metadata(
                    {},
                    response_preview=response_text,
                    trace_metadata=trace_metadata,
                )
        except Exception as exc:
            exc_text = str(exc)
            if "solution" in exc_text.lower():
                response_text = exc_text
                transport_success = True
                transport_status = "salvaged_exception_response"
                error = None
            else:
                response_text = ""
                transport_success = False
                transport_status = "runtime_error"
                error = exc_text
            if _contains_infra_failure_text(exc_text):
                transport_status = "infra_error"
            trace_metadata = {
                "runtime_status": runtime_status,
                "transport_status": transport_status,
                "transport_error": error or "",
                "pass_count": 0,
                "response_bytes": len(response_text),
                "prompt_bytes": len(prompt),
            }
            update_current_mlflow_trace(
                response_preview=response_text,
                trace_metadata=trace_metadata,
            )
            mlflow_result_metadata = merge_trace_result_metadata(
                {},
                response_preview=response_text,
                trace_metadata=trace_metadata,
            )
        finally:
            flush_mlflow_traces()
        elapsed_ms = int((time.time() - started) * 1000)
        with print_lock:
            completed_count += 1
            print(
                f"  [{completed_count}/{total}] {qid} ({domain_name})... {transport_status} ({elapsed_ms}ms)"
            )
        result = {
            "question_id": qid,
            "domain": domain_name,
            "difficulty": q.get("difficulty", "unknown"),
            "successful": transport_success,
            "runtime_status": runtime_status,
            "transport_status": transport_status,
            "response_text": response_text,
            "error": error,
            "contract_warning": "rlm_mode_uses_code_execution",
            "model": model_label,
            "provider": provider_label,
            "elapsed_ms": elapsed_ms,
            "mode": "rlm",
        }
        result.update(mlflow_result_metadata)
        return result

    output_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    output_file = output_dir / f"longcot_rlm_{domain or 'all'}_{difficulty}_{ts}.jsonl"

    results: list[dict[str, Any]] = []
    with open(output_file, "w", encoding="utf-8") as out_f:
        with ThreadPoolExecutor(max_workers=num_workers) as executor:
            futures = [
                executor.submit(_run_one, (i, q)) for i, q in enumerate(questions, 1)
            ]
            in_flight = set(futures)
            while in_flight:
                done, in_flight = wait(
                    in_flight, timeout=1.0, return_when=FIRST_COMPLETED
                )
                for fut in done:
                    r = fut.result()
                    results.append(r)
                    out_f.write(json.dumps(r) + "\n")
                    out_f.flush()
    flush_mlflow_traces()

    succeeded = sum(1 for r in results if r["successful"])
    print(
        f"\nRLM mode complete: {succeeded}/{len(results)} transport-successful responses"
    )
    print(f"Results: {output_file}")

    summary = _build_summary(
        results,
        difficulty=difficulty,
        mode="rlm",
        provider=provider_label,
    )
    summary["summary_kind"] = "transport"
    summary["native_eval_required"] = True
    summary_path = output_dir / "longcot-rlm-transport-summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"Transport summary: {summary_path}")

    return output_file


# ---------------------------------------------------------------------------
# Summary generation
# ---------------------------------------------------------------------------


def _build_summary(
    results: list[dict[str, Any]],
    *,
    difficulty: str,
    mode: str,
    provider: str | None = None,
) -> dict[str, Any]:
    total = len(results)
    succeeded = sum(1 for r in results if r.get("successful"))
    by_domain: dict[str, dict[str, int]] = {}

    for r in results:
        dom = r.get("domain", "unknown")
        if dom not in by_domain:
            by_domain[dom] = {"total": 0, "successful": 0}
        by_domain[dom]["total"] += 1
        if r.get("successful"):
            by_domain[dom]["successful"] += 1

    model = next((r.get("model", "unknown") for r in results), "unknown")
    resolved_provider = provider or _infer_provider(model_name=model)
    return {
        "benchmark": "longcot",
        "difficulty": difficulty,
        "mode": mode,
        "model": model,
        "provider": resolved_provider,
        "tasks_total": total,
        "tasks_successful": succeeded,
        "success_rate": succeeded / total if total else 0.0,
        "by_domain": {
            dom: {
                **counts,
                "success_rate": counts["successful"] / counts["total"]
                if counts["total"]
                else 0.0,
            }
            for dom, counts in by_domain.items()
        },
        "generated_at": datetime.now(UTC).isoformat(),
    }


# ---------------------------------------------------------------------------
# MLflow logging
# ---------------------------------------------------------------------------


def log_to_mlflow(
    summary: dict[str, Any],
    output_dir: Path,
    *,
    results_file: Path | None = None,
) -> None:
    """Log LongCoT benchmark results to MLflow. No-ops gracefully if not configured."""
    try:
        import mlflow

        from fleet_rlm.integrations.observability.mlflow_runtime import (
            get_mlflow_config,
            initialize_mlflow,
        )

        config = get_mlflow_config()
        if not initialize_mlflow(config):
            return

        mode = summary.get("mode", "direct")
        model = summary.get("model", "unknown")
        experiment_name = f"{config.experiment}/longcot-benchmark"
        mlflow.set_experiment(experiment_name)

        run_name = (
            f"longcot-{mode}-{model.split('/')[-1]}-"
            f"{summary.get('difficulty', '?')}-"
            f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}"
        )

        with mlflow.start_run(run_name=run_name):
            mlflow.log_params(
                {
                    "benchmark": "longcot",
                    "mode": mode,
                    "model": model,
                    "provider": summary.get("provider", "unknown"),
                    "difficulty": summary.get("difficulty", "unknown"),
                    "tasks_total": summary.get("tasks_total", 0),
                }
            )

            metrics: dict[str, float] = {
                "tasks_total": float(summary.get("tasks_total", 0)),
                "tasks_successful": float(summary.get("tasks_successful", 0)),
                "success_rate": float(summary.get("success_rate", 0.0)),
            }
            # direct mode eval metrics from LongCoT verifier
            for key in (
                "correct",
                "incorrect",
                "failed",
                "accuracy",
                "overall_accuracy",
            ):
                if key in summary:
                    metrics[key] = float(summary[key])
            # per-domain metrics
            for dom, vals in summary.get("by_domain", {}).items():
                if isinstance(vals, dict):
                    for k, v in vals.items():
                        if isinstance(v, (int, float)):
                            metrics[f"{dom}_{k}"] = float(v)

            mlflow.log_metrics(metrics)

            summary_path = output_dir / "longcot-summary.json"
            summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
            mlflow.log_artifact(str(summary_path))
            if results_file and results_file.exists():
                mlflow.log_artifact(str(results_file))

        print(f"MLflow run logged: {experiment_name} / {run_name}")
    except Exception as exc:
        print(f"MLflow logging skipped: {exc}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Run LongCoT benchmark for fleet-rlm evaluation"
    )
    parser.add_argument(
        "--mode",
        choices=["direct", "rlm"],
        default="direct",
        help="Execution mode: 'direct' calls provider natively, 'rlm' uses fleet-rlm pipeline",
    )
    parser.add_argument(
        "--config",
        default="bedrock_nemotron_120b",
        help=(
            "LongCoT provider config name from vendor/longcot/src/configs/ "
            "(default: bedrock_nemotron_120b). "
            "Options: bedrock_nemotron_120b, dashscope_qwen3_flash, dashscope_qwen3_plus"
        ),
    )
    parser.add_argument(
        "--difficulty",
        choices=["easy", "medium", "hard", "longcot-mini", "longcot"],
        default="longcot-mini",
        help="Difficulty level (default: longcot-mini = easy subset, ~500 questions)",
    )
    parser.add_argument(
        "--domain", choices=["logic", "cs", "chemistry", "chess", "math"]
    )
    parser.add_argument(
        "--max-questions", type=int, help="Cap number of questions (direct mode)"
    )
    parser.add_argument("--max-tasks", type=int, help="Cap number of tasks (rlm mode)")
    parser.add_argument(
        "--num-workers",
        type=int,
        default=4,
        help="Parallel workers for rlm mode (default: 4)",
    )
    parser.add_argument(
        "--rlm-max-passes",
        type=int,
        default=1,
        help="Max recursive workspace passes in rlm mode (default: 1)",
    )
    parser.add_argument(
        "--rlm-max-repair-attempts",
        type=int,
        default=0,
        help="Max repair attempts in rlm mode (default: 0)",
    )
    parser.add_argument(
        "--rlm-subquery-budget",
        type=int,
        default=2,
        help="Subquery budget per recursive pass in rlm mode (default: 2)",
    )
    parser.add_argument(
        "--rlm-max-llm-calls",
        type=int,
        default=20,
        help="Shared LLM call budget per RLM task (default: 20)",
    )
    parser.add_argument(
        "--rlm-max-iterations",
        type=int,
        default=20,
        help="Max DSPy RLM REPL iterations per module call (default: 20)",
    )
    parser.add_argument("--output-dir", default=str(ROOT / "output" / "longcot-eval"))
    parser.add_argument(
        "--no-fallback",
        action="store_true",
        help="Skip Gemini math/chemistry fallback judges in eval (avoids needing GEMINI_API_KEY)",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Show what would run without calling API"
    )
    parser.add_argument(
        "--slice-file",
        help="Optional JSON manifest listing a deterministic subset of question IDs.",
    )
    parser.add_argument(
        "--tips-file",
        help="Optional benchmark-only steering text injected into Fleet-RLM prompts.",
    )
    parser.add_argument(
        "--eval-only", help="Skip inference, evaluate existing JSONL file"
    )

    args = parser.parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    slice_file = Path(args.slice_file).resolve() if args.slice_file else None
    tips_file = Path(args.tips_file).resolve() if args.tips_file else None

    print("=" * 60)
    print("Fleet-RLM × LongCoT Benchmark")
    print(f"Mode:       {args.mode}")
    print(f"Config:     {args.config}")
    print(f"Difficulty: {args.difficulty}")
    print(f"Domain:     {args.domain or 'all'}")
    if slice_file is not None:
        print(f"Slice:      {slice_file}")
    if tips_file is not None:
        print(f"Tips:       {tips_file}")
    print("=" * 60)

    if args.eval_only:
        run_eval(
            Path(args.eval_only),
            output_dir=output_dir,
            no_fallback=args.no_fallback,
            mode=args.mode,
            config_name=args.config,
        )
        return

    if args.mode == "direct":
        output_file = run_direct(
            config=args.config,
            difficulty=args.difficulty,
            domain=args.domain,
            max_questions=args.max_questions,
            output_dir=output_dir,
            slice_file=slice_file,
            dry_run=args.dry_run,
        )
        if output_file and not args.dry_run:
            run_eval(
                output_file,
                output_dir=output_dir,
                no_fallback=args.no_fallback,
                mode="direct",
                config_name=args.config,
            )

    elif args.mode == "rlm":
        output_file = run_rlm(
            config=args.config,
            difficulty=args.difficulty,
            domain=args.domain,
            max_tasks=args.max_tasks or args.max_questions,
            output_dir=output_dir,
            slice_file=slice_file,
            tips_file=tips_file,
            num_workers=args.num_workers,
            rlm_max_passes=args.rlm_max_passes,
            rlm_max_repair_attempts=args.rlm_max_repair_attempts,
            rlm_subquery_budget=args.rlm_subquery_budget,
            rlm_max_llm_calls=args.rlm_max_llm_calls,
            rlm_max_iterations=args.rlm_max_iterations,
        )
        if output_file and not args.dry_run:
            run_eval(
                output_file,
                output_dir=output_dir,
                no_fallback=args.no_fallback,
                mode="rlm",
                config_name=args.config,
            )

    print("\n" + "=" * 60)
    print(f"Results in: {output_dir}")
    print("=" * 60)


if __name__ == "__main__":
    main()
