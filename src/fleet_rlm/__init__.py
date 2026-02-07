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

Example:
    >>> from fleet_rlm import configure_planner_from_env, sandbox_driver
    >>> from fleet_rlm import ModalInterpreter, ExtractArchitecture
    >>> configure_planner_from_env()
    >>> interpreter = ModalInterpreter()
    >>> rlm = dspy.RLM(signature=ExtractArchitecture, interpreter=interpreter)
"""

from .config import configure_planner_from_env
from .driver import sandbox_driver
from .interpreter import ModalInterpreter
from .signatures import (
    ExtractAPIEndpoints,
    ExtractArchitecture,
    ExtractWithCustomTool,
    FindErrorPatterns,
)
from .tools import regex_extract

__all__ = [
    "configure_planner_from_env",
    "sandbox_driver",
    "ModalInterpreter",
    "ExtractArchitecture",
    "ExtractAPIEndpoints",
    "FindErrorPatterns",
    "ExtractWithCustomTool",
    "regex_extract",
]
