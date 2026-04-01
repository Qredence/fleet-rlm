#!/usr/bin/env zsh

set -euo pipefail

script_dir=${0:A:h}
repo_root=${script_dir:h}

cd "$repo_root"

if [[ -x ".venv/bin/python" ]]; then
  exec .venv/bin/python -m ty check src --exclude "src/fleet_rlm/_scaffold/**"
fi

if command -v uv >/dev/null 2>&1; then
  exec uv run ty check src --exclude "src/fleet_rlm/_scaffold/**"
fi

print -u2 "Unable to run ty: expected .venv/bin/python or uv on PATH. Run 'uv sync --all-extras --dev' from the repo root."
exit 1
