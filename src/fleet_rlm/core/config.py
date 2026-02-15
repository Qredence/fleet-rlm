"""Configuration utilities for DSPy RLM with Modal.

This module handles environment configuration, including loading .env files,
finding project roots, and guarding against module shadowing issues with Modal.
"""

from __future__ import annotations

import os
from pathlib import Path

import dspy


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


def _load_dotenv(path: Path) -> None:
    """Load environment variables from a .env file.

    Parses the file line by line, handling comments (lines starting with #),
    empty lines, and quoted values. Only sets variables that are not already
    present in the environment.

    Args:
        path: Path to the .env file.
    """
    if not path.exists():
        return

    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and (
            (value[0] == value[-1] == '"') or (value[0] == value[-1] == "'")
        ):
            value = value[1:-1]

        if key and key not in os.environ:
            os.environ[key] = value


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

    _load_dotenv(dotenv_path)
    _guard_modal_shadowing()

    api_key = os.environ.get("DSPY_LLM_API_KEY") or os.environ.get("DSPY_LM_API_KEY")
    model = os.environ.get("DSPY_LM_MODEL")

    if not model or not api_key:
        return False

    planner_lm = dspy.LM(
        model,
        api_base=os.environ.get("DSPY_LM_API_BASE"),
        api_key=api_key,
        max_tokens=int(os.environ.get("DSPY_LM_MAX_TOKENS", "64000")),
    )
    dspy.configure(lm=planner_lm)
    return True


def get_planner_lm_from_env(
    *, env_file: Path | None = None, model_name: str | None = None
) -> dspy.LM | None:
    """Create and return a DSPy LM from environment.

    This is the async-safe version of configure_planner_from_env(). It creates
    and returns the LM object without calling dspy.configure(), allowing the
    caller to use dspy.context() for thread-local configuration instead.

    Args:
        env_file: Optional path to a specific .env file.
        model_name: Optional explicit model identifier to use, overriding environment.

    Returns:
        A configured dspy.LM instance if configuration is available, None otherwise.
    """
    dotenv_path = env_file
    if dotenv_path is None:
        project_root = _find_project_root(Path.cwd())
        dotenv_path = project_root / ".env"

    _load_dotenv(dotenv_path)
    _guard_modal_shadowing()

    api_key = os.environ.get("DSPY_LLM_API_KEY") or os.environ.get("DSPY_LM_API_KEY")
    model = model_name or os.environ.get("DSPY_LM_MODEL")

    if not model or not api_key:
        return None

    return dspy.LM(
        model,
        api_base=os.environ.get("DSPY_LM_API_BASE"),
        api_key=api_key,
        max_tokens=int(os.environ.get("DSPY_LM_MAX_TOKENS", "64000")),
    )


def load_rlm_settings(*, config_path: Path | None = None) -> dict[str, object]:
    """Load RLM settings from the YAML configuration file.

    Reads the rlm_settings section from config/config.yaml and returns
    the configuration values with defaults for missing keys.

    Args:
        config_path: Optional path to the configuration file. If not provided,
            searches for config.yaml in the project root (directory containing
            pyproject.toml) or current working directory.

    Returns:
        Dictionary containing RLM configuration with the following keys:
            - max_iterations: Maximum iterations for RLM code execution (default: 30)
            - max_llm_calls: Maximum LLM calls per task (default: 50)
            - max_output_chars: Maximum output characters (default: 10000)
            - stdout_summary_threshold: Threshold for stdout summarization (default: 10000)
            - stdout_summary_prefix_len: Prefix length in summaries (default: 200)
            - verbose: Enable verbose logging (default: False)

    Example:
        >>> from fleet_rlm.core.config import load_rlm_settings
        >>> settings = load_rlm_settings()
        >>> print(f"Max iterations: {settings['max_iterations']}")
    """
    import yaml

    if config_path is None:
        project_root = _find_project_root(Path.cwd())
        config_path = project_root / "config" / "config.yaml"

    defaults = {
        "max_iterations": 30,
        "max_llm_calls": 50,
        "max_output_chars": 10000,
        "stdout_summary_threshold": 10000,
        "stdout_summary_prefix_len": 200,
        "verbose": False,
    }

    if not config_path.exists():
        return defaults

    try:
        config = yaml.safe_load(config_path.read_text())
        rlm_config = config.get("rlm_settings", {})
        return {**defaults, **rlm_config}
    except Exception:
        return defaults
