"""Unit tests for ModalInterpreter context manager support.

These tests mock Modal to avoid requiring credentials. They verify
the ``__enter__`` / ``__exit__`` protocol is wired correctly.

Run with: uv run pytest tests/test_context_manager.py -v
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


class _FakeSandbox:
    """Mock Modal Sandbox."""

    def __init__(self, **kwargs):
        self._terminated = False

    def exec(self, *args, **kwargs):
        proc = MagicMock()
        proc.stdin = MagicMock()
        # Return an iterator that immediately signals EOF
        proc.stdout = iter([])
        proc.stderr = iter([])
        return proc

    def terminate(self):
        self._terminated = True


class _FakeApp:
    """Mock Modal App."""

    pass


class _FakeSecret:
    """Mock Modal Secret."""

    @classmethod
    def from_name(cls, name):
        return cls()


class _FakeImage:
    """Mock Modal Image."""

    @staticmethod
    def debian_slim(**kwargs):
        return _FakeImage()

    def pip_install(self, *args):
        return self


@pytest.fixture
def mock_modal(monkeypatch):
    """Patch Modal classes for unit testing."""
    import modal

    monkeypatch.setattr(modal, "App", MagicMock(return_value=_FakeApp()))
    monkeypatch.setattr(modal.App, "lookup", MagicMock(return_value=_FakeApp()))
    monkeypatch.setattr(
        modal, "Sandbox", MagicMock(create=MagicMock(return_value=_FakeSandbox()))
    )
    monkeypatch.setattr(modal.Sandbox, "create", MagicMock(return_value=_FakeSandbox()))
    monkeypatch.setattr(modal, "Secret", _FakeSecret)
    monkeypatch.setattr(modal, "Image", _FakeImage)


class TestContextManager:
    """Test ModalInterpreter as a context manager."""

    def test_enter_calls_start(self, mock_modal):
        """__enter__ should call start() and return self."""
        from fleet_rlm.interpreter import ModalInterpreter

        interp = ModalInterpreter(timeout=10)

        with (
            patch.object(interp, "start") as mock_start,
            patch.object(interp, "shutdown"),
        ):
            result = interp.__enter__()

        assert result is interp
        mock_start.assert_called_once()

    def test_exit_calls_shutdown(self, mock_modal):
        """__exit__ should call shutdown()."""
        from fleet_rlm.interpreter import ModalInterpreter

        interp = ModalInterpreter(timeout=10)

        with patch.object(interp, "shutdown") as mock_shutdown:
            interp.__exit__(None, None, None)

        mock_shutdown.assert_called_once()

    def test_exit_returns_false(self, mock_modal):
        """__exit__ should return False (don't suppress exceptions)."""
        from fleet_rlm.interpreter import ModalInterpreter

        interp = ModalInterpreter(timeout=10)

        with patch.object(interp, "shutdown"):
            result = interp.__exit__(ValueError, ValueError("test"), None)

        assert result is False

    def test_exit_called_on_exception(self, mock_modal):
        """__exit__ should be called even when exception occurs."""
        from fleet_rlm.interpreter import ModalInterpreter

        interp = ModalInterpreter(timeout=10)

        with (
            patch.object(interp, "start"),
            patch.object(interp, "shutdown") as mock_shutdown,
        ):
            with pytest.raises(ValueError, match="test error"):
                with interp:
                    raise ValueError("test error")

            mock_shutdown.assert_called_once()

    def test_context_manager_full_lifecycle(self, mock_modal):
        """Test the full with-block lifecycle."""
        from fleet_rlm.interpreter import ModalInterpreter

        interp = ModalInterpreter(timeout=10)

        start_called = False
        shutdown_called = False

        def mock_start():
            nonlocal start_called
            start_called = True

        def mock_shutdown():
            nonlocal shutdown_called
            shutdown_called = True

        with (
            patch.object(interp, "start", mock_start),
            patch.object(interp, "shutdown", mock_shutdown),
        ):
            with interp as ctx:
                assert ctx is interp
                assert start_called
                assert not shutdown_called

        assert shutdown_called
