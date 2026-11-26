"""Unit tests for llm_registry.py: Fernet crypto, SSRF guards, /models probing, and DSPy 3.5 LM."""

from __future__ import annotations

import json
import socket
from typing import Any

import httpx
import pytest
from cryptography.fernet import Fernet
from dspy.clients.backend_selection import select_backend
from pydantic import SecretStr

from fleet_rlm.config import Settings
from fleet_rlm.llm_registry import (
    PROVIDER_DIALECTS,
    CredentialEncryptionError,
    EndpointValidationError,
    ProviderProbeError,
    build_dspy_lm,
    decrypt_secret,
    encrypt_secret,
    encryption_configured,
    fetch_provider_models,
    mask_secret_hint,
    validate_database_url,
    validate_endpoint_url,
)


def _make_settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "_env_file": None,
        "database_url": "postgresql://primary-host/neondb",
        "database_direct_url": "postgresql://primary-host/neondb",
        "daytona_api_key": "offline",
        "daytona_image": "offline",
        "llm_model": "openai/gpt-4o-mini",
        "sub_llm_model": "openai/gpt-4o-mini",
        "llm_api_key": "offline",
    }
    base.update(overrides)
    return Settings(**base)


def _mock_dns(monkeypatch: pytest.MonkeyPatch, ips: list[str]) -> None:
    def fake_getaddrinfo(host: str, port: int, *args: Any, **kwargs: Any):
        result = []
        for ip in ips:
            family = socket.AF_INET6 if ":" in ip else socket.AF_INET
            sockaddr = (ip, port, 0, 0) if family == socket.AF_INET6 else (ip, port)
            result.append((family, socket.SOCK_STREAM, 6, "", sockaddr))
        return result

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


def test_fernet_encryption_roundtrip_and_errors() -> None:
    fernet_key = Fernet.generate_key().decode("ascii")
    settings_none = _make_settings()
    settings_key = _make_settings(credentials_encryption_key=SecretStr(fernet_key))
    settings_passphrase = _make_settings(
        credentials_encryption_key=SecretStr("super-strong-passphrase-123456")
    )

    assert not encryption_configured(settings_none)
    assert not encryption_configured(None)
    assert not encryption_configured("   ")
    assert encryption_configured(settings_key)
    assert encryption_configured(fernet_key)

    ct = encrypt_secret("sk-proj-secret-value-9876", settings_key)
    assert ct != "sk-proj-secret-value-9876"
    assert decrypt_secret(ct, settings_key) == "sk-proj-secret-value-9876"

    ct2 = encrypt_secret("sk-ant-another-key-1234", settings_passphrase)
    assert decrypt_secret(ct2, settings_passphrase) == "sk-ant-another-key-1234"

    with pytest.raises(CredentialEncryptionError):
        encrypt_secret("sk-test-1234567890", settings_none)
    with pytest.raises(CredentialEncryptionError):
        encrypt_secret("   ", settings_key)
    with pytest.raises(CredentialEncryptionError):
        encrypt_secret("sk-test-1234567890", "too-short")
    with pytest.raises(CredentialEncryptionError):
        decrypt_secret(ct, settings_passphrase)
    with pytest.raises(CredentialEncryptionError):
        decrypt_secret("not-valid-fernet-token", settings_key)


@pytest.mark.parametrize(
    ("secret", "expected"),
    [
        (None, None),
        ("", None),
        ("   ", None),
        ("abcd", "****"),
        ("12345678", "***78"),
        ("sk-1234567890abcdef9a4f", "sk-...9a4f"),
        ("sk-ant-api03-abcdef1234", "sk-ant-...1234"),
        ("sk-or-v1-abcdef5678", "sk-or-v1-...5678"),
        ("gsk_abcdefghijklmnop4321", "gsk_...4321"),
        ("xai-abcdefghijklmnop8888", "xai-...8888"),
        ("AIzaSyDummyKeyValue1234567890", "AIza...7890"),
        ("custom-provider-key-xyz9", "cus...xyz9"),
    ],
)
def test_mask_secret_hint(secret: str | None, expected: str | None) -> None:
    assert mask_secret_hint(secret) == expected


@pytest.mark.parametrize(
    "blocked_ip",
    [
        "169.254.169.254",
        "169.254.0.1",
        "fe80::1",
        "fd00:ec2::254",
        "::ffff:169.254.169.254",
        "0.0.0.0",
        "::",
        "224.0.0.1",
        "ff02::1",
    ],
)
def test_ssrf_always_blocks_metadata_link_local_and_multicast(
    monkeypatch: pytest.MonkeyPatch, blocked_ip: str
) -> None:
    _mock_dns(monkeypatch, [blocked_ip])
    with pytest.raises(EndpointValidationError):
        validate_endpoint_url("https://evil.example.test/v1", allow_private=False)
    with pytest.raises(EndpointValidationError):
        validate_endpoint_url("https://evil.example.test/v1", allow_private=True)
    with pytest.raises(EndpointValidationError):
        validate_database_url("postgresql://user:pass@evil.example.test/neondb", allow_private=True)


