#!/bin/bash
# Discover the input/output schema from the first trace in a session.
#
# Usage: bash discover_schema.sh <EXPERIMENT_ID> <SESSION_ID>

set -euo pipefail

EXPERIMENT_ID="$1"
SESSION_ID="$2"

# Find the first trace ID in the session
TRACE_ID=$(mlflow traces search \
  --experiment-id "$EXPERIMENT_ID" \
  --filter-string 'metadata.`mlflow.trace.session` = "'"$SESSION_ID"'"' \
  --order-by "timestamp_ms ASC" \
  --extract-fields 'info.trace_id' \
  --output json \
  --max-results 1 | jq -r '.traces[0].info.trace_id')
echo "First trace ID: $TRACE_ID"

# Keep full trace data private and remove it on success, failure, or interruption.
TRACE_DETAIL=$(mktemp "${TMPDIR:-/tmp}/mlflow-trace.XXXXXX")
trap 'rm -f -- "$TRACE_DETAIL"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# Fetch the full trace detail (always outputs JSON, no --output flag needed)
mlflow traces get \
  --trace-id "$TRACE_ID" > "$TRACE_DETAIL"

echo ""
echo "=== Root span attribute keys ==="
jq '.data.spans[] | select(.parent_span_id == null) | .attributes | keys' "$TRACE_DETAIL"

echo ""
echo "=== Root span inputs ==="
jq '.data.spans[] | select(.parent_span_id == null) | .attributes["mlflow.spanInputs"]' "$TRACE_DETAIL"

echo ""
echo "=== Root span outputs ==="
jq '.data.spans[] | select(.parent_span_id == null) | .attributes["mlflow.spanOutputs"]' "$TRACE_DETAIL"
