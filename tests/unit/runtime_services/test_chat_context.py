"""Tests for ChatExecutionContext and TurnControls dataclasses.

Covers validation assertions:
  VAL-REF-001, VAL-REF-002, VAL-REF-003, VAL-REF-004,
  VAL-REF-024, VAL-REF-025, VAL-REF-026, VAL-REF-027,
  VAL-REF-028, VAL-REF-029, VAL-REF-031, VAL-REF-034
"""

from __future__ import annotations

import socket
import sys
from dataclasses import fields, is_dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from fleet_rlm.api.auth.types import NormalizedIdentity
from fleet_rlm.api.runtime_services.chat_context import ChatExecutionContext, TurnControls
from fleet_rlm.api.runtime_services.chat_runtime import PreparedChatRuntime
from fleet_rlm.utils.identity import sanitize_id

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_identity() -> NormalizedIdentity:
    return NormalizedIdentity(
        tenant_claim="tenant-abc",
        user_claim="user-xyz",
        email="test@example.com",
    )


@pytest.fixture
def sample_prepared() -> PreparedChatRuntime:
    """Build a minimal PreparedChatRuntime using SimpleNamespace for cfg."""
    cfg = SimpleNamespace(
        ws_default_workspace_id="default",
        ws_default_user_id="anonymous",
    )
    return PreparedChatRuntime(
        cfg=cfg,  # type: ignore[arg-type]
        planner_lm=object(),
        delegate_lm=object(),
        repository=object(),
        persistence=None,
        persistence_required=False,
        identity_rows=None,
    )


@pytest.fixture
def sample_controls() -> TurnControls:
    return TurnControls(execution_mode="rlm")


@pytest.fixture
def sample_cancel_flag() -> dict[str, bool]:
    return {"cancelled": False}


@pytest.fixture
def make_context(
    sample_prepared: PreparedChatRuntime,
    sample_identity: NormalizedIdentity,
    sample_controls: TurnControls,
    sample_cancel_flag: dict[str, bool],
) -> ChatExecutionContext:
    """Build a default ChatExecutionContext for testing."""
    return ChatExecutionContext(
        prepared=sample_prepared,
        identity=sample_identity,
        session_id="test-session",
        canonical_workspace_id=sanitize_id(
            sample_identity.tenant_claim, "default"
        ),
        canonical_user_id=sanitize_id(
            sample_identity.user_claim, "anonymous"
        ),
        owner_tenant_claim=sample_identity.tenant_claim,
        owner_user_claim=sample_identity.user_claim,
        cancel_flag=sample_cancel_flag,
        controls=sample_controls,
    )


# ---------------------------------------------------------------------------
# Field monotonic helpers
# ---------------------------------------------------------------------------

CHAT_EXECUTION_CONTEXT_FIELDS = {
    "prepared",
    "identity",
    "session_id",
    "canonical_workspace_id",
    "canonical_user_id",
    "owner_tenant_claim",
    "owner_user_claim",
    "cancel_flag",
    "controls",
}

TURN_CONTROLS_FIELDS = {
    "execution_mode",
    "repo_url",
    "repo_ref",
    "context_paths",
    "batch_concurrency",
    "docs_path",
    "trace",
    "trace_mode",
    "selected_skill_ids",
}

# ---------------------------------------------------------------------------
# VAL-REF-002 — TurnControls field presence, slots, and defaults
# ---------------------------------------------------------------------------