@pytest.mark.parametrize(
    "private_ip",
    ["127.0.0.1", "::1", "10.0.0.5", "172.16.1.2", "192.168.1.9"],
)
def test_ssrf_private_and_loopback_respects_allow_private(
    monkeypatch: pytest.MonkeyPatch, private_ip: str
) -> None:
    _mock_dns(monkeypatch, [private_ip])
    with pytest.raises(EndpointValidationError):
        validate_endpoint_url("http://local-llm.internal:11434/v1", allow_private=False)
    assert (
        validate_endpoint_url("http://local-llm.internal:11434/v1/", allow_private=True)
        == "http://local-llm.internal:11434/v1"
    )
    with pytest.raises(EndpointValidationError):
        validate_database_url("postgresql://u:p@local-db.internal/neondb", allow_private=False)
    assert (
        validate_database_url(
            "postgresql://u:p@local-db.internal/neondb?sslmode=disable", allow_private=True
        )
        == "postgresql+psycopg://u:p@local-db.internal/neondb?sslmode=disable"
    )


def test_ssrf_blocks_metadata_hostnames_credentials_queries_and_dns_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_dns(monkeypatch, ["93.184.216.34"])
    assert (
        validate_endpoint_url("https://api.openai.com/v1/", allow_private=False)
        == "https://api.openai.com/v1"
    )
    with pytest.raises(EndpointValidationError):
        validate_endpoint_url(
            "https://metadata.google.internal/computeMetadata/v1", allow_private=True
        )
    with pytest.raises(EndpointValidationError):
        validate_endpoint_url("https://user:pass@api.openai.com/v1", allow_private=False)
    with pytest.raises(EndpointValidationError):
        validate_endpoint_url("https://api.openai.com/v1?foo=bar", allow_private=False)
    with pytest.raises(EndpointValidationError):
        validate_endpoint_url("file:///etc/passwd", allow_private=True)

    def fail_dns(*_args: Any, **_kwargs: Any):
        raise socket.gaierror("Name or service not known")

    monkeypatch.setattr(socket, "getaddrinfo", fail_dns)
    with pytest.raises(EndpointValidationError):
        validate_endpoint_url("https://nonexistent.invalid/v1", allow_private=False)


def test_provider_dialects_registry_complete() -> None:
    expected = {
        "openai",
        "openai_compatible",
        "anthropic",
        "gemini",
        "openrouter",
        "xai",
        "groq",
        "deepseek",
        "ollama",
        "vllm",
        "typesafe",
    }
    assert set(PROVIDER_DIALECTS) == expected
    assert PROVIDER_DIALECTS["openai_compatible"].default_api_mode == "chat"
    assert PROVIDER_DIALECTS["vllm"].dspy_separator == ":"
    assert PROVIDER_DIALECTS["typesafe"].dspy_separator == ":"


@pytest.mark.asyncio
async def test_fetch_provider_models_all_auth_dialects(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_dns(monkeypatch, ["93.184.216.34"])
    seen_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append(request)
        if request.url.host == "api.anthropic.com":
            assert request.headers["x-api-key"] == "ant-key"
            assert request.headers["anthropic-version"] == "2023-06-01"
            assert request.url.params["limit"] == "1000"
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": "claude-sonnet-4-5", "display_name": "Claude Sonnet 4.5"},
                        {"id": "claude-haiku-4-5", "display_name": "Claude Haiku 4.5"},
                    ]
                },
            )
        if request.url.host == "generativelanguage.googleapis.com":
            assert request.headers["x-goog-api-key"] == "gem-key"
            assert request.url.params["pageSize"] == "1000"
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "name": "models/gemini-2.5-pro",
                            "displayName": "Gemini 2.5 Pro",
                            "supportedGenerationMethods": ["generateContent"],
                        },
                        {
                            "name": "models/text-embedding-004",
                            "displayName": "Embedding",
                            "supportedGenerationMethods": ["embedContent"],
                        },
                    ]
                },
            )
        if request.url.host == "api.typesafe.ai":
            assert request.headers["authorization"] == "Bearer ts-key"
            assert request.url.path == "/v1/models"
            return httpx.Response(200, json={"data": [{"id": "jev-latest"}]})
        if request.url.host == "gw.example.test":
            assert request.headers["authorization"] == "Bearer gw-key"
            assert request.url.path == "/v1/models"
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": "gpt-4o-mini", "type": "language"},
                        {"id": "text-embedding-3-small", "type": "embedding"},
                        {"id": "dall-e-3", "architecture": {"output_modalities": ["image"]}},
                    ]
                },
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    original_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: original_client(transport=transport, **kw),
    )

    ant_models = await fetch_provider_models("anthropic", None, "ant-key")
    assert [m["id"] for m in ant_models] == ["claude-haiku-4-5", "claude-sonnet-4-5"]

    gem_models = await fetch_provider_models("gemini", None, "gem-key")
    assert gem_models == [{"id": "gemini-2.5-pro", "label": "Gemini 2.5 Pro"}]

    ts_models = await fetch_provider_models("typesafe", None, "ts-key")
    assert ts_models == [{"id": "jev-latest", "label": "jev-latest"}]

    # TypeSafe with /v1 already on base_url must not double /v1/v1/models
    ts_models_v1 = await fetch_provider_models("typesafe", "https://api.typesafe.ai/v1", "ts-key")
    assert ts_models_v1 == [{"id": "jev-latest", "label": "jev-latest"}]

    gw_models = await fetch_provider_models(
        "openai_compatible", "https://gw.example.test/v1", "gw-key"
    )
    assert gw_models == [{"id": "gpt-4o-mini", "label": "gpt-4o-mini"}]


