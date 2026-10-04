---
name: running-the-fleet-tui
description: Drive a real Fleet Turn through the terminal TUI from an agent session, then verify it from the persisted MLflow traces.
metadata:
  compatibility: Fleet-RLM repository with its pinned DSPy and Daytona dependencies, a configured Daytona runtime, tmux, and operator authorization for the live provider run.
---

# Running the Fleet TUI

Use the maintained terminal client when a check needs a real end-to-end Turn —
live provider calls, a real Sandbox, settlement, and a durable trace — rather
than a pytest lane. The
[terminal TUI guide](../../../docs/how-to-guides/terminal-tui.md) owns launch,
`scripts/db_init.py`, configuration, Session resume, the backend/client split, and the
full slash-command list; read it for those and do not restate them here.

## Preconditions

- Run `uv run python scripts/db_init.py` before the first launch. The supervisor
  verifies Alembic head and never migrates, so a stale database fails the launch.
- Inspect the single `config/fleet.toml` policy before launching. The shipped
  configuration enables bounded children; edit `/settings` or TOML and restart
  to change `rlm.recursion_enabled`.
- A Turn consumes provider and Daytona quota. Confirm the run is authorized
  before starting.

## Drive the client in tmux

Size the pane explicitly. A detached session otherwise gets 80x24, which
truncates the trajectory and hides the footer evidence you are about to read.

```bash
tmux kill-session -t fleet 2>/dev/null || true
tmux new-session -d -s fleet -x 200 -y 50 -c "$PWD" "uv run fleet cli --port 8000"
```

Poll for readiness instead of sleeping. The pane renders **nothing for tens of
seconds** while the supervisor starts the database preflight, MLflow, and the
backend — its own readiness bound for the Daytona runtime is 150s, so poll at
least that long before concluding the launch failed:

```bash
timeout 150 bash -c 'until tmux capture-pane -t fleet -p | grep -qE "."; do sleep 2; done'
tmux capture-pane -t fleet -p
```

Ready looks like the header `FLEET  /  RLM OPERATOR`, a `› Start a Turn` empty
state, and the idle footer `Enter send · Shift+Enter newline · / commands`.

If nothing appears, read `.fleet_rlm/logs/latest.log` (the supervised backend)
and `.fleet_rlm/logs/mlflow-latest.log` rather than guessing.

## Send a Turn

Use `-l` so the text is sent literally — without it tmux interprets words as key
names — and send `Enter` as a separate call:

```bash
tmux send-keys -t fleet -l 'Report the mean of 1, 2 and 3. Show the arithmetic.'
tmux send-keys -t fleet Enter
```

## Wait for settlement

`Ctrl-C` does **not** quit the client; `/exit` is the quit command. During a
Turn, `Escape` cancels it. Do not reach for Ctrl-C when the pane looks stuck.

Wait in two stages. First confirm the Turn is actually running — the footer
switches from the idle hint to a cancel hint ending in `draft stays unsent` —
then wait for an outcome token:

```bash
timeout 120 bash -c 'until tmux capture-pane -t fleet -p | grep -q "draft stays unsent"; do sleep 2; done'
timeout 900 bash -c 'until tmux capture-pane -t fleet -p | grep -qE "[✓×!] (completed|failed|cancelled|interrupted)"; do sleep 5; done'
```

Do not grep for `completed` on its own. The dock keeps the **previous** Turn's
`✓ completed` on screen, so a naive poll returns immediately with the old
outcome before the new Turn has even started. Require the running hint first,
and after the outcome token confirm `Preparing Turn` is gone.

## Read the result

The client shows two usage surfaces, and the assistant message is the informative
one. Its `USAGE` line reports iterations, sub-LM calls, host calls, interpreter
errors, tokens, and elapsed time:

```text
USAGE  2 iterations · 0 sub-LM · 0 host · 0 errors · ↑ input 11k · ↓ output 685 · 0:11
```

The dock footer carries the same counts in abbreviated form plus the outcome:
`TOKENS ↑ 11k ↓ 685 · 2 iter · 0 sub-LM · 0 host · 0 errors · 0:11 · ✓ completed`.
The final answer prints on its own line below the transcript.

`capture-pane -p` returns only the visible screen; add `-S -2000` to include
scrollback when a long Turn pushed the answer off the top.

A Turn can finish with no semantic calls or children when they are unnecessary.
Child availability is controlled by `rlm.recursion_enabled` in the configuration;
usage counts alone do not prove a child ran.

## Inspect the traces

A TUI run is only verifiable from its trace. The MLflow tracking server is
supervisor-managed on `127.0.0.1:5001` and **dies with the TUI**, so read the
persisted store offline after exit:

- [Reading traces offline](references/reading-traces-offline.md): the
  `MlflowClient` recipe over `.fleet_rlm/mlflow/mlflow.db`, the expected span
  set, and the checks that separate a healthy Turn from a retry storm.

`/trace` prints the current Run's trace ID if you would rather grab the ID from
the client than search the experiment.

## Stop the client

```bash
tmux send-keys -t fleet -l '/exit'
tmux send-keys -t fleet Enter
tmux kill-session -t fleet 2>/dev/null || true
```

Read the traces before this: stopping the client stops the tracking server.
