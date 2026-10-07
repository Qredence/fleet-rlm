#!/bin/bash
# Inspect a specific turn (trace) in detail.
#
# Usage: bash inspect_turn.sh <TRACE_ID>

set -euo pipefail

TRACE_ID="$1"

# Keep full trace data private and remove it on success, failure, or interruption.
TRACE_DETAIL=$(mktemp "${TMPDIR:-/tmp}/mlflow-trace.XXXXXX")
trap 'rm -f -- "$TRACE_DETAIL"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# Fetch the full trace (always outputs JSON, no --output flag needed)
mlflow traces get \
  --trace-id "$TRACE_ID" > "$TRACE_DETAIL"

echo "=== All spans ==="
jq '.data.spans[] | {name: .name, status: .status.code, parent_span_id: .parent_span_id}' "$TRACE_DETAIL"

echo ""
echo "=== Error spans ==="
jq '.data.spans[] | select(.status.code != "STATUS_CODE_OK")' "$TRACE_DETAIL"

echo ""
echo "=== Assessments ==="
jq '.info.assessments' "$TRACE_DETAIL"
