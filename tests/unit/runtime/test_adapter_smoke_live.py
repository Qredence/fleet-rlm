"""Live smoke test: ChatAdapter primary vs JSONAdapter primary parse-failure rate.

Guards the adapter realignment in ``factory.py`` (drop JSONAdapter forcing, rely
on DSPy's native ``ChatAdapter`` → ``JSONAdapter`` fallback,
``dspy/adapters/chat_adapter.py:46,68,87-94``). Run inside a Daytona sandbox
per the ``/daytona`` skill so a real ``qwen3.x`` provider turn can be driven
from an isolated runtime without polluting the host.

Skipped automatically unless:
  - ``DAYTONA_API_KEY`` is set (sandbox provisioning), AND
  - ``FLEET_RLM_SMOKE_QWEN_KEY`` (or ``OPENAI_API_KEY``) is set (the qwen
    provider key reached via the Tier 1/2 sandbox network AI-API whitelist).

Marked ``live_llm`` so it stays out of the default suite
(``not live_llm and not live_daytona and not benchmark and not db``).
"""

from __future__ import annotations

import json
import os
import textwrap

import pytest

pytestmark = pytest.mark.live_llm

# Smoke script uploaded into the Daytona sandbox. Builds a minimal
# ``generate_action``-shaped signature, runs N turns under each adapter primary,
# counts ``dspy.AdapterParseError``s, prints a JSON verdict.
_SMOKE_SCRIPT = textwrap.dedent(
    '''
    import json
    import os
    import sys
    import dspy

    N = int(os.environ.get("FLEET_RLM_SMOKE_TURNS", "5"))
    KEY = os.environ.get("FLEET_RLM_SMOKE_QWEN_KEY") or os.environ.get("OPENAI_API_KEY", "")
    BASE = os.environ.get("FLEET_RLM_SMOKE_QWEN_BASE", "https://openai.api.levers.ai/v1")
    MODEL = os.environ.get("FLEET_RLM_SMOKE_QWEN_MODEL", "hosted-vllm/Qwen/Qwen3-32B")

    class GenerateAction(dspy.Signature):
        """Generate the next agent action."""
        reasoning: str = dspy.InputField(desc="why this action")
        code: str = dspy.OutputField(desc="python code to run")

    lm = dspy.LM(MODEL, api_base=BASE, api_key=KEY)
    predictor = dspy.Predict(GenerateAction)

    def run_under(adapter, n):
        failures = 0
        with dspy.settings.context(lm=lm, adapter=adapter):
            for i in range(n):
                try:
                    predictor(reasoning=f"step {i}: emit a no-op pass")
                except dspy.AdapterParseError:
                    failures += 1
        return failures

    chat_fail = run_under(dspy.ChatAdapter(), N)
    json_fail = run_under(dspy.JSONAdapter(), N)
    verdict = {"chat": chat_fail, "json": json_fail, "n": N,
               "drop": max(0, json_fail - chat_fail)}
    print("FLEET_RLM_SMOKE_VERDICT=" + json.dumps(verdict))
    sys.stdout.flush()
    '''
).strip()


def _have_creds() -> bool:
    if (os.environ.get("FLEET_RLM_RUN_LIVE_LLM_TESTS") or "").strip() != "1":
        return False
    if not os.environ.get("DAYTONA_API_KEY"):
        return False
    if not (os.environ.get("FLEET_RLM_SMOKE_QWEN_KEY") or os.environ.get("OPENAI_API_KEY")):
        return False
    return True


def test_chat_adapter_primary_parse_failure_rate_drops() -> None:
    """ChatAdapter primary should produce <= JSONAdapter primary parse failures.

    Asserts ``verdict["drop"] >= 0`` (ChatAdapter never worse than JSONAdapter
    primary) over N real qwen3.x turns. Executed inside a Daytona sandbox:
    build snapshot ``rlm-adapter-smoke`` once, create sandbox from it, upload
    the smoke script, run via ``sandbox.process.code_run``, ``sandbox.delete()``
    after — no quota leak. Snapshot is retained for fast repeat runs.
    """
    if not _have_creds():
        pytest.skip("DAYTONA_API_KEY + FLEET_RLM_SMOKE_QWEN_KEY/OPENAI_API_KEY not configured")

    from daytona import (
        CreateSandboxFromSnapshotParams,
        CreateSnapshotParams,
        Daytona,
        Image,
    )

    daytona = Daytona()
    snapshot_name = "rlm-adapter-smoke"
    # Build snapshot once if absent (idempotent across runs; kept for fast reuse).
    try:
        snapshot = daytona.snapshot.get(snapshot_name)
    except Exception:
        image = Image.debian_slim("3.12").pip_install(["dspy-ai", "pytest"])
        snapshot = daytona.snapshot.create(CreateSnapshotParams(name=snapshot_name, image=image))

    sandbox = daytona.create(CreateSandboxFromSnapshotParams(snapshot=snapshot.name))
    try:
        resp = sandbox.process.code_run(_SMOKE_SCRIPT)
        if resp.exit_code != 0:
            pytest.fail(f"smoke script failed (exit={resp.exit_code}): {resp.result}")
        out = resp.result or ""
        verdict_line = next(
            (ln for ln in out.splitlines() if ln.startswith("FLEET_RLM_SMOKE_VERDICT=")),
            None,
        )
        if verdict_line is None:
            pytest.fail(f"no FLEET_RLM_SMOKE_VERDICT= line in output: {out!r}")
        verdict = json.loads(verdict_line.split("=", 1)[1])
        assert verdict["drop"] >= 0, f"ChatAdapter primary worse than JSONAdapter primary: {verdict}"
    finally:
        sandbox.delete()
