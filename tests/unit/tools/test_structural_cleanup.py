from __future__ import annotations

import importlib
import inspect
from types import SimpleNamespace
from typing import Any

from fleet_rlm.runtime.tools.binding import bind_runtime_tools
from fleet_rlm.runtime.tools.registry import discover_tools, list_react_tool_names
from fleet_rlm.runtime.tools.sandbox_filesystem import (
    _sandbox_list_files_impl,
    _sandbox_read_file_impl,
    _SandboxFilesystemToolContext,
)
from fleet_rlm.tools.filesystem import list_files_impl, read_file_impl
from fleet_rlm.tools.registry import descriptor_by_name


class _FakeSession:
    workspace_path = "/workspace/repo"

    def __init__(self) -> None:
        self.list_calls: list[str] = []
        self.read_calls: list[str] = []
        self.file_contents = {
            "/workspace/repo/README.md": "hello fleet",
        }
        self.list_entries = {
            "/workspace/repo": [
                SimpleNamespace(name="README.md", is_dir=False, size=11),
            ],
        }

    def list_files(self, path: str) -> list[Any]:
        self.list_calls.append(path)
        return self.list_entries.get(path, [])

    def read_file(self, path: str) -> str:
        self.read_calls.append(path)
        return self.file_contents[path]


def _interpreter(session: _FakeSession | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        _session=session or _FakeSession(),
        volume_mount_path="/home/daytona/memory",
    )


def test_sandbox_list_files_alias_matches_canonical_list_files() -> None:
    session = _FakeSession()
    interpreter = _interpreter(session)
    ctx = _SandboxFilesystemToolContext(interpreter=interpreter)

    alias_payload = _sandbox_list_files_impl(ctx, path=".")
    canonical_payload = list_files_impl(".", root="workspace", interpreter=interpreter)

    assert alias_payload == canonical_payload


def test_sandbox_read_file_alias_matches_canonical_read_file() -> None:
    session = _FakeSession()
    interpreter = _interpreter(session)
    ctx = _SandboxFilesystemToolContext(interpreter=interpreter)

    alias_payload = _sandbox_read_file_impl(ctx, path="README.md")
    canonical_payload = read_file_impl("README.md", root="workspace", interpreter=interpreter)

    assert alias_payload == canonical_payload


def test_bind_runtime_tools_does_not_apply_policy_filter() -> None:
    import fleet_rlm.runtime.tools.binding as binding_mod

    source = inspect.getsource(binding_mod.bind_runtime_tools)
    assert "filter_tool_names" not in source

    bound = bind_runtime_tools(
        discover_tools(sandbox_available=True),
        runtime=SimpleNamespace(core_memory={}),
        interpreter=_interpreter(),
    )

    assert bound


def test_discover_tools_default_hides_sandbox_tools() -> None:
    names = set(list_react_tool_names(discover_tools()))

    assert "list_files" not in names
    assert "sandbox_list_files" not in names
    assert "execute_code" not in names
    assert "find_files" in names


def test_discover_tools_with_sandbox_exposes_allowed_sandbox_tools() -> None:
    names = set(list_react_tool_names(discover_tools(sandbox_available=True)))

    assert "list_files" in names
    assert "sandbox_list_files" in names
    assert "execute_code" in names


def test_all_discovered_tools_have_descriptors() -> None:
    from fleet_rlm.runtime.tools.registry import _discover_unfiltered_tools

    discovered = set(list_react_tool_names(list(_discover_unfiltered_tools())))
    missing = discovered - set(descriptor_by_name())
    assert not missing, f"Tools missing descriptors: {sorted(missing)}"


def test_skills_install_policy_does_not_import_api_config() -> None:
    import fleet_rlm.skills.install_policy as install_policy

    source = inspect.getsource(install_policy)
    assert "fleet_rlm.api.config" not in source


def test_backend_checkpoint_package_imports_are_side_effect_light() -> None:
    packages = [
        "fleet_rlm.api",
        "fleet_rlm.daytona",
        "fleet_rlm.integrations.daytona",
        "fleet_rlm.tools",
        "fleet_rlm.runtime.tools",
        "fleet_rlm.artifacts",
        "fleet_rlm.files",
        "fleet_rlm.skills",
        "fleet_rlm.rlm",
        "fleet_rlm.runtime",
    ]

    for package in packages:
        importlib.import_module(package)
