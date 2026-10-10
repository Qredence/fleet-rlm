# PRD 1 completion receipt — October 10, 2026

Implemented against Fleet RLM 0.7.11, HEAD `d733aca7d`, preserving the existing
uncommitted provider-setting retirement and unrelated operator changes. This
receipt describes the working tree; it is not a commit, release, deployment,
or live provider certification.

## Resulting behavior

- Legacy `rlm.max_provider_attempts` configurations fail with an actionable
  removal diagnostic. Active settings, editor metadata, and budget snapshots
  do not advertise that ceiling. Stock DSPy LMs retain their retry policy.
- Preparation gives the shared ledger the existing absolute execution deadline;
  it does not refresh time already spent preparing. `wrap_up_seconds` now
  refuses new tool/child admissions inside the reserve. Validated root
  finalization output can still be accounted until the deadline, subject to
  byte and count ceilings.
- Root corrections use the shared allowance plus the invocation-local allowance.
  Child corrections use local capacity only, but cannot outlive shared Turn
  admission or its deadline. The first final-iteration response remains uncharged.
- Settlement, cancellation, and cleanup handoff close admission before owned
  work drains. A late malformed response cannot start a new Fleet parse
  correction; this does not intercept DSPy-owned provider retries. Existing
  claim/generation fences continue to prevent late
  publication. Resource ownership remains with the existing cleanup owners.
- `rlm/adapter.py` isolates the iteration marker, private response-format hook,
  bounded parse repair, and finalization correction. Invocation policy replaces
  LM policy attributes; traces identify the actual isolated LM copies.
- Local and shared finalization exhaustion identify `finalization_attempts`
  through bounded diagnostics, including chained parse failures. Existing
  terminal status compatibility is preserved.
- Accounting happens before observer delivery. A stdout callback budget refusal
  remains terminal even when a backend serializes it into an execution error.

The [budget contract table](../reference/configuration.md#execution-budget-contract)
records the unit, owner, admission point, scope, and limits for each control.

## Acceptance coverage

| Criterion | Implementation and regression evidence |
| --- | --- |
| AC-01-01 | Policy/Settings retirement; frozen schema/editor inventory in `tests/config/test_config_schema.py`; budget snapshot regression; generated-reference check. |
| AC-01-02 | `test_retired_provider_attempt_key_has_actionable_error` in `tests/config/test_config.py`. |
| AC-01-03 | `tests/rlm/test_fleet_json_adapter.py`: native sync/async iteration markers, parse repair, wrap-up corrections, native extraction, semantic batching and prompt admission, DSPy-owned provider retries, cancellation. Endpoint response-format regressions remain in `tests/rlm/test_program_inputs.py`. |
| AC-01-04 | `tests/rlm/test_budget.py`: separate allowances, settlement/deadline checks, atomic reservations and concurrency. Adapter diagnostic tests preserve the dimension through parse causes. Interpreter tests exercise root-only reserve output, byte ceilings, and invalid finalization grammar. Preparation test verifies shared/execution deadline identity. |
| AC-01-05 | `test_transferred_cleanup_closes_admission_before_blocked_worker_drains` plus existing coordinator cancellation, claim-loss, cleanup, commit, and stream tests. Interpreter action admission also refuses settled Turns. |
| AC-01-06 | Native DSPy construction/version tests and import-safety checks; no LM wrapper, retry interceptor, DSPy upgrade, or replacement loop. Copy/history and direct callback attribution regressions retain parity. |

## Validation

| Command | Outcome |
| --- | --- |
| `uv run pytest -n 0 tests/config/test_config.py tests/config/test_config_schema.py tests/rlm/test_budget.py tests/rlm/test_fleet_json_adapter.py tests/rlm/test_native_dspy_contract.py tests/sessions/test_turn_preparation.py` | 262 passed before the additional late-parse-delivery regression. |
| `uv run pytest -n 0 tests/rlm/test_fleet_json_adapter.py tests/rlm/test_budget.py` | 124 passed, including both sync/async late-response refusal cases. |
| `uv run pytest -n 0 tests/daytona/test_daytona_adapter.py tests/sessions/test_turn_coordinator_execution.py tests/sessions/test_turn_coordinator_cancellation.py tests/rlm/test_recursion_isolation.py tests/rlm/test_runtime_execution.py tests/observability/test_turn_tracing.py tests/rlm/test_program_inputs.py tests/rlm/test_turn_history_integration.py tests/rlm/test_recursion_lease_cleanup.py` | 304 passed. |
| `uv run pytest -n 0 tests/skills/test_skill_turn_contract.py` | 7 passed after replacing the infinite preparation-deadline fixture. |
| `make config-reference` | Generated reference through the owning generator. |
| `make check` | Passed: 83.63% backend coverage; 31 TUI test files / 474 tests; API and stream contract checks; lint, format, type, architecture, hygiene, and configuration-reference checks. Final rerun includes the late-parse-delivery regressions. |
| `git diff --check` | Passed. |

The full offline gate clears credential environment variables for its coverage
lane. Existing non-fatal SQLAlchemy warnings and the TUI explicit-any warning
were observed; no new live provider run was authorized or executed.

The initial new regressions failed as expected for child settlement/deadline
bypass and missing local exhaustion dimension. An initial `make check` stopped
on pre-existing formatting in `tests/live/_tool_chunks.py`; its formatting was
normalized without changing logic before retrying. The first coverage run
then found one remaining infinite-deadline Skill test fixture; it was updated
to a finite deadline and its owning suite passed.

## Documentation and compatibility

The PRD 1 Markdown and report register distinguish current implementation from
pinned historical evidence. HTML dossiers, workbench data, original manifests,
and prior probe receipts remain historical snapshots. No broad repository
rearrangement, database migration, public SSE shape change, or API/stream
artifact regeneration was required. Configuration documentation was regenerated
through its owning command.

Dependencies remain DSPy 3.4.0, Daytona 0.220.0, and FastAPI 0.142.2. Sources:
[DSPy RLM](https://dspy.ai/3.4.0/diving-deeper/rlm/),
[DSPy LM](https://dspy.ai/3.4.0/api/models/LM/),
[Daytona Process](https://www.daytona.io/docs/en/python-sdk/sync/process/),
[Daytona Sandbox](https://www.daytona.io/docs/en/python-sdk/sync/sandbox/), and
[FastAPI lifespan](https://fastapi.tiangolo.com/advanced/events/).

Offline tests establish contract behavior. No credentialed LLM, Daytona,
database, or benchmark run was performed for this implementation. Client wait
timeouts do not certify remote termination; cleanup ownership and confirmation
remain necessary. No task-quality uplift, provider containment, or aggregate
spend guarantee is claimed.