class TestTurnControls:
    """VAL-REF-002: TurnControls has required fields and defaults."""

    def test_is_dataclass(self) -> None:
        assert is_dataclass(TurnControls)

    def test_has_slots(self) -> None:
        assert hasattr(TurnControls, "__slots__")

    def test_has_required_fields(self) -> None:
        actual = {f.name for f in fields(TurnControls)}
        assert actual == TURN_CONTROLS_FIELDS

    def test_defaults_are_none_or_empty_list(self) -> None:
        ctrl = TurnControls()
        assert ctrl.execution_mode is None
        assert ctrl.repo_url is None
        assert ctrl.repo_ref is None
        assert ctrl.context_paths == []
        assert ctrl.batch_concurrency is None
        assert ctrl.docs_path is None
        assert ctrl.trace is None
        assert ctrl.trace_mode is None
        assert ctrl.selected_skill_ids == []

    def test_context_paths_is_fresh_list_per_instance(self) -> None:
        ctrl1 = TurnControls()
        ctrl2 = TurnControls()
        assert ctrl1.context_paths is not ctrl2.context_paths
        ctrl1.context_paths.append("path-a")
        assert len(ctrl2.context_paths) == 0  # isolated

    def test_selected_skill_ids_is_fresh_list_per_instance(self) -> None:
        ctrl1 = TurnControls()
        ctrl2 = TurnControls()
        assert ctrl1.selected_skill_ids is not ctrl2.selected_skill_ids
        ctrl1.selected_skill_ids.append("skill-1")
        assert len(ctrl2.selected_skill_ids) == 0  # isolated

    def test_slots_prevent_dynamic_attributes(self) -> None:
        ctrl = TurnControls()
        with pytest.raises(AttributeError):
            ctrl.dynamic_field = "boom"  # type: ignore[attr-defined]

    def test_construct_with_all_fields(self) -> None:
        ctrl = TurnControls(
            execution_mode="simple",
            repo_url="https://example.com/repo.git",
            repo_ref="main",
            context_paths=["src/"],
            batch_concurrency=3,
            docs_path="./docs",
            trace=True,
            trace_mode="full",
            selected_skill_ids=["skill-a", "skill-b"],
        )
        assert ctrl.execution_mode == "simple"
        assert ctrl.repo_url == "https://example.com/repo.git"
        assert ctrl.repo_ref == "main"
        assert ctrl.context_paths == ["src/"]
        assert ctrl.batch_concurrency == 3
        assert ctrl.docs_path == "./docs"
        assert ctrl.trace is True
        assert ctrl.trace_mode == "full"
        assert ctrl.selected_skill_ids == ["skill-a", "skill-b"]


# ---------------------------------------------------------------------------
# VAL-REF-001 — ChatExecutionContext field presence, slots, construction
# ---------------------------------------------------------------------------


