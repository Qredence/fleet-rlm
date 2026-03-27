"""Embedded Daytona bridge assets extracted from the main bridge module."""

from __future__ import annotations

import inspect
from typing import Any, Callable

from dspy.primitives import CodeInterpreterError

_BROKER_PORT = 3000
_BROKER_SERVER_PATH = "/home/daytona/broker_server.py"
_BROKER_SESSION_COMMAND = (
    f"cd /home/daytona && python {_BROKER_SERVER_PATH.rsplit('/', 1)[-1]}"
)
_BROKER_SERVER_CODE = """
\"\"\"Broker server for mediating tool calls between sandbox code and the host.\"\"\"

import json
import threading
import time
import uuid

from flask import Flask, jsonify, request

app = Flask(__name__)
_lock = threading.Lock()
_pending_requests: dict[str, dict[str, object]] = {}
_results: dict[str, object] = {}


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/tool_call", methods=["POST"])
def tool_call():
    data = request.json or {}
    call_id = str(data.get("id") or uuid.uuid4())
    tool_name = str(data.get("tool_name") or "")
    args = data.get("args", [])
    kwargs = data.get("kwargs", {})

    with _lock:
        _pending_requests[call_id] = {
            "tool_name": tool_name,
            "args": args if isinstance(args, list) else [],
            "kwargs": kwargs if isinstance(kwargs, dict) else {},
            "claimed": False,
            "claimed_at": None,
            "lease_token": None,
        }

    timeout = 120.0
    interval = 0.05
    elapsed = 0.0
    while elapsed < timeout:
        with _lock:
            if call_id in _results:
                result = _results.pop(call_id)
                _pending_requests.pop(call_id, None)
                return jsonify({"result": result})
        time.sleep(interval)
        elapsed += interval

    with _lock:
        _pending_requests.pop(call_id, None)
    return jsonify({"error": "Tool call timeout"}), 504


@app.route("/pending", methods=["GET"])
def get_pending():
    try:
        max_items = int(request.args.get("max", "1"))
    except ValueError:
        max_items = 1
    max_items = max(1, max_items)

    try:
        lease_seconds = float(request.args.get("lease_seconds", "60"))
    except ValueError:
        lease_seconds = 60.0
    lease_seconds = max(1.0, lease_seconds)

    requests_out = []
    with _lock:
        now = time.time()
        for call_id, payload in _pending_requests.items():
            if len(requests_out) >= max_items:
                break
            if call_id in _results:
                continue
            claimed_at = payload.get("claimed_at")
            if payload.get("claimed") and isinstance(claimed_at, (int, float)):
                if now - claimed_at < lease_seconds:
                    continue
            claim_token = str(uuid.uuid4())
            payload["claimed"] = True
            payload["claimed_at"] = now
            payload["lease_token"] = claim_token
            requests_out.append(
                {
                    "id": call_id,
                    "tool_name": payload["tool_name"],
                    "args": payload["args"],
                    "kwargs": payload["kwargs"],
                    "claim_token": claim_token,
                }
            )
    return jsonify({"requests": requests_out})


@app.route("/result/<call_id>", methods=["POST"])
def post_result(call_id: str):
    data = request.json or {}
    result = data.get("result")
    claim_token = data.get("claim_token")
    with _lock:
        req = _pending_requests.get(call_id)
        if req is None:
            return jsonify({"error": "Unknown or expired call_id"}), 404
        expected_token = req.get("lease_token")
        if not expected_token or claim_token != expected_token:
            return jsonify({"error": "Stale or invalid claim token"}), 409
        _results[call_id] = result
        req["lease_token"] = None
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=3000, threaded=True)
""".strip()

_TOOL_WRAPPER_TEMPLATE = """
def {tool_name}({signature}):
    \"\"\"Wrapper for {tool_name} tool - calls host via broker.\"\"\"
    import json as _json
    import urllib.request as _urllib_request
    import uuid as _uuid

    call_id = str(_uuid.uuid4())
    payload = _json.dumps(
        {{
            "id": call_id,
            "tool_name": "{tool_name}",
            "args": [{args_list}],
            "kwargs": {{{kwargs_dict}}},
        }}
    ).encode("utf-8")
    req = _urllib_request.Request(
        "http://localhost:{broker_port}/tool_call",
        data=payload,
        headers={{"Content-Type": "application/json"}},
        method="POST",
    )
    with _urllib_request.urlopen(req, timeout=130) as resp:
        data = _json.loads(resp.read().decode("utf-8"))
    if "error" in data:
        raise RuntimeError(f"Tool call failed: {{data['error']}}")
    result = data.get("result")
    if isinstance(result, str):
        try:
            return _json.loads(result)
        except (ValueError, _json.JSONDecodeError):
            return result
    return result
""".strip()


def generate_tool_wrapper(
    *,
    tool_name: str,
    tool_func: Callable[..., Any],
) -> str:
    signature = inspect.signature(tool_func)
    params = list(signature.parameters.values())
    sig_parts: list[str] = []
    args_list: list[str] = []
    kwargs_parts: list[str] = []
    added_kw_only_separator = False
    last_positional_only_idx = max(
        (
            index
            for index, param in enumerate(params)
            if param.kind == inspect.Parameter.POSITIONAL_ONLY
        ),
        default=-1,
    )

    def _format_param(param: inspect.Parameter) -> str:
        if param.default is inspect.Parameter.empty:
            return param.name
        return f"{param.name}={repr(param.default)}"

    for index, param in enumerate(params):
        if param.kind == inspect.Parameter.POSITIONAL_ONLY:
            sig_parts.append(_format_param(param))
            args_list.append(param.name)
            if index == last_positional_only_idx:
                sig_parts.append("/")
            continue
        if param.kind == inspect.Parameter.POSITIONAL_OR_KEYWORD:
            sig_parts.append(_format_param(param))
            if param.default is inspect.Parameter.empty:
                args_list.append(param.name)
            else:
                kwargs_parts.append(f'"{param.name}": {param.name}')
            continue
        if param.kind == inspect.Parameter.VAR_POSITIONAL:
            sig_parts.append(f"*{param.name}")
            args_list.append(f"*{param.name}")
            added_kw_only_separator = True
            continue
        if param.kind == inspect.Parameter.KEYWORD_ONLY:
            if not added_kw_only_separator:
                sig_parts.append("*")
                added_kw_only_separator = True
            sig_parts.append(_format_param(param))
            kwargs_parts.append(f'"{param.name}": {param.name}')
            continue
        if param.kind == inspect.Parameter.VAR_KEYWORD:
            sig_parts.append(f"**{param.name}")
            kwargs_parts.append(f"**{param.name}")
            continue
        raise CodeInterpreterError(
            f"Unsupported parameter kind for tool '{tool_name}': {param.kind}"
        )

    return _TOOL_WRAPPER_TEMPLATE.format(
        tool_name=tool_name,
        signature=", ".join(sig_parts),
        args_list=", ".join(args_list),
        kwargs_dict=", ".join(kwargs_parts),
        broker_port=_BROKER_PORT,
    )


__all__ = [
    "_BROKER_PORT",
    "_BROKER_SERVER_CODE",
    "_BROKER_SERVER_PATH",
    "_BROKER_SESSION_COMMAND",
    "_TOOL_WRAPPER_TEMPLATE",
    "generate_tool_wrapper",
]
