"""Typed errors for RLM construction and budget validation."""

from __future__ import annotations


class RLMConfigError(ValueError):
    """Base class for clean-backend RLM configuration failures."""


class RLMBudgetError(RLMConfigError):
    """Raised when an RLMBudget is invalid before external execution."""


class RLMModelBundleError(RLMConfigError):
    """Raised when required model roles are missing or invalid."""
