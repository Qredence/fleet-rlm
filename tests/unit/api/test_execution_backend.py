"""Unit tests for ExecutionBackend enum.

Covers all VAL-ENUM-* assertions from the validation contract.
"""

from __future__ import annotations

from enum import StrEnum

from fleet_rlm.api.runtime_services import execution_backend
from fleet_rlm.api.runtime_services.execution_backend import ExecutionBackend

# ---------------------------------------------------------------------------
# VAL-ENUM-001: Exactly two members
# ---------------------------------------------------------------------------


def test_execution_backend_has_two_members():
    """ExecutionBackend must define exactly two members."""
    members = list(ExecutionBackend)
    assert len(members) == 2, f"Expected 2 members, got {len(members)}: {members}"


# ---------------------------------------------------------------------------
# VAL-ENUM-002: Member string values match canonical names
# ---------------------------------------------------------------------------


def test_execution_backend_member_values():
    """Each member's .value must equal its name."""
    assert ExecutionBackend.legacy_agent_runtime.value == "legacy_agent_runtime"
    assert ExecutionBackend.direct_rlm.value == "direct_rlm"


def test_execution_backend_only_canonical_members():
    """Only the two expected members exist with no extras."""
    names = {m.name for m in ExecutionBackend}
    assert names == {"legacy_agent_runtime", "direct_rlm"}, f"Unexpected members: {names}"


# ---------------------------------------------------------------------------
# VAL-ENUM-003: StrEnum string equality
# ---------------------------------------------------------------------------


def test_execution_backend_str_equality():
    """StrEnum members compare equal to their own string value and unequal to
    the other member's string value."""
    # Positive equality
    assert ExecutionBackend.legacy_agent_runtime == "legacy_agent_runtime"
    assert ExecutionBackend.direct_rlm == "direct_rlm"
    # Negative equality (cross-check)
    assert ExecutionBackend.legacy_agent_runtime != "direct_rlm"
    assert ExecutionBackend.direct_rlm != "legacy_agent_runtime"


# ---------------------------------------------------------------------------
# VAL-ENUM-004: Importable from the new module path
# ---------------------------------------------------------------------------


def test_execution_backend_importable():
    """from fleet_rlm.api.runtime_services.execution_backend import ExecutionBackend
    must succeed."""
    # The import at the top of this file already validates this, but we
    # re-import explicitly to make the assertion self-documenting.
    import importlib

    mod = importlib.import_module("fleet_rlm.api.runtime_services.execution_backend")
    assert hasattr(mod, "ExecutionBackend")
    assert mod.ExecutionBackend is ExecutionBackend


# ---------------------------------------------------------------------------
# VAL-ENUM-005: Docstring documents orthogonality with ExecutionMode
# ---------------------------------------------------------------------------


def test_execution_backend_docstring_orthogonality():
    """The enum docstring must mention 'ExecutionMode' and 'orthogonal'."""
    doc = ExecutionBackend.__doc__
    assert doc is not None, "ExecutionBackend must have a docstring"
    assert "ExecutionMode" in doc, f"Docstring must mention ExecutionMode. Got:\n{doc}"
    assert "orthogonal" in doc.lower() or "orthogonal" in doc, f"Docstring must contain 'orthogonal'. Got:\n{doc}"


# ---------------------------------------------------------------------------
# VAL-ENUM-006: __all__ exports only ExecutionBackend
# ---------------------------------------------------------------------------


def test_execution_backend_all_exports():
    """The module's __all__ must contain exactly 'ExecutionBackend'."""
    assert hasattr(execution_backend, "__all__"), "Module must define __all__"
    assert execution_backend.__all__ == ["ExecutionBackend"], (
        f"Expected __all__ = ['ExecutionBackend'], got {execution_backend.__all__}"
    )


def test_execution_backend_star_import_exposes_only_backend():
    """Simulate 'from module import *' to confirm only ExecutionBackend is exposed."""
    # Collect what __all__ would expose
    all_names = execution_backend.__all__
    assert len(all_names) == 1
    assert all_names[0] == "ExecutionBackend"
    # Verify the name actually resolves to the enum
    assert getattr(execution_backend, "ExecutionBackend") is ExecutionBackend


# ---------------------------------------------------------------------------
# VAL-ENUM-007: Hashable and usable as dict keys
# ---------------------------------------------------------------------------


def test_execution_backend_hashable():
    """Both members must be hashable and usable as dict keys."""
    d = {
        ExecutionBackend.legacy_agent_runtime: "legacy",
        ExecutionBackend.direct_rlm: "direct",
    }
    assert len(d) == 2
    assert d[ExecutionBackend.legacy_agent_runtime] == "legacy"
    assert d[ExecutionBackend.direct_rlm] == "direct"

    # Also hashable via hash()
    assert isinstance(hash(ExecutionBackend.legacy_agent_runtime), int)
    assert isinstance(hash(ExecutionBackend.direct_rlm), int)

    # Usable in a set
    s = {ExecutionBackend.legacy_agent_runtime, ExecutionBackend.direct_rlm}
    assert len(s) == 2


# ---------------------------------------------------------------------------
# VAL-ENUM-008: Subclass of StrEnum
# ---------------------------------------------------------------------------


def test_execution_backend_is_strenum():
    """ExecutionBackend must be a subclass of StrEnum, and members must be
    instances of str."""
    assert issubclass(ExecutionBackend, StrEnum), "ExecutionBackend must inherit from StrEnum"
    assert isinstance(ExecutionBackend.legacy_agent_runtime, str), "Members must be instances of str"
    assert isinstance(ExecutionBackend.direct_rlm, str), "Members must be instances of str"
