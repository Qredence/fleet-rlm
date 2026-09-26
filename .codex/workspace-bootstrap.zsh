#!/usr/bin/env zsh
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$repo_root"

zsh .codex/cloud-preflight.zsh --skip-harness

if ! command -v uv >/dev/null 2>&1; then
  echo "ERROR: uv is required for fleet-rlm bootstrap." >&2
  exit 1
fi

echo "==> fleet-rlm Codex bootstrap"
echo "repo: $repo_root"

uv sync --all-extras --dev --frozen

if command -v pnpm >/dev/null 2>&1; then
  pnpm_command=pnpm
elif command -v corepack >/dev/null 2>&1; then
  pnpm_command=corepack
else
  echo "ERROR: pnpm or corepack is required for the fleet TUI." >&2
  exit 1
fi
# Run inside the workspace so Corepack resolves its pinned packageManager.
if [[ "$pnpm_command" == corepack ]]; then
  (cd tools/fleet-tui && corepack pnpm install --frozen-lockfile)
else
  (cd tools/fleet-tui && pnpm install --frozen-lockfile)
fi

echo "python: $(uv run python --version 2>&1)"
uv run python scripts/check_harness_engineering.py --skip-script-help

echo "==> Bootstrap complete"
echo "Use Codex actions for common development commands; use the Makefile for the full command set."
