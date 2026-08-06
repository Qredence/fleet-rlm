from __future__ import annotations

from types import SimpleNamespace

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


@pytest.mark.asyncio
async def test_provider_probe_reports_native_extraction_fallback_for_forced_final_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import fleet_rlm.rlm.provider_probe as provider_probe

    class FakeRecursiveExecutor:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs
            self.tool = object()

        def summary(self) -> SimpleNamespace:
            return SimpleNamespace(call_count=1)

    class FakeRLM:
        async def acall(self, interpreter, **kwargs):
            del interpreter
            assert "probe" in kwargs
            return SimpleNamespace(
                trajectory=["step-1", "step-2", "step-3"],
                answer="child-ok",
                final_reasoning="Extract forced final output",
            )

    monkeypatch.setattr(provider_probe, "RecursiveRLMExecutor", FakeRecursiveExecutor)
    monkeypatch.setattr(provider_probe, "build_native_rlm", lambda *args, **kwargs: FakeRLM())

    result = await provider_probe.probe_root_lm(dspy.utils.DummyLM([], adapter=dspy.JSONAdapter()))

    assert result.iterations == 3
    assert result.termination_mode == "native_extraction_fallback"
