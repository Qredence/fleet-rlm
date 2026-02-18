"""Sandbox, buffer, and volume tool definitions.

These tools run code inside the Modal sandbox or manage buffer/volume
operations. They are built by :func:`build_sandbox_tools` and merged
into the main tool list by :func:`~fleet_rlm.react.tools.build_tool_list`.

RLM delegation tools have been extracted to tools_rlm_delegate.py and
memory intelligence tools to tools_memory_intelligence.py as part of
the modularization effort (Linear: QRE-273).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .tools import execute_submit
from .tools_memory_intelligence import build_memory_intelligence_tools
from .tools_rlm_delegate import build_rlm_delegate_tools
from .tools_sandbox_helpers import _resolve_volume_path

if TYPE_CHECKING:
    from .agent import RLMReActChatAgent


# ---------------------------------------------------------------------------
# Tool factory
# ---------------------------------------------------------------------------


def build_sandbox_tools(
    agent: "RLMReActChatAgent",
) -> list[Any]:
    """Build sandbox / buffer / volume tools bound to *agent*.

    Returns a list of ``dspy.Tool`` wrappers ready to be appended to the
    main tool list built by ``build_tool_list``.
    """
    tools: list[Any] = []

    # -- RLM delegation tools (extracted to tools_rlm_delegate.py) ------------
    tools.extend(build_rlm_delegate_tools(agent))

    # -- Memory intelligence tools (extracted to tools_memory_intelligence.py) -
    tools.extend(build_memory_intelligence_tools(agent))

    # -- Sandbox editing -----------------------------------------------------

    def edit_file(path: str, old_snippet: str, new_snippet: str) -> dict[str, Any]:
        """Robustly edit a file by finding and replacing a unique text snippet.

        Fails if the old_snippet is not found or is not unique in the file.
        Use this over fragile `sed` commands for precise code editing.
        """
        code = """
try:
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
except FileNotFoundError:
    SUBMIT(status="error", error=f"File not found: {path}")
    exit(0)

count = content.count(old_snippet)
if count == 0:
    SUBMIT(status="error", error="old_snippet not found in file")
elif count > 1:
    SUBMIT(status="error", error=f"old_snippet is ambiguous (found {count} times)")
else:
    new_content = content.replace(old_snippet, new_snippet)
    with open(path, "w", encoding="utf-8") as f:
        f.write(new_content)
    SUBMIT(status="ok", path=path, message="File updated successfully")
"""
        return execute_submit(
            agent,
            code,
            variables={
                "path": path,
                "old_snippet": old_snippet,
                "new_snippet": new_snippet,
            },
        )

    # -- Buffer & volume management ------------------------------------------

    def read_buffer(name: str) -> dict[str, Any]:
        """Read the full contents of a sandbox buffer."""
        result = execute_submit(
            agent, "SUBMIT(items=get_buffer(name))", variables={"name": name}
        )
        items = result.get("items", [])
        return {"status": "ok", "name": name, "items": items, "count": len(items)}

    def clear_buffer(name: str = "") -> dict[str, Any]:
        """Clear one sandbox buffer (or all buffers when name is empty)."""
        if name:
            code = 'clear_buffer(name)\nSUBMIT(status="ok", scope="single", name=name)'
            variables: dict[str, Any] = {"name": name}
        else:
            code = 'clear_buffer()\nSUBMIT(status="ok", scope="all")'
            variables = {}
        return execute_submit(agent, code, variables=variables)

    def save_buffer_to_volume(name: str, path: str) -> dict[str, Any]:
        """Persist a sandbox buffer to Modal Volume storage as JSON."""
        try:
            resolved_path = _resolve_volume_path(
                path,
                default_root="/data/workspace/buffers",
                allowed_root="/data",
            )
        except ValueError as exc:
            return {"status": "error", "error": str(exc)}

        code = """
