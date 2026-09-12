# Broker latency regressions

Inspect `src/fleet_rlm/daytona/broker.py` and `interpreter.py` at the affected
cell boundary. Separate configured wait time from observed transport latency.

Attribute time using callback poll count, empty polls, pending-batch size,
callback dispatch, host-tool execution, result posting, output polling,
output characters, release count, and total cell versus `run_code` duration.

Use the dominant measured cost to select an investigation:

- Repeated setup: inspect callback executor and HTTP-client reuse and ownership.
- Empty polling: compare bounded long polling/backoff with current request volume
  and response latency; verify prompt recovery when useful work arrives.
- Dispatch stalls: distinguish tool duration from lock contention and network I/O.
- Output drain: inspect bounded release reads and final-output delivery.

Preserve single fulfillment/result posting, ordered output, bounded waits, and
owned cleanup. Do not remove instrumentation to improve apparent latency.
Exercise streaming, callback fulfillment, poll failure recovery, reuse, and
cleanup in the existing broker tests when changing those behaviors.
