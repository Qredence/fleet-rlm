# Description

Describe the concrete problem and resulting behavior. Include a before/after
example when useful, and identify any contract or migration impact.

Fixes # (issue)

## Type of change

Please delete options that are not relevant.

- [ ] Bug fix (non-breaking change which fixes an issue)
- [ ] New feature (non-breaking change which adds functionality)
- [ ] Breaking change (fix or feature that would cause existing functionality to not work as expected)
- [ ] Documentation update

## Checklist

- [ ] Applicable validation from AGENTS.md passed; exact commands/results appear below
- [ ] Documentation matches the affected implementation
- [ ] Generated contracts were regenerated when their source changed
- [ ] `git diff --check` passed and unrelated changes were excluded

## Validation

List the commands run and outcomes. For documentation-only changes, use
`make check-docs` and `git diff --check`. For broader code/contract changes,
follow AGENTS.md. Distinguish deterministic checks from live provider,
database, security, and release evidence; name any remaining validation gap.
