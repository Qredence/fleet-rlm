"""K-003: Daytona adapter unit tests (offline, injectable backend)."""

from __future__ import annotations

import pytest


class _FakeBackend:
    """In-memory REPL stand-in for offline adapter tests."""

    def __init__(self) -> None:
        self.namespace: dict[str, object] = {"_out": ""}
        self.closed = False
        self.fail_with: BaseException | None = None

    def run(self, code: str, variables: dict[str, object] | None = None) -> str:
        if self.closed:
            msg = "backend already closed"
            raise RuntimeError(msg)
        if self.fail_with is not None:
            raise self.fail_with
        if variables:
            self.namespace.update(variables)
        exec(code, self.namespace, self.namespace)  # noqa: S102
        return str(self.namespace.get("_out", ""))

    def close(self) -> None:
        self.closed = True


def test_execute_returns_string_and_preserves_state() -> None:
    from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter

    backend = _FakeBackend()
    interp = DaytonaCodeInterpreter(backend=backend)
    interp.start()
    interp.execute("value = 41")
    result = interp.execute("_out = str(value)")
    assert result == "41"


def test_execute_returns_user_code_errors_for_rlm_repair() -> None:
    from fleet_rlm.daytona.in_process import InProcessInterpreterBackend
    from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter

    interp = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())

    assert interp.execute("metrics = {'precision': 0.9}\nprint(metrics['prec'])") == "[Error] 'prec'"


def test_shutdown_is_idempotent() -> None:
    from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter

    backend = _FakeBackend()
    interp = DaytonaCodeInterpreter(backend=backend)
    interp.start()
    interp.shutdown()
    interp.shutdown()
    assert backend.closed is True


def test_lease_release_is_idempotent_and_does_not_delete_sandbox() -> None:
    from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter
    from fleet_rlm.daytona.leases import InterpreterLease

    backend = _FakeBackend()
    interp = DaytonaCodeInterpreter(backend=backend)
    interp.start()
    deleted: list[str] = []

    lease = InterpreterLease(
        sandbox_id="sbx-1",
        interpreter_id="interp-1",
        volume_id="vol-1",
        mount_path="/home/daytona/memory",
        interpreter=interp,
        delete_sandbox=lambda sandbox_id: deleted.append(sandbox_id),
    )
    lease.release()
    lease.release()
    assert backend.closed is True
    assert deleted == []


def test_provider_errors_map_to_sanitized_fleet_errors() -> None:
    from daytona import DaytonaError

    from fleet_rlm.daytona.errors import DaytonaAdapterError, map_provider_error
    from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter

    backend = _FakeBackend()
    backend.fail_with = DaytonaError("boom api_key=sk-secret path=/tmp/secret")
    interp = DaytonaCodeInterpreter(backend=backend)
    interp.start()

    with pytest.raises(DaytonaAdapterError) as exc_info:
        interp.execute("print(1)")

    message = str(exc_info.value)
    assert "sk-secret" not in message
    assert "/tmp/secret" not in message
    mapped = map_provider_error(backend.fail_with)
    assert isinstance(mapped, DaytonaAdapterError)
    assert "sk-secret" not in mapped.message


def test_sanitize_provider_message_strips_secrets_and_paths() -> None:
    from fleet_rlm.daytona.errors import sanitize_provider_message

    cleaned = sanitize_provider_message("failed api_key=sk-secret path=/tmp/secret")
    assert "sk-secret" not in cleaned
    assert "/tmp/secret" not in cleaned
    assert "[redacted]" in cleaned
