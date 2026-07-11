"""Runtime binding helpers for fleet tools that need live agent state."""

from __future__ import annotations

import functools
import re
from collections.abc import Callable
from typing import Any

from fleet_rlm.runtime.sandbox_execution import coerce_sandbox_result, execute_sandbox_tool
from fleet_rlm.runtime.tools.document_tools import _load_document_impl, _validate_download_url
from fleet_rlm.runtime.tools.knowledge_tools import _search_knowledge_impl
from fleet_rlm.runtime.tools.rlm_delegate import (
    delegate_to_rlm as _delegate_to_rlm,
)
from fleet_rlm.runtime.tools.rlm_delegate import (
    delegate_to_rlm_batched as _delegate_to_rlm_batched,
)
from fleet_rlm.runtime.tools.sandbox_filesystem import (
    _sandbox_create_directory_impl,
    _sandbox_delete_file_impl,
    _sandbox_find_in_files_impl,
    _sandbox_get_file_info_impl,
    _sandbox_list_files_impl,
    _sandbox_move_file_impl,
    _sandbox_read_file_impl,
    _sandbox_replace_in_files_impl,
    _sandbox_search_files_impl,
    _sandbox_write_file_impl,
    _SandboxFilesystemToolContext,
)
from fleet_rlm.runtime.tools.skill_tools import (
    list_skills_impl,
    load_skill_tool_impl,
    read_skill_resource_impl,
    run_skill_script_tool_impl,
)
from fleet_rlm.runtime.tools.volume_memory_tools import (
    _recall_impl,
    _remember_impl,
)
from fleet_rlm.skills.execution_deps import SkillExecutionDeps
from fleet_rlm.skills.loader import default_skill_runtime_context
from fleet_rlm.tools.artifacts import (
    create_artifact_impl,
    list_artifacts_impl,
    read_artifact_impl,
    update_artifact_impl,
)
from fleet_rlm.tools.filesystem import list_files_impl, read_file_impl, reject_legacy_list_files_pattern
from fleet_rlm.tools.sandbox import inspect_workspace_impl

INTERPRETER_TOOL_NAMES = frozenset(
    {
        "browser_fetch_page",
        "clear_buffer",
        "create_artifact",
        "delegate_to_rlm",
        "delegate_to_rlm_batched",
        "execute_code",
        "inspect_workspace",
        "list_artifacts",
        "list_files",
        "list_skills",
        "load_skill",
        "read_buffer",
        "read_artifact",
        "read_file",
        "read_skill_resource",
        "run_skill_script",
        "recursive_workspace",
        "remember",
        "recall",
        "sandbox_create_directory",
        "sandbox_delete_file",
        "sandbox_find_in_files",
        "sandbox_get_file_info",
        "sandbox_list_files",
        "sandbox_move_file",
        "sandbox_read_file",
        "sandbox_replace_in_files",
        "sandbox_search_files",
        "sandbox_write_file",
        "search_knowledge",
        "update_artifact",
        "write_buffer",
    }
)


def _tool_name(tool: Any) -> str | None:
    return getattr(tool, "name", None) or getattr(getattr(tool, "func", tool), "__name__", None)


def _context_with_remote_document(query: str, context: str) -> str:
    match = re.search(r"https?://\S+", f"{query}\n{context}")
    if match is None:
        return context
    url = match.group(0).rstrip(").,;]")
    from fleet_rlm.runtime.tools.document_tools import fetch_document_text

    fetch_result = fetch_document_text(url)
    if fetch_result.get("status") != "ok":
        return (
            context + f"\n\nNote: Attempted to fetch {url} for recursive workspace "
            f"context but failed: {fetch_result.get('error', 'unknown error')}"
        ).strip()
    text = str(fetch_result.get("text", ""))
    char_count = int(fetch_result.get("char_count", len(text)))
    return (
        context + f"\n\n--- Document fetched from {url} ({char_count} chars) ---\n{text}\n--- End of document ---"
    ).strip()


def _tool_description(tool: Any) -> str:
    return getattr(tool, "desc", None) or getattr(getattr(tool, "func", tool), "__doc__", "") or ""


def _wrap_tool(tool: Any, func: Callable[..., Any]) -> Any:
    from dspy import Tool

    name = _tool_name(tool)
    if name is None:
        return tool
    return Tool(func, name=name, desc=_tool_description(tool))


