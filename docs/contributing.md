# Contributing to fleet-rlm

Thank you for your interest in contributing to **fleet-rlm**! This document provides guidelines and instructions for contributing to the project.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Development Workflow](#development-workflow)
- [Coding Standards](#coding-standards)
- [Testing Guidelines](#testing-guidelines)
- [Documentation](#documentation)
- [Submitting Changes](#submitting-changes)

## Code of Conduct

This project adheres to a code of conduct that all contributors are expected to follow:

- Be respectful and inclusive.
- Provide constructive feedback.

## Getting Started

### Prerequisites

- Python >= 3.10
- `uv` package manager
- Modal account

### Setup

```bash
# Fork and clone
git clone https://github.com/your-username/fleet-rlm.git
cd fleet-rlm

# Install deps
uv sync --all-groups
uv run pre-commit install
```

## Development Workflow

1.  **Branching**: Create a branch from `main` (e.g., `feature/my-feature`).
2.  **Coding**: Make changes.
3.  **Testing**: Run `uv run pytest`.
4.  **Linting**: Run `uv run ruff check .` and `uv run ruff format .`.

## Coding Standards

- **Style**: We use `ruff` for formatting.
- **Types**: Use type hints (`from __future__ import annotations`).
- **Docs**: Include docstrings for all modules and functions.

## Testing Guidelines

Run all tests before submitting:

```bash
uv run pytest
```

Add new tests in `tests/` for any new functionality. Mock external services like Modal using `monkeypatch`.

## Documentation

- **README.md**: High-level overview.
- **docs/**: This directory contains guides, tutorials, and concepts.
- **Inline**: Update docstrings for any code changes.

## Submitting Changes

1.  Update documentation if behavior changes.
2.  Ensure CI checks pass (lint, test).
3.  Submit a Pull Request with a clear description.

For more details, see the root [CONTRIBUTING.md](../CONTRIBUTING.md) file.
