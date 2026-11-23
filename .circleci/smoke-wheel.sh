#!/usr/bin/env bash
set -euo pipefail

test "$#" -eq 1
smoke_dir=$(mktemp -d)
trap 'rm -rf "$smoke_dir"' EXIT
uv venv --python 3.13 "$smoke_dir"
smoke_python="$smoke_dir/bin/python"
uv pip install --python "$smoke_python" --index-url https://pypi.org/simple "$1"
uv pip check --python "$smoke_python"
"$smoke_python" -I - <<'PY'
import os
from importlib.metadata import version

from fleet_rlm.api import app

assert version("fleet-rlm") == os.environ["FLEET_RELEASE_VERSION"]
assert "/healthz" in app.openapi()["paths"]
PY
