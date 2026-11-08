"""Direct native DSPy model configuration and optional endpoint browsing."""

from __future__ import annotations

import contextlib
import hashlib
import ipaddress
import json
from dataclasses import fields
from typing import TYPE_CHECKING, Annotated, Literal
from urllib.parse import urlsplit

import dspy
import httpx
from dspy.lm15 import AccessPolicy, OpenAIChatCompat, ProviderDefinition, register_provider
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, SecretStr, model_validator

if TYPE_CHECKING:
    from fleet_rlm.config import FleetSettings

Role = Literal["root", "sub", "reflection", "judge"]
ModelRoute = Annotated[str, Field(min_length=1, max_length=300, pattern=r"^\S+$")]
EnvReference = Annotated[str, Field(pattern=r"^(?:[A-Za-z_][A-Za-z0-9_]*)?$")]


def validate_endpoint(value: str) -> str:
    """Accept HTTPS or loopback HTTP without embedded credentials."""
    if not value:
        return value
    parsed = urlsplit(value)
    host = parsed.hostname
    loopback = host == "localhost"
    if host:
        with contextlib.suppress(ValueError):
            loopback = loopback or ipaddress.ip_address(host).is_loopback
    if (
        not host
        or parsed.scheme not in {"https", "http"}
        or (parsed.scheme == "http" and not loopback)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Endpoint must be HTTPS or loopback HTTP without credentials, query, or fragment")
    _ = parsed.port
    return value.rstrip("/")


Endpoint = Annotated[str, AfterValidator(validate_endpoint)]


class ResolvedModel(BaseModel):
    """One role's validated settings and private credential snapshot."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    model: ModelRoute
    api_base: Endpoint = ""
    api_key: SecretStr = Field(default_factory=lambda: SecretStr(""), exclude=True)
    adapter: Literal["chat", "json"] = "chat"
    response_format: Literal["auto", "json_object"] = "auto"
    max_tokens: int | None = Field(default=None, gt=0)
    temperature: float | None = Field(default=None, ge=0, le=2, allow_inf_nan=False)
    num_retries: int = Field(default=3, ge=0, le=10)
    cache: bool = False

    @model_validator(mode="after")
    def validate_response_format(self) -> ResolvedModel:
        """JSON transport mode requires native JSON field validation."""
        if self.response_format == "json_object" and self.adapter != "json":
            raise ValueError("json_object response format requires the JSON adapter")
        return self

    def build_adapter(self) -> dspy.Adapter:
        """Create a native DSPy adapter for this role."""
        return dspy.JSONAdapter() if self.adapter == "json" else dspy.ChatAdapter()


def build_lm(settings: FleetSettings, model_id: str, role: Role) -> dspy.LM:
    """Build a native client, retaining the role connection on route replacement."""
    resolved = settings.resolved_model(role)
    route = model_id or resolved.model
    if (
        route.startswith(("openai/", "fleet-unity-"))
        and urlsplit(resolved.api_base).path.rstrip("/") == "/ai-gateway/mlflow/v1"
    ):
        # Native declarations require an address. Stable per-endpoint IDs avoid
        # replacing another workspace's binding; credentials remain LM-owned.
        provider = "fleet-unity-" + hashlib.sha256(resolved.api_base.encode()).hexdigest()
        register_provider(
            ProviderDefinition.chat(
                AccessPolicy(provider=provider, base_url=resolved.api_base),
                compat=OpenAIChatCompat(max_tokens_field="max_tokens"),
            )
        )
        route = provider + "/" + route.split("/", 1)[1]
    options = resolved.model_dump(exclude={"model", "adapter", "api_base", "response_format"}, exclude_none=True)
    if resolved.api_base:
        options["api_base"] = resolved.api_base
    if resolved.api_key.get_secret_value():
        options["api_key"] = resolved.api_key.get_secret_value()
    if resolved.response_format == "json_object":
        options["response_format"] = {"type": "json_object"}
    return dspy.LM(route, engine="lm15", **options)


def complete_text(
    lm: dspy.LM,
    prompt: str,
    *,
    system: str | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
) -> str:
    """Run one canonical lm15 text request and return its normalized response text."""
    from dspy.lm15 import Config, Message, Request

    config_fields = {field.name for field in fields(Config)}
    defaults = {
        key: value for key, value in getattr(lm, "kwargs", {}).items() if key in config_fields and value is not None
    }
    if max_tokens is not None:
        defaults["max_tokens"] = max_tokens
    if temperature is not None:
        defaults["temperature"] = temperature
    config = Config(**defaults)
    response = lm(Request(model=lm.model, system=system, messages=(Message.user(prompt),), config=config))
    return response.text


def list_models(settings: FleetSettings, *, scope: str = "", role: Role | Literal["shared"] = "root") -> list[str]:
    """Browse only the selected role's explicit OpenAI-compatible endpoint."""
    del scope
    resolved = settings.resolved_model(role)
    if not resolved.api_base or not resolved.model.startswith("openai/"):
        return []
    headers = {}
    if resolved.api_key.get_secret_value():
        headers["Authorization"] = "Bearer " + resolved.api_key.get_secret_value()
    with httpx.stream("GET", resolved.api_base + "/models", headers=headers, timeout=10) as response:
        response.raise_for_status()
        body = bytearray()
        for chunk in response.iter_bytes(chunk_size=65536):
            if len(body) + len(chunk) > 1_000_000:
                raise ValueError("Model list exceeds size limit")
            body.extend(chunk)
    payload = json.loads(body)
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ValueError("Invalid model list")
    models = set()
    for row in payload["data"][:1000]:
        identifier = row.get("id") if isinstance(row, dict) else None
        if (
            not isinstance(identifier, str)
            or not identifier
            or len(identifier) > 293
            or any(c.isspace() for c in identifier)
        ):
            raise ValueError("Invalid model identifier")
        models.add("openai/" + identifier)
    return sorted(models)
