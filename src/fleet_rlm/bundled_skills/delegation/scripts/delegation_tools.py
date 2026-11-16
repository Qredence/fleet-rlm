"""Select delegation mechanisms, validate child task specs, and collect promoted child outputs."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

_ALLOWED_INPUT_ROOTS = frozenset({"files", "skills", "artifacts"})
_MAX_PROMPT_CHARS = 20_000
_MAX_SUBTASKS = 8
_HIERARCHY = ("jev_decide_batched", "llm_query_batched", "dspy.Predict", "spawn_agents")


def _validate_rel_path(raw_path: str, allowed_roots: frozenset[str]) -> str:
    """Normalize and validate a workspace-relative POSIX path without symlinks or traversal."""
    if not isinstance(raw_path, str) or not raw_path or "\\" in raw_path or "\x00" in raw_path or len(raw_path) > 1024:
        raise ValueError(f"Invalid workspace path: {raw_path!r}")
    if any(ord(ch) < 32 for ch in raw_path):
        raise ValueError(f"Control character in workspace path: {raw_path!r}")
    clean = raw_path.removeprefix("/workspace/")
    if clean.startswith("/"):
        raise ValueError(f"Workspace path must remain inside /workspace: {raw_path!r}")
    parts = clean.split("/")
    if not parts or parts[0] not in allowed_roots or any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"Path must be beneath {', '.join(sorted(allowed_roots))}/: {raw_path!r}")
    return "/".join(parts)


def _safe_resolve_under(root: Path, rel_path: str) -> Path:
    """Resolve rel_path under root while rejecting any symlink traversal or escape."""
    resolved_root = root.resolve()
    current = resolved_root
    for part in rel_path.split("/"):
        current = current / part
        if current.is_symlink():
            raise ValueError(f"Symbolic links are not permitted in workspace paths: {rel_path!r}")
    resolved = current.resolve()
    if os.path.commonpath((str(resolved_root), str(resolved))) != str(resolved_root):
        raise ValueError(f"Workspace path escapes root: {rel_path!r}")
    return resolved


def choose_mechanism(
    *,
    item_count: int = 1,
    needs_choice_or_ranking: bool = False,
    needs_freeform_text: bool = False,
    needs_isolated_files_or_code_exec: bool = False,
) -> dict[str, Any]:
    """Recommend the lowest-cost DSPy 3.5.0 execution or delegation mechanism for a task."""
    if isinstance(item_count, bool) or not isinstance(item_count, int) or item_count < 1:
        raise ValueError("item_count must be a positive integer")

    if needs_isolated_files_or_code_exec:
        if item_count > 1:
            return {
                "mechanism": "spawn_agents",
                "reason": (
                    "Independent tasks need isolated sandboxes, separate file scopes, or parallel code execution."
                ),
                "batch_size_limit": _MAX_SUBTASKS,
                "batches_needed": (item_count + _MAX_SUBTASKS - 1) // _MAX_SUBTASKS,
                "hierarchy": list(_HIERARCHY),
            }
        return {
            "mechanism": "spawn_agent",
            "reason": "Single subtask needs an isolated child sandbox or separate artifact scope.",
            "batch_size_limit": 1,
            "batches_needed": 1,
            "hierarchy": list(_HIERARCHY),
        }

    if needs_choice_or_ranking and not needs_freeform_text:
        return {
            "mechanism": "jev_decide_batched",
            "reason": "Fixed-choice classification, filtering, or ranking is fastest and typed via Jev.",
            "batch_size_limit": 100,
            "batches_needed": (item_count + 99) // 100,
            "hierarchy": list(_HIERARCHY),
        }

    if needs_freeform_text:
        if item_count > 1:
            return {
                "mechanism": "llm_query_batched",
                "reason": "Multiple independent free-form text prompts without child sandbox isolation.",
                "batch_size_limit": 100,
                "batches_needed": (item_count + 99) // 100,
                "hierarchy": list(_HIERARCHY),
            }
        return {
            "mechanism": "dspy.Predict",
            "reason": "Single structured or typed synthesis without spawning a child sandbox.",
            "batch_size_limit": 1,
            "batches_needed": 1,
            "hierarchy": list(_HIERARCHY),
        }

    return {
        "mechanism": "local_python",
        "reason": "Deterministic local computation requires neither sub-LM calls nor child sandboxes.",
        "batch_size_limit": None,
        "batches_needed": 1,
        "hierarchy": list(_HIERARCHY),
    }


def build_child_specs(
    tasks: Sequence[Mapping[str, Any] | str],
    *,
    max_batch: int = _MAX_SUBTASKS,
    workspace_root: str | Path = "/workspace",
) -> dict[str, Any]:
    """Validate subtask prompts and workspace input_paths and return spawn_agents-ready specs."""
    if isinstance(max_batch, bool) or not isinstance(max_batch, int) or not 1 <= max_batch <= _MAX_SUBTASKS:
        raise ValueError(f"max_batch must be an integer in 1..{_MAX_SUBTASKS}")
    if isinstance(tasks, (str, bytes)) or not isinstance(tasks, Sequence) or not 1 <= len(tasks) <= max_batch:
        raise ValueError(f"tasks must be a sequence of 1-{max_batch} items")

    ws_root = Path(workspace_root)
    if not ws_root.is_dir():
        raise FileNotFoundError(f"workspace_root does not exist: {workspace_root!s}")

    subtasks: list[str] = []
    input_paths_list: list[list[str]] = []
    specs: list[dict[str, Any]] = []

    for idx, task in enumerate(tasks):
        if isinstance(task, str):
            prompt = task
            raw_inputs: Sequence[Any] = ()
            extra: dict[str, Any] = {}
        elif isinstance(task, Mapping):
            raw_prompt = task.get("prompt", task.get("subtask"))
            if not isinstance(raw_prompt, str):
                raise ValueError(f"Task {idx} must include a string 'prompt' or 'subtask'")
            prompt = raw_prompt
            raw_inputs_val = task.get("input_paths") or ()
            if isinstance(raw_inputs_val, (str, bytes)) or not isinstance(raw_inputs_val, Sequence):
                raise ValueError(f"Task {idx} input_paths must be a sequence of workspace paths")
            raw_inputs = raw_inputs_val
            extra = {k: v for k, v in task.items() if k not in {"prompt", "subtask", "input_paths"}}
        else:
            raise TypeError(f"Task {idx} must be a string or mapping")

        if not prompt.strip() or len(prompt) > _MAX_PROMPT_CHARS:
            raise ValueError(f"Task {idx} prompt must be a non-empty string up to {_MAX_PROMPT_CHARS} characters")

        validated_inputs: list[str] = []
        for raw_path in raw_inputs:
            rel_path = _validate_rel_path(str(raw_path), _ALLOWED_INPUT_ROOTS)
            resolved = _safe_resolve_under(ws_root, rel_path)
            if not resolved.is_file():
                raise FileNotFoundError(f"Task {idx} input_path not found: {rel_path}")
            validated_inputs.append(rel_path)

        subtasks.append(prompt)
        input_paths_list.append(validated_inputs)
        specs.append({"index": idx, "subtask": prompt, "input_paths": validated_inputs, **extra})

    return {
        "subtasks": subtasks,
        "input_paths": input_paths_list,
        "task_count": len(subtasks),
        "max_batch": max_batch,
        "specs": specs,
    }


def _resolve_artifact_file(art_root: Path, art_path: str) -> Path:
    """Resolve a promoted child artifact path relative to artifacts_root safely."""
    rel = _validate_rel_path(art_path, frozenset({"artifacts"}))
    candidate = _safe_resolve_under(art_root, rel)
    if not candidate.is_file() and art_root.name == "artifacts" and rel.startswith("artifacts/"):
        fallback_rel = rel.removeprefix("artifacts/")
        fallback = _safe_resolve_under(art_root, fallback_rel)
        if fallback.is_file():
            return fallback
    return candidate


def collect_child_outputs(
    results: Sequence[Mapping[str, Any]],
    *,
    parse_json_artifacts: bool = True,
    artifacts_root: str | Path = "/workspace",
) -> dict[str, Any]:
    """Summarize spawn_agents results, separating child failures and parsing promoted JSON artifacts."""
    if isinstance(results, (str, bytes)) or not isinstance(results, Sequence):
        raise TypeError("results must be a sequence of child result mappings")

    art_root = Path(artifacts_root)
    items: list[dict[str, Any]] = []
    parsed_json: dict[str, Any] = {}
    errors: list[dict[str, Any]] = []
    succeeded_count = 0

    for idx, row in enumerate(results):
        if not isinstance(row, Mapping):
            raise TypeError(f"Child result at index {idx} must be a mapping")
        error_msg = row.get("error")
        succeeded = not bool(error_msg)
        if succeeded:
            succeeded_count += 1
        else:
            errors.append({"index": idx, "error": str(error_msg)})

        artifact_entries: list[dict[str, Any]] = []
        for raw_art in row.get("artifacts") or ():
            entry: dict[str, Any] = (
                {str(k): v for k, v in raw_art.items()} if isinstance(raw_art, Mapping) else {"path": str(raw_art)}
            )
            art_path = str(entry.get("path", ""))

            resolved = _resolve_artifact_file(art_root, art_path)
            entry["exists"] = resolved.is_file()
            if parse_json_artifacts and resolved.is_file() and resolved.suffix.lower() == ".json":
                try:
                    loaded = json.loads(resolved.read_text(encoding="utf-8"))
                    entry["json"] = loaded
                    parsed_json[art_path] = loaded
                except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                    entry["json_error"] = f"{type(exc).__name__}: {exc}"
            artifact_entries.append(entry)

        items.append(
            {
                "index": idx,
                "succeeded": succeeded,
                "error": str(error_msg) if error_msg else None,
                "findings": str(row.get("findings", "")),
                "artifacts": artifact_entries,
            }
        )

    total = len(results)
    failed_count = total - succeeded_count
    return {
        "total": total,
        "succeeded_count": succeeded_count,
        "failed_count": failed_count,
        "all_succeeded": failed_count == 0,
        "items": items,
        "parsed_json": parsed_json,
        "errors": errors,
    }


def run(tasks: Sequence[Mapping[str, Any] | str], *, workspace_root: str | Path = "/workspace") -> dict[str, Any]:
    """Validate child delegation tasks against workspace_root and recommend execution mechanism."""
    specs = build_child_specs(tasks, workspace_root=workspace_root)
    recommendation = choose_mechanism(item_count=specs["task_count"], needs_isolated_files_or_code_exec=True)
    return {**specs, "recommendation": recommendation}


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for building and validating child delegation specs."""
    parser = argparse.ArgumentParser(description="Validate delegation task specifications.")
    parser.add_argument("--tasks-json", default=None, help="JSON string or path to JSON list of tasks")
    parser.add_argument("--task", action="append", default=[], help="Subtask prompt string (repeatable)")
    parser.add_argument(
        "--workspace-root",
        default=os.environ.get("FLEET_WORKSPACE", "/workspace"),
        help="Workspace root directory (default: $FLEET_WORKSPACE or /workspace)",
    )
    parser.add_argument("--max-batch", type=int, default=_MAX_SUBTASKS, help="Max subtasks per batch (1..8)")
    args = parser.parse_args(argv)

    try:
        tasks: list[Any] = []
        if args.tasks_json:
            candidate = Path(args.tasks_json)
            raw = candidate.read_text(encoding="utf-8") if candidate.is_file() else args.tasks_json
            loaded = json.loads(raw)
            if not isinstance(loaded, list):
                raise ValueError("--tasks-json must decode to a JSON array")
            tasks.extend(loaded)
        tasks.extend(args.task)
        if not tasks:
            raise ValueError("Provide --tasks-json or at least one --task")
        specs = build_child_specs(tasks, max_batch=args.max_batch, workspace_root=args.workspace_root)
        recommendation = choose_mechanism(item_count=specs["task_count"], needs_isolated_files_or_code_exec=True)
        sys.stdout.write(json.dumps({**specs, "recommendation": recommendation}, indent=2) + "\n")
        return 0
    except Exception as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