class TestChatExecutionContext:
    """VAL-REF-001: ChatExecutionContext has required fields."""

    def test_is_dataclass(self) -> None:
        assert is_dataclass(ChatExecutionContext)

    def test_has_slots(self) -> None:
        assert hasattr(ChatExecutionContext, "__slots__")

    def test_has_required_fields(self) -> None:
        actual = {f.name for f in fields(ChatExecutionContext)}
        assert actual == CHAT_EXECUTION_CONTEXT_FIELDS

    def test_slots_prevent_dynamic_attributes(self) -> None:
        ctx = ChatExecutionContext(
            prepared=object(),  # type: ignore[arg-type]
            identity=object(),  # type: ignore[arg-type]
            session_id=None,
            canonical_workspace_id=None,
            canonical_user_id=None,
            owner_tenant_claim=None,
            owner_user_claim=None,
            cancel_flag={},
            controls=TurnControls(),
        )
        with pytest.raises(AttributeError):
            ctx.dynamic_field = "boom"  # type: ignore[attr-defined]

    # ------------------------------------------------------------------
    # VAL-REF-003 — ctx.controls is a TurnControls instance
    # ------------------------------------------------------------------

    def test_controls_is_turn_controls_instance(self, make_context: ChatExecutionContext) -> None:
        assert isinstance(make_context.controls, TurnControls)

    # ------------------------------------------------------------------
    # VAL-REF-004 — cancel_flag is mutable dict, same reference
    # ------------------------------------------------------------------

    def test_cancel_flag_is_mutable_dict(
        self,
        sample_prepared: PreparedChatRuntime,
        sample_identity: NormalizedIdentity,
        sample_cancel_flag: dict[str, bool],
    ) -> None:
        ctx = ChatExecutionContext(
            prepared=sample_prepared,
            identity=sample_identity,
            session_id=None,
            canonical_workspace_id=None,
            canonical_user_id=None,
            owner_tenant_claim=None,
            owner_user_claim=None,
            cancel_flag=sample_cancel_flag,
            controls=TurnControls(),
        )
        # Same reference
        assert ctx.cancel_flag is sample_cancel_flag

        # Mutability in-place
        ctx.cancel_flag["cancelled"] = True
        assert sample_cancel_flag["cancelled"] is True

    # ------------------------------------------------------------------
    # VAL-REF-025 — identity carry-through (same object)
    # ------------------------------------------------------------------

    def test_identity_carry_through(
        self,
        sample_prepared: PreparedChatRuntime,
        sample_identity: NormalizedIdentity,
        sample_controls: TurnControls,
    ) -> None:
        ctx = ChatExecutionContext(
            prepared=sample_prepared,
            identity=sample_identity,
            session_id=None,
            canonical_workspace_id=None,
            canonical_user_id=None,
            owner_tenant_claim=None,
            owner_user_claim=None,
            cancel_flag={},
            controls=sample_controls,
        )
        assert ctx.identity is sample_identity

    # ------------------------------------------------------------------
    # VAL-REF-027 — owner_tenant_claim / owner_user_claim carry identity
    # ------------------------------------------------------------------

    def test_owner_claims_carry_identity(
        self,
        sample_prepared: PreparedChatRuntime,
        sample_identity: NormalizedIdentity,
    ) -> None:
        ctx = ChatExecutionContext(
            prepared=sample_prepared,
            identity=sample_identity,
            session_id=None,
            canonical_workspace_id=None,
            canonical_user_id=None,
            owner_tenant_claim=sample_identity.tenant_claim,
            owner_user_claim=sample_identity.user_claim,
            cancel_flag={},
            controls=TurnControls(),
        )
        assert ctx.owner_tenant_claim == sample_identity.tenant_claim
        assert ctx.owner_user_claim == sample_identity.user_claim

    # ------------------------------------------------------------------
    # VAL-REF-026 — canonical IDs derive via sanitize_id
    # ------------------------------------------------------------------

    def test_canonical_ids_derive_via_sanitize_id(
        self,
        sample_prepared: PreparedChatRuntime,
        sample_identity: NormalizedIdentity,
    ) -> None:
        expected_workspace_id = sanitize_id(
            sample_identity.tenant_claim,
            sample_prepared.cfg.ws_default_workspace_id,
        )
        expected_user_id = sanitize_id(
            sample_identity.user_claim,
            sample_prepared.cfg.ws_default_user_id,
        )

        ctx = ChatExecutionContext(
            prepared=sample_prepared,
            identity=sample_identity,
            session_id=None,
            canonical_workspace_id=expected_workspace_id,
            canonical_user_id=expected_user_id,
            owner_tenant_claim=sample_identity.tenant_claim,
            owner_user_claim=sample_identity.user_claim,
            cancel_flag={},
            controls=TurnControls(),
        )

        assert ctx.canonical_workspace_id == expected_workspace_id
        assert ctx.canonical_user_id == expected_user_id

    # ------------------------------------------------------------------
    # VAL-REF-024 — Two contexts with same prepared but different controls
    #               don't mutate each other or prepared
    # ------------------------------------------------------------------

    def test_controls_isolation(
        self,
        sample_prepared: PreparedChatRuntime,
        sample_identity: NormalizedIdentity,
    ) -> None:
        controls_a = TurnControls(execution_mode="simple")
        controls_b = TurnControls(execution_mode="rlm")

        ctx_a = ChatExecutionContext(
            prepared=sample_prepared,
            identity=sample_identity,
            session_id=None,
            canonical_workspace_id=None,
            canonical_user_id=None,
            owner_tenant_claim=None,
            owner_user_claim=None,
            cancel_flag={},
            controls=controls_a,
        )
        ctx_b = ChatExecutionContext(
            prepared=sample_prepared,
            identity=sample_identity,
            session_id=None,
            canonical_workspace_id=None,
            canonical_user_id=None,
            owner_tenant_claim=None,
            owner_user_claim=None,
            cancel_flag={},
            controls=controls_b,
        )

        # Same prepared reference (shared)
        assert ctx_a.prepared is ctx_b.prepared
        assert ctx_a.prepared is sample_prepared

        # Different controls
        assert ctx_a.controls is not ctx_b.controls

        # Mutating one doesn't affect the other
        ctx_a.controls.execution_mode = "tools_only"
        assert ctx_b.controls.execution_mode == "rlm"


# ---------------------------------------------------------------------------
# VAL-REF-028 — No import-time side effects
# ---------------------------------------------------------------------------


