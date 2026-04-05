#!/usr/bin/env zsh

set -euo pipefail

script_dir=${0:A:h}
repo_root=${script_dir:h}

cd "$repo_root"

if [[ -x ".venv/bin/python" ]]; then
  exec .venv/bin/python -m pytest -q -m "not live_llm and not benchmark"
fi

if command -v uv >/dev/null 2>&1; then
  exec uv run pytest -q -m "not live_llm and not benchmark"
fi

if [[ -x "${HOME:-}/.local/bin/uv" ]]; then
  exec "${HOME}/.local/bin/uv" run pytest -q -m "not live_llm and not benchmark"
fi

print -u2 "Unable to run backend fast tests: expected .venv/bin/python or uv. Run 'uv sync --extra dev' from the repo root."
exit 1
