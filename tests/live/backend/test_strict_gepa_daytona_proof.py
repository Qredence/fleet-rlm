"""Opt-in production-boundary proof for the no-gateway Daytona evaluator."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from fleet_rlm.config.loader import load_runtime_settings
from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, sandbox_backend
from fleet_rlm.daytona.platform import LiveDaytonaPlatform
from fleet_rlm.daytona.provisioning import DaytonaSandboxSpec
from fleet_rlm.daytona.runtime import build_daytona_client
from fleet_rlm.optimization.curated_input import CuratedEvaluationStore
from fleet_rlm.optimization.daytona import DisposableOptimizationSandboxFactory, OptimizationSandboxPolicy
from fleet_rlm.optimization.evidence import (
    EvidenceStore,
    StrictDaytonaProofReceipt,
    write_strict_daytona_proof,
)
from fleet_rlm.optimization.types import OptimizationRecord

_LIVE_VALUES = frozenset({"1", "true", "yes"})


def _sha(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode()).hexdigest()


def _record() -> OptimizationRecord:
    return OptimizationRecord(
        record_id="strict-proof-record",
        query="Return the strict proof marker.",
        output_contract={"answer": "string"},
        expectations={"marker": "strict"},
        execution_requirements={},
        provenance={"redaction_version": "strict-proof-v2"},
        content_sha256=_sha("strict-proof-record"),
    )


def _value(sandbox: object, name: str) -> object:
    if isinstance(sandbox, dict):
        return sandbox.get(name)
    return getattr(sandbox, name, None)


async def _deleted(platform: LiveDaytonaPlatform, sandbox_id: str) -> None:
    for _ in range(30):
        if await platform.get(sandbox_id) is None:
            return
        await asyncio.sleep(0.5)
    raise AssertionError("Daytona did not confirm strict evaluator sandbox deletion")


async def _execute(interpreter: DaytonaCodeInterpreter, code: str, variables: dict[str, object]) -> object:
    return await asyncio.to_thread(interpreter.execute, code, variables)


@pytest.mark.live_daytona
@pytest.mark.asyncio
@pytest.mark.timeout(240)
async def test_strict_gepa_daytona_block_all_proof(tmp_path: Path) -> None:
    """Prove host-polled broker mediation without a public gateway or tunnel."""
    if os.environ.get("FLEET_LIVE", "").strip().lower() not in _LIVE_VALUES:
        pytest.skip("FLEET_LIVE=1 is required")
    if os.environ.get("RUN_LIVE_DAYTONA_STRICT_PROOF", "").strip().lower() not in _LIVE_VALUES:
        pytest.skip("RUN_LIVE_DAYTONA_STRICT_PROOF=1 is required")

    settings = load_runtime_settings()
    spec = DaytonaSandboxSpec.from_settings(settings)
    policy = OptimizationSandboxPolicy(
        snapshot=spec.snapshot,
        gateway_domains=(),
        network_block_all=True,
        auto_stop_interval_seconds=300,
        auto_delete_interval_seconds=0,
    )
    platform = LiveDaytonaPlatform(build_daytona_client(settings), spec)
    factory = DisposableOptimizationSandboxFactory(platform=platform, sandbox_spec=spec)
    curated = CuratedEvaluationStore(candidate="strict-proof-candidate", record=_record())
    handle = curated.handle.public_value()
    interpreter: DaytonaCodeInterpreter | None = None
    sandbox: object | None = None
    sandbox_id = ""
    outcomes = {
        "broker_started": False,
        "broker_round_trip": False,
        "transport_authentication": False,
        "valid_capability_read": False,
        "invalid_transaction_denied": False,
        "invalid_digest_denied": False,
        "direct_egress_denied": False,
        "essential_service_egress_denied": False,
        "raw_socket_egress_denied": False,
        "dns_egress_denied": False,
        "denied_egress_unobserved": False,
        "effective_policy_verified": False,
        "host_credentials_absent": False,
        "interpreter_cleanup": False,
        "broker_cleanup": False,
        "sandbox_deleted": False,
    }
    primary_error: BaseException | None = None
    try:
        sandbox = await factory.create(
            policy=policy,
            run_id="strict-gepa-proof",
            candidate_sha256=_sha("strict-proof-candidate"),
            record_id=_record().record_id,
        )
        sandbox_id = str(_value(sandbox, "id") or "")
        assert sandbox_id
        effective = await platform.get(sandbox_id)
        assert effective is not None
        assert _value(effective, "volumes") in (None, [])
        assert _value(effective, "network_block_all") is True
        assert _value(effective, "network_allow_list") in (None, "")
        assert _value(effective, "domain_allow_list") in (None, "")
        outcomes["effective_policy_verified"] = True

        reader = curated.broker_tool(handle=curated.handle)
        interpreter = DaytonaCodeInterpreter(
            backend=sandbox_backend(sandbox, loop=asyncio.get_running_loop(), timeout_s=20),
            tools={"read_curated_input": reader},
            output_fields=[{"name": "answer", "type": "str"}],
        )
        valid = await _execute(
            interpreter,
            "value = read_curated_input(\n"
            "    transaction_id=curated_input_handle['transaction_id'],\n"
            "    sha256=curated_input_handle['sha256'],\n"
            "    json_pointer='/record/record_id',\n"
            ")\n"
            "SUBMIT(answer='valid' if value['complete'] else 'partial')",
            {"curated_input_handle": handle},
        )
        assert "valid" in str(valid)
        outcomes["broker_started"] = True
        outcomes["broker_round_trip"] = True
        outcomes["transport_authentication"] = bool(interpreter.broker)
        outcomes["valid_capability_read"] = True

        for name in ("DAYTONA_API_KEY", "FLEET_DAYTONA_API_KEY", "DATABRICKS_TOKEN", "OPENAI_API_KEY"):
            credential_result = await _execute(
                interpreter,
                "import os\nSUBMIT(answer='present' if name in os.environ else 'absent')",
                {"name": name},
            )
            assert "present" not in str(credential_result)
        outcomes["host_credentials_absent"] = True

        invalid_transaction = dict(handle)
        invalid_transaction["transaction_id"] = "invalid"
        denied = await _execute(
            interpreter,
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
        assert "denied" in str(denied)
        outcomes["invalid_transaction_denied"] = True

        invalid_digest = dict(handle)
        invalid_digest["sha256"] = "0" * 64
        denied_digest = await _execute(
            interpreter,
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
        assert "denied" in str(denied_digest)
        outcomes["invalid_digest_denied"] = True

        direct = await _execute(
            interpreter,
            "import urllib.request\n"
            "try:\n"
            "    urllib.request.urlopen('https://example.com', timeout=3)\n"
            "except Exception:\n"
            "    SUBMIT(answer='blocked')\n"
            "else:\n"
            "    SUBMIT(answer='allowed')",
            {},
        )
        assert "blocked" in str(direct)
        outcomes["direct_egress_denied"] = True

        essential = await _execute(
            interpreter,
            "import urllib.request\n"
            "try:\n"
            "    urllib.request.urlopen('https://api.daytona.io', timeout=3)\n"
            "except Exception:\n"
            "    SUBMIT(answer='blocked')\n"
            "else:\n"
            "    SUBMIT(answer='allowed')",
            {},
        )
        assert "blocked" in str(essential)
        outcomes["essential_service_egress_denied"] = True

        raw_socket = await _execute(
            interpreter,
            "import socket\n"
            "try:\n"
            "    socket.create_connection(('1.1.1.1', 443), timeout=3)\n"
            "except Exception:\n"
            "    SUBMIT(answer='blocked')\n"
            "else:\n"
            "    SUBMIT(answer='allowed')",
            {},
        )
        assert "blocked" in str(raw_socket)
        outcomes["raw_socket_egress_denied"] = True

        dns = await _execute(
            interpreter,
            "import socket\n"
            "try:\n"
            "    socket.getaddrinfo('example.com', 443)\n"
            "except Exception:\n"
            "    SUBMIT(answer='blocked')\n"
            "else:\n"
            "    SUBMIT(answer='allowed')",
            {},
        )
        assert "blocked" in str(dns)
        outcomes["dns_egress_denied"] = True
        outcomes["denied_egress_unobserved"] = all(
            outcomes[key]
            for key in (
                "direct_egress_denied",
                "essential_service_egress_denied",
                "raw_socket_egress_denied",
                "dns_egress_denied",
            )
        )
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        cleanup_error: BaseException | None = None
        if interpreter is not None:
            try:
                await asyncio.to_thread(interpreter.shutdown, strict_broker_cleanup=True)
                outcomes["interpreter_cleanup"] = True
                outcomes["broker_cleanup"] = True
            except Exception as exc:
                cleanup_error = exc
        try:
            curated.consume()
        except Exception as exc:
            cleanup_error = cleanup_error or exc
        if sandbox is not None:
            try:
                await asyncio.shield(factory.delete(sandbox))
                await _deleted(platform, sandbox_id)
                outcomes["sandbox_deleted"] = True
            except Exception as exc:
                cleanup_error = cleanup_error or exc
        if cleanup_error is not None:
            if primary_error is None:
                raise cleanup_error
            primary_error.add_note(f"cleanup also failed: {type(cleanup_error).__name__}")

    assert all(outcomes.values()), outcomes
    evidence_root = Path(os.environ.get("FLEET_STRICT_DAYTONA_PROOF_ROOT", str(tmp_path)))
    evidence = EvidenceStore(evidence_root, "strict-gepa-proof")
    evidence.initialize({"schema": "fleet.strict-daytona-proof/v2", "production": True})
    proof = write_strict_daytona_proof(
        evidence,
        StrictDaytonaProofReceipt(
            schema="fleet.strict-daytona-proof/v2",
            policy_id=policy.policy_id,
            snapshot=policy.snapshot,
            gateway_domains=(),
            controls={
                "no_volume_requested": True,
                "ephemeral_requested": True,
                "network_block_all_requested": True,
                "auto_stop_seconds": 300,
                "auto_delete_seconds": 0,
            },
            outcomes={key: "passed" for key in outcomes},
        ),
    )
    assert proof.receipt.schema == "fleet.strict-daytona-proof/v2"
