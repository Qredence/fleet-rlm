#!/usr/bin/env bash

set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "$script_dir/.." && pwd)"

cd "$repo_root"

jscpd_bin="src/frontend/node_modules/.bin/jscpd"

if [ ! -x "$jscpd_bin" ]; then
  printf '%s\n' "Unable to run jscpd: expected $jscpd_bin. Run 'cd src/frontend && pnpm install --frozen-lockfile' first." >&2
  exit 1
fi

ignore_patterns="**/__tests__/**,**/*.test.*,**/*.spec.*,**/generated/**,**/scaffold/**,**/*.d.ts,**/dist/**,**/build/**,**/node_modules/**,**/.factory/**,**/dogfood-output/**,**/routeTree.gen.ts,**/openapi/fleet-rlm.openapi.yaml,**/lib/rlm-api/generated/openapi.ts"

exec "$jscpd_bin" \
  --min-lines 12 \
  --min-tokens 80 \
  --threshold 1.5 \
  --reporters console \
  --gitignore \
  --format "python,javascript,typescript,tsx,jsx" \
  --ignore "$ignore_patterns" \
  src/fleet_rlm \
  src/frontend/src
