#!/usr/bin/env zsh

set -euo pipefail

script_dir=${0:A:h}
repo_root=${script_dir:h}

cd "$repo_root"

jscpd_bin="src/frontend/node_modules/.bin/jscpd"

if [[ ! -x "$jscpd_bin" ]]; then
  print -u2 "Unable to run jscpd: expected $jscpd_bin. Run 'cd src/frontend && pnpm install --frozen-lockfile' first."
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
