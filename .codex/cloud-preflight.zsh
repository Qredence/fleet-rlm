#!/usr/bin/env zsh
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$repo_root"

branch="$(git branch --show-current)"
if [[ "$branch" == "main" || "$branch" == "master" ]]; then
  echo "ERROR: Codex Cloud must not run from $branch; use dev-0.7." >&2
  exit 1
fi

if ! git show-ref --verify --quiet refs/remotes/origin/dev-0.7; then
  echo "ERROR: origin/dev-0.7 is unavailable; cannot verify the Cloud base." >&2
  exit 1
fi

if ! git merge-base --is-ancestor origin/dev-0.7 HEAD; then
  echo "ERROR: HEAD is not based on origin/dev-0.7." >&2
  exit 1
fi

echo "Codex Cloud branch guard: current=$branch base=origin/dev-0.7"
git status --short --branch --untracked-files=all

if (( $# == 0 )); then
  uv run python scripts/check_harness_engineering.py --skip-script-help
fi
