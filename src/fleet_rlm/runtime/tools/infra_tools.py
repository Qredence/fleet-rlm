"""DSPy tools for Daytona snapshot and LSP management.

Exposes snapshot lifecycle and code-intelligence helpers as ``dspy.Tool``
instances so the ReAct agent can manage sandbox cold-start optimisation
and request code completions/diagnostics through tool calls.

Reference:
  - Daytona SDK: ``AsyncSnapshotService.create/get/list``
  - Daytona SDK: ``Sandbox.create_lsp_server`` → ``LspServer.start/completions/stop``
  - DSPy: ``dspy.Tool(func, name=..., desc=...)``
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fleet_rlm.runtime.agent.chat_agent import RLMReActChatAgent

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Snapshot tools
# ---------------------------------------------------------------------------


def build_snapshot_tools(agent: RLMReActChatAgent) -> list[Any]:
    """Return ``dspy.Tool`` wrappers for Daytona snapshot management.

    Tools:
      - ``list_snapshots``: List available snapshots with state info.
      - ``resolve_snapshot``: Check if a named snapshot is ACTIVE and usable.
    """
    from dspy import Tool

    def list_snapshots(limit: int = 20) -> str:
        """List available Daytona snapshots with name, state, and image.

        Args:
            limit: Maximum number of snapshots to return (default 20).
        """
        from fleet_rlm.integrations.providers.daytona.snapshots import alist_snapshots
        from fleet_rlm.integrations.providers.daytona.runtime_helpers import (
            _run_async_compat,
        )

        try:
            items = _run_async_compat(alist_snapshots)
            return json.dumps(items[:limit], indent=2)
        except Exception as exc:
            return f"Error listing snapshots: {exc}"

    def resolve_snapshot(name: str = "fleet-rlm-base") -> str:
        """Check whether a named snapshot exists and is ACTIVE.

        Args:
            name: Snapshot name to resolve (default 'fleet-rlm-base').

        Returns:
            The snapshot name if active, or an explanation if unavailable.
        """
        from fleet_rlm.integrations.providers.daytona.snapshots import aresolve_snapshot
        from fleet_rlm.integrations.providers.daytona.runtime_helpers import (
            _run_async_compat,
        )

        try:
            result = _run_async_compat(aresolve_snapshot, name)
            if result:
                return f"Snapshot '{result}' is ACTIVE and ready."
            return f"Snapshot '{name}' is not available or not in ACTIVE state."
        except Exception as exc:
            return f"Error resolving snapshot: {exc}"

    return [
        Tool(
            list_snapshots,
            name="list_snapshots",
            desc="List available Daytona snapshots with name, state, and image info.",
        ),
        Tool(
            resolve_snapshot,
            name="resolve_snapshot",
            desc="Check if a named Daytona snapshot is ACTIVE and usable for sandbox creation.",
        ),
    ]


# ---------------------------------------------------------------------------
# LSP tools
# ---------------------------------------------------------------------------


def build_lsp_tools(agent: RLMReActChatAgent) -> list[Any]:
    """Return ``dspy.Tool`` wrappers for Daytona LSP code intelligence.

    Tools:
      - ``lsp_completions``: Get code completions at a file position.
      - ``lsp_document_symbols``: List symbols (functions, classes) in a file.
    """
    from dspy import Tool

    def lsp_completions(file_path: str, line: int, character: int) -> str:
        """Get code completions at a specific position in a file.

        Uses the Daytona sandbox's native LSP server (per Daytona SDK
        ``Sandbox.create_lsp_server``) for Python code intelligence.

        Args:
            file_path: Path to the file in the sandbox workspace.
            line: Zero-based line number.
            character: Zero-based character offset.
        """
        from fleet_rlm.integrations.providers.daytona.runtime_helpers import (
            _run_async_compat,
        )

        async def _get_completions() -> str:
            session = getattr(agent, "_session", None) or getattr(
                agent, "interpreter", None
            )
            if session is None:
                return "No active sandbox session for LSP."
            sess = getattr(session, "_session", session)
            if not hasattr(sess, "create_lsp_server"):
                return "Current session does not support LSP."
            lsp = sess.create_lsp_server(language="python")
            try:
                await lsp.start()
                await lsp.did_open(file_path)
                items = await lsp.completions(file_path, line, character)
                result = []
                for item in getattr(items, "items", items) or []:
                    label = getattr(item, "label", str(item))
                    kind = getattr(item, "kind", "")
                    result.append(f"{label} ({kind})" if kind else label)
                return json.dumps(result[:30]) if result else "No completions found."
            finally:
                await lsp.stop()

        try:
            return _run_async_compat(_get_completions)
        except Exception as exc:
            return f"LSP error: {exc}"

    def lsp_document_symbols(file_path: str) -> str:
        """List symbols (functions, classes, variables) in a file.

        Uses the Daytona sandbox's native LSP server for symbol discovery.

        Args:
            file_path: Path to the file in the sandbox workspace.
        """
        from fleet_rlm.integrations.providers.daytona.runtime_helpers import (
            _run_async_compat,
        )

        async def _get_symbols() -> str:
            session = getattr(agent, "_session", None) or getattr(
                agent, "interpreter", None
            )
            if session is None:
                return "No active sandbox session for LSP."
            sess = getattr(session, "_session", session)
            if not hasattr(sess, "create_lsp_server"):
                return "Current session does not support LSP."
            lsp = sess.create_lsp_server(language="python")
            try:
                await lsp.start()
                await lsp.did_open(file_path)
                symbols = await lsp.document_symbols(file_path)
                result = []
                for sym in symbols or []:
                    name = getattr(sym, "name", str(sym))
                    kind = getattr(sym, "kind", "")
                    result.append(f"{name} ({kind})" if kind else name)
                return json.dumps(result[:50]) if result else "No symbols found."
            finally:
                await lsp.stop()

        try:
            return _run_async_compat(_get_symbols)
        except Exception as exc:
            return f"LSP error: {exc}"

    return [
        Tool(
            lsp_completions,
            name="lsp_completions",
            desc="Get code completions at a file:line:character position using sandbox LSP.",
        ),
        Tool(
            lsp_document_symbols,
            name="lsp_document_symbols",
            desc="List functions, classes, and variables in a file using sandbox LSP.",
        ),
    ]
