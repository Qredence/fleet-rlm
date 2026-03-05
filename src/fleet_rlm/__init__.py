"""RLM with Modal package for sandboxed code execution."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

__version__ = "0.4.94"

__all__ = [
    "__version__",
    "configure_planner_from_env",
    "get_planner_lm_from_env",
    "sandbox_driver",
    "ModalInterpreter",
    "RLMReActChatAgent",
    "RLMReActChatSignature",
    "build_tool_list",
    "list_react_tool_names",
    "COMMAND_DISPATCH",
    "execute_command",
    "AnalyzeLongDocument",
    "SummarizeLongDocument",
    "ExtractFromLogs",
    "GroundedAnswerWithCitations",
    "IncidentTriageFromLogs",
    "CodeChangePlan",
    "CoreMemoryUpdateProposal",
    "VolumeFileTreeSignature",
    "MemoryActionIntentSignature",
    "MemoryStructureAuditSignature",
    "MemoryStructureMigrationPlanSignature",
    "ClarificationQuestionSignature",
    "regex_extract",
    "chunk_by_size",
    "chunk_by_headers",
    "chunk_by_timestamps",
    "chunk_by_json_keys",
    "get_scaffold_dir",
    "install_agents",
    "install_all",
    "install_skills",
    "list_agents",
    "list_skills",
    "configure_analytics",
    "PostHogConfig",
    "PostHogLLMCallback",
]

# Keep lazy exports while making symbol definitions visible to static analyzers
# (including CodeQL's py/undefined-export) without eager runtime imports.
if TYPE_CHECKING:
    from .analytics import PostHogConfig, PostHogLLMCallback, configure_analytics
    from .chunking import (
        chunk_by_headers,
        chunk_by_json_keys,
        chunk_by_size,
        chunk_by_timestamps,
    )
    from .core import (
        ModalInterpreter,
        configure_planner_from_env,
        get_planner_lm_from_env,
        sandbox_driver,
    )
    from .react import (
        COMMAND_DISPATCH,
        RLMReActChatAgent,
        RLMReActChatSignature,
        build_tool_list,
        execute_command,
        list_react_tool_names,
    )
    from .react.signatures import (
        AnalyzeLongDocument,
        ClarificationQuestionSignature,
        CodeChangePlan,
        CoreMemoryUpdateProposal,
        ExtractFromLogs,
        GroundedAnswerWithCitations,
        IncidentTriageFromLogs,
        MemoryActionIntentSignature,
        MemoryStructureAuditSignature,
        MemoryStructureMigrationPlanSignature,
        SummarizeLongDocument,
        VolumeFileTreeSignature,
    )
    from .utils import (
        get_scaffold_dir,
        install_agents,
        install_all,
        install_skills,
        list_agents,
        list_skills,
        regex_extract,
    )

    _TYPE_CHECK_EXPORTS = (
        configure_planner_from_env,
        get_planner_lm_from_env,
        sandbox_driver,
        ModalInterpreter,
        RLMReActChatAgent,
        RLMReActChatSignature,
        build_tool_list,
        list_react_tool_names,
        COMMAND_DISPATCH,
        execute_command,
        AnalyzeLongDocument,
        SummarizeLongDocument,
        ExtractFromLogs,
        GroundedAnswerWithCitations,
        IncidentTriageFromLogs,
        CodeChangePlan,
        CoreMemoryUpdateProposal,
        VolumeFileTreeSignature,
        MemoryActionIntentSignature,
        MemoryStructureAuditSignature,
        MemoryStructureMigrationPlanSignature,
        ClarificationQuestionSignature,
        regex_extract,
        chunk_by_size,
        chunk_by_headers,
        chunk_by_timestamps,
        chunk_by_json_keys,
        get_scaffold_dir,
        install_agents,
        install_all,
        install_skills,
        list_agents,
        list_skills,
        configure_analytics,
        PostHogConfig,
        PostHogLLMCallback,
    )

_LAZY_ATTRS: dict[str, tuple[str, str]] = {
    "chunk_by_headers": ("fleet_rlm.chunking", "chunk_by_headers"),
    "chunk_by_json_keys": ("fleet_rlm.chunking", "chunk_by_json_keys"),
    "chunk_by_size": ("fleet_rlm.chunking", "chunk_by_size"),
    "chunk_by_timestamps": ("fleet_rlm.chunking", "chunk_by_timestamps"),
    "configure_planner_from_env": ("fleet_rlm.core", "configure_planner_from_env"),
    "get_planner_lm_from_env": ("fleet_rlm.core", "get_planner_lm_from_env"),
    "sandbox_driver": ("fleet_rlm.core", "sandbox_driver"),
    "ModalInterpreter": ("fleet_rlm.core", "ModalInterpreter"),
    "COMMAND_DISPATCH": ("fleet_rlm.react", "COMMAND_DISPATCH"),
    "execute_command": ("fleet_rlm.react", "execute_command"),
    "RLMReActChatAgent": ("fleet_rlm.react", "RLMReActChatAgent"),
    "RLMReActChatSignature": ("fleet_rlm.react", "RLMReActChatSignature"),
    "build_tool_list": ("fleet_rlm.react", "build_tool_list"),
    "list_react_tool_names": ("fleet_rlm.react", "list_react_tool_names"),
    "regex_extract": ("fleet_rlm.utils", "regex_extract"),
    "get_scaffold_dir": ("fleet_rlm.utils", "get_scaffold_dir"),
    "install_agents": ("fleet_rlm.utils", "install_agents"),
    "install_all": ("fleet_rlm.utils", "install_all"),
    "install_skills": ("fleet_rlm.utils", "install_skills"),
    "list_agents": ("fleet_rlm.utils", "list_agents"),
    "list_skills": ("fleet_rlm.utils", "list_skills"),
    "AnalyzeLongDocument": ("fleet_rlm.react.signatures", "AnalyzeLongDocument"),
    "CodeChangePlan": ("fleet_rlm.react.signatures", "CodeChangePlan"),
    "ClarificationQuestionSignature": (
        "fleet_rlm.react.signatures",
        "ClarificationQuestionSignature",
    ),
    "CoreMemoryUpdateProposal": (
        "fleet_rlm.react.signatures",
        "CoreMemoryUpdateProposal",
    ),
    "ExtractFromLogs": ("fleet_rlm.react.signatures", "ExtractFromLogs"),
    "GroundedAnswerWithCitations": (
        "fleet_rlm.react.signatures",
        "GroundedAnswerWithCitations",
    ),
    "IncidentTriageFromLogs": ("fleet_rlm.react.signatures", "IncidentTriageFromLogs"),
    "MemoryActionIntentSignature": (
        "fleet_rlm.react.signatures",
        "MemoryActionIntentSignature",
    ),
    "MemoryStructureAuditSignature": (
        "fleet_rlm.react.signatures",
        "MemoryStructureAuditSignature",
    ),
    "MemoryStructureMigrationPlanSignature": (
        "fleet_rlm.react.signatures",
        "MemoryStructureMigrationPlanSignature",
    ),
    "SummarizeLongDocument": ("fleet_rlm.react.signatures", "SummarizeLongDocument"),
    "VolumeFileTreeSignature": (
        "fleet_rlm.react.signatures",
        "VolumeFileTreeSignature",
    ),
    "configure_analytics": ("fleet_rlm.analytics", "configure_analytics"),
    "PostHogConfig": ("fleet_rlm.analytics", "PostHogConfig"),
    "PostHogLLMCallback": ("fleet_rlm.analytics", "PostHogLLMCallback"),
}

_LAZY_MODULES: dict[str, str] = {
    "scaffold": "fleet_rlm.utils.scaffold",
    "tools": "fleet_rlm.utils.tools",
}


def __getattr__(name: str) -> Any:
    """Load exported symbols lazily to reduce top-level import cost."""
    attr_spec = _LAZY_ATTRS.get(name)
    if attr_spec is not None:
        module_name, attr_name = attr_spec
        value = getattr(import_module(module_name), attr_name)
        globals()[name] = value
        return value

    module_name = _LAZY_MODULES.get(name)
    if module_name is not None:
        module = import_module(module_name)
        globals()[name] = module
        return module

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__) | set(_LAZY_MODULES))
