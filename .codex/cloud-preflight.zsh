#!/usr/bin/env zsh
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$repo_root"

branch="$(git branch --show-current)"
if [[ -z "$branch" ]]; then
  echo "ERROR: Unable to determine current branch (detached HEAD?); use a feature branch based on origin/main." >&2
  exit 1
fi
if [[ "$branch" == "main" || "$branch" == "master" ]]; then
  echo "ERROR: Codex Cloud must not run from $branch; use a feature branch based on origin/main." >&2
  exit 1
fi

if ! git show-ref --verify --quiet refs/remotes/origin/main; then
  echo "ERROR: origin/main is unavailable; cannot verify the Cloud base." >&2
  exit 1
fi

if ! git merge-base --is-ancestor origin/main HEAD; then
  echo "ERROR: HEAD is not based on origin/main." >&2
  exit 1
fi

echo "Codex Cloud branch guard: current=$branch base=origin/main"
git status --short --branch --untracked-files=all

if (( $# == 0 )); then
  uv run python scripts/check_harness_engineering.py --skip-script-help
fi
