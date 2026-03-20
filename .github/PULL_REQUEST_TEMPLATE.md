# Description

Please include a summary of the change and which issue is fixed. Please also include relevant motivation and context. List any dependencies that are required for this change.

Fixes # (issue)

## Type of change

Please delete options that are not relevant.

- [ ] Bug fix (non-breaking change which fixes an issue)
- [ ] New feature (non-breaking change which adds functionality)
- [ ] Breaking change (fix or feature that would cause existing functionality to not work as expected)
- [ ] Documentation update

## Checklist

- [ ] Relevant local validation passed (`make test-fast` for backend-only work, `make quality-gate` for shared-contract work)
- [ ] Frontend changes passed repo checks (`cd src/frontend && pnpm run api:check && pnpm run type-check && pnpm run lint:robustness && pnpm run test:unit && pnpm run build`)
- [ ] Pre-commit and pre-push hooks are installed locally (`uv run pre-commit install` and `uv run pre-commit install --hook-type pre-push`)
- [ ] Documentation updated (README, AGENTS.md, docstrings)
- [ ] Commit messages follow conventions
- [ ] PR description clearly explains the change
- [ ] Link related issues in the PR description
