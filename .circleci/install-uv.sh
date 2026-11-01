#!/usr/bin/env bash
set -euo pipefail

# Digest published on astral-sh/uv's 0.12.21 GitHub release asset.
installer_dir=$(mktemp -d)
trap 'rm -rf "$installer_dir"' EXIT
curl --fail --silent --show-error --location --max-time 60 \
  https://github.com/astral-sh/uv/releases/download/0.12.21/uv-installer.sh \
  --output "$installer_dir/install.sh"
printf '%s  %s\n' 0722d6c438395e39e1c27a86a79054d3b2820dd9399c7f8b0f6f84cd27ce36c3 \
  "$installer_dir/install.sh" | sha256sum --check -
sh "$installer_dir/install.sh"
echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$BASH_ENV"
