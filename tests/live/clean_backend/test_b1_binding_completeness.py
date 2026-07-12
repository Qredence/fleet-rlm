"""Opt-in live B1 proof: host-tool + llm_query + SUBMIT via Daytona HTTP broker.

Gate: FLEET_CLEAN_LIVE=1
Requires: DAYTONA_API_KEY or FLEET_CLEAN_DAYTONA_API_KEY

Evidence: .scratch/clean-backend-refoundation/assets/live-b1-binding-completeness-evidence.json
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
from dspy.primitives.code_interpreter import FinalOutput

from fleet_rlm_clean.config import Settings
from fleet_rlm_clean.daytona.client import build_daytona_client
from fleet_rlm_clean.daytona.interpreter import DaytonaCodeInterpreter, sandbox_backend
from fleet_rlm_clean.daytona.leases import InterpreterLease
from fleet_rlm_clean.rlm.budgets import RLMBudget
from fleet_rlm_clean.rlm.factory import RLMFactory
from fleet_rlm_clean.rlm.model_bundle import RLMModelBundle

pytestmark = [pytest.mark.live_daytona]


class _StubLM:
    """Minimal callable LM for RLM-injected llm_query (Sub Model stand-in)."""

    def __init__(self, label: str) -> None:
        self.label = label
        self.prompts: list[str] = []

    def __call__(self, prompt: str = "", **_kwargs: Any) -> list[dict[str, str]]:
        text = str(prompt)
        self.prompts.append(text)
        return [{"text": f"{self.label}:{text}"}]


def _live_enabled() -> bool:
    return os.environ.get("FLEET_CLEAN_LIVE", "").strip() in {"1", "true", "yes"}


def _have_daytona() -> bool:
    return bool(os.environ.get("DAYTONA_API_KEY") or os.environ.get("FLEET_CLEAN_DAYTONA_API_KEY"))


def _git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def _write_evidence(payload: dict[str, Any]) -> Path:
    evidence_dir = Path(".scratch/clean-backend-refoundation/assets")
    evidence_dir.mkdir(parents=True, exist_ok=True)
    path = evidence_dir / "live-b1-binding-completeness-evidence.json"
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


@pytest.mark.timeout(300)
def test_live_b1_rlm_llm_query_host_tool_submit() -> None:
    if not _live_enabled():
        pytest.skip("Set FLEET_CLEAN_LIVE=1 for live B1 binding proof")
    if not _have_daytona():
        pytest.skip("DAYTONA_API_KEY / FLEET_CLEAN_DAYTONA_API_KEY not configured")

    settings = Settings()
    if settings.daytona_api_key is None and os.environ.get("DAYTONA_API_KEY"):
        settings = Settings(daytona_api_key=os.environ["DAYTONA_API_KEY"])

    host_tool_calls: list[str] = []

    def load_skill(name: str) -> str:
        host_tool_calls.append(name)
        return f"card:{name}"

    root_lm = _StubLM("root")
    sub_lm = _StubLM("sub")
    client = build_daytona_client(settings)
    sandbox = client.create()
    interpreter = DaytonaCodeInterpreter(backend=sandbox_backend(sandbox))
    lease = InterpreterLease(
        sandbox_id=str(getattr(sandbox, "id", "unknown")),
        interpreter_id="b1-live",
        volume_id="none",
        mount_path="/home/daytona/memory",
        interpreter=interpreter,
    )
    evidence_path: Path | None = None
    try:
        rlm = RLMFactory().create(
            models=RLMModelBundle(root_lm=root_lm, sub_lm=sub_lm),
            budget=RLMBudget(max_iterations=2, max_llm_calls=4, max_output_chars=4000),
            interpreter=interpreter,
            tools=[load_skill],
        )
        # RLM injects llm_query + user tools (DSPy contract). Root-LM code generation
        # needs a real BaseLM provider response; this proof runs the Sandbox HTTP
        # broker with RLM-injected tools and a deterministic program.
        execution_tools = rlm._prepare_execution_tools()
        rlm._inject_execution_context(interpreter, execution_tools)
        interpreter.start()

        result = interpreter.execute("SUBMIT(answer=llm_query('ping') + '|' + load_skill(name='demo'))")

        assert isinstance(result, FinalOutput)
        assert result.output == {"answer": "sub:ping|card:demo"}
        assert sub_lm.prompts == ["ping"]
        assert host_tool_calls == ["demo"]

        evidence_path = _write_evidence(
            {
                "claim": "B1 binding completeness",
                "tip_commit": _git_commit(),
                "transport": "http-in-sandbox-broker",
                "proof_shape": "rlm_inject_plus_sandbox_execute",
                "note": "Root-LM generated code path deferred to L1 with real LM; tools dispatched via Daytona HTTP broker.",
                "observed": {
                    "final_output": result.output,
                    "sub_lm_prompts": sub_lm.prompts,
                    "host_tool_calls": host_tool_calls,
                    "sandbox_id": getattr(sandbox, "id", None),
                },
            }
        )
        assert evidence_path.exists()
    finally:
        lease.release()
        try:
            sandbox.delete()
        except Exception:  # noqa: BLE001 - best-effort cleanup
            pass
