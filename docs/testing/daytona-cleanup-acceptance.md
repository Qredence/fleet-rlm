# Daytona cleanup correctness and acceptance

Date: 2026-10-01.

## Candidate and status

| Revision | Status |
| --- | --- |
| Merged code | Cleanup follow-up #579 merged to `main` as `e45bff132e5b2fdfe14ff81f7cc66ac89aedd536` (base `a041d747f711747d527724ddeb0100318c2406b9`) |
| Locally tested | #579 tree: receipts below. Follow-ups A and B: `927ccccbac47a64fb141beb7eb191363599061bf`, see [2026-10-01 follow-up](#2026-10-01-follow-up-attachment-parity-and-broker-results) |
| Live tested | `927ccccbac47a64fb141beb7eb191363599061bf`: the interpreter/deletion lifecycle gate passed; the other three gates remain pending (see the follow-up section) |

## Implemented behavior

Runtime shutdown selects its call shape through signature binding before invoking
the method. A body-level `TypeError` remains a cleanup failure, while verified
no-argument implementations remain supported. Opaque callables receive one
keyword invocation, and unsupported or non-callable shutdowns fail visibly.

The broker retains failed session/client handles until their respective cleanup
operations succeed. Explicit retries preserve ownership through one or two
consecutive deletion failures and become idempotent after success.

Runtime release retains the failed lease, late owner, and admission until existing
Sandbox containment succeeds. Confirmed closed/provider-retired leases no longer
reuse a cached failed release task. Runtime closure then removes the root record
and remains idempotent without additional SDK deletion or Sandbox fencing.

These are deterministic local assertions with mocked provider operations, not
live provider certification. Production configuration, dependencies, models,
public schemas, and recursion policy are unchanged. Existing `codex-cloud`
artifacts are preserved.

## Local receipts

Environment: Python 3.13.13, DSPy 3.4.0, Node 24.16.0, pnpm 12.6.0.
Node satisfies the TUI package's `>=22.19.0` engine; CI uses Node 22.23.2.
Python 3.11.15 dependencies were installed from the frozen lockfile into
`/tmp/fleet-rlm-cleanup-py311-20261001`; its import/version check passed.

| Gate | Result |
| --- | --- |
| Focused lifecycle/DSPy/FastAPI lane | 234 passed, zero failures/errors/skips; `.scratch/cleanup-focused.xml` |
| Initial `make check` | Stopped at formatting of a new adapter regression; formatting corrected |
| Final `make check` | Passed; coverage 83.06%, all 444 TUI tests passed; API/stream, typing, lint, boundaries, and docs current |
| Python 3.11 backend/contracts compatibility | 2,096 passed, zero failures/errors/skips; `.scratch/cleanup-py311.xml` |

Focused command (provider/database environment variables were blanked):

```bash
FLEET_DAYTONA_API_KEY= FLEET_OPENAI_API_KEY= FLEET_LLM_BASE_URL= FLEET_DATABASE_URL= \
uv run pytest -q -n 0 \
  -m 'not live_llm and not live_daytona and not benchmark and not db and not packaging' \
  --junitxml=.scratch/cleanup-focused.xml \
  tests/unit/backend/daytona/test_daytona_adapter.py \
  tests/unit/backend/daytona/test_sandbox_variable_binding.py \
  tests/unit/backend/daytona/test_broker.py \
  tests/unit/backend/daytona/test_interpreter_adversarial.py \
  tests/unit/backend/daytona/test_direct_daytona_stress.py \
  tests/unit/backend/daytona/test_runtime.py \
  tests/unit/backend/daytona/test_child_lease_cleanup_ownership.py \
  tests/unit/backend/daytona/test_recursive_child_runtime.py \
  tests/unit/backend/rlm/test_dspy_compat_seam.py \
  tests/unit/backend/rlm/test_recursion_lease_cleanup.py \
  tests/unit/backend/rlm/test_recursion_isolation.py \
  tests/contracts/backend/test_native_dspy_fastapi_vertical_slice.py
```

Python 3.11 compatibility command, using the isolated frozen environment and the
same provider/database variable fence:

```bash
UV_PROJECT_ENVIRONMENT=/tmp/fleet-rlm-cleanup-py311-20261001 \
UV_PYTHON=/Users/zocho/.local/share/uv/python/cpython-3.11-macos-aarch64-none/bin/python3.11 \
FLEET_DAYTONA_API_KEY= FLEET_OPENAI_API_KEY= FLEET_LLM_BASE_URL= FLEET_DATABASE_URL= \
uv run --no-sync --python 3.11 pytest -q -n 2 --dist=loadfile -p no:warnings \
  -m 'not live_llm and not live_daytona and not benchmark and not db and not packaging' \
  --junitxml=.scratch/cleanup-py311.xml \
  tests/unit/backend tests/contracts/backend
```

## Remaining live acceptance

