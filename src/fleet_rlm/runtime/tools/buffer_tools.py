"""Buffer-oriented sandbox tool builders."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from fleet_rlm.runtime.agent.tool_delegation import _sync_compatible_tool_callable

from .sandbox_common import (
    _adaytona_read_text,
    _adaytona_write_text,
    _aexecute_submit_ctx,
    _aget_daytona_session,
    _commit_volume_best_effort,
    _daytona_file_error,
    _document_load_result,
    _persistent_roots,
    _reload_volume_best_effort,
    _resolve_path_or_error,
    _SandboxToolContext,
)

if TYPE_CHECKING:
    from ..agent.chat_agent import RLMReActChatAgent


def build_buffer_tools(agent: RLMReActChatAgent) -> list[Any]:
    """Build sandbox buffer and volume-load tools bound to *agent*."""
    from dspy import Tool

    ctx = _SandboxToolContext(agent=agent)
    tools: list[Any] = []

    async def read_buffer(name: str) -> dict[str, Any]:
        """Read the full contents of a sandbox buffer."""
        result = await _aexecute_submit_ctx(
            ctx,
            "SUBMIT(items=get_buffer(name))",
            variables={"name": name},
        )
        items = result.get("items", [])
        return {"status": "ok", "name": name, "items": items, "count": len(items)}

    async def clear_buffer(name: str = "") -> dict[str, Any]:
        """Clear one sandbox buffer (or all buffers when name is empty)."""
        if name:
            code = 'clear_buffer(name)\nSUBMIT(status="ok", scope="single", name=name)'
            variables: dict[str, Any] = {"name": name}
        else:
            code = 'clear_buffer()\nSUBMIT(status="ok", scope="all")'
            variables = {}
        return await _aexecute_submit_ctx(ctx, code, variables=variables)

    async def save_buffer_to_volume(name: str, path: str) -> dict[str, Any]:
        """Persist a sandbox buffer to persistent storage as JSON."""
        roots = _persistent_roots(ctx)
        resolved_path, error = _resolve_path_or_error(
            path=path,
            default_root=roots.buffers_root,
            allowed_root=roots.allowed_root,
        )
        if error is not None:
            return error
        assert resolved_path is not None

        daytona_session = await _aget_daytona_session(ctx)
        if daytona_session is not None:
            result = await _aexecute_submit_ctx(
                ctx,
                "SUBMIT(items=get_buffer(name))",
                variables={"name": name},
            )
            items = result.get("items", [])
            payload = json.dumps(items, indent=2, ensure_ascii=False, default=str)
            try:
                saved_path = await _adaytona_write_text(
                    daytona_session,
                    resolved_path,
                    payload,
                )
            except Exception as exc:
                return _daytona_file_error(path=resolved_path, exc=exc)
            return {"status": "ok", "saved_path": saved_path, "item_count": len(items)}

        code = """
import json
items = get_buffer(name)
payload = json.dumps(items, indent=2, ensure_ascii=False, default=str)
saved_path = save_to_volume(path, payload)
SUBMIT(status="ok", saved_path=saved_path, item_count=len(items))
"""
        result = await _aexecute_submit_ctx(
            ctx,
            code,
            variables={"name": name, "path": resolved_path},
        )
        if result.get("status") == "ok":
            _commit_volume_best_effort(ctx)
        return result

    async def load_text_from_volume(path: str, alias: str = "active") -> dict[str, Any]:
        """Load text from persistent storage into host-side document memory."""
        roots = _persistent_roots(ctx)
        resolved_path, error = _resolve_path_or_error(
            path=path,
            default_root=roots.artifacts_root,
            allowed_root=roots.allowed_root,
        )
        if error is not None:
            return error
        assert resolved_path is not None

        daytona_session = await _aget_daytona_session(ctx)
        if daytona_session is not None:
            try:
                text = await _adaytona_read_text(daytona_session, resolved_path)
            except Exception as exc:
                return _daytona_file_error(path=resolved_path, exc=exc)
            return _document_load_result(
                ctx,
                alias=alias,
                path=resolved_path,
                text=text,
            )

        _reload_volume_best_effort(ctx)

        result = await _aexecute_submit_ctx(
            ctx,
            'text = load_from_volume(path)\nSUBMIT(status="ok", text=text)',
            variables={"path": resolved_path},
        )
        text = str(result.get("text", ""))
        if text.startswith("[error:"):
            return {"status": "error", "error": text, "path": resolved_path}
        return _document_load_result(
            ctx,
            alias=alias,
            path=resolved_path,
            text=text,
        )

    async def process_document(path: str, alias: str = "active") -> dict[str, Any]:
        """Load a durable volume-backed document and register it for analysis."""
        loaded = await load_text_from_volume(path, alias=alias)
        if loaded.get("status") != "ok":
            return loaded
        text = ctx.agent.documents.get(alias, "")
        return {
            "status": "ok",
            "alias": alias,
            "path": loaded.get("path", path),
            "chars": len(text),
            "lines": len(text.splitlines()),
            "hint": "Preferred over workspace_read for durable document analysis. Use load_document for host, URL, or transient Daytona workspace files.",
        }

    tools.extend(
        [
            Tool(
                _sync_compatible_tool_callable(read_buffer),
                name="read_buffer",
                desc="Read the full contents of a sandbox buffer",
            ),
            Tool(
                _sync_compatible_tool_callable(clear_buffer),
                name="clear_buffer",
                desc="Clear one sandbox buffer (or all buffers when name is empty)",
            ),
            Tool(
                _sync_compatible_tool_callable(save_buffer_to_volume),
                name="save_buffer_to_volume",
                desc="Persist a sandbox buffer to durable mounted-volume storage as JSON",
            ),
            Tool(
                _sync_compatible_tool_callable(load_text_from_volume),
                name="load_text_from_volume",
                desc="Load text from the durable mounted volume (artifacts by default) into host-side document memory",
            ),
            Tool(
                _sync_compatible_tool_callable(process_document),
                name="process_document",
                desc="Load a durable volume-backed document into agent memory and register it for downstream analysis",
            ),
        ]
    )

    return tools