def _bound_runtime_tool_factories(
    *,
    runtime: Any,
    interpreter: Any | None,
) -> dict[str, Callable[..., Any]]:
    factories: dict[str, Callable[..., Any]] = {}

    def read_core_memory(key: str = "") -> dict[str, Any]:
        if key:
            return {
                "status": "ok",
                "key": key,
                "value": runtime.core_memory.get(key),
            }
        return {"status": "ok", "entries": dict(runtime.core_memory)}

    def write_core_memory(key: str, value: str) -> dict[str, Any]:
        runtime.core_memory[key] = value
        return {"status": "ok", "key": key, "value": value}

    factories["read_core_memory"] = read_core_memory
    factories["write_core_memory"] = write_core_memory

    _interp = interpreter if interpreter is not None else getattr(runtime, "interpreter", None)
    volume_mount_path: str = str(getattr(_interp, "volume_mount_path", "") or "")
    agent_depth: int = int(getattr(runtime, "agent_depth", 0) or 0)

    if volume_mount_path:
        skill_context = default_skill_runtime_context(volume_mount_path=volume_mount_path)

        def _skill_context_with_activations() -> Any:
            """Re-read activations from runtime so late chat-prepare attach is visible."""
            activated_markdown = dict(getattr(runtime, "activated_skill_markdown", None) or {})
            if not activated_markdown:
                return skill_context
            return skill_context.model_copy(update={"activated_skill_markdown": activated_markdown})

        def remember(key: str, value: str) -> dict[str, Any]:
            return _remember_impl(key, value, volume_mount_path=volume_mount_path, agent_depth=agent_depth)

        def recall(query: str) -> dict[str, Any]:
            return _recall_impl(query, volume_mount_path=volume_mount_path)

        def load_document(source: str, alias: str = "active") -> dict[str, Any]:
            return _load_document_impl(source, alias=alias, volume_mount_path=volume_mount_path).model_dump()

        def search_knowledge(query: str, max_results: int = 20) -> dict[str, Any]:
            return _search_knowledge_impl(
                query, volume_mount_path=volume_mount_path, max_results=max_results
            ).model_dump()

        def list_skills() -> dict[str, Any]:
            return list_skills_impl(context=_skill_context_with_activations())

        def load_skill(name: str) -> dict[str, Any]:
            return load_skill_tool_impl(name, context=_skill_context_with_activations())

        def read_skill_resource(name: str, resource_path: str) -> dict[str, Any]:
            return read_skill_resource_impl(name, resource_path, context=_skill_context_with_activations())

        factories["load_document"] = load_document
        factories["list_skills"] = list_skills
        factories["load_skill"] = load_skill
        factories["read_skill_resource"] = read_skill_resource
        factories["remember"] = remember
        factories["recall"] = recall
        factories["search_knowledge"] = search_knowledge

    if interpreter is None:
        return factories

    def run_skill_script(
        skill_name: str,
        script_path: str,
        args: list[Any] | None = None,
        timeout_s: int | None = None,
    ) -> dict[str, Any]:
        deps = SkillExecutionDeps.from_runtime(runtime, volume_mount_path=volume_mount_path or None)
        return run_skill_script_tool_impl(
            skill_name,
            script_path,
            args=args,
            timeout_s=timeout_s,
            context=deps.runtime_context(),
            interpreter=interpreter,
            resources=deps.resources,
            sandbox_paths=deps.sandbox_paths,
        )

    factories["run_skill_script"] = run_skill_script

    sandbox_ctx = _SandboxFilesystemToolContext(interpreter=interpreter)
    session_id = str(getattr(runtime, "_db_session_id", "") or "").strip()

    def execute_code(
        code: str,
        variables: dict[str, Any] | None = None,
        timeout: int = 30,
    ) -> dict[str, Any]:
        _ = timeout
        return coerce_sandbox_result(interpreter.execute(code, variables or {}))

    def list_files(
        path: str = ".",
        root: str = "workspace",
        pattern: str | None = None,
    ) -> dict[str, Any]:
        rejected = reject_legacy_list_files_pattern(pattern)
        if rejected is not None:
            return rejected
        return list_files_impl(path, root=root, interpreter=interpreter)

    def read_file(path: str, root: str = "workspace", max_bytes: int = 200_000) -> dict[str, Any]:
        return read_file_impl(
            path, root=root, max_bytes=max_bytes, interpreter=interpreter, session_id=session_id or None
        )

    def inspect_workspace(path: str = ".", max_entries: int = 50) -> dict[str, Any]:
        return inspect_workspace_impl(path, max_entries=max_entries, interpreter=interpreter)

    def read_buffer(name: str = "default") -> dict[str, Any]:
        return execute_sandbox_tool(
            interpreter,
            "items = get_buffer(buffer_name)\nSUBMIT(status='ok', name=buffer_name, items=items)",
            {"buffer_name": name},
        )

    def write_buffer(name: str, content: str) -> dict[str, Any]:
        return execute_sandbox_tool(
            interpreter,
            "add_buffer(buffer_name, content)\n"
            "items = get_buffer(buffer_name)\n"
            "SUBMIT(status='ok', name=buffer_name, item_count=len(items))",
            {"buffer_name": name, "content": content},
        )

    def clear_buffer(name: str = "default") -> dict[str, Any]:
        return execute_sandbox_tool(
            interpreter,
            "clear_buffer(buffer_name)\nSUBMIT(status='ok', name=buffer_name)",
            {"buffer_name": name},
        )

    def delegate_to_rlm(query: str, context: str = "", document_url: str = "") -> dict[str, Any]:
        return _delegate_to_rlm(
            query=query,
            context=context,
            document_url=document_url,
            interpreter=interpreter,
        )

    def delegate_to_rlm_batched(queries: list[str], context: str = "", document_url: str = "") -> dict[str, Any]:
        return _delegate_to_rlm_batched(
            queries=queries,
            context=context,
            document_url=document_url,
            interpreter=interpreter,
        )

    def _artifact_session_error() -> dict[str, Any]:
        return {
            "status": "error",
            "error": "session_id is required for artifact tools.",
        }

    def create_artifact(
        category: str,
        relative_path: str,
        content: str,
        mime_type: str | None = None,
        title: str | None = None,
    ) -> dict[str, Any]:
        if not session_id:
            return _artifact_session_error()
        return create_artifact_impl(
            session_id=session_id,
            category=category,
            relative_path=relative_path,
            content=content,
            mime_type=mime_type,
            title=title,
            interpreter=interpreter,
        )

    def update_artifact(
        content: str,
        artifact_id: str | None = None,
        category: str | None = None,
        relative_path: str | None = None,
        mime_type: str | None = None,
        title: str | None = None,
    ) -> dict[str, Any]:
        if not session_id:
            return _artifact_session_error()
        return update_artifact_impl(
            session_id=session_id,
            content=content,
            artifact_id=artifact_id,
            category=category,
            relative_path=relative_path,
            mime_type=mime_type,
            title=title,
            interpreter=interpreter,
        )

    def list_artifacts(category: str | None = None) -> dict[str, Any]:
        if not session_id:
            return _artifact_session_error()
        return list_artifacts_impl(
            session_id=session_id,
            category=category,
            interpreter=interpreter,
        )

    def read_artifact(
        artifact_id: str | None = None,
        category: str | None = None,
        relative_path: str | None = None,
        max_bytes: int = 200_000,
    ) -> dict[str, Any]:
        if not session_id:
            return _artifact_session_error()
        return read_artifact_impl(
            session_id=session_id,
            artifact_id=artifact_id,
            category=category,
            relative_path=relative_path,
            max_bytes=max_bytes,
            interpreter=interpreter,
        )

    factories.update(
        {
            "clear_buffer": clear_buffer,
            "create_artifact": create_artifact,
            "delegate_to_rlm": delegate_to_rlm,
            "delegate_to_rlm_batched": delegate_to_rlm_batched,
            "execute_code": execute_code,
            "inspect_workspace": inspect_workspace,
            "list_artifacts": list_artifacts,
            "list_files": list_files,
            "read_buffer": read_buffer,
            "read_artifact": read_artifact,
            "read_file": read_file,
            "sandbox_list_files": functools.partial(_sandbox_list_files_impl, sandbox_ctx),
            "sandbox_read_file": functools.partial(_sandbox_read_file_impl, sandbox_ctx),
            "sandbox_write_file": functools.partial(_sandbox_write_file_impl, sandbox_ctx),
            "sandbox_create_directory": functools.partial(_sandbox_create_directory_impl, sandbox_ctx),
            "sandbox_delete_file": functools.partial(_sandbox_delete_file_impl, sandbox_ctx),
            "sandbox_move_file": functools.partial(_sandbox_move_file_impl, sandbox_ctx),
            "sandbox_search_files": functools.partial(_sandbox_search_files_impl, sandbox_ctx),
            "sandbox_find_in_files": functools.partial(_sandbox_find_in_files_impl, sandbox_ctx),
            "sandbox_replace_in_files": functools.partial(_sandbox_replace_in_files_impl, sandbox_ctx),
            "sandbox_get_file_info": functools.partial(_sandbox_get_file_info_impl, sandbox_ctx),
            "update_artifact": update_artifact,
            "write_buffer": write_buffer,
        }
    )

    def recursive_workspace(
        query: str,
        context: str = "",
        max_passes: int = 3,
    ) -> dict[str, Any]:
        """Run a multi-pass recursive analysis with decomposition and verification."""
        from fleet_rlm.integrations.daytona.isolation import DaytonaEvidenceSink
        from fleet_rlm.runtime.modules.workspace import RecursiveWorkspaceModule

        module = RecursiveWorkspaceModule(
            interpreter=interpreter,
            max_passes=max_passes,
            verbose=False,
            sub_lm=getattr(interpreter, "sub_lm", None),
            evidence_sink=DaytonaEvidenceSink(interpreter),
        )
        prediction = module(
            user_request=query,
            context=_context_with_remote_document(query, context),
        )
        return {
            "status": str(getattr(prediction, "status", "ok")),
            "answer": str(getattr(prediction, "answer", "")),
            "passes": int(getattr(prediction, "passes", 0)),
        }

    factories["recursive_workspace"] = recursive_workspace

    def browser_fetch_page(
        url: str,
        wait_until: str = "networkidle",
        extract_links: bool = False,
    ) -> dict[str, Any]:
        """Fetch a JS-rendered page using Playwright inside the sandbox."""
        _validate_download_url(url)
        return execute_sandbox_tool(
            interpreter,
            _BROWSER_FETCH_PAGE_CODE,
            {"target_url": url, "wait_until": wait_until, "extract_links": extract_links},
        )

    factories["browser_fetch_page"] = browser_fetch_page
    return factories


