#!/usr/bin/env zsh
set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

status_output="$(git status --short -- openapi.yaml 2>/dev/null || true)"
if [[ -z "$status_output" ]]; then
  exit 0
fi

{
  echo "Generated OpenAPI contract changed: openapi.yaml"
  echo ""
  echo "Use the command that owns this artifact:"
  echo "  - Regenerate: make api-sync"
  echo "  - Verify: make api-check"
} >&2
