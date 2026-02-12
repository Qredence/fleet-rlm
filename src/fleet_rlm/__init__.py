"""RLM with Modal package for sandboxed code execution.

This package provides a Recursive Language Model (RLM) implementation backed by
Modal sandboxes for secure, scalable code execution. It integrates with DSPy
for language model orchestration and provides tools for extracting information
from documentation.

Key components:
    - ModalInterpreter: Code interpreter backed by Modal sandbox processes
    - configure_planner_from_env: Configure DSPy LM from environment variables
    - Signatures: DSPy signatures for various extraction tasks
    - runners: High-level functions for running RLM demonstrations
    - cli: Command-line interface for running demos
    - scaffold: Functions for installing skills/agents to ~/.claude/

Example:
    >>> from fleet_rlm import configure_planner_from_env, sandbox_driver
    >>> from fleet_rlm import ModalInterpreter, ExtractArchitecture
    >>> configure_planner_from_env()
    >>> interpreter = ModalInterpreter()
    >>> rlm = dspy.RLM(signature=ExtractArchitecture, interpreter=interpreter)
"""

__version__ = "0.3.3"

from .chunking import (
    chunk_by_headers,
    chunk_by_json_keys,
    chunk_by_size,
    chunk_by_timestamps,
)
from .core import (
    configure_planner_from_env,
    get_planner_lm_from_env,
    sandbox_driver,
    ModalInterpreter,
)
from .react import (
    COMMAND_DISPATCH,
    execute_command,
    RLMReActChatAgent,
    RLMReActChatSignature,
    build_tool_list,
    list_react_tool_names,
)
from .scaffold import (
    get_scaffold_dir,
    install_agents,
    install_all,
    install_skills,
    list_agents,
    list_skills,
)
from .agent_state import (
    AgentStateManager,
    AnalysisResult,
    CodeScript,
)
from .stateful_sandbox import (
    ExecutionRecord,
    SandboxResult,
    StatefulSandboxManager,
)
from .signatures import (
    AnalyzeLongDocument,
    ExtractAPIEndpoints,
    ExtractArchitecture,
    ExtractFromLogs,
    ExtractWithCustomTool,
    FindErrorPatterns,
    SummarizeLongDocument,
)
from .tools import regex_extract

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
    "AgentStateManager",
    "AnalysisResult",
    "CodeScript",
    "StatefulSandboxManager",
    "ExecutionRecord",
    "SandboxResult",
    "ExtractArchitecture",
    "ExtractAPIEndpoints",
    "FindErrorPatterns",
    "ExtractWithCustomTool",
    "AnalyzeLongDocument",
    "SummarizeLongDocument",
    "ExtractFromLogs",
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
]