On a clean non-main candidate, with operator authorization, use fresh evidence
paths and the existing bounded native/recursive profiles for:

1. `scripts/live_daytona_verify.py`: native semantics and attachment/artifact durability.
2. `scripts/live_recursive_batch_canary.py`: ordered children, retained-root reuse, isolation, and cleanup.
3. The interpreter-invocation deletion lifecycle test: strict shutdown and provider-confirmed Sandbox absence.
4. The FastAPI cancellation test: unresolved execution blocks reuse and cleanup settles.

Record the immutable candidate SHA, exact commands, environment, receipt paths,
cleanup observations, and unresolved failures. Full acceptance remains pending
until these live gates pass; local results do not certify provider behavior.

## #579 review before merge

The #579 tree was reviewed before merge on 2026-10-01. No actionable production
code defect was found. The imported bundle README now distinguishes historical
plans and patches from current behavior and explains where its helper files
can be extracted. Loose Markdown whitespace was normalized; original ZIPs and
patch bytes are preserved. The acceptance receipt is linked from `docs/index.md`.

The fresh `make check` run passed Python, coverage (83.1%), all 444 TUI tests,
generated contracts, typing, lint, and boundary checks, then detected the
unlinked receipt. After linking it, `make check-docs` passed. Logs are local:
`/tmp/fleet-cleanup-review-check.log` and `/tmp/fleet-cleanup-review-docs.log`.
The three modified test modules also passed in a separate focused run
(`/tmp/fleet-cleanup-review-focused.log`). No live provider lane was run.

The final complete `make check` rerun passed after these review fixes; its log
is `/tmp/fleet-cleanup-review-final-check.log`.

## 2026-10-01 follow-up: attachment parity and broker results

Scope: Follow-ups A and B of the active
[implementation plan](../../codex-cloud/IMPLEMENTATION-PLAN.md), on branch
`fix/daytona-attachment-parity-and-broker`, candidate
`927ccccbac47a64fb141beb7eb191363599061bf`. Environment: Python 3.13.13,
DSPy 3.4.0, Daytona SDK 0.218.0, Fleet 0.7.10.

Behavior established by the new local regressions:

- Both backends now run the same prepared-attachment materializer. Matrix tests
  run against the real generated Sandbox loader and the in-process backend, and
  cover the following:
  - text and multiple-attachment metadata;
  - invalid UTF-8 and NUL bytes;
  - the single-text `context` value;
  - rejection of a bad manifest digest or root, a wrong file length or digest,
    and an out-of-mount symlink.
- The loader is installed only for DSPy's setup action, so later model actions
  cannot call it. Its failures map to `ContextVerificationError`.
- Verified attachment IDs come from factory-created invocations, are recorded
  once after settlement on success and on a later failure, and never come from
  the retained template.
- Run scratch deletion makes exactly one call. A body-level `TypeError` stays
  visible, the path is retained, and an explicit retry succeeds.
- The broker accepts one result per call. A duplicate before consumption, a
  result before a lease is issued, a stale lease, and a duplicate after
  consumption are each rejected with 409. The first value survives.

| Local gate | Result |
| --- | --- |
| Backend Daytona/RLM/contract lane (`-n 0`, non-live markers) | 1,168 passed |
| `make check` | Lint, format, `ty`, coverage (83.11%), 444 TUI tests, codebase-tree and dependency-boundary checks passed. `check-instructions` then stopped on an untracked, git-ignored local `docs/plans/` directory that is not part of the candidate; the tracked docs check is recorded separately below |

Live gates were run by the operator on 2026-10-01 against the candidate above,
with receipts in `.scratch/daytona-current-acceptance/<sha>/`:

| Live gate | Outcome |
| --- | --- |
| Interpreter/deletion lifecycle test | **Passed**. Same-invocation state, fresh invocation state, async host tools, typed SUBMIT, strict interpreter cleanup, and provider-confirmed Sandbox absence |
| `scripts/live_recursive_batch_canary.py` (`daytona-recursive`, `deepseek-v4.1-flash`) | **Pending**. Ordered two-child outcomes, distinct child Sandboxes and Volume subpaths, call indexes `[1, 2]`, and peak concurrency 2 were asserted. The run then failed its final check because no root MLflow trace ID was exposed: the local tracking server was not running |
| `scripts/live_daytona_verify.py` | **Pending, harness defect**. The durability test calls `DaytonaRuntime.from_settings()` without the now-required `dispatcher` argument. It fails identically on `main`, before any provider operation. It now also asserts text and binary prepared-context loading and access reporting, but that assertion has not yet run live |
| FastAPI cancellation lane | **Pending, harness defect**. The test assigns to the frozen `TurnPreparationPlan` (`preparation._models`). It fails identically on `main`, before any provider operation |

The two harness defects come from earlier lifecycle refactors and have not been
fixed by this follow-up. Full live acceptance stays pending until all four gates
pass on one candidate.
