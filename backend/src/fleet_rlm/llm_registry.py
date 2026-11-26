"""Encrypted LLM provider registry, SSRF guard, /models discovery, and DSPy LM factory."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import ipaddress
import json
import socket
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

import dspy
import httpx
from cryptography.fernet import Fernet, InvalidToken
from pydantic import SecretStr

from .config import Settings

type ProviderDialect = Literal[
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
]
type ProviderApiMode = Literal["auto", "chat", "responses"]

NATIVE_CLIENT_KEYS: frozenset[str] = frozenset({"api_key", "api_base", "timeout"})
_DISALLOWED_LM_KWARGS: frozenset[str] = frozenset(
    {
        "base_url",
        "headers",
        "extra_headers",
        "api_version",
        "azure_ad_token_provider",
        "organization",
        "project",
        "extra_query",
        "custom_llm_provider",
    }
)
_MAX_MODELS_RESPONSE_BYTES = 2 * 1024 * 1024
_ANTHROPIC_VERSION = "2023-06-01"
_METADATA_HOSTNAMES: frozenset[str] = frozenset(
    {
        "metadata.google.internal",
        "metadata.goog",
        "metadata",
        "instance-data",
        "instance-data.ec2.internal",
    }
)
_ALWAYS_BLOCKED_NETWORKS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = (
    ipaddress.ip_network("169.254.0.0/16"),  # IPv4 link-local / cloud IMDS
    ipaddress.ip_network("0.0.0.0/8"),  # Unspecified / current network
    ipaddress.ip_network("224.0.0.0/4"),  # IPv4 multicast
    ipaddress.ip_network("240.0.0.0/4"),  # IPv4 reserved / broadcast
    ipaddress.ip_network("fe80::/10"),  # IPv6 link-local
    ipaddress.ip_network("fd00:ec2::254/128"),  # AWS IPv6 IMDS
    ipaddress.ip_network("ff00::/8"),  # IPv6 multicast
    ipaddress.ip_network("::/128"),  # IPv6 unspecified
)
_PRIVATE_NETWORKS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = (
    ipaddress.ip_network("127.0.0.0/8"),  # IPv4 loopback
    ipaddress.ip_network("10.0.0.0/8"),  # RFC1918
    ipaddress.ip_network("172.16.0.0/12"),  # RFC1918
    ipaddress.ip_network("192.168.0.0/16"),  # RFC1918
    ipaddress.ip_network("100.64.0.0/10"),  # CGNAT
    ipaddress.ip_network("::1/128"),  # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),  # IPv6 unique local
)
_KNOWN_KEY_PREFIXES: tuple[str, ...] = (
    "sk-proj-",
    "sk-ant-",
    "sk-or-v1-",
    "sk-or-",
    "sk-",
    "gsk_",
    "xai-",
    "AIza",
    "ts_",
    "napi_",
    "dtn_",
    "tvly-",
)


class CredentialEncryptionError(RuntimeError):
    """Raised when provider credentials cannot be encrypted or decrypted."""


class EndpointValidationError(ValueError):
    """Raised when a provider or database endpoint URL fails SSRF or syntax validation."""


class ProviderProbeError(RuntimeError):
    """Raised when probing a provider /models endpoint fails."""


@dataclass(frozen=True)
class ProviderDialectSpec:
    dialect: ProviderDialect
    label: str
    default_base_url: str | None
    default_api_mode: ProviderApiMode
    auth_scheme: Literal["bearer", "x-api-key", "x-goog-api-key"]
    requires_api_key: bool
    requires_base_url: bool
    models_path: str
    dspy_prefix: str
    dspy_separator: Literal["/", ":"]
    supports_api_mode: bool = False


PROVIDER_DIALECTS: dict[ProviderDialect, ProviderDialectSpec] = {
    "openai": ProviderDialectSpec(
        dialect="openai",
        label="OpenAI",
        default_base_url="https://api.openai.com/v1",
        default_api_mode="auto",
        auth_scheme="bearer",
        requires_api_key=True,
        requires_base_url=False,
        models_path="/models",
        dspy_prefix="openai",
        dspy_separator="/",
        supports_api_mode=True,
    ),
    "openai_compatible": ProviderDialectSpec(
        dialect="openai_compatible",
        label="OpenAI-Compatible Gateway",
        default_base_url=None,
        default_api_mode="chat",
        auth_scheme="bearer",
        requires_api_key=False,
        requires_base_url=True,
        models_path="/models",
        dspy_prefix="openai",
        dspy_separator="/",
        supports_api_mode=True,
    ),
    "anthropic": ProviderDialectSpec(
        dialect="anthropic",
        label="Anthropic",
        default_base_url="https://api.anthropic.com/v1",
        default_api_mode="auto",
        auth_scheme="x-api-key",
        requires_api_key=True,
        requires_base_url=False,
        models_path="/models",
        dspy_prefix="anthropic",
        dspy_separator="/",
    ),
    "gemini": ProviderDialectSpec(
        dialect="gemini",
        label="Google Gemini",
        default_base_url="https://generativelanguage.googleapis.com/v1beta",
        default_api_mode="auto",
        auth_scheme="x-goog-api-key",
        requires_api_key=True,
        requires_base_url=False,
        models_path="/models",
        dspy_prefix="gemini",
        dspy_separator="/",
    ),
    "openrouter": ProviderDialectSpec(
        dialect="openrouter",
        label="OpenRouter",
        default_base_url="https://openrouter.ai/api/v1",
        default_api_mode="auto",
        auth_scheme="bearer",
        requires_api_key=True,
        requires_base_url=False,
        models_path="/models",
        dspy_prefix="openrouter",
        dspy_separator="/",
    ),
    "xai": ProviderDialectSpec(
        dialect="xai",
        label="xAI",
        default_base_url="https://api.x.ai/v1",
        default_api_mode="auto",
        auth_scheme="bearer",
        requires_api_key=True,
        requires_base_url=False,
        models_path="/models",
        dspy_prefix="xai",
        dspy_separator="/",
    ),
    "groq": ProviderDialectSpec(
        dialect="groq",
        label="Groq",
        default_base_url="https://api.groq.com/openai/v1",
        default_api_mode="auto",
        auth_scheme="bearer",
        requires_api_key=True,
        requires_base_url=False,
        models_path="/models",
        dspy_prefix="groq",
        dspy_separator="/",
    ),
    "deepseek": ProviderDialectSpec(
        dialect="deepseek",
        label="DeepSeek",
        default_base_url="https://api.deepseek.com",
        default_api_mode="auto",
        auth_scheme="bearer",
        requires_api_key=True,
        requires_base_url=False,
        models_path="/models",
        dspy_prefix="deepseek",
        dspy_separator="/",
    ),
    "ollama": ProviderDialectSpec(
        dialect="ollama",
        label="Ollama",
        default_base_url="http://localhost:11434/v1",
        default_api_mode="chat",
        auth_scheme="bearer",
        requires_api_key=False,
        requires_base_url=False,
        models_path="/models",
        dspy_prefix="ollama",
        dspy_separator="/",
    ),
    "vllm": ProviderDialectSpec(
        dialect="vllm",
        label="vLLM",
        default_base_url="http://localhost:8000/v1",
        default_api_mode="chat",
        auth_scheme="bearer",
        requires_api_key=False,
        requires_base_url=False,
        models_path="/models",
        dspy_prefix="vllm",
        dspy_separator=":",
    ),
    "typesafe": ProviderDialectSpec(
        dialect="typesafe",
        label="TypeSafe (Jev)",
        default_base_url="https://api.typesafe.ai",
        default_api_mode="auto",
        auth_scheme="bearer",
        requires_api_key=True,
        requires_base_url=False,
        models_path="/v1/models",
        dspy_prefix="typesafe",
        dspy_separator=":",
    ),
}


def _extract_raw_key(source: Settings | SecretStr | str | None) -> str | None:
    if source is None:
        return None
    if isinstance(source, Settings):
        if source.credentials_encryption_key is None:
            return None
        raw = source.credentials_encryption_key.get_secret_value().strip()
        return raw or None
    if isinstance(source, SecretStr):
        raw = source.get_secret_value().strip()
        return raw or None
    raw = source.strip()
    return raw or None


def encryption_configured(settings: Settings | SecretStr | str | None) -> bool:
    """Return True if a non-empty credentials encryption key is configured."""
    return _extract_raw_key(settings) is not None


def _build_fernet(source: Settings | SecretStr | str | None) -> Fernet:
    raw = _extract_raw_key(source)
    if not raw:
        raise CredentialEncryptionError(
            "FLEET_CREDENTIALS_ENCRYPTION_KEY must be configured to store encrypted API keys"
        )
    try:
        return Fernet(raw.encode("ascii"))
    except (ValueError, UnicodeEncodeError):
        if len(raw) < 16:
            raise CredentialEncryptionError(
                "FLEET_CREDENTIALS_ENCRYPTION_KEY must be a valid Fernet key "
                "or a passphrase of at least 16 characters"
            ) from None
        derived = base64.urlsafe_b64encode(hashlib.sha256(raw.encode("utf-8")).digest())
        return Fernet(derived)


def encrypt_secret(plaintext: str, key_source: Settings | SecretStr | str | None) -> str:
    """Encrypt a plaintext API key using Fernet authenticated encryption."""
    cleaned = plaintext.strip()
    if not cleaned:
        raise CredentialEncryptionError("Cannot encrypt an empty secret")
    fernet = _build_fernet(key_source)
    return fernet.encrypt(cleaned.encode("utf-8")).decode("ascii")


def decrypt_secret(ciphertext: str, key_source: Settings | SecretStr | str | None) -> str:
    """Decrypt a stored Fernet ciphertext back to its plaintext API key."""
    cleaned = ciphertext.strip()
    if not cleaned:
        raise CredentialEncryptionError("Stored ciphertext is empty")
    fernet = _build_fernet(key_source)
    try:
        return fernet.decrypt(cleaned.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeDecodeError) as error:
        raise CredentialEncryptionError(
            "Could not decrypt stored provider credential; verify FLEET_CREDENTIALS_ENCRYPTION_KEY"
        ) from error


def mask_secret_hint(secret: str | None) -> str | None:
    """Return a safe write-only hint (e.g. 'sk-...9a4f') without leaking the raw secret."""
    if secret is None:
        return None
    cleaned = secret.strip()
    if not cleaned:
        return None
    if len(cleaned) <= 4:
        return "****"
    if len(cleaned) <= 8:
        return f"***{cleaned[-2:]}"
    for prefix in _KNOWN_KEY_PREFIXES:
        if cleaned.startswith(prefix) and len(cleaned) > len(prefix) + 4:
            return f"{prefix}...{cleaned[-4:]}"
    return f"{cleaned[:3]}...{cleaned[-4:]}"


def _unwrap_ip(
    addr: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        return addr.ipv4_mapped
    return addr


def _check_ip_allowed(
    addr: ipaddress.IPv4Address | ipaddress.IPv6Address, *, allow_private: bool
) -> None:
    ip = _unwrap_ip(addr)
    if (
        ip.is_link_local
        or ip.is_unspecified
        or ip.is_multicast
        or any(ip in net for net in _ALWAYS_BLOCKED_NETWORKS)
    ):
        raise EndpointValidationError(f"Endpoint IP address {ip} is blocked by SSRF policy")
    if not allow_private and (
        ip.is_loopback
        or ip.is_private
        or ip.is_reserved
        or any(ip in net for net in _PRIVATE_NETWORKS)
    ):
        raise EndpointValidationError(
            f"Private or loopback endpoint IP {ip} is blocked "
            "(enable FLEET_ALLOW_PRIVATE_LLM_ENDPOINTS=true to allow local endpoints)"
        )


def _resolve_and_check_host(hostname: str, port: int, *, allow_private: bool) -> None:
    normalized_host = hostname.strip().rstrip(".").lower()
    if not normalized_host:
        raise EndpointValidationError("Endpoint URL must include a valid hostname")
    if normalized_host in _METADATA_HOSTNAMES:
        raise EndpointValidationError(
            f"Endpoint host {normalized_host!r} is blocked by SSRF policy"
        )

    # Check direct IP literal before DNS lookup as well.
    try:
        literal_ip = ipaddress.ip_address(normalized_host)
    except ValueError:
        literal_ip = None
    if literal_ip is not None:
        _check_ip_allowed(literal_ip, allow_private=allow_private)

    try:
        infos = socket.getaddrinfo(normalized_host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, OSError) as error:
        raise EndpointValidationError(
            f"Could not resolve endpoint host {normalized_host!r}"
        ) from error

    resolved_ips: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for _family, _socktype, _proto, _canonname, sockaddr in infos:
        raw_ip = sockaddr[0]
        if not isinstance(raw_ip, str):
            continue
        # Strip IPv6 scope IDs like fe80::1%en0 if present.
        clean_ip = raw_ip.split("%", 1)[0]
        resolved_ips.append(ipaddress.ip_address(clean_ip))

    if not resolved_ips:
        raise EndpointValidationError(
            f"Endpoint host {normalized_host!r} resolved to no IP addresses"
        )
    for ip in resolved_ips:
        _check_ip_allowed(ip, allow_private=allow_private)


def validate_endpoint_url(
    url: str,
    *,
    allow_private: bool = False,
    schemes: Sequence[str] = ("https", "http"),
) -> str:
    """Validate an HTTP(S) provider endpoint URL against DNS-resolved SSRF rules."""
    cleaned = url.strip()
    if not cleaned:
        raise EndpointValidationError("Endpoint URL must not be empty")
    if any(ord(ch) < 32 or ch.isspace() for ch in cleaned):
        raise EndpointValidationError(
            "Endpoint URL contains invalid whitespace or control characters"
        )
    try:
        parsed = urlsplit(cleaned)
    except ValueError as error:
        raise EndpointValidationError(f"Malformed endpoint URL: {error}") from error

    allowed_schemes = {scheme.lower() for scheme in schemes}
    scheme = parsed.scheme.lower()
    if scheme not in allowed_schemes:
        allowed_text = ", ".join(sorted(allowed_schemes))
        raise EndpointValidationError(
            f"Unsupported URL scheme {parsed.scheme!r} (allowed: {allowed_text})"
        )
    if parsed.username is not None or parsed.password is not None:
        raise EndpointValidationError("Endpoint URL must not embed credentials")
    if parsed.query or parsed.fragment:
        raise EndpointValidationError(
            "Endpoint base URL must not contain query parameters or fragments"
        )
    if not parsed.hostname:
        raise EndpointValidationError("Endpoint URL must include a hostname")
    try:
        port = parsed.port
    except ValueError as error:
        raise EndpointValidationError("Endpoint URL contains an invalid port") from error
    if port is not None and not (1 <= port <= 65535):
        raise EndpointValidationError("Endpoint URL port is out of range")

    effective_port = port if port is not None else (443 if scheme == "https" else 80)
    _resolve_and_check_host(parsed.hostname, effective_port, allow_private=allow_private)
    path = parsed.path.rstrip("/")
    return urlunsplit((scheme, parsed.netloc, path, "", ""))


def validate_database_url(url: str, *, allow_private: bool = False) -> str:
    """Validate and normalize a PostgreSQL URL against DNS-resolved SSRF rules."""
    raw = url.strip()
    if not raw:
        raise EndpointValidationError("Database URL must not be empty")
    if any(ord(ch) < 32 or ch.isspace() for ch in raw):
        raise EndpointValidationError(
            "Database URL contains invalid whitespace or control characters"
        )
    if raw.startswith("postgresql://"):
        normalized = raw.replace("postgresql://", "postgresql+psycopg://", 1)
    elif raw.startswith("postgresql+psycopg://"):
        normalized = raw
    else:
        raise EndpointValidationError(
            "Database URL must start with postgresql:// or postgresql+psycopg://"
        )
    try:
        parsed = urlsplit(normalized)
    except ValueError as error:
        raise EndpointValidationError(f"Malformed database URL: {error}") from error
    if not parsed.hostname:
        raise EndpointValidationError("Database URL must include a hostname")
    try:
        port = parsed.port
    except ValueError as error:
        raise EndpointValidationError("Database URL contains an invalid port") from error
    if port is not None and not (1 <= port <= 65535):
        raise EndpointValidationError("Database URL port is out of range")

    effective_port = port if port is not None else 5432
    _resolve_and_check_host(parsed.hostname, effective_port, allow_private=allow_private)
    return normalized


def _build_models_request(
    spec: ProviderDialectSpec,
    validated_base: str,
    api_key: str | None,
) -> tuple[str, dict[str, str], dict[str, str]]:
    base = validated_base.rstrip("/")
    headers: dict[str, str] = {"Accept": "application/json"}
    params: dict[str, str] = {}

    if spec.dialect == "anthropic":
        headers["anthropic-version"] = _ANTHROPIC_VERSION
        if api_key:
            headers["x-api-key"] = api_key
        params["limit"] = "1000"
        url = f"{base}/models"
    elif spec.dialect == "gemini":
        if api_key:
            headers["x-goog-api-key"] = api_key
        params["pageSize"] = "1000"
        url = f"{base}/models"
    elif spec.dialect == "typesafe":
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        url = f"{base}/models" if base.endswith("/v1") else f"{base}/v1/models"
    else:
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        url = f"{base}/models"
    return url, params, headers


def _is_valid_model_id(value: Any) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and not any(ch.isspace() or not ch.isprintable() for ch in value)
    )


def _normalize_models_payload(dialect: ProviderDialect, payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        raise ProviderProbeError("Provider /models response must be a JSON object")

    raw_items: Any
    if dialect == "gemini":
        raw_items = payload.get("models")
    else:
        raw_items = payload.get("data")
        if raw_items is None and isinstance(payload.get("models"), list):
            raw_items = payload.get("models")

    if not isinstance(raw_items, list):
        raise ProviderProbeError("Provider /models response did not include a model list")

    by_id: dict[str, dict[str, Any]] = {}
    for entry in raw_items:
        if not isinstance(entry, dict):
            continue
        if dialect == "gemini":
            raw_name = entry.get("name")
            if not isinstance(raw_name, str) or not raw_name.strip():
                continue
            model_id = raw_name.strip().removeprefix("models/")
            if not _is_valid_model_id(model_id):
                continue
            methods = entry.get("supportedGenerationMethods")
            if isinstance(methods, list) and methods and "generateContent" not in methods:
                continue
            label = entry.get("displayName")
            item: dict[str, Any] = {
                "id": model_id,
                "label": label.strip() if isinstance(label, str) and label.strip() else model_id,
            }
            by_id[model_id] = item
            continue

        raw_id = entry.get("id") or entry.get("name")
        if not _is_valid_model_id(raw_id):
            continue
        assert isinstance(raw_id, str)
        model_type = entry.get("type")
        if (
            isinstance(model_type, str)
            and model_type
            and model_type not in {"model", "language", "chat", "text"}
        ):
            continue
        architecture = entry.get("architecture")
        output = architecture.get("output_modalities") if isinstance(architecture, dict) else None
        if isinstance(output, list) and output and "text" not in output:
            continue

        display_name = entry.get("display_name") or entry.get("name")
        label = (
            display_name.strip()
            if isinstance(display_name, str) and display_name.strip()
            else raw_id
        )
        normalized: dict[str, Any] = {"id": raw_id, "label": label}
        owned_by = entry.get("owned_by")
        if isinstance(owned_by, str) and owned_by.strip():
            normalized["owned_by"] = owned_by.strip()
        by_id[raw_id] = normalized

    return sorted(by_id.values(), key=lambda item: (item["id"].casefold(), item["id"]))


async def fetch_provider_models(
    dialect: ProviderDialect,
    base_url: str | None,
    api_key: str | None = None,
    *,
    allow_private: bool = False,
    timeout: float = 8.0,
) -> list[dict[str, Any]]:
    """Probe a provider's /models endpoint safely (SSRF-checked, no redirects, 2 MiB cap)."""
    spec = PROVIDER_DIALECTS.get(dialect)
    if spec is None:
        raise ValueError(f"Unsupported provider dialect: {dialect!r}")

    effective_base = (base_url or "").strip() or spec.default_base_url
    if not effective_base:
        raise EndpointValidationError(f"Dialect {dialect!r} requires an explicit base_url")
    validated_base = await asyncio.to_thread(
        validate_endpoint_url, effective_base, allow_private=allow_private
    )

    cleaned_key = api_key.strip() if isinstance(api_key, str) and api_key.strip() else None
    url, params, headers = _build_models_request(spec, validated_base, cleaned_key)

    try:
        async with (
            asyncio.timeout(timeout + 1.0),
            httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client,
            client.stream("GET", url, params=params, headers=headers) as response,
        ):
            if 300 <= response.status_code < 400:
                raise ProviderProbeError(
                    f"Provider endpoint attempted an HTTP redirect ({response.status_code}), "
                    "which is disallowed"
                )
            if response.status_code >= 400:
                raise ProviderProbeError(f"Provider /models returned HTTP {response.status_code}")
            chunks = bytearray()
            async for chunk in response.aiter_bytes():
                chunks.extend(chunk)
                if len(chunks) > _MAX_MODELS_RESPONSE_BYTES:
                    raise ProviderProbeError(
                        "Provider /models response exceeded the 2 MiB size limit"
                    )
    except ProviderProbeError:
        raise
    except TimeoutError as error:
        raise ProviderProbeError(f"Timed out probing provider after {timeout:.1f}s") from error
    except httpx.HTTPError as error:
        raise ProviderProbeError(
            f"Could not connect to provider endpoint ({type(error).__name__})"
        ) from error

    try:
        payload = json.loads(chunks.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProviderProbeError("Provider /models returned invalid JSON") from error

    return _normalize_models_payload(dialect, payload)


def _format_model_and_type(
    dialect: ProviderDialect,
    api_mode: ProviderApiMode,
    model: str,
) -> tuple[str, Literal["auto", "chat", "responses"]]:
    cleaned = model.strip()
    if not cleaned:
        raise ValueError("model must be a non-empty string")
    if dialect not in PROVIDER_DIALECTS:
        raise ValueError(f"Unsupported provider dialect: {dialect!r}")
    if api_mode not in {"auto", "chat", "responses"}:
        raise ValueError(f"Unsupported api_mode: {api_mode!r}")

    if dialect == "openai_compatible":
        if cleaned.startswith("openai-chat:"):
            return cleaned, "auto"
        bare = cleaned.removeprefix("openai/")
        if not bare:
            raise ValueError(f"Model {model!r} is missing a model identifier")
        resolved_type: Literal["auto", "chat", "responses"] = (
            "responses" if api_mode == "responses" else "chat"
        )
        return f"openai/{bare}", resolved_type

    if dialect == "openai":
        if cleaned.startswith("openai-chat:"):
            return cleaned, "auto"
        bare = cleaned.removeprefix("openai/")
        if not bare:
            raise ValueError(f"Model {model!r} is missing a model identifier")
        return f"openai/{bare}", api_mode

    if dialect == "vllm":
        if cleaned.startswith("vllm:"):
            bare = cleaned.removeprefix("vllm:")
        elif cleaned.startswith("vllm/"):
            bare = cleaned.removeprefix("vllm/")
        else:
            bare = cleaned
        if not bare:
            raise ValueError(f"Model {model!r} is missing a model identifier")
        return f"vllm:{bare}", "auto"

    if dialect == "typesafe":
        if cleaned.startswith("typesafe:"):
            bare = cleaned.removeprefix("typesafe:")
        elif cleaned.startswith("typesafe/"):
            bare = cleaned.removeprefix("typesafe/")
        else:
            bare = cleaned
        if not bare:
            raise ValueError(f"Model {model!r} is missing a model identifier")
        return f"typesafe:{bare}", "auto"

    if dialect == "gemini":
        bare = cleaned.removeprefix("models/").removeprefix("gemini/")
        if not bare:
            raise ValueError(f"Model {model!r} is missing a model identifier")
        return f"gemini/{bare}", "auto"

    if dialect == "ollama":
        bare = cleaned.removeprefix("ollama/").removeprefix("ollama:")
        if not bare:
            raise ValueError(f"Model {model!r} is missing a model identifier")
        return f"ollama/{bare}", "auto"

    spec = PROVIDER_DIALECTS[dialect]
    prefix = f"{spec.dspy_prefix}{spec.dspy_separator}"
    if cleaned.startswith(prefix):
        if not cleaned.removeprefix(prefix):
            raise ValueError(f"Model {model!r} is missing a model identifier")
        return cleaned, "auto"
    return f"{prefix}{cleaned}", "auto"


def build_dspy_lm(
    dialect: ProviderDialect,
    api_mode: ProviderApiMode,
    model: str,
    api_key: str | None = None,
    base_url: str | None = None,
    **kwargs: Any,
) -> dspy.LM:
    """Construct a DSPy 3.5 native lm15 LM respecting prefix separators and NATIVE_CLIENT_KEYS."""
    full_model, resolved_model_type = _format_model_and_type(dialect, api_mode, model)

    lm_kwargs: dict[str, Any] = {}
    for key, value in kwargs.items():
        if key in _DISALLOWED_LM_KWARGS:
            continue
        if value is None and key in NATIVE_CLIENT_KEYS:
            continue
        lm_kwargs[key] = value

    resolved_key = api_key.strip() if isinstance(api_key, str) and api_key.strip() else None
    if resolved_key is not None:
        lm_kwargs["api_key"] = resolved_key

    resolved_base = base_url.strip() if isinstance(base_url, str) and base_url.strip() else None
    if resolved_base is not None:
        lm_kwargs["api_base"] = resolved_base

    return dspy.LM(full_model, model_type=resolved_model_type, **lm_kwargs)


build_lm = build_dspy_lm
