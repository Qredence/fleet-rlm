<!--
Source: .qoder/repowiki (Qoder-generated knowledge card)
Original YAML frontmatter:
  kind: external_dependency
  name: Ruff Code Quality and Formatting
  slug: ruff
  category: external_dependency
  category_hints:
      - framework_behavior
  scope:
      - '**'
-->


### Ruff Code Quality Framework
- Unified linting and formatting tool replacing multiple Python linters with single configuration
- Comprehensive rule selection including pyflakes, pycodestyle, isort, flake8-bugbear, and ruff-specific rules
- Pre-commit hooks enforce both checking and auto-fixing capabilities with strict exit codes
- Targeted Python 3.11 compatibility with extensive ignore lists for existing codebase debt
- Line length policy delegated to formatter (E501) while maintaining other style checks
- Per-file ignore configurations for specific patterns like __init__.py files and test backlogs