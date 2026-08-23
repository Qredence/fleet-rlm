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

`fleet_rlm.observability.callback_shadow.CallbackShadowRecorder` produces one
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

- `tests/unit/backend/daytona/test_interpreter_callback_shadow.py`
  - success and terminal lifecycle pairing;
  - Tool success/failure parity and ancestry;
  - duration-only comparison;
  - callback handler/exporter failure fail-soft behavior;
  - no change to Fleet observation details.
- `tests/unit/backend/daytona/test_interpreter_tracing.py`;
- `tests/unit/backend/rlm/test_tool_observer.py`;
- `tests/contracts/backend/test_native_rlm_tracer.py`.

The focused lane is:

```text
uv run pytest \
  tests/unit/backend/daytona/test_interpreter_callback_shadow.py \
  tests/unit/backend/daytona/test_interpreter_tracing.py \
  tests/unit/backend/rlm/test_tool_observer.py \
  tests/contracts/backend/test_native_rlm_tracer.py -q
```

The lane passed on the candidate SHA with 31 tests. Type and lint checks also
passed for the changed modules.

The credentialed Daytona MVP lane was attempted serially with
`FLEET_LIVE=1` and mission-owned evidence output. It did not produce
callback-specific acceptance evidence: the pre-existing model-driven MVP
scenario failed its protocol assertion after the provider generated an
extra exploratory action. No adoption claim is made from that run. The
credentialed Root/child callback graph remains a required P38 re-certification
before any manual span deletion.

## Product parity conclusion

The shadow path has zero product effect by construction: callbacks are
attached only when explicitly supplied, callback records are not converted to
Runtime Events, and all callback/export/sanitization/finalization failures
are swallowed by the shadow path. The existing manual observer and
`turn_phase_span("sandbox.execute")` lanes remain unchanged and green.
