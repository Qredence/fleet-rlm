"""Policy-aware registry metadata for RLM-facing tools."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ToolCategory = Literal["filesystem", "skills", "artifacts", "sandbox", "memory", "web", "runtime"]
ToolRiskLevel = Literal["low", "medium", "high"]


class ToolDescriptor(BaseModel):
    """Stable metadata for one RLM-facing capability."""

    name: str
    description: str
    category: ToolCategory | str
    callable_path: str
    enabled_by_default: bool = True
    required_capabilities: list[str] = Field(default_factory=list)
    sandbox_required: bool = False
    write_capability: bool = False
    risk_level: ToolRiskLevel | str = "low"


class ToolExposurePolicy(BaseModel):
    """Session/profile policy used to decide which tools are visible."""

    allow_write_tools: bool = False
    allow_sandbox_tools: bool = True
    allowed_capabilities: list[str] | None = None
    enabled_tool_names: list[str] = Field(default_factory=list)
    disabled_tool_names: list[str] = Field(default_factory=list)


class ToolRuntimeContext(BaseModel):
    """Runtime facts that affect tool exposure."""

    sandbox_available: bool = False
    policy: ToolExposurePolicy = Field(default_factory=ToolExposurePolicy)


_TOOL_DESCRIPTORS: tuple[ToolDescriptor, ...] = (
    ToolDescriptor(
        name="list_files",
        description="List files and directories inside an approved Daytona workspace or volume root.",
        category="filesystem",
        callable_path="fleet_rlm.tools.filesystem:list_files_impl",
        required_capabilities=["filesystem:read"],
        sandbox_required=True,
        risk_level="low",
    ),
    ToolDescriptor(
        name="read_file",
        description="Read a bounded UTF-8 preview from an approved Daytona workspace or volume path.",
        category="filesystem",
        callable_path="fleet_rlm.tools.filesystem:read_file_impl",
        required_capabilities=["filesystem:read"],
        sandbox_required=True,
        risk_level="low",
    ),
    ToolDescriptor(
        name="write_file",
        description="Deferred shape for writing files inside an approved Daytona root.",
        category="filesystem",
        callable_path="fleet_rlm.tools.filesystem:write_file_impl",
        enabled_by_default=False,
        required_capabilities=["filesystem:write"],
        sandbox_required=True,
        write_capability=True,
        risk_level="high",
    ),
    ToolDescriptor(
        name="inspect_workspace",
        description="Inspect a bounded Daytona workspace listing without reading file bodies.",
        category="sandbox",
        callable_path="fleet_rlm.tools.sandbox:inspect_workspace_impl",
        required_capabilities=["sandbox:inspect", "filesystem:read"],
        sandbox_required=True,
        risk_level="low",
    ),
    ToolDescriptor(
        name="find_files",
        description="Search host file contents with a regex pattern.",
        category="filesystem",
        callable_path="fleet_rlm.runtime.tools.filesystem:find_files",
        required_capabilities=["host:read"],
        risk_level="medium",
    ),
    ToolDescriptor(
        name="read_file_slice",
        description="Read a line range from a host file without loading the full document.",
        category="filesystem",
        callable_path="fleet_rlm.runtime.tools.filesystem:read_file_slice",
        required_capabilities=["host:read"],
        risk_level="low",
    ),
    ToolDescriptor(
        name="list_skills",
        description="List visible skill metadata from the catalog.",
        category="skills",
        callable_path="fleet_rlm.tools.skill_tools:list_skills_impl",
        required_capabilities=["skills:read"],
        risk_level="low",
    ),
    ToolDescriptor(
        name="load_skill",
        description="Load one visible skill bundle.",
        category="skills",
        callable_path="fleet_rlm.tools.skill_tools:load_skill_tool_impl",
        required_capabilities=["skills:read"],
        risk_level="low",
    ),
    ToolDescriptor(
        name="read_skill_resource",
        description="Read one safe resource body from a visible skill bundle.",
        category="skills",
        callable_path="fleet_rlm.tools.skill_tools:read_skill_resource_impl",
        required_capabilities=["skills:read"],
        risk_level="low",
    ),
    ToolDescriptor(
        name="run_skill_script",
        description="Execute one trusted selected-skill script inside Daytona.",
        category="skills",
        callable_path="fleet_rlm.tools.skill_tools:run_skill_script_tool_impl",
        required_capabilities=["skills:script"],
        sandbox_required=True,
        risk_level="medium",
    ),
    ToolDescriptor(
        name="create_artifact",
        description="Create a new session artifact under an approved artifact root.",
        category="artifacts",
        callable_path="fleet_rlm.tools.artifacts:create_artifact_impl",
        enabled_by_default=False,
        required_capabilities=["artifacts:write"],
        sandbox_required=True,
        write_capability=True,
        risk_level="medium",
    ),
    ToolDescriptor(
        name="update_artifact",
        description="Update an existing session artifact under an approved artifact root.",
        category="artifacts",
        callable_path="fleet_rlm.tools.artifacts:update_artifact_impl",
        enabled_by_default=False,
        required_capabilities=["artifacts:write"],
        sandbox_required=True,
        write_capability=True,
        risk_level="medium",
    ),
    ToolDescriptor(
        name="list_artifacts",
        description="List safe artifact metadata for the current session.",
        category="artifacts",
        callable_path="fleet_rlm.tools.artifacts:list_artifacts_impl",
        required_capabilities=["artifacts:read"],
        sandbox_required=True,
        risk_level="low",
    ),
    ToolDescriptor(
        name="read_artifact",
        description="Read bounded artifact content from an approved session artifact root.",
        category="artifacts",
        callable_path="fleet_rlm.tools.artifacts:read_artifact_impl",
        required_capabilities=["artifacts:read"],
        sandbox_required=True,
        risk_level="low",
    ),
    ToolDescriptor(
        name="sandbox_list_files",
        description="Compatibility alias for listing files inside the Daytona sandbox.",
        category="filesystem",
        callable_path="fleet_rlm.tools.filesystem:list_files_impl",
        required_capabilities=["filesystem:read"],
        sandbox_required=True,
        risk_level="low",
    ),
    ToolDescriptor(
        name="sandbox_read_file",
        description="Compatibility alias for reading files inside the Daytona sandbox.",
        category="filesystem",
        callable_path="fleet_rlm.tools.filesystem:read_file_impl",
        required_capabilities=["filesystem:read"],
        sandbox_required=True,
        risk_level="low",
    ),
    ToolDescriptor(
        name="sandbox_get_file_info",
        description="Inspect metadata for a Daytona sandbox file or directory.",
        category="filesystem",
        callable_path="fleet_rlm.runtime.tools.sandbox_filesystem:_sandbox_get_file_info_impl",
        required_capabilities=["filesystem:read"],
        sandbox_required=True,
        risk_level="low",
    ),
    ToolDescriptor(
        name="sandbox_search_files",
        description="Search Daytona sandbox files by name pattern.",
        category="filesystem",
        callable_path="fleet_rlm.runtime.tools.sandbox_filesystem:_sandbox_search_files_impl",
        required_capabilities=["filesystem:read"],
        sandbox_required=True,
        risk_level="low",
    ),
    ToolDescriptor(
        name="sandbox_find_in_files",
        description="Search Daytona sandbox file contents by text pattern.",
        category="filesystem",
        callable_path="fleet_rlm.runtime.tools.sandbox_filesystem:_sandbox_find_in_files_impl",
        required_capabilities=["filesystem:read"],
        sandbox_required=True,
        risk_level="low",
    ),
    ToolDescriptor(
        name="sandbox_write_file",
        description="Compatibility alias for writing a file inside the Daytona sandbox.",
        category="filesystem",
        callable_path="fleet_rlm.runtime.tools.sandbox_filesystem:_sandbox_write_file_impl",
        enabled_by_default=False,
        required_capabilities=["filesystem:write"],
        sandbox_required=True,
        write_capability=True,
        risk_level="high",
    ),
    ToolDescriptor(
        name="sandbox_create_directory",
        description="Compatibility alias for creating a directory inside the Daytona sandbox.",
        category="filesystem",
        callable_path="fleet_rlm.runtime.tools.sandbox_filesystem:_sandbox_create_directory_impl",
        enabled_by_default=False,
        required_capabilities=["filesystem:write"],
        sandbox_required=True,
        write_capability=True,
        risk_level="medium",
    ),
    ToolDescriptor(
        name="sandbox_delete_file",
        description="Compatibility alias for deleting a file inside the Daytona sandbox.",
        category="filesystem",
        callable_path="fleet_rlm.runtime.tools.sandbox_filesystem:_sandbox_delete_file_impl",
        enabled_by_default=False,
        required_capabilities=["filesystem:write"],
        sandbox_required=True,
        write_capability=True,
        risk_level="high",
    ),
    ToolDescriptor(
        name="sandbox_move_file",
        description="Compatibility alias for moving a file inside the Daytona sandbox.",
        category="filesystem",
        callable_path="fleet_rlm.runtime.tools.sandbox_filesystem:_sandbox_move_file_impl",
        enabled_by_default=False,
        required_capabilities=["filesystem:write"],
        sandbox_required=True,
        write_capability=True,
        risk_level="medium",
    ),
    ToolDescriptor(
        name="sandbox_replace_in_files",
        description="Compatibility alias for replacing text inside Daytona sandbox files.",
        category="filesystem",
        callable_path="fleet_rlm.runtime.tools.sandbox_filesystem:_sandbox_replace_in_files_impl",
        enabled_by_default=False,
        required_capabilities=["filesystem:write"],
        sandbox_required=True,
        write_capability=True,
        risk_level="high",
    ),
    ToolDescriptor(
        name="browser_fetch_page",
        description="Fetch a JavaScript-rendered page inside a browser-capable Daytona sandbox.",
        category="web",
        callable_path="fleet_rlm.runtime.tools.browser_tools:browser_fetch_page",
        required_capabilities=["web:read"],
        sandbox_required=True,
        risk_level="medium",
    ),
    ToolDescriptor(
        name="chunk_document",
        description="Chunk a loaded document using a supported strategy.",
        category="runtime",
        callable_path="fleet_rlm.runtime.tools.chunking_tools:chunk_document",
        required_capabilities=["documents:read"],
        risk_level="low",
    ),
    ToolDescriptor(
        name="delegate_to_rlm",
        description="Delegate one sub-query to a child RLM inside Daytona.",
        category="runtime",
        callable_path="fleet_rlm.runtime.tools.rlm_delegate:delegate_to_rlm",
        required_capabilities=["runtime:delegate"],
        sandbox_required=True,
        risk_level="medium",
    ),
    ToolDescriptor(
        name="delegate_to_rlm_batched",
        description="Delegate multiple sub-queries to child RLMs inside Daytona.",
        category="runtime",
        callable_path="fleet_rlm.runtime.tools.rlm_delegate:delegate_to_rlm_batched",
        required_capabilities=["runtime:delegate"],
        sandbox_required=True,
        risk_level="medium",
    ),
    ToolDescriptor(
        name="execute_code",
        description="Execute Python code inside the Daytona sandbox.",
        category="sandbox",
        callable_path="fleet_rlm.runtime.tools.sandbox_tools:execute_code",
        required_capabilities=["sandbox:execute"],
        sandbox_required=True,
        risk_level="high",
    ),
    ToolDescriptor(
        name="fetch_page",
        description="Fetch a public HTTP(S) page and extract readable text.",
        category="web",
        callable_path="fleet_rlm.runtime.tools.web_tools:fetch_page",
        required_capabilities=["web:read"],
        risk_level="low",
    ),
    ToolDescriptor(
        name="list_documents",
        description="List documents currently loaded in the runtime document store.",
        category="runtime",
        callable_path="fleet_rlm.runtime.tools.document_tools:list_documents",
        required_capabilities=["documents:read"],
        risk_level="low",
    ),
    ToolDescriptor(
        name="load_document",
        description="Load a document from a path or URL into the runtime document store.",
        category="runtime",
        callable_path="fleet_rlm.runtime.tools.document_tools:load_document",
        required_capabilities=["documents:read"],
        risk_level="low",
    ),
    ToolDescriptor(
        name="recall",
        description="Recall persisted memory entries from the Daytona volume store.",
        category="memory",
        callable_path="fleet_rlm.runtime.tools.volume_memory_tools:recall",
        required_capabilities=["memory:read"],
        risk_level="low",
    ),
    ToolDescriptor(
        name="recursive_workspace",
        description="Run a multi-pass recursive Daytona workspace analysis.",
        category="sandbox",
        callable_path="fleet_rlm.runtime.tools.sandbox_tools:recursive_workspace",
        required_capabilities=["sandbox:execute"],
        sandbox_required=True,
        risk_level="medium",
    ),
    ToolDescriptor(
        name="remember",
        description="Persist a key/value pair into the Daytona volume memory store.",
        category="memory",
        callable_path="fleet_rlm.runtime.tools.volume_memory_tools:remember",
        required_capabilities=["memory:write"],
        write_capability=True,
        risk_level="medium",
    ),
    ToolDescriptor(
        name="search_knowledge",
        description="Search the persisted knowledge index on the Daytona volume.",
        category="memory",
        callable_path="fleet_rlm.runtime.tools.knowledge_tools:search_knowledge",
        required_capabilities=["knowledge:read"],
        risk_level="low",
    ),
    ToolDescriptor(
        name="set_active_document",
        description="Set the active document alias for downstream document tools.",
        category="runtime",
        callable_path="fleet_rlm.runtime.tools.document_tools:set_active_document",
        required_capabilities=["documents:read"],
        risk_level="low",
    ),
    ToolDescriptor(
        name="web_search",
        description="Search the public web and return result URLs, titles, and snippets.",
        category="web",
        callable_path="fleet_rlm.runtime.tools.web_tools:web_search",
        required_capabilities=["web:read"],
        risk_level="low",
    ),
)


def list_tool_descriptors() -> list[ToolDescriptor]:
    """Return all known tool descriptors, including deferred capabilities."""
    return list(_TOOL_DESCRIPTORS)


def descriptor_by_name() -> dict[str, ToolDescriptor]:
    """Return descriptors keyed by tool name."""
    return {descriptor.name: descriptor for descriptor in _TOOL_DESCRIPTORS}


def is_tool_exposed(descriptor: ToolDescriptor, context: ToolRuntimeContext) -> bool:
    """Return whether *descriptor* is allowed by runtime/session policy."""
    policy = context.policy
    if descriptor.name in policy.disabled_tool_names:
        return False
    explicitly_enabled = descriptor.name in policy.enabled_tool_names
    write_enabled = policy.allow_write_tools or explicitly_enabled
    if not descriptor.enabled_by_default and not explicitly_enabled:
        # allow_write_tools lifts only artifact write tools; other deferred
        # write tools (filesystem/sandbox) still require enabled_tool_names.
        artifact_write_opt_in = (
            descriptor.write_capability and write_enabled and "artifacts:write" in descriptor.required_capabilities
        )
        if not artifact_write_opt_in:
            return False
    if descriptor.write_capability and not write_enabled:
        return False
    if descriptor.sandbox_required and (not policy.allow_sandbox_tools or not context.sandbox_available):
        return False
    if policy.allowed_capabilities is not None:
        allowed = set(policy.allowed_capabilities)
        if not set(descriptor.required_capabilities).issubset(allowed):
            return False
    return True


def list_exposed_tool_descriptors(
    *,
    context: ToolRuntimeContext | None = None,
    policy: ToolExposurePolicy | None = None,
    sandbox_available: bool = False,
) -> list[ToolDescriptor]:
    """Return descriptors visible in the current runtime/session context."""
    ctx = context or ToolRuntimeContext(
        sandbox_available=sandbox_available,
        policy=policy or ToolExposurePolicy(),
    )
    return [descriptor for descriptor in _TOOL_DESCRIPTORS if is_tool_exposed(descriptor, ctx)]


def filter_tool_names(
    names: list[str],
    *,
    context: ToolRuntimeContext | None = None,
    policy: ToolExposurePolicy | None = None,
    sandbox_available: bool = False,
) -> list[str]:
    """Filter known tool names through policy while preserving unknown legacy names."""
    descriptors = descriptor_by_name()
    ctx = context or ToolRuntimeContext(
        sandbox_available=sandbox_available,
        policy=policy or ToolExposurePolicy(),
    )
    filtered: list[str] = []
    for name in names:
        descriptor = descriptors.get(name)
        if descriptor is not None and not is_tool_exposed(descriptor, ctx):
            continue
        filtered.append(name)
    return filtered


__all__ = [
    "ToolCategory",
    "ToolDescriptor",
    "ToolExposurePolicy",
    "ToolRiskLevel",
    "ToolRuntimeContext",
    "descriptor_by_name",
    "filter_tool_names",
    "is_tool_exposed",
    "list_exposed_tool_descriptors",
    "list_tool_descriptors",
]
