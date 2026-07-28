<!--
Source: .qoder/repowiki (Qoder-generated knowledge card)
Original YAML frontmatter:
  kind: external_dependency
  name: CircleCI Continuous Integration Pipeline
  slug: circleci
  category: external_dependency
  category_hints:
      - vendor_identity
  scope:
      - '**'
-->


### CircleCI Continuous Integration
- Multi-job pipeline with separate quality, lint/typecheck, unit tests, E2E tests, Daytona coverage, and Deno contract jobs
- Uses Smarter Testing integration for dynamic test splitting and parallel execution across multiple workers
- Dedicated jobs for different test categories: unit tests (4 parallel workers), E2E tests (2 parallel workers), and specialized Daytona/Deno environments
- Artifact storage for test results and coverage reports with structured output directories
- Cache optimization using uv.lock checksums for dependency caching across builds