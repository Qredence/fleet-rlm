<!--
Source: .qoder/repowiki (Qoder-generated knowledge card)
Original YAML frontmatter:
  kind: external_dependency
  name: Ty Static Type Checker
  slug: ty
  category: external_dependency
  category_hints:
      - framework_behavior
  scope:
      - '**'
-->


### Ty Static Type Analysis
- Modern Python type checker integrated into pre-commit hooks and Makefile targets
- Configured to exclude tests, scripts, and notebooks from type checking scope
- Enforces type safety as part of the quality gate alongside linting and formatting
- Runs as a system command through pre-commit with pass_filenames=false for full repository analysis