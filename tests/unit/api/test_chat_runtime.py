from __future__ import annotations

from types import SimpleNamespace

from fleet_rlm.api.runtime_services.chat_runtime import (
    PreparedChatRuntime,
    _chat_agent_builder_kwargs,
)


def test_chat_agent_builder_kwargs_forwards_rlm_limits_from_server_config() -> None:
    cfg = SimpleNamespace(
        react_max_iters=15,
        rlm_max_iterations=31,
        rlm_max_llm_calls=17,
        agent_max_output_chars=12_345,
    )
    runtime = PreparedChatRuntime(
        cfg=cfg,  # type: ignore[arg-type]
        planner_lm=object(),
        delegate_lm=object(),
        repository=object(),
        persistence=None,
        persistence_required=False,
        identity_rows=None,
    )

    kwargs = _chat_agent_builder_kwargs(runtime)

    assert kwargs["react_max_iters"] == 15
    assert kwargs["rlm_max_iterations"] == 31
    assert kwargs["rlm_max_llm_calls"] == 17
    assert kwargs["rlm_max_output_chars"] == 12_345
