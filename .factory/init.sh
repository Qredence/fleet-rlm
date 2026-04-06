#!/usr/bin/env bash
set -euo pipefail

export UV_LINK_MODE=copy
uv sync --all-extras --dev
(cd src/frontend && pnpm install --frozen-lockfile)
