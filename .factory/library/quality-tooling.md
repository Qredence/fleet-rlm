# Quality Tooling Quirks

Factual knowledge about the project's quality, evaluation, and dataset-generation tooling discovered during milestone work.

## LongCoT Vendor Data — Logic Domain Has No Answers

The `vendor/longcot/src/data/logic/` domain files contain only `null` answers. The `scripts/generate_longcot_gepa_dataset.py` generation script handles this gracefully by filtering out empty answers and warning, but this means the `logic` domain yields zero rows unless a separate answer source is provided. Workers extending the dataset generator or adding new domains should expect this and validate accordingly.

## Module Registry Test Isolation

`module_registry.py` exposes `_reset_registry()` (prefixed as internal) for test isolation. Tests that touch the module registry should reset state before each test via an `autouse` fixture:

```python
@pytest.fixture(autouse=True)
def _clean_registry() -> None:
    from fleet_rlm.runtime.quality.module_registry import _reset_registry
    _reset_registry()
```

Failure to reset the registry can cause cross-test pollution because `_ensure_registered()` imports entrypoint modules once per process and caches results in `_REGISTRY`.

## Canonical Per-Module Optimization Entrypoint Pattern

New optimizable DSPy modules should follow the pattern established by `runtime/quality/optimize_longcot.py`:

1. Create a file named `runtime/quality/optimize_<module_slug>.py`.
2. Define `_module_factory()`, `_row_converter()`, and `_metric_builder()` with **lazy DSPy imports inside the functions** (not at module top level).
3. Construct a `ModuleOptimizationSpec` with all required fields (`module_slug`, `label`, `program_spec`, `artifact_filename`, `input_keys`, `required_dataset_keys`, `module_factory`, `row_converter`, `metric_builder`).
4. Call `register_module(spec)` at module load time.
5. Add the module's import path to `module_registry._MODULE_ENTRYPOINTS` so lazy registration discovers it.
6. Add corresponding tests in `tests/unit/runtime/quality/test_optimize_<module_slug>.py` covering: spec structure, registry lookup, row converter output, metric scoring, and module factory return type.

This pattern is consumed by the offline CLI (`fleet-rlm optimize`), the API router (`POST /api/v1/optimization/run`), and the frontend metadata endpoint (`GET /api/v1/optimization/modules`).

## check_agents_md_freshness.py — Node.js Frontend Path Resolution

`scripts/check_agents_md_freshness.py` discovers AGENTS.md files by walking the repo and resolving each file's project root to find the corresponding package manager manifest (pyproject.toml, package.json, etc.). For the Node.js frontend at `src/frontend/AGENTS.md`, the script uses a subdirectory-candidate strategy:

1. Detects `package.json` in the AGENTS.md's directory or parent directories.
2. Tries subdirectory candidates `src`, `src/components`, `src/lib`, `src/features` when resolving the frontend root.
3. This logic was added in commit `8916178f` to fix a prior breakage where the freshness check failed on the frontend AGENTS.md.

If you encounter freshness-check failures for `src/frontend/AGENTS.md`, verify that the `package.json` detection branch in the script (around lines 167-171 of the version from that commit) is running correctly.

## check_docs_quality.py — Backtick Path References Not Validated

`scripts/check_docs_quality.py` validates only markdown hyperlinks (`[text](url)` syntax). It does **not** validate backtick-wrapped path references in documentation prose (e.g., `` `src/fleet_rlm/agent_host/` ``).

Consequence: stale path references in prose — like deleted directory paths wrapped in backticks — will not be caught by this validator or by `check_agents_md_freshness.py` (which only checks AGENTS.md files, not docs/*.md).