import json
items = get_buffer(name)
payload = json.dumps(items, indent=2, ensure_ascii=False, default=str)
saved_path = save_to_volume(path, payload)
SUBMIT(status="ok", saved_path=saved_path, item_count=len(items))
"""
        result = execute_submit(
            agent,
            code,
            variables={"name": name, "path": resolved_path},
        )
        if result.get("status") == "ok" and agent.interpreter._volume:
            try:
                agent.interpreter.commit()
            except Exception:
                pass
        return result

    def load_text_from_volume(path: str, alias: str = "active") -> dict[str, Any]:
        """Load text from Modal Volume into host-side document memory."""
        try:
            resolved_path = _resolve_volume_path(
                path,
                default_root="/data/workspace",
                allowed_root="/data",
            )
        except ValueError as exc:
            return {"status": "error", "error": str(exc)}

        if agent.interpreter._volume:
            try:
                # Pull latest writes from other containers before reading.
                agent.interpreter.reload()
            except Exception:
                pass

        result = execute_submit(
            agent,
            'text = load_from_volume(path)\nSUBMIT(status="ok", text=text)',
            variables={"path": resolved_path},
        )
        text = str(result.get("text", ""))
        if text.startswith("[error:"):
            return {"status": "error", "error": text, "path": resolved_path}
        agent._set_document(alias, text)
        agent.active_alias = alias
        return {
            "status": "ok",
            "alias": alias,
            "path": resolved_path,
            "chars": len(text),
            "lines": len(text.splitlines()),
        }

    def process_document(path: str, alias: str = "active") -> dict[str, Any]:
        """Load a document from volume and register it for downstream analysis."""
        loaded = load_text_from_volume(path, alias=alias)
        if loaded.get("status") != "ok":
            return loaded
        text = agent._documents.get(alias, "")
        return {
            "status": "ok",
            "alias": alias,
            "path": loaded.get("path", path),
            "chars": len(text),
            "lines": len(text.splitlines()),
            "hint": "Use analyze_long_document or summarize_long_document for semantic processing.",
        }

    # -- Persistent memory management ----------------------------------------
    # These tools allow the agent to use the mounted volume as a persistent
    # "hard drive" for storing user profiles, archival documents, etc.

    def memory_read(path: str) -> dict[str, Any]:
        """Read a file from persistent memory (Modal Volume)."""
        try:
            resolved_path = _resolve_volume_path(
                path,
                default_root="/data/memory",
                allowed_root="/data",
            )
        except ValueError as exc:
            return {"status": "error", "error": str(exc)}

        if agent.interpreter._volume:
            try:
                agent.interpreter.reload()
            except Exception:
                pass

        code = """
try:
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    SUBMIT(status="ok", path=path, content=content, chars=len(content))
except FileNotFoundError:
    SUBMIT(status="error", error=f"File not found: {path}")
except Exception as e:
    SUBMIT(status="error", error=f"{type(e).__name__}: {e}")
"""
        return execute_submit(agent, code, variables={"path": resolved_path})

    def memory_write(path: str, content: str) -> dict[str, Any]:
        """Write content to a file in persistent memory (Modal Volume)."""
        try:
            resolved_path = _resolve_volume_path(
                path,
                default_root="/data/memory",
                allowed_root="/data",
            )
        except ValueError as exc:
            return {"status": "error", "error": str(exc)}

        code = """
import os
import subprocess
try:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    # Sync filesystem to volume if supported (optional but good for safety)
    try:
        os.sync()
    except AttributeError:
        pass
    sync_rc = 0
    try:
        proc = subprocess.run(["sync", "/data"], check=False, capture_output=True)
        sync_rc = int(proc.returncode)
    except Exception:
        sync_rc = -1
    SUBMIT(status="ok", path=path, chars=len(content), sync_rc=sync_rc)
except Exception as e:
    SUBMIT(status="error", error=f"{type(e).__name__}: {e}")
"""
        # Note: Modal volumes are eventually consistent, but os.sync() helps.
        # The Interpreter also exposes a .commit() method if needed on the host side.
        result = execute_submit(
            agent,
            code,
            variables={"path": resolved_path, "content": content},
        )

        # Trigger explicit commit on the host side for immediate persistence
        if result.get("status") == "ok":
            if agent.interpreter._volume:
                try:
                    agent.interpreter.commit()
                except Exception:
                    pass  # Ignore commit errors, best effort
        return result

    def write_to_file(path: str, content: str, append: bool = False) -> dict[str, Any]:
        """Write/append text to a file in Modal Volume with safe path handling."""
        if not append:
            return memory_write(path=path, content=content)

        try:
            resolved_path = _resolve_volume_path(
                path,
                default_root="/data/memory",
                allowed_root="/data",
            )
        except ValueError as exc:
            return {"status": "error", "error": str(exc)}

        code = """
