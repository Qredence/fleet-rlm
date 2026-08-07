"""Compatibility re-exports for Sandbox bindings.

The binding domain and in-memory store are provider-neutral. SQL persistence
lives in ``fleet_rlm.persistence.repositories.sandbox_bindings`` so importing
this Daytona compatibility module never imports SQLAlchemy row models.
"""

from __future__ import annotations

from fleet_rlm.runtime.bindings import (
    InMemorySandboxBindingStore,
    SandboxBinding,
    SandboxBindingStore,
    validate_sandbox_binding,
)

BindingStore = SandboxBindingStore
InMemoryBindingStore = InMemorySandboxBindingStore

__all__ = [
    "BindingStore",
    "InMemoryBindingStore",
    "SandboxBinding",
    "SandboxBindingStore",
    "validate_sandbox_binding",
]
