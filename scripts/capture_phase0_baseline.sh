#!/bin/bash
# Phase 0 baseline capture script
# Run this to capture golden payloads and openapi.yaml baseline before refactoring

set -e

echo "=== Phase 0: Capturing golden payloads and openapi.yaml baseline ==="

# Create golden payloads directory
mkdir -p tests/contracts/golden_payloads

# Capture openapi.yaml baseline
echo "Capturing openapi.yaml baseline..."
cp openapi.yaml tests/contracts/golden_payloads/openapi_baseline.yaml

# Capture frontend generated client baseline
echo "Capturing frontend generated client baseline..."
cp src/frontend/src/lib/rlm-api/generated/openapi.ts tests/contracts/golden_payloads/openapi_client_baseline.ts

# Run golden payload capture tests
echo "Running golden payload capture tests..."
# Temporarily remove golden payloads directory to trigger capture
rm -rf tests/contracts/golden_payloads
uv run pytest tests/contracts/test_golden_payloads.py::test_capture_chat_websocket_golden_payloads -v
uv run pytest tests/contracts/test_golden_payloads.py::test_capture_passive_events_websocket_golden_payloads -v

echo "=== Phase 0 baseline capture complete ==="
echo "Golden payloads saved to: tests/contracts/golden_payloads/"
echo "OpenAPI baseline saved to: tests/contracts/golden_payloads/openapi_baseline.yaml"
echo "Client baseline saved to: tests/contracts/golden_payloads/openapi_client_baseline.ts"
