<!--
Source: .qoder/repowiki (Qoder-generated knowledge card)
Original YAML frontmatter:
  kind: dependency_management
  name: uv + pnpm Monorepo Dependency Management with Lockfiles and Override Policy
  category: dependency_management
  scope:
      - '**'
  source_files:
      - pyproject.toml
      - uv.lock
      - tools/fleet-tui/package.json
      - tools/fleet-tui/pnpm-lock.yaml
      - .github/dependabot.yml
      - .pre-commit-config.yaml
      - Makefile
-->


This repository manages dependencies across two ecosystems — Python (backend) and Node.js (TUI client) — using a lockfile-first strategy with centralized override policies for security and compatibility.

**Python dependencies (backend)**
- Declaration: `pyproject.toml` under `[project].dependencies` and `[project.optional-dependencies]` (dev, server, benchmark). Versions are pinned exactly for critical packages (`dspy==3.3.0b1`, `fastapi[standard]==0.139.0`) and use bounded ranges (`>=x,<y`) for others.
- Resolution & locking: `uv` is the resolver; `uv.lock` is committed and used as the single source of truth. CI caches and tests key off `uv.lock` checksums.
- Override policy: `[tool.uv].override-dependencies` enforces minimum patched versions for transitive dependencies pulled in by DSPy/litellm (e.g., `litellm>=1.87.0`, `aiohttp>=3.13.4`, `idna>=3.16`, `starlette>=1.3.1`, `urllib3>=2.7.0`). Comments explain each override's CVE or advisory rationale.
- Security scanning: `make check-security` runs `pip-audit` (with targeted ignore flags for known unpatched upstreams) and `bandit` over `src/fleet_rlm`.
- Dependency auditing: `deptry` is configured via `[tool.deptry]` to flag unused/missing deps, with module-name mappings for common mismatches.
- Environment pinning: `.python-version` pins the interpreter range; `Makefile` targets (`install`, `install-dev`, `install-all`) all go through `uv sync`.

**Node.js dependencies (TUI)**
- Declaration: `tools/fleet-tui/package.json` declares runtime and dev dependencies with exact versions.
- Resolution & locking: `pnpm-lock.yaml` is committed; `pnpm-workspace.yaml` exists at the TUI root.
- Engine constraint: `package.json` requires `node >=22.19.0`.

**Automation & governance**
- Dependabot: `.github/dependabot.yml` tracks GitHub Actions and pip ecosystems on a weekly cadence; it notes that `uv.lock` is not yet fully supported so updates target `pyproject.toml` changes.
- Pre-commit hooks: `.pre-commit-config.yaml` runs ruff check/format, type checking (`ty`), and fast test lane on push.
- Makefile quality gate: `make check-deps` invokes `deptry`; `make api-sync` regenerates OpenAPI/TUI types; `make tui-check` runs pnpm format/lint/typecheck/test.
- CI integration: CircleCI and GitHub Actions cache and validate against `uv.lock`; a dedicated workflow checks DSPy pin consistency.

**Conventions observed**
- Critical runtime dependencies are pinned to exact versions; other dependencies use bounded ranges to allow patch upgrades while preventing major bumps.
- Transitive dependency vulnerabilities are mitigated centrally via `override-dependencies` rather than per-package patches.
- Lockfiles (`uv.lock`, `pnpm-lock.yaml`) are committed and treated as immutable inputs to builds/tests.
- Optional extras (`dev`, `server`, `benchmark`) separate production vs. development tooling.