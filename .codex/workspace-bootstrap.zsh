#!/usr/bin/env zsh
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$repo_root"

if ! command -v uv >/dev/null 2>&1; then
  echo "ERROR: uv is required for fleet-rlm bootstrap." >&2
  exit 1
fi

echo "==> fleet-rlm Codex bootstrap"
echo "repo: $repo_root"
echo "python: $(uv run python --version 2>&1)"

if [[ -f uv.lock ]]; then
  uv sync --all-extras --dev --frozen
else
  uv sync --all-extras --dev
fi

if [[ -f src/frontend/package.json ]]; then
  if ! command -v pnpm >/dev/null 2>&1; then
    echo "ERROR: pnpm is required for src/frontend bootstrap." >&2
    exit 1
  fi
  echo "==> Installing frontend dependencies"
  (cd src/frontend && pnpm install --frozen-lockfile)
fi

echo "==> Bootstrap complete"
echo "Use Codex actions for run, validation, OpenAPI sync, release, and diagnostics lanes."