import os
import subprocess
try:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(content)
    try:
        os.sync()
    except AttributeError:
        pass
    sync_rc = 0
    try:
        proc = subprocess.run(["sync", "/data"], check=False, capture_output=True)
        sync_rc = int(proc.returncode)
    except Exception:
        sync_rc = -1
    SUBMIT(status="ok", path=path, chars=len(content), mode="append", sync_rc=sync_rc)
except Exception as e:
    SUBMIT(status="error", error=f"{type(e).__name__}: {e}")
"""
        result = execute_submit(
            agent,
            code,
            variables={"path": resolved_path, "content": content},
        )
        if result.get("status") == "ok" and agent.interpreter._volume:
            try:
                agent.interpreter.commit()
            except Exception:
                pass
        return result

    def edit_core_memory(
        section: str,
        content: str,
        mode: str = "append",
    ) -> dict[str, Any]:
        """Edit core memory via append/replace operations with validation."""
        mode_norm = mode.strip().lower()
        if mode_norm not in {"append", "replace"}:
            return {
                "status": "error",
                "error": "mode must be one of: append, replace",
            }

        message = (
            agent.core_memory_append(section, content)
            if mode_norm == "append"
            else agent.core_memory_replace(section, content)
        )
        if message.startswith("Error:"):
            return {"status": "error", "error": message}

        return {
            "status": "ok",
            "section": section,
            "mode": mode_norm,
            "message": message,
            "chars": len(agent._core_memory.get(section, "")),
        }

    def memory_list(path: str = ".") -> dict[str, Any]:
        """List files and directories in persistent memory."""
        try:
            resolved_path = (
                _resolve_volume_path(
                    path,
                    default_root="/data/memory",
                    allowed_root="/data",
                )
                if path.strip() not in {"", ".", "./"}
                else "/data/memory"
            )
        except ValueError as exc:
            return {"status": "error", "error": str(exc)}

        if agent.interpreter._volume:
            try:
                agent.interpreter.reload()
            except Exception:
                pass

        code = """
import os
try:
    items = []
    for name in os.listdir(path):
        full = os.path.join(path, name)
        kind = "dir" if os.path.isdir(full) else "file"
        items.append({"name": name, "type": kind})
    SUBMIT(status="ok", path=path, items=items, count=len(items))
except Exception as e:
    SUBMIT(status="error", error=f"{type(e).__name__}: {e}")
"""
        return execute_submit(agent, code, variables={"path": resolved_path})

    # -- Assemble tool list --------------------------------------------------

    from dspy import Tool

    # Sandbox editing tools
    tools.append(
        Tool(
            edit_file,
            name="edit_file",
            desc="Robustly edit a file by finding and replacing a unique text snippet",
        )
    )

    # Buffer & volume tools
    tools.extend(
        [
            Tool(
                read_buffer,
                name="read_buffer",
                desc="Read the full contents of a sandbox buffer",
            ),
            Tool(
                clear_buffer,
                name="clear_buffer",
                desc="Clear one sandbox buffer (or all buffers when name is empty)",
            ),
            Tool(
                save_buffer_to_volume,
                name="save_buffer_to_volume",
                desc="Persist a sandbox buffer to Modal Volume storage as JSON",
            ),
            Tool(
                load_text_from_volume,
                name="load_text_from_volume",
                desc="Load text from Modal Volume into host-side document memory",
            ),
            Tool(
                process_document,
                name="process_document",
                desc="Load a document from Modal Volume and register it for analysis",
            ),
        ]
    )

    # Persistent memory tools
    tools.extend(
        [
            Tool(
                memory_read,
                name="memory_read",
                desc="Read a file from persistent memory (Modal Volume)",
            ),
            Tool(
                memory_write,
                name="memory_write",
                desc="Write content to a file in persistent memory (Modal Volume)",
            ),
            Tool(
                write_to_file,
                name="write_to_file",
                desc="Write or append text to a file in persistent memory (Modal Volume)",
            ),
            Tool(
                edit_core_memory,
                name="edit_core_memory",
                desc="Edit core memory blocks using append or replace mode",
            ),
            Tool(
                memory_list,
                name="memory_list",
                desc="List files and directories in persistent memory",
            ),
        ]
    )

    return tools
