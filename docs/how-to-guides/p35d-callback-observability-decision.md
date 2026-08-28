# P35-D callback observability decision

Status: **shadow-only, do not adopt for product or authoritative spans**.

## Decision

Fleet keeps the existing manual adapter instrumentation as the authoritative
engineering trace and keeps Runtime Events, SSE, pi-tui, and durable replay
strictly Fleet-owned. DSPy 3.3.1 interpreter callbacks are available as an
opt-in shadow probe on `DaytonaCodeInterpreter`, but they do not publish
Runtime Events, replace manual spans, or own cleanup.

P38 must not delete `sandbox.execute`, Fleet Tool observation, or any product
projection based on this record. A later adoption decision requires new
evidence for the named deletion inventory, a single-span trace count, and the
credentialed Root/child graph.

## Certified DSPy contract

The implementation follows the installed DSPy 3.3.1 public callback contract:

- `dspy.utils.callback.BaseCallback`;
- `dspy.utils.callback.with_callbacks`;
- `on_interpreter_startup_start/end`;
- `on_interpreter_execute_start/end`;
- `on_interpreter_tool_call_start/end`;
- `on_interpreter_shutdown_start/end`;
- `dspy.utils.callback_context.ACTIVE_CALL_ID` for start-hook parentage.

DSPy callback exceptions are fail-soft at the framework seam. Fleet also
contains recorder/exporter failures and stores only bounded operation,
status, duration, Tool name, parent identity, and exception category.

## Normalization and parity

`fleet_rlm.observability.dspy_callbacks.CallbackShadowRecorder` produces one
completed record per callback start/end pair. `compare_callback_records`
ignores generated DSPy call IDs and classifies differences as:

- **timing-only:** duration differences;
- **semantic:** operation, status, exception category, Tool identity, parent
  relation, or record-count differences.

The callback recorder keeps callback graph order separate from completion
order, so nested startup and Tool calls remain parented under the enclosing
execute call. Broker Tool requests route through the same decorated
`invoke_tool` seam; in-process and live backends therefore use one callback
operation per Tool request while the existing `observe_tool` path remains the
only product observer.

## Evidence

Deterministic evidence is maintained in:

- `.fleet-evidence/receipts/p35d-callback-shadow.json` (sanitized,
  git-ignored receipt for commit `1f83503a05a3bb36fefcf31f457c1731d6ddc430`);
- `tests/unit/backend/daytona/test_interpreter_callback_shadow.py`
  - success and terminal lifecycle pairing;
  - Tool success/failure parity and ancestry;
  - cancellation and timeout/provider-failure classification;
  - async Tool lifecycle parity;
  - argument-validation failure parity;
  - recursive `rlm_query` Tool lifecycle parity;
  - root versus local versus external parent normalization;
  - duration-only comparison;
  - callback handler/exporter failure fail-soft behavior;
  - no change to Fleet observation details.
- `tests/unit/backend/daytona/test_interpreter_tracing.py`;
- `tests/unit/backend/rlm/test_events_tool_observer.py`;
- `tests/contracts/backend/test_native_rlm_tracer.py`.

The focused lane is:

```text
uv run pytest \
  tests/unit/backend/daytona/test_interpreter_callback_shadow.py \
  tests/unit/backend/daytona/test_interpreter_tracing.py \
  tests/unit/backend/rlm/test_events_tool_observer.py \
  tests/contracts/backend/test_native_rlm_tracer.py -q
```

The lane passed on the candidate SHA, including the expanded cancellation,
timeout, async Tool, validation, recursive Tool, and parent-normalization
fixtures. Type and lint checks also passed for the changed modules.

The original credentialed Daytona MVP lane remains non-accepting because its
provider-driven scenario generated an extra exploratory action. A dedicated
serial credentialed lane avoids that model variability with DSPy
`DummyLM` scripts while still exercising the actual Root/child interpreter
construction seams:

```text
FLEET_LIVE=1 uv run pytest \
  tests/live/backend/test_callback_shadow_root_child.py -q -x
```

It passed and wrote the sanitized receipt
`.fleet-evidence/receipts/p35d-callback-shadow-root-child.json`. The receipt
proves DSPy 3.3.1 callbacks attached to the actual Root and child instances,
Root depth 0 and child depth 1, one `rlm_query` Tool edge, paired start/end
records, shared Run ancestry, no grandchild interpreter, and cleanup success.
The child interpreter's startup and execute records are externally parented
at the interpreter boundary, while its shutdown is parented to the local
Run; the explicit Root Tool edge is the certified ancestry link.

## Product parity conclusion

The shadow path has zero product effect by construction: callbacks are
attached only when explicitly supplied, callback records are not converted to
Runtime Events, and all callback/export/sanitization/finalization failures
are swallowed by the shadow path. The existing manual observer and
`turn_phase_span("sandbox.execute")` lanes remain unchanged and green.

This evidence supports **shadow-only** for P38. It does not authorize removal
of Fleet's manual adapter, Tool observer, or product projection paths.
