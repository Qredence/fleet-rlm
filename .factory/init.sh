#!/bin/bash
# Frontend Architecture Optimization - Environment Setup
# This script is idempotent and safe to run multiple times

set -e

cd "$(dirname "$0")/../../src/frontend"

# Install dependencies if needed
if [ ! -d "node_modules" ]; then
  echo "Installing dependencies..."
  bun install
fi

echo "Frontend environment ready."
