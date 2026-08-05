from __future__ import annotations

import dspy
import pytest

from fleet_rlm.rlm.provider_probe import RLMProviderContractError, probe_root_lm


@pytest.mark.asyncio
async def test_provider_probe_requires_multiple_native_actions_and_typed_submit() -> None:
    lm = dspy.utils.DummyLM(
        [
            {"reasoning": "initialize", "code": "marker = 'probe-slice'"},
            {"reasoning": "delegate", "code": "child = rlm_query(prompt='Classify: ' + marker)"},
            {"reasoning": "child submit", "code": "SUBMIT(answer='child-ok')"},
            {"reasoning": "submit", "code": "SUBMIT(answer=child)"},
        ],
        adapter=dspy.JSONAdapter(),
    )

    result = await probe_root_lm(lm)

    assert result.iterations == 3
    assert result.termination_mode == "typed_submit"


@pytest.mark.asyncio
async def test_provider_probe_rejects_unparseable_native_provider_output() -> None:
    lm = dspy.utils.DummyLM(
        [{"answer": "provider-native tool tokens"}],
        adapter=dspy.JSONAdapter(),
    )

    with pytest.raises(RLMProviderContractError, match="unparseable"):
        await probe_root_lm(lm)
