from fleet_rlm.server.config import ServerRuntimeConfig


def test_default_config():
    cfg = ServerRuntimeConfig()
    assert cfg.secret_name == "LITELLM"
    assert cfg.timeout == 900
    assert cfg.react_max_iters == 10
    assert cfg.volume_name is None


def test_custom_config():
    cfg = ServerRuntimeConfig(secret_name="CUSTOM", timeout=60, volume_name="vol")
    assert cfg.secret_name == "CUSTOM"
    assert cfg.timeout == 60
    assert cfg.volume_name == "vol"
    assert cfg.rlm_max_iterations == 30
    assert cfg.rlm_max_llm_calls == 50
