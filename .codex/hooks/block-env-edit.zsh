#!/usr/bin/env zsh
set -euo pipefail

payload="$(cat)"

if command -v jq >/dev/null 2>&1; then
  file_path="$(
    printf '%s' "$payload" | jq -r '
      .tool_input.file_path //
      .tool_input.path //
      .toolInput.file_path //
      .toolInput.path //
      empty
    ' 2>/dev/null || true
  )"
elif command -v python3 >/dev/null 2>&1; then
  file_path="$(
    printf '%s' "$payload" | python3 -c '
import json
import sys

try:
    data = json.load(sys.stdin)
except json.JSONDecodeError:
    raise SystemExit(0)

for key in ("tool_input", "toolInput"):
    value = data.get(key)
    if isinstance(value, dict):
        path = value.get("file_path") or value.get("path")
        if path:
            print(path)
            break
' 2>/dev/null || true
  )"
else
  file_path=""
fi

if [[ "$file_path" == ".env" || "$file_path" == */.env ]]; then
  printf '{"decision":"block","reason":"Direct edits to .env are blocked. Edit .env.example or document required variables instead."}\n'
  exit 2
fi

exit 0
