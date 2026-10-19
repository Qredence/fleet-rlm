#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${TMUX:-}" ]]; then
  echo "Run this from the Amp Terminal tab so it can open a server pane." >&2
  exit 1
fi

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

if curl --silent --fail --max-time 2 http://127.0.0.1:8000/health >/dev/null; then
  echo "Fleet API is already running; starting the TUI without another server pane."
  exec uv run fleet cli "$@"
fi

server_pane="$(tmux split-window -h -d -P -F '#{pane_id}' -c "$repo_root" 'uv run fleet serve')"
printf 'Starting Fleet server in pane %s...\n' "$server_pane"

ready=0
for _ in {1..60}; do
  if curl --silent --fail --max-time 2 http://127.0.0.1:8000/health >/dev/null; then
    ready=1
    break
  fi
  if ! tmux list-panes -F '#{pane_id}' | grep --fixed-strings --line-regexp --quiet "$server_pane"; then
    echo "Fleet server pane exited before the API became healthy." >&2
    exit 1
  fi
  sleep 1
done

if [[ "$ready" -ne 1 ]]; then
  printf 'Fleet API did not become healthy within 60 seconds; inspect pane %s.\n' "$server_pane" >&2
  exit 1
fi

cleanup() {
  tmux kill-pane -t "$server_pane" 2>/dev/null || true
}
trap cleanup EXIT

uv run fleet cli "$@"
