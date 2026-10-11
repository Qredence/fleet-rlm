# Remaining legacy cleanup verification

This pass follows the data-preserving cleanup plan. Saved-memory migration,
historical idempotency matching, admitted HTTP/SSE shapes, mandatory recursion,
provider cleanup retries, and cancellation containment remain supported. It
makes no schema, dependency, model, snapshot, or configuration changes.

## Contract changes

- Mounted workspace acquisition calls `open_workspace_sandbox` directly with
  `reuse_warm=True`. The test runtime asserts that argument.
- Every Daytona runtime uses its initialized binding authority. Unknown binding
  identities are rejected; replacement and revocation preserve generation fencing.
- The synchronous Sandbox view constructs all three required SDK services
  directly. Test Sandboxes supply those services; typed secrets are read directly.
  The pinned SDK organization-header workaround remains in its existing owner.
- MLflow uses one certified clear-before-restore span sanitizer. Sanitization
  tests construct real SDK spans; export privacy and observation-outage tests remain.
- Diagnostic producers use explicit four-state statuses. The existing `ok`
  flag is derived from status for public presentation. Claim usage is selected
  through explicit command types and tested through durable SQL transitions.
- The unused history validator, its export, and obsolete-mode tests are removed.
  The settings presenter requires the save callback; the one-shot presenter and
  returned-update fallback are removed. Save errors and revision conflicts retain
  their existing product behavior.

## Validation

Exact commands and results are recorded in ignored `.fleet_rlm/legacy-cleanup-*.log`
files. Live evidence is stored in `.fleet_rlm/legacy-cleanup-validation/` without
credentials in this report or Git.

The backend source count decreased from 51,499 lines at the preceding completed
simplification checkpoint to 51,320 lines in this pass (179 fewer lines).

Deterministic validation passed:

```bash
uv run pytest tests/workspace/test_host_io.py tests/sessions/test_session_manager.py -q
uv run pytest tests/sessions/test_turn_preparation.py tests/skills/test_skill_turn_contract.py -q
uv run pytest tests/sessions/test_turn_claim_adapter_parity.py -q
uv run pytest tests/sessions/test_turn_claim_adapter_parity.py tests/observability/test_daytona_diagnostics.py tests/observability/test_mlflow_tracing_config.py -q
make check
make check-docs
git diff --check
```

The initial focused run exposed incomplete filesystem doubles. They were updated
and verified by the focused suites and final full gate. `make check` passed with
84.2% backend coverage and all 473 TUI tests; generated API, SSE, and configuration
contracts were current. No generated contract changes were necessary.

## Live execution

The existing operator authorization covers model, Daytona, database, and MLflow
usage. The previous CLI instance was stopped normally, then restarted with the
exact command `uv run fleet cli`. Models, snapshots, DSPy 3.4.0, Daytona 0.220.0,
and MLflow 3.16.1 remain those recorded in the preceding
[Fleet simplification receipt](fleet-simplification-verification.md).

Session: `1235fecf-9d07-4cef-9647-e96e031fda3f`.

The recursive/memory Run `a381d49b-942d-4776-9c6b-e73edd9fb633` committed revision
1. Preparation trace `tr-3ecca4551d17e899020e903603bb2485` has 5 spans; execution
trace `tr-ef7c014f8d53f3284f42889cee647dfd` has 72 spans. Complete JSON inspection
confirmed both traces were OK, every span finished, no error spans, and every
non-root parent ID resolved within its trace.

Remote root Python computed `sum(range(1,1001)) = 500500`. Exactly one isolated
child completed two iterations, independently verifying the integer formula and
direct sum. Two child model calls and two child Sandbox executions were correctly
nested under `RLM.child.invoke`, attributed to call index 1 and depth 1. Five
actual model calls and six Sandbox executions occurred overall, including root
history binding; repeated native tool observation wrappers did not represent
repeated host execution. The root invoked `remember` once and
`read_workspace_memory` once, verifying the exact learning
`Legacy cleanup verified integer 500500` with memory ID `27c157c8`.

The complete trace scan found no configured secret values or local private-path
prefixes. Child cleanup reported confirmed; independent Daytona SDK `get` returned
NotFound for child `1f163823-278a-4694-a4f5-ede08bd76b0e`, and the Session's child
resource listing was empty. The healthy root remained explicitly runtime-owned
for the following warm continuation.

Warm Run `606fc86b-60c1-48a7-a474-24e386c3db63` committed revision 2. Its preparation
trace `tr-cc2180c3256359a756eb0a252ca83b87` has 5 spans, and execution trace
`tr-9427732a1172c4f044861e112eaffb8a` has 30. Both complete traces were OK with
no failed/unfinished spans or unresolved parent IDs. The root Sandbox
`8e5b9d6d-3c45-42c2-a737-31b549ab2d9b` and binding generation 1 were unchanged.
Remote output confirmed `local_sum` and `child` were absent before assignment;
the memory read returned the existing exact learning. There was one host memory
read, no memory write, and no child invocation. All four complete traces passed
the configured-secret and private-path scans.

After `/exit`, the backend completed normal application shutdown. Independent
SDK checks confirmed the root and child deleted, with empty Session root,
recursive-child, and Workspace host-I/O listings. SQL showed no running or
settling Runs in this Session. These checks are preserved in `provider-state.json`;
trace success alone was not treated as deletion evidence.

A fresh `uv run fleet cli` instance was then launched in tmux session
`fleet-simplify-final` and left running at the operator's request, separately from
the completed validation shutdown. The application serves FastAPI on port 8000
and MLflow on port 5001. All changes remain uncommitted; saved data and unrelated
local edits are preserved.
