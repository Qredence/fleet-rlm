"""Sandbox, RLM-delegate, and buffer/volume tool definitions.

These tools run code inside the Modal sandbox or delegate to RLM
sub-agents.  They are built by :func:`build_sandbox_tools` and merged
into the main tool list by :func:`~fleet_rlm.react.tools.build_tool_list`.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import dspy


from ..core.interpreter import ExecutionProfile
from ..signatures import AnalyzeLongDocument, ExtractFromLogs, SummarizeLongDocument
from .tools import (
    chunk_text,
    chunk_to_text,
    execute_submit,
    resolve_document,
    _rlm_trajectory_payload,
)

if TYPE_CHECKING:
    from .agent import RLMReActChatAgent

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool factory
# ---------------------------------------------------------------------------


def build_sandbox_tools(
    agent: "RLMReActChatAgent",
) -> list[Any]:
    """Build sandbox / RLM / buffer / volume tools bound to *agent*.

    Returns a list of ``dspy.Tool`` wrappers ready to be appended to the
    main tool list built by ``build_tool_list``.
    """

    # -- Long-context analysis -----------------------------------------------

    def parallel_semantic_map(
        query: str,
        chunk_strategy: str = "headers",
        max_chunks: int = 24,
        buffer_name: str = "findings",
    ) -> dict[str, Any]:
        """Run parallel semantic analysis over chunks via llm_query_batched."""
        text = resolve_document(agent, "active")
        chunks = chunk_text(
            text, chunk_strategy, size=80_000, overlap=1_000, pattern=""
        )
        chunk_texts = [chunk_to_text(c) for c in chunks][:max_chunks]

        prompts = []
        for idx, chunk_item in enumerate(chunk_texts):
            prompts.append(
                (
                    f"Query: {query}\n"
                    f"Chunk index: {idx}\n"
                    "Return concise findings as plain text.\n\n"
                    f"{chunk_item[:6000]}"
                )
            )

        code = """
clear_buffer(buffer_name)
responses = llm_query_batched(prompts)
for idx, response in enumerate(responses):
    add_buffer(buffer_name, {"chunk_index": idx, "response": response})

SUBMIT(
    status="ok",
    strategy=chunk_strategy,
    chunk_count=len(prompts),
    findings_count=len(responses),
    buffer_name=buffer_name,
)
"""
        return execute_submit(
            agent,
            code,
            variables={
                "prompts": prompts,
                "buffer_name": buffer_name,
                "chunk_strategy": chunk_strategy,
            },
        )

    def analyze_long_document(
        query: str,
        alias: str = "active",
        include_trajectory: bool = True,
    ) -> dict[str, Any]:
        """Analyze a long document with the AnalyzeLongDocument RLM signature."""
        agent.start()
        document = resolve_document(agent, alias)
        rlm = dspy.RLM(
            signature=AnalyzeLongDocument,
            interpreter=agent.interpreter,
            max_iterations=agent.rlm_max_iterations,
            max_llm_calls=agent.rlm_max_llm_calls,
            verbose=agent.verbose,
        )
        with agent.interpreter.execution_profile(ExecutionProfile.RLM_DELEGATE):
            result = rlm(document=document, query=query)
        response = {
            "status": "ok",
            "findings": result.findings,
            "answer": result.answer,
            "sections_examined": result.sections_examined,
            "doc_chars": len(document),
        }
        response.update(
            _rlm_trajectory_payload(result, include_trajectory=include_trajectory)
        )
        return response

    def summarize_long_document(
        focus: str,
        alias: str = "active",
        include_trajectory: bool = True,
    ) -> dict[str, Any]:
        """Summarize a long document with the SummarizeLongDocument RLM signature."""
        agent.start()
        document = resolve_document(agent, alias)
        rlm = dspy.RLM(
            signature=SummarizeLongDocument,
            interpreter=agent.interpreter,
            max_iterations=agent.rlm_max_iterations,
            max_llm_calls=agent.rlm_max_llm_calls,
            verbose=agent.verbose,
        )
        with agent.interpreter.execution_profile(ExecutionProfile.RLM_DELEGATE):
            result = rlm(document=document, focus=focus)
        response = {
            "status": "ok",
            "summary": result.summary,
            "key_points": result.key_points,
            "coverage_pct": result.coverage_pct,
            "doc_chars": len(document),
        }
        response.update(
            _rlm_trajectory_payload(result, include_trajectory=include_trajectory)
        )
        return response

    def extract_from_logs(
        query: str,
        alias: str = "active",
        include_trajectory: bool = True,
    ) -> dict[str, Any]:
        """Extract structured patterns from log text via ExtractFromLogs RLM signature."""
        agent.start()
        logs = resolve_document(agent, alias)
        rlm = dspy.RLM(
            signature=ExtractFromLogs,
            interpreter=agent.interpreter,
            max_iterations=agent.rlm_max_iterations,
            max_llm_calls=agent.rlm_max_llm_calls,
            verbose=agent.verbose,
        )
        with agent.interpreter.execution_profile(ExecutionProfile.RLM_DELEGATE):
            result = rlm(logs=logs, query=query)
        response = {
            "status": "ok",
            "matches": result.matches,
            "patterns": result.patterns,
            "time_range": result.time_range,
        }
        response.update(
            _rlm_trajectory_payload(result, include_trajectory=include_trajectory)
        )
        return response

    def rlm_query(query: str, context: str = "") -> dict[str, Any]:
        """Delegate a complex sub-task to a recursive sub-agent.

        Spawns a new independent RLM agent to solve the query.
        """
        SubAgentClass = agent.__class__

        sub_agent = SubAgentClass(
            interpreter=agent.interpreter,
        )

        prompt = query
        if context:
            prompt = f"Context:\n{context}\n\nTask: {query}"

        result = sub_agent.chat_turn(prompt)

        return {
            "status": "ok",
            "answer": result.get("answer", ""),
            "sub_agent_history": sub_agent.history_turns(),
        }

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
        code = """
