"""P40 contract checks for explicit Session Workspace and Project hosts."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from fleet_rlm.workspace.projects import ProjectToolHost
from fleet_rlm.workspace.workspace import WorkspaceToolHost

WORKSPACE_SOURCE = Path(__file__).parents[3] / "src" / "fleet_rlm" / "workspace"
FORBIDDEN_GENERIC_SYMBOLS = (
    "WorkspaceLikeConfig",
    "workspace_like_tools",
    "workspace_like_event_views",
)


def _filesystem_sources() -> tuple[Path, ...]:
    return tuple(sorted(WORKSPACE_SOURCE.glob("*.py")))


def test_workspace_like_factory_and_dynamic_event_views_are_deleted() -> None:
    source = "\n".join(path.read_text(encoding="utf-8") for path in _filesystem_sources())

    for symbol in FORBIDDEN_GENERIC_SYMBOLS:
        assert symbol not in source


def test_session_and_project_hosts_are_explicit_classes_with_owned_tools() -> None:
    workspace_tree = ast.parse(
        (WORKSPACE_SOURCE / "workspace.py").read_text(encoding="utf-8"),
        filename="workspace_tools.py",
    )
    project_tree = ast.parse(
        (WORKSPACE_SOURCE / "projects.py").read_text(encoding="utf-8"),
        filename="project_tools.py",
    )

    workspace_hosts = [
        node for node in workspace_tree.body if isinstance(node, ast.ClassDef) and node.name == "WorkspaceToolHost"
    ]
    project_hosts = [
        node for node in project_tree.body if isinstance(node, ast.ClassDef) and node.name == "ProjectToolHost"
    ]
    assert len(workspace_hosts) == 1
    assert len(project_hosts) == 1

    for host in (*workspace_hosts, *project_hosts):
        assert any(isinstance(node, ast.FunctionDef) and node.name == "as_tools" for node in host.body)
        assert any(isinstance(node, ast.FunctionDef) and node.name == "event_views" for node in host.body)


def test_canonical_tool_catalogs_match_the_frozen_snapshots() -> None:
    session_tools = WorkspaceToolHost(None, max_file_bytes=1).as_tools()  # type: ignore[arg-type]
    project_tools = ProjectToolHost(None, max_file_bytes=1).as_tools()  # type: ignore[arg-type]

    assert tuple((str(tool.name), inspect.signature(tool.func), tool.args, tool.desc) for tool in session_tools) == (
        (
            "list_workspace_files",
            inspect.signature(session_tools[0].func),
            {
                "path": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                "after": {"type": ["string", "null"]},
            },
            "List immediate entries in this Session's durable Workspace only when existing durable "
            "state is relevant; do not explore it for a self-contained request.",
        ),
        (
            "stat_workspace_file",
            inspect.signature(session_tools[1].func),
            {"path": {"type": "string"}},
            "Read bounded metadata for a relevant durable Session Workspace path.",
        ),
        (
            "read_workspace_text",
            inspect.signature(session_tools[2].func),
            {
                "path": {"type": "string"},
                "cursor": {"type": ["string", "null"]},
                "max_chars": {"type": "integer", "minimum": 1, "maximum": 10_000},
            },
            "Read one relevant UTF-8 durable Workspace page with max_chars in 1..10000. Continue "
            "with next_cursor until eof.",
        ),
        (
            "write_workspace_text",
            inspect.signature(session_tools[3].func),
            {
                "path": {"type": "string"},
                "content": {"type": "string"},
                "overwrite": {"type": "boolean"},
            },
            "Write UTF-8 text immediately into this Session's durable Workspace when the result must "
            "survive the Run; this durability is independent of Turn Commit.",
        ),
        (
            "append_workspace_text",
            inspect.signature(session_tools[4].func),
            {"path": {"type": "string"}, "content": {"type": "string"}},
            "Append UTF-8 text immediately into this Session's durable Workspace when incremental "
            "state must survive the Run; this durability is independent of Turn Commit.",
        ),
        (
            "delete_workspace_path",
            inspect.signature(session_tools[5].func),
            {"path": {"type": "string"}, "expected_sha256": {"type": ["string", "null"]}},
            "Delete one file or one empty directory immediately from this Session's durable "
            "Workspace; non-empty directories are refused, and a supplied expected_sha256 guards "
            "against deleting changed content. This durability is independent of Turn Commit.",
        ),
        (
            "edit_workspace_text",
            inspect.signature(session_tools[6].func),
            {
                "path": {"type": "string"},
                "old": {"type": "string"},
                "new": {"type": "string"},
                "expected_sha256": {"type": ["string", "null"]},
            },
            "Replace exactly one unique occurrence of old with new in one UTF-8 Session Workspace "
            "file; the edit fails when old is absent or occurs more than once, and a supplied "
            "expected_sha256 guards against editing changed content. Read the file first and keep "
            "old short and unique. This durability is independent of Turn Commit.",
        ),
    )

    assert tuple((str(tool.name), inspect.signature(tool.func), tool.args, tool.desc) for tool in project_tools) == (
        (
            "list_project_files",
            inspect.signature(project_tools[0].func),
            {
                "path": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                "after": {"type": ["string", "null"]},
            },
            "List immediate entries under projects/<slug>/ (or the projects root) only when existing "
            "durable Project deliverables are relevant; do not explore them for a self-contained request.",
        ),
        (
            "stat_project_file",
            inspect.signature(project_tools[1].func),
            {"path": {"type": "string"}},
            "Read bounded metadata for a relevant durable Project deliverable path under projects/<slug>/.",
        ),
        (
            "read_project_text",
            inspect.signature(project_tools[2].func),
            {
                "path": {"type": "string"},
                "cursor": {"type": ["string", "null"]},
                "max_chars": {"type": "integer", "minimum": 1, "maximum": 10_000},
            },
            "Read one relevant UTF-8 Project deliverable page with max_chars in 1..10000 using a "
            "projects/<slug>/<path> target. Continue with next_cursor until eof.",
        ),
        (
            "write_project_text",
            inspect.signature(project_tools[3].func),
            {
                "path": {"type": "string"},
                "content": {"type": "string"},
                "overwrite": {"type": "boolean"},
            },
            "Write UTF-8 text immediately as a durable deliverable under projects/<slug>/ when the "
            "result must stay browsable across Sessions; choose a short repo/task-derived slug and "
            "keep scratch in the Session Workspace. This durability is independent of Turn Commit.",
        ),
        (
            "delete_project_path",
            inspect.signature(project_tools[4].func),
            {"path": {"type": "string"}, "expected_sha256": {"type": ["string", "null"]}},
            "Delete one file or one empty directory immediately under projects/<slug>/; non-empty "
            "directories are refused, and a supplied expected_sha256 guards against deleting changed "
            "content. This durability is independent of Turn Commit.",
        ),
        (
            "edit_project_text",
            inspect.signature(project_tools[5].func),
            {
                "path": {"type": "string"},
                "old": {"type": "string"},
                "new": {"type": "string"},
                "expected_sha256": {"type": ["string", "null"]},
            },
            "Replace exactly one unique occurrence of old with new in one UTF-8 Project file under "
            "projects/<slug>/; the edit fails when old is absent or occurs more than once, and a "
            "supplied expected_sha256 guards against editing changed content. Read the file first "
            "and keep old short and unique. This durability is independent of Turn Commit.",
        ),
    )
