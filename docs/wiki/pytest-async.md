<!--
Source: .qoder/repowiki (Qoder-generated knowledge card)
Original YAML frontmatter:
  kind: external_dependency
  name: Pytest Testing Framework with Async Support
  slug: pytest
  category: external_dependency
  category_hints:
      - framework_behavior
  scope:
      - '**'
-->


### Pytest Testing Framework
- Primary testing framework with async support via pytest-asyncio for concurrent test execution
- Organized into distinct suites: unit, contracts, e2e, live, and deno tests with marker-based filtering
- Custom conftest.py provides automatic suite detection from file paths and JUnit XML post-processing for CI integration
- Parallel execution via pytest-xdist with configurable worker limits (default 2 workers)
- Coverage collection is package-wide over `src/fleet_rlm` with a 75% threshold (branch coverage; raised from the legacy daytona-only 70% gate)
- Timeout protection at 30 seconds per test with thread-based timeout method
- Strict marker enforcement prevents accidental inclusion of live/external dependency tests in default runs