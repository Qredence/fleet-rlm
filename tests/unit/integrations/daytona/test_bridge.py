"""Phase 3 broker harden tests.

Spins up the embedded broker server in-process on an ephemeral port using
``http.server.ThreadingHTTPServer`` and exercises the secret-gated endpoints,
orphan sweeper, lease re-claim, and concurrent tool-call round-trips.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from typing import Any
from unittest.mock import MagicMock

import pytest

from fleet_rlm.integrations.daytona.bridge import (
    _BROKER_SERVER_CODE,
    DaytonaToolBridge,
)
from fleet_rlm.integrations.daytona.errors import DaytonaDiagnosticError

_SECRET = "test-secret"


def _start_broker(secret: str = _SECRET, timeout: float = 10.0):
    """Exec the broker server code and start it on an ephemeral port.

    Returns ``(server, thread, port, namespace)``.
    """
    code = _BROKER_SERVER_CODE
    code = code.replace("__BROKER_SECRET__", repr(secret))
    code = code.replace("__DAYTONA_TOOL_CALL_TIMEOUT_S__", repr(timeout))

    namespace: dict[str, Any] = {"__name__": "broker_server_test"}
    exec(code, namespace)  # noqa: S102 - intentional exec for test harness

    handler_cls = namespace["_BrokerHandler"]
    server_cls = namespace["_ThreadedHTTPServer"]
    # Increase listen backlog so 32 concurrent connections are not rejected.
    server_cls.request_queue_size = 128
    server = server_cls(("127.0.0.1", 0), handler_cls)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, port, namespace


def _stop_broker(server: Any, thread: threading.Thread) -> None:
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def _post_json(
    url: str,
    data: dict,
    headers: dict[str, str] | None = None,
    timeout: float = 15.0,
) -> tuple[int, dict]:
    payload = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers=headers or {"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        try:
            return exc.code, json.loads(body)
        except (ValueError, json.JSONDecodeError):
            return exc.code, {"raw": body}


def _get_json(
    url: str,
    headers: dict[str, str] | None = None,
    timeout: float = 10.0,
) -> tuple[int, dict]:
    req = urllib.request.Request(url, headers=headers or {}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        try:
            return exc.code, json.loads(body)
        except (ValueError, json.JSONDecodeError):
            return exc.code, {"raw": body}


def _secret_headers() -> dict[str, str]:
    return {"Content-Type": "application/json", "X-Broker-Secret": _SECRET}


# ---------------------------------------------------------------------------
# Test 1: /tool_call returns 401 without X-Broker-Secret
# ---------------------------------------------------------------------------


def test_tool_call_401_without_secret() -> None:
    server, thread, port, _ns = _start_broker()
    try:
        status, body = _post_json(
            f"http://127.0.0.1:{port}/tool_call",
            {"tool_name": "foo", "args": [], "kwargs": {}},
        )
        assert status == 401
        assert body["error"] == "unauthorized"
    finally:
        _stop_broker(server, thread)


# ---------------------------------------------------------------------------
# Test 2: /tool_call returns 200 with valid secret (full round-trip)
# ---------------------------------------------------------------------------


def test_tool_call_200_with_valid_secret() -> None:
    server, thread, port, _ns = _start_broker()
    try:
        result_holder: dict[str, Any] = {}

        def _call() -> None:
            result_holder["status"], result_holder["body"] = _post_json(
                f"http://127.0.0.1:{port}/tool_call",
                {"tool_name": "foo", "args": [1, 2], "kwargs": {"k": "v"}},
                headers=_secret_headers(),
            )

        caller = threading.Thread(target=_call)
        caller.start()

        # Wait for the request to register, then claim it.
        time.sleep(0.2)
        status, body = _get_json(f"http://127.0.0.1:{port}/pending?max=1&lease_seconds=60")
        assert status == 200
        assert len(body["requests"]) == 1
        req = body["requests"][0]
        call_id = req["id"]
        claim_token = req["claim_token"]

        # Post the result.
        status, body = _post_json(
            f"http://127.0.0.1:{port}/result/{call_id}",
            {"result": "ok", "claim_token": claim_token},
            headers=_secret_headers(),
        )
        assert status == 200

        caller.join(timeout=5)
        assert result_holder["status"] == 200
        assert result_holder["body"]["result"] == "ok"
    finally:
        _stop_broker(server, thread)


# ---------------------------------------------------------------------------
# Test 3: /result/<id> returns 401 without secret
# ---------------------------------------------------------------------------


def test_result_401_without_secret() -> None:
    server, thread, port, _ns = _start_broker()
    try:
        status, body = _post_json(
            f"http://127.0.0.1:{port}/result/some-id",
            {"result": "ok", "claim_token": "tok"},
        )
        assert status == 401
        assert body["error"] == "unauthorized"
    finally:
        _stop_broker(server, thread)


# ---------------------------------------------------------------------------
# Test 4: /result/<id> returns 409 on stale claim token (claim already released)
# ---------------------------------------------------------------------------


def test_result_409_on_stale_claim() -> None:
    server, thread, port, ns = _start_broker()
    try:
        call_id = "test-call-409"
        ns["_pending_requests"][call_id] = {
            "tool_name": "foo",
            "args": [],
            "kwargs": {},
            "claimed": True,
            "claimed_at": time.time(),
            "lease_token": "valid-token",
        }

        # First post with correct token → 200 (releases the claim).
        status, _body = _post_json(
            f"http://127.0.0.1:{port}/result/{call_id}",
            {"result": "ok", "claim_token": "valid-token"},
            headers=_secret_headers(),
        )
        assert status == 200

        # Second post with same token → 409 (claim already released).
        status, body = _post_json(
            f"http://127.0.0.1:{port}/result/{call_id}",
            {"result": "ok", "claim_token": "valid-token"},
            headers=_secret_headers(),
        )
        assert status == 409
        assert "Stale" in body["error"]
    finally:
        _stop_broker(server, thread)


# ---------------------------------------------------------------------------
# Test 5: Orphan result sweeper evicts entries older than _RESULT_TTL_S
# ---------------------------------------------------------------------------


def test_orphan_sweeper_evicts_old_entries() -> None:
    server, thread, port, ns = _start_broker()
    try:
        ns["_RESULT_TTL_S"] = 0.1

        call_id = "orphan-result"
        ns["_results"][call_id] = ("old-result", time.time() - 1.0)
        ns["_pending_requests"][call_id] = {
            "tool_name": "foo",
            "args": [],
            "kwargs": {},
            "claimed": False,
            "claimed_at": None,
            "lease_token": None,
        }

        # Trigger the sweeper via /pending (calls _sweep_stale_results).
        _get_json(f"http://127.0.0.1:{port}/pending?max=1")

        assert call_id not in ns["_results"]
    finally:
        _stop_broker(server, thread)


# ---------------------------------------------------------------------------
# Test 6: /pending long-poll blocks for the lease window then re-claims
# ---------------------------------------------------------------------------


def test_pending_reclaims_after_lease_expiry() -> None:
    server, thread, port, ns = _start_broker()
    try:
        call_id = "lease-test"
        ns["_pending_requests"][call_id] = {
            "tool_name": "foo",
            "args": [],
            "kwargs": {},
            "claimed": False,
            "claimed_at": None,
            "lease_token": None,
        }

        # Claim with a short lease (clamped to 1.0s minimum by broker).
        status, body = _get_json(
            f"http://127.0.0.1:{port}/pending?max=1&lease_seconds=1.0",
        )
        assert status == 200
        assert len(body["requests"]) == 1
        first_token = body["requests"][0]["claim_token"]

        # Immediately poll again — lease still active, no re-claim.
        status, body = _get_json(
            f"http://127.0.0.1:{port}/pending?max=1&lease_seconds=1.0",
        )
        assert status == 200
        assert len(body["requests"]) == 0

        # Wait for lease to expire (must exceed 1.0s clamped minimum).
        time.sleep(1.2)

        # Poll again — should re-claim with a new token.
        status, body = _get_json(
            f"http://127.0.0.1:{port}/pending?max=1&lease_seconds=1.0",
        )
        assert status == 200
        assert len(body["requests"]) == 1
        second_token = body["requests"][0]["claim_token"]
        assert second_token != first_token
    finally:
        _stop_broker(server, thread)


# ---------------------------------------------------------------------------
# Test 7: 32 concurrent /tool_call round-trips all complete successfully
# ---------------------------------------------------------------------------


def test_32_concurrent_tool_call_round_trips() -> None:
    server, thread, port, _ns = _start_broker(timeout=30.0)
    try:
        results: dict[int, tuple[int, dict]] = {}
        results_lock = threading.Lock()

        def _call(idx: int) -> None:
            status, body = _post_json(
                f"http://127.0.0.1:{port}/tool_call",
                {"tool_name": "foo", "args": [idx], "kwargs": {}},
                headers=_secret_headers(),
                timeout=30.0,
            )
            with results_lock:
                results[idx] = (status, body)

        threads = [threading.Thread(target=_call, args=(i,)) for i in range(32)]
        for t in threads:
            t.start()

        # Collect and satisfy all pending requests.
        deadline = time.time() + 20
        satisfied = 0
        while satisfied < 32 and time.time() < deadline:
            _status, body = _get_json(
                f"http://127.0.0.1:{port}/pending?max=32&lease_seconds=60",
            )
            requests = body.get("requests", [])
            for req in requests:
                call_id = req["id"]
                claim_token = req["claim_token"]
                idx = req["args"][0]
                s, _b = _post_json(
                    f"http://127.0.0.1:{port}/result/{call_id}",
                    {"result": f"result-{idx}", "claim_token": claim_token},
                    headers=_secret_headers(),
                )
                if s == 200:
                    satisfied += 1
            if not requests:
                time.sleep(0.01)

        for t in threads:
            t.join(timeout=15)

        assert len(results) == 32
        for idx in range(32):
            status, body = results[idx]
            assert status == 200, f"call {idx} got status {status}: {body}"
            assert body["result"] == f"result-{idx}"
    finally:
        _stop_broker(server, thread)


# ---------------------------------------------------------------------------
# Test 8: sha256 mismatch raises DaytonaDiagnosticError(category=broker_asset_integrity)
# ---------------------------------------------------------------------------


def test_sha256_mismatch_raises_diagnostic_error() -> None:
    sandbox = MagicMock()

    # upload_file is a no-op mock (data is not actually uploaded).
    # process.exec returns a fake hash that will not match the expected sha.
    verify_resp = MagicMock()
    verify_resp.result = "0" * 64
    sandbox.process.exec.return_value = verify_resp

    bridge = DaytonaToolBridge(sandbox=sandbox, context=MagicMock())
    with pytest.raises(DaytonaDiagnosticError) as exc_info:
        bridge.ensure_started()

    assert exc_info.value.category == "broker_asset_integrity"
    assert exc_info.value.phase == "broker_start"
