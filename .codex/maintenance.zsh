#!/usr/bin/env zsh
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$repo_root"

zsh .codex/cloud-preflight.zsh --skip-harness

if ! command -v uv >/dev/null 2>&1; then
  echo "ERROR: uv is required for fleet-rlm maintenance." >&2
  exit 1
fi

uv sync --all-extras --dev --frozen

if ! command -v pnpm >/dev/null 2>&1; then
  if ! command -v corepack >/dev/null 2>&1; then
    echo "ERROR: pnpm or corepack is required for the fleet TUI." >&2
    exit 1
  fi
  corepack enable
fi
pnpm --dir tools/fleet-tui install --frozen-lockfile

uv run python scripts/check_harness_engineering.py --skip-script-help
echo "Codex Cloud maintenance complete"
