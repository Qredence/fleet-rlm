#!/bin/bash
# Phase 0 regression verification script
# Run this after refactoring to verify against golden payload baseline

set -e

echo "=== Phase 0: Verifying regression against baseline ==="

# Run regression tests
echo "Running regression tests against golden payloads..."
uv run pytest tests/contracts/test_golden_payloads.py::test_regression_chat_websocket_events -v
uv run pytest tests/contracts/test_golden_payloads.py::test_regression_passive_events_websocket_events -v

# Compare openapi.yaml
echo "Comparing openapi.yaml against baseline..."
if ! diff -u tests/contracts/golden_payloads/openapi_baseline.yaml openapi.yaml; then
    echo "WARNING: openapi.yaml has changed from baseline"
    echo "Review the diff above. If changes are intentional, update the baseline."
fi

# Compare generated client
echo "Comparing generated client against baseline..."
if ! diff -u tests/contracts/golden_payloads/openapi_client_baseline.ts src/frontend/src/lib/rlm-api/generated/openapi.ts; then
    echo "WARNING: Generated client has changed from baseline"
    echo "Review the diff above. If changes are intentional, update the baseline."
fi

echo "=== Phase 0 regression verification complete ==="