@pytest.mark.asyncio
async def test_fetch_provider_models_blocks_redirects_and_oversized_responses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_dns(monkeypatch, ["93.184.216.34"])

    def redirect_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "http://169.254.169.254/latest/meta-data"})

    original_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: original_client(transport=httpx.MockTransport(redirect_handler), **kw),
    )
    with pytest.raises(ProviderProbeError, match="redirect"):
        await fetch_provider_models("openai", "https://api.openai.com/v1", "sk-test-secret-12345")

    big_payload = json.dumps({"data": [{"id": "m" * 1024} for _ in range(2500)]}).encode("utf-8")
    assert len(big_payload) > 2 * 1024 * 1024

    def oversized_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=big_payload)

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: original_client(transport=httpx.MockTransport(oversized_handler), **kw),
    )
    with pytest.raises(ProviderProbeError, match="2 MiB"):
        await fetch_provider_models("openai", "https://api.openai.com/v1", "sk-test-secret-12345")


@pytest.mark.parametrize(
    ("dialect", "api_mode", "model", "base_url", "expected_provider", "expected_model_type"),
    [
        ("openai", "auto", "gpt-4o-mini", None, "openai", "responses"),
        ("openai", "chat", "gpt-4o-mini", None, "openai-chat", "chat"),
        ("openai", "responses", "gpt-4o-mini", None, "openai", "responses"),
        (
            "openai_compatible",
            "chat",
            "meta-llama/Llama-3.3-70B",
            "https://gw.example.test/v1",
            "openai-chat",
            "chat",
        ),
        (
            "openai_compatible",
            "responses",
            "custom-resp-model",
            "https://gw.example.test/v1",
            "openai",
            "responses",
        ),
        ("anthropic", "auto", "claude-sonnet-4-5", None, "anthropic", "chat"),
        ("gemini", "auto", "models/gemini-2.5-pro", None, "gemini", "chat"),
        ("openrouter", "auto", "openai/gpt-4o-mini", None, "openrouter", "chat"),
        ("xai", "auto", "grok-3", None, "xai", "chat"),
        ("groq", "auto", "llama-3.3-70b-versatile", None, "groq", "chat"),
        ("deepseek", "auto", "deepseek-chat", None, "deepseek", "chat"),
        ("ollama", "chat", "llama3.2", "http://127.0.0.1:11434/v1", "ollama", "chat"),
        ("vllm", "chat", "Qwen/Qwen2.5-7B", "http://127.0.0.1:8000/v1", "vllm", "chat"),
        ("typesafe", "auto", "jev-latest", None, "typesafe", "chat"),
    ],
)
def test_build_dspy_lm_uses_native_lm15_and_filters_client_keys(
    dialect: Any,
    api_mode: Any,
    model: str,
    base_url: str | None,
    expected_provider: str,
    expected_model_type: str,
) -> None:
    lm = build_dspy_lm(
        dialect,
        api_mode,
        model,
        api_key="test-key",
        base_url=base_url,
        timeout=30,
        extra_headers={"X-Custom": "ignored"},
        organization="ignored-org",
        custom_llm_provider="ignored",
    )
    selection = select_backend(lm)
    assert selection.native is True
    assert selection.resolution is not None
    assert selection.resolution.provider == expected_provider
    assert selection.model_type == expected_model_type
    assert "extra_headers" not in lm.kwargs
    assert "organization" not in lm.kwargs
    assert "custom_llm_provider" not in lm.kwargs
    assert lm.kwargs["timeout"] == 30
