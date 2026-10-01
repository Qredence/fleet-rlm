# Daytona cleanup correctness and acceptance

Date: 2026-10-01.

## Candidate and status

- Base commit: `a041d747f711747d527724ddeb0100318c2406b9` on `main`.
- Implementation: cleanup changes prepared on `fix/daytona-cleanup-ownership`;
  no live-certified candidate SHA yet.
- Local acceptance: focused lifecycle lane, full gate, and Python 3.11 compatibility passed.
- Live acceptance: pending separate operator authorization and a clean non-main candidate.

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

After explicit operator authorization and preparation of a clean non-main
candidate, use fresh evidence paths and existing bounded native/recursive
profiles for:

1. `scripts/live_daytona_verify.py`: native semantics and attachment/artifact durability.
2. `scripts/live_recursive_batch_canary.py`: ordered children, retained-root reuse, isolation, and cleanup.
3. The interpreter-invocation deletion lifecycle test: strict shutdown and provider-confirmed Sandbox absence.
4. The FastAPI cancellation test: unresolved execution blocks reuse and cleanup settles.

Record the immutable candidate SHA, exact commands, environment, receipt paths,
cleanup observations, and unresolved failures. Full acceptance remains pending
until these live gates pass; local results do not certify provider behavior.

## Pre-commit review

The full uncommitted tree was reviewed on 2026-10-01. No actionable production
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
