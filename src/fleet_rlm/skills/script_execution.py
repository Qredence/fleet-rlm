"""Trusted skill script validation and Daytona-only execution."""

from __future__ import annotations

import json
import uuid
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote

from fleet_rlm.runtime.sandbox_execution import coerce_sandbox_result, execute_sandbox_tool
from fleet_rlm.skills.catalog import inventory_skill_resources, resolve_skill_directory, resolve_skill_metadata
from fleet_rlm.skills.errors import (
    SkillNotFoundError,
    SkillNotVisibleError,
    SkillResourcePathError,
    SkillScriptNotFoundError,
    SkillScriptNotPermittedError,
    SkillValidationError,
)
from fleet_rlm.skills.loader import load_skill_bundle
from fleet_rlm.skills.permissions import is_skill_script_execution_permitted, is_skill_visible
from fleet_rlm.skills.schemas import SkillMetadata, SkillResource, SkillResourceKind, SkillRuntimeContext
from fleet_rlm.skills.sync import resolve_sandbox_script_path, resolve_skill_sandbox_root
from fleet_rlm.skills.validator import safe_skill_name, validate_resource_path

_DEFAULT_TIMEOUT_S = 120
_MAX_TIMEOUT_S = 300
_DEFAULT_TRUNCATION_CHARS = 8000

_RUN_SKILL_SCRIPT_CODE = """\
import json
import os
import subprocess
import sys

script_path = _script_path
args_payload = _args_json
timeout_s = _timeout_s

try:
    args = json.loads(args_payload) if args_payload else []
    if not isinstance(args, list):
        SUBMIT(success=False, error="Skill script execution failed.")
except json.JSONDecodeError:
    SUBMIT(success=False, error="Skill script execution failed.")
else:
    if not os.path.isfile(script_path):
        SUBMIT(success=False, error="Skill script execution failed.")
    else:
        script_dir = os.path.dirname(script_path) or "."
        if script_path.endswith(".py"):
            cmd = [sys.executable, script_path] + [str(arg) for arg in args]
        elif script_path.endswith(".sh"):
            cmd = ["/bin/bash", script_path] + [str(arg) for arg in args]
        else:
            SUBMIT(success=False, error="Skill script execution failed.")
        try:
            completed = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout_s or 120,
                cwd=script_dir,
            )
            SUBMIT(
                success=completed.returncode == 0,
                exit_code=completed.returncode,
                stdout=completed.stdout or "",
                stderr=completed.stderr or "",
            )
        except subprocess.TimeoutExpired as exc:
            SUBMIT(
                success=False,
                exit_code=None,
                stdout=exc.stdout or "",
                stderr=exc.stderr or "",
                error="Skill script execution failed.",
            )
        except Exception:
            SUBMIT(success=False, error="Skill script execution failed.")
"""

_STORE_SKILL_SCRIPT_LOG_CODE = """\
import os

parent = os.path.dirname(_log_path)
if parent:
    os.makedirs(parent, exist_ok=True)
with open(_log_path, "w", encoding="utf-8") as handle:
    handle.write(_log_content)
SUBMIT(status="ok")
"""


def validate_json_args(args: Any) -> list[Any]:
    if args is None:
        return []
    if not isinstance(args, list):
        raise SkillValidationError("Args must be a JSON-serializable list.", code="invalid_script_args")
    try:
        json.dumps(args)
    except (TypeError, ValueError) as exc:
        raise SkillValidationError("Args must be JSON-serializable.", code="invalid_script_args") from exc
    return args


def _bounded_timeout(timeout_s: int | None) -> int:
    timeout = timeout_s if timeout_s is not None else _DEFAULT_TIMEOUT_S
    return max(1, min(int(timeout), _MAX_TIMEOUT_S))


def _require_script_under_scripts(script_path: str) -> None:
    result = validate_resource_path(script_path)
    if not result.valid:
        issue = result.issues[0]
        raise SkillResourcePathError(issue.message, code=issue.code)
    parts = [part for part in unquote(script_path.strip()).split("/") if part]
    if not parts or parts[0] != "scripts":
        raise SkillResourcePathError(
            "Script path must be under scripts/.",
            code="invalid_script_path",
        )


def _script_in_inventory(script_path: str, resources: list[SkillResource]) -> bool:
    normalized = script_path.strip()
    return any(resource.kind == SkillResourceKind.SCRIPT and resource.path == normalized for resource in resources)


def _validate_host_script_path(skill_dir: Path, script_path: str) -> None:
    target = (skill_dir / script_path).resolve()
    resolved_root = skill_dir.resolve()
    if not target.is_relative_to(resolved_root):
        raise SkillResourcePathError("Resource path escapes skill root.", code="traversal")
    if not target.is_file():
        raise SkillScriptNotFoundError()


def _resolve_script_inventory(
    normalized: str,
    *,
    metadata: SkillMetadata,
    context: SkillRuntimeContext,
    resources: dict[str, list[SkillResource]] | None,
) -> list[SkillResource]:
    inventory = resources.get(normalized) if resources else None
    if inventory is not None:
        return inventory
    skill_dir = resolve_skill_directory(metadata, context)
    if skill_dir is not None:
        return inventory_skill_resources(skill_dir)
    bundle = load_skill_bundle(normalized, context)
    return bundle.resources


