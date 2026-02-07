from __future__ import annotations

import os
from pathlib import Path

import dspy


def _find_project_root(start: Path) -> Path:
    for path in [start, *start.parents]:
        if (path / "pyproject.toml").exists():
            return path
    return start


def _load_dotenv(path: Path) -> None:
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

    Required variables:
    - DSPY_LM_MODEL
    - DSPY_LLM_API_KEY (or DSPY_LM_API_KEY)

    Optional variables:
    - DSPY_LM_API_BASE
    - DSPY_LM_MAX_TOKENS
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
        max_tokens=int(os.environ.get("DSPY_LM_MAX_TOKENS", "16000")),
    )
    dspy.configure(lm=planner_lm)
    return True