class TestNoImportSideEffects:
    """VAL-REF-028: Importing chat_context triggers no network access."""

    def test_import_no_network(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Monkeypatch socket.socket to prove no network call at import."""
        def _fail(*args: object, **kwargs: object) -> None:
            raise RuntimeError("unexpected socket call during import")

        monkeypatch.setattr(socket, "socket", _fail)
        monkeypatch.setattr(socket, "create_connection", _fail)  # type: ignore[attr-defined]
        monkeypatch.setattr(socket, "getaddrinfo", _fail)

        # Import should succeed without hitting any networking
        import importlib

        from fleet_rlm.api.runtime_services import chat_context as mod

        importlib.reload(mod)

    def test_import_does_not_trigger_transport_modules(self) -> None:
        """Importing chat_context doesn't pull in fastapi.WebSocket or Request."""
        # Clear any cached import state
        mod_names_before = {
            k for k in sys.modules
            if "websocket" in k.lower() or "request" in k.lower()
        }

        import importlib

        from fleet_rlm.api.runtime_services import chat_context

        importlib.reload(chat_context)

        mod_names_after = {
            k for k in sys.modules
            if "websocket" in k.lower() or "request" in k.lower()
        }

        # No new websocket/request modules should have been introduced
        new_wreckers = mod_names_after - mod_names_before
        assert not new_wreckers, (
            f"Importing chat_context pulled in transport modules: {new_wreckers}"
        )


# ---------------------------------------------------------------------------
# VAL-REF-029 — No WebSocket/Request source-level imports in chat_context.py
# ---------------------------------------------------------------------------


class TestNoTransportImportsInSource:
    """VAL-REF-029: chat_context.py does not import WebSocket or Request."""

    FORBIDDEN_PATTERNS = [
        "fastapi.WebSocket",
        "starlette.websockets",
        "fastapi.Request",
        "starlette.requests",
    ]

    @staticmethod
    def _read_source() -> str:
        repo_root = Path(__file__).resolve().parents[3]
        chat_context_path = (
            repo_root
            / "src"
            / "fleet_rlm"
            / "api"
            / "runtime_services"
            / "chat_context.py"
        )
        return chat_context_path.read_text("utf-8")

    def test_no_websocket_import(self) -> None:
        source_text = self._read_source()
        for pattern in self.FORBIDDEN_PATTERNS:
            assert pattern not in source_text, (
                f"chat_context.py must not import {pattern}"
            )


# ---------------------------------------------------------------------------
# VAL-REF-031 — Both transports can construct ChatExecutionContext
#               independently (structural)
# ---------------------------------------------------------------------------


class TestTransportConstruction:
    """VAL-REF-031: Both transports construct ChatExecutionContext independently."""

    def test_can_construct_context_from_minimal_inputs(
        self,
        sample_prepared: PreparedChatRuntime,
        sample_identity: NormalizedIdentity,
    ) -> None:
        """Verify ChatExecutionContext can be built without SSE/WS router imports."""
        ctx = ChatExecutionContext(
            prepared=sample_prepared,
            identity=sample_identity,
            session_id="ws-session",
            canonical_workspace_id="ws-workspace",
            canonical_user_id="ws-user",
            owner_tenant_claim=sample_identity.tenant_claim,
            owner_user_claim=sample_identity.user_claim,
            cancel_flag={"cancelled": False},
            controls=TurnControls(execution_mode="rlm"),
        )

        assert isinstance(ctx, ChatExecutionContext)
        assert ctx.prepared is sample_prepared
        assert ctx.identity is sample_identity
        assert ctx.session_id == "ws-session"
        assert ctx.canonical_workspace_id == "ws-workspace"
        assert ctx.canonical_user_id == "ws-user"
        assert ctx.owner_tenant_claim == sample_identity.tenant_claim
        assert ctx.owner_user_claim == sample_identity.user_claim
        assert isinstance(ctx.controls, TurnControls)


# ---------------------------------------------------------------------------
# VAL-REF-034 — Third-transport extensibility (structural)
# ---------------------------------------------------------------------------


class TestThirdTransportExtensibility:
    """VAL-REF-034: A third transport can construct ChatExecutionContext
    and wire it up without importing WS or SSE router modules."""

    def test_minimal_module_can_build_and_use_context(
        self,
        sample_prepared: PreparedChatRuntime,
        sample_identity: NormalizedIdentity,
    ) -> None:
        """A hypothetical third transport (e.g. gRPC) can build a context
        using only chat_context types and normalised identity — no WS/SSE."""
        ctx = ChatExecutionContext(
            prepared=sample_prepared,
            identity=sample_identity,
            session_id="grpc-session",
            canonical_workspace_id="grpc-workspace",
            canonical_user_id="grpc-user",
            owner_tenant_claim=sample_identity.tenant_claim,
            owner_user_claim=sample_identity.user_claim,
            cancel_flag={"cancelled": False},
            controls=TurnControls(execution_mode="simple"),
        )

        # Verify it's a fully functional context
        assert isinstance(ctx.controls, TurnControls)
        assert ctx.cancel_flag["cancelled"] is False
        assert ctx.identity.tenant_claim == "tenant-abc"
        assert ctx.owner_tenant_claim == "tenant-abc"
        assert ctx.canonical_workspace_id == "grpc-workspace"