def validate_skill_script_request(
    skill_name: str,
    script_path: str,
    *,
    context: SkillRuntimeContext,
    resources: dict[str, list[SkillResource]] | None = None,
    sandbox_paths: dict[str, str] | None = None,
) -> str:
    """Validate a script execution request and return the sandbox script path."""
    normalized = safe_skill_name(skill_name)
    if normalized not in context.selected_skill_ids:
        raise SkillNotFoundError(normalized)

    _require_script_under_scripts(script_path)

    metadata = resolve_skill_metadata(normalized, context)
    if metadata is None:
        raise SkillNotFoundError(normalized)
    if not is_skill_visible(metadata.name, metadata.scope, context.visibility):
        raise SkillNotVisibleError(normalized)

    inventory = _resolve_script_inventory(
        normalized,
        metadata=metadata,
        context=context,
        resources=resources,
    )
    if not _script_in_inventory(script_path, inventory):
        raise SkillScriptNotFoundError()

    if not is_skill_script_execution_permitted(metadata):
        raise SkillScriptNotPermittedError()

    sandbox_root = resolve_skill_sandbox_root(
        metadata=metadata,
        context=context,
        sandbox_paths=sandbox_paths,
    )
    if sandbox_root is None:
        raise SkillScriptNotFoundError()

    skill_dir = resolve_skill_directory(metadata, context)
    if skill_dir is not None:
        _validate_host_script_path(skill_dir, script_path)

    return resolve_sandbox_script_path(sandbox_root, script_path)


def _truncate_output(text: str, *, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n...[truncated]"


def _maybe_store_large_output(
    interpreter: Any,
    *,
    stdout: str,
    stderr: str,
    volume_mount_path: str | None,
    max_chars: int,
) -> tuple[str | None, str | None, str | None]:
    combined = "\n".join(part for part in (stdout, stderr) if part)
    if len(combined) <= max_chars or not volume_mount_path:
        return stdout or None, stderr or None, None

    artifact_id = uuid.uuid4().hex
    public_log_path = f"artifacts/skill-scripts/{artifact_id}.log"
    sandbox_log_path = f"{volume_mount_path.rstrip('/')}/{public_log_path}"
    execute_sandbox_tool(
        interpreter,
        _STORE_SKILL_SCRIPT_LOG_CODE,
        {
            "_log_path": sandbox_log_path,
            "_log_content": combined,
        },
    )

    return (
        _truncate_output(stdout, max_chars=max_chars) or None,
        _truncate_output(stderr, max_chars=max_chars) or None,
        public_log_path,
    )


def execute_skill_script_in_daytona(
    interpreter: Any,
    *,
    sandbox_script_path: str,
    args: list[Any] | None = None,
    timeout_s: int | None = None,
    max_output_chars: int = _DEFAULT_TRUNCATION_CHARS,
) -> dict[str, Any]:
    """Execute a validated skill script inside Daytona via the interpreter."""
    validated_args = validate_json_args(args)
    timeout = _bounded_timeout(timeout_s)
    raw = execute_sandbox_tool(
        interpreter,
        _RUN_SKILL_SCRIPT_CODE,
        {
            "_script_path": sandbox_script_path,
            "_args_json": json.dumps(validated_args),
            "_timeout_s": timeout,
        },
    )
    payload = coerce_sandbox_result(raw)
    stdout = str(payload.get("stdout") or "")
    stderr = str(payload.get("stderr") or "")
    volume_mount_path = str(getattr(interpreter, "volume_mount_path", "") or "") or None
    truncation_limit = int(
        getattr(interpreter, "delegate_result_truncation_chars", max_output_chars) or max_output_chars
    )
    stdout, stderr, log_path = _maybe_store_large_output(
        interpreter,
        stdout=stdout,
        stderr=stderr,
        volume_mount_path=volume_mount_path,
        max_chars=truncation_limit,
    )
    success = bool(payload.get("success"))
    exit_code = payload.get("exit_code")
    error = payload.get("error")
    result = {
        "success": success,
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "error": str(error) if error else None,
    }
    if log_path:
        result["log_path"] = log_path
        result["artifact_id"] = PurePosixPath(log_path).stem
    return result


def run_skill_script_impl(
    skill_name: str,
    script_path: str,
    *,
    args: list[Any] | None = None,
    timeout_s: int | None = None,
    context: SkillRuntimeContext,
    interpreter: Any,
    resources: dict[str, list[SkillResource]] | None = None,
    sandbox_paths: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Validate and execute a trusted selected skill script inside Daytona."""
    if interpreter is None:
        raise SkillValidationError("Skill script execution requires a Daytona interpreter.", code="missing_interpreter")
    sandbox_script_path = validate_skill_script_request(
        skill_name,
        script_path,
        context=context,
        resources=resources,
        sandbox_paths=sandbox_paths,
    )
    return execute_skill_script_in_daytona(
        interpreter,
        sandbox_script_path=sandbox_script_path,
        args=args,
        timeout_s=timeout_s,
    )


__all__ = [
    "execute_skill_script_in_daytona",
    "run_skill_script_impl",
    "validate_json_args",
    "validate_skill_script_request",
]
