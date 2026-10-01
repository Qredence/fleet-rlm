"""The Daytona broker is the only live Python namespace for a Turn."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import io
import json
from pathlib import Path
from typing import Any, ClassVar
from unittest.mock import MagicMock
from uuid import UUID

import pytest

from fleet_rlm.daytona.broker import DaytonaHttpToolBroker
from fleet_rlm.daytona.errors import DaytonaAdapterError
from fleet_rlm.daytona.interpreter import (
    DEFAULT_BROKER_PORT,
    BackendExecutionResult,
    DaytonaCodeInterpreter,
    InProcessInterpreterBackend,
    _SyncBridgeLoop,
    sandbox_backend,
)
from fleet_rlm.rlm.events import RLMOutput
from fleet_rlm.rlm.program import AttachmentContextCapsule, AttachmentContextEntry


class _LocalBroker:
    """Exercise the backend protocol with JSON transport and a persistent namespace."""

    instances: ClassVar[list[_LocalBroker]] = []

    def __init__(self, _sandbox: Any, *, port: int, **_kwargs: Any) -> None:
        assert port > 0
        self.port = port
        self.namespace: dict[str, Any] = {}
        self.calls: list[str] = []
        self.closed = False
        self.instances.append(self)

    def bind_tools(self, tools: dict[str, Any]) -> None:
        self.tools = tools
        self.namespace.update(tools)

    def bind_async_bridge(self, _bridge: Any) -> None:
        pass

    def setup_source(self, source: str) -> str:
        return source

    def execute(self, code: str, variables: dict[str, Any], *, timeout_s: int) -> dict[str, Any]:
        assert timeout_s > 0
        self.calls.append(code)
        self.namespace.update(json.loads(json.dumps(variables, ensure_ascii=False, allow_nan=False)))
        stdout = io.StringIO()
        error = None
        final = None
        try:
            with contextlib.redirect_stdout(stdout):
                exec(compile(code, "<broker-test>", "exec"), self.namespace, self.namespace)
        except BaseException as exc:
            if type(exc).__name__ == "FleetFinalOutputError":
                final = exc.value
            else:
                error = str(exc)
        return {"stdout": stdout.getvalue(), "stderr": "", "error": error, "final": final}

    def stop(self, *, strict: bool) -> None:
        assert strict
        self.closed = True


def test_broker_namespace_persists_within_turn_and_resets_for_next_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _LocalBroker.instances.clear()
    monkeypatch.setattr("fleet_rlm.daytona.interpreter.DaytonaHttpToolBroker", _LocalBroker)
    root = DaytonaCodeInterpreter(backend=sandbox_backend(MagicMock()))
    first = root.new_invocation()
    first.execute(
        "cached = session_context['workspace']['available']", {"session_context": {"workspace": {"available": True}}}
    )
    assert first.execute("print(cached)") == "True\n"
    assert len(_LocalBroker.instances) == 1

    second = root.new_invocation()
    with pytest.raises(Exception, match="cached"):
        second.execute("print(cached)")
    assert len(_LocalBroker.instances) == 2
    first.shutdown()
    second.shutdown()
    assert all(broker.closed for broker in _LocalBroker.instances)


def test_broker_receives_typed_submit_and_rejects_unserializable_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _LocalBroker.instances.clear()
    monkeypatch.setattr("fleet_rlm.daytona.interpreter.DaytonaHttpToolBroker", _LocalBroker)
    backend = sandbox_backend(MagicMock())
    backend.ensure_submit([{"name": "answer", "type": "str", "required": True}])
    result = backend.run("SUBMIT(answer=note)", {"note": "café"})
    assert isinstance(result, BackendExecutionResult)
    assert result.final == {"answer": "café"}
    assert "SUBMIT(answer=note)" in _LocalBroker.instances[0].calls[0]
    with pytest.raises(DaytonaAdapterError, match="binding 'opaque' contains unsupported type"):
        backend.run("pass", {"opaque": object()})
    backend.close()


@pytest.mark.parametrize("configured_port", [None, 9017])
def test_backend_owns_broker_configuration_tools_streaming_and_cleanup(
    monkeypatch: pytest.MonkeyPatch, configured_port: int | None
) -> None:
    _LocalBroker.instances.clear()
    monkeypatch.setattr("fleet_rlm.daytona.interpreter.DaytonaHttpToolBroker", _LocalBroker)
    options = {} if configured_port is None else {"broker_port": configured_port}
    backend = sandbox_backend(object(), **options)
    root = DaytonaCodeInterpreter(backend=backend, tools={"host_tool": lambda: "host result"})
    expected_port = DEFAULT_BROKER_PORT if configured_port is None else configured_port
    assert backend.broker_port == expected_port
    assert root.broker is None
    root.start()
    assert not _LocalBroker.instances

    observed: list[object] = []
    first = root.new_invocation(observer=observed.append)
    assert first._backend.broker_port == expected_port
    assert first.broker is None
    assert first.execute("cached = host_tool(); print(cached)") == "host result\n"
    broker = _LocalBroker.instances[0]
    assert first.broker is first._backend.broker is broker
    assert broker.port == expected_port
    assert set(broker.tools) == {"host_tool"}
    assert "".join(event.output for event in observed if isinstance(event, RLMOutput)) == "host result\n"
    assert first.execute("SUBMIT(answer=cached)").output == {"answer": "host result"}
    assert len(_LocalBroker.instances) == 1
    first.shutdown(strict_broker_cleanup=True)
    assert broker.closed
    assert first.broker is None
    assert root.broker is None
    assert not backend._closed

    second = root.new_invocation()
    assert second._backend.broker_port == expected_port
    assert second.execute("print('cached' in globals())") == "False\n"
    assert second.broker is not broker
    assert second.broker.port == expected_port
    second.shutdown()
    root.shutdown()
    assert all(instance.closed for instance in _LocalBroker.instances)


@pytest.mark.parametrize("port", [-1, 0, 65536])
def test_backend_rejects_invalid_port_before_broker_creation(monkeypatch: pytest.MonkeyPatch, port: int) -> None:
    _LocalBroker.instances.clear()
    monkeypatch.setattr("fleet_rlm.daytona.interpreter.DaytonaHttpToolBroker", _LocalBroker)
    with pytest.raises(DaytonaAdapterError, match="broker port must be between"):
        sandbox_backend(object(), broker_port=port)
    assert not _LocalBroker.instances


def test_broker_submit_size_failure_is_recoverable(monkeypatch: pytest.MonkeyPatch) -> None:
    _LocalBroker.instances.clear()
    monkeypatch.setattr("fleet_rlm.daytona.interpreter.DaytonaHttpToolBroker", _LocalBroker)
    backend = sandbox_backend(MagicMock())
    backend.ensure_submit([{"name": "answer", "type": "str", "required": True}], max_output_chars=20)

    oversized = backend.run("SUBMIT(answer='x' * 30)")
    assert oversized.final is None
    assert "SUBMIT output is too large" in str(oversized.error)
    assert backend.run("SUBMIT(answer='short')").final == {"answer": "short"}
    backend.close()


@pytest.mark.asyncio
async def test_broker_resolves_awaitable_tools_and_returns_structured_failure() -> None:
    """Broker polling keeps host-tool failures structured."""

    class _Response:
        def __init__(self, body: dict[str, object]) -> None:
            self._body = body
            self.status_code = 200

        def json(self) -> dict[str, object]:
            return self._body

    class _Client:
        def __init__(self) -> None:
            self.posts: list[dict[str, object]] = []
            self._requests = [
                {"id": "ok-1", "lease": "lease-1", "tool_name": "async_tool", "args": [], "kwargs": {}},
                {"id": "bad-1", "lease": "lease-2", "tool_name": "missing", "args": [], "kwargs": {}},
            ]

        def get(self, _path: str) -> _Response:
            return _Response({"requests": self._requests})

        def post(self, _path: str, *, content: bytes, headers: dict[str, str]) -> _Response:
            assert headers == {"Content-Type": "application/json"}
            self.posts.append(json.loads(content))
            return _Response({"status": "ok"})

    async def async_tool() -> dict[str, object]:
        return {"ok": True}

    broker = DaytonaHttpToolBroker(MagicMock(), port=8765)
    client = _Client()
    broker._client = client  # type: ignore[assignment]
    broker.bind_tools({"async_tool": async_tool})
    broker.bind_async_bridge(_SyncBridgeLoop(caller_loop=asyncio.get_running_loop()))

    await asyncio.to_thread(broker._poll_once)

    assert client.posts[0]["result"] == {"ok": True}
    failure = client.posts[1]["tool_error"]
    assert isinstance(failure, dict)
    assert failure["category"] == "KeyError"
    assert failure["call_id"] == "bad-1"


# --- prepared-attachment contract shared by both backends ----------------
_TEXT_ID = UUID("00000000-0000-4000-8000-0000000000a1")
_OTHER_ID = UUID("00000000-0000-4000-8000-0000000000a2")
_ATTACHMENT_PROBE = (
    "print(repr([(a['id'], a['filename'], a['content_type'], a['byte_size'], a['data'], a['encoding'])"
    " for a in attachments]))\nprint(repr(context))"
)


def _capsule(mount: Path, files: dict[UUID, tuple[str, bytes]]) -> AttachmentContextCapsule:
    entries = []
    for attachment_id, (name, body) in files.items():
        path = mount / name
        path.write_bytes(body)
        entries.append(
            AttachmentContextEntry(
                attachment_id, name, "text/plain", len(body), hashlib.sha256(body).hexdigest(), str(path)
            )
        )
    return AttachmentContextCapsule(tuple(entries), mount_root=str(mount))


def _attachment_backend(kind: str, monkeypatch: pytest.MonkeyPatch) -> Any:
    if kind == "in_process":
        return InProcessInterpreterBackend()
    _LocalBroker.instances.clear()
    monkeypatch.setattr("fleet_rlm.daytona.interpreter.DaytonaHttpToolBroker", _LocalBroker)
    return sandbox_backend(MagicMock())


def _load_attachments(
    backend: Any, capsule: AttachmentContextCapsule, *, raw: bytes | None = None, bound_raw: bytes | None = None
) -> BackendExecutionResult:
    manifest = capsule.to_sandbox() if raw is None else raw
    backend.bind_context_manifest(
        trusted_mount_root=capsule.mount_root,
        expected_manifest_sha256=hashlib.sha256(manifest if bound_raw is None else bound_raw).hexdigest(),
    )
    return backend.run(
        capsule.sandbox_assignment("attachments", "_raw_attachments"),
        {"_raw_attachments": manifest.decode("utf-8")},
    )


_BACKENDS = pytest.mark.parametrize("kind", ["in_process", "sandbox"])


@_BACKENDS
@pytest.mark.parametrize(
    ("files", "expected_records", "expected_context"),
    [
        pytest.param(
            {_TEXT_ID: ("note.txt", b"Fleet context")},
            [(str(_TEXT_ID), "note.txt", "text/plain", 13, "Fleet context", "utf-8")],
            "Fleet context",
            id="single-text",
        ),
        pytest.param(
            {_TEXT_ID: ("a.txt", b"first"), _OTHER_ID: ("b.txt", b"second")},
            [
                (str(_TEXT_ID), "a.txt", "text/plain", 5, "first", "utf-8"),
                (str(_OTHER_ID), "b.txt", "text/plain", 6, "second", "utf-8"),
            ],
            [],
            id="multiple",
        ),
        pytest.param(
            {_TEXT_ID: ("blob.bin", b"\xff\xfe\x00")},
            [(str(_TEXT_ID), "blob.bin", "text/plain", 3, b"\xff\xfe\x00", "bytes")],
            [],
            id="invalid-utf8",
        ),
        pytest.param(
            {_TEXT_ID: ("nul.txt", b"a\x00b")},
            [(str(_TEXT_ID), "nul.txt", "text/plain", 3, b"a\x00b", "bytes")],
            [],
            id="nul-bearing",
        ),
    ],
)
def test_prepared_attachments_materialize_identically(
    kind: str,
    files: dict[UUID, tuple[str, bytes]],
    expected_records: list[tuple[object, ...]],
    expected_context: object,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _attachment_backend(kind, monkeypatch)
    loaded = _load_attachments(backend, _capsule(tmp_path, files))

    assert loaded.error is None
    assert loaded.context_accesses == tuple(str(attachment_id) for attachment_id in files)
    probe = backend.run(_ATTACHMENT_PROBE)
    assert probe.error is None
    assert probe.stdout == f"{expected_records!r}\n{expected_context!r}\n"
    assert probe.context_accesses == ()
    leaked = backend.run(
        "print(sorted(n for n in globals() if n.startswith(('_fleet_load', '_fleet_materialize', '_CONTEXT'))"
        " or n in ('_hashlib', '_json_ctx', '_os_ctx')))"
    )
    assert leaked.stdout == "[]\n"
    backend.close()


def _forge(raw: bytes, **changes: object) -> bytes:
    manifest = json.loads(raw)
    manifest.update(changes)
    return json.dumps(manifest).encode()


@_BACKENDS
@pytest.mark.parametrize("case", ["manifest-digest", "manifest-root", "file-length", "file-digest", "symlink"])
def test_prepared_attachments_reject_integrity_violations(
    kind: str, case: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mount = tmp_path / "mount"
    mount.mkdir()
    capsule = _capsule(mount, {_TEXT_ID: ("note.txt", b"Fleet context")})
    raw = capsule.to_sandbox()
    backend = _attachment_backend(kind, monkeypatch)
    target = mount / "note.txt"
    if case == "manifest-digest":
        result = _load_attachments(backend, capsule, raw=_forge(raw, extra=1), bound_raw=raw)
    elif case == "manifest-root":
        other = tmp_path / "other"
        other.mkdir()
        forged = _forge(raw, mount_root=str(other))
        result = _load_attachments(backend, capsule, raw=forged, bound_raw=forged)
    else:
        if case == "file-length":
            target.write_bytes(b"Fleet context, longer")
        elif case == "file-digest":
            target.write_bytes(b"Fleet CONTEXT")
        else:
            outside = tmp_path / "outside.txt"
            outside.write_bytes(b"Fleet context")
            target.unlink()
            target.symlink_to(outside)
        result = _load_attachments(backend, capsule)

    assert result.error in {"context manifest is invalid", "prepared context failed integrity verification"}
    assert result.context_accesses == ()
    backend.close()


@_BACKENDS
def test_context_loader_is_unavailable_to_later_actions(
    kind: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = _attachment_backend(kind, monkeypatch)
    assert _load_attachments(backend, _capsule(tmp_path, {_TEXT_ID: ("note.txt", b"x")})).error is None

    later = backend.run("_fleet_load_context_manifest")

    assert later.error is not None and "_fleet_load_context_manifest" in later.error
    backend.close()


def test_sandbox_context_integrity_failure_is_a_verification_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _LocalBroker.instances.clear()
    monkeypatch.setattr("fleet_rlm.daytona.interpreter.DaytonaHttpToolBroker", _LocalBroker)
    capsule = _capsule(tmp_path, {_TEXT_ID: ("note.txt", b"Fleet context")})
    (tmp_path / "note.txt").write_bytes(b"Fleet CONTEXT")
    invocation = DaytonaCodeInterpreter(backend=sandbox_backend(MagicMock())).new_invocation(context_capsule=capsule)

    with pytest.raises(DaytonaAdapterError) as raised:
        invocation.execute(
            capsule.sandbox_assignment("attachments", "_raw_attachments"),
            {"_raw_attachments": capsule.to_sandbox().decode("utf-8")},
        )

    assert raised.value.cause_type == "ContextVerificationError"
    assert invocation.drain_context_accesses() == ()
    invocation.shutdown()
