# Branch simplification and test pruning receipt

Measured on `chore/live-evidence-and-wire-hygiene` against the pre-change working
branch, 2026-09-29. This receipt covers local validation only.

## Implementation

- Capture retention runs on the existing writer after completion and during idle
  operation. Active files are protected; their slots count toward retention.
- Any rendering, encoding, or filesystem write failure disables that capture,
  preventing a later footer from claiming completeness.
- Captures reuse the bounded, key-aware recursive sanitizer with capture text and
  path policy. Six new cases cover sequential retention, active-file protection,
  encoding/rendering failures, nested sensitive keys, and collection/depth bounds.
  The existing filesystem failure test remains.
- `RuntimeInventory` owns the store. `TurnRuntime` accepts only an optional
  `EventCaptureSource`. No wire contract changed.
- The trace guide requires successful span outcomes and durable Run state to
  establish commitment.

## Counts and coverage

| Measurement | Before | After | Change |
| --- | ---: | ---: | ---: |
| Non-live Python collected cases | 3,010 | 2,408 | -602 (20%) |
| TUI collected cases | 555 | 444 | -111 (20%) |
| Combined Python statement/branch coverage | 84.0796% | 82.8905% | -1.1891 pp |
| Python statement coverage | 87.0058% | 85.9951% | -1.0107 pp |
| Python branch coverage | 74.2198% | 72.4206% | -1.7992 pp |
| Python contextual coverage run duration | 65.531 s | 59.255 s | -6.276 s |
| TUI observed JSON-report run duration | 1.534 s | 1.605 s | +0.071 s |

Python removed 608 original cases and added six regressions. The coverage runs
used the same sources, exclusions, repository threshold, two-worker loadfile
execution, and canonical selection:

```sh
uv run --no-sync pytest -q -n auto --maxprocesses=2 --dist=loadfile \
  tests/unit/backend tests/unit/scripts tests/unit/optimization \
  tests/contracts/backend tests/freeze tests/unit/test_litellm_invariant.py \
  tests/e2e -m 'not live_llm and not live_daytona and not benchmark and not db and not packaging' \
  --cov --cov-context=test --cov-config=pyproject.toml
```

All three coverage decreases are below two percentage points. Sources changed
only with the capture fixes; coverage configuration and marker selection stayed
unchanged. Durations are observed local runs, not a performance benchmark.

## Deletion rationale

Temporary per-test coverage contexts, execution arcs, collected parameter data,
and duration reports supported source inspection. No ranking infrastructure was
added to the repository. Coverage overlap alone was not sufficient evidence of
redundancy; explicit outcomes and assertions also guided selection.

- 515 cases across 437 test functions removed repetitive construction/default
  checks, dependency-owned behavior, thin forwarding assertions, and overlapping
  heavily mocked setup checks. Group totals: RLM 121, Workspace 81, scripts 79,
  observability 51, Daytona 45, configuration 45, Sessions 34, Skills 28, CLI 23,
  optimization 8.
- 93 equivalent parameter cases removed repeated runtime paths with the same
  explicit expected outcome. Representative rows and test bodies remain. Privacy
  corpora, cancellation and terminal-status matrices, provider deletion-state
  taxonomy, and generated-contract tables were excluded from this selection.
- TUI removed 104 generated corpus cases by retaining 13 of 26 seeds. The
  retained corpus preserves all 41 observed feature classes, including tool
  outcomes, terminal reasons, part types, usage, cancellation, and fragments.
  Seven additional cases duplicated dependency defaults, palette snapshots,
  factory existence, and plain-object identity checks.

An initial Python cut exceeded the branch tolerance. Fifteen lifecycle,
publication, storage, settings, and telemetry cases were restored; equivalent
parameter rows replaced their deletions. Independent concurrency, cancellation,
claim/settlement, persistence, authorization, privacy, SSE ordering, and
hand-authored reducer regressions remain. No counts were lowered using skips,
deselection, marker changes, weakened assertions, or merged unrelated scenarios.
Live, database, benchmark, and packaging lanes were not selected for pruning.

## Validation

The final contextual Python run passed 2,408 tests with zero skips, errors, or
failures. The TUI run passed all 444 cases. Focused capture/lifecycle tests passed
before pruning. `make check` passed, including Python lint/format/type checks,
the canonical coverage suite, TUI test/lint/format/type lanes, generated
contracts, documentation, source-tree and dependency boundaries, and repository
hygiene. Its repeated combined coverage result was 82.87%. `git diff --check`
passed.

The pre-existing `.gitignore` edits and untracked reasoning benchmark Skill were
preserved. No commit, push, provider run, or infrastructure operation was made.

## Follow-up: test ownership and broader reduction target

Tests were aligned with the current `src/fleet_rlm` owners without changing
collection: capability preparation, coordinator, stream, runtime, and memory
promotion tests now live under `tests/unit/backend/turn/`; owned-effect tests
under `rlm/`; Daytona lifecycle tests under `daytona/`; and SQL sandbox-binding
repository tests under `sessions/`. The empty `chat/` and `runtime/` package
markers were removed. The moved Turn, RLM, Daytona, and Session suites passed.

| Measurement | Follow-up result |
| --- | ---: |
| Canonical Python cases after rehome | 2,408 |
| Requested 35% target | 1,957 |
| Additional cases removed in this follow-up | 0 |
| Remaining reduction needed | 451 |
| Statement coverage | 85.9717% (-1.0341 pp from baseline) |
| Branch coverage | 72.3943% (-1.8255 pp from baseline) |
| Combined coverage | 82.8665% (-1.2131 pp from baseline) |
| `make check` wall time | about 65 s |

The 35% count target was not met. Per-test coverage overlap and duration data
identified candidates for review, but source and assertion inspection did not
justify 451 further removals while retaining distinct contracts, authorization,
lifecycle, privacy, and settlement checks. The owner reorganization is complete;
the 1,957-case target remains unresolved under the stated deletion criteria.

`make check`, `make check-codebase-tree`, focused moved suites, and
`git diff --check` passed. The full gate collected 2,408 Python cases and passed
all 444 TUI tests. No live, database, benchmark, packaging, or TUI lane was
changed in this follow-up.
