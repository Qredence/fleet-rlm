#!/usr/bin/env zsh
set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

generated_paths=(
  "openapi.yaml"
  "src/frontend/openapi/fleet-rlm.openapi.yaml"
  "src/frontend/src/lib/rlm-api/generated/openapi.ts"
  "src/frontend/src/routeTree.gen.ts"
  "src/frontend/dist"
  "src/fleet_rlm/ui/dist"
)

dirty=()
for path in "${generated_paths[@]}"; do
  status_output="$(git status --short -- "$path" 2>/dev/null || true)"
  if [[ -n "$status_output" ]]; then
    dirty+=("$path")
  fi
done

if (( ${#dirty[@]} == 0 )); then
  exit 0
fi

{
  echo "Generated or synced artifacts changed:"
  for path in "${dirty[@]}"; do
    echo "  - $path"
  done
  echo ""
  echo "Use the Codex actions or commands that own these artifacts:"
  echo "  - OpenAPI: Sync / make api-sync"
  echo "  - OpenAPI: Check / make api-check"
  echo "  - Release: Build UI / make build-ui"
} >&2

exit 0