_BROWSER_FETCH_PAGE_CODE = """\
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    SUBMIT(
        status="error",
        error="Playwright is not installed in this sandbox. "
        "Use a browser-capable sandbox (fleet-rlm-browser snapshot) for rendered page fetching.",
    )
else:
    import logging
    logger = logging.getLogger(__name__)
    with sync_playwright() as p:
        browser = None
        page = None
        try:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            page = browser.new_page()
            page.goto(target_url, wait_until=wait_until, timeout=30000)
            text = page.inner_text("body")
            title = page.title()
            links = []
            if extract_links:
                links = page.eval_on_selector_all(
                    "a[href]",
                    "els => els.map(e => ({href: e.href, text: (e.textContent || '').trim()}))",
                )
            SUBMIT(
                status="ok",
                url=target_url,
                title=title,
                text=text[:200000],
                char_count=len(text),
                links=links[:100] if extract_links else [],
            )
        except Exception:
            logger.exception("Browser execution failed")
            SUBMIT(
                status="error",
                error="Browser execution failed. Please check the logs for details.",
            )
        finally:
            if page is not None:
                page.close()
            if browser is not None:
                browser.close()
"""


def bind_runtime_tools(
    tools: list[Any],
    *,
    runtime: Any,
    interpreter: Any | None,
) -> list[Any]:
    """Bind runtime-backed stubs and remove unavailable interpreter tools."""
    bound_factories = _bound_runtime_tool_factories(
        runtime=runtime,
        interpreter=interpreter,
    )
    result: list[Any] = []
    for tool in tools:
        name = _tool_name(tool)
        if name in bound_factories:
            result.append(_wrap_tool(tool, bound_factories[name]))
            continue
        if interpreter is None and name in INTERPRETER_TOOL_NAMES:
            continue
        result.append(tool)
    return result


__all__ = [
    "INTERPRETER_TOOL_NAMES",
    "bind_runtime_tools",
    "coerce_sandbox_result",
    "execute_sandbox_tool",
]
