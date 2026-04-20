#!/usr/bin/env bash
set -euo pipefail

cd /Volumes/SSD-T7/qredence-environnement/fleet-rlm

# Install dependencies
uv sync --all-extras
