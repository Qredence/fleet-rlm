# Daytona cleanup correctness and acceptance

Date: 2026-10-01.

## Candidate and status

| Revision | Status |
| --- | --- |
| Merged code | Cleanup follow-up #579 merged to `main` as `e45bff132e5b2fdfe14ff81f7cc66ac89aedd536` (base `a041d747f711747d527724ddeb0100318c2406b9`) |
| Locally tested | #579 tree: receipts below. Follow-up candidate `e7b1e6e72`: see [2026-10-01 follow-up](#2026-10-01-follow-up-attachment-parity-and-broker-results) |
| Live tested | `e7b1e6e72`: all four live gates passed (see the follow-up section) |

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

## Live acceptance gates

On a clean non-main candidate, with operator authorization, use fresh evidence
paths and the existing bounded native/recursive profiles for:

1. `scripts/live_daytona_verify.py`: native semantics and attachment/artifact durability.
2. `scripts/live_recursive_batch_canary.py`: ordered children, retained-root reuse, isolation, and cleanup.
3. The interpreter-invocation deletion lifecycle test: strict shutdown and provider-confirmed Sandbox absence.
4. The FastAPI cancellation test: unresolved execution blocks reuse and cleanup settles.

Record the immutable candidate SHA, exact commands, environment, receipt paths,
cleanup observations, and unresolved failures. Local results do not certify
provider behavior.

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
[implementation plan](../../codex-cloud/IMPLEMENTATION-PLAN.md), plus the defects
the live gates then exposed. Branch: `fix/daytona-attachment-parity-and-broker`.
Candidate: `e7b1e6e72b5bdaa6e558f1cf7765b2960ce59303`. Environment: Python 3.13.13, DSPy 3.4.0, Daytona SDK 0.218.0,
MLflow 3.16.1 (local server via `uv run fleet cli`), Fleet 0.7.10, policy models
`deepseek-v4.1-flash`.

Behavior established by new regressions:

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

Defects found by the live gates and fixed in their owners:

- **Concurrent Volume creation (`daytona/runtime.py`).** When two creates race,
  the provider rejects the loser with HTTP 500 or 400 "already exists", not 409.
  This was reproduced directly against the provider. The Turn failed with
  `ProviderRequestError`. Those responses are now reconciled by one lookup,
  without repeating creation.
- **Declared-output credential rule (`rlm/result.py`).** It rejected
  `iteration_token=iteration_token` and `iteration_token=...` in a model's
  summary of its own code, so valid Turns failed with "Turn output is invalid".
  Native-proof live runs passed 2 of 7 before the fix and 4 of 4 after it. Real
  values are still rejected.
- **Stale live harnesses.** Several live tests had drifted from the runtime:
  - the bridge dispatcher and session-scoped Volume subpath;
  - hand-rolled attachment and artifact storage. The durability test now
    composes production's objects: durable blobs go to the Workspace Volume
    through host-I/O Sandboxes, Run copies are staged in `/tmp/fleet/<run>`,
    and the Artifact is read back from the Volume after replacement;
  - the frozen preparation plan;
  - `close_root_session` never being called;
  - `_bindings` access.

  Separately, the verifier replaced proof failures with `cleanup_failed`.

| Local gate (candidate above) | Result |
| --- | --- |
| Python tests with coverage (`make check`) | Passed; total coverage 83.12% (75% required) |
| Lint, format, `ty` | Passed |
| API/stream contracts, 444 TUI tests | Passed |
| Codebase-tree, dependency-boundary, docs/hygiene, profile matrix | Passed. Docs/hygiene ran from a clean worktree; a local, git-ignored `docs/plans/` directory fails the hygiene check in the working checkout |

| Live gate (candidate above) | Result |
| --- | --- |
| Interpreter/deletion lifecycle test | **Passed**. Same-invocation and fresh-invocation state, async host tools, typed SUBMIT, strict interpreter cleanup, and provider-confirmed Sandbox absence |
| `scripts/live_daytona_verify.py` | **Passed**. Native semantic calls, and attachment/artifact durability, including text and binary prepared-context loading with reported access IDs |
| `scripts/live_recursive_batch_canary.py` (`daytona-recursive`) | **Passed**. Two ordered native children, peak concurrency 2, retained root reused on a second Turn, and cleanup with admission restored |
| FastAPI cancellation lane | **Passed**. Cancellation observed, lease released, admission restored, and Sandbox confirmed absent |

Receipts: `.scratch/daytona-current-acceptance/e7b1e6e72b5bdaa6e558f1cf7765b2960ce59303/`. These results certify
this candidate's local and live gates. They do not certify release or
promotion.

## 2026-10-02 follow-up: Workspace files on the Daytona Volume

Branch `fix/workspace-files-on-volume`, candidate
`d37219504` (Python 3.13.13, DSPy 3.4.0, Daytona SDK 0.218.0).

Defects fixed:

- **`/api/files` used the API host's disk.** `DaytonaWorkspaceGateway` opened a
  host-I/O Sandbox, then read and wrote host-local paths. Files now go through
  that Sandbox's Volume mount (`DaytonaWorkspaceFiles`).
- **Writes into new directories failed through the Sandbox storage.** The
  SDK's not-found error was not recognized while probing path components.
- **The model's filesystem contract named `/home/daytona/fleet`.** The Sandbox
  mounts only the Session Workspace at `/workspace`; the contract now says so.
- **Another declared-output false positive.** `iteration_token =
  issue_iteration_token()` failed the native proof once (trace
  `tr-344122bf…`); call-valued assignments are now accepted.

| Gate (candidate above) | Result |
| --- | --- |
| Local: lint, format, `ty`, coverage (83.15%), boundaries, docs, API/stream contracts | Passed |
| Live through `uv run fleet cli`: `PUT`/`GET /api/files/content`, then `/api/volume/tree` | Passed. The file is listed as `files/volume-check/…` on the Volume; nothing on the API host |
| Interpreter/deletion lifecycle test | Passed |
| `scripts/live_daytona_verify.py` (native semantic calls + production-path durability) | Passed |

Defaults now match the layout: one `SESSION_WORKSPACE_MOUNT_PATH`, the
`fleet-volume` name default, and no dead `/home/daytona/fleet` or child mount
arguments.
