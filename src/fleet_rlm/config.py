"""Configuration utilities for DSPy RLM with Modal.

This module handles environment configuration, including loading .env files,
finding project roots, and guarding against module shadowing issues with Modal.
"""

from __future__ import annotations

import os
from pathlib import Path

import dspy
from dotenv import load_dotenv


def _find_project_root(start: Path) -> Path:
    """Find the project root by locating pyproject.toml.

    Searches upward from the given path through parent directories until
    a pyproject.toml file is found.

    Args:
        start: The starting path to begin searching from.

    Returns:
        Path to the directory containing pyproject.toml, or the start path
        if no pyproject.toml is found in any parent directory.
    """
    for path in [start, *start.parents]:
        if (path / "pyproject.toml").exists():
            return path
    return start


def _guard_modal_shadowing() -> None:
    """Guard against module shadowing that can break Modal imports.

    Checks for and handles shadowing issues:
    - A local 'modal.py' file that shadows the modal package
    - Compiled bytecode files (__pycache__/modal.*.pyc) from previous shadowing

    Raises:
        RuntimeError: If a modal.py shadow file exists (user must rename/delete),
            or if bytecode files exist but cannot be removed.
    """
    shadow_py = Path.cwd() / "modal.py"
    shadow_pyc_dir = Path.cwd() / "__pycache__"
    shadow_pycs = (
        list(shadow_pyc_dir.glob("modal.*.pyc")) if shadow_pyc_dir.exists() else []
    )

    if shadow_py.exists():
        raise RuntimeError(
            f"Found {shadow_py} which shadows the 'modal' package. "
            "Rename/delete it and restart your shell or kernel."
        )

    failed: list[str] = []
    for pyc in shadow_pycs:
        try:
            pyc.unlink()
        except OSError:
            failed.append(str(pyc))

    if failed:
        raise RuntimeError(
            "Found shadowing bytecode files but could not remove them:\n"
            + "\n".join(failed)
            + "\nDelete them manually and retry."
        )


def configure_planner_from_env(*, env_file: Path | None = None) -> bool:
    """Configure DSPy's planner LM from environment variables.

    Loads environment variables from a .env file (if found) and configures
    DSPy with a language model based on the loaded configuration.

    Required environment variables:
        - DSPY_LM_MODEL: The model identifier (e.g., "openai/gpt-4")
        - DSPY_LLM_API_KEY or DSPY_LM_API_KEY: API key for the model provider

    Optional environment variables:
        - DSPY_LM_API_BASE: Custom API base URL
        - DSPY_LM_MAX_TOKENS: Maximum tokens for generation (default: 16000)

    Also guards against modal module shadowing issues.

    Args:
        env_file: Optional path to a specific .env file. If not provided,
            searches for .env in the project root (directory containing
            pyproject.toml) or current working directory.

    Returns:
        True if the planner was successfully configured, False if required
        environment variables (DSPY_LM_MODEL and API key) are not set.

    Example:
        >>> from fleet_rlm import configure_planner_from_env
        >>> success = configure_planner_from_env()
        >>> if not success:
        ...     print("Failed to configure planner - check environment variables")
    """

    dotenv_path = env_file
    if dotenv_path is None:
        project_root = _find_project_root(Path.cwd())
        dotenv_path = project_root / ".env"

    load_dotenv(dotenv_path, override=False)
    _guard_modal_shadowing()

    api_key = os.environ.get("DSPY_LLM_API_KEY") or os.environ.get("DSPY_LM_API_KEY")
    model = os.environ.get("DSPY_LM_MODEL")

    if not model or not api_key:
        return False

    planner_lm = dspy.LM(
        model,
        api_base=os.environ.get("DSPY_LM_API_BASE"),
        api_key=api_key,
        max_tokens=int(os.environ.get("DSPY_LM_MAX_TOKENS", "16000")),
    )
    dspy.configure(lm=planner_lm)
    return True
