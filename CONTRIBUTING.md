# Contributing to fleet-rlm

Thank you for your interest in contributing to **fleet-rlm**! This document provides guidelines and instructions for contributing to the project.

---

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Development Workflow](#development-workflow)
- [Coding Standards](#coding-standards)
- [Testing Guidelines](#testing-guidelines)
- [Documentation](#documentation)
- [Submitting Changes](#submitting-changes)
- [Reporting Issues](#reporting-issues)

---

## Code of Conduct

This project adheres to a code of conduct that all contributors are expected to follow:

- Be respectful and inclusive
- Provide constructive feedback
- Focus on what is best for the community
- Show empathy towards other community members

---

## Getting Started

### Prerequisites

- Python >= 3.10
- `uv` package manager (see [UV Docs](https://docs.astral.sh/uv/))
- Git
- A Modal account (for sandbox execution)

### Initial Setup

```bash
# Fork and clone the repository
git clone https://github.com/your-username/fleet-rlm.git
cd fleet-rlm

# Add upstream remote
git remote add upstream https://github.com/qredence/fleet-rlm.git

# Install dependencies
uv sync --extra dev

# Install pre-commit hooks
uv run pre-commit install
```

### Development Environment

```bash
# Verify installation
uv run fleet-rlm --help

# Run tests
uv run pytest

# Quality gate
uv run ruff check src tests && uv run ty check src && uv run pytest -q
```

---

## Development Workflow

### 1. Branch Strategy

- Create a branch from `main` for your work
- Use descriptive branch names: `feature/your-feature`, `fix/bug-fix`, `docs/update-docs`
- Keep branches focused and small

```bash
git checkout main
git pull upstream main
git checkout -b feature/your-feature-name
```

### 2. Making Changes

- Write clear, focused commits
- Follow commit message conventions (see [Commit Messages](#commit-messages))
- Run tests and linting before committing

```bash
# Make your changes
git add .
git commit -m "feat: add new CLI command for X"

# Run pre-commit hooks
uv run pre-commit run --all-files
```

### 3. Syncing with Upstream

```bash
git fetch upstream
git rebase upstream/main
```

---

## Coding Standards

### Python Style

We use **ruff** for both linting and formatting:

```bash
# Format code
uv run ruff format .

# Check linting
uv run ruff check .

# Auto-fix linting issues
uv run ruff check --fix .
```

### Code Quality Standards

- **Type Hints**: Use type hints for all function signatures
- **Docstrings**: Include module and function docstrings
- **PEP 8**: Follow PEP 8 guidelines (enforced by ruff)
- **Type Checking**: Use `ty` for type checking (not `mypy`)

Example:

```python
"""Module docstring describing the purpose of this module."""


def my_function(param1: str, param2: int) -> dict[str, str]:
    """
    Brief description of what the function does.

    Args:
        param1: Description of param1
        param2: Description of param2

    Returns:
        Description of return value
    """
    # ...existing code...
```

### Project-Specific Conventions

- **Package Structure**: All source code under `src/fleet_rlm/`
- **Testing**: Tests in `tests/` mirroring the source structure
- **Environment Config**: Use `.env` for local development, never commit it
- **Secrets**: Use Modal secrets for API keys, never hardcode them

---

## Testing Guidelines

### Running Tests

```bash
# Run all tests
uv run pytest

# Run specific test file
uv run pytest tests/test_cli_smoke.py

# Run with coverage
uv run pytest --cov=fleet_rlm --cov-report=html
```

### Writing Tests

- Place tests in `tests/` directory
- Use `pytest` as the test framework
- Use descriptive test names: `test_function_name_expected_behavior`
- Mock external dependencies (DSPy, Modal) using `monkeypatch`

Example:

```python
"""Tests for the config module."""

def test_env_loading_with_quotes(monkeypatch):
    """Test that environment variables with quotes are handled correctly."""
    monkeypatch.setenv("DSPY_LLM_API_KEY", '"sk-test-key"')
    # ...existing code...
```

### Test Coverage

- Aim for >80% code coverage on new features
- Write tests for bug fixes that reproduce the issue
- Include tests for edge cases and error conditions

---

## Documentation

### Types of Documentation

1. **README.md**: High-level project overview and quick start
2. **AGENTS.md**: Detailed project architecture and workflows
3. **Docstrings**: Function and module documentation in code
4. **Notebooks**: Jupyter notebooks demonstrating usage

### Documentation Updates

- Update README.md for user-facing changes
- Update AGENTS.md for architectural changes
- Add docstrings for all new functions and classes
- Update inline comments for complex logic

---

## Commit Messages

We follow conventional commit messages:

- `feat:` New feature
- `fix:` Bug fix
- `docs:` Documentation changes
- `style:` Code style changes (formatting, etc.)
- `refactor:` Code refactoring
- `test:` Adding or updating tests
- `chore:` Maintenance tasks

Examples:

```
feat: add batch processing support for API endpoints

fix: handle empty responses from Modal sandbox

docs: update README with new CLI commands

test: add tests for regex extraction tool
```

---

## Submitting Changes

### Pull Request Process

1. **Update Documentation**: Ensure README and AGENTS.md are updated
2. **Run Tests**: All tests must pass
3. **Run Linting**: Code must be formatted and pass ruff checks
4. **Create PR**: Provide a clear description of changes

```bash
# Ensure everything is up to date
git fetch upstream
git rebase upstream/main

# Push to your fork
git push origin feature/your-feature-name
```

### Pull Request Checklist

- [ ] Tests pass locally
- [ ] Code is formatted with `ruff format`
- [ ] No linting errors with `ruff check`
- [ ] Documentation updated (README, AGENTS.md, docstrings)
- [ ] Commit messages follow conventions
- [ ] PR description clearly explains the change
- [ ] Link related issues in the PR description

---

## Reporting Issues

### Bug Reports

Include the following information:

- Python version
- Project version (from `pyproject.toml`)
- Steps to reproduce the issue
- Expected behavior vs. actual behavior
- Error messages and stack traces
- Environment details (OS, etc.)

### Feature Requests

- Describe the feature and use case
- Explain why it would be useful
- Provide examples of how it would work
- Consider if it fits the project scope

---

## Development Commands Reference

```bash
# Dependency Management
uv sync                    # Install dependencies
uv sync --extra dev        # Install with dev dependencies
uv add <package>          # Add a new dependency

# Code Quality
make format               # Format code with ruff
make lint                 # Check linting
make check                # Run lint + tests
make release-check        # Run lint + tests + build + twine checks

# Testing
make test                 # Run all tests
uv run pytest             # Run tests directly
uv run pytest -v          # Verbose test output

# Pre-commit
make precommit-install    # Install git hooks
make precommit-run        # Run pre-commit manually

# CLI Testing
make cli-help             # Show CLI help
uv run fleet-rlm --help   # Direct CLI help

# Modal Setup
uv run modal setup        # Authenticate Modal
uv run modal volume create rlm-volume-dspy  # Create volume
```

For package publication workflow (TestPyPI then PyPI), see [RELEASING.md](scripts/RELEASING.md).

---

## Getting Help

- **Documentation**: Check [AGENTS.md](AGENTS.md) for detailed project docs
- **Issues**: Search existing issues on GitHub
- **Discussions**: Use GitHub Discussions for questions
- **DSPy Docs**: https://dspy-docs.vercel.app/
- **Modal Docs**: https://modal.com/docs

---

## License

By contributing to this project, you agree that your contributions will be licensed under the [MIT License](LICENSE).

---

Thank you for contributing to fleet-rlm! 🚀