import json
items = get_buffer(name)
payload = json.dumps(items, indent=2, ensure_ascii=False, default=str)
saved_path = save_to_volume(path, payload)
SUBMIT(status="ok", saved_path=saved_path, item_count=len(items))
"""
        return execute_submit(agent, code, variables={"name": name, "path": path})

    def load_text_from_volume(path: str, alias: str = "active") -> dict[str, Any]:
        """Load text from Modal Volume into host-side document memory."""
        result = execute_submit(
            agent,
            'text = load_from_volume(path)\nSUBMIT(status="ok", text=text)',
            variables={"path": path},
        )
        text = str(result.get("text", ""))
        agent._set_document(alias, text)
        agent.active_alias = alias
        return {
            "status": "ok",
            "alias": alias,
            "chars": len(text),
            "lines": len(text.splitlines()),
        }

    # -- Persistent memory management ----------------------------------------
    # These tools allow the agent to use the mounted volume as a persistent
    # "hard drive" for storing user profiles, archival documents, etc.

    def memory_read(path: str) -> dict[str, Any]:
        """Read a file from persistent memory (Modal Volume)."""
        # Ensure path is relative to volume root if possible, or absolute.
        # Ideally, we should enforce a /data/memory root, but for now we trust the mount path.
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
        return execute_submit(agent, code, variables={"path": path})

    def memory_write(path: str, content: str) -> dict[str, Any]:
        """Write content to a file in persistent memory (Modal Volume)."""
        code = """
import os
try:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    # Sync filesystem to volume if supported (optional but good for safety)
    try:
        os.sync()
    except AttributeError:
        pass
    SUBMIT(status="ok", path=path, chars=len(content))
except Exception as e:
    SUBMIT(status="error", error=f"{type(e).__name__}: {e}")
"""
        # Note: Modal volumes are eventually consistent, but os.sync() helps.
        # The Interpreter also exposes a .commit() method if needed on the host side.
        result = execute_submit(
            agent, code, variables={"path": path, "content": content}
        )

        # Trigger explicit commit on the host side for immediate persistence
        if result.get("status") == "ok":
            if agent.interpreter._volume:
                try:
                    agent.interpreter.commit()
                except Exception:
                    pass  # Ignore commit errors, best effort
        return result

    def memory_list(path: str = ".") -> dict[str, Any]:
        """List files and directories in persistent memory."""
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
        return execute_submit(agent, code, variables={"path": path})

    # -- Assemble tool list --------------------------------------------------

    from dspy import Tool

    return [
        Tool(
            parallel_semantic_map,
            name="parallel_semantic_map",
            desc="Run parallel semantic analysis over chunks via llm_query_batched",
        ),
        Tool(
            analyze_long_document,
            name="analyze_long_document",
            desc="Analyze a long document with the AnalyzeLongDocument RLM signature",
        ),
        Tool(
            summarize_long_document,
            name="summarize_long_document",
            desc="Summarize a long document with the SummarizeLongDocument RLM signature",
        ),
        Tool(
            extract_from_logs,
            name="extract_from_logs",
            desc="Extract structured patterns from log text via ExtractFromLogs RLM signature",
        ),
        Tool(
            rlm_query,
            name="rlm_query",
            desc="Delegate a complex sub-task to a recursive sub-agent",
        ),
        Tool(
            edit_file,
            name="edit_file",
            desc="Robustly edit a file by finding and replacing a unique text snippet",
        ),
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
            memory_list,
            name="memory_list",
            desc="List files and directories in persistent memory",
        ),
    ]
