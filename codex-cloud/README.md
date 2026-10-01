# Fleet-RLM Daytona simplification — archived implementation bundles

## Archive status in this checkout

These files preserve the 2026-09-30 proposals against commit
`c3f3d536242c7663cfeecdfbf63670b51630e9aa`. Their implementation status,
source findings, and patch instructions refer to that historical baseline;
they do not describe the current checkout. Do not apply these patches to the
current branch. The cleanup fix now being delivered is documented in
[Daytona cleanup acceptance](../docs/testing/daytona-cleanup-acceptance.md).

The unsuffixed Markdown and patch files are the v2 bundle's extracted documents.
Loose Markdown has normalized trailing whitespace; the ZIPs preserve original bytes.
The `(1)` PRD and patch preserve the original bundle version. Both ZIPs are
retained as original inputs. The helper scripts, manifests, and validation
receipts listed below are inside the ZIPs, rather than loose files here.

To inspect or reproduce the historical isolated checks, extract the selected
ZIP into a separate scratch directory and run from its extracted bundle directory:

```bash
unzip fleet-daytona-implementation-bundle-v2.zip -d /tmp/fleet-daytona-bundle-v2
cd /tmp/fleet-daytona-bundle-v2/fleet-daytona-self-contained
```

The following sections retain the v2 bundle instructions for its pinned baseline.

This bundle is designed to be reviewed and used **without any earlier Plan A/Plan B/Plan C/Plan D documents**.

Start with:

1. `PRD-and-implementation-plan.md` — normative target design and three-PR implementation plan.
2. `IMPLEMENTATION-PLAN.md` — standalone ordered implementation plan and complete intended touch tree.
3. `BASELINE-AND-EVIDENCE.md` — pinned repository facts and the exact evidence behind the design.
4. `DECISION-RECORD.md` — alternatives, ownership decision, invariants and future reconsideration criteria.
5. `IMPLEMENTATION-CHECKLIST.md` — file/function-level work list and acceptance matrix.
6. `fleet-daytona-correctness.patch` — directly applicable PR 1 patch with its own cover letter.

## What is implemented versus specified

```text
PR 1  correctness hardening                         PATCH INCLUDED
PR 2  invocation-lifetime binding simplification   SPECIFIED, NOT IMPLEMENTED
PR 3  typed backend/result + broker ownership       SPECIFIED, NOT IMPLEMENTED
```

The patch is intentionally small. It changes two existing repository files and creates no production/test file in Fleet.

## Pinned baseline

```text
Repository:  Qredence/fleet-rlm
Commit:      c3f3d536242c7663cfeecdfbf63670b51630e9aa
Fleet:       0.7.10
DSPy:        3.4.0
Daytona:     0.218.0
```

Reviewed source blob IDs are stored in `manifest.json` and `BASELINE-AND-EVIDENCE.md`.

## Bundle contents

| File | Purpose |
|---|---|
| `PRD-and-implementation-plan.md` | Full standalone PRD: objective, requirements, target runtime, file-by-file plan, tests, rollout and definition of done |
| `IMPLEMENTATION-PLAN.md` | Standalone ordered PR plan, complete intended touch tree, reference scans and exit criteria |
| `BASELINE-AND-EVIDENCE.md` | Source-observed facts, findings, current architecture, exact commit/blobs and source register |
| `DECISION-RECORD.md` | Why the current broker/runtime is simplified rather than replaced; alternatives and invariants |
| `IMPLEMENTATION-CHECKLIST.md` | Actionable PR 1/2/3 checklist, reference scans and acceptance matrix |
| `fleet-daytona-correctness.patch` | PR 1 mail-style unified patch with rationale, baseline and verification commands in the patch header |
| `replacement-methods.py` | Complete PR 1 replacement methods for inspection; not a complete module |
| `regression-tests.py` | Complete PR 1 test additions; insertions, not a complete test module |
| `reviewed-methods.txt` | Original method bodies used by the isolated before/after check |
| `validate_isolated.py` | Method-level harness that does not import the real Fleet/DSPy/Daytona stack |
| `baseline-validation.txt` | Isolated original result |
| `patched-validation.txt` | Isolated patched result |
| `validation-summary.json` | Machine-readable validation limits/results |
| `manifest.json` | Baseline identities, bundle scope and checksums |

## Architecture in one screen

```text
native dspy.RLM
   |
   | creates/finalizes one interpreter per invocation
   v
DaytonaCodeInterpreter
   |
   | adapts DSPy tools, output contract, errors, observations
   v
_SandboxProcessBackend
   |
   | owns live invocation execution transport
   v
DaytonaHttpToolBroker ---- host tool calls ----> Fleet registered tools
   |
   v
Daytona Sandbox

Separately:
DaytonaRuntime owns root/child Sandbox leases, Volumes, admission and cleanup.
```

The refactor preserves that ownership split and removes unnecessary mutable/compatibility paths inside the adapter.

## PR 1: apply

Work from a clean checkout of the pinned base:

```bash
git switch -c fix/daytona-dispatch c3f3d536242c7663cfeecdfbf63670b51630e9aa
git apply --check /absolute/path/fleet-daytona-correctness.patch
git apply /absolute/path/fleet-daytona-correctness.patch
```

Then run:

```bash
uv sync --frozen --dev
uv run pytest tests/unit/backend/daytona/test_daytona_adapter.py
uv run ruff check src/fleet_rlm/daytona/interpreter.py tests/unit/backend/daytona/test_daytona_adapter.py
uv run ruff format --check src/fleet_rlm/daytona/interpreter.py tests/unit/backend/daytona/test_daytona_adapter.py
uv run ty check
git diff --check
```

Follow with the repository's wider established unit/contract/live lifecycle gates before merge.

## Reproduce only the isolated PR 1 checks

With pytest installed, from this bundle directory:

```bash
python validate_isolated.py
python validate_isolated.py --baseline
```

Expected bundle evidence:

```text
patched:  8 passed
baseline: 5 passed, 3 failed
```

The baseline command intentionally returns failure. These checks prove the two changed method behaviors in isolation only; they are not a replacement for the real Fleet dependency or Daytona lifecycle tests.

## Important scope limits

This bundle does **not** claim that PRs 2/3 have been implemented. It also does not claim live Daytona certification, full repository `git apply --check`, or full Ruff/type/test success in the generation environment.

It deliberately does **not** include a Daytona native-context migration, new memory design, new recursion architecture, provider registry, new dependency, or public API redesign.
